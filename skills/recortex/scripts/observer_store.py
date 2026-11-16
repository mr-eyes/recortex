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
from urllib.parse import quote

import observer_holds
import recortex
import record_format
import storage

try:
    import fcntl
except ImportError:
    fcntl = None


DEFAULT_BYTES = 128 * 1024 * 1024
DEFAULT_PENDING = 10000
TERMINAL = frozenset(("verified", "ignored", "rejected"))
STATES = frozenset(("observed", "candidate", "reviewed", "ready", "sending", "verified",
                    "deferred", "ignored", "rejected", "uncertain"))
TRANSITIONS = {"ready": {"sending", "deferred", "rejected"},
               "sending": {"verified", "uncertain"}, "uncertain": {"verified"},
               "deferred": {"ready", "rejected", "sending"}, "reviewed": {"ready", "deferred", "rejected"}}
SOURCE_FIELDS = frozenset(("source_id", "session_id", "role", "event_type", "path", "offset", "end",
                           "sha256", "content_sha256", "observed_at"))
SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}")
KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")
HEX = re.compile(r"[0-9a-f]{64}")
EVENT_KEY = re.compile(r"[0-9a-f]{32}")
SCHEMA = """
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
"""
# Format 2 added to each event the digests its copies are looked up by and the held mark (copy_of, decision_of).
FORMAT = "2"
ADDED = (("body_hash", "TEXT NOT NULL DEFAULT ''"), ("words_hash", "TEXT NOT NULL DEFAULT ''"),
         ("held", "INTEGER NOT NULL DEFAULT 0"))
SCHEMA_1 = SCHEMA.replace(", body_hash TEXT NOT NULL DEFAULT '', words_hash TEXT NOT NULL DEFAULT '',\n held INTEGER NOT "
                          "NULL DEFAULT 0)", ")")
INDEXES = ("CREATE INDEX IF NOT EXISTS events_body ON events(body_hash)",
           "CREATE INDEX IF NOT EXISTS events_words ON events(words_hash)")
# Reasons an earlier queue kept an event for that are no longer given: back-filled as held.
EARLIER_HELD = ("sensitive_project", "confidentiality", "sheet_not_approved")


class StoreError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__("observer store unavailable: " + reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def candidate_hash(candidate):
    return hashlib.sha256(canonical(candidate).encode("utf-8")).hexdigest()


def _id(value):
    if not isinstance(value, str) or not SOURCE_ID.fullmatch(value):
        raise StoreError("invalid_source")
    return value


def _reason(value):
    if not isinstance(value, str) or not KEY.fullmatch(value):
        raise StoreError("invalid_reason")
    return value


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
            if self.format == "1":
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
        version = meta.get("format") if meta.get("format") in ("1", FORMAT) else FORMAT
        for statement in (SCHEMA_1 if version == "1" else SCHEMA).split(";"):
            if not statement.strip():
                continue
            expected = re.sub(r"\s+", " ", statement.replace(" IF NOT EXISTS", "")).strip()
            name = expected.split("(", 1)[0].split()[-1]
            if name not in actual or re.sub(r"\s+", " ", actual[name] or "").strip() != expected:
                raise StoreError("corrupt")
        if version == FORMAT:
            indexes = dict(self.db.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"))
            for statement in INDEXES:
                expected = statement.replace(" IF NOT EXISTS", "")
                if re.sub(r"\s+", " ", indexes.get(expected.split()[2]) or "").strip() != expected:
                    raise StoreError("corrupt")
        if meta != {"format": version, "repo": self.repo,
                    "mark": storage.mark(self.path, self.repo, create=False, tighten=False)}:
            raise StoreError("identity")
        self.format = version

    def _migrate(self):
        """Format 1 to 2 in one transaction: the columns and indexes are added and every event's digests and held
        mark are back-filled, so that a copy of an earlier event is found as a copy of a new one is."""
        with self.transaction():
            for name, kind in ADDED:
                self.db.execute("ALTER TABLE events ADD COLUMN %s %s" % (name, kind))
            for statement in INDEXES:
                self.db.execute(statement)
            for row in self.db.execute("SELECT eventkey,candidate,reason FROM events").fetchall():
                candidate = json.loads(row["candidate"])
                body_hash, words_hash = copy_digests(candidate.get("kind"), candidate.get("body"))
                self.db.execute("UPDATE events SET body_hash=?,words_hash=?,held=? WHERE eventkey=?",
                                (body_hash, words_hash, int(_held_before(row["reason"])), row["eventkey"]))
            self.db.execute("UPDATE meta SET value=? WHERE key='format'", (FORMAT,))
        self.format = FORMAT

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
                if key in ("offset", "end", "observed_at"):
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                        raise StoreError("invalid_source")
                elif not isinstance(value, str) or len(value) > (4096 if key == "path" else 256):
                    raise StoreError("invalid_source")
            for key in ("sha256", "content_sha256"):
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

    def reserve_invocation(self, purpose, limit=60, window_seconds=86400):
        _reason(purpose)
        now = time.time()
        with self.transaction():
            window = max(86400, min(168 * 3600, int(window_seconds)))
            count = self.db.execute("SELECT COUNT(*) FROM invocations WHERE created>?", (now - window,)).fetchone()[0]
            if count >= min(60, max(0, int(limit))):
                return None
            return self.db.execute("INSERT INTO invocations(purpose,created) VALUES (?,?)", (purpose, now)).lastrowid

    def complete_invocation(self, ident, success, output_hash=""):
        if output_hash and not HEX.fullmatch(output_hash):
            raise StoreError("invalid_review")
        with self.transaction():
            row = self.db.execute("SELECT completed FROM invocations WHERE id=?", (ident,)).fetchone()
            if row is None or row[0] is not None:
                raise StoreError("invalid_review")
            self.db.execute("UPDATE invocations SET completed=?,success=?,output_hash=? WHERE id=?",
                            (time.time(), int(success is True), output_hash, ident))

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
        if (type(issue) is not int or issue < 1 or kind not in record_format.MEMORY_TYPES
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

    def review_complete(self, source_id, candidate, review):
        _id(source_id)
        if not isinstance(candidate, dict) or set(candidate) - {"kind", "issue", "body", "sources", "excerpts"}:
            raise StoreError("invalid_candidate")
        sources = candidate.get("sources")
        if not isinstance(sources, list) or not sources or len(sources) > 64:
            raise StoreError("invalid_candidate")
        sources = sorted({_id(source) for source in sources})
        if source_id not in sources or any(self.observation(source) is None for source in sources):
            raise StoreError("missing_source")
        if candidate.get("kind") not in record_format.MEMORY_TYPES:
            raise StoreError("invalid_candidate")
        issue = candidate.get("issue")
        if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
            raise StoreError("invalid_candidate")
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
        if review.get("accepted") is not True:
            self.mark_handled(source_id, "rejected")
            return None
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
            overlap = self.db.execute("SELECT eventkey FROM event_sources WHERE source_id IN (%s)" % ",".join("?" * len(sources)), sources).fetchone()
            if overlap:
                self._overlap(source_id)
                return None
            body_hash, words_hash = copy_digests(candidate["kind"], body)
            self.db.execute("INSERT INTO events(eventkey,source_id,candidate,review,body,payload_hash,state,created,updated,"
                            "body_hash,words_hash) VALUES (?,?,?,?,?,?,'ready',?,?,?,?)",
                            (key, source_id, canonical(candidate), canonical(review), posted_body,
                             hashlib.sha256(posted_body.encode("utf-8")).hexdigest(), now, now, body_hash, words_hash))
            self.db.executemany("INSERT INTO event_sources VALUES (?,?)", [(source, key) for source in sources])
            for state in ("candidate", "reviewed", "ready"):
                self.db.execute("INSERT INTO transitions VALUES (?,?,?)", (key, state, now))
            for source in sources:
                self._observation_state(source, "ready")
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
        a copy does. Raises StoreError as copy_of."""
        marker = record_format.parse_marker(record_format.first_lines(draft or "")[0]) or {}
        targets = {target for key, target in record_format.links(marker) if key in ("replaces", "instead", "checks")}
        if not targets:
            return None
        if self.format != FORMAT:
            raise StoreError("lookup_unavailable")
        try:
            rows = self.db.execute("SELECT eventkey,body FROM events WHERE state IN ('ready','deferred') AND held=1 "
                                   "ORDER BY created DESC,eventkey LIMIT 1000").fetchall()
        except sqlite3.Error as error:
            raise StoreError("lookup_unavailable") from error
        for row in rows:
            theirs = record_format.parse_marker(record_format.first_lines(row["body"])[0]) or {}
            if targets & {target for key, target in record_format.links(theirs)
                          if key in ("replaces", "instead", "checks")}:
                return self.event(row["eventkey"])
        return None

    def _lookup(self, where, digest):
        if self.format != FORMAT:
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
        if self.format != FORMAT:
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

    def update_holds(self, transform):
        with self.transaction():
            before = self.holds()
            changed = transform(before)
            if not isinstance(changed, list) or len(canonical(changed).encode("utf-8")) > 1024 * 1024:
                raise StoreError("invalid_holds")
            self.db.execute("INSERT OR REPLACE INTO controls VALUES ('holds',?)", (canonical(changed),))
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
        return len(rows)

    @contextlib.contextmanager
    def publication_lock(self):
        with _exclusive_lock(self.folder, "publication.lock"):
            yield
