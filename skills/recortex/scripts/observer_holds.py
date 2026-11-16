#!/usr/bin/env python3
"""Explicit user controls and narrow local action checks; no I/O or persistence.

Callers must resolve source identity/role from the actual user transcript. Model
output never establishes user authority. Only a complete recognized user message
compiles into a hold. Arbitrary shell programs cannot be policed by this module.
"""

import hashlib
import json
import re
import shlex

import recortex


ACTIONS = frozenset(("git-tag", "git-push", "gh-release", "command", "publish"))
SOURCE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,200}")
HOLD_ID = re.compile(r"[a-f0-9]{24}")
PHRASES = {
    "do not push": "git-push", "don't push": "git-push", "never push": "git-push",
    "do not create tags": "git-tag", "do not tag": "git-tag", "don't tag": "git-tag", "never tag": "git-tag",
    "do not create a release": "gh-release", "do not release": "gh-release", "don't release": "gh-release",
}
# The user withholding a session's moments from the record: "don't post this", "keep this off the record". Found
# anywhere in a user's message, each holds the automatic publication of that session's moments (action "publish");
# the hold keeps only the phrase, never the message. The publisher enforces it; the main session posts only on the
# user's later, explicit request.
WITHHOLD = {"do not post": "publish", "don't post": "publish", "dont post": "publish", "never post": "publish",
            "do not record": "publish", "don't record": "publish", "dont record": "publish", "never record": "publish",
            "do not publish": "publish", "don't publish": "publish", "dont publish": "publish",
            "off the record": "publish"}
PHRASES.update(WITHHOLD)
WITHHELD = re.compile(r"(?<![A-Za-z0-9])(?:(?:do not|don't|dont|never)[ \t]+(?:post|record|publish)|off the record)"
                      r"(?![A-Za-z0-9])", re.IGNORECASE)


def withheld(text):
    """The phrase by which a message withholds its session's moments from the record, as a key of WITHHOLD, or None."""
    match = WITHHELD.search(text.replace("\u2019", "'")) if isinstance(text, str) else None
    return re.sub(r"[ \t]+", " ", match.group(0).lower()) if match else None


def _source(source):
    """A hold comes from a user message: one the user typed, or one of the forms a typed control is stored in (a
    /recortex command's wrapper). It only denies, so the looser rule (control_user) is enough."""
    sid = source.get("source_id") or source.get("id")
    if (source.get("role") != "user" or True not in (source.get("control_user"), source.get("direct_user")) or
            not isinstance(sid, str) or not SOURCE_ID.fullmatch(sid) or not isinstance(source.get("text"), str)):
        raise ValueError("a hold requires a resolved original user message")
    if len(source["text"]) > 8192:
        raise ValueError("hold control is too long")
    return sid, source["text"].strip()


NATIVE = re.compile(r"<command-message>[^<\n]*</command-message>\n<command-name>/(?:recortex:)?recortex</command-name>\n"
                    r"<command-args>([^<\n]*)</command-args>")


def _control(text):
    native = NATIVE.fullmatch(text)
    if native:
        # How Claude Code stores a typed /recortex command. A model's tool call can produce the same wrapper, which
        # can only add a hold (a hold only denies); no text in a session ever releases one (see release).
        text = ("/recortex " + native.group(1).strip()).strip()
    if "\n" in text or "\r" in text:
        return None
    if text.lower().rstrip(".! ") in PHRASES:
        return {"operation": "hold", "action": PHRASES[text.lower().rstrip(".! ")], "command": None, "plain": True}
    match = re.fullmatch(r"/recortex hold (git-tag|git-push|gh-release|command)(?: (.+))?", text)
    if match:
        action, command = match.groups()
        if (action == "command") != bool(command):
            return None
        return {"operation": "hold", "action": action, "command": command}
    match = re.fullmatch(r"/recortex release ([a-f0-9]{24})", text)
    return {"operation": "release", "hold_id": match.group(1)} if match else None


def _identity(repo, sid, action, scope, command):
    value = json.dumps([repo.lower(), sid, action, scope, command], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def make_hold(source, repo, action, scope=None, command=None):
    """Compile a recognized direct user command into a hold; default scope is repo.

scope optionally contains session_id and/or issue; both must match an action for
that hold to apply. Runtime-supplied scope must reflect the user's control UI.
"""
    sid, text = _source(source)
    parsed = _control(text)
    if (not recortex.valid_repo(repo) or action not in ACTIONS or not parsed or parsed["operation"] != "hold" or
            parsed["action"] != action or parsed["command"] != command):
        raise ValueError("the original user message does not establish this hold")
    scope = dict(scope or {})
    if set(scope) - {"session_id", "issue"}:
        raise ValueError("unsupported hold scope")
    if "session_id" in scope and (not isinstance(scope["session_id"], str) or not SOURCE_ID.fullmatch(scope["session_id"])):
        raise ValueError("invalid session scope")
    if "issue" in scope and (type(scope["issue"]) is not int or scope["issue"] < 1):
        raise ValueError("invalid issue scope")
    return {"id": _identity(repo, sid, action, scope, command), "repo": repo.lower(), "action": action,
            "scope": scope, "command": command, "state": "active", "source_id": sid, "source_role": "user",
            "quote": text, "released_by": None}


def compile_prompt_control(text, source, repo, session_id=None):
    """Recognize only a whole direct user control; None means ordinary conversation.

session_id identifies the observing session, but does not narrow the user's
repository-wide hold. Narrower scope is an explicit make_hold argument instead.
"""
    try:
        _, original = _source(source)
    except (ValueError, TypeError, AttributeError):
        return None
    if text.strip() != original:
        return None
    parsed = _control(original)
    if not parsed:
        phrase = withheld(original)
        if not phrase:
            return None
        # Within a longer message: a hold of this session's publication, quoting the phrase only.
        return {"operation": "hold", "hold": make_hold(dict(source, text=phrase), repo, "publish",
                                                       scope={"session_id": source.get("session_id")})}
    if parsed["operation"] == "release":
        return parsed
    # A plain phrase ("Don't push.") holds this session; /recortex hold holds the repository until released.
    scope = {"session_id": source.get("session_id")} if parsed.get("plain") else None
    return {"operation": "hold", "hold": make_hold(source, repo, parsed["action"], scope=scope, command=parsed["command"])}


def release(holds, hold_id, by="terminal"):
    """Return a new list with the named active hold released. Only observer.py release calls it, after the user
    confirmed in a terminal of their own: nothing in a session (a prompt, a transcript line, a model's tool call or
    a hook's input) releases a hold."""
    if not HOLD_ID.fullmatch(hold_id or ""):
        raise ValueError("the named hold does not exist")
    result, seen = [], False
    for old in holds:
        hold = dict(old)
        if hold.get("id") == hold_id and hold.get("state") == "active":
            hold.update(state="released", released_by=by)
            seen = True
        result.append(hold)
    if not seen:
        raise ValueError("the named hold does not exist")
    return result


PLAIN = re.compile(r"[A-Za-z0-9_./@%+,:-]+")


def read_only(tool_name, tool_input):
    """True only for the plainest reads: pwd, ls or cat with plain path arguments, no option and no shell syntax.
    When the holds cannot be read, these are the only Bash commands that go on: any option of a longer list of
    reading programs can run another program (rg --pre, git diff --ext-diff), and git obeys the repository's config."""
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if tool_name not in ("Bash", "bash") or not isinstance(command, str) or len(command) > 4096:
        return False
    words = command.split()
    if not words or words[0] not in ("pwd", "ls", "cat") or words[0] == "pwd" and len(words) > 1:
        return False
    return all(PLAIN.fullmatch(word) and not word.startswith("-") for word in words[1:])


def classify_action(tool_name, tool_input):
    """Recognize direct shell git-tag/git-push/gh-release operations only.

Exact-command holds work even when family classification is unsupported. Shell
expansion, scripts, aliases, compound commands and non-shell tools are not claimed
to be covered. The result never itself authorizes an action.
"""
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if tool_name not in ("Bash", "bash") or not isinstance(command, str):
        return {"supported": False, "actions": [], "command": None}
    if len(command) > 32768:
        return {"supported": False, "actions": [], "command": command}
    # Refuse to interpret shell execution syntax instead of guessing its effect.
    if any(char in command for char in ("\n", "\r", ";", "&", "|", "`", "$", "(", ")", "<", ">")):
        return {"supported": False, "actions": [], "command": command}
    try:
        words = shlex.split(command)
    except ValueError:
        return {"supported": False, "actions": [], "command": command}
    if not words:
        return {"supported": True, "actions": [], "command": command}
    if words[0] == "command":
        words = words[1:]
    if not words:
        return {"supported": False, "actions": [], "command": command}
    executable, args = words[0], words[1:]
    target_repo = None
    if executable not in ("git", "/usr/bin/git", "/bin/git", "gh", "/usr/bin/gh", "/bin/gh"):
        return {"supported": False, "actions": [], "command": command}
    if executable.endswith("git"):
        while args and args[0] in ("-C", "--git-dir", "--work-tree", "--no-pager", "--literal-pathspecs"):
            option = args.pop(0)
            if option in ("-C", "--git-dir", "--work-tree"):
                if not args:
                    return {"supported": False, "actions": [], "command": command}
                # This pure module cannot establish which repository that path
                # names. A hold on the current repository must not block another.
                return {"supported": False, "actions": [], "command": command, "target_unknown": True}
        action = args[0] if args else ""
        rest = args[1:]
        if action == "push":
            actions = [] if "--dry-run" in rest or "-n" in rest else ["git-push"]
        elif action == "tag":
            read_only = not rest or any(flag in rest for flag in ("-l", "--list", "-v", "--verify"))
            actions = [] if read_only else ["git-tag"]
        else:
            return {"supported": False, "actions": [], "command": command}
    else:
        remaining = []
        while args:
            word = args.pop(0)
            if word in ("--repo", "-R"):
                if not args or target_repo is not None:
                    return {"supported": False, "actions": [], "command": command, "target_unknown": True}
                target_repo = args.pop(0)
            elif word.startswith("--repo="):
                if target_repo is not None:
                    return {"supported": False, "actions": [], "command": command, "target_unknown": True}
                target_repo = word[len("--repo="):]
            else:
                remaining.append(word)
        args = remaining
        if target_repo is not None and not recortex.valid_repo(target_repo):
            return {"supported": False, "actions": [], "command": command, "target_unknown": True}
        if len(args) < 2 or args[0] != "release":
            return {"supported": False, "actions": [], "command": command}
        if args[1] in ("create", "edit", "delete", "upload", "delete-asset"):
            actions = ["gh-release"]
        elif args[1] in ("view", "list", "download", "verify", "verify-asset"):
            actions = []
        else:
            return {"supported": False, "actions": [], "command": command}
    return {"supported": True, "actions": actions, "command": command, "target_repo": target_repo}


def _valid(hold):
    try:
        rebuilt = make_hold({"source_id": hold["source_id"], "role": hold["source_role"], "direct_user": True,
                             "text": hold["quote"]}, hold["repo"], hold["action"], hold["scope"], hold["command"])
        return rebuilt["id"] == hold["id"]
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def check_action(holds, repo, tool_name, tool_input, session_id=None, issue=None):
    classified = classify_action(tool_name, tool_input)
    blocking = []
    for hold in holds:
        if not _valid(hold) or hold.get("state") != "active" or hold["repo"] != repo.lower():
            continue
        scope = hold["scope"]
        if ("session_id" in scope and scope["session_id"] != session_id) or ("issue" in scope and scope["issue"] != issue):
            continue
        exact = (hold["action"] == "command" and classified["command"] is not None and
                 classified["command"].strip() == hold["command"].strip())
        target_matches = not classified.get("target_unknown") and (classified.get("target_repo") or repo).lower() == hold["repo"]
        if exact or (target_matches and hold["action"] in classified["actions"]):
            blocking.append(hold["id"])
    return {"decision": "deny" if blocking else "allow", "hold_ids": sorted(set(blocking)),
            "reason": "The user has asked that this action not be taken." if blocking else "",
            "supported": classified["supported"] or bool(blocking)}


def publication_withheld(holds, repo, session_ids):
    """True when an active hold the user set ("don't post", "keep this off the record") withholds the automatic
    publication of a moment from any of these sessions."""
    sessions = set(session_ids)
    return any(_valid(hold) and hold.get("state") == "active" and hold["repo"] == repo.lower()
               and hold["action"] == "publish" and hold["scope"].get("session_id", None) in sessions | {None}
               for hold in holds)


def advisory_key(repo, source_ids, topic):
    """Stable receipt key for nonblocking advice about the same evidence."""
    data = json.dumps([repo.lower(), sorted(set(source_ids)), topic], separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def should_advise(key, seen_keys, relevant=True, material_change=False):
    """Ordinary advice never creates a hold; suppress repeated unchanged advice."""
    return bool(relevant and (material_change or key not in seen_keys))
