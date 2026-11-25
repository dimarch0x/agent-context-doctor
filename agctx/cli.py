"""CLI: agent-context-doctor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .checks import CHECKS, apply_fixes, scan
from .discover import load_text
from .models import FixRecord, Issue


def _rel_path(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        return path


def _format_issue(i: Issue, root: Path) -> str:
    rel = _rel_path(i.path, root)
    where = f"{rel}:{i.line}" if i.line else str(rel)
    mark = {"error": "ERR ", "warning": "WARN", "info": "INFO"}[i.severity]
    line = f"{mark} [{i.check}] {where} — {i.message}"
    if i.hint:
        line += f"\n         ↳ {i.hint}"
    return line


def _format_fix(f: FixRecord, root: Path) -> str:
    rel = _rel_path(f.path, root)
    prefix = "FIX (dry-run)" if not f.applied else "FIX "
    return f"{prefix} [{f.check}] {rel}:{f.line} — {f.description}"


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
    p_scan.add_argument("--check", choices=list(CHECKS), action="append", help="только указанные проверки")
    p_scan.add_argument("--quiet", action="store_true", help="только итоговая строка")
    p_scan.add_argument("--no-info", action="store_true", help="скрыть info-находки")
    p_scan.add_argument("--fix", action="store_true", help="безопасно исправить детерминированные проблемы")
    p_scan.add_argument("--dry-run", action="store_true", help="показать исправления от --fix без записи")

    args = parser.parse_args(argv)

    root = Path(args.path).expanduser().resolve()
    if not root.exists():
        print(f"agctx: путь не найден: {root}", file=sys.stderr)
        return 3

    files, issues = scan(root, checks=args.check)

    fixes: list[FixRecord] = []
    if args.fix:
        texts = load_text(files)
        fixes = apply_fixes(files, texts, issues, root, dry_run=args.dry_run)
        if fixes and not args.dry_run:
            files, issues = scan(root, checks=args.check)

    if args.no_info:
        issues = [i for i in issues if i.severity != "info"]

    counts = {s: sum(1 for i in issues if i.severity == s) for s in ("error", "warning", "info")}

    if args.json:
        payload: dict[str, object] = {
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
        if args.fix:
            payload["fixes"] = [
                {
                    "check": fx.check,
                    "path": str(fx.path),
                    "line": fx.line,
                    "description": fx.description,
                    "applied": fx.applied,
                }
                for fx in fixes
            ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not args.quiet:
            for fx in fixes:
                print(_format_fix(fx, root))
            for i in issues:
                print(_format_issue(i, root))
            if fixes or issues:
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
