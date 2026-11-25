"""Разбор markdown-контекста: frontmatter, блоки кода, пути, команды, версии."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import PathRef

FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(?:(.*?)\r?\n)?---[ \t]*(?:\r?\n|\Z)", re.S)
FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})([^\n]*)$")

# `token` — инлайн-код в одинарных обратных кавычках
CODE_SPAN_RE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")

RUNNERS = (
    "npm", "pnpm", "yarn", "bun", "make", "cargo", "go", "uv", "poetry",
    "python", "python3", "pytest", "node", "deno", "bundle", "dotnet", "php",
)

PROSE_SAFE_RUNNERS = ("npm", "pnpm", "yarn", "bun", "python", "python3")

COMMAND_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(r) for r in RUNNERS)
    + r")\b(?:\s+[\w./:=@\"'*-]+)*"
)

PROSE_COMMAND_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(r) for r in PROSE_SAFE_RUNNERS)
    + r")\b(?:\s+[\w./:=@\"'*-]+)*"
)

SHELL_FENCE_LANGS = {"", "bash", "sh", "zsh", "shell", "console", "fish", "pwsh", "powershell"}

VERSION_RE = re.compile(r"(?:version|версия|версии)\s*[:=]?\s*v?(\d+\.\d+(?:\.\d+)?)", re.I)

PATHISH_RE = re.compile(r"^(?:~|\$HOME|/|\./|\.\./)")

# Номер строки или диапазон, приклеенный к пути: file.py:12, file.py:12-30
LINE_SUFFIX_RE = re.compile(r":\d+(?:[-–]\d+)?$")
# Символ после :: и якорь после # — не часть пути
SYMBOL_SUFFIX_RE = re.compile(r"::.*$")
ANCHOR_SUFFIX_RE = re.compile(r"#.*$")

# Строгие лексические ограничения для путей
EXT_RE = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,9}$")
DOTFILE_RE = re.compile(r"^\.[A-Za-z0-9][A-Za-z0-9_.-]*$")
SEMVER_LEAF_RE = re.compile(r"^v?\d+\.\d+(?:\.\d+)?$")
SLASH_CMD_RE = re.compile(r"^/[A-Za-z0-9_-]+$")
SPECIAL_BASENAMES = {
    "Makefile", "Dockerfile", "Vagrantfile", "Procfile", "Justfile", "Rakefile", "Gemfile", "LICENSE", "README",
}

# Маркеры учебных заглушек: такие пути в документации не проверяем
PLACEHOLDER_MARKERS = (
    "path/to", "exact/path", "your_", "your-", "your/", "example", "placeholder",
    "tbd", "todo", "<", ">", "...", "…",
)

YAML_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:(?:\s+(.*)|\s*)$")


@dataclass(frozen=True)
class ParsedDocument:
    """Однократно разобранная структура markdown-файла."""

    lines: list[str]
    frontmatter: dict[str, Any]
    body_start: int
    key_lines: dict[str, int]
    fence_langs: list[str | None]


def _strip_yaml_comment(raw: str) -> str:
    in_single = False
    in_double = False
    for idx, ch in enumerate(raw):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single and (idx == 0 or raw[idx - 1] != "\\"):
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if idx == 0 or raw[idx - 1].isspace():
                return raw[:idx].rstrip()
    return raw.strip()


def _unquote_yaml(val: str) -> str:
    val = val.strip()
    if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
        inner = val[1:-1]
        return inner.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
    if len(val) >= 2 and val[0] == "'" and val[-1] == "'":
        return val[1:-1].replace("''", "'")
    return val


def _parse_flow_list(val: str) -> list[str]:
    inner = val[1:-1].strip()
    if not inner:
        return []
    items: list[str] = []
    cur: list[str] = []
    in_single = False
    in_double = False
    for idx, ch in enumerate(inner):
        if ch == "'" and not in_double:
            in_single = not in_single
            cur.append(ch)
        elif ch == '"' and not in_single and (idx == 0 or inner[idx - 1] != "\\"):
            in_double = not in_double
            cur.append(ch)
        elif ch == "," and not in_single and not in_double:
            item = _unquote_yaml("".join(cur))
            if item:
                items.append(item)
            cur = []
        else:
            cur.append(ch)
    tail = _unquote_yaml("".join(cur))
    if tail:
        items.append(tail)
    return items


def _next_significant_line(lines: list[str], idx: int) -> int | None:
    for i in range(idx, len(lines)):
        s = lines[i].strip()
        if s and not s.startswith("#"):
            return i
    return None


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _read_block_scalar(lines: list[str], start: int, key_indent: int, style: str) -> tuple[str, int]:
    collected: list[str] = []
    idx = start
    block_indent: int | None = None
    while idx < len(lines):
        raw = lines[idx]
        if not raw.strip():
            collected.append("")
            idx += 1
            continue
        ind = _indent_of(raw)
        if ind <= key_indent:
            break
        if block_indent is None:
            block_indent = ind
        collected.append(raw[min(block_indent, len(raw)) :])
        idx += 1
    while collected and collected[-1] == "":
        collected.pop()
    if not collected:
        return "", idx
    if style.startswith("|"):
        return "\n".join(collected), idx
    # Folded scalar `>`: join non-empty lines in paragraph with spaces
    paragraphs: list[str] = []
    buf: list[str] = []
    for part in collected:
        if part == "":
            if buf:
                paragraphs.append(" ".join(buf))
                buf = []
        else:
            buf.append(part.strip())
    if buf:
        paragraphs.append(" ".join(buf))
    return "\n".join(paragraphs), idx


def _parse_yaml_list(lines: list[str], start: int, base_indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    idx = start
    while idx < len(lines):
        raw = lines[idx]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            idx += 1
            continue
        ind = _indent_of(raw)
        if ind < base_indent or not (stripped == "-" or stripped.startswith("- ")):
            break
        rest = _strip_yaml_comment(stripped[1:].strip()) if len(stripped) > 1 else ""
        idx += 1
        if not rest:
            nxt = _next_significant_line(lines, idx)
            if nxt is not None and _indent_of(lines[nxt]) > ind:
                nxt_s = lines[nxt].strip()
                if nxt_s == "-" or nxt_s.startswith("- "):
                    sub, idx = _parse_yaml_list(lines, nxt, _indent_of(lines[nxt]))
                else:
                    sub, idx, _ = _parse_yaml_mapping(lines, nxt, _indent_of(lines[nxt]), 0)
                items.append(sub)
            else:
                items.append("")
            continue
        m = YAML_KEY_RE.match(rest)
        if m:
            item_dict: dict[str, Any] = {m.group(1): _unquote_yaml(m.group(2) or "")}
            nxt = _next_significant_line(lines, idx)
            if nxt is not None and _indent_of(lines[nxt]) > ind:
                more, idx, _ = _parse_yaml_mapping(lines, nxt, _indent_of(lines[nxt]), 0)
                item_dict.update(more)
            items.append(item_dict)
        elif rest.startswith("[") and rest.endswith("]"):
            items.append(_parse_flow_list(rest))
        else:
            items.append(_unquote_yaml(rest))
    return items, idx


def _parse_yaml_mapping(
    lines: list[str], start: int, base_indent: int, line_offset: int
) -> tuple[dict[str, Any], int, dict[str, int]]:
    data: dict[str, Any] = {}
    key_lines: dict[str, int] = {}
    idx = start
    while idx < len(lines):
        raw = lines[idx]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            idx += 1
            continue
        ind = _indent_of(raw)
        if ind < base_indent:
            break
        if ind > base_indent:
            idx += 1
            continue
        m = YAML_KEY_RE.match(stripped)
        if not m:
            idx += 1
            continue
        key = m.group(1)
        key_lines.setdefault(key, line_offset + idx)
        raw_val = _strip_yaml_comment(m.group(2) or "")
        idx += 1
        if raw_val in {"|", "|-", "|+", ">", ">-", ">+"}:
            val, idx = _read_block_scalar(lines, idx, ind, raw_val)
            data[key] = val
        elif raw_val == "":
            nxt = _next_significant_line(lines, idx)
            if nxt is not None and _indent_of(lines[nxt]) > ind:
                nxt_s = lines[nxt].strip()
                nxt_ind = _indent_of(lines[nxt])
                if nxt_s == "-" or nxt_s.startswith("- "):
                    data[key], idx = _parse_yaml_list(lines, nxt, nxt_ind)
                else:
                    sub, idx, _ = _parse_yaml_mapping(lines, nxt, nxt_ind, line_offset)
                    data[key] = sub
            else:
                data[key] = ""
        elif raw_val.startswith("[") and raw_val.endswith("]"):
            data[key] = _parse_flow_list(raw_val)
        else:
            parts = [_unquote_yaml(raw_val)]
            # Support multiline plain scalars indented under key
            if not (raw_val.startswith(('"', "'")) and raw_val.endswith(('"', "'"))):
                while idx < len(lines):
                    cont = lines[idx]
                    cs = cont.strip()
                    if not cs or cs.startswith("#"):
                        break
                    if _indent_of(cont) <= ind or YAML_KEY_RE.match(cs) or cs.startswith("- "):
                        break
                    parts.append(_strip_yaml_comment(cs))
                    idx += 1
            data[key] = " ".join(p for p in parts if p)
    return data, idx, key_lines


def parse_frontmatter_info(text: str) -> tuple[dict[str, Any], int, dict[str, int]]:
    """Разобрать YAML frontmatter: вернуть (словарь, число строк блока, номера строк ключей)."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, 0, {}
    body_start = text[: m.end()].count("\n")
    block = m.group(1)
    if not block:
        return {}, body_start, {}
    lines = block.splitlines()
    first = _next_significant_line(lines, 0)
    if first is None:
        return {}, body_start, {}
    data, _, key_lines = _parse_yaml_mapping(lines, first, _indent_of(lines[first]), line_offset=2)
    return data, body_start, key_lines


def parse_frontmatter(text: str) -> tuple[dict[str, Any], int]:
    """Вернуть (frontmatter, число строк блока). Поддерживает вложенные ключи, списки и multiline."""
    data, body_start, _ = parse_frontmatter_info(text)
    return data, body_start


def fenced_blocks(lines: list[str], body_start: int = 0) -> list[str | None]:
    """Для каждой строки вернуть язык открытого code-fence ('' без тега) или None вне блока."""
    out: list[str | None] = [None] * len(lines)
    fence_char = ""
    fence_len = 0
    fence_lang: str | None = None
    for idx, line in enumerate(lines):
        if idx < body_start:
            continue
        m = FENCE_OPEN_RE.match(line)
        if fence_lang is None:
            if m:
                marker = m.group(1)
                fence_char = marker[0]
                fence_len = len(marker)
                info = m.group(2).strip().split()
                fence_lang = info[0].lower() if info else ""
                out[idx] = fence_lang
        else:
            out[idx] = fence_lang
            stripped = line.strip()
            if (
                len(stripped) >= fence_len
                and set(stripped) == {fence_char}
            ):
                fence_lang = None
                fence_char = ""
                fence_len = 0
    return out


def parse_document(text: str) -> ParsedDocument:
    """Разобрать markdown-документ за один проход."""
    lines = text.splitlines()
    fm, body_start, key_lines = parse_frontmatter_info(text)
    fence_langs = fenced_blocks(lines, body_start)
    return ParsedDocument(
        lines=lines,
        frontmatter=fm,
        body_start=body_start,
        key_lines=key_lines,
        fence_langs=fence_langs,
    )


def code_spans_with_pos(
    text: str, doc: ParsedDocument | None = None
) -> list[tuple[int, int, int, str]]:
    """Инлайн-код-спаны с (lineno, start_col, end_col, token) вне frontmatter и fenced-блоков."""
    d = doc or parse_document(text)
    out: list[tuple[int, int, int, str]] = []
    for lineno, line in enumerate(d.lines, start=1):
        if lineno <= d.body_start or d.fence_langs[lineno - 1] is not None:
            continue
        for m in CODE_SPAN_RE.finditer(line):
            out.append((lineno, m.start(), m.end(), m.group(1).strip()))
    return out


def code_spans(text: str, doc: ParsedDocument | None = None) -> list[tuple[int, str]]:
    """Все инлайн-код-спаны вне frontmatter и fenced-блоков с номерами строк (1-based)."""
    return [(lineno, tok) for lineno, _, _, tok in code_spans_with_pos(text, doc)]


def clean_token(token: str) -> str:
    """Убрать пунктуацию по краям и хвосты вида `:12-30`, `#anchor`, `::sym`, не ломая `./`, `../`, `.dir`."""
    t = token.strip().lstrip(",;:()[]{}\"'").rstrip(",;:()[]{}\"'")
    if t.endswith(".") and not t.endswith(("..", "/.")):
        t = t[:-1]
    t = ANCHOR_SUFFIX_RE.sub("", t)
    t = SYMBOL_SUFFIX_RE.sub("", t)
    t = LINE_SUFFIX_RE.sub("", t)
    return t


def is_placeholder(token: str) -> bool:
    low = token.lower()
    return any(marker in low for marker in PLACEHOLDER_MARKERS)


def is_slash_command(token: str) -> bool:
    """`/plan`, `/status` — команды агента, а не пути."""
    return bool(SLASH_CMD_RE.match(token))


def parse_path_ref(token: str) -> PathRef | None:
    """Формальный разбор токена в PathRef либо None, если это не файловый путь."""
    t = clean_token(token)
    if not t or len(t) > 200:
        return None
    if "://" in t or t.startswith(("http:", "https:", "www.", "mailto:")):
        return None
    if " " in t or t.startswith("-"):
        return None
    if is_slash_command(t) or is_placeholder(t):
        return None
    if any(ch in t for ch in "*?<>|()[]{}\"'"):
        return None
    if "$" in t and not t.startswith("$HOME/"):
        return None

    if t.startswith(("~/", "$HOME/")):
        anchor = "home"
    elif t.startswith("/") and not t.startswith("//"):
        anchor = "abs"
    elif t.startswith(("./", "../")) or (t.startswith(".") and "/" in t):
        anchor = "rel_explicit"
    elif "/" in t:
        anchor = "rel_bare"
    else:
        return None

    is_dir = t.endswith("/")
    if is_dir:
        return PathRef(raw=token, normalized=t, anchor=anchor, is_dir=True)

    leaf = t.rsplit("/", 1)[-1]
    if not leaf or SEMVER_LEAF_RE.match(leaf):
        return None
    if anchor in {"rel_bare", "rel_explicit"}:
        if not (EXT_RE.search(leaf) or DOTFILE_RE.match(leaf) or leaf in SPECIAL_BASENAMES):
            return None
    return PathRef(raw=token, normalized=t, anchor=anchor, is_dir=False)


def looks_like_path(token: str) -> bool:
    """Похоже ли на файловый путь, а не на команду, URL, флаг или заглушку."""
    return parse_path_ref(token) is not None


def _clean_command_str(raw: str) -> str:
    return raw.strip().rstrip(",;:\"')")


def commands(text: str, doc: ParsedDocument | None = None) -> list[tuple[int, str]]:
    """Команды из инлайн-кода и shell-блоков (и однозначные runner-строки в прозе)."""
    d = doc or parse_document(text)
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(d.lines, start=1):
        if lineno <= d.body_start:
            continue
        fence = d.fence_langs[lineno - 1]
        if fence is not None:
            if fence in SHELL_FENCE_LANGS:
                # Внутри shell code-block берём команды напрямую (снимая префикс '$ ')
                cleaned_line = re.sub(r"^\s*\$\s+", "", line)
                for m in COMMAND_RE.finditer(cleaned_line):
                    cmd = _clean_command_str(m.group(0))
                    if cmd:
                        out.append((lineno, cmd))
            continue

        # Вне fenced-блока: внутри `...` извлекаем все runners, в голой прозе — только однозначные
        seen_spans: list[tuple[int, int]] = []
        for span in CODE_SPAN_RE.finditer(line):
            seen_spans.append(span.span())
            inner = span.group(1)
            for m in COMMAND_RE.finditer(inner):
                cmd = _clean_command_str(m.group(0))
                if cmd:
                    out.append((lineno, cmd))
        for m in PROSE_COMMAND_RE.finditer(line):
            pos = m.start()
            if any(a <= pos < b for a, b in seen_spans):
                continue
            cmd = _clean_command_str(m.group(0))
            if cmd:
                out.append((lineno, cmd))
    return out


def versions(text: str, doc: ParsedDocument | None = None) -> list[tuple[int, str]]:
    d = doc or parse_document(text)
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(d.lines, start=1):
        if lineno <= d.body_start or d.fence_langs[lineno - 1] is not None:
            continue
        for m in VERSION_RE.finditer(line):
            out.append((lineno, m.group(1)))
    return out


def resolve_path(
    token: str,
    scope: Path,
    root: Path,
    home: Path,
    extra_bases: tuple[Path, ...] = (),
) -> Path:
    """Превратить токен в путь, пробуя scope, указанные базовые каталоги и root."""
    t = clean_token(token)
    if t.startswith("$HOME"):
        rel = t.removeprefix("$HOME").lstrip("/")
        return Path(os.path.normpath(home / rel))
    if t.startswith("~"):
        rel = t.removeprefix("~").lstrip("/")
        return Path(os.path.normpath(home / rel))
    p = Path(t)
    if p.is_absolute():
        return Path(os.path.normpath(p))
    candidates: list[Path] = [scope, *extra_bases, root]
    seen: set[Path] = set()
    for base in candidates:
        if base in seen:
            continue
        seen.add(base)
        candidate = Path(os.path.normpath(base / p))
        if candidate.exists():
            return candidate
    return Path(os.path.normpath(scope / p))