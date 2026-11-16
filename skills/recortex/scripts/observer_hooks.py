"""Opted-in local capture and explicit holds; no network or inference in a hook.

The native transcript, not hook prompt text or a model result, establishes the
source role. Collection and controls are separate from the disposable index.
"""
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time

import index
import lint_drafts
import names
import observer_context
import observer_holds
import observer_runtime
import observer_sources
import observer_store
import recortex
import storage

EVENTS = {"session-start": "SessionStart", "subagent-start": "SubagentStart",
          "prompt-submit": "UserPromptSubmit", "pre-compact": "PreCompact",
          "pre-tool": "PreToolUse", "post-tool": "PostToolUse", "tool-failure": "PostToolUseFailure",
          "stop": "Stop", "subagent-stop": "SubagentStop"}
NOTICES = {
    "approval": "recortex observer: local approval is missing or settings changed; observation is inactive.",
    "capture": "recortex observer: capture is incomplete; pending work remains local. Run observer.py status.",
    "source": "recortex observer: transcript sources are unavailable or incomplete; new holds may not be active. Run observer.py status.",
    "runtime": "recortex observer: independent checks are unavailable; captured work remains queued. Run observer.py status.",
    "context": "recortex observer: task context is unavailable; read the current record before relying on it.",
    "paused": "recortex observer: capture is paused; earlier holds remain active. Run observer.py status.",
    "control": "recortex observer: an explicit hold was recorded. Run observer.py holds.",
    "worker": "recortex observer: the worker could not start; captured work remains queued. Run observer.py status.",
    "pending": "recortex observer: captured work is still pending; it is not yet verified in GitHub. Run observer.py status.",
    "budget": "recortex observer: the model budget is exhausted; captured work remains queued. Run observer.py status.",
    "control-refused": "recortex observer: an explicit control could not be stored safely; that control is not active. Run observer.py status.",
    "cap": "recortex observer: today's limit of automatic comments is reached; reviewed memories wait in the queue. Run observer.py pending.",
    "release-refused": "recortex observer: nothing in a session releases a hold; run observer.py release HOLD_ID in a separate terminal, outside Claude Code. Run observer.py holds.",
}
TOOL_EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure")
SCAN_ROUNDS = 16  # bounded scans one hook makes to catch up with its transcript
STALE = 900  # seconds after which new captured work that no worker has taken up is worth a notice
UNCHECKED = {"reason": "recortex observer: this machine's holds could not be checked, so every command but pwd, ls and "
                       "cat is stopped. The user can see why with observer.py status, in a separate terminal outside "
                       "Claude Code."}
# Lines that answer something the user just did (a hold, a release); each arises once, so it is never held back.
ANSWERS = frozenset(("control", "control-refused", "release-refused"))


def _output(event, context="", notices=(), denial=None):
    if event == "PreCompact":
        return {}  # Claude Code discards a PreCompact hook's systemMessage; its capture has run
    out = {}
    if notices:
        out["systemMessage"] = "\n".join(dict.fromkeys(NOTICES[code] for code in notices))
    if context or denial:
        specific = {"hookEventName": event}
        if context and event not in ("Stop", "SubagentStop", "PreCompact"):
            specific["additionalContext"] = context
        if denial:
            specific.update(permissionDecision="deny", permissionDecisionReason=denial["reason"])
        if len(specific) > 1:
            out["hookSpecificOutput"] = specific
    return out


def launch_worker(project_root):
    """Detach a finite worker; its repository lock prevents competing workers."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "observer.py")
    subprocess.Popen([sys.executable, path, "run-worker"], cwd=project_root,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True)


def _controls(store, rows, project_root, repo, config):
    """Holds from the user's messages in the transcript. A release found there is never applied: only observer.py
    release, run by the user in a terminal, releases a hold. When it names a hold that is still active, the third value
    says so, and the session is told how to release it."""
    controls, refused, unproven = [], False, False
    for row in rows:
        if row.get("role") != "user":
            continue
        source = observer_sources.resolve(row, project_root)
        control = observer_holds.compile_prompt_control(source["text"], source, repo)
        if control and control["operation"] == "release":
            unproven = unproven or any(hold.get("id") == control["hold_id"] and hold.get("state") == "active"
                                       for hold in store.holds())
            continue
        if control:
            # What is stored is the hold's quote: the whole message, or only the phrase that withheld a session.
            if any(f.code in ("secret", "restricted") for f in lint_drafts.lint_text(control["hold"]["quote"],
                                                                                     config=config)):
                refused = True
                continue
            controls.append((control, source))
    if not controls:
        return False, refused, unproven

    def change(holds):
        changed = list(holds)
        for control, source in controls:
            hold = control["hold"]
            if not any(item.get("id") == hold["id"] for item in changed):
                changed.append(hold)
        return changed

    store.update_holds(change)
    return True, refused, unproven


def _capture(store, event, data, project_root, repo, config, capture=True):
    # PreToolUse rescans the persisted transcript synchronously: never wait for a
    # model/worker to compile a recognized original-user hold before an action.
    scan_data = dict(data, hook_event_name=event)
    path = scan_data.get("agent_transcript_path") if event == "SubagentStop" else scan_data.get("transcript_path")
    if not isinstance(path, str):
        raise observer_sources.SourceError("source-path")
    stream = hashlib.sha256(path.encode("utf-8")).hexdigest()
    start = store.cursor(stream) or 0
    if not capture:
        stream = "controls_" + stream
    controlled = refused = unproven = False
    # Each scan is bounded; a few in a row catch up with a transcript that grew by a large turn (attachments alone
    # can pass a scan's 256 KiB), so that lag is not reported as a problem.
    for _ in range(SCAN_ROUNDS):
        old = store.cursor(stream)
        rows, cursor = observer_sources.scan(scan_data, project_root, cursor=old if old is not None else start)
        # Holds are saved first; a crash can repeat an idempotent control, but must
        # never advance a cursor past a hold that has not been compiled.
        found = _controls(store, rows, project_root, repo, config)
        controlled, refused, unproven = controlled or found[0], refused or found[1], unproven or found[2]
        store.capture(rows if capture else [], stream=stream, cursor=cursor, expected_cursor=old)
        if cursor == (old if old is not None else start) or os.stat(path, follow_symlinks=False).st_size <= cursor:
            break
    # What is left is a line Claude Code is still writing, or a backlog the next hook goes on with.
    return controlled, os.stat(path, follow_symlinks=False).st_size > cursor, refused, unproven


def _notices(store, notices, data, event):
    """Bounded suppression state holds only hashes and times, never source text."""
    session = data.get("session_id", "")
    if not isinstance(session, str) or not observer_sources.SESSION.fullmatch(session):
        return notices
    key = hashlib.sha256(session.encode()).hexdigest()[:24]
    previous = store.get_control("hook_notices", {})
    if not isinstance(previous, dict):
        previous = {}
    now = time.time()
    old = previous.get(key, {})
    shown = set(old.get("codes", [])) if isinstance(old.get("codes"), list) else set()
    # Each line is shown once per session, whatever event carries it: a turn alternates between sets (a prompt
    # without "pending", a stop with it), so comparing whole sets would show both lines on every turn. A line that
    # answers something the user just did is always shown.
    fresh = [code for code in notices if code not in shown or code in ANSWERS]
    if not fresh:
        return []
    previous[key] = {"codes": sorted(shown | set(notices)), "at": now}
    previous = dict(sorted(previous.items(), key=lambda pair: pair[1].get("at", 0))[-50:])
    store.set_control("hook_notices", previous)
    return fresh


def _entry_repo():
    """The repository whose holds apply here: the one this folder's private entry names, never the project file's,
    which a single edit could change. None without an entry; False when the approvals cannot be read or the entry
    names no valid repository, so that the check fails closed."""
    try:
        entry = observer_runtime.stored_approval()
    except (ValueError, storage.StorageError, OSError):
        return False
    if entry is None:
        return None
    repo = entry.get("repo") if isinstance(entry, dict) else None
    return repo if recortex.valid_repo(repo) else False


def _unchecked(data):
    """The decision when the holds cannot be known: every Bash command but the plainest reads is denied."""
    tool_name, tool_input = data.get("tool_name"), data.get("tool_input")
    if observer_holds.classify_action(tool_name, tool_input)["command"] is None:
        return None
    return None if observer_holds.read_only(tool_name, tool_input) else UNCHECKED


SELF_RELEASE = {"reason": "recortex observer: only the user releases a hold, switches the observer off, resumes it or "
                          "turns on automatic posting, in a separate terminal outside Claude Code."}
UNDO = re.compile(r"observer(?:\.py)?\b.*\b(?:release|disable|resume|automatic)\b")


def _undo(data):
    """True for a Bash call that names observer.py together with release, disable, resume or automatic mode: the
    user's own commands, which a session never needs. The command is read as the shell splits it (quotes taken out),
    and as written when it does not split. A guard against mistakes: a session with shell access can get round it."""
    command = (data.get("tool_input") or {}).get("command") if isinstance(data.get("tool_input"), dict) else None
    if data.get("tool_name") not in ("Bash", "bash") or not isinstance(command, str):
        return False
    try:
        spoken = " ".join(shlex.split(command))
    except ValueError:
        spoken = command
    return bool(UNDO.search(spoken) or UNDO.search(command))


def _held(repo, data):
    """The decision before a tool call, from holds read without any lock (observer_store.read_holds). When they cannot
    be read, every Bash command is denied with one fixed reason, except pwd, ls and cat with plain path arguments
    (observer_holds.read_only), and those too when the held exact commands, still known beside the queue, name them.
    No hold applies to another tool."""
    tool_name, tool_input = data.get("tool_name"), data.get("tool_input")
    try:
        holds = observer_store.read_holds(repo)
    except (observer_store.StoreError, storage.StorageError, OSError, ValueError, KeyError, TypeError):
        command = observer_holds.classify_action(tool_name, tool_input)["command"]
        if command is None:
            return None  # not a shell command: no hold applies to it
        if not observer_holds.read_only(tool_name, tool_input):
            return UNCHECKED
        try:
            return UNCHECKED if observer_store._command_digest(command) in observer_store.held_commands(repo) else None
        except observer_store.StoreError:
            return None  # the plainest reads go on even when the held commands are unknown
    result = observer_holds.check_action(holds, repo, tool_name, tool_input, session_id=data.get("session_id"))
    return result if result["decision"] == "deny" else None


def _degraded(store, reason):
    try:
        store.set_control("hook_degraded", reason)
    except observer_store.StoreError:
        pass  # a full queue must not prevent checking an already stored hold


def _context(store, event, data, repo, allowed):
    if event not in ("UserPromptSubmit", "SubagentStart", "PreToolUse"):
        return ""
    query = data.get("prompt", "")
    if event == "PreToolUse":
        # Only before a supported git or gh action, where a hold can apply; other calls would add up to 4,000
        # characters whenever their words match another group.
        if not observer_holds.classify_action(data.get("tool_name"), data.get("tool_input"))["actions"]:
            return ""
        query = json.dumps(data.get("tool_input", {}), sort_keys=True)
    if not isinstance(query, str):
        query = ""
    db = index.connect(repo, create=False, tighten=False)
    if db is None:
        raise ValueError("context-unavailable")
    try:
        active = [row[0] for row in db.execute("SELECT issue FROM labels WHERE label='active' ORDER BY issue")]
        packet = observer_context.build_packet(db, repo, allowed, query=query[:48000],
                                               issue=active[0] if len(active) == 1 else None, max_chars=4000)
    finally:
        db.close()
    session = data.get("session_id", "")
    if not isinstance(session, str) or not observer_sources.SESSION.fullmatch(session):
        return ""
    # The selection, not the text: the text carries the index's sync time, so every sync would make it new.
    digest = hashlib.sha256(json.dumps([packet.get("selected"), packet.get("resume_id")], sort_keys=True,
                                       default=str).encode("utf-8")).hexdigest()
    key = hashlib.sha256((session + ":" + str(data.get("agent_id", ""))).encode()).hexdigest()[:24]
    seen = store.get_control("hook_context", {})
    if not isinstance(seen, dict):
        seen = {}
    if seen.get(key, {}).get("digest") == digest:
        return ""
    seen[key] = {"digest": digest, "at": time.time()}
    store.set_control("hook_context", dict(sorted(seen.items(), key=lambda pair: pair[1].get("at", 0))[-50:]))
    return packet["text"]


def handle(event, data, config, repo):
    """Return hook JSON. Empty means no observer decision, never tool approval.

The CLI must call this before model/worker dispatch. Inputs are native Claude
hook JSON; they do not confer authority to stored quotes or arbitrary files.
"""
    if names.env(observer_runtime.GUARD, ""):
        return {}
    event = EVENTS.get(event, event)
    if event not in EVENTS.values() or not isinstance(data, dict) or not isinstance(config, dict):
        return {}
    # A hold stays in force while this folder has an entry on this machine, whatever the project file says (its
    # repository, its trust, or "observer": false): one edit of a shared file must not lift what the user held. The
    # holds of the repository the project file names apply too, whether or not this folder's approval holds.
    held, checked = None, False
    if event == "PreToolUse":
        held_repo = _entry_repo()
        if held_repo is not None and _undo(data):
            # Always, not only while a hold applies: these are the user's commands, run in their own terminal.
            return _output(event, denial=SELF_RELEASE)
        if held_repo is not None:
            held, checked = (_held(held_repo, data) if held_repo else _unchecked(data)), True
            if not held and recortex.valid_repo(repo) and repo.lower() != held_repo:
                held = _held(repo.lower(), data)
    if not recortex.valid_repo(repo) or repo.lower() not in recortex.trusted_repos():
        return _output(event, denial=held) if held else {}
    raw = config.get("observer", {})
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        return _output(event, denial=held) if held else {}
    notices, context, denial = [], "", held
    try:
        settings = observer_runtime.settings(config)
        project_root = recortex.project_root()
        if not observer_runtime.approval_valid(repo, config):
            # This machine's entry for the folder does not hold (another repository or machine key, or settings
            # that no longer validate). Nothing is created, and the line shows at session start only.
            return _output(event, notices=["approval"] if event == "SessionStart" else [], denial=held)
        if event == "PreToolUse" and not checked:
            denial = _held(repo, data)
        with observer_store.Store(repo, max_bytes=settings["max_pending_bytes"], wait=0.1,
                                  max_pending=settings["max_pending_observations"], config=config) as store:
            paused = store.get_control("capture_paused", False)
            if paused:
                notices.append("paused")
            if event in observer_sources.EVENTS:
                try:
                    controlled, incomplete, refused, unproven = _capture(store, event, data, project_root, repo,
                                                                         config, capture=not paused)
                    if controlled:
                        notices.append("control")
                    # A transcript still being written, or a backlog the next hook goes on with, is not worth a line;
                    # a hold typed now that the transcript does not show yet is: it may not be active.
                    waiting = False
                    if event == "UserPromptSubmit" and not controlled and isinstance(data.get("prompt"), str):
                        typed = observer_holds._control(data["prompt"].strip())
                        waiting = bool(typed and typed["operation"] == "hold")
                    if unproven:
                        notices.append("release-refused")
                    if waiting:
                        notices.append("source")
                        _degraded(store, "source-incomplete")
                    elif refused:
                        notices.append("control-refused")
                        _degraded(store, "control-unavailable")
                    else:
                        _degraded(store, "")
                except TimeoutError:
                    raise  # The one-shot hook deadline must end work, not start more I/O.
                except (observer_sources.SourceError, OSError):
                    notices.append("source")
                    _degraded(store, "source-unavailable")
                except ValueError:
                    notices.append("control-refused")
                    _degraded(store, "control-unavailable")
                except observer_store.StoreError:
                    notices.append("capture")
                    _degraded(store, "capture-unavailable")
            if event == "PreToolUse":
                result = observer_holds.check_action(store.holds(), repo, data.get("tool_name"),
                                                     data.get("tool_input"), session_id=data.get("session_id"))
                denial = result if result["decision"] == "deny" else denial  # nothing in a session releases a hold
            try:
                context = _context(store, event, data, repo, recortex.allowed_users(repo))
            except TimeoutError:
                raise
            except Exception:
                notices.append("context")
            runtime_ready = observer_runtime.gate_valid(store.get_control("runtime_gate"), settings)
            if not runtime_ready:
                notices.append("runtime")
            status = store.status()
            retention_last = store.get_control("retention_last", 0)
            maintenance_due = (type(retention_last) not in (int, float) or
                               time.time() - retention_last >= 86400)
            has_handled = any(status["events"].get(state, 0) for state in observer_store.TERMINAL)
            # Local retention remains available while models are unavailable or
            # capture is paused. The worker checks its model/network gates itself.
            # Tool events fire on every call; the worker starts from the edges of a turn only.
            if event in ("SessionStart", "UserPromptSubmit", "Stop", "SubagentStop", "PreCompact") and (
                    runtime_ready or (maintenance_due and has_handled)):
                try:
                    launch_worker(project_root)
                except OSError:
                    notices.append("worker")
            if store.get_control("hook_degraded") and not any(code in notices for code in ("source", "capture")):
                notices.append("capture")
            if status["invocations_24h"] >= settings["max_invocations"]:
                notices.append("budget")
            # Pending work is worth a line only when the worker cannot take it up (a degraded worker), or when new
            # captured work has waited longer than STALE; ordinary backlog at a stop is not. Once per session.
            degraded = store.get_control("worker_degraded")
            if degraded:
                notices.append("cap" if degraded == "publish_daily_cap" else "pending")
            elif event in ("Stop", "SubagentStop", "PreCompact"):
                oldest = store.db.execute("SELECT MIN(created) FROM observations WHERE state='observed'").fetchone()[0]
                if oldest is not None and time.time() - oldest > STALE:
                    notices.append("pending")
            notices = _notices(store, notices, data, event)
    except Exception:
        # Fixed text only: exception strings can contain source text or paths. Tool events fire on every call and
        # cannot record a suppression when the queue is what failed, so the notice waits for the prompt or stop.
        if event not in TOOL_EVENTS:
            notices.append("capture")
    return _output(event, context=context, notices=notices, denial=denial)
