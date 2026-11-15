#!/usr/bin/env python3
"""The mind of a project: what the mind page tells, derived from the local index alone.

Python 3 standard library only. No network, no hook, no write. build(db, repo, allowed, today) reads the index in one
transaction and returns one dict that json.dumps can print; mind.py renders it and derives nothing itself.

Who is told (references/mind.md): the memories of allowed accounts that were never edited, as index.memories decides;
a topic body only when an allowed account wrote it, its edit history is read and every edit was made by an allowed
account; another issue's title only under the title rule of the brief. Everything else is counted, never told.

Every string the model takes from the index passes clean(). Text is kept as text: the model never builds markup and
never reads an address from the index (no url or image column); the page builds every address from checked parts.

Fixed wording lives in PHRASES (the sentences, the link words, the chips) and FIELD_NAMES (the human names of the
fields of a memory). Record text is structured for the page as a doc: a list of blocks, each a dict with one key
("p", "ul", "quote" or "table"), whose inline parts are runs, each a dict with one key: "t" (text), "code", "w" (a
project word, with its number "n") or "cite" (a citation told in words, with "n" and "text").
"""

import datetime
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import index  # noqa: E402
import recortex  # noqa: E402
import record_format  # noqa: E402

CAP_MEMORIES = 2000       # the newest told memories on the page; older ones are counted
CAP_MAP_THREADS = 24      # rows of the map; the quietest threads fold into one row
CAP_WORDS = 400
CAP_FIELD = 1200          # characters of a field
CAP_STORY = 3000          # characters of a word's longer story
CAP_SECTION = 6000        # characters of a section of a topic body
CAP_TABLE_ROWS = 40
CAP_TABLE_COLUMNS = 12
CAP_FIRST_LINES = 600     # characters of a cited issue's first lines
CAP_LINE = 300            # characters of a name, a title, a plain line or a pause line

PHRASES = {
    # a sentence: the lead is fixed, the words are the record's, the full stop is the page's
    "lead.result": "On %s we found that ",
    "lead.decision": "On %s we decided to ",
    "lead.failure": "On %s, ",
    "lead.idea": "On %s we had the idea to ",
    "lead.question": "On %s we asked ",
    "lead.correction": "On %s we corrected ourselves: ",
    "lead.checkpoint": "On %s we paused here: ",
    # a memory without a plain line: the lead, then the record's own short words in quotation marks
    "old.result": "On %s we recorded a result, noted as ",
    "old.decision": "On %s we decided: ",
    "old.failure": "On %s something went wrong: ",
    "old.idea": "On %s we noted an idea: ",
    "old.question": "On %s we noted a question: ",
    "old.correction": "On %s we corrected ourselves: ",
    "old.checkpoint": "On %s we paused: ",
    # two linked memories told as one passage
    "join.because": ", so ",
    "join.instead": " That was set aside. Instead, ",
    "join.replaces": " Later that changed: ",
    "join.checks.idea": " We tried it: ",
    "join.checks.question": " The answer came later: ",
    "join.checks.decision": " We checked it: ",
    # the words between two memories of a chain, and before a linked memory
    "link.because": "so",
    "link.instead": "that was set aside; instead",
    "link.replaces": "later that changed",
    "link.checks.idea": "we tried it",
    "link.checks.question": "the answer",
    "link.checks.decision": "we checked it",
    "link.back": ", going back to ",
    "link.thread": ", from the thread ",
    "link.then": "then",
    # chips
    "chip.replaced": "Replaced on %s",
    "chip.aside": "Set aside on %s",
    "chip.answered": "Answered on %s",
    "chip.tried": "Tried on %s",
    "chip.question": "Still open",
    "chip.idea": "Not tried yet",
    "chip.nothing": "Nothing followed yet",
    "chip.led": "Led to a change",
    "chip.stands": "Still stands",
    "latest.decision": "The latest decision here",
    "latest.result": "The latest result here",
    "latest.replaced": "; it replaced an earlier one",
    # threads
    "status.aside": "Set aside",
    "status.open": "1 open thought",
    "status.opens": "%d open thoughts",
    "status.settled": "Settled for now",
    "thread.loose": "Loose thoughts",
    "thread.unnamed": "a thread",
    # what stands in for text that is not told
    "not told": "an earlier memory that is not told here",
    "cite.issue": "an issue on GitHub",
    "cite.comment": "a comment elsewhere on GitHub",
    "cite.untold": "a comment that is not told here",
    "cite.memory": "the memory of %s",
    "cite.someone": "an issue someone else opened",
    "cite.pull": "A pull request",
    "account": "an account",
    "day.unknown": "an unknown day",
}

FIELD_NAMES = {
    "Exact words": "In their exact words",
    "Decided by": "Who decided",
    "Effect": "What it changes",
    "What failed": "What we saw",
    "Cause": "Why it happened",
    "Impact": "What it cost",
    "Not established": "What this does not show",
    "Was": "What we said before",
    "Is": "What is right",
    "Why": "How the mistake happened",
    "Numbers": "The numbers",
}
WHERE_FIELDS = ("Corrects", "Where recorded")          # shown under "Where this was said"
PEOPLE_FIELDS = ("Decided by",)
OLD_FIELD = {"decision": "Decision", "failure": "What failed", "correction": "Is", "checkpoint": "State"}
LINK_ORDER = ("because", "instead", "replaces", "checks")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")

BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f‎‏‪-‮⁦-⁩﻿]")
LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:\[bot\])?")
WALL = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})(?:T([0-9]{2}):([0-9]{2}))?")
CODE = re.compile(r"`([^`\n]+)`")
IMAGE_LINE = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)")
SEPARATOR = re.compile(r":?-{3,}:?")
BLOCK_HEADING = re.compile(r"^### (Figure|Table): ([A-Za-z0-9][A-Za-z0-9_.-]*) - ")
HEADING = re.compile(r"^#{1,3} ")
GOAL = "goal"


def clean(value):
    """A string from the record as the model keeps it: CR LF and lone CR become LF, the line and paragraph
    separators become LF, and control characters and characters that reorder or hide text become U+FFFD."""
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", "\n").replace(" ", "\n")
    return BAD.sub("�", text)


def short(text, cap=CAP_LINE):
    text = " ".join(clean(text).split())
    return text if len(text) <= cap else text[:cap - 3].rstrip() + "..."


def login(value):
    """An account name as GitHub spells one, else None (the page says "an account")."""
    value = clean(value)
    return value if LOGIN.fullmatch(value) else None


def wall(at):
    """(year, month, day, hour, minute) of a time as written in the record, without converting its zone, or None."""
    match = WALL.match(clean(at))
    if not match:
        return None
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    hour, minute = int(match.group(4) or 0), int(match.group(5) or 0)
    try:
        datetime.datetime(year, month, day, hour, minute)
    except ValueError:
        return None
    return year, month, day, hour, minute


def wall_key(at):
    parts = wall(at)
    return "%04d-%02d-%02dT%02d:%02d" % parts if parts else ""


def day_words(at, years):
    parts = wall(at)
    if not parts:
        return PHRASES["day.unknown"]
    text = "%s %d" % (MONTHS[parts[1] - 1], parts[2])
    return text + ", %d" % parts[0] if years else text


def full_day(value):
    parts = wall(value)
    return "%s %d, %d" % (MONTHS[parts[1] - 1], parts[2], parts[0]) if parts else PHRASES["day.unknown"]


def lower_first(text):
    return text[:1].lower() + text[1:] if text.startswith("On ") else text


def plural(count, one, many):
    return one if count == 1 else many % count


# ---------------------------------------------------------------- record text as blocks and runs

def split_cells(line):
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def is_table_line(line):
    stripped = line.strip()
    return len(stripped) > 1 and stripped.startswith("|") and stripped.endswith("|")


def table_of(lines):
    """(head, rows, cut) of a Markdown table given as its lines (head line, separator, rows), or None."""
    if len(lines) < 2:
        return None
    separator = split_cells(lines[1])
    if not separator or not all(SEPARATOR.fullmatch(cell) for cell in separator if cell):
        return None
    head = split_cells(lines[0])
    rows = [split_cells(line) for line in lines[2:]]
    cut = len(rows) > CAP_TABLE_ROWS or len(head) > CAP_TABLE_COLUMNS or any(
        len(row) > CAP_TABLE_COLUMNS for row in rows)
    return head[:CAP_TABLE_COLUMNS], [row[:CAP_TABLE_COLUMNS] for row in rows[:CAP_TABLE_ROWS]], cut


def first_table(text):
    lines = clean(text).split("\n")
    start = next((i for i, line in enumerate(lines) if is_table_line(line)), None)
    if start is None:
        return None
    end = start
    while end < len(lines) and is_table_line(lines[end]):
        end += 1
    return table_of(lines[start:end])


class Words:
    """The project words of the mind and the matcher that finds them in a text."""

    def __init__(self, entries):
        names = {}
        for n, spelled in entries:
            for name in spelled:
                name = " ".join(clean(name).split())
                if not name or len(name) > 60:
                    continue
                key = name if len(name) < 4 else name.lower()
                names.setdefault(key, (name, n))
        ordered = sorted(names.values(), key=lambda pair: (-len(pair[0]), pair[0]))
        self.owner, parts = {}, []
        for number, (name, n) in enumerate(ordered):
            group = "g%d" % number
            body = re.escape(name)
            parts.append("(?P<%s>%s)" % (group, body if len(name) < 4 else "(?i:%s)" % body))
            self.owner[group] = n
        self.pattern = re.compile(r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])" % "|".join(parts)) if parts else None

    def find(self, text, seen):
        """[(start, end, n)] of the words in `text`, the first appearance of each word not already in `seen`."""
        out = []
        if self.pattern is None:
            return out
        for match in self.pattern.finditer(text):
            n = self.owner[match.lastgroup]
            if n in seen:
                continue
            seen.add(n)
            out.append((match.start(), match.end(), n))
        return out


# ---------------------------------------------------------------- the build

def build(db, repo, allowed, today):
    """The model of the mind of `repo` (a dict that json.dumps can print), read from the open index `db` in one
    transaction that is rolled back. `allowed` is recortex.allowed_users(repo) and must not be empty; `today` is the
    day of the build (a datetime.date), from which ages are counted."""
    if not allowed:
        raise ValueError("the mind is built only for a repository in the user's trusted list")
    began = not db.in_transaction
    if began:
        db.execute("BEGIN")
    try:
        return Build(db, repo, {name.lower() for name in allowed}, today).model()
    finally:
        if began and db.in_transaction:
            db.execute("ROLLBACK")


class Build:
    def __init__(self, db, repo, allowed, today):
        self.db, self.repo, self.allowed, self.today = db, repo, allowed, today
        self.cited, self.first_lines = {}, []

    def ok(self, author):
        return bool(author) and clean(author).lower() in self.allowed

    def meta(self, key):
        return clean(index.get_meta(self.db, key) or "")

    # ------------------------------------------------------------ issues

    def read_issues(self):
        self.issues = {}
        for row in self.db.execute("SELECT number, title, state, is_pr, author, body, created_at, closed_at, "
                                   "timeline_read, edits_read FROM issues"):
            self.issues[row["number"]] = {"number": row["number"], "title": row["title"] or "", "state": row["state"],
                                          "pull": bool(row["is_pr"]), "author": row["author"], "body": row["body"] or "",
                                          "created": row["created_at"], "closed": row["closed_at"],
                                          "timeline_read": bool(row["timeline_read"]),
                                          "edits_read": bool(row["edits_read"]), "renamers": [], "editors": []}
        for row in self.db.execute("SELECT issue, event, actor FROM timeline WHERE event IN ('renamed', 'edited')"):
            issue = self.issues.get(row["issue"])
            if issue is not None:
                issue["renamers" if row["event"] == "renamed" else "editors"].append(row["actor"])
        self.labels = {}
        for row in self.db.execute("SELECT issue, label FROM labels ORDER BY issue, label"):
            self.labels.setdefault(row["issue"], []).append(row["label"] or "")

    def body_shown(self, number):
        """The topic-body rule: an allowed account opened the issue, its edit history is read, and every edit of
        its body was made by an allowed account."""
        issue = self.issues.get(number)
        return bool(issue) and self.ok(issue["author"]) and issue["edits_read"] and all(
            self.ok(actor) for actor in issue["editors"])

    def title_shown(self, number):
        """The title rule of the brief: an allowed account opened it, its history is read, nobody else renamed it."""
        issue = self.issues.get(number)
        return bool(issue) and self.ok(issue["author"]) and issue["timeline_read"] and all(
            self.ok(actor) for actor in issue["renamers"])

    def has_label(self, number, name):
        return name in {label.lower() for label in self.labels.get(number, [])}

    # ------------------------------------------------------------ memories

    def read_memories(self):
        found = index.memories(self.db, self.allowed)
        every = sorted(found["items"].values(), key=lambda m: (m["time"], m["id"]))
        self.older = max(0, len(every) - CAP_MEMORIES)
        kept = every[self.older:]
        self.found = found
        self.years = len({wall(m["at"])[0] for m in kept if wall(m["at"])}) > 1
        self.fields = {}
        for row in self.db.execute("SELECT comment, ordinal, label, text FROM event_fields ORDER BY comment, ordinal"):
            if row["comment"] in found["items"]:
                self.fields.setdefault(row["comment"], []).append((clean(row["label"]), clean(row["text"])))
        self.evidence = {}
        for row in self.db.execute("SELECT comment, ordinal, kind, ident, repo, ref, path, line_from, line_to "
                                   "FROM evidence ORDER BY comment, ordinal"):
            if row["comment"] in found["items"]:
                self.evidence.setdefault(row["comment"], []).append(row)
        self.m = {}
        for item in kept:
            self.m[item["id"]] = {
                "id": item["id"], "issue": item["issue"], "kind": item["kind"], "at": clean(item["at"]),
                "time": item["time"], "wall": wall_key(item["at"]), "day": day_words(item["at"], self.years),
                "plain": short(item["plain"]).rstrip(".").strip() or None if item["plain"] is not None else None,
                "title": short(item["title"]), "state": item["state"], "author": login(item["author"]),
                "links": [(key, target) for key, target in item["links"]],
            }
        self.order = [self.m[item["id"]] for item in kept]
        self.incoming = {}
        for memory in self.order:
            for key, target in memory["links"]:
                if target in self.m:
                    self.incoming.setdefault(target, []).append((key, memory["id"]))

    def field(self, cid, label):
        return next((text for name, text in self.fields.get(cid, []) if name == label), None)

    def sentence(self, memory):
        kind, day = memory["kind"], memory["day"]
        if memory["plain"]:
            return {"lead": PHRASES["lead." + kind] % day, "words": memory["plain"], "quoted": False}
        words = self.field(memory["id"], OLD_FIELD.get(kind, "")) if kind in OLD_FIELD else None
        if words and kind == "decision":
            words = re.split(r"(?<=[.!?])\s", words.strip(), maxsplit=1)[0]
        words = short((words or "").strip().split("\n")[0] or memory["title"]).rstrip(".").strip()
        return {"lead": PHRASES["old." + kind] % day, "words": words, "quoted": True}

    @staticmethod
    def text_of(sentence):
        if sentence["quoted"]:
            return sentence["lead"] + "“" + sentence["words"] + ".”"
        return sentence["lead"] + sentence["words"] + "."

    def link_phrase(self, key, target):
        if key == "checks":
            kind = self.m[target]["kind"] if target in self.m else ""
            return PHRASES.get("link.checks." + kind, PHRASES["link.checks.decision"])
        return PHRASES["link." + key]

    def joined(self, earlier, key, later):
        first, second = self.text_of(earlier["sentence"]), self.text_of(later["sentence"])
        if key == "because":
            return first[:-1] + PHRASES["join.because"] + lower_first(second)
        if key == "checks":
            return first + PHRASES.get("join.checks." + earlier["kind"], PHRASES["join.checks.decision"]) + \
                lower_first(second)
        return first + PHRASES["join." + key] + lower_first(second)

    def passage(self, memory):
        """The memory told with what led to it, or else with what came of it; a memory without a plain line is never
        joined."""
        if not memory["old"]:
            for key, target in sorted(memory["links"], key=lambda pair: LINK_ORDER.index(pair[0])):
                other = self.m.get(target)
                if other is not None and not other["old"]:
                    return self.joined(other, key, memory)
            for key, source in self.incoming.get(memory["id"], []):
                other = self.m[source]
                if not other["old"]:
                    return self.joined(memory, key, other)
        return self.text_of(memory["sentence"])

    def state_of(self, memory):
        """(css, chip) of a memory: css is one of stands, replaced, aside, answered, tried, open, led, plain."""
        incoming = self.incoming.get(memory["id"], [])

        def first(key):
            sources = sorted((self.m[s] for k, s in incoming if k == key), key=lambda m: (m["time"], m["id"]))
            return sources[0]["day"] if sources else PHRASES["day.unknown"]

        state, kind = memory["state"], memory["kind"]
        if state == "replaced":
            return "replaced", PHRASES["chip.replaced"] % first("replaces")
        if state == "set aside":
            return "aside", PHRASES["chip.aside"] % first("instead")
        if state == "answered":
            return "answered", PHRASES["chip.answered"] % first("checks")
        if state == "tried":
            return "tried", PHRASES["chip.tried"] % first("checks")
        if state == "open":
            return "open", PHRASES["chip.question"]
        if state == "untried":
            return "open", PHRASES["chip.idea"]
        if memory["old"] and not incoming and not memory["links"]:
            return "plain", ""
        if kind == "failure":
            if incoming:
                return "led", PHRASES["chip.led"]
            return "open", PHRASES["chip.nothing"]
        return "stands", PHRASES["chip.stands"]

    # ------------------------------------------------------------ text with words and citations

    def runs(self, text, seen):
        """The inline runs of one line or paragraph of record text."""
        spans = [(m.start(), m.end(), {"code": m.group(1)}) for m in CODE.finditer(text)]
        for item in record_format.citations(text, self.repo):
            spans.append((item["start"], item["end"], self.citation(item, text[item["start"]:item["end"]])))
        spans.sort(key=lambda span: span[0])
        out, at = [], 0
        for start, end, run in spans:
            if start < at:
                continue
            self.plain_runs(text[at:start], seen, out)
            out.append(run)
            at = end
        self.plain_runs(text[at:], seen, out)
        return out

    def plain_runs(self, text, seen, out):
        at = 0
        for start, end, n in self.words.find(text, seen):
            if start > at:
                out.append({"t": text[at:start]})
            out.append({"w": text[start:end], "n": n})
            at = end
        if at < len(text):
            out.append({"t": text[at:]})

    def citation(self, item, written):
        if item["kind"] == "memory":
            target = self.m.get(item["comment"])
            if target is None:
                return {"cite": "none", "n": 0, "text": PHRASES["cite.untold"]}
            return {"cite": "memory", "n": target["id"], "text": PHRASES["cite.memory"] % target["day"]}
        if item["kind"] == "elsewhere":
            word = "cite.comment" if "#issuecomment-" in written else "cite.issue"
            return {"cite": "none", "n": 0, "text": PHRASES[word]}
        number = item["issue"]
        thread = self.topic_thread.get(number)
        if thread is not None:
            return {"cite": "thread", "n": thread["n"], "text": thread["name"]}
        if number not in self.issues:
            return {"cite": "none", "n": 0, "text": PHRASES["cite.issue"]}
        return {"cite": "issue", "n": number, "text": self.cite_issue(number)}

    def cite_issue(self, number):
        if number not in self.cited:
            issue = self.issues[number]
            shown = self.title_shown(number)
            title = short(issue["title"]) if shown and issue["title"].strip() else (
                PHRASES["cite.pull"] if issue["pull"] else PHRASES["cite.someone"])
            self.cited[number] = {"n": number, "title": title, "pull": issue["pull"],
                                  "author": login(issue["author"]), "opened": full_day(issue["created"]),
                                  "open": issue["state"] == "open", "closed": full_day(issue["closed"])
                                  if issue["state"] != "open" and issue["closed"] else None, "first": None}
            if not issue["pull"] and self.body_shown(number):
                lines = [re.sub(r"^#{1,6}\s+", "", line) for line in clean(issue["body"]).split("\n")
                         if not line.lstrip().startswith("<!--")]
                text = "\n".join(lines).strip()
                if text:
                    self.first_lines.append((number, text))  # read by read_first_lines: a body may cite further issues
        return self.cited[number]["title"]

    def read_first_lines(self):
        """The first lines of the cited issues, one after another and not one inside the other, so that a long chain
        of issues that cite each other is walked without recursion."""
        while self.first_lines:
            number, text = self.first_lines.pop(0)
            self.cited[number]["first"] = self.doc(text, set(), cap=CAP_FIRST_LINES)

    def doc(self, text, seen, cap=CAP_FIELD):
        """Record text as blocks: paragraphs at blank lines, a list for lines that start with "- ", a quotation for
        lines that start with ">", a table for a Markdown table; a line that is only an image is left out. Cut at
        `cap` characters, and then "cut" is true."""
        text = clean(text).strip("\n")
        cut = len(text) > cap
        text = text[:cap]
        lines, blocks, paragraph, i = text.split("\n"), [], [], 0
        inline = self.runs

        def flush():
            if paragraph:
                blocks.append({"p": inline(" ".join(paragraph), seen)})
                del paragraph[:]

        while i < len(lines):
            stripped = lines[i].strip()
            if not stripped or IMAGE_LINE.fullmatch(stripped):
                flush()
                i += 1
                continue
            if is_table_line(stripped):
                end = i
                while end < len(lines) and is_table_line(lines[end]):
                    end += 1
                table = table_of(lines[i:end])
                if table:
                    flush()
                    head, rows, table_cut = table
                    blocks.append({"table": {"head": [inline(cell, seen) for cell in head],
                                             "rows": [[inline(cell, seen) for cell in row] for row in rows],
                                             "cut": table_cut}})
                    i = end
                    continue
            if stripped.startswith("- "):
                flush()
                items = []
                while i < len(lines) and lines[i].strip().startswith("- "):
                    items.append(inline(lines[i].strip()[2:], seen))
                    i += 1
                blocks.append({"ul": items})
                continue
            if stripped.startswith(">"):
                flush()
                quoted = []
                while i < len(lines) and lines[i].strip().startswith(">"):
                    quoted.append(lines[i].strip()[1:].strip())
                    i += 1
                blocks.append({"quote": inline(" ".join(part for part in quoted if part), seen)})
                continue
            paragraph.append(stripped)
            i += 1
        flush()
        return {"blocks": blocks, "cut": cut}

    # ------------------------------------------------------------ threads

    def read_threads(self):
        topics = sorted({label for labels in self.labels.values() for label in labels
                         if label.lower().startswith("topic:")})
        first = {}
        for number, labels in self.labels.items():
            mine = sorted(label for label in labels if label.lower().startswith("topic:"))
            if mine:
                first[number] = mine[0]
        threads, taken = [], set()
        for label in topics:
            issues = sorted(n for n, labels in self.labels.items() if label in labels)
            finals = [n for n in issues if n in self.issues and self.issues[n]["state"] == "open"
                      and not self.issues[n]["pull"] and self.has_label(n, "final-record")]
            threads.append({"label": label, "issues": issues, "topic": finals[0] if finals else None,
                            "own": [n for n in issues if first.get(n) == label]})
        self.first_topic = first
        for memory in self.order:
            label = first.get(memory["issue"])
            memory["thread_label"] = label
        loose = [m for m in self.order if m["thread_label"] is None]
        for thread in threads:
            thread["memories"] = [m for m in self.order if m["thread_label"] == thread["label"]]
        threads = [t for t in threads if t["memories"] or t["topic"] is not None]
        if loose:
            threads.append({"label": None, "issues": sorted({m["issue"] for m in loose}), "topic": None, "own": [],
                            "memories": loose})
        position = {m["id"]: i for i, m in enumerate(self.order)}
        threads.sort(key=lambda t: (t["label"] is None, position[t["memories"][0]["id"]] if t["memories"]
                                    else len(self.order), t["label"] or ""))
        for count, thread in enumerate(threads):
            candidates = ([thread["topic"]] if thread["topic"] else []) + thread["own"] + thread["issues"]
            if thread["label"] is None:
                candidates = [0]
            n = next((c for c in candidates if c not in taken), 10 ** 10 + count)
            taken.add(n)
            thread["n"] = n
        self.threads = threads
        self.topic_thread = {}
        for thread in threads:
            label = thread["label"]
            shown = thread["topic"] is not None and self.body_shown(thread["topic"])
            name, source = None, "label"
            if shown:
                title = next((line[2:] for line in clean(self.issues[thread["topic"]]["body"]).split("\n")
                              if line.startswith("# ")), "")
                if title.strip():
                    name, source = short(title), "body"
            if name is None and label is None:
                name, source = PHRASES["thread.loose"], "fixed"
            elif name is None:
                rest = label.split(":", 1)[1]
                if recortex.LABEL_NAME.fullmatch(label) and rest:
                    name = rest
                else:
                    name, source = PHRASES["thread.unnamed"], "fixed"
            thread.update(name=name, name_from=source, shown=shown, set_aside=label is not None and
                          thread["topic"] is None, loose=label is None)
            if thread["topic"] is not None and thread["topic"] not in self.topic_thread:
                self.topic_thread[thread["topic"]] = thread
            for memory in thread["memories"]:
                memory["thread"] = thread["n"]
        self.thread_of = {t["n"]: t for t in threads}
        self.thread_by_label = {t["label"]: t["n"] for t in threads if t["label"] is not None}

    # ------------------------------------------------------------ words and openings

    def read_words(self):
        rows = index.explained(self.db, self.allowed, "word")
        by_key = {}
        for row in rows:
            by_key.setdefault(row["key"], []).append(row)
        keys = sorted(by_key)[:CAP_WORDS]
        self.words_cut = max(0, len(by_key) - CAP_WORDS)
        self.word_rows = [(n, by_key[key]) for n, key in enumerate(keys, 1)]
        entries = []
        for n, rows_of_key in self.word_rows:
            newest = rows_of_key[-1]
            entries.append((n, [newest["word"]] + list(newest["also"])))
        self.words = Words(entries)
        self.openings = {}
        for row in index.explained(self.db, self.allowed, "thread"):  # oldest first: the newest stands
            n = self.thread_by_label.get(self.first_topic.get(row["issue"]))
            if n is not None:
                self.openings[n] = row

    # ------------------------------------------------------------ the parts of the model

    def memory_model(self, memory):
        cid = memory["id"]
        seen = set()
        sentence = memory["sentence"]
        sentence["marks"] = [list(span) for span in self.words.find(sentence["words"], seen)]
        fields, where = [], []
        for label, text in self.fields.get(cid, []):
            if label == "plain" or (label == "Decision" and memory["plain"]):
                continue
            if label in PEOPLE_FIELDS:
                entry = {"label": label, "name": FIELD_NAMES.get(label, label), "type": "people",
                         "people": short(text, CAP_FIELD)}
            elif label == "Numbers":
                table = first_table(text)
                entry = {"label": label, "name": FIELD_NAMES[label], "type": "numbers",
                         "doc": self.doc(text, seen)}
                entry["type"] = "numbers" if table else "text"
            else:
                entry = {"label": label, "name": FIELD_NAMES.get(label, label),
                         "type": "quote" if label == "Exact words" else "text", "doc": self.doc(text, seen)}
            (where if label in WHERE_FIELDS else fields).append(entry)
        out = []
        for key, target in memory["links"]:
            other = self.m.get(target)
            out.append({"kind": key, "id": target, "told": other is not None, "phrase": self.link_phrase(key, target)})
        incoming = [{"kind": key, "id": source, "phrase": self.link_phrase(key, cid)}
                    for key, source in sorted(self.incoming.get(cid, []), key=lambda p: (self.m[p[1]]["time"], p[1]))]
        parts = wall(memory["at"])
        thread = self.thread_of[memory["thread"]]
        return {
            "id": cid, "issue": memory["issue"], "thread": memory["thread"], "kind": memory["kind"],
            "at": memory["at"], "day": memory["day"], "plain": memory["plain"], "title": memory["title"],
            "old": memory["old"], "state": memory["state"], "css": memory["css"], "chip": memory["chip"],
            "latest": memory.get("latest", ""), "group": memory["group"], "sentence": sentence,
            "passage": memory["passage"], "out": out, "in": incoming, "fields": fields, "where": where,
            "evidence": [self.evidence_model(row, thread) for row in self.evidence.get(cid, [])],
            "author": memory["author"],
            "written": {"time": "%02d:%02d" % parts[3:5] if parts else None, "day": full_day(memory["at"])},
            "side": memory["side"], "remind": self.remind(memory),
        }

    def block_parts(self, number):
        """{(kind, id): block} of the figure and table blocks of a topic body, from the index, with the table cells
        and the figure's repository read from the body."""
        body = clean(self.issues[number]["body"])
        lines = body.split("\n")
        texts, repos = {}, {}
        for i, line in enumerate(lines):
            match = BLOCK_HEADING.match(line)
            if match:
                end = i + 1
                while end < len(lines) and not HEADING.match(lines[end]):
                    end += 1
                texts[(match.group(1).lower(), match.group(2))] = "\n".join(lines[i + 1:end])
        for block in record_format.blocks(body):
            repos[(block["kind"], block["id"])] = block["repo"]
        out = {}
        for row in self.db.execute("SELECT kind, id, title, section, caption, ref, path, data, command FROM blocks "
                                   "WHERE issue = ? ORDER BY rowid", (number,)):
            key = (row["kind"], row["id"])
            entry = {"kind": row["kind"], "id": short(row["id"], 60), "title": short(row["title"]),
                     "section": clean(row["section"] or ""), "caption": short(row["caption"], CAP_FIELD),
                     "data": short(row["data"] or ""), "command": short(row["command"] or "")}
            if row["kind"] == "table":
                table = first_table(texts.get(key, ""))
                seen = set()
                entry["table"] = {"head": [self.runs(cell, seen) for cell in table[0]],
                                  "rows": [[self.runs(cell, seen) for cell in row_] for row_ in table[1]],
                                  "cut": table[2]} if table else None
            else:
                entry.update(repo=clean(repos.get(key) or ""), ref=clean(row["ref"] or ""),
                             path=clean(row["path"] or ""))
            out[key] = entry
        return out

    def evidence_model(self, row, thread):
        kind = row["kind"]
        if kind in ("table", "figure"):
            topic = thread["topic"]
            if topic is None or not thread["shown"]:
                return {"kind": kind, "found": False, "shown": False}  # no text of this thread is on the page
            block = self.blocks_of(topic).get((kind, row["ident"]))
            if block is None:
                return {"kind": kind, "found": False, "shown": True}
            return dict(block, found=True, shown=True)
        if kind == "code":
            return {"kind": "code", "repo": clean(row["repo"] or "") or self.repo, "ref": clean(row["ref"] or ""),
                    "path": clean(row["path"] or ""), "from": row["line_from"], "to": row["line_to"]}
        number = int(row["ident"]) if str(row["ident"]).isdigit() else 0
        other = clean(row["repo"] or "")
        title = PHRASES["cite.pull"]
        if not other and number in self.issues and self.issues[number]["pull"] and self.title_shown(number):
            title = short(self.issues[number]["title"]) or title
        return {"kind": "pull", "repo": other or self.repo, "number": number, "title": title}

    def blocks_of(self, number):
        if not hasattr(self, "block_cache"):
            self.block_cache = {}
        if number not in self.block_cache:
            self.block_cache[number] = self.block_parts(number)
        return self.block_cache[number]

    def remind(self, memory):
        before = [p for p in self.pauses if p["wall"] and memory["wall"] and p["wall"] < memory["wall"]
                  and p["id"] != memory["id"]]
        root, hops = memory, 0
        while hops < 12:
            nxt = [self.m[t] for k, t in sorted(root["links"], key=lambda p: LINK_ORDER.index(p[0])) if t in self.m]
            if not nxt:
                break
            root, hops = nxt[0], hops + 1
        thread = self.thread_of[memory["thread"]]["memories"]
        earlier = [m["id"] for m in thread if (m["time"], m["id"]) < (memory["time"], memory["id"])][-2:]
        return {"pause": before[-1]["index"] if before else None, "root": root["id"] if hops > 1 else None,
                "before": earlier}

    def read_pauses(self):
        """Earlier pauses (resume comments of allowed accounts never edited, and told checkpoints) and the last one,
        from meta.resume, which the sync stores only after its author and never-edited checks. The stored block is told
        only when the hub comment that holds it is one of the told pauses of this build: the allowed list may have
        changed since the sync."""
        hub = self.meta("hub")
        hub = int(hub) if hub.isdigit() else None
        edited = index.edited_comments(self.db)
        pauses = []
        resume = clean(index.get_meta(self.db, "resume") or "").strip()
        last_id = None
        if hub is not None:
            for row in self.db.execute("SELECT e.comment, e.at, e.author, c.body FROM events e JOIN comments c ON "
                                       "c.id = e.comment WHERE e.type = 'resume' AND e.issue = ? ORDER BY e.comment",
                                       (hub,)):
                block = recortex.extract_resume_block(row["body"] or "")
                if block is None:
                    continue
                if resume and clean(block).strip() == resume:
                    last_id = row["comment"]
                if not self.ok(row["author"]) or row["comment"] in edited:
                    continue
                focus = next((text for label, text in record_format.fields(block) if label == "Current focus"), "")
                pauses.append({"kind": "resume", "id": row["comment"], "at": clean(row["at"]),
                               "wall": wall_key(row["at"]), "day": day_words(row["at"], self.years),
                               "focus": short(focus, CAP_FIELD)})
        for memory in self.order:
            if memory["kind"] == "checkpoint":
                focus = memory["plain"] or self.field(memory["id"], "State") or memory["title"]
                pauses.append({"kind": "checkpoint", "id": memory["id"], "at": memory["at"], "wall": memory["wall"],
                               "day": memory["day"], "focus": short(focus, CAP_FIELD)})
        pauses.sort(key=lambda p: (p["wall"], p["id"]))
        for i, pause in enumerate(pauses):
            pause["index"] = i
        self.pauses = pauses
        self.last, self.pause_untold = None, False
        last = next((p for p in pauses if p["kind"] == "resume" and p["id"] == last_id), None)
        if resume and last is None:
            # The sync stored this block for the allowed list of its day. The hub comment that holds it is not one
            # this build may tell (its author is not allowed now, it was edited, or it is not in the index yet).
            self.pause_untold = True
        elif resume:
            values = dict(record_format.fields(resume))
            seen = set()
            next_rows = []
            table = first_table(values.get("Next actions", ""))
            if table:
                head = [cell.lower() for cell in table[0]]
                column = lambda name, default: head.index(name) if name in head else default
                spots = (column("action", 0), column("owner", 1), column("where", 2))
                for row in table[1]:
                    cell = lambda i: row[i] if i < len(row) else ""
                    next_rows.append({"action": self.runs(cell(spots[0]), seen), "owner": short(cell(spots[1])),
                                      "where": self.runs(cell(spots[2]), seen)})
            self.last = {"id": last_id, "day": last["day"], "wall": last["wall"],
                         "focus": self.doc(values.get("Current focus", ""), seen),
                         "next": next_rows, "holds": self.doc(values["Holds"], seen) if values.get("Holds") else None}

    # ------------------------------------------------------------ the model

    def model(self):
        self.read_issues()
        self.read_memories()
        for memory in self.order:
            memory["old"] = memory["plain"] is None
        self.read_threads()
        self.read_words()
        for memory in self.order:
            memory["sentence"] = self.sentence(memory)
        for memory in self.order:
            memory["passage"] = self.passage(memory)
            memory["css"], memory["chip"] = self.state_of(memory)
            memory["group"] = {"replaced": "changed", "aside": "changed", "open": "open", "led": "wrong"}.get(
                memory["css"], "wrong" if memory["kind"] == "failure" else "stands")
        for memory in self.order:
            memory["side"] = memory["css"] == "aside" or any(
                key == "checks" and target in self.m and self.m[target]["css"] == "aside"
                for key, target in memory["links"])
        for thread in self.threads:
            seen = set()
            for memory in reversed(thread["memories"]):
                kind = memory["kind"]
                if memory["state"] == "stands" and kind in ("decision", "result") and kind not in seen:
                    seen.add(kind)
                    memory["latest"] = PHRASES["latest." + kind] + (PHRASES["latest.replaced"] if any(
                        key == "replaces" for key, _ in memory["links"]) else "")
        self.read_pauses()
        memories = [self.memory_model(memory) for memory in self.order]
        threads = [self.thread_model(thread) for thread in self.threads]
        words = [self.word_model(n, rows) for n, rows in self.word_rows]
        opened = self.open_model()
        not_told = self.not_told_model()
        self.read_first_lines()
        return {
            "repo": self.repo, "name": self.repo.split("/")[1],
            "built": {"day": self.today.isoformat(), "words": full_day(self.today.isoformat()),
                      "synced_at": self.meta("synced_at"), "complete": self.meta("sync_complete") != "0"},
            "years": self.years,
            "first": self.order[0]["day"] if self.order else None,
            "latest": self.order[-1]["day"] if self.order else None,
            "memories": memories, "threads": threads, "words": words,
            "pauses": [{key: p[key] for key in ("kind", "id", "day", "focus", "wall")} for p in self.pauses],
            "last": self.last,
            "since": [m["id"] for m in self.order if self.last and self.last["wall"] and m["wall"] > self.last["wall"]],
            "latest_of": self.latest_of(),
            "cited": [self.cited[n] for n in sorted(self.cited)],
            "open": opened, "map": self.map_model(),
            "counts": {"memories": len(self.order), "threads": len(self.threads), "words": len(words),
                       "open": sum(1 for m in self.order if m["css"] == "open")},
            "not_told": not_told,
        }

    def latest_of(self):
        out = {}
        for kind in ("decision", "result", "failure", "question"):
            found = [m["id"] for m in self.order if m["kind"] == kind]
            out[kind] = found[-1] if found else None
        return out

    def thread_model(self, thread):
        memories = thread["memories"]
        opened = sum(1 for m in memories if m["css"] == "open")
        if thread["set_aside"]:
            status, css = PHRASES["status.aside"], "aside"
        elif opened:
            status, css = PHRASES["status.open"] if opened == 1 else PHRASES["status.opens"] % opened, "open"
        else:
            status, css = PHRASES["status.settled"], "stands"
        opening = self.openings.get(thread["n"])
        seen = set()
        sections, sources, blocks = [], [], []
        if thread["shown"]:
            topic = thread["topic"]
            parts = self.blocks_of(topic)
            for row in self.db.execute("SELECT ordinal, heading, text FROM sections WHERE issue = ? ORDER BY ordinal",
                                       (topic,)):
                heading = clean(row["heading"])
                if heading.lower() == recortex.SOURCES_HEADING.lower():
                    continue
                prose, skipping = [], False
                for line in clean(row["text"]).split("\n"):
                    if BLOCK_HEADING.match(line):
                        skipping = True
                        continue
                    if skipping and HEADING.match(line):
                        skipping = False
                    if not skipping:
                        prose.append(line)
                sections.append({"heading": short(heading), "doc": self.doc("\n".join(prose), seen, cap=CAP_SECTION),
                                 "blocks": [list(key) for key, block in parts.items() if block["section"] == heading]})
            blocks = [dict(block) for block in parts.values()]
            for row in self.db.execute("SELECT statement, source FROM sources WHERE issue = ? ORDER BY ordinal",
                                       (topic,)):
                sources.append({"statement": self.runs(short(row["statement"], CAP_FIELD), seen),
                                "source": self.runs(short(row["source"], CAP_FIELD), seen)})
        steps, previous, noted = [], None, False
        for memory in memories:
            step = {"id": memory["id"], "conn": None, "note": False}
            if previous is not None:
                direct = [(k, t) for k, t in memory["links"] if t == previous["id"]]
                back = [(k, t) for k, t in memory["links"] if t in self.m and self.m[t]["thread"] == memory["thread"]]
                away = [(k, t) for k, t in memory["links"] if t in self.m]
                if direct:
                    step["conn"] = {"phrase": self.link_phrase(*direct[0]), "to": None}
                elif back:
                    key, target = back[0]
                    step["conn"] = {"phrase": self.link_phrase(key, target) + PHRASES["link.back"],
                                    "to": {"id": target, "day": self.m[target]["day"]}}
                elif away:
                    key, target = away[0]
                    other = self.thread_of[self.m[target]["thread"]]
                    step["conn"] = {"phrase": self.link_phrase(key, target) + PHRASES["link.thread"],
                                    "to": {"thread": other["n"], "name": other["name"]}}
                else:
                    step["conn"] = {"phrase": PHRASES["link.then"], "to": None, "time": True}
            if memory["old"] and not noted:
                step["note"] = noted = True
            steps.append(step)
            previous = memory
        aside_by = [m for m in memories if m["kind"] == "decision"]
        tasks = [task for task in self.tasks() if task["thread"] == thread["n"]]
        return {
            "n": thread["n"], "label": clean(thread["label"]) if thread["label"] else None, "name": thread["name"],
            "name_from": thread["name_from"], "topic": thread["topic"], "issues": thread["issues"],
            "set_aside": thread["set_aside"], "loose": thread["loose"], "shown": thread["shown"],
            "status": status, "status_css": css, "open": opened,
            "opening": {"comment": opening["comment"], "day": day_words(opening["at"], self.years),
                        "about": self.doc(opening["meaning"], seen), "why": self.doc(opening["why"], seen)}
            if opening else None,
            "sections": sections, "blocks": blocks, "sources": sources,
            "memories": [m["id"] for m in memories], "steps": steps,
            "began": memories[0]["day"] if memories else None, "ended": memories[-1]["day"] if memories else None,
            "aside_by": aside_by[-1]["id"] if thread["set_aside"] and aside_by else None,
            "rests": self.rests_on(memories) if not thread["set_aside"] else [],
            "tasks": tasks,
        }

    def rests_on(self, memories):
        """The standing decisions, results and corrections of a thread, newest first, each with what it replaced."""
        out = []
        for memory in reversed(memories):
            if memory["state"] != "stands" or memory["kind"] not in ("decision", "result", "correction"):
                continue
            older, cursor = [], memory
            while len(older) < 50:
                prior = [t for k, t in cursor["links"] if k == "replaces" and t in self.m]
                if not prior or prior[0] in older:
                    break
                older.append(prior[0])
                cursor = self.m[prior[0]]
            out.append({"id": memory["id"], "replaced": older})
        return out

    def tasks(self):
        if not hasattr(self, "task_cache"):
            out = []
            for number, issue in sorted(self.issues.items()):
                if issue["state"] != "open" or issue["pull"] or not self.has_label(number, "active"):
                    continue
                goal = None
                if self.body_shown(number):
                    row = self.db.execute("SELECT text FROM sections WHERE issue = ? AND lower(heading) = ? "
                                          "ORDER BY ordinal LIMIT 1", (number, GOAL)).fetchone()
                    line = next((l.strip() for l in clean(row["text"] if row else "").split("\n") if l.strip()), "")
                    if line:
                        goal = self.runs(short(line), set())
                out.append({"issue": number, "thread": self.thread_by_label.get(self.first_topic.get(number)),
                            "goal": goal,
                            "shown": self.body_shown(number)})
            self.task_cache = out
        return self.task_cache

    def word_model(self, n, rows):
        newest = rows[-1]
        seen = {n}
        uses = [m["id"] for m in self.order if any(mark[2] == n for mark in m["sentence"]["marks"])]
        first = rows[0]
        thread = next((t for t in self.threads if first["issue"] in t["issues"]), None)
        story = self.doc(newest["story"], seen, cap=CAP_STORY) if newest["story"] else None
        return {
            "n": n, "word": short(newest["word"], 60), "meaning": short(newest["meaning"]),
            "why": self.doc(newest["why"], seen) if newest["why"] else None,
            "also": [short(name, 60) for name in newest["also"]], "story": story,
            "first": {"comment": first["comment"], "day": day_words(first["at"], self.years),
                      "told": first["comment"] in self.m,
                      "thread": thread["n"] if thread else None, "thread_name": thread["name"] if thread else None},
            "earlier": [{"comment": row["comment"], "meaning": short(row["meaning"]),
                         "day": day_words(row["at"], self.years)} for row in rows[:-1]],
            "uses": uses,
        }

    def open_model(self):
        set_aside = {t["n"] for t in self.threads if t["set_aside"]}
        unknown = []
        for memory in self.order:
            text = self.field(memory["id"], "Not established")
            if text and memory["kind"] == "result" and memory["state"] == "stands" and memory["thread"] not in set_aside:
                unknown.append({"id": memory["id"], "doc": self.doc(text, set())})

        def age(memory):
            parts = wall(memory["at"])
            if not parts:
                return None
            return max(0, (self.today - datetime.date(*parts[:3])).days)

        pick = lambda test: [{"id": m["id"], "age": age(m)} for m in self.order if test(m)]
        return {
            "questions": pick(lambda m: m["kind"] == "question" and m["state"] == "open"),
            "ideas": pick(lambda m: m["kind"] == "idea" and m["state"] == "untried"),
            "failures": pick(lambda m: m["kind"] == "failure" and m["css"] == "open"),
            "unknown": unknown,
            "holds": self.last["holds"] if self.last else None,
            "tasks": self.tasks(),
        }

    def not_told_model(self):
        edited = []
        for cid in self.found["edited_ids"]:
            row = self.db.execute("SELECT issue FROM events WHERE comment = ?", (cid,)).fetchone()
            if row is not None:
                edited.append({"id": cid, "issue": row["issue"]})
        bodies = [{"thread": t["n"], "topic": t["topic"]} for t in self.threads if t["topic"] and not t["shown"]]
        return {"others": self.found["hidden"], "edited": self.found["edited"], "edited_ids": edited,
                "older": self.older, "words": self.words_cut, "bodies": bodies, "pause": self.pause_untold}

    # ------------------------------------------------------------ the map

    def map_model(self):
        width, left, right, top, gap = 1080, 226, 30, 64, 104
        timed = [m for m in self.order if wall(m["at"])]
        if not timed:
            return None
        moment = lambda at: datetime.datetime(*wall(at))
        start = min(moment(m["at"]) for m in timed).replace(hour=0, minute=0)
        end = max(moment(m["at"]) for m in timed)
        span = (end - start).total_seconds() + 86400
        scale = lambda value: left + (width - left - right) * (value - start).total_seconds() / span
        rows = [t for t in self.threads if t["memories"]]
        folded = []
        if len(rows) > CAP_MAP_THREADS:
            keep = sorted(rows, key=lambda t: -len(t["memories"]))[:CAP_MAP_THREADS - 1]
            folded = [t for t in rows if t not in keep]
            rows = [t for t in rows if t in keep]
        lines = [(t["n"], t["name"], t["memories"]) for t in rows]
        if folded:
            lines.append((None, None, sorted((m for t in folded for m in t["memories"]),
                                             key=lambda m: (m["time"], m["id"]))))
        height = top + gap * len(lines) + 30
        days = (end.date() - start.date()).days + 1
        step = max(1, -(-days // 8))
        ticks = []
        for offset in range(0, days + 1, step):
            value = start + datetime.timedelta(days=offset)
            ticks.append({"x": round(scale(value), 1), "day": day_words(value.strftime("%Y-%m-%d"), self.years)})
        position, rows_out = {}, []
        for row, (n, name, memories) in enumerate(lines):
            y, last_x = top + gap * row + 30, 0
            xs = []
            for memory in memories:
                if not wall(memory["at"]):
                    continue
                x = max(scale(moment(memory["at"])), last_x + 26)
                last_x = x
                position[memory["id"]] = (round(x, 1), y + (34 if memory["side"] else 0), memory["side"], y)
                xs.append(x)
            status = None
            if n is not None:
                thread = self.thread_of[n]
                opened = sum(1 for m in thread["memories"] if m["css"] == "open")
                status = "aside" if thread["set_aside"] else "open" if opened else "stands"
            rows_out.append({"n": n, "name": name, "y": y, "x1": round(min(xs) - 14, 1) if xs else left,
                             "x2": round(max(xs) + 14, 1) if xs else left, "status_css": status,
                             "folded": len(folded) if n is None else 0})
        width = max(width, int(max([p[0] for p in position.values()] or [0]) + right + 10))
        dots = [{"id": m["id"], "x": position[m["id"]][0], "y": position[m["id"]][1], "side": position[m["id"]][2],
                 "row_y": position[m["id"]][3], "kind": m["kind"], "css": m["css"]}
                for m in self.order if m["id"] in position]
        links = []
        for memory in self.order:
            for key, target in memory["links"]:
                if target in position and memory["id"] in position:
                    links.append({"a": target, "b": memory["id"], "kind": key,
                                  "x1": position[target][0], "y1": position[target][1],
                                  "x2": position[memory["id"]][0], "y2": position[memory["id"]][1]})
        pauses = [{"x": round(scale(datetime.datetime(*wall(p["at"]))), 1)} for p in self.pauses
                  if wall(p["at"]) and start <= datetime.datetime(*wall(p["at"])) <= end + datetime.timedelta(days=1)]
        return {"width": width, "height": height, "top": top, "rows": rows_out, "dots": dots, "links": links,
                "ticks": ticks, "pauses": pauses, "folded": len(folded)}
