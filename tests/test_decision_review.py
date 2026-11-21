"""Human verdicts, cross-device conflicts, separate native edits and trusted index replay."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_scripts as ts

sys.path.insert(0, ts.SCRIPTS)
import dashboard_action
import decision_review as ledger
import decision_review_io as io
import index
import observer_store
import project_map_io
import recortex


def event(ident, **extra):
    sample = dict(id=ident * 32, group="impact-18", target="b" * 64, revision="a" * 64, previous=0,
                  verdict="needs_update", note="The branch cites the old decision.", draft="Update the citation.",
                  action="map", receipt=None)
    return dict(sample, **extra)


def row(cid, e, author="octocat"):
    return dict(author=author, id=cid, created_at="2026-01-05T12:00:00Z", body=ledger.encode(ts.REPO, 4, e))


class DecisionReviewLedgerTests(unittest.TestCase):
    def test_concurrent_reviews_revision_and_pending_claim(self):
        reviewed = event("0")
        claim = dict(reviewed, id="1" * 32, previous=100, verdict="applying")
        applied = dict(claim, id="2" * 32, previous=102, verdict="applied", receipt={"issue": 4, "comment": 55})
        history = ledger.replay(ts.REPO, 4, {"octocat", "ed"}, [
            row(100, reviewed), row(101, dict(reviewed, id="f" * 32)), row(102, claim),
            row(103, applied, "ed"), row(104, applied), row(105, applied),
            row(106, dict(reviewed, revision="f" * 64, id="7" * 32))])
        self.assertEqual([100, 102, 104, 106], [r["comment"] for r in history])
        group = {"id": "impact-18", "items": [{"id": reviewed["target"], "revision": "c" * 64},
                                              {"id": reviewed["target"], "revision": reviewed["revision"]}]}
        ledger.attach([group], history)
        self.assertIsNone(group["items"][0]["review"])
        self.assertEqual("applied", group["items"][1]["review"]["verdict"])
        self.assertEqual(history, group["review_history"])

    def test_invalid_ledger_cannot_change_the_record(self):
        reviewed = event("0")
        for value in (dict(reviewed, previous=True), dict(reviewed, verdict="applied"), dict(reviewed, note="\x00"),
                      dict(reviewed, action="command"), dict(reviewed, draft="\n")):
            self.assertFalse(ledger.valid(value))
        good = row(101, reviewed)
        rows = [dict(good, body=good["body"].replace(ts.REPO, ts.REPO + "-foreign")),
                dict(good, author="outside"), dict(good, body=good["body"] + "\nextra")]
        self.assertEqual([], ledger.replay(ts.REPO, 4, {"octocat"}, rows))


class DecisionReviewActionTests(ts.MindSandbox):
    def setUp(self):
        super().setUp()
        self.in_process(extra={"CLAUDE_PROJECT_DIR": self.project})
        self.actions = dashboard_action.Actions()
        self.service = io.Service(self.actions)
        self.base = dict(group="impact-18", target="b" * 64, revision="a" * 64, previous=0)
        ref = lambda n: "https://github.com/%s/issues/5#issuecomment-%d" % (ts.REPO, n)
        self.item = {"id": self.base["target"], "revision": self.base["revision"], "kind": "branch",
                     "label": "Format", "relation": "direct", "branch": "format", "url": ref(18),
                     "draft": "Update the branch evidence.", "issue": 5}
        self.group = {"id": self.base["group"], "old": {"id": 18, "url": ref(18)},
                      "successors": [{"id": 21, "url": ref(21)}], "conflict": False, "items": [self.item]}
        self.use_service(self.service)

    def use_service(self, service):
        patch = mock.patch.object(service, "state", return_value={"groups": [self.group]})
        patch.start()
        self.addCleanup(patch.stop)

    def draft(self, verdict="needs_update", **fields):
        return self.service.stage(dict(dict(self.base, verdict=verdict, note="", draft=""), **fields))

    def test_all_verdicts_stale_previews_and_cited_handoff(self):
        self.item["kind"] = "task"
        pending = self.draft()
        stale = self.draft("unrelated")
        saved = self.service.commit(pending["id"])
        self.assertEqual("followup", saved["action"])
        self.assertIn(self.item["url"], saved["detail"]["request"])
        self.assertFalse(saved["applied"])
        self.assertEqual(saved, self.service.commit(pending["id"]))
        with self.assertRaisesRegex(io.ReviewError, "Another review"):
            self.service.commit(stale["id"])
        with self.assertRaisesRegex(io.ReviewError, "supported target edit"):
            self.service.apply(pending["id"])
        previous = saved["review"]["comment"]
        for verdict in ("defer", "unrelated", "still_correct"):
            op = self.draft(verdict, previous=previous)
            reviewed = self.service.commit(op["id"])["review"]
            self.assertEqual((verdict, "octocat"), (reviewed["verdict"], reviewed["reviewer"]))
            previous = reviewed["comment"]
        index.sync(ts.REPO, 4, rebuild=True, log=lambda *args: None)
        db = index.connect(ts.REPO, create=False)
        self.addCleanup(db.close)
        events = ledger.read(db, ts.REPO, 4, {"octocat"})
        self.assertEqual(self.service.history(), events)
        self.assertEqual([], ledger.read(db, ts.REPO, 4, set()))
        self.assertNotIn(ledger.MARKER, str(index.timeline(db)))

    def test_readback_after_lost_response_and_failed_send_never_duplicates(self):
        p = self.draft("defer")
        original = recortex.gh
        def lost(args, **kwargs):
            value = original(args, **kwargs)
            if args[:2] == ["issue", "comment"]:
                raise recortex.GhError("reply lost")
            return value
        count = len(self.repo()["comments"])
        with mock.patch.object(recortex, "gh", side_effect=lost):
            saved = self.service.commit(p["id"])
            self.assertEqual(saved, self.service.commit(p["id"]))
        self.assertEqual(count + 1, len(self.repo()["comments"]))
        p = self.draft("still_correct", previous=saved["review"]["comment"])
        def fail(args, **kwargs):
            if args[:2] == ["issue", "comment"]:
                raise recortex.GhError("no reply")
            return original(args, **kwargs)
        with mock.patch.object(recortex, "gh", side_effect=fail):
            with self.assertRaisesRegex(io.ReviewError, "not verified"):
                self.service.commit(p["id"])
        with self.assertRaisesRegex(io.ReviewError, "unconfirmed"):
            self.service.commit(p["id"])
        self.assertEqual(count + 1, len(self.repo()["comments"]))

    def test_holds_tampering_and_change_of_target_or_trust(self):
        p = self.draft("still_correct")
        self.item["revision"] = "1" * 64
        with self.assertRaisesRegex(io.ReviewError, "changed"):
            self.service.commit(p["id"])
        self.item["revision"] = self.base["revision"]
        with mock.patch.object(recortex, "allowed_users", return_value=set()):
            with self.assertRaises(dashboard_action.ActionError):
                self.service.commit(p["id"])
        with mock.patch.object(observer_store, "read_holds", return_value=[dict(state="active", repo=ts.REPO,
                                                                               action="publish", scope={})]):
            with self.assertRaisesRegex(ValueError, "hold"):
                self.service.commit(p["id"])
        with self.assertRaisesRegex(io.ReviewError, "publication checks"):
            self.draft("defer", note="My key is ghp_" + "A" * 36)
        self.group["conflict"] = True
        with self.assertRaisesRegex(io.ReviewError, "conflicting"):
            self.draft()
        self.assertFalse(self.service.history())

    def test_review_text_is_checked_again_at_send_when_the_projects_patterns_tighten_after_the_preview(self):
        p = self.draft("defer", note="The Bench notes still hold for this branch.")   # passes under the launch's patterns
        count = len(self.repo()["comments"])
        self.write_config(dict(self.config, restricted_patterns=["Bench notes"]))
        # The launch that took the preview is refused at once: its project file changed under it.
        with self.assertRaises(dashboard_action.ActionError):
            self.service.commit(p["id"])
        # A later launch holds the tightened patterns and takes up the same saved preview: the check at the write
        # boundary is the only one left between the stored note and the record.
        later = dashboard_action.Actions()
        service = io.Service(later)
        op = service.load(p["id"])
        with self.assertRaisesRegex(io.ReviewError, "publication checks"):
            service.append(op, op["event"])
        self.assertEqual(count, len(self.repo()["comments"]))
        self.assertEqual([], service.load(p["id"])["sent"])   # nothing was marked as sent
        self.assertEqual([], service.history())
        self.write_config(self.config)   # the restriction is lifted: the same preview saves
        service = io.Service(dashboard_action.Actions())
        self.assertEqual("defer", service.append(service.load(p["id"]), op["event"])["verdict"])
        self.assertEqual(count + 1, len(self.repo()["comments"]))

    def test_signed_map_edit_resumes_on_another_session_and_verifies_actual_comment(self):
        initial = project_map_io.preview(ts.REPO, 4, {"action": "add", "id": "format", "title": "Format",
                                                     "evidence": ["issue:3", "comment:18"]})
        project_map_io.commit(ts.REPO, 4, initial["operation"])
        p = self.draft()
        self.assertEqual("issue:3\ncomment:21", p["detail"]["after"])
        accepted = self.service.commit(p["id"])
        self.assertFalse(accepted["applied"])
        second = io.Service(dashboard_action.Actions())
        self.use_service(second)
        resumed = second.stage(dict(self.base, previous=accepted["review"]["comment"]), resume=True)
        applied = second.apply(resumed["id"])
        self.assertTrue(applied["applied"])
        self.assertEqual(applied, second.apply(resumed["id"]))
        mapped = project_map_io.current(ts.REPO, 4)
        self.assertEqual(mapped["history"][-1]["comment"], applied["receipt"]["comment"])
        self.assertEqual(["issue:3", "comment:21"], mapped["nodes"]["format"]["evidence"])
        with self.assertRaisesRegex(io.ReviewError, "newer review"):
            self.service.apply(p["id"])

    def test_real_output_body_preview_preserves_the_rest_and_verifies_successor(self):
        # A software release note remains editable when the same project also produces a paper.
        self.write_config(dict(self.config, profiles=["software", "research", "manuscript"]))
        self.actions = dashboard_action.Actions()
        self.add_chain()
        data = self.repo()
        old = "https://github.com/%s/issues/5#issuecomment-9102" % ts.REPO
        paragraph = "The release follows the reader policy in %s." % old
        base = data["issues"]["5"]["body"].replace("\r\n", "\n").replace("## Changed\n", "## Changed\n\n" + paragraph + "\n")
        data["issues"]["5"]["body"] = base
        self.save(data)
        # Exercise the normal dashboard model and immutable editor evidence through the fake GitHub CLI.
        service = io.Service(self.actions)
        result = service.state()
        changed = next(g for g in result["groups"] if g["id"] == "impact-9102")
        item = next(item for item in changed["items"] if item.get("edit_before") == paragraph)
        fields = dict(group=changed["id"], target=item["id"], revision=item["revision"], previous=0,
                      verdict="needs_update", note="", draft=item["edit_text"])
        preview = service.stage(fields)
        self.assertEqual("body", preview["action"])
        self.assertEqual(paragraph, preview["detail"]["before"])
        self.assertEqual(base, self.repo()["issues"]["5"]["body"])
        saved = service.commit(preview["id"])
        self.assertFalse(saved["applied"])
        self.assertEqual(base, self.repo()["issues"]["5"]["body"])
        with mock.patch.object(observer_store, "read_holds",
                               return_value=[dict(state="active", repo=ts.REPO, action="publish", scope={"issue": 5})]):
            with self.assertRaisesRegex(ValueError, "hold"):
                service.apply(preview["id"])
        self.assertEqual("needs_update", service.history()[-1]["verdict"])
        # Lose the edit reply, but read the exact body back through the fake API.
        original = recortex.gh
        def lost(args, **kwargs):
            result = original(args, **kwargs)
            if args[:2] == ["issue", "edit"]:
                raise recortex.GhError("lost body edit reply")
            return result
        with mock.patch.object(recortex, "gh", side_effect=lost):
            applied = service.apply(preview["id"])
        self.assertTrue(applied["applied"])
        self.assertEqual(io.normalized_digest(base.replace(paragraph, item["edit_text"])), applied["receipt"]["sha256"])
        self.assertEqual(recortex.normalize(base.replace(paragraph, item["edit_text"])),
                         recortex.normalize(self.repo()["issues"]["5"]["body"]))
        self.assertEqual(applied, service.apply(preview["id"]))
        refreshed = self.actions.snapshot()
        self.assertIn("Update verified", refreshed)
        self.assertIn("#issuecomment-%d" % applied["review"]["comment"], refreshed)
        self.assertFalse(self.actions.impact_dirty)

    def test_interrupted_map_write_can_be_reconciled_from_the_originating_device(self):
        first = project_map_io.preview(ts.REPO, 4, {"action": "add", "id": "format", "title": "Format",
                                                   "evidence": ["comment:18"]})
        project_map_io.commit(ts.REPO, 4, first["operation"])
        p = self.draft()
        self.service.commit(p["id"])
        with mock.patch.object(project_map_io, "commit", side_effect=recortex.GhError("temporarily unavailable")):
            with self.assertRaises(recortex.GhError):
                self.service.apply(p["id"])
        pending = self.service.history()[-1]
        self.assertEqual("applying", pending["verdict"])
        second = io.Service(dashboard_action.Actions())
        self.use_service(second)
        resolved = second.stage(dict(self.base, previous=pending["comment"]), resume=True)
        self.assertFalse(resolved["applied"])
        self.assertEqual(p["id"], resolved["id"])
        self.assertTrue(second.apply(p["id"])["applied"])

    def test_unknown_edit_history_cannot_restore_an_edited_review(self):
        p = self.draft("still_correct")
        review = self.service.commit(p["id"])["review"]
        data = self.repo()
        current = data["comments"][str(review["comment"])]
        current["last_edited_at"] = current["created_at"]
        self.save(data)
        self.assertEqual([], self.service.history())
        index.sync(ts.REPO, 4, rebuild=True, log=lambda *args: None)
        db = index.connect(ts.REPO, create=False)
        self.addCleanup(db.close)
        self.assertEqual([], ledger.read(db, ts.REPO, 4, {"octocat"}))
