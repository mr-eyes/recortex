#!/usr/bin/env python3
"""What a task and its steps are, from told memories alone.

Pure: no database, no network, no clock. index.py reads the record and hands the facts to these functions; tasks.py
writes what they compute. The rules are references/issue-model.md, "Tasks, steps and marks":

- A task's steps are numbered 1 to N across its standing `steps` memories, oldest first (`numbered`). A steps memory
  that restates another (replaces=) takes the place of the oldest memory in its chain, so the numbers of the lists
  after it do not move. One that is replaced gives no steps.
- A step is, by the first rule that applies: dropped (a told decision marks it `dropped=`), done (a told memory marks
  it `done=`), blocked (a failure names the task and nothing followed: the first step still open), else open
  (`assess`). A mark counts only when the memory that carries it is told (it is among `items`), is of a kind that may
  carry that key, is not reconstructed, and is neither replaced nor set aside, and the steps memory it points at is
  standing and has that step.
- A task's body shows its steps as a task list under `## Plan` (`plan_section`), written by code and never believed the
  other way round: a box ticked on GitHub is not a mark. The line `<!-- ghim:list sha256 ... -->` after the list is the
  plugin's own mark of what it last wrote, so that a list changed by hand is told from a list that is only behind
  (`hand_changed`).
"""

import hashlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import record_format  # noqa: E402

STATES = ("done", "dropped", "blocked", "open")
LIST_MARK = "<!-- ghim:list sha256 %s -->"
LIST_MARK_LINE = re.compile(r"^<!-- ghim:list sha256 ([0-9a-f]{64}) -->$")
ITEM = re.compile(r"^- \[( |x)\] ([0-9]{1,3})\. (.*)$")
NOT_FINISHED = "Not finished."
TODO_TITLE = "to do"
UNREPLACED = ("replaced", "set aside")   # states of a memory whose marks count for nothing


def rank(memory, by_id, seen=()):
    """The place of a steps memory in the numbering: (time, id) of the oldest memory in the chain that it restates."""
    own = (memory["time"], memory["comment"])
    found = [own]
    for target in memory.get("replaces", ()):
        if target in by_id and target != memory["comment"] and target not in seen:
            found.append(rank(by_id[target], by_id, seen + (memory["comment"],)))
    return min(found)


def numbered(memories):
    """The steps of one task as [{"n", "comment", "ordinal", "text"}], numbered from 1.

    memories: the told steps memories of the task, each {"comment", "time" (sortable), "texts" (its steps in order),
    "replaces" (ids of steps memories it restates), "state" ("replaced" or anything else)}. A replaced memory gives
    no steps."""
    by_id = {m["comment"]: m for m in memories}
    standing = [m for m in memories if m.get("state", "stands") != "replaced"]
    standing.sort(key=lambda m: (rank(m, by_id), m["time"], m["comment"]))
    out = []
    for memory in standing:
        for ordinal, text in enumerate(memory["texts"], 1):
            out.append({"n": len(out) + 1, "comment": memory["comment"], "ordinal": ordinal, "text": text})
    return out


def assess(steps, marks, items, blocked=False):
    """The steps with their states: [{"n", "comment", "ordinal", "text", "state", "by"}], `by` being the id of the
    memory whose mark decides a done or dropped step (the oldest, when several mark it), else None.

    steps    numbered()
    marks    [(source memory id, key "done" or "dropped", steps comment id, step number inside it)], as stored
    items    {memory id: {"kind", "state", "time", "reconstructed"}}: the told memories, and only they; a mark by
             any other comment counts for nothing
    blocked  True when a failure names the task and nothing followed: the first step that is neither done nor
             dropped is blocked"""
    where = {(step["comment"], step["ordinal"]) for step in steps}
    decided = {}
    for source, key, target, number in sorted(
            marks, key=lambda row: ((items[row[0]]["time"] if row[0] in items else ""), row[0])):
        item = items.get(source)
        if item is None:
            continue   # not told: an account that is not allowed, or an edit that no allowed account made
        if item["kind"] not in record_format.MARK_RULES.get(key, ()) or item.get("reconstructed") \
                or item["state"] in UNREPLACED:
            continue
        if (target, number) in where:
            decided.setdefault((target, number), {})[key] = decided.get((target, number), {}).get(key, source)
    out, open_seen = [], False
    for step in steps:
        row = dict(step, state="open", by=None)
        marked = decided.get((step["comment"], step["ordinal"]), {})
        if "dropped" in marked:
            row.update(state="dropped", by=marked["dropped"])
        elif "done" in marked:
            row.update(state="done", by=marked["done"])
        elif blocked and not open_seen:
            row["state"] = "blocked"
        if row["state"] in ("open", "blocked"):
            open_seen = True
        out.append(row)
    return out


def counts(assessed):
    """{"steps" (not dropped), "done", "dropped", "open" (open and blocked)} of assessed steps."""
    states = [row["state"] for row in assessed]
    return {"steps": len(states) - states.count("dropped"), "done": states.count("done"),
            "dropped": states.count("dropped"), "open": states.count("open") + states.count("blocked")}


def finished(assessed):
    """True when every step is done or dropped and at least one is done: the task may be closed. A task with no steps
    is never finished."""
    states = {row["state"] for row in assessed}
    return bool(assessed) and states <= {"done", "dropped"} and "done" in states


def next_open(assessed):
    """The first step that is neither done nor dropped, or None."""
    return next((row for row in assessed if row["state"] in ("open", "blocked")), None)


def is_todo_title(title):
    """True for the title of the standing to-do task: "To do"."""
    return (title or "").strip().lower() == TODO_TITLE


# ---------------------------------------------------------------- the list under ## Plan

def clean_step(text):
    """A step's words as one line of the list: control characters out, runs of spaces made one, '<' written as an entity
    so that a step can never close the plugin's mark or open markup."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text or "")
    return re.sub(r" {2,}", " ", text).strip().replace("<", "&lt;")


def list_lines(assessed):
    """The task list of assessed steps: '- [ ] <n>. <step>', '- [x] <n>. <step>' for a done step, and
    '- [x] <n>. ~~<step>~~ (dropped)' for a dropped one, which GitHub counts as finished, as `finished` does."""
    out = []
    for row in assessed:
        text = clean_step(row["text"])
        if row["state"] == "dropped":
            out.append("- [x] %d. ~~%s~~ (dropped)" % (row["n"], text))
        else:
            out.append("- [%s] %d. %s" % ("x" if row["state"] == "done" else " ", row["n"], text))
    return out


def list_digest(lines):
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def plan_section(assessed):
    """The text under '## Plan': the task list, then the plugin's mark of it. Empty when there are no steps."""
    lines = list_lines(assessed)
    return "\n".join(lines + [LIST_MARK % list_digest(lines)]) if lines else ""


def plan_list(body):
    """(item lines, the hash the plugin's mark records or None, whether the body has a '## Plan' section) of a task's
    body. Item lines are the lines of the section that start with '- ['; line endings are read as LF."""
    text = (body or "").replace("\r\n", "\n")
    section = record_format.section_text(text, "Plan")
    if section is None:
        return [], None, False
    items, mark = [], None
    for line in section.split("\n"):
        line = line.rstrip()
        found = LIST_MARK_LINE.match(line)
        if found:
            mark = found.group(1)
        elif line.startswith("- ["):
            items.append(line)
    return items, mark, True


def hand_changed(body):
    """True when the list under '## Plan' is not what the plugin last wrote: its mark is there and the list's hash
    differs, or the list has a checked box and no mark (a list that code never wrote holds no checked box). A body with
    no '## Plan', or with no list and no mark, was not changed by hand."""
    items, mark, has = plan_list(body)
    if not has:
        return False
    if mark is not None:
        return list_digest(items) != mark
    return any(line.startswith("- [x]") for line in items)


def box_differences(body, assessed):
    """The numbers of the steps whose box in the body's list shows something other than the computed state: ticked in the
    body for a step that no told memory marks, or unticked for one that is done or dropped."""
    shown = {}
    for line in plan_list(body)[0]:
        found = ITEM.match(line)
        if found:
            shown[int(found.group(2))] = found.group(1) == "x"
    return [row["n"] for row in assessed
            if row["n"] in shown and shown[row["n"]] != (row["state"] in ("done", "dropped"))]


def list_agrees(body, assessed):
    """True when the list in the body is the one `plan_section` would write now."""
    items, mark, has = plan_list(body)
    lines = list_lines(assessed)
    return has and items == lines and mark == (list_digest(lines) if lines else None)


# ---------------------------------------------------------------- a new task's body, and the Result of a finished one

def task_body(goal, topic, done_when, assessed, opened):
    """The body of a new task (templates/active-task.md), made by code from the steps memory: its goal (the plain line
    as a sentence), the topic number, the Done when line and the steps, and the time it was opened."""
    destination = ("#%d: the result of this task is posted there as a dated comment when the task ends, and enters "
                   "that topic's body only once it is adopted." % topic) if topic else \
        "None: this is a standing list, not a task with a result for one topic."
    return "\n".join(["## Goal", "", sentence(goal), "", "## Destination topic", "", destination, "",
                      "## Done when", "", " ".join((done_when or "").split()), "", "## Plan", "",
                      plan_section(assessed), "", "## Result", "", "%s Opened %s." % (NOT_FINISHED, opened), ""])


def sentence(plain):
    """A plain line ('let the reader carry ...') as a sentence: first letter up, a period at the end."""
    text = " ".join((plain or "").split())
    return (text[:1].upper() + text[1:] + ("" if text.endswith(".") else ".")) if text else "None."


def result_section(rows, stamp):
    """The 'Result' of a finished task: one line per memory that marked a step, its plain line and its address.
    rows: [(plain line, address)] oldest first."""
    lines = ["Finished %s." % stamp, ""]
    seen = set()
    for plain, address in rows:
        if address in seen:
            continue
        seen.add(address)
        lines.append("- %s (%s)" % (" ".join((plain or "a memory without a plain line").split()), address))
    return "\n".join(lines)
