"""Модели данных."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

KINDS = {"AGENTS.md": "agents", "CLAUDE.md": "claude", "SKILL.md": "skill"}

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class ContextFile:
    """Найденный файл контекста."""

    path: Path
    kind: str
    scope: Path  # каталог, которым управляет файл

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.path.name}"


@dataclass(frozen=True)
class PathRef:
    """Классифицированная ссылка на файловый путь."""

    raw: str
    normalized: str
    anchor: str  # home | abs | rel_explicit | rel_bare
    is_dir: bool


@dataclass(frozen=True)
class Issue:
    """Одна находка линтера."""

    check: str
    severity: str  # error | warning | info
    path: Path
    message: str
    line: int | None = None
    hint: str = ""

    def sort_key(self) -> tuple:
        return (SEVERITY_ORDER.get(self.severity, 9), str(self.path), self.line or 0)


@dataclass(frozen=True)
class FixRecord:
    """Запись об автоисправлении."""

    check: str
    path: Path
    line: int
    description: str
    applied: bool = True
