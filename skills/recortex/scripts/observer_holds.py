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
# Every reason an event is kept from posting on its own, in one place. The publisher raises only these for a hold or
# a held-back event, the worker defers an event with them, and the queue marks an event deferred or discarded with
# one of them as held, for good, so that a copy of it is never posted around it (observer_store.Store.copy_of):
# - explicit_hold: a /recortex hold covers the comment (check_action);
# - withheld: the user kept that session off the record (publication_withheld);
# - status_change: a change of an earlier memory's status that rests on neither the user's typed words nor the
#   agent's message to the user.
# The value says who lifts it: "user", the user's own act (a release); "answer", the user's answer to the question the
# main session asks at the next session start or pause (post it, or drop it), through --eventkey.
# The observer does not judge confidentiality, so no reason is about a marked project.
HELD_REASONS = {"explicit_hold": "user", "withheld": "user", "status_change": "answer"}
ANSWER_REASONS = tuple(sorted(reason for reason, who in HELD_REASONS.items() if who == "answer"))

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


class Unparsed(ValueError):
    """A shell line this module does not take apart: an unbalanced quote, a command or process substitution, a
    here-document, a subshell."""


OPERATORS = (";;", "&&", "||", "|&", ";", "&", "|")
REDIRECT = re.compile(r"(?:[0-9]*|&)(?:>>|>&|>\||<<<|<<-?|<>|<&|>|<)")


def _shell_tokens(command):
    """The words of a shell line, with ";" for every command separator (; && || | & and newlines), redirections and
    comments dropped. Raises Unparsed for what is not taken apart here."""
    out, word, quoted, i, n = [], [], False, 0, len(command)

    def end():
        nonlocal word, quoted
        if word or quoted:
            out.append("".join(word))
        word, quoted = [], False

    while i < n:
        char = command[i]
        if char in " \t":
            end()
            i += 1
        elif char in "\r\n":
            end()
            out.append(";")
            i += 1
        elif char == "\\":
            if i + 1 < n and command[i + 1] == "\n":
                i += 2  # a line continuation
            else:
                word.append(command[i + 1] if i + 1 < n else "")
                i += 2
        elif char == "'":
            close = command.find("'", i + 1)
            if close < 0:
                raise Unparsed("quote")
            word.append(command[i + 1:close])
            quoted, i = True, close + 1
        elif char == '"':
            i += 1
            while True:
                if i >= n:
                    raise Unparsed("quote")
                if command[i] == '"':
                    break
                if command[i] == "`" or command.startswith("$(", i):
                    raise Unparsed("substitution")
                if command[i] == "\\" and i + 1 < n and command[i + 1] in '"\\$`\n':
                    i += 1
                word.append(command[i])
                i += 1
            quoted, i = True, i + 1
        elif char == "`" or command.startswith("$(", i) or command.startswith("<(", i) or command.startswith(">(", i):
            raise Unparsed("substitution")
        elif char in "()":
            raise Unparsed("subshell")
        elif char == "#" and not word and not quoted:
            while i < n and command[i] not in "\r\n":
                i += 1
        else:
            match = REDIRECT.match(command, i) if char in "0123456789&<>" else None
            if match and (char in "<>&" or not word):  # a descriptor number starts its own word
                operator = match.group(0)
                if "<<" in operator and "<<<" not in operator:
                    raise Unparsed("here-document")
                end()
                i = match.end()
                while i < n and command[i] in " \t":
                    i += 1
                if operator.endswith("&") and i < n and (command[i].isdigit() or command[i] == "-"):
                    while i < n and (command[i].isdigit() or command[i] == "-"):
                        i += 1
                    continue
                i = _shell_tokens_word(command, i)  # the target: one word, dropped
                continue
            operator = next((op for op in OPERATORS if command.startswith(op, i)), None)
            if operator:
                end()
                out.append(";")
                i += len(operator)
            else:
                word.append(char)
                i += 1
    end()
    return out


def _shell_tokens_word(command, i):
    """The index after the shell word that starts at `i` (a redirection's target)."""
    n = len(command)
    while i < n and command[i] not in " \t\r\n;&|<>()":
        if command[i] == "'":
            close = command.find("'", i + 1)
            if close < 0:
                raise Unparsed("quote")
            i = close + 1
        elif command[i] == '"':
            i += 1
            while i < n and command[i] != '"':
                if command[i] == "`" or command.startswith("$(", i):
                    raise Unparsed("substitution")
                i += 2 if command[i] == "\\" else 1
            if i >= n:
                raise Unparsed("quote")
            i += 1
        elif command[i] == "`" or command.startswith("$(", i):
            raise Unparsed("substitution")
        else:
            i += 2 if command[i] == "\\" else 1
    return i


ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.S)
PREFIXES = {"command": (), "nohup": (), "exec": (), "nice": ("-n",), "time": (), "env": ("-u", "-C", "-S"),
            "sudo": ("-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T")}
SHELLS = ("bash", "sh", "dash", "zsh", "ksh")
# For a line not taken apart: a held action whose words are all in it is taken as present (a wrong denial is
# acceptable, since the user asked for the hold).
ACTION_WORDS = {"git-push": ("git", "push"), "git-tag": ("git", "tag"), "gh-release": ("gh", "release")}


def _simple(words):
    """The words of one simple command without its leading assignments and wrapper prefixes (env, sudo, command,
    nohup, exec, nice, time, a { group), each prefix's own options included."""
    words = list(words)
    while words:
        head = words[0].rsplit("/", 1)[-1] if words[0] not in ("{", "}") else words[0]
        if ASSIGNMENT.fullmatch(words[0]) or words[0] in ("{", "}", "!"):
            words.pop(0)
        elif head in PREFIXES:
            takes = PREFIXES[head]
            words.pop(0)
            while words and (words[0].startswith("-") or head == "env" and ASSIGNMENT.fullmatch(words[0])):
                option = words.pop(0)
                if option in takes and words:
                    words.pop(0)
        else:
            break
    return words


def _hits(command, depth=0):
    """[(action, target repository or None)] of the held actions in a shell line, part by part; raises Unparsed."""
    words, hits, parts = _shell_tokens(command), [], [[]]
    for word in words:
        if word == ";":
            parts.append([])
        else:
            parts[-1].append(word)
    for part in parts:
        part = _simple(part)
        if not part:
            continue
        program = part[0].rsplit("/", 1)[-1]
        if program in SHELLS:
            script = next((part[n + 1] for n, word in enumerate(part[1:-1], 1)
                           if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", word)), None)
            if script is not None:
                if depth >= 3:
                    raise Unparsed("depth")
                try:
                    hits += _hits(script, depth + 1)
                except Unparsed:
                    hits += [(action, None) for action in _worded(script)]
            continue
        hits += _part_hits(program, part[1:])
    return hits


def _part_hits(program, args):
    args = list(args)
    if program == "git":
        while args and args[0].startswith("-"):
            option = args.pop(0)
            if option in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env") and args:
                args.pop(0)  # its argument; a path that may name this repository: a held action is taken as one
        action, rest = (args[0], args[1:]) if args else ("", [])
        if action == "push" and not ({"--dry-run", "-n"} & set(rest)):
            return [("git-push", None)]
        if action == "tag" and rest and not ({"-l", "--list", "-v", "--verify"} & set(rest)):
            return [("git-tag", None)]
        return []
    if program == "gh":
        target, remaining = None, []
        while args:
            word = args.pop(0)
            if word in ("--repo", "-R") and args:
                target = args.pop(0)
            elif word.startswith("--repo="):
                target = word[len("--repo="):]
            else:
                remaining.append(word)
        if target is not None and not recortex.valid_repo(target):
            target = None  # not a repository name: taken as this one
        if len(remaining) >= 2 and remaining[0] == "release" and remaining[1] not in (
                "view", "list", "download", "verify", "verify-asset"):
            return [("gh-release", target)]
    return []


def _worded(command):
    """The held actions whose words are all in a line that is not taken apart."""
    found = set(re.findall(r"[A-Za-z][A-Za-z-]*", command))
    return sorted(action for action, words in ACTION_WORDS.items() if set(words) <= found)


def classify_action(tool_name, tool_input):
    """The held actions a Bash line may take (git-push, git-tag, gh-release), looked for in every simple command of it:
    split on ; && || | & and newlines, with redirections, leading assignments and the env, sudo, command, nohup, exec,
    nice and time prefixes left out, and inside bash -c and sh -c strings. A line that is not taken apart (an
    unbalanced quote, a substitution, a here-document, a subshell) counts as taking every held action whose words are
    all in it: a wrong denial is acceptable, since the user asked for the hold. `hits` pairs each action with the
    repository a gh --repo names, or None for this one. Exact-command holds compare the whole line. Aliases, scripts
    and programs run by other programs are not covered. The result never itself authorizes an action."""
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if tool_name not in ("Bash", "bash") or not isinstance(command, str):
        return {"supported": False, "actions": [], "hits": [], "command": None}
    try:
        if len(command) > 32768:
            raise Unparsed("length")
        hits = _hits(command)
        supported = True
    except Unparsed:
        hits, supported = [(action, None) for action in _worded(command)], False
    return {"supported": supported, "actions": sorted({action for action, _ in hits}), "hits": hits,
            "command": command}


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
        held = any(action == hold["action"] and (target or repo).lower() == hold["repo"]
                   for action, target in classified["hits"])
        if exact or held:
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
