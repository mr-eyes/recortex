"""Tests of the versions of the paper's text (1.3.0): the branch of versions, its layout, the workflow that builds it, the
names, the restore, the pull request path of an approval and the History on the mind page. Nothing here reaches GitHub; the
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

sys.path.insert(0, ts.SCRIPTS)
import paper_layout  # noqa: E402
import record_format  # noqa: E402
import recortex  # noqa: E402

BRANCH = "recortex-manuscript"
WORKFLOW = ".github/workflows/recortex-manuscript.yaml"


class VersionBox(tw.WritingBox):
    """The writing box, with helpers for the branch of versions in the fake repository."""

    def git(self):
        return self.repo().get("git", {"refs": {}, "commits": {}, "trees": {}, "updates": []})

    def head(self, ref="heads/" + BRANCH):
        return self.git()["refs"].get(ref)

    def files_at(self, commit=None):
        git = self.git()
        commit = commit or self.head()
        return git["trees"][git["commits"][commit]["tree"]]

    def history(self):
        """The commits of the branch, newest first."""
        git, out, current = self.git(), [], self.head()
        while current:
            out.append(current)
            parents = git["commits"][current]["parents"]
            current = parents[0] if parents else None
        return out

    def written(self, mode="autonomous", env=None):
        self.ready()
        self.mode(mode)
        folder = self.start(5)
        self.draft(folder)
        done = self.cmd("write.py", "apply", "--dir", folder, *(("--words-file", self.words("Approve")) if mode == "review" else ()),
                        env=env)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.ready()
        return done

    def tags(self):
        return {ref: sha for ref, sha in self.git()["refs"].items() if ref.startswith("tags/")}


class VersionTests(VersionBox):
    def test_a_mode_of_review_makes_the_branch_with_a_first_commit_that_has_no_parent(self):
        self.ready()
        done = self.cmd("setup.py", "writing", "set", "--mode", "review", "--yes")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("versions: recortex keeps every version of the manuscript in octo-org/sample-project from now on.", done.stdout)
        git = self.git()
        self.assertEqual(["heads/" + BRANCH], list(git["refs"]))
        first = git["commits"][self.head()]
        self.assertEqual([], first["parents"])
        files = self.files_at()
        self.assertIn(WORKFLOW, files)
        self.assertIn("topics/5.md", files)
        self.assertIn("VERSION.json", files)
        self.assertEqual(self.body(5).rstrip("\n") + "\n", files["topics/5.md"])
        self.assertTrue(any(name.startswith("content/10.") for name in files))
        self.assertEqual(len(git["commits"]), 1)

    def test_a_mode_of_off_or_none_makes_no_branch_and_a_body_edit_makes_no_commit(self):
        self.ready()
        folder = os.path.join(self.dir, "text.md")
        open(folder, "w").write("## Results\n\ntext\n")
        done = self.cmd("versions.py", "commit", "--issue", "5", "--body-file", folder)   # no mode chosen yet
        self.assertEqual(1, done.returncode)
        self.assertIn("Not kept: versions are not kept here", done.stdout)
        self.assertEqual(0, self.cmd("setup.py", "writing", "set", "--mode", "off", "--yes").returncode)
        done = self.cmd("versions.py", "commit", "--issue", "5", "--body-file", folder)
        self.assertEqual(1, done.returncode)
        self.assertEqual({}, self.git()["refs"])
        self.assertEqual({}, self.git()["commits"])

    def test_a_write_in_autonomous_mode_makes_one_commit_after_the_first_and_names_it_in_the_memory(self):
        self.ready()
        self.mode("autonomous")
        first = self.head()
        self.assertIsNotNone(first)
        folder = self.start(5)
        self.draft(folder)
        done = self.cmd("write.py", "apply", "--dir", folder)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        history = self.history()
        self.assertEqual(2, len(history))
        self.assertEqual(first, history[1])
        files = self.files_at()
        self.assertEqual(self.body(5).rstrip("\n") + "\n", files["topics/5.md"])
        self.assertIn("The pooled fit and the dry-spell comparison agree", files["topics/5.md"])
        (cid, memory), = self.text_memories()
        self.assertEqual("`%s`" % history[0], dict(record_format.fields(memory))["Version"])
        self.assertEqual("Text: Results of topic 5", self.git()["commits"][history[0]]["message"])
        self.assertEqual(sorted(self.git()["updates"][-1][:1]), ["heads/" + BRANCH])
        self.assertEqual([False], [force for _, _, force in self.git()["updates"][1:]])   # never a forced move

    def test_status_log_and_diff_over_three_versions(self):
        self.ready()
        self.mode("autonomous")
        folder = self.start(5)
        self.draft(folder)
        self.assertEqual(0, self.cmd("write.py", "apply", "--dir", folder).returncode)
        status = self.cmd("versions.py", "status")
        self.assertIn("#5", status.stdout)
        self.assertIn("in step with its file", status.stdout)
        self.assertIn("workflow: as shipped", status.stdout)
        self.ready()
        log = self.cmd("versions.py", "log", "--issue", "5", "--json")
        rows = json.loads(log.stdout)
        self.assertEqual(1, len(rows))
        self.assertEqual("written by the plugin without asking", rows[0]["how"])
        self.assertEqual(self.history()[0], rows[0]["commit"])
        diff = self.cmd("versions.py", "diff", self.history()[1], self.history()[0], "--issue", "5")
        self.assertEqual(0, diff.returncode, diff.stdout + diff.stderr)
        self.assertIn("+The pooled fit and the dry-spell comparison agree", diff.stdout)
        self.assertIn("text change %d (written by the plugin without asking)" % rows[0]["id"], diff.stdout)
        same = self.cmd("versions.py", "diff", "HEAD", self.history()[0], "--issue", "5")
        self.assertIn("no difference", same.stdout)

    def test_a_hand_edit_is_recorded_by_sync_with_its_login_and_stamp(self):
        self.ready()
        self.mode("autonomous")
        data = self.repo()
        issue = data["issues"]["5"]
        issue["body"] = issue["body"].replace("\r\n", "\n").replace(
            "\n### Figure: dryspell", "\nA sentence the author typed on GitHub.\n\n### Figure: dryspell", 1)
        issue["edits"] = [{"at": "2026-12-02T10:00:00Z", "by": "octocat"}, {"at": issue["created_at"], "by": "octocat"}]
        issue["last_edited_at"] = issue["updated_at"] = "2026-12-02T10:00:00Z"
        self.save(data)
        self.ready()
        before = len(self.history())
        done = self.cmd("versions.py", "sync")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(before + 1, len(self.history()))
        (cid, memory), = self.text_memories()
        fields = dict(record_format.fields(memory))
        self.assertEqual("Edited by octocat on GitHub, 2026-12-02 10:00 UTC.", fields["Approved"])
        self.assertEqual("Results", fields["Section"])
        self.assertTrue(fields["Why"].startswith("Not recorded: the text was edited by hand"))
        self.assertEqual("`%s`" % self.head(), fields["Version"])
        again = self.cmd("versions.py", "sync")
        self.assertIn("sync made 0 versions", again.stdout)
        self.assertEqual(before + 1, len(self.history()))

    def test_a_head_that_moved_is_read_again_once_and_never_forced(self):
        self.ready()
        self.mode("autonomous")
        folder = self.start(5)
        self.draft(folder)
        done = self.cmd("write.py", "apply", "--dir", folder, env={"FAKE_GH_MOVE_HEAD": "once"})
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        updates = self.git()["updates"]
        self.assertTrue(all(force is False for _, _, force in updates))
        history = self.history()
        self.assertEqual("another commit", self.git()["commits"][history[1]]["message"])
        self.assertEqual("Text: Results of topic 5", self.git()["commits"][history[0]]["message"])

    def test_a_branch_that_keeps_moving_gives_the_fixed_line_and_not_kept(self):
        self.ready()
        self.mode("autonomous")
        folder = self.start(5)
        self.draft(folder)
        done = self.cmd("write.py", "apply", "--dir", folder, env={"FAKE_GH_MOVE_HEAD": "always"})
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("versions: the branch changed while writing; run versions.py sync.", done.stdout)
        self.assertIn("The pooled fit", self.body(5))
        (cid, memory), = self.text_memories()
        self.assertEqual("Not kept: the branch changed while writing", dict(record_format.fields(memory))["Version"])
        self.assertTrue(all(force is False for _, _, force in self.git()["updates"]))


class BodyPathTests(VersionBox):
    def edit(self, issue=5):
        """A body edit by the session's ordinary work: the live body with one sentence added to Results."""
        live = self.body(issue)
        new = live.replace("\n### Figure: dryspell", "\nThe dry spells were counted per plot.\n\n### Figure: dryspell", 1)
        self.assertNotEqual(live, new)
        path = os.path.join(self.dir, "edit-body.md")
        base = os.path.join(self.dir, "edit-base.md")
        open(path, "w").write(new)
        open(base, "w").write(live)
        return live, new, path, base

    def post_body(self, *extra, issue=5):
        live, new, path, base = self.edit(issue)
        return live, new, self.cmd("post_verified.py", "body", "--issue", str(issue), "--kind", "final-record", "--body-file", path,
                                   "--expected-body-file", base, "--backup", os.path.join(self.dir, "bk%d.md" % len(os.listdir(self.dir))),
                                   *extra)

    def memory(self, live, new, text="the dry spells were counted per plot"):
        return manuscript_record.text_memory("2026-12-03T10:00-05:00", "Results", text, "One sentence was added.", "The count was missing.",
                                              "Written without asking, under the autonomous mode.",
                                              before=record_format.body_sha(live), after=record_format.body_sha(new),
                                              version="b" * 40).replace("`%s`" % ("b" * 40), "pending")

    def test_a_body_edit_with_a_text_memory_makes_one_commit_and_one_memory(self):
        self.ready()
        self.mode("autonomous")
        live, new, path, base = self.edit()
        memory = os.path.join(self.dir, "memory.md")
        open(memory, "w").write(self.memory(live, new))
        done = self.cmd("post_verified.py", "body", "--issue", "5", "--kind", "final-record", "--body-file", path,
                        "--expected-body-file", base, "--backup", os.path.join(self.dir, "bk.md"), "--text-memory", memory)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(2, len(self.history()))
        (cid, text), = self.text_memories()
        self.assertEqual("`%s`" % self.head(), dict(record_format.fields(text))["Version"])
        self.assertIn("The dry spells were counted per plot.", self.files_at()["topics/5.md"])

    def test_a_body_edit_without_a_text_memory_is_left_for_sync_and_says_so(self):
        self.ready()
        self.mode("autonomous")
        live, new, done = self.post_body()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("versions: this body change has no text change of its own; pass --text-memory FILE next time, or "
                      "versions.py sync records it as an edit.", done.stdout)
        self.assertEqual(1, len(self.history()))
        self.assertEqual([], self.text_memories())

    def test_a_text_memory_that_is_not_for_this_body_is_refused_after_the_edit_and_makes_no_commit(self):
        self.ready()
        self.mode("autonomous")
        live, new, path, base = self.edit()
        memory = os.path.join(self.dir, "memory.md")
        open(memory, "w").write(self.memory(live, live))   # its after hash is the old body
        done = self.cmd("post_verified.py", "body", "--issue", "5", "--kind", "final-record", "--body-file", path,
                        "--expected-body-file", base, "--backup", os.path.join(self.dir, "bk.md"), "--text-memory", memory)
        self.assertEqual(1, done.returncode)
        self.assertIn("the body was written and has no text change yet", done.stdout)
        self.assertEqual(1, len(self.history()))
        self.assertEqual([], self.text_memories())

    def test_no_commit_and_no_note_when_the_mode_is_off_or_not_chosen(self):
        self.ready()
        for mode in (None, "off"):
            if mode:
                self.mode(mode)
            live, new, done = self.post_body()
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            self.assertNotIn("versions:", done.stdout)
            self.assertEqual({}, self.git()["refs"])
            self.save_body(5, live)

    def save_body(self, issue, text):
        data = self.repo()
        data["issues"][str(issue)]["body"] = text
        self.save(data)
        self.ready()


class NameAndRefusalTests(VersionBox):
    def test_a_name_makes_a_decision_with_the_fixed_line_and_a_tag_at_the_same_commit(self):
        self.written()
        done = self.cmd("versions.py", "name", "submitted", "--words-file", self.words("Call this one submitted"))
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        head = self.head()
        self.assertEqual({"tags/paper-submitted": head}, self.tags())
        decisions = [c["body"] for c in self.repo()["comments"].values() if "type=decision" in c["body"].split("\n")[0]
                     and "Version name:" in c["body"]]
        self.assertEqual(1, len(decisions))
        effect = record_format.field_text(decisions[0], "Effect")
        name, commit, manifest = record_format.version_name(effect)
        self.assertEqual(("submitted", head), (name, commit))
        self.assertEqual(hashlib.sha256(self.files_at()["VERSION.json"].encode()).hexdigest(), manifest)
        self.assertIn("> Call this one submitted", decisions[0])
        issue = next(c["issue"] for c in self.repo()["comments"].values() if "Version name:" in c["body"])
        self.assertEqual(13, issue)   # the plan issue
        self.ready()
        self.assertIn("name submitted: commit %s" % head[:12], self.cmd("versions.py", "status").stdout)
        rows = json.loads(self.cmd("versions.py", "log", "--json").stdout)
        self.assertEqual([["submitted"]], [row["names"] for row in rows])

    def test_an_existing_name_and_a_bad_name_are_refused(self):
        self.written()
        words = self.words("Name it")
        self.assertEqual(0, self.cmd("versions.py", "name", "submitted", "--words-file", words).returncode)
        self.ready()
        before = (len(self.repo()["comments"]), self.tags())
        for bad in ("submitted", "Submitted", "-x", "a b", "x" * 41, ".x", "x/y"):
            done = self.cmd("versions.py", "name", bad, "--words-file", words)
            self.assertNotEqual(0, done.returncode, bad)
        self.assertEqual(before, (len(self.repo()["comments"]), self.tags()))
        self.assertNotEqual(0, self.cmd("versions.py", "name", "later").returncode)   # the author's words are needed
        empty = os.path.join(self.dir, "empty")
        open(empty, "w").write("  \n")
        self.assertNotEqual(0, self.cmd("versions.py", "name", "later", "--words-file", empty).returncode)

    def test_a_tag_that_already_exists_blocks_the_name_and_nothing_is_moved(self):
        self.written()
        data = self.repo()
        data["git"]["refs"]["tags/paper-draft"] = self.history()[1]
        self.save(data)
        done = self.cmd("versions.py", "name", "draft", "--words-file", self.words("x"))
        self.assertNotEqual(0, done.returncode)
        self.assertEqual(self.history()[1], self.tags()["tags/paper-draft"])

    def test_a_name_is_refused_while_a_body_and_its_file_differ(self):
        self.written()
        data = self.repo()
        git = data["git"]
        files = dict(git["trees"][git["commits"][git["refs"]["heads/" + BRANCH]]["tree"]])
        files["topics/5.md"] = files["topics/5.md"] + "\nsomeone edited this file on the branch\n"
        tree = hashlib.sha1(json.dumps(files, sort_keys=True).encode()).hexdigest()
        git["trees"][tree] = files
        commit = "c" * 40
        git["commits"][commit] = {"tree": tree, "parents": [git["refs"]["heads/" + BRANCH]], "message": "by hand"}
        git["refs"]["heads/" + BRANCH] = commit
        self.save(data)
        status = self.cmd("versions.py", "status").stdout
        self.assertIn("the file of this part was changed on the branch and the text was not; ask the author", status)
        done = self.cmd("versions.py", "name", "later", "--words-file", self.words("x"))
        self.assertNotEqual(0, done.returncode)
        self.assertIn("differ", done.stdout + done.stderr)
        self.assertEqual({}, self.tags())
        sync = self.cmd("versions.py", "sync")
        self.assertIn("nothing is taken from it", sync.stdout)
        self.assertEqual(commit, self.head())

    def refused_line(self, env, line_key):
        lines = {"push": "versions: this account may not write files to octo-org/sample-project, so this version was not kept as a "
                         "file. The text itself is saved. Run versions.py doctor.",
                 "rule": "versions: a branch rule of octo-org/sample-project refused the write to recortex-manuscript, so this "
                         "version was not kept as a file. The text itself is saved. Run versions.py doctor."}
        reasons = {"push": "Not kept: this account may not write files to the repository",
                   "rule": "Not kept: a branch rule refused the write"}
        self.ready()
        chosen = self.cmd("setup.py", "writing", "set", "--mode", "autonomous", "--yes", env=env)
        self.assertEqual(0, chosen.returncode, chosen.stdout + chosen.stderr)
        self.assertIn(lines[line_key], chosen.stdout)   # said when the mode is chosen
        folder = self.start(5)
        self.draft(folder)
        done = self.cmd("write.py", "apply", "--dir", folder, env=env)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn(lines[line_key], done.stdout)
        self.assertIn("The pooled fit", self.body(5))   # the body is written first and stays written
        (cid, memory), = self.text_memories()
        self.assertEqual(reasons[line_key], dict(record_format.fields(memory))["Version"])
        doctor = self.cmd("versions.py", "doctor", env=env)
        self.assertIn("last refusal", doctor.stdout)
        self.assertIn(lines[line_key], doctor.stdout)
        return folder

    def test_a_token_that_may_not_write_keeps_the_text_and_says_so(self):
        for status in ("403", "404"):
            self.refused_line({"FAKE_GH_GIT_NO_PUSH": status}, "push")
            break
        self.assertEqual({}, self.git()["refs"])
        # a later sync, once the permission is there, makes the branch and the missing commit
        self.ready()
        done = self.cmd("versions.py", "sync")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIsNotNone(self.head())
        self.assertIn("The pooled fit", self.files_at()["topics/5.md"])

    def test_a_write_refused_with_404_is_treated_like_403(self):
        self.refused_line({"FAKE_GH_GIT_NO_PUSH": "404"}, "push")

    def test_a_branch_rule_is_a_refusal_of_its_own(self):
        self.refused_line({"FAKE_GH_BRANCH_RULE": "1"}, "rule")
        self.assertEqual({}, self.git()["refs"])

    def test_a_sign_in_that_may_not_add_a_workflow_keeps_the_versions_without_it(self):
        self.ready()
        done = self.cmd("setup.py", "writing", "set", "--mode", "autonomous", "--yes", env={"FAKE_GH_NO_WORKFLOW": "1"})
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("builds: this sign-in may not add a workflow to octo-org/sample-project, so the paper is not built on "
                      "GitHub; its versions are kept. Run versions.py doctor.", done.stdout)
        self.assertNotIn(WORKFLOW, self.files_at())
        self.assertIn("topics/5.md", self.files_at())
        doctor = self.cmd("versions.py", "doctor", env={"FAKE_GH_NO_WORKFLOW": "1"}).stdout
        self.assertIn("gh auth refresh -s workflow", doctor)
        self.assertIn("workflow: missing", doctor)
        # the permission is granted: setting the mode again adds the workflow in its own commit
        before = len(self.history())
        again = self.cmd("setup.py", "writing", "set", "--mode", "autonomous", "--yes")
        self.assertIn(WORKFLOW, self.files_at())
        self.assertEqual(before + 1, len(self.history()))

    def test_a_workflow_that_differs_is_reported_and_never_replaced(self):
        self.ready()
        self.mode("autonomous")
        data = self.repo()
        git = data["git"]
        head = git["refs"]["heads/" + BRANCH]
        files = dict(git["trees"][git["commits"][head]["tree"]])
        files[WORKFLOW] = files[WORKFLOW] + "\n# changed by hand\n"
        tree = "d" * 40
        git["trees"][tree] = files
        git["commits"]["e" * 40] = {"tree": tree, "parents": [head], "message": "by hand"}
        git["refs"]["heads/" + BRANCH] = "e" * 40
        self.save(data)
        self.assertIn("differs from the one shipped with the plugin", self.cmd("versions.py", "status").stdout)
        self.cmd("setup.py", "writing", "set", "--mode", "autonomous", "--yes")
        self.assertIn("# changed by hand", self.files_at()[WORKFLOW])
        self.assertEqual("e" * 40, self.head())


class RestoreTests(VersionBox):
    def two_versions(self, name_first=None):
        """V1 written by the plugin (named when asked), V2 an edit by hand (recorded by sync) in Results and in Limitations."""
        self.ready()
        self.mode("autonomous")
        folder = self.start(5)
        self.draft(folder)
        self.assertEqual(0, self.cmd("write.py", "apply", "--dir", folder).returncode)
        v1 = self.head()
        if name_first:
            self.ready()
            self.assertEqual(0, self.cmd("versions.py", "name", name_first, "--words-file", self.words("name it")).returncode)
        text_one = self.body(5)
        data = self.repo()
        issue = data["issues"]["5"]
        body = issue["body"].replace("\r\n", "\n")
        body = body.replace("\n### Figure: dryspell", "\nA second look at the spells, typed by hand.\n\n### Figure: dryspell", 1)
        limits = re.search(r"## Limitations\n\n", body)
        self.assertIsNotNone(limits)
        body = body[:limits.end()] + "Typed by hand into the limits.\n\n" + body[limits.end():]
        issue["body"] = body
        issue["edits"] = [{"at": "2026-12-02T10:00:00Z", "by": "octocat"}, {"at": issue["created_at"], "by": "octocat"}]
        issue["last_edited_at"] = issue["updated_at"] = "2026-12-02T10:00:00Z"
        self.save(data)
        self.ready()
        self.assertEqual(0, self.cmd("versions.py", "sync").returncode)
        self.ready()
        return v1, text_one

    def restore(self, to, *extra, name="r1"):
        out = os.path.join(self.dir, name)
        return out, self.cmd("versions.py", "restore", "--issue", "5", "--to", to, "--out", out, *extra)

    def test_a_restore_of_one_section_leaves_the_others_and_is_a_new_version_that_equals_an_old_one(self):
        v1, text_one = self.two_versions()
        versions_before = len(self.history())
        out, done = self.restore(v1, "--section", "Results")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        draft = self.read(out, "draft.md")
        self.assertNotIn("A second look at the spells, typed by hand.", draft)
        self.assertIn("Typed by hand into the limits.", draft)
        checked = self.cmd("write.py", "check", "--dir", out)
        self.assertEqual(0, checked.returncode, checked.stdout)
        refused = self.cmd("write.py", "apply", "--dir", out)   # a restore is always asked in the chat, autonomous included
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("a restore is always asked in the chat", refused.stderr)
        self.assertIn("A second look", self.body(5))
        done = self.cmd("write.py", "apply", "--dir", out, "--words-file", self.words("Restore this one"))
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertNotIn("A second look", self.body(5))
        self.assertIn("Typed by hand into the limits.", self.body(5))
        self.assertEqual(versions_before + 1, len(self.history()))   # history only grows
        newest = self.text_memories()[-1][1]
        fields = dict(record_format.fields(newest))
        self.assertTrue(fields["Approved"].startswith("Restored by the author in the chat, "))
        self.assertIn("> Restore this one", fields["Approved"])
        self.assertEqual("Results", fields["Section"])
        self.assertEqual("`%s`" % self.head(), fields["Version"])
        self.assertIn("The pooled fit and the dry-spell comparison agree", self.files_at()["topics/5.md"])
        self.ready()
        rows = json.loads(self.cmd("versions.py", "log", "--issue", "5", "--json").stdout)
        self.assertEqual("restored by you", rows[0]["how"])

    def test_a_file_the_record_does_not_vouch_for_is_never_restored(self):
        v1, text_one = self.two_versions()
        data = self.repo()
        git = data["git"]
        head = git["refs"]["heads/" + BRANCH]
        files = dict(git["trees"][git["commits"][head]["tree"]])
        files["topics/5.md"] = files["topics/5.md"].replace("pooled fit", "pooled FIT")
        tree = "f" * 40
        git["trees"][tree] = files
        git["commits"]["a" * 40] = {"tree": tree, "parents": [head], "message": "by hand"}
        git["refs"]["heads/" + BRANCH] = "a" * 40
        self.save(data)
        for target in ("a" * 40, "0" * 40, "nosuchname", "HEAD"):
            out, done = self.restore(target, name="r-" + target[:6])
            self.assertNotEqual(0, done.returncode, target)
            self.assertIn("that file is not a version the record holds, so it is not restored; copy it by hand if it is yours.",
                          done.stdout + done.stderr, target)
            self.assertFalse(os.path.exists(os.path.join(out, "draft.md")))

    def test_a_restore_that_changes_anything_else_is_refused_by_check_nine(self):
        v1, text_one = self.two_versions()
        out, done = self.restore(v1, "--section", "Results")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        draft = self.read(out, "draft.md").replace("Typed by hand into the limits.", "Rewritten limits.")
        self.write_file(out, "draft.md", draft)
        checked = self.cmd("write.py", "check", "--dir", out)
        self.assertEqual(1, checked.returncode)
        self.assertIn("not ready (check 9)", checked.stdout)
        draft = self.read(out, "draft.md").replace("Over the whole season", "Over some of the season")
        self.write_file(out, "draft.md", draft)
        self.assertIn("is not the text of the version being restored", self.cmd("write.py", "check", "--dir", out).stdout)
        applied = self.cmd("write.py", "apply", "--dir", out, "--words-file", self.words("Restore"))
        self.assertNotEqual(0, applied.returncode)
        self.assertIn("A second look", self.body(5))

    def test_a_restore_is_offered_for_a_name_and_not_when_the_text_already_says_it(self):
        v1, text_one = self.two_versions(name_first="first")
        out, done = self.restore("first", "--section", "Results", name="r-name")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertNotIn("A second look", self.read(out, "draft.md"))
        done = self.cmd("write.py", "apply", "--dir", out, "--words-file", self.words("Restore first"))
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.ready()
        out, again = self.restore("first", "--section", "Results", name="r-again")
        self.assertNotEqual(0, again.returncode)
        self.assertIn("the text already says what that version said", again.stdout + again.stderr)
        self.assertFalse(os.path.exists(os.path.join(out, "draft.md")))


class CompileVersionTests(VersionBox):
    def compiled(self, name, *extra):
        out = os.path.join(self.dir, name)
        done = self.cmd("compile.py", "--out", out, "--no-sync", *extra)
        return out, done

    def sections_of(self, out):
        folder = os.path.join(out, "deliverable")
        return {n: open(os.path.join(folder, n), "rb").read() for n in sorted(os.listdir(folder)) if re.match(r"[0-9]{2}-", n)}

    def named(self):
        self.written()
        self.assertEqual(0, self.cmd("versions.py", "name", "submitted", "--words-file", self.words("submit")).returncode)
        self.ready()

    def test_a_named_version_compiles_to_the_bytes_an_ordinary_compile_gave_at_that_moment(self):
        self.named()
        ordinary, done = self.compiled("now")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        # the text moves on after the name
        data = self.repo()
        data["issues"]["5"]["body"] = data["issues"]["5"]["body"].replace("pooled fit", "pooled FIT")
        data["issues"]["5"]["updated_at"] = "2026-12-09T10:00:00Z"
        self.save(data)
        self.ready()
        out, done = self.compiled("v", "--version", "submitted")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(self.sections_of(ordinary), self.sections_of(out))
        self.assertNotIn(b"pooled FIT", b"".join(self.sections_of(out).values()))
        manifest = json.load(open(os.path.join(out, "MANIFEST.json")))
        self.assertEqual("submitted", manifest["version"]["name"])
        self.assertNotIn("plan", manifest)
        self.assertFalse(os.path.exists(os.path.join(out, "deliverable", "plan.md")))
        later, done = self.compiled("later")
        self.assertIn(b"pooled FIT", b"".join(self.sections_of(later).values()))

    def test_a_tag_no_told_decision_names_writes_nothing(self):
        self.named()
        data = self.repo()
        data["git"]["refs"]["tags/paper-ghost"] = self.head()
        self.save(data)
        out, done = self.compiled("ghost", "--version", "ghost")
        self.assertNotEqual(0, done.returncode)
        self.assertIn("no decision of an allowed account names a version called ghost", done.stderr + done.stdout)
        self.assertFalse(os.path.exists(out))

    def test_a_decision_of_an_account_that_is_not_allowed_names_nothing(self):
        self.written()
        words = self.words("name it")
        self.assertEqual(0, self.cmd("versions.py", "name", "rogue", "--words-file", words).returncode)
        data = self.repo()
        for cid, comment in data["comments"].items():
            if "Version name: rogue" in comment["body"]:
                comment["user"] = "hubot"
        self.save(data)
        self.ready()
        out, done = self.compiled("rogue", "--version", "rogue")
        self.assertNotEqual(0, done.returncode)
        self.assertIn("no decision of an allowed account names a version called rogue", done.stderr + done.stdout)
        self.assertFalse(os.path.exists(out))

    def tree_of(self):
        git = self.git()
        return git["commits"][self.head()]["tree"]

    def test_a_version_json_that_is_not_the_one_the_decision_names_writes_nothing(self):
        self.named()
        data = self.repo()
        git = data["git"]
        tree = git["trees"][git["commits"][git["refs"]["heads/" + BRANCH]]["tree"]]
        tree["VERSION.json"] = tree["VERSION.json"].replace('"hub": 1', '"hub": 2')
        self.save(data)
        out, done = self.compiled("v", "--version", "submitted")
        self.assertNotEqual(0, done.returncode)
        self.assertIn("VERSION.json at that commit is not the one its decision names", done.stderr + done.stdout)
        self.assertFalse(os.path.exists(out))

    def test_a_topic_file_that_is_not_the_text_version_json_says_writes_nothing(self):
        self.named()
        data = self.repo()
        git = data["git"]
        tree = git["trees"][git["commits"][git["refs"]["heads/" + BRANCH]]["tree"]]
        tree["topics/5.md"] = tree["topics/5.md"] + "\nAn extra line nobody vouches for.\n"
        self.save(data)
        out, done = self.compiled("v", "--version", "submitted")
        self.assertNotEqual(0, done.returncode)
        self.assertIn("the file of #5 at that commit is not the text VERSION.json says", done.stderr + done.stdout)
        self.assertFalse(os.path.exists(out))

    def test_a_tag_that_points_somewhere_else_writes_nothing(self):
        self.named()
        data = self.repo()
        data["git"]["refs"]["tags/paper-submitted"] = self.history()[1]
        self.save(data)
        out, done = self.compiled("v", "--version", "submitted")
        self.assertNotEqual(0, done.returncode)
        self.assertIn("the tag paper-submitted does not point at the commit its decision names", done.stderr + done.stdout)
        self.assertFalse(os.path.exists(out))

    def test_the_manubot_layout_is_the_branchs_own_content(self):
        self.named()
        out, done = self.compiled("m", "--layout", "manubot")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        branch = {n: t for n, t in self.files_at().items() if n.startswith("content/")}
        here = {}
        for folder, _, names in os.walk(os.path.join(out, "content")):
            for name in names:
                here[os.path.relpath(os.path.join(folder, name), out)] = open(os.path.join(folder, name), encoding="utf-8").read()
        self.assertEqual(branch, here)
        again, _ = self.compiled("m2", "--version", "submitted", "--layout", "manubot")
        self.assertEqual(branch["content/20.results.md"], open(os.path.join(again, "content", "20.results.md")).read())


# ---------------------------------------------------------------- the layout

FIGURE_BODY = """Over the season the difference was small $x$ and $$a+b=c$$ (see @fig:dry and @tbl:fit, @eq:one).

### A subheading

Daily water in plots [@doi:10.1234/abc; @pmid:12345].

### Figure: dry - Soil moisture through the spell

Daily water content at 5 cm.

![x](https://github.com/octo-org/sample-project/blob/v0.4/figures/dry.png?raw=true)

### Table: fit - Model fit

Fit by season.

| a | b |
|---|---|
| 1 | 2 |

## Sources

| Statement | Source |
|---|---|
| a | doi:10.1/x |
"""


class LayoutTests(unittest.TestCase):
    def lay(self, title="Moisture", **kw):
        text = record_format.section_text(FIGURE_BODY, "Results") or ""
        return paper_layout.layout("octo-org/sample-project", 1, title, [
            ("Introduction", [(2, "Why we looked", "The first words.")]),
            ("Results", [(5, "Dry spells", FIGURE_BODY.split("## Sources")[0].strip("\n"))]),
            ("Discussion", [(6, "Nothing yet", "")])], **kw)

    def test_the_file_names_and_their_order_follow_the_outline_and_empty_sections_are_left_out(self):
        files, _ = self.lay()
        self.assertEqual(["content/00.front-matter.md", "content/10.introduction.md", "content/20.results.md", "content/90.back-matter.md",
                          "content/images.tsv", "content/manual-references.json", "content/metadata.yaml"], sorted(files))

    def test_a_topics_headings_go_one_level_lower_under_the_sections_own(self):
        files, _ = self.lay()
        text = files["content/20.results.md"]
        self.assertTrue(text.startswith("## Results\n\n### Dry spells\n\n"))
        self.assertIn("\n#### A subheading\n", text)

    def test_a_figure_and_a_table_block_take_manubots_forms_with_their_ids(self):
        files, images = self.lay()
        text = files["content/20.results.md"]
        self.assertIn("\n\n![Soil moisture through the spell. Daily water content at 5 cm.](images/dry.png){#fig:dry}\n\n", text)
        self.assertIn("| a | b |\n|---|---|\n| 1 | 2 |\n\nTable: Model fit. Fit by season. {#tbl:fit}\n", text)
        self.assertEqual([("dry", "octo-org/sample-project", "v0.4", "figures/dry.png")], images)
        self.assertIn("dry\toctopus".replace("octopus", "octo-org/sample-project") + "\tv0.4\tfigures/dry.png", files["content/images.tsv"])

    def test_formulas_citations_and_references_pass_through_and_sources_stays_out(self):
        files, _ = self.lay()
        text = files["content/20.results.md"]
        for kept in ("$x$", "$$a+b=c$$", "@fig:dry", "@tbl:fit", "@eq:one", "[@doi:10.1234/abc; @pmid:12345]"):
            self.assertIn(kept, text)
        self.assertNotIn("Sources", text)
        self.assertNotIn("doi:10.1/x", "".join(files.values()))

    def test_a_title_with_a_colon_a_quote_and_a_line_break_is_one_quoted_yaml_string(self):
        files, _ = self.lay(title='A: "b"\nauthors: [evil]\n---', authors=["Ada: Lovelace", "x\ny"], keywords=["k: v"])
        meta = files["content/metadata.yaml"]
        self.assertEqual('title: "A: \\"b\\"\\nauthors: [evil]\\n---"', meta.split("\n")[0])
        self.assertEqual(['title', 'keywords', 'lang', 'authors', '  - name', '  - name'], [
            line.split(":")[0] for line in meta.rstrip("\n").split("\n")])
        self.assertEqual((["Ada: Lovelace", "x\ny"], ["k: v"]), paper_layout.read_metadata(meta))

    def test_two_runs_give_the_same_bytes(self):
        self.assertEqual(self.lay(), self.lay())

    def test_the_figure_is_never_made_by_the_layout(self):
        files, _ = self.lay()
        self.assertFalse([name for name in files if name.startswith("content/images/")])


# ---------------------------------------------------------------- the workflow file

PINNED = {"actions/checkout": "d23441a48e516b6c34aea4fa41551a30e30af803",
          "actions/cache": "caa296126883cff596d87d8935842f9db880ef25",
          "conda-incubator/setup-miniconda": "fc2d68f6413eb2d87b895e92f8584b5b94a10167",
          "actions/upload-artifact": "b7c566a772e6b6bfb58ed0dc250532a479d7789f"}
ROOTSTOCK = "f44f9bbe35441a8acd51a5898e6e739acaf54c1c"


def top_block(lines, key):
    """The (indent-stripped) child lines of a top-level key."""
    out, inside = [], False
    for line in lines:
        if re.match(r"^%s:\s*$" % re.escape(key), line):
            inside = True
            continue
        if inside:
            if line.strip() == "" or line.lstrip().startswith("#"):
                continue
            if not line.startswith(" "):
                break
            out.append(line)
    return out


def step_blocks(lines):
    blocks, current = [], None
    started = False
    for line in lines:
        if re.match(r"^    steps:\s*$", line):
            started = True
            continue
        if not started:
            continue
        if re.match(r"^      - ", line):
            current = [line]
            blocks.append(current)
        elif current is not None:
            current.append(line)
    return blocks


def run_blocks(block):
    """The text of each `run:` of a step."""
    out, lines = [], block
    for number, line in enumerate(lines):
        found = re.match(r"^(\s*)(?:- )?run:\s*(.*)$", line)
        if not found:
            continue
        indent, rest = len(found.group(1)), found.group(2)
        if rest and not rest.startswith(("|", ">")):
            out.append(rest)
            continue
        body = []
        for following in lines[number + 1:]:
            if following.strip() and len(following) - len(following.lstrip()) <= indent + 1:
                break
            body.append(following)
        out.append("\n".join(body))
    return out


def workflow_problems(text):
    """What is wrong with a workflow file for the manuscript branch, by the rules of references/versions.md; [] when nothing."""
    problems = []
    lines = text.split("\n")
    permissions = {}
    for line in top_block(lines, "permissions"):
        found = re.match(r"^\s+([a-z-]+):\s*(\S+)\s*$", line)
        if found:
            permissions[found.group(1)] = found.group(2)
    if permissions != {"contents": "read", "actions": "read"}:
        problems.append("the top-level permissions are not exactly contents: read and actions: read")
    if len(re.findall(r"^\s*permissions:", text, re.M)) != 1:
        problems.append("a job or a step sets permissions of its own")
    if re.search(r":\s*write\b", text):
        problems.append("the word write is a permission")
    for key in ("pages", "id-token", "deployments", "environment"):
        if re.search(r"^\s*%s\s*:" % re.escape(key), text, re.M):
            problems.append("the key %s is set" % key)
    for name in re.findall(r"secrets\.([A-Za-z_]+)", text):
        if name != "GITHUB_TOKEN":
            problems.append("a secret other than GITHUB_TOKEN is read: %s" % name)
    triggers = top_block(lines, "on")
    keys = [re.match(r"^(\s*)([a-z_]+):", line) for line in triggers]
    top = [k.group(2) for k in keys if k and len(k.group(1)) == 2]
    if top != ["push"]:
        problems.append("the triggers are not only a push: %s" % ", ".join(top))
    branches = [m.group(1) for line in triggers for m in [re.match(r"^\s+- '?([^']+)'?\s*$", line)] if m]
    if branches != ["recortex-manuscript", "paper-*"]:
        problems.append("the push filters are not the branch and the tags paper-*")
    for found in re.findall(r"^\s*(?:- )?uses:\s*(\S+)", text, re.M):
        match = re.fullmatch(r"([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)@([0-9a-f]{40})", found)
        if not match:
            problems.append("an action is not pinned to 40 hex characters: %s" % found)
        elif PINNED.get(match.group(1)) != match.group(2):
            problems.append("an action is not one of the pinned ones: %s" % found)
    blocks = step_blocks(lines)
    for block in blocks:
        joined = "\n".join(block)
        if "actions/checkout@" in joined and not re.search(r"persist-credentials:\s*false", joined):
            problems.append("a checkout keeps its credentials")
        if "actions/upload-artifact@" in joined and "retention-days:" not in joined:
            problems.append("an upload sets no retention-days")
        if "manubot/rootstock" in joined and not re.search(r"ref:\s*%s\s*$" % ROOTSTOCK, joined, re.M):
            problems.append("rootstock is not pinned to its commit")
        for run in run_blocks(block):
            if "github.event" in run or "${{" in run:
                problems.append("a run line holds an expression of the event or of the workflow context")
    if not any("manubot/rootstock" in "\n".join(b) for b in blocks):
        problems.append("rootstock is not checked out")
    for word in ("gh-pages", "ghp-import", "deploy.sh", "git push", "pages:"):
        if word in text:
            problems.append("something publishes: %s" % word)
    if re.search(r"^\s*-?\s*name:\s*Deploy", text, re.M):
        problems.append("a deploy step")
    return problems


class BuildWorkflowTests(unittest.TestCase):
    def shipped(self):
        with open(os.path.join(os.path.dirname(ts.SCRIPTS), "templates", "recortex-manuscript.yaml"), encoding="utf-8") as handle:
            return handle.read()

    def test_the_shipped_workflow_breaks_no_rule(self):
        self.assertEqual([], workflow_problems(self.shipped()))

    def test_the_four_actions_and_rootstock_are_the_pinned_commits(self):
        text = self.shipped()
        found = dict(re.findall(r"uses:\s*([^@\s]+)@([0-9a-f]{40})", text))
        self.assertEqual(PINNED, found)
        self.assertIn("ref: %s" % ROOTSTOCK, text)
        self.assertEqual(2, text.count("persist-credentials: false"))

    def test_the_triggers_the_retention_and_the_environment_are_what_the_docs_say(self):
        text = self.shipped()
        self.assertIn("MANUBOT_USE_DOCKER: 'false'", text)
        self.assertIn("BUILD_DOCX: 'true'", text)
        self.assertIn("SPELLCHECK: 'false'", text)
        self.assertIn("retention-days: ${{ github.ref_type == 'tag' && 90 || 30 }}", text)
        self.assertNotIn("400", text)

    def mutated(self, old, new):
        text = self.shipped()
        self.assertIn(old, text)
        return text.replace(old, new, 1)

    def assertCaught(self, text, words):
        found = workflow_problems(text)
        self.assertTrue([p for p in found if words in p], (words, found))

    def test_each_rule_catches_its_own_breach(self):
        self.assertCaught(self.mutated("permissions:\n  contents: read\n  actions: read", "permissions:\n  contents: read\n  actions: read\n  pages: write"),
                          "top-level permissions")
        self.assertCaught(self.mutated("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6", "actions/checkout@v6"),
                          "not pinned to 40 hex")
        self.assertCaught(self.mutated("    tags:\n      - 'paper-*'\n", "    tags:\n      - 'paper-*'\n  schedule:\n    - cron: '0 3 * * *'\n"),
                          "triggers")
        self.assertCaught(self.shipped() + "      - name: Deploy the paper\n        run: git push origin HEAD:gh-pages\n", "publishes")
        self.assertCaught(self.mutated("persist-credentials: false", "persist-credentials: true"), "keeps its credentials")
        self.assertCaught(self.mutated("          retention-days: ${{ github.ref_type == 'tag' && 90 || 30 }}\n", ""), "retention-days")
        self.assertCaught(self.mutated('echo "skip=false" >> "$GITHUB_OUTPUT"', 'echo "${{ github.event.head_commit.message }}"'),
                          "run line")
        self.assertCaught(self.mutated("GH_TOKEN: ${{ github.token }}", "GH_TOKEN: ${{ secrets.DEPLOY_KEY }}"), "secret")
        self.assertCaught(self.mutated("ref: %s" % ROOTSTOCK, "ref: main"), "rootstock")
        self.assertCaught(self.mutated("name: Build\n", "name: Build\n    environment: production\n"), "environment")
        self.assertCaught(self.mutated("actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f", "actions/upload-artifact@" + "a" * 40),
                          "not one of the pinned")

    def test_the_file_the_branch_is_made_with_is_the_shipped_text(self):
        self.assertEqual(self.shipped(), open(os.path.join(os.path.dirname(ts.SCRIPTS), "templates", "recortex-manuscript.yaml"),
                                             encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
