#!/usr/bin/env python3
"""Where the project stands, from the local index alone (read-only, no network, no model call).

It prints, under the data header and in this order:
  In short   the first field of the newest resume block the index holds.
  Tasks      the open tasks, how many of their steps are done and the next open step of each.
  Text       what is due in the paper's text (only when the project has a plan), as write.py status says it.
  Waiting    what waits for the user's yes: observer.py pending --waiting when the observer is on here, else one line.
`status.py --waiting` prints only the last part.

Everything from the record goes through index.clean(), as for the query commands. Nothing is written, posted or asked.

Examples:
  status.py
  status.py --waiting
"""

import argparse
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guidance
import index  # noqa: E402
import observer_runtime as runtime  # noqa: E402
import recortex  # noqa: E402
import record_format  # noqa: E402
import storage  # noqa: E402
import task_rules  # noqa: E402
import write_rules  # noqa: E402
import writing  # noqa: E402

MAX_TASKS = 10
LINE_CAP = 600
NO_RESUME = "In short: the index holds no resume block that an allowed account wrote."
NO_TASKS = "Tasks: none are open."
NO_PLAN = "Text: this project has no plan of a paper, so nothing is due in a text."
WAITING_OFF = "Waiting: nothing waits for a yes here, because the observer is off for this folder."
WAITING_UNREADABLE = ("Waiting: this machine's observer settings cannot be read, so the list is not available; run "
                      "hooks.py --doctor.")
WAITING_UNAVAILABLE = "Waiting: the list is not available; run observer.py pending --waiting in your terminal."
WAITING_NONE = "Waiting: nothing waits for a yes."


def say(text=""):
    print(text)


def one(value, limit=LINE_CAP):
    return index.clean(value, limit)


def open_index(repo):
    try:
        db = index.connect(repo, create=False)
    except storage.StorageError as error:
        sys.exit("error: %s" % error)
    if db is None:
        sys.exit("error: no local index yet; run index.py sync first")
    if not index.derived_current(db):
        sys.exit(index.NOT_READY)
    return db


def in_short(db):
    """Lines: the "In short" of the newest cached resume block, or the fixed line."""
    block = index.get_meta(db, "resume") or ""
    text = record_format.resume_in_short(block)
    if not text:
        return [NO_RESUME]
    return ["In short:"] + ["  " + one(paragraph, 1300) for paragraph in text.split("\n\n")[:2]]


def task_lines(db, allowed):
    """Lines: the open tasks (the standing to-do list last) and the next open step of each."""
    report = index.tasks(db, allowed)
    live = [task for task in report["tasks"] if task["state"] == "open"]
    if not live:
        return [NO_TASKS]
    live.sort(key=lambda task: (task["todo"], task["issue"]))
    out = ["Tasks:"]
    for task in live[:MAX_TASKS]:
        counts = task["counts"]
        title = one(task["title"], 100) if task["title_told"] else "(title not told)"
        out.append("  #%d %s%s: %d of %d steps done%s" % (
            task["issue"], title, " (the standing to-do list)" if task["todo"] else "", counts["done"], counts["steps"],
            "; the list in the body was changed by hand" if task["hand"] else ""))
        step = task_rules.next_open(task["steps"])
        if step is not None:
            out.append("    next: step %d, %s" % (step["n"], one(step["text"], 200)))
    if len(live) > MAX_TASKS:
        out.append("  and %d more open tasks" % (len(live) - MAX_TASKS))
    return out


def text_lines(db, config, repo, allowed):
    """Lines: the writing mode and what is due, only with a plan."""
    plan = recortex.plan_profile(recortex.shipped_profiles(config.get("profiles")))
    if plan is None:
        return [NO_PLAN]
    state = index.planned(db, config, allowed, plan)
    out = ["Text: " + writing.sentence(repo)]
    due = write_rules.due_parts(db, config, allowed, state)
    for entry in due:
        out.append("  due: #%d %s (%s)" % (entry["topic"], one(entry["heading"], 60), entry["why"]))
    items = state["found"]["items"] if state else index.memories(db, allowed)["items"]
    for issue, cid in sorted(write_rules.open_proposals(db, items, allowed).items()):
        out.append("  proposal waiting on GitHub: #%d (comment %d)" % (issue, cid))
    if len(out) == 1:
        out.append("  nothing is due")
    return out


def waiting_output(argv):
    """(exit code, text) of observer.main(argv), run in this process with its output kept; (1, "") when it cannot run."""
    import observer
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
            code = observer.main(argv)
    except SystemExit as error:
        code = error.code if isinstance(error.code, int) else 1
    except Exception:  # noqa: BLE001 - one fixed line, never a traceback
        code = 1
    return code, buffer.getvalue()


def waiting_lines(config, repo):
    """Lines: what waits for a yes. Off, paused or unreadable settings are one fixed line each; a model is never called."""
    try:
        local = runtime.local_config(config)
        if runtime.paused(repo, config):
            return ["Waiting: " + runtime.PAUSED]
        if not runtime.approval_valid(repo, local):
            return [WAITING_OFF]
    except (ValueError, OSError, storage.StorageError):
        return [WAITING_UNREADABLE]
    code, text = waiting_output(["pending", "--waiting"])
    lines = [one(line, LINE_CAP) for line in text.splitlines() if line.strip()]
    if code != 0 or not lines:
        return [WAITING_UNAVAILABLE] if code != 0 else [WAITING_NONE]
    return ["Waiting:"] + ["  " + line for line in lines]


def load(args):
    try:
        config = recortex.load_config()
    except (OSError, ValueError) as error:
        sys.exit("error: the configuration file cannot be read (%s)" % error)
    repo = recortex.resolve_repo(args.repo, config)
    allowed = recortex.allowed_users(repo)
    if not allowed:
        sys.exit("error: %s is not in your trusted repositories (%s); the status is told only from the accounts you "
                 "allow" % (repo, recortex.trusted_target()))
    return config, repo, allowed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", help="owner/name (default: from the project file)")
    parser.add_argument("--waiting", action="store_true", help="print only what waits for the user's yes")
    args = parser.parse_args(argv)
    config, repo, allowed = load(args)
    say(recortex.describe(config, repo))
    say(index.DATA_HEADER)
    if args.waiting:
        for line in waiting_lines(config, repo):
            say(line)
        return 0
    db = open_index(repo)
    try:
        say("Index: the local copy is %s." % index.age_text(index.get_meta(db, "synced_at")))
        judgment = guidance.return_pointer(db, repo, allowed)
        if judgment:
            say(judgment)
        for part in (in_short(db), task_lines(db, allowed), text_lines(db, config, repo, allowed)):
            for line in part:
                say(line)
    finally:
        db.close()
    for line in waiting_lines(config, repo):
        say(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
