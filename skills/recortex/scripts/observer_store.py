"""Private pending observations and publication receipts, separate from the disposable index.

SQLite transactions keep source cursors with metadata. Raw evidence is accepted
only with a completed independent review, bound to the exact candidate. No
operation in this module contacts GitHub or executes source text.
"""

import contextlib
import hashlib
import json
import math
import os
import re
import sqlite3
import time
import unicodedata
from urllib.parse import quote

import observer_holds
import recortex
import record_format
import storage

try:
    import fcntl
except ImportError:
    fcntl = None


COUNT_MAX = 10 ** 9   # a token count the store keeps; anything else is stored as 0
COMMENT_MAX = 2 ** 53   # a comment number on GitHub (about 7 * 10 ** 9 in 2026) is far above COUNT_MAX
USAGE_DAYS = 35       # how long a call's token counts are kept
ACTS_FAILED = "acts_failed"            # control: {"n": acts the log could not take, "day": UTC day of the last, YYYY-MM-DD}
ACTS_FAILED_TOLD = "acts_failed_told"  # control: the UTC day the session-start notice for it was last shown
ACTS_FAILED_DAYS = 7  # a failure older than this is not "still failing": no notice, and the count starts again
DAY_FORM = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
MODEL = re.compile(r"[A-Za-z0-9_.:-]{1,100}")
DEFAULT_BYTES = 128 * 1024 * 1024
DEFAULT_PENDING = 10000
TERMINAL = frozenset(("verified", "ignored", "rejected"))
STATES = frozenset(("observed", "candidate", "reviewed", "ready", "sending", "verified",
                    "deferred", "ignored", "rejected", "uncertain"))
TRANSITIONS = {"ready": {"sending", "deferred", "rejected"},
               "sending": {"verified", "uncertain"}, "uncertain": {"verified"},
               "deferred": {"ready", "rejected", "sending"}, "reviewed": {"ready", "deferred", "rejected"}}
SOURCE_FIELDS = frozenset(("source_id", "session_id", "role", "event_type", "path", "offset", "end",
                           "sha256", "content_sha256", "observed_at", "via", "call_id", "pair_offset", "pair_end",
                           "pair_sha256"))
SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}")
KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")
HEX = re.compile(r"[0-9a-f]{64}")
EVENT_KEY = re.compile(r"[0-9a-f]{32}")
SCHEMA_3 = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS controls(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cursors(stream TEXT PRIMARY KEY, position INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS observations(source_id TEXT PRIMARY KEY, metadata TEXT NOT NULL,
 state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS invocations(id INTEGER PRIMARY KEY AUTOINCREMENT, purpose TEXT NOT NULL,
 created REAL NOT NULL, completed REAL, success INTEGER, output_hash TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS events(eventkey TEXT PRIMARY KEY, source_id TEXT NOT NULL, candidate TEXT NOT NULL,
 review TEXT NOT NULL, body TEXT NOT NULL, payload_hash TEXT NOT NULL, state TEXT NOT NULL,
 account TEXT NOT NULL DEFAULT '', receipt TEXT NOT NULL DEFAULT '{}', reason TEXT NOT NULL DEFAULT '',
 created REAL NOT NULL, updated REAL NOT NULL, body_hash TEXT NOT NULL DEFAULT '', words_hash TEXT NOT NULL DEFAULT '',
 held INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS event_sources(source_id TEXT PRIMARY KEY, eventkey TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transitions(eventkey TEXT NOT NULL, state TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS event_targets(eventkey TEXT NOT NULL, target TEXT NOT NULL, PRIMARY KEY(eventkey, target));
"""
# Format 2 added to each event the digests its copies are looked up by and the held mark (copy_of, decision_of);
# format 3, the memories each event changes the status of (event_targets, held_change_of); format 4 (1.5.0), the
# tier, model, job and token counts of each call (the caps' ledger and the usage view) and the tables of the acts the
# observer did, of the drafts of the paper's text that wait for the user's answer, and of sorted stretches.
FORMAT = "4"
ADDED = (("body_hash", "TEXT NOT NULL DEFAULT ''"), ("words_hash", "TEXT NOT NULL DEFAULT ''"),
         ("held", "INTEGER NOT NULL DEFAULT 0"))
TARGETS = ("CREATE TABLE IF NOT EXISTS event_targets(eventkey TEXT NOT NULL, target TEXT NOT NULL, PRIMARY KEY(eventkey, "
           "target));\n")
SCHEMA_2 = SCHEMA_3.replace(TARGETS, "")
SCHEMA_1 = SCHEMA_2.replace(", body_hash TEXT NOT NULL DEFAULT '', words_hash TEXT NOT NULL DEFAULT '',\n held INTEGER "
                            "NOT NULL DEFAULT 0)", ")")
INVOCATION_ADDED = (("tier", "TEXT NOT NULL DEFAULT ''"), ("model", "TEXT NOT NULL DEFAULT ''"),
                    ("job", "TEXT NOT NULL DEFAULT ''"), ("input_tokens", "INTEGER NOT NULL DEFAULT 0"),
                    ("output_tokens", "INTEGER NOT NULL DEFAULT 0"), ("cache_read", "INTEGER NOT NULL DEFAULT 0"),
                    ("cache_write", "INTEGER NOT NULL DEFAULT 0"))
NEW_TABLES = """
CREATE TABLE IF NOT EXISTS acts(id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, kind TEXT NOT NULL,
 issue INTEGER NOT NULL DEFAULT 0, comment INTEGER NOT NULL DEFAULT 0, numbers TEXT NOT NULL DEFAULT '[]',
 backup TEXT NOT NULL DEFAULT '', undone INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, topic INTEGER NOT NULL, headings TEXT NOT NULL,
 folder TEXT NOT NULL, base_hash TEXT NOT NULL, due_hash TEXT NOT NULL DEFAULT '', state TEXT NOT NULL,
 reason TEXT NOT NULL DEFAULT '', until REAL NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS labels(batch TEXT PRIMARY KEY, sources TEXT NOT NULL, labels TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'waiting', created REAL NOT NULL, updated REAL NOT NULL);
"""
SCHEMA = (SCHEMA_3.replace("output_hash TEXT NOT NULL DEFAULT '');", "output_hash TEXT NOT NULL DEFAULT '', " +
                           ", ".join("%s %s" % pair for pair in INVOCATION_ADDED) + ");") + NEW_TABLES)
SCHEMAS = {"1": SCHEMA_1, "2": SCHEMA_2, "3": SCHEMA_3, "4": SCHEMA}
INDEXES_2 = ("CREATE INDEX IF NOT EXISTS events_body ON events(body_hash)",
             "CREATE INDEX IF NOT EXISTS events_words ON events(words_hash)")
INDEXES_3 = INDEXES_2 + ("CREATE INDEX IF NOT EXISTS event_targets_target ON event_targets(target)",)
INDEXES = INDEXES_3 + ("CREATE INDEX IF NOT EXISTS invocations_tier ON invocations(tier, created)",
                       "CREATE INDEX IF NOT EXISTS drafts_state ON drafts(state)")
INDEX_SETS = {"2": INDEXES_2, "3": INDEXES_3, "4": INDEXES}
LOOKUP_FORMATS = ("3", FORMAT)   # the formats that hold the tables the lookups read; a read-only older one cannot
ACT_KINDS = ("memory", "task_opened", "steps_added", "step_marked", "task_rendered", "task_closed", "hub_rendered",
             "summary", "board", "draft_proposed", "draft_applied")
DRAFT_STATES = ("waiting", "refused", "stale", "applied", "dropped", "later")
DRAFT_ID = re.compile(r"[0-9a-f]{8}")
BATCH = re.compile(r"[0-9a-f]{32}")
BACKUP = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")  # the name of a file in the private folder, never a path
RELEASED = "hold_released"   # the control: the requests the user lifted, per source, until the source is handled
RELEASED_MAX = 1000
QUOTE_MAX, QUOTES_MAX, QUOTES_JOIN = 120, 300, " | "
STATUS_KEYS = ("replaces", "instead", "checks")  # the links that change an earlier memory's status
# Reasons an earlier queue kept an event for that are no longer given: back-filled as held.
EARLIER_HELD = ("sensitive_project", "confidentiality", "sheet_not_approved")
# The reasons for which a steps memory that opens a task waits as a task to open ("Open it") and not as a held memory.
TASK_ASKS = ("level_ask", "withheld_review")


class StoreError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__("observer store unavailable: " + reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def candidate_hash(candidate):
    return hashlib.sha256(canonical(candidate).encode("utf-8")).hexdigest()


def day_of(moment):
    """The UTC day of a time, as YYYY-MM-DD."""
    return time.strftime("%Y-%m-%d", time.gmtime(moment))


def _count(value):
    """A token count from a model call's usage: an integer from 0 to 10^9, else 0. A bool, a float or a text is no count."""
    return value if type(value) is int and 0 <= value <= COUNT_MAX else 0


def _id(value):
    if not isinstance(value, str) or not SOURCE_ID.fullmatch(value):
        raise StoreError("invalid_source")
    return value


def _reason(value):
    if not isinstance(value, str) or not KEY.fullmatch(value):
        raise StoreError("invalid_reason")
    return value


def clean_quote(text, limit=QUOTE_MAX):
    """The typed words of a hold, as data for a model: one line, no control or format character, at most `limit`
    characters (QUOTE_MAX for one hold's words). "" for anything that is not a string or holds no word."""
    if not isinstance(text, str):
        return ""
    kept = "".join(" " if char.isspace() else char for char in text if unicodedata.category(char) not in ("Cc", "Cf", "Cs", "Zl", "Zp") or char.isspace())
    return " ".join(kept.split())[:limit].strip()


def _lifted(value):
    """{source id: [quotes]} of a stored RELEASED control, with whatever is not in that form left out."""
    if not isinstance(value, dict):
        return {}
    out = {}
    for sid, quotes in value.items():
        if isinstance(sid, str) and isinstance(quotes, list):
            cleaned = [clean_quote(words) for words in quotes[:8]]
            if any(cleaned):
                out[sid] = [words for words in cleaned if words]
    return out


def event_key(repo, sources):
    return hashlib.sha256(canonical([repo.lower(), sorted(set(sources))]).encode("utf-8")).hexdigest()[:32]


@contextlib.contextmanager
def _exclusive_lock(folder, name, wait=0):
    if fcntl is None:
        raise StoreError("locking_unavailable")
    path = os.path.join(folder, name)
    if os.path.lexists(path):
        storage.check_path(path, "file")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not storage.same_file(path, os.fstat(descriptor)):
            raise StoreError("changed")
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as error:
                if time.monotonic() >= deadline:
                    raise StoreError("publisher_busy" if not wait else "store_busy") from error
                time.sleep(0.01)
        yield
    finally:
        os.close(descriptor)


COMMAND_HOLDS = "command-holds.json"
GRANTS = "answer-grants.json"  # one-time grants to post a held event, from the user's answer (observer_hooks)
GRANT_SECONDS = 600
CLOSE_ITEMS = "task_close_items"   # control: [{"issue", "created"}], the finished tasks the user is asked to close
CLOSE_MAX = 50
LATER = "waiting_later"          # control: {short id: time until which "Not now" puts the item off}
LATER_MAX = 100
ANSWER_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
USED = re.compile(r"[A-Za-z0-9_-]{1,128}:[0-9a-f]{32}")  # an answer and the held event it granted


def _command_digest(command):
    return hashlib.sha256(command.strip().encode("utf-8")).hexdigest()


def read_holds(repo, tries=3):
    """The holds, for the check before a tool call: read through a read-only connection with a short retry, without
    the initialization lock or a write, so that a busy queue does not keep a hold from being checked. [] when this
    machine has no queue for the repository. Raises StoreError when they cannot be read."""
    folder = storage.folder(storage.state_top(), storage.repo_folder(repo.lower()), "observer", create=False,
                            tighten=False)
    if folder is None:
        return []
    path = os.path.join(folder, "pending.sqlite")
    before = storage.check_path(path, "file")
    if not before:
        return []
    for attempt in range(tries):
        db = None
        try:
            db = sqlite3.connect("file:" + quote(path, safe="/") + "?mode=ro", uri=True, timeout=0.1,
                                 isolation_level=None)
            if not storage.same_file(path, before):
                raise StoreError("changed")
            meta = dict(db.execute("SELECT key,value FROM meta"))
            if meta.get("repo") != repo.lower() or meta.get("mark") != storage.mark(path, repo.lower(), create=False,
                                                                                      tighten=False):
                raise StoreError("identity")
            row = db.execute("SELECT value FROM controls WHERE key='holds'").fetchone()
            holds = json.loads(row[0]) if row else []
            if not isinstance(holds, list):
                raise StoreError("invalid_holds")
            return holds
        except (sqlite3.Error, ValueError) as error:
            if attempt + 1 == tries:
                raise StoreError("unreadable") from error
            time.sleep(0.05)
        finally:
            if db is not None:
                db.close()


def held_commands(repo):
    """The digests of the commands under active exact-command holds, from the file kept beside the queue: what is
    still known about them when the queue itself cannot be read. update_holds keeps it a superset of the queue's held
    commands at every moment. Raises StoreError when it is unknown: missing, unreadable or not of its form; an unknown
    set never counts as empty."""
    try:
        folder = storage.folder(storage.state_top(), storage.repo_folder(repo.lower()), "observer", create=False,
                                tighten=False)
        text = storage.read_file(os.path.join(folder, COMMAND_HOLDS)) if folder else None
        if text is None:
            raise StoreError("unreadable")
        digests = json.loads(text)
    except (storage.StorageError, OSError, ValueError) as error:
        raise StoreError("unreadable") from error
    if not isinstance(digests, list) or not all(isinstance(item, str) and HEX.fullmatch(item) for item in digests):
        raise StoreError("unreadable")
    return set(digests)


def exact_words(body):
    """The text of a decision's "Exact words" field: its quoted lines, joined."""
    fields = dict(record_format.fields(body))
    return "\n".join(line.lstrip()[1:].lstrip() for line in fields.get("Exact words", "").splitlines()
                     if line.lstrip().startswith(">"))


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def copy_digests(kind, body):
    """(body digest, words digest) of a reviewed body, as its copies are looked up: the body without its eventkey,
    normalized as GitHub's copy is compared, and for a decision its exact words ("" otherwise)."""
    plain = recortex.normalize(re.sub(r" eventkey=[0-9a-f]{32}", "", body or "", count=1))
    words = exact_words(body or "").strip() if kind == "decision" else ""
    return _digest(plain), _digest(words) if words else ""


def status_targets(body):
    """The comment ids whose status `body` changes, by the replaces=, instead= and checks= links of its marker."""
    marker = record_format.parse_marker(record_format.first_lines(body or "")[0]) or {}
    return sorted({str(target) for key, target in record_format.links(marker) if key in STATUS_KEYS})


def _held_before(reason):
    """True for a reason an event was deferred or discarded with that kept it from posting (HELD_REASONS, earlier
    ones, and a format 1 discard's "discarded_<reason>")."""
    reason = reason[len("discarded_"):] if reason.startswith("discarded_") else reason
    return reason in observer_holds.HELD_REASONS or reason in EARLIER_HELD


def description(kind, body):
    """What the session's question about a held event quotes, whitespace normalized: the memory's plain line, or, for
    a body without one, its kind and the first 120 characters of its first paragraph (the event marker aside)."""
    line = record_format.plain(body or "", kind)
    if not line or not line.strip():
        text = re.sub(r"<!--.*?-->", "", body or "", count=1, flags=re.S)
        paragraph = next((part for part in re.split(r"\n[ \t]*\n", text) if part.strip()), "")
        line = "%s: %s" % (kind, " ".join(paragraph.split())[:120])
    return " ".join(line.split())


CANDIDATE_KEYS = frozenset(("kind", "issue", "body", "sources", "excerpts", "task_title", "topic", "marks", "links",
                            "with", "because_with"))
NEW_TASK_KINDS = ("steps", "checkpoint")   # the kinds that may name issue 0: the task a steps memory of the pass opens
LINK_PARTS = ("checks", "against")         # the keys a plan link may take
TASK_TITLE = 120


def _check_extras(candidate, issue):
    """The fields 1.5.0 added to a reviewed candidate, in their strict forms (the worker writes them, the publisher
    composes the marker from them): task_title and topic only for a steps memory that opens a task (issue 0, or the
    to-do task's own items carry neither); marks, a list of {"task", "steps"} (task 0 is the task a steps memory of the
    same pass opens); links, a list of {"key", "part"} naming plan parts by comment id; with, the first source of the
    candidate that opens the task a mark of task 0 is about; because_with (1.5.1, a decision only), the key of the event
    of the failure of the same pass that the decision was made because of. Anything else raises invalid_candidate."""
    def whole(value, low, high):
        return type(value) is int and low <= value <= high
    opens = issue == 0 and candidate["kind"] == "steps"
    if ("task_title" in candidate or "topic" in candidate) and not opens:
        raise StoreError("invalid_candidate")
    if opens:
        title = candidate.get("task_title")
        if (not isinstance(title, str) or not title.strip() or len(title) > TASK_TITLE or "\n" in title
                or not whole(candidate.get("topic"), 0, COUNT_MAX)):
            raise StoreError("invalid_candidate")
    marks = candidate.get("marks", [])
    if not isinstance(marks, list) or len(marks) > 6:
        raise StoreError("invalid_candidate")
    for mark in marks:
        if (not isinstance(mark, dict) or set(mark) != {"task", "steps"} or not whole(mark["task"], 0, 100000)
                or not isinstance(mark["steps"], list) or not 1 <= len(mark["steps"]) <= 20
                or not all(whole(number, 1, 20) for number in mark["steps"])
                or len(set(mark["steps"])) != len(mark["steps"])):
            raise StoreError("invalid_candidate")
    links = candidate.get("links", [])
    if not isinstance(links, list) or len(links) > 4:
        raise StoreError("invalid_candidate")
    for link in links:
        if (not isinstance(link, dict) or set(link) != {"key", "part"} or link["key"] not in LINK_PARTS
                or not whole(link["part"], 1, 10 ** 18)):
            raise StoreError("invalid_candidate")
    if "because_with" in candidate:
        # The failure of the same pass that a decision was made because of: its event's key. Code writes the link.
        key = candidate["because_with"]
        if candidate["kind"] != "decision" or not isinstance(key, str) or not EVENT_KEY.fullmatch(key):
            raise StoreError("invalid_candidate")
    if "with" in candidate:
        _id(candidate["with"])
        if not any(mark["task"] == 0 for mark in marks) and not (issue == 0 and candidate["kind"] == "checkpoint"):
            raise StoreError("invalid_candidate")
    if issue == 0 and candidate["kind"] == "checkpoint" and not any(mark["task"] == 0 for mark in marks):
        raise StoreError("invalid_candidate")


def _with_key(body, key):
    lines = body.splitlines(keepends=True)
    first = next((n for n, line in enumerate(lines) if line.strip()), None)
    if first is None:
        raise StoreError("invalid_candidate")
    marker = record_format.parse_marker(lines[first].strip())
    if not marker or ("eventkey" in marker and marker["eventkey"] != key):
        raise StoreError("invalid_candidate")
    if "eventkey" not in marker:
        line = lines[first].rstrip("\r\n")
        lines[first] = line[:-4] + " eventkey=" + key + " -->\n"
    return "".join(lines)


class Store:
    def __init__(self, repo, create=True, max_bytes=DEFAULT_BYTES, max_pending=DEFAULT_PENDING, config=None, readonly=False,
                 wait=1):
        if not recortex.valid_repo(repo):
            raise StoreError("invalid_repository")
        self.repo = repo.lower()
        self.readonly = readonly
        self.offered_again = 0   # the sources the last update_holds offered to the sort again (a release)
        create = create and not readonly
        self.config = recortex.load_config() if config is None else config
        self.max_bytes = min(DEFAULT_BYTES, max(1, int(max_bytes)))
        self.max_pending = min(DEFAULT_PENDING, max(1, int(max_pending)))
        self.db = None
        self.folder = storage.folder(storage.state_top(), storage.repo_folder(self.repo), "observer", create=create,
                                     tighten=not readonly)
        if self.folder is None:
            raise StoreError("missing")
        self.path = os.path.join(self.folder, "pending.sqlite")
        self.format = None
        if readonly:
            self._read_only()
            return
        # The hooks wait at most 0.1 s for another process's initialization; a busy queue must not slow every call.
        with _exclusive_lock(self.folder, "initialization.lock", wait=max(0.01, min(1, wait))):
            self._initialize(create)

    def _read_only(self):
        before = storage.check_path(self.path, "file")
        if not before:
            raise StoreError("missing")
        self._check_paths()
        try:
            self.db = sqlite3.connect("file:" + quote(self.path, safe="/") + "?mode=ro", uri=True,
                                      timeout=0.2, isolation_level=None)
            if not storage.same_file(self.path, before):
                raise StoreError("changed")
            self.identity = before
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA query_only=ON")
            self._validate_existing()
        except sqlite3.Error as error:
            self.close()
            raise StoreError("corrupt") from error
        except BaseException:
            self.close()
            raise

    def _initialize(self, create):
        exists = storage.check_path(self.path, "file")
        if not exists and not create:
            raise StoreError("missing")
        if not exists:
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                     getattr(os, "O_NOFOLLOW", 0), 0o600)
                os.close(descriptor)
            except FileExistsError:
                storage.check_path(self.path, "file")
        self._check_paths()
        try:
            before = storage.check_path(self.path, "file")
            self.db = sqlite3.connect(self.path, timeout=0.2, isolation_level=None)
            if not storage.same_file(self.path, before):
                raise StoreError("changed")
            self.identity = before
            self.db.row_factory = sqlite3.Row
            # Existing stores are validated before any potentially mutating pragma
            # or schema statement. Missing tables are corruption, never an empty queue.
            initialized = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if initialized:
                self._validate_existing()
            elif exists:
                raise StoreError("corrupt")
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA synchronous=EXTRA")
            self.db.execute("PRAGMA secure_delete=ON")
            self.db.execute("PRAGMA foreign_keys=ON")
            if not initialized:
                with self.transaction():
                    for statement in SCHEMA.split(";"):
                        if statement.strip():
                            self.db.execute(statement)
                    for statement in INDEXES:
                        self.db.execute(statement)
                    self.db.executemany("INSERT INTO meta(key,value) VALUES (?,?)",
                                        [("format", FORMAT), ("repo", self.repo),
                                         ("mark", storage.mark(self.path, self.repo))])
                    self._write_held([])  # a new queue holds no command; a missing file later means unknown
                self.format = FORMAT
            if self.format != FORMAT:
                self._migrate()
            if not initialized:
                descriptor = os.open(self.folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except (sqlite3.Error, OSError) as error:
            self.close()
            raise StoreError("corrupt" if isinstance(error, sqlite3.DatabaseError) else "storage") from error
        except BaseException:
            self.close()
            raise

    def _validate_existing(self):
        """Check an existing queue against its format's schema: the current one, or format 1, which a writable open
        then migrates (_migrate) and a read-only open reads as it is."""
        if [row[0] for row in self.db.execute("PRAGMA quick_check")] != ["ok"]:
            raise StoreError("corrupt")
        actual = dict(self.db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
        if "meta" not in actual:
            raise StoreError("corrupt")
        meta = dict(self.db.execute("SELECT key,value FROM meta"))
        version = meta.get("format") if meta.get("format") in SCHEMAS else FORMAT
        for statement in SCHEMAS[version].split(";"):
            if not statement.strip():
                continue
            expected = re.sub(r"\s+", " ", statement.replace(" IF NOT EXISTS", "")).strip()
            name = expected.split("(", 1)[0].split()[-1]
            if name not in actual or re.sub(r"\s+", " ", actual[name] or "").strip() != expected:
                raise StoreError("corrupt")
        if version != "1":
            indexes = dict(self.db.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"))
            for statement in INDEX_SETS[version]:
                expected = statement.replace(" IF NOT EXISTS", "")
                if re.sub(r"\s+", " ", indexes.get(expected.split()[2]) or "").strip() != expected:
                    raise StoreError("corrupt")
        if meta != {"format": version, "repo": self.repo,
                    "mark": storage.mark(self.path, self.repo, create=False, tighten=False)}:
            raise StoreError("identity")
        self.format = version

    def _migrate(self):
        """An earlier format to the current one in one transaction. From format 1, the columns and indexes are added
        and every event's digests and held mark are back-filled, so that a copy of an earlier event is found as a copy
        of a new one is; from format 1 or 2, the table of the memories each event changes is made and back-filled; from
        any earlier one, the invocations gain their tier, model, job and token columns (a call recorded before has no
        tier and counts as a middle call) and the tables of acts, drafts and labels are made."""
        with self.transaction():
            if self.format == "1":
                for name, kind in ADDED:
                    self.db.execute("ALTER TABLE events ADD COLUMN %s %s" % (name, kind))
                for statement in INDEXES_2:
                    self.db.execute(statement)
            if self.format in ("1", "2"):
                rows = self.db.execute("SELECT eventkey,candidate,reason FROM events").fetchall()
                if self.format == "1":
                    for row in rows:
                        candidate = json.loads(row["candidate"])
                        body_hash, words_hash = copy_digests(candidate.get("kind"), candidate.get("body"))
                        self.db.execute("UPDATE events SET body_hash=?,words_hash=?,held=? WHERE eventkey=?",
                                        (body_hash, words_hash, int(_held_before(row["reason"])), row["eventkey"]))
                self.db.execute(TARGETS)
                self.db.execute(INDEXES_3[-1])
                for row in rows:
                    self._store_targets(row["eventkey"], json.loads(row["candidate"]).get("body"))
            for name, kind in INVOCATION_ADDED:
                self.db.execute("ALTER TABLE invocations ADD COLUMN %s %s" % (name, kind))
            for statement in NEW_TABLES.split(";"):
                if statement.strip():
                    self.db.execute(statement)
            for statement in INDEXES[len(INDEXES_3):]:
                self.db.execute(statement)
            self.db.execute("UPDATE meta SET value=? WHERE key='format'", (FORMAT,))
        self.format = FORMAT

    def _store_targets(self, key, body):
        self.db.executemany("INSERT OR IGNORE INTO event_targets VALUES (?,?)",
                            [(key, target) for target in status_targets(body)])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None

    def _check_paths(self):
        storage.check_path(self.folder)
        storage.check_path(self.path, "file")
        if getattr(self, "identity", None) and not storage.same_file(self.path, self.identity):
            raise StoreError("changed")
        for suffix in ("-journal", "-wal", "-shm"):
            if os.path.lexists(self.path + suffix):
                storage.check_path(self.path + suffix, "file")

    @contextlib.contextmanager
    def transaction(self):
        if self.readonly:
            raise StoreError("read_only")
        self._check_paths()
        try:
            self.db.execute("BEGIN IMMEDIATE")
            yield
            pages = self.db.execute("PRAGMA page_count").fetchone()[0]
            size = self.db.execute("PRAGMA page_size").fetchone()[0]
            if pages * size > self.max_bytes:
                raise StoreError("capacity")
            self.db.execute("COMMIT")
        except BaseException as error:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            if isinstance(error, sqlite3.Error):
                raise StoreError("storage") from error
            raise

    def get_control(self, key, default=None):
        _reason(key)
        row = self.db.execute("SELECT value FROM controls WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_control(self, key, value):
        _reason(key)
        payload = canonical(value)
        if len(payload.encode("utf-8")) > 1024 * 1024:
            raise StoreError("capacity")
        with self.transaction():
            self.db.execute("INSERT OR REPLACE INTO controls VALUES (?,?)", (key, payload))

    def policy(self):
        return self.get_control("settings", {})

    def cursor(self, stream):
        row = self.db.execute("SELECT position FROM cursors WHERE stream=?", (_id(stream),)).fetchone()
        return row[0] if row else None

    def capture(self, observations, stream=None, cursor=None, expected_cursor=None):
        if not isinstance(observations, list) or len(observations) > 1000:
            raise StoreError("invalid_source")
        if stream is not None:
            _id(stream)
            if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
                raise StoreError("invalid_cursor")
        prepared = []
        for metadata in observations:
            if not isinstance(metadata, dict) or set(metadata) - SOURCE_FIELDS:
                raise StoreError("raw_source_refused")
            source = _id(metadata.get("source_id"))
            for key, value in metadata.items():
                if key in ("offset", "end", "observed_at", "pair_offset", "pair_end"):
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                        raise StoreError("invalid_source")
                elif not isinstance(value, str) or len(value) > (4096 if key == "path" else 256):
                    raise StoreError("invalid_source")
            for key in ("sha256", "content_sha256", "pair_sha256"):
                if key in metadata and not HEX.fullmatch(metadata[key]):
                    raise StoreError("invalid_source")
            prepared.append((source, canonical(metadata)))
        now = time.time()
        with self.transaction():
            if stream is not None and self.cursor(stream) != expected_cursor:
                raise StoreError("cursor_changed")
            pending = self.db.execute("SELECT COUNT(*) FROM observations WHERE state NOT IN ('verified','ignored','rejected')").fetchone()[0]
            for source, metadata in prepared:
                old = self.db.execute("SELECT metadata FROM observations WHERE source_id=?", (source,)).fetchone()
                if old:
                    before = json.loads(old[0])
                    after = json.loads(metadata)
                    before.pop("observed_at", None)
                    after.pop("observed_at", None)
                    if before.get("content_sha256") and (before.get("content_sha256"), before.get("role")) == \
                            (after.get("content_sha256"), after.get("role")):
                        # A native record copied into a fork retains its source ID.
                        # Preserve the original pointer instead of re-reviewing or
                        # overwriting frozen evidence with a new session wrapper.
                        continue
                    if before != after:
                        raise StoreError("source_changed")
                    continue
                if pending >= self.max_pending:
                    raise StoreError("capacity")
                self.db.execute("INSERT INTO observations VALUES (?,?,'observed','',?,?)", (source, metadata, now, now))
                pending += 1
            if stream is not None:
                if expected_cursor is not None and cursor < expected_cursor:
                    raise StoreError("cursor_regressed")
                self.db.execute("INSERT OR REPLACE INTO cursors VALUES (?,?)", (stream, cursor))
        return [source for source, _ in prepared]

    def observation(self, source_id):
        row = self.db.execute("SELECT * FROM observations WHERE source_id=?", (_id(source_id),)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["metadata"] = json.loads(item["metadata"])
        return item

    def pending(self, limit=100):
        rows = self.db.execute("SELECT source_id FROM observations WHERE state NOT IN ('verified','ignored','rejected') "
                               "ORDER BY created,source_id LIMIT ?", (max(1, min(10000, int(limit))),))
        return [self.observation(row[0]) for row in rows]

    def _tier_of(self, purpose, tier=None):
        import observer_runtime as runtime  # here: the hooks open the store on every Bash call
        chosen = tier or runtime.TIER_OF.get(purpose) or runtime.OTHER_TIER.get(purpose, "middle")
        if chosen not in runtime.TIERS:
            raise StoreError("invalid_reason")
        return chosen, runtime

    def reserve_invocation(self, purpose, limit=None, window_seconds=86400, tier=None, tokens=None):
        """Reserve one call of `purpose` on its tier (observer_runtime.TIER_OF, or `tier` for a purpose that has none) and
        return its id, or None when the tier's calls in the window have reached `limit` (never above the tier's
        default cap) or its recorded tokens have reached `tokens` (never above the tier's default budget). The budget
        stops the next call; a call is never cut short."""
        _reason(purpose)
        chosen, runtime = self._tier_of(purpose, tier)
        ceiling = runtime.DEFAULTS[runtime.CALL_CAPS[chosen]]
        budget_ceiling = runtime.DEFAULTS[runtime.TOKEN_BUDGETS[chosen]]
        cap = ceiling if limit is None else min(ceiling, max(0, int(limit)))
        budget = budget_ceiling if tokens is None else min(budget_ceiling, max(0, int(tokens)))
        job = runtime.JOB_OF.get(purpose, "")
        now = time.time()
        with self.transaction():
            window = max(86400, min(168 * 3600, int(window_seconds)))
            # A call recorded before format 4 has no tier and counts as a middle call.
            row = self.db.execute("SELECT COUNT(*), COALESCE(SUM(input_tokens + output_tokens), 0) FROM invocations "
                                  "WHERE created>? AND (tier=? OR (tier='' AND ?='middle'))",
                                  (now - window, chosen, chosen)).fetchone()
            if row[0] >= cap or row[1] >= budget:
                return None
            return self.db.execute("INSERT INTO invocations(purpose,created,tier,job) VALUES (?,?,?,?)",
                                   (purpose, now, chosen, job)).lastrowid

    def complete_invocation(self, ident, success, output_hash="", usage=None, model=""):
        """Close a reservation. `usage` is the call's usage as the runtime returned it: only the four token counts are
        read, each kept as an integer from 0 to 10^9 and else as 0; the input tokens are the fresh, cache-write and
        cache-read ones added, as the evaluation counts them. `model` is kept when it is a model name."""
        if output_hash and not HEX.fullmatch(output_hash):
            raise StoreError("invalid_review")
        usage = usage if isinstance(usage, dict) else {}
        fresh, written, read, out = (_count(usage.get(key)) for key in (
            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
        name = model if isinstance(model, str) and MODEL.fullmatch(model) else ""
        with self.transaction():
            row = self.db.execute("SELECT completed FROM invocations WHERE id=?", (ident,)).fetchone()
            if row is None or row[0] is not None:
                raise StoreError("invalid_review")
            self.db.execute("UPDATE invocations SET completed=?,success=?,output_hash=?,model=?,input_tokens=?,"
                            "output_tokens=?,cache_read=?,cache_write=? WHERE id=?",
                            (time.time(), int(success is True), output_hash, name, min(COUNT_MAX, fresh + written + read),
                             out, read, written, ident))

    def usage_today(self, window_seconds=86400):
        """{tier: {"calls", "input_tokens", "output_tokens"}} for the window, every tier present: what the caps count.
        Calls recorded before format 4 count as middle calls."""
        import observer_runtime as runtime
        since = time.time() - max(86400, min(168 * 3600, int(window_seconds)))
        out = {tier: {"calls": 0, "input_tokens": 0, "output_tokens": 0} for tier in runtime.TIERS}
        for row in self.db.execute("SELECT CASE WHEN tier='' THEN 'middle' ELSE tier END, COUNT(*), SUM(input_tokens), "
                                   "SUM(output_tokens) FROM invocations WHERE created>? GROUP BY 1", (since,)):
            if row[0] in out:
                out[row[0]] = {"calls": row[1], "input_tokens": row[2] or 0, "output_tokens": row[3] or 0}
        return out

    def usage_rows(self, days=30):
        """The calls of the last `days` days (1 to 35), newest first, as plain rows for the usage view: when, tier, job,
        model, whether it succeeded and its token counts. Nothing but numbers, a tier, a job and a model name."""
        since = time.time() - max(1, min(USAGE_DAYS, int(days))) * 86400
        rows = self.db.execute("SELECT created, CASE WHEN tier='' THEN 'middle' ELSE tier END, job, model, success, "
                               "input_tokens, output_tokens, cache_read, cache_write FROM invocations WHERE created>? "
                               "ORDER BY created DESC, id DESC", (since,))
        return [{"at": row[0], "tier": row[1], "job": row[2], "model": row[3], "success": row[4] == 1,
                 "input_tokens": row[5], "output_tokens": row[6], "cache_read": row[7], "cache_write": row[8]}
                for row in rows]

    def expire_usage(self, days=USAGE_DAYS):
        """Zero the token counts of calls older than `days` (35): their times stay, as the cap's ledger needs them."""
        cutoff = time.time() - max(1, int(days)) * 86400
        with self.transaction():
            return self.db.execute("UPDATE invocations SET input_tokens=0,output_tokens=0,cache_read=0,cache_write=0 "
                                   "WHERE created<? AND input_tokens+output_tokens+cache_read+cache_write>0",
                                   (cutoff,)).rowcount

    def _observation_state(self, source_id, state, reason=""):
        if state not in STATES or self.observation(source_id) is None:
            raise StoreError("invalid_state")
        self.db.execute("UPDATE observations SET state=?,reason=?,updated=? WHERE source_id=?",
                        (state, reason, time.time(), source_id))

    def defer(self, source_id, reason):
        with self.transaction():
            self._observation_state(source_id, "deferred", _reason(reason))

    def _overlap(self, source_id):
        existing = self.db.execute("SELECT eventkey FROM event_sources WHERE source_id=?", (source_id,)).fetchone()
        if existing:
            self.db.execute("UPDATE observations SET reason='source_overlap' WHERE source_id=?", (source_id,))
        else:
            self._observation_state(source_id, "deferred", "source_overlap")

    def mark_handled(self, source_id, state="ignored", reason=""):
        if state not in ("ignored", "rejected"):
            raise StoreError("invalid_state")
        with self.transaction():
            self._observation_state(source_id, state, _reason(reason) if reason else "")

    def register_main(self, issue, kind, comment_id, now=None):
        """A memory the main session posted itself, so that nothing is posted twice: the observer drops its own
        candidates of the same kind for the same issue that rest on what came before this post (main_post_after), and
        its ready events of that kind for that issue wait for the main session. The keys of those events are returned.
        The record is refreshed before the next selection."""
        if (type(issue) is not int or issue < 1 or kind not in record_format.OBSERVED_TYPES
                or type(comment_id) is not int or comment_id < 1):
            raise StoreError("invalid_candidate")
        now = time.time() if now is None else now
        posts = [post for post in self.get_control("main_posts", [])
                 if isinstance(post, dict) and type(post.get("at")) in (int, float) and now - post["at"] < 7 * 86400]
        posts = (posts + [{"issue": issue, "kind": kind, "comment_id": comment_id, "at": now}])[-200:]
        with self.transaction():
            self.db.execute("INSERT OR REPLACE INTO controls VALUES ('main_posts',?)", (canonical(posts),))
            self.db.execute("INSERT OR REPLACE INTO controls VALUES ('sync_needed',?)", (canonical(True),))
        waiting = []
        for event in self.events(states=["ready"], limit=10000):
            if event["candidate"]["issue"] == issue and event["candidate"]["kind"] == kind and event["created"] <= now:
                self.transition(event["eventkey"], "deferred", reason="main_session")
                waiting.append(event["eventkey"])
        return waiting

    def main_post_after(self, issue, kind, since):
        """True when the main session posted a memory of this kind for this issue at or after `since`."""
        return any(isinstance(post, dict) and post.get("issue") == issue and post.get("kind") == kind
                   and type(post.get("at")) in (int, float) and post["at"] >= since
                   for post in self.get_control("main_posts", []))

    def review_complete(self, source_id, candidate, review, hold=None):
        """Store a reviewed candidate as a ready event and return its key (None when the review did not accept it, or
        when its sources already belong to another event). With `hold` (a reason of observer_holds.ANSWER_REASONS) the
        reviewer's refusal `withheld` is kept instead: the candidate becomes an event deferred and held for that reason,
        which waits for the user's answer, Post or Drop (waiting), and posts on no other path."""
        _id(source_id)
        if not isinstance(candidate, dict) or set(candidate) - CANDIDATE_KEYS:
            raise StoreError("invalid_candidate")
        sources = candidate.get("sources")
        if not isinstance(sources, list) or not sources or len(sources) > 64:
            raise StoreError("invalid_candidate")
        sources = sorted({_id(source) for source in sources})
        if source_id not in sources or any(self.observation(source) is None for source in sources):
            raise StoreError("missing_source")
        if candidate.get("kind") not in record_format.OBSERVED_TYPES:
            raise StoreError("invalid_candidate")
        issue = candidate.get("issue")
        if isinstance(issue, bool) or not isinstance(issue, int) or issue < 0 or (
                issue == 0 and candidate["kind"] not in NEW_TASK_KINDS):
            raise StoreError("invalid_candidate")   # 0 is for a new task: its steps memory, and a checkpoint on it
        _check_extras(candidate, issue)
        body = candidate.get("body")
        if not isinstance(body, str) or len(body) > recortex.BODY_LIMIT:
            raise StoreError("invalid_candidate")
        excerpts = candidate.get("excerpts", [])
        if not isinstance(excerpts, list) or any(not isinstance(x, str) for x in excerpts) or sum(map(len, excerpts)) > 48000:
            raise StoreError("invalid_candidate")
        import lint_drafts  # here: the hooks open the store on every Bash call
        restricted = lint_drafts.lint_text(body + "\n" + "\n".join(excerpts), kind="generic", config=self.config)
        if any(f.level == "error" and f.code in ("secret", "restricted") for f in restricted):
            raise StoreError("restricted")
        if not isinstance(review, dict) or review.get("candidate_hash") != candidate_hash(candidate):
            raise StoreError("review_changed")
        if hold is not None:
            if (hold not in observer_holds.ANSWER_REASONS or review.get("accepted") is not False
                    or review.get("reason") != "withheld"):
                raise StoreError("invalid_review")
        elif review.get("accepted") is not True:
            self.mark_handled(source_id, "rejected")
            return None
        state, reason = ("deferred", hold) if hold else ("ready", "")
        key = event_key(self.repo, sources)
        posted_body = _with_key(body, key)
        now = time.time()
        with self.transaction():
            invocation = self.db.execute("SELECT * FROM invocations WHERE id=?", (review.get("reservation_id"),)).fetchone()
            if invocation is None or invocation["purpose"] not in ("review", "reviewer") or invocation["success"] != 1 \
                    or invocation["output_hash"] != review["candidate_hash"]:
                raise StoreError("invalid_review")
            existing = self.event(key)
            if existing:
                if candidate_hash(existing["candidate"]) == candidate_hash(candidate):
                    return key
                self._overlap(source_id)
                return None
            # A decision drafted because of a failure of the same pass may rest on messages that failure rests on: the user's
            # one message holds both. Those stay the failure's rows; the decision's own sources (it may have none) are its.
            shared = set()
            if "because_with" in candidate:
                parent = self.event(candidate["because_with"])
                if parent is None or parent["candidate"]["kind"] != "failure" or parent["eventkey"] == key:
                    raise StoreError("invalid_candidate")
                shared = {row[0] for row in self.db.execute("SELECT source_id FROM event_sources WHERE eventkey=?",
                                                            (parent["eventkey"],))}
            own = [source for source in sources if source not in shared]
            overlap = own and self.db.execute("SELECT eventkey FROM event_sources WHERE source_id IN (%s)" % ",".join("?" * len(own)), own).fetchone()
            if overlap:
                self._overlap(source_id)
                return None
            body_hash, words_hash = copy_digests(candidate["kind"], body)
            self.db.execute("INSERT INTO events(eventkey,source_id,candidate,review,body,payload_hash,state,reason,created,"
                            "updated,body_hash,words_hash,held) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (key, source_id, canonical(candidate), canonical(review), posted_body,
                             hashlib.sha256(posted_body.encode("utf-8")).hexdigest(), state, reason, now, now, body_hash,
                             words_hash, int(bool(hold))))
            self.db.executemany("INSERT INTO event_sources VALUES (?,?)", [(source, key) for source in own])
            self._store_targets(key, body)
            for step in ("candidate", "reviewed", state):
                self.db.execute("INSERT INTO transitions VALUES (?,?,?)", (key, step, now))
            for source in own:
                self._observation_state(source, state, reason)
        return key

    def event(self, key):
        if not isinstance(key, str) or not EVENT_KEY.fullmatch(key):
            raise StoreError("invalid_event")
        row = self.db.execute("SELECT * FROM events WHERE eventkey=?", (key,)).fetchone()
        if row is None:
            return None
        value = dict(row)
        for name in ("candidate", "review", "receipt"):
            value[name] = json.loads(value[name])
        return value

    def recompose(self, key, body):
        """Replace the frozen body of a ready or deferred event by the body the publisher composed from its reviewed
        candidate (observer_publisher.compose: the marks and plan links resolved into the marker). The eventkey and the
        type in the marker must be the event's; the digests its copies are looked up by and the memories it changes the
        status of follow the new body. An event already sending, uncertain or verified keeps the body it was sent with."""
        with self.transaction():
            event = self.event(key)
            if event is None or event["state"] not in ("ready", "deferred"):
                raise StoreError("invalid_transition")
            marker = record_format.parse_marker(record_format.first_lines(body)[0]) or {}
            if (not isinstance(body, str) or len(body) > recortex.BODY_LIMIT or marker.get("eventkey") != key
                    or marker.get("type") != event["candidate"]["kind"]):
                raise StoreError("invalid_candidate")
            if body == event["body"]:
                return False
            body_hash, words_hash = copy_digests(event["candidate"]["kind"], body)
            self.db.execute("UPDATE events SET body=?,payload_hash=?,body_hash=?,words_hash=?,updated=? WHERE eventkey=?",
                            (body, hashlib.sha256(body.encode("utf-8")).hexdigest(), body_hash, words_hash,
                             time.time(), key))
            self.db.execute("DELETE FROM event_targets WHERE eventkey=?", (key,))
            self._store_targets(key, body)
            return True

    def event_by_source(self, source_id):
        """The event whose first source is `source_id` (events.source_id), or None."""
        row = self.db.execute("SELECT eventkey FROM events WHERE source_id=?", (_id(source_id),)).fetchone()
        return self.event(row[0]) if row else None

    def recent_events(self, since, limit=200):
        """The events created after `since`, newest first (at most `limit`)."""
        rows = self.db.execute("SELECT eventkey FROM events WHERE created>? ORDER BY created DESC,eventkey LIMIT ?",
                               (float(since), max(1, min(1000, int(limit)))))
        return [self.event(row[0]) for row in rows.fetchall()]

    def review_valid(self, event):
        review = event.get("review", {})
        row = self.db.execute("SELECT purpose,success,output_hash FROM invocations WHERE id=?",
                              (review.get("reservation_id"),)).fetchone()
        return (row is not None and row["purpose"] in ("review", "reviewer") and row["success"] == 1
                and row["output_hash"] == review.get("candidate_hash") == candidate_hash(event["candidate"]))

    def events(self, states=None, limit=100):
        states = tuple(states or sorted(STATES))
        if any(state not in STATES for state in states):
            raise StoreError("invalid_state")
        rows = self.db.execute("SELECT eventkey FROM events WHERE state IN (%s) ORDER BY created,eventkey LIMIT ?" %
                               ",".join("?" * len(states)), states + (max(1, min(10000, int(limit))),))
        return [self.event(row[0]) for row in rows]

    def copy_of(self, draft):
        """The newest event, in any state, whose reviewed body `draft` copies (its eventkey aside), or None. Looked up
        by digest across the whole queue. Raises StoreError when the lookup cannot complete, so that a post that
        cannot be checked is refused."""
        return self._lookup("body_hash=?", copy_digests(None, draft)[0])

    def decision_of(self, draft):
        """The newest decision event with the exact words of `draft`, or None: in any state but discarded, and a
        discarded one too when it was held (held mark). Raises StoreError as copy_of."""
        words = exact_words(draft or "").strip()
        return self._lookup("words_hash=? AND (state!='rejected' OR held=1)", _digest(words)) if words else None

    def held_change_of(self, draft):
        """A held event still waiting (ready or deferred) that changes the status of a memory `draft` changes too (the
        same replaces=, instead= or checks= target), or None: a rewrite of it goes through that event's question, as
        a copy does. Looked up by target in an indexed table, across the whole queue. Raises StoreError as copy_of."""
        targets = status_targets(draft)
        if not targets:
            return None
        if self.format not in LOOKUP_FORMATS:
            raise StoreError("lookup_unavailable")
        try:
            row = self.db.execute("SELECT e.eventkey FROM event_targets t JOIN events e ON e.eventkey=t.eventkey "
                                  "WHERE t.target IN (%s) AND e.held=1 AND e.state IN ('ready','deferred') "
                                  "ORDER BY e.created DESC,e.eventkey LIMIT 1" % ",".join("?" * len(targets)),
                                  targets).fetchone()
            return self.event(row[0]) if row else None
        except (sqlite3.Error, ValueError, TypeError) as error:
            raise StoreError("lookup_unavailable") from error

    def _lookup(self, where, digest):
        if self.format not in LOOKUP_FORMATS:
            raise StoreError("lookup_unavailable")  # a format 1 queue, read only: the next writable open migrates it
        try:
            row = self.db.execute("SELECT eventkey FROM events WHERE %s ORDER BY created DESC,eventkey LIMIT 1" % where,
                                  (digest,)).fetchone()
            return self.event(row[0]) if row else None
        except (sqlite3.Error, ValueError, TypeError) as error:
            raise StoreError("lookup_unavailable") from error

    def _grants(self):
        """{"grants": {eventkey: {"expires", "answer"}}, "used": ["answer:eventkey"]}; anything not of that form counts as
        no grant (an unreadable grant never posts)."""
        empty = {"grants": {}, "used": []}
        try:
            data = json.loads(storage.read_file(os.path.join(self.folder, GRANTS)) or "null")
        except (storage.StorageError, OSError, ValueError, RecursionError):
            return empty
        if not isinstance(data, dict) or set(data) != {"grants", "used"} or not isinstance(data["grants"], dict) \
                or not isinstance(data["used"], list):
            return empty
        grants = {key: grant for key, grant in data["grants"].items()
                  if isinstance(key, str) and EVENT_KEY.fullmatch(key) and isinstance(grant, dict)
                  and set(grant) == {"expires", "answer"} and type(grant["expires"]) in (int, float)
                  and math.isfinite(grant["expires"]) and isinstance(grant["answer"], str)
                  and ANSWER_ID.fullmatch(grant["answer"])}
        used = [item for item in data["used"] if isinstance(item, str) and USED.fullmatch(item)]
        return {"grants": grants, "used": used[-200:]}

    def grant_answer(self, key, answer, now=None):
        """Record the user's answer `answer` (an AskUserQuestion call's id) as a one-time grant to post the held event
        `key`, valid for GRANT_SECONDS. An answer already used for this memory grants it nothing again; one call may answer several memories,
        each once. True when granted."""
        if not isinstance(key, str) or not EVENT_KEY.fullmatch(key) or not isinstance(answer, str) \
                or not ANSWER_ID.fullmatch(answer):
            raise StoreError("invalid_grant")
        now = time.time() if now is None else now
        with _exclusive_lock(self.folder, "grants.lock", wait=0.2):
            data = self._grants()
            if "%s:%s" % (answer, key) in data["used"]:
                return False  # one call may answer several questions, each about its own memory
            data["grants"] = {k: g for k, g in data["grants"].items() if g["expires"] > now}
            data["grants"][key] = {"expires": now + GRANT_SECONDS, "answer": answer}
            storage.write_file(os.path.join(self.folder, GRANTS), canonical(data))
        return True

    def has_grant(self, key, now=None):
        """True while a grant for `key` is valid: not expired, and not set further ahead than GRANT_SECONDS."""
        now = time.time() if now is None else now
        grant = self._grants()["grants"].get(key)
        return bool(grant) and now < grant["expires"] <= now + GRANT_SECONDS

    def take_grant(self, key, now=None):
        """Consume the grant for `key`: True when it was valid. Taken or expired, it is gone, and its answer is marked
        used for this memory, so that the same answer never grants it again."""
        now = time.time() if now is None else now
        with _exclusive_lock(self.folder, "grants.lock", wait=0.2):
            data = self._grants()
            grant = data["grants"].pop(key, None)
            if grant is None:
                return False
            data["used"] = (data["used"] + ["%s:%s" % (grant["answer"], key)])[-200:]
            storage.write_file(os.path.join(self.folder, GRANTS), canonical(data))
        return now < grant["expires"] <= now + GRANT_SECONDS

    def waiting_for_answer(self):
        """The events the automatic gate held back for the user's answer (observer_holds.ANSWER_REASONS), oldest
        first: deferred, and held."""
        reasons = observer_holds.ANSWER_REASONS
        if self.format not in LOOKUP_FORMATS:
            raise StoreError("lookup_unavailable")  # a format 1 queue, read only: the next writable open migrates it
        try:
            rows = self.db.execute("SELECT eventkey FROM events WHERE state='deferred' AND held=1 AND reason IN (%s) "
                                   "ORDER BY created,eventkey LIMIT 100" % ",".join("?" * len(reasons)), reasons)
            return [self.event(row[0]) for row in rows.fetchall()]
        except (sqlite3.Error, ValueError, TypeError) as error:
            raise StoreError("lookup_unavailable") from error

    def transition(self, key, state, **fields):
        """Move an event on. One deferred or discarded with a reason of observer_holds.HELD_REASONS, or with
        held=True, is marked held for good."""
        if set(fields) - {"account", "receipt", "reason", "held"}:
            raise StoreError("invalid_state")
        with self.transaction():
            event = self.event(key)
            if event is None or state not in TRANSITIONS.get(event["state"], set()):
                raise StoreError("invalid_transition")
            account = fields.get("account", event["account"])
            if account and (not isinstance(account, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", account)):
                raise StoreError("invalid_account")
            if event["account"] and event["account"] != account:
                raise StoreError("account_changed")
            reason = fields.get("reason", "")
            if reason:
                _reason(reason)
            receipt = fields.get("receipt", event["receipt"])
            if state == "verified" and (not isinstance(receipt, dict) or not receipt.get("comment_id")):
                raise StoreError("invalid_receipt")
            held = int(bool(event["held"]) or fields.get("held") is True or reason in observer_holds.HELD_REASONS)
            self.db.execute("UPDATE events SET state=?,account=?,receipt=?,reason=?,updated=?,held=? WHERE eventkey=?",
                            (state, account, canonical(receipt), reason, time.time(), held, key))
            self.db.execute("INSERT INTO transitions VALUES (?,?,?)", (key, state, time.time()))
            for row in self.db.execute("SELECT source_id FROM event_sources WHERE eventkey=?", (key,)):
                self._observation_state(row[0], state, reason)

    # ------------------------------------------------------------------ what the observer did (acts)

    def add_act(self, kind, issue=0, comment=0, numbers=(), backup="", at=None):
        """Record one act: its kind (ACT_KINDS), the issue and comment it is about (0 for none), up to 20 whole numbers
        (counts, never text) and the name of the backup file it left, when there is one. Returns the act's id."""
        if kind not in ACT_KINDS or backup is None or (backup and not BACKUP.fullmatch(backup)):
            raise StoreError("invalid_act")
        values = [issue] + list(numbers)
        if (len(numbers) > 20 or any(type(value) is not int or not 0 <= value <= COUNT_MAX for value in values)
                or type(comment) is not int or not 0 <= comment <= COMMENT_MAX
                or not isinstance(at if at is not None else 0.0, (int, float))):
            raise StoreError("invalid_act")
        with self.transaction():
            return self.db.execute("INSERT INTO acts(at,kind,issue,comment,numbers,backup) VALUES (?,?,?,?,?,?)",
                                   (time.time() if at is None else float(at), kind, issue, comment,
                                    canonical(list(numbers)), backup)).lastrowid

    def log_act(self, kind, **fields):
        """add_act for an act whose work is already done: the act's id, or None when the log could not take it. The failure
        is counted (note_act_failure) and never raised, so that what was done is not stopped by its own log."""
        try:
            return self.add_act(kind, **fields)
        except Exception:  # noqa: BLE001 - the work stands; only the log of it is missing, and that is counted
            try:
                self.note_act_failure()
            except Exception:  # noqa: BLE001 - a log that cannot say it failed is the log's problem
                pass
            return None

    def note_act_failure(self, now=None):
        """Count one write of the acts log that failed (the post it was for stands): the control `acts_failed` holds a whole
        count and the UTC day of the last failure, nothing else. The count starts again at 1 when the last failure is
        more than ACTS_FAILED_DAYS days old. The session-start notice, `observer.py status` and `hooks.py --doctor` read
        it."""
        now = time.time() if now is None else now
        today = day_of(now)
        found = self.acts_failed()
        count = found["n"] if found and found["day"] >= day_of(now - ACTS_FAILED_DAYS * 86400) else 0
        self.set_control(ACTS_FAILED, {"n": min(count + 1, COUNT_MAX), "day": today})

    def acts_failed(self):
        """{"n": whole count, "day": "YYYY-MM-DD"} of the failed writes of the acts log, or None when there is none or the
        control is not in that form."""
        found = self.get_control(ACTS_FAILED, None)
        if (isinstance(found, dict) and type(found.get("n")) is int and 1 <= found["n"] <= COUNT_MAX
                and isinstance(found.get("day"), str) and DAY_FORM.fullmatch(found["day"])):
            return {"n": found["n"], "day": found["day"]}
        return None

    def acts(self, days=7, kinds=None):
        """The acts of the last `days` days (1 to 35), newest first, as dicts with their numbers as a list."""
        since = time.time() - max(1, min(USAGE_DAYS, int(days))) * 86400
        rows = self.db.execute("SELECT id,at,kind,issue,comment,numbers,backup,undone FROM acts WHERE at>? "
                               "ORDER BY at DESC, id DESC", (since,)).fetchall()
        return [{"id": row[0], "at": row[1], "kind": row[2], "issue": row[3], "comment": row[4],
                 "numbers": json.loads(row[5]), "backup": row[6], "undone": row[7] == 1}
                for row in rows if kinds is None or row[2] in kinds]

    def act(self, ident):
        """One act by its id, or None."""
        if type(ident) is not int:
            return None
        row = self.db.execute("SELECT id,at,kind,issue,comment,numbers,backup,undone FROM acts WHERE id=?",
                              (ident,)).fetchone()
        return None if row is None else {"id": row[0], "at": row[1], "kind": row[2], "issue": row[3], "comment": row[4],
                                         "numbers": json.loads(row[5]), "backup": row[6], "undone": row[7] == 1}

    def mark_undone(self, ident):
        """Mark an act as undone, once. False when there is no such act or it was undone before."""
        if type(ident) is not int:
            return False
        with self.transaction():
            return self.db.execute("UPDATE acts SET undone=1 WHERE id=? AND undone=0", (ident,)).rowcount == 1

    # ------------------------------------------------------------------ drafts of the paper's text that wait

    def add_draft(self, topic, headings, folder, base_hash, due_hash="", state="waiting", reason=""):
        """Record a draft of the paper's text. Returns its short id (8 hex digits). `state` is "waiting" for a draft
        that waits for the user and "refused" for one the checks refused; the folder is where the keeper kept it."""
        if (type(topic) is not int or topic < 1 or topic > COUNT_MAX or not isinstance(headings, (list, tuple))
                or not 1 <= len(headings) <= 12
                or any(not isinstance(h, str) or not 1 <= len(h) <= 120 or any(ord(c) < 32 for c in h) for h in headings)
                or not isinstance(folder, str) or not 1 <= len(folder) <= 1000 or "\x00" in folder
                or not isinstance(base_hash, str) or not HEX.fullmatch(base_hash)
                or not isinstance(due_hash, str) or (due_hash and not HEX.fullmatch(due_hash))
                or state not in ("waiting", "refused") or (reason and not KEY.fullmatch(reason))):
            raise StoreError("invalid_draft")
        now = time.time()
        ident = hashlib.sha256(canonical([self.repo, topic, base_hash, due_hash, now, folder]).encode("utf-8")).hexdigest()[:8]
        with self.transaction():
            self.db.execute("INSERT INTO drafts(id,topic,headings,folder,base_hash,due_hash,state,reason,created,updated) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?)", (ident, topic, canonical(list(headings)), folder, base_hash,
                                                              due_hash, state, reason, now, now))
        return ident

    def draft(self, ident):
        if not isinstance(ident, str) or not DRAFT_ID.fullmatch(ident):
            return None
        row = self.db.execute("SELECT id,topic,headings,folder,base_hash,due_hash,state,reason,until,created,updated "
                              "FROM drafts WHERE id=?", (ident,)).fetchone()
        if row is None:
            return None
        return {"id": row[0], "topic": row[1], "headings": json.loads(row[2]), "folder": row[3], "base_hash": row[4],
                "due_hash": row[5], "state": row[6], "reason": row[7], "until": row[8], "created": row[9],
                "updated": row[10]}

    def drafts(self, states=None):
        """The drafts in `states` (default: all), oldest first."""
        states = tuple(states or DRAFT_STATES)
        if any(state not in DRAFT_STATES for state in states):
            raise StoreError("invalid_draft")
        rows = self.db.execute("SELECT id FROM drafts WHERE state IN (%s) ORDER BY created,id" % ",".join("?" * len(states)),
                               states).fetchall()
        return [self.draft(row[0]) for row in rows]

    def set_draft(self, ident, state, reason="", until=0):
        """Move a draft to `state`: "later" with the time until which it is not asked about again, "dropped", "stale",
        "applied" or "refused" with a fixed reason. False when there is no such draft."""
        if state not in DRAFT_STATES or (reason and not KEY.fullmatch(reason)) or not isinstance(until, (int, float)) \
                or not math.isfinite(until) or until < 0 or not isinstance(ident, str) or not DRAFT_ID.fullmatch(ident):
            raise StoreError("invalid_draft")
        with self.transaction():
            return self.db.execute("UPDATE drafts SET state=?,reason=?,until=?,updated=? WHERE id=?",
                                   (state, reason, float(until), time.time(), ident)).rowcount == 1

    # ------------------------------------------------------------------ sorted stretches that wait for a draft

    def save_labels(self, batch, sources, labels):
        """Keep the labels the sorter gave a stretch (checked by code first), so that a draft the budget or a failure
        postponed does not need a second sort. `batch` is 32 hex digits, `sources` the stretch's source ids."""
        if not isinstance(batch, str) or not BATCH.fullmatch(batch) or not isinstance(sources, list) \
                or not 1 <= len(sources) <= 64 or not isinstance(labels, list) or len(labels) > 24:
            raise StoreError("invalid_labels")
        sources = [_id(source) for source in sources]
        payload = canonical(labels)
        if len(payload) > 65536:
            raise StoreError("invalid_labels")
        now = time.time()
        with self.transaction():
            self.db.execute("INSERT OR REPLACE INTO labels(batch,sources,labels,state,created,updated) VALUES "
                            "(?,?,?,'waiting',?,?)", (batch, canonical(sources), payload, now, now))

    def waiting_labels(self):
        rows = self.db.execute("SELECT batch,sources,labels FROM labels WHERE state='waiting' ORDER BY created,batch")
        return [{"batch": row[0], "sources": json.loads(row[1]), "labels": json.loads(row[2])} for row in rows.fetchall()]

    def finish_labels(self, batch):
        """The stretch's labels are drafted (or dropped for good); they no longer wait."""
        if not isinstance(batch, str) or not BATCH.fullmatch(batch):
            raise StoreError("invalid_labels")
        with self.transaction():
            return self.db.execute("UPDATE labels SET state='done',updated=? WHERE batch=? AND state='waiting'",
                                   (time.time(), batch)).rowcount == 1

    # ------------------------------------------------------------------ everything that waits for the user's answer

    # The question to close a finished task (level Assistant) and the items put off with "Not now" are kept in controls,
    # whole numbers and times only.
    def close_key(self, issue):
        """The 32 hex digits that name the question "close #issue?": its grant is bound to them, and their first 8 are the
        item's short id."""
        if type(issue) is not int or not 1 <= issue <= COUNT_MAX:
            raise StoreError("invalid_close")
        return hashlib.sha256(canonical([self.repo, "close", issue]).encode("utf-8")).hexdigest()[:32]

    def close_items(self):
        """The finished tasks the observer asks the user to close: [{"issue", "created"}], oldest first."""
        rows = self.get_control(CLOSE_ITEMS, [])
        if not isinstance(rows, list):
            return []
        return [dict(issue=row["issue"], created=row["created"]) for row in rows
                if isinstance(row, dict) and set(row) == {"issue", "created"} and type(row["issue"]) is int
                and 1 <= row["issue"] <= COUNT_MAX and type(row["created"]) in (int, float)]

    def set_close_items(self, issues, now=None):
        """Keep the question for exactly the tasks in `issues` (a list of whole numbers): new ones are added with the
        time `now`, those no longer in the list are dropped. At most 50."""
        now = time.time() if now is None else now
        known = {row["issue"]: row["created"] for row in self.close_items()}
        rows = [{"issue": n, "created": known.get(n, now)} for n in sorted(set(issues))[:CLOSE_MAX]
                if type(n) is int and 1 <= n <= COUNT_MAX]
        if rows != self.close_items():
            self.set_control(CLOSE_ITEMS, rows)
        return rows

    def put_off(self, short, until):
        """Put the item `short` off until the time `until` ("Not now"); the entries past their time are forgotten."""
        if not isinstance(short, str) or not DRAFT_ID.fullmatch(short) or type(until) not in (int, float) \
                or not math.isfinite(until):
            raise StoreError("invalid_later")
        now = time.time()
        later = self.get_control(LATER, {})
        later = {k: v for k, v in (later if isinstance(later, dict) else {}).items()
                 if isinstance(k, str) and type(v) in (int, float) and v > now}
        later[short] = float(until)
        self.set_control(LATER, dict(sorted(later.items(), key=lambda pair: pair[1])[-LATER_MAX:]))

    def put_off_until(self, short):
        """The time until which `short` is put off, or 0."""
        later = self.get_control(LATER, {})
        value = later.get(short) if isinstance(later, dict) else None
        return float(value) if type(value) in (int, float) and math.isfinite(value) else 0.0

    def waiting(self):
        """The one list of what needs the user's answer, oldest first, each as {"kind", "key", "short", "created", ...}:
        a held memory (kind "held", key its event key) and a draft of the paper's text that waits (kind "draft", key its
        short id; one put off with "later" waits again when its time has passed), a task the level Assistant holds
        for the user's answer (kind "task_open", key its event key, with its title and its number of steps) and a
        finished task the observer asks to close (kind "task_close", key close_key(issue), with its issue). A task item
        put off with "Not now" (put_off) is left out until its time has passed."""
        out = []
        now = time.time()
        for row in self.close_items():
            key = self.close_key(row["issue"])
            if self.put_off_until(key[:8]) <= now:
                out.append({"kind": "task_close", "key": key, "short": key[:8], "created": row["created"],
                            "issue": row["issue"]})
        for event in self.waiting_for_answer():
            candidate = event["candidate"]
            if event.get("reason") in TASK_ASKS and candidate.get("kind") == "steps" and candidate.get("issue") == 0:
                # A task to open, held at the level Assistant or kept off the record by the reviewer (withheld_review):
                # "Task <short>: open a task ..." (observer_publisher).
                if self.put_off_until(event["eventkey"][:8]) > now:
                    continue
                out.append({"kind": "task_open", "key": event["eventkey"], "short": event["eventkey"][:8],
                            "created": event["created"], "event": event, "title": candidate["task_title"],
                            "steps": len(record_format.steps_list(candidate["body"]))})
                continue
            out.append({"kind": "held", "key": event["eventkey"], "short": event["eventkey"][:8],
                        "created": event["created"], "event": event})
        for draft in self.drafts(states=("waiting", "later")):
            if draft["state"] == "waiting" or draft["until"] <= now:
                out.append({"kind": "draft", "key": draft["id"], "short": draft["id"], "created": draft["created"],
                            "draft": draft})
        return sorted(out, key=lambda item: (item["created"], item["key"]))

    def holds(self):
        return self.get_control("holds", [])

    def save_holds(self, holds):
        if not isinstance(holds, list):
            raise StoreError("invalid_holds")
        self.set_control("holds", holds)

    def _write_held(self, holds):
        """Beside the queue, the digests of the held exact commands, so that a call can still be stopped when the
        queue cannot be read (held_commands)."""
        digests = sorted({_command_digest(hold["command"]) for hold in holds if isinstance(hold, dict)
                          and hold.get("state") == "active" and hold.get("action") == "command"
                          and isinstance(hold.get("command"), str)})
        storage.write_file(os.path.join(self.folder, COMMAND_HOLDS), json.dumps(digests))

    def _offer_again(self, before, changed):
        """The sources that an active "publish" hold of `before`, released or gone in `changed`, kept off the record
        (deferred as withheld) and that no hold still active in `changed` covers, go back to observed: the next pass
        sorts them again, under its usual caps and gates. A source another hold covers stays withheld. Runs inside the
        transaction of update_holds, so that a release never leaves them behind. Returns how many were offered.

        Each hold that is lifted lifts its own typed request and no other: the quote of a freed hold of this session is
        kept for the sources it covered, offered now or still withheld by another hold (RELEASED, one list of quotes per
        source), and the models are told that request, and that one only, no longer applies (released_sources). A hold of
        the whole repository frees a source and leaves it unmarked: its words say nothing about what this session typed."""
        alive = {hold.get("id") for hold in changed if isinstance(hold, dict) and hold.get("state") == "active"}
        freed = [hold for hold in before if isinstance(hold, dict) and hold.get("state") == "active"
                 and hold.get("action") == "publish" and hold.get("id") not in alive]
        if not freed:
            return 0
        row = self.db.execute("SELECT value FROM controls WHERE key=?", (RELEASED,)).fetchone()
        lifted = _lifted(json.loads(row[0]) if row else {})
        offered = []
        for source_id, metadata in self.db.execute("SELECT source_id,metadata FROM observations WHERE state='deferred' "
                                                   "AND reason='withheld'").fetchall():
            session = json.loads(metadata).get("session_id")
            covering = [hold for hold in freed if observer_holds.publication_withheld([hold], self.repo, {session})]
            if not covering:
                continue
            quotes = lifted.get(source_id, [])
            for hold in covering:
                scope = hold.get("scope")
                words = clean_quote(hold.get("quote"))
                if words and isinstance(scope, dict) and scope.get("session_id") == session and words not in quotes:
                    quotes.append(words)
            if quotes:
                lifted[source_id] = quotes
            if not observer_holds.publication_withheld(changed, self.repo, {session}):
                self.db.execute("UPDATE observations SET state='observed',reason='',updated=? WHERE source_id=?",
                                (time.time(), source_id))
                offered.append(source_id)
        if lifted:
            self.db.execute("INSERT OR REPLACE INTO controls VALUES (?,?)",
                            (RELEASED, canonical(dict(list(lifted.items())[-RELEASED_MAX:]))))
        return len(offered)

    def released_sources(self):
        """{source id: the requests the user lifted} of the sources still waiting to be sorted (observed, or deferred for
        another try) whose hold the user released: the quotes of the holds, joined, which the models are told, as
        hold_released, of that source. A source that is handled (an event, set aside, rejected) leaves the list; this
        prunes it. A source a hold of the whole repository freed has no entry."""
        found = _lifted(self.get_control(RELEASED, {}))
        pending = {sid: quotes for sid, quotes in found.items()
                   if (self.observation(sid) or {}).get("state") in ("observed", "deferred")}
        if pending != found and not self.readonly:
            self.set_control(RELEASED, pending)
        return {sid: QUOTES_JOIN.join(quotes)[:QUOTES_MAX] for sid, quotes in pending.items()}

    def update_holds(self, transform):
        """Replace the holds by what `transform` makes of them. A hold of the record's publication that this releases
        offers again the sources it withheld (_offer_again, counted in `offered_again`)."""
        with self.transaction():
            before = self.holds()
            changed = transform(before)
            if not isinstance(changed, list) or len(canonical(changed).encode("utf-8")) > 1024 * 1024:
                raise StoreError("invalid_holds")
            self.db.execute("INSERT OR REPLACE INTO controls VALUES ('holds',?)", (canonical(changed),))
            self.offered_again = self._offer_again(before, changed)
            # Before the commit: the held commands of the old and the new holds together, so that the file never
            # holds fewer than the queue, whether the commit happens or not. A write that fails aborts the update.
            self._write_held(before + changed)
        try:
            self._write_held(changed)  # after the commit: exactly the queue's; a failure leaves the superset
        except (storage.StorageError, OSError):
            pass
        return changed

    def status(self):
        return {"observations": dict(self.db.execute("SELECT state,COUNT(*) FROM observations GROUP BY state")),
                "events": dict(self.db.execute("SELECT state,COUNT(*) FROM events GROUP BY state")),
                "invocations_24h": self.db.execute("SELECT COUNT(*) FROM invocations WHERE created>?", (time.time() - 86400,)).fetchone()[0],
                "tiers_24h": self.usage_today() if self.format == FORMAT else {},
                "bytes": os.path.getsize(self.path), "max_bytes": self.max_bytes, "max_pending": self.max_pending}

    def expire_handled(self, days=7):
        cutoff = time.time() - max(1, min(7, int(days))) * 86400
        with self.transaction():
            rows = self.db.execute("SELECT eventkey,candidate FROM events WHERE state IN ('verified','ignored','rejected') AND updated<?", (cutoff,)).fetchall()
            for row in rows:
                candidate = json.loads(row["candidate"])
                if candidate.pop("excerpts", None) is not None:
                    # Keep the original reviewed hash in review; verified receipts remain immutable.
                    self.db.execute("UPDATE events SET candidate=? WHERE eventkey=?", (canonical(candidate), row["eventkey"]))
        if self.format == FORMAT:
            self.expire_usage()
        return len(rows)

    @contextlib.contextmanager
    def publication_lock(self):
        with _exclusive_lock(self.folder, "publication.lock"):
            yield
