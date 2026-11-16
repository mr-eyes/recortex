"""Bounded native transcript pointers. Source contents exist only in the worker's memory."""
import datetime
import functools
import hashlib
import json
import os
import re
import stat
import time

try:
    import grp
    import pwd
except ImportError:  # no user database: group write is never accepted
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


def private_group(uid, gid):
    """True when `gid` is the user private group of `uid`: the owner's primary group, named after the owner, with no
    member but the owner, and no other user's primary group. Only then is a group-writable folder as private as one
    without group write. Default Ubuntu makes ~/.claude and ~/.claude/projects 775 in such a group."""
    if grp is None or pwd is None or type(uid) is not int or type(gid) is not int:
        return False
    return _private_group(uid, gid)


@functools.lru_cache(maxsize=16)
def _private_group(uid, gid):
    try:
        owner = pwd.getpwuid(uid)
        group = grp.getgrgid(gid)
        if gid != owner.pw_gid or group.gr_name != owner.pw_name:
            return False
        if any(member != owner.pw_name for member in group.gr_mem):
            return False
        return not any(entry.pw_gid == gid and entry.pw_uid != uid for entry in pwd.getpwall())
    except (KeyError, OverflowError, OSError):
        return False


def _writable(info):
    """World write, or group write by any group but the owner's private group."""
    return bool(info.st_mode & stat.S_IWOTH) or bool(info.st_mode & stat.S_IWGRP) and not private_group(
        info.st_uid, getattr(info, "st_gid", None))


def _component_problem(path, info, folder, source_file=None):
    """One ownership/mode policy for preflight and actual source resolution.

An ancestor's owner can replace its children even when group/other write bits
are clear. Sticky mode protects entries from other users, not from that owner.
Only sticky ancestors owned by root or the current user, outside the source
tree, are an exception to the write-bit rule; source directories stay private.
Group write is accepted only for the owner's user private group (private_group).
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
    if _writable(info) and not shared_sticky:
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
        if _writable(info):
            raise SourceError("source-permissions")
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


def _content(record):
    message = record.get("message")
    if not isinstance(message, dict):
        return "unknown", "", False
    role = message.get("role") or record.get("type")
    if role not in ("user", "assistant"):
        return "unknown", "", False
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        if any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
            role = "tool"
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif block.get("type") == "tool_use" and block.get("name") == "SubagentHandback":
                message = (block.get("input") or {}).get("message")
                if isinstance(message, str):
                    parts.append(message)
            elif block.get("type") == "tool_use":
                parts.append("Assistant tool invocation (request, not completion): " + json.dumps(
                    {key: block.get(key) for key in ("id", "name", "input")}, sort_keys=True, ensure_ascii=False))
            elif block.get("type") == "tool_result":
                result = block.get("content")
                if isinstance(result, str):
                    parts.append(result)
                elif isinstance(result, list):
                    parts.extend(item["text"] for item in result if isinstance(item, dict)
                                 and item.get("type") == "text" and isinstance(item.get("text"), str))
        text = "\n".join(parts)
    else:
        text = ""
    direct = role == "user" and not record.get("isMeta") and not record.get("isSidechain")
    if re.search(r"(?m)^\s*>|<pasted_content\b|<system-reminder\b|<tool_result\b", text):
        direct = False
    return role, text, direct


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
        role, text, _ = _content(record) if isinstance(record, dict) else ("unknown", "", False)
        if text:
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


def capture_descriptors(data, project_root, event_type=None):
    """Compatibility helper for a bounded tail; scan+atomic cursor is preferred by hooks."""
    selected = dict(data)
    if event_type:
        selected["hook_event_name"] = event_type
    return scan(selected, project_root)[0]


def _tool_provenance(path, start, record, session, observed_at):
    """Match output to an earlier native invocation within a bounded local window.

No name inferred from output text; absent/duplicate IDs remain unmatched. The
matching descriptor is metadata only and its input is read transiently.
"""
    content = (record.get("message") or {}).get("content")
    results = [block for block in content if isinstance(block, dict) and block.get("type") == "tool_result"] if isinstance(content, list) else []
    wanted = {block.get("tool_use_id") for block in results if isinstance(block.get("tool_use_id"), str)
              and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", block["tool_use_id"])}
    if not wanted:
        return [], []
    low = max(0, start - MAX_READ)
    previous = _read(path, low, start - low)
    if low and _read(path, low - 1, 1) != b"\n":
        cut = previous.find(b"\n")
        if cut < 0:
            return [], []
        low += cut + 1; previous = previous[cut + 1:]
    matches = {}
    subagent = os.sep + "subagents" + os.sep in path
    offset = low
    for line in previous.splitlines(keepends=True):
        end = offset + len(line)
        try:
            prior = json.loads(line)
        except (ValueError, UnicodeError):
            offset = end
            continue
        message = prior.get("message") if isinstance(prior, dict) else None
        if isinstance(message, dict) and message.get("role") == "assistant" and isinstance(message.get("content"), list):
            for block in message["content"]:
                if (isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("id"), str) and block["id"] in wanted
                        and isinstance(block.get("name"), str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", block["name"])
                        and isinstance(block.get("input"), dict)):
                    metadata = {"source_id": _identity(session, "assistant", line, prior, subagent), "session_id": session,
                                "role": "assistant", "event_type": "PostToolUse", "path": path, "offset": offset, "end": end,
                                "sha256": hashlib.sha256(line).hexdigest(), "content_sha256": _content_hash(prior, "assistant", subagent),
                                "observed_at": observed_at}
                    matches.setdefault(block["id"], []).append((block, metadata))
        offset = end
    provenance, invocations = [], []
    for result in results:
        ident = result.get("tool_use_id")
        item = {"tool_use_id": ident if isinstance(ident, str) else "", "matched": False,
                "is_error": result.get("is_error") if type(result.get("is_error")) is bool else None,
                "output": result.get("content")}
        found = matches.get(ident, []) if isinstance(ident, str) else []
        if len(found) == 1:
            block, metadata = found[0]
            item.update(matched=True, tool_name=block["name"], input=block["input"], invocation_source_id=metadata["source_id"])
            if not any(m["source_id"] == metadata["source_id"] for m in invocations):
                invocations.append(metadata)
        provenance.append(item)
    return provenance, invocations


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
    role, text, direct = _content(record)
    if role != descriptor.get("role") or _identity(descriptor["session_id"], role, raw, record, subagent) != descriptor.get("source_id"):
        raise SourceError("source-identity")
    if descriptor.get("content_sha256") and descriptor["content_sha256"] != _content_hash(record, role, subagent):
        raise SourceError("source-identity")
    if not text:
        raise SourceError("source-empty")
    subagent = os.sep + "subagents" + os.sep in path
    provenance, invocations = _tool_provenance(path, start, record, descriptor["session_id"], descriptor["observed_at"]) if role == "tool" else ([], [])
    out = {"id": descriptor["source_id"], "source_id": descriptor["source_id"], "role": role, "origin": role,
            "text": text, "direct_user": bool(direct and not subagent), "session_id": descriptor["session_id"],
            "at": record.get("timestamp") if isinstance(record.get("timestamp"), str) else datetime.datetime.fromtimestamp(descriptor["observed_at"], datetime.timezone.utc).isoformat(),
            "sha256": descriptor["sha256"], "message_id": record.get("uuid"), "subagent": subagent,
            "tool_results": provenance, "invocation_descriptors": invocations}
    if len(provenance) == 1 and provenance[0].get("matched"):
        out["invocation_source_id"] = provenance[0]["invocation_source_id"]
    return out
