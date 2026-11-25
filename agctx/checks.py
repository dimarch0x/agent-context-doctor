"""Проверки контекста агента, кеширование прогона и безопасный --fix."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    tomllib = None  # type: ignore[assignment]

from . import parse
from .models import ContextFile, FixRecord, Issue

CHECKS = (
    "dead-path",
    "dead-command",
    "version-drift",
    "skill-frontmatter",
    "duplicate-rule",
    "rule-conflict",
    "empty-dir",
)

BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
# Глаголы создания файла — применяются только к локальной фразе перед токеном
CREATE_VERBS = re.compile(
    r"(созда\w*|запиш\w*|записать|сгенерир\w*|помест\w*|полож\w*|"
    r"\bcreate\b|\bwrite\b|\bgenerate\b|\bmkdir\b|\btouch\b|\badd file\b|\bsave\b|\binit\b)",
    re.I,
)
COPY_SOURCE_VERBS = re.compile(r"(скопир\w*|взять|\bcopy\b|\bcp\b)", re.I)
NEGATION_RE = re.compile(r"(?:\bне\s+|\bнельзя\b|\bnot\s+|\bdon't\s+|\bdo\s+not\s+|\bnever\s+|\bavoid\s+)", re.I)
CLAUSE_SEP_RE = re.compile(r"[;.]\s+|\s+->\s+|\s+→\s+")
DEST_PREP_RE = re.compile(r"^\s*(?:в|на|to|into|->|→)\b", re.I)

WORD_RE = re.compile(r"[a-zа-яё0-9_]+", re.I)
MAKE_TARGET_RE = re.compile(r"^([\w.-]+)\s*:", re.M)
RUNTIME_VER_RE = re.compile(
    r"\b(python|node(?:\.js)?|bun|php)\s*(?:версии|version|v|>=|>|=|:)?\s*(\d+\.\d+(?:\.\d+)?)\+?",
    re.I,
)
URL_HINT_RE = re.compile(r"https?://|github\.com/", re.I)
SCRIPT_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

# Токены для семантического анализа противоречий правил
DIRECTIVE_NEG_RE = re.compile(
    r"(?:\bне\s+(?:использ\w*|добавля\w*|возвраща\w*|лома\w*|храни\w*|дела\w*|пиши\w*|сокраща\w*|применя\w*)|"
    r"\bникогда\s+не(?:\s+использ\w*)?|\bзапрещен\w*(?:\s+использ\w*)?|\bникакого\b|\bбез\s+|"
    r"\bforbid\w*(?:\s+(?:using|to\s+use))?|\bnever(?:\s+use)?|\bavoid(?:\s+using)?|"
    r"\bdon't(?:\s+use)?|\bdo\s+not(?:\s+use)?|\bmust\s+not(?:\s+use)?|"
    r"\bno\s+(?:mock|mocks|mocking|comments?|composer|print)\b)",
    re.I,
)
DIRECTIVE_POS_RE = re.compile(
    r"(?:\bвсегда\b|\bиспольз\w*\b|\bобязательн\w*\b|\bнужно\b|\bпредпочита\w*\b|"
    r"\balways\b|\bmust\b|\buse\b|\bprefer\b|\brequire[sd]?\b|\benforce\b)",
    re.I,
)
CONTRAST_RE = re.compile(
    r"\b(.+?)\s+(?:вместо|instead\s+of|rather\s+than|over)\s+(.+)",
    re.I,
)
STOP_SUBJECT_WORDS = {
    "не", "никогда", "всегда", "используй", "использовать", "используйте", "используя",
    "нужно", "обязательно", "запрещено", "без", "никакого", "предпочитай", "для", "при",
    "чтобы", "или", "только", "как", "это", "все", "каждый", "каждом", "в", "на", "по",
    "из", "с", "к", "о", "и", "а", "но",
    "never", "always", "use", "using", "prefer", "avoid", "forbid", "must", "not", "do",
    "don", "t", "no", "require", "required", "enforce", "only", "when", "with", "without",
    "for", "the", "a", "an", "to", "in", "on", "of", "and", "or", "all", "every", "by",
}

EXCLUSIVE_GROUPS: tuple[tuple[str, tuple[tuple[str, re.Pattern[str]], ...]], ...] = (
    (
        "python-package-manager",
        (
            ("uv", re.compile(r"\buv\b", re.I)),
            ("pip", re.compile(r"\bpip3?\b", re.I)),
            ("poetry", re.compile(r"\bpoetry\b", re.I)),
        ),
    ),
    (
        "python-test-runner",
        (
            ("pytest", re.compile(r"\bpytest\b", re.I)),
            ("unittest", re.compile(r"\bunittest\b", re.I)),
        ),
    ),
    (
        "js-package-manager",
        (
            ("bun", re.compile(r"\bbun\b", re.I)),
            ("npm", re.compile(r"\bnpm\b", re.I)),
            ("pnpm", re.compile(r"\bpnpm\b", re.I)),
            ("yarn", re.compile(r"\byarn\b", re.I)),
        ),
    ),
    (
        "php-architecture",
        (
            ("clean-architecture", re.compile(r"\bclean\s+architecture\b|\bчист\w+\s+архитектур", re.I)),
            ("procedural-php", re.compile(r"\bпроцедурн\w+\s+php\b|\bprocedural\s+php\b", re.I)),
        ),
    ),
)


class _ScanCache:
    """Кеш файловой системы, манифестов и разобранных документов в рамках одного сканирования."""

    def __init__(self, root: Path, home: Path) -> None:
        self.root = root
        self.home = home
        self._exists: dict[Path, bool] = {}
        self._is_file: dict[Path, bool] = {}
        self._is_dir: dict[Path, bool] = {}
        self._dir_has_files: dict[Path, bool] = {}
        self._nearest: dict[tuple[Path, str], Path | None] = {}
        self._pkg: dict[Path, dict] = {}
        self._pyproj: dict[Path, dict[str, str]] = {}
        self._makefile: dict[Path, frozenset[str]] = {}
        self._docs: dict[Path, parse.ParsedDocument] = {}
        self._manifest_bases: dict[Path, tuple[Path, ...]] = {}

    def exists(self, path: Path) -> bool:
        val = self._exists.get(path)
        if val is None:
            try:
                val = path.exists()
            except OSError:
                val = False
            self._exists[path] = val
        return val

    def is_file(self, path: Path) -> bool:
        val = self._is_file.get(path)
        if val is None:
            try:
                val = path.is_file()
            except OSError:
                val = False
            self._is_file[path] = val
        return val

    def is_dir(self, path: Path) -> bool:
        val = self._is_dir.get(path)
        if val is None:
            try:
                val = path.is_dir()
            except OSError:
                val = False
            self._is_dir[path] = val
        return val

    def dir_has_files(self, path: Path) -> bool:
        val = self._dir_has_files.get(path)
        if val is None:
            val = False
            try:
                for _, _, filenames in os.walk(path):
                    if filenames:
                        val = True
                        break
            except OSError:
                val = False
            self._dir_has_files[path] = val
        return val

    def nearest(self, scope: Path, filename: str) -> Path | None:
        key = (scope, filename)
        if key in self._nearest:
            return self._nearest[key]
        visited: list[Path] = []
        cur = scope
        found: Path | None = None
        while True:
            ck = (cur, filename)
            if ck in self._nearest:
                found = self._nearest[ck]
                break
            visited.append(cur)
            candidate = cur / filename
            if self.is_file(candidate):
                found = candidate
                break
            if cur == self.root or cur.parent == cur:
                break
            cur = cur.parent
        for v in visited:
            self._nearest[(v, filename)] = found
        return found

    def manifest_bases(self, scope: Path) -> tuple[Path, ...]:
        if scope in self._manifest_bases:
            return self._manifest_bases[scope]
        bases: list[Path] = []
        if scope.name == "rules" and scope.parent != self.root.parent:
            bases.append(scope.parent)
        for fn in ("package.json", "pyproject.toml", "composer.json", "Cargo.toml", "go.mod"):
            m = self.nearest(scope, fn)
            if m and m.parent != scope and m.parent not in bases:
                bases.append(m.parent)
        res = tuple(bases)
        self._manifest_bases[scope] = res
        return res

    def resolve(self, token: str, scope: Path) -> Path:
        t = parse.clean_token(token)
        if t.startswith("$HOME"):
            return Path(os.path.normpath(self.home / t.removeprefix("$HOME").lstrip("/")))
        if t.startswith("~"):
            return Path(os.path.normpath(self.home / t.removeprefix("~").lstrip("/")))
        p = Path(t)
        if p.is_absolute():
            return Path(os.path.normpath(p))
        candidates = (scope, *self.manifest_bases(scope), self.root)
        seen: set[Path] = set()
        for base in candidates:
            if base in seen:
                continue
            seen.add(base)
            candidate = Path(os.path.normpath(base / p))
            if self.exists(candidate):
                return candidate
        return Path(os.path.normpath(scope / p))

    def doc(self, path: Path, text: str) -> parse.ParsedDocument:
        d = self._docs.get(path)
        if d is None:
            d = parse.parse_document(text)
            self._docs[path] = d
        return d

    def package_json(self, path: Path) -> dict:
        if path not in self._pkg:
            self._pkg[path] = _package_json(path)
        return self._pkg[path]

    def pyproject_meta(self, path: Path) -> dict[str, str]:
        if path not in self._pyproj:
            self._pyproj[path] = _pyproject_meta(path)
        return self._pyproj[path]

    def makefile_targets(self, path: Path) -> frozenset[str]:
        if path not in self._makefile:
            try:
                txt = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                txt = ""
            self._makefile[path] = frozenset(MAKE_TARGET_RE.findall(txt))
        return self._makefile[path]



def _pyproject_meta(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if tomllib is not None:
        try:
            data = tomllib.loads(text)
        except Exception:
            data = {}
        proj = data.get("project") if isinstance(data.get("project"), dict) else {}
        poetry = (
            data.get("tool", {}).get("poetry", {})
            if isinstance(data.get("tool"), dict) and isinstance(data.get("tool", {}).get("poetry"), dict)
            else {}
        )
        name = str(proj.get("name") or poetry.get("name") or "")
        version = str(proj.get("version") or poetry.get("version") or "")
        req_py = str(proj.get("requires-python") or "")
        if not req_py and isinstance(poetry.get("dependencies"), dict):
            req_py = str(poetry["dependencies"].get("python") or "")
        return {"name": name, "version": version, "requires_python": req_py}

    # Python 3.10 fallback: читаем строго внутри [project] / [tool.poetry]
    meta: dict[str, str] = {}
    section = ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip()
            continue
        if section not in {"project", "tool.poetry"}:
            continue
        for key, dst in (("name", "name"), ("version", "version"), ("requires-python", "requires_python")):
            m = re.match(rf"^{key}\s*=\s*[\"']([^\"']+)[\"']", s)
            if m and dst not in meta:
                meta[dst] = m.group(1)
    return meta



def _package_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _has_create_intent(line_text: str, span_start: int, span_end: int) -> bool:
    """Определить, предписывает ли строка СОЗДАТЬ именно этот путь (а не прочитать/скопировать ИЗ него)."""
    before = line_text[:span_start]
    # Берём только последнюю синтаксическую клаузу перед токеном
    parts = CLAUSE_SEP_RE.split(before)
    clause = parts[-1] if parts else before
    if NEGATION_RE.search(clause):
        return False
    if COPY_SOURCE_VERBS.search(clause):
        after = line_text[span_end:]
        # Если после токена идёт `в ...` / `to ...`, то текущий токен — источник, а не цель создания
        if DEST_PREP_RE.match(after):
            return False
        # Если нет предлога направления перед токеном — скорее всего тоже источник
        if not re.search(r"(?:в|to|into)\s*$", clause, re.I):
            return False
    return bool(CREATE_VERBS.search(clause))


def check_dead_paths(
    files: list[ContextFile],
    texts: dict[Path, str],
    root: Path,
    home: Path,
    cache: _ScanCache | None = None,
) -> list[Issue]:
    """Мёртвые пути с формализованной оценкой серьёзности и защитой от ложноотрицательных."""
    sc = cache or _ScanCache(root, home)
    raw: list[dict] = []
    for f in files:
        text = texts.get(f.path, "")
        doc = sc.doc(f.path, text)
        lines = doc.lines
        for lineno, start_col, end_col, token in parse.code_spans_with_pos(text, doc):
            ref = parse.parse_path_ref(token)
            if ref is None:
                continue
            resolved = sc.resolve(token, f.scope)
            if sc.exists(resolved):
                continue
            line_text = lines[lineno - 1] if 0 <= lineno - 1 < len(lines) else ""
            parent_exists = sc.exists(resolved.parent)
            severity = _dead_path_severity(ref, resolved, sc)
            if severity != "info" and _has_create_intent(line_text, start_col, end_col):
                severity = "info"
            raw.append(
                {
                    "file": f.path,
                    "line": lineno,
                    "token": token,
                    "resolved": resolved,
                    "severity": severity,
                    "anchored": ref.anchor != "rel_bare",
                    "parent_exists": parent_exists,
                }
            )

    by_file: dict[Path, list[dict]] = {}
    for entry in raw:
        by_file.setdefault(entry["file"], []).append(entry)

    for items in by_file.values():
        items.sort(key=lambda e: e["line"])
        i = 0
        while i < len(items):
            j = i
            while j + 1 < len(items) and items[j + 1]["line"] - items[j]["line"] <= 4:
                j += 1
            group = items[i : j + 1]
            # Кластер примеров понижаем до info только если ни один путь не лежит в реально существующем каталоге
            if (
                len(group) >= 3
                and all(not e["anchored"] for e in group)
                and not any(e["parent_exists"] for e in group)
            ):
                for e in group:
                    e["severity"] = "info"
            i = j + 1

    return [
        Issue(
            check="dead-path",
            severity=e["severity"],
            path=e["file"],
            line=e["line"],
            message=f"путь не существует: {e['token']}",
            hint=f"проверено как {e['resolved']}",
        )
        for e in raw
    ]


def _dead_path_severity(ref: parse.PathRef, resolved: Path, sc: _ScanCache) -> str:
    """Степень уверенности по глубине существования предка."""
    if sc.exists(resolved):
        return "info"
    t = ref.normalized
    if ref.anchor == "home":
        rest = t.removeprefix("$HOME").removeprefix("~").lstrip("/")
        if "/" not in rest:
            return "warning"
        top = rest.split("/", 1)[0]
        if top and not sc.exists(sc.home / top):
            return "info"
    elif ref.anchor == "abs":
        segs = [seg for seg in t.split("/") if seg]
        if segs and not sc.exists(Path("/" + segs[0])):
            return "info"
    if ref.is_dir:
        return "info"
    parent = resolved.parent
    if sc.exists(parent):
        return "error"
    cur = parent
    while cur != cur.parent:
        cur = cur.parent
        if sc.exists(cur):
            return "warning"
    return "info"


def check_dead_commands(
    files: list[ContextFile],
    texts: dict[Path, str],
    root: Path,
    cache: _ScanCache | None = None,
) -> list[Issue]:
    sc = cache or _ScanCache(root, root)
    issues: list[Issue] = []
    for f in files:
        text = texts.get(f.path, "")
        doc = sc.doc(f.path, text)
        for lineno, cmd in parse.commands(text, doc):
            parts = cmd.split()
            if not parts:
                continue
            runner = parts[0]
            problem = None
            severity = "warning"

            if runner in {"npm", "pnpm", "yarn", "bun"} and len(parts) >= 3 and parts[1] == "run":
                script = parts[2].strip("\"'`,;:")
                if not script or parse.is_placeholder(script) or "*" in script:
                    continue
                if runner == "bun" and (script.endswith(SCRIPT_EXTS) or "/" in script):
                    manifest = sc.nearest(f.scope, "package.json")
                    if manifest or script.startswith(("./", "../", "scripts/")):
                        candidate = sc.resolve(script, f.scope)
                        if not sc.exists(candidate):
                            problem = f"файл скрипта не найден: {script}"
                else:
                    manifest = sc.nearest(f.scope, "package.json")
                    if manifest:
                        scripts = sc.package_json(manifest).get("scripts") or {}
                        if script not in scripts:
                            problem = f"скрипт '{script}' отсутствует в {manifest.name}"
                            if manifest.parent != f.scope:
                                severity = "info"
            elif runner == "make" and len(parts) >= 2:
                target = None
                for tok in parts[1:]:
                    if tok.startswith("-") or "=" in tok:
                        continue
                    target = tok.strip("\"'`,;:")
                    break
                if not target or parse.is_placeholder(target):
                    continue
                mf = sc.nearest(f.scope, "Makefile")
                if mf:
                    targets = sc.makefile_targets(mf)
                    if target not in targets:
                        problem = f"цель '{target}' отсутствует в Makefile"
                        if mf.parent != f.scope:
                            severity = "info"
            elif runner in {"python", "python3"}:
                script_tok = None
                for tok in parts[1:]:
                    if tok in {"-u", "-B", "-O", "-E", "-s"}:
                        continue
                    if tok.startswith("-"):
                        break
                    script_tok = tok.strip("\"'`,;:")
                    break
                if script_tok and script_tok.endswith(".py") and not parse.is_placeholder(script_tok):
                    candidate = sc.resolve(script_tok, f.scope)
                    if not sc.exists(candidate):
                        problem = f"файл скрипта не найден: {script_tok}"
            elif runner == "php" and len(parts) >= 2:
                script_tok = parts[1].strip("\"'`,;:")
                if script_tok.endswith(".php") and not parse.is_placeholder(script_tok):
                    candidate = sc.resolve(script_tok, f.scope)
                    if not sc.exists(candidate):
                        problem = f"файл скрипта не найден: {script_tok}"

            if problem:
                issues.append(
                    Issue(
                        check="dead-command",
                        severity=severity,
                        path=f.path,
                        line=lineno,
                        message=f"команда невыполнима: {cmd}",
                        hint=problem,
                    )
                )
    return issues


def _parse_ver_tuple(ver: str) -> tuple[int, ...]:
    nums = [int(x) for x in re.findall(r"\d+", ver)]
    return tuple(nums[:3])


def _version_satisfies(ver_str: str, spec: str) -> bool:
    """Проверить базовые ограничения PEP 440 / semver: >=, >, <, <=, ==, ^."""
    ver = _parse_ver_tuple(ver_str)
    if not ver:
        return True
    # Выбираем первую ветку при `||`
    branch = spec.split("||")[0].strip()
    clauses = [c.strip() for c in branch.split(",") if c.strip()]
    for clause in clauses:
        m = re.match(r"^(>=|<=|==|!=|>|<|\^|~)?\s*v?(\d+(?:\.\d+){0,2})", clause)
        if not m:
            continue
        op = m.group(1) or ">="
        bound = _parse_ver_tuple(m.group(2))
        n = max(len(ver), len(bound))
        v_pad = ver + (0,) * (n - len(ver))
        b_pad = bound + (0,) * (n - len(bound))
        if op in {">=", "^", "~"} and v_pad < b_pad:
            return False
        if op == ">" and v_pad <= b_pad:
            return False
        if op == "<=" and v_pad > b_pad:
            return False
        if op == "<" and v_pad >= b_pad:
            return False
        if op == "==" and v_pad[: len(bound)] != bound:
            return False
    return True


def check_version_drift(
    files: list[ContextFile],
    texts: dict[Path, str],
    root: Path,
    cache: _ScanCache | None = None,
) -> list[Issue]:
    sc = cache or _ScanCache(root, root)
    issues: list[Issue] = []
    # Кросс-файловое отслеживание версий в пределах одного каталога манифеста
    manifest_proj_versions: dict[Path, list[tuple[ContextFile, int, str]]] = {}
    manifest_runtime_versions: dict[tuple[Path, str], list[tuple[ContextFile, int, str]]] = {}

    for f in files:
        text = texts.get(f.path, "")
        doc = sc.doc(f.path, text)
        lines = doc.lines
        pyproject = sc.nearest(f.scope, "pyproject.toml")
        package = sc.nearest(f.scope, "package.json")
        # Привязываем к ближайшему манифесту
        if pyproject and package:
            manifest = pyproject if len(pyproject.parts) >= len(package.parts) else package
        else:
            manifest = pyproject or package
        if manifest is None:
            continue

        if manifest == pyproject:
            py_meta = sc.pyproject_meta(manifest)
            actual = py_meta.get("version", "")
            proj_name = py_meta.get("name", "")
            engines = {"python": py_meta.get("requires_python", "")} if py_meta.get("requires_python") else {}
        else:
            pkg = sc.package_json(manifest)
            actual = str(pkg.get("version") or "")
            proj_name = str(pkg.get("name") or "")
            eng = pkg.get("engines") if isinstance(pkg.get("engines"), dict) else {}
            engines = {str(k).lower(): str(v) for k, v in eng.items() if v}
            pm = str(pkg.get("packageManager") or "")
            if "@" in pm:
                pm_name, pm_ver = pm.split("@", 1)
                engines.setdefault(pm_name.lower(), f">={pm_ver.split('+')[0]}")

        names: set[str] = {manifest.parent.name.lower()}
        if proj_name:
            low_name = proj_name.lower()
            names.update({low_name, low_name.replace("-", "_"), low_name.replace("_", "-"), low_name.replace("-", " ")})
        names.discard("")

        # Проверяем заголовок файла — относится ли документ к проекту целиком
        header_text = "\n".join(lines[: min(len(lines), doc.body_start + 15)]).lower()
        file_mentions_project = manifest.parent == f.scope or any(n in header_text for n in names)

        # 1. Версия проекта
        name_alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        proj_ver_re = re.compile(rf"(?:{name_alt})\s+v?(\d+\.\d+\.\d+)\b", re.I) if name_alt else None
        seen_lines: set[int] = set()
        check_against_actual = bool(actual and actual != "0.0.0")

        for lineno, v in parse.versions(text, doc):
            line_raw = lines[lineno - 1] if 0 <= lineno - 1 < len(lines) else ""
            if URL_HINT_RE.search(line_raw):
                continue
            line = line_raw.lower()
            in_header_zone = (
                f.kind in {"agents", "claude", "gemini", "rules"}
                and lineno <= doc.body_start + 20
                and file_mentions_project
                and not RUNTIME_VER_RE.search(line_raw)
            )
            explicit_ref = any(n in line for n in names) or bool(re.search(r"\b(?:проект|project|repo|package)\b", line))
            if not (explicit_ref or in_header_zone):
                continue
            seen_lines.add(lineno)
            manifest_proj_versions.setdefault(manifest, []).append((f, lineno, v))
            if check_against_actual and v != actual:
                issues.append(
                    Issue(
                        check="version-drift",
                        severity="warning",
                        path=f.path,
                        line=lineno,
                        message=f"версия в правилах {v} ≠ {actual} в {manifest.name}",
                        hint="обнови правило или манифест",
                    )
                )

        if proj_ver_re:
            for lineno, line_raw in enumerate(lines, start=1):
                if lineno in seen_lines or lineno <= doc.body_start or doc.fence_langs[lineno - 1] is not None:
                    continue
                if URL_HINT_RE.search(line_raw):
                    continue
                for m in proj_ver_re.finditer(line_raw):
                    v = m.group(1)
                    manifest_proj_versions.setdefault(manifest, []).append((f, lineno, v))
                    if check_against_actual and v != actual:
                        issues.append(
                            Issue(
                                check="version-drift",
                                severity="warning",
                                path=f.path,
                                line=lineno,
                                message=f"версия в правилах {v} ≠ {actual} в {manifest.name}",
                                hint="обнови правило или манифест",
                            )
                        )

        # 2. Версии рантайма / стека в инструкциях проекта
        if f.kind in {"agents", "claude", "gemini", "rules"} and manifest.parent == f.scope:
            for lineno, line_raw in enumerate(lines, start=1):
                if lineno <= doc.body_start or doc.fence_langs[lineno - 1] is not None:
                    continue
                if URL_HINT_RE.search(line_raw):
                    continue
                for m in RUNTIME_VER_RE.finditer(line_raw):
                    tool = m.group(1).lower().replace(".js", "")
                    ver = m.group(2)
                    manifest_runtime_versions.setdefault((manifest, tool), []).append((f, lineno, ver))
                    spec = engines.get(tool)
                    if spec and not _version_satisfies(ver, spec):
                        issues.append(
                            Issue(
                                check="version-drift",
                                severity="warning",
                                path=f.path,
                                line=lineno,
                                message=f"версия {tool} {ver} расходится с '{spec}' в {manifest.name}",
                                hint="согласуй минимальную версию стека с манифестом",
                            )
                        )

    # 3. Кросс-файловый дрейф (например AGENTS.md vs CLAUDE.md)
    for manifest, entries in manifest_proj_versions.items():
        distinct = {v for _, _, v in entries}
        by_file = {cf.path for cf, _, _ in entries}
        if len(distinct) >= 2 and len(by_file) >= 2:
            first_f, first_line, first_v = entries[0]
            other = next((e for e in entries[1:] if e[2] != first_v and e[0].path != first_f.path), None)
            if other:
                issues.append(
                    Issue(
                        check="version-drift",
                        severity="warning",
                        path=first_f.path,
                        line=first_line,
                        message=f"версия проекта {first_v} расходится с {other[2]} в {other[0].path.name}",
                        hint=f"синхронизируй версию для {manifest.parent.name}",
                    )
                )

    for (manifest, tool), entries in manifest_runtime_versions.items():
        distinct = {v for _, _, v in entries}
        by_file = {cf.path for cf, _, _ in entries}
        if len(distinct) >= 2 and len(by_file) >= 2:
            first_f, first_line, first_v = entries[0]
            other = next((e for e in entries[1:] if e[2] != first_v and e[0].path != first_f.path), None)
            if other:
                issues.append(
                    Issue(
                        check="version-drift",
                        severity="warning",
                        path=first_f.path,
                        line=first_line,
                        message=f"версия {tool} {first_v} расходится с {other[2]} в {other[0].path.name}",
                        hint=f"синхронизируй требования {tool} для {manifest.parent.name}",
                    )
                )

    return issues


def _is_skill_dir(path: Path) -> bool:
    """Скилл должен находиться внутри дерева `skills/` или `optional-skills/`."""
    return any(p.name in {"skills", "optional-skills"} for p in path.parent.parents)


def check_skill_frontmatter(
    files: list[ContextFile],
    texts: dict[Path, str],
    root: Path | None = None,
    cache: _ScanCache | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    for f in files:
        if f.kind != "skill":
            continue
        if not _is_skill_dir(f.path):
            continue
        text = texts.get(f.path, "")
        doc = cache.doc(f.path, text) if cache else parse.parse_document(text)
        meta = doc.frontmatter
        body_start = doc.body_start
        key_lines = doc.key_lines

        if body_start == 0:
            issues.append(
                Issue("skill-frontmatter", "error", f.path, "нет YAML frontmatter", line=1)
            )
            continue
        if not meta:
            issues.append(
                Issue("skill-frontmatter", "error", f.path, "пустой YAML frontmatter", line=1)
            )
            continue

        raw_name = meta.get("name", "")
        name = raw_name if isinstance(raw_name, str) else ""
        expected = f.path.parent.name
        if name != expected:
            issues.append(
                Issue(
                    "skill-frontmatter",
                    "warning",
                    f.path,
                    f"name '{name}' не совпадает с каталогом '{expected}'",
                    line=key_lines.get("name", 2),
                    hint="name должен равняться имени каталога скилла",
                )
            )

        raw_desc = meta.get("description", "")
        desc = raw_desc if isinstance(raw_desc, str) else ""
        desc_line = key_lines.get("description", 3)
        if not desc.strip():
            issues.append(Issue("skill-frontmatter", "error", f.path, "пустой description", line=desc_line))
        elif not desc.lower().startswith("use when"):
            issues.append(
                Issue(
                    "skill-frontmatter",
                    "info",
                    f.path,
                    "description не начинается с 'Use when'",
                    line=desc_line,
                    hint="правило скиллов: только триггеры активации",
                )
            )

        scripts_dir_exists = (
            cache.exists(f.path.parent / "scripts") if cache else (f.path.parent / "scripts").exists()
        )
        seen_script_links: set[tuple[int, str]] = set()
        for lineno, token in parse.code_spans(text, doc):
            head = token.split()[0] if token.split() else ""
            cleaned = parse.clean_token(head)
            if not cleaned.startswith(("scripts/", "./scripts/")):
                continue
            rel = cleaned.removeprefix("./")
            if rel.endswith("/") or parse.is_placeholder(rel):
                continue
            if (lineno, rel) in seen_script_links:
                continue
            seen_script_links.add((lineno, rel))
            local_target = f.path.parent / rel
            root_target = (root / rel) if root else None
            local_ok = cache.exists(local_target) if cache else local_target.exists()
            root_ok = (
                (cache.exists(root_target) if cache else root_target.exists())
                if root_target is not None
                else False
            )
            if not (local_ok or root_ok):
                issues.append(
                    Issue(
                        "skill-frontmatter",
                        "error" if scripts_dir_exists else "warning",
                        f.path,
                        f"битая ссылка на скрипт: {rel}",
                        line=lineno,
                    )
                )
    return issues


def _short_label(cf: ContextFile, root: Path) -> str:
    try:
        rel = cf.path.relative_to(root)
        if len(rel.parts) >= 3:
            return f"{rel.parent.parent.name}/{rel.parent.name}/{rel.name}"
        if len(rel.parts) == 2:
            return f"{rel.parent.name}/{rel.name}"
        return str(rel)
    except ValueError:
        return f"{cf.path.parent.name}/{cf.path.name}"


def check_duplicate_rules(
    files: list[ContextFile],
    texts: dict[Path, str],
    root: Path | None = None,
    cache: _ScanCache | None = None,
) -> list[Issue]:
    seen: dict[str, list[ContextFile]] = {}
    lines_by_file: dict[Path, dict[str, int]] = {}
    for f in files:
        text = texts.get(f.path, "")
        doc = cache.doc(f.path, text) if cache else parse.parse_document(text)
        per_file: dict[str, int] = {}
        for lineno, line in enumerate(doc.lines, start=1):
            if lineno <= doc.body_start or doc.fence_langs[lineno - 1] is not None:
                continue
            norm = BULLET_RE.sub("", line).strip()
            if len(norm) < 25 or norm.startswith(("#", "|", "```", "~~~")):
                continue
            key = " ".join(WORD_RE.findall(norm.lower()))
            if len(key) < 12:
                continue
            per_file.setdefault(key, lineno)
            seen.setdefault(key, []).append(f)
        lines_by_file[f.path] = per_file

    issues: list[Issue] = []
    for key, holders in seen.items():
        unique: list[ContextFile] = []
        seen_paths: set[Path] = set()
        for h in holders:
            if h.path not in seen_paths:
                seen_paths.add(h.path)
                unique.append(h)
        if len(unique) < 2:
            continue
        first = unique[0]
        lineno = lines_by_file[first.path].get(key)
        base = root or first.scope
        names = [_short_label(u, base) for u in unique[1:3]]
        issues.append(
            Issue(
                "duplicate-rule",
                "info",
                first.path,
                f"одно и то же правило в {len(unique)} файлах: {_short_label(first, base)} и {', '.join(names)}",
                line=lineno,
                hint="вынеси в корневой AGENTS.md, иначе копии разойдутся",
            )
        )
    return issues


def _subject_tokens(phrase: str) -> frozenset[str]:
    words = WORD_RE.findall(phrase.lower())
    return frozenset(w for w in words if len(w) >= 2 and w not in STOP_SUBJECT_WORDS)


def _extract_directives(cf: ContextFile, doc: parse.ParsedDocument) -> list[tuple[int, str, frozenset[str], str]]:
    """Извлечь нормализованные правила вида (line, polarity 'pos'|'neg', subject_tokens, raw)."""
    directives: list[tuple[int, str, frozenset[str], str]] = []
    for lineno, line in enumerate(doc.lines, start=1):
        if lineno <= doc.body_start or doc.fence_langs[lineno - 1] is not None:
            continue
        if line.startswith((" ", "\t")) and not BULLET_RE.match(line):
            continue
        raw = BULLET_RE.sub("", line).strip()
        if not raw or raw.startswith(("#", "|", ">")) or len(raw) < 10:
            continue
        cm = CONTRAST_RE.search(raw)
        if cm:
            pos_part, neg_part = cm.group(1), cm.group(2)
            pos_tok = _subject_tokens(pos_part)
            neg_tok = _subject_tokens(neg_part)
            if 1 <= len(pos_tok) <= 4 and 1 <= len(neg_tok) <= 4:
                directives.append((lineno, "pos", pos_tok, raw))
                directives.append((lineno, "neg", neg_tok, raw))
                continue

        # Разделяем сложные правила вида "Используй X, никогда не Y" / "X — never Y" на клаузы
        subclauses = [c.strip() for c in re.split(r"(?:,\s+|\s+[—–-]{1,2}\s+|;\s+)", raw) if c.strip()]
        for clause in subclauses:
            is_neg = bool(DIRECTIVE_NEG_RE.search(clause))
            raw_without_neg = DIRECTIVE_NEG_RE.sub(" ", clause) if is_neg else clause
            is_pos = bool(DIRECTIVE_POS_RE.search(raw_without_neg))
            if is_neg and not is_pos:
                toks = _subject_tokens(clause)
                if len(toks) >= 2:
                    directives.append((lineno, "neg", toks, raw))
            elif is_pos and not is_neg:
                toks = _subject_tokens(clause)
                if len(toks) >= 2:
                    directives.append((lineno, "pos", toks, raw))
    return directives


def check_rule_conflicts(
    files: list[ContextFile],
    texts: dict[Path, str],
    root: Path | None = None,
    cache: _ScanCache | None = None,
) -> list[Issue]:
    """Семантические противоречия между правилами одного проекта (полярность и выбор стека)."""
    non_skills = [f for f in files if f.kind != "skill"]
    if len(non_skills) < 2:
        return []

    by_scope: dict[Path, list[ContextFile]] = {}
    parsed_directives: dict[Path, list[tuple[int, str, frozenset[str], str]]] = {}
    exclusive_by_file: dict[Path, dict[str, dict[str, int]]] = {}
    for f in non_skills:
        by_scope.setdefault(f.scope, []).append(f)
        text = texts.get(f.path, "")
        doc = cache.doc(f.path, text) if cache else parse.parse_document(text)
        parsed_directives[f.path] = _extract_directives(f, doc)
        exclusive_by_file[f.path] = _find_exclusive_choices(doc)

    # Строим уникальные пары (fa, fb) только вдоль цепочки предков за O(N * depth) вместо O(N^2)
    candidate_pairs: list[tuple[ContextFile, ContextFile]] = []
    seen_file_pairs: set[tuple[Path, Path]] = set()
    for fa in non_skills:
        scopes_to_check: list[Path] = [fa.scope]
        cur = fa.scope
        while cur != cur.parent:
            if root is not None and cur == root:
                break
            cur = cur.parent
            scopes_to_check.append(cur)
        for sc in scopes_to_check:
            for fb in by_scope.get(sc, ()):
                if fa.path == fb.path:
                    continue
                ordered = (fa, fb) if str(fa.path) < str(fb.path) else (fb, fa)
                key = (ordered[0].path, ordered[1].path)
                if key not in seen_file_pairs:
                    seen_file_pairs.add(key)
                    candidate_pairs.append(ordered)

    issues: list[Issue] = []
    seen_pairs: set[tuple[Path, int, Path, int]] = set()
    base = root or non_skills[0].scope

    for fa, fb in candidate_pairs:
        dirs_a = parsed_directives[fa.path]
        dirs_b = parsed_directives[fb.path]
        found_a = exclusive_by_file[fa.path]
        found_b = exclusive_by_file[fb.path]

        # 1. Противоположная полярность по одному и тому же объекту/действию
        for la, pola, toka, rawa in dirs_a:
            for lb, polb, tokb, rawb in dirs_b:
                if pola == polb:
                    continue
                inter = toka & tokb
                union = toka | tokb
                if not union:
                    continue
                jaccard = len(inter) / len(union)
                min_len = min(len(toka), len(tokb))
                contain = len(inter) / min_len if min_len else 0.0
                if not (
                    (len(inter) >= 2 and (jaccard >= 0.55 or (min_len >= 3 and contain >= 0.75)))
                    or (len(inter) == 1 and min_len == 1 and max(len(toka), len(tokb)) <= 3)
                ):
                    continue
                pair_key = (fa.path, la, fb.path, lb)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                issues.append(
                    Issue(
                        check="rule-conflict",
                        severity="warning",
                        path=fa.path,
                        line=la,
                        message=f"противоречие с {_short_label(fb, base)}:{lb} — '{rawa[:60]}' vs '{rawb[:60]}'",
                        hint="согласуй правило между файлами инструкций проекта",
                    )
                )

        # 2. Взаимоисключающие выборы инструментов/архитектуры в одном проекте
        for group_name, _ in EXCLUSIVE_GROUPS:
            ca = found_a.get(group_name)
            cb = found_b.get(group_name)
            if not ca or not cb:
                continue
            for label_a, la in ca.items():
                for label_b, lb in cb.items():
                    if label_a == label_b:
                        continue
                    if label_b in ca or label_a in cb:
                        continue
                    pair_key = (fa.path, la, fb.path, lb)
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)
                    issues.append(
                        Issue(
                            check="rule-conflict",
                            severity="warning",
                            path=fa.path,
                            line=la,
                            message=f"конфликт выбора ({group_name}): '{label_a}' здесь vs '{label_b}' в {_short_label(fb, base)}:{lb}",
                            hint="оставь единый стек в инструкциях проекта",
                        )
                    )
    return issues


def _find_exclusive_choices(doc: parse.ParsedDocument) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    section = ""
    for lineno, line in enumerate(doc.lines, start=1):
        if lineno <= doc.body_start or doc.fence_langs[lineno - 1] is not None:
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            section = stripped.lstrip("#").strip()
            continue
        raw = BULLET_RE.sub("", line).strip()
        if not raw or CONTRAST_RE.search(raw):
            continue
        ctx = f"{section} {raw}"
        if not (
            DIRECTIVE_POS_RE.search(raw)
            or re.search(
                r"(?:стек|тест|архитектур|паттерн|стил|stack|test|build|install|package|architectur|pattern|style|запуск)",
                ctx,
                re.I,
            )
            or "`" in raw
        ):
            continue
        for group_name, choices in EXCLUSIVE_GROUPS:
            matched: list[tuple[str, int]] = []
            for label, pat in choices:
                m = pat.search(raw)
                if not m:
                    continue
                pre = raw[max(0, m.start() - 25) : m.start()]
                if NEGATION_RE.search(pre):
                    continue
                matched.append((label, lineno))
            if len(matched) == 1:
                label, ln = matched[0]
                out.setdefault(group_name, {}).setdefault(label, ln)
    return out


def apply_fixes(
    files: list[ContextFile],
    texts: dict[Path, str],
    issues: list[Issue],
    root: Path,
    dry_run: bool = False,
) -> list[FixRecord]:
    """Безопасное детерминированное автоисправление очевидных проблем."""
    by_path_text: dict[Path, list[str]] = {}
    dirty: set[Path] = set()
    fixes: list[FixRecord] = []

    def get_lines(p: Path) -> list[str]:
        if p not in by_path_text:
            by_path_text[p] = texts.get(p, "").splitlines(keepends=True)
        return by_path_text[p]

    file_map = {f.path: f for f in files}

    for issue in issues:
        if not issue.line:
            continue
        lines = get_lines(issue.path)
        idx = issue.line - 1
        if idx < 0 or idx >= len(lines):
            continue
        line = lines[idx]

        # 1. skill-frontmatter: name mismatch -> ожидаемое имя каталога
        if issue.check == "skill-frontmatter" and "не совпадает с каталогом" in issue.message:
            cf = file_map.get(issue.path)
            if not cf:
                continue
            expected = cf.path.parent.name
            m = re.match(r"^(\s*name\s*:\s*)(.+?)(\r?\n?)$", line)
            if m:
                raw_val = m.group(2).strip()
                quote = raw_val[0] if len(raw_val) >= 2 and raw_val[0] == raw_val[-1] and raw_val[0] in "\"'" else ""
                new_val = f"{quote}{expected}{quote}"
                new_line = f"{m.group(1)}{new_val}{m.group(3)}"
                if new_line != line:
                    lines[idx] = new_line
                    dirty.add(issue.path)
                    fixes.append(
                        FixRecord("skill-frontmatter", issue.path, issue.line, f"name -> {expected}", applied=not dry_run)
                    )

        # 2. version-drift: прямое расхождение версии проекта с манифестом
        elif issue.check == "version-drift" and " ≠ " in issue.message:
            m_msg = re.match(r"версия в правилах (\S+) ≠ (\S+) в ", issue.message)
            if m_msg:
                old_v, new_v = m_msg.group(1), m_msg.group(2)
                if line.count(old_v) == 1:
                    lines[idx] = line.replace(old_v, new_v, 1)
                    dirty.add(issue.path)
                    fixes.append(
                        FixRecord("version-drift", issue.path, issue.line, f"{old_v} -> {new_v}", applied=not dry_run)
                    )

        # 3. dead-command / dead-path внутри скилла: `foo.py` -> `scripts/foo.py`, если файл в scripts/
        elif issue.check in {"dead-command", "dead-path"}:
            cf = file_map.get(issue.path)
            if not cf or cf.kind != "skill":
                continue
            tok = None
            if issue.check == "dead-path" and issue.message.startswith("путь не существует: "):
                tok = issue.message.removeprefix("путь не существует: ").strip()
            elif issue.check == "dead-command" and "файл скрипта не найден: " in issue.hint:
                tok = issue.hint.removeprefix("файл скрипта не найден: ").strip()
            if tok and "/" not in tok and tok.endswith(".py"):
                script_candidate = cf.path.parent / "scripts" / tok
                if script_candidate.is_file() and line.count(tok) == 1:
                    lines[idx] = line.replace(tok, f"scripts/{tok}", 1)
                    dirty.add(issue.path)
                    fixes.append(
                        FixRecord(issue.check, issue.path, issue.line, f"{tok} -> scripts/{tok}", applied=not dry_run)
                    )

    if not dry_run:
        for path in dirty:
            path.write_text("".join(by_path_text[path]), encoding="utf-8")

    return fixes


def check_empty_dirs(
    files: list[ContextFile],
    texts: dict[Path, str],
    root: Path,
    home: Path,
    cache: _ScanCache | None = None,
) -> list[Issue]:
    """Каталог существует на диске, но не содержит ни одного файла (включая вложенные)."""
    sc = cache or _ScanCache(root, home)
    issues: list[Issue] = []
    seen: set[tuple[Path, int, Path]] = set()
    for f in files:
        text = texts.get(f.path, "")
        doc = sc.doc(f.path, text)
        lines = doc.lines
        for lineno, start_col, end_col, token in parse.code_spans_with_pos(text, doc):
            if parse.parse_path_ref(token) is None:
                continue
            resolved = sc.resolve(token, f.scope)
            if not sc.is_dir(resolved) or sc.dir_has_files(resolved):
                continue
            key = (f.path, lineno, resolved)
            if key in seen:
                continue
            seen.add(key)
            line_text = lines[lineno - 1] if 0 <= lineno - 1 < len(lines) else ""
            severity = "info" if _has_create_intent(line_text, start_col, end_col) else "warning"
            issues.append(
                Issue(
                    check="empty-dir",
                    severity=severity,
                    path=f.path,
                    line=lineno,
                    message=f"каталог пуст: {token}",
                    hint=f"проверено как {resolved}",
                )
            )
    return issues


def scan(
    root: Path,
    home: Path | None = None,
    checks: Iterable[str] | None = None,
) -> tuple[list[ContextFile], list[Issue]]:
    """Полный (или выборочный) прогон проверок с единым кешем прогона."""
    from .discover import discover, load_text

    home = home or Path.home()
    root = root.resolve()
    files = discover(root)
    texts = load_text(files)
    sc = _ScanCache(root, home)
    selected = set(checks) if checks else set(CHECKS)

    issues: list[Issue] = []
    if "dead-path" in selected:
        issues += check_dead_paths(files, texts, root, home, cache=sc)
    if "dead-command" in selected:
        issues += check_dead_commands(files, texts, root, cache=sc)
    if "version-drift" in selected:
        issues += check_version_drift(files, texts, root, cache=sc)
    if "skill-frontmatter" in selected:
        issues += check_skill_frontmatter(files, texts, root=root, cache=sc)
    if "duplicate-rule" in selected:
        issues += check_duplicate_rules(files, texts, root=root, cache=sc)
    if "rule-conflict" in selected:
        issues += check_rule_conflicts(files, texts, root=root, cache=sc)
    if "empty-dir" in selected:
        issues += check_empty_dirs(files, texts, root, home, cache=sc)
    return files, sorted(issues, key=lambda i: i.sort_key())
