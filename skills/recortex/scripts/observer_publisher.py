"""Publish reviewed queue events only, with uncertain-outcome reconciliation.

Only comment append requests are allowed. No issue, body, hub, board or label
mutation is available here. A local lock prevents concurrent local sends; this
does not promise exactly-once physical comments across machines.
"""

import hashlib
import html
import json
import os
import posixpath
import re
import time
from urllib.parse import unquote, urljoin, urlsplit

import field_sheet
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


# Checked on the raw body, code included: GitHub's own rules for code spans (backslash escapes, backtick runs of equal
# length) decide what renders as a live mention, so nothing is taken for code here. An @ after a word character is an
# email address or a path, never a mention.
MENTION = re.compile(r"(?<![A-Za-z0-9_])@[A-Za-z0-9]")
ADDRESS = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*://|www\.)[^\s<>()\]]+")
# Targets of inline links and images, and of reference definitions; each is resolved as a browser would.
LINK_TARGET = re.compile(r"\]\(\s*<?([^\s)>]*)")
REFERENCE = re.compile(r"(?m)^ {0,3}\[[^\]\n]+\]:[ \t]*\n?[ \t]*<?([^\s>]+)")
ESCAPED = re.compile(r"\\([!-/:-@\[-`{-~])")
# Not every Markdown form of a link can be matched with the standard library, so these are refused outright once this
# repository's own addresses are taken out: a reference definition ("]:" anywhere), "//", a backslash pair, "www.",
# and anything shaped like a scheme ("data:", "javascript:"; a field label ends in ":**" or ": ", a time in a digit).
OUTSIDE = re.compile(r"\]:|//|\\\\|(?i:www\.)|(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*:(?=[^\s*0-9])")
# owner/repo#123 and owner/repo@<sha> link, and can notify, another repository; user@<sha> names a fork's commit.
CROSS = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:#[0-9]+|@[0-9a-fA-F]{7,40}\b)")
FORK = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z0-9-]+)@[0-9a-fA-F]{7,40}\b")
# Checked on the body without its first-line event marker: any other HTML comment, a second marker included, can
# carry text that GitHub does not show.
MARKUP = re.compile(r"!\[|<(?!/?(?:details|summary)>)[A-Za-z!/?]")
# This machine's own details never go out in an automatic body: an email address, an absolute local path (two or more
# components after a leading / or ~/, not part of an address), the home folder and the local user name.
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
LOCAL_PATH = re.compile(r"(?<![A-Za-z0-9_.~:/%-])(?:~?/(?:[A-Za-z0-9_.@+%-]+/)+[A-Za-z0-9_.@+%-]*|~/)")
# A user name that is also an ordinary word would refuse ordinary text; such a name is left to the reviewer.
GENERIC_USERS = frozenset(("user", "users", "admin", "root", "ubuntu", "dev", "test", "guest", "runner", "node",
                           "owner", "home", "data", "work", "code", "app"))
STATUS_LINKS = ("replaces", "instead", "checks")  # checks= answers a question or marks an idea tried
STATUS_AUTHORITY = ("user", "agent")  # the user's typed words, or the agent's message to the user
# A problem of the event itself: the event is deferred with this reason and the next one is tried.
EVENT_PROBLEMS = frozenset(("lint_failed", "invalid_target", "explicit_hold", "status_change", "outbound_markup",
                            "outbound_mention", "outbound_address", "outbound_reference", "outbound_local",
                            "payload_changed", "review_changed", "withheld"))


def off_repository(target, repo):
    """True unless `target`, decoded as Markdown decodes a link destination and resolved against the repository's page
    as a browser resolves it, is a page of this repository on github.com."""
    value = ESCAPED.sub(r"\1", html.unescape(target)).replace("\\", "/")
    if any(ord(char) < 32 or char.isspace() for char in value):
        return True
    home = "https://github.com/%s/" % repo.lower()
    if value.lower().startswith("www."):
        value = "https://" + value
    try:
        parts = urlsplit(urljoin(home, value))
    except ValueError:
        return True
    path = posixpath.normpath(unquote(parts.path) or "/")
    return (parts.scheme.lower() != "https" or parts.netloc.lower() != "github.com"
            or not (path.lower() + "/").startswith("/%s/" % repo.lower()))


def exact_words(body):
    """The text of a decision's "Exact words" field: its quoted lines, joined."""
    fields = dict(record_format.fields(body))
    return "\n".join(line.lstrip()[1:].lstrip() for line in fields.get("Exact words", "").splitlines()
                     if line.lstrip().startswith(">"))


def queued_decision(store, draft):
    """An observer event that already holds the decision in `draft` (the same exact words), or None."""
    quoted = exact_words(draft).strip()
    if not quoted:
        return None
    for event in store.events(states=["ready", "deferred", "sending", "uncertain", "verified"], limit=10000):
        if event["candidate"]["kind"] == "decision" and exact_words(event["candidate"]["body"]).strip() == quoted:
            return event
    return None


def queued_copy(store, draft):
    """An observer event whose reviewed body is this draft, its eventkey aside, or None. Such a draft is posted with
    --eventkey, which keeps the event's confirmation on the user's terminal and the session's withhold."""
    def plain(text):
        return recortex.normalize(re.sub(r" eventkey=[0-9a-f]{32}", "", text or "", count=1))
    wanted = plain(draft)
    for event in store.events(states=["ready", "deferred", "sending", "uncertain", "verified"], limit=10000):
        if wanted in (plain(event["body"]), plain(event["candidate"]["body"])):
            return event
    return None


def register_main(repo, config, issue, kind, comment_id):
    """Record a memory the main session posted itself in the observer's queue; the keys of the events it sets
    aside are returned."""
    with Store(repo, config=config, create=False) as store:
        return store.register_main(issue, kind, comment_id)


def _without_marker(body):
    """The body without its event marker, when the first line is exactly one clean marker; else the body unchanged."""
    lines = body.split("\n")
    first = next((n for n, line in enumerate(lines) if line.strip()), None)
    if first is not None:
        line = lines[first].strip()
        if (record_format.parse_marker(line) and line.count("<") == 1 and line.count(">") == 1
                and line.count("--") == 2):
            return "\n".join(lines[:first] + lines[first + 1:])
    return body


def status_change(body):
    """True for a memory that changes what an earlier one means: a correction, or a replaces=, instead= or checks= key
    (checks= closes a question or marks an idea tried)."""
    marker = record_format.parse_marker(record_format.first_lines(body)[0]) or {}
    return marker.get("type") == "correction" or any(key in marker for key in STATUS_LINKS)


def local_names():
    """This machine's home folders and user names, as the account database and the environment give them."""
    homes, users = {os.path.expanduser("~")}, {os.environ.get("USER", ""), os.environ.get("LOGNAME", "")}
    try:
        import pwd
        account = pwd.getpwuid(os.getuid())
        homes.add(account.pw_dir)
        users.add(account.pw_name)
    except (ImportError, KeyError, AttributeError, OSError):
        pass
    return ({home.rstrip("/") for home in homes if len(home.rstrip("/")) > 1},
            {user for user in users if len(user) >= 3 and user.lower() not in GENERIC_USERS})


def local_problem(body):
    """True when the body holds an email address, an absolute local path, the home folder or the local user name, as
    written, with entities decoded, or as GitHub shows it once Markdown's backslash escapes are dropped (\\/home\\/x)."""
    shown = html.unescape(body)
    texts = (body, shown, ESCAPED.sub(r"\1", shown))
    homes, users = local_names()
    if any(EMAIL.search(text) or LOCAL_PATH.search(ADDRESS.sub(" ", text)) for text in texts):
        return True
    return any(home in text for home in homes for text in texts) or any(
        re.search(r"(?<![A-Za-z0-9_.-])" + re.escape(user) + r"(?![A-Za-z0-9_-])", text, re.IGNORECASE)
        for user in users for text in texts)


def outbound_problem(body, repo, status_ok=False):
    """What a model wrote stays inside the record. A memory that changes what an earlier one means posts on its own
    only when it rests on the user's own words or on a command that ran (status_ok); otherwise it waits for the main
    session. No image and no HTML beyond the event marker and folds; no @mention; no address other than this
    repository on github.com; nothing of this machine (local_problem). None when the body passes."""
    if status_change(body) and not status_ok:
        return "status_change"
    if MARKUP.search(_without_marker(body)):
        return "outbound_markup"
    # GitHub decodes entities (&#64;, &commat;, &#47;) before it looks for mentions and links, so both forms are checked.
    texts = (body, html.unescape(body))
    if any(MENTION.search(text) for text in texts):
        return "outbound_mention"
    targets = [target for text in texts for target in ADDRESS.findall(text) + LINK_TARGET.findall(text) + REFERENCE.findall(text)]
    if any(off_repository(target, repo) for target in targets):
        return "outbound_address"
    # Every address left is this repository's; the event marker is the record's own.
    rests = [ADDRESS.sub(" ", text).replace("<!-- ghim:event ", "<!-- ", 1) for text in texts]
    if any(OUTSIDE.search(rest) for rest in rests):
        return "outbound_address"
    owner = repo.split("/")[0].lower()
    if any(any((first + "/" + second).lower() != repo.lower() for first, second in CROSS.findall(rest))
           or any(user.lower() != owner for user in FORK.findall(rest)) for rest in rests):
        return "outbound_reference"
    if local_problem(body):
        return "outbound_local"
    return None


def held_back(store, event, config):
    """Why the automatic gate holds this event back, as reasons the main session may pass only with the user's
    confirmation on their own terminal: a change of an earlier memory's status that rests neither on the user's typed
    words nor on the agent's message to the user, and a project marked sensitive (or whose field sheet is not
    approved on this machine)."""
    reasons = []
    if status_change(event["body"]) and event["review"].get("status_authority") not in STATUS_AUTHORITY:
        reasons.append("status_change")
    sheet = field_sheet.state(config, store.repo)
    if sheet["marks"] or sheet["state"] in ("malformed", "unreadable", "new", "changed"):
        reasons.append("sensitive_project")
    return reasons


def _gate(store, config, main_session=False):
    settings = observer_runtime.settings(config)
    if store.repo not in recortex.trusted_repos():
        raise PublishError("not_trusted")
    if settings["mode"] != "automatic" or not observer_runtime.approval_valid(store.repo, config):
        raise PublishError("not_approved")
    if not observer_runtime.gate_valid(store.get_control("runtime_gate"), settings):
        raise PublishError("runtime_unavailable")
    if store.get_control("publication_paused", False):
        raise PublishError("publication_paused")
    if not main_session:
        sheet = field_sheet.state(config, store.repo)
        if sheet["marks"] or sheet["state"] in ("malformed", "unreadable", "new", "changed"):
            # 0.8.0: before anything cited is posted in a marked project, the step-7 reviewer answers the
            # confidentiality question and the user is told. Only the main session can do that.
            raise PublishError("sensitive_project")
    return settings


def _daily_cap(store, settings):
    """At most max_posts automatic comments in any 24 hours for this project; the rest waits in the queue. Counted
    under the publication lock, before the request."""
    now = time.time()
    sent = [at for at in store.get_control("automatic_posts", [])
            if type(at) in (int, float) and 0 <= now - at < 86400]
    if len(sent) >= settings["max_posts"]:
        raise PublishError("daily_cap")
    store.set_control("automatic_posts", sent + [now])


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


def _hold(store, event, main_session=False):
    command = recortex.show_command(_command(store.repo, event))
    observation = store.observation(event["source_id"])
    session = observation["metadata"].get("session_id") if observation else None
    holds = store.holds()
    answer = observer_holds.check_action(holds, store.repo, "Bash", {"command": command},
                                         session_id=session, issue=event["candidate"]["issue"])
    if answer["decision"] == "deny":
        raise PublishError("explicit_hold")
    # The user asked in a session that its moments not be posted ("don't post", "keep this off the record"): nothing
    # of it posts, the main session's --eventkey included, until the user releases that hold in their own terminal
    # (observer.py release HOLD_ID), which is the later, explicit request.
    sessions = {item["metadata"].get("session_id") for item in (store.observation(sid) for sid in
                                                                 event["candidate"]["sources"]) if item}
    if observer_holds.publication_withheld(holds, store.repo, sessions | {session}):
        raise PublishError("withheld")


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


def publish(repo, eventkey, config, dry_run=False, main_session=False, confirmed=False):
    """Append one frozen reviewed event. A dry run returns the exact command without network or writes.
    main_session=True is post_verified.py, run by the main session after its own review: it may post a deferred
    event, and with confirmed=True, which post_verified.py sets only after the user typed the confirmation on their
    own terminal, one the automatic gate held back (held_back)."""
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
            _gate(store, config, main_session)
            event = store.event(eventkey)
            if event["state"] == "verified":
                _payload_bound(event)
                return {"eventkey": eventkey, "state": "verified", "receipt": event["receipt"]}
            if event["state"] in ("sending", "uncertain"):
                return _reconcile_locked(store, event)
            # A deferred event waits for the main session, which may post it after its own review.
            if event["state"] != "ready" and not (main_session and event["state"] == "deferred"):
                raise PublishError("not_ready")
            if main_session and not confirmed and held_back(store, event, config):
                raise PublishError("confirmation_required")
            _bound(event, store)
            problem = outbound_problem(event["body"], repo, status_ok=main_session and confirmed or
                                       event["review"].get("status_authority") in STATUS_AUTHORITY)
            if problem:
                raise PublishError(problem)
            _hold(store, event, main_session)
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
            settings = _gate(store, config, main_session)
            _hold(store, event, main_session)
            if _account(repo) != me:
                raise PublishError("account_changed")
            if not main_session:
                _daily_cap(store, settings)
            # Account and intent are durable before the first possible remote write.
            store.transition(eventkey, "sending", account=me)
            event = store.event(eventkey)
            try:
                prior = _find_remote(repo, event)
                if prior is not None:
                    return _reconcile_locked(store, event)
                _gate(store, config, main_session)
                _hold(store, event, main_session)
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
            if not observer_runtime.approval_valid(repo, config):
                raise PublishError("not_approved")
            return _reconcile_locked(store, event)
