"""Decision changes use trusted sources and current, independently reviewable targets."""

import datetime
import json
import re
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "skills/recortex/scripts"), str(ROOT / "examples/dashboard")]
import decision_impact as impact
import decision_review
import index
import manuscript_record as record
import mind_model
import preview
from test_scripts import MindSandbox


class DecisionImpactTests(unittest.TestCase):
    """Sync one fake GitHub project, then use an independent copy of its checked index in each test."""

    @classmethod
    def setUpClass(cls):
        cls.fixture = MindSandbox()
        cls.fixture.setUp()
        try:
            fixture = cls.fixture
            fixture.config["profiles"] = ["software"]
            fixture.write_config(fixture.config)
            preview.add_decision_change(fixture, "software")
            preview.add_project_map(fixture, "software")
            for cid, user, links in ((9710, None, "because=9701"), (9711, "outsider", "because=9701"),
                                     (9712, None, "because=9701"), (9713, None, "replaces=9712")):
                at = "2026-04-11T14:%02d+00:00" % (cid - 9700)
                fixture.put(cid, 16, record.decision(at, "work item %d" % cid, "publish item %d" % cid,
                                                      "Publish the checklist.", "The lead", "Publish the checklist.",
                                                      "In planning.", "Establishes its scope.", "Review it.", links=links),
                            user=user, created=record.utc(at))
            fixture.ready()
        except BaseException:
            cls.fixture.doCleanups()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.fixture.doCleanups()

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        original = sqlite3.connect(self.fixture.index_file())
        try:
            original.backup(self.db)
        finally:
            original.close()
        self.repo = self.fixture.config["repo"]
        self.allowed = {"octocat"}

    def data(self, **kwargs):
        return impact.from_index(self.db, self.repo, self.allowed, **kwargs)

    def group(self, result=None):
        return next(g for g in (result if result is not None else self.data())["groups"] if g["id"] == "impact-9701")

    def dashboard(self):
        return mind_model.build(self.db, self.repo, self.allowed, datetime.date.today(), ["software"],
                                self.fixture.config)["decision_impacts"]

    def test_checked_links_suggestions_and_deliberately_unrelated_work(self):
        # Both decisions still require a release gate. That wording alone does not point back to the cobalt policy.
        self.db.execute("UPDATE steps SET text='Prepare the release gate for tomorrow' WHERE comment=9702 AND ordinal=3")
        group = self.group(self.data(suggestions=True))
        self.assertEqual(9701, group["old"]["id"])
        self.assertEqual([9703], [s["id"] for s in group["successors"]])
        self.assertFalse(group["conflict"])
        items = group["items"]
        self.assertEqual({"task", "step", "branch", "section", "record", "source"}, {i["kind"] for i in items})
        steps = {i["ordinal"]: i for i in items if i["kind"] == "step"}
        self.assertEqual({1, 2}, set(steps))
        self.assertEqual(["direct", "suggested"], [steps[n]["relation"] for n in (1, 2)])
        self.assertIn("issuecomment-9701", steps[1]["quote"])
        self.assertIn("cobalt delimiter", steps[2]["quote"])
        self.assertIn("cobalt delimiter", steps[2]["reason"])
        self.assertTrue(all(i["review"] is None and re.fullmatch(r"[0-9a-f]{64}", i["revision"]) for i in items))
        self.assertEqual({9710}, {i["comment"] for i in items if i["kind"] == "record"})
        self.assertTrue(all(i["relation"] == "direct" for i in self.group()["items"]))

    def test_current_standalone_paragraph_can_be_previewed_and_manuscript_is_protected(self):
        with mock.patch.object(impact, "suggestions_enabled", return_value=False):
            group = self.group(self.dashboard())
            section = next(i for i in group["items"] if i["kind"] == "section")
            self.assertIn("#issuecomment-9701", section["edit_before"])
            self.assertIn("#issuecomment-9703", section["edit_text"])
            self.assertIn("body_digest", section)
            self.assertTrue(section["anchor"])
            self.db.execute("INSERT INTO labels VALUES (3,'ms:methods')")
            self.assertIn("edit_text", next(i for i in self.group(self.dashboard())["items"] if i["kind"] == "section"))
            self.db.execute("DELETE FROM labels WHERE issue=16 AND label='rel:docs'")
            self.db.execute("INSERT INTO labels VALUES (16,'ms:methods')")
            protected = self.group(self.dashboard())
            self.assertNotIn("edit_text", next(i for i in protected["items"] if i["kind"] == "section"))

    def test_citation_only_draft_never_points_old_policy_wording_at_the_new_source(self):
        group = self.group(self.dashboard())
        section = next(item for item in group["items"] if item["kind"] == "section")
        before, old, new = section["edit_before"], group["old"]["url"], group["successors"][0]["url"]
        body = self.db.execute("SELECT body FROM issues WHERE number=16").fetchone()[0]
        for wording, can_retarget in (
                ("Use cobalt as the release gate (%s).", False),
                ("Choose the cobalt delimiter corpus according to the current release-gate decision (%s).", False),
                ("Current policy: %s. Use the cobalt corpus.", False),
                ("[Use the cobalt corpus](%s)", False),
                ("Current decision: %s.", True),
                ("[Current policy](%s)", True),
                ("%s", False)):  # The bare URL also occurs in the separate baseline source.
            with self.subTest(wording=wording):
                current = wording % old
                self.db.execute("UPDATE issues SET body=? WHERE number=16", (body.replace(before, current),))
                self.db.execute("UPDATE sections SET text=? WHERE issue=16 AND heading='Documentation'", (current,))
                item = next(i for i in self.group(self.dashboard())["items"] if i["kind"] == "section")
                self.assertEqual("direct", item["relation"])
                self.assertTrue(item["draft"])
                self.assertEqual(can_retarget, "edit_before" in item)
                if can_retarget:
                    self.assertEqual(wording % new, item["edit_text"])

        # With a single bare reference the exact native change has only one possible location.
        self.db.execute("UPDATE issues SET body=? WHERE number=16", ("## Documentation\n\n" + old,))
        bare = next(i for i in self.group(self.dashboard())["items"] if i["kind"] == "section")
        self.assertEqual(old, bare["edit_before"])
        self.assertEqual(new, bare["edit_text"])

    def test_manuscript_guard_follows_target_section_in_mixed_projects(self):
        config = {"profiles": ["research", "software", "manuscript"], "plans": {"manuscript": 7},
                  "own_manuscript": {"path": "paper.md", "brought": {"16": {"Imported": {"text": "a" * 64}}}}}
        plan = {"parts": {91: {"standing": True, "topic": 16, "section": "Results"}}}
        self.assertTrue(impact.protected_section(config, 7, "Current plan", [], plan))
        self.assertTrue(impact.protected_section(config, 16, "Imported", [], plan))
        self.assertTrue(impact.protected_section(config, 16, "Results", ["rd:results"], plan))
        self.assertTrue(impact.protected_section(config, 16, "Methods", ["ms:methods"], plan))
        self.assertFalse(impact.protected_section(config, 16, "Methods", ["rd:methods"], plan))
        self.assertFalse(impact.protected_section(config, 16, "Documentation", ["rel:docs", "ms:methods"], plan))

    def test_changed_target_revision_reopens_only_that_item(self):
        group = self.group()
        step = next(i for i in group["items"] if i["kind"] == "step")
        event = {"id": "a" * 32, "group": group["id"], "target": step["id"], "revision": step["revision"],
                 "previous": 0, "verdict": "still_correct", "action": "review", "note": "Intentional comparison.",
                 "draft": "", "receipt": None}
        # Replay the immutable, properly stamped hub comment exactly as read_map and review_io do.
        stamp = "2026-11-18T00:00:00Z"
        self.db.execute("INSERT INTO comments(id,issue,body,author,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                        (9901, self.fixture.config["hub"], decision_review.encode(self.repo, self.fixture.config["hub"], event),
                         "octocat", stamp, stamp))
        self.db.execute("INSERT INTO comment_edit_reads VALUES(?,?,?,?,?,?)", (9901, "", stamp, 0, None, 1))
        index.set_meta(self.db, "mind_derived", "1")
        index.set_meta(self.db, "derive_version", index.DERIVE_VERSION)
        group = self.group()
        self.assertEqual("still_correct", next(i for i in group["items"] if i["id"] == step["id"])["review"]["verdict"])
        self.db.execute("UPDATE steps SET text = text || ' New qualifier.' WHERE comment=9702 AND ordinal=1")
        fresh = self.group()
        reopened = next(i for i in fresh["items"] if i["id"] == step["id"])
        self.assertNotEqual(step["revision"], reopened["revision"])
        self.assertIsNone(reopened["review"])
        self.assertEqual(1, len(fresh["review_history"]))

    def test_conflicting_successors_chains_and_out_of_page_original(self):
        with mock.patch.object(mind_model, "CAP_MEMORIES", 1):
            group = self.group(self.dashboard())
            self.assertFalse(group["old"]["anchor"])
            self.assertTrue(group["old"]["url"].endswith("/issues/16#issuecomment-9701"))
        self.db.execute("INSERT INTO links VALUES (9713,'replaces',9701)")
        conflict = self.group()
        self.assertTrue(conflict["conflict"])
        self.assertEqual({9703, 9713}, {s["id"] for s in conflict["successors"]})
        self.assertFalse(any("edit_text" in i for i in conflict["items"]))
        self.db.execute("DELETE FROM links WHERE source=9713 AND target=9701")
        self.db.execute("INSERT INTO links VALUES (9713,'replaces',9703)")
        self.assertEqual([9713], [s["id"] for s in self.group()["successors"]])

    def test_task_body_goal_is_separate_from_agreed_steps_and_retired_work(self):
        url = self.group()["old"]["url"]
        body = ("## Goal\n\nShip the profile required by %s.\n\n## Plan\n\n- [x] A finished step, %s."
                "\n\n## Result\n\nAn old result, %s.\n\n## Done when\n\n<!-- %s -->\n\n> Quoted %s"
                "\n\n## Scope\n\n`%s`") % ((url,) * 6)
        self.db.execute("UPDATE issues SET body=? WHERE number=15", (body,))
        items = [i for i in self.group()["items"] if i["kind"] == "task"]
        self.assertEqual(1, len(items))
        self.assertEqual("Goal", items[0]["heading"])
        self.assertEqual("direct", items[0]["relation"])
        self.assertIn("Ship the profile", items[0]["quote"])
        self.assertNotIn("edit_before", items[0])
        self.db.execute("UPDATE issues SET edits_read=0 WHERE number=15")
        self.assertFalse(any(i["kind"] == "task" for i in self.group()["items"]))
        self.db.execute("UPDATE issues SET edits_read=1, state='closed' WHERE number=15")
        self.assertFalse(any(i["kind"] in ("task", "step") for i in self.group()["items"]))

    def test_excludes_closed_work_unverified_bodies_code_foreign_citations_and_retired_records(self):
        url = self.group()["old"]["url"]
        self.db.execute("UPDATE steps SET text=? WHERE comment=9702 AND ordinal=1", ("`%s`" % url,))
        self.db.execute("UPDATE steps SET text=? WHERE comment=9702 AND ordinal=2", (url.replace(self.repo, "other/place"),))
        self.db.execute("UPDATE steps SET text=? WHERE comment=9702 AND ordinal=3", (url.replace("/16#", "/9#"),))
        self.db.execute("UPDATE issues SET edits_read=0 WHERE number=16")
        result = self.data()
        self.assertTrue(result["partial"])
        self.assertEqual({"task", "record", "branch"}, {i["kind"] for i in self.group(result)["items"]})
        self.db.execute("UPDATE issues SET state='closed' WHERE number=16")
        self.assertEqual({"task", "branch"}, {i["kind"] for i in self.group()["items"]})
        self.assertNotIn("outsider", json.dumps(result))

    def test_empty_untrusted_partial_stale_bounded_and_missing_old_source(self):
        self.assertEqual([], impact.from_index(self.db, self.repo, set())["groups"])
        with mock.patch.object(impact, "MAX_TARGETS", 1):
            self.assertTrue(self.data()["partial"])
        self.db.execute("INSERT INTO links VALUES(9703,'replaces',1234567)")
        result = self.data()
        self.assertTrue(any("earlier record" in line for line in result["coverage"]))
        index.set_meta(self.db, "sync_complete", "0")
        self.assertEqual([], self.data()["groups"])
        index.set_meta(self.db, "sync_complete", "1")
        index.set_meta(self.db, "synced_at", "2000-01-01T00:00:00Z")
        self.assertEqual([], self.data(suggestions=True)["groups"])
