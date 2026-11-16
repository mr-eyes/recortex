"""Finite selection and fresh review. Unreviewed source/draft text stays in memory."""
import hashlib
import json
import os
import re
import time

import index
import mind_model
import observer_context
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
    "ignored": {"type": "array", "items": ID, "maxItems": 8}},
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


def candidate_problem(candidate, supplied, context, config):
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
    if any(not any(excerpt in supplied[source]["text"] for source in selected) for excerpt in candidate["excerpts"]):
        return "excerpt-binding"
    if candidate["kind"] == "decision":
        original = [supplied[source]["text"] for source in selected if supplied[source].get("direct_user") is True and supplied[source]["role"] == "user"]
        fields = dict(record_format.fields(candidate["body"]))
        quoted = "\n".join(line.lstrip()[1:].lstrip() for line in fields.get("Exact words", "").splitlines() if line.lstrip().startswith(">"))
        if not quoted or not any(quoted in text for text in original):
            return "decision-authority"
    if candidate["kind"] in ("result", "failure"):
        execution = any(item.get("matched") is True and item.get("tool_name") == "Bash"
                        and item.get("invocation_source_id") in selected
                        for source in selected if supplied[source]["role"] == "tool"
                        for item in supplied[source].get("tool_results", []))
        if not execution:
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


def _process(repo, config, project_root=None, invoke_fn=None):
    """Process eight queued observations plus matched requests/four candidates; bounded model input."""
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
        supplied, length = {}, 0
        # Ambiguity, privacy and unavailable sources wait for an explicit retry.
        # New work wins over bounded retries; an old permanent deferral cannot starve it.
        fresh_ids = [row[0] for row in store.db.execute(
            "SELECT source_id FROM observations WHERE state='observed' ORDER BY created,source_id LIMIT 8")]
        retry_ids = [row[0] for row in store.db.execute(
            "SELECT source_id FROM observations WHERE state='deferred' AND reason IN "
            "('budget','extract_failed','review_failed','worker_budget','record_unavailable','candidate_limit') "
            "AND updated < ? ORDER BY updated,source_id LIMIT 8", (time.time() - 300,))]
        # Reserve three places for eligible retries even during continuous arrivals.
        pending_ids = fresh_ids[:5] + retry_ids[:3]
        pending_ids += [sid for sid in fresh_ids[5:] + retry_ids[3:] if sid not in pending_ids][:8-len(pending_ids)]
        for item in (store.observation(sid) for sid in pending_ids):
            if item["state"] not in ("observed", "deferred"):
                continue
            sid = item["source_id"]
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
            if length + len(source["text"]) > 12000:
                store.defer(sid, "input_limit"); report["deferred"] += 1
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
                related[descriptor["source_id"]] = (descriptor, invocation)
            if related is None:
                store.defer(sid, related_error); report["deferred"] += 1
                continue
            extra = sum(len(runtime.canonical(pair[1])) for key, pair in related.items() if key not in supplied)
            if length + len(runtime.canonical(source)) + extra > 12000:
                store.defer(sid, "input_limit"); report["deferred"] += 1
                continue
            if related:
                store.capture([pair[0] for pair in related.values()])
                for key, pair in related.items():
                    if key not in supplied:
                        supplied[key] = pair[1]
                        length += len(runtime.canonical(pair[1]))
            supplied[sid] = source
            length += len(runtime.canonical(source))
        if not supplied:
            return dict(report, code="no-sources")
        context = record_context(repo, config, query="\n".join(source["text"] for source in supplied.values()))
        if not context or not context["targets"] or context["incomplete"]:
            for sid in supplied: store.defer(sid, "record_unavailable")
            return dict(report, code="record-unavailable")
        if source_privacy_problem(context, config):
            for sid in supplied: store.defer(sid, "privacy_context")
            return dict(report, code="privacy")
        reservation = store.reserve_invocation("extract", options["max_invocations"], options["window_hours"] * 3600)
        if reservation is None:
            for sid in supplied: store.defer(sid, "budget")
            return dict(report, code="budget")
        started = time.monotonic()
        payload = {"sources": list(supplied.values()), "record": context, "templates": template_context(), "max_candidates": options["max_candidates"]}
        result = invoke(payload, EXTRACTION, "extract", options)
        valid = result.get("ok") is True and runtime.validate(result.get("structured"), EXTRACTION)
        store.complete_invocation(reservation, valid, runtime.candidate_hash(result["structured"]) if valid else "")
        if not valid:
            for sid in supplied: store.defer(sid, "extract_failed")
            return dict(report, code="extract-failed")
        extracted = result["structured"]
        candidates = extracted["candidates"][:options["max_candidates"]]
        report["selected"] = len(candidates)
        selected_sources = {sid for candidate in candidates for sid in candidate["sources"]}
        for sid in set(extracted["ignored"]) & set(supplied) - selected_sources:
            store.mark_handled(sid); report["ignored"] += 1
        handled = set(extracted["ignored"]) & set(supplied) - selected_sources
        for candidate in candidates:
            primary = next((sid for sid in candidate["sources"] if sid in supplied), None)
            problem = candidate_problem(candidate, supplied, context, config)
            if problem or not primary or set(candidate["sources"]) & handled:
                if primary and primary not in handled:
                    store.defer(primary, (problem or "source-overlap").replace("-", "_"))
                report["deferred"] += 1
                continue
            if time.monotonic() - started + options["timeout_seconds"] > 240:
                store.defer(primary, "worker_budget"); report["deferred"] += 1
                continue
            review_id = store.reserve_invocation("reviewer", options["max_invocations"], options["window_hours"] * 3600)
            if review_id is None:
                store.defer(primary, "budget"); report["deferred"] += 1
                continue
            digest = runtime.candidate_hash(candidate)
            reviewed = invoke({"candidate": candidate, "candidate_hash": digest, "sources": [supplied[sid] for sid in candidate["sources"]], "record": context}, REVIEW, "review", options)
            answer = reviewed.get("structured")
            valid = reviewed.get("ok") is True and runtime.validate(answer, REVIEW) and answer["candidate_hash"] == digest
            store.complete_invocation(review_id, valid, digest if valid else "")
            report["reviewed"] += 1
            if not valid:
                store.defer(primary, "review_failed"); report["deferred"] += 1
                continue
            if not answer["accepted"] or answer["reason"] != "supported":
                store.defer(primary, "review_rejected"); report["deferred"] += 1
                continue
            eventkey = store.review_complete(primary, candidate, {"accepted": True, "candidate_hash": digest, "reservation_id": review_id})
            if eventkey is None:
                report["deferred"] += 1
                continue
            report["ready"].append(eventkey)
            handled.update(candidate["sources"])
        for sid in set(supplied) - handled:
            if store.observation(sid)["state"] == "observed":
                store.defer(sid, "candidate_limit" if len(candidates) == options["max_candidates"] else "uncertain")
        store.set_control("worker_last", {"at": time.time(), "code": "ok", "selected": report["selected"], "reviewed": report["reviewed"], "ready": len(report["ready"])})
    return dict(report, code="ok")


def refresh_record(repo, config, store, options):
    """Bounded incremental refresh in the worker; hooks never wait on GitHub."""
    now = time.time()
    due = now - store.get_control("sync_last_attempt", 0) >= options["sync_interval_seconds"]
    if not due and not store.get_control("sync_needed", False):
        return store.get_control("sync_ok", False)
    store.set_control("sync_last_attempt", now)
    try:
        result = index.sync(repo, config.get("hub"), timeout=10, deadline=10, log=lambda *args: None)
        complete = result.get("complete") is True
    except (index.SyncBusy, index.DeadlineReached, recortex.GhError, OSError, ValueError):
        complete = False
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
                    import observer_publisher
                    # Includes reviewed events left ready by a previous interrupted worker.
                    for event in store.events(states=["ready"], limit=4):
                        observer_publisher.publish(repo, event["eventkey"], config)
                    if store.get_control("sync_needed", False) and not refresh_record(repo, config, store, options):
                        store.set_control("worker_degraded", "record_unavailable")
                return report
        except observer_store.StoreError as error:
            if error.reason == "publisher_busy":
                return {"code": "worker-busy", "ready": []}
            store.set_control("worker_degraded", "store_failure")
            raise
