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
    # How a session's own call to each subcommand is kept in check (observer_hooks.COMMANDS holds the same table, and a test
    # holds the two together): safe, grant (the user's recorded answer to that item's question, or the user's terminal), or
    # terminal (the user's own command; the hook refuses a session's own call).
    # status: safe, it only reads.
    status = sub.add_parser("status")
    status.add_argument("--source-details", action="store_true",
                        help="show native directory paths, modes and ownership for capture diagnosis")
    # holds: safe, it only reads. runtime-check: safe, at most one small call per model, counted in the day's caps.
    for name in ("holds", "runtime-check"):
        sub.add_parser(name)
    # pending: safe, it only reads (--show-body prints a reviewed comment; --waiting lists what waits).
    pending = sub.add_parser("pending")
    pending.add_argument("--eventkey")
    pending.add_argument("--show-body", action="store_true", help="explicitly display the reviewed comment for this event key")
    pending.add_argument("--held", action="store_true",
                         help="list the reviewed memories that wait for the user's answer: post each, or drop it")
    pending.add_argument("--waiting", action="store_true",
                         help="everything that waits for the user's answer: held memories, tasks to open or close, "
                              "drafts of the paper's text, each with its question")
    pending.add_argument("--json", action="store_true", help="with --waiting: JSON instead of text")
    # enable: grant. Turning the observer on, raising its level, starting posting or adding the stronger model needs the
    # user's recorded answer to the question about it (observer_grants: the hook records a one-time grant for exactly the
    # level, or the stronger model, the user chose) or the user's terminal; lowering anything needs neither. It cannot be
    # run by a project file, and the user sees and removes it with status and disable.
    enable = sub.add_parser("enable", help="preview this machine's settings for this folder; --yes approves and saves them")
    enable.add_argument("--mode", choices=("shadow", "automatic"), default="shadow")
    enable.add_argument("--model", help="required: the Claude model alias or ID for the middle tier (drafts, reviews, summaries)")
    enable.add_argument("--level", choices=runtime.LEVELS,
                        help="what the observer may do on its own: notes, assistant or everything (required unless this "
                             "folder's approval already holds one)")
    enable.add_argument("--sort-model", help="the model that sorts each stretch of the conversation (default: the middle model)")
    enable.add_argument("--strong-model", help="the model that drafts the paper's text for the user to accept (default: none, "
                                               "the manuscript keeper stays off)")
    enable.add_argument("--yes", action="store_true")
    enable.add_argument("--pass", dest="enable_pass", help=argparse.SUPPRESS)   # the word of setup.py's chain (observer_grants)
    # limits: safe, it only lowers the day's caps (a higher number is refused): a session can make the observer do less, never more.
    limits = sub.add_parser("limits", help="lower this folder's daily call caps (a new approval of the same settings); "
                                           "a higher number is refused")
    for tier in runtime.TIERS:
        limits.add_argument("--" + tier, type=int, metavar="N", help="calls a day on the %s tier" % tier)
    # disable: terminal, it removes the folder's approval and ends its holds, so it is the user's own command.
    sub.add_parser("disable", help="remove this machine's settings and approval for this folder")
    # pause: safe, it only stops capture or publication; resume: terminal, it ends a pause the user made.
    for name in ("pause", "resume"):
        command = sub.add_parser(name)
        command.add_argument("scope", choices=("capture", "publication", "all"), default="all", nargs="?")
    # run-worker, record, reconcile: safe, the worker's own steps (the hooks start it), under its lock, the caps and the
    # level; reconcile only reads GitHub to settle an event that was being sent.
    for name in ("run-worker", "record", "reconcile"):
        command = sub.add_parser(name)
        command.add_argument("--dry-run", action="store_true")
        if name == "record":
            command.add_argument("--source", required=True, help="retry a known deferred source through independent review")
        if name == "reconcile":
            command.add_argument("eventkey")
    # release: terminal, it ends a hold the user made.
    release = sub.add_parser("release", help="release a hold; run it yourself in a separate terminal, which asks you "
                                             "to confirm there")
    release.add_argument("hold_id")
    # act, later: grant, the user's recorded "Open it" or "Close it" (act) or "Not now" (later) to that item's question.
    for name, text in (("act", "carry out the user's yes to a task that waits: open it, or close the finished one"),
                       ("later", "put a task that waits off for a day (the user's Not now)")):
        command = sub.add_parser(name, help=text)
        command.add_argument("short", metavar="SHORT", help="the item's short id, as pending --waiting lists it")
    # log: safe, it only reads.
    log = sub.add_parser("log", help="what the observer did on its own, with the address of each act")
    log.add_argument("--days", type=int, default=7, help="how many days back (1 to 35, default 7)")
    # undo: terminal, it changes GitHub (reopens a task, restores a body, closes a task).
    undo = sub.add_parser("undo", help="reverse an act where it can be reversed (see observer.py log for the numbers); "
                                       "run it yourself in a separate terminal, which asks you to confirm there")
    undo.add_argument("act", type=int, metavar="ACT")
    # discard: grant, the user's recorded "Drop" or "Drop it" to that item's question (or the user's terminal).
    discard = sub.add_parser("discard", help="drop a reviewed event that will not be posted; its excerpts then expire")
    discard.add_argument("eventkey")
    # drafts: grant, apply needs the user's recorded "Approve", later and drop their "Not now" and "Drop it" (listing and
    # --show only read).
    drafts = sub.add_parser("drafts", help="the drafts of the paper's text that wait for the user's answer: list them, "
                                          "--show ID what would change, or apply, later, drop ID")
    drafts.add_argument("action", nargs="?", choices=("apply", "later", "drop"))
    drafts.add_argument("draft", nargs="?", metavar="ID")
    drafts.add_argument("--show", metavar="ID")
    # context: safe, it only reads. hook: safe, the hook's own entry point (no model or network call).
    context = sub.add_parser("context")
    context.add_argument("--query", default="")
    context.add_argument("--issue", type=int)
    hook = sub.add_parser("hook")
    hook.add_argument("event")
    return root


REASONS = frozenset(("model_required", "level_required", "folder_name", "observer_settings", "observer_mode",
                     "observer_publisher", "observer_model", "observer_level", "observer_limit", "observer_approvals"))
NOTES = {
    "model_required": "Choose the model for the background calls with --model (a Claude alias or model ID).",
    "level_required": "Choose what the observer may do with --level notes, assistant or everything.",
    "folder_name": "The project folder's path holds a quote, a backslash or a control character; the observer cannot "
                   "be enabled there.",
    "observer_approvals": "This machine's private observer approvals file is too large, not JSON or not of the "
                          "expected form; it is not replaced on its own. Move it aside, then enable again.",
}


def _stored_settings(repo, root=None):
    """This folder's stored settings for `repo` as a dict, or {} (no entry, another repository, or one that cannot be
    read). Read, never validated here."""
    try:
        entry = runtime.stored_approval(root)
    except (ValueError, storage.StorageError, OSError):
        return {}
    if not isinstance(entry, dict) or entry.get("repo") != repo.lower() or not isinstance(entry.get("settings"), dict):
        return {}
    return entry["settings"]


def _tier_gates_valid(repo, config, options):
    """True when every tier that has a model holds a valid proof for it, read from this folder's queue."""
    try:
        with observer_store.Store(repo, create=False, readonly=True, config=config) as store:
            state = runtime.tier_state(options, store)
    except observer_store.StoreError as error:
        if error.reason != "missing":
            raise
        state = runtime.tier_state(options, None)
    return all(item["gate"] for item in state.values() if item["configured"])


def enable(repo, config, mode, yes=False, model=None, root=None, confirm=None, given="terminal", level=None,
           sort_model=None, strong_model=None):
    """Preview, or with yes=True record, this machine's settings for this project folder. Nothing is written to the
    project's shared configuration file, and nothing is created before every check has passed. `confirm`, when given,
    is asked last, before anything is written; a code it returns stops the write. The level is the user's choice (one of
    runtime.LEVELS); this folder's earlier choice stands when none is given, and without either nothing is enabled. The
    sort model defaults to the middle model and the strong model to none (the manuscript keeper stays off). Caps the
    user lowered with `limits` stay lowered when the same repository is enabled again."""
    root = runtime.folder_key(root)
    problem = runtime.folder_problem(root)
    if problem:
        raise ValueError(problem)
    if not model:
        raise ValueError("model_required")
    stored = _stored_settings(repo, root)
    level = level or (stored.get("level") if stored.get("level") in runtime.LEVELS else "")
    if level not in runtime.LEVELS:
        raise ValueError("level_required")
    raw = {"enabled": True, "mode": mode, "model": model, "level": level}
    if sort_model:
        raw["sort_model"] = sort_model
    if strong_model:
        raw["strong_model"] = strong_model
    caps = set(runtime.CALL_CAPS.values()) | set(runtime.TOKEN_BUDGETS.values())
    if stored.get("level") in runtime.LEVELS:   # an entry of 1.4.0 holds that version's defaults, never the user's choice
        raw.update({key: stored[key] for key in caps if key in stored})
    options = runtime.settings({"observer": raw})
    try:
        identity = runtime.publisher_id(create=False)
    except (storage.StorageError, OSError):
        identity = None
    options["publisher_id"] = identity or "allocated_on_explicit_approval"
    preview = {"action": "enable", "repo": repo, "folder": root, "settings": options, "approved": False,
               "level": level, "level_label": runtime.LEVEL_LABELS[level], "usage": runtime.usage_note(options),
               "note": "Settings stay on this machine for this folder; the project file can only switch the observer "
                       "off. An isolated runtime check must succeed for each model before independent checks or "
                       "automatic posting."}
    billing = runtime.billing_variables()
    if billing:
        preview["billing"] = runtime.BILLING_NOTE % ", ".join(billing)
    if runtime.project_off(config):
        preview["project_switch"] = "off"
    if not yes:
        return preview
    if runtime.project_off(config):
        return dict(preview, code="project-off")
    if mode == "automatic" and not _tier_gates_valid(repo, config, options):
        return dict(preview, code="runtime-gate")
    refused = confirm() if confirm is not None else None
    if refused:
        return dict(preview, code=refused)
    options["publisher_id"] = runtime.publisher_id(create=True)
    with observer_store.Store(repo, config=dict(config, observer=options), max_bytes=options["max_pending_bytes"],
                              max_pending=options["max_pending_observations"]):
        pass  # the queue, so that status and the hooks find it
    runtime.approve(repo, {"observer": options}, root, given=given)
    return dict(preview, settings=options, approved=True)


def limits(repo, config, wanted, given="terminal"):
    """Lower this folder's daily call caps: a new approval of the same settings with the lower numbers. `wanted` maps a
    tier to a number. A number above the cap in force is refused (code "limit-higher"), and a number below 1 is
    refused too; nothing else changes, and the approval keeps its own time and where it was given."""
    if not wanted:
        return {"code": "nothing-to-change", "caps": {tier: runtime.settings(config)[key] for tier, key in
                                                       runtime.CALL_CAPS.items()}}
    options = runtime.settings(config)
    if not runtime.approval_valid(repo, config):
        return {"code": "approval", "note": "Run observer.py enable first; the limits change an approved folder."}
    changed = dict(options)
    for tier, number in wanted.items():
        key = runtime.CALL_CAPS[tier]
        if type(number) is not int or number < 1:
            return {"code": "limit-invalid", "tier": tier}
        if number > options[key]:
            return {"code": "limit-higher", "tier": tier, "cap": options[key],
                    "note": "A cap can be lowered here and never raised."}
        changed[key] = number
    entry = runtime.stored_approval() or {}
    kept = entry.get("given") if entry.get("given") in ("terminal", "session") else given
    runtime.approve(repo, {"observer": changed}, given=kept, approved_at=entry.get("approved_at"))
    return {"action": "limits", "caps": {tier: changed[key] for tier, key in runtime.CALL_CAPS.items()}, "approved": True}


HELD_NOTE = ("Ask the user about each with the multiple-choice question tool, one question per memory, from the "
             "template: Memory SHORT (KIND) on #ISSUE: \"LINE\". It would change: CHANGES. Post it, or drop it? Give "
             "the short id and the line exactly as listed here, and the options Post and Drop. For each Post, run "
             "post_verified.py comment --issue N --kind KIND --body-file <its body, from pending --eventkey KEY "
             "--show-body> --eventkey KEY right after; for each Drop, observer.py discard KEY.")
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


def on_terminal():
    """True when this command runs on the user's own terminal: a controlling terminal and a tty for stdin and stdout, which a
    command run by Claude Code's Bash tool does not have."""
    tty = terminal()
    if tty is None:
        return False
    with tty:
        return True


def declined(store, ident, answer):
    """None when the user may decline item `ident` ("later" or "no") now: they run the command on their own terminal, or
    their recorded answer to that item's question left the one-time grant, which is consumed here. Else the refusal that
    is emitted. A session that was not answered, or answered another question, cannot postpone or drop what waits."""
    import observer_waiting
    item = observer_waiting.find_any(store, ident)
    if item is None:
        return {"code": "not_waiting"}
    if answer not in observer_waiting.ANSWERS[item["kind"]]:
        return {"code": "other_kind", "kind": item["kind"]}
    if on_terminal() or store.take_grant(observer_waiting.answer_key(item, answer)):
        return None
    return {"code": "answer_unanswered", "note": observer_waiting.UNANSWERED}


def entry_holds(keep=None):
    """The active holds that replacing this folder's entry would stop applying here: those of the repository the
    entry names, [] without an entry or when it names `keep` (the replacement names it too, so they still apply), and
    [None] when the approvals or the holds cannot be read, since those holds may be active."""
    try:
        entry = runtime.stored_approval()
    except (ValueError, storage.StorageError, OSError):
        return [None]
    if entry is None:
        return []
    name = entry.get("repo") if isinstance(entry, dict) else None
    if not recortex.valid_repo(name):
        return [None]
    if keep and name.lower() == keep.lower():
        return []
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


TOOL_EVENTS = ("pre-tool",)


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


def tiers_report(options, store):
    """Per tier: its model, whether it holds a valid proof for it, the day's calls and tokens, and the caps."""
    state = runtime.tier_state(options, store)
    today = store.usage_today() if store is not None and store.format == observer_store.FORMAT else {}
    out = {}
    for tier in runtime.TIERS:
        used = today.get(tier, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        out[tier] = dict(state[tier], calls_24h=used["calls"], tokens_24h=used["input_tokens"] + used["output_tokens"],
                         call_cap=options[runtime.CALL_CAPS[tier]], token_budget=options[runtime.TOKEN_BUDGETS[tier]])
    return out


def _status(repo, config, loaded, store, options, details):
    variables = runtime.billing_variables()
    last = store.get_control("runtime_last") if store else None
    seen = store.get_control("call_environment", []) if store else []
    called = sorted(set(variables) | {name for name in seen if name in runtime.BILLING})
    out = {"settings": options, "approved": runtime.approval_valid(repo, config), "folder": runtime.folder_key(),
           "level": options["level"], "level_label": runtime.LEVEL_LABELS.get(options["level"], "not chosen"),
           "paused": runtime.paused(repo, loaded), "usage": runtime.usage_note(options),
           "capture_preflight": observer_sources.preflight(recortex.project_root(), details=details)}
    if out["paused"]:
        out["note"] = runtime.PAUSED + " Answer the level question (setup.py questions), or run observer.py enable --level."
    if called:
        out["billing"] = runtime.BILLING_NOTE % ", ".join(called)
    if runtime.project_off(loaded):
        out["project_switch"] = "off"
    if store is None:
        return dict(out, code="empty", action="status", dry_run=False, queue={}, tiers=tiers_report(options, None))
    return dict(out, runtime_ready=runtime.tier_state(options, store)["middle"]["gate"],
                tiers=tiers_report(options, store),
                runtime_last=last, capture_paused=store.get_control("capture_paused", False),
                publication_paused=store.get_control("publication_paused", False),
                worker_degraded=store.get_control("worker_degraded", ""), queue=store.status(),
                worker_last=store.get_control("worker_last"), dialogs_unread=observer_sources.unread(store))


def runtime_check(repo, options, store):
    """One smoke call per distinct model of the tiers that have one, each on a reservation of its own tier. A proof is
    kept for every tier of its model; a failed check clears only those tiers' proofs, so the other tiers go on.
    Returns (ok, {tier: safe summary})."""
    done, out, ok = {}, {}, True
    for tier in ("middle", "sort", "strong"):
        model = runtime.model_for(options, tier)
        if tier == "strong" and not model:
            continue   # no strong model: the keeper is off, and there is nothing to prove
        if model not in done:
            reservation = store.reserve_invocation("runtime_check", options[runtime.CALL_CAPS[tier]],
                                                   options["window_hours"] * 3600, tier=tier,
                                                   tokens=options[runtime.TOKEN_BUDGETS[tier]])
            if reservation is None:
                done[model] = None
                out[tier] = {"code": "budget"}
                ok = False
                continue
            proof = runtime.check_runtime(options, tier)
            valid = runtime.gate_valid(proof, options, tier)
            store.complete_invocation(reservation, valid, runtime.candidate_hash(proof), usage=proof.get("usage"),
                                      model=proof.get("actual_model"))
            done[model] = (proof, valid)
        elif done[model] is None:
            out[tier] = {"code": "budget"}
            ok = False
            continue
        proof, valid = done[model]
        store.set_control(runtime.gate_control(tier, options), proof if valid else None)
        out[tier] = {key: proof.get(key) for key in ("ok", "code", "version", "elapsed", "usage", "isolation_checked",
                                                      "canary_absent", "structured_exact", "requested_model",
                                                      "actual_model", "api_key_source", "billing_variables")}
        out[tier]["model"] = model
        ok = ok and valid
    return ok, out


def _acts(args, store, repo, config, options):
    """act, later, log and undo: what the observer waits to be told, and what it did. The "yes" of act and the writes of
    undo need an approved folder in automatic mode; the grant an act needs is the user's recorded answer (the hook
    records it, observer_hooks)."""
    import observer_jobs
    import observer_waiting
    if args.command == "log":
        for text in observer_jobs.log_lines(store, repo, config, max(1, min(35, args.days))) or ["No act in this period."]:
            print(text)
        return 0
    if args.command == "later":
        result = observer_waiting.later(store, args.short, time.time(), answered=on_terminal())
        emit(result)
        return int(result["code"] != "later")
    if args.command == "undo":
        # Terminal-only, as release is: it changes GitHub (reopens a task, restores a body, closes a task), so it is the
        # user's own command, and the hook refuses a session's own Bash call to it.
        refused = confirmed("undo", "Undo act %d of %s?" % (args.act, repo))
        if refused:
            emit({"code": refused, "note": ("Run observer.py undo yourself in a separate terminal, outside Claude Code: "
                                            "it asks you to confirm there.") if refused == "terminal-required" else ""})
            return 1
        result = observer_jobs.undo(store, repo, config, args.act)
        emit(result)
        return int(result["code"] not in ("restored", "reopened", "closed_not_planned"))
    if not runtime.approval_valid(repo, config) or options["mode"] != "automatic":
        emit({"code": "approval", "note": "Only an approved folder in automatic mode acts."}); return 1
    import tasks
    close = lambda issue: observer_jobs.quiet(tasks.close_task, repo, config, issue)[0]   # its words are not the JSON
    result = observer_waiting.act(store, repo, config, args.short, close)
    emit(result)
    return int(result["code"] not in ("opened", "closed"))


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
            # Automatic mode is the user's answer to the setup question, which the setup wizard's session records
            # here, as it records the field sheet's approval: no terminal step. Replacing this folder's entry with one
            # for another repository, so that the holds of the one it names stop applying here, is asked on the
            # user's own terminal, as disable and release are.
            confirm = None
            if entry_holds(keep=repo):
                confirm = lambda: confirmed("enable", "This replaces this folder's settings while holds apply.")
            # Where the approval is given: the user's own terminal, or a session (Claude Code's Bash tool has none).
            given = "terminal" if sys.stdin.isatty() and sys.stdout.isatty() else "session"
            wanted, folder = [], runtime.folder_key()
            if args.yes and not on_terminal():
                import observer_grants
                wanted = observer_grants.needs(_stored_settings(repo), args.level, args.strong_model, args.mode)
            if wanted:   # asked last, with the hold check, once every setting has been checked: the answer is for this level
                holds_confirm = confirm
                confirm = lambda: ("enable_unanswered" if not (observer_grants.in_pass(folder, wanted, args.enable_pass)
                                                              or observer_grants.held(folder, wanted))
                                   else holds_confirm() if holds_confirm else None)
            result = enable(repo, loaded, args.mode, args.yes, args.model, confirm=confirm, given=given, level=args.level,
                            sort_model=args.sort_model, strong_model=args.strong_model)
            if wanted and result.get("approved") and not observer_grants.in_pass(folder, wanted, args.enable_pass):
                observer_grants.take(folder, wanted)   # the user's answer is used once
            if result.get("code") == "enable_unanswered":
                result["note"] = observer_grants.UNANSWERED
            if result.get("code") == "terminal-required":
                result["note"] = ("Run this enable yourself in a separate terminal, outside Claude Code: it asks you to "
                                  "confirm there.")
            emit(result)
            return int(args.yes and not result["approved"])
        if args.command == "limits":
            given = "terminal" if sys.stdin.isatty() and sys.stdout.isatty() else "session"
            result = limits(repo, config, {tier: getattr(args, tier) for tier in runtime.TIERS
                                           if getattr(args, tier) is not None}, given=given)
            emit(result)
            return int("approved" not in result)
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
            emit({"code": "approval", "note": "Run observer.py enable --model MODEL --level LEVEL --yes first; the check is "
                                              "bound to the models chosen there."}); return 1
        readonly = (args.command in ("status", "pending", "holds", "log") or getattr(args, "dry_run", False)
                    or (args.command == "drafts" and args.action is None and not args.show))
        try:
            opened = observer_store.Store(repo, create=not readonly, readonly=readonly,
                         max_bytes=options["max_pending_bytes"], max_pending=options["max_pending_observations"], config=config)
        except observer_store.StoreError as error:
            if readonly and error.reason == "missing":
                if args.command == "pending" and args.waiting:
                    import index
                    import observer_waiting
                    return observer_waiting.print_listing(None, args.json, emit, index.clean, index.DATA_HEADER)
                if args.command == "status":
                    emit(_status(repo, config, loaded, None, options, args.source_details)); return 0
                emit({"code": "empty", "action": args.command, "dry_run": getattr(args, "dry_run", False), "queue": {}, "capture_preflight": observer_sources.preflight(recortex.project_root(), details=getattr(args, "source_details", False))}); return 0
            raise
        with opened as store:
            if args.command == "status":
                emit(_status(repo, config, loaded, store, options, args.source_details)); return 0
            if args.command == "pending":
                if args.waiting:
                    import index
                    import observer_waiting
                    return observer_waiting.print_listing(store, args.json, emit, index.clean, index.DATA_HEADER)
                if args.held:
                    import record_format
                    emit({"held": [{"eventkey": event["eventkey"], "short": event["eventkey"][:8],
                                    "kind": event["candidate"]["kind"],
                                    "issue": event["candidate"]["issue"], "reason": event["reason"],
                                    "line": observer_store.description(event["candidate"]["kind"], event["body"]),
                                    "changes": {key: value for key, value in (record_format.parse_marker(
                                        record_format.first_lines(event["body"])[0]) or {}).items()
                                                if key in ("replaces", "instead", "checks")}}
                                   for event in store.waiting_for_answer()],
                          "note": HELD_NOTE}); return 0
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
                # Dropping changes what waits for the user: on their own terminal, or after their recorded "Drop" (a held
                # memory) or "Drop it" (a task to open) to that item's question. A session cannot drop a memory on its own.
                if not on_terminal():
                    import observer_waiting
                    item = next((row for row in store.waiting() if row["key"] == args.eventkey), None)
                    if item is None:
                        emit({"code": "answer_unanswered", "note": observer_waiting.UNANSWERED}); return 1
                    refused = declined(store, args.eventkey, "no")
                    if refused:
                        emit(refused); return 1
                # Dropping an event posts nothing; one that was held stays marked so, and its copies stay refused.
                store.transition(args.eventkey, "rejected", reason="discarded",
                                 held=observer_publisher.kept(store, event, config))
                if observer_publisher.opens_task(event["candidate"]):
                    # The task will not be opened: the marks that waited for it have nothing to mark.
                    observer_publisher.release_dependents(store, event, False)
                emit({"action": "discard", "eventkey": args.eventkey}); return 0
            if args.command == "holds":
                emit({"holds": store.holds()}); return 0
            if args.command in ("act", "later", "log", "undo"):
                return _acts(args, store, repo, config, options)
            if args.command == "drafts":
                import observer_drafts
                if args.show or args.action is None:
                    return observer_drafts.show(store, args.show, repo, recortex.project_root()) if args.show \
                        else observer_drafts.listing(store)
                if args.draft is None:
                    emit({"code": "draft_required"}); return 1
                if args.action == "apply":
                    return observer_drafts.apply(store, args.draft, repo, recortex.project_root())
                refused = declined(store, args.draft, "later" if args.action == "later" else "no")
                if refused and not (refused["code"] == "not_waiting"):   # an unknown draft says so itself, below
                    emit(refused); return 1
                return {"later": observer_drafts.later, "drop": observer_drafts.drop}[args.action](store, args.draft)
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
                ok, tiers = runtime_check(repo, options, store)
                middle = tiers.get("middle", {})
                safe = dict(middle, tiers=tiers)
                if middle.get("code") != "budget":
                    store.set_control("runtime_last", safe)
                emit(safe); return int(not ok)
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
            clock = observer_worker.Clock(observer_worker.lifetime(options), armed=True)   # sets the alarm
            try:
                emit(observer_worker.run_once(repo, config, settle_first=args.command == "run-worker", clock=clock))
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
