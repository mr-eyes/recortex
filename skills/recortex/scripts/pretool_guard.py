#!/usr/bin/env python3
"""The PreToolUse check that holds in every project, whether or not the observer is on (run_pretool_guard.sh starts it
only for a Bash call whose input mentions the ask agent or the observer).

1. The ask agent (`agent_type` "recortex:ask", which Claude Code puts in the input of a subagent's tool call) is read-only
   because this check stops every Bash command of its that is not exactly
   `python3 <this scripts folder>/index.py query (find|recall|timeline|tasks|plan) ...` with no shell operator. The check
   fails closed: an input it cannot read, for that agent, is a denial. Any other input is left alone.
2. A Bash call that turns the observer on or changes its level or its stronger model (`observer.py enable`, `setup.py
   observer`, `setup.py apply`): when the user answered the session's question about it, a one-time grant is recorded
   (observer_grants) for the level or the model the user chose. Nothing is printed for it.

Only the Python standard library; the observer's own modules are imported only for the second job."""

import json
import os
import re
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ASK = "recortex:ask"
QUERIES = ("find", "recall", "timeline", "tasks", "plan")
# What may not appear in the command at all: a shell operator, a substitution, a line break or a control character. The
# check reads the command as written, so an operator inside quotes is refused too; the agent asks again without it.
OPERATORS = re.compile(r"[;&|<>`$\\(){}\x00-\x1f\x7f]")
DENIAL = ("recortex: the ask agent only reads the record. Run exactly one command, python3 %s/index.py query find, recall, "
          "timeline, tasks or plan with its options (--words, --issue, --json), with no cd, no &&, no pipe, no redirect "
          "and no other command.")
UNREADABLE = ("recortex: the ask agent only reads the record, and this command could not be checked, so it is stopped. "
              "Run python3 index.py query find, recall, timeline, tasks or plan, and nothing else.")


def deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def allowed_command(command, scripts=HERE):
    """True when `command` is exactly one `python3 <scripts>/index.py query <read> ...` and nothing else, with the scripts
    folder's own absolute path."""
    if not isinstance(command, str) or not command.strip() or len(command) > 4000 or OPERATORS.search(command):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if len(words) < 4 or words[0] != "python3" or words[2] != "query" or words[3] not in QUERIES:
        return False
    # The path is the plugin's own, written out in full: a relative one would be resolved against the hook's folder and
    # not the one the command runs in, and a link elsewhere would run another file. So it must be the same string, and
    # its real path must still be the plugin's real index.py.
    index = os.path.join(scripts, "index.py")
    try:
        return os.path.isabs(words[1]) and words[1] == index and os.path.realpath(words[1]) == os.path.realpath(index)
    except (OSError, ValueError):
        return False


def ask_decision(data, scripts=HERE):
    """The denial for the ask agent's call `data`, or None when the call may go on."""
    tool_input = data.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if data.get("tool_name") in ("Bash", "bash") and allowed_command(command, scripts):
        return None
    return deny(DENIAL % scripts)


def decide(raw, scripts=HERE):
    """The hook's output (a dict, or None for nothing) for the raw input text."""
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        data = None
    if not isinstance(data, dict):
        # Unreadable input: it is the ask agent's only when the text says so, and then it is stopped.
        return deny(UNREADABLE) if ASK in raw else None
    if data.get("agent_type") == ASK:
        return ask_decision(data, scripts)
    record_enable_grant(data)
    return None


def record_enable_grant(data):
    """Job 2: never raises and never prints; without the user's answer nothing is recorded."""
    command = (data.get("tool_input") or {}).get("command") if isinstance(data.get("tool_input"), dict) else None
    if data.get("tool_name") not in ("Bash", "bash") or not isinstance(command, str) or "observer" not in command:
        return
    try:
        import observer_grants
        observer_grants.record_from_hook(data)
    except Exception:  # noqa: BLE001 - a hook check must not break the session; the command is then refused for want of a grant
        pass


def main():
    raw = sys.stdin.read(8388609)
    out = decide(raw)
    if out:
        sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
