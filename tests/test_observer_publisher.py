"""Append-only publication and ambiguous-request recovery, against fake GitHub."""

import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import time
import unittest
from unittest import mock

from tests import test_observer_store as fixtures

import observer_publisher as publisher
import observer_runtime as runtime
import observer_store as store
import lint_drafts
import record_format


class ObserverPublisherTests(unittest.TestCase):
    metadata = fixtures.ObserverStoreTests.metadata

    def setUp(self):
        fixtures.ObserverStoreTests.setUp(self)
        self.remote = self.base / "remote.json"
        shutil.copyfile(fixtures.ROOT / "tests/fixtures/repo-state.json", self.remote)
        trusted = self.base / "trusted"
        trusted.write_text(fixtures.REPO + " octocat hubot\n")
        os.environ.update(RECORTEX_GH=shlex.join([sys.executable, str(fixtures.ROOT / "tests/fake_gh.py")]),
                          FAKE_GH_STATE=str(self.remote), RECORTEX_TRUSTED_FILE=str(trusted))
        self.config = {"observer": {"enabled": True, "mode": "automatic", "publisher_id": runtime.publisher_id(create=True)}}
        body = (fixtures.ROOT / "examples/octo-repo/chain/1-idea.md").read_text()
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.capture([self.metadata()])
            candidate = {"kind": "idea", "issue": 3, "body": body, "sources": ["source1"]}
            ident = db.reserve_invocation("reviewer")
            db.complete_invocation(ident, True, output_hash=store.candidate_hash(candidate))
            self.key = db.review_complete("source1", candidate, {"reservation_id": ident, "accepted": True,
                                          "candidate_hash": store.candidate_hash(candidate)})
            self.db_path = Path(db.path)
            db.set_control("runtime_gate", {"test": True})
        runtime.approve(fixtures.REPO, self.config)
        self.gate = mock.patch.object(runtime, "gate_valid", return_value=True)
        self.gate.start()
        self.addCleanup(self.gate.stop)

    def state(self):
        return json.loads(self.remote.read_text())

    def calls(self):
        return self.state().get("log", [])

    def publish(self, **kwargs):
        return publisher.publish(fixtures.REPO, self.key, self.config, **kwargs)

    def _second(self, body, source="source2", review=None):
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.capture([self.metadata(source)])
            candidate = {"kind": "idea", "issue": 3, "body": body, "sources": [source]}
            ident = db.reserve_invocation("reviewer")
            db.complete_invocation(ident, True, output_hash=store.candidate_hash(candidate))
            return db.review_complete(source, candidate, dict({"reservation_id": ident, "accepted": True,
                                                               "candidate_hash": store.candidate_hash(candidate)},
                                                              **(review or {})))

    def test_frozen_body_with_an_outside_address_is_refused_before_any_write(self):
        body = (fixtures.ROOT / "examples/octo-repo/chain/1-idea.md").read_text().replace(
            "**What it would take:**", "![s](https://collector.example.test/p.png)\n\n**What it would take:**")
        key = self._second(body)
        for main_session in (False, True):
            with self.assertRaisesRegex(publisher.PublishError, "outbound_markup"):
                publisher.publish(fixtures.REPO, key, self.config, main_session=main_session)
        self.assertEqual([], [a for a in self.calls() if a[:2] == ["issue", "comment"]])

    def test_hidden_mentions_and_outside_targets_are_refused(self):
        repo = fixtures.REPO
        mentions = ["see \\`@hubot\\` now", "``a` @hubot `b``", "```\n@hubot\n```", "`@hubot`", "(@hubot)",
                    "x/@hubot", "\\@hubot"]
        for text in mentions:
            self.assertEqual("outbound_mention", publisher.outbound_problem("Plain text " + text, repo), text)
        self.assertIsNone(publisher.outbound_problem("Mail octocat@example.test about it.", repo))
        addresses = ["[status][1]\n\n[1]: https://collector.example.test/p.png",
                     "[status][1]\n\n[1]:\n  //collector.example.test/p",
                     "[x](//collector.example.test/x)", "[x](\\\\collector.example.test/x)",
                     "[x](javascript:alert(1))", "[x](data:text/html;base64,PHA+)", "[x](&#47;&#47;collector.example.test)",
                     "[x](\\/\\/collector.example.test)", "[x](../../other/repo)", "[x](https://github.com@collector.example.test/)",
                     "[x](https://github.com/%s/../../other)" % repo, "[x]( <//collector.example.test> )"]
        for text in addresses:
            self.assertIn(publisher.outbound_problem("Plain text " + text, repo), ("outbound_address", "outbound_markup"), text)
        self.assertEqual("outbound_markup", publisher.outbound_problem("![s][1]\n\n[1]: https://collector.example.test/p", repo))
        for text in ("[x](https://github.com/%s/issues/3)" % repo, "[x](issues/3)", "[x](#fold)", "As in #3."):
            self.assertIsNone(publisher.outbound_problem("Plain text " + text, repo), text)

    def test_forms_the_filters_cannot_parse_are_refused_outright(self):
        repo = fixtures.REPO
        hostile = {
            "outbound_address": ["[status][1]\n\n- [1]: https://collector.example.test/p",
                                 "[status][1]\n\n> [1]: //collector.example.test/p",
                                 "[status][1]\n\n      [1]: collector.example.test/p",
                                 "see //collector.example.test/x", "share \\\\server\\share", "WWW.collector.example.test",
                                 "data:text/html,x", "javascript:alert(1)", "mailto:octocat@example.test",
                                 "https://github.com/%s/x then //collector.example.test" % repo],
            "outbound_mention": ["caf\u00e9@octocat", "\u00e9@hubot"],
            "outbound_reference": ["fixed in other-org/other-repo#12", "see other-org/other-repo@abc1234",
                                   "from hubot@abc1234def", "Octo-Org/Other#3"]}
        for reason, texts in hostile.items():
            for text in texts:
                self.assertEqual(reason, publisher.outbound_problem("Plain text " + text + ".", repo), text)
        same = ["As in #3.", "As in GH-3.", "As in Octo-Org/Octo-Repo#3.", "At octo-org@abc1234.",
                "Mail octocat@example.test.", "**Effect:** none", "Took 12:30 minutes.",
                "See https://github.com/%s/issues/3#issuecomment-9003 for it." % repo]
        for text in same:
            self.assertIsNone(publisher.outbound_problem("Plain text " + text, repo), text)

    def test_a_mention_written_as_an_entity_waits(self):
        idea = (fixtures.ROOT / "examples/octo-repo/chain/1-idea.md").read_text()
        for mention in ("&#64;hubot", "&commat;hubot", "&#x40;hubot"):
            body = idea.replace("**What it would take:** A flag", "**What it would take:** Ask %s. A flag" % mention)
            self.assertEqual("outbound_mention", publisher.outbound_problem(body, fixtures.REPO), mention)
        for text in ("see &#47;&#47;collector.example.test", "data&#58;text/html,x", "other-org/other-repo&#35;12"):
            body = idea.replace("**What it would take:** A flag", "**What it would take:** %s. A flag" % text)
            self.assertIn(publisher.outbound_problem(body, fixtures.REPO), ("outbound_address", "outbound_reference"), text)

    def test_only_the_first_line_may_be_an_html_comment(self):
        idea = (fixtures.ROOT / "examples/octo-repo/chain/1-idea.md").read_text()
        self.assertIsNone(publisher.outbound_problem(idea, fixtures.REPO))
        marker = idea.splitlines()[0]
        hidden = {"a second marker": idea + "\n" + marker + "\n",
                  "another comment": idea + "\n<!-- hidden text -->\n",
                  "an unterminated comment": idea + "\n<!-- hidden text\n",
                  "a marker that closes early": idea.replace(" -->", "--><img src=x -->", 1)}
        for case, body in hidden.items():
            self.assertEqual("outbound_markup", publisher.outbound_problem(body, fixtures.REPO), case)

    def test_change_of_status_without_the_users_words_or_a_command_waits_for_the_main_session(self):
        idea = (fixtures.ROOT / "examples/octo-repo/chain/1-idea.md").read_text()
        data = self.state()
        data["comments"]["9050"] = {"issue": 3, "user": "octocat", "body": idea, "created_at": "2026-03-05T18:00:00Z"}
        self.remote.write_text(json.dumps(data))
        body = idea.replace("-08:00 -->", "-08:00 instead=9050 -->").replace("open quote", "open quote again")
        key = self._second(body)
        with self.assertRaisesRegex(publisher.PublishError, "status_change"):
            publisher.publish(fixtures.REPO, key, self.config)
        self.assertEqual([], self.calls())
        command = self._second(body.replace("again", "once more"), source="source3",
                               review={"status_authority": "command"})
        self.assertEqual("verified", publisher.publish(fixtures.REPO, command, self.config)["state"])
        self.assertEqual("verified", publisher.publish(fixtures.REPO, key, self.config, main_session=True)["state"])

    def test_daily_cap_counts_automatic_comments_only(self):
        self.config["observer"]["max_posts"] = 1
        runtime.approve(fixtures.REPO, self.config)
        self.assertEqual("verified", self.publish()["state"])
        body = (fixtures.ROOT / "examples/octo-repo/chain/1-idea.md").read_text().replace("open quote", "open quote again")
        key = self._second(body)
        with self.assertRaisesRegex(publisher.PublishError, "daily_cap"):
            publisher.publish(fixtures.REPO, key, self.config)
        self.assertEqual(1, len([a for a in self.calls() if a[:2] == ["issue", "comment"]]))
        with store.Store(fixtures.REPO, config=self.config) as db:
            self.assertEqual("ready", db.event(key)["state"])
        self.assertEqual("verified", publisher.publish(fixtures.REPO, key, self.config, main_session=True)["state"])
        self.assertEqual(25, runtime.settings({"observer": {"max_posts": 900}})["max_posts"])

    def test_deferred_event_waits_for_the_main_session(self):
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.transition(self.key, "deferred", reason="status_change")
        with self.assertRaisesRegex(publisher.PublishError, "not_ready"):
            self.publish()
        self.assertEqual([], self.calls())
        self.assertEqual("verified", self.publish(main_session=True)["state"])

    def test_marked_project_waits_for_the_main_session(self):
        self.config["fields"] = {"sensitive": ["people"]}
        with self.assertRaisesRegex(publisher.PublishError, "sensitive_project"):
            self.publish()
        self.assertEqual([], [a for a in self.calls() if a[:2] == ["issue", "comment"]])
        self.assertEqual("verified", publisher.publish(fixtures.REPO, self.key, self.config, main_session=True)["state"])

    def test_untrusted_repository_posts_nothing(self):
        with mock.patch.object(publisher.recortex, "trusted_repos", return_value={}):
            with self.assertRaisesRegex(publisher.PublishError, "not_trusted"):
                self.publish()
        self.assertEqual([], [a for a in self.calls() if a[:2] == ["issue", "comment"]])

    def test_account_outside_the_allowed_list_posts_nothing(self):
        with mock.patch.dict(os.environ, {"FAKE_GH_LOGIN": "someone-else"}):
            with self.assertRaisesRegex(publisher.PublishError, "account_not_allowed"):
                self.publish()
        self.assertEqual([], [a for a in self.calls() if a[:2] == ["issue", "comment"]])

    def test_exact_command_hold_stops_the_post(self):
        with mock.patch.object(publisher.observer_holds, "check_action", return_value={"decision": "deny"}):
            with self.assertRaisesRegex(publisher.PublishError, "explicit_hold"):
                self.publish()
        self.assertEqual([], [a for a in self.calls() if a[:2] == ["issue", "comment"]])

    def test_publish_time_lint_failure_posts_nothing(self):
        body = (fixtures.ROOT / "examples/octo-repo/chain/1-idea.md").read_text().replace(
            "**What it would take:** A flag", "**What it would take:** As in #99, a flag")
        key = self._second(body)
        with self.assertRaisesRegex(publisher.PublishError, "lint_failed"):
            publisher.publish(fixtures.REPO, key, self.config)
        self.assertEqual([], [a for a in self.calls() if a[:2] == ["issue", "comment"]])

    def test_read_back_refuses_another_body_or_issue(self):
        event = {"candidate": {"issue": 3}, "account": "octocat", "body": "posted text", "payload_hash": "a" * 64}
        good = {"id": 5, "issue_url": "https://api.github.com/repos/%s/issues/3" % fixtures.REPO,
                "user": {"login": "octocat"}, "created_at": "t", "updated_at": "t", "body": "posted text"}
        self.assertEqual(5, publisher._check_remote(fixtures.REPO, event, good)["comment_id"])
        for change, reason in (({"body": "other text"}, "remote_changed"),
                               ({"issue_url": good["issue_url"][:-1] + "4"}, "wrong_target"),
                               ({"user": {"login": "hubot"}}, "wrong_author")):
            with self.assertRaisesRegex(publisher.PublishError, reason):
                publisher._check_remote(fixtures.REPO, event, dict(good, **change))

    def test_verified_post_is_append_only_and_retry_is_noop(self):
        result = self.publish()
        self.assertEqual("verified", result["state"])
        self.assertEqual(9100, result["receipt"]["comment_id"])
        second = self.publish()
        self.assertEqual(result, second)
        writes = [args for args in self.calls() if args[:2] in (["issue", "comment"], ["issue", "edit"], ["issue", "create"])]
        self.assertEqual(1, len(writes))
        self.assertEqual(["issue", "comment"], writes[0][:2])
        with store.Store(fixtures.REPO, config=self.config) as db:
            self.assertTrue(db.get_control("sync_needed"))
            self.assertEqual([], db.pending())

    def test_verified_receipt_survives_handled_excerpt_expiry(self):
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.capture([self.metadata("excerpt-source")])
            candidate = dict(db.event(self.key)["candidate"], sources=["excerpt-source"], excerpts=["Reviewed evidence"])
            ident = db.reserve_invocation("reviewer")
            digest = store.candidate_hash(candidate)
            db.complete_invocation(ident, True, output_hash=digest)
            self.key = db.review_complete("excerpt-source", candidate,
                                          {"reservation_id": ident, "accepted": True, "candidate_hash": digest})
        first = self.publish()
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.db.execute("UPDATE events SET updated=? WHERE eventkey=?", (time.time() - 8 * 86400, self.key))
            db.expire_handled()
            self.assertNotIn("excerpts", db.event(self.key)["candidate"])
            self.assertEqual(digest, db.event(self.key)["review"]["candidate_hash"])
        self.assertEqual(first, self.publish())
        self.assertEqual(first, publisher.reconcile(fixtures.REPO, self.key, self.config))
        self.assertEqual(1, len([args for args in self.calls() if args[:2] == ["issue", "comment"]]))

    def test_verified_shortcut_still_rejects_a_changed_frozen_payload(self):
        self.publish()
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.db.execute("UPDATE events SET body=body || ? WHERE eventkey=?", ("Changed after verification", self.key))
        before = len(self.calls())
        with self.assertRaisesRegex(publisher.PublishError, "payload_changed"):
            self.publish()
        with self.assertRaisesRegex(publisher.PublishError, "payload_changed"):
            publisher.reconcile(fixtures.REPO, self.key, self.config)
        self.assertEqual(before, len(self.calls()))

    def test_verified_shortcut_rejects_a_receipt_bound_to_another_payload(self):
        self.publish()
        with store.Store(fixtures.REPO, config=self.config) as db:
            receipt = db.event(self.key)["receipt"]
            receipt["payload_hash"] = "a" * 64
            db.db.execute("UPDATE events SET receipt=? WHERE eventkey=?", (json.dumps(receipt), self.key))
        with self.assertRaisesRegex(publisher.PublishError, "receipt_changed"):
            self.publish()

    def test_timeout_after_acceptance_reconciles_without_second_post(self):
        with mock.patch.dict(os.environ, {"FAKE_GH_FAIL_AFTER": "issue comment"}):
            result = self.publish()
        self.assertEqual("uncertain", result["state"])
        recovered = publisher.reconcile(fixtures.REPO, self.key, self.config)
        self.assertEqual("verified", recovered["state"])
        self.assertEqual(1, len([args for args in self.calls() if args[:2] == ["issue", "comment"]]))

    def test_uncertain_absence_does_not_retry(self):
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.transition(self.key, "sending", account="octocat")
        result = self.publish()
        self.assertEqual("uncertain", result["state"])
        self.assertEqual("remote_not_found", result["reason"])
        self.assertEqual([], [args for args in self.calls() if args[:2] == ["issue", "comment"]])

    def test_conflicting_remote_copies_stay_uncertain(self):
        with mock.patch.dict(os.environ, {"FAKE_GH_FAIL_AFTER": "issue comment"}):
            self.publish()
        data = self.state()
        data["comments"]["9101"] = copy.deepcopy(data["comments"]["9100"])
        self.remote.write_text(json.dumps(data))
        result = publisher.reconcile(fixtures.REPO, self.key, self.config)
        self.assertEqual("duplicate_event", result["reason"])
        self.assertEqual("uncertain", result["state"])

    def test_wrong_author_is_not_adopted(self):
        with mock.patch.dict(os.environ, {"FAKE_GH_FAIL_AFTER": "issue comment"}):
            self.publish()
        data = self.state()
        data["comments"]["9100"]["user"] = "hubot"
        self.remote.write_text(json.dumps(data))
        result = publisher.reconcile(fixtures.REPO, self.key, self.config)
        self.assertEqual("wrong_author", result["reason"])

    def test_shadow_mode_cannot_post(self):
        self.config["observer"]["mode"] = "shadow"
        with self.assertRaisesRegex(publisher.PublishError, "not_approved"):
            self.publish()
        self.assertEqual([], self.calls())

    def test_runtime_failure_and_publication_pause_make_no_network_call(self):
        with mock.patch.object(runtime, "gate_valid", return_value=False):
            with self.assertRaisesRegex(publisher.PublishError, "runtime_unavailable"):
                self.publish()
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.set_control("publication_paused", True)
        with self.assertRaisesRegex(publisher.PublishError, "publication_paused"):
            self.publish()
        self.assertEqual([], self.calls())

    def test_changed_settings_invalidate_local_approval(self):
        self.config["observer"]["max_invocations"] = 10
        with self.assertRaisesRegex(publisher.PublishError, "not_approved"):
            self.publish()
        self.assertEqual([], self.calls())

    def test_account_changed_after_lint_is_not_used_for_a_post(self):
        with mock.patch.object(publisher, "_account", side_effect=["octocat", "hubot"]):
            with self.assertRaisesRegex(publisher.PublishError, "account_changed"):
                self.publish()
        with store.Store(fixtures.REPO, config=self.config) as db:
            self.assertEqual("ready", db.event(self.key)["state"])
            self.assertEqual("", db.event(self.key)["account"])
        self.assertEqual([], [args for args in self.calls() if args[:2] == ["issue", "comment"]])

    def test_account_changed_after_intent_remains_uncertain_without_post(self):
        with mock.patch.object(publisher, "_account", side_effect=["octocat", "octocat", "hubot"]):
            result = self.publish()
        self.assertEqual("account_changed", result["reason"])
        self.assertEqual("uncertain", result["state"])
        self.assertEqual([], [args for args in self.calls() if args[:2] == ["issue", "comment"]])

    def test_dry_run_changes_no_store_bytes_and_makes_no_network_call(self):
        before = self.db_path.read_bytes()
        stamp = self.db_path.stat().st_mtime_ns
        result = self.publish(dry_run=True)
        self.assertIn("gh issue comment 3", result["command"])
        self.assertEqual(before, self.db_path.read_bytes())
        self.assertEqual(stamp, self.db_path.stat().st_mtime_ns)
        self.assertEqual([], self.calls())

    def test_dry_run_on_missing_store_creates_no_queue(self):
        before = set((self.base / "state").rglob("*"))
        with self.assertRaisesRegex(store.StoreError, "missing"):
            publisher.publish("octo-org/another", self.key, self.config, dry_run=True)
        self.assertEqual(before, set((self.base / "state").rglob("*")))

    def test_review_reservation_tampering_refuses_before_network(self):
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.db.execute("UPDATE invocations SET output_hash=?", ("a" * 64,))
        with self.assertRaisesRegex(publisher.PublishError, "review_changed"):
            self.publish()
        self.assertEqual([], self.calls())

    def test_event_key_lint_and_legacy_parser_compatibility(self):
        with store.Store(fixtures.REPO, config=self.config) as db:
            event = db.event(self.key)
        self.assertEqual(self.key, record_format.parse_marker(record_format.first_lines(event["body"])[0])["eventkey"])
        findings = lint_drafts.lint_text(event["body"], kind="idea", config={})
        self.assertFalse(any(f.code in ("event-key", "marker-key") for f in findings))
        bad = event["body"].replace(self.key, "G" * 32)
        self.assertTrue(any(f.code == "event-key" for f in lint_drafts.lint_text(bad, kind="idea", config={})))
        import importlib.util
        spec = importlib.util.spec_from_file_location("legacy_event_reader", fixtures.ROOT / "tests/fixtures/compat-0.5.0/record_format.py")
        legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy)
        # 0.5.0 did not know ideas but still retains the event marker's time and extra key.
        self.assertEqual(self.key, legacy.parse_marker(record_format.first_lines(event["body"])[0])["eventkey"])
        self.assertEqual("2026-03-05T10:00-08:00", legacy.parse_event(event["body"])["at"])


if __name__ == "__main__":
    unittest.main()
