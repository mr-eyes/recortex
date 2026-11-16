"""Bounded native transcript pointers. Source contents exist only in the worker's memory."""
import datetime
import hashlib
import json
import os
import re
import stat
import time

try:
    import grp
    import pwd
except ImportError:  # no user database: group write on a folder is never accepted
    grp = pwd = None

MAX_READ = 262144
MAX_ROWS = 512
SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
AGENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
EVENTS = {"UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop", "SubagentStop", "PreCompact"}


class SourceError(ValueError):
    """Fixed reason only, never source contents or filesystem paths."""


def _boundary(path, folder, source_file=None):
    """Fixed boundary names are safe in default status and hook diagnostics."""
    if path == source_file:
        return "transcript"
    if path == folder:
        return "native-project"
    if path.startswith(folder + os.sep):
        return "source-directory"
    if path == os.path.dirname(folder):
        return "native-projects"
    if path == os.path.dirname(os.path.dirname(folder)):
        return "native-config"
    return "ancestor"


PASSWD = "/etc/passwd"
GROUP = "/etc/group"
ACCOUNTS_CAP = 4 * 1024 * 1024
NUMBER = re.compile(r"[0-9]{1,10}")


def _local(path, fields, numbers):
    """The entries of a local account file, with the fields at `numbers` as integers; None, which counts as "not
    private", when the file is larger than ACCOUNTS_CAP, cannot be read, or holds any line that is a NIS compat entry
    (+ or -), has another number of fields or a field at `numbers` that is not a number. Nothing is skipped."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(ACCOUNTS_CAP + 1)
    except OSError:
        return None
    if len(raw) > ACCOUNTS_CAP:
        return None
    entries = []
    for line in raw.decode("utf-8", "replace").split("\n"):
        if line == "":
            continue
        parts = line.split(":")
        if line[0] in "+-" or len(parts) != fields or not all(NUMBER.fullmatch(parts[n]) for n in numbers):
            return None
        entries.append([int(part) if n in numbers else part for n, part in enumerate(parts)])
    return entries


def private_group(uid, gid):
    """True when `gid` is the user private group of `uid`, established from the local account files and never from
    an enumeration that a network directory can leave incomplete: the owner and the group are each defined once in
    /etc/passwd and /etc/group, the group is the owner's primary group and is named after the owner, it has no member
    but the owner, and no other local account has it as its primary group. The system's own lookup (NSS) must agree.
    A network account or group, or a file that does not parse cleanly, is refused. Default Ubuntu makes ~/.claude
    and ~/.claude/projects 775 in such a group."""
    if type(uid) is not int or type(gid) is not int or grp is None or pwd is None:
        return False
    users, groups = _local(PASSWD, 7, (2, 3)), _local(GROUP, 4, (2,))
    if users is None or groups is None:
        return False
    owners = [entry for entry in users if entry[2] == uid]
    named = [entry for entry in groups if entry[2] == gid]
    if len(owners) != 1 or len(named) != 1:
        return False
    owner, group = owners[0], named[0]
    members = [member for member in group[3].split(",") if member]
    if not (owner[3] == gid and group[0] == owner[0] and all(member == owner[0] for member in members)
            and not any(entry[3] == gid and entry[0] != owner[0] for entry in users)):
        return False
    try:
        looked_up, account = grp.getgrgid(gid), pwd.getpwuid(uid)
    except (KeyError, OverflowError, OSError):
        return False
    return (looked_up.gr_name == owner[0] and all(member == owner[0] for member in looked_up.gr_mem)
            and account.pw_gid == gid and account.pw_name == owner[0])


def _writable(info, leaf=False):
    """World write; group write on a transcript file; group write on a folder by any group but the owner's private
    group."""
    if info.st_mode & stat.S_IWOTH or leaf and info.st_mode & stat.S_IWGRP:
        return True
    return bool(info.st_mode & stat.S_IWGRP) and not private_group(info.st_uid, getattr(info, "st_gid", None))


def _component_problem(path, info, folder, source_file=None):
    """One ownership/mode policy for preflight and actual source resolution.

An ancestor's owner can replace its children even when group/other write bits
are clear. Sticky mode protects entries from other users, not from that owner.
Only sticky ancestors owned by root or the current user, outside the source
tree, are an exception to the write-bit rule; source directories stay private.
Group write is accepted only on a folder, and only for the owner's user private group
(private_group); a transcript file must be writable by the user alone.
"""
    if stat.S_ISLNK(info.st_mode):
        return "source-symlink"
    leaf = path == source_file
    if not (stat.S_ISREG(info.st_mode) if leaf else stat.S_ISDIR(info.st_mode)):
        return "source-file" if leaf else "source-path"
    inside = path == folder or path.startswith(folder + os.sep)
    if hasattr(os, "getuid"):
        owners = (os.getuid(),) if inside else (os.getuid(), 0)
        if info.st_uid not in owners:
            return "source-owner"
    trusted_owner = info.st_uid == 0 or hasattr(os, "getuid") and info.st_uid == os.getuid()
    shared_sticky = (not inside and not leaf and trusted_owner and
                     stat.S_ISDIR(info.st_mode) and bool(info.st_mode & stat.S_ISVTX))
    if _writable(info, leaf) and not shared_sticky:
        return "source-permissions"
    return None


def project_directory(project_root):
    root = os.path.abspath(project_root)
    config = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    if not os.path.isabs(config):
        raise SourceError("source-config")
    encoded = re.sub(r"[^A-Za-z0-9]", "-", root)
    return os.path.join(config, "projects", encoded)


def preflight(project_root, details=False):
    """Inspect directory metadata only; paths require an explicit diagnostic request."""
    try:
        folder = project_directory(project_root)
        missing = not os.path.isdir(folder)
        current = folder
        issues = []
        while True:
            if os.path.lexists(current):
                info = os.lstat(current)
                problem = _component_problem(current, info, folder)
                if problem:
                    issue = {"code": problem, "boundary": _boundary(current, folder),
                             "action": "review-owned-permissions" if problem == "source-permissions" else "review-source-location"}
                    if details:
                        issue.update(path=current, mode=format(stat.S_IMODE(info.st_mode), "04o"),
                                     owner="current-user" if hasattr(os, "getuid") and info.st_uid == os.getuid()
                                     else "root" if info.st_uid == 0 else "other")
                    issues.append(issue)
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        if issues:
            return dict(issues[0], ready=False, issues=issues)
        return {"ready": not missing, "code": "source-missing" if missing else "ok"}
    except (OSError, SourceError):
        return {"ready": False, "code": "source-unavailable"}


def approved_path(path, project_root, session_id):
    """Only this project's native main/subagent transcript, never an arbitrary hook/model path."""
    if not isinstance(path, str) or not os.path.isabs(path) or not isinstance(session_id, str) or not SESSION.fullmatch(session_id):
        raise SourceError("source-identity")
    folder = project_directory(project_root)
    relative = os.path.relpath(path, folder)
    main = session_id + ".jsonl"
    sub = re.fullmatch(re.escape(session_id) + r"/subagents/agent-([A-Za-z0-9][A-Za-z0-9_-]{0,127})\.jsonl", relative)
    if relative != main and not sub:
        raise SourceError("source-project")
    if os.path.normpath(path) != path:
        raise SourceError("source-path")
    current = path
    while True:
        info = os.lstat(current)
        problem = _component_problem(current, info, folder, source_file=path)
        if problem:
            raise SourceError(problem)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return path


def _read(path, offset, size):
    if type(offset) is not int or offset < 0 or type(size) is not int or size < 0 or size > MAX_READ:
        raise SourceError("source-range")
    before = os.lstat(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
            raise SourceError("source-changed")
        # Recheck the opened file rather than relying only on a prior pathname
        # validation. A swapped regular file must not acquire source authority.
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise SourceError("source-owner")
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise SourceError("source-permissions")  # the transcript itself is writable by the user alone
        os.lseek(descriptor, offset, os.SEEK_SET)
        chunks, left = [], size
        while left:
            chunk = os.read(descriptor, left)
            if not chunk:
                break
            chunks.append(chunk)
            left -= len(chunk)
        after = os.lstat(path)
        if (after.st_dev, after.st_ino) != (info.st_dev, info.st_ino):
            raise SourceError("source-changed")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


# Records that are not part of the conversation: a copy of what was sent to the API (api-request-blob, which repeats
# messages and carries system reminders), attachments, the skill text Claude Code adds (isMeta), a compaction summary
# (model text in a user record) and lines shown only in the transcript.
HIDDEN = ("isMeta", "isCompactSummary", "isVisibleInTranscriptOnly")
# A user record counts as the user's own words only when it is an ordinary typed prompt: none of these is set, its
# userType is the standard one, it holds text only, and no harness wrapper, quote or pasted block is in it.
NOT_TYPED = ("isMeta", "isSidechain", "isCompactSummary", "isVisibleInTranscriptOnly", "toolUseResult",
             "sourceToolUseID", "sourceToolAssistantUUID")
HARNESS = re.compile(r"<(?:command-[a-z]+|local-command-[a-z]+|bash-[a-z]+|system-reminder|task-notification|"
                     r"user-prompt-submit-hook|pasted_content|tool_result|tool_use_error)\b|\[Pasted text|"
                     r"\[Request interrupted|(?m:^Caveat: )|(?m:^[ \t]*(?:>|```|~~~))")
REMINDER = re.compile(r"<system-reminder>(?:.*?</system-reminder>|.*\Z)[ \t\r\n]*", re.S)


def typed_prompt(record, content, text):
    """True only for a prompt the user typed (an allowlist): a "user" record of userType "external" whose content is
    text only, with none of NOT_TYPED set, no harness wrapper (a command, a reminder, a notification), no quoted line,
    code fence or pasted block. Anything else (a tool result, a compaction summary, a skill's text, a command wrapper
    or a subagent's prompt) is never the user's own words for a decision or a change of status."""
    if record.get("type") != "user" or record.get("userType") != "external":
        return False
    if any(key in record and record[key] is not False for key in NOT_TYPED):
        return False
    if isinstance(content, list) and not all(isinstance(block, dict) and block.get("type") == "text"
                                             for block in content):
        return False
    return bool(text.strip()) and not HARNESS.search(text)


def _content(record):
    """(role, text, typed, control) of one transcript record. The observer is a note-taker of the conversation: it reads
    the user's prompts and the text the agent writes to the user, nothing else. A tool result has role "tool" and no
    text; an assistant record gives its text blocks only, not its thinking or its tool calls. typed: the user's own
    words (typed_prompt); control: a user message that may set a hold, by the looser earlier rule, since a hold only
    denies. Text in system reminders never reaches a model."""
    message = record.get("message")
    if not isinstance(message, dict) or record.get("type") not in ("user", "assistant"):
        return "unknown", "", False, False
    role = message.get("role")
    if role != record["type"] or any(record.get(key) for key in HIDDEN):
        return "unknown", "", False, False
    content = message.get("content")
    if isinstance(content, list) and any(isinstance(block, dict) and block.get("type") == "tool_result"
                                         for block in content):
        return "tool", "", False, False  # tool output, file contents, pages and command results are not read
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(block["text"] for block in content if isinstance(block, dict)
                         and block.get("type") == "text" and isinstance(block.get("text"), str))
    else:
        text = ""
    control = role == "user" and not record.get("isMeta") and not record.get("isSidechain")
    if re.search(r"(?m)^\s*>|<pasted_content\b|<system-reminder\b|<tool_result\b", text):
        control = False
    typed = role == "user" and typed_prompt(record, content, text)
    return role, REMINDER.sub("", text), typed, control


def _content_hash(record, role, subagent=False):
    content = (record.get("message") or {}).get("content")
    canonical = json.dumps([role, content, bool(record.get("isMeta")), bool(record.get("isSidechain")), subagent], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _identity(session, role, raw, record, subagent=False):
    native_id = record.get("uuid")
    if isinstance(native_id, str) and re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", native_id):
        # Replay/fork wrappers do not create new observations of an existing message.
        return hashlib.sha256((native_id.lower() + "\n" + role + "\n" + _content_hash(record, role, subagent)).encode()).hexdigest()
    return hashlib.sha256((session + "\n" + role + "\n").encode() + raw).hexdigest()


def scan(data, project_root, cursor=0, max_bytes=MAX_READ):
    """Return (metadata-only observations, next byte cursor). Store both in one transaction."""
    session = data.get("session_id")
    event = data.get("hook_event_name", "Stop")
    if event not in EVENTS:
        return [], cursor
    path = data.get("agent_transcript_path") if event == "SubagentStop" else data.get("transcript_path")
    path = approved_path(path, project_root, session)
    subagent = os.sep + "subagents" + os.sep in path
    info = os.stat(path, follow_symlinks=False)
    if cursor > info.st_size:
        raise SourceError("source-truncated")
    limit = min(MAX_READ, max_bytes)
    raw = _read(path, cursor, limit)
    rows, position = [], cursor
    stamp = time.time()

    def gap(chunk, start):
        digest = hashlib.sha256(chunk).hexdigest()
        sid = hashlib.sha256((session + "\nsource_gap\n" + str(start) + "\n" + digest).encode()).hexdigest()
        rows.append({"source_id": sid, "session_id": session, "role": "unknown", "event_type": "source_gap",
                     "path": path, "offset": start, "end": start + len(chunk), "sha256": digest, "observed_at": stamp})

    # A prior full bounded read may have stopped inside an oversized line. Keep
    # a frozen metadata pointer for each omitted piece and progress to its end.
    if cursor and _read(path, cursor - 1, 1) != b"\n" and raw:
        newline = raw.find(b"\n")
        consumed = newline + 1 if newline >= 0 else len(raw)
        gap(raw[:consumed], cursor)
        position += consumed
        raw = raw[consumed:]
    remainder = raw
    for line in raw.splitlines(keepends=True):
        if len(rows) >= MAX_ROWS:
            break
        if not line.endswith(b"\n"):
            break  # wait for an asynchronous transcript writer to finish the line
        end = position + len(line)
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError):
            raise SourceError("source-format")
        role, text, _, _ = _content(record) if isinstance(record, dict) else ("unknown", "", False, False)
        if text and not subagent:  # a subagent's transcript is not the conversation with the user
            digest = hashlib.sha256(line).hexdigest()
            rows.append({"source_id": _identity(session, role, line, record, subagent), "session_id": session, "role": role,
                         "event_type": event, "path": path, "offset": position, "end": end,
                         "sha256": digest, "content_sha256": _content_hash(record, role, subagent), "observed_at": stamp})
        position = end
        remainder = remainder[len(line):]
    if remainder and position == cursor and len(remainder) >= limit:
        gap(remainder, position)
        position += len(remainder)
    return rows, position


TAIL_BYTES = 32 * 1024 * 1024  # how much unread transcript the check before a Bash call reads for controls
TAIL_SECONDS = 1.0  # and for how long
# Words every control has: /recortex hold, "don't push", "do not tag", "don't release", "don't post", "off the record".
CONTROL_WORDS = (b"recortex", b"push", b"tag", b"release", b"post", b"record", b"publish")


def control_tail(data, project_root, start, max_bytes=None, seconds=None):
    """(rows, complete): metadata rows for the user messages past `start` that may hold a control, read without being
    captured, so that the check before a Bash call sees a hold typed in a part of the transcript capture has not
    reached yet. Other lines are passed over without being parsed. complete is False when the end of the transcript
    was not reached within `max_bytes` and `seconds`; a line longer than one read (MAX_READ) is passed over."""
    session = data.get("session_id")
    event = data.get("hook_event_name", "PreToolUse")
    path = approved_path(data.get("agent_transcript_path") if event == "SubagentStop" else data.get("transcript_path"),
                         project_root, session)
    subagent = os.sep + "subagents" + os.sep in path
    size = os.stat(path, follow_symlinks=False).st_size
    if size - start > (TAIL_BYTES if max_bytes is None else max_bytes):
        return [], False
    until = time.monotonic() + (TAIL_SECONDS if seconds is None else seconds)
    rows, stamp, position = [], time.time(), start
    buffer, buffer_start = b"", start
    skip = bool(start) and _read(path, start - 1, 1) != b"\n"  # a cursor inside an oversized line
    while position < size:
        if time.monotonic() > until:
            return rows, False
        chunk = _read(path, position, min(MAX_READ, size - position))
        if not chunk:
            break
        position += len(chunk)
        buffer += chunk
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                break
            line, line_start = buffer[:newline + 1], buffer_start
            buffer, buffer_start = buffer[newline + 1:], buffer_start + newline + 1
            if skip:
                skip = False
                continue
            low = line.lower()
            if b'"user"' not in low or not any(word in low for word in CONTROL_WORDS):
                continue
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            role, text, _, control = _content(record) if isinstance(record, dict) else ("unknown", "", False, False)
            if role == "user" and control and text:
                rows.append({"source_id": _identity(session, role, line, record, subagent), "session_id": session,
                             "role": role, "event_type": event, "path": path, "offset": line_start,
                             "end": line_start + len(line), "sha256": hashlib.sha256(line).hexdigest(),
                             "content_sha256": _content_hash(record, role, subagent), "observed_at": stamp})
        if len(buffer) > MAX_READ:  # an oversized line holds no typed control
            skip, buffer_start, buffer = True, buffer_start + len(buffer), b""
    return rows, True


def capture_descriptors(data, project_root, event_type=None):
    """Compatibility helper for a bounded tail; scan+atomic cursor is preferred by hooks."""
    selected = dict(data)
    if event_type:
        selected["hook_event_name"] = event_type
    return scan(selected, project_root)[0]


def resolve(descriptor, project_root):
    """Resolve one frozen byte range and its digest. A model never supplies a source path."""
    if descriptor.get("event_type") == "source_gap":
        raise SourceError("source-gap")
    path = approved_path(descriptor.get("path"), project_root, descriptor.get("session_id"))
    subagent = os.sep + "subagents" + os.sep in path
    start, end = descriptor.get("offset"), descriptor.get("end")
    if type(start) is not int or type(end) is not int or end <= start:
        raise SourceError("source-range")
    raw = _read(path, start, end - start)
    if hashlib.sha256(raw).hexdigest() != descriptor.get("sha256"):
        raise SourceError("source-changed")
    try:
        record = json.loads(raw)
    except (ValueError, UnicodeError):
        raise SourceError("source-format")
    role, text, typed, control = _content(record)
    if role != descriptor.get("role") or _identity(descriptor["session_id"], role, raw, record, subagent) != descriptor.get("source_id"):
        raise SourceError("source-identity")
    if descriptor.get("content_sha256") and descriptor["content_sha256"] != _content_hash(record, role, subagent):
        raise SourceError("source-identity")
    if not text:
        raise SourceError("source-empty")
    return {"id": descriptor["source_id"], "source_id": descriptor["source_id"], "role": role, "origin": role,
            "text": text, "direct_user": bool(typed and not subagent), "control_user": bool(control and not subagent),
            "session_id": descriptor["session_id"],
            "at": record.get("timestamp") if isinstance(record.get("timestamp"), str) else datetime.datetime.fromtimestamp(descriptor["observed_at"], datetime.timezone.utc).isoformat(),
            "sha256": descriptor["sha256"], "message_id": record.get("uuid"), "subagent": subagent}
