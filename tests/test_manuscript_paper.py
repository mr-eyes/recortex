"""Tests of the paper's own export (1.3.0, spec section 20): the print layout of the Manuscript view and `mind.py --paper
[--version NAME]`, the standalone paper.html. Nothing here reaches GitHub; the scripts run against tests/fake_gh.py."""

import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import manuscript_record  # noqa: E402
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

    def test_the_manuscript_view_has_the_print_button_and_the_text_view_points_to_it(self):
        text = self.mind_page()
        self.assertEqual([], ts.page_problems(text))
        text_view = text[text.index('<section class="view" id="text"'):text.index('<section class="view" id="paper"')]
        self.assertEqual(0, text_view.count('data-print="1"'))
        self.assertIn("Read it as a paper", self.words(text_view))
        paper_view = text[text.index('<section class="view" id="paper"'):text.index('<section class="view" id="threads"')]
        self.assertEqual(2, paper_view.count('data-print="1"'))   # the bar, and the line under the title on a phone
        self.assertEqual(2, text.count('data-print="1"'))          # nowhere else
        self.assertIn("Print or save as PDF", self.words(paper_view))

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

    PART = re.compile(r'<h4 class="parth"[^>]*>([^<]*)</h4>')
    SECTION = re.compile(r'<h3 class="sech"[^>]*>([^<]*)</h3>|<section class="sec papersec"><h3>([^<]*)</h3>')

    def headings(self, html, level):
        """The part titles (level 4) or the section headings (level 3: the view's `h3 sech`, paper.html's `h3`) of a page, in order."""
        if level == 4:
            return self.PART.findall(html)
        return [view or paper for view, paper in self.SECTION.findall(html)]

    def view_of(self, mind_html):
        return mind_html[mind_html.index('<section class="view" id="paper"'):mind_html.index('<section class="view" id="threads"')]

    def test_paper_html_has_the_views_title_and_the_titles_of_the_texts_not_the_hubs_or_the_issues(self):
        self.written()
        view = self.view_of(self.mind_page())
        self.paper()
        text = self.written_file("paper.html")
        self.assertIn('<h2 class="papertitle">The manuscript</h2>', text)   # the view's rule: the noun of the profile, capitalized
        self.assertIn('<p class="papername">sample-project</p>', text)       # and the project's name under it
        self.assertNotIn("sample-project manuscript hub", text)             # never the hub's title
        self.assertIn('<h2 id="paper-h" class="ptitle">The manuscript</h2>', view)
        self.assertIn('<p class="psub">sample-project<span class="noprint"> · ', view)
        self.assertIn('<a class="lk" href="#project-map">Project Map</a>', view)
        # each part is titled by its text's own first heading, as the view titles it, not by the title of its issue
        paper_parts = self.headings(text, 4)
        view_parts = self.headings(view, 4)
        self.assertIn("Cover crops keep the topsoil wetter in dry spells", paper_parts)
        self.assertIn("What the trial shows and what it does not", paper_parts)
        for title in ("Results: dry spells", "Discussion: limits and next work", "Methods: field design and sampling"):
            self.assertNotIn(title, paper_parts)                            # the titles of the issues
            self.assertNotIn(title, text)
        for title in paper_parts:
            self.assertIn(title, view_parts)                                  # the view's own part titles
        self.assertEqual([t for t in view_parts if t in paper_parts], paper_parts)   # in the view's order
        # the sections are those of the view, in its order, with the numbers the style counts
        paper_sections = self.headings(text, 3)
        view_sections = self.headings(view, 3)
        self.assertTrue(paper_sections)
        self.assertEqual([t for t in view_sections if t in paper_sections], paper_sections)

    def test_a_project_with_two_profiles_gets_the_manuscripts_sections_only(self):
        self.written()
        data = self.repo()
        data["issues"]["20"] = manuscript_record.issue(
            "A pilot study", "# Pilot results of the first season\n\n## Results\n\nPILOT-ONLY-TEXT of a second deliverable.\n",
            ["final-record", "rd:results"], "2026-12-10T10:00:00Z", parent=1)   # newer than the last sync, so the next one reads it
        self.save(data)
        self.write_config(dict(self.config, profiles=["research", "manuscript"]))
        self.ready()
        self.paper()
        text = self.written_file("paper.html")
        self.assertNotIn("PILOT-ONLY-TEXT", text)                            # a section of the research profile is not the manuscript's
        self.assertNotIn("Pilot results of the first season", text)
        self.assertIn('<h2 class="papertitle">The manuscript</h2>', text)
        self.assertIn("dry spells", self.words(text))                       # the manuscript's own sections are all there
        self.assertEqual(1, self.headings(text, 3).count("Results"))
        self.assertEqual(0, self.tool("mind.py", "--paper").returncode)
        self.assertEqual(text, self.written_file("paper.html"))             # a second run writes the same page

    def test_a_project_with_no_plan_profile_keeps_its_old_title_and_every_section(self):
        self.written()
        self.write_config(dict(self.config, profiles=["research"]))
        self.ready()
        self.paper()
        text = self.written_file("paper.html")
        self.assertIn('<h2 class="papertitle">sample-project manuscript hub</h2>', text)
        self.assertNotIn("papername", text.split("<style>")[1].split("</style>")[1])

    def test_a_named_version_has_the_title_of_the_view_and_the_headings_of_its_own_text(self):
        self.written()
        self.assertEqual(0, self.cmd("versions.py", "name", "submitted", "--words-file", self.words_file("call it submitted")).returncode)
        self.ready()
        self.paper("--version", "submitted")
        text = self.written_file("paper-submitted.html")
        self.assertIn('<h2 class="papertitle">The manuscript</h2>', text)
        self.assertIn('<p class="papername">sample-project</p>', text)
        self.assertIn("Cover crops keep the topsoil wetter in dry spells", self.headings(text, 4))
        self.assertNotIn("Results: dry spells", text)

    def test_a_part_with_no_heading_of_its_own_falls_back_to_the_name_it_is_given(self):
        import mind_paper
        body = "## Results\n\nText of the part.\n"
        paper = mind_paper.paper("octo-org/sample-project", "The manuscript", "A note.", [("ms:results", "Results", [5, 6])],
                                 {5: "# The text's own title\n\n" + body, 6: body}, {5: "Issue title five", 6: "Issue title six"}, "sample-project")
        self.assertEqual(["The text's own title", "Issue title six"], [part["name"] for part in paper["sections"][0]["parts"]])
        self.assertEqual("sample-project", paper["name"])
        self.assertEqual("", mind_paper.paper("octo-org/sample-project", "T", "N", [], {}, {})["name"])

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

    def test_a_title_another_account_set_never_reaches_the_paper(self):
        self.written()
        data = self.repo()
        hub = data["issues"]["1"]
        hub["title"] = "HOSTILE-TITLE set by a stranger"
        hub["timeline"] = [{"event": "renamed", "created_at": "2026-12-09T10:00:00Z", "actor": {"login": "hubot"},
                            "rename": {"from": "a", "to": "b"}}]
        hub["updated_at"] = "2026-12-09T10:00:00Z"
        self.save(data)
        self.paper()
        self.assertNotIn("HOSTILE-TITLE", self.written_file("paper.html"))

    def test_the_paper_page_is_a_pure_function_of_its_data(self):
        import mind
        import mind_paper
        paper = mind_paper.paper("octo-org/sample-project", "A title", "A note.", [("ms:results", "Results", [5]), ("x", "Sources", [5])],
                                 {5: "# T\n\n## Results\n\nText with $x^2$ and $$y=z$$.\n\n## Sources\n\n| Statement or number | Source |\n"
                                     "|---|---|\n| 9 of 11 | the log |\n"}, {5: "Topic five"})
        self.assertEqual([("Results", 1)], [(s["heading"], len(s["parts"])) for s in paper["sections"]])
        self.assertEqual([{"topic": 5, "statement": [{"t": "9 of 11"}], "source": [{"t": "the log"}]}], paper["sources"])
        first, second = mind.render_paper(paper), mind.render_paper(paper)
        self.assertEqual(first, second)
        self.assertEqual([], ts.page_problems(first))
        self.assertEqual(2, first.count("<math"))


    def test_code_in_the_rows_of_sources_is_code_in_the_export_and_not_backticks(self):
        """The rows of "Where the numbers come from" draw a code span as code, not as backticks around its text."""
        import mind
        import mind_paper
        body = ("# T\n\n## Results\n\nText.\n\n## Sources\n\n| Statement or number | Source |\n|---|---|\n"
                "| 0.024 from `fit_pooled.csv` | `results/fit_pooled.csv`, commit 3f9c2ab |\n"
                "| <b>raw</b> and `<script>alert(1)</script>` | `a` <img src=x onerror=alert(1)> |\n")
        paper = mind_paper.paper("octo-org/sample-project", "A title", "A note.", [("ms:results", "Results", [5]), ("x", "Sources", [5])],
                                 {5: body}, {5: "Topic five"})
        page = mind.render_paper(paper)
        self.assertIn("<code>results/fit_pooled.csv</code>, commit 3f9c2ab", page)
        self.assertIn("<code>fit_pooled.csv</code>", page)
        listed = page.split('class="srcs"')[1].split("</ul>")[0]
        self.assertNotIn("`", listed)
        self.assertNotIn("<script", listed)       # untrusted text stays text, whatever it holds
        self.assertNotIn("<img", listed)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", listed)
        self.assertEqual([], ts.page_problems(page))
        self.assertEqual(page, mind.render_paper(paper))


if __name__ == "__main__":
    unittest.main()
