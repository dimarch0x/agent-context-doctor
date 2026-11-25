"""CLI: agent-context-doctor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .checks import scan
from .models import Issue

CHECKS = ["dead-path", "dead-command", "version-drift", "skill-frontmatter", "duplicate-rule"]
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _format_issue(i: Issue, root: Path) -> str:
    try:
        rel = i.path.relative_to(root)
    except ValueError:
        rel = i.path
    where = f"{rel}:{i.line}" if i.line else str(rel)
    mark = {"error": "ERR ", "warning": "WARN", "info": "INFO"}[i.severity]
    line = f"{mark} [{i.check}] {where} — {i.message}"
    if i.hint:
        line += f"\n         ↳ {i.hint}"
    return line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agctx",
        description="Линтер файлов контекста ИИ-агентов: AGENTS.md, CLAUDE.md, SKILL.md.",
    )
    parser.add_argument("--version", action="version", version=f"agctx {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="проверить дерево и вывести находки")
    p_scan.add_argument("path", nargs="?", default=".", help="корень обхода (по умолчанию .)")
    p_scan.add_argument("--json", action="store_true", help="машинный вывод")
    p_scan.add_argument("--check", choices=CHECKS, action="append", help="только указанные проверки")
    p_scan.add_argument("--quiet", action="store_true", help="только итоговая строка")
    p_scan.add_argument("--no-info", action="store_true", help="скрыть info-находки")

    args = parser.parse_args(argv)

    root = Path(args.path).expanduser().resolve()
    if not root.exists():
        print(f"agctx: путь не найден: {root}", file=sys.stderr)
        return 3

    files, issues = scan(root)

    if args.check:
        issues = [i for i in issues if i.check in set(args.check)]
    if args.no_info:
        issues = [i for i in issues if i.severity != "info"]

    counts = {s: sum(1 for i in issues if i.severity == s) for s in ("error", "warning", "info")}

    if args.json:
        payload = {
            "root": str(root),
            "files": [{"path": str(f.path), "kind": f.kind} for f in files],
            "summary": counts,
            "issues": [
                {
                    "check": i.check,
                    "severity": i.severity,
                    "path": str(i.path),
                    "line": i.line,
                    "message": i.message,
                    "hint": i.hint,
                }
                for i in issues
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not args.quiet:
            for i in issues:
                print(_format_issue(i, root))
            if issues:
                print()
        print(
            f"Файлов контекста: {len(files)} | ошибок: {counts['error']} | "
            f"предупреждений: {counts['warning']} | заметок: {counts['info']}"
        )

    if counts["error"]:
        return 2
    if counts["warning"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
