"""Tests of the usage view (1.5.0): usage.db, the subagent-stop hook, the injected-characters count, `usage.py show` and
the two views of the mind page. Nothing here reaches GitHub; the scripts run against tests/fake_gh.py."""

import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_scripts as ts  # noqa: E402
from observer_source_fixture import fixture_trusted_ancestors  # noqa: E402

sys.path.insert(0, ts.SCRIPTS)
import hooks  # noqa: E402
import observer_sources  # noqa: E402
import recortex  # noqa: E402
import storage  # noqa: E402
import usage  # noqa: E402

TRANSCRIPT = os.path.join(ROOT, "tests", "fixtures", "subagent-transcript.jsonl")
SESSION = "5b0c7f1e-2d44-4c1a-9e53-0a6d1f8b7c20"
AGENT_ID = "a3f19c7b2e8d4a05c"
DAY = 86400
NOW = 1794945600   # 2026-11-17 20:00 UTC


class UsageBox(ts.RecordSandbox):
    """The example record in a fake repository, a configured and trusted project folder, and the state in this test's own
    folder (in-process, as the scripts see it)."""

    def setUp(self):
        super().setUp()
        self.native = tempfile.mkdtemp(prefix="usage-native-", dir="/tmp")
        os.chmod(self.native, 0o700)
        self.addCleanup(shutil.rmtree, self.native, True)
        ancestors = fixture_trusted_ancestors(self.native)
        ancestors.__enter__()
        self.addCleanup(ancestors.__exit__, None, None, None)
        self.in_process({"CLAUDE_CONFIG_DIR": self.native, "CLAUDE_PROJECT_DIR": self.project})
        self.env["CLAUDE_CONFIG_DIR"] = self.native

    def rows(self, table):
        path = usage.db_path(ts.REPO)
        if not path or not os.path.exists(path):
            return []
        db = sqlite3.connect(path)
        try:
            return db.execute("SELECT * FROM %s ORDER BY rowid" % table).fetchall()
        finally:
            db.close()

    def state_folder(self):
        return storage.folder(storage.state_top(), storage.repo_folder(ts.REPO))


class UsageFileTests(UsageBox):
    def test_a_subagent_run_keeps_four_whole_numbers_and_a_model_name(self):
        self.assertTrue(usage.note_agent(ts.REPO, "recortex:record-auditor", "claude-haiku-5-5",
                                         {"input_tokens": 5, "output_tokens": 201, "cache_read": 35003,
                                          "cache_write": 1200}, now=NOW))
        self.assertEqual([(NOW, "record-auditor", "claude-haiku-5-5", 5, 201, 35003, 1200)], self.rows("agents"))

    def test_numbers_that_are_not_whole_numbers_become_zero_or_are_clamped(self):
        usage.note_agent(ts.REPO, "recortex:ask", "x<img src=x onerror=1>",
                         {"input_tokens": "9", "output_tokens": -5, "cache_read": 3.5, "cache_write": 10 ** 12}, now=NOW)
        usage.note_agent(ts.REPO, "recortex:ask", "claude-haiku-5-5",
                         {"input_tokens": True, "output_tokens": None, "cache_read": [1], "cache_write": {"a": 1}}, now=NOW)
        self.assertEqual([(NOW, "ask", "", 0, 0, 0, 10 ** 9), (NOW, "ask", "claude-haiku-5-5", 0, 0, 0, 0)],
                         self.rows("agents"))

    def test_an_agent_that_is_not_on_the_list_writes_nothing(self):
        for agent in ("recortex:other", "recortex:record-auditor; DROP TABLE agents", "other:ask", "", None, 7,
                      "recortex:ask-more", "xrecortex:ask"):
            self.assertFalse(usage.note_agent(ts.REPO, agent, "m", {"input_tokens": 1}), agent)
        self.assertEqual([], self.rows("agents"))

    def test_injected_characters_add_up_by_day_and_event(self):
        for characters in (120, 80):
            self.assertTrue(usage.note_injected(ts.REPO, "prompt-submit", characters, now=NOW))
        self.assertTrue(usage.note_injected(ts.REPO, "session-start", 3000, now=NOW))
        self.assertTrue(usage.note_injected(ts.REPO, "prompt-submit", 50, now=NOW + DAY))
        self.assertEqual([("2026-11-17", "prompt-submit", 200, 2), ("2026-11-17", "session-start", 3000, 1),
                          ("2026-11-18", "prompt-submit", 50, 1)], sorted(self.rows("injected")))

    def test_an_event_off_the_list_or_a_bad_count_writes_nothing(self):
        for event, count in (("pre-tool", 10), ("session-start; x", 10), ("session-start", 0), ("session-start", -3),
                             ("session-start", True), ("session-start", "40"), ("session-start", 2.5), (None, 5)):
            self.assertFalse(usage.note_injected(ts.REPO, event, count), (event, count))
        self.assertEqual([], self.rows("injected"))

    def test_the_file_is_private_in_a_private_folder(self):
        usage.note_injected(ts.REPO, "session-start", 10)
        folder = self.state_folder()
        self.assertEqual(0o700, stat.S_IMODE(os.lstat(folder).st_mode))
        self.assertEqual(0o600, stat.S_IMODE(os.lstat(os.path.join(folder, "usage.db")).st_mode))

    def test_a_file_with_loose_permissions_is_not_used(self):
        usage.note_injected(ts.REPO, "session-start", 10)
        path = os.path.join(self.state_folder(), "usage.db")
        os.chmod(path, 0o644)
        self.assertFalse(usage.note_injected(ts.REPO, "session-start", 10))
        self.assertIn("refused", usage.problem(ts.REPO))
        os.chmod(path, 0o600)
        self.assertEqual("", usage.problem(ts.REPO))

    def test_a_symlink_is_not_followed(self):
        folder = self.state_folder()
        target = os.path.join(folder, "elsewhere.db")
        sqlite3.connect(target).close()
        os.chmod(target, 0o600)
        os.symlink(target, os.path.join(folder, "usage.db"))
        self.assertFalse(usage.note_agent(ts.REPO, "recortex:ask", "m", {"input_tokens": 1}))
        self.assertFalse(usage.note_injected(ts.REPO, "session-start", 10))
        self.assertIn("refused", usage.problem(ts.REPO))
        db = sqlite3.connect(target)
        try:
            self.assertEqual([], db.execute("SELECT name FROM sqlite_master").fetchall())
        finally:
            db.close()

    def test_a_locked_file_makes_a_writer_give_up_in_time(self):
        usage.note_injected(ts.REPO, "session-start", 10)
        holder = sqlite3.connect(os.path.join(self.state_folder(), "usage.db"), isolation_level=None)
        holder.execute("BEGIN EXCLUSIVE")
        self.addCleanup(holder.close)
        started = time.monotonic()
        self.assertFalse(usage.note_injected(ts.REPO, "prompt-submit", 10))
        self.assertFalse(usage.note_agent(ts.REPO, "recortex:ask", "m", {"input_tokens": 1}))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual("locked or not writable", usage.problem(ts.REPO))
        holder.execute("ROLLBACK")
        self.assertEqual("", usage.problem(ts.REPO))

    def test_the_problem_line_changes_nothing(self):
        self.assertEqual("", usage.problem(ts.REPO))
        folder = storage.folder(storage.state_top(), storage.repo_folder(ts.REPO), create=False)
        self.assertTrue(folder is None or not os.path.exists(os.path.join(folder, "usage.db")))

    def test_rows_older_than_35_days_are_removed(self):
        usage.note_agent(ts.REPO, "recortex:ask", "m", {"input_tokens": 1}, now=NOW - 36 * DAY)
        usage.note_agent(ts.REPO, "recortex:ask", "m", {"input_tokens": 2}, now=NOW - 34 * DAY)
        usage.note_injected(ts.REPO, "session-start", 10, now=NOW - 36 * DAY)
        usage.note_injected(ts.REPO, "session-start", 20, now=NOW - 34 * DAY)
        self.assertEqual(2, usage.cleanup(ts.REPO, now=NOW))
        self.assertEqual([2], [row[3] for row in self.rows("agents")])
        self.assertEqual([20], [row[2] for row in self.rows("injected")])

    def test_cleanup_with_no_file_does_nothing(self):
        self.assertEqual(0, usage.cleanup(ts.REPO, now=NOW))

    def test_the_database_holds_no_text_but_a_fixed_name_a_model_and_a_day(self):
        usage.note_agent(ts.REPO, "recortex:record-drafter", "claude-haiku-5-5", {"input_tokens": 1}, now=NOW)
        usage.note_injected(ts.REPO, "prompt-submit", 5, now=NOW)
        for table in ("agents", "injected"):
            for row in self.rows(table):
                for value in row:
                    self.assertTrue(isinstance(value, int) or re.fullmatch(r"[A-Za-z0-9_.:-]+", value), value)


def store_row(day, tier, job, calls, tokens_in, tokens_out, model="claude-haiku-5-5"):
    return {"day": day, "tier": tier, "job": job, "model": model, "calls": calls, "input_tokens": tokens_in,
            "output_tokens": tokens_out}


class ReportTests(UsageBox):
    def rows_of_the_store(self):
        return [store_row("2026-11-17", "sort", "sort", 12, 80000, 24000),
                store_row("2026-11-17", "middle", "memories", 5, 50000, 9000, "claude-sonnet-5-5"),
                store_row("2026-11-17", "middle", "status", 1, 3000, 700, "claude-sonnet-5-5"),
                store_row("2026-11-14", "sort", "sort", 6, 40000, 12000),
                store_row("2026-11-12", "strong", "paper", 1, 40000, 9000, "claude-opus-5-5"),
                store_row("2026-10-20", "sort", "sort", 9, 60000, 18000)]

    def report(self, rows=None, today=None):
        return usage.report(ts.REPO, ts.EXAMPLE_CONFIG, now=NOW, rows=self.rows_of_the_store() if rows is None else rows,
                            today=today if today is not None else {"sort": {"calls": 12, "tokens": 104000},
                                                                   "middle": {"calls": 6, "tokens": 62700}})

    def test_sums_by_tier_job_and_day(self):
        data = self.report()
        seven = {(row["tier"], row["job"]): (row["calls"], row["input"], row["output"]) for row in data["periods"]["7"]}
        self.assertEqual({("sort", "sort"): (18, 120000, 36000), ("middle", "memories"): (5, 50000, 9000),
                          ("middle", "status"): (1, 3000, 700), ("strong", "paper"): (1, 40000, 9000)}, seven)
        thirty = {(row["tier"], row["job"]): row["calls"] for row in data["periods"]["30"]}
        self.assertEqual(27, thirty[("sort", "sort")])
        self.assertEqual(30, len(data["days"]))
        self.assertEqual("2026-11-17", data["days"][-1]["day"])
        self.assertEqual("2026-10-19", data["days"][0]["day"])
        last = data["days"][-1]
        self.assertEqual((104000, 62700, 0), (last["sort"], last["middle"], last["strong"]))
        self.assertEqual(49000, data["days"][-6]["strong"])

    def test_the_five_largest_of_the_week_include_subagents(self):
        usage.note_agent(ts.REPO, "recortex:record-reviewer", "claude-sonnet-5-5",
                         {"input_tokens": 500, "output_tokens": 100, "cache_read": 100000, "cache_write": 2000}, now=NOW - DAY)
        data = self.report()
        names = [(entry["kind"], entry["name"]) for entry in data["largest"]]
        self.assertEqual(("observer", "sort sort"), names[0])
        self.assertEqual(("agent", "record-reviewer"), names[1])
        self.assertLessEqual(len(names), 5)
        self.assertEqual(sorted((entry["tokens"] for entry in data["largest"]), reverse=True),
                         [entry["tokens"] for entry in data["largest"]])

    def test_subagents_by_agent_for_7_and_30_days(self):
        usage.note_agent(ts.REPO, "recortex:ask", "m", {"input_tokens": 10, "output_tokens": 5, "cache_read": 100}, now=NOW - DAY)
        usage.note_agent(ts.REPO, "recortex:ask", "m", {"input_tokens": 1, "output_tokens": 1}, now=NOW - 20 * DAY)
        data = self.report()
        self.assertEqual([{"agent": "ask", "runs": 1, "input": 110, "output": 5}], data["agents"]["7"])
        self.assertEqual([{"agent": "ask", "runs": 2, "input": 111, "output": 6}], data["agents"]["30"])

    def test_the_injected_estimate_is_characters_over_four(self):
        usage.note_injected(ts.REPO, "session-start", 4001, now=NOW)
        usage.note_injected(ts.REPO, "prompt-submit", 400, now=NOW - 2 * DAY)
        usage.note_injected(ts.REPO, "prompt-submit", 400, now=NOW - 20 * DAY)
        data = self.report()
        self.assertEqual([{"event": "session-start", "characters": 4001, "count": 1, "tokens": 1000},
                          {"event": "prompt-submit", "characters": 400, "count": 1, "tokens": 100}], data["estimated"])

    def test_today_names_each_tier_with_its_cap_and_budget(self):
        data = self.report()
        sort = data["tiers"][0]
        self.assertEqual(("sort", 12, 104000), (sort["tier"], sort["calls"], sort["tokens"]))
        self.assertEqual(["sort", "middle", "strong"], [entry["tier"] for entry in data["tiers"]])
        self.assertEqual((80, 800000), (sort["cap"], sort["budget"]))
        self.assertEqual((70, 700000), (data["tiers"][1]["cap"], data["tiers"][1]["budget"]))
        self.assertEqual((8, 320000), (data["tiers"][2]["cap"], data["tiers"][2]["budget"]))

    def test_hostile_rows_are_dropped_and_hostile_numbers_clamped(self):
        rows = [store_row("2026-11-17", "sort", "sort", 1, 10, 10, "<script>alert(1)</script>"),
                store_row("2026-11-17", "weird", "sort", 1, 10, 10),
                store_row("2026-11-17", "sort", "<b>x</b>", 1, 10, 10),
                store_row("not a day", "sort", "sort", 1, 10, 10),
                store_row("2026-11-17", "sort", "sort", "7", -3, 10 ** 15),
                "not a row", None]
        data = self.report(rows=rows)
        seven = data["periods"]["7"]
        self.assertEqual([{"tier": "sort", "job": "sort", "calls": 1, "input": 10, "output": 10 + 10 ** 9}], seven)
        self.assertEqual("claude-haiku-5-5", data["tiers"][0]["model"])   # the hostile name was not taken
        text = json.dumps(data)
        for planted in ("<script>", "<b>", "weird", "not a day"):
            self.assertNotIn(planted, text)

    def test_with_no_data_the_report_says_so(self):
        data = usage.report(ts.REPO, ts.EXAMPLE_CONFIG, now=NOW, rows=[], today={})
        self.assertTrue(data["empty"])
        self.assertEqual("off", data["observer"])
        self.assertEqual({"7": [], "30": []}, data["periods"])
        self.assertEqual("2026-11-17 20:00 UTC", data["built"])

    def test_the_report_holds_no_money(self):
        text = json.dumps(self.report()).lower() + "\n".join(usage.lines(self.report())).lower()
        for word in ("$", "dollar", "usd", "price", "cost"):
            if word == "price":
                continue   # the first line says that no price applies
            self.assertNotIn(word, text)

    def test_the_terminal_lines_tell_the_same_numbers(self):
        out = "\n".join(usage.lines(self.report()))
        self.assertIn("Tokens and calls, not money", out)
        self.assertIn("18 calls, 120000 input tokens, 36000 output tokens", out)
        self.assertIn("Not measured:", out)

    def test_show_runs_from_the_terminal_with_gh_off(self):
        self.ready()
        shown = self.tool("usage.py", "show", gh="false")
        self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
        self.assertIn("Tokens and calls, not money", shown.stdout)
        self.assertIn("The observer is off here", shown.stdout)
        as_json = self.tool("usage.py", "show", "--json", "--days", "30", gh="false")
        self.assertEqual(0, as_json.returncode, as_json.stdout + as_json.stderr)
        self.assertEqual({"agents", "built", "days", "empty", "estimated", "largest", "observer", "periods", "tiers"},
                         set(json.loads(as_json.stdout)))


class SubagentBox(UsageBox):
    """A native transcript of a subagent in this project's own folder under the Claude configuration folder."""

    def setUp(self):
        super().setUp()
        self.folder = observer_sources.project_directory(self.project)
        os.makedirs(os.path.join(self.folder, SESSION, "subagents"), mode=0o700)
        for path in (os.path.join(self.native, "projects"), self.folder, os.path.join(self.folder, SESSION),
                     os.path.join(self.folder, SESSION, "subagents")):
            os.chmod(path, 0o700)
        self.path = os.path.join(self.folder, SESSION, "subagents", "agent-%s.jsonl" % AGENT_ID)

    def install(self, lines=None, mode=0o600):
        with open(TRANSCRIPT, encoding="utf-8") as handle:
            text = handle.read() if lines is None else "".join(line for line in handle.readlines()[:lines])
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(self.path, mode)

    def payload(self, **changes):
        data = {"session_id": SESSION, "transcript_path": "unused", "cwd": self.project, "hook_event_name": "SubagentStop",
                "agent_id": AGENT_ID, "agent_type": "recortex:record-auditor", "agent_transcript_path": self.path,
                "stop_hook_active": False, "last_assistant_message": "ready"}
        data.update(changes)
        return data

    def stop(self, **changes):
        hooks.subagent_stop(self.payload(**changes))


class SubagentStopTests(SubagentBox):
    def test_a_stop_of_a_listed_agent_keeps_one_row_summed_once_per_message(self):
        self.install()
        self.stop()
        rows = self.rows("agents")
        self.assertEqual(1, len(rows))
        self.assertEqual(("record-auditor", "claude-haiku-5-5", 5, 201, 35003, 1200), rows[0][1:])

    def test_every_agent_of_the_list_is_kept_and_nothing_else(self):
        self.install()
        for name in ("record-auditor", "record-drafter", "record-reviewer", "record-planner", "record-planner-session", "ask"):
            self.stop(agent_type="recortex:" + name)
        self.assertEqual(6, len(self.rows("agents")))
        for name in ("ask", "other:ask", "recortex:", "recortex:record-auditor2", "Recortex:ask", "recortex:ask\n", None, 5):
            self.stop(agent_type=name)
        self.assertEqual(6, len(self.rows("agents")))

    def test_an_agent_off_the_list_is_never_read(self):
        self.install()
        with mock.patch.object(usage, "measure", side_effect=AssertionError("read")) as measured:
            self.stop(agent_type="other:ask")
            self.stop(agent_type="ask")
        measured.assert_not_called()
        self.assertEqual([], self.rows("agents"))

    def test_a_path_outside_this_projects_native_folder_is_refused(self):
        self.install()
        elsewhere = tempfile.mkdtemp(prefix="usage-else-", dir="/tmp")
        os.chmod(elsewhere, 0o700)
        self.addCleanup(shutil.rmtree, elsewhere, True)
        other = os.path.join(elsewhere, "agent-x.jsonl")
        shutil.copy(self.path, other)
        os.chmod(other, 0o600)
        sibling = os.path.join(self.native, "projects", "-some-other-project", SESSION, "subagents")
        os.makedirs(sibling, mode=0o700)
        copied = os.path.join(sibling, "agent-%s.jsonl" % AGENT_ID)
        shutil.copy(self.path, copied)
        os.chmod(copied, 0o600)
        with mock.patch.object(usage, "measure", side_effect=AssertionError("read")) as measured:
            for path in (other, copied, self.path + "/../agent-x.jsonl", "relative/agent-x.jsonl", None, 7,
                         os.path.join(self.folder, SESSION + ".jsonl.extra")):
                self.stop(agent_transcript_path=path)
            self.stop(session_id="another-session")   # the path names this session, the hook another one
            self.stop(session_id="bad id/..")
        measured.assert_not_called()
        self.assertEqual([], self.rows("agents"))

    def test_a_symlink_is_refused(self):
        self.install()
        real = os.path.join(self.folder, SESSION, "subagents", "agent-real.jsonl")
        os.rename(self.path, real)
        os.symlink(real, self.path)
        with mock.patch.object(usage, "measure", side_effect=AssertionError("read")) as measured:
            self.stop()
        measured.assert_not_called()
        self.assertEqual([], self.rows("agents"))

    def test_a_file_another_user_owns_is_refused(self):
        self.install()
        original = os.lstat
        foreign = max(os.getuid(), 1) + 10000

        def inspect(candidate, *args, **kwargs):
            info = original(candidate, *args, **kwargs)
            if os.fspath(candidate) == self.path:
                values = {key: getattr(info, key) for key in ("st_mode", "st_dev", "st_ino", "st_size")}
                from types import SimpleNamespace
                return SimpleNamespace(st_uid=foreign, st_gid=info.st_gid, **values)
            return info

        with mock.patch.object(observer_sources.os, "lstat", side_effect=inspect):
            self.stop()
        self.assertEqual([], self.rows("agents"))

    def test_a_transcript_other_users_can_write_is_refused(self):
        self.install(mode=0o666)
        self.stop()
        self.assertEqual([], self.rows("agents"))

    def test_only_integers_and_a_model_name_leave_the_transcript(self):
        records = [json.loads(line) for line in open(TRANSCRIPT, encoding="utf-8")]
        for record in records:
            message = record.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                message["model"] = "<img src=x onerror=alert(1)>"
                message["content"] = [{"type": "text", "text": "CANARY-TEXT-OF-THE-SUBAGENT"}]
                message["usage"]["input_tokens"] = "12"
                message["usage"]["note"] = "CANARY-USAGE-TEXT"
        with open(self.path, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        os.chmod(self.path, 0o600)
        self.stop()
        rows = self.rows("agents")
        self.assertEqual(1, len(rows))
        self.assertEqual(("record-auditor", "", 0, 201, 35003, 1200), rows[0][1:])
        with open(os.path.join(self.state_folder(), "usage.db"), "rb") as handle:
            raw = handle.read()
        self.assertNotIn(b"CANARY", raw)
        self.assertNotIn(b"onerror", raw)

    def test_a_record_that_is_not_json_or_not_an_assistant_is_skipped(self):
        self.install()
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write("not json at all\n[1, 2]\n{\"type\": \"assistant\", \"message\": 5}\n")
        self.stop()
        self.assertEqual(("record-auditor", "claude-haiku-5-5", 5, 201, 35003, 1200), self.rows("agents")[0][1:])

    def test_the_hook_waits_for_a_transcript_that_fills_up_later(self):
        self.install(lines=6)    # the records before the first assistant record, as at the moment the hook fires
        timer = threading.Timer(0.35, self.install)
        timer.start()
        self.addCleanup(timer.join)
        started = time.monotonic()
        self.stop()
        waited = time.monotonic() - started
        self.assertGreaterEqual(waited, 0.3)
        self.assertLess(waited, 1.5)
        self.assertEqual(1, len(self.rows("agents")))
        self.assertEqual(5, self.rows("agents")[0][3])

    def test_a_transcript_that_never_fills_up_writes_no_row_and_returns_in_time(self):
        self.install(lines=6)
        started = time.monotonic()
        self.stop()
        waited = time.monotonic() - started
        self.assertGreaterEqual(waited, 1.4)
        self.assertLess(waited, 2.5)
        self.assertEqual([], self.rows("agents"))

    def test_an_assistant_record_with_no_stop_reason_is_not_yet_complete(self):
        lines = open(TRANSCRIPT, encoding="utf-8").read().splitlines()
        partial = []
        for line in lines:
            record = json.loads(line)
            if record.get("type") == "assistant":
                record["message"]["stop_reason"] = None
            partial.append(json.dumps(record))
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(partial) + "\n")
        os.chmod(self.path, 0o600)
        with mock.patch.object(usage, "WAIT_FOR_TRANSCRIPT", 0.3):
            self.stop()
        self.assertEqual([], self.rows("agents"))

    def test_the_tail_is_read_from_the_end_and_the_cut_line_is_dropped(self):
        self.install()
        with open(self.path, "r", encoding="utf-8") as handle:
            text = handle.read()
        pad = ("x" * 1000 + "\n") * 40   # lines of no kind, before the real records
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(pad + text)
        os.chmod(self.path, 0o600)
        lines = usage.read_tail(self.path, limit=len(text.encode()) + 500)
        self.assertEqual(0, sum(1 for line in lines if line.startswith(b"x")))   # 500 bytes of the cut pad line: dropped
        total, model, complete = usage.sum_transcript(lines)
        self.assertTrue(complete)
        self.assertEqual(("claude-haiku-5-5", 5), (model, total["input_tokens"]))

    def test_nothing_is_done_when_the_repository_is_not_trusted(self):
        self.install()
        with open(self.trusted, "w") as handle:
            handle.write("")
        with mock.patch.object(usage, "measure", side_effect=AssertionError("read")) as measured:
            self.stop()
        measured.assert_not_called()
        self.assertEqual([], self.rows("agents"))

    def test_nothing_is_done_when_the_project_is_not_set_up(self):
        self.install()
        os.unlink(os.path.join(self.project, recortex.CONFIG_NAME))
        with mock.patch.object(usage, "measure", side_effect=AssertionError("read")) as measured:
            self.stop()
        measured.assert_not_called()

    @unittest.skipUnless(os.lstat("/tmp").st_uid == 0, "the native folder needs a /tmp that root owns, as the hook checks it")
    def test_a_locked_usage_file_makes_the_hook_give_up_in_time_and_print_nothing(self):
        self.install()
        usage.note_injected(ts.REPO, "session-start", 10)
        holder = sqlite3.connect(os.path.join(self.state_folder(), "usage.db"), isolation_level=None)
        holder.execute("BEGIN EXCLUSIVE")
        self.addCleanup(holder.close)
        started = time.monotonic()
        done = self.run_hook()
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual((0, ""), (done.returncode, done.stdout))
        holder.execute("ROLLBACK")
        self.assertEqual([], self.rows("agents"))

    @unittest.skipUnless(os.lstat("/tmp").st_uid == 0, "the native folder needs a /tmp that root owns, as the hook checks it")
    def test_the_hook_script_prints_nothing_and_keeps_the_row(self):
        self.install()
        done = self.run_hook()
        self.assertEqual((0, "", ""), (done.returncode, done.stdout, done.stderr))
        self.assertEqual(1, len(self.rows("agents")))

    def run_hook(self, **changes):
        full_env = ts.environment_for(os.environ, {"RECORTEX_GH": "false", "PYTHONDONTWRITEBYTECODE": "1"})
        full_env.update(XDG_CACHE_HOME=os.path.join(self.dir, "xdg-cache"), XDG_STATE_HOME=os.path.join(self.dir, "xdg-state"),
                        XDG_CONFIG_HOME=os.path.join(self.dir, "xdg-config"))
        full_env = ts.overlay(full_env, self.env)
        return subprocess.run(["bash", os.path.join(ts.SCRIPTS, "run_hook.sh"), "subagent-stop"],
                              input=json.dumps(self.payload(**changes)), capture_output=True, text=True, env=full_env,
                              cwd=self.project)


class RegistrationTests(unittest.TestCase):
    """hooks/hooks.json and the settings snippet register SubagentStop with an anchored matcher and the runner's time."""

    def entries(self, path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)["hooks"]["SubagentStop"]

    def test_the_matcher_is_anchored_and_names_the_plugins_subagents(self):
        for path in (os.path.join(ROOT, "hooks", "hooks.json"),
                     os.path.join(ts.TEMPLATES, "settings-hook-snippet.json")):
            entries = self.entries(path)
            self.assertEqual(1, len(entries))
            matcher = entries[0]["matcher"]
            self.assertTrue(matcher.startswith("^") and matcher.endswith("$"), matcher)
            for name in ("record-auditor", "record-drafter", "record-reviewer", "record-planner", "record-planner-session", "ask"):
                self.assertTrue(re.search(matcher, "recortex:" + name), name)
            for name in ("ask", "recortex:ask2", "other:record-auditor", "recortex:", "xrecortex:ask", "recortex:record-planner-x"):
                self.assertFalse(re.search(matcher, name), name)
            self.assertEqual(5, entries[0]["hooks"][0]["timeout"])
            self.assertIn("run_hook.sh", entries[0]["hooks"][0]["command"])
            self.assertTrue(entries[0]["hooks"][0]["command"].endswith("subagent-stop"))

    def test_the_hook_list_in_the_code_and_the_matcher_say_the_same(self):
        matcher = self.entries(os.path.join(ROOT, "hooks", "hooks.json"))[0]["matcher"]
        listed = set(re.search(r"\^recortex:\((.*)\)\$", matcher).group(1).split("|"))
        self.assertEqual(listed, set(usage.AGENTS))
        for name in listed:
            self.assertTrue(hooks.STOP_AGENTS.fullmatch("recortex:" + name))


class InjectedCountTests(UsageBox):
    def test_a_printed_session_start_is_counted_in_characters(self):
        self.ready()
        done = self.tool("hooks.py", "session-start", stdin="{}")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        rows = self.rows("injected")
        self.assertEqual(1, len(rows))
        self.assertEqual("session-start", rows[0][1])
        self.assertEqual(len(done.stdout.rstrip("\n")), rows[0][2])

    def test_a_subagent_start_counts_the_context_inside_its_json(self):
        self.ready()
        done = self.tool("hooks.py", "subagent-start", stdin="{}")
        context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
        rows = self.rows("injected")
        self.assertEqual([("subagent-start", len(context))], [(row[1], row[2]) for row in rows])

    def test_the_payload_is_the_same_with_the_count(self):
        self.ready()
        first = self.tool("hooks.py", "pre-compact", stdin="{}")
        usage_db = os.path.join(self.state_folder(), "usage.db")
        os.unlink(usage_db)
        os.chmod(self.state_folder(), 0o500)
        try:
            second = self.tool("hooks.py", "pre-compact", stdin="{}")
        finally:
            os.chmod(self.state_folder(), 0o700)
        self.assertEqual(first.stdout, second.stdout)   # a count that cannot be written changes nothing the user sees
        self.assertEqual(0, second.returncode)

    def test_the_doctor_says_whether_the_file_can_be_written(self):
        self.ready()
        done = self.tool("hooks.py", "--doctor")
        self.assertIn("usage: usage.db can be written", done.stdout)
        self.tool("hooks.py", "session-start", stdin="{}")
        path = os.path.join(self.state_folder(), "usage.db")
        os.chmod(path, 0o644)
        done = self.tool("hooks.py", "--doctor")
        self.assertIn("usage: usage.db cannot be written (refused (permissions))", done.stdout)
        self.assertNotIn(self.state_folder(), [line for line in done.stdout.splitlines() if line.startswith("usage:")][0])


if __name__ == "__main__":
    unittest.main()
