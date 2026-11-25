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


if __name__ == "__main__":
    unittest.main()
