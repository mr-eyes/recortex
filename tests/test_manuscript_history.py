"""Tests of the History of the paper's text on the mind page (1.3.0): fetching the vouched versions, the difference model, the
versions block of the page and the layout of the files for Manubot. Nothing here reaches GitHub."""

import hashlib
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_manuscript_plan as tp  # noqa: E402
import test_manuscript_write as tw  # noqa: E402
import test_scripts as ts  # noqa: E402
import manuscript_record  # noqa: E402
from test_manuscript_versions import BRANCH, VersionBox, recortex_reason  # noqa: E402

sys.path.insert(0, ts.SCRIPTS)
import paper_layout  # noqa: E402
import record_format  # noqa: E402
import recortex  # noqa: E402


class FetchTests(VersionBox):
    def versions_cache(self):
        return os.path.join(self.data, "private", self.slug(), "versions")

    def test_fetch_keeps_only_vouched_versions_and_a_second_fetch_asks_for_nothing(self):
        self.two_written()
        done = self.cmd("versions.py", "fetch")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("fetched 2, already kept 0", done.stdout)
        kept = sorted(os.listdir(self.versions_cache()))
        self.assertEqual(2, len([n for n in kept if re.fullmatch(r"[0-9a-f]{40}", n)]))
        before = len(self.repo().get("log", []))
        again = self.cmd("versions.py", "fetch")
        self.assertIn("fetched 0, already kept 2", again.stdout)
        for name in os.listdir(self.versions_cache()):
            path = os.path.join(self.versions_cache(), name)
            if os.path.isdir(path):
                self.assertEqual(0o700, os.stat(path).st_mode & 0o777)
                for leaf in os.listdir(path):
                    self.assertEqual(0o600, os.stat(os.path.join(path, leaf)).st_mode & 0o777)

    def test_a_file_with_another_hash_is_never_kept(self):
        self.two_written()
        data = self.repo()
        git = data["git"]
        for commit in git["commits"].values():
            tree = git["trees"][commit["tree"]]
            if "topics/5.md" in tree and "second look" in tree["topics/5.md"]:
                tree["topics/5.md"] = tree["topics/5.md"].replace("second look", "SECOND look")
        self.save(data)
        done = self.cmd("versions.py", "fetch")
        self.assertIn("fetched 1, already kept 0", done.stdout)
        self.assertIn("1 version was not fetched (0 past the budget, 1 failed)", done.stdout)

    def test_a_commit_only_a_memory_of_an_account_that_is_not_allowed_names_is_never_asked_for(self):
        self.two_written()
        data = self.repo()
        for cid, comment in data["comments"].items():
            if "type=text" in comment["body"].split("\n")[0]:
                comment["user"] = "hubot"
        self.save(data)
        self.assertEqual(0, self.tool("index.py", "sync", "--rebuild").returncode)   # the authors are read again, nothing else changes
        done = self.cmd("versions.py", "fetch")
        self.assertIn("fetched 0, already kept 0", done.stdout)
        self.assertFalse(os.path.isdir(self.versions_cache()) and [n for n in os.listdir(self.versions_cache())
                                                                  if re.fullmatch(r"[0-9a-f]{40}", n)])

    def test_the_budgets_leave_what_is_past_them_and_say_so(self):
        self.two_written()
        script = os.path.join(self.dir, "budget.py")
        with open(script, "w") as handle:
            handle.write("import sys; sys.path.insert(0, %r)\nimport versions, recortex\nversions.FETCH_FILES = 1\n"
                         "config = recortex.load_config()\nprint(versions.fetch(config, config['repo']))\n" % ts.SCRIPTS)
        ran = self.tool(script)
        self.assertEqual(0, ran.returncode, ran.stderr)
        self.assertIn("'fetched': 1", ran.stdout)
        self.assertIn("'left': 1", ran.stdout)
        self.assertIn("1 version was not fetched (1 past the budget, 0 failed); build the page again with --sync.", ran.stdout)


class MindVersionTests(unittest.TestCase):
    """The differences of the History (pure): mind_versions."""

    def mv(self):
        import mind_versions
        return mind_versions

    def both(self, a, b):
        ops = self.mv().diff_text(a, b, collapse=False)
        self.assertEqual(self.mv().paragraphs(a), self.mv().rebuild(ops, "before"))
        self.assertEqual(self.mv().paragraphs(b), self.mv().rebuild(ops, "after"))
        return self.mv().diff_text(a, b)

    def test_equal_texts_are_one_collapsed_count_and_no_change(self):
        ops = self.both("a\n\nb\n\nc", "a\n\nb\n\nc")
        self.assertEqual([{"op": "same", "n": 3}], ops)
        self.assertFalse(self.mv().changed(ops))

    def test_an_added_a_removed_a_moved_and_a_changed_paragraph(self):
        added = self.both("a\n\nb", "a\n\nb\n\nc")
        self.assertEqual([{"op": "same", "n": 2}, {"op": "add", "runs": [{"t": "add", "text": "c"}]}], added)
        removed = self.both("a\n\nb\n\nc", "a\n\nc")
        self.assertEqual([{"op": "same", "n": 1}, {"op": "del", "runs": [{"t": "del", "text": "b"}]}, {"op": "same", "n": 1}], removed)
        moved = self.both("a\n\nb\n\nc", "b\n\nc\n\na")
        self.assertTrue(self.mv().changed(moved))
        changed = self.both("The fit was good.\n\nb", "The fit was very good.\n\nb")
        self.assertEqual({"op": "change", "runs": [{"t": "same", "text": "The fit was "}, {"t": "add", "text": "very "},
                                                    {"t": "same", "text": "good."}]}, changed[0])

    def test_a_text_with_no_trailing_line_break_and_with_windows_endings(self):
        self.assertFalse(self.mv().changed(self.both("a\n\nb", "a\r\n\r\nb\r\n")))
        self.assertEqual([], self.mv().diff_text("", ""))
        self.assertEqual([{"op": "add", "runs": [{"t": "add", "text": "x"}]}], self.both("", "x"))

    def test_a_long_difference_is_cut_with_a_line_of_its_own(self):
        ops = self.mv().diff_text("", "\n\n".join("paragraph %d " % n + "word " * 200 for n in range(60)))
        self.assertEqual({"op": "cut"}, ops[-1])
        self.assertLess(self.mv().size(ops), 60 * 1100)

    def test_three_hundred_seeded_pairs_never_raise_and_always_rebuild_both_sides(self):
        import random
        rng = random.Random(1303)
        words = ["fit", "model", "season", "0.024", "9 of 11", "$x$", "[@doi:10.1/x]", "dry", "spell", "cover", "é", "\t", "  "]

        def text():
            return "\n\n".join(" ".join(rng.choice(words) for _ in range(rng.randint(0, 8))) for _ in range(rng.randint(0, 7)))

        for _ in range(300):
            a = text()
            b = text() if rng.random() < .6 else a.replace(rng.choice(words), rng.choice(words), 1)
            self.both(a, b)


class VersionPageTests(VersionBox):
    def page(self, *args, gh="false"):
        self.ready()
        built = self.tool("mind.py", *args, gh=gh)
        self.assertEqual(0, built.returncode, built.stdout + built.stderr)
        with open(os.path.join(self.data, "private", self.slug(), "mind.html"), encoding="utf-8") as handle:
            return handle.read()

    def words(self, text):
        return re.sub(r" ([.,;:])", r"\1", " ".join(" ".join(ts.PageReader(text).texts).split()))

    def test_the_history_of_a_part_draws_the_difference_from_the_cache_and_builds_offline(self):
        self.two_written()
        self.assertEqual(0, self.cmd("versions.py", "fetch").returncode)
        text = self.page()   # gh is `false`: nothing can be asked of GitHub
        self.assertEqual([], ts.page_problems(text))
        words = self.words(text)
        # the History is drawn once, in the box of the manuscript view; the text view says so and links to the box
        text_view = text[text.index('<section class="view" id="text"'):text.index('<section class="view" id="paper"')]
        self.assertIn("The history of this part, with each version, is in Read the manuscript.", self.words(text_view))
        self.assertRegex(text_view, r'<a class="lk" href="#hist-[0-9]+-[0-9]+">Read the manuscript</a>')
        self.assertNotIn("<ins>", text_view)
        self.assertNotIn("History (", self.words(text_view))
        self.assertRegex(words, r"History \([23]\)")
        self.assertIn("<ins>A second look, by hand.</ins>", text)
        self.assertIn("by you by hand", words)
        self.assertIn("by the plugin without asking", words)
        self.assertIn("What changed", words)
        self.assertRegex(words, r"[0-9]+ paragraphs are the same")
        self.assertRegex(words, r"Version [0-9]+ · [A-Za-z]+ [0-9]+, by the plugin")   # each entry is headed by its version
        self.assertIn("Restore the Results of", words)
        self.assertEqual(1, text.count("<ins>"))   # only the sentence that was added by hand: the first version has no before

    def test_a_version_whose_fetched_file_has_another_hash_shows_the_sentence_and_none_of_its_text(self):
        self.two_written()
        self.cmd("versions.py", "fetch")
        cache = os.path.join(self.data, "private", self.slug(), "versions")
        for commit in os.listdir(cache):
            path = os.path.join(cache, commit, "5.md")
            if os.path.isfile(path) and "second look" in open(path).read():
                os.chmod(path, 0o600)
                open(path, "w").write(open(path).read().replace("second look", "SMUGGLED look"))
        text = self.page()
        self.assertIn("The text of this version could not be checked, so it is not shown.", self.words(text))
        self.assertNotIn("SMUGGLED", text)
        self.assertNotIn("<ins>", text)   # the current text says it, but no difference of that version is drawn
        self.assertEqual([], ts.page_problems(text))

    def test_without_a_fetch_the_entries_say_they_were_not_fetched(self):
        self.two_written()
        text = self.page()
        self.assertIn("The text of this version has not been fetched yet; build the page again with --sync.", self.words(text))
        self.assertNotIn("<ins>", text)

    def test_with_the_mode_off_the_fold_holds_days_only(self):
        self.two_written()
        self.mode("off")
        text = self.page()
        words = self.words(text)
        self.assertIn("Versions with their differences are kept once you choose review or autonomous writing.", words)
        self.assertIn("Only the day the text changed is known here.", words)
        self.assertNotIn("<ins>", text)
        self.assertNotIn("Versions of the paper", words)

    def test_named_versions_say_what_changed_since_the_name_before(self):
        self.written()
        self.assertEqual(0, self.cmd("versions.py", "name", "first", "--words-file", self.words_file("Call this first")).returncode)
        data = self.repo()
        issue = data["issues"]["5"]
        issue["body"] = issue["body"].replace("\r\n", "\n").replace("\n### Figure: dryspell", "\nA second look, by hand.\n\n### Figure: dryspell", 1)
        issue["edits"] = [{"at": "2026-12-02T10:00:00Z", "by": "octocat"}, {"at": issue["created_at"], "by": "octocat"}]
        issue["last_edited_at"] = issue["updated_at"] = "2026-12-02T10:00:00Z"
        self.save(data)
        self.ready()
        self.assertEqual(0, self.cmd("versions.py", "sync").returncode)
        self.ready()
        self.assertEqual(0, self.cmd("versions.py", "name", "second", "--words-file", self.words_file("And this second")).returncode)
        self.ready()
        self.assertEqual(0, self.cmd("versions.py", "fetch").returncode)
        text = self.page()
        words = self.words(text)
        self.assertEqual([], ts.page_problems(text))
        self.assertIn("Versions of the paper", words)
        self.assertRegex(words, r"first Named on [A-Z][a-z]+ [0-9]+\.")
        self.assertRegex(words, r"second Named on [A-Z][a-z]+ [0-9]+\.")
        self.assertNotIn("last built", words.lower())   # no build is made or linked
        self.assertIn("“Call this first”", words)
        self.assertIn("What changed since first", words)
        self.assertIn("<ins>A second look, by hand.</ins>", text)
        since = text[text.index('id="versions-of-the-paper"'):text.index("</section>", text.index('id="versions-of-the-paper"'))]
        self.assertIn("<ins>A second look, by hand.</ins>", since)   # drawn in the block of the names, not only in the part's fold
        self.assertRegex(self.words(since), r"[0-9]+ parts? did not change")
        self.assertRegex(words, r"[0-9]+ parts? did not change\.")
        # nothing of the branch, a commit or a tag is linked
        for form in ("/commit/", "/tree/", recortex_branch(), "paper-first", "paper-second"):
            self.assertNotIn(form, text, form)
        self.assertEqual([], [m for m in re.findall(r'href="(https://github\.com/[^"]+)"', text)
                              if re.search(r"/blob/[0-9a-f]{40}|/tree/|/commit/", m)])
        self.assertNotIn("/actions/", text)

    def words_file(self, text):
        return self.words(text) if False else tw.WritingBox.words(self, text)

    def test_the_budgets_keep_the_entries_and_say_so(self):
        self.two_written()
        self.cmd("versions.py", "fetch")
        script = os.path.join(self.dir, "budget-page.py")
        with open(script, "w") as handle:
            handle.write("import sys; sys.path.insert(0, %r)\nimport mind_versions, mind\nmind_versions.MAX_PARTS = 1\nsys.exit(mind.main([]))\n" % ts.SCRIPTS)
        self.ready()
        ran = self.tool(script, gh="false")
        self.assertEqual(0, ran.returncode, ran.stdout + ran.stderr)
        text = open(os.path.join(self.data, "private", self.slug(), "mind.html"), encoding="utf-8").read()
        self.assertIn("This page shows the differences of the newest versions only; older ones keep their entries.", self.words(text))
        self.assertEqual([], ts.page_problems(text))
        script2 = os.path.join(self.dir, "budget-page2.py")
        with open(script2, "w") as handle:
            handle.write("import sys; sys.path.insert(0, %r)\nimport mind_versions, mind\nmind_versions.MAX_BYTES = 10\nsys.exit(mind.main([]))\n" % ts.SCRIPTS)
        ran = self.tool(script2, gh="false")
        self.assertEqual(0, ran.returncode, ran.stdout + ran.stderr)
        text = open(os.path.join(self.data, "private", self.slug(), "mind.html"), encoding="utf-8").read()
        self.assertNotIn("<ins>", text)
        self.assertIn("older ones keep their entries", self.words(text))

    def test_a_hostile_model_draws_every_operation_and_run_as_text(self):
        self.two_written()
        self.cmd("versions.py", "fetch")
        self.ready()
        done = self.tool("index.py", "query", "mind", "--json")
        model = json.loads(done.stdout)
        hostile = "<ins onclick=alert(1)>HOSTILE</ins><script>x</script>"
        found = []
        for group in model["outline"]["deliverables"]:
            for section in group["sections"]:
                for part in section["parts"]:
                    entries = (part.get("history") or {}).get("entries") or []
                    for entry in entries:
                        entry["diff"] = [{"op": "script", "runs": [{"t": "add", "text": hostile}]},
                                         {"op": "change", "runs": [{"t": "script", "text": hostile}, {"t": "del", "text": "gone"}]},
                                         {"op": "add", "runs": [{"t": "add", "text": hostile}, "not a run", {"t": "add", "text": 5}]},
                                         {"op": "same", "n": "<b>9</b>"}, "not an op", {"op": "cut", "x": hostile}]
                        entry["no_diff"] = None
                        found.append(entry)
        self.assertTrue(found)
        model["versions"] = {"kept": True, "unfetched": hostile,
                             "names": [{"name": hostile, "day": hostile, "words": hostile, "id": hostile,
                                        "since": {"name": hostile, "same": hostile, "parts": [
                                            {"thread": hostile, "ordinal": hostile, "label": hostile, "diff": [{"op": "add", "runs": [
                                                {"t": "add", "text": hostile}]}], "no_diff": None}, "x"]}}]}
        import mind
        text = mind.render(model, {})
        self.assertEqual([], ts.page_problems(text))
        self.assertNotIn("<script>x", text)
        self.assertNotIn("onclick", text.replace("&lt;ins onclick", "").replace("onclick=alert(1)&gt;", ""))
        self.assertNotIn("actions/", text)
        # the only ins and del elements are the ones made from a run of type add or del of a known operation: two runs of the
        # add operation and one del run of the change operation per entry (drawn once, in the box of the manuscript view), and one
        # add run of the versions block
        self.assertEqual(2 * len(found) + 1, text.count("<ins>"))
        self.assertEqual(len(found), text.count("<del>"))
        self.assertEqual([], re.findall(r"<(?:ins|del) [^>]*>", text))

    def test_ins_and_del_are_made_by_page_diff_only(self):
        with open(os.path.join(ts.SCRIPTS, "mind.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertEqual(2, len(re.findall(r'E\("(?:ins|del)"', source)))
        start = source.index("    def diff(self, ops):")
        end = source.index("    def versions_block(self):")
        self.assertEqual(2, len(re.findall(r'E\("(?:ins|del)"', source[start:end])))
        for other in ("mind_model.py", "mind_plan.py", "mind_figures.py"):
            with open(os.path.join(ts.SCRIPTS, other), encoding="utf-8") as handle:
                self.assertNotIn("<ins", handle.read())


def recortex_branch():
    return "recortex-manuscript"


# ---------------------------------------------------------------- the layout

FIGURE_BODY = """Over the season the difference was small $x$ and $$a+b=c$$ (see @fig:dry and @tbl:fit, @eq:one).

### A subheading

Daily water in plots [@doi:10.1234/abc; @pmid:12345].

### Figure: dry - Soil moisture through the spell

Daily water content at 5 cm.

![x](https://github.com/octo-org/sample-project/blob/v0.4/figures/dry.png?raw=true)

### Table: fit - Model fit

Fit by season.

| a | b |
|---|---|
| 1 | 2 |

## Sources

| Statement | Source |
|---|---|
| a | doi:10.1/x |
"""


class LayoutTests(unittest.TestCase):
    def lay(self, title="Moisture", **kw):
        text = record_format.section_text(FIGURE_BODY, "Results") or ""
        return paper_layout.layout("octo-org/sample-project", 1, title, [
            ("Introduction", [(2, "Why we looked", "The first words.")]),
            ("Results", [(5, "Dry spells", FIGURE_BODY.split("## Sources")[0].strip("\n"))]),
            ("Discussion", [(6, "Nothing yet", "")])], **kw)


    def test_record_text_reaches_manubot_with_no_jinja_delimiter(self):
        text = "The fit $\\mathrm{{d}}\\theta$ held {% raw %} and {{ config }} and {## a ##}."
        files, _ = paper_layout.layout("octo-org/sample-project", 1, "T", [("Results", [(5, "Topic {{ x }}", text)])])
        laid = files["content/10.results.md"]
        for found in ("{{ config }}", "{% raw %} and", "{## a", "Topic {{ x }}", "{{d}}"):
            self.assertNotIn(found, laid)
        self.assertIn("{{ '{' }}{ config }}", laid)

    def test_the_jinja_escape_changes_only_a_brace_that_opens_a_template(self):
        text = "\\frac{a}{b} and {#fig:dryspell} and {#tbl:plots} and {a} and a lone { and }}"
        files, _ = paper_layout.layout("octo-org/sample-project", 1, "T", [("Results", [(5, "Topic", text)])])
        self.assertIn(text, files["content/10.results.md"])

    def test_the_file_names_and_their_order_follow_the_outline_and_empty_sections_are_left_out(self):
        files, _ = self.lay()
        self.assertEqual(["content/00.front-matter.md", "content/10.introduction.md", "content/20.results.md", "content/90.back-matter.md",
                          "content/images.tsv", "content/manual-references.json", "content/metadata.yaml"], sorted(files))

    def test_a_topics_headings_go_one_level_lower_under_the_sections_own(self):
        files, _ = self.lay()
        text = files["content/20.results.md"]
        self.assertTrue(text.startswith("## Results\n\n### Dry spells\n\n"))
        self.assertIn("\n#### A subheading\n", text)

    def test_a_figure_and_a_table_block_take_manubots_forms_with_their_ids(self):
        files, images = self.lay()
        text = files["content/20.results.md"]
        self.assertIn("\n\n![Soil moisture through the spell. Daily water content at 5 cm.](images/dry.png){#fig:dry}\n\n", text)
        self.assertIn("| a | b |\n|---|---|\n| 1 | 2 |\n\nTable: Model fit. Fit by season. {#tbl:fit}\n", text)
        self.assertEqual([("dry", "octo-org/sample-project", "v0.4", "figures/dry.png")], images)
        self.assertIn("dry\toctopus".replace("octopus", "octo-org/sample-project") + "\tv0.4\tfigures/dry.png", files["content/images.tsv"])

    def test_formulas_citations_and_references_pass_through_and_sources_stays_out(self):
        files, _ = self.lay()
        text = files["content/20.results.md"]
        for kept in ("$x$", "$$a+b=c$$", "@fig:dry", "@tbl:fit", "@eq:one", "[@doi:10.1234/abc; @pmid:12345]"):
            self.assertIn(kept, text)
        self.assertNotIn("Sources", text)
        self.assertNotIn("doi:10.1/x", "".join(files.values()))

    def test_a_title_with_a_colon_a_quote_and_a_line_break_is_one_quoted_yaml_string(self):
        files, _ = self.lay(title='A: "b"\nauthors: [evil]\n---', authors=["Ada: Lovelace", "x\ny"], keywords=["k: v"])
        meta = files["content/metadata.yaml"]
        self.assertEqual('title: "A: \\"b\\"\\nauthors: [evil]\\n---"', meta.split("\n")[0])
        self.assertEqual(['title', 'keywords', 'lang', 'authors', '  - name', '  - name'], [
            line.split(":")[0] for line in meta.rstrip("\n").split("\n")])
        self.assertEqual((["Ada: Lovelace", "x\ny"], ["k: v"]), paper_layout.read_metadata(meta))

    def test_two_runs_give_the_same_bytes(self):
        self.assertEqual(self.lay(), self.lay())

    def test_the_figure_is_never_made_by_the_layout(self):
        files, _ = self.lay()
        self.assertFalse([name for name in files if name.startswith("content/images/")])


if __name__ == "__main__":
    unittest.main()
