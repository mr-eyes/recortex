"""What is due to be written, and what a text change was: the rules of section 7.2, read from the index.

Python 3 standard library only. No network. index.plan_counts, write.py and the mind page use these functions, so the
rules exist once. A part of the text is a topic issue and one of the profile's `write` headings. It is due when:

  claim        a backed claim, or a standing discussion part whose every linked claim is backed or in the text, names
               the topic, and no told text change newer than it links it;
  changed      a memory that a told text change links was replaced, corrected or set aside after that change, and no
               told text change newer than that one, for the same topic and heading, links what replaced it (or what
               replaced that in turn) without a restore of that topic and heading made after it;
  methods      under a `write_from_record` heading: a told standing decision on the topic's thread, or a standing result
               that a backed claim links, is newer than the topic's newest told text change or checkpoint and is linked
               by no told text change.

Never due, and never material: a part that is planned, in doubt, moved, waiting, dropped or revised; a comparison that
names no paper; a result that does not stand; a topic whose body an outside account wrote or changed.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import index  # noqa: E402
import recortex  # noqa: E402
import record_format  # noqa: E402

DISCUSSION = ("interpretation", "limitation", "comparison", "implication")


def text_memories(db, items):
    """The told text changes, oldest first, as dicts: id, issue, time, at, sections (the headings), form (the key of the
    Approved form or None), before, after (SHA-256 values or None), version (40 hex, "" or the reason), links (the
    counted because targets). From the index alone; an entry that does not fit its form keeps what it can."""
    out = []
    for cid, memory in items.items():
        if memory["kind"] != "text":
            continue
        sections = [part.strip() for part in index.event_field(db, cid, "Section").split(",") if part.strip()]
        pair = record_format.sha_pair(index.event_field(db, cid, "Body"))
        version = index.event_field(db, cid, "Version")
        commit = re.fullmatch(r"`([0-9a-f]{40})`", version)
        out.append({"id": cid, "issue": memory["issue"], "time": memory["time"], "at": memory["at"], "sections": sections,
                    "form": record_format.approved_form(index.event_field(db, cid, "Approved")),
                    "before": pair[0] if pair else None, "after": pair[1] if pair else None,
                    "version": commit.group(1) if commit else "", "plain": memory["plain"] or "",
                    "links": [target for key, target in memory["links"] if key == "because"]})
    return sorted(out, key=lambda row: (row["time"], row["id"]))


def newest_text(texts, issue):
    found = [row for row in texts if row["issue"] == issue]
    return found[-1] if found else None


def heading_labels(config, plan):
    """{heading lower: [labels]} of the plan's sections in this project."""
    profiles = recortex.shipped_profiles((config or {}).get("profiles"))
    headings = {h.lower() for h in plan["sections"]}
    found = {}
    for label, heading in recortex.section_labels(profiles):
        if heading.lower() in headings:
            found.setdefault(heading.lower(), []).append(label.lower())
    return found


def has_citation(db, cid):
    words = " ".join(index.event_field(db, cid, label) for label in ("Statement", "It needs", "We have"))
    return bool(record_format.citations_manubot(words) or re.search(r"10\.[0-9]{4,9}/", words)
                or record_format.URL.search(words))


def due_parts(db, config, allowed, state):
    """[{"topic", "heading", "why", "parts", "results", "decisions"}] for the parts of the text that are due; [] without a
    plan. A topic is due under a heading only when it carries that heading's label and its body is told."""
    if state is None:
        return []
    plan, found, parts = state["plan"], state["found"], state["parts"]
    items = found["items"]
    labels = heading_labels(config, plan)
    told = index.told_bodies(db, allowed)
    texts = text_memories(db, items)
    write = {h.lower(): h for h in plan["write"]}
    linked_by_text = {target for row in texts for target in row["links"]}
    out = {}

    def topic_ok(topic, heading):
        have = {label.lower() for label in index.issue_labels(db, topic)}
        return topic in told and bool(have & set(labels.get(heading.lower(), [])))

    def add(topic, heading, why, part=None, results=(), decisions=()):
        entry = out.setdefault((topic, heading), {"topic": topic, "heading": heading, "why": why, "parts": [],
                                                  "results": [], "decisions": []})
        for key, values in (("parts", [part] if part else []), ("results", results), ("decisions", decisions)):
            for value in values:
                if value not in entry[key]:
                    entry[key].append(value)

    for cid, found_part in sorted(parts.items()):
        if not found_part["standing"] or found_part["topic"] is None or found_part["section"].lower() not in write:
            continue
        heading, topic, kind = write[found_part["section"].lower()], found_part["topic"], found_part["part"]
        if heading in plan["write_from_record"] or not topic_ok(topic, heading):
            continue
        linked_claims = [t for key, t in items[cid]["links"] if key == "because" and t in parts
                         and parts[t]["part"] in plan["evidence"]]
        if kind in plan["evidence"] and found_part["state"] == "backed":
            add(topic, heading, "claim", cid, found_part["backs"])
        elif kind in DISCUSSION and found_part["state"] == "stands" and all(
                parts[t]["state"] in ("backed", "text") for t in linked_claims):
            if kind == "comparison" and not has_citation(db, cid):
                continue
            add(topic, heading, "claim", cid, [t for key, t in items[cid]["links"] if key == "because"
                                                and items.get(t, {}).get("kind") == "result"
                                                and items[t]["state"] == "stands"])
    pointing = {}
    for memory in items.values():
        for key, target in memory["links"]:
            pointing.setdefault(target, []).append((key, memory))

    def covered(row, heading, target):
        """True when a told text change newer than `row` for the same topic and heading links what replaced `target`, or what
        replaced that in turn, and no restore of that topic and heading was made after it: the text already says what the
        record now holds, so nothing is due for it. A restore puts an older text back, so a change before it no longer
        counts."""
        later, todo = set(), [target]
        while todo:
            for key, other in pointing.get(todo.pop(), []):
                if key in ("replaces", "instead") and other["id"] not in later:
                    later.add(other["id"])
                    todo.append(other["id"])
        here = heading.lower()
        for newer in texts:
            if newer["issue"] == row["issue"] and (newer["time"], newer["id"]) > (row["time"], row["id"]) \
                    and here in {name.lower() for name in newer["sections"]} and later & set(newer["links"]) \
                    and not any(back["form"] == "restored" and back["issue"] == row["issue"]
                                and here in {name.lower() for name in back["sections"]}
                                and (back["time"], back["id"]) > (newer["time"], newer["id"]) for back in texts):
                return True
        return False

    for row in texts:
        for target in row["links"]:
            moved = items.get(target)
            if moved is None or moved["state"] not in ("replaced", "set aside"):
                continue
            newer = [other for key, other in pointing.get(target, []) if key in ("replaces", "instead")
                     and other["time"] > row["time"] and other["state"] == "stands"]
            if not any(key in ("replaces", "instead") and other["time"] > row["time"] for key, other in pointing.get(target, [])):
                continue
            standing_parts = [t for t in row["links"] if t in parts and parts[t]["standing"]
                              and parts[t]["state"] in ("backed", "stands", "text")]
            for heading in row["sections"]:
                if heading.lower() in write and topic_ok(row["issue"], write[heading.lower()]) \
                        and write[heading.lower()] not in plan["write_from_record"] and not covered(row, heading, target):
                    add(row["issue"], write[heading.lower()], "changed", results=[o["id"] for o in newer
                                                                                if o["kind"] in ("result", "correction")],
                        decisions=[o["id"] for o in newer if o["kind"] == "decision"])
                    for cid in standing_parts + [o["id"] for o in newer if o["kind"] == "plan" and o["id"] in parts
                                                 and parts[o["id"]]["standing"]]:
                        add(row["issue"], write[heading.lower()], "changed", part=cid)
    for heading in plan["write_from_record"]:
        for topic in index.plan_topics(db, plan, config):
            if not topic_ok(topic, heading):
                continue
            thread = set(index.thread_of(db, topic)[1])
            anchor = max([row["time"] for row in texts if row["issue"] == topic]
                         + [m["time"] for m in items.values() if m["kind"] == "checkpoint" and m["issue"] in thread],
                         default="")
            backed_links = {t for found_part in parts.values() if found_part["standing"]
                            and found_part["state"] == "backed" for t in found_part["backs"]}
            decisions = [m["id"] for m in items.values() if m["kind"] == "decision" and m["state"] == "stands"
                         and m["issue"] in thread and m["time"] > anchor and m["id"] not in linked_by_text]
            results = [m["id"] for m in items.values() if m["kind"] == "result" and m["state"] == "stands"
                       and m["issue"] in thread and m["id"] in backed_links and m["time"] > anchor
                       and m["id"] not in linked_by_text]
            if decisions or results:
                add(topic, heading, "methods", results=results, decisions=decisions)
    return sorted(out.values(), key=lambda row: (row["topic"], row["heading"]))


CLOSED_STATE = re.compile(r"The author closed pull request ([1-9][0-9]{0,8}) without merging it, so the proposed change is "
                          r"dropped and the text is as it was\.")


def closed_pulls(db, allowed):
    """{pull request number} of the proposals that a told checkpoint says were closed without being merged: a checkpoint of an
    allowed account, never edited, whose State field is the fixed sentence that proposals.drop_proposal writes. Only the
    number is read from it, and only in that form; this is what makes a rejection final, so that it is recorded once and
    the proposal no longer counts as waiting."""
    edited = index.edited_comments(db)
    found = set()
    for row in db.execute("SELECT comment, author FROM events WHERE type = 'checkpoint'"):
        if (row["author"] or "").lower() not in allowed or row["comment"] in edited:
            continue
        match = CLOSED_STATE.fullmatch(index.event_field(db, row["comment"], "State").strip())
        if match:
            found.add(int(match.group(1)))
    return found


def open_proposals(db, items, allowed):
    """The topics with a proposal that waits on GitHub: the newest proposal comment of the topic by an allowed account
    that was never edited, with no told text change after it and whose pull request no told checkpoint says was closed
    without merging. {issue: comment id}."""
    edited = index.edited_comments(db)
    closed = closed_pulls(db, allowed)
    newest = {}
    for row in db.execute("SELECT comment, issue, at, author, created_at FROM events WHERE type = 'proposal'"):
        if (row["author"] or "").lower() not in allowed:
            continue
        key = (record_format.instant(row["at"], row["created_at"]), row["comment"])
        if row["issue"] not in newest or key > newest[row["issue"]][0]:
            newest[row["issue"]] = (key, row["comment"])
    texts = text_memories(db, items)
    waiting = {}
    for issue, (key, cid) in newest.items():
        if cid in edited:
            continue
        row = db.execute("SELECT body FROM comments WHERE id = ?", (cid,)).fetchone()   # a proposal is no memory: no fields kept
        pull = re.fullmatch(r"#([1-9][0-9]{0,8})", (dict(record_format.fields(row["body"] or "")).get("Pull request") or "").strip()) \
            if row else None
        if pull and int(pull.group(1)) in closed:
            continue
        later = [row for row in texts if row["issue"] == issue and row["time"] > key[0]]
        if not later:
            waiting[issue] = cid
    return waiting


def texts_since_pause(db, items, allowed):
    """The told text changes written since the newest told resume block (all of them when there is none). A resume block is
    told when an allowed account posted it and nobody edited it afterwards, as the page's pauses are; the time of a block
    is its instant (record_format.instant)."""
    edited = index.edited_comments(db)
    pause = max([record_format.instant(row["at"], row["created_at"]) for row in db.execute(
        "SELECT comment, at, author, created_at FROM events WHERE type = 'resume'")
        if (row["author"] or "").lower() in allowed and row["comment"] not in edited], default="")
    return [row for row in text_memories(db, items) if row["time"] > pause]


def counts(db, config, allowed, state):
    """{"due", "proposals", "texts"} as integers for meta.plan_counts."""
    if state is None:
        return {"due": 0, "proposals": 0, "texts": 0}
    items = state["found"]["items"]
    return {"due": len(due_parts(db, config, allowed, state)), "proposals": len(open_proposals(db, items, allowed)),
            "texts": len(texts_since_pause(db, items, allowed))}
