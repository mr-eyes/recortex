#!/usr/bin/env python3
"""Where each card of the board belongs, from the record alone.

One function, `desired`, answers for every issue and pull request that deserves a card; the table of
references/board.md is the table in `place()`. Nothing here reads the board: `compare` sets what the record wants
against the cards the sync stored in the index (`board_cards`), and `hand_moved` names the cards that someone moved
by hand. It reads the index only, makes no call and imports no module of the mind page.

The Why text of a card is the plugin's own mark of what it last wrote: the status name, then the address of the memory
behind it when there is one. A card whose Status differs from the record but whose Why equals the mark the record
gives now was put there by someone else: a hand move. A card whose Why differs is simply behind.
"""

import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import index  # noqa: E402
import record_format  # noqa: E402

STATUSES = ("To triage", "Todo", "In Progress", "Waiting for a yes", "Blocked", "Done", "Set aside")
WINDOW_DAYS = 30
WRITE_CAP = 60          # cards one `board.py apply` writes; the rest waits for the next run
MARK_CAP = 300
ADDRESS = re.compile(r"https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/issues/[0-9]+#issuecomment-[0-9]+",
                     re.ASCII)
HOLDS = re.compile(r"^\*\*Holds:\*\*(.*)$", re.M)


def mark(status, address=""):
    """The Why text for a card in `status` because of the memory at `address` (checked by the caller)."""
    return status if not address else "%s %s" % (status, address)


def address_of(value):
    return value if isinstance(value, str) and ADDRESS.fullmatch(value) else ""


def closed_within(stamp, now, days=WINDOW_DAYS):
    try:
        then = datetime.datetime.strptime(stamp or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return False
    return (now - then).total_seconds() <= days * 86400


def cited(texts):
    """The issue numbers that texts name as #N."""
    found = set()
    for text in texts:
        for match in record_format.MENTION.finditer(text or ""):
            found.add(int(match.group(1)))
    return found


def holds(db):
    """(the issue numbers the checked resume block's Holds line names, the address of that resume comment)."""
    block = index.get_meta(db, "resume") or ""
    where = address_of(index.get_meta(db, "resume_url") or "")
    match = HOLDS.search(block)
    return (cited([match.group(1)]) if match and where else set()), where


def close_reason(db, number):
    """completed or not_planned: the state_reason of the newest `closed` event of the timeline; completed when
    the timeline does not say."""
    row = db.execute("SELECT detail FROM timeline WHERE issue = ? AND event = 'closed' ORDER BY at DESC, seq DESC "
                     "LIMIT 1", (number,)).fetchone()
    return "not_planned" if row and (row["detail"] or "") == "not_planned" else "completed"


class Facts:
    """What the memories say about each task, read once."""

    def __init__(self, db, allowed):
        found = index.memories(db, allowed)
        self.items = found["items"]
        self.url = {row["comment"]: address_of(row["url"]) for row in db.execute("SELECT comment, url FROM events")}
        texts = {}
        for row in db.execute("SELECT comment, text FROM event_fields"):
            texts.setdefault(row["comment"], []).append(row["text"])
        self.cites = {cid: cited(texts.get(cid, [])) for cid in self.items}
        self.pointed = {target for item in self.items.values() for _, target in item["links"]}

    def about(self, number, kinds):
        """The memories of `kinds` on issue `number` or naming it in a field, newest first."""
        rows = [m for cid, m in self.items.items() if m["kind"] in kinds and (m["issue"] == number
                                                                                or number in self.cites[cid])]
        return sorted(rows, key=lambda m: (m["time"], m["id"]), reverse=True)

    def on(self, number, kinds=None):
        rows = [m for m in self.items.values() if m["issue"] == number and (kinds is None or m["kind"] in kinds)]
        return sorted(rows, key=lambda m: (m["time"], m["id"]), reverse=True)

    def address(self, memory):
        return self.url.get(memory["id"], "") if memory else ""


def place(db, facts, issue, labels, held, where, now):
    """(status, address) for an `active` task, by the first row of the table that matches; None for a task that
    left the window."""
    number = issue["number"]
    if issue["state"] != "open":
        if not closed_within(issue["closed_at"], now):
            return None
        if close_reason(db, number) == "not_planned":
            decisions = facts.about(number, ("decision",))
            return "Set aside", facts.address(decisions[0] if decisions else None)
        section = db.execute("SELECT text FROM sections WHERE issue = ? AND heading = 'Result' ORDER BY ordinal",
                             (number,)).fetchone()
        linked = [int(c) for c in re.findall(r"#issuecomment-([0-9]+)", section["text"] if section else "")]
        for cid in linked:
            if cid in facts.items and facts.items[cid]["kind"] in ("result", "failure"):
                return "Done", facts.address(facts.items[cid])
        ended = facts.about(number, ("result", "failure"))
        return "Done", facts.address(ended[0] if ended else None)
    if "needs-decision" in labels or number in held:
        return "Waiting for a yes", where
    open_failures = [m for m in facts.about(number, ("failure",)) if m["id"] not in facts.pointed]
    if open_failures:
        return "Blocked", facts.address(open_failures[0])
    if facts.on(number):
        checkpoints = facts.on(number, ("checkpoint",))
        return "In Progress", facts.address(checkpoints[0] if checkpoints else None)
    return "Todo", ""


def desired(db, allowed, now=None):
    """[{"number", "kind" ("issue" or "pull"), "status", "address", "mark"}] for every card the record wants, by
    number. `now` is a datetime (tests give one)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    facts = Facts(db, allowed)
    held, where = holds(db)
    triage = {row["issue"] for row in index.triage(db, allowed)}
    merged = {row["number"] for row in db.execute("SELECT number FROM pull_merge WHERE merged = 1")}
    labels = {}
    for row in db.execute("SELECT issue, label FROM labels"):
        labels.setdefault(row["issue"], set()).add(row["label"].lower())
    cards = []
    for issue in db.execute("SELECT * FROM issues ORDER BY number"):
        number, status, address, kind = issue["number"], None, "", "issue"
        if issue["is_pr"]:
            kind = "pull"
            if issue["state"] == "open":
                status = "In Progress"
            elif closed_within(issue["closed_at"], now):
                status = "Done" if number in merged else "Set aside"
        elif number in triage:
            status = "To triage"
        elif "active" in labels.get(number, ()):
            placed = place(db, facts, issue, labels.get(number, set()), held, where, now)
            if placed:
                status, address = placed
        if status:
            cards.append({"number": number, "kind": kind, "status": status, "address": address,
                          "mark": mark(status, address)[:MARK_CAP]})
    return cards


def compare(wanted, cards, known):
    """Set the wanted cards against the stored ones. `cards` is {number: {"status", "why", "archived", "item"}}, and
    `known` the numbers that are issues or pull requests of the record. Returns a dict of lists of cards (by number):
    missing (no card yet), behind (the record moved on, or the card has no state yet), moved (someone moved the card
    since the plugin wrote it), and gone (a card whose number is not in the record, as numbers)."""
    out = {"missing": [], "behind": [], "moved": [], "gone": sorted(n for n in cards if n not in known)}
    for want in wanted:
        card = cards.get(want["number"])
        if card is None:
            out["missing"].append(want)
            continue
        if card["archived"]:
            continue
        if card["status"] == want["status"]:
            if card["why"] != want["mark"]:
                out["behind"].append(dict(want, why_only=True))
            continue
        if card["status"] and card["why"] == want["mark"]:
            if card["status"] != "other":
                out["moved"].append(dict(want, board=card["status"]))
            continue  # a column the user added is not reported until the record itself moves on
        out["behind"].append(dict(want, why_only=False))
    return out


def stored_cards(db):
    return {row["number"]: {"status": row["status"], "why": row["why"] or "", "archived": bool(row["archived"]),
                            "item": row["item"]} for row in db.execute("SELECT * FROM board_cards")}


def plan(db, allowed, now=None):
    wanted = desired(db, allowed, now)
    known = {row["number"] for row in db.execute("SELECT number FROM issues")}
    result = compare(wanted, stored_cards(db), known)
    result["wanted"] = wanted
    return result


def hand_moved(db, allowed, now=None):
    """The numbers of the cards that were moved by hand, for the brief; [] when no board was read."""
    if not db.execute("SELECT 1 FROM board_cards LIMIT 1").fetchone():
        return []
    return sorted(card["number"] for card in plan(db, allowed, now)["moved"])
