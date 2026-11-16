"""Finite selection and fresh review. Unreviewed source/draft text stays in memory."""
import hashlib
import json
import os
import re
import time

import field_sheet
import index
import mind_model
import observer_context
import observer_publisher
import lint_drafts
import names
import observer_runtime as runtime
import observer_sources as sources
import observer_store
import recortex
import record_format

KINDS = ("result", "decision", "failure", "correction", "idea", "question", "checkpoint")
STRING = {"type": "string", "maxLength": 24000}
ID = {"type": "string", "pattern": "[0-9a-f]{64}"}
CANDIDATE = {"type": "object", "properties": {
    "kind": {"type": "string", "enum": list(KINDS)}, "issue": {"type": "integer", "minimum": 1},
    "body": STRING, "sources": {"type": "array", "items": ID, "minItems": 1, "maxItems": 8},
    "excerpts": {"type": "array", "items": {"type": "string", "maxLength": 2000}, "maxItems": 8}},
    "required": ["kind", "issue", "body", "sources", "excerpts"], "additionalProperties": False}
EXTRACTION = {"type": "object", "properties": {
    "candidates": {"type": "array", "items": CANDIDATE, "maxItems": 4},
    "ignored": {"type": "array", "items": ID, "maxItems": 64}},
    "required": ["candidates", "ignored"], "additionalProperties": False}
REVIEW = {"type": "object", "properties": {
    "candidate_hash": ID, "accepted": {"type": "boolean"},
    "reason": {"type": "string", "enum": ["supported", "unsupported", "attribution", "privacy", "duplicate", "uncertain"]}},
    "required": ["candidate_hash", "accepted", "reason"], "additionalProperties": False}


def privacy_problem(text, config):
    findings = lint_drafts.lint_text(text, kind="generic", config=config)
    return any(f.level == "error" and f.code in ("secret", "restricted") for f in findings)


def source_privacy_problem(value, config):
    """Check original string values, preserving tabs/anchors lost in JSON escaping."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str) and privacy_problem(item, config):
            return True
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False


def record_context(repo, config, query=""):
    """Task-ranked trusted destinations and intact evidence groups from the checked index."""
    db = index.connect(repo, create=False)
    if db is None:
        return None
    try:
        allowed = recortex.allowed_users(repo)
        if not allowed or not index.mind_ready(db):
            return None
        probe = mind_model.Build(db, repo, {name.lower() for name in allowed}, None)
        probe.read_issues()
        wanted = observer_context.tokens(query)
        ranked = []
        for number, issue in probe.issues.items():
            if (issue["state"] != "open" or issue["pull"] or not probe.has_label(number, "final-record")
                    or not probe.body_shown(number) or not probe.title_shown(number)):
                continue
            score = len(wanted & observer_context.tokens(issue["title"] + " " + issue["body"]))
            if score or not wanted:
                ranked.append((score, number, issue))
        targets, used, omitted = [], 0, 0
        for _, number, issue in sorted(ranked, key=lambda item: (-item[0], item[1])):
            target = {"issue": number, "title": issue["title"], "body": issue["body"]}
            size = len(runtime.canonical(target))
            if used + size > 6000 or len(targets) >= 8:
                omitted += 1
                continue  # Never truncate a destination's constraints mid-claim.
            targets.append(target); used += size
        packet = observer_context.build_packet(db, repo, allowed, query=query, max_chars=8000)
        return {"targets": targets, "memories": [{"id": cid} for cid in packet["selected"]],
                "context": packet["text"], "recall_incomplete": packet["incomplete"],
                "omitted_targets": omitted, "synced_at": packet["freshness"]["synced_at"],
                "incomplete": not packet["freshness"]["sync_complete"]}
    finally:
        db.close()


def template_context():
    base = os.path.join(os.path.dirname(__file__), "..", "templates")
    templates = {}
    for kind in KINDS:
        with open(os.path.join(base, "comment-" + kind + ".md"), encoding="utf-8") as handle:
            templates[kind] = handle.read().split("<!-- Optional parts", 1)[0].strip()
    return templates


MIN_WORDS = 6  # a shorter quote ("go ahead", "this is fine") cannot back a change of status


# Runs that only show a file, a page or their own arguments: their output is not an observed result.
READERS = frozenset(("cat", "head", "tail", "less", "more", "curl", "wget", "echo", "printf"))


def _ran(item):
    """True for a tool result of a Bash call that ran, found by matching its id to the call in the transcript, other
    than a call that only shows a file, fetches a page or prints its arguments."""
    command = ((item.get("input") or {}).get("command") or "") if isinstance(item.get("input"), dict) else ""
    return (item.get("matched") is True and item.get("tool_name") == "Bash"
            and os.path.basename((command.split() or [""])[0]) not in READERS)


def _executed(candidate, supplied):
    """True when the candidate cites the result of a Bash call that ran (_ran); the call itself is found by matching,
    so the candidate need not cite it too."""
    return any(_ran(item) for source in candidate["sources"] if supplied[source]["role"] == "tool"
               for item in supplied[source].get("tool_results", []))


def _own_sources(candidate, supplied):
    """True when every source the candidate cites is a direct user message, a Bash call, or that call's matched result
    (other than a call that only shows a file or fetches a page): no page, file or other tool output stands behind
    it, whatever else the selector read."""
    calls = {item.get("invocation_source_id") for sid in candidate["sources"]
             for item in supplied[sid].get("tool_results") or []
             if item.get("matched") is True and item.get("tool_name") == "Bash"}
    for sid in candidate["sources"]:
        source = supplied[sid]
        if sid in calls or source.get("role") == "user" and source.get("direct_user") is True:
            continue
        results = source.get("tool_results") or []
        if source.get("role") == "tool" and results and all(_ran(item) for item in results):
            continue
        return False
    return True


def _users(candidate, supplied):
    return [supplied[source]["text"] for source in candidate["sources"]
            if supplied[source].get("direct_user") is True and supplied[source]["role"] == "user"]


def status_authority(candidate, supplied):
    """What a memory that overturns or sets aside an earlier one rests on. "user": a decision whose own "Exact words"
    field quotes at least MIN_WORDS words of one direct user message, and at least half of that message, so that a
    short or generic phrase cannot back it. "command": a Bash call that ran, among its own sources. "" otherwise,
    including every other kind that cites the user, since no field of theirs carries the user's words: it then
    waits for the main session. Every source it cites must be the user's own message or such a run (_own_sources)."""
    if not _own_sources(candidate, supplied):
        return ""
    if candidate["kind"] == "decision":
        quoted = observer_publisher.exact_words(candidate["body"]).strip()
        if len(quoted.split()) >= MIN_WORDS and any(quoted in text and 2 * len(quoted) >= len(text.strip())
                                                   for text in _users(candidate, supplied)):
            return "user"
    return "command" if _executed(candidate, supplied) else ""


def candidate_problem(candidate, supplied, context, config, repo=None):
    if not runtime.validate(candidate, CANDIDATE):
        return "candidate-schema"
    selected = candidate["sources"]
    if len(set(selected)) != len(selected) or not set(selected) <= set(supplied):
        return "source-binding"
    if candidate["issue"] not in {target["issue"] for target in context["targets"]}:
        return "destination"
    event = record_format.parse_event(candidate["body"])
    if event["type"] != candidate["kind"] or event.get("reconstructed"):
        return "candidate-event"
    if privacy_problem(candidate["body"] + "\n" + "\n".join(candidate["excerpts"]), config):
        return "privacy"
    # Images, HTML, @mentions and outside addresses wait for the main session; a change of status is checked when
    # the event is posted, against what it rests on (status_authority).
    outbound = observer_publisher.outbound_problem(candidate["body"], repo or config.get("repo", ""), status_ok=True)
    if outbound:
        return outbound
    if any(not any(excerpt in supplied[source]["text"] for source in selected) for excerpt in candidate["excerpts"]):
        return "excerpt-binding"
    if candidate["kind"] == "decision":
        quoted = observer_publisher.exact_words(candidate["body"])
        if not quoted or not any(quoted in text for text in _users(candidate, supplied)):
            return "decision-authority"
    if candidate["kind"] in ("result", "failure") and not _executed(candidate, supplied):
        return "observed-evidence"
    marker = record_format.parse_marker(record_format.first_lines(candidate["body"])[0])
    known = {m["id"] for m in context["memories"]}
    if any(target not in known for _, target in record_format.links(marker)):
        return "link-binding"
    # Local structural checks run before review; record lookups and posting checks run in the publisher.
    findings = lint_drafts.lint_text(candidate["body"], kind=candidate["kind"], config=config)
    if any(f.level == "error" for f in findings):
        return "candidate-lint"
    return None


MAX_SOURCES = 16  # queued observations offered to one selection call
SOURCE_TEXT = 6000  # characters of one source a model sees; a longer one keeps its start and its end
RECORD_ROOM = 18000  # what the record context may take of a call (destinations, the task packet and their quoting)
# Deferrals tried again at the next pass, up to this many attempts in all: the pass was full (input_limit,
# candidate_limit); the selector neither used nor set aside the source (uncertain), often because what it belongs with
# had not happened yet; or the selector's draft failed a mechanical check. Each attempt is counted in the reason
# ("uncertain_2"); a source that has had its attempts stays deferred, in `pending`, for `record --source`.
RETRY_SOON = {"input_limit": 6, "candidate_limit": 6, "uncertain": 3, "candidate_lint": 2, "excerpt_binding": 2,
              "observed_evidence": 2, "decision_authority": 2, "outbound_local": 2, "outbound_address": 2,
              "link_binding": 2, "candidate_event": 2}
# An uncertain source is offered again once its session has gone on (new sources of that session are in the pass), or
# after five minutes; the others in RETRY_SOON at the next pass.
WAITING = ("uncertain",)
# Deferrals tried again after five minutes: a failed call, the budget, the record.
RETRY_LATER = ("budget", "extract_failed", "review_failed", "worker_budget", "record_unavailable")


def _attempts(reason):
    """(kind, attempts so far) of a deferral reason: "uncertain_2" is the second uncertain pass of a source."""
    kind, _, count = (reason or "").rpartition("_")
    if kind in RETRY_SOON and count.isdigit():
        return kind, int(count)
    return reason or "", 1


def _again(kind, previous=""):
    """The reason for one more deferral as `kind`, counting the automatic attempts the source has had."""
    base, count = _attempts(previous)
    count = count + 1 if base in RETRY_SOON else 1
    return kind if count == 1 else "%s_%d" % (kind, count)


def _order(item):
    meta = item["metadata"]
    return (meta.get("observed_at", 0), meta.get("path", ""), meta.get("offset", 0), item["source_id"])


def _queued(store):
    """The observations offered to one pass, oldest first: deferrals due again soon, new ones and older failures,
    with places kept for each so that none starves the others."""
    now = time.time()
    fresh = sorted((store.observation(row[0]) for row in store.db.execute(
        "SELECT source_id FROM observations WHERE state='observed' ORDER BY created,source_id LIMIT 64")), key=_order)
    going_on = {item["metadata"].get("session_id") for item in fresh}
    soon, later = [], []
    rows = store.db.execute(
        "SELECT source_id,reason,updated FROM observations WHERE state='deferred' AND (reason IN (%s) OR %s) "
        "ORDER BY created,source_id LIMIT 256" % (",".join("?" * len(RETRY_LATER)),
                                                  " OR ".join("reason LIKE ?" for _ in RETRY_SOON)),
        RETRY_LATER + tuple(kind + "%" for kind in RETRY_SOON)).fetchall()
    for sid, reason, updated in rows:
        kind, count = _attempts(reason)
        if kind in RETRY_SOON and count < RETRY_SOON[kind]:
            item = store.observation(sid)
            if kind not in WAITING or item["metadata"].get("session_id") in going_on or updated < now - 300:
                soon.append(item)
        elif reason in RETRY_LATER and updated < now - 300:
            later.append(store.observation(sid))
    soon.sort(key=_order)
    chosen = soon[:6] + fresh[:7] + later[:3]
    chosen += [item for item in fresh[7:] + soon[6:] + later[3:] if item not in chosen][:MAX_SOURCES - len(chosen)]
    return chosen[:MAX_SOURCES]


def _brief(value):
    """A tool call's input as a model sees it: each long string cut to its start, nested values left out."""
    if not isinstance(value, dict):
        return None
    return {key: (item[:500] + " [...]" if isinstance(item, str) and len(item) > 500 else
                  "[...]" if isinstance(item, (dict, list)) else item) for key, item in value.items()}


def _view(source):
    """What a model is shown of one source: its role and text (a long text keeps its start and its end, marked) and,
    for a tool result, which call it answers and whether that call failed. The worker's checks use the whole source."""
    text = source["text"]
    if len(text) > SOURCE_TEXT:
        text = text[:4000] + "\n[... %d characters left out ...]\n" % (len(text) - 5500) + text[-1500:]
    view = {"id": source["id"], "message_id": source.get("message_id"), "role": source["role"], "at": source.get("at"),
            "direct_user": source.get("direct_user"), "subagent": source.get("subagent"), "text": text}
    results = [{"tool_use_id": item.get("tool_use_id"), "tool_name": item.get("tool_name"),
                "input": _brief(item.get("input")), "is_error": item.get("is_error"), "matched": item.get("matched"),
                "invocation_source_id": item.get("invocation_source_id")} for item in source.get("tool_results") or []]
    if results:
        view["tool_results"] = results
    if source.get("invocation_source_id"):
        view["invocation_source_id"] = source["invocation_source_id"]
    return view


def _calls(candidate, supplied):
    """The tool calls the candidate's cited results answer, found by matching, for the reviewer to see."""
    return [item["invocation_source_id"] for sid in candidate["sources"]
            for item in supplied[sid].get("tool_results") or []
            if item.get("matched") is True and item.get("invocation_source_id") in supplied
            and item["invocation_source_id"] not in candidate["sources"]]


def _account(repo):
    """The account a decision's words came from, when the record allows only one: the publisher can post as no other,
    and on one machine with one account it is the person typing. "" otherwise."""
    allowed = recortex.allowed_users(repo)
    return next(iter(allowed)) if len(allowed) == 1 else ""


def _process(repo, config, project_root=None, invoke_fn=None):
    """One pass: up to MAX_SOURCES queued observations, in the order they happened, with the tool calls they answer,
    to one selection call within the call's input limit, then a fresh review of each of at most four candidates."""
    invoke = invoke_fn or runtime.invoke
    options = runtime.settings(config)
    report = {"code": "disabled", "selected": 0, "reviewed": 0, "ready": [], "deferred": 0, "ignored": 0}
    if names.env(runtime.GUARD) == "1":
        return dict(report, code="recursion")
    if not options["enabled"] or not recortex.allowed_users(repo):
        return report
    root = project_root or recortex.project_root()
    with observer_store.Store(repo, max_bytes=options["max_pending_bytes"], max_pending=options["max_pending_observations"], config=config) as store:
        if not runtime.approval_valid(repo, config):
            return dict(report, code="approval")
        if store.get_control("capture_paused", False):
            return dict(report, code="paused")
        gate = store.get_control("runtime_gate")
        if not runtime.gate_valid(gate, options):
            return dict(report, code="runtime-gate")
        options["runtime_gate"] = gate
        templates = template_context()
        room = (options["max_input_chars"] - len(runtime.PROMPTS["extract"]) - len(runtime.canonical(EXTRACTION))
                - len(runtime.canonical(templates)) - RECORD_ROOM - 1000)
        supplied, offered, length = {}, [], 0
        for item in _queued(store):
            sid, previous = item["source_id"], item["reason"] if item["state"] == "deferred" else ""
            if item["metadata"].get("event_type") == "source_gap":
                store.defer(sid, "source_gap"); report["deferred"] += 1
                continue
            try:
                source = sources.resolve(item["metadata"], root)
            except (sources.SourceError, OSError, ValueError):
                store.defer(sid, "source_unavailable"); report["deferred"] += 1
                continue
            if source_privacy_problem(source, config):
                store.defer(sid, "privacy"); report["deferred"] += 1
                continue
            related = {}
            related_error = "privacy"
            for descriptor in source.pop("invocation_descriptors", []):
                try:
                    invocation = sources.resolve(descriptor, root)
                except (sources.SourceError, OSError, ValueError):
                    related = None; related_error = "source_unavailable"
                    break
                if source_privacy_problem(invocation, config):
                    related = None
                    break
                invocation.pop("invocation_descriptors", None)
                related[descriptor["source_id"]] = (descriptor, invocation)
            if related is None:
                store.defer(sid, related_error); report["deferred"] += 1
                continue
            extra = sum(len(runtime.canonical(_view(pair[1]))) for key, pair in related.items() if key not in supplied)
            if length + len(runtime.canonical(_view(source))) + extra > room:
                store.defer(sid, _again("input_limit", previous)); report["deferred"] += 1
                continue
            if related:
                store.capture([pair[0] for pair in related.values()])
                for key, pair in related.items():
                    if key not in supplied:
                        supplied[key] = pair[1]
                        length += len(runtime.canonical(_view(pair[1])))
            supplied[sid] = source
            offered.append(item)
            length += len(runtime.canonical(_view(source)))
        if not offered:
            return dict(report, code="no-sources")
        context = record_context(repo, config, query="\n".join(source["text"] for source in supplied.values()))
        if not context or not context["targets"] or context["incomplete"]:
            for item in offered: store.defer(item["source_id"], "record_unavailable")
            return dict(report, code="record-unavailable")
        if source_privacy_problem(context, config):
            for item in offered: store.defer(item["source_id"], "privacy_context")
            return dict(report, code="privacy")
        reservation = store.reserve_invocation("extract", options["max_invocations"], options["window_hours"] * 3600)
        if reservation is None:
            for item in offered: store.defer(item["source_id"], "budget")
            return dict(report, code="budget")
        started = time.monotonic()
        sheet = field_sheet.state(config, repo)
        never = (sheet["entry"] or {}).get("brief", {}).get("never", "") if sheet["state"] == "approved" else ""
        account = _account(repo)
        # In the order they happened: a turn reads as it ran.
        order = {item["source_id"]: _order(item) for item in offered}
        views = [_view(supplied[key]) for key in sorted(supplied, key=lambda key: order.get(key) or _order(
            {"source_id": key, "metadata": store.observation(key)["metadata"]}))]
        payload = {"sources": views, "record": context, "templates": templates,
                   "max_candidates": options["max_candidates"], "never_write_down": never, "account": account}
        result = invoke(payload, EXTRACTION, "extract", options)
        valid = result.get("ok") is True and runtime.validate(result.get("structured"), EXTRACTION)
        store.complete_invocation(reservation, valid, runtime.candidate_hash(result["structured"]) if valid else "")
        if not valid:
            for item in offered: store.defer(item["source_id"], "extract_failed")
            return dict(report, code="extract-failed")
        extracted = result["structured"]
        candidates = extracted["candidates"][:options["max_candidates"]]
        report["selected"] = len(candidates)
        selected_sources = {sid for candidate in candidates for sid in candidate["sources"]}
        handled = set(extracted["ignored"]) & set(supplied) - selected_sources
        for sid in handled:
            if store.observation(sid)["state"] in ("observed", "deferred"):
                store.mark_handled(sid); report["ignored"] += 1
        for candidate in candidates:
            primary = next((sid for sid in candidate["sources"] if sid in supplied), None)
            problem = candidate_problem(candidate, supplied, context, config, repo)
            if problem or not primary or set(candidate["sources"]) & handled:
                if primary and primary not in handled:
                    reason = (problem or "source-overlap").replace("-", "_")
                    before = store.observation(primary)
                    store.defer(primary, _again(reason, before["reason"] if before["state"] == "deferred" else "")
                                if reason in RETRY_SOON else reason)
                    handled.add(primary)
                report["deferred"] += 1
                continue
            newest = max(store.observation(sid)["metadata"].get("observed_at", 0) for sid in candidate["sources"])
            if store.main_post_after(candidate["issue"], candidate["kind"], newest):
                # The main session posted this kind for this issue after these sources: not posted twice.
                for sid in candidate["sources"]:
                    if store.observation(sid)["state"] in ("observed", "deferred"):
                        store.mark_handled(sid, reason="main_session")
                handled.update(candidate["sources"]); report["ignored"] += 1
                continue
            if time.monotonic() - started + options["timeout_seconds"] > 240:
                store.defer(primary, "worker_budget"); report["deferred"] += 1
                handled.add(primary)
                continue
            review_id = store.reserve_invocation("reviewer", options["max_invocations"], options["window_hours"] * 3600)
            if review_id is None:
                store.defer(primary, "budget"); report["deferred"] += 1
                handled.add(primary)
                continue
            digest = runtime.candidate_hash(candidate)
            shown = candidate["sources"] + [sid for sid in dict.fromkeys(_calls(candidate, supplied))]
            reviewed = invoke({"candidate": candidate, "candidate_hash": digest,
                               "sources": [_view(supplied[sid]) for sid in shown], "record": context,
                               "never_write_down": never, "account": account}, REVIEW, "review", options)
            answer = reviewed.get("structured")
            valid = reviewed.get("ok") is True and runtime.validate(answer, REVIEW) and answer["candidate_hash"] == digest
            store.complete_invocation(review_id, valid, digest if valid else "")
            report["reviewed"] += 1
            if not valid:
                store.defer(primary, "review_failed"); report["deferred"] += 1
                handled.add(primary)
                continue
            if not answer["accepted"] or answer["reason"] != "supported":
                store.defer(primary, "review_rejected"); report["deferred"] += 1
                handled.add(primary)
                continue
            review = {"accepted": True, "candidate_hash": digest, "reservation_id": review_id}
            if observer_publisher.status_change(candidate["body"]):
                review["status_authority"] = status_authority(candidate, supplied)
            eventkey = store.review_complete(primary, candidate, review)
            if eventkey is None:
                report["deferred"] += 1
                handled.add(primary)
                continue
            report["ready"].append(eventkey)
            handled.update(candidate["sources"])
        # What the selector neither used nor set aside is offered again, a bounded number of times.
        leftover = "candidate_limit" if len(candidates) == options["max_candidates"] else "uncertain"
        for item in offered:
            current = store.observation(item["source_id"])
            if item["source_id"] in handled or current["state"] not in ("observed", "deferred"):
                continue
            if current["state"] == "observed" or _attempts(current["reason"])[0] in RETRY_SOON:
                store.defer(item["source_id"], _again(leftover, current["reason"] if current["state"] == "deferred"
                                                      else ""))
        store.set_control("worker_last", {"at": time.time(), "code": "ok", "selected": report["selected"], "reviewed": report["reviewed"], "ready": len(report["ready"])})
    return dict(report, code="ok")


SYNC_RETRY = 30  # seconds before the next worker tries again after a refresh that failed
BUSY_WAITS = (3, 5, 8)  # seconds to wait for another process's sync (the brief's, at session start) to finish


def refresh_record(repo, config, store, options):
    """Bounded incremental refresh in the worker; hooks never wait on GitHub. A refresh that failed is tried again
    after SYNC_RETRY seconds, not after the full interval, and a sync another process holds (the brief hook's, at
    session start) is waited for briefly."""
    now = time.time()
    ok = store.get_control("sync_ok", False)
    interval = options["sync_interval_seconds"] if ok else SYNC_RETRY
    due = now - store.get_control("sync_last_attempt", 0) >= interval
    if not due and not store.get_control("sync_needed", False):
        return ok
    store.set_control("sync_last_attempt", now)
    complete = False
    for wait in BUSY_WAITS + (None,):
        try:
            result = index.sync(repo, config.get("hub"), timeout=10, deadline=10, log=lambda *args: None)
            complete = result.get("complete") is True
            break
        except index.SyncBusy:
            if wait is None:
                break
            time.sleep(wait)
        except (index.DeadlineReached, recortex.GhError, OSError, ValueError):
            break
    store.set_control("sync_ok", complete)
    if complete:
        store.set_control("sync_needed", False)
    return complete


def run_once(repo, config, project_root=None, invoke_fn=None):
    """One locked finite pass; existing ready events survive restarts and capture pauses."""
    options = runtime.settings(config)
    if names.env(runtime.GUARD) == "1" or not options["enabled"] or not recortex.allowed_users(repo):
        return {"code": "disabled", "ready": []}
    with observer_store.Store(repo, config=config, max_bytes=options["max_pending_bytes"], max_pending=options["max_pending_observations"]) as store:
        try:
            with observer_store._exclusive_lock(store.folder, "worker.lock"):
                if not runtime.approval_valid(repo, config):
                    store.set_control("worker_degraded", "approval")
                    return {"code": "approval", "ready": []}
                billing = runtime.billing_variables()
                if store.get_control("call_environment", []) != billing:
                    store.set_control("call_environment", billing)  # names only, for status
                if time.time() - store.get_control("retention_last", 0) > 86400:
                    store.expire_handled(options["retention_days"])
                    store.set_control("retention_last", time.time())
                if not runtime.gate_valid(store.get_control("runtime_gate"), options):
                    store.set_control("worker_degraded", "runtime_gate")
                    return {"code": "runtime-gate", "ready": []}
                if not refresh_record(repo, config, store, options):
                    store.set_control("worker_degraded", "record_unavailable")
                    return {"code": "record-unavailable", "ready": []}
                report = _process(repo, config, project_root, invoke_fn)
                store.set_control("worker_degraded", "" if report["code"] in ("ok", "no-sources", "paused")
                                  else report["code"].replace("-", "_"))
                approved = runtime.approval_valid(repo, config)
                if (approved and runtime.gate_valid(store.get_control("runtime_gate"), options)
                        and options["mode"] == "automatic" and not store.get_control("publication_paused", False)):
                    # Includes reviewed events left ready by a previous interrupted worker.
                    for event in store.events(states=["ready"], limit=4):
                        try:
                            observer_publisher.publish(repo, event["eventkey"], config)
                        except (observer_publisher.PublishError, recortex.GhError) as error:
                            # One event's own problem defers that event and the next one is tried; anything else
                            # (the daily cap, a marked project, GitHub unavailable) stops the pass. Both are kept.
                            reason = getattr(error, "reason", "github_unavailable")
                            store.set_control("worker_degraded", "publish_" + reason)
                            if reason in observer_publisher.EVENT_PROBLEMS:
                                store.transition(event["eventkey"], "deferred", reason=reason)
                                continue
                            break
                    if store.get_control("sync_needed", False) and not refresh_record(repo, config, store, options):
                        store.set_control("worker_degraded", "record_unavailable")
                return report
        except observer_store.StoreError as error:
            if error.reason == "publisher_busy":
                return {"code": "worker-busy", "ready": []}
            store.set_control("worker_degraded", "store_failure")
            raise
