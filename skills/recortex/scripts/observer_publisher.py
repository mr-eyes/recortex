"""Publish reviewed queue events only, with uncertain-outcome reconciliation.

Only comment append requests are allowed. No issue, body, hub, board or label
mutation is available here. A local lock prevents concurrent local sends; this
does not promise exactly-once physical comments across machines.
"""

import hashlib
import json
import re

import lint_drafts
import lookup
import observer_holds
import observer_runtime
from observer_store import Store, StoreError, candidate_hash
import recortex
import record_format


class PublishError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__("observer publication deferred: " + reason)


def _gate(store, config):
    settings = observer_runtime.settings(config)
    if store.repo not in recortex.trusted_repos():
        raise PublishError("not_trusted")
    if settings["mode"] != "automatic" or not observer_runtime.approval_valid(
            store.repo, config, store.get_control("observer_approval")):
        raise PublishError("not_approved")
    if not observer_runtime.gate_valid(store.get_control("runtime_gate"), settings):
        raise PublishError("runtime_unavailable")
    if store.get_control("publication_paused", False):
        raise PublishError("publication_paused")
    return settings


def _payload_bound(event):
    """The frozen published body survives private excerpt retention unchanged."""
    if event is None:
        raise PublishError("missing_event")
    if hashlib.sha256(event["body"].encode("utf-8")).hexdigest() != event["payload_hash"]:
        raise PublishError("payload_changed")
    marker = record_format.parse_marker(record_format.first_lines(event["body"])[0]) or {}
    if marker.get("eventkey") != event["eventkey"] or marker.get("type") != event["candidate"]["kind"]:
        raise PublishError("payload_changed")
    if event["state"] == "verified" and (event["receipt"].get("payload_hash") != event["payload_hash"] or
                                           event["receipt"].get("account") != event["account"]):
        raise PublishError("receipt_changed")


def _bound(event, store):
    _payload_bound(event)
    if event["review"].get("accepted") is not True or event["review"].get("candidate_hash") != candidate_hash(event["candidate"]):
        raise PublishError("review_changed")
    if not store.review_valid(event):
        raise PublishError("review_changed")


def _account(repo):
    me = recortex.gh(["api", "user", "--hostname", "github.com", "--jq", ".login"]).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", me) or me.lower() not in recortex.allowed_users(repo):
        raise PublishError("account_not_allowed")
    return me.lower()


def _command(repo, event):
    return ["issue", "comment", str(event["candidate"]["issue"]), "--repo", repo, "--body-file", "-"]


def _hold(store, event):
    command = recortex.show_command(_command(store.repo, event))
    observation = store.observation(event["source_id"])
    session = observation["metadata"].get("session_id") if observation else None
    answer = observer_holds.check_action(store.holds(), store.repo, "Bash", {"command": command},
                                         session_id=session, issue=event["candidate"]["issue"])
    if answer["decision"] == "deny":
        raise PublishError("explicit_hold")


def _remote_comment(repo, comment_id):
    data = json.loads(recortex.gh(["api", "repos/%s/issues/comments/%d" % (repo, int(comment_id))]))
    if not isinstance(data, dict):
        raise PublishError("invalid_receipt")
    return data


def _check_remote(repo, event, data):
    cid = data.get("id")
    if isinstance(cid, bool) or not isinstance(cid, int) or cid < 1:
        raise PublishError("invalid_receipt")
    issue = event["candidate"]["issue"]
    expected = "https://api.github.com/repos/%s/issues/%d" % (repo, issue)
    if data.get("issue_url", "").lower() != expected.lower():
        raise PublishError("wrong_target")
    if (data.get("user") or {}).get("login", "").lower() != event["account"]:
        raise PublishError("wrong_author")
    if event["account"] not in recortex.allowed_users(repo):
        raise PublishError("account_not_allowed")
    if not data.get("created_at") or data.get("created_at") != data.get("updated_at"):
        raise PublishError("remote_edited")
    if recortex.normalize(data.get("body") or "") != recortex.normalize(event["body"]):
        raise PublishError("remote_changed")
    return {"comment_id": cid, "url": "https://github.com/%s/issues/%d#issuecomment-%d" % (repo, issue, cid),
            "payload_hash": event["payload_hash"], "account": event["account"]}


def _find_remote(repo, event):
    number = event["candidate"]["issue"]
    rows = recortex.parse_json_stream(recortex.gh(
        ["api", "--paginate", "repos/%s/issues/%d/comments?per_page=100" % (repo, number)]))
    found = []
    for row in rows:
        if not isinstance(row, dict):
            raise PublishError("invalid_remote")
        marker = record_format.parse_marker(record_format.first_lines(row.get("body") or "")[0]) or {}
        if marker.get("eventkey") == event["eventkey"]:
            found.append(row)
    if len(found) > 1:
        raise PublishError("duplicate_event")
    return found[0] if found else None


def _uncertain(store, key, reason):
    event = store.event(key)
    if event["state"] == "sending":
        store.transition(key, "uncertain", reason=reason)
    return {"eventkey": key, "state": "uncertain", "reason": reason}


def _reconcile_locked(store, event):
    if event["state"] == "verified":
        _payload_bound(event)
        return {"eventkey": event["eventkey"], "state": "verified", "receipt": event["receipt"]}
    _bound(event, store)
    if event["state"] not in ("sending", "uncertain"):
        raise PublishError("not_uncertain")
    try:
        found = _find_remote(store.repo, event)
        if found is None:
            return _uncertain(store, event["eventkey"], "remote_not_found")
        # Verify the individual comment, not only a potentially stale listing.
        receipt = _check_remote(store.repo, event, _remote_comment(store.repo, found["id"]))
        store.transition(event["eventkey"], "verified", receipt=receipt)
        store.set_control("sync_needed", True)
        return {"eventkey": event["eventkey"], "state": "verified", "receipt": receipt}
    except (recortex.GhError, ValueError, KeyError, TypeError, PublishError) as error:
        return _uncertain(store, event["eventkey"], getattr(error, "reason", "remote_unavailable"))


def publish(repo, eventkey, config, dry_run=False):
    """Append one frozen reviewed event. A dry run returns the exact command without network or writes."""
    settings = observer_runtime.settings(config)
    with Store(repo, config=config, create=False, readonly=dry_run, max_bytes=settings["max_pending_bytes"],
               max_pending=settings["max_pending_observations"]) as store:
        event = store.event(eventkey)
        if event is None:
            raise PublishError("missing_event")
        if dry_run:
            return {"eventkey": eventkey, "state": event["state"], "dry_run": True,
                    "command": recortex.show_command(_command(repo, event), "frozen reviewed body from stdin"),
                    "payload_hash": event["payload_hash"]}
        with store.publication_lock():
            _gate(store, config)
            event = store.event(eventkey)
            if event["state"] == "verified":
                _payload_bound(event)
                return {"eventkey": eventkey, "state": "verified", "receipt": event["receipt"]}
            if event["state"] in ("sending", "uncertain"):
                return _reconcile_locked(store, event)
            if event["state"] != "ready":
                raise PublishError("not_ready")
            _bound(event, store)
            _hold(store, event)
            me = _account(repo)
            # Target existence and draft rules are checked on the live record before sending.
            target = json.loads(recortex.gh(["api", "repos/%s/issues/%d" % (repo, event["candidate"]["issue"])]))
            if not isinstance(target, dict) or target.get("pull_request"):
                raise PublishError("invalid_target")
            record = lookup.github(repo, event["candidate"]["issue"])
            try:
                findings = lint_drafts.lint_text(event["body"], kind=event["candidate"]["kind"], config=config,
                                                number_checker=lambda n: recortex.number_exists(repo, n), lookup=record)
            finally:
                record.close()
            if any(f.level == "error" for f in findings):
                raise PublishError("lint_failed")
            _gate(store, config)
            _hold(store, event)
            if _account(repo) != me:
                raise PublishError("account_changed")
            # Account and intent are durable before the first possible remote write.
            store.transition(eventkey, "sending", account=me)
            event = store.event(eventkey)
            try:
                prior = _find_remote(repo, event)
                if prior is not None:
                    return _reconcile_locked(store, event)
                _gate(store, config)
                _hold(store, event)
                if _account(repo) != event["account"]:
                    raise PublishError("account_changed")
                output = recortex.gh(_command(repo, event), stdin=event["body"])
                match = re.search(r"https://github\.com/" + re.escape(repo) + r"/issues/" +
                                  str(event["candidate"]["issue"]) + r"#issuecomment-([0-9]+)", output)
                if not match:
                    return _uncertain(store, eventkey, "receipt_missing")
                receipt = _check_remote(repo, event, _remote_comment(repo, int(match.group(1))))
                store.transition(eventkey, "verified", receipt=receipt)
                store.set_control("sync_needed", True)
                return {"eventkey": eventkey, "state": "verified", "receipt": receipt}
            except (recortex.GhError, ValueError, KeyError, TypeError, PublishError) as error:
                return _uncertain(store, eventkey, getattr(error, "reason", "request_failed"))


def reconcile(repo, eventkey, config, dry_run=False):
    """Read the remote target for an uncertain send; never append or blindly retry."""
    with Store(repo, config=config, create=False, readonly=dry_run) as store:
        event = store.event(eventkey)
        if event is None:
            raise PublishError("missing_event")
        if dry_run:
            return {"eventkey": eventkey, "state": event["state"], "dry_run": True,
                    "command": recortex.show_command(["api", "--paginate", "repos/%s/issues/%d/comments?per_page=100" %
                                                       (repo, event["candidate"]["issue"])])}
        with store.publication_lock():
            # A pause stops new writes, not read-only receipt reconciliation.
            if repo.lower() not in recortex.trusted_repos():
                raise PublishError("not_trusted")
            if not observer_runtime.approval_valid(repo, config, store.get_control("observer_approval")):
                raise PublishError("not_approved")
            return _reconcile_locked(store, event)
