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


class CodeSpanTests(unittest.TestCase):
    def test_spans_with_line_numbers(self):
        text = "line one `~/a/b.sh`\nline two `x`\n"
        spans = parse.code_spans(text)
        self.assertIn((1, "~/a/b.sh"), spans)
        self.assertIn((2, "x"), spans)

    def test_ignores_double_backticks(self):
        text = "``not a span``\n"
        self.assertEqual(parse.code_spans(text), [])


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


if __name__ == "__main__":
    unittest.main()
