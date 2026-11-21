"""The shared project hierarchy, append protocol and immutable editor evidence."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_scripts as ts
sys.path.insert(0, ts.SCRIPTS)
import index
import observer_store
import project_map as model
import project_map_io as api
import recortex


class ReducerTests(unittest.TestCase):
    def setUp(self):
        self.state = model.empty()
        self.apply("add", "core", title="Calibration")
        self.apply("add", "sample", title="Samples", parent="core")
        self.apply("add", "tools", title="Tools")

    def apply(self, action, key, **fields):
        op = model.prepare(self.state, dict(fields, action=action, id=key))
        self.state = model.apply(self.state, op)
        return op

    def test_binding_retry_and_disjoint_concurrent_changes(self):
        op = model.prepare(self.state, {"action": "rename", "id": "sample", "title": "Specimens"})
        self.apply("rename", "tools", title="Equipment")
        self.state = model.apply(self.state, op)
        self.assertIs(self.state, model.apply(self.state, op))
        with self.assertRaisesRegex(model.MapError, "identifier"):
            model.apply(self.state, dict(op, base="a" * 64))
        stale = model.prepare(self.state, {"action": "move", "id": "sample", "parent": "tools"})
        self.apply("rename", "sample", title="Sample set")
        with self.assertRaisesRegex(model.MapError, "after preview"):
            model.apply(self.state, stale)

    def test_split_merge_and_restore_keep_identity_evidence_and_links(self):
        self.apply("update", "sample", evidence=["issue:27"], links=["tools"])
        self.apply("split", "core", branches=[{"id": "a", "title": "Bench", "children": ["sample"]},
                                             {"id": "b", "title": "Field"}])
        self.assertEqual("a", self.state["nodes"]["sample"]["parent"])
        self.apply("update", "tools", links=["a"])
        self.apply("merge", "a", into="b")
        self.assertTrue(self.state["nodes"]["a"]["deleted"])
        self.assertEqual("b", self.state["nodes"]["sample"]["parent"])
        self.assertEqual(["b"], self.state["nodes"]["tools"]["links"])
        self.apply("restore", "a")
        self.assertFalse(self.state["nodes"]["a"]["deleted"])
        self.assertEqual("b", self.state["nodes"]["sample"]["parent"])
        self.assertEqual(["issue:27"], self.state["nodes"]["sample"]["evidence"])

    def test_child_policies_and_explicit_restore(self):
        delete = self.apply("delete", "core", children="subtree")
        self.assertEqual(delete["id"], self.state["nodes"]["sample"]["deleted_by"])
        self.apply("restore", "core")
        self.assertFalse(self.state["nodes"]["sample"]["deleted"])
        self.apply("delete", "core", children="keep")
        self.assertIsNone(self.state["nodes"]["sample"]["parent"])
        self.apply("restore", "core")
        self.assertIsNone(self.state["nodes"]["sample"]["parent"])
        self.apply("delete", "core", children="keep")
        self.apply("add", "replacement", title="Calibration")
        with self.assertRaisesRegex(model.MapError, "duplicate"):
            self.apply("restore", "core")

    def test_observer_respects_corrections_and_tombstones_across_branches(self):
        self.apply("rename", "sample", title="Cohort")
        self.apply("delete", "sample", children="keep")
        for title in ("Samples", "Cohort"):
            change = {"action": "add", "title": title, "parent": "tools", "evidence": ["comment:18"]}
            with self.assertRaises(model.MapError):
                model.prepare(self.state, change, "observer")
        self.apply("restore", "sample")
        with self.assertRaisesRegex(model.MapError, "corrected"):
            model.prepare(self.state, {"action": "rename", "id": "sample", "title": "Other", "evidence": ["issue:99"]},
                          "observer")
        with self.assertRaisesRegex(model.MapError, "review"):
            model.prepare(self.state, {"action": "delete", "id": "sample", "children": "keep"}, "observer")

    def test_automatic_rename_cannot_reintroduce_another_nodes_corrected_or_deleted_title(self):
        self.apply("rename", "sample", title="Specimens")
        change = {"action": "rename", "id": "tools", "title": "SAMPLES", "evidence": ["comment:18"]}
        with self.assertRaisesRegex(model.MapError, "explicit review"):
            model.prepare(self.state, change, "observer")
        self.apply("delete", "sample", children="keep")
        change["title"] = "Specimens"
        with self.assertRaisesRegex(model.MapError, "explicit review"):
            model.prepare(self.state, change, "observer")
        reviewed = model.prepare(self.state, change, "user")
        self.assertEqual("Specimens", model.apply(self.state, reviewed)["nodes"]["tools"]["title"])

    def test_stale_descendants_and_cycles(self):
        plan = model.prepare(self.state, {"action": "delete", "id": "core", "children": "subtree"})
        self.apply("add", "extra", title="Extra", parent="sample")
        with self.assertRaisesRegex(model.MapError, "after preview"):
            model.apply(self.state, plan)
        for change in ({"action": "move", "id": "core", "parent": "extra"},
                       {"action": "merge", "id": "core", "into": "sample"}):
            with self.assertRaisesRegex(model.MapError, "ancestor"):
                model.prepare(self.state, change)

    def test_sibling_name_collisions_across_move_and_merge(self):
        self.apply("add", "in-tools", title="Samples", parent="tools")
        for action, options in (("merge", {"id": "tools", "into": "core"}),
                                ("move", {"id": "sample", "parent": "tools"}),
                                ("split", {"id": "core", "branches": [{"title": "Samples"}, {"title": "New"}]})):
            with self.assertRaises(model.MapError):
                model.prepare(self.state, dict(options, action=action))


class HubTests(ts.RecordSandbox):
    def setUp(self):
        super().setUp()
        self.in_process(extra={"CLAUDE_PROJECT_DIR": self.project})
        self.hub = self.config["hub"]
        self.change = {"action": "add", "id": "intake", "title": "Intake", "evidence": ["issue:12"]}

    def preview(self, source="user"):
        return api.preview(ts.REPO, self.hub, self.change, source)

    def test_rebuild_and_chat_retry_use_one_verified_comment(self):
        first = self.preview()
        op = first["operation"]
        done = api.commit(ts.REPO, self.hub, op)
        self.assertGreater(done["comment"], 0)
        before = len(self.repo()["comments"])
        self.assertEqual(done, api.commit(ts.REPO, self.hub, op))
        self.assertEqual(before, len(self.repo()["comments"]))
        index.sync(ts.REPO, self.hub, rebuild=True, log=lambda *args: None)
        db = index.connect(ts.REPO, create=False)
        self.addCleanup(db.close)
        self.assertEqual(done["state"], model.read_map(db, self.hub, recortex.allowed_users(ts.REPO)))
        self.assertEqual({}, model.read_map(db, self.hub, set())["nodes"])
        self.assertFalse(any(e["comment"] == done["comment"] for e in index.timeline(db)))

    def test_edit_even_in_the_creation_second_or_foreign_author_is_excluded(self):
        op = self.preview()["operation"]
        cid = str(api.commit(ts.REPO, self.hub, op)["comment"])
        data = self.repo()
        row = data["comments"][cid]
        row["edits"] = [{"at": row["created_at"], "by": "intruder"}]
        self.save(data)
        self.assertEqual({}, api.current(ts.REPO, self.hub)["nodes"])
        index.sync(ts.REPO, self.hub, rebuild=True, log=lambda *args: None)
        db = index.connect(ts.REPO, create=False)
        self.addCleanup(db.close)
        self.assertEqual({}, model.read_map(db, self.hub, recortex.allowed_users(ts.REPO))["nodes"])
        row.pop("edits")
        row["user"] = "intruder"
        self.save(data)
        self.assertEqual({}, api.current(ts.REPO, self.hub)["nodes"])

    def test_lost_reply_idempotent_reconciliation_and_holds(self):
        op = self.preview()["operation"]
        before = len(self.repo()["comments"])
        hold = {"repo": ts.REPO, "action": "publish", "state": "active", "scope": {}}
        with mock.patch.object(observer_store, "read_holds", return_value=[hold]):
            with self.assertRaisesRegex(model.MapError, "hold"):
                api.commit(ts.REPO, self.hub, op)
        self.assertEqual(before, len(self.repo()["comments"]))
        original = recortex.gh

        def lost(args, **kwargs):
            result = original(args, **kwargs)
            if args[:2] == ["issue", "comment"]:
                raise recortex.GhError("response lost")
            return result
        with mock.patch.object(recortex, "gh", side_effect=lost):
            result = api.commit(ts.REPO, self.hub, op)
        self.assertEqual(result, api.commit(ts.REPO, self.hub, op))
        self.assertEqual(before + 1, len(self.repo()["comments"]))

    def test_observer_requires_fresh_permission_and_signed_local_preview(self):
        op = self.preview("observer")["operation"]
        with self.assertRaisesRegex(model.MapError, "authorization"):
            api.commit(ts.REPO, self.hub, op)
        with self.assertRaisesRegex(model.MapError, "paused"):
            api.commit(ts.REPO, self.hub, op, guard=mock.Mock(side_effect=model.MapError("paused")))
        with self.assertRaisesRegex(model.MapError, "Preview"):
            api.commit(ts.REPO, self.hub, dict(op, change=dict(op["change"], title="Tampered")), guard=lambda: None)
        self.assertEqual({}, api.current(ts.REPO, self.hub)["nodes"])

    def test_observer_rechecks_scope_and_holds_after_verifying_evidence(self):
        op = self.preview("observer")["operation"]
        before = len(self.repo()["comments"])
        with self.assertRaisesRegex(model.MapError, "configured project or hub"):
            api.commit(ts.REPO, self.hub, op, guard=lambda: self.write_config(dict(self.config, hub=self.hub + 1)))
        self.assertEqual(before, len(self.repo()["comments"]))
        self.write_config(self.config)
        holds = []
        with mock.patch.object(observer_store, "read_holds", return_value=holds):
            with self.assertRaisesRegex(model.MapError, "hold"):
                api.commit(ts.REPO, self.hub, op, guard=lambda: holds.append(
                    {"repo": ts.REPO, "state": "active", "action": "publish", "scope": {}}))
        self.assertEqual(before, len(self.repo()["comments"]))
        self.assertFalse(api.load(ts.REPO, self.hub, op["id"])["sending"])

    def test_refused_content_is_never_kept_in_a_preview(self):
        self.write_config(dict(self.config, restricted_patterns=[r"\bSample-[0-9]+\b"]))
        for content in ({"title": "Sample-803"}, {"evidence": ["file:notes/Sample-803.md"]},
                        {"id": "Sample-803"}, {"description": "ghp_" + "A" * 36},
                        {"description": "Send results to sample@example.org"}):
            with self.subTest(fields=list(content)):
                with self.assertRaisesRegex(model.MapError, "publication checks"):
                    api.preview(ts.REPO, self.hub, dict(self.change, **content), op_id="preview-check")
                self.assertIsNone(api.load(ts.REPO, self.hub, "preview-check"))
        op = self.preview()["operation"]
        api.commit(ts.REPO, self.hub, op)
        branches = [{"title": "Bench"}, {"title": "Sample-803"}]
        with self.assertRaisesRegex(model.MapError, "publication checks"):
            api.preview(ts.REPO, self.hub, {"action": "split", "id": "intake", "branches": branches},
                        op_id="split-check")
        self.assertIsNone(api.load(ts.REPO, self.hub, "split-check"))

    def test_organization_collaborator_gets_the_trusted_entry_instruction(self):
        with mock.patch.object(recortex, "gh_account", return_value=("ready", "hubot")):
            with self.assertRaisesRegex(model.MapError, "add your GitHub login.*trusted-repos entry"):
                self.preview()

    def test_relative_evidence_and_fresh_privacy_rules_at_send(self):
        refs = ["file:docs/Bench notes.md", "issue:12", "comment:42"]
        op = api.preview(ts.REPO, self.hub, dict(self.change, evidence=refs))["operation"]
        before = len(self.repo()["comments"])
        self.write_config(dict(self.config, restricted_patterns=["Bench notes"]))
        with self.assertRaisesRegex(model.MapError, "publication checks"):
            api.commit(ts.REPO, self.hub, op)
        self.assertEqual(before, len(self.repo()["comments"]))
        self.assertFalse(api.load(ts.REPO, self.hub, op["id"])["sending"])
        self.write_config(self.config)
        result = api.commit(ts.REPO, self.hub, op)
        self.assertEqual(refs, result["state"]["nodes"]["intake"]["evidence"])
        child = {"action": "add", "id": "Sample-803", "parent": "intake", "title": "Sample cards"}
        api.commit(ts.REPO, self.hub, api.preview(ts.REPO, self.hub, child)["operation"])
        self.write_config(dict(self.config, restricted_patterns=[r"^Sample-[0-9]+$"]))
        with self.assertRaisesRegex(model.MapError, "publication checks"):
            api.preview(ts.REPO, self.hub, {"action": "delete", "id": "intake", "children": "keep"},
                        op_id="child-check")
        self.assertIsNone(api.load(ts.REPO, self.hub, "child-check"))

    def test_observer_review_preview_cannot_be_committed_from_chat(self):
        op = model.prepare(model.empty(), self.change, "user")
        api.remember(ts.REPO, self.hub, op, channel="observer")
        with self.assertRaisesRegex(model.MapError, "authorization"):
            api.commit(ts.REPO, self.hub, op)
        with self.assertRaisesRegex(model.MapError, "different change"):
            api.remember(ts.REPO, self.hub, op, channel="manual")
        verified = api.commit(ts.REPO, self.hub, op, guard=lambda: None)
        self.assertIn("intake", verified["state"]["nodes"])
