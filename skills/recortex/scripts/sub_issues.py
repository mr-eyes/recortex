#!/usr/bin/env python3
"""Show the record's structure as GitHub sub-issues: the hub's topics under the hub, each `active` task under its
destination topic.

The links are computed from the record (the hub's tables, a task's "Destination topic"), never believed from GitHub:
a task that already has another parent, or a topic someone moved by hand, is reported and left alone. The writing
needs only the `repo` scope that gh already has. Nothing is removed and no issue is edited.

plan     the links the record wants, and which are missing or differ on GitHub (reads GitHub; writes nothing).
apply    adds each missing link, then reads every one back through Issue.parent. --dry-run prints the exact commands.

create_issues.py calls link() for a topic or task it creates.

Examples:
  sub_issues.py plan
  sub_issues.py apply --dry-run
  sub_issues.py apply
"""

import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_api  # noqa: E402
import index  # noqa: E402
import recortex  # noqa: E402

BATCH = 40
UNREAD = "sub-issues: GitHub could not be read just now; run the command again."
REFUSED = ("sub-issues: GitHub refused the link (an older GitHub Enterprise Server, or more than 100 sub-issues "
           "under one parent); the hub's tables stay the structure.")
MENTION = re.compile(r"(?<![\w/&#])#([0-9]{1,7})\b")


def destination(db, number):
    """The topic a task points at: the first #N of its "Destination topic" section that is an issue labeled
    final-record; else the only final-record issue that shares the task's topic: label; else None."""
    row = db.execute("SELECT text FROM sections WHERE issue = ? AND heading = 'Destination topic' ORDER BY ordinal",
                     (number,)).fetchone()
    topics = {r["issue"] for r in db.execute("SELECT issue FROM labels WHERE lower(label) = 'final-record'")}
    for match in MENTION.finditer(row["text"] if row else ""):
        if int(match.group(1)) in topics:
            return int(match.group(1))
    found = index.topic_issues(db, number)
    return found[0] if len(found) == 1 else None


def wanted(db, hub):
    """[(parent, child)] the record wants, hub to topics first, then topic to tasks."""
    links = []
    labelled = {}
    for row in db.execute("SELECT issue, lower(label) AS label FROM labels"):
        labelled.setdefault(row["label"], set()).add(row["issue"])
    for topic in sorted(labelled.get("final-record", ())):
        if hub and topic != hub:
            links.append((hub, topic))
    for task in sorted(labelled.get("active", ())):
        topic = destination(db, task)
        if topic is not None and topic != task:
            links.append((topic, task))
    return links


def read_parents(repo, numbers, timeout=60):
    """({number: node id}, {number: parent number or None}) read from GitHub in batches."""
    owner, name = repo.split("/")
    ids, parents = {}, {}
    numbers = sorted(set(numbers))
    for start in range(0, len(numbers), BATCH):
        part = numbers[start:start + BATCH]
        aliases = " ".join("i%d: issue(number: %d) { id parent { number repository { nameWithOwner } } }" % (n, n)
                           for n in part)
        data = board_api.graphql("query { repository(owner: %s, name: %s) { %s } }"
                                 % (board_api.quote(owner), board_api.quote(name), aliases), timeout)
        found = data.get("repository") or {}
        for number in part:
            node = found.get("i%d" % number)
            if not isinstance(node, dict) or not isinstance(node.get("id"), str) \
                    or not board_api.GH_ID.fullmatch(node["id"]):
                continue
            ids[number] = node["id"]
            parent = node.get("parent")
            same = isinstance(parent, dict) and type(parent.get("number")) is int and \
                str((parent.get("repository") or {}).get("nameWithOwner", "")).lower() == repo.lower()
            parents[number] = parent["number"] if same else (-1 if isinstance(parent, dict) else None)
    return ids, parents


def add_text(parent_id, child_id):
    return ("mutation { addSubIssue(input: {issueId: %s, subIssueId: %s}) { issue { id } } }"
            % (board_api.quote(board_api.ident(parent_id)), board_api.quote(board_api.ident(child_id))))


def link(repo, parent, child, timeout=60):
    """Make `child` a sub-issue of `parent`. "linked", "already", "other" (it has another parent, left alone) or
    "refused". Never raises for a refusal; a failed call is "refused" too, after one fixed line."""
    try:
        ids, parents = read_parents(repo, [parent, child], timeout)
        if parent not in ids or child not in ids:
            return "refused"
        if parents.get(child) == parent:
            return "already"
        if parents.get(child) is not None:
            return "other"
        board_api.graphql(add_text(ids[parent], ids[child]), timeout)
        return "linked" if read_parents(repo, [child], timeout)[1].get(child) == parent else "refused"
    except board_api.BoardError:
        return "refused"


def difference(repo, links):
    """(rows, ids): each row is (parent, child, state) with state ok, missing, other or unknown."""
    numbers = {n for pair in links for n in pair}
    ids, parents = read_parents(repo, numbers)
    rows = []
    for parent, child in links:
        if child not in ids or parent not in ids:
            rows.append((parent, child, "unknown"))
        elif parents.get(child) == parent:
            rows.append((parent, child, "ok"))
        elif parents.get(child) is None:
            rows.append((parent, child, "missing"))
        else:
            rows.append((parent, child, "other"))
    return rows, ids


def show(rows):
    words = {"missing": "is not linked yet", "other": "has another parent on GitHub (left alone)",
             "unknown": "was not found on GitHub"}
    for parent, child, state in rows:
        if state != "ok":
            print("#%d under #%d: %s" % (child, parent, words[state]))
    done = sum(1 for r in rows if r[2] == "ok")
    print("sub-issues: %d of %d link(s) are in place" % (done, len(rows)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "apply"):
        p = sub.add_parser(name)
        p.add_argument("--repo", help="owner/name (default: from the project file)")
        p.add_argument("--hub", type=int, help="hub issue number (default: from the project file)")
        if name == "apply":
            p.add_argument("--dry-run", action="store_true", help="print the exact commands; write nothing")
            p.add_argument("--pause", type=float, default=1.0, help="seconds between writes")
    args = parser.parse_args(argv)
    config = recortex.load_config()
    repo = recortex.resolve_repo(args.repo, config)
    hub = recortex.resolve_hub(args.hub, config)
    print(recortex.describe(config, repo))
    db = index.connect(repo, create=False)
    if db is None:
        sys.exit("error: no local index yet; run index.py sync first")
    try:
        links = wanted(db, hub)
    finally:
        db.close()
    if not links:
        print("sub-issues: the record names no topic or task to link")
        return 0
    try:
        rows, ids = difference(repo, links)
    except board_api.BoardError:
        print(UNREAD)
        return 1
    show(rows)
    if args.command == "plan":
        return 0
    todo = [(p, c) for p, c, state in rows if state == "missing"]
    if args.dry_run:
        for parent, child in todo:
            print("would run: " + recortex.show_command(["api", "graphql", "-f", "query=" + add_text(ids[parent],
                                                                                                      ids[child])]))
        print("dry run: nothing was written")
        return 0
    failed = 0
    for parent, child in todo:
        try:
            board_api.graphql(add_text(ids[parent], ids[child]))
        except board_api.BoardError:
            print(REFUSED)
            return 1
        if args.pause > 0:
            time.sleep(args.pause)
    if todo:
        _, parents = read_parents(repo, [c for _, c in todo])
        for parent, child in todo:
            if parents.get(child) == parent:
                print("linked: #%d is under #%d" % (child, parent))
            else:
                print("MISMATCH: #%d does not read back under #%d" % (child, parent))
                failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
