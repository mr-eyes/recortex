"""Private per-project handoff preferences. Only fixed style names enter durable state."""

import argparse
import json
import os
import sqlite3
import sys

import field_sheet
import recortex
import storage


STYLES = ("standard", "short", "detailed")
HINTS = {
    "short": "The user prefers short handoffs here. Give up to three brief points, including any open judgment and its source.",
    "detailed": "The user prefers detailed handoffs here. Include the reason, remaining uncertainty and the source of the next step.",
}
DEFAULT = {"style": "standard", "previous": "", "offered": []}


def _path(repo, create=False):
    if not recortex.valid_repo(repo):
        raise ValueError("invalid repository")
    folder = storage.folder(storage.state_top(), storage.repo_folder(repo.lower()), create=create, tighten=create)
    return os.path.join(folder, "handoff-style.json") if folder else None


def _read(repo):
    path = _path(repo)
    raw = storage.read_file(path) if path else None
    if raw is None:
        return dict(DEFAULT, offered=[])
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != set(DEFAULT) or value["style"] not in STYLES
            or value["previous"] not in ("", "short", "detailed") or not isinstance(value["offered"], list)
            or len(value["offered"]) > 2 or any(s not in HINTS for s in value["offered"])):
        raise ValueError("invalid handoff preference")
    return value


def hint(repo):
    """The session hook only reads. A missing or unavailable preference leaves its ordinary brief usable."""
    try:
        return HINTS.get(_read(repo)["style"], "")
    except (OSError, ValueError, TypeError, storage.StorageError):
        return ""


def change(repo, action, style):
    if action not in ("set", "once") or style not in (STYLES if action == "set" else HINTS):
        raise ValueError("invalid handoff choice")
    with field_sheet.approvals_lock(storage.state_top(), name="handoff-style.lock"):
        value = _read(repo)
        offer = False
        if action == "set":
            value = dict(DEFAULT, style=style, offered=[])
        else:
            offer = value["previous"] == style and style != value["style"] and style not in value["offered"]
            value["previous"] = style
            if offer:
                value["offered"].append(style)
        storage.write_file(_path(repo, create=True), json.dumps(value))
    return {"style": value["style"], "offer": offer}


def return_pointer(db, repo, allowed):
    """One outstanding judgment or accepted follow-up on an explicitly linked active task. Quoted record text stays inside its data frame."""
    import decision_impact
    try:
        active = {row[0] for row in db.execute("SELECT issue FROM labels WHERE label='active'")}
        if not active or not next(iter(db.execute("SELECT 1 FROM links WHERE kind='replaces' LIMIT 1")), None):
            return ""
        result = decision_impact.from_index(db, repo, allowed)
        candidates = []
        for group in result["groups"]:
            if group["conflict"] or len(group["successors"]) != 1:
                continue
            for item in group["items"]:
                verdict = (item.get("review") or {}).get("verdict")
                if (item["kind"] in ("step", "task") and item["issue"] in active and item["relation"] == "direct"
                        and verdict in (None, "needs_update", "applying")):
                    candidates.append((group, item, verdict))
        if candidates:
            group, item, verdict = max(candidates, key=lambda c: (bool(c[2]), c[1]["kind"] == "step"))
            lead = {None: "Open judgment: does the agreed work on #%d still apply after the decision changed?",
                    "needs_update": "An update to the work on #%d was requested. Pick up its saved follow-up.",
                    "applying": "An update to the work on #%d awaits verification. Check its receipt."}[verdict]
            return ((lead % item["issue"]) + " Earlier: %s Current: %s Work: %s. Read guidance.py context %s %s "
                    "to describe the choices and next move." % (group["old"]["url"], group["successors"][0]["url"],
                                                               item["url"], group["id"], item["id"]))
    except (OSError, ValueError, sqlite3.Error, storage.StorageError):
        pass
    return ""


def context(repo, config, allowed, group_id, item_id):
    import decision_impact
    import decision_review
    import index

    if not (decision_review.matches(decision_review.GROUP, group_id)
            and decision_review.matches(decision_review.HASH, item_id)):
        raise ValueError("invalid review reference")
    db = index.connect(repo, create=False, tighten=False)
    if db is None:
        return {"available": False, "reason": "No local index. Refresh the project record."}
    try:
        enabled = decision_impact.suggestions_enabled(repo, config)
        result = decision_impact.from_index(db, repo, allowed, suggestions=enabled)
    finally:
        db.close()
    for group in result["groups"]:
        if group["id"] == group_id:
            for item in group["items"]:
                if item["id"] == item_id:
                    return {"available": True, "earlier": group["old"], "current": group["successors"],
                            "conflict": group["conflict"], "target": item, "coverage": result["coverage"]}
    return {"available": False, "reason": "This item is absent from the current trusted review.",
            "coverage": result["coverage"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("show")
    for name, choices in (("once", HINTS), ("set", STYLES)):
        commands.add_parser(name).add_argument("style", choices=choices)
    read = commands.add_parser("context")
    read.add_argument("group")
    read.add_argument("item")
    args = parser.parse_args()
    try:
        config = recortex.load_config()
        repo = config.get("repo")
        allowed = recortex.allowed_users(repo) if recortex.valid_repo(repo) else None
        if not allowed:
            parser.exit(1, "A trusted configured project is required.\n")
        if args.action == "context":
            result = context(repo, config, allowed, args.group, args.item)
        elif args.action == "show":
            result = {"style": _read(repo)["style"]}
        else:
            result = change(repo, args.action, args.style)
    except (ValueError, TypeError, OSError, sqlite3.Error, storage.StorageError):
        parser.exit(1, "Project guidance unavailable.\n")
    print(json.dumps(result))


if __name__ == "__main__":
    sys.exit(main())
