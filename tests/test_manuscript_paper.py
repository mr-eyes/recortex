"""Tests of the paper's own export (1.3.0, spec section 20): the print layout of the Manuscript view and `mind.py --paper
[--version NAME]`, the standalone paper.html. Nothing here reaches GitHub; the scripts run against tests/fake_gh.py."""

import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_manuscript_versions as tv  # noqa: E402
import test_scripts as ts  # noqa: E402

sys.path.insert(0, ts.SCRIPTS)
import record_format  # noqa: E402

PAGE = os.path.join(ROOT, "skills", "recortex", "page")


class PrintLayoutTests(unittest.TestCase):
    """The style and the scripts are fixed files: the print rules are checked as text, the page they style as built."""

    def style(self):
        with open(os.path.join(PAGE, "mind.css"), encoding="utf-8") as handle:
            return handle.read()

    def printed(self):
        style = self.style()
        return style[style.index("@media print{"):]

    def test_the_print_rules_leave_the_paper_alone_on_the_sheet(self):
        rules = self.printed()
        for hidden in (".side", ".tour", ".panel", ".wordbox", ".noprint", "#text .partlinks", "#text .status", "#text details",
                       "#text .versions", "#text .contents"):
            self.assertIn(hidden, rules.split("{display:none!important}")[0], hidden)
        self.assertIn("#text .sec,.paperdoc .papersec{break-before:page}", rules)   # a page break before each section
        self.assertIn(".blockfig,.formula,table,blockquote,pre,img{break-inside:avoid}", rules)   # figures and equations stay whole
        self.assertIn("@page{margin:2cm}", self.style())
        self.assertNotIn("url(", self.style())    # the style loads nothing, in print as on screen

    def test_the_fixed_scripts_ask_the_browser_to_print_and_do_nothing_else(self):
        for name in ("mind.js", "paper.js"):
            with open(os.path.join(PAGE, name), encoding="utf-8") as handle:
                script = handle.read()
            self.assertIn("window.print()", script, name)
            self.assertIn("[data-print]", script, name)
            for word in ts.SCRIPT_BANNED:
                self.assertNotIn(word, script, (name, word))
            self.assertNotIn("</", script)
            self.assertNotIn("<!--", script)
        with open(os.path.join(PAGE, "paper.js"), encoding="utf-8") as handle:
            self.assertEqual(1, handle.read().count("window.print()"))


class PaperExportTests(tv.VersionBox):
    def mind_page(self):
        self.ready()
        built = self.tool("mind.py")
        self.assertEqual(0, built.returncode, built.stdout + built.stderr)
        with open(os.path.join(self.data, "private", self.slug(), "mind.html"), encoding="utf-8") as handle:
            return handle.read()

    def paper(self, *args, expect=0):
        self.ready()
        done = self.tool("mind.py", "--paper", *args)
        self.assertEqual(expect, done.returncode, done.stdout + done.stderr)
        return done

    def written_file(self, name):
        with open(os.path.join(self.data, "private", self.slug(), name), encoding="utf-8") as handle:
            return handle.read()

    def words(self, text):
        return re.sub(r" ([.,;:])", r"\1", " ".join(" ".join(ts.PageReader(text).texts).split()))

    def test_the_manuscript_view_has_a_print_button_and_the_page_keeps_its_rules(self):
        text = self.mind_page()
        self.assertEqual([], ts.page_problems(text))
        text_view = text[text.index('<section class="view" id="text"'):text.index('<section class="view" id="threads"')]
        self.assertEqual(1, text_view.count('data-print="1"'))
        self.assertIn("Print or save as PDF", self.words(text_view))
        self.assertEqual(1, text.count('data-print="1"'))   # only in the Manuscript view

    def test_paper_html_holds_the_paper_alone_in_the_dashboards_design_under_the_same_policy(self):
        self.written()
        done = self.paper()
        self.assertIn("paper.html", done.stdout)
        text = self.written_file("paper.html")
        self.assertEqual([], ts.page_problems(text))
        words = self.words(text)
        self.assertIn("Print or save as PDF", words)
        self.assertIn("Results", words)
        self.assertIn("dry spells", words)
        self.assertIn("The pooled fit", words)                 # the text the write put in
        for absent in ("Ways in", "The plan against the text", "History", "Why it says this", "How we got here",
                       "Versions of the paper", "First visit", "Switch light and dark"):
            self.assertNotIn(absent, words, absent)             # no navigation, no plan, no side panel, no history
        self.assertIn("<math", text)                            # the equations are the page's own MathML
        self.assertIn("Spell length", words)                    # a table
        self.assertIn("Where the numbers come from", words)
        self.assertEqual(1, text.count("<script>"))
        self.assertEqual(1, text.count("<style>"))
        self.assertIn("default-src &#x27;none&#x27;", text)
        self.assertNotIn("http://", text)
        self.assertEqual(text, self.written_file("paper.html"))
        self.paper()
        self.assertEqual(text, self.written_file("paper.html"))         # the same record gives the same page, day aside

    def test_paper_html_is_written_beside_the_mind_page_in_the_private_folder_and_its_address_is_printed(self):
        self.written()
        done = self.paper()
        folder = os.path.join(self.data, "private", self.slug())
        path = os.path.join(folder, "paper.html")
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(0o600, os.stat(path).st_mode & 0o777)
        self.assertEqual(0o700, os.stat(folder).st_mode & 0o777)
        self.assertIn("open it in a browser: file://", done.stdout)
        self.assertIn(path, done.stdout.replace("file://", ""))
        self.assertFalse(os.path.exists(os.path.join(folder, "mind.html")))   # --paper leaves the mind page alone

    def test_record_text_is_text_in_the_paper(self):
        self.written()
        hostile = "<script>alert(1)</script><img src=x onerror=alert(2)>"
        data = self.repo()
        issue = data["issues"]["5"]
        issue["body"] = issue["body"].replace("\r\n", "\n").replace("\n### Figure: dryspell", "\n%s\n\n### Figure: dryspell" % hostile, 1)
        issue["edits"] = [{"at": "2026-12-02T10:00:00Z", "by": "octocat"}, {"at": issue["created_at"], "by": "octocat"}]
        issue["last_edited_at"] = issue["updated_at"] = "2026-12-02T10:00:00Z"
        issue["title"] = "A topic <b onclick=x>named</b>"
        self.save(data)
        self.paper()
        text = self.written_file("paper.html")
        self.assertEqual([], ts.page_problems(text, marker="alert(2)"))
        self.assertNotIn("<script>alert", text)
        self.assertNotIn("onerror", re.sub(r"&lt;[^&]*&gt;", "", text))
        self.assertIn("&lt;script&gt;alert(1)", text)

    def test_a_named_version_is_written_as_it_was_in_the_sections_it_had(self):
        self.written()
        self.assertEqual(0, self.cmd("versions.py", "name", "submitted", "--words-file", self.words_file("call it submitted")).returncode)
        self.ready()
        data = self.repo()
        issue = data["issues"]["5"]
        issue["body"] = issue["body"].replace("\r\n", "\n").replace("pooled fit", "pooled FIT AFTER THE NAME")
        issue["edits"] = [{"at": "2026-12-09T10:00:00Z", "by": "octocat"}, {"at": issue["created_at"], "by": "octocat"}]
        issue["last_edited_at"] = issue["updated_at"] = "2026-12-09T10:00:00Z"
        issue["labels"] = [label for label in issue["labels"] if not label.startswith("ms:")]   # the outline changes too
        self.save(data)
        done = self.paper("--version", "submitted")
        self.assertIn("paper-submitted.html", done.stdout)
        text = self.written_file("paper-submitted.html")
        self.assertEqual([], ts.page_problems(text))
        words = self.words(text)
        self.assertIn("Version submitted, named on", words)
        self.assertIn("pooled fit", words)
        self.assertNotIn("AFTER THE NAME", text)
        self.assertIn("Results", words)                          # the section it had, though the labels are gone now
        self.paper()
        now = self.words(self.written_file("paper.html"))
        self.assertNotIn("pooled fit", now.replace("pooled FIT AFTER THE NAME", ""))   # the paper of today has the new text only

    def words_file(self, text):
        return tv.tw.WritingBox.words(self, text)

    def test_a_version_the_record_does_not_vouch_for_writes_nothing(self):
        self.written()
        done = self.paper("--version", "nosuch", expect=1)
        self.assertIn("no decision of an allowed account names a version called nosuch", done.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.data, "private", self.slug(), "paper-nosuch.html")))
        bad = self.paper("--version", "../x", expect=1)
        self.assertIn("a name is 1 to 40 characters", bad.stdout)
        alone = self.tool("mind.py", "--version", "submitted")
        self.assertEqual(1, alone.returncode)
        self.assertIn("--version goes with --paper", alone.stdout)

    def test_a_repository_that_is_not_trusted_gets_no_paper(self):
        self.written()
        self.untrust()
        done = self.tool("mind.py", "--paper")
        self.assertEqual(1, done.returncode)
        self.assertIn("not in your trusted list", done.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.data, "private", self.slug(), "paper.html")))

    def test_the_paper_never_reads_a_body_the_page_would_not_tell(self):
        self.written()
        data = self.repo()
        issue = data["issues"]["5"]
        issue["body"] = issue["body"].replace("\r\n", "\n").replace("pooled fit", "pooled FIT BY A STRANGER")
        issue["edits"] = [{"at": "2026-12-09T10:00:00Z", "by": "hubot"}, {"at": issue["created_at"], "by": "octocat"}]
        issue["last_edited_at"] = issue["updated_at"] = "2026-12-09T10:00:00Z"
        self.save(data)
        self.paper()
        self.assertNotIn("BY A STRANGER", self.written_file("paper.html"))

    def test_the_paper_page_is_a_pure_function_of_its_data(self):
        import mind
        import mind_paper
        paper = mind_paper.paper("octo-org/sample-project", "A title", "A note.", [("ms:results", "Results", [5]), ("x", "Sources", [5])],
                                 {5: "# T\n\n## Results\n\nText with $x^2$ and $$y=z$$.\n\n## Sources\n\n| Statement or number | Source |\n"
                                     "|---|---|\n| 9 of 11 | the log |\n"}, {5: "Topic five"})
        self.assertEqual([("Results", 1)], [(s["heading"], len(s["parts"])) for s in paper["sections"]])
        self.assertEqual([{"topic": 5, "statement": "9 of 11", "source": "the log"}], paper["sources"])
        first, second = mind.render_paper(paper), mind.render_paper(paper)
        self.assertEqual(first, second)
        self.assertEqual([], ts.page_problems(first))
        self.assertEqual(2, first.count("<math"))


if __name__ == "__main__":
    unittest.main()
