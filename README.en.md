**English** | [Русский](README.md)

# Agent Context Doctor

[![CI](https://github.com/dimarch0x/agent-context-doctor/actions/workflows/ci.yml/badge.svg)](https://github.com/dimarch0x/agent-context-doctor/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A zero-dependency Python CLI linter for AI coding agent context files (`AGENTS.md`,
`CLAUDE.md`, `GEMINI.md`, `SKILL.md`, `.agents/rules/*.md`, `.claude/rules/*.md`).
It validates instructions against the actual repository on disk and surfaces stale paths,
broken commands, version drift, malformed skill metadata, duplicate or conflicting rules,
and empty directories.

Requires Python 3.10+ (stdlib only).

## Why

In [Xu & Wu (2026, arXiv:2610.30571)](https://arxiv.org/abs/2610.30571), coding agents
followed stale instruction files over real repository code in 90.1% of decisions (Claude Code
explicitly noticed the conflict in 79 out of 80 runs yet still obeyed the outdated rule).
Across 441 repositories studied in [Denisov-Blanch et al. (ASE 2026, arXiv:2608.25241)](https://arxiv.org/abs/2608.25241),
73.8% of committed AI configuration files were created once and never modified again.
The bottleneck isn't the model — it's that nobody lints the instructions.

## Checks

| Check | What it catches | Default severity |
|---|---|---|
| `dead-path` | inline-code path missing on disk | `error` if parent dir exists |
| `dead-command` | missing `npm/pnpm/yarn/bun run X`, `make X`, `python3/php <script>` | `warning` |
| `version-drift` | version mismatch vs `pyproject.toml`, `package.json`, or sibling context files | `warning` |
| `skill-frontmatter` | missing YAML frontmatter, `name` ≠ directory, broken `scripts/` links | `error` / `warning` |
| `duplicate-rule` | identical rule repeated across multiple context files | `info` |
| `rule-conflict` | semantic contradictions across project rules (polarity & conflicting stacks) | `warning` |
| `empty-dir` | referenced directory exists on disk but contains 0 files | `warning` |

Severity is derived from filesystem evidence rather than ignore lists: a missing file inside
an existing directory is an `error`; a missing subtree with an existing ancestor is a `warning`;
non-existent foreign roots, paths preceded by creation verbs, or clusters of non-existent example
paths degrade to `info`.

## Install & Usage

```bash
pip install .                        # or run directly: python3 -m agctx
python3 -m agctx scan .              # scan current repository
python3 -m agctx scan ~/skills       # scan a skill collection
python3 -m agctx scan . --json       # machine-readable JSON output
python3 -m agctx scan . --check dead-path --check skill-frontmatter
python3 -m agctx scan . --no-info
python3 -m agctx scan . --fix --dry-run  # preview safe deterministic fixes
python3 -m agctx scan . --fix            # apply safe deterministic fixes
```

Exit codes (CI-friendly): `0` clean, `1` warnings only, `2` errors present, `3` path not found.

## Live Corpus Benchmarks (Nov 2026)

| Corpus | Files | Errors | Warnings | Info | Time |
|---|---|---|---|---|---|
| Personal skill pack | 16 | 0 | 9 | 36 | ~0.15s |
| Large monorepo (`hermes-agent`) | 223 | 13 | 189 | 554 | ~1.0s |
| Multi-project workspace (`rules/` + skills) | 96 | 0 | 3 | 67 | ~0.15s |

Before evidence-based severity tuning, `hermes-agent` produced 188 errors and 568 warnings
(mostly API routes, architecture tree examples, and paths from other machines).

## Tests

```bash
python3 -m unittest discover -s tests -t . -v
```

59 tests covering the indentation-aware YAML frontmatter parser, formal path grammar, code
fence handling, all 7 checks (including `rule-conflict`, `empty-dir`, `GEMINI.md`, and
`rules/*.md`), example clusters, `--fix`, exit codes, and JSON output.

## Handing Off to an Agent

`agctx` is built for a *scan → triage → approve → fix* loop. `--fix` safely touches only
three deterministic cases (skill `name` mismatch, explicit `version-drift` on single-version
lines, and `foo.py` → `scripts/foo.py` when present in `scripts/`); everything else requires
human confirmation.

Prompt template for any shell-capable agent (Claude Code, Antigravity, Pi, DSH, Hermes):

```text
You are auditing AI agent context files with a linter and fixing only confirmed issues.

TOOL
Path: <path to agent-context-doctor>
Run from its directory:
  python3 -m agctx scan <target path> --json     # machine-readable report
  python3 -m agctx scan <path> --fix --dry-run    # preview safe fixes
  python3 -m agctx scan <path> --fix              # apply safe fixes
  python3 -m unittest discover -s tests -t .      # linter test suite

WORKFLOW
1. Run scan and report the summary (errors, warnings, info).
2. Classify each finding:
   - confirmed broken (missing path in existing dir, dead command, conflicting rules, empty source dir);
   - needs user decision (runtime-generated file, foreign machine path, illustrative example);
   - heuristic noise.
3. Propose minimal diffs for confirmed bugs with a one-line rationale each.
4. Do NOT edit anything until I approve.
5. After applying approved fixes, re-run scan, report before/after counts, and run the linter tests.

CONSTRAINTS
- Do not modify linter code or tests to silence findings.
- Do not create files in $HOME or guess missing paths.
- Do not make unrelated edits or git commits without explicit approval.
```

## Limitations

- Path checks inspect inline backtick spans, not plain prose or fenced code blocks.
- `rule-conflict` relies on deterministic polarity and mutually-exclusive stack heuristics (no LLM).
- Caching is in-memory per scan (no persistent cross-run disk cache).

## License

[MIT](LICENSE)
