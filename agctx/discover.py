"""Поиск файлов контекста в дереве."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from .models import KINDS, ContextFile

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "site-packages",
    ".idea",
    "target",
    ".next",
    ".nuxt",
    ".turbo",
    ".svelte-kit",
    ".parcel-cache",
    "coverage",
    ".terraform",
    "vendor",
}

HOME_SKIP_DIRS = {
    ".local",
    ".npm",
    ".cargo",
    ".rustup",
    ".pyenv",
    ".nvm",
    "Library",
    "snap",
    ".Trash",
}


def discover(root: Path, follow_home: bool = False) -> list[ContextFile]:
    """Найти все AGENTS.md/CLAUDE.md/SKILL.md под root.

    При обходе домашнего каталога без follow_home=True пропускает тяжёлые системные кэши.
    """
    root = root.resolve()
    is_home_root = False
    if not follow_home:
        try:
            is_home_root = root == Path.home().resolve()
        except OSError:
            is_home_root = False
    found: list[ContextFile] = []
    for dirpath, dirnames, filenames in os.walk(root):
        skip = SKIP_DIRS | HOME_SKIP_DIRS if is_home_root and Path(dirpath) == root else SKIP_DIRS
        dirnames[:] = sorted(d for d in dirnames if d not in skip and not d.startswith(".cache"))
        d = Path(dirpath)
        for name in filenames:
            kind = KINDS.get(name)
            if not kind:
                continue
            found.append(ContextFile(path=d / name, kind=kind, scope=d))
    return sorted(found, key=lambda c: str(c.path))


def load_text(files: Iterable[ContextFile]) -> dict[Path, str]:
    """Прочитать содержимое, переживая битые кодировки."""
    out: dict[Path, str] = {}
    for f in files:
        try:
            out[f.path] = f.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            out[f.path] = ""
    return out
