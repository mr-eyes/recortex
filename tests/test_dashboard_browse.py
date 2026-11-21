"""The shared dashboard reads one trusted snapshot; no model or live GitHub calls."""
import datetime
import json
import re
import sqlite3
import sys
from unittest.mock import patch

from tests import manuscript_record
from tests.test_manuscript_plan import PaperSandbox
from tests.test_scripts import MindSandbox, PageReader, SCRIPTS, chain_text, memory_draft, page_problems

sys.path.insert(0, SCRIPTS)
import mind
import mind_model
import record_format


class DashboardBrowseTests(MindSandbox):
    def model(self):
        db = sqlite3.connect(self.index_file())
        db.row_factory = sqlite3.Row
        try:
            return mind_model.build(db, self.config["repo"], {"octocat"}, datetime.date(2026, 11, 18),
                                    profiles=mind_model.recortex.shipped_profiles(self.config.get("profiles", [])), config=self.config)
        finally:
            db.close()

    def record(self):
        self.add_chain()
        self.ready()
        return self.model()

    def test_posting_time_and_event_time_are_independent_and_backfill_is_findable(self):
        model = self.record()
        memory = next(m for m in model["memories"] if m["id"] == 9103)
        self.assertTrue(memory["posted"].startswith("2026-04-10"))
        self.assertTrue(memory["event_time"].startswith("2026-03-"))
        self.assertLess(memory["event_time"], memory["posted"])
        self.assertIn(9103, model["recorded_since"])
        self.assertEqual(sorted(model["memories"], key=lambda m: (m["posted"] or "", m["id"]), reverse=True)[0]["id"],
                         model["recorded"][0])

    def test_reconstruction_has_its_own_flag_independent_of_legacy_plain_lines(self):
        self.record()
        self.sql("UPDATE events SET reconstructed=1 WHERE comment=9103")
        memory = next(m for m in self.model()["memories"] if m["id"] == 9103)
        self.assertTrue(memory["reconstructed"])
        self.assertFalse(memory["old"])

    def test_automatic_status_is_identified_from_its_eligible_recorded_block(self):
        self.record()
        db = sqlite3.connect(self.index_file())
        try:
            resume = db.execute("SELECT value FROM meta WHERE key='resume'").fetchone()[0]
            stamp = dict(record_format.fields(resume))["Last updated"]
            revised = resume.replace(stamp, stamp.split(" by ")[0] + " by the observer")
            db.execute("UPDATE comments SET body=replace(body, ?, ?)", (resume, revised))
            db.execute("UPDATE meta SET value=? WHERE key='resume'", (revised,))
            db.commit()
        finally:
            db.close()
        model = self.model()
        self.assertTrue(model["last"]["automatic"])
        text = mind.render(model)
        self.assertIn("Status automatically refreshed on", text)
        self.assertIn("Original status", text)

    def test_shared_navigation_keeps_legacy_links_and_contextual_words(self):
        text = mind.render(self.record())
        self.assertEqual([], page_problems(text))
        page = PageReader(text)
        nav = [dict(attrs)["data-nav"] for _, attrs in page.elements if "data-nav" in dict(attrs)]
        self.assertEqual(["now", "project-map", "work", "history", "outputs"], nav)
        ids = {dict(attrs).get("id") for _, attrs in page.elements}
        self.assertTrue({"now", "todo", "work", "history", "text", "outputs", "threads", "settled", "lessons",
                         "open", "map", "ask", "usage", "m-9103", "thread-5", "settled-5"} <= ids)
        self.assertIn("What led here", text)
        self.assertIn("What came of it", text)
        self.assertIn("Where it came up", text)

    def test_no_resume_has_a_distinct_fallback_from_an_unavailable_resume(self):
        self.record()
        self.sql("UPDATE meta SET value='' WHERE key='resume'")
        missing = self.model()
        self.assertIsNone(missing["last"])
        self.assertEqual([], missing["recorded_since"])
        self.assertFalse(missing["not_told"]["pause"])
        self.assertIn("No pause has been recorded yet", mind.render(missing))
        self.assertNotIn('<option value="since">', mind.render(missing))
        missing["not_told"]["pause"] = True
        self.assertIn("The last pause is not told here", mind.render(missing))

    def test_failed_work_read_is_not_presented_as_no_work(self):
        self.record()
        with patch.object(mind_model.index, "tasks", side_effect=sqlite3.DatabaseError("unavailable")):
            model = self.model()
        self.assertFalse(model["tasks"]["available"])
        text = mind.render(model)
        self.assertIn("Work could not be read", text)
        self.assertNotIn(mind.TODO_NONE, text)

    def test_sparse_core_record_has_no_output_navigation_and_keeps_browsing(self):
        model = self.record()
        model.update(memories=[], threads=[], words=[], cited=[], pauses=[], last=None, since=[], recorded=[], recorded_since=[],
                     outline={}, map={}, tree={}, sheet=None, plan=None, outputs_available=False,
                     tasks={"available": True, "open": [], "closed": []})
        text = mind.render(model)
        self.assertEqual([], page_problems(text))
        self.assertNotIn('data-nav="outputs"', text)
        self.assertIn('id="history"', text)
        self.assertIn("No memories are included yet", text)
        stories = mind.Page(model).threads_view()
        self.assertIn("No topic stories are included in this snapshot.", stories)
        self.assertNotIn('class="story-topics"', stories)

    def test_new_project_guidance_needs_a_complete_read_and_an_empty_record(self):
        self.ready()
        model = self.model()
        model.update(memories=[], threads=[], last=None, project_map={}, not_told={"count": 0},
                     tasks={"available": True, "open": [], "closed": []})
        def start():
            return "Start your project record" in mind.Page(model).now_view()
        self.assertTrue(start())
        model["built"]["complete"] = False
        self.assertFalse(start())
        model["built"]["complete"] = True
        model["not_told"]["count"] = 1
        self.assertFalse(start())
        model["not_told"]["count"] = 0
        model["tasks"]["available"] = False
        self.assertFalse(start())
        model["tasks"]["available"] = True
        model["project_map"] = {"nodes": {"existing": {"title": "Field work"}}}
        self.assertFalse(start())

    def test_changed_decision_scope_citations_and_shared_verdicts(self):
        model = self.record()
        source = lambda cid, words: {"id": cid, "issue": 5, "text": words,
                                     "url": "https://github.com/%s/issues/5#issuecomment-%d" % (model["repo"], cid)}
        old, replacement = source(9102, "Old wording"), source(9105, "Replacement wording")
        group = {"id": "impact-9102", "hub": 4, "old": old, "successors": [replacement], "items": [],
                 "review_history": [], "conflict": False}
        model["decision_impacts"] = {"groups": [group], "partial": True, "coverage": ["A source is unavailable."]}
        item = {"id": "a"*64, "revision": "b"*64, "label": "Compatibility checks", "quote": "Continue the original test.",
                "reason": "This paragraph cites the earlier decision.", "relation": "direct",
                "url": old["url"], "review": None}
        group["items"].append(item)
        page = mind.Page(model)
        overview = page.now_view()
        history = page.history_view()
        self.assertIn('id="impact-9102"', overview)
        self.assertIn('id="history-impact-9102"', history)
        self.assertIn("Explicit connections", history)
        self.assertIn("Continue the original test.", history)
        self.assertIn("/recortex:guide What am I missing about impact-9102, item " + item["id"], history)
        self.assertIn("A source is unavailable.", history)
        self.assertIn("Next judgment — review Compatibility checks", page.handoff_brief([]))
        self.assertEqual([], page_problems(mind.render(model)))
        event = {"target": item["id"], "verdict": "unrelated", "reviewer": "octocat", "comment": 171,
                 "at": "2026-11-17T00:00:00Z", "note": "Historical comparison."}
        item["review"] = event
        group["review_history"] = [event]
        self.assertNotIn('id="impact-9102"', page.now_view())
        self.assertNotIn("Next judgment", page.handoff_brief([]))
        self.assertIn("Unrelated", page.history_view())
        self.assertIn("Historical comparison.", page.history_view())
        self.assertIn("#issuecomment-171", page.history_view())
        group["conflict"] = True
        group["successors"].append(source(9107, "Other recorded wording"))
        self.assertIn("Choose which replacement governs", page.now_view())
        self.assertIn("confirm which recorded replacement governs", page.handoff_brief([]))

    def test_attention_traces_the_latest_hold_and_current_blocker_to_their_own_sources(self):
        self.put_review_handoff()
        self.put(9112, 7, memory_draft("failure", "2026-04-10T14:00Z"), created="2026-04-10T14:00:00Z")
        self.ready()
        model = self.model()
        overview = mind.Page(model).now_view()
        attention = overview.split('id="attention-h"', 1)[1].split("</section>", 1)[0]
        self.assertIn("Wait for the release owner.", attention)
        self.assertIn("issues/4#issuecomment-9110", attention)
        self.assertIn('href="#work-step-7-9108-1"', attention)
        self.assertIn('href="#m-9112"', attention)
        self.assertLess(overview.index("Needs your attention"), overview.index('class="focus"'))
        self.assertEqual([], page_problems(mind.render(model)))

        self.sql("UPDATE meta SET value='' WHERE key='resume'")
        self.assertIn("Read the blocking failure", mind.Page(self.model()).now_view())
        self.sql("UPDATE meta SET value='0' WHERE key='sync_complete'")
        self.assertNotIn("Needs your attention", mind.Page(self.model()).now_view())
        self.sql("UPDATE meta SET value='1' WHERE key='sync_complete'")
        self.sql("UPDATE events SET author='untrusted' WHERE comment=9112")
        self.assertNotIn("Needs your attention", mind.Page(self.model()).now_view())

    def test_only_explicit_holds_and_blockers_get_attention_and_the_list_has_a_limit(self):
        self.put_review_handoff()
        original = self.repo()["comments"]["9110"]["body"]
        self.put(9110, self.config["hub"], original.replace("Wait for the release owner.", "None."),
                 created="2026-04-10T11:00:00Z")
        self.ready()
        model = self.model()
        self.assertIsNone(model["last"]["holds"])
        page = mind.Page(model)
        self.assertNotIn("Needs your attention", page.now_view())
        tasks = [{"issue": n, "title": "Pump %d" % n, "blocked": True, "steps": []} for n in range(1, 7)]
        task_only = page.overview_attention(None, tasks)
        self.assertEqual(3, task_only.count("<li>"))
        self.assertIn('href="#work">See 3 more blocked tasks in Work', task_only)
        self.assertIn('href="#work-task-1"', task_only)
        self.assertNotIn("Pump 4", task_only)
        model["last"]["holds"] = {"blocks": [{"p": [{"t": "Wait for the lab lead."}]}]}
        with_hold = page.overview_attention(model["last"], tasks)
        self.assertEqual(3, with_hold.count("<li>"))
        self.assertIn("See 4 more blocked tasks in Work", with_hold)

    def test_history_filters_are_bounded_and_dates_are_text_not_script(self):
        model = self.record()
        text = mind.render(model)
        page = PageReader(text)
        rows = [dict(attrs) for _, attrs in page.elements if "history-row" in dict(attrs).get("class", "").split()]
        self.assertEqual(len(model["memories"]), len(rows))
        self.assertTrue(all("data-posted" in row and "data-event" in row for row in rows))
        for ident in ("history-clock", "history-order", "history-kind", "history-state", "history-topic", "history-query", "history-reset"):
            self.assertIn('id="%s"' % ident, text)
        self.assertNotIn(model["repo"], page.raw["script"][0])

    def test_unknown_posting_time_stays_unknown(self):
        self.record()
        self.sql("UPDATE events SET created_at='not a time' WHERE comment=9103")
        model = self.model()
        memory = next(m for m in model["memories"] if m["id"] == 9103)
        self.assertIsNone(memory["posted"])
        self.assertNotIn(9103, model["recorded_since"])
        self.assertIn("Recorded Time unknown", mind.render(model))

    def test_event_clock_explains_only_real_zone_fallback(self):
        self.record()
        for stamp, basis in ((record_format.to_iso("2026-03-05 09:00 EST"), "event"),
                             (record_format.to_iso("2026-03-05 09:00 XYZ"), "posting")):
            with self.subTest(stamp=stamp):
                self.sql("UPDATE events SET at='%s' WHERE comment=9103" % stamp)
                memory = next(m for m in self.model()["memories"] if m["id"] == 9103)
                self.assertEqual(basis, memory["time_basis"])
                self.assertEqual(record_format.instant(stamp, memory["posted"]), memory["event_time"])

    def test_two_custom_outputs_keep_names_membership_and_stable_identity(self):
        self.manuscript_record()
        self.ready()
        self.config["profiles"] = [
            {"name": "custom", "deliverable": "API handbook", "sections": [["sec:first-methods", "Methods"]]},
            {"name": "custom", "deliverable": "Field guide", "sections": [["sec:second-methods", "Methods"]]}]
        self.sql("INSERT INTO labels VALUES (3, 'sec:first-methods')", "INSERT INTO labels VALUES (3, 'sec:second-methods')")
        model = self.model()
        groups = model["outline"]["deliverables"]
        self.assertEqual(["API handbook", "Field guide"], [group["title"] for group in groups])
        self.assertNotEqual(groups[0]["id"], groups[1]["id"])
        self.assertEqual([g["id"] for g in groups], [g["id"] for g in self.model()["outline"]["deliverables"]])
        self.assertEqual([], page_problems(mind.render(model)))
        self.assertIn("Shared current text", mind.render(model))

    def test_core_topic_text_does_not_become_an_output(self):
        self.record()
        self.config["profiles"] = ["core"]
        model = self.model()
        self.assertFalse(model["outputs_available"])
        text = mind.render(model)
        self.assertNotIn('data-nav="outputs"', text)
        self.assertIn("Current topic text", text)
        self.assertEqual([], page_problems(text))

    def test_action_links_are_explicit_and_bound_to_stable_step_identity(self):
        model = self.record()
        model["tasks"] = {"available": True, "open": [{"issue": 7, "title": "Review the reader", "counts": {"steps": 1, "done": 0},
                           "steps": [{"n": 3, "comment": 9900, "ordinal": 1, "text": "Check quoted fields", "state": "open"}]}], "closed": []}
        text = mind.render(model)
        self.assertIn('id="work-step-7-9900-1"', text)
        self.assertNotIn('/actions', text)
        model.update(interactive_actions=True, interactive_base="/" + "a" * 32)
        self.assertIn('/' + 'a' * 32 + '/actions#step-7-9900-1', mind.render(model))
        model["interactive_base"] = '//elsewhere.invalid'
        self.assertNotIn('/actions', mind.render(model))

    def test_recorded_resume_step_links_only_when_number_and_text_still_match(self):
        model = self.record()
        step = {"n": 3, "comment": 9900, "ordinal": 1, "text": "Check quoted fields", "state": "open"}
        model["tasks"] = {"available": True, "open": [{"issue": 7, "steps": [step]}], "closed": []}
        row = {"action_text": "Step 3: Check quoted fields", "task_refs": [7]}
        page = mind.Page(model)
        self.assertIn('href="#work-step-7-9900-1"', page.resume_work_link(row, True))
        step["text"] = "Review the new fields"
        self.assertNotIn("work-step", page.resume_work_link(row, True))
        self.assertIn('href="#work-task-7"', page.resume_work_link(row, True))
        step["text"] = "Check quoted fields"
        self.assertNotIn("work-step", page.resume_work_link(row, False))
        self.assertIn('href="#work-task-7"', page.resume_work_link(row, False))
        step["text"] = "Read the quoted fields " * 8
        row["action_text"] = "Step 3: " + mind_model.short(step["text"], 120)
        self.assertNotIn("work-step", page.resume_work_link(row, True))
        row["task_refs"] = [99]
        self.assertEqual("", page.resume_work_link(row, True))

    def put_review_handoff(self, action="Step 1: Carry the quote state across physical lines", where="#7", automatic=True):
        self.put(9108, 7, chain_text("6-steps.md"), created="2026-04-10T10:00:00Z")
        who = "the observer" if automatic else "the session"
        block = ("### Resume block: 2026-04-10 11:00 UTC\n\n%s\n"
                 "**Last updated:** 2026-04-10 11:00 UTC by %s\n**Current focus:** Fix quoted fields\n"
                 "**Active issues:** #7\n**Last decisions:** none\n**Next actions:**\n\n"
                 "| Action | Owner | Where |\n|---|---|---|\n| %s | not stated | %s |\n\n"
                 "**Holds:** Wait for the release owner.\n%s\n"
                 % (mind_model.recortex.RESUME_START, who, action, where, mind_model.recortex.RESUME_END))
        self.put(9110, self.config["hub"], block, created="2026-04-10T11:00:00Z")

    def put_review_checkpoint(self, target="9108:1", posted="2026-04-10T12:00:00Z"):
        source = memory_draft("checkpoint", posted[:16] + "Z", "done=" + target,
                              plain="the reader now keeps the opening quote across two physical lines",
                              rest="**State:** The conformance run processed all twelve files.\n\n"
                                   "**Done since the last checkpoint:** The reader uses the new state.\n\n"
                                   "**In progress:** Rerun the suite.\n\n**Next actions:** Rerun the suite.\n\n"
                                   "**Open questions:** Three files still fail.")
        self.put(9111, 7, source, created=posted)

    def rederive_review_comment(self, cid):
        db = sqlite3.connect(self.index_file())
        db.row_factory = sqlite3.Row
        try:
            mind_model.index.derive(db, [], [cid])
        finally:
            db.close()

    @staticmethod
    def handoff_area(model):
        # The same source can already appear in recent activity. Require the update inside the saved handoff.
        overview = mind.Page(model).now_view()
        return overview.split("What we meant to do next</h3>", 1)[1].split("</section>", 1)[0]

    def test_completed_handoff_step_keeps_original_words_and_expands_its_checkpoint_in_place(self):
        self.put_review_handoff()
        self.put_review_checkpoint()
        self.ready()
        model = self.model()
        area = self.handoff_area(model)
        self.assertIn("Step 1: Carry the quote state across physical lines", area)
        self.assertIn("Completed since handoff", area)
        disclosure = "<details" + area.split("<details", 1)[1].split("</details>", 1)[0] + "</details>"
        self.assertIn("<summary>Read checkpoint</summary>", disclosure)
        self.assertIn("The conformance run processed all twelve files.", disclosure)
        self.assertIn("https://github.com/octo-org/octo-repo/issues/7#issuecomment-9111", disclosure)
        self.assertIn("Next open step", area)
        self.assertIn("Rerun the conformance set", area)
        self.assertIn('href="#work-step-7-9108-2"', area)
        details = [dict(attrs) for tag, attrs in PageReader(area).elements if tag == "details"]
        self.assertEqual(1, len(details))
        self.assertNotIn("open", details[0])
        self.assertEqual([], page_problems(mind.render(model)))

    def test_inline_checkpoint_figures_keep_their_sources_without_reembedding_image_bytes(self):
        self.put_review_handoff()
        self.put_review_checkpoint()
        self.ready()
        model = self.model()
        checkpoint = next(memory for memory in model["memories"] if memory["id"] == 9111)
        figure = {"kind": "figure", "found": True, "repo": model["repo"], "ref": "a" * 40,
                  "path": "plots/cases.png", "title": "Conformance cases", "caption": "Twelve files were checked."}
        checkpoint["evidence"].append(figure)
        page = mind.Page(model)
        row = model["last"]["next"][0]
        with patch.object(page, "picture_of", return_value="cached image bytes") as embed:
            inline = page.handoff_row(row, True)
            embed.assert_not_called()
            self.assertIn(figure["caption"], inline)
            self.assertIn("https://github.com/%s/blob/%s/%s" % (model["repo"], figure["ref"], figure["path"]), inline)
            self.assertNotIn("cached image bytes", inline)
            self.assertIn("cached image bytes", "".join(page.references(checkpoint)))
            embed.assert_called_once_with(figure)

    def test_copied_handoff_distinguishes_the_plan_from_its_later_sources(self):
        self.put_review_handoff()
        self.put_review_checkpoint()
        self.ready()
        model = self.model()
        page = mind.Page(model)
        tasks = model["tasks"]["open"]
        brief = page.handoff_brief(tasks)
        self.assertIn("Focus — Fix quoted fields", brief)
        self.assertIn("Holds — Wait for the release owner.", brief)
        self.assertIn("Planned at handoff — Step 1: Carry the quote state", brief)
        self.assertIn("issues/4#issuecomment-9110", brief)
        self.assertIn("Completed since handoff", brief)
        self.assertIn("issues/7#issuecomment-9111", brief)
        task_title = next(task["title"] for task in tasks if task["issue"] == 7)
        self.assertIn("Open step · " + task_title, brief)
        reading = " ".join(PageReader(brief).texts)
        self.assertIn("Read the completion record", reading)
        self.assertNotIn("#issuecomment-", reading)
        self.assertIn("issues/7#issuecomment-9108", brief)
        model["last"]["holds"]["cut"] = True
        self.assertIn("Holds — Read the full field in the original handoff.", page.handoff_brief(tasks))
        model["last"] = None
        self.assertIn("Open step · " + task_title, page.handoff_brief(tasks))
        model["built"]["complete"] = False
        self.assertEqual("", page.handoff_brief(tasks))

    def test_explicit_manual_handoff_uses_the_result_mark_on_its_original_topic(self):
        self.put_review_handoff(automatic=False)
        self.put(9111, 5, chain_text("7-result.md"), created="2026-04-10T12:00:00Z")
        self.ready()
        area = self.handoff_area(self.model())
        self.assertIn("Completed since handoff", area)
        self.assertIn("Read result", area)
        self.assertIn("9 of the 12 quoted-newline files pass", area)
        self.assertIn("https://github.com/octo-org/octo-repo/issues/5#issuecomment-9111", area)
        self.assertNotIn("Read checkpoint", area)

    def test_handoff_requires_a_known_source_posted_strictly_after_the_status(self):
        self.put_review_handoff()
        self.put_review_checkpoint()
        self.ready()
        for stamp in ("2026-04-10T11:00:00Z", "2026-04-10T10:59:59Z", ""):
            with self.subTest(stamp=stamp):
                self.sql("UPDATE comments SET created_at='%s', updated_at='%s' WHERE id=9111" % (stamp, stamp))
                self.rederive_review_comment(9111)
                area = self.handoff_area(self.model())
                self.assertIn("Step 1: Carry the quote state across physical lines", area)
                self.assertNotIn("Completed since handoff", area)
                self.assertNotIn("Next open step", area)

    def test_replacement_with_identical_step_number_and_text_cannot_complete_original_handoff(self):
        self.put_review_handoff()
        replacement = chain_text("6-steps.md").replace("at=2026-03-12T11:00-08:00",
                                                      "at=2026-04-10T11:10Z replaces=9108")
        self.put(9112, 7, replacement, created="2026-04-10T11:15:00Z")
        self.put_review_checkpoint(target="9112:1")
        self.ready()
        model = self.model()
        task = next(task for task in model["tasks"]["open"] if task["issue"] == 7)
        self.assertEqual((9112, "done"), (task["steps"][0]["comment"], task["steps"][0]["state"]))
        area = self.handoff_area(model)
        self.assertNotIn("Completed since handoff", area)
        self.assertNotIn('href="#work-step-7-9112-1"', area)
        self.assertIn('href="#work-task-7"', area)

    def test_later_backfill_cannot_turn_an_already_completed_step_into_new_progress(self):
        self.put_review_handoff()
        self.put_review_checkpoint(posted="2026-04-10T10:30:00Z")
        self.put(9112, 5, chain_text("7-result.md"), created="2026-04-10T12:00:00Z")
        self.ready()
        model = self.model()
        task = next(task for task in model["tasks"]["open"] if task["issue"] == 7)
        # The result's March event date wins the task assessment, but the checkpoint was posted before the handoff.
        self.assertEqual(9112, task["steps"][0]["by"]["id"])
        self.assertNotIn("Completed since handoff", self.handoff_area(model))

    def test_earlier_result_edited_after_the_handoff_leaves_its_original_marks_unknown(self):
        self.put_review_handoff()
        self.put(9109, 5, chain_text("7-result.md").replace(" done=9108:1", ""), created="2026-04-10T10:30:00Z")
        self.put_review_checkpoint()
        data = self.repo()
        result = data["comments"]["9109"]
        result["updated_at"] = "2026-04-10T12:30:00Z"
        result["edits"] = [{"at": result["updated_at"], "by": "octocat"}, {"at": result["created_at"], "by": "octocat"}]
        self.save(data)
        self.ready()
        area = self.handoff_area(self.model())
        self.assertNotIn("Completed since handoff", area)
        self.assertIn('href="#work-step-7-9108-1"', area)

    def test_freeform_handoff_retains_its_words_after_the_task_step_is_checked(self):
        self.put_review_handoff(action="Finish the reader change", automatic=False)
        self.put_review_checkpoint()
        self.ready()
        area = self.handoff_area(self.model())
        self.assertIn("Finish the reader change", area)
        self.assertNotIn("Completed since handoff", area)

    def test_handoff_referring_to_two_tasks_with_the_same_step_is_ambiguous(self):
        self.put_review_handoff(where="#7 and #3")
        self.put(9112, 3, chain_text("6-steps.md"), created="2026-04-10T10:05:00Z")
        self.put_review_checkpoint()
        self.ready()
        model = self.model()
        self.assertEqual({3, 7}, set(model["last"]["next"][0]["task_refs"]))
        area = self.handoff_area(model)
        self.assertIn("Step 1: Carry the quote state across physical lines", area)
        self.assertNotIn("Completed since handoff", area)

    def test_handoff_naming_a_local_and_a_foreign_task_cannot_select_the_local_step(self):
        self.put_review_handoff(where="octo-org/another-reader#7 and #7")
        self.put_review_checkpoint()
        self.ready()
        area = self.handoff_area(self.model())
        self.assertIn("Step 1: Carry the quote state across physical lines", area)
        self.assertNotIn("Completed since handoff", area)
        self.assertNotIn('href="#work-step-7-9108-1"', area)

    def test_completed_final_step_does_not_link_to_work_elsewhere(self):
        self.put_review_handoff(action="Step 2: Rerun the conformance set")
        self.put_review_checkpoint(target="9108:1,2")
        other_steps = chain_text("6-steps.md").replace("Carry the quote state across physical lines", "Collect timing samples")
        self.put(9112, 3, other_steps, created="2026-04-10T10:05:00Z")
        self.ready()
        area = self.handoff_area(self.model())
        self.assertIn("Completed since handoff", area)
        self.assertIn("No open steps in this task.", area)
        self.assertNotIn("Next open step", area)
        self.assertNotIn("Collect timing samples", area)

    def test_closed_task_retains_completion_but_does_not_offer_later_work(self):
        self.put_review_handoff()
        self.put_review_checkpoint()
        self.ready()
        self.sql("UPDATE issues SET state='closed',closed_at='2026-11-12T11:00:00Z' WHERE number=7")
        area = self.handoff_area(self.model())
        self.assertIn("Completed since handoff", area)
        self.assertIn("Task closed.", area)
        self.assertNotIn("Next open step", area)
        self.assertNotIn("Rerun the conformance set", area)

    def test_blocked_next_step_is_identified_beside_the_completed_handoff(self):
        self.put_review_handoff()
        self.put_review_checkpoint()
        self.put(9112, 7, memory_draft("failure", "2026-04-10T14:00Z"), created="2026-04-10T14:00:00Z")
        self.ready()
        area = self.handoff_area(self.model())
        self.assertIn("Completed since handoff", area)
        self.assertIn("Next step is blocked", area)
        self.assertIn("Rerun the conformance set", area)
        self.assertIn('href="#work-step-7-9108-2"', area)
        self.assertNotIn("Next open step", area)

    def test_omitted_or_untrusted_mark_never_becomes_inline_handoff_evidence(self):
        self.put_review_handoff()
        self.put_review_checkpoint()
        self.put(9112, 5, memory_draft("idea", "2026-04-11T12:00Z"), created="2026-04-11T12:00:00Z")
        self.ready()
        with patch.object(mind_model, "CAP_MEMORIES", 1):
            limited = self.model()
        self.assertNotIn(9111, [m["id"] for m in limited["memories"]])
        self.assertNotIn("Completed since handoff", self.handoff_area(limited))
        self.sql("UPDATE meta SET value='0' WHERE key='sync_complete'")
        self.assertNotIn("Completed since handoff", self.handoff_area(self.model()))
        self.sql("UPDATE meta SET value='1' WHERE key IN ('sync_complete','derive_pending')")
        self.assertNotIn("Completed since handoff", self.handoff_area(self.model()))
        self.sql("UPDATE meta SET value='0' WHERE key='derive_pending'")
        self.sql("UPDATE comments SET author='untrusted' WHERE id=9111")
        self.rederive_review_comment(9111)
        area = self.handoff_area(self.model())
        self.assertNotIn("Completed since handoff", area)
        self.assertNotIn("The conformance run processed all twelve files.", area)

    def test_optional_tour_teaches_shared_areas_and_keeps_secondary_paths(self):
        model = self.record()
        page = mind.Page(model)
        tour = page.tour()
        reader = PageReader(tour)
        routes = [dict(attrs)["data-go"] for _, attrs in reader.elements if "data-go" in dict(attrs)]
        self.assertEqual(["#now", "#work", "#history", "#outputs", "#ask"], routes)
        hrefs = {dict(attrs).get("href") for _, attrs in reader.elements}
        self.assertTrue({"#open", "#settled", "#threads", "#lessons", "#map"} <= hrefs)
        self.assertIn("event kind", tour)
        self.assertIn("Close or Back", tour)
        self.assertNotIn('data-go="#text"', tour)
        self.assertIn('id="tour"', tour)
        self.assertIn('hidden="hidden"', tour)
        self.assertLess(len(" ".join(reader.texts).split()), 250)
        page.sheet = {}
        self.assertIn('href="#paper"', page.tour())
        self.assertIn('href="#paper"', page.explain_store())

    def test_core_and_sparse_tour_omit_outputs_and_preserve_legacy_targets(self):
        model = self.record()
        model["outputs_available"] = False
        for sparse in (False, True):
            with self.subTest(sparse=sparse):
                if sparse:
                    model.update(memories=[], threads=[], sheet=None, outline={})
                page = mind.Page(model)
                tour = page.tour()
                routes = [dict(attrs)["data-go"] for _, attrs in PageReader(tour).elements if "data-go" in dict(attrs)]
                self.assertEqual(["#now", "#work", "#history", "#ask"], routes)
                self.assertNotIn('href="#outputs"', page.explain_store())
                rendered = mind.render(model)
                self.assertIn("Project tools", rendered)
                self.assertIn('id="text"', rendered)
                self.assertIn('id="todo"', rendered)
                self.assertIn('id="history"', rendered)


class ProjectStoryTests(PaperSandbox):
    def stories(self):
        self.ready()
        result = self.tool("index.py", "query", "mind", "--json", gh="false")
        self.assertEqual(0, result.returncode, result.stderr)
        model = json.loads(result.stdout)
        page = mind.Page(model).threads_view()
        directory = re.search(r'<nav\b[^>]*class="story-topics"[^>]*>(.*?)</nav>', page, re.S)
        self.assertIsNotNone(directory, "History should open with a project topic directory")
        links = [dict(attrs)["href"] for tag, attrs in PageReader(directory[1]).elements if tag == "a"]
        return model, page, links

    def add_topic(self, number, name):
        state = self.repo()
        state["issues"][str(number)] = manuscript_record.issue(
            name, "# " + name, ["final-record", "topic:operations-%d" % number], "2026-09-01T14:00:00Z", parent=1)
        state["next_issue"] = number + 1
        self.save(state)

    def test_project_history_lists_each_topic_once_including_work_outside_the_manuscript(self):
        self.add_topic(14, "Field equipment maintenance")
        self.add_topic(15, "Winter field logistics")
        self.put(9901, 14, memory_draft("result", "2026-09-04T14:00Z", plain="the serviced pump holds pressure"),
                 created="2026-11-25T14:00:00Z")
        self.put(9902, 2, memory_draft("result", "2026-11-26T14:00Z"), user="ghost", created="2026-11-26T14:00:00Z")
        model, stories, links = self.stories()
        self.assertEqual([], page_problems(mind.render(model)))
        # One directory entry per topic, even for one appearing in several output sections.
        self.assertCountEqual(["#thread-%d" % t["n"] for t in model["threads"]], links)
        self.assertEqual("#thread-14", links[0])  # Its September result was recorded most recently.
        self.assertEqual("#thread-15", links[-1])  # A topic without an included memory is still reachable.
        empty_row = stories.split('href="#thread-15"', 1)[1].split("</li>", 1)[0]
        self.assertIn("No records in this snapshot", empty_row)
        self.assertTrue({"#thread-13", "#thread-7", "#thread-0"} <= set(links))  # Plan, set-aside topic, other notes.
        for target in ("thread-14", "task-8", "latest-4"):
            self.assertIn('id="%s"' % target, stories)
        self.assertIn("the serviced pump holds pressure", stories)

    def test_same_posting_time_orders_by_topic_name_then_number(self):
        self.add_topic(14, "valve checks")
        self.add_topic(15, "Valve checks")
        self.add_topic(16, "Battery checks")
        for number in (15, 14, 16):
            self.put(9900 + number, number, memory_draft("result", "2026-10-%02dT14:00Z" % number),
                     created="2026-11-25T14:00:00Z")
        _, _, links = self.stories()
        self.assertEqual(["#thread-16", "#thread-14", "#thread-15"], links[:3])
