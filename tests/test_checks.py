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

    def test_relative_dot_slash_in_existing_dir_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            write(root, "AGENTS.md", "Смотри `./src/missing.py`\n")
            _, issues = scan(root)
            dead = [i for i in issues if i.check == "dead-path"]
            self.assertEqual(len(dead), 1)
            self.assertEqual(dead[0].severity, "error")
            self.assertTrue(dead[0].hint.endswith("src/missing.py"))

    def test_copy_source_before_destination_is_not_downgraded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            write(root, "AGENTS.md", "Взять `src/missing.md` и положить в корень проекта.\n")
            _, issues = scan(root)
            dead = [i for i in issues if i.check == "dead-path"]
            self.assertEqual(len(dead), 1)
            self.assertEqual(dead[0].severity, "error")

    def test_negated_create_is_not_downgraded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            write(root, "AGENTS.md", "Не создавай `src/missing.py`.\n")
            _, issues = scan(root)
            dead = [i for i in issues if i.check == "dead-path"]
            self.assertEqual(dead[0].severity, "error")


class ExampleClusterTests(unittest.TestCase):
    def test_example_tree_cluster_becomes_info(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "AGENTS.md", "Структура:\n\n`lib/a.ts`\n`lib/b.ts`\n`lib/c.ts`\n")
            _, issues = scan(root)
            dead = [i for i in issues if i.check == "dead-path"]
            self.assertEqual(len(dead), 3)
            self.assertTrue(all(i.severity == "info" for i in dead))

    def test_cluster_inside_existing_dir_stays_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            write(root, "AGENTS.md", "`src/a.py`\n`src/b.py`\n`src/c.py`\n")
            _, issues = scan(root)
            dead = [i for i in issues if i.check == "dead-path"]
            self.assertEqual(len(dead), 3)
            self.assertTrue(all(i.severity == "error" for i in dead))


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

    def test_placeholder_and_wildcard_commands_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "package.json", json.dumps({"name": "demo", "version": "1.0.0", "scripts": {"lint:js": "eslint"}}))
            write(root, "AGENTS.md", "Run `python3 /path/to/recalc.py` and `npm run lint:*`.\n")
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

    def test_header_version_and_runtime_drift(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "pyproject.toml", '[project]\nname = "demo"\nversion = "1.2.0"\nrequires-python = ">=3.11"\n')
            write(root, "AGENTS.md", "# demo\nВерсия: 1.0.0\nСтек: Python 3.9+\n")
            _, issues = scan(root)
            drift = [i for i in issues if i.check == "version-drift"]
            self.assertEqual(len(drift), 2)

    def test_cross_file_runtime_drift(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "pyproject.toml", '[project]\nname = "demo"\nversion = "1.2.0"\nrequires-python = ">=3.10"\n')
            write(root, "AGENTS.md", "Стек: Python 3.10+\n")
            write(root, "CLAUDE.md", "Стек: Python 3.12+\n")
            _, issues = scan(root)
            drift = [i for i in issues if i.check == "version-drift"]
            self.assertTrue(any("CLAUDE.md" in i.message for i in drift))

    def test_upstream_url_and_subdir_name_not_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "pyproject.toml", '[project]\nname = "hermes"\nversion = "0.0.0"\n')
            write(
                root,
                "skills/creative/humanizer/SKILL.md",
                "---\nname: humanizer\ndescription: Use when editing\n---\nRepo: https://github.com/x/humanizer (version 2.5.1)\n",
            )
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

    def test_nested_category_skill_and_multiline_yaml(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(
                root,
                "skills/cat/demo/SKILL.md",
                "---\nname: wrong\ndescription: >\n  Use when testing\n---\nUse `scripts/.cache/` and `scripts/missing.py`.\n",
            )
            (root / "skills/cat/demo/scripts").mkdir(parents=True)
            _, issues = scan(root)
            fm = [i for i in issues if i.check == "skill-frontmatter"]
            self.assertTrue(any(i.severity == "warning" and "wrong" in i.message for i in fm))
            self.assertTrue(any(i.severity == "error" and "scripts/missing.py" in i.message for i in fm))
            self.assertFalse(any("scripts/.cache/" in i.message for i in fm))
            self.assertFalse(any("Use when" in i.message for i in fm))


class DuplicateRuleTests(unittest.TestCase):
    def test_same_rule_in_two_files_is_info(self):
        rule = "- Всегда запускай линтер перед коммитом и не пуши без зелёного теста\n"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "AGENTS.md", rule)
            write(root, "sub/AGENTS.md", rule)
            _, issues = scan(root)
            self.assertTrue(any(i.check == "duplicate-rule" for i in issues))

    def test_ignores_frontmatter_and_fenced_blocks(self):
        fm = "---\nname: s1\ndescription: Use when testing\nplatforms: [linux, macos, windows]\n---\n```bash\npytest -q --maxfail=1 --tb=short\n```\n"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "skills/s1/SKILL.md", fm)
            write(root, "skills/s2/SKILL.md", fm.replace("s1", "s2"))
            _, issues = scan(root)
            self.assertFalse([i for i in issues if i.check == "duplicate-rule"])


class RuleConflictTests(unittest.TestCase):
    def test_polarity_conflict_between_agents_and_claude(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "AGENTS.md", "- Не использовать bun run compile для быстрой итерации\n")
            write(root, "CLAUDE.md", "- Всегда использовать bun run compile для быстрой итерации\n")
            _, issues = scan(root)
            conflicts = [i for i in issues if i.check == "rule-conflict"]
            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0].severity, "warning")

    def test_exclusive_architecture_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "AGENTS.md", "## Паттерны\n- Clean Architecture: разделение слоёв.\n")
            write(root, "CLAUDE.md", "## Архитектура\nПроект — процедурный PHP (без фреймворка).\n")
            _, issues = scan(root)
            conflicts = [i for i in issues if i.check == "rule-conflict"]
            self.assertEqual(len(conflicts), 1)
            self.assertIn("php-architecture", conflicts[0].message)


class EmptyDirTests(unittest.TestCase):
    def test_empty_dir_and_nested_empty_subdir_warn(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "empty_parent" / "skills").mkdir(parents=True)
            write(root, "AGENTS.md", "Бери скиллы из `empty_parent/` и `empty_parent/skills/`.\n")
            _, issues = scan(root)
            empty = [i for i in issues if i.check == "empty-dir"]
            self.assertEqual(len(empty), 2)
            self.assertTrue(all(i.severity == "warning" for i in empty))

    def test_non_empty_dir_is_clean_and_create_intent_is_info(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "populated" / "sub").mkdir(parents=True)
            (root / "populated" / "sub" / "a.txt").write_text("ok", encoding="utf-8")
            (root / "out").mkdir()
            write(root, "AGENTS.md", "Скиллы в `populated/`. Создай каталог `out/`.\n")
            _, issues = scan(root)
            empty = [i for i in issues if i.check == "empty-dir"]
            self.assertEqual(len(empty), 1)
            self.assertEqual(empty[0].severity, "info")
            self.assertIn("out/", empty[0].message)


class MultiHarnessFormatTests(unittest.TestCase):
    def test_gemini_md_and_rules_dir_use_project_scope(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            write(root, "GEMINI.md", "## Стек\n- Используем uv для зависимостей.\n")
            write(root, ".agents/rules/python.md", "## Стек\n- Используем pip для зависимостей.\nСмотри `src/missing.py`.\n")
            write(root, ".claude/rules/local.md", "Обычное правило без конфликта.\n")
            files, issues = scan(root)
            self.assertEqual(len(files), 3)
            self.assertTrue(all(f.scope == root for f in files))
            self.assertTrue(any(i.check == "rule-conflict" for i in issues))
            self.assertTrue(any(i.check == "dead-path" and i.severity == "error" for i in issues))

    def test_trailing_never_clause_does_not_invert_positive_rule(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root, "AGENTS.md", "- ALWAYS use environment variables or a secret manager\n")
            write(root, ".agents/rules/sec.md", "- Load secrets from environment variables or a secret manager, never from committed files.\n")
            _, issues = scan(root)
            self.assertFalse([i for i in issues if i.check == "rule-conflict"])


if __name__ == "__main__":
    unittest.main()
