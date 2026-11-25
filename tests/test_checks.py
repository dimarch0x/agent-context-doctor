import json
import tempfile
import unittest
from pathlib import Path

from agctx.checks import scan


def write(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


class DeadPathTests(unittest.TestCase):
    def test_home_path_with_unknown_top_is_info(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "AGENTS.md", "Ставь в `~/definitely/not/here.sh`\n")
            _, issues = scan(root)
            self.assertTrue(any(i.check == "dead-path" and i.severity == "info" for i in issues))

    def test_home_path_inside_existing_dir_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / "work").mkdir()
            write(home, "AGENTS.md", "Ставь в `~/work/missing.sh`\n")
            _, issues = scan(home, home=home)
            self.assertTrue(any(i.check == "dead-path" and i.severity == "error" for i in issues))

    def test_existing_path_is_clean(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "src/real.py", "print(1)\n")
            write(root, "AGENTS.md", "Код в `src/real.py`\n")
            _, issues = scan(root)
            self.assertFalse([i for i in issues if i.check == "dead-path"])


class ExampleClusterTests(unittest.TestCase):
    def test_example_tree_cluster_becomes_info(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "AGENTS.md", "Структура:\n\n`lib/a.ts`\n`lib/b.ts`\n`lib/c.ts`\n")
            _, issues = scan(root)
            dead = [i for i in issues if i.check == "dead-path"]
            self.assertEqual(len(dead), 3)
            self.assertTrue(all(i.severity == "info" for i in dead))


class DeadCommandTests(unittest.TestCase):
    def test_missing_npm_script_warns(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "package.json", json.dumps({"name": "demo", "version": "1.0.0", "scripts": {"build": "tsc"}}))
            write(root, "AGENTS.md", "Запускай `npm run test`\n")
            _, issues = scan(root)
            self.assertTrue(any(i.check == "dead-command" and "test" in i.hint for i in issues))

    def test_existing_script_is_clean(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "package.json", json.dumps({"name": "demo", "version": "1.0.0", "scripts": {"build": "tsc"}}))
            write(root, "AGENTS.md", "Запускай `npm run build`\n")
            _, issues = scan(root)
            self.assertFalse([i for i in issues if i.check == "dead-command"])


class VersionDriftTests(unittest.TestCase):
    def test_mismatch_warns(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "pyproject.toml", '[project]\nname = "demo"\nversion = "1.2.0"\n')
            write(root, "AGENTS.md", "Проект demo, версия 1.0.0\n")
            _, issues = scan(root)
            drift = [i for i in issues if i.check == "version-drift"]
            self.assertEqual(len(drift), 1)
            self.assertIn("1.2.0", drift[0].message)

    def test_match_is_clean(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "pyproject.toml", '[project]\nname = "demo"\nversion = "1.2.0"\n')
            write(root, "AGENTS.md", "Проект demo, версия 1.2.0\n")
            _, issues = scan(root)
            self.assertFalse([i for i in issues if i.check == "version-drift"])


class SkillFrontmatterTests(unittest.TestCase):
    def test_name_mismatch_warns(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "skills/wrong-name/SKILL.md", "---\nname: other\ndescription: Use when x\n---\n")
            _, issues = scan(root)
            warns = [i for i in issues if i.check == "skill-frontmatter" and i.severity == "warning"]
            self.assertTrue(any("не совпадает" in i.message for i in warns))

    def test_missing_frontmatter_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "skills/demo/SKILL.md", "# без фронтматтера\n")
            _, issues = scan(root)
            self.assertTrue(any(i.check == "skill-frontmatter" and i.severity == "error" for i in issues))

    def test_bad_description_warns(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "skills/demo/SKILL.md", "---\nname: demo\ndescription: Cool skill\n---\n")
            _, issues = scan(root)
            notes = [i for i in issues if i.check == "skill-frontmatter" and i.severity == "info"]
            self.assertEqual(len(notes), 1)

    def test_broken_script_link_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "skills/demo/SKILL.md", "---\nname: demo\ndescription: Use when x\n---\n\nЗапусти `scripts/missing.py`\n")
            _, issues = scan(root)
            self.assertTrue(any("scripts/missing.py" in i.message for i in issues))

    def test_valid_skill_is_clean(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "skills/demo/SKILL.md", "---\nname: demo\ndescription: Use when x\n---\n")
            write(root, "skills/demo/scripts/run.py", "print(1)\n")
            _, issues = scan(root)
            self.assertFalse([i for i in issues if i.check == "skill-frontmatter"])


class DuplicateRuleTests(unittest.TestCase):
    def test_same_rule_in_two_files_is_info(self):
        rule = "- Всегда запускай линтер перед коммитом и не пуши без зелёного теста\n"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "AGENTS.md", rule)
            write(root, "sub/AGENTS.md", rule)
            _, issues = scan(root)
            self.assertTrue(any(i.check == "duplicate-rule" for i in issues))


if __name__ == "__main__":
    unittest.main()
