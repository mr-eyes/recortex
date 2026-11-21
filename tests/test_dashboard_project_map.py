"""Project map rendering, trust boundaries, and loopback edit review with fake GitHub."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_jobs_tasks as tj
import test_scripts as ts
sys.path.insert(0, ts.SCRIPTS)
import dashboard_action as action
import mind
import project_map
import project_map_io


class MapPageTests(unittest.TestCase):
    def test_safe_stable_nested_outline_and_alias_independent_address(self):
        state = project_map.empty()
        for change in ({"action": "add", "id": "persistent_id", "title": "Testing <ideas>", "description": "A & B"},
                       {"action": "add", "id": "leaf", "title": "Field notes", "parent": "persistent_id"},
                       {"action": "rename", "id": "persistent_id", "title": "Testing <revised>"}):
            state = project_map.apply(state, project_map.prepare(state, change))
        text = mind.render({"repo": ts.REPO, "name": "Project", "project_map": state})
        self.assertEqual([], ts.page_problems(text))
        self.assertIn("Testing &lt;revised&gt;", text)
        self.assertNotIn("Testing <ideas>", text)
        self.assertIn("Renamed Testing &lt;ideas&gt; to ", text)
        self.assertIn('id="%s"' % mind.branch_anchor("persistent_id"), text)
        self.assertNotIn('id="branch-apply"', text)
        self.assertNotIn('/map-editor"', text)
        self.assertIn("Branches under ", text)

    def test_move_history_uses_the_parent_names_at_the_time_of_the_change(self):
        state = project_map.empty()
        for change in ({"action": "add", "id": "old", "title": "Field work"},
                       {"action": "add", "id": "new", "title": "Methods"},
                       {"action": "add", "id": "grid", "title": "Sampling grid", "parent": "old"},
                       {"action": "move", "id": "grid", "parent": "new"},
                       {"action": "rename", "id": "new", "title": "Protocols"}):
            state = project_map.apply(state, project_map.prepare(state, change))
        text = mind.render({"repo": ts.REPO, "name": "Project", "project_map": state})
        self.assertIn("Moved from Field work to Methods", text)

    def test_no_branches_gives_entry_point_to_chat_and_local_editor(self):
        text = mind.render({"repo": ts.REPO, "interactive_actions": True})
        self.assertIn('/recortex:map', text)
        self.assertIn('href="/map-editor"', text)


class MapEditorTests(tj.TaskBox):
    def setUp(self):
        super().setUp()
        self.in_process(extra={"CLAUDE_PROJECT_DIR": self.project})
        self.actions = action.Actions()

    def test_live_preview_and_retry_update_authenticated_snapshot(self):
        before = len(self.repo()["comments"])
        old = self.actions.map_state()["state"]
        checked = self.actions.map_preview({"action": "add", "id": "stable", "title": "Parser"}, old["revision"])
        self.assertEqual(["stable"], checked["affected"])
        self.assertEqual(before, len(self.repo()["comments"]))
        saved = self.actions.map_commit(checked["id"])
        self.assertEqual("Parser", saved["state"]["nodes"]["stable"]["title"])
        self.assertEqual(saved, self.actions.map_commit(checked["id"]))
        self.assertEqual(before + 1, len(self.repo()["comments"]))
        self.assertIn('id="%s"' % mind.branch_anchor("stable"), self.actions.snapshot())

        with self.assertRaisesRegex(action.ActionError, "map_changed"):
            self.actions.map_preview({"action": "rename", "id": "stable", "title": "Reader"}, old["revision"])

    def test_request_cannot_apply_client_chosen_change_or_another_sessions_preview(self):
        checked = self.actions.map_preview({"action": "add", "title": "Reader"}, project_map.empty()["revision"])
        other = action.Actions()
        with self.assertRaises(action.ActionError):
            other.map_commit(checked["id"])
        with self.assertRaises(action.ActionError):
            self.actions.map_commit({"action": "delete", "id": "Reader"})
        with mock.patch.object(project_map_io, "commit") as post, mock.patch.object(self.actions, "account", return_value="changed"):
            with self.assertRaisesRegex(action.ActionError, "account_changed"):
                self.actions.map_commit(checked["id"])
            post.assert_not_called()
