"""Tests of the cleanup of the manuscript keeper's old working folders (1.5.1): storage.remove_file and remove_folder, which
take away what the plugin made and follow no link, and observer_jobs.clean_keeper, which the daily housekeeping runs.
Nothing here reaches GitHub; the scripts run against tests/fake_gh.py."""

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_jobs_jobs as tjj  # noqa: E402
import test_jobs_keeper as tk  # noqa: E402
import test_scripts as ts  # noqa: E402

sys.path.insert(0, ts.SCRIPTS)
import observer_jobs as jobs  # noqa: E402
import observer_keeper  # noqa: E402
import observer_store as store_module  # noqa: E402
import storage  # noqa: E402

REPO = ts.REPO
DAY = 86400


def slurp(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# ---------------------------------------------------------------- the removal helpers of storage.py

class RemoveTests(unittest.TestCase):
    """storage.remove_file and remove_folder: the checks of the other helpers, and never through a link."""

    def setUp(self):
        os.makedirs(os.path.join(ROOT, ".tmp"), exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="cleanup-", dir=os.path.join(ROOT, ".tmp"))
        self.addCleanup(temporary.cleanup)
        self.base = temporary.name
        self.parent = storage.folder(os.path.join(self.base, "top"), "area")
        self.outside = os.path.join(self.base, "outside")
        os.mkdir(self.outside, 0o700)
        self.target = os.path.join(self.outside, "keep.txt")
        storage.write_file(self.target, "mine\n")

    def make(self, name, files=("a.md", "b.md")):
        path = os.path.join(self.parent, name)
        os.mkdir(path, 0o700)
        for file in files:
            storage.write_file(os.path.join(path, file), "x\n")
        return path

    def refused(self, call, kind):
        with self.assertRaises(storage.StorageError) as caught:
            call()
        self.assertEqual(kind, caught.exception.kind)

    def test_a_folder_goes_with_its_files_and_a_missing_one_is_no_error(self):
        path = self.make("one")
        self.assertTrue(storage.remove_folder(self.parent, "one"))
        self.assertFalse(os.path.lexists(path))
        self.assertFalse(storage.remove_folder(self.parent, "one"))
        self.assertTrue(os.path.isdir(self.parent))

    def test_a_file_goes_and_a_missing_one_is_no_error(self):
        path = os.path.join(self.parent, "note.txt")
        storage.write_file(path, "x\n")
        self.assertTrue(storage.remove_file(self.parent, "note.txt"))
        self.assertFalse(os.path.lexists(path))
        self.assertFalse(storage.remove_file(self.parent, "note.txt"))

    def test_a_link_at_the_name_is_not_followed_or_removed_through(self):
        os.symlink(self.outside, os.path.join(self.parent, "link"))
        self.refused(lambda: storage.remove_folder(self.parent, "link"), "symlink")
        self.assertEqual(["keep.txt"], os.listdir(self.outside))
        self.assertTrue(os.path.islink(os.path.join(self.parent, "link")))
        os.symlink(self.target, os.path.join(self.parent, "file-link"))
        self.refused(lambda: storage.remove_file(self.parent, "file-link"), "symlink")
        self.assertTrue(os.path.isfile(self.target))

    def test_a_link_inside_the_folder_stops_everything_and_its_target_is_untouched(self):
        path = self.make("one")
        os.symlink(self.target, os.path.join(path, "evil"))
        self.refused(lambda: storage.remove_folder(self.parent, "one"), "symlink")
        self.assertEqual(["a.md", "b.md", "evil"], sorted(os.listdir(path)))   # not even the plain files went
        self.assertEqual("mine\n", slurp(self.target))
        link = os.path.join(path, "folder-link")
        os.symlink(self.outside, link)
        self.refused(lambda: storage.remove_folder(self.parent, "one"), "symlink")
        self.assertEqual(["keep.txt"], os.listdir(self.outside))

    def test_a_folder_inside_the_folder_is_not_entered(self):
        path = self.make("one")
        os.mkdir(os.path.join(path, "inner"), 0o700)
        storage.write_file(os.path.join(path, "inner", "deep.md"), "x\n")
        self.refused(lambda: storage.remove_folder(self.parent, "one"), "not-folder")
        self.assertEqual(["a.md", "b.md", "inner"], sorted(os.listdir(path)))
        self.assertTrue(os.path.isfile(os.path.join(path, "inner", "deep.md")))

    def test_a_file_others_can_read_and_a_loose_folder_are_refused(self):
        path = self.make("one")
        os.chmod(os.path.join(path, "a.md"), 0o644)
        self.refused(lambda: storage.remove_folder(self.parent, "one"), "permissions")
        self.assertEqual(["a.md", "b.md"], sorted(os.listdir(path)))
        os.chmod(os.path.join(path, "a.md"), 0o600)
        os.chmod(path, 0o755)
        self.refused(lambda: storage.remove_folder(self.parent, "one"), "permissions")
        self.assertTrue(os.path.isdir(path))
        loose = os.path.join(self.parent, "loose.txt")
        storage.write_file(loose, "x\n")
        os.chmod(loose, 0o644)
        self.refused(lambda: storage.remove_file(self.parent, "loose.txt"), "permissions")
        self.assertTrue(os.path.isfile(loose))

    def test_a_folder_is_not_a_file_and_a_file_is_not_a_folder(self):
        self.make("one")
        self.refused(lambda: storage.remove_file(self.parent, "one"), "not-folder")
        storage.write_file(os.path.join(self.parent, "plain"), "x\n")
        self.refused(lambda: storage.remove_folder(self.parent, "plain"), "not-folder")
        self.assertTrue(os.path.isfile(os.path.join(self.parent, "plain")))

    def test_a_name_that_is_not_one_plain_name_is_refused(self):
        for name in ("../outside", "a/b", "", ".", "..", "x\0y", "\\x", None, 5):
            self.refused(lambda: storage.remove_folder(self.parent, name), "name")
            self.refused(lambda: storage.remove_file(self.parent, name), "name")
        self.assertEqual(["keep.txt"], os.listdir(self.outside))
        self.assertTrue(os.path.isdir(self.parent))

    def test_the_parent_must_be_a_private_folder_and_not_a_link(self):
        os.mkdir(os.path.join(self.outside, "one"), 0o700)
        storage.write_file(os.path.join(self.outside, "one", "a.md"), "x\n")
        link = os.path.join(self.base, "top", "private", "link")
        os.symlink(self.outside, link)
        self.refused(lambda: storage.remove_folder(link, "one"), "symlink")
        self.refused(lambda: storage.remove_file(link, "keep.txt"), "symlink")
        self.assertEqual(["keep.txt", "one"], sorted(os.listdir(self.outside)))
        self.assertFalse(storage.remove_folder(os.path.join(self.base, "nowhere"), "one"))

    def test_more_files_than_the_plugin_makes_are_refused(self):
        path = self.make("one", files=("a", "b", "c", "d"))
        with mock.patch.object(storage, "REMOVE_MAX", 3):
            self.refused(lambda: storage.remove_folder(self.parent, "one"), "refused")
        self.assertEqual(4, len(os.listdir(path)))
        self.assertTrue(storage.remove_folder(self.parent, "one"))


# ---------------------------------------------------------------- the keeper's folders, taken away by the housekeeping

class KeeperHelpers:
    """The keeper's private folder, which the helpers fill with folders as the keeper makes them (a token of 16 hex
    digits, a draft's `<id>-approved` copy), and the store's drafts. Needs self.store() and self.dir."""

    def start_keeper(self):
        self.keeper = storage.cache_folder(REPO, "keeper")
        self.count = 0

    def token(self):
        self.count += 1
        return "%016x" % (0xabc0000000000000 + self.count)

    def folder(self, name=None, files=("base.md", "draft.md", "used.json"), age_days=0):
        path = storage.cache_folder(REPO, "keeper", name or self.token())
        for file in files:
            storage.write_file(os.path.join(path, file), "x\n")
        if age_days:
            then = time.time() - age_days * DAY
            os.utime(path, (then, then))
        return path

    def draft(self, state="waiting", approved=True):
        """A draft in `state` with the keeper's folder and, by default, the approved copy; returns (id, folder name)."""
        path = self.folder()
        with self.store() as db:
            ident = db.add_draft(5, ["Results"], path, "a" * 64, "b" * 64, state="refused" if state == "refused" else "waiting")
            if state == "later":
                db.set_draft(ident, "later", until=time.time() + 7 * DAY)
            elif state not in ("waiting", "refused"):
                db.set_draft(ident, state, reason="stale" if state == "stale" else "")
        if approved:
            self.folder(ident + "-approved")
        return ident, os.path.basename(path)

    def present(self):
        return sorted(os.listdir(self.keeper))

    def clean(self, now=None):
        with self.store() as db:
            return jobs.clean_keeper(REPO, db, time.time() if now is None else now)

    def draft_state(self, ident):
        with self.store() as db:
            return db.draft(ident)["state"]


class KeeperFolders(KeeperHelpers, unittest.TestCase):
    """The state and the index's folders in a folder of this test's own, with no record."""

    def setUp(self):
        os.makedirs(os.path.join(ROOT, ".tmp"), exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="cleanup-", dir=os.path.join(ROOT, ".tmp"))
        self.addCleanup(temporary.cleanup)
        self.dir = temporary.name
        environment = mock.patch.dict(os.environ, {"XDG_STATE_HOME": os.path.join(self.dir, "state"),
                                                   "XDG_CACHE_HOME": os.path.join(self.dir, "cache"),
                                                   "XDG_CONFIG_HOME": os.path.join(self.dir, "config")}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.start_keeper()

    def store(self):
        return store_module.Store(REPO, config={})


class CleanupTests(KeeperFolders):
    def test_the_keeper_names_its_folders_as_the_cleanup_reads_them(self):
        self.assertEqual("keeper_failed", observer_keeper.FAILED)
        self.assertEqual(jobs.KEEPER_FAILED, observer_keeper.FAILED)
        self.assertRegex(self.token(), jobs.KEEPER_NAME)
        self.assertRegex("0123abcd-approved", jobs.KEEPER_NAME)
        import secrets
        self.assertRegex(secrets.token_hex(8), jobs.KEEPER_NAME)

    def test_an_applied_dropped_stale_or_refused_draft_loses_its_folder_and_its_approved_copy(self):
        for state in ("applied", "dropped", "stale", "refused"):
            self.draft(state)
        self.assertEqual(8, len(self.present()))
        self.assertEqual(("ok", 8), self.clean())
        self.assertEqual([], self.present())
        self.assertEqual(("nothing", 0), self.clean())

    def test_a_draft_that_waits_or_is_put_off_keeps_its_folder_and_its_approved_copy(self):
        kept = [self.draft("waiting"), self.draft("later")]
        self.draft("applied")
        self.assertEqual(("ok", 2), self.clean())
        self.assertEqual(sorted([name for _, name in kept] + [ident + "-approved" for ident, _ in kept]), self.present())
        self.assertEqual(["waiting", "later"], [self.draft_state(ident) for ident, _ in kept])

    def test_fourteen_days_after_the_last_change_a_folder_goes_whatever_the_state(self):
        (ident, name), (later, other) = self.draft("waiting"), self.draft("later")
        self.assertEqual(("nothing", 0), self.clean(time.time() + 13 * DAY))
        self.assertEqual(4, len(self.present()))
        self.assertEqual(("ok", 4), self.clean(time.time() + 15 * DAY))
        self.assertEqual([], self.present())
        # the files being gone, a draft that still waited can no longer be approved: it is stale, and the topic is free
        self.assertEqual(["stale", "stale"], [self.draft_state(ident), self.draft_state(later)])

    def test_the_last_change_is_the_newest_of_the_draft_and_the_folder(self):
        ident, name = self.draft("waiting", approved=False)
        with self.store() as db:
            db.db.execute("UPDATE drafts SET updated=?", (time.time() - 20 * DAY,))
        then = time.time() - 3 * DAY   # the folder was written 3 days ago
        os.utime(os.path.join(self.keeper, name), (then, then))
        self.assertEqual(("nothing", 0), self.clean())
        then = time.time() - 20 * DAY
        os.utime(os.path.join(self.keeper, name), (then, then))
        self.assertEqual(("ok", 1), self.clean())

    def test_a_folder_with_no_draft_goes_after_fourteen_days(self):
        fresh, old = self.folder(), self.folder(age_days=15)
        approved = self.folder("0123abcd-approved", age_days=15)   # the copy of a draft the store never held
        self.assertEqual(("ok", 2), self.clean())
        self.assertEqual([os.path.basename(fresh)], self.present())
        self.assertFalse(os.path.exists(old) or os.path.exists(approved))

    def test_a_folder_with_no_draft_goes_at_once_after_the_keeper_recorded_a_failed_attempt(self):
        path = self.folder()
        with self.store() as db:
            db.set_control("keeper_failed", {"5": {"key": "a" * 64, "at": time.time() - 3 * DAY}})   # before the folder: another try
        self.assertEqual(("nothing", 0), self.clean())
        with self.store() as db:
            db.set_control("keeper_failed", {"5": {"key": "a" * 64, "at": time.time() + 60}})   # after it: its attempt failed
        self.assertEqual(("ok", 1), self.clean())
        self.assertFalse(os.path.exists(path))
        with self.store() as db:
            for bad in ("x", [1], {"5": 3}, {"5": {"at": "later"}}, {"5": {"at": 10 ** 15}}):
                db.set_control("keeper_failed", bad)
                self.assertEqual(0, jobs._keeper_failed_at(db), bad)

    def test_a_folder_whose_name_is_not_the_keepers_is_left_alone(self):
        names = ("notes", "ABCDEF0123456789", "0123456789abcde", "0123456789abcdef0", "0123456789abcdez", "abc-approved",
                 "0123abcd-approved-2", "0123ABCD-approved", ".0123456789abcdef", "0123456789abcdef.old")
        for name in names:
            self.folder(name, age_days=40)
        with self.store() as db:
            ident = db.add_draft(5, ["Results"], os.path.join(self.keeper, "notes"), "a" * 64, state="refused")
            db.set_draft(ident, "applied")
        self.assertEqual(("nothing", 0), self.clean())
        self.assertEqual(sorted(names), self.present())

    def test_a_link_inside_a_folder_is_left_alone_with_its_target_and_the_rest_go_on(self):
        outside = os.path.join(self.dir, "target")
        os.mkdir(outside, 0o700)
        keep = os.path.join(outside, "mine.txt")
        storage.write_file(keep, "mine\n")
        ident, name = self.draft("applied", approved=False)
        os.symlink(keep, os.path.join(self.keeper, name, "evil"))
        good, _ = self.draft("applied", approved=False)
        self.assertEqual(("failed", 1), self.clean())
        self.assertEqual(["base.md", "draft.md", "evil", "used.json"], sorted(os.listdir(os.path.join(self.keeper, name))))
        self.assertEqual("mine\n", slurp(keep))
        self.assertEqual(1, len(self.present()))

    def test_a_link_named_like_a_folder_is_not_followed_and_stays(self):
        outside = os.path.join(self.dir, "target")
        os.mkdir(outside, 0o700)
        keep = os.path.join(outside, "mine.txt")
        storage.write_file(keep, "mine\n")
        name = self.token()
        os.symlink(outside, os.path.join(self.keeper, name))
        with self.store() as db:
            ident = db.add_draft(5, ["Results"], os.path.join(self.keeper, name), "a" * 64, state="refused")   # final: it is due
        self.assertEqual(("failed", 0), self.clean())
        self.assertTrue(os.path.islink(os.path.join(self.keeper, name)))
        self.assertEqual(["mine.txt"], os.listdir(outside))
        self.assertEqual("mine\n", slurp(keep))

    def test_a_keeper_folder_that_is_itself_a_link_removes_nothing(self):
        self.draft("applied")
        moved = os.path.join(os.path.dirname(self.keeper), "keeper-moved")
        os.rename(self.keeper, moved)
        os.symlink(moved, self.keeper)
        self.assertEqual(("failed", 0), self.clean())
        self.assertEqual(2, len(os.listdir(moved)))   # both folders of the draft are still there

    def test_nothing_outside_the_keepers_folder_is_ever_touched(self):
        sibling = storage.cache_folder(REPO, "write", "5")
        storage.write_file(os.path.join(sibling, "%s.json" % ("0" * 64)), "{}\n")
        texts = storage.cache_folder(REPO, "texts", "5")
        storage.write_file(os.path.join(texts, "text.md"), "x\n")
        far = os.path.join(self.dir, "elsewhere", "0123456789abcdef")   # a stored folder that is not under keeper/
        os.makedirs(far, 0o700)
        storage.write_file(os.path.join(far, "draft.md"), "x\n")
        with self.store() as db:
            for where in (far, sibling, texts, self.dir, "/"):
                db.set_draft(db.add_draft(5, ["Results"], where, "a" * 64, state="refused"), "applied")
        self.assertEqual(("nothing", 0), self.clean(time.time() + 40 * DAY))
        for path in (os.path.join(far, "draft.md"), os.path.join(sibling, "%s.json" % ("0" * 64)),
                     os.path.join(texts, "text.md")):
            self.assertTrue(os.path.isfile(path), path)

    def test_a_failed_removal_is_said_and_the_rest_go_on(self):
        first, second = self.draft("applied", approved=False), self.draft("dropped", approved=False)
        real = storage.remove_folder

        def refuse_the_first(parent, name):
            if name == first[1]:
                raise OSError("the disk said no")
            return real(parent, name)
        with mock.patch.object(jobs.storage, "remove_folder", side_effect=refuse_the_first):
            self.assertEqual(("failed", 1), self.clean())
        self.assertEqual([first[1]], self.present())
        self.assertEqual(("ok", 1), self.clean())

    def test_a_failure_to_read_the_drafts_is_a_failed_step_and_nothing_goes(self):
        self.draft("applied")
        with mock.patch.object(store_module.Store, "drafts", side_effect=store_module.StoreError("corrupt")):
            with self.store() as db:
                self.assertEqual(("failed", 0), jobs._guard(jobs.clean_keeper, REPO, db, time.time()))
        self.assertEqual(2, len(self.present()))


class CleanupRunTests(KeeperHelpers, tjj.JobsBox):
    """Where the cleanup runs: the daily housekeeping, under the gates the jobs have. The observer is approved at the level
    Everything in automatic mode."""

    def setUp(self):
        super().setUp()
        self.start_keeper()

    def store(self):
        return store_module.Store(REPO, config=self.pcfg)

    def last(self):
        with self.store() as db:
            return db.get_control(jobs.JOBS_LAST)

    def test_the_daily_run_cleans_and_says_how_many_in_jobs_last(self):
        self.draft("applied")
        steps = self.jobs(posted=False, now=2000000.0)
        self.assertEqual("ok", steps["keeper"])
        self.assertEqual([], self.present())
        found = self.last()
        self.assertEqual(2, found["cleaned"])
        self.assertIs(int, type(found["cleaned"]))
        self.assertEqual("ok", found["steps"]["keeper"])

    def test_a_pass_that_posted_does_not_clean_and_the_next_day_does(self):
        self.jobs(posted=False, now=2000000.0)
        self.draft("applied")
        steps = self.jobs(posted=True, now=2000000.0 + 100)
        self.assertEqual("skipped", steps["keeper"])
        self.assertEqual(0, self.last()["cleaned"])
        self.assertEqual(2, len(self.present()))
        steps = self.jobs(posted=False, now=2000000.0 + DAY + 5)
        self.assertEqual("ok", steps["keeper"])
        self.assertEqual(2, self.last()["cleaned"])
        self.assertEqual([], self.present())

    def test_notes_only_still_cleans_its_own_files_and_changes_nothing_on_github(self):
        self.draft("applied")
        steps = self.jobs(level="notes")
        self.assertEqual("level", steps["render"])
        self.assertEqual("ok", steps["keeper"])
        self.assertEqual([], self.present())
        self.assertEqual([], self.writes())
        self.assertEqual([], self.acts())   # no act is made for it

    def test_shadow_mode_and_no_level_clean_nothing(self):
        self.draft("applied")
        self.assertEqual({"code": "shadow"}, self.jobs(mode="shadow"))
        self.use()
        options = dict(tjj.runtime.settings(self.pcfg), level="")
        with self.store() as db:
            self.assertEqual({"code": "level"}, jobs.run(REPO, self.pcfg, db, options, posted=True))
        self.assertEqual(2, len(self.present()))

    def test_a_step_with_no_time_left_waits_for_the_next_day(self):
        self.draft("applied")
        clock = mock.Mock()
        clock.remaining.return_value = 0
        self.use()
        with self.store() as db:
            steps = jobs.run(REPO, self.pcfg, db, tjj.runtime.settings(self.pcfg), refresh=None, posted=False, clock=clock)
        self.assertEqual("time", steps["keeper"])
        self.assertEqual(2, len(self.present()))

    def test_a_failure_of_the_cleanup_stops_only_itself(self):
        self.draft("applied")
        with mock.patch.object(jobs, "clean_keeper", side_effect=RuntimeError("boom")):
            steps = self.jobs(posted=False, now=2000000.0)
        self.assertEqual("failed", steps["keeper"])
        self.assertEqual("ok", steps["render"])
        self.assertEqual(0, self.last()["cleaned"])
        self.assertEqual(2, len(self.present()))

    def run_worker(self):
        import observer_worker
        with mock.patch.object(observer_worker.observer_keeper, "consider", return_value={"code": "x"}):
            return observer_worker.run_once(REPO, self.pcfg, project_root=self.project, settle_first=False)

    def test_the_worker_cleans_in_automatic_mode_and_not_when_publication_is_paused_or_in_shadow_mode(self):
        self.use()
        self.draft("applied")
        with self.store() as db:
            db.set_control("publication_paused", True)
        self.run_worker()
        self.assertEqual(2, len(self.present()))
        self.use(mode="shadow")
        with self.store() as db:
            db.set_control("publication_paused", False)
        self.run_worker()
        self.assertEqual(2, len(self.present()))
        self.use()
        self.run_worker()
        self.assertEqual([], self.present())
        self.assertEqual(2, self.last()["cleaned"])


class RealKeeperFolderTests(tk.KeeperBox):
    """The folders the keeper and `observer.py drafts` really make (write.py start's files, the approved copy), not copies of
    their names: the cleanup takes them away whole."""

    def draft_waiting(self):
        self.consider()
        self.assertEqual("drafted", self.consider(now=self.t0 + 31 * 60)["code"])
        (found,) = self.kept()
        return found

    def test_a_real_draft_folder_and_its_approved_copy_go_when_the_draft_is_dropped_and_not_before(self):
        import observer_drafts
        found = self.draft_waiting()
        self.assertEqual(0o700, os.stat(found["folder"]).st_mode & 0o777)
        with self.open_store() as store:
            copy, _ = observer_drafts.approved_copy(store, self.repo_name, found)
            self.assertTrue(os.path.isfile(os.path.join(copy, "draft.md")))
            self.assertEqual(("nothing", 0), jobs.clean_keeper(self.repo_name, store, time.time()))
            self.assertTrue(os.path.isdir(found["folder"]) and os.path.isdir(copy))
            store.set_draft(found["id"], "dropped", reason="dropped")
            self.assertEqual(("ok", 2), jobs.clean_keeper(self.repo_name, store, time.time()))
        self.assertFalse(os.path.lexists(found["folder"]) or os.path.lexists(copy))
        self.assertEqual([], os.listdir(storage.cache_folder(self.repo_name, "keeper", create=False)))

    def test_the_folder_of_a_draft_the_checks_refused_goes_at_once(self):
        self.verdict = ("numbers", False)
        self.consider()
        self.assertEqual("refused", self.consider(now=self.t0 + 31 * 60)["code"])
        (found,) = self.kept()
        self.assertEqual("refused", found["state"])
        self.assertTrue(os.path.isdir(found["folder"]))
        with self.open_store() as store:
            self.assertEqual(("ok", 1), jobs.clean_keeper(self.repo_name, store, time.time()))
        self.assertFalse(os.path.lexists(found["folder"]))


if __name__ == "__main__":
    unittest.main()
