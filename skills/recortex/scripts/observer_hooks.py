"""Opted-in local capture and explicit holds; no network or inference in a hook.

The native transcript, not hook prompt text or a model result, establishes the
source role. Collection and controls are separate from the disposable index.
"""
import hashlib
import json
import os
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
UNCHECKED = {"reason": "recortex observer: the holds could not be checked, so this kind of action, which a hold can "
                       "stop, is stopped too. Run observer.py status."}
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
            if any(f.code in ("secret", "restricted") for f in lint_drafts.lint_text(source["text"], config=config)):
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
    old = store.cursor(stream)
    rows, cursor = observer_sources.scan(scan_data, project_root, cursor=old if old is not None else start)
    # Holds are saved first; a crash can repeat an idempotent control, but must
    # never advance a cursor past a hold that has not been compiled.
    controlled, refused, unproven = _controls(store, rows, project_root, repo, config)
    store.capture(rows if capture else [], stream=stream, cursor=cursor, expected_cursor=old)
    # Bounded scans may lag or end on a partially written line. Do not claim
    # complete capture or complete enforcement of newly submitted controls.
    incomplete = any(row.get("event_type") == "source_gap" for row in rows) or os.stat(path, follow_symlinks=False).st_size > cursor
    return controlled, incomplete, refused, unproven


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
    digest = hashlib.sha256(json.dumps(sorted(set(notices))).encode()).hexdigest()
    old = previous.get(key, {})
    # An unchanged set is shown once per session, Stop included: Stop fires after every turn.
    if old.get("digest") == digest and not set(notices) & ANSWERS:
        return []
    previous[key] = {"digest": digest, "at": now}
    previous = dict(sorted(previous.items(), key=lambda pair: pair[1].get("at", 0))[-50:])
    store.set_control("hook_notices", previous)
    return notices


def _held(repo, data):
    """The decision before a tool call, from holds read without any lock (observer_store.read_holds). When they cannot
    be read, a call a hold could stop (a push, a tag, a release, or a command under an exact-command hold) is denied
    with one fixed reason; every other call goes on."""
    tool_name, tool_input = data.get("tool_name"), data.get("tool_input")
    try:
        holds = observer_store.read_holds(repo)
    except (observer_store.StoreError, storage.StorageError, OSError, ValueError, KeyError, TypeError):
        classified = observer_holds.classify_action(tool_name, tool_input)
        if classified["actions"] or classified.get("target_unknown"):
            return UNCHECKED
        try:
            held = observer_store.held_commands(repo)
        except observer_store.StoreError:
            held = set()
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        return UNCHECKED if isinstance(command, str) and observer_store._command_digest(command) in held else None
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
    raw = config.get("observer", {})
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        return {}
    if not recortex.valid_repo(repo) or repo.lower() not in recortex.trusted_repos():
        return {}
    notices, context, denial = [], "", None
    try:
        settings = observer_runtime.settings(config)
        project_root = recortex.project_root()
        if not observer_runtime.approval_valid(repo, config):
            # This machine's entry for the folder does not hold (another repository or machine key, or settings
            # that no longer validate). Nothing is created, and the line shows at session start only.
            return _output(event, notices=["approval"]) if event == "SessionStart" else {}
        if event == "PreToolUse":
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
                    if event == "UserPromptSubmit" and not controlled and isinstance(data.get("prompt"), str):
                        typed = observer_holds._control(data["prompt"].strip())
                        if typed and typed["operation"] == "hold":
                            incomplete = True  # a hold typed now that the transcript does not show yet
                    if unproven:
                        notices.append("release-refused")
                    if incomplete:
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
                denial = result if result["decision"] == "deny" else None
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
            degraded = store.get_control("worker_degraded")
            if degraded:
                notices.append("cap" if degraded == "publish_daily_cap" else "pending")
            if event in ("Stop", "SubagentStop", "PreCompact") and any(
                    count for state, count in status["observations"].items() if state not in observer_store.TERMINAL):
                notices.append("pending")
            notices = _notices(store, notices, data, event)
    except Exception:
        # Fixed text only: exception strings can contain source text or paths. Tool events fire on every call and
        # cannot record a suppression when the queue is what failed, so the notice waits for the prompt or stop.
        if event not in TOOL_EVENTS:
            notices.append("capture")
    return _output(event, context=context, notices=notices, denial=denial)
