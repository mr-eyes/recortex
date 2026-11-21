"""Local dashboard action boundaries and real helpers against fake GitHub. No sockets or models."""

import contextlib
import io
import html
import json
import os
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_jobs_tasks as tj
import test_scripts as ts
sys.path.insert(0, ts.SCRIPTS)
import dashboard_action as action
import observer_store
import recortex
import storage


class ActionTests(tj.TaskBox):
    def setUp(self):
        super().setUp()
        self.in_process(extra={"CLAUDE_PROJECT_DIR": self.project})
        self.actions = action.Actions()

    def preview(self):
        return self.actions.preview("7:9108:2", "I reran the conformance set and all twelve files passed.")

    def count(self):
        return len(self.repo()["comments"])

    def test_frozen_preview_records_one_verified_completion_with_truthful_provenance(self):
        before = self.count()
        preview = self.preview()
        self.assertEqual(before, self.count())
        self.assertIn("local dashboard", preview["body"])
        self.assertNotIn("in the chat", preview["body"])
        self.assertIn("done=9108:2", preview["body"])
        result = self.actions.commit(preview["id"], preview["body_hash"])
        self.assertEqual("recorded", result["state"])
        self.assertEqual("updated", result["refresh"])
        self.assertEqual(before + 1, self.count())
        self.assertEqual("done", self.task_at()["steps"][1]["state"])
        again = self.actions.commit(preview["id"], preview["body_hash"])
        self.assertEqual(result["receipt"], again["receipt"])
        self.assertEqual(before + 1, self.count())

    def test_changed_preview_bytes_or_step_refuse_without_a_write(self):
        preview = self.preview()
        before = self.count()
        with self.assertRaisesRegex(action.ActionError, "preview_expired_or_changed"):
            self.actions.commit(preview["id"], "0" * 64)
        self.post(9200, 5, tj.result_text(marker=" done=9108:2"))
        before = self.count()
        with self.assertRaisesRegex(action.ActionError, "step_changed"):
            self.actions.commit(preview["id"], preview["body_hash"])
        self.assertEqual(before, self.count())

    def test_edited_step_and_revoked_trust_refuse(self):
        preview = self.preview()
        data = self.repo()
        data["comments"]["9108"]["body"] = data["comments"]["9108"]["body"].replace("Rerun the conformance set", "Rerun another set")
        self.save(data)
        with self.assertRaisesRegex(action.ActionError, "step_changed"):
            self.actions.commit(preview["id"], preview["body_hash"])
        with mock.patch.object(recortex, "allowed_users", return_value=set()):
            with self.assertRaisesRegex(action.ActionError, "trust_required"):
                self.actions.commit(preview["id"], preview["body_hash"])

    def test_hold_and_unreadable_holds_fail_closed(self):
        preview = self.preview()
        before = self.count()
        for replacement in ([{"state": "active", "repo": ts.REPO, "action": "publish", "scope": {}}], None):
            kwargs = {"return_value": replacement} if replacement is not None else {"side_effect": observer_store.StoreError("unreadable")}
            with mock.patch.object(observer_store, "read_holds", **kwargs):
                with self.assertRaises(action.ActionError):
                    self.actions.commit(preview["id"], preview["body_hash"])
        self.assertEqual(before, self.count())

    def test_unrelated_command_hold_does_not_block_but_exact_publication_does(self):
        preview = self.preview()
        hold = {"state": "active", "repo": ts.REPO, "action": "command", "scope": {}, "command": "git push"}
        with mock.patch.object(observer_store, "read_holds", return_value=[hold]):
            self.actions.holds(7, self.actions.load(preview["id"]))
        hold["command"] = recortex.show_command(["issue", "comment", "7", "--repo", ts.REPO, "--body-file",
                                                 os.path.join(self.actions.folder, preview["id"] + ".md")])
        before = self.count()
        with mock.patch.object(observer_store, "read_holds", return_value=[hold]):
            with self.assertRaisesRegex(action.ActionError, "held"):
                self.actions.commit(preview["id"], preview["body_hash"])
        self.assertEqual(before, self.count())

    def test_lost_response_is_reconciled_without_a_second_append_after_restart(self):
        preview = self.preview()
        before = self.count()
        original = self.actions.post
        def lost(operation, dry_run=False):
            result = original(operation, dry_run)
            if not dry_run:
                raise recortex.GhError("reply lost")
            return result
        with mock.patch.object(self.actions, "post", side_effect=lost), \
                mock.patch.object(self.actions, "_reconcile", side_effect=lambda op: self.actions.public(op)):
            result = self.actions.commit(preview["id"], preview["body_hash"])
        self.assertEqual("uncertain", result["state"])
        restarted = action.Actions()
        result = restarted.reconcile(preview["id"])
        self.assertEqual("recorded", result["state"])
        self.assertEqual(before + 1, self.count())

    def test_unconfirmed_send_is_never_automatically_retried(self):
        preview = self.preview()
        before = self.count()
        original = self.actions.post
        def failed(operation, dry_run=False):
            if dry_run:
                return original(operation, True)
            raise recortex.GhError("disconnected")
        with mock.patch.object(self.actions, "post", side_effect=failed) as post:
            result = self.actions.commit(preview["id"], preview["body_hash"])
            self.assertEqual("uncertain", result["state"])
            calls = post.call_count
            self.actions.commit(preview["id"], preview["body_hash"])
            self.assertEqual(calls, post.call_count)
        with self.assertRaisesRegex(action.ActionError, "reconcile_first"):
            self.preview()
        self.assertEqual(before, self.count())

    def test_refresh_failure_does_not_erase_recorded_receipt(self):
        preview = self.preview()
        original = self.actions.sync
        calls = []
        def sync():
            calls.append(1)
            if len(calls) > 1:
                raise recortex.GhError("offline after acceptance")
            return original()
        with mock.patch.object(self.actions, "sync", side_effect=sync):
            result = self.actions.commit(preview["id"], preview["body_hash"])
        self.assertEqual(("recorded", "failed"), (result["state"], result["refresh"]))

    def test_existing_previews_cannot_bypass_another_launches_pending_send(self):
        first = self.preview()
        second = self.preview()
        other = action.Actions()
        third = other.preview("7:9108:2", "I independently checked all twelve files.")
        before = self.count()
        pending = self.actions.load(first["id"])
        for state in ("sending", "uncertain"):
            pending["state"] = state
            self.actions.save(pending)
            for backend, preview in ((self.actions, second), (other, third)):
                with self.subTest(state=state, same_launch=backend is self.actions), \
                        mock.patch.object(backend, "post") as post:
                    with self.assertRaisesRegex(action.ActionError, "reconcile_first"):
                        backend.commit(preview["id"], preview["body_hash"])
                    post.assert_not_called()
                    self.assertEqual("preview", backend.load(preview["id"])["state"])
        self.assertEqual(before, self.count())

    def test_account_change_during_task_render_preserves_receipt_without_body_write(self):
        preview = self.preview()
        before = self.repo()["issues"]["7"]["body"]
        original = action.tasks.render_task
        def switched(*args, **kwargs):
            # The newly active account is trusted too, but was not the account
            # bound to this operation. Change it after the render's live read.
            read = recortex.read_issue_body
            def changed(*read_args, **read_kwargs):
                body = read(*read_args, **read_kwargs)
                account.return_value = ("ready", "other")
                return body
            with mock.patch.object(recortex, "gh_account", return_value=("ready", "octocat")) as account, \
                    mock.patch.object(recortex, "allowed_users", return_value={"octocat", "other"}), \
                    mock.patch.object(recortex, "read_issue_body", side_effect=changed):
                return original(*args, **kwargs)
        with mock.patch.object(action.tasks, "render_task", side_effect=switched):
            result = self.actions.commit(preview["id"], preview["body_hash"])
        self.assertEqual(("recorded", "failed"), (result["state"], result["refresh"]))
        self.assertEqual(before, self.repo()["issues"]["7"]["body"])
        self.assertEqual(result["receipt"], self.actions.commit(preview["id"], preview["body_hash"])["receipt"])

    def test_trust_revoked_during_task_render_preserves_receipt_without_body_write(self):
        preview = self.preview()
        before = self.repo()["issues"]["7"]["body"]
        original = action.tasks.render_task
        def revoked(*args, **kwargs):
            read = recortex.read_issue_body
            def changed(*read_args, **read_kwargs):
                body = read(*read_args, **read_kwargs)
                allowed.return_value = set()
                return body
            with mock.patch.object(recortex, "allowed_users", return_value={"octocat"}) as allowed, \
                    mock.patch.object(recortex, "read_issue_body", side_effect=changed):
                return original(*args, **kwargs)
        with mock.patch.object(action.tasks, "render_task", side_effect=revoked):
            result = self.actions.commit(preview["id"], preview["body_hash"])
        self.assertEqual(("recorded", "failed"), (result["state"], result["refresh"]))
        self.assertEqual(before, self.repo()["issues"]["7"]["body"])

    def test_account_change_during_dry_run_cannot_send(self):
        preview = self.preview()
        before = self.count()
        original = self.actions.post
        def switched(operation, dry_run=False):
            result = original(operation, dry_run)
            account.return_value = "other"
            return result
        with mock.patch.object(self.actions, "account", return_value="octocat") as account, \
                mock.patch.object(self.actions, "post", side_effect=switched) as post:
            with self.assertRaisesRegex(action.ActionError, "account_changed"):
                self.actions.commit(preview["id"], preview["body_hash"])
        self.assertEqual(1, post.call_count)
        self.assertEqual(before, self.count())

    def test_expired_and_restarted_previews_cannot_be_confirmed(self):
        preview = self.preview()
        with mock.patch.object(action.time, "time", return_value=preview["created"] + 601):
            with self.assertRaisesRegex(action.ActionError, "preview_expired_or_changed"):
                self.actions.commit(preview["id"], preview["body_hash"])
        with self.assertRaisesRegex(action.ActionError, "preview_expired_or_changed"):
            action.Actions().commit(preview["id"], preview["body_hash"])

    def test_ledger_tampering_and_parallel_writer_are_refused(self):
        preview = self.preview()
        path = self.actions.path(preview["id"])
        signed = json.loads(storage.read_file(path))
        signed["operation"]["target"]["issue"] = 5
        storage.write_file(path, json.dumps(signed))
        with self.assertRaisesRegex(action.ActionError, "operation_invalid"):
            self.actions.commit(preview["id"], preview["body_hash"])
        with observer_store._exclusive_lock(self.actions.locks, "worker.lock"):
            with self.assertRaisesRegex(action.ActionError, "busy"):
                self.preview()

    def test_only_expired_unsent_previews_are_pruned(self):
        preview = self.preview()
        old = self.actions.load(preview["id"])
        old["created"] -= 601
        self.actions.save(old)
        pending = dict(old, id="a" * 32, state="uncertain")
        # A receipt's signed identity must remain valid after restart; its body
        # is irrelevant to pruning and is never sent in this test.
        self.actions.save(pending)
        self.actions.prune_previews()
        self.assertFalse(os.path.exists(self.actions.path(old["id"])))
        self.assertEqual("uncertain", self.actions.load(pending["id"])["state"])

    def test_cannot_send_when_durable_send_intent_cannot_be_saved(self):
        preview = self.preview()
        before = self.count()
        original = self.actions.save
        def save(operation):
            if operation["state"] == "sending":
                raise OSError("disk full")
            return original(operation)
        with mock.patch.object(self.actions, "save", side_effect=save):
            with self.assertRaises(OSError):
                self.actions.commit(preview["id"], preview["body_hash"])
        self.assertEqual(before, self.count())

    def test_partial_sync_and_changed_account_cannot_write(self):
        preview = self.preview()
        before = self.count()
        with mock.patch.object(action.index, "sync", return_value={"complete": False}):
            with self.assertRaisesRegex(action.ActionError, "sync_incomplete"):
                self.actions.commit(preview["id"], preview["body_hash"])
        with mock.patch.object(recortex, "gh_account", return_value=("ready", "untrusted")):
            with self.assertRaisesRegex(action.ActionError, "allowed_account_required"):
                self.actions.commit(preview["id"], preview["body_hash"])
        self.assertEqual(before, self.count())

    def test_authenticated_snapshot_has_scoped_step_links_without_changing_export(self):
        path = os.path.join(action.index.index_dir(ts.REPO), "mind.html")
        before = storage.read_file(path)
        page = self.actions.snapshot("/" + "a" * 32)
        self.assertIn('/' + 'a' * 32 + '/actions#step-7-9108-2', page)
        self.assertIn('/' + 'a' * 32 + '/suggestions', page)
        self.assertIn("Local interactive session.", page)
        self.assertIn("Report completed steps from Work.", page)
        self.assertTrue("connect-src 'none'" in html.unescape(page))
        self.assertEqual(before, storage.read_file(path))

    def test_suggestion_feedback_uses_current_trusted_sources_and_survives_reopen(self):
        import observer_context
        config = dict(self.actions.config, observer={"enabled": True, "advisory_mode": "deliver"})
        db = action.index.connect(ts.REPO, create=False)
        try:
            packet = observer_context.build_packet(db, ts.REPO, self.actions.authority(),
                                                   "Rewrite the parser for quoted lines in the line splitter", issue=7)
        finally:
            db.close()
        self.assertTrue(packet["advisories"])
        with observer_store.Store(ts.REPO, config=config) as store:
            self.assertTrue(action.observer_advisory.deliver(store, packet, "a private prompt", "session A",
                            mode="deliver", root=self.actions.root))
        with mock.patch.object(action.observer_runtime, "local_config", return_value=config), \
                mock.patch.object(action.observer_runtime, "approval_valid", return_value=True):
            data = self.actions.suggestions()
            self.assertEqual("deliver", data["mode"])
            row, = data["suggestions"]
            self.assertEqual(["decision", "failure"], [source["kind"] for source in row["sources"]])
            self.assertIn("#issuecomment-9105", row["sources"][0]["url"])
            self.assertNotIn("a private prompt", json.dumps(data))
            self.assertTrue(self.actions.suggestions(row["id"], True)["muted"])
            self.assertTrue(action.Actions().suggestions()["suggestions"][0]["muted"])
            with mock.patch.object(action.index, "memories", return_value={"items": {}}):
                redacted, = self.actions.suggestions()["suggestions"]
                self.assertEqual({"id", "at", "muted", "available"}, set(redacted))
                self.assertFalse(redacted["available"])
                with self.assertRaisesRegex(action.ActionError, "sources_unavailable"):
                    self.actions.suggestions(row["id"], True)
                self.assertFalse(self.actions.suggestions(row["id"], False)["muted"])
            with self.assertRaisesRegex(action.ActionError, "suggestion_missing"):
                self.actions.suggestions("b" * 64, True)
            with mock.patch.object(self.actions, "authority", side_effect=action.ActionError("trust_required", 403)):
                with self.assertRaisesRegex(action.ActionError, "trust_required"):
                    self.actions.suggestions(row["id"], False)

    def test_suggestion_history_requires_folder_approval_and_an_existing_issue(self):
        with mock.patch.object(action.observer_runtime, "approval_valid", return_value=False), \
                mock.patch.object(observer_store, "Store") as open_store:
            self.assertEqual({"repo": ts.REPO.lower(), "mode": "disabled", "suggestions": []}, self.actions.suggestions())
            with self.assertRaisesRegex(action.ActionError, "observer_disabled"):
                self.actions.suggestions("a" * 64, True)
            open_store.assert_not_called()
        with self.assertRaisesRegex(action.ActionError, "invalid_fields"):
            self.actions.suggestions("malformed", True)

    def test_receipt_with_changed_author_is_uncertain(self):
        preview = self.preview()
        with mock.patch.object(self.actions, "_reconcile", side_effect=lambda op: self.actions.public(op)):
            self.actions.commit(preview["id"], preview["body_hash"])
        data = self.repo()
        comment = next(c for c in data["comments"].values() if "eventkey=" + preview["id"] in c["body"])
        comment["user"] = "untrusted"
        self.save(data)
        before = self.count()
        self.assertEqual("uncertain", self.actions.reconcile(preview["id"])["state"])
        self.assertEqual(before, self.count())

    def test_unresolved_receipt_stays_visible_after_twenty_newer_previews(self):
        preview = self.preview()
        pending = self.actions.load(preview["id"])
        pending["state"] = "uncertain"
        self.actions.save(pending)
        for n in range(25):
            newer = dict(pending, id="%032x" % n, state="preview", created=pending["created"] + n + 1)
            self.actions.save(newer)
        states = self.actions.state()["operations"]
        self.assertEqual(21, len(states))
        self.assertEqual(preview["id"], states[0]["id"])
        self.assertEqual("uncertain", states[0]["state"])
        self.assertEqual({"issue", "comment", "ordinal"}, set(states[0]["target"]))

    def test_recorded_completion_does_not_claim_a_missing_task_list_was_updated(self):
        self.set_body(7, self.body(7).replace("## Plan", "## Other"))
        preview = self.preview()
        result = self.actions.commit(preview["id"], preview["body_hash"])
        self.assertEqual(("recorded", "no_task_list"), (result["state"], result["refresh"]))

    def test_missing_index_is_explicit_and_cli_configuration_failures_are_concise(self):
        with mock.patch.object(action.index, "connect", return_value=None):
            with self.assertRaisesRegex(action.ActionError, "index_unavailable"):
                self.actions.state()
        for config in ({}, {"repo": "not/a/repository"}):
            error = io.StringIO()
            with mock.patch.object(recortex, "load_config", return_value=config), contextlib.redirect_stderr(error):
                self.assertEqual(1, action.main([]))
            self.assertIn("configured project folder", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())


class HttpTests(unittest.TestCase):
    def request(self, path="/api/preview", data=None, changes=None):
        handler = object.__new__(action.Handler)
        handler.server = types.SimpleNamespace(host="127.0.0.1:1234", token="secret", actions=mock.Mock(),
                                               mount="/" + "a" * 32, server_port=1234)
        body = json.dumps(data if data is not None else {"step": "7:9108:2", "words": "done"}).encode()
        handler.headers = {"Host": "127.0.0.1:1234", "Origin": "http://127.0.0.1:1234",
                           "Content-Type": "application/json", "Authorization": "Bearer secret", "Content-Length": str(len(body))}
        handler.headers.update(changes or {})
        handler.path, handler.rfile = handler.server.mount + path, io.BytesIO(body)
        handler.respond = mock.Mock()
        handler.server.actions.preview.return_value = {"state": "preview"}
        handler.dispatch("POST")
        return handler

    def test_cross_origin_missing_token_and_dns_rebinding_never_reach_backend(self):
        for changes in ({"Host": "attacker.invalid:1234"}, {"Origin": "https://attacker.invalid"},
                        {"Origin": None}, {"Authorization": ""}, {"Sec-Fetch-Site": "cross-site"},
                        {"Content-Type": "text/plain"}, {"Authorization": "Bearer sécret"}):
            handler = self.request(changes=changes)
            self.assertEqual(403, handler.respond.call_args[0][0])
            handler.server.actions.preview.assert_not_called()

    def test_bounded_json_exact_operation_schema_and_no_paths_or_shell(self):
        for path, data, changes in (("/api/preview", {"step": "7:9108:2", "words": "done", "repo": "evil/repo"}, {}),
                                    ("/api/preview", None, {"Content-Length": "999999"}),
                                    ("/api/preview", None, {"Transfer-Encoding": "chunked"}),
                                    ("/../../settings.json", None, {}), ("/api/shell", {"cmd": "pwd"}, {})):
            handler = self.request(path, data, changes)
            self.assertIn(handler.respond.call_args[0][0], (400, 404))
            handler.server.actions.preview.assert_not_called()
        handler = self.request()
        self.assertEqual(200, handler.respond.call_args[0][0])
        handler.server.actions.preview.assert_called_once_with("7:9108:2", "done")

    def test_cookie_is_scoped_to_unpredictable_launch_prefix_and_wrong_mount_is_refused(self):
        handler = self.request("/api/session", {})
        self.assertTrue(handler.respond.call_args[1]["session"])
        handler.respond = types.MethodType(action.Handler.respond, handler)
        handler.send_response, handler.send_header, handler.end_headers = mock.Mock(), mock.Mock(), mock.Mock()
        handler.wfile = io.BytesIO()
        handler.respond(200, "{}", session=True)
        cookies = [c.args[1] for c in handler.send_header.call_args_list if c.args[0] == "Set-Cookie"]
        self.assertEqual(1, len(cookies))
        self.assertIn("Path=/" + "a" * 32 + "/;", cookies[0])
        self.assertIn("HttpOnly; SameSite=Strict", cookies[0])
        handler.respond = mock.Mock()
        handler.path = "/" + "b" * 32 + "/api/state"
        handler.dispatch("GET")
        self.assertEqual(404, handler.respond.call_args.args[0])
        handler.server.actions.state.assert_not_called()

    def test_snapshot_requires_auth_and_only_delivers_context_after_auth(self):
        handler = self.request(changes={"Authorization": ""})
        handler.path = handler.server.mount + "/snapshot"
        handler.dispatch("GET")
        self.assertEqual(403, handler.respond.call_args.args[0])
        handler.server.actions.snapshot.assert_not_called()
        handler.headers["Cookie"] = "recortex_action=secret"
        handler.server.actions.snapshot.return_value = "<p>trusted context</p>"
        handler.dispatch("GET")
        self.assertEqual(200, handler.respond.call_args.args[0])
        handler.server.actions.snapshot.assert_called_once_with(handler.server.mount)

    def test_personal_suggestions_require_same_origin_and_an_authenticated_session(self):
        path, data = "/api/suggestions/feedback", {"id": "1" * 64, "muted": True}
        for changed in ({"Origin": None}, {"Authorization": ""}, {"Origin": "http://foreign.invalid"}):
            handler = self.request(path, data, changed)
            self.assertEqual(403, handler.respond.call_args.args[0])
            handler.server.actions.suggestions.assert_not_called()
        handler = self.request(path, dict(data, muted=1))
        self.assertEqual(400, handler.respond.call_args.args[0])
        handler.server.actions.suggestions.assert_not_called()
        handler = self.request(changes={"Authorization": ""})
        handler.path = handler.server.mount + "/api/suggestions"
        handler.dispatch("GET")
        self.assertEqual(403, handler.respond.call_args.args[0])
        handler.server.actions.suggestions.assert_not_called()
        handler.server.actions.suggestions.return_value = {"suggestions": []}
        handler.headers["Cookie"] = "recortex_action=secret"
        handler.dispatch("GET")
        self.assertEqual(200, handler.respond.call_args.args[0])

    def test_first_snapshot_without_index_keeps_refresh_controls_available(self):
        handler = self.request()
        handler.path = handler.server.mount + "/snapshot"
        handler.server.actions.snapshot.side_effect = action.ActionError("index_unavailable")
        handler.dispatch("GET")
        self.assertEqual(200, handler.respond.call_args.args[0])
        self.assertIn('id="refresh"', handler.respond.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
