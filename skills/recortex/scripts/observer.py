#!/usr/bin/env python3
"""Explicit observer controls and bounded hook entry point. No automatic opt-in."""
import json
import os
import signal
import sys
import time

import names
import observer_holds
import observer_runtime as runtime
import observer_store
import observer_sources
import recortex
import storage


def emit(value):
    print(json.dumps(value, sort_keys=True, ensure_ascii=True))


def parser():
    import argparse  # the modules only some commands need are imported where they are used: a hook starts this
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--repo")
    sub = root.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("--source-details", action="store_true",
                        help="show native directory paths, modes and ownership for capture diagnosis")
    for name in ("holds", "runtime-check"):
        sub.add_parser(name)
    pending = sub.add_parser("pending")
    pending.add_argument("--eventkey")
    pending.add_argument("--show-body", action="store_true", help="explicitly display the reviewed comment for this event key")
    enable = sub.add_parser("enable", help="preview this machine's settings for this folder; --yes approves and saves them")
    enable.add_argument("--mode", choices=("shadow", "automatic"), default="shadow")
    enable.add_argument("--model", help="required: the Claude model alias or ID for the background calls")
    enable.add_argument("--yes", action="store_true")
    sub.add_parser("disable", help="remove this machine's settings and approval for this folder")
    for name in ("pause", "resume"):
        command = sub.add_parser(name)
        command.add_argument("scope", choices=("capture", "publication", "all"), default="all", nargs="?")
    for name in ("run-worker", "record", "reconcile"):
        command = sub.add_parser(name)
        command.add_argument("--dry-run", action="store_true")
        if name == "record":
            command.add_argument("--source", required=True, help="retry a known deferred source through independent review")
        if name == "reconcile":
            command.add_argument("eventkey")
    release = sub.add_parser("release", help="release a hold; run it yourself in a separate terminal, which asks you "
                                             "to confirm there")
    release.add_argument("hold_id")
    discard = sub.add_parser("discard", help="drop a reviewed event that will not be posted; its excerpts then expire")
    discard.add_argument("eventkey")
    context = sub.add_parser("context")
    context.add_argument("--query", default="")
    context.add_argument("--issue", type=int)
    hook = sub.add_parser("hook")
    hook.add_argument("event")
    return root


REASONS = frozenset(("model_required", "folder_name", "observer_settings", "observer_mode", "observer_publisher",
                     "observer_model", "observer_limit", "observer_approvals"))
NOTES = {
    "model_required": "Choose the model for the background calls with --model (a Claude alias or model ID).",
    "folder_name": "The project folder's path holds a quote, a backslash or a control character; the observer cannot "
                   "be enabled there.",
    "observer_approvals": "This machine's private observer approvals file is too large, not JSON or not of the "
                          "expected form; it is not replaced on its own. Move it aside, then enable again.",
}


def enable(repo, config, mode, yes=False, model=None, root=None, confirm=None):
    """Preview, or with yes=True record, this machine's settings for this project folder. Nothing is written to the
    project's shared configuration file, and nothing is created before every check has passed. `confirm`, when given,
    is asked last, before anything is written; a code it returns stops the write."""
    root = runtime.folder_key(root)
    problem = runtime.folder_problem(root)
    if problem:
        raise ValueError(problem)
    if not model:
        raise ValueError("model_required")
    options = runtime.settings({"observer": {"enabled": True, "mode": mode, "model": model}})
    try:
        identity = runtime.publisher_id(create=False)
    except (storage.StorageError, OSError):
        identity = None
    options["publisher_id"] = identity or "allocated_on_explicit_approval"
    preview = {"action": "enable", "repo": repo, "folder": root, "settings": options, "approved": False,
               "usage": runtime.USAGE_NOTE % (options["max_invocations"], options["max_input_chars"]),
               "note": "Settings stay on this machine for this folder; the project file can only switch the observer "
                       "off. An isolated runtime check must succeed before independent checks or automatic posting."}
    billing = runtime.billing_variables()
    if billing:
        preview["billing"] = runtime.BILLING_NOTE % ", ".join(billing)
    if runtime.project_off(config):
        preview["project_switch"] = "off"
    if not yes:
        return preview
    if runtime.project_off(config):
        return dict(preview, code="project-off")
    if mode == "automatic":
        try:
            with observer_store.Store(repo, create=False, readonly=True, config=config) as store:
                gate = store.get_control("runtime_gate")
        except observer_store.StoreError as error:
            if error.reason != "missing":
                raise
            gate = None
        if not runtime.gate_valid(gate, options):
            return dict(preview, code="runtime-gate")
    refused = confirm() if confirm is not None else None
    if refused:
        return dict(preview, code=refused)
    options["publisher_id"] = runtime.publisher_id(create=True)
    with observer_store.Store(repo, config=dict(config, observer=options), max_bytes=options["max_pending_bytes"],
                              max_pending=options["max_pending_observations"]):
        pass  # the queue, so that status and the hooks find it
    runtime.approve(repo, {"observer": options}, root)
    return dict(preview, settings=options, approved=True)


TERMINAL = ("Run observer.py release HOLD_ID yourself in a separate terminal, outside Claude Code: it asks you to "
            "confirm there. Nothing in a session releases a hold.")


class Terminal:
    """The controlling terminal, read and written directly, never through stdin or stdout."""

    def __init__(self, descriptor):
        self.descriptor = descriptor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        os.close(self.descriptor)

    def ask(self, question):
        os.write(self.descriptor, question.encode("utf-8"))
        answer = b""
        while not answer.endswith(b"\n") and len(answer) < 256:
            chunk = os.read(self.descriptor, 256)
            if not chunk:
                break
            answer += chunk
        return answer.decode("utf-8", "replace").strip()


def terminal():
    """The user's own terminal, opened for the confirmation, or None. A command run by Claude Code's Bash tool has no
    controlling terminal and pipes for stdin and stdout, so a model cannot release a hold."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    try:
        return Terminal(os.open("/dev/tty", os.O_RDWR | getattr(os, "O_NOCTTY", 0)))
    except OSError:
        return None


def entry_holds():
    """The active holds of the repository this folder's entry names: [] without an entry, [None] when the approvals
    or the holds cannot be read, since those holds may be active."""
    try:
        entry = runtime.stored_approval()
    except (ValueError, storage.StorageError, OSError):
        return [None]
    if entry is None:
        return []
    name = entry.get("repo") if isinstance(entry, dict) else None
    if not recortex.valid_repo(name):
        return [None]
    try:
        return [hold for hold in observer_store.read_holds(name) if hold.get("state") == "active"]
    except (observer_store.StoreError, storage.StorageError, OSError, ValueError, KeyError, TypeError):
        return [None]


def confirmed(word, question):
    """None when the user typed `word` on their own terminal; else the code that stops the action. A command run by
    Claude Code's Bash tool has no terminal, so it is refused."""
    tty = terminal()
    if tty is None:
        return "terminal-required"
    with tty:
        return None if tty.ask(question + " Type %s to confirm: " % word) == word else "not-confirmed"


def release(store, hold_id, tty):
    """Release one active hold after the user typed the confirmation word on their terminal."""
    hold = next((item for item in store.holds() if item.get("id") == hold_id and item.get("state") == "active"), None)
    if hold is None:
        return {"code": "hold-unavailable"}
    question = "Release hold %s (%s%s) of %s? Type release to confirm: " % (
        hold_id, hold.get("action"), " " + json.dumps(hold["command"]) if hold.get("command") else "", store.repo)
    if tty.ask(question) != "release":
        return {"code": "not-confirmed", "hold_id": hold_id}
    store.update_holds(lambda holds: observer_holds.release(holds, hold_id))
    return {"action": "release", "hold_id": hold_id, "released": True}


def _alarm(signum, frame):
    raise TimeoutError("deadline")


TOOL_EVENTS = ("pre-tool", "post-tool", "tool-failure")


def hook(event, config=None, repo=None):
    """One hook event. Without `config`, the project file and this machine's settings for this folder are read. A
    failure prints one fixed line, except on tool events, which fire on every call and stay silent."""
    previous = signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, 3.5)
    trusted = unreadable = False
    holds = event == "pre-tool"  # the holds follow this folder's private entry, whatever the project file says
    data = None
    try:
        if config is None:
            try:
                loaded = recortex.load_config()
            except (OSError, ValueError, RecursionError):
                if not holds:
                    raise
                loaded = {}
            repo = repo or loaded.get("repo")
            if not recortex.valid_repo(repo) and not holds:
                return 0
            trusted = recortex.valid_repo(repo) and bool(recortex.allowed_users(repo))
            try:
                config = runtime.local_config(loaded)
            except (ValueError, storage.StorageError):
                unreadable = True  # this machine's approvals exist but cannot be read
                if not holds:
                    raise
                config = {key: value for key, value in loaded.items() if key != "observer"}
        repo = repo or config.get("repo")
        if not recortex.valid_repo(repo) and not holds:
            return 0
        raw = sys.stdin.read(8388609)
        if len(raw) > 8388608:
            raise ValueError("hook_input_limit")
        if len(raw) > 262144 and event != "pre-tool":
            return 0  # a large Write, Edit or tool result; the next hook scans its transcript lines
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("hook_input")
        import observer_hooks
        result = observer_hooks.handle(event, data, config, repo)
        if result:
            emit(result)
        return 0
    except (TimeoutError, OSError, ValueError, observer_store.StoreError, storage.StorageError) as error:
        if holds and isinstance(error, TimeoutError):
            # Out of time before the holds were decided: stopped as when they cannot be read, never let through.
            import observer_hooks
            result = observer_hooks.fail_closed(data)
            if result:
                emit(result)
            return 0
        raw_settings = (config or {}).get("observer", {})
        enabled = unreadable or isinstance(raw_settings, dict) and raw_settings.get("enabled") is True
        if (event not in TOOL_EVENTS and enabled and recortex.valid_repo(repo)
                and (trusted or recortex.allowed_users(repo))):
            message = "recortex observer unavailable: hook deadline or local state failure; run observer.py status."
            emit({"systemMessage": message})
        return 0
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _status(repo, config, loaded, store, options, details):
    variables = runtime.billing_variables()
    last = store.get_control("runtime_last") if store else None
    seen = store.get_control("call_environment", []) if store else []
    called = sorted(set(variables) | {name for name in seen if name in runtime.BILLING})
    out = {"settings": options, "approved": runtime.approval_valid(repo, config), "folder": runtime.folder_key(),
           "usage": runtime.USAGE_NOTE % (options["max_invocations"], options["max_input_chars"]),
           "capture_preflight": observer_sources.preflight(recortex.project_root(), details=details)}
    if called:
        out["billing"] = runtime.BILLING_NOTE % ", ".join(called)
    if runtime.project_off(loaded):
        out["project_switch"] = "off"
    if store is None:
        return dict(out, code="empty", action="status", dry_run=False, queue={})
    return dict(out, runtime_ready=runtime.gate_valid(store.get_control("runtime_gate"), options),
                runtime_last=last, capture_paused=store.get_control("capture_paused", False),
                publication_paused=store.get_control("publication_paused", False),
                worker_degraded=store.get_control("worker_degraded", ""), queue=store.status())


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "hook":
        return hook(args.event, repo=args.repo)
    import index
    import observer_context
    import observer_publisher
    try:
        loaded = recortex.load_config()
        if args.command == "disable":
            # The holds to check are those of the repository this folder's private entry names, never the project
            # file's; an entry that cannot be read, or names no valid repository, may have active holds.
            try:
                entry = runtime.stored_approval()
            except (ValueError, storage.StorageError, OSError):
                entry = {}
            name = entry.get("repo") if isinstance(entry, dict) else None
            try:
                active = [] if entry is None else [h for h in observer_store.read_holds(name)
                                                   if h.get("state") == "active"] if recortex.valid_repo(name) else [None]
            except (observer_store.StoreError, storage.StorageError, OSError, ValueError, KeyError, TypeError):
                active = [None]  # holds that cannot be read may be active
            if active:
                tty = terminal()
                if tty is None:
                    emit({"code": "terminal-required", "note": "Holds apply here. " + TERMINAL.replace(
                        "release HOLD_ID", "disable").replace("releases a hold", "switches the observer off")}); return 1
                with tty:
                    if tty.ask("Holds of %s stop applying in this folder. Type disable to confirm: " % name) != "disable":
                        emit({"code": "not-confirmed"}); return 1
            emit({"action": "disable", "folder": runtime.folder_key(), "was_enabled": runtime.forget()}); return 0
        config = runtime.local_config(loaded)
        repo = recortex.resolve_repo(args.repo, config)
        if not recortex.allowed_users(repo):
            emit({"code": "repository-not-trusted"}); return 1
        options = runtime.settings(config)
        if args.command == "enable":
            # Like disable and release, these change what the user set up, so they are asked on the user's own
            # terminal: automatic mode, and replacing this folder's entry while its repository's holds apply.
            questions = []
            if args.mode == "automatic":
                questions.append("The observer will post reviewed memories to %s on its own." % repo)
            if entry_holds():
                questions.append("This replaces this folder's settings while holds apply.")
            confirm = (lambda: confirmed("enable", " ".join(questions))) if questions else None
            result = enable(repo, loaded, args.mode, args.yes, args.model, confirm=confirm)
            if result.get("code") == "terminal-required":
                result["note"] = ("Run this enable yourself in a separate terminal, outside Claude Code: it asks you to "
                                  "confirm there.")
            emit(result)
            return int(args.yes and not result["approved"])
        if args.command == "context":
            db = index.connect(repo, create=False)
            if db is None:
                emit({"code": "record-unavailable"}); return 1
            try:
                packet = observer_context.build_packet(db, repo, recortex.allowed_users(repo), query=args.query, issue=args.issue)
                print(packet["text"])
            finally:
                db.close()
            return 0
        if args.command == "reconcile":
            emit(observer_publisher.reconcile(repo, args.eventkey, config, dry_run=args.dry_run)); return 0
        tty = None
        if args.command == "release":
            tty = terminal()
            if tty is None:
                emit({"code": "terminal-required", "note": TERMINAL}); return 1
        if args.command == "runtime-check" and not runtime.approval_valid(repo, config):
            emit({"code": "approval", "note": "Run observer.py enable --model MODEL --yes first; the check is bound "
                                              "to the model chosen there."}); return 1
        readonly = args.command in ("status", "pending", "holds") or getattr(args, "dry_run", False)
        try:
            opened = observer_store.Store(repo, create=not readonly, readonly=readonly,
                         max_bytes=options["max_pending_bytes"], max_pending=options["max_pending_observations"], config=config)
        except observer_store.StoreError as error:
            if readonly and error.reason == "missing":
                if args.command == "status":
                    emit(_status(repo, config, loaded, None, options, args.source_details)); return 0
                emit({"code": "empty", "action": args.command, "dry_run": getattr(args, "dry_run", False), "queue": {}, "capture_preflight": observer_sources.preflight(recortex.project_root(), details=getattr(args, "source_details", False))}); return 0
            raise
        with opened as store:
            if args.command == "status":
                emit(_status(repo, config, loaded, store, options, args.source_details)); return 0
            if args.command == "pending":
                if args.show_body:
                    if not args.eventkey:
                        emit({"code": "eventkey-required"}); return 1
                    event = store.event(args.eventkey)
                    if event is None:
                        emit({"code": "reviewed-event-unavailable"}); return 1
                    if event["state"] == "verified":
                        observer_publisher._payload_bound(event)
                    else:
                        observer_publisher._bound(event, store)
                    print(event["body"], end=""); return 0
                if args.eventkey:
                    event = store.event(args.eventkey)
                    emit({key: event[key] for key in ("eventkey", "state", "reason")} if event else {"code": "event-unavailable"}); return int(event is None)
                emit({"sources": [{key: row[key] for key in ("source_id", "state", "reason", "created", "updated")}
                                  for row in store.pending(limit=100)],
                      "events": [{key: row[key] for key in ("eventkey", "state", "reason")} for row in store.events(limit=100)]}); return 0
            if args.command == "release":
                with tty:
                    result = release(store, args.hold_id, tty)
                emit(result); return int(not result.get("released"))
            if args.command == "discard":
                event = store.event(args.eventkey)
                if event is None or event["state"] not in ("ready", "deferred"):
                    emit({"code": "event-not-discardable"}); return 1
                store.transition(args.eventkey, "rejected", reason="discarded")
                emit({"action": "discard", "eventkey": args.eventkey}); return 0
            if args.command == "holds":
                emit({"holds": store.holds()}); return 0
            if args.command in ("pause", "resume"):
                scopes = ("capture", "publication") if args.scope == "all" else (args.scope,)
                if args.command == "resume" and any(store.get_control(scope + "_paused", False) for scope in scopes):
                    # A pause is the user's control; undoing it is asked on the user's own terminal, like disable.
                    refused = confirmed("resume", "Resume %s for %s?" % (" and ".join(scopes), repo))
                    if refused:
                        out = {"code": refused}
                        if refused == "terminal-required":
                            out["note"] = ("Run observer.py resume yourself in a separate terminal, outside Claude Code: "
                                           "it asks you to confirm there.")
                        emit(out); return 1
                for scope in scopes:
                    store.set_control(scope + "_paused", args.command == "pause")
                emit({"action": args.command, "scope": args.scope}); return 0
            if args.command == "runtime-check":
                reservation = store.reserve_invocation("runtime_check", options["max_invocations"], options["window_hours"] * 3600)
                if reservation is None:
                    emit({"code": "budget"}); return 1
                proof = runtime.check_runtime(options)
                ok = runtime.gate_valid(proof, options)
                store.complete_invocation(reservation, ok, runtime.candidate_hash(proof))
                store.set_control("runtime_gate", proof if ok else None)
                safe = {key: proof.get(key) for key in ("ok", "code", "version", "elapsed", "usage", "isolation_checked", "canary_absent", "structured_exact", "requested_model", "actual_model", "api_key_source", "billing_variables")}
                store.set_control("runtime_last", safe); emit(safe); return int(not ok)
            if args.command in ("run-worker", "record") and args.dry_run:
                emit({"action": args.command, "dry_run": True, "queue": store.status(),
                      "note": "No model call, queue transition or publication is performed."}); return 0
            if args.command == "record":
                item = store.observation(args.source)
                if item is None or item["state"] not in ("observed", "deferred"):
                    emit({"code": "source-not-retryable"}); return 1
                if not runtime.approval_valid(repo, config):
                    emit({"code": "approval"}); return 1
                with store.transaction():
                    store.db.execute("UPDATE observations SET state='observed',reason='',updated=? WHERE source_id=? AND state IN ('observed','deferred')", (time.time(), args.source))
        if args.command in ("run-worker", "record"):
            import observer_worker
            previous = signal.signal(signal.SIGALRM, _alarm)
            signal.setitimer(signal.ITIMER_REAL, 250)
            try:
                emit(observer_worker.run_once(repo, config))
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0); signal.signal(signal.SIGALRM, previous)
            return 0
    except (OSError, ValueError, TimeoutError, storage.StorageError, observer_store.StoreError, observer_publisher.PublishError) as error:
        out = {"code": "observer-unavailable", "note": "Configuration, private state or runtime could not be validated."}
        reason = str(error) if isinstance(error, ValueError) else ""
        if reason in REASONS:
            out.update(reason=reason, note=NOTES.get(reason, "The observer settings did not validate; nothing was "
                                                                "recorded."))
        emit(out)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
