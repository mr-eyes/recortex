"""Each check of check.py must fail when its guard is removed. These tests build a tiny plugin and a tiny site,
change one thing at a time, and look for the failure. Run: python3 -m unittest discover -s tests"""
import contextlib
import io
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import check  # noqa: E402

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:">
<title>%(title)s</title><meta name="description" content="A page.">
<link rel="icon" href="/favicon.ico">
%(head)s
</head><body><h1 id="top">%(h1)s</h1>%(body)s</body></html>
"""
CONFIG = {"plugin_repo": "recortex/recortex", "plugin_ref": "v1.5.1", "version": "1.5.1", "url": "https://recortex.github.io"}
PAGES = [{"id": "home", "path": "/", "max_words": 100}, {"id": "404", "path": "/404.html", "max_words": 100}]


def write(root, rel, text, binary=False):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if binary else "w", **({} if binary else {"encoding": "utf-8"})) as handle:
        handle.write(text)


def make_plugin(root):
    write(root, ".claude-plugin/plugin.json", '{"name": "recortex", "version": "1.5.1"}')
    write(root, ".claude-plugin/marketplace.json", '{"name": "recortex"}')
    write(root, "commands/ask.md", "ask")
    write(root, "agents/ask.md", "ask")
    write(root, "skills/recortex/SKILL.md", "skill")
    write(root, "skills/recortex/scripts/observer.py", 'sub("enable")\nflag("--level")\n')
    write(root, "skills/recortex/scripts/names.py", 'CONFIG = ".recortex.json"\n')
    write(root, "docs/security.md", "trusted-repos and RECORTEX_HOOK_TIMEOUT")


def make_site(root, body="<p>Fine.</p>", title="Home", h1="Home", head="", js=None):
    write(root, "index.html", PAGE % {"title": title, "h1": h1, "body": body, "head": head})
    write(root, "404.html", PAGE % {"title": "Gone", "h1": "Gone", "body": "", "head": ""})
    write(root, "site.js", js or "try { localStorage.setItem('theme', 'dark'); } catch (e) {}\n")
    write(root, "theme.js", "try { localStorage.getItem('theme'); } catch (e) {}\n")
    for name in ("search-index.js", "site.css", ".nojekyll", "robots.txt", "sitemap.xml", "favicon.ico"):
        write(root, name, "x")
    write(root, "assets/social-preview.png", b"x", binary=True)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plugin = os.path.join(self.tmp.name, "plugin")
        self.site = os.path.join(self.tmp.name, "site")
        make_plugin(self.plugin)

    def run_check(self, config=None, counts=None, **site):
        make_site(self.site, **site)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.run(self.site, dict(CONFIG, **(config or {})), PAGES, counts, {"plugin": self.plugin})
        return code, out.getvalue()

    def assert_fails(self, text, expect, **site):
        code, out = self.run_check(**site, body=text)
        self.assertEqual(code, 1, "no failure for: %s\n%s" % (text, out))
        self.assertIn(expect, out)

    def test_a_clean_site_passes(self):
        code, out = self.run_check(body='<p>Use <code>/recortex:ask</code> and <code>observer.py enable --level x</code>.</p>'
                                        '<p><code>.recortex.json</code> <code>RECORTEX_HOOK_TIMEOUT</code> <code>trusted-repos</code></p>'
                                        '<a href="#top">top</a> <a href="/404.html">x</a> octo-org/billing-service')
        self.assertEqual(code, 0, out)

    def test_the_plugin_facts_must_exist(self):
        for text, expect in (
            ("<code>/recortex:frobnicate</code>", "no such command"),
            ("<code>nothing.py</code>", "no such script"),
            ("<code>observer.py enable --nope</code>", "has no flag"),
            ("<code>observer.py frobnicate</code>", "no subcommand"),
            ("<p>@agent-recortex:nothing</p>", "no such agent"),
            ("<code>.nothere.json</code>", "no such file name"),
            ("<code>skills/recortex/missing</code>", "no such path"),
            ("<code>some-kebab-name</code>", "no such name"),
            ("<code>RECORTEX_NOT_A_VARIABLE</code>", "no such variable"),
            ("<code>claude plugin install recortex@other</code>", "install names"),
            ("<code>claude plugin marketplace add acme/x</code>", "marketplace add names"),
            ("<code>claude plugin frobnicate</code>", "does not know"),
            ("<code>/recortex hold git-nothing</code>", "never shows this line"),
        ):
            with self.subTest(text=text):
                self.assert_fails(text, expect)

    def test_words_names_and_spelling(self):
        for text, expect in (
            ("<p>A comprehensive tool.</p>", "banned word"),
            ("<p>A Robust tool.</p>", "banned word"),
            ("<p>It is crucial.</p>", "banned word"),
            ("<p>Nice \U0001F600</p>", "emoji"),
            ("<p>The colour.</p>", "British"),
            ("<code>acme-corp/payments</code>", "not on the list"),
            ("<p>acme-corp/payments</p>", "not on the list"),
            ("<p>Ask @someone.</p>", "not on the list"),
            ('<a href="https://github.com/acme-corp/payments">x</a>', "not on the list"),
            ('<a href="https://example.org/">x</a>', "not on the list"),
        ):
            with self.subTest(text=text):
                self.assert_fails(text, expect)

    def test_links_and_loads(self):
        for text, expect in (
            ('<a href="/nothing/">x</a>', "broken link"),
            ('<a href="#nope">x</a>', "no anchor"),
            ('<a href="/#nope">x</a>', "no anchor"),
            ('<a href="relative.html">x</a>', "not root-absolute"),
            ('<script src="https://cdn.example.com/x.js"></script>', "loads from outside"),
            ('<img src="//cdn.example.com/x.png">', "loads from outside"),
            ("<script>var a = 1;</script>", "inline script"),
            ('<p style="color:red">x</p>', "inline script, style"),
            ('<a href="https://github.com/recortex/recortex/blob/v1.5.1/docs/missing.md">x</a>', "not in the plugin"),
            ('<a href="https://github.com/recortex/recortex/blob/main/docs/security.md">x</a>', "site.json pins"),
        ):
            with self.subTest(text=text):
                self.assert_fails(text, expect)

    def test_structure_length_and_policy(self):
        code, out = self.run_check(title="  ")
        self.assertEqual(code, 1)
        self.assertIn("no title", out)
        code, out = self.run_check(h1="")
        self.assertIn("exactly one h1", out)
        code, out = self.run_check(counts={"home": 101})
        self.assertIn("over the limit", out)
        make_site(self.site)
        with open(os.path.join(self.site, "index.html"), encoding="utf-8") as handle:
            text = handle.read().replace("default-src 'none'", "default-src *")
        write(self.site, "index.html", text)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(check.run(self.site, CONFIG, PAGES, None, {"plugin": self.plugin}), 1)
        self.assertIn("policy is not strict", out.getvalue())

    def test_the_script_may_store_only_the_theme_and_load_nothing(self):
        for js, expect in (
            ("try { localStorage.setItem('visits', '1'); } catch (e) {}", "besides the theme"),
            ("fetch('/x');", "makes a request"),
            ("document.cookie = 'a=b';", "makes a request"),
        ):
            with self.subTest(js=js):
                code, out = self.run_check(js=js)
                self.assertEqual(code, 1)
                self.assertIn(expect, out)

    def test_the_version_follows_the_pin(self):
        code, out = self.run_check(config={"version": "1.5.2"})
        self.assertEqual(code, 1)
        self.assertIn("describes 1.5.2", out)
        code, out = self.run_check(config={"version": "1.5.2", "plugin_ref": "main"})
        self.assertEqual(code, 0)
        self.assertIn("warning", out)


if __name__ == "__main__":
    unittest.main()
