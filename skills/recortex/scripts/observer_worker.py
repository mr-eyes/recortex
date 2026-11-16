"""Finite selection and fresh review. Unreviewed source/draft text stays in memory."""
import hashlib
import json
import os
import re
import time

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
    "reason": {"type": "string", "enum": ["supported", "unsupported", "attribution", "privacy", "duplicate", "uncertain",
                                          "withheld"]}},
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


MIN_WORDS = 6  # a shorter typed message ("go ahead", "this is fine") cannot back a change of status


def _main_agent(source):
    """The main session's agent writing to the user: an assistant record of the main transcript, not a subagent's or a
    sidechain's (observer_sources drops meta, compaction and transcript-only records before this)."""
    return source.get("role") == "assistant" and source.get("subagent") is False and source.get("sidechain") is False


def _said(source):
    """The conversation the observer takes notes of: a prompt the user typed, or the main session's agent writing to
    the user. Nothing else reaches the selector or the reviewer."""
    return source.get("role") == "user" and source.get("direct_user") is True or _main_agent(source)


def _users(candidate, supplied):
    return [supplied[source]["text"] for source in candidate["sources"]
            if supplied[source].get("direct_user") is True and supplied[source]["role"] == "user"]


def _agent(candidate, supplied):
    """True when the candidate cites the agent's own message to the user."""
    return any(_main_agent(supplied[source]) for source in candidate["sources"])


def _quoted(candidate):
    """The user's words the memory quotes for itself: a decision's Exact words, or the quoted lines of another kind."""
    if candidate["kind"] == "decision":
        return observer_publisher.exact_words(candidate["body"]).strip()
    return "\n".join(line.lstrip()[1:].strip() for line in candidate["body"].splitlines()
                     if line.lstrip().startswith(">")).strip()


def status_authority(candidate, supplied):
    """What a memory that changes an earlier one's status rests on, all of it said in the conversation. "user": the
    memory quotes, in its own field (a decision's Exact words, another kind's quoted lines), at least MIN_WORDS words
    and at least half of one typed message it cites, so that the user's words back this very change. "agent": the
    agent's message to the user, cited. "" otherwise (a short phrase such as "go ahead" on its own, or a long message
    the memory does not quote): it then waits for the main session."""
    if not all(_said(supplied[source]) for source in candidate["sources"]):
        return ""
    quoted = _quoted(candidate)
    if len(quoted.split()) >= MIN_WORDS and any(quoted in text and 2 * len(quoted) >= len(text.strip())
                                               for text in _users(candidate, supplied)):
        return "user"
    return "agent" if _agent(candidate, supplied) else ""


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
    if candidate["kind"] in ("result", "failure") and not _agent(candidate, supplied):
        return "agent-account"  # a result or a failure is what the agent told the user it found
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
REVIEW_CONTEXT = 12000  # characters of the rest of the stretch a reviewer is shown beside a candidate's cited messages
# Deferrals tried again at the next pass, up to this many attempts in all: the pass was full (input_limit,
# candidate_limit); the selector neither used nor set aside the source (uncertain), often because what it belongs with
# had not happened yet; or the selector's draft failed a mechanical check. Each attempt is counted in the reason
# ("uncertain_2"); a source that has had its attempts stays deferred, in `pending`, for `record --source`.
RETRY_SOON = {"input_limit": 6, "candidate_limit": 6, "uncertain": 3, "candidate_lint": 2, "excerpt_binding": 2,
              "agent_account": 2, "decision_authority": 2, "outbound_local": 2, "outbound_address": 2,
              "link_binding": 2, "candidate_event": 2, "destination": 2}
# An uncertain source is offered again once its session has gone on (new sources of that session are in the pass), or
# after five minutes; the others in RETRY_SOON at the next pass.
WAITING = ("uncertain",)
# Deferrals tried again after five minutes: a failed call, the budget, the record.
RETRY_LATER = ("budget", "extract_failed", "review_failed", "worker_budget", "record_unavailable")
# Failed calls are tried again after five minutes, at most this many times for a source shown alone; a failed batch is
# offered again in halves, so that a source the model cannot answer for is found in a few calls and stops there.
RETRY_COUNTED = dict(RETRY_SOON, extract_failed=3, review_failed=3)
# Failures of a call that say nothing about its input: the batch is kept whole and tried again after five minutes.
TRANSIENT = frozenset(("timeout", "runtime-unavailable", "runtime-gate", "recursion", "model-changed", "isolation"))
# A pass waits for at least this many sources, or for one that has waited ten minutes, so that a call sees a stretch
# of the session and the day's calls last a working day.
ENOUGH, PATIENCE = 8, 600


def _attempts(reason):
    """(kind, attempts so far) of a deferral reason: "uncertain_2" is the second uncertain pass of a source."""
    kind, _, count = (reason or "").rpartition("_")
    if kind in RETRY_COUNTED and count.isdigit():
        return kind, int(count)
    return reason or "", 1


def _again(kind, previous=""):
    """The reason for one more deferral as `kind`, counting the automatic attempts the source has had."""
    base, count = _attempts(previous)
    count = count + 1 if base in RETRY_COUNTED else 1
    return kind if count == 1 else "%s_%d" % (kind, count)


def _order(item):
    meta = item["metadata"]
    return (meta.get("observed_at", 0), meta.get("path", ""), meta.get("offset", 0), item["source_id"])


def _queued(store):
    """(items, half): the observations offered to one pass, oldest first: deferrals due again soon, new ones and older
    failures, with places kept for each so that none starves the others. The halves of a failed batch come first, one
    half a pass and alone (half True), so that a source the model cannot answer for is found in a few calls."""
    batches = store.get_control("retry_halves", [])
    while isinstance(batches, list) and batches:
        batch = batches.pop(0)
        items = [store.observation(sid) for sid in batch if isinstance(sid, str)] if isinstance(batch, list) else []
        items = [item for item in items if item and item["state"] == "deferred"]
        if items:
            store.set_control("retry_halves", batches)
            return sorted(items, key=_order), True
    if batches != []:
        store.set_control("retry_halves", [])
    now = time.time()
    fresh = sorted((store.observation(row[0]) for row in store.db.execute(
        "SELECT source_id FROM observations WHERE state='observed' ORDER BY created,source_id LIMIT 64")), key=_order)
    going_on = {item["metadata"].get("session_id") for item in fresh}
    soon, later = [], []
    rows = store.db.execute(
        "SELECT source_id,reason,updated FROM observations WHERE state='deferred' AND (reason IN (%s) OR %s) "
        "ORDER BY created,source_id LIMIT 256" % (",".join("?" * len(RETRY_LATER)),
                                                  " OR ".join("reason LIKE ?" for _ in RETRY_COUNTED)),
        RETRY_LATER + tuple(kind + "%" for kind in RETRY_COUNTED)).fetchall()
    for sid, reason, updated in rows:
        kind, count = _attempts(reason)
        if kind in RETRY_SOON and count < RETRY_SOON[kind]:
            item = store.observation(sid)
            if kind not in WAITING or item["metadata"].get("session_id") in going_on or updated < now - 300:
                soon.append(item)
        elif (kind in RETRY_LATER and (kind not in RETRY_COUNTED or count < RETRY_COUNTED[kind])
              and updated < now - 300):
            later.append(store.observation(sid))
    soon.sort(key=_order)
    chosen = soon[:6] + fresh[:7] + later[:3]
    chosen += [item for item in fresh[7:] + soon[6:] + later[3:] if item not in chosen][:MAX_SOURCES - len(chosen)]
    return chosen[:MAX_SOURCES], False


def _view(source):
    """What a model is shown of one source: who said it, when, and its text (a long text keeps its start and its end,
    marked). The worker's checks use the whole source."""
    text = source["text"]
    if len(text) > SOURCE_TEXT:
        text = text[:4000] + "\n[... %d characters left out ...]\n" % (len(text) - 5500) + text[-1500:]
    return {"id": source["id"], "message_id": source.get("message_id"), "role": source["role"], "at": source.get("at"),
            "direct_user": source.get("direct_user"), "text": text}


def _stretch(candidate, supplied, order):
    """The other messages of the candidate's sessions in this pass, in order, as the reviewer's context: what else was
    said around the cited messages (the command the user named, the commit the agent gave). Bounded by REVIEW_CONTEXT;
    status_authority still rests on the cited messages alone."""
    cited = set(candidate["sources"])
    sessions = {supplied[sid].get("session_id") for sid in cited}
    out, used = [], 0
    for sid in sorted(supplied, key=lambda key: order[key]):
        if sid in cited or supplied[sid].get("session_id") not in sessions:
            continue
        view = _view(supplied[sid])
        size = len(runtime.canonical(view))
        if used + size <= REVIEW_CONTEXT:
            out.append(view)
            used += size
    return out


def _account(repo):
    """The account a decision's words came from, when the record allows only one: the publisher can post as no other,
    and on one machine with one account it is the person typing. "" otherwise."""
    allowed = recortex.allowed_users(repo)
    return next(iter(allowed)) if len(allowed) == 1 else ""


def _process(repo, config, project_root=None, invoke_fn=None):
    """One pass: up to MAX_SOURCES queued observations of the conversation (the user's typed prompts and the agent's
    messages to the user), in the order they happened, to one selection call within the call's input limit, then a
    fresh review of each of at most four candidates. A pass waits for ENOUGH sources or for one older than
    PATIENCE."""
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
        queued, half = _queued(store)
        if not half and len(queued) < ENOUGH and all(time.time() - item["created"] < PATIENCE for item in queued):
            return dict(report, code="no-sources")  # wait for a stretch of the session
        for item in queued:
            sid, previous = item["source_id"], item["reason"] if item["state"] == "deferred" else ""
            meta = item["metadata"]
            if meta.get("event_type") == "source_gap":
                store.defer(sid, "source_gap"); report["deferred"] += 1
                continue
            if meta.get("role") not in ("user", "assistant") or os.sep + "subagents" + os.sep in meta.get("path", ""):
                # Tool output and subagents' transcripts are not the conversation (a queue from before 1.1.0).
                store.mark_handled(sid, reason="not_conversation"); report["ignored"] += 1
                continue
            try:
                source = sources.resolve(meta, root)
            except (sources.SourceError, OSError, ValueError):
                store.defer(sid, "source_unavailable"); report["deferred"] += 1
                continue
            if not _said(source):
                # A user record that is not a typed prompt (a command's wrapper): it may set a hold, nothing else.
                store.mark_handled(sid, reason="not_typed"); report["ignored"] += 1
                continue
            if source_privacy_problem(source, config):
                store.defer(sid, "privacy"); report["deferred"] += 1
                continue
            if length + len(runtime.canonical(_view(source))) > room:
                store.defer(sid, _again("input_limit", previous)); report["deferred"] += 1
                continue
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
        account = _account(repo)
        # In the order they happened: a turn reads as it ran.
        order = {item["source_id"]: _order(item) for item in offered}
        views = [_view(supplied[key]) for key in sorted(supplied, key=lambda key: order[key])]
        # The observer does not judge confidentiality: no field sheet, mark or pattern goes to the models. The
        # mechanical refusals (secrets, the project's restricted patterns, this machine's details) and the linter
        # apply to every body.
        payload = {"sources": views, "record": context, "templates": templates,
                   "max_candidates": options["max_candidates"], "account": account}
        result = invoke(payload, EXTRACTION, "extract", options)
        valid = result.get("ok") is True and runtime.validate(result.get("structured"), EXTRACTION)
        store.complete_invocation(reservation, valid, runtime.candidate_hash(result["structured"]) if valid else "")
        if not valid:
            # Counted only for a source shown alone; a larger batch is offered again in halves, so that a source the
            # model cannot answer for is found in a few calls (RETRY_COUNTED). A transient failure keeps it whole.
            alone = len(offered) == 1
            for item in offered:
                previous = item["reason"] if item["state"] == "deferred" else ""
                kept = previous if _attempts(previous)[0] == "extract_failed" else "extract_failed"
                store.defer(item["source_id"], _again("extract_failed", previous) if alone else kept)
            if not alone and result.get("code") not in TRANSIENT:
                ids, middle = [item["source_id"] for item in offered], len(offered) // 2
                batches = store.get_control("retry_halves", [])
                store.set_control("retry_halves", ([ids[:middle], ids[middle:]] + (batches if isinstance(batches, list)
                                                                                   else []))[:32])
            return dict(report, code="extract-failed")
        extracted = result["structured"]
        candidates = extracted["candidates"][:options["max_candidates"]]
        report["selected"] = len(candidates)
        selected_sources = {sid for candidate in candidates for sid in candidate["sources"]}
        handled = set(extracted["ignored"]) & set(supplied) - selected_sources
        for sid in handled:
            if store.observation(sid)["state"] in ("observed", "deferred"):
                store.mark_handled(sid); report["ignored"] += 1
        used = set()  # the sources of this pass's events
        for candidate in candidates:
            # One agent message often tells several moments (a result and the idea it raised). A source backs one
            # event: a candidate whose other sources remain drops the ones an event already holds.
            taken = [sid for sid in candidate["sources"] if sid in used or store.db.execute(
                "SELECT 1 FROM event_sources WHERE source_id=?", (sid,)).fetchone()]
            if taken and len(taken) < len(candidate["sources"]):
                candidate = dict(candidate, sources=[sid for sid in candidate["sources"] if sid not in taken])
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
            reviewed = invoke({"candidate": candidate, "candidate_hash": digest,
                               "sources": [_view(supplied[sid]) for sid in candidate["sources"]],
                               "context": _stretch(candidate, supplied, order), "record": context,
                               "account": account}, REVIEW, "review", options)
            answer = reviewed.get("structured")
            valid = reviewed.get("ok") is True and runtime.validate(answer, REVIEW) and answer["candidate_hash"] == digest
            store.complete_invocation(review_id, valid, digest if valid else "")
            report["reviewed"] += 1
            if not valid:
                before = store.observation(primary)
                store.defer(primary, _again("review_failed", before["reason"] if before["state"] == "deferred" else ""))
                report["deferred"] += 1
                handled.add(primary)
                continue
            if not answer["accepted"] or answer["reason"] != "supported":
                # "withheld": the user asked that this not be posted or recorded; it is never offered again.
                store.defer(primary, "withheld" if answer["reason"] == "withheld" else "review_rejected")
                report["deferred"] += 1
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
            used.update(candidate["sources"])
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


SETTLE_SECONDS = 3.0  # how long the worker waits for the transcripts it knows to stop growing
SETTLE_QUIET = 0.4  # and how long one must stay the same size to count as settled
KNOWN_HOURS = 6  # a transcript it knows: one with an observation this recent


def settle(store, repo, config, project_root, seconds=SETTLE_SECONDS, quiet=SETTLE_QUIET):
    """Capture what the hooks could not. Claude Code writes a turn's final message after its Stop hook has run, so the
    worker started there waits until the transcripts it knows stop growing (at most `seconds`), then scans their unread
    tails as a hook does, holds included. The transcript stays the only source. Returns the number of transcripts
    read; one that cannot be read is left to the next hook."""
    import observer_hooks  # here: the worker needs it only for this
    since = time.time() - KNOWN_HOURS * 3600
    known = {}
    for (metadata,) in store.db.execute("SELECT metadata FROM observations WHERE updated>? ORDER BY updated DESC "
                                        "LIMIT 2000", (since,)):
        meta = json.loads(metadata)
        path, session = meta.get("path"), meta.get("session_id")
        if isinstance(path, str) and isinstance(session, str) and os.path.basename(path) == session + ".jsonl":
            known.setdefault(path, session)
    known = dict(list(known.items())[:20])

    def sizes():
        out = {}
        for path in known:
            try:
                out[path] = os.stat(path, follow_symlinks=False).st_size
            except OSError:
                out[path] = None
        return out
    last, changed, until = sizes(), time.monotonic(), time.monotonic() + seconds
    while known and time.monotonic() < until:
        time.sleep(0.05)
        now = sizes()
        if now != last:
            last, changed = now, time.monotonic()
        elif time.monotonic() - changed >= quiet:
            break
    read = 0
    paused = store.get_control("capture_paused", False)
    for path, session in known.items():
        data = {"session_id": session, "transcript_path": path, "hook_event_name": "Stop"}
        try:
            observer_hooks._capture(store, "Stop", data, project_root, repo, config, capture=not paused)
            read += 1
        except (sources.SourceError, observer_store.StoreError, OSError, ValueError):
            continue
    return read


def run_once(repo, config, project_root=None, invoke_fn=None, settle_first=False):
    """One locked finite pass; existing ready events survive restarts and capture pauses. With settle_first (the
    worker a hook starts), the known transcripts are captured again first (settle)."""
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
                if settle_first:
                    settle(store, repo, config, project_root or recortex.project_root())
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
