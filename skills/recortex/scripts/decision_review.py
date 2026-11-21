"""Replay append-only human impact reviews independently for each target revision."""
import json
import re

MARKER = "<!-- recortex:decision-review v1 -->"
HASH = re.compile(r"[0-9a-f]{64}")
IDENT = re.compile(r"[0-9a-f]{32}")
GROUP = re.compile(r"impact-[1-9][0-9]{0,18}")
VERDICTS = ("still_correct", "needs_update", "unrelated", "defer")
NATIVE = ("map", "body")


def matches(pattern, value):
    return isinstance(value, str) and bool(pattern.fullmatch(value))


def words(value, limit):
    return (isinstance(value, str) and len(value) <= limit and
            not any((ord(c) < 32 and c != "\n") or 127 <= ord(c) <= 159 or
                    0xd800 <= ord(c) <= 0xdfff for c in value))


def valid_context(context, group):
    if not isinstance(context, dict) or set(context) != {"label", "kind", "issue", "comment", "decisions"}:
        return False
    refs = context["decisions"]
    return (words(context["label"], 200) and context["kind"] in ("task", "step", "branch", "section", "source", "record")
            and type(context["issue"]) is int and context["issue"] > 0
            and type(context["comment"]) is int and context["comment"] >= 0
            and isinstance(refs, list) and 1 < len(refs) <= 129
            and all(isinstance(ref, dict) and set(ref) == {"issue", "comment"}
                    and all(type(v) is int and v > 0 for v in ref.values()) for ref in refs)
            and group == "impact-%d" % refs[0]["comment"])


def valid(event):
    if not isinstance(event, dict) or set(event) - {"context"} != {
            "id", "group", "target", "revision", "previous", "verdict", "action", "note", "draft", "receipt"}:
        return False
    if (not matches(IDENT, event["id"]) or not matches(GROUP, event["group"])
            or not all(matches(HASH, event[k]) for k in ("revision", "target"))
            or type(event["previous"]) is not int or event["previous"] < 0
            or not words(event["note"], 1500) or not words(event["draft"], 4000)):
        return False
    if "context" in event and not valid_context(event["context"], event["group"]):
        return False
    verdict, action, receipt = event["verdict"], event["action"], event["receipt"]
    if verdict in ("applying", "applied"):
        if action not in NATIVE or not event["previous"]:
            return False
        if verdict == "applying":
            return receipt is None
        if not isinstance(receipt, dict) or type(receipt.get("issue")) is not int or receipt["issue"] < 1:
            return False
        if action == "map":
            return set(receipt) == {"issue", "comment"} and type(receipt["comment"]) is int and receipt["comment"] > 0
        return set(receipt) == {"issue", "sha256"} and matches(HASH, receipt["sha256"])
    if verdict == "needs_update":
        return bool(event["draft"].strip()) and action in ("body", "map", "followup") and receipt is None
    return verdict in VERDICTS and action == "review" and not event["draft"] and receipt is None


def encode(repo, hub, event):
    if not valid(event):
        raise ValueError("Invalid decision review.")
    data = json.dumps({"repo": repo.lower(), "hub": hub, "event": event}, sort_keys=True, ensure_ascii=True)
    body = MARKER + "\n```json\n" + data + "\n```"
    if len(body) > 62000:
        raise ValueError("The review is too large for one hub comment.")
    return body


def replay(repo, hub, allowed, rows):
    """First immutable comment referencing the current winner advances that revision."""
    history, heads, seen = [], {}, set()
    allowed = {login.lower() for login in allowed}
    for row in rows:  # authoritative callers select immutable records in ascending comment ID
        try:
            if row["author"].lower() not in allowed:
                continue
            body = row["body"].replace("\r\n", "\n")
            prefix = MARKER + "\n```json\n"
            if len(body) > 62000 or not body.startswith(prefix) or not body.endswith("\n```"):
                continue
            data = json.loads(body[len(prefix):-4])
            if not isinstance(data, dict) or data.get("repo") != repo.lower() or type(data.get("hub")) is not int or data["hub"] != hub:
                continue
            event = data.get("event")
            if not valid(event) or event["id"] in seen:
                continue
            key = (event["group"], event["target"], event["revision"])
            prior = heads.get(key, {})
            if event["previous"] != prior.get("comment", 0):
                continue
            verdict = event["verdict"]
            if verdict in ("applying", "applied"):
                if (prior.get("verdict") != ("needs_update" if verdict == "applying" else "applying") or
                        any(event.get(k) != prior.get(k) for k in ("action", "draft", "note", "context"))):
                    continue
                if verdict == "applied" and row["author"].lower() != prior["reviewer"].lower():
                    continue
            elif prior.get("verdict") == "applying":
                # The claimant owns the in-flight write. Only that account may release an unconfirmed claim.
                if row["author"].lower() != prior["reviewer"].lower():
                    continue
            entry = dict(event, comment=row["id"], reviewer=row["author"], at=row["created_at"])
            history.append(entry)
            seen.add(entry["id"])
            heads[key] = entry
        except (ValueError, TypeError, AttributeError, KeyError, RecursionError):
            continue
    return history


def read(db, repo, hub, allowed):
    if not allowed or type(hub) is not int:
        return []
    local = db.execute("SELECT value FROM meta WHERE key='repo'").fetchone()
    if not local or local[0].lower() != repo.lower():
        return []
    rows = db.execute("SELECT c.id,c.body,c.author,c.created_at FROM comments c "
                      "JOIN comment_edit_reads r ON r.comment=c.id AND r.stamp=c.updated_at "
                      "WHERE c.issue=? AND c.created_at=c.updated_at AND r.complete=1 AND r.edits=0 "
                      "AND r.last_edited IS NULL AND c.body LIKE ? ORDER BY c.id", (hub, MARKER + "%"))
    return replay(repo, hub, allowed, rows)


def attach(groups, history):
    heads = {(event["group"], event["target"], event["revision"]): event for event in history}
    for group in groups:
        group["review_history"] = [event for event in history if event["group"] == group["id"]]
        present = {(group["id"], item["id"], item["revision"]) for item in group["items"]}
        group["pending_verifications"] = [event for key, event in heads.items()
                                          if event["group"] == group["id"] and key not in present
                                          and event["verdict"] == "applying"]
        for item in group["items"]:
            item["review"] = heads.get((group["id"], item["id"], item["revision"]))
    return groups
