import tempfile
import unittest
from pathlib import Path

from agctx import parse


class FrontmatterTests(unittest.TestCase):
    def test_parses_simple_keys(self):
        text = "---\nname: demo\ndescription: Use when testing\n---\n\n# Body\n"
        meta, body_start = parse.parse_frontmatter(text)
        self.assertEqual(meta["name"], "demo")
        self.assertEqual(meta["description"], "Use when testing")
        self.assertEqual(body_start, 4)

    def test_missing_frontmatter(self):
        meta, body_start = parse.parse_frontmatter("# Just a header\n")
        self.assertEqual(meta, {})
        self.assertEqual(body_start, 0)

    def test_multiline_scalars_lists_and_nested_keys(self):
        text = (
            "---\r\n"
            "name: demo\r\n"
            "description: >-\r\n"
            "  Use when checking\r\n"
            "  multiline YAML.\r\n"
            "platforms: [linux, macos]\r\n"
            "triggers:\r\n"
            "  - first trigger\r\n"
            "  - \"second: trigger\"\r\n"
            "metadata:\r\n"
            "  author:\r\n"
            "    name: Nested Author\r\n"
            "---\r\n"
        )
        meta, body_start = parse.parse_frontmatter(text)
        self.assertEqual(meta["name"], "demo")
        self.assertEqual(meta["description"], "Use when checking multiline YAML.")
        self.assertEqual(meta["platforms"], ["linux", "macos"])
        self.assertEqual(meta["triggers"], ["first trigger", "second: trigger"])
        self.assertEqual(meta["metadata"]["author"]["name"], "Nested Author")
        self.assertGreater(body_start, 0)

    def test_empty_frontmatter_keeps_body_start(self):
        meta, body_start = parse.parse_frontmatter("---\n---\nBody\n")
        self.assertEqual(meta, {})
        self.assertEqual(body_start, 2)


class CodeSpanTests(unittest.TestCase):
    def test_spans_with_line_numbers(self):
        text = "line one `~/a/b.sh`\nline two `x`\n"
        spans = parse.code_spans(text)
        self.assertIn((1, "~/a/b.sh"), spans)
        self.assertIn((2, "x"), spans)

    def test_ignores_double_backticks(self):
        text = "``not a span``\n"
        self.assertEqual(parse.code_spans(text), [])

    def test_ignores_fenced_code_blocks_and_frontmatter(self):
        text = "---\nname: demo\ndesc: `front/path.py`\n---\n```ts\nconst x = `tpl/file.ts`;\n```\nReal `src/app.py`\n"
        spans = parse.code_spans(text)
        self.assertEqual(spans, [(8, "src/app.py")])


class PathHeuristicTests(unittest.TestCase):
    def test_absolute_and_home_are_paths(self):
        self.assertTrue(parse.looks_like_path("~/x/y.sh"))
        self.assertTrue(parse.looks_like_path("/etc/foo.conf"))
        self.assertTrue(parse.looks_like_path("src/main.py"))

    def test_urls_and_commands_are_not(self):
        self.assertFalse(parse.looks_like_path("https://example.com/a"))
        self.assertFalse(parse.looks_like_path("npm run build"))
        self.assertFalse(parse.looks_like_path("--flag"))
        self.assertFalse(parse.looks_like_path("*.py"))


class CommandTests(unittest.TestCase):
    def test_extracts_npm_and_make(self):
        text = "run `npm run build` and `make test`\n"
        cmds = [c for _, c in parse.commands(text)]
        self.assertTrue(any(c.startswith("npm run build") for c in cmds))
        self.assertTrue(any(c.startswith("make test") for c in cmds))

    def test_ignores_prose_make_and_extracts_shell_fence(self):
        text = "Always make sure tests pass.\n```bash\nmake lint\n```\n```python\nmake_it()\n```\n"
        cmds = [c for _, c in parse.commands(text)]
        self.assertEqual(cmds, ["make lint"])


class VersionTests(unittest.TestCase):
    def test_russian_and_english(self):
        text = "версия 1.2.3\nversion: 4.5.6\n"
        self.assertEqual([v for _, v in parse.versions(text)], ["1.2.3", "4.5.6"])


class NoiseFilterTests(unittest.TestCase):
    def test_placeholders_are_not_paths(self):
        self.assertFalse(parse.looks_like_path("exact/path/to/new_file.py"))
        self.assertFalse(parse.looks_like_path("tests/path/to/test_file.py"))
        self.assertFalse(parse.looks_like_path("src/<name>/file.py"))

    def test_slash_commands_are_not_paths(self):
        self.assertFalse(parse.looks_like_path("/plan"))
        self.assertFalse(parse.looks_like_path("/status"))

    def test_real_absolute_paths_survive(self):
        self.assertTrue(parse.looks_like_path("/etc/hosts"))
        self.assertTrue(parse.looks_like_path("/usr/bin/python3"))

    def test_line_suffix_is_stripped(self):
        self.assertEqual(parse.clean_token("src/a.py:12-30"), "src/a.py")
        self.assertEqual(parse.clean_token("~/x.sh:7"), "~/x.sh")

    def test_resolve_home_token_without_expanduser(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / "a" / "b").mkdir(parents=True)
            (home / "a" / "b" / "c.conf").write_text("x")
            got = parse.resolve_path("~/a/b/c.conf", home, home, home)
            self.assertTrue(got.exists())

    def test_dot_prefixes_preserved_by_clean_token(self):
        self.assertEqual(parse.clean_token("./scripts/a.py"), "./scripts/a.py")
        self.assertEqual(parse.clean_token("../foo/bar.py"), "../foo/bar.py")
        self.assertEqual(parse.clean_token(".claude/evals/x.md"), ".claude/evals/x.md")
        self.assertEqual(parse.clean_token(".work/shot.mjs"), ".work/shot.mjs")
        self.assertEqual(parse.clean_token("src/a.py."), "src/a.py")
        self.assertTrue(parse.looks_like_path("./scripts/a.py"))
        self.assertTrue(parse.looks_like_path(".claude/evals/x.md"))

    def test_formal_grammar_rejects_model_ids_and_semver_leaves(self):
        self.assertFalse(parse.looks_like_path("openrouter/anthropic/claude-sonnet-4.5"))
        self.assertFalse(parse.looks_like_path("/org/project/v1.2.0"))
        self.assertFalse(parse.looks_like_path("tools/bot_mode_probe._roster"))

    def test_resolve_relative_does_not_fallback_to_home(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            proj = base / "proj"
            (home / "src").mkdir(parents=True)
            (home / "src" / "main.py").write_text("x")
            proj.mkdir()
            got = parse.resolve_path("src/main.py", proj, proj, home)
            self.assertFalse(got.exists())
            self.assertEqual(got, proj / "src" / "main.py")


if __name__ == "__main__":
    unittest.main()
