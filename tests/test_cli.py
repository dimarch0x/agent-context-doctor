import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agctx.cli import main


def write(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(list(args))
        return code, buf.getvalue()

    def test_clean_tree_returns_zero(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "AGENTS.md", "# Правила\n\nНичего особенного.\n")
            code, out = self.run_cli("scan", str(root))
            self.assertEqual(code, 0)
            self.assertIn("Файлов контекста: 1", out)

    def test_error_returns_two(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "skills/bad/SKILL.md", "# без фронтматтера\n")
            code, _ = self.run_cli("scan", str(root))
            self.assertEqual(code, 2)

    def test_warning_only_returns_one(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "package.json", json.dumps({"name": "demo", "version": "1.0.0", "scripts": {}}))
            write(root, "AGENTS.md", "Запускай `npm run test`\n")
            code, out = self.run_cli("scan", str(root))
            self.assertEqual(code, 1)
            self.assertIn("WARN", out)

    def test_json_output_is_parseable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "sub/keep.txt", "x")
            write(root, "AGENTS.md", "Ставь в `sub/missing.sh`\n")
            code, out = self.run_cli("scan", str(root), "--json")
            self.assertEqual(code, 2)
            payload = json.loads(out)
            self.assertEqual(payload["summary"]["error"], 1)
            self.assertTrue(payload["issues"][0]["message"].startswith("путь не существует"))

    def test_version_is_semver(self):
        from agctx import __version__

        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

    def test_missing_path_returns_three(self):
        code, _ = self.run_cli("scan", "/nonexistent/place/at/all")
        self.assertEqual(code, 3)

    def test_check_filter_limits_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "sub/keep.txt", "x")
            write(root, "AGENTS.md", "Ставь в `sub/missing.sh`\n")
            code, out = self.run_cli("scan", str(root), "--check", "duplicate-rule")
            self.assertEqual(code, 0)
            self.assertIn("ошибок: 0", out)

    def test_fix_dry_run_and_apply(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "pyproject.toml", '[project]\nname = "demo"\nversion = "2.0.0"\n')
            write(root, "AGENTS.md", "Проект demo, версия 1.0.0\n")
            skill = write(root, "skills/demo/SKILL.md", "---\nname: wrong\ndescription: Use when testing\n---\n")

            code_dry, out_dry = self.run_cli("scan", str(root), "--fix", "--dry-run")
            self.assertEqual(code_dry, 1)
            self.assertIn("FIX (dry-run)", out_dry)
            self.assertIn("name: wrong", skill.read_text(encoding="utf-8"))

            code_fix, out_fix = self.run_cli("scan", str(root), "--fix", "--json")
            self.assertEqual(code_fix, 0)
            payload = json.loads(out_fix)
            self.assertEqual(len(payload["fixes"]), 2)
            self.assertIn("name: demo", skill.read_text(encoding="utf-8"))
            self.assertIn("2.0.0", (root / "AGENTS.md").read_text(encoding="utf-8"))

    def test_check_rule_conflict_filter(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "AGENTS.md", "## Стек\n- Используем uv для зависимостей.\n")
            write(root, "CLAUDE.md", "## Стек\n- Используем pip для зависимостей.\n")
            code, out = self.run_cli("scan", str(root), "--check", "rule-conflict")
            self.assertEqual(code, 1)
            self.assertIn("rule-conflict", out)

    def test_check_empty_dir_filter(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "templates").mkdir()
            write(root, "AGENTS.md", "Копируй шаблоны из `templates/`.\n")
            code, out = self.run_cli("scan", str(root), "--check", "empty-dir")
            self.assertEqual(code, 1)
            self.assertIn("empty-dir", out)


if __name__ == "__main__":
    unittest.main()
