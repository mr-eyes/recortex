"""Tests of the approval path and the dry run of the paper's writing commands (1.3.0): approval on GitHub by a pull request, each
row of its table, and --dry-run on the commands of write.py, versions.py and own_paper.py. Nothing here reaches GitHub; the
scripts run against tests/fake_gh.py and its git endpoints (tests/fake_git.py)."""

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


class WriteTrustTests(VersionBox):
    """Approval on GitHub (the table of references/writing.md): each row, removed one at a time, writes nothing."""

    def proposed(self):
        self.ready()
        self.mode("review")
        folder = self.start(5)
        self.draft(folder)
        done = self.cmd("write.py", "propose", "--dir", folder)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.pull = next(int(k) for k, v in self.repo()["issues"].items() if v.get("pull_request") and v.get("head"))
        self.proposal = next(int(k) for k, v in self.repo()["comments"].items() if "type=proposal" in v["body"].split("\n")[0])
        return folder

    def fake(self, *args, login=None, stdin="{}"):
        env = dict(os.environ, FAKE_GH_STATE=self.state, **({"FAKE_GH_LOGIN": login} if login else {}))
        import subprocess
        return subprocess.run(ts.FAKE.split() + list(args), input=stdin, capture_output=True, text=True, env=env)

    def merge(self, login=None):
        done = self.fake("api", "-X", "PUT", "repos/octo-org/sample-project/pulls/%d/merge" % self.pull, login=login)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def approvals(self, *extra, env=None):
        return self.cmd("write.py", "approvals", *extra, env=env)

    def nothing_written(self, before_memories=0):
        self.assertEqual(before_memories, len(self.text_memories()))
        self.assertNotIn("The pooled fit", self.body(5))

    def test_the_author_of_a_proposal_is_read_live_and_not_from_the_index(self):
        import proposals
        from unittest import mock
        self.proposed()
        data = self.repo()
        data["comments"][str(self.proposal)]["user"] = "hubot"   # GitHub says hubot; the index still says octocat
        self.save(data)
        with mock.patch.dict(os.environ, {"RECORTEX_GH": ts.FAKE, "FAKE_GH_STATE": self.state}):
            found = proposals.evaluate(self.config, "octo-org/sample-project", 1, None, {"octocat"}, None, None,
                                       self.proposal, 5, "octocat")
        self.assertEqual(("not-ours", self.proposal), found)

    def test_a_proposal_is_a_pull_request_and_a_comment_that_name_the_same_hashes(self):
        self.proposed()
        data = self.repo()
        pull = data["issues"][str(self.pull)]
        self.assertRegex(pull["head"]["ref"], r"^recortex-proposal-5-[0-9a-f]{8}$")
        self.assertEqual(BRANCH, pull["base"]["ref"])
        comment = data["comments"][str(self.proposal)]["body"]
        fields = dict(record_format.fields(comment))
        self.assertEqual("#%d" % self.pull, fields["Pull request"])
        git = data["git"]
        proposed = git["trees"][git["commits"][git["refs"]["heads/" + pull["head"]["ref"]]]["tree"]]["topics/5.md"]
        self.assertEqual("sha256 `%s`" % record_format.body_sha(proposed), fields["Text"])
        self.assertEqual("sha256 `%s`" % record_format.body_sha(self.body(5)), fields["Base"])
        self.assertNotIn("The pooled fit", self.body(5))   # nothing is written to the topic by a proposal
        self.assertEqual("heads/" + BRANCH, [k for k in git["refs"] if k == "heads/" + BRANCH][0])

    def test_a_merged_proposal_is_written_with_the_form_of_a_merge_and_a_version(self):
        self.proposed()
        waiting = self.approvals().stdout
        self.assertIn("is open; the change waits for the author to merge or close it", waiting)
        self.nothing_written()
        self.merge()
        listed = self.approvals()
        self.assertIn("is approved on GitHub (pull request #%d merged by octocat)" % self.pull, listed.stdout)
        self.nothing_written()
        done = self.approvals("--apply")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("The pooled fit and the dry-spell comparison agree", self.body(5))
        (cid, memory), = self.text_memories()
        fields = dict(record_format.fields(memory))
        self.assertEqual("By octocat on GitHub, by merging pull request %d." % self.pull, fields["Approved"])
        self.ready()
        self.assertEqual("approved by you on GitHub", json.loads(self.cmd("versions.py", "log", "--json").stdout)[0]["how"])
        self.assertRegex(fields["Version"], r"^`[0-9a-f]{40}`$")
        self.assertEqual(self.body(5).rstrip("\n") + "\n", self.files_at()["topics/5.md"])
        again = self.approvals("--apply")
        self.assertEqual(1, len(self.text_memories()))   # one body edit and one memory, however often it runs
        self.assertEqual(1, len([e for e in self.repo()["log"] if e[:2] == ["issue", "edit"]]))

    def test_a_proposal_comment_of_another_account_is_not_ours(self):
        self.proposed()
        self.merge()
        data = self.repo()
        data["comments"][str(self.proposal)]["user"] = "hubot"
        self.save(data)
        out = self.approvals("--apply").stdout
        self.assertIn("approvals: no proposal waits", out)   # a proposal-typed comment of another account is no proposal
        self.nothing_written()

    def test_only_the_newest_proposal_of_an_allowed_account_counts(self):
        self.proposed()
        self.merge()
        newer = manuscript_record.proposal("2026-12-09T10:00-05:00", "Results", "Another change.", "Another reason.", self.pull,
                                           "0" * 64, "1" * 64)
        self.add_comment(9600, 5, newer, tp.later(self))   # a newer proposal by an allowed account: the older one is void
        out = self.approvals("--apply").stdout
        self.assertIn("merged with other text than proposed", out)   # the newest one is judged, and its text is not the merged one
        self.nothing_written()

    def test_a_newer_proposal_of_an_account_that_is_not_allowed_never_blocks_the_one_that_stands(self):
        """Anyone who may comment can post a proposal-typed comment; it must not stop the author's approval."""
        self.proposed()
        self.merge()
        later = manuscript_record.proposal("2026-12-09T10:00-05:00", "Results", "Another change.", "Another reason.", self.pull,
                                           "0" * 64, "1" * 64)
        self.add_comment(9600, 5, later, tp.later(self), "hubot")
        done = self.approvals("--apply")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertNotIn("is not by an allowed account or is not the newest proposal on #5", done.stdout)
        self.assertIn("The pooled fit", self.body(5))

    def test_a_file_edited_on_the_branch_makes_proposals_for_that_topic_wait(self):
        self.proposed()
        self.merge()
        data = self.repo()
        git = data["git"]
        head = git["refs"]["heads/" + BRANCH]
        files = dict(git["trees"][git["commits"][head]["tree"]])
        files["topics/5.md"] = files["topics/5.md"] + "\nA line somebody put in the file on the branch.\n"
        tree = "b" * 40
        git["trees"][tree] = files
        git["commits"]["d" * 40] = {"tree": tree, "parents": [head], "message": "by hand"}
        git["refs"]["heads/" + BRANCH] = "d" * 40
        self.save(data)
        out = self.approvals("--apply").stdout
        self.assertIn("the proposal waits until the author answers", out)
        self.nothing_written()
        # a new proposal is refused the same way
        folder = self.start(5, "again")
        self.draft(folder)
        refused = self.cmd("write.py", "propose", "--dir", folder)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("was changed on the branch and the text was not", refused.stderr + refused.stdout)

    def test_an_edited_proposal_is_void(self):
        self.proposed()
        self.merge()
        data = self.repo()
        comment = data["comments"][str(self.proposal)]
        comment["edits"] = [{"at": "2026-12-09T10:00:00Z", "by": "octocat"}, {"at": comment["created_at"], "by": "octocat"}]
        self.save(data)
        out = self.approvals("--apply").stdout
        self.assertIn("was edited, so it is void", out)
        self.nothing_written()

    def test_a_merge_by_an_account_that_is_not_allowed_writes_nothing(self):
        self.proposed()
        self.merge(login="hubot")
        out = self.approvals("--apply").stdout
        self.assertIn("was merged by an account that is not allowed", out)
        self.assertIn("1 proposal refused", out)
        self.nothing_written()

    def test_a_pull_request_merged_somewhere_else_or_from_another_branch_writes_nothing(self):
        for key, value in (("base", "main"), ("head", "feature/x")):
            self.proposed()
            self.merge()
            data = self.repo()
            data["issues"][str(self.pull)][key]["ref"] = value
            self.save(data)
            out = self.approvals("--apply").stdout
            self.assertIn("was merged by an account that is not allowed", out, key)
            self.nothing_written()
            self.setUp()

    def test_a_merged_file_with_another_hash_is_never_written_on_its_own(self):
        self.proposed()
        self.merge()
        data = self.repo()
        git = data["git"]
        tree = git["trees"][git["commits"][git["refs"]["heads/" + BRANCH]]["tree"]]
        tree["topics/5.md"] = tree["topics/5.md"] + "\nThe author changed it before merging.\n"
        self.save(data)
        out = self.approvals("--apply").stdout
        self.assertIn("was merged with other text than proposed", out)
        self.nothing_written()

    def test_a_body_that_moved_is_not_applied_and_the_pull_request_is_closed_with_a_fixed_comment(self):
        for merged_first in (False, True):
            self.setUp() if merged_first else None
            self.proposed()
            if merged_first:
                self.merge()
            data = self.repo()
            data["issues"]["5"]["body"] = data["issues"]["5"]["body"] + "\nAn edit the proposal knows nothing about.\n"
            data["issues"]["5"]["updated_at"] = "2026-12-09T10:00:00Z"
            self.save(data)
            out = self.approvals("--apply").stdout
            self.assertIn("changed after the proposal was made; the proposal is not applied", out)
            self.assertEqual(0, len(self.text_memories()))
            self.assertNotIn("The pooled fit", self.body(5))
            said = " ".join(c["body"] for c in self.repo()["comments"].values() if c["issue"] == self.pull)
            if merged_first:
                self.assertEqual("", said)   # a merged pull request is left as it is
            else:
                self.assertIn("recortex: the text of this topic changed after this change was proposed", said)
                self.assertEqual("closed", self.repo()["issues"][str(self.pull)]["state"])

    def quiet(self, out, pull):
        """What a proposal that was applied leaves in the output of approvals: nothing about it."""
        self.assertNotIn("changed after the proposal was made", out)
        self.assertNotIn("would close", out)
        self.assertNotIn("pull request #%d" % pull, out)
        self.assertIn("approvals: no proposal waits", out)

    def test_a_merged_proposal_that_was_applied_is_done_and_approvals_says_nothing_and_closes_nothing(self):
        self.proposed()
        self.merge()
        self.assertEqual(0, self.approvals("--apply").returncode)
        self.assertIn("The pooled fit", self.body(5))
        self.ready()
        for extra in ((), ("--apply", "--dry-run"), ("--apply",)):
            done = self.approvals(*extra)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            self.quiet(done.stdout, self.pull)
        self.assertEqual(1, len(self.text_memories()))
        said = " ".join(c["body"] for c in self.repo()["comments"].values() if c["issue"] == self.pull)
        self.assertEqual("", said)                                                   # nothing was posted on the pull request
        self.assertEqual([], [c for c in self.repo()["comments"].values() if c["issue"] == 5 and "type=checkpoint" in c["body"].split("\n")[0]])

    def test_a_proposal_whose_text_is_already_the_body_is_done_with_no_text_change_at_all(self):
        self.proposed()
        self.merge()
        data = self.repo()
        git = data["git"]
        name = data["issues"][str(self.pull)]["head"]["ref"]
        data["issues"]["5"]["body"] = git["trees"][git["commits"][git["refs"]["heads/" + name]]["tree"]]["topics/5.md"]
        data["issues"]["5"]["updated_at"] = "2026-12-09T10:00:00Z"
        self.save(data)
        self.ready()
        for extra in ((), ("--apply", "--dry-run"), ("--apply",)):
            self.quiet(self.approvals(*extra).stdout, self.pull)
        self.assertEqual([], self.text_memories())    # nothing was written, and nothing was needed

    def test_a_proposal_that_a_text_change_records_as_applied_stays_done_after_the_body_moves_on(self):
        self.proposed()
        self.merge()
        self.assertEqual(0, self.approvals("--apply").returncode)
        data = self.repo()
        data["issues"]["5"]["body"] = data["issues"]["5"]["body"] + "\nAn edit made by hand after the write.\n"
        data["issues"]["5"]["updated_at"] = "2026-12-09T10:00:00Z"
        self.save(data)
        self.ready()
        for extra in ((), ("--apply", "--dry-run"), ("--apply",)):
            self.quiet(self.approvals(*extra).stdout, self.pull)
        self.assertEqual(1, len(self.text_memories()))

    def test_a_text_change_that_does_not_go_from_the_base_to_the_text_does_not_make_it_done(self):
        """Only a text change whose body went from the proposal's Base to its Text records the proposal as written."""
        self.proposed()
        self.merge()
        fields = dict(record_format.fields(self.repo()["comments"][str(self.proposal)]["body"]))
        text = re.search(r"[0-9a-f]{64}", fields["Text"]).group(0)
        other = manuscript_record.text_memory("2026-12-01T10:00-05:00", "Results", "another text change", "It changed.", "It was due.",
                                              "Written without asking, under the autonomous mode.", before="c" * 64, after=text)
        self.add_comment(9610, 5, other, "2026-12-01T15:00:00Z")
        data = self.repo()
        data["issues"]["5"]["body"] = data["issues"]["5"]["body"] + "\nAn edit the proposal knows nothing about.\n"
        data["issues"]["5"]["updated_at"] = "2026-12-09T10:00:00Z"
        self.save(data)
        self.ready()
        out = self.approvals("--apply").stdout
        self.assertIn("changed after the proposal was made; the proposal is not applied", out)   # the real moved case stays

    def test_a_text_that_no_longer_passes_the_checks_writes_nothing(self):
        self.proposed()
        self.merge()
        fix = manuscript_record.correction("2026-12-09T09:00-05:00", "the dry-spell count", "we corrected the dry-spell count",
                                           "Result 9140 on the dry-spell thread.", "9 of 11", "8 of 11", "One spell was counted twice.",
                                           "None.", links="replaces=9140")
        self.add_comment(9500, 5, fix, tp.later(self))
        out = self.approvals("--apply").stdout
        self.assertIn("what this text rests on changed while it waited", out)
        self.nothing_written()

    def test_a_mode_of_off_on_this_machine_writes_nothing(self):
        self.proposed()
        self.merge()
        self.mode("off")
        done = self.approvals("--apply")
        self.assertNotEqual(0, done.returncode)
        self.assertIn("writing is off on this machine", done.stderr)
        self.nothing_written()

    def test_a_proposal_closed_without_merging_is_dropped_with_a_checkpoint(self):
        self.proposed()
        closed = self.fake("api", "-X", "PATCH", "repos/octo-org/sample-project/pulls/%d" % self.pull, stdin=json.dumps({"state": "closed"}))
        self.assertEqual(0, closed.returncode, closed.stdout + closed.stderr)
        out = self.approvals("--apply").stdout
        self.assertIn("was closed without merging, so the proposal is dropped", out)
        checkpoints = [c for c in self.repo()["comments"].values() if c["issue"] == 5 and "type=checkpoint" in c["body"].split("\n")[0]]
        self.assertEqual(1, len(checkpoints))
        self.assertIn("a proposed change of the text was closed on GitHub without being merged", checkpoints[0]["body"])
        self.nothing_written()

    def test_a_rejection_is_recorded_once_and_the_proposal_stops_waiting(self):
        """Codex review of 1.3.0: every later run posted another checkpoint, and the brief went on counting the proposal."""
        self.proposed()
        self.ready()
        self.assertEqual(1, self.plan_json()["counts"]["proposals"])
        closed = self.fake("api", "-X", "PATCH", "repos/octo-org/sample-project/pulls/%d" % self.pull, stdin=json.dumps({"state": "closed"}))
        self.assertEqual(0, closed.returncode, closed.stdout + closed.stderr)
        self.approvals("--apply")
        for _ in range(2):
            self.approvals("--apply")
        checkpoints = [c for c in self.repo()["comments"].values() if c["issue"] == 5 and "type=checkpoint" in c["body"].split("\n")[0]]
        self.assertEqual(1, len(checkpoints))
        self.ready()
        self.assertEqual(0, self.plan_json()["counts"]["proposals"])
        self.assertNotIn("proposal", self.tool("index.py", "query", "brief").stdout.split("===== BEGIN")[0].lower().replace(
            "plan of the paper", ""))

    def test_a_checkpoint_of_an_account_that_is_not_allowed_or_that_was_edited_does_not_close_a_proposal(self):
        self.proposed()
        closed = self.fake("api", "-X", "PATCH", "repos/octo-org/sample-project/pulls/%d" % self.pull, stdin=json.dumps({"state": "closed"}))
        self.approvals("--apply")
        self.ready()
        self.assertEqual(0, self.plan_json()["counts"]["proposals"])
        data = self.repo()
        for comment in data["comments"].values():
            if comment["issue"] == 5 and "type=checkpoint" in comment["body"].split("\n")[0]:
                comment["user"] = "hubot"
        self.save(data)
        self.assertEqual(0, self.tool("index.py", "sync", "--rebuild").returncode)
        self.assertEqual(1, self.plan_json()["counts"]["proposals"])   # a stranger's checkpoint settles nothing

    def test_a_proposal_made_on_another_machine_is_not_applied_here(self):
        self.proposed()
        self.merge()
        folder = os.path.join(self.data, "private", self.slug(), "write", "5")
        for name in os.listdir(folder):
            os.unlink(os.path.join(folder, name))
        out = self.approvals("--apply").stdout
        self.assertIn("was made on another machine; apply it there", out)
        self.nothing_written()

    def test_a_proposal_needs_review_mode_and_a_sign_in_that_may_write(self):
        self.ready()
        self.mode("autonomous")
        folder = self.start(5)
        self.draft(folder)
        done = self.cmd("write.py", "propose", "--dir", folder)
        self.assertNotEqual(0, done.returncode)
        self.assertIn("a proposal on GitHub is for review mode", done.stderr)
        self.mode("review")
        folder = self.start(5, "second")
        self.draft(folder)
        refused = self.cmd("write.py", "propose", "--dir", folder, env={"FAKE_GH_GIT_NO_PUSH": "403"})
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("approval on GitHub is not available here, so ask in the chat", refused.stderr)
        self.assertFalse([k for k, v in self.repo()["issues"].items() if v.get("pull_request") and v.get("head")])


class DryRunTests(VersionBox):
    """Every command of write.py, versions.py and own_paper.py that writes has --dry-run: the checks run, the commands it would
    make are printed, and nothing is written (the fake repository, the project file and the author's file stay as they were)."""

    def frozen(self):
        data = self.repo()
        data.pop("log", None)
        data.pop("reads", None)   # the fake counts its reads
        with open(os.path.join(self.project, recortex.CONFIG_NAME), "rb") as handle:
            return json.dumps(data, sort_keys=True), handle.read()

    def dry(self, *command, env=None, wants="dry run: "):
        before = self.frozen()
        done = self.cmd(*command, env=env)
        self.assertEqual(0, done.returncode, " ".join(command) + "\n" + done.stdout + done.stderr)
        self.assertIn("would ", done.stdout, command)
        self.assertIn(wants, done.stdout, command)
        self.assertEqual(before, self.frozen(), command)
        return done

    def test_write_apply_runs_every_check_and_writes_nothing_then_a_real_apply_still_works(self):
        self.ready()
        self.mode("autonomous")
        folder = self.start(5)
        self.draft(folder)
        files = sorted(os.listdir(folder))
        done = self.dry("write.py", "apply", "--dir", folder, "--dry-run")
        self.assertIn("would run: gh issue edit 5", done.stdout)
        self.assertEqual(files, sorted(os.listdir(folder)))   # no backup, no comment text
        self.assertNotIn("The pooled fit", self.body(5))
        self.draft(folder, text=self.base(folder), parts=(), results=())   # a draft that fails a check is still refused
        refused = self.cmd("write.py", "apply", "--dir", folder, "--dry-run")
        self.assertNotEqual(0, refused.returncode)
        self.draft(folder)
        self.assertEqual(0, self.cmd("write.py", "apply", "--dir", folder).returncode)
        self.assertIn("The pooled fit", self.body(5))

    def test_write_propose_and_approvals_apply_write_nothing(self):
        self.ready()
        self.mode("review")
        folder = self.start(5)
        self.draft(folder)
        self.dry("write.py", "propose", "--dir", folder, "--dry-run")
        self.assertEqual([], [k for k, v in self.repo()["issues"].items() if v.get("pull_request") and v.get("head")])
        self.assertEqual(0, self.cmd("write.py", "propose", "--dir", folder).returncode)
        pull = next(int(k) for k, v in self.repo()["issues"].items() if v.get("pull_request") and v.get("head"))
        import subprocess
        merged = subprocess.run(ts.FAKE.split() + ["api", "-X", "PUT", "repos/octo-org/sample-project/pulls/%d/merge" % pull],
                                input="{}", capture_output=True, text=True, env=dict(os.environ, FAKE_GH_STATE=self.state))
        self.assertEqual(0, merged.returncode, merged.stdout + merged.stderr)
        done = self.dry("write.py", "approvals", "--apply", "--dry-run")
        self.assertIn("would apply: #5", done.stdout)
        self.assertNotIn("The pooled fit", self.body(5))
        self.assertEqual(0, self.cmd("write.py", "approvals", "--apply").returncode)
        self.assertIn("The pooled fit", self.body(5))

    def hand_edit(self):
        self.written()
        self.edited_by_hand(5, "\n### Figure: dryspell", "\nA second look, by hand.\n\n### Figure: dryspell")

    def test_versions_sync_name_meta_and_commit_write_nothing(self):
        self.hand_edit()
        heads = self.head()
        self.dry("versions.py", "sync", "--dry-run")
        self.assertEqual(heads, self.head())
        self.assertEqual(1, len(self.text_memories()))
        live = os.path.join(self.dir, "live.md")
        with open(live, "w") as handle:
            handle.write(self.body(5))
        self.dry("versions.py", "commit", "--issue", "5", "--body-file", live, "--dry-run")
        other = os.path.join(self.dir, "other.md")
        with open(other, "w") as handle:
            handle.write(self.body(5) + "\nNot the live body.\n")
        refused = self.cmd("versions.py", "commit", "--issue", "5", "--body-file", other, "--dry-run")
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("is not the live body of #5", refused.stderr)
        self.assertEqual(0, self.cmd("versions.py", "sync").returncode)   # in step again, so that a name may be given
        self.ready()
        self.dry("versions.py", "name", "submitted", "--words-file", self.words("name it"), "--dry-run")
        self.assertEqual({}, self.tags())
        self.dry("versions.py", "meta", "--authors", "A. Author", "--dry-run")

    def test_versions_put_back_writes_nothing(self):
        self.written()
        files = dict(self.files_at())
        head = self.head()
        files["topics/5.md"] = files["topics/5.md"] + "\nTyped into the file.\n"
        data = self.repo()
        data["git"]["trees"]["d" * 40] = files
        data["git"]["commits"]["e" * 40] = {"tree": "d" * 40, "parents": [head], "message": "by hand"}
        data["git"]["refs"]["heads/" + BRANCH] = "e" * 40
        self.save(data)
        self.dry("versions.py", "put-back", "--issue", "5", "--dry-run")
        self.assertEqual("e" * 40, self.head())

    def test_own_paper_where_and_apply_write_nothing(self):
        self.ready()
        path = os.path.join(self.project, "paper", "paper.tex")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as handle:
            handle.write(tw.PAPER_TEX)
        self.dry("own_paper.py", "where", "--set", path, "--dry-run")
        self.dry("own_paper.py", "where", "--set", "no", "--dry-run")
        self.assertEqual(0, self.cmd("own_paper.py", "where", "--set", path, "--yes").returncode)
        folder = os.path.join(self.dir, "since")
        self.assertEqual(0, self.cmd("own_paper.py", "since", "--issue", "5", "--section", "Results", "--out", folder).returncode)
        new = os.path.join(self.dir, "new.tex")
        with open(new, "w") as handle:
            handle.write(tw.PAPER_TEX.replace("9 of 11 spells.", "9 of 11 spells, and 12 of 14 with a third season."))
        self.assertEqual(0, self.cmd("own_paper.py", "propose", "--file", new, "--out", folder).returncode)
        words = self.words("Apply")
        before = open(path, "rb").read()
        self.dry("own_paper.py", "apply", "--dir", folder, "--words-file", words, "--parts", "5:Results", "--dry-run")
        self.dry("own_paper.py", "apply", "--dir", folder, "--words-file", words, "--parts", "5:Results", "--pasted", "--dry-run")
        self.assertEqual(before, open(path, "rb").read())
        self.assertEqual(0, self.cmd("own_paper.py", "apply", "--dir", folder, "--words-file", words, "--parts", "5:Results").returncode)
        self.assertNotEqual(before, open(path, "rb").read())


if __name__ == "__main__":
    unittest.main()
