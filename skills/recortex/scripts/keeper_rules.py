"""The rules of the manuscript keeper: when a change to the paper's text is notable, stable and allowed to be proposed. Pure.

Python 3 standard library only. No network, no database handle, no clock: every function takes what it needs as plain
data (observer_keeper.gather builds it from the index) and the time as a number. The owner's rule: paper text from the
observer is a prompt for the user to accept, never autonomous, and only when there is something notable.

A part of the text is a topic and one of the plan's `write` headings (write_rules.due_parts). Notable, mechanically,
against the newest told text change of that topic and heading (references/observer.md, "The manuscript keeper"):

  N1  a claim became backed     the part is due for a claim, and one of its plan parts reached the state `backed` after
                                the newest text change. Leads to a draft.
  N2  a correction touched it   the part is due because a memory that the newest text change links was replaced,
                                corrected or set aside. Leads to a draft.
  N3  a figure or table exists  a planned figure or table of the topic has its block in the told body and no told text
                                change links it. Joins a draft of the same topic when N1 or N2 holds there; alone, a
                                line only.
  N4  in doubt, or the message  a standing part the text links is in the state `doubt` or `moved`, or the plan's message
      changed                   part was revised after the newest text change. A line only, never a draft.

A part that is due only because of a new Methods decision (why `methods`) is not notable: write.py status lists it for
the main session. Nothing here loosens a check of write.py, which has the last word on every draft.

Stable, and rare: the due row has not changed for KEEPER_STABLE seconds; no source arrived for KEEPER_QUIET seconds, or
a session just started; a topic is proposed at most once in KEEPER_TOPIC_HOURS; at most `max_proposals` a day; never
while a draft for the topic waits; "Not now" puts a draft off LATER_DAYS days; "Drop it" and a draft that write.py check
refused are not tried again until the due row changes; and the keeper stays off a topic the main session started
writing in the last FOREIGN_START_HOURS hours.
"""

import hashlib
import json

import recortex

KEEPER_STABLE = 30 * 60
KEEPER_QUIET = 10 * 60
KEEPER_TOPIC_HOURS = 24
LATER_DAYS = 7
FOREIGN_START_HOURS = 2
NOTABLE_WHY = ("claim", "changed")          # what leads to a draft; "methods" alone does not
UNSETTLED = ("doubt", "moved")              # the states of a part N4 counts
FIGURES_LINE = "Manuscript: %d planned figures or tables now exist and are not in the text."
# The reasons a topic is not proposed now; fixed words, never text from the record.
REASONS = ("waiting", "dropped", "refused", "topic_day", "cap", "unstable", "busy", "quiet")


def figures_line(count):
    """The one fixed line of N3 (integers only), or "" when there is none."""
    return FIGURES_LINE % count if type(count) is int and count > 0 else ""


def newest_text_time(texts, topic, heading):
    """The time of the newest told text change of `topic` that names `heading` (case aside), or ""."""
    here = heading.lower()
    found = [row["time"] for row in texts if row["issue"] == topic
             and here in {name.lower() for name in row["sections"]}]
    return max(found, default="")


def topic_key(keys):
    """The hash of a topic's due rows: the SHA-256 of their own keys (write_rules.due_key), sorted."""
    return hashlib.sha256(json.dumps(sorted(keys)).encode("utf-8")).hexdigest()


def notable(facts):
    """What is worth proposing, from plain facts:

    facts["due"]      [{"topic", "heading", "why", "parts", "results", "decisions", "key"}], write_rules.due_parts with
                      each row's due_key
    facts["parts"]    {plan part id: {"part", "state", "topic", "standing", "text", "time", "backed_at", "replaces"}},
                      "text" the id of a told text change that links the part or None, "backed_at" the time of its
                      oldest standing backing result or ""
    facts["display"]  the kinds of a plan part that carry a block (figure, table)
    facts["texts"]    [{"issue", "sections", "time", "links"}], the told text changes

    Returns {"topics": [{"topic", "headings", "tests", "key", "rows"}] (the drafts worth making, by topic),
    "figures": the planned figures and tables that exist and are in no text and no draft, "in_doubt": the parts N4
    counts, "message_revised": bool}."""
    parts, texts, display = facts["parts"], facts["texts"], set(facts.get("display", ()))
    found = {}
    for row in facts["due"]:
        if row["why"] not in NOTABLE_WHY:
            continue
        newest = newest_text_time(texts, row["topic"], row["heading"])
        tests = []
        if row["why"] == "claim" and any(
                parts.get(cid, {}).get("state") == "backed" and parts[cid].get("backed_at", "") > newest
                for cid in row["parts"]):
            tests.append("N1")
        if row["why"] == "changed":
            tests.append("N2")
        if not tests:
            continue
        entry = found.setdefault(row["topic"], {"topic": row["topic"], "headings": [], "tests": set(), "rows": []})
        entry["headings"].append(row["heading"])
        entry["tests"].update(tests)
        entry["rows"].append(row)
    shown = {}
    for cid, part in parts.items():
        if (part["part"] in display and part["standing"] and part["state"] == "text" and part["text"] is None
                and type(part["topic"]) is int):
            shown.setdefault(part["topic"], []).append(cid)
    figures = 0
    for topic, ids in shown.items():
        if topic in found:
            found[topic]["tests"].add("N3")
        else:
            figures += len(ids)
    newest_any = max((row["time"] for row in texts), default="")
    linked = {cid for row in texts for cid in row["links"]}
    in_doubt = sum(1 for cid in linked if parts.get(cid, {}).get("standing") and parts[cid]["state"] in UNSETTLED)
    message = any(part["part"] == "message" and part["standing"] and part["replaces"] and part["time"] > newest_any
                  for part in parts.values())
    topics = []
    for topic in sorted(found):
        entry = found[topic]
        topics.append({"topic": topic, "headings": sorted(set(entry["headings"])), "tests": sorted(entry["tests"]),
                       "key": topic_key(row["key"] for row in entry["rows"]), "rows": entry["rows"]})
    return {"topics": topics, "figures": figures, "in_doubt": in_doubt, "message_revised": message}


def settle(seen, keys, now):
    """The record of when each topic's due row was first seen as it is now: {topic (text): {"key", "since"}}. A topic
    whose key is the same keeps its time; a changed one starts again at `now`; a topic no longer notable is forgotten.
    `keys` is {topic: key}."""
    out = {}
    for topic, key in keys.items():
        before = seen.get(str(topic)) if isinstance(seen, dict) else None
        same = (isinstance(before, dict) and before.get("key") == key
                and type(before.get("since")) in (int, float) and before["since"] <= now)
        out[str(topic)] = {"key": key, "since": before["since"] if same else now}
    return out


def stable(seen, topic, key, now):
    """True when the topic's due row has been the same for KEEPER_STABLE seconds."""
    before = seen.get(str(topic)) if isinstance(seen, dict) else None
    return (isinstance(before, dict) and before.get("key") == key and type(before.get("since")) in (int, float)
            and now - before["since"] >= KEEPER_STABLE)


def quiet(last_source, now, session_started=False):
    """True when no source arrived for KEEPER_QUIET seconds (or none ever did), or a session just started."""
    return bool(session_started) or last_source is None or now - last_source >= KEEPER_QUIET


def held_off(topic, key, drafts, now, max_proposals):
    """Why a topic is not proposed now, one of REASONS' draft reasons, or "" when it may be. `drafts` are the store's
    rows ({"topic", "state", "due_hash", "created"}); `key` is the topic's current due key."""
    mine = [row for row in drafts if row["topic"] == topic]
    if any(row["state"] in ("waiting", "later") for row in mine):
        return "waiting"   # a draft that waits, or one put off; a put-off draft asks again when its time has passed
    for state, reason in (("dropped", "dropped"), ("refused", "refused")):
        if any(row["state"] == state and row["due_hash"] == key for row in mine):
            return reason   # not again until the due row changes
    day = KEEPER_TOPIC_HOURS * 3600
    proposed = [row for row in drafts if row["state"] not in ("refused", "stale") and now - row["created"] < day]
    if any(row["topic"] == topic for row in proposed):
        return "topic_day"
    if len(proposed) >= max_proposals:
        return "cap"
    return ""


def foreign_start(records, topic, own, now):
    """True when this machine holds a write.py start record for `topic` made in the last FOREIGN_START_HOURS hours by
    something other than the keeper: the main session is writing there, and a second start would replace its record.
    `records` is [{"issue", "nonce", "at"}], `own` the nonces of the keeper's own starts."""
    return any(row["issue"] == topic and row["nonce"] not in own and now - row["at"] < FOREIGN_START_HOURS * 3600
               for row in records)


# ---------------------------------------------------------------- the fixed words of a draft that waits
# One question per draft, with the options below; observer_sources.OWN_DIALOG already recognizes it as the plugin's own
# (it starts "Draft <8 hex>: update the "), so the user's answer is never a source of a memory. The session quotes
# `question_line` verbatim, and the grant looks for it.
DRAFT_OPTIONS = ("Approve", "Not now", "Drop it")
DRAFT_LINE = "update the %s of #%d"
DRAFT_QUESTION = "Draft %s: %s. Approve it, or not?"
GRANT_SECONDS = 600   # the one-time grant of an "Approve" (observer_store.GRANT_SECONDS)
WAITING_LINE = ("recortex: %d %s of the paper's text %s for the user's answer (%s). Load the recortex skill, run its "
                "observer.py drafts, show what would change and ask with the multiple-choice prompt as the skill's "
                "\"Drafts that wait\" section says.")
STALE_LINE = ("The paper's text or what it rests on changed while the draft waited, so nothing was written. recortex "
              "will draft it again when something is notable.")


def grant_key(short):
    """The 32 hex digits the store's one-time grants are kept under for a draft: a hash of its short id, so that no
    draft's grant can be taken for a held memory's, whose key is an event key."""
    return hashlib.sha256(("draft:" + short).encode("ascii")).hexdigest()[:32]


def question_line(headings, topic):
    """What the session's question about a draft quotes: `update the <heading> of #<N>`. Only headings that
    recortex.HEADING_TEXT accepts are named (plugin words, a checked heading, integers); another becomes "text"."""
    names = [h for h in headings if isinstance(h, str) and recortex.HEADING_TEXT.fullmatch(h)] or ["text"]
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return DRAFT_LINE % (joined, topic)


def question(short, headings, topic):
    """The whole question the session asks for a draft, from the fixed template."""
    return DRAFT_QUESTION % (short, question_line(headings, topic))


def draft_line(count, issue):
    """The fixed line for the session start and the reminder (integers only): `count` drafts wait, for the topic
    `issue` (one number) or the topics in the list `issue` (at most five are named)."""
    issues = [issue] if type(issue) is int else [n for n in issue if type(n) is int][:5]
    if type(count) is not int or count < 1 or not issues:
        return ""
    return WAITING_LINE % (count, "draft" if count == 1 else "drafts", "waits" if count == 1 else "wait",
                           ", ".join("#%d" % n for n in issues))
