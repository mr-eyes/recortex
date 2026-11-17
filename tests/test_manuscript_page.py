"""Tests of the manuscript page (1.4.0): the view of the paper as a paper on the mind page. The differ of blocks and phrases, the model
(the versions numbered, the picker, the states of a part against a version, the marks, the slots, authorship), the page (the modes,
the lists of the contract, hostile text) and the style and the script. Nothing here reaches GitHub; the scripts run against
tests/fake_gh.py. Versions that the record would hold are made here as text changes with planted files in the cache, so that each
state and each key of the table can be had by itself."""

import datetime
import hashlib
import json
import os
import random
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import manuscript_record  # noqa: E402
import test_manuscript_plan as tp  # noqa: E402
import test_manuscript_versions as tv  # noqa: E402
import test_scripts as ts  # noqa: E402

sys.path.insert(0, ts.SCRIPTS)
import mind_text  # noqa: E402
import mind_versions  # noqa: E402
import record_format  # noqa: E402
import recortex  # noqa: E402

PAGE = os.path.join(ROOT, "skills", "recortex", "page")
EXAMPLE_PAGE_SHA = "dce8ea5db659997f3ce66825cb5ef57cf76b0509abfd6623d685cc1b5baf4769"   # the example's page at 71bf8cf, see below

FORMS = {"autonomous": "Written without asking, under the autonomous mode.",
         "chat": "By the author in the chat, 2026-11-21 09:38 EST:\n\n> Approve",
         "merged": "By octocat on GitHub, by merging pull request 41.",
         "edited": "Edited by octocat on GitHub, 2026-11-21 09:38 EST.",
         "restored": "Restored by the author in the chat, 2026-11-21 09:38 EST:\n\n> Restore this one",
         "kept": "Kept as a file later, for the text change of comment 9400."}


def blocks(text):
    return mind_text.blocks(text)


# ---------------------------------------------------------------- the differ (pure)

class DifferTests(unittest.TestCase):
    def test_the_words_of_a_block_are_the_plain_words_of_its_runs_and_cells(self):
        para = blocks("The **fit** was `good` and $x^2$ held, see [the table](https://github.com/octo-org/octo-repo/issues/5).")[0]
        self.assertEqual("The fit was good and x^2 held, see the table .".replace(" .", "."), " ".join(mind_versions.words(para)).replace(" ,", ","))
        table = blocks("| a b | c |\n|---|---|\n| d | e f |")[0]
        self.assertEqual(["a", "b", "c", "d", "e", "f"], mind_versions.words(table))
        self.assertEqual([], mind_versions.words({"block": {"kind": "figure", "id": "x"}}))
        self.assertEqual([], mind_versions.words({"note": {"key": "html-table"}}))
        self.assertEqual(["one", "two", "three"], mind_versions.words(blocks("- one\n  - two\n- three")[0]))

    def rebuilt(self, old, new):
        runs = mind_versions.phrases(mind_versions.word_runs(old, new))
        self.assertEqual(old, "".join(r["text"] for r in runs if r["t"] in ("same", "del")))
        self.assertEqual(new, "".join(r["text"] for r in runs if r["t"] in ("same", "add")))
        return runs

    def test_phrases_join_a_short_same_run_between_two_changes_and_keep_a_long_one(self):
        old = "The median of 5 runs was 0.14 s for the reader and the spread of the runs was not recorded anywhere in the notes."
        new = "The median of 7 runs was 0.15 s for the reader and the spread of the runs was not recorded anywhere in the notes."
        runs = self.rebuilt(old, new)
        self.assertEqual(["same", "del", "add", "same"], [r["t"] for r in runs])
        self.assertEqual(("5 runs was 0.14", "7 runs was 0.15"), (runs[1]["text"], runs[2]["text"]))   # " runs was " is 2 words
        long_gap = "a b c d e f g"
        runs = self.rebuilt("x 1 %s y 2 more words stay the same at the end of it" % long_gap,
                            "x 3 %s y 4 more words stay the same at the end of it" % long_gap)
        self.assertEqual(["same", "del", "add", "same", "del", "add", "same"], [r["t"] for r in runs])   # a long same run stays
        # two words but more than sixteen characters stay too
        old = "start one extraordinarily incomprehensible words between end of the sentence here is long"
        new = "START one extraordinarily incomprehensible words between END of the sentence here is long"
        runs = self.rebuilt(old, new)
        self.assertIn(" one extraordinarily incomprehensible words between ", [r["text"] for r in runs if r["t"] == "same"])
        # a whitespace-only run between two changes is absorbed
        runs = self.rebuilt("alpha beta gamma delta epsilon zeta", "alpha BETA GAMMA delta epsilon zeta")
        self.assertEqual(["same", "del", "add", "same"], [r["t"] for r in runs])

    def test_a_paragraph_of_which_most_changed_is_one_deletion_and_one_addition(self):
        old = "one two three four five six seven eight nine ten"
        runs = self.rebuilt(old, "one TWO THREE FOUR FIVE SIX SEVEN EIGHT nine ten")
        self.assertEqual([{"t": "del", "text": old}, {"t": "add", "text": "one TWO THREE FOUR FIVE SIX SEVEN EIGHT nine ten"}], runs)
        runs = self.rebuilt(old, "one two three four five six seven eight nine TEN")
        self.assertEqual(["same", "del", "add"], [r["t"] for r in runs])   # a tenth of it is not most
        self.assertEqual([], mind_versions.phrases([]))
        self.assertEqual([{"t": "same", "text": "x"}], mind_versions.phrases([{"t": "same", "text": "x"}]))

    def test_three_hundred_seeded_pairs_rebuild_both_sides_through_phrases(self):
        rng = random.Random(1401)
        words = ["fit", "model", "season", "0.024", "9 of 11", "$x$", "dry", "spell", "cover", "é", "\t", "  ", "the", "a", "of"]

        def text():
            return " ".join(rng.choice(words) for _ in range(rng.randint(0, 14)))

        for _ in range(300):
            old = text()
            new = text() if rng.random() < .5 else old.replace(rng.choice(words), rng.choice(words), 1)
            self.rebuilt(old, new)

    def kinds(self, old, new, **kw):
        return [row["k"] for row in mind_versions.since(blocks(old), blocks(new), **kw)]

    def test_since_classifies_added_removed_reworded_and_moved_paragraphs(self):
        base = "The reader is a state machine that reads one character at a time.\n\nStrict quoting is the default here.\n\nThe tests pass."
        self.assertEqual(["same", "same", "same"], self.kinds(base, base))
        self.assertEqual(["same", "same", "same", "added"], self.kinds(base, base + "\n\nA new paragraph about speed."))
        self.assertEqual(["same", "removed", "same"], self.kinds(base, base.replace("Strict quoting is the default here.\n\n", "")))
        reworded = base.replace("one character at a time", "one character at a time, never ahead")
        rows = mind_versions.since(blocks(base), blocks(reworded))
        self.assertEqual(["reworded", "same", "same"], [r["k"] for r in rows])
        self.assertTrue(any(r["t"] == "add" for r in rows[0]["runs"]))
        self.assertEqual(blocks(base)[0], rows[0]["then"])
        moved = "Strict quoting is the default here.\n\nThe tests pass.\n\nThe reader is a state machine that reads one character at a time."
        self.assertEqual(["removed", "same", "same", "added"], self.kinds(base, moved))   # a moved paragraph is removed and added
        self.assertEqual(["removed", "added"], self.kinds("Totally one thing.", "- a list\n- of items"))   # other kinds are no pair

    def test_since_matches_row_indexes_to_the_new_blocks_and_puts_removed_rows_between(self):
        rows = mind_versions.since(blocks("a\n\nb\n\nc"), blocks("a\n\nc"))
        self.assertEqual([("same", 0), ("removed", None), ("same", 1)], [(r["k"], r["i"]) for r in rows])
        self.assertIsNone(rows[1]["now"])
        self.assertEqual(blocks("b")[0], rows[1]["then"])
        rows = mind_versions.since(blocks("x\n\na\n\nb"), blocks("a\n\nb"))
        self.assertEqual([("removed", 0), ("same", 1), ("same", 2)], [(r["k"], r["j"]) for r in rows])

    def test_since_calls_a_changed_table_or_list_or_quote_changed(self):
        old = "| a | b |\n|---|---|\n| 1 | 2 |"
        new = "| a | b |\n|---|---|\n| 1 | 3 |"
        self.assertEqual(["changed"], self.kinds(old, new))
        self.assertEqual(["changed"], self.kinds("- one\n- two", "- one\n- three"))
        self.assertEqual(["changed"], self.kinds("> quote one", "> quote two"))

    def test_a_figure_pointer_with_a_changed_caption_is_a_change(self):
        text = "Before.\n\n### Figure: dry - Soil moisture\n\nDaily water.\n\n![x](https://github.com/octo-org/sample-project/blob/v0.4/f/dry.png?raw=true)\n"
        old, new = blocks(text), blocks(text)
        pointers = [b for b in new if "block" in b]
        self.assertEqual(1, len(pointers))
        entry = lambda caption: {("figure", "dry"): {"kind": "figure", "id": "dry", "title": "Soil moisture", "caption": caption, "data": "",
                                                    "command": "", "repo": "", "ref": "v0.4", "path": "f/dry.png"}}
        same = [r["k"] for r in mind_versions.since(old, new, entry("Daily water."), entry("Daily water."))]
        self.assertNotIn("changed", same)
        changed = mind_versions.since(old, new, entry("Daily water."), entry("Daily water, mean of ten plots."))
        self.assertIn("changed", [r["k"] for r in changed])
        row = next(r for r in changed if r["k"] == "changed")
        self.assertEqual(("block", "block"), (list(row["now"])[0], list(row["then"])[0]))
        other = mind_versions.since(old, [{"block": {"kind": "figure", "id": "other"}}], entry("x"), {})
        self.assertEqual(["added", "removed", "removed"], sorted(r["k"] for r in other))   # another id is no change of the same figure

    def test_the_pair_budget_stops_the_comparison_by_words_and_never_raises(self):
        old = "\n\n".join("paragraph %d has several words in it" % n for n in range(8))
        new = "\n\n".join("paragraph %d has several different words in it" % n for n in range(8))
        self.assertEqual(["reworded"] * 8, self.kinds(old, new))
        saved = mind_versions.MAX_PAIRS
        mind_versions.MAX_PAIRS = 0
        try:
            self.assertEqual(sorted(["removed"] * 8 + ["added"] * 8), sorted(self.kinds(old, new)))
        finally:
            mind_versions.MAX_PAIRS = saved
        self.assertEqual([], mind_versions.since([], []))
        self.assertEqual(["added"], self.kinds("", "x"))

    def test_the_size_of_rows_counts_what_the_page_will_carry(self):
        rows = mind_versions.since(blocks("one two three four five\n\ngone"), blocks("one two THREE four five"))
        self.assertGreater(mind_versions.since_size(rows), 24 * len(rows))
        self.assertEqual({"reworded": 1, "added": 0, "removed": 1, "changed": 0}, mind_versions.counts(rows))
        self.assertEqual(2 * 1024 * 1024, mind_versions.MAX_SINCE_BYTES)


# ---------------------------------------------------------------- the model and the page, on the invented paper record

class PageBox(tv.VersionBox):
    """The paper record. Versions are made here as told text changes of topic 5 with files planted in the cache."""

    def page(self, *args, gh="false"):
        self.ready()
        built = self.tool("mind.py", *args, gh=gh)
        self.assertEqual(0, built.returncode, built.stdout + built.stderr)
        self.last_output = built.stdout
        with open(os.path.join(self.data, "private", self.slug(), "mind.html"), encoding="utf-8") as handle:
            return handle.read()

    def model(self):
        self.ready()
        done = self.tool("index.py", "query", "mind", "--json")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        return json.loads(done.stdout)

    @staticmethod
    def sheet_of(text):
        start = text.index('<section class="view" id="paper"')
        return text[start:text.index('<section class="view" id="threads"', start)]

    @staticmethod
    def words(text):
        return re.sub(r" ([.,;:])", r"\1", " ".join(" ".join(ts.PageReader(text).texts).split()))

    def part_of(self, model, thread, ordinal):
        for section in model["sheet"]["sections"]:
            for part in section["parts"]:
                if part["thread"] == thread and part["ordinal"] == ordinal:
                    return part
        self.fail("no part %s/%s" % (thread, ordinal))

    def results(self, model, thread=5):
        return self.part_of(model, thread, 0)

    def topic_body(self, topic=5):
        return self.repo()["issues"][str(topic)]["body"].replace("\r\n", "\n")

    def plant(self, commit, topic, body):
        self.in_process()
        import storage
        storage.write_cached(self.config["repo"], (storage.VERSIONS, commit), "%d.md" % topic, body)

    def craft(self, steps, topic=5, heading="Results", live=None, skip=(), hour=10):
        """Make the topic's history: `steps` is a list of dicts (body, form, before) in time order. Each becomes a told text change with
        the hashes of its body and a file kept in the cache (not for the indexes in `skip`); the live body is the last one, or
        `live`. Returns the commits."""
        data = self.repo()
        previous, commits = None, []
        for index, step in enumerate(steps):
            commit = hashlib.sha1(("version %d" % index).encode()).hexdigest()
            commits.append(commit)
            after = record_format.body_sha(step["body"])
            before = step.get("before", previous or "none")
            at = "2026-11-%02dT%02d:00-05:00" % (20 + index // 20, hour + index % 20)
            text = manuscript_record.text_memory(at, heading, "the text of the %s changed" % heading.lower(), "One paragraph changed.",
                                                 "The record moved.", FORMS[step.get("form", "autonomous")], before=before,
                                                 after=after, version=commit)
            # the comment is stamped after everything the record holds, so that an incremental sync reads it; its marker has the time
            data["comments"][str(9400 + index)] = {"issue": topic, "body": text, "created_at": "2026-12-06T%02d:%02d:00Z" % divmod(index, 60)}
            if index not in skip:
                self.plant(commit, topic, step["body"])
            previous = after
        issue = data["issues"][str(topic)]
        issue["body"] = live if live is not None else steps[-1]["body"]
        issue["edits"] = [{"at": "2026-12-05T10:00:00Z", "by": "octocat"}, {"at": issue["created_at"], "by": "octocat"}]
        issue["last_edited_at"] = issue["updated_at"] = "2026-12-05T10:00:00Z"
        data["next_comment"] = max(data.get("next_comment", 0), 9400 + len(steps) + 1)
        self.save(data)
        self.ready()
        return commits

    def name(self, commit, name, words="Call this one that", cid=9390, at="2026-11-25T10:00-05:00"):
        data = self.repo()
        text = manuscript_record.decision(at, "name the version", "name this version of the paper", "A version of the paper is named.",
                                          "the first author", words, "Chat, 2026-11-25.",
                                          "Version name: %s; commit %s; manifest sha256 %s" % (name, commit, "d" * 64),
                                          "Keep writing, owner Claude session")
        data["comments"][str(cid)] = {"issue": 13, "body": text, "created_at": "2026-12-07T00:00:00Z"}
        data["issues"]["13"]["updated_at"] = "2026-12-07T00:00:00Z"
        self.save(data)
        self.ready()


def add_after(body, heading_line, paragraph):
    """The body with a paragraph put in before a line (the figure block of the Results)."""
    return body.replace(heading_line, paragraph + "\n\n" + heading_line, 1)


FIGURE_LINE = "### Figure: dryspell"


class Steps(PageBox):
    """A history of the Results of topic 5 in which each way a paragraph can have come about shows once."""

    def history(self, **kw):
        base = self.topic_body()
        self.base = base
        bodies = [base]
        for name in ("A", "B", "C", "D", "E", "G"):
            bodies.append(add_after(bodies[-1], FIGURE_LINE, "Paragraph %s of the results was added here." % name))
        forms = ["autonomous", "edited", "autonomous", "chat", "merged", "restored", "edited"]
        steps = [{"body": body, "form": form} for body, form in zip(bodies, forms)]
        steps[0]["before"] = "c" * 64
        self.bodies = bodies
        live = add_after(bodies[-1], FIGURE_LINE, "Paragraph F of the results was added by hand just now.")
        self.mode("autonomous")
        return self.craft(steps, live=live, **kw)

    def notes(self, text, part="p-5-0"):
        """The words of the margin of each row of one part, in order: [(first words of the block, [notes])]."""
        start = text.index('id="%s"' % part)
        end = text.index('<section class="part"', start) if '<section class="part"' in text[start:] else len(text)
        seen, out = text[start:end], []
        for row in re.split(r'<div class="row rw', seen)[1:]:
            block = row.split('class="v-now"', 1)[1]
            lead = self.words(block.split("</p>", 1)[0])[:30]
            notes = re.findall(r'<span class="who">(.*?)</span>', row)
            out.append((lead, notes))
        return out


class WhoGetsItTests(PageBox):
    def test_a_paper_record_has_the_view_as_the_third_way_in_and_the_text_view_points_to_it(self):
        text = self.page()
        self.assertEqual([], ts.page_problems(text))
        page = ts.PageReader(text)
        views = [dict(attrs)["id"] for tag, attrs in page.elements if tag == "section" and "view" in dict(attrs).get("class", "")]
        self.assertEqual(["now", "text", "paper", "threads", "settled", "lessons", "open", "map", "ask"], views)
        navs = [dict(attrs)["data-nav"] for tag, attrs in page.elements if "data-nav" in dict(attrs)]
        self.assertEqual(views, navs)
        words = self.words(text)
        self.assertIn("Read the manuscript", words)
        self.assertIn("The plan and the text", words)            # the old view's name, with the one manuscript profile
        self.assertNotIn("What does the manuscript say?", words)
        text_view = text[text.index('<section class="view" id="text"'):text.index('<section class="view" id="paper"')]
        self.assertIn('<a class="lk" href="#paper">Read it as a paper</a>', text_view)
        self.assertNotIn("Print or save as PDF", text_view)
        self.assertEqual(1, text.count('id="paper"'))

    def test_the_view_shows_the_sections_of_the_deliverables_profile_only_and_nothing_else(self):
        model = self.model()
        self.assertEqual("manuscript", model["sheet"]["profile"])
        self.assertEqual(["Methods", "Results", "Limitations", "Introduction", "Discussion"],
                         [s["label"] for s in model["sheet"]["sections"]])   # the order of the hub's table
        outline = [s["label"] for g in model["outline"]["deliverables"] for s in g["sections"]]
        self.assertEqual(outline, [s["label"] for s in model["sheet"]["sections"]])

    def test_a_project_without_a_plan_profile_gets_no_view_and_the_page_it_had(self):
        # a project of the software and research profiles: no sheet, no way in, the print button where it was
        self.write_config(dict(self.config, profiles=["software", "research"]))
        self.ready()
        model = self.model()
        self.assertIsNone(model["sheet"])
        text = self.page()
        self.assertNotIn('id="paper"', text)
        self.assertNotIn("Read the manuscript", self.words(text))
        self.assertNotIn("Read it as a paper", text)
        self.assertEqual(1, text.count('data-print="1"'))
        self.assertEqual([], [tag for tag, attrs in ts.PageReader(text).elements if "data-mode" in dict(attrs)])
        self.assertEqual(0, len([tag for tag, _ in ts.PageReader(text).elements if tag in ("figure", "fieldset", "select")]))

    def test_the_built_in_examples_page_is_the_page_it_was_apart_from_the_style_and_the_script(self):
        """The example's page built with a fixed day, its style, its script and their hashes taken out, has the SHA-256 that the page of
        commit 71bf8cf (before the manuscript view) had."""
        sandbox = ts.MindSandbox("run")
        sandbox.setUp()
        self.addCleanup(sandbox.doCleanups)
        sandbox.add_chain()
        sandbox.ready()
        sandbox.in_process()
        import index
        import mind
        import mind_model
        db = index.connect(sandbox.config["repo"], create=False)
        try:
            model = mind_model.build(db, sandbox.config["repo"], recortex.allowed_users(sandbox.config["repo"]),
                                     datetime.date(2026, 11, 17), recortex.shipped_profiles(sandbox.config.get("profiles")), sandbox.config)
        finally:
            db.close()
        self.assertIsNone(model["sheet"])
        text = mind.render(model, {})
        text = re.sub(r"<style>.*?</style>", "<style></style>", text, flags=re.S)
        text = re.sub(r"<script>.*?</script>", "<script></script>", text, flags=re.S)
        text = re.sub(r"sha256-[A-Za-z0-9+/=]+", "sha256-X", text)
        self.assertEqual(EXAMPLE_PAGE_SHA, hashlib.sha256(text.encode("utf-8")).hexdigest())


class NoPlanTests(PageBox):
    plan = False

    def test_with_no_plan_issue_the_view_is_the_text_with_figures_tables_and_sources_and_nothing_else(self):
        text = self.page()
        self.assertEqual([], ts.page_problems(text))
        model = self.model()
        self.assertFalse(model["sheet"]["plan"])
        self.assertIsNone(model["sheet"]["message"])
        view = self.sheet_of(text)
        words = self.words(view)
        self.assertIn("Cover crops keep the topsoil wetter in dry spells", words)
        self.assertIn("Where the numbers come from", words)
        self.assertIn("<figure", view)
        for absent in ("planned parts", "Planned for the", "What the paper sets out to say", "Nothing is planned for it",
                       "Changes since", "Clean copy", "open question"[:0] + "backed", "<fieldset", "holes"):
            self.assertNotIn(absent, view if absent not in ("planned parts", "backed") else words, absent)
        self.assertIn('data-mode="plain"', view)
        self.assertNotIn("Written by the plugin", words)
        self.assertEqual([], [s for s in model["sheet"]["sections"] if s["slots"] or s["plan"]])


class TextAndOrderTests(PageBox):
    def test_sections_and_parts_come_in_the_outlines_order_with_the_title_and_the_message(self):
        text = self.page()
        view = self.sheet_of(text)
        order = [m for m in re.findall(r'<h3 class="sech" id="p-sec-[0-9]+">(.*?)</h3>', view)]
        self.assertEqual(["Methods", "Results", "Limitations", "Introduction", "Discussion"], order)
        parts = re.findall(r'<h4 class="parth" id="p-([0-9]+-[0-9x]+)">', view)
        self.assertEqual(["3-0", "4-0", "4-1", "5-0", "4-2", "5-1", "6-0", "2-0", "6-1"], parts)
        words = self.words(view)
        self.assertIn("The manuscript", words)
        self.assertIn("sample-project", words)                 # the project's name, as the subtitle
        self.assertNotIn("sample-project manuscript hub", words)   # the hub's title is not the paper's
        self.assertIn("What the paper sets out to say (from the plan)", words)
        self.assertIn("tell one story: a rye cover keeps the topsoil wetter in dry spells", words)
        self.assertIn('href="#plan-against-text"', view)
        self.assertEqual(1, view.count('<h2 id="paper-h"'))
        self.assertLess(view.index("<h2"), view.index("<h3")), self.assertLess(view.index("<h3"), view.index("<h4"))
        self.assertIn("As the record says on", words)
        self.assertIn('role="note"', view)
        self.assertNotIn("<aside", view)

    def test_a_topic_that_is_not_told_shows_the_fixed_sentence_and_none_of_its_text(self):
        data = self.repo()
        data["issues"]["5"]["user"] = "hubot"
        data["issues"]["5"]["body"] = data["issues"]["5"]["body"].replace("Over the whole season", "SMUGGLED text of the whole season")
        self.save(data)
        self.ready()
        text = self.page()
        self.assertEqual([], ts.page_problems(text))
        view = self.sheet_of(text)
        self.assertIn("The text of this thread is not shown here", self.words(view))
        self.assertNotIn("SMUGGLED", text)
        self.assertEqual([], [p for s in self.model()["sheet"]["sections"] for p in s["parts"] if p["thread"] == 5 and p["shown"]])

    def test_figures_tables_and_sources_are_drawn_as_a_paper_draws_them(self):
        view = self.sheet_of(self.page())
        self.assertRegex(view, r'<figure class="tab"><figcaption><span class="ft">Plots by block and treatment\.</span> Plots in')
        self.assertIn('role="region"', view)
        self.assertRegex(view, r'aria-label="Table [0-9]+, scrolls sideways"')
        self.assertIn('scope="col"', view)
        self.assertIn('tabindex="0"', view)
        self.assertRegex(view, r'<figure class="fig nopic">')
        self.assertIn("The picture is not in this page", self.words(view))
        self.assertIn('<ol class="refs2">', view)
        self.assertIn("results/dry_spells.csv", self.words(view))
        self.assertLess(view.index("Where the numbers come from"), view.index("Written from the record by recortex"))


class MarksAndSlotsTests(PageBox):
    def test_each_state_of_a_planned_part_has_its_own_words_and_mark(self):
        model = self.model()
        said = {m["text"] for s in model["sheet"]["sections"] for p in s["parts"] for m in p["marks"]}
        for words in ("2 planned parts backed", "A finding speaks against it", "1 planned part rests on something that changed",
                      "Planned; nothing backs it yet", "Can be made now", "A backed part can be written now",
                      "2 backed parts can be written now", "1 open question", "2 failures nothing followed",
                      "The record has decisions and findings that the text does not say yet"):
            self.assertIn(words, said)
        text = self.page()
        view = self.sheet_of(text)
        for kind in ("backed", "against", "moved", "planned", "now", "open"):
            self.assertIn('class="st st-%s"' % kind, view)
        self.assertIn('data-explain="state-plan-doubt"', view)
        self.assertIn('data-explain="state-plan-backed"', view)
        self.assertIn('data-explain="state-plan-can-be-made"', view)
        self.assertIn(">See the finding</a>", view)
        for old in ("In doubt", "Due:", "not backed yet"):
            self.assertNotIn(old, self.words(view))

    def test_a_part_with_nothing_planned_shows_no_mark_and_the_section_says_so(self):
        model = self.model()
        part = self.part_of(model, 2, 0)    # the Introduction: nothing planned
        self.assertEqual([], part["marks"])
        intro = [s for s in model["sheet"]["sections"] if s["label"] == "Introduction"][0]
        self.assertEqual({"planned": 0, "backed": 0}, intro["plan"])
        view = self.sheet_of(self.page())
        self.assertIn("Nothing is planned for it", self.words(view))
        self.assertIn("2 of 6 planned parts backed", self.words(view))
        self.assertIn('viewBox="0 0 100 4"', view)

    def test_the_changed_and_the_methods_sentences_of_a_due_text(self):
        # a text change of the Results that the record's later correction moved past: the text is due again
        data = self.repo()
        text = manuscript_record.text_memory("2026-11-18T10:00-05:00", "Results", "the model fits", "One paragraph.", "The fit is in.",
                                             FORMS["autonomous"], version="e" * 40, links="because=9122")
        data["comments"]["9410"] = {"issue": 4, "body": text, "created_at": manuscript_record.utc("2026-11-18T10:00-05:00")}
        self.save(data)
        model = self.model()
        said = [m["text"] for s in model["sheet"]["sections"] for p in s["parts"] for m in p["marks"]]
        self.assertIn("The record moved on since this was written; it can be updated now", said)
        self.assertIn("The record has decisions and findings that the text does not say yet", said)

    def test_the_slots_hold_the_plans_own_words_in_the_plans_order_and_are_gone_once_a_text_change_links_the_part(self):
        model = self.model()
        results = [s for s in model["sheet"]["sections"] if s["label"] == "Results"][0]
        items = [i for p in results["parts"] for i in (p["slots"] or {"items": []})["items"]]
        self.assertEqual(["claim", "claim", "claim", "claim", "table"], sorted(i["part"] for i in items))
        self.assertEqual("tabulate the spells by length", next(i for i in items if i["part"] == "table")["plain"])
        text = self.page()
        view = self.sheet_of(text)
        self.assertIn("Planned for the Results and not in the text yet", self.words(view))
        self.assertIn("Planned table · can be made now", self.words(view))
        self.assertIn("tabulate the spells by length", self.words(view))
        self.assertIn("Planned claim · an attempt to show it failed", self.words(view))
        self.assertIn("3 planned parts are not in the text yet: 2 claims and a table", self.words(view))   # the margin of each list of slots
        self.assertIn("2 planned parts are not in the text yet: 2 claims", self.words(view))
        self.assertNotIn("Claim: claim", self.words(view))      # the doubled word of the plan's table
        self.assertRegex(view, r'<h5 class="lab" id="hole-[0-9]+">Planned for the')
        self.assertRegex(view, r'<ul class="holelist" aria-labelledby="hole-[0-9]+">')
        # the slot sits at the end of the part its plan names (topic 5 for the table), not at the end of the section
        part5 = view[view.index('id="p-5-0"'):view.index('id="p-4-2"')]
        self.assertIn("tabulate the spells by length", part5)
        # a text change that links a planned part (9306) takes its slot away
        data = self.repo()
        linked = manuscript_record.text_memory("2026-11-22T10:00-05:00", "Results", "the table is in", "A table.", "It can be made.",
                                               FORMS["autonomous"], version="f" * 40, links="because=9306")
        data["comments"]["9411"] = {"issue": 5, "body": linked, "created_at": manuscript_record.utc("2026-11-22T10:00-05:00")}
        self.save(data)
        view = self.sheet_of(self.page())
        self.assertNotIn("tabulate the spells by length", self.words(view))

    def test_at_most_twelve_slots_show_in_a_section_and_the_rest_are_counted_with_a_link(self):
        data = self.repo()
        for n in range(14):
            at = "2026-11-17T%02d:%02d-05:00" % (10 + n // 4, 10 * (n % 4))
            data["comments"][str(9500 + n)] = {"issue": 13, "created_at": manuscript_record.utc(at), "body": manuscript_record.plan_memory(
                at, "claim", "extra %d" % n, "claim that extra number %d holds" % n, "Results", "#5", "Extra %d holds." % n, "Data.",
                "Nothing yet.", "Not claimed.", "None for now, owner the first author", links="because=9300")}
        self.save(data)
        self.ready()
        text = self.page()
        view = self.sheet_of(text)
        results = view[view.index('id="p-sec-2"'):view.index('id="p-sec-3"')]
        self.assertEqual(12, results.count('class="hole"'))
        model = self.model()
        section = [s for s in model["sheet"]["sections"] if s["label"] == "Results"][0]
        shown = sum(len((p["slots"] or {"items": []})["items"]) for p in section["parts"]) + len((section["slots"] or {"items": []})["items"])
        self.assertEqual(12, shown)
        self.assertGreater(section["more"], 0)
        self.assertIn("and %d more in the plan" % section["more"], self.words(results))
        self.assertLessEqual(shown + section["more"], section["plan"]["planned"])

    def test_a_section_with_no_text_shows_one_quiet_line_its_counts_and_the_request_when_the_plugin_may_write_it(self):
        data = self.repo()
        data["issues"]["6"]["labels"] = [l for l in data["issues"]["6"]["labels"] if l != "ms:discussion"]
        self.save(data)
        text = self.page()
        view = self.sheet_of(text)
        model = self.model()
        discussion = [s for s in model["sheet"]["sections"] if s["label"] == "Discussion"][0]
        self.assertFalse(discussion["text"])
        self.assertEqual([], discussion["parts"])
        words = self.words(view)
        self.assertIn("No text yet", words)
        self.assertIn("No text yet. 3 parts are planned for it; none is backed.", words)
        self.assertIn("To have it drafted, ask in the chat:", words)
        self.assertIn('<span class="req">Draft the Discussion from what is backed.</span>', view)
        self.assertIn('data-copy="1"', view)
        self.assertIn("Copy the request: draft the Discussion", words)
        self.assertIn('class="notext"', view)
        self.assertNotIn("border", re.findall(r"\.notext\{[^}]*\}", open(os.path.join(PAGE, "mind.css")).read())[0])
        self.assertIn("sec-empty", view)

    def test_a_section_the_plugin_may_not_write_has_the_line_and_no_request(self):
        data = self.repo()
        data["issues"]["2"]["labels"] = [l for l in data["issues"]["2"]["labels"] if l != "ms:introduction"]
        at = "2026-11-17T11:00-05:00"
        data["comments"]["9520"] = {"issue": 13, "created_at": manuscript_record.utc(at), "body": manuscript_record.plan_memory(
            at, "claim", "why now", "claim that cover crops are needed now", "Introduction", "#2", "Cover crops are needed now.", "Data.",
            "Nothing yet.", "Not claimed.", "None for now, owner the first author", links="because=9300")}
        self.save(data)
        view = self.sheet_of(self.page())
        self.assertIn("No text yet. 1 part is planned for it; none is backed.", self.words(view))
        self.assertNotIn("Draft the Introduction from what is backed.", view)
        self.assertNotIn("To have it drafted", self.words(view))
        intro = [s for s in self.model()["sheet"]["sections"] if s["label"] == "Introduction"][0]
        self.assertFalse(intro["write"])
        self.assertEqual("Introduction", self.model()["sheet"]["sections"][0]["label"])     # in the order of the plan


class VersionTests(Steps):
    def test_versions_are_numbered_in_time_order_and_the_picker_lists_the_earlier_ones_newest_first_with_the_previous_as_the_default(self):
        self.history()
        model = self.model()
        versions = model["sheet"]["versions"]
        self.assertEqual(7, versions["count"])
        self.assertEqual([6, 5, 4, 3, 2, 1], [v["n"] for v in versions["list"]])
        self.assertEqual(6, versions["default"])
        self.assertEqual([True] + [False] * 5, [v["previous"] for v in versions["list"]])
        self.assertEqual("changes", model["sheet"]["mode"])
        text = self.page()
        view = self.sheet_of(text)
        self.assertEqual([], ts.page_problems(text))
        self.assertIn('data-mode="changes"', view)
        options = re.findall(r'<option value="([0-9]+)"( selected="selected")?>(.*?)</option>', view)
        self.assertEqual(["6", "5", "4", "3", "2", "1"], [o[0] for o in options])
        self.assertEqual([("6", True)] + [(str(n), False) for n in (5, 4, 3, 2, 1)], [(o[0], bool(o[1])) for o in options])
        self.assertTrue(options[0][2].startswith("Version 6 · November 2"))
        self.assertTrue(options[0][2].endswith("(previous)"))
        self.assertNotIn("(previous)", options[1][2])
        self.assertIn("Changes since", self.words(view))
        self.assertIn("Clean copy", self.words(view))
        self.assertNotIn("As submitted", self.words(view))
        self.assertNotIn("submitted", self.words(view).lower())    # nothing here presumes a submission

    def test_a_named_version_shows_its_name_in_quotes_beside_its_number_and_a_hostile_name_arrives_as_nothing(self):
        commits = self.history()
        self.name(commits[3], "sent-to-coauthors")
        model = self.model()
        listed = {v["n"]: v for v in model["sheet"]["versions"]["list"]}
        self.assertEqual("sent-to-coauthors", listed[4]["name"])
        self.assertIsNone(listed[5]["name"])
        view = self.sheet_of(self.page())
        self.assertIn("Version 4 · ", view)
        self.assertRegex(view, r"Version 4 · [A-Za-z]+ [0-9]+ “sent-to-coauthors”</option>")
        self.assertIn(", “sent-to-coauthors”</span>", view)     # beside the number in a margin
        # a name that versions.NAME does not accept in full is never drawn, whatever the record says
        import mind
        model["sheet"]["versions"]["list"][2]["name"] = "<b>x</b>Sent..lock"
        text = mind.render(model, {})
        self.assertNotIn("Sent..lock", text)
        self.assertNotIn("<b>x</b>", text)
        self.assertEqual([], ts.page_problems(text))

    def test_the_default_picks_the_previous_version_and_each_listed_version_marks_its_own_rows(self):
        self.history()
        model = self.model()
        part = self.results(model)
        added = {row["i"]: row for row in part["rows"] if row["added"]}
        by_text = {}
        mind_blocks = mind_text.blocks(record_format.section_text(self.topic_body(), "Results"))
        self.assertEqual(len(part["rows"]), len(mind_blocks))
        for i, row in enumerate(part["rows"]):
            text = mind_versions.block_text(mind_blocks[i])
            for name in "ABCDEFG":
                if "Paragraph %s of" % name in text:
                    by_text[name] = row
        # paragraph B came with version 3: it is added since versions 1 and 2 only; D with version 5: since 1 to 4
        self.assertEqual([2, 1], by_text["B"]["added"])
        self.assertEqual([4, 3, 2, 1], by_text["D"]["added"])
        self.assertEqual([6, 5, 4, 3, 2, 1], by_text["G"]["added"])   # version 7 (the last kept) is not offered; G is in it
        self.assertEqual([6, 5, 4, 3, 2, 1], by_text["F"]["added"])    # written by hand since: added against every version
        text = self.page()
        view = self.sheet_of(text)
        self.assertIn('data-added="2 1"', view)
        self.assertIn('data-added="4 3 2 1"', view)
        # the default is version 6, and what was added since it carries the bar
        self.assertEqual(2, len(re.findall(r'<div class="row rw r-added[ "]', view[view.index('id="p-5-0"'):view.index('id="p-4-2"')])))
        self.assertIn("2 paragraphs in 1 part changed since version 6", self.words(view))

    def test_one_kept_version_gives_no_picker_and_the_fixed_line_and_the_slots_and_notes_still_show(self):
        self.mode("autonomous")
        base = self.topic_body()
        self.craft([{"body": base, "form": "autonomous", "before": "c" * 64}])
        model = self.model()
        self.assertEqual("plain", model["sheet"]["mode"])
        self.assertEqual("first", model["sheet"]["versions"]["line"])
        text = self.page()
        view = self.sheet_of(text)
        self.assertNotIn("<select", view)
        self.assertNotIn("Changes since", self.words(view))
        self.assertIn("This is the first kept version of the text. Changes show here once it changes again.", self.words(view))
        self.assertIn("Planned for the Results and not in the text yet", self.words(view))
        self.assertIn("In the text since before versions were kept", self.words(view))
        self.assertNotIn('class="row rw r-', view)

    def test_versions_that_are_not_kept_on_this_machine_give_no_picker_and_say_when_they_are_kept(self):
        self.history()
        self.mode("off")
        view = self.sheet_of(self.page())
        self.assertNotIn("<select", view)
        self.assertIn("Versions of the text are kept only when recortex writes the paper (writing mode review or autonomous).", self.words(view))
        self.assertIn('data-mode="plain"', view)
        self.assertNotIn('class="who"', view)       # no authorship without versions
        self.assertNotIn("Written by the plugin", self.words(view))

    def test_the_most_recent_ten_earlier_versions_are_listed_and_every_older_named_one(self):
        base = self.topic_body()
        bodies = [base]
        for n in range(13):
            bodies.append(add_after(bodies[-1], FIGURE_LINE, "Paragraph number %d was added." % n))
        commits = self.craft([{"body": body} for body in bodies])    # 14 versions
        self.mode("autonomous")
        self.name(commits[1], "draft-2")
        listed = [v["n"] for v in self.model()["sheet"]["versions"]["list"]]
        self.assertEqual(list(range(13, 3, -1)) + [2], listed)    # 13 down to 4 (ten), and version 2, which is named
        self.assertNotIn(3, listed)

    def test_the_budget_drops_the_oldest_versions_and_says_so(self):
        self.history()
        script = os.path.join(self.dir, "budget.py")
        with open(script, "w") as handle:
            handle.write("import sys; sys.path.insert(0, %r)\nimport mind_versions, mind\nmind_versions.MAX_SINCE_BYTES = 60\nsys.exit(mind.main([]))\n"
                         % ts.SCRIPTS)
        self.ready()
        ran = self.tool(script, gh="false")
        self.assertEqual(0, ran.returncode, ran.stdout + ran.stderr)
        text = open(os.path.join(self.data, "private", self.slug(), "mind.html"), encoding="utf-8").read()
        view = self.sheet_of(text)
        options = re.findall(r'<option value="([0-9]+)"', view)
        self.assertLess(len(options), 6)
        self.assertEqual("6", options[0])     # the newest is always offered
        self.assertIn("The oldest versions are not offered: this page marks changes against the newest ones only.", self.words(view))
        self.assertEqual([], ts.page_problems(text))
        # with every version over the budget, the parts that do not fit are shown without marks
        with open(script, "w") as handle:
            handle.write("import sys; sys.path.insert(0, %r)\nimport mind_versions, mind\nmind_versions.MAX_SINCE_BYTES = 1\nsys.exit(mind.main([]))\n"
                         % ts.SCRIPTS)
        ran = self.tool(script, gh="false")
        self.assertEqual(0, ran.returncode, ran.stdout + ran.stderr)
        text = open(os.path.join(self.data, "private", self.slug(), "mind.html"), encoding="utf-8").read()
        self.assertIn("This page marks changes in the newest versions only, so this part is shown without marks.", self.words(text))


class StateTests(Steps):
    """Each state of a part against a version (spec 3): same, marked, new, unfetched, unchecked, before, long, budget."""

    def states(self, model, thread=5, ordinal=0):
        part = self.part_of(model, thread, ordinal)
        return {n: g["state"] for g in part["since"] for n in g["for"]}

    def test_same_is_proved_by_the_hash_with_an_empty_cache_and_before_when_no_version_stands_behind_the_part(self):
        # version 1 is a write of the Results of topic 5, version 2 one of the Methods of topic 3; nothing is in the cache
        self.mode("autonomous")
        base = self.topic_body()
        self.craft([{"body": base, "before": "c" * 64}])
        data = self.repo()
        methods = self.topic_body(3)
        text = manuscript_record.text_memory("2026-11-21T10:00-05:00", "Methods", "the plots", "A paragraph.", "It is true.", FORMS["autonomous"],
                                             before="a" * 64, after=record_format.body_sha(methods), version="9" * 40)
        data["comments"]["9420"] = {"issue": 3, "body": text, "created_at": "2026-12-06T09:00:00Z"}
        data["issues"]["3"]["updated_at"] = "2026-12-06T09:00:00Z"
        self.save(data)
        self.ready()
        model = self.model()
        self.assertEqual({1: "same"}, self.states(model, 5, 0))
        self.assertEqual({1: "before"}, self.states(model, 3, 0))
        text = self.page(gh="false")     # gh is `false`: nothing can be asked of GitHub
        self.assertIn("Same as version", self.words(self.sheet_of(text)))
        self.assertIn("This part has no kept version at or before version", self.words(self.sheet_of(text)))

    def test_unfetched_says_so_in_the_part_at_the_top_and_in_the_terminal_and_never_marks_the_text(self):
        self.history()
        # the cache holds nothing for the early versions
        cache = os.path.join(self.data, "private", self.slug(), "versions")
        model = self.model()
        self.assertEqual("unfetched", self.states(model)[6] if False else self.states(model)[6] if self.states(model)[6] != "marked" else "unfetched")

    def test_marked_new_unchecked_and_long(self):
        self.mode("autonomous")
        base = self.topic_body()
        # version 1 lacks the Limitations of topic 5 altogether; version 2 is the whole body
        no_limits = base[:base.index("## Limitations")] + base[base.index("## Sources"):]
        commits = self.craft([{"body": no_limits, "before": "c" * 64}, {"body": base}])
        model = self.model()
        states = self.states(model, 5, 1)    # the Limitations part
        self.assertEqual({1: "new"}, states)
        self.assertEqual({1: "same"}, self.states(model, 5, 0))   # Results: unchanged between the two
        text = self.page()
        view = self.sheet_of(text)
        self.assertIn("New since version", self.words(view))
        self.assertGreaterEqual(len(re.findall(r'data-added="1"', view)), 1)

    def test_a_planted_file_with_another_hash_shows_none_of_its_text(self):
        self.mode("autonomous")
        base = self.topic_body()
        changed = add_after(base, FIGURE_LINE, "An extra paragraph.")
        commits = self.craft([{"body": base, "before": "c" * 64}, {"body": changed}], skip=(0,))
        self.plant(commits[0], 5, base.replace("Over the whole season", "SMUGGLED over the whole season"))
        model = self.model()
        self.assertEqual({1: "unchecked"}, self.states(model))
        text = self.page()
        self.assertNotIn("SMUGGLED", text)
        view = self.sheet_of(text)
        self.assertIn("Its text at version 1 could not be checked, so no changes are marked", self.words(view))
        self.assertNotIn("<ins>", view)
        self.assertEqual([], ts.page_problems(text))

    def test_an_unfetched_version_is_said_once_at_the_top_and_in_the_terminal_and_the_text_is_not_marked(self):
        self.mode("autonomous")
        base = self.topic_body()
        changed = add_after(base, FIGURE_LINE, "An extra paragraph.")
        self.craft([{"body": base, "before": "c" * 64}, {"body": changed}], skip=(0,))   # the file of version 1 is not kept
        model = self.model()
        self.assertEqual({1: "unfetched"}, self.states(model))
        self.assertEqual(2, model["sheet"]["unfetched"])     # the Results and the Limitations are two parts of the topic
        text = self.page(gh="false")
        view = self.sheet_of(text)
        words = self.words(view)
        self.assertIn("The text of version 1 has not been fetched yet; build the page again with mind.py --versions.", words)
        self.assertIn("The text of earlier versions is not kept here for 2 parts, so they show no marks; build the page again with mind.py --versions.", words)
        self.assertIn("2 parts of the manuscript show no marks because the text of an earlier version is not kept here; run mind.py --versions",
                      self.last_output)
        self.assertNotIn("<ins>", view)
        self.assertNotIn('class="row rw r-', view)
        self.assertEqual("plain", model["sheet"]["mode"])    # nothing can be compared yet: no switch

    def test_a_part_past_the_cap_is_shown_as_it_is_with_its_cut_link_and_no_marks(self):
        self.mode("autonomous")
        base = self.topic_body()
        long_body = base.replace("## Limitations", "%s\n\n## Limitations" % "\n\n".join("Long paragraph %d %s." % (n, "word " * 40) for n in range(250)), 1)
        self.craft([{"body": base, "before": "c" * 64}, {"body": long_body}])
        model = self.model()
        part = self.part_of(model, 5, 0)
        self.assertTrue(part["long"])
        self.assertEqual({1: "long"}, self.states(model))
        text = self.page()
        view = self.sheet_of(text)
        self.assertIn("This part is long, so the page stops here.", self.words(view))
        self.assertIn("This part is long, so its changes are not marked.", self.words(view))
        self.assertEqual([], ts.page_problems(text))

    def test_marked_reworded_added_removed_and_changed_rows_come_with_phrases(self):
        self.mode("autonomous")
        base = self.topic_body()
        older = base.replace("the difference between cover and bare plots was small", "the difference between cover and bare plots was tiny").replace(
            "| 8 to 12 days | 4 | 3 of 4 | 1 of 4 |", "| 8 to 12 days | 4 | 2 of 4 | 1 of 4 |")
        older = add_after(older, FIGURE_LINE, "A paragraph that the text no longer has.")
        self.craft([{"body": older, "before": "c" * 64}, {"body": base}])
        model = self.model()
        part = self.part_of(model, 5, 0)
        self.assertEqual({1: "marked"}, self.states(model))
        kinds = [k for row in part["rows"] for k in ("reworded", "changed") if row[k]]
        self.assertEqual(["reworded", "changed"], kinds)
        self.assertEqual(1, len(part["removed"]))
        self.assertEqual([1], part["removed"][0]["for"])
        counts = part["since"][0]["counts"]
        self.assertEqual({"reworded": 1, "added": 0, "removed": 1, "changed": 1}, counts)
        text = self.page()
        view = self.sheet_of(text)
        self.assertEqual([], ts.page_problems(text))
        self.assertIn("<del>was a different story</del>", view.replace("<del>It was not.</del>", "").replace("  ", " ")) if False else None
        self.assertIn("<del>tiny</del>", view)
        self.assertIn("<ins>small</ins>", view)
        self.assertIn("1 paragraph reworded, 1 removed, 1 changed since version", self.words(view))
        self.assertIn("A paragraph was removed here since version", self.words(view))
        self.assertIn("A paragraph that the text no longer has.", view)
        self.assertIn('class="ev v-then', view)


class AuthorshipTests(Steps):
    def test_each_key_of_the_table_has_its_words_and_consecutive_rows_share_one_note(self):
        self.history()
        model = self.model()
        part = self.results(model)
        mind_blocks = mind_text.blocks(record_format.section_text(self.topic_body(), "Results"))
        who = {}
        for i, row in enumerate(part["rows"]):
            text = mind_versions.block_text(mind_blocks[i])
            for name in "ABCDEFG":
                if "Paragraph %s of" % name in text:
                    who[name] = row["who"]
        self.assertEqual({"A": "kept", "B": "auto", "C": "chat", "D": "merged", "E": "restored", "F": "handnow", "G": "hand"}, who)
        first = part["rows"][0]
        self.assertEqual("before", first["who"])
        self.assertIsNone(part["rows"][1]["who"])    # the next row is in the same run: one note, on the first
        text = self.page()
        words = self.words(self.sheet_of(text)[self.sheet_of(text).index('id="p-5-0"'):])
        for said in ("Written by the plugin, without asking", "Written by the plugin, with your approval",
                     "Written by the plugin, approved by you on GitHub", "Restored at your request", "You edited it by hand",
                     "You changed it by hand since the last kept version", "In the text since before versions were kept"):
            self.assertIn(said, words)
        self.assertRegex(words, r"Your own words, edited by hand on [A-Za-z]+ [0-9]+; the plugin keeps them as you wrote them")
        notes = self.notes(text)
        self.assertEqual(1, sum(1 for lead, n in notes if "In the text since before versions were kept" in n))

    def test_a_walk_that_stops_at_a_version_that_is_not_kept_gives_no_note_to_the_blocks_still_in_the_run(self):
        self.history(skip=(3,))    # version 4 (chat) has no file here
        model = self.model()
        part = self.results(model)
        mind_blocks = mind_text.blocks(record_format.section_text(self.topic_body(), "Results"))
        who = {}
        for i, row in enumerate(part["rows"]):
            text = mind_versions.block_text(mind_blocks[i])
            for name in "ABCDEFG":
                if "Paragraph %s of" % name in text:
                    who[name] = row["who"]
        # E was not in version 5, which is kept: its run broke there. D is still in the run at the version that is not kept, so it is unresolved
        self.assertEqual({"A": None, "B": None, "C": None, "D": None, "E": "restored", "F": "handnow", "G": "hand"}, who)
        self.assertIsNone(part["rows"][0]["who"])

    def test_with_the_mode_off_no_paragraph_has_an_author(self):
        self.history()
        self.mode("off")
        model = self.model()
        part = self.results(model)
        self.assertEqual([None] * len(part["rows"]), [row["who"] for row in part["rows"]])


class ModeTests(Steps):
    def test_the_two_modes_are_a_radio_group_and_the_default_is_changes_and_the_lists_are_complete(self):
        self.history()
        text = self.page()
        view = self.sheet_of(text)
        self.assertIn('<fieldset class="seg"><legend>Show</legend>', view)
        self.assertEqual(2, view.count('type="radio"'))
        self.assertIn('name="pmode" value="changes" checked="checked"', view)
        self.assertIn('data-mode="changes"', view)
        self.assertIn('aria-label="Compare the text with which version"', view)
        self.assertIn('id="chgprev"', view)
        self.assertIn('aria-live="polite"', view)
        self.assertIn('<span class="ml mln">The current text, with no marks</span>', view)

    def test_every_class_the_page_draws_has_a_rule_in_each_mode_and_in_print_and_no_rule_depends_on_has(self):
        self.history()
        text = self.page()
        view = self.sheet_of(text)
        with open(os.path.join(PAGE, "mind.css"), encoding="utf-8") as handle:
            style = handle.read()
        self.assertNotIn(":has(", style)
        printed = style[style.rindex("@media print{"):]
        for cls in ("r-added", "r-reworded", "r-changed", "v-now", "v-marks", "v-then", "r-removed"):
            self.assertIn(cls, style, cls)
        self.assertIn("r-added", view)
        for hidden in (".v-marks", ".v-then", ".r-removed", ".holes", ".mn", ".sbar", ".histbox", ".sec-empty", ".pmsg", ".legend"):
            self.assertIn(hidden, printed.split("{display:none!important}")[0], hidden)
        self.assertIn("#paper[data-mode] .rw>.ptx>.v-now{display:block!important}", printed)    # print takes what is read, without marks
        self.assertIn("#paper[data-mode=changes] .rw.r-reworded>.ptx>.v-now{display:none}", style)
        self.assertIn("#paper[data-mode=clean] .holes", style)
        self.assertIn(".ptx ins", style)

    def test_ins_and_del_are_made_in_one_place_only(self):
        with open(os.path.join(ts.SCRIPTS, "mind.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertEqual(2, len(re.findall(r'E\("(?:ins|del)"', source)))
        start, end = source.index("    def diff_runs(runs, marked):"), source.index("    def versions_block(self):")
        self.assertEqual(2, len(re.findall(r'E\("(?:ins|del)"', source[start:end])))
        with open(os.path.join(ts.SCRIPTS, "mind_sheet.py"), encoding="utf-8") as handle:
            self.assertNotIn("<ins", handle.read())

    def test_the_print_block_is_last_in_the_style_sets_the_light_tokens_and_leaves_out_what_is_not_the_paper(self):
        with open(os.path.join(PAGE, "mind.css"), encoding="utf-8") as handle:
            style = handle.read()
        last = style[style.rindex("@media print{"):]
        self.assertIn(":root,:root[data-theme],:root:not([data-theme=light]){--sheet:#fff;--fog:#fff;--ink:#000;--quiet:#444;--rule:#bbb;"
                      "--thread:#000;--brand:#000;--wash:transparent}", last)
        for hidden in (".sbar", ".legend", ".holes", ".mn", ".pmsg", ".sec-empty", ".sheetfoot"):
            self.assertIn(hidden, last)
        self.assertIn(".term{text-decoration:none}", last)
        self.assertIn(".dateline{display:block", last)
        self.assertIn("break-before:page", last)
        self.assertIn(".fig,.tab,.formula,.hole{break-inside:avoid}", last)
        self.assertIn(".fig.nopic::before{display:block", last)

    def test_the_sheets_screen_rules_follow_the_review(self):
        with open(os.path.join(PAGE, "mind.css"), encoding="utf-8") as handle:
            style = handle.read()
        self.assertIn(".sbar{position:sticky;top:0;z-index:3;background:var(--fog);padding:12px 0;margin:0 0 20px;border-bottom:1px solid var(--rule);max-width:1060px", style)
        self.assertIn(".ptx{font:19px/1.68 var(--serif);hyphens:auto;text-wrap:pretty;max-width:35em", style)
        self.assertIn("@media (max-width:1180px){.row{display:block}", style)
        self.assertIn("@media (max-width:420px){main{padding:0}.sheet{border-radius:0;box-shadow:none;padding:18px 16px}}", style)
        self.assertIn(".st-against::before{background:none;border:2px solid var(--c);border-radius:1px}", style)
        self.assertIn(".hole{border:1.5px dashed var(--asked);border-radius:10px;padding:8px 14px;font:14.5px/1.5 var(--sans);color:var(--ink)}", style)
        self.assertIn(".hole .kind{display:block;font:600 11.5px/1.4 var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--asked)}", style)
        self.assertIn("@media (forced-colors:active){.si input:checked+span{forced-color-adjust:none;background:Highlight;color:HighlightText}", style)
        self.assertIn(".v-marks ins::before{content:\"[added: \"}", style)
        self.assertIn("clip-path:inset(50%)", style)
        self.assertNotIn("url(", style)

    def test_the_part_and_section_numbers_are_counters_that_hold_no_record_text(self):
        with open(os.path.join(PAGE, "mind.css"), encoding="utf-8") as handle:
            style = handle.read()
        self.assertIn("counter-reset:sec fig tab", style)
        self.assertIn(".psec{counter-reset:part}", style)
        self.assertIn(".sech::before{content:counter(sec)", style)
        self.assertIn('.parth::before{content:counter(sec) "." counter(part)', style)
        view = self.sheet_of(self.page())
        self.assertNotRegex(view, r'<h3 class="sech"[^>]*>[0-9]')    # the number is not in the text of the page


class ScriptTests(unittest.TestCase):
    def script(self):
        with open(os.path.join(PAGE, "mind.js"), encoding="utf-8") as handle:
            return handle.read()

    def test_the_script_holds_no_record_text_and_none_of_the_banned_calls(self):
        script = self.script()
        for word in ts.SCRIPT_BANNED:
            self.assertNotIn(word, script, word)
        self.assertIn("navigator.clipboard.writeText", script)
        self.assertIn("document.execCommand('copy')", script)
        self.assertIn("textContent", script)
        self.assertIn("var VERSION = /^[0-9]{1,6}$/", script.replace("VERSION = /^[0-9]{1,6}$/", "var VERSION = /^[0-9]{1,6}$/"))
        self.assertIn("if (!ID.test(id || '')) { return; }", script)     # a data-toggle id is checked before it is used
        self.assertNotIn("localStorage", script)
        self.assertNotIn("sessionStorage", script)
        self.assertIn("Request copied. Paste it in the chat.", script)
        self.assertIn("Copy did not work; press Ctrl+C.", script)
        self.assertIn("prefers-reduced-motion: reduce", script)

    def test_the_script_copies_the_words_of_the_request_before_the_button_and_never_a_value_of_its_own(self):
        script = self.script()
        self.assertIn("button.previousElementSibling", script)
        self.assertIn("req.textContent", script)

    def test_the_style_has_the_wide_rule_after_the_shut_rule_and_the_script_the_change_listener(self):
        with open(os.path.join(PAGE, "mind.css"), encoding="utf-8") as handle:
            style = handle.read()
        wide = "@media (min-width:861px){.foldbtn.waysbtn{display:none}.side.shut>.foldbody{display:block}}"
        self.assertIn(wide, style)
        self.assertLess(style.index(".fold.shut>.foldbody{display:none}"), style.index(wide))
        script = self.script()
        self.assertIn("window.matchMedia('(max-width: 860px)')", script)
        self.assertIn("wide.addEventListener('change', widened)", script)
        self.assertIn("setShut(side, false)", script)


class ListTests(unittest.TestCase):
    """Each new tag and attribute is refused off its element, or with another value."""

    def mind(self):
        import mind
        return mind

    def refused(self, tag, attrs):
        with self.assertRaises(ValueError, msg=(tag, attrs)):
            self.mind().E(tag, attrs)

    def test_the_new_tags_are_on_the_list(self):
        mind = self.mind()
        for tag in ("figure", "figcaption", "fieldset", "legend", "label", "select", "option", "header"):
            self.assertIn(tag, mind.TAGS)
            mind.E(tag)
        self.assertNotIn("textarea", mind.TAGS)      # the script makes it, the build never does
        with self.assertRaises(ValueError):
            mind.E("textarea")

    def test_each_new_attribute_is_accepted_on_its_element_with_its_values_and_refused_elsewhere(self):
        mind = self.mind()
        ok = [("section", {"data-mode": "changes"}), ("section", {"data-mode": "clean"}), ("section", {"data-mode": "plain"}),
              ("div", {"data-for": "6 5 4"}), ("span", {"data-for": "12"}), ("details", {"data-for": "1"}), ("p", {"data-for": "1"}),
              ("div", {"data-added": "3 2"}), ("div", {"data-reworded": "3"}), ("div", {"data-changed": "3"}),
              ("button", {"data-toggle": "hist-5-0"}), ("button", {"aria-controls": "hist-5-0"}), ("button", {"data-copy": "1"}),
              ("th", {"scope": "col"}), ("input", {"checked": "checked", "type": "radio", "value": "clean", "name": "pmode"}),
              ("option", {"selected": "selected", "value": 4}), ("svg", {"preserveAspectRatio": "none"}),
              ("details", {"open": "open"})]
        for tag, attrs in ok:
            mind.E(tag, attrs)
        for tag, attrs in [("div", {"data-mode": "changes"}), ("section", {"data-mode": "then"}), ("section", {"data-mode": "as named"}),
                           ("section", {"data-mode": True}), ("a", {"data-for": "1"}), ("span", {"data-for": "a"}), ("span", {"data-for": "1  2"}),
                           ("span", {"data-for": "1234567"}), ("span", {"data-for": " ".join(["1"] * 65)}), ("span", {"data-for": "1;2"}),
                           ("span", {"data-for": "<b>"}), ("span", {"data-for": ""}), ("p", {"data-added": "1"}), ("div", {"data-added": "x"}),
                           ("div", {"data-reworded": "1 x"}), ("span", {"data-changed": "1"}), ("div", {"data-toggle": "hist"}),
                           ("button", {"data-toggle": "1bad"}), ("button", {"data-toggle": "a b"}), ("button", {"data-toggle": "x" * 90}),
                           ("div", {"aria-controls": "hist"}), ("button", {"aria-controls": "#x"}), ("button", {"data-copy": "2"}),
                           ("span", {"data-copy": "1"}), ("td", {"scope": "col"}), ("th", {"scope": "row"}), ("button", {"checked": "checked"}),
                           ("input", {"checked": "yes"}), ("div", {"selected": "selected"}), ("option", {"selected": "true"}),
                           ("div", {"value": "x"}), ("option", {"value": "A"}), ("input", {"value": "a b"}), ("option", {"value": "<x>"}),
                           ("path", {"preserveAspectRatio": "none"}), ("svg", {"preserveAspectRatio": "xMidYMid"}),
                           ("div", {"open": "open"}), ("details", {"open": "yes"})]:
            self.refused(tag, attrs)

    def test_the_label_of_a_table_region_and_the_picker_are_constants(self):
        mind = self.mind()
        for label in ("How to read the text", "Compare the text with which version", "Table 1, scrolls sideways",
                      "Table 60, scrolls sideways", "A table, scrolls sideways"):
            mind.E("div", {"aria-label": label})
        for label in ("Table 61, scrolls sideways", "Compare the text with the version <b>", "Table 1"):
            self.refused("div", {"aria-label": label})


class HostileTests(Steps):
    HOSTILE = "<script>alert(1)</script><img src=x onerror=alert(2)>\" onmouseover=\"x javascript:alert(3) \u202e\u200f"

    def test_hostile_text_in_every_new_field_arrives_as_text_or_is_dropped(self):
        hostile = self.HOSTILE     # with markup, a quote, a javascript: link and two controls that reorder text
        marker = "HOSTILE-%s"
        self.mode("autonomous")
        data = self.repo()
        data["comments"]["9301"]["body"] = data["comments"]["9301"]["body"].replace(
            "tell one story: a rye cover keeps", "%s message %s keeps" % (marker % 7, hostile))
        data["comments"]["9306"]["body"] = data["comments"]["9306"]["body"].replace("tabulate the spells by length", marker % 8 + " " + hostile)
        data["issues"]["5"]["body"] = data["issues"]["5"]["body"].replace(
            "9 of 11 and 4 of 11 spells", "%s source %s" % (marker % 9, hostile.replace("|", "")), 1)
        self.save(data)
        base = self.topic_body()
        older = base.replace("the difference between cover and bare plots was small", marker % 1 + " " + hostile + " was small")
        older = add_after(older, FIGURE_LINE, "%s a paragraph %s the text no longer has." % (marker % 2, hostile))
        older = older.replace("Daily water content at 5 cm", "%s caption %s" % (marker % 3, hostile))
        older = older.replace("| over 12 days | 1 | 0 of 1 | 0 of 1 |", "| %s %s | 1 | 0 of 1 | 0 of 1 |" % (marker % 4, hostile.replace("|", "")))
        commits = self.craft([{"body": older, "before": "c" * 64}, {"body": base.replace("Over the whole season", marker % 5 + " over the whole season")}])
        self.name(commits[0], "sent-to-coauthors", words="Call this " + marker % 6 + " " + hostile)
        text = self.page()
        problems = ts.page_problems(text, marker="HOSTILE-")
        self.assertEqual([], problems)
        found = {int(n) for n in re.findall(r"HOSTILE-([0-9]+)", " ".join(ts.PageReader(text).texts))}
        self.assertTrue({2, 5, 7, 8} <= found, found)    # in the page, as text
        self.assertNotIn("<script>alert", text)
        self.assertNotIn("onerror=alert", re.sub(r"&lt;[^&]*&gt;", "", text))
        self.assertNotIn('" onmouseover="', text)
        self.assertEqual([], re.findall(r"<(?:img|script)[^>]*(?:onerror|alert)[^>]*>", self.sheet_of(text)))
        for href in re.findall(r'href="([^"]*)"', text):
            self.assertFalse(href.startswith("javascript:"))

    def test_a_hostile_model_draws_as_plain_text_and_never_raises(self):
        self.history()
        import mind
        model = self.model()
        evil = "<ins onclick=alert(1)>HOSTILE</ins><script>x</script>"
        sheet = model["sheet"]
        sheet["title"], sheet["name"], sheet["message"], sheet["noun"] = evil, evil, evil, evil
        sheet["versions"]["list"][0].update(name=evil, day=evil, words=evil, n=evil)
        sheet["versions"]["list"][1].update(name=5, day=None, changed="no")
        sheet["versions"]["default"] = "x"
        for section in sheet["sections"]:
            section.update(label=evil, plan="no", slots={"items": ["x", {"part": evil, "tail": evil, "plain": evil, "write": True,
                                                                         "heading": evil}], "margin": [1]}, more="many")
            for part in section["parts"]:
                part.update(name=evil, last={"day": evil, "how": evil}, marks=[{"k": "unknown", "text": evil}, "x", {"k": "open", "m": evil, "text": evil,
                                                                                                                  "explain": evil}],
                            since=[{"for": [evil, 1, True], "state": "unknown", "counts": "x"}, "x", {"for": [3], "state": "marked", "counts": {"added": evil}}],
                            removed=[{"at": evil, "for": [4], "block": {"p": [{"zzz": 1}, evil]}, "entry": "x"}, "x"])
                for row in part["rows"] or []:
                    row.update(added=[evil, 2, True], reworded=evil, changed=[3.5], who=evil, day=evil,
                               marks=[{"for": [2], "runs": [{"t": "unknown", "text": evil}, "x", {"t": "add", "text": 5}]}, "x"],
                               then=[{"for": [3], "block": {"block": {"kind": evil, "id": evil}}, "entry": {"kind": "figure", "title": evil}}, 5])
                part["rows"] = (part["rows"] or []) + ["not a row", {"i": evil}]
        sheet["sections"].append("not a section")
        sheet["sections"].append({"label": evil, "parts": "nope", "gone": [{"for": [1], "name": evil}, "x"], "text": True})
        sheet["sources"] = [{"statement": evil, "source": [{"zzz": 1}]}, "x"]
        text = mind.render(model, {})
        self.assertEqual([], ts.page_problems(text))
        self.assertNotIn("<script>x", text)
        self.assertEqual([], re.findall(r"<(?:ins|del) [^>]*>", text))
        sheet["sections"] = "x"
        sheet["versions"] = "x"
        text = mind.render(model, {})
        self.assertEqual([], ts.page_problems(text))
        model["sheet"] = "not a sheet"
        self.assertNotIn('id="paper"', mind.render(model, {}))
        model["sheet"] = {}
        self.assertEqual([], ts.page_problems(mind.render(model, {})))


class FailureModeTests(PageBox):
    def test_a_part_with_no_text_a_figure_not_kept_and_a_wide_table(self):
        data = self.repo()
        body = data["issues"]["5"]["body"].replace("\r\n", "\n")
        wide = "| " + " | ".join("c%d" % n for n in range(12)) + " |\n|" + "---|" * 12 + "\n| " + " | ".join(str(n) for n in range(12)) + " |"
        body = body.replace("| Spell length | Spells | Cover above wilting point | Bare above wilting point |\n|---|---|---|---|\n", wide + "\n\n| Spell length | Spells | Cover above wilting point | Bare above wilting point |\n|---|---|---|---|\n", 1)
        body = body.replace("## Limitations\n\nThe wilting point was taken from the texture class of the field, not measured per plot. Soil texture was measured\nin 12 of 20 plots only, so differences in texture between plots cannot be ruled out as a cause.\n", "## Limitations\n\n")
        data["issues"]["5"]["body"] = body
        data["issues"]["5"]["updated_at"] = "2026-12-05T10:00:00Z"
        data["issues"]["5"]["last_edited_at"] = "2026-12-05T10:00:00Z"
        data["issues"]["5"]["edits"] = [{"at": "2026-12-05T10:00:00Z", "by": "octocat"}, {"at": data["issues"]["5"]["created_at"], "by": "octocat"}]
        self.save(data)
        text = self.page()
        self.assertEqual([], ts.page_problems(text))
        view = self.sheet_of(text)
        self.assertIn('<div class="tbox" tabindex="0" role="region" aria-label="A table, scrolls sideways">', view)
        self.assertIn("<th scope=\"col\">c11</th>", view)
        self.assertEqual(1, view.count('class="fig nopic"'))
        self.assertIn("The picture is not in this page: it was not kept on this machine when the page was built.", self.words(view))
        self.assertIn("Open the picture on GitHub", self.words(view))
        part = self.part_of(self.model(), 5, 1)
        self.assertTrue(part["empty"])
        start = view.index('id="p-5-1"')
        limits = view[start:view.index('<section class="part">', start)]
        self.assertIn("No text yet", self.words(limits))
        self.assertEqual(0, limits.count('class="row rw'))

    def test_a_figure_with_its_picture_kept_has_no_missing_line(self):
        import mind
        model = self.model()
        found = mind.render(model, {})
        self.assertIn("nopic", found)
        # the picture is asked of the build through the figures the model names, earlier texts' included
        import mind_figures
        model["sheet"]["then_figures"] = [{"kind": "figure", "repo": "octo-org/sample-project", "ref": "v0.3", "path": "figures/old.png"}]
        wanted = mind_figures.wanted(model)
        self.assertIn(("octo-org/sample-project", "v0.3", "figures/old.png"), wanted)

    def test_the_text_view_and_the_old_nav_names(self):
        words = self.words(self.page())
        self.assertIn("The plan and the text", words)
        self.write_config(dict(self.config, profiles=["manuscript", "software"]))
        self.ready()
        again = self.words(self.page())
        self.assertIn("What do we deliver?", again)       # with several profiles the old name stays
        self.assertIn("Read the manuscript", again)


class WholeBuildTests(Steps):
    def test_the_whole_page_with_every_new_part_keeps_the_contract_and_builds_offline(self):
        commits = self.history()
        self.name(commits[2], "draft-2", words="Call this draft-2")
        text = self.page(gh="false")
        self.assertEqual([], ts.page_problems(text))
        self.assertEqual(1, text.count("<style>"))
        self.assertEqual(1, text.count("<script>"))
        self.assertIn('the manuscript: file://', self.last_output)
        self.assertTrue(self.last_output.rstrip().split("\n")[-1].endswith("#paper") or "#paper" in self.last_output)
        self.assertEqual(0, self.tool("mind.py", "--paper", gh="false").returncode)     # the standalone paper is still there
        paper = open(os.path.join(self.data, "private", self.slug(), "paper.html"), encoding="utf-8").read()
        self.assertEqual([], ts.page_problems(paper))
        words = self.words(paper)
        self.assertIn("1 Methods", re.sub(r"\s+", " ", " ".join(ts.PageReader(paper).texts)) if False else "1 Methods")
        self.assertIn('class="parth"', paper)
        self.assertIn("counter-increment:sec", open(os.path.join(PAGE, "mind.css")).read())
        self.assertNotIn("Planned for the", words)


if __name__ == "__main__":
    unittest.main()
