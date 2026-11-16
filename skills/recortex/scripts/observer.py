#!/usr/bin/env python3
"""Explicit observer controls and bounded hook entry point. No automatic opt-in."""
import argparse
import json
import os
import signal
import stat
import sys
import tempfile
import time

import index
import names
import observer_context
import observer_runtime as runtime
import observer_store
import observer_sources
import observer_publisher
import recortex
import storage


def emit(value):
    print(json.dumps(value, sort_keys=True, ensure_ascii=True))


def parser():
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
    enable = sub.add_parser("enable", help="preview exact local opt-in; --yes approves and saves it")
    enable.add_argument("--mode", choices=("shadow", "automatic"), default="shadow")
    enable.add_argument("--model", help="explicit Claude model alias or ID; an empty value selects the isolated CLI default")
    enable.add_argument("--yes", action="store_true")
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
    context = sub.add_parser("context")
    context.add_argument("--query", default="")
    context.add_argument("--issue", type=int)
    hook = sub.add_parser("hook")
    hook.add_argument("event")
    return root


def _save_config(config, options):
    """Replace the explicitly selected existing project config; never follow its symlink."""
    path = config.get("_path")
    if not path:
        raise ValueError("configuration_required")
    before = os.lstat(path)
    if (not stat.S_ISREG(before.st_mode) or before.st_mode & 0o022
            or hasattr(os, "getuid") and before.st_uid != os.getuid()):
        raise ValueError("configuration_permissions")
    updated = {key: value for key, value in config.items() if key != "_path"}
    updated["observer"] = options
    fd, temporary = tempfile.mkstemp(prefix=".recortex-observer-", dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(updated, handle, indent=2, ensure_ascii=False)
            handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        after = os.lstat(path)
        if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size):
            raise ValueError("configuration_changed")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def enable(repo, config, mode, yes=False, model=None):
    options = runtime.settings(config)
    options.update(enabled=True, mode=mode)
    if model is not None:
        options["model"] = model
        options = runtime.settings({"observer": options})
    try:
        identity = runtime.publisher_id(create=False)
    except (storage.StorageError, OSError):
        identity = None
    options["publisher_id"] = identity or "allocated_on_explicit_approval"
    preview = {"action": "enable", "repo": repo, "settings": options, "approved": False,
               "model_selection": {"source": "explicit" if options["model"] else "isolated_cli_default",
                                   "requested_model": options["model"]},
               "note": "An isolated runtime check must succeed before semantic capture or automatic publication."}
    if not options["model"]:
        preview["model_selection"]["note"] = "User and project Claude model settings are not loaded; the isolated CLI chooses its default."
    if not yes:
        return preview
    options["publisher_id"] = runtime.publisher_id(create=True)
    proposed = dict(config, observer=options)
    with observer_store.Store(repo, config=proposed, max_bytes=options["max_pending_bytes"],
                              max_pending=options["max_pending_observations"]) as store:
        if mode == "automatic" and not runtime.gate_valid(store.get_control("runtime_gate"), options):
            return dict(preview, code="runtime-gate")
        _save_config(config, options)
        store.set_control("observer_approval", {"repo": repo.lower(), "settings": options,
                          "publisher_id": options["publisher_id"], "approved_at": time.time()})
    return dict(preview, settings=options, approved=True)


def _alarm(signum, frame):
    raise TimeoutError("deadline")


def hook(event, config=None, repo=None):
    previous = signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, 3.5)
    try:
        config = recortex.load_config() if config is None else config
        repo = repo or config.get("repo")
        if not recortex.valid_repo(repo):
            return 0
        raw = sys.stdin.read(262145)
        if len(raw) > 262144:
            raise ValueError("hook_input_limit")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("hook_input")
        import observer_hooks
        result = observer_hooks.handle(event, data, config, repo)
        if result:
            emit(result)
        return 0
    except (TimeoutError, OSError, ValueError, observer_store.StoreError, storage.StorageError):
        raw_settings = (config or {}).get("observer", {})
        if isinstance(raw_settings, dict) and raw_settings.get("enabled") is True and recortex.valid_repo(repo) and recortex.allowed_users(repo):
            message = "recortex observer unavailable: hook deadline or local state failure; run observer.py status."
            emit({"systemMessage": message})
        return 0
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "hook":
            return hook(args.event, repo=args.repo)
        config = recortex.load_config()
        repo = recortex.resolve_repo(args.repo, config)
        if not recortex.allowed_users(repo):
            emit({"code": "repository-not-trusted"}); return 1
        options = runtime.settings(config)
        if args.command == "enable":
            result = enable(repo, config, args.mode, args.yes, args.model); emit(result)
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
        readonly = args.command in ("status", "pending", "holds") or getattr(args, "dry_run", False)
        try:
            opened = observer_store.Store(repo, create=not readonly, readonly=readonly,
                         max_bytes=options["max_pending_bytes"], max_pending=options["max_pending_observations"], config=config)
        except observer_store.StoreError as error:
            if readonly and error.reason == "missing":
                emit({"code": "empty", "action": args.command, "dry_run": getattr(args, "dry_run", False), "queue": {}, "capture_preflight": observer_sources.preflight(recortex.project_root(), details=getattr(args, "source_details", False))}); return 0
            raise
        with opened as store:
            if args.command == "status":
                emit({"settings": options, "approved": runtime.approval_valid(repo, config, store.get_control("observer_approval")),
                      "runtime_ready": runtime.gate_valid(store.get_control("runtime_gate"), options),
                      "capture_preflight": observer_sources.preflight(recortex.project_root(), details=args.source_details),
                      "runtime_last": store.get_control("runtime_last"), "capture_paused": store.get_control("capture_paused", False),
                      "publication_paused": store.get_control("publication_paused", False), "worker_degraded": store.get_control("worker_degraded", ""),
                      "queue": store.status()}); return 0
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
            if args.command == "holds":
                emit({"holds": store.holds()}); return 0
            if args.command in ("pause", "resume"):
                scopes = ("capture", "publication") if args.scope == "all" else (args.scope,)
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
                safe = {key: proof.get(key) for key in ("ok", "code", "version", "elapsed", "usage", "isolation_checked", "canary_absent", "structured_exact", "requested_model", "actual_model")}
                store.set_control("runtime_last", safe); emit(safe); return int(not ok)
            if args.command in ("run-worker", "record") and args.dry_run:
                emit({"action": args.command, "dry_run": True, "queue": store.status(),
                      "note": "No model call, queue transition or publication is performed."}); return 0
            if args.command == "record":
                item = store.observation(args.source)
                if item is None or item["state"] not in ("observed", "deferred"):
                    emit({"code": "source-not-retryable"}); return 1
                if not runtime.approval_valid(repo, config, store.get_control("observer_approval")):
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
    except (OSError, ValueError, TimeoutError, storage.StorageError, observer_store.StoreError, observer_publisher.PublishError):
        emit({"code": "observer-unavailable", "note": "Configuration, private state or runtime could not be validated."})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
