"""Проверки контекста агента."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import parse
from .models import ContextFile, Issue

BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
# Глаголы, после которых путь — то, что только предстоит создать
CREATE_VERBS = re.compile(
    r"(созда|создай|создайте|запиши|запишите|скопируй|скопируйте|помести|положи|"
    r"\bcreate\b|\bwrite\b|\bcopy\b|\bmkdir\b|\btouch\b|\badd file\b)",
    re.I,
)
WORD_RE = re.compile(r"[a-zа-я0-9_]+", re.I)
MAKE_TARGET_RE = re.compile(r"^([\w.-]+)\s*:", re.M)


def _nearest(scope: Path, root: Path, filename: str) -> Path | None:
    """Ближайший вверх от scope до root файл с указанным именем."""
    cur = scope
    while True:
        candidate = cur / filename
        if candidate.is_file():
            return candidate
        if cur == root or cur.parent == cur:
            return None
        cur = cur.parent


def _pyproject_version(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"^\s*version\s*=\s*[\"']([^\"']+)[\"']", text, re.M)
    return m.group(1) if m else None


def _package_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def check_dead_paths(files: list[ContextFile], texts: dict[Path, str], root: Path, home: Path) -> list[Issue]:
    """Мёртвые пути с демотированием кластеров-примеров.

    Если в одном файле подряд идут три и более относительных «мёртвых» пути
    (типичный пример структуры проекта в документации), они становятся заметками.
    """
    raw: list[dict] = []
    for f in files:
        for lineno, token in parse.code_spans(texts.get(f.path, "")):
            if not parse.looks_like_path(token):
                continue
            resolved = parse.resolve_path(token, f.scope, root, home)
            if resolved.exists():
                continue
            cleaned = parse.clean_token(token)
            lines = texts.get(f.path, "").splitlines()
            line_text = lines[lineno - 1] if 0 <= lineno - 1 < len(lines) else ""
            severity = _dead_path_severity(cleaned, resolved, home)
            if severity != "info" and CREATE_VERBS.search(line_text):
                # правило велит этот файл создать — он не «мёртвый»
                severity = "info"
            raw.append(
                {
                    "file": f.path,
                    "line": lineno,
                    "token": token,
                    "resolved": resolved,
                    "severity": severity,
                    "anchored": bool(parse.PATHISH_RE.match(cleaned)),
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
            while j + 1 < len(items) and items[j + 1]["line"] - items[i]["line"] <= 4:
                j += 1
            group = items[i : j + 1]
            if len(group) >= 3 and all(not e["anchored"] for e in group):
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


def _dead_path_severity(token: str, resolved: Path, home: Path) -> str:
    """Степень уверенности по глубине существования предка.

    Файл отсутствует в существующем каталоге — ошибка.
    Каталог отсутствует, но предок есть — предупреждение.
    Ничего вокруг не существует — заметка (другая машина, контейнер, ассет движка).
    """
    if resolved.exists():
        return "info"
    if token.startswith("~") or token.startswith("$HOME"):
        rest = token.replace("$HOME", "").lstrip("~").lstrip("/")
        if "/" not in rest:
            # файл прямо в домашнем каталоге — это пользовательский артефакт
            return "warning"
        top = rest.split("/")[0]
        if top and not (home / top).exists():
            # ~/Library на Linux, чужой инструмент, другая машина
            return "info"
    if token.startswith("/") and not token.startswith("//"):
        segs = [seg for seg in token.split("/") if seg]
        if segs and not Path("/" + segs[0]).exists():
            # верхний сегмент не существует: ассет движка, контейнер, другая ОС
            return "info"
    if token.endswith("/"):
        # упоминание каталога в прозе, а не проверяемый артефакт
        return "info"
    parent = resolved.parent
    if parent.exists():
        # относительные от текущего каталога примеры не считаем ошибкой
        if token.startswith(("./", "../")):
            return "warning"
        return "error"
    cur = parent
    while cur != cur.parent:
        cur = cur.parent
        if cur.exists():
            return "warning"
    return "info"


def check_dead_commands(files: list[ContextFile], texts: dict[Path, str], root: Path) -> list[Issue]:
    issues: list[Issue] = []
    for f in files:
        for lineno, cmd in parse.commands(texts.get(f.path, "")):
            parts = cmd.split()
            if not parts:
                continue
            runner = parts[0]
            problem = None
            severity = "warning"

            if runner in {"npm", "pnpm", "yarn", "bun"} and len(parts) >= 3 and parts[1] == "run":
                script = parts[2]
                manifest = _nearest(f.scope, root, "package.json")
                if manifest:
                    scripts = _package_json(manifest).get("scripts") or {}
                    if script not in scripts:
                        problem = f"скрипт '{script}' отсутствует в {manifest.name}"
                        if manifest.parent != f.scope:
                            severity = "info"
            elif runner == "make" and len(parts) >= 2:
                target = parts[1]
                mf = _nearest(f.scope, root, "Makefile")
                if mf:
                    targets = set(MAKE_TARGET_RE.findall(mf.read_text(encoding="utf-8", errors="replace")))
                    if target not in targets:
                        problem = f"цель '{target}' отсутствует в Makefile"
                        if mf.parent != f.scope:
                            severity = "info"
            elif runner in {"python", "python3"} and len(parts) >= 2 and parts[1].endswith(".py"):
                candidate = parse.resolve_path(parts[1], f.scope, root, root)
                if not candidate.exists():
                    problem = f"файл скрипта не найден: {parts[1]}"

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


def check_version_drift(files: list[ContextFile], texts: dict[Path, str], root: Path) -> list[Issue]:
    issues: list[Issue] = []
    for f in files:
        text = texts.get(f.path, "")
        found = parse.versions(text)
        if not found:
            continue
        pyproject = _nearest(f.scope, root, "pyproject.toml")
        package = _nearest(f.scope, root, "package.json")
        manifest = pyproject or package
        if manifest is None:
            continue
        actual = _pyproject_version(pyproject) if pyproject else str(_package_json(package).get("version") or "")
        if not actual:
            continue
        names = {f.scope.name.lower()}
        if pyproject:
            m = re.search(r"^\s*name\s*=\s*[\"']([^\"']+)[\"']", pyproject.read_text(encoding="utf-8", errors="replace"), re.M)
            if m:
                names.add(m.group(1).lower())
        if package:
            pj = _package_json(package).get("name")
            if pj:
                names.add(str(pj).lower())
        text_lower = text.lower()
        if not any(n in text_lower for n in names if n):
            continue
        lines = text.splitlines()
        for lineno, v in found:
            line = lines[lineno - 1].lower() if 0 <= lineno - 1 < len(lines) else ""
            # сравниваем только там, где версия прямо относится к проекту
            if not (any(n in line for n in names if n) or re.search(r"проект|project", line)):
                continue
            if v != actual:
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
    return issues


def check_skill_frontmatter(files: list[ContextFile], texts: dict[Path, str]) -> list[Issue]:
    issues: list[Issue] = []
    for f in files:
        if f.kind != "skill":
            continue
        # проверяем только скиллы в каталогах skills/<name>/, чужие конвенции не трогаем
        if f.path.parent.parent.name != "skills":
            continue
        text = texts.get(f.path, "")
        meta, body_start = parse.parse_frontmatter(text)
        if not meta:
            issues.append(
                Issue("skill-frontmatter", "error", f.path, "нет YAML frontmatter", line=1)
            )
            continue
        name = meta.get("name", "")
        expected = f.path.parent.name
        if name != expected:
            issues.append(
                Issue(
                    "skill-frontmatter",
                    "warning",
                    f.path,
                    f"name '{name}' не совпадает с каталогом '{expected}'",
                    line=2,
                    hint="name должен равняться имени каталога скилла",
                )
            )
        desc = meta.get("description", "")
        if not desc:
            issues.append(Issue("skill-frontmatter", "error", f.path, "пустой description", line=3))
        elif not desc.lower().startswith("use when"):
            issues.append(
                Issue(
                    "skill-frontmatter",
                    "info",
                    f.path,
                    "description не начинается с 'Use when'",
                    line=3,
                    hint="правило скиллов: только триггеры активации",
                )
            )
        for lineno, token in parse.code_spans(text):
            token = token.strip()
            if not token.startswith(("scripts/", "./scripts/")):
                continue
            rel = token.lstrip("./")
            if not (f.path.parent / rel).exists():
                issues.append(
                    Issue(
                        "skill-frontmatter",
                        "warning",
                        f.path,
                        f"битая ссылка на скрипт: {token}",
                        line=lineno,
                    )
                )
        del body_start
    return issues


def check_duplicate_rules(files: list[ContextFile], texts: dict[Path, str]) -> list[Issue]:
    seen: dict[str, list[ContextFile]] = {}
    lines_by_file: dict[Path, dict[str, int]] = {}
    for f in files:
        per_file: dict[str, int] = {}
        for lineno, line in enumerate(texts.get(f.path, "").splitlines(), start=1):
            norm = BULLET_RE.sub("", line).strip()
            if len(norm) < 25 or norm.startswith("#"):
                continue
            key = " ".join(WORD_RE.findall(norm.lower()))
            if len(key) < 12:
                continue
            per_file.setdefault(key, lineno)
            seen.setdefault(key, []).append(f)
        lines_by_file[f.path] = per_file

    issues: list[Issue] = []
    for key, holders in seen.items():
        unique = []
        seen_paths = set()
        for h in holders:
            if h.path not in seen_paths:
                seen_paths.add(h.path)
                unique.append(h)
        if len(unique) < 2:
            continue
        first = unique[0]
        lineno = lines_by_file[first.path].get(key)
        issues.append(
            Issue(
                "duplicate-rule",
                "info",
                first.path,
                f"одно и то же правило в {len(unique)} файлах: {first.path.name} и {', '.join(u.path.name for u in unique[1:3])}",
                line=lineno,
                hint="вынеси в корневой AGENTS.md, иначе копии разойдутся",
            )
        )
    return issues


def scan(root: Path, home: Path | None = None) -> tuple[list[ContextFile], list[Issue]]:
    """Полный прогон всех проверок."""
    from .discover import discover, load_text

    home = home or Path.home()
    files = discover(root)
    texts = load_text(files)
    issues: list[Issue] = []
    issues += check_dead_paths(files, texts, root, home)
    issues += check_dead_commands(files, texts, root)
    issues += check_version_drift(files, texts, root)
    issues += check_skill_frontmatter(files, texts)
    issues += check_duplicate_rules(files, texts)
    return files, sorted(issues, key=lambda i: i.sort_key())
