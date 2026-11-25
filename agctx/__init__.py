"""Agent Context Doctor — линтер файлов контекста ИИ-агентов.

Проверяет AGENTS.md, CLAUDE.md, GEMINI.md и SKILL.md против реального репозитория:
мёртвые пути, несуществующие команды, расхождение версий, битый frontmatter скиллов.
"""

from importlib.metadata import PackageNotFoundError, version as _dist_version

try:
    __version__ = _dist_version("agctx")
except PackageNotFoundError:  # запуск из исходников без установки
    __version__ = "0.2.0"
