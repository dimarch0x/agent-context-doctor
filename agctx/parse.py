"""Разбор markdown-контекста: frontmatter, инлайн-код, команды, версии."""

from __future__ import annotations

import re
from pathlib import Path

FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n", re.S)

# `token` — инлайн-код в одинарных обратных кавычках
CODE_SPAN_RE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")

RUNNERS = (
    "npm", "pnpm", "yarn", "bun", "make", "cargo", "go", "uv", "poetry",
    "python", "python3", "pytest", "node", "deno", "bundle", "dotnet",
)

COMMAND_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(r) for r in RUNNERS)
    + r")\b(?:\s+[\w./:=@\"'§-]+)*"
)

VERSION_RE = re.compile(r"(?:version|версия|версии)\s*[:=]?\s*v?(\d+\.\d+(?:\.\d+)?)", re.I)

PATHISH_RE = re.compile(r"^(?:~|\$HOME|/|\./|\.\./)")

# Номер строки или диапазон, приклеенный к пути: file.py:12, file.py:12-30
LINE_SUFFIX_RE = re.compile(r":\d+(?:[-–]\d+)?$")
# Символ после :: и якорь после # — не часть пути
SYMBOL_SUFFIX_RE = re.compile(r"::.*$")
ANCHOR_SUFFIX_RE = re.compile(r"#.*$")

# Маркеры учебных заглушек: такие пути в документации не проверяем
PLACEHOLDER_MARKERS = (
    "path/to", "exact/path", "your_", "your-", "your/", "example", "placeholder",
    "tbd", "todo", "<", ">", "...", "…",
)


def parse_frontmatter(text: str) -> tuple[dict[str, str], int]:
    """Вернуть (frontmatter, число строк блока). Поддерживает простой key: value."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, 0
    data: dict[str, str] = {}
    for raw in m.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data, text[: m.end()].count("\n")


def code_spans(text: str) -> list[tuple[int, str]]:
    """Все инлайн-код-спаны с номерами строк (1-based)."""
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in CODE_SPAN_RE.finditer(line):
            out.append((lineno, m.group(1).strip()))
    return out


def clean_token(token: str) -> str:
    """Убрать хвосты вида `:12-30`, точки и запятые."""
    t = token.strip().strip(".,;:()[]{}\"'")
    t = ANCHOR_SUFFIX_RE.sub("", t)
    t = SYMBOL_SUFFIX_RE.sub("", t)
    t = LINE_SUFFIX_RE.sub("", t)
    return t


def is_placeholder(token: str) -> bool:
    low = token.lower()
    return any(marker in low for marker in PLACEHOLDER_MARKERS)


def is_slash_command(token: str) -> bool:
    """`/plan`, `/status` — команды агента, а не пути."""
    if not token.startswith("/"):
        return False
    if token.count("/") != 1:
        return False
    return "." not in token


def looks_like_path(token: str) -> bool:
    """Похоже ли на файловый путь, а не на команду, URL, флаг или заглушку."""
    t = clean_token(token)
    if not t or len(t) > 200:
        return False
    if "://" in t or t.startswith(("http", "www.", "mailto:")):
        return False
    if " " in t or t.startswith("-"):
        return False
    if is_slash_command(t) or is_placeholder(t):
        return False
    if any(ch in t for ch in "*?<>|$()[]{}\"'"):
        if not t.startswith("$HOME"):
            return False
    if "/" not in t and not t.startswith(("~", "$HOME")):
        return False
    if t.endswith("/"):
        return True
    if t.startswith(("~", "$HOME", "/")):
        return True
    # относительный путь: считаем путём только при наличии расширения
    return "." in t.rsplit("/", 1)[-1]


def commands(text: str) -> list[tuple[int, str]]:
    """Команды, упомянутые в тексте, с номерами строк."""
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in COMMAND_RE.finditer(line):
            out.append((lineno, m.group(0).strip()))
    return out


def versions(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in VERSION_RE.finditer(line):
            out.append((lineno, m.group(1)))
    return out


def resolve_path(token: str, scope: Path, root: Path, home: Path) -> Path:
    """Превратить токен в путь, пробуя scope, root и домашний каталог."""
    t = clean_token(token)
    if t.startswith("$HOME"):
        return home / t[len("$HOME"):].lstrip("/")
    if t.startswith("~"):
        return home / t[1:].lstrip("/")
    p = Path(t)
    if p.is_absolute():
        return p
    for base in (scope, root, home):
        candidate = base / p
        if candidate.exists():
            return candidate
    return scope / p
