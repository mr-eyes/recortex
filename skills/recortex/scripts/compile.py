#!/usr/bin/env python3
"""Compile the deliverable and the dated history of the project from the record.

Read-only on GitHub. It first rebuilds the local index from GitHub (index.py
sync --rebuild), so that issues and comments deleted on GitHub are not
compiled; --incremental syncs only what changed (an incremental sync does not
see deletions) and --no-sync uses the index as it is. Then it writes, under --out:

  deliverable/NN-<label>.md   for each deliverable section, in the hub's order,
                              the text under that section's heading in every
                              topic body that carries the section label, in the
                              hub's topic order, each under a topic heading with
                              the issue link
  deliverable/figures.tsv     every figure block: id, issue, title, caption, image at its ref, data, command
  deliverable/tables.tsv      every table block, the same columns
  deliverable/sources.tsv     every row of every "Sources" section, with its topic
  deliverable/plan.md         the plan of the paper as it stands (only with a plan): each part with its state, the
  deliverable/plan.tsv        memories behind it, and the gaps; one row per part. No planned statement enters the
                              section files above
  history/timeline.tsv        every event comment of every issue in time order, with
  history/timeline.md         the issue events (opened, labeled, unlabeled, renamed,
                              closed, reopened, pinned) and body edits; a comment
                              that is not a typed event is kept with type "other"
  MANIFEST.json               repository, hub, time of compilation, counts, --ref; with a plan, its counts; with versions
                              kept, the branch's head commit and the names

Files of the same name are replaced; nothing else in --out is touched.

--version NAME compiles a named version of the paper instead of the record as it stands: the name must be one that a told
decision of an allowed account carries, its tag must point at the commit the decision names, and every topic file at that
commit must be the text the version's VERSION.json says; no body is read from the index, the plan files are left out and the
timeline stops at the decision. --layout manubot also writes content/ (the Manubot layout of the branch of versions) under
--out, from the same code that fills the branch.

Examples:
  compile.py --out .tmp/compiled --dry-run
  compile.py --out .tmp/compiled --ref v0.3.0
"""

import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recortex  # noqa: E402
import index  # noqa: E402
import record_format  # noqa: E402
import storage  # noqa: E402
import write_rules  # noqa: E402

TIMELINE_COLUMNS = ["at", "type", "issue", "issue_title", "labels", "title", "author", "url", "recorded", "reconstructed",
                    "comment", "plain", "links"]
BLOCK_COLUMNS = ["id", "issue", "title", "section", "caption", "image", "ref", "path", "data", "command"]
PLAN_COLUMNS = ["id", "part", "section", "topic", "block", "plain", "state", "agreed", "findings_for", "findings_against",
                "section_has_text", "text_changed", "newest_text"]
GAP_WORDS = {"unbacked": "claims nothing backs", "doubt": "parts with a finding against them",
             "moved": "parts resting on something that changed", "display": "figures and tables not made yet",
             "sections": "sections with no part and no text", "unused": "findings no part uses",
             "limits": "stated limits that are not planned", "comparisons": "comparisons that name no paper"}
GAP_LINES = {"unbacked": "Claims nothing backs", "doubt": "Parts with a finding against them",
             "moved": "Parts resting on something that changed", "display": "Figures and tables not made yet",
             "sections": "Sections with no part and no text", "unused": "Findings no part uses",
             "limits": "Stated limits that are not planned", "comparisons": "Comparisons that name no paper"}


def plan_files(db, config, repo, deliverable, dry_run):
    """The files of the plan (deliverable/plan.md and plan.tsv) and its counts, or ([], None) for a project with no plan or
    whose repository is not trusted. Only told memories and told bodies; the statements stay out of the section files."""
    allowed = recortex.allowed_users(repo)
    state = index.planned(db, config, allowed) if allowed else None
    if state is None:
        return [], None
    import plan as plan_module
    items, parts, profile = state["found"]["items"], state["parts"], state["plan"]
    gaps = index.gaps(db, config, allowed, state)
    told = index.told_bodies(db, allowed)
    texts = write_rules.text_memories(db, items)
    issues = {r["number"]: r for r in db.execute("SELECT number, title, url, last_edited_at, created_at FROM issues")}
    urls = {r["comment"]: r["url"] or "" for r in db.execute("SELECT comment, url FROM events")}
    order = ["All"] + list(profile["sections"])
    shown = {"All": "Message"}

    def link(cid):
        return "[%d](%s)" % (cid, urls.get(cid, "")) if urls.get(cid) else "%d" % cid

    def statement(cid):
        return plan_module.one_line(index.event_field(db, cid, "Statement"), 400) or plan_module.one_line(items[cid]["plain"] or "")

    def state_words(found):
        words = plan_module.STATE_WORDS[found["state"]]
        if found["state"] == "backed":
            words = "backed by %d finding%s" % (len(found["backs"]), "" if len(found["backs"]) == 1 else "s")
        return words

    rows, lines = [], ["# The plan of the %s" % profile["noun"], "",
                       "Compiled from %s: plan issue #%d. Each part with its state (computed from the links), whether the "
                       "author agreed it or the session added it, and the memories behind it. The text of the section "
                       "files holds none of this." % (repo, state["issue"]), ""]
    for heading in order:
        section = [(cid, found) for cid, found in sorted(parts.items(), key=lambda row: (items[row[0]]["time"], row[0]))
                   if found["standing"] and (found["section"] or "All") == heading]
        if not section:
            continue
        lines += ["## %s" % shown.get(heading, heading), ""]
        for cid, found in section:
            behind = [("plan comment", [cid]), ("findings for it", found["backs"]), ("findings against it", found["against"]),
                      ("decisions", found["decisions"]), ("rests on", [c for c in found["rests"] if c not in found["backs"]]),
                      ("in the text", [found["text"]] if found["text"] else [])]
            memories = "; ".join("%s: %s" % (name, ", ".join(link(c) for c in ids)) for name, ids in behind if ids)
            lines.append("- **%s** (topic %s): %s. %s%s. %s" % (
                found["part"], "#%d" % found["topic"] if found["topic"] else "none yet", statement(cid).rstrip("."),
                state_words(found).capitalize(), "" if found["agreed"] else "; added by the session", memories))
        lines.append("")
    dropped = [(cid, found) for cid, found in sorted(parts.items()) if not found["standing"]]
    if dropped:
        lines += ["## Dropped and revised parts", ""]
        lines += ["- %s %s: %s" % (found["part"], link(cid), "dropped" if found["dropped_by"] else "revised")
                  for cid, found in dropped] + [""]
    lines += ["## Gaps", ""]
    anything = False
    for name, words in GAP_WORDS.items():
        value = gaps[name]
        if value:
            anything = True
            lines.append("- %s: %d" % (GAP_LINES[name], len(value)))
    lines += ["" if anything else "None.", ""]
    for cid, found in sorted(parts.items(), key=lambda row: (items[row[0]]["time"], row[0])):
        topic = issues.get(found["topic"]) if found["topic"] else None
        has_text = bool(topic and found["topic"] in told and found["section"] and
                        (record_format.section_text(db.execute("SELECT body FROM issues WHERE number = ?",
                                                               (found["topic"],)).fetchone()["body"] or "", found["section"]) or "").strip())
        newest = write_rules.newest_text(texts, found["topic"]) if found["topic"] else None
        rows.append({"id": cid, "part": found["part"], "section": found["section"], "topic": found["topic"] or "",
                     "block": found["block"], "plain": items[cid]["plain"] or "", "state": found["state"],
                     "agreed": "yes" if found["agreed"] else "no", "findings_for": len(found["backs"]),
                     "findings_against": len(found["against"]), "section_has_text": "yes" if has_text else "no",
                     "text_changed": (topic["last_edited_at"] or topic["created_at"]) if has_text and topic else "",
                     "newest_text": newest["id"] if newest else ""})
    files = [os.path.join(deliverable, "plan.md"), os.path.join(deliverable, "plan.tsv")]
    if not dry_run:   # no name is written through a link
        recortex.write_working_files(deliverable, {"plan.md": "\n".join(lines).rstrip("\n") + "\n",
                                                   "plan.tsv": tsv_text(PLAN_COLUMNS, rows)})
    counts = index.plan_counts(db, config, allowed, state)
    return files, dict(counts, gap_classes={name: len(gaps[name]) for name in GAP_WORDS if gaps[name]})


def cell(value):
    return re.sub(r"[\t\r\n]+", " ", "" if value is None else str(value)).strip()


def tsv_text(columns, rows):
    return "\t".join(columns) + "\n" + "".join("\t".join(cell(row.get(c)) for c in columns) + "\n" for row in rows)


def write_tsv(path, columns, rows):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(tsv_text(columns, rows))


def kept_versions(db, config, repo):
    """{"branch", "head", "names"} for the manifest when versions are kept on this machine (a mode of review or autonomous): the
    names come from the told decisions in the index, the head from one read of GitHub, left out when it cannot be read.
    None otherwise."""
    try:
        import versions  # noqa: E402
        if not versions.kept_here(repo):
            return None
        allowed = recortex.allowed_users(repo)
        plan = recortex.plan_profile(recortex.shipped_profiles(config.get("profiles")))
        state, items = versions.told_items(db, config, allowed, plan)
        out = {"branch": versions.BRANCH, "names": [{"name": row["name"], "commit": row["commit"]}
                                                    for row in versions.named_versions(db, items)]}
        try:
            head = versions.head_of(repo)
        except versions.GitError:
            head = None
        if head:
            out["head"] = head
        return out
    except Exception:  # noqa: BLE001 - a manifest line is never worth a compile that fails
        return None


def build(db, config, repo, hub, out, ref=None, dry_run=False, bodies=None, cut=None, version=None, layout=None):
    """`layout` ([(label, heading, [issues])]) is the outline a named version was laid out in, from its own VERSION.json:
    without it the outline is the index's, as it is now."""
    profiles = recortex.shipped_profiles(config.get("profiles"))
    plan = layout if layout is not None else index.deliverable_order(db, hub, profiles)
    kept = bodies is not None   # a named version: the bodies are the version's, never the index's
    files = []
    issues = {r["number"]: r for r in db.execute("SELECT * FROM issues")}
    deliverable = os.path.join(out, "deliverable")
    history = os.path.join(out, "history")
    if not dry_run:
        os.makedirs(deliverable, exist_ok=True)
        os.makedirs(history, exist_ok=True)
    counts = {"sections": 0, "section_texts": 0, "figures": 0, "tables": 0, "sources": 0, "events": 0,
              "timeline_rows": 0, "other_comments": 0}
    for position, (label, heading, numbers) in enumerate(plan, 1):
        name = os.path.join(deliverable, "%02d-%s.md" % (position, re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")))
        files.append(name)
        parts = ["# %s" % heading, "", "Section label `%s`. Compiled from %s." % (label, repo), ""]
        for number in numbers:
            if kept and number not in bodies:
                continue
            issue = issues.get(number) if kept else issues[number]
            text = record_format.section_text(bodies[number] if kept else issue["body"] or "", heading)
            if text is None:
                text = "(The body of #%d has no '## %s' section, although the issue carries %s.)" % (number, heading, label)
            parts += ["## %s (#%d)" % (issue["title"] if issue else "Topic %d" % number, number), "",
                      (issue["url"] if issue else "") or "", "", text.strip(), ""]
            counts["section_texts"] += 1
        if not dry_run:
            with open(name, "w", encoding="utf-8") as handle:
                handle.write("\n".join(parts).rstrip("\n") + "\n")
        counts["sections"] += 1

    blocks = {"figure": [], "table": []}
    if kept:
        found, listed = [], []
        for number in sorted(bodies):
            for block in record_format.blocks(bodies[number]):
                found.append(dict(block, issue=number, image=block["image"], has_source=1 if block["source"] else 0))
            for ordinal, row in enumerate(record_format.sources(bodies[number])):
                listed.append({"issue": number, "ordinal": ordinal, "statement": row["statement"], "source": row["source"]})
        rows_of = lambda kind: [b for b in found if b["kind"] == kind]  # noqa: E731
    else:
        for row in db.execute("SELECT * FROM blocks ORDER BY issue"):
            blocks[row["kind"]].append(dict(row))
        rows_of = lambda kind: blocks[kind]  # noqa: E731
    for kind in ("figure", "table"):
        name = os.path.join(deliverable, "%ss.tsv" % kind)
        files.append(name)
        counts["%ss" % kind] = len(rows_of(kind))
        if not dry_run:
            write_tsv(name, BLOCK_COLUMNS, rows_of(kind))
    if kept:
        sources = [dict(r, issue_title=issues.get(r["issue"])["title"] if issues.get(r["issue"]) else "") for r in listed]
    else:
        sources = [dict(r, issue_title=issues.get(r["issue"])["title"] if issues.get(r["issue"]) else "")
                   for r in db.execute("SELECT * FROM sources ORDER BY issue, ordinal")]
    counts["sources"] = len(sources)
    files.append(os.path.join(deliverable, "sources.tsv"))
    if not dry_run:
        write_tsv(files[-1], ["issue", "issue_title", "statement", "source"], sources)

    plan_made, plan_counts = ([], None) if kept else plan_files(db, config, repo, deliverable, dry_run)
    files += plan_made
    rows = [dict(r, reconstructed="yes" if r["reconstructed"] else "no") for r in index.timeline(db)
            if cut is None or (r["at"] or "") <= cut]
    counts["timeline_rows"] = len(rows)
    counts["events"] = sum(1 for r in rows if r["type"] in record_format.EVENT_TYPES)
    counts["other_comments"] = sum(1 for r in rows if r["type"] == "other")
    files += [os.path.join(history, "timeline.tsv"), os.path.join(history, "timeline.md")]
    if not dry_run:
        write_tsv(files[-2], TIMELINE_COLUMNS, rows)
        lines = ["# Timeline of %s" % repo, "",
                 "Every comment and issue event in time order. Times are as recorded: the event time from a comment's "
                 "marker or heading, else the time GitHub stored. Type \"other\" is a comment that is not a typed event.",
                 "", "| Time | Type | Issue | Title | In plain words | Author | Labels |", "|---|---|---|---|---|---|---|"]
        for row in rows:
            lines.append("| %s | %s%s | [#%d](%s) | %s | %s | %s | %s |" % (
                cell(row["at"]), row["type"], " (reconstructed)" if row["reconstructed"] == "yes" else "", row["issue"],
                cell(row["url"]), cell(row["title"]).replace("|", "\\|"), cell(row["plain"]).replace("|", "\\|"),
                cell(row["author"]), cell(row["labels"]).replace("|", "\\|")))
        with open(files[-1], "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    manifest = {"repository": repo, "hub": hub, "ref": ref,
                "compiled_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "index_synced_at": index.get_meta(db, "synced_at"), "counts": counts,
                "files": [os.path.relpath(f, out) for f in files]}
    if plan_counts is not None:
        manifest["plan"] = plan_counts
    if version is not None:
        manifest["version"] = version
    elif plan_counts is not None:
        kept = kept_versions(db, config, repo)
        if kept is not None:
            manifest["versions"] = kept
    files.append(os.path.join(out, "MANIFEST.json"))
    if not dry_run:
        with open(files[-1], "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
            handle.write("\n")
    counts["plan"] = plan_counts
    return files, counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", help="owner/name (default: from .recortex.json)")
    parser.add_argument("--out", required=True, help="folder for the compiled files")
    parser.add_argument("--ref", help="git ref of the project the compilation belongs to, recorded in MANIFEST.json")
    parser.add_argument("--no-sync", action="store_true", help="use the index as it is, without reading GitHub")
    parser.add_argument("--incremental", action="store_true",
                        help="sync only what changed instead of rebuilding; deletions on GitHub are then not seen")
    parser.add_argument("--version", help="compile this named version of the paper (see the top of this file)")
    parser.add_argument("--layout", choices=("manubot",), help="also write content/, the Manubot layout, under --out")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be read and written; read nothing from GitHub, write nothing")
    args = parser.parse_args(argv)

    config = recortex.load_config()
    repo = recortex.resolve_repo(args.repo, config)
    hub = config.get("hub") if config.get("repo", "").lower() == repo.lower() else None
    print(recortex.describe(config, repo))
    if args.dry_run:
        if not args.no_sync:
            print("would read (index.py sync%s): gh api --paginate repos/%s/issues?state=all, "
                  "repos/%s/issues/comments, the timeline of each changed issue, and their body edits"
                  % ("" if args.incremental else " --rebuild", repo, repo))
        db = index.connect(repo, create=False)
        if db is None:
            print("would write: %s/deliverable/<NN>-<label>.md per section, figures.tsv, tables.tsv, sources.tsv, "
                  "history/timeline.tsv, history/timeline.md, MANIFEST.json (no index yet to list the sections)" % args.out)
            return 0
        files, counts = build(db, config, repo, hub, args.out, args.ref, dry_run=True)
        print("from the index synced %s:" % index.get_meta(db, "synced_at", "never"))
        for name in files:
            print("would write: %s" % name)
        return 0
    if not args.no_sync:
        summary = index.sync(repo, hub, rebuild=not args.incremental, deadline=3600)
        print("synced: %d issues, %d comments, %d timelines changed" % (summary["issues"], summary["comments"],
                                                                         summary["timelines"]))
    db = index.connect(repo, create=False)
    if db is None:
        sys.exit("error: no index for %s; run without --no-sync" % repo)
    bodies = cut = version = layout_bodies = layout_meta = layout = None
    if args.version:
        import versions  # noqa: E402
        allowed = recortex.allowed_users(repo)
        plan = recortex.plan_profile(recortex.shipped_profiles(config.get("profiles")))
        if not allowed or plan is None:
            sys.exit("compile: this project has no plan or is not in your trusted repositories, so no version can be vouched for")
        try:
            bodies, decision, authors, keywords, layout = versions.vouched_version(config, repo, db, allowed, plan, args.version)
        except versions.NotVouched as error:
            sys.exit(str(error))
        if layout is None:
            print("compile: this version was kept before its sections were recorded, so they follow the outline as it is now.")
        cut = decision["time"]
        version = {"name": args.version, "commit": decision["commit"], "manifest_sha256": decision["manifest"],
                   "decision": decision["id"]}
        layout_bodies, layout_meta = bodies, (authors, keywords)
    files, counts = build(db, config, repo, hub, args.out, args.ref, bodies=bodies, cut=cut, version=version, layout=layout)
    if args.layout:
        import versions  # noqa: E402
        allowed = recortex.allowed_users(repo)
        plan = recortex.plan_profile(recortex.shipped_profiles(config.get("profiles")))
        if layout_bodies is None:
            layout_bodies = versions.paper_bodies(db, config, repo, hub, allowed, plan, {})[0]
        laid = versions.layout_files(db, config, repo, hub, allowed, plan, layout_bodies, layout_meta, order=layout)
        content = {path[len("content/"):]: text for path, text in sorted(laid.items()) if path.startswith("content/")}
        recortex.write_working_files(os.path.join(args.out, "content"), content)   # no name is written through a link
        files += [os.path.join(args.out, "content", name) for name in sorted(content)]
    for name in files:
        print("wrote: %s" % name)
    print("sections %(sections)d (texts %(section_texts)d), figures %(figures)d, tables %(tables)d, sources %(sources)d, "
          "typed events %(events)d, other comments %(other_comments)d, timeline rows %(timeline_rows)d" % counts)
    for name, count in ((counts.get("plan") or {}).get("gap_classes") or {}).items():
        print("plan gap %s: %d (%s)" % (name, count, GAP_WORDS[name]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (recortex.GhError, storage.StorageError, index.SyncBusy) as error:
        sys.exit("error: %s" % error)
