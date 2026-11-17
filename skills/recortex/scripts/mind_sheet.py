"""The manuscript as a view of its own, for the mind page: the part of the model that tells the paper as a paper.

Python 3 standard library only. No network, no write. A mixin of mind_model.Build: it reads the index, the plan and the
versions this machine kept (the cache that mind.py --versions fills), and returns plain data for Page.sheet_view, which draws it
and derives nothing. Every sentence the page adds around the record's words is a fixed phrase here (PHRASES) or a fixed word of
the page; a count, a day or a version name is checked before it is told (versions.NAME for a name).

What it tells (references/mind.md, "The manuscript"):

  the text      each section of the deliverable's profile with its parts, a part as the rows of its blocks, in the order of
                the deliverable; a section that has no text yet but has planned parts is told too
  the plan      per part the states of its planned parts, per section how many are backed, and the planned parts that the
                text does not say yet (slots), each at the end of the part its plan names
  versions      the paper's versions numbered 1 to N, one per told text change with a kept commit, in time order; the page
                compares the current text with an earlier one. Each earlier text of a part is compared once, and the page
                shows the rows marked for the version the reader picked (the previous one by default)
  authorship    per paragraph who wrote it, from the versions kept on this machine; nothing is told without them

A part's text at a version is only ever read from a file whose hash is the "after" value of a told text change (version_text), or
is proven the live body by that hash. Nothing is fetched here.
"""

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mind_plan  # noqa: E402
import mind_text  # noqa: E402
import mind_versions  # noqa: E402
import record_format  # noqa: E402
import recortex  # noqa: E402
import versions  # noqa: E402
import write_rules  # noqa: E402

LISTED = 10          # the most recent earlier versions the picker offers; named older ones are offered too, up to NAMED
NAMED = 20           # the most named older versions the picker offers, the newest first; the rest are counted, not offered
SLOTS = 12           # slots shown in a section
PLAIN = ("autonomous", "chat", "merged")   # forms of a text change the plugin made

PHRASES = {
    # the head of the sheet and its bar
    "sheet.nav": "Read %s", "sheet.skip": "Skip to the text", "sheet.message": "What the paper sets out to say (from the plan)",
    "sheet.plan": "See the plan", "sheet.deliver": "The plan and the text", "sheet.print": "Print or save as PDF",
    "sheet.legend": "What the marks mean", "sheet.rh": "%s, as the record says on %s",
    "sheet.rh.changes": "The text with its changes since version %d",
    "sheet.read": "Read it as a paper", "sheet.dateline": "As the record says on %s",
    "sheet.hint": "Print or save as PDF prints the clean text. mind.py --paper writes the paper as a page of its own.",
    "leg.backed": "A planned part that a finding backs", "leg.against": "A finding speaks against it",
    "leg.planned": "Planned; nothing backs it yet", "leg.now": "Can be written or made now", "leg.open": "Still open",
    "leg.changed": "A paragraph that changed since the version chosen above",
    "leg.slot": "A dashed frame is a part that the plan names and the text does not say yet.",
    "sheet.view": "Show", "sheet.changes": "Changes since", "sheet.clean": "Clean copy",
    "sheet.prev": "Previous change", "sheet.next": "Next change",
    "sheet.version": "Version %d \u00b7 %s", "sheet.previous": " (previous)",
    # the line in the bar, and the fixed lines that stand where the picker is not
    "line.changed": "%s in %s since version %d, %s", "line.same": "Nothing changed since version %d, %s",
    "line.same.some": "Nothing changed since version %d, %s, in the parts compared",
    "line.clean": "The current text, with no marks",
    "line.changes": ("1 change", "%d changes"), "line.parts": ("1 part", "%d parts"),
    "line.before": ("1 part has no earlier version to compare", "%d parts have no earlier version to compare"),
    "line.not": ("1 part is not compared", "%d parts are not compared"),
    "line.first": "This is the first kept version of the text. Changes show here once it changes again.",
    "line.none": "No version of the text has been kept yet. Changes show here once it has changed.",
    "line.off": "Earlier versions of the text are kept only when recortex writes the paper for you, with your review or on its own.",
    "line.unchecked": "The earlier versions of the text could not be compared here, so no changes are marked.",
    "line.dropped": "The oldest versions are not offered: this page marks changes against the newest ones only.",
    "line.omitted": ("1 older named version is not offered: the picker holds the 10 latest versions and the 20 newest named ones.",
                     "%d older named versions are not offered: the picker holds the 10 latest versions and the 20 newest named ones."),
    "line.unfetched": "The text of earlier versions is not kept here for %s, so they show no marks; build the page again with "
                      "mind.py --versions.",
    # the marks of a part and a section
    "mark.backed.one": "1 planned part backed", "mark.backed": "%d planned parts backed",
    "mark.against.one": "A finding speaks against 1 planned part", "mark.against": "Findings speak against %d planned parts",
    "mark.moved.one": "1 planned part rests on something that changed",
    "mark.moved": "%d planned parts rest on something that changed",
    "mark.planned.one": "1 planned part: nothing backs it yet", "mark.planned": "%d planned parts: nothing backs them yet",
    "mark.now.one": "1 planned part can be made now", "mark.now": "%d planned parts can be made now",
    "mark.claim.one": "1 planned part can be written now", "mark.claim": "%d planned parts can be written now",
    "mark.changed.text": "The record moved on since this was written; it can be updated now",
    "mark.methods.text": "The record has decisions and findings that the text does not say yet",
    "mark.open.one": "1 open question", "mark.open": "%d open questions",
    "mark.failures.one": "1 failure nothing followed", "mark.failures": "%d failures nothing followed",
    "mark.see": "See the finding",
    "sec.plan": "%d of %d planned parts backed", "sec.none": "Nothing is planned for it", "sec.link": "The plan for the %s",
    "part.changed": "Last changed %s", "part.how": "How we got here", "part.history": "History (%d)", "part.history.days": "History",
    # a part against a version
    "since.same": "Same as version %d", "since.new": "New since version %d",
    "since.unfetched": "The text of version %d has not been fetched yet; build the page again with mind.py --versions.",
    "since.unchecked": "Its text at version %d could not be checked, so no changes are marked",
    "since.before": "This part has no kept version at or before version %d, so nothing is compared.",
    "since.long": "This part is long, so its changes are not marked.",
    "since.budget": "This page marks changes in the newest versions only, so this part is shown without marks.",
    "since.reworded": "Reworded since version %d", "since.added": "Added since version %d",
    "since.changed": "Changed since version %d", "since.removed": "A paragraph was removed here since version %d",
    "since.removed.part": "A part was removed here since version %d",
    "since.show": "Show what was removed", "since.then": "Show it as it was", "cell.was": " (was %s)",
    "count.reworded": ("1 paragraph reworded", "%d paragraphs reworded"), "count.added": ("1 added", "%d added"),
    "count.removed": ("1 removed", "%d removed"), "count.changed": ("1 changed", "%d changed"),
    "count.since": " since version %d",
    # who wrote a paragraph
    "who.auto": "Written by the plugin, without asking", "who.chat": "Written by the plugin, with your approval",
    "who.merged": "Written by the plugin, approved by you on GitHub", "who.restored": "Restored at your request",
    "who.hand": "You edited it by hand", "who.handnow": "You changed it by hand since the last kept version",
    "who.kept": "Your own words, edited by hand on %s; the plugin keeps them as you wrote them",
    "who.before": "In the text since before versions were kept",
    # what the plan says is not in the text yet
    "slot.label": "Planned for the %s and not in the text yet", "slot.kind": "Planned %s \u00b7 %s",
    "slot.kind.bare": "Planned \u00b7 %s",
    "slot.more": "and %d more in the plan", "slot.write": "write this part",
    "slot.margin": ("1 planned part is not in the text yet: %s", "%d planned parts are not in the text yet: %s"),
    "slot.request": "Write this part of the %s: planned part %d.",
    "tail.planned": "nothing backs it yet", "tail.failed": "an attempt to show it failed",
    "tail.waits": "waits for the part it follows", "tail.doubt": "a finding speaks against it",
    "tail.moved": "what it rested on has changed", "tail.can be made": "can be made now",
    "tail.backed.one": "backed by 1 finding", "tail.backed": "backed by %d findings", "tail.stands": "not written yet",
    "empty.line": "No text yet", "empty.plan": "No text yet. %s planned for it; %s backed.",
    "empty.are": ("1 part is", "%d parts are"), "empty.backed": ("none is", "1 is", "%d are"),
    "empty.draft": "To have it drafted, ask in the chat:", "empty.request": "Draft the %s from what is backed.",
    "copy": "Copy the request: %s", "copy.draft": "draft the %s",
    # the history box
    "history.title": "History of %s, newest first", "history.close": "Close the history",
    "history.restore": "Restore the %s of %s at %s.", "history.restore.day": "Restore the %s of %s.",
    "history.copy": "restore the %s of %s at %s", "history.copy.day": "restore the %s of %s",
    "history.how": ", the version changed %s", "history.version": "Version %d \u00b7 %s",
    # figures, tables and the sources
    "fig.missing": "The picture is not in this page: it was not kept on this machine when the page was built.",
    "src.title": "Where the numbers come from",
    "foot": "Written from the record by recortex. Every word is the record's own text.",
}


def article(word):
    return ("an " if word[:1] in "aeiou" else "a ") + word


def kinds_phrase(kinds):
    """"a claim, a figure and a table" for a list of part kinds (lower-case words of the plan's profile): a kind more than once
    is told with its count ("2 claims")."""
    counted = {}
    for kind in kinds:
        counted[kind] = counted.get(kind, 0) + 1
    pieces = [article(kind) if count == 1 else "%d %ss" % (count, kind) for kind, count in counted.items()]
    return pieces[0] if len(pieces) == 1 else ", ".join(pieces[:-1]) + " and " + pieces[-1] if pieces else ""


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SheetMixin:
    """Methods of Build that tell the manuscript view. They need the plan's reading (read_plan), the outline and the thread
    models; with no plan issue the view is the text alone."""

    # ------------------------------------------------------------ the versions of the paper, numbered

    def sheet_versions(self):
        """{"kept", "list", "count", "line", "dropped", "omitted"}: the numbered versions (one per told text change with a kept
        commit, in the order of the record) and, for the picker, the earlier ones: the LISTED most recent and the NAMED newest
        named ones older (`omitted` counts the named ones left out). `line` is the fixed line that stands where the picker is
        not."""
        found = {"kept": False, "rows": [], "list": [], "count": 0, "line": "off", "dropped": False, "omitted": 0,
                 "numbered": []}
        if self.plan_state is None or self.writing == "off":
            return found
        from mind_model import short  # noqa: E402
        names = self.version_names()
        rows = []
        for change in self.texts:
            if change["version"]:
                rows.append(dict(change, n=len(rows) + 1))
        named = {}
        for row in names:
            if versions.NAME.fullmatch(row["name"]):
                same = [version for version in rows if version["version"] == row["commit"]]
                if same:
                    named[same[-1]["n"]] = row   # a commit two changes share is named once, on the newer
        found.update(kept=True, rows=rows, count=len(rows), line="none" if not rows else "first" if len(rows) == 1 else None,
                     numbered=[{"n": v["n"], "id": v["id"], "name": named[v["n"]]["name"] if v["n"] in named else None}
                               for v in rows])
        earlier = list(reversed(rows[:-1]))
        older = [version for version in earlier[LISTED:] if version["n"] in named]
        keep = earlier[:LISTED] + older[:NAMED]
        found["omitted"] = len(older) - NAMED if len(older) > NAMED else 0
        for version in keep:
            row = named.get(version["n"])
            found["list"].append({"n": version["n"], "id": version["id"], "time": version["time"], "day": self.day_of(version["at"]),
                                  "previous": version["n"] == len(rows) - 1, "name": row["name"] if row else None,
                                  "words": short(row["words"]) if row else None, "changed": None})
        return found

    def text_at_version(self, topic, heading, version):
        """(body, state) of a topic as it stood at a numbered version: state "ok" with the body when the file of the newest told
        change of the topic up to the version is kept here and its hash is that change's "after" value; "same" with no body when
        no told change of the topic came after the version and the live body is, by its hash, what that change left (so nothing
        was changed since, and no file is needed); "unfetched", "unchecked" as version_text; "before" when no told change of the
        topic stands at or before the version."""
        if topic is None:
            return None, "unfetched"
        mine = [t for t in self.texts if t["issue"] == topic]
        upto = [t for t in mine if (t["time"], t["id"]) <= (version["time"], version["id"])]
        if not upto:
            return None, "before"
        newest = upto[-1]
        if len(upto) == len(mine) and newest["after"] and self.body_shown(topic) and not self.by_hand(topic, newest):
            return None, "same"
        if not newest["version"]:
            return None, "unchecked"
        return self.version_text(newest["version"], topic, newest["after"])

    # ------------------------------------------------------------ the model

    def sheet_model(self, outline, threads):
        """The model of the manuscript view, or None when no profile of the project has a plan."""
        profile = recortex.plan_profile(self.profiles)
        if profile is None:
            return None
        from mind_model import CAP_TEXT  # noqa: E402
        self.sheet_cap = CAP_TEXT
        noun = self.NOUNS.get(profile["profile"], (None, "the " + profile["noun"]))[1]
        by_n = {m["n"]: m for m in threads}
        group = next((g for g in outline["deliverables"] if g["profile"] == profile["profile"]), None)
        planned = self.plan_state is not None
        info = self.sheet_versions()
        due = write_rules.due_parts(self.db, self.config, self.allowed, self.plan_state) if planned else []
        due_ids = {cid for row in due for cid in row["parts"]}
        sections = self.sheet_sections(group, by_n, planned)
        ctx = {"cache": {}, "spent": 0, "unfetched": set(), "then_figures": [], "figures": set()}
        for section in sections:
            for part in section["parts"]:
                self.sheet_part(part, section, by_n)
        listed = self.sheet_compare(sections, info, ctx)
        for section in sections:
            for part in section["parts"]:
                if part["rows"] is not None:
                    self.sheet_merge(part, ctx)
                    self.sheet_blame_rows(part)
                part.pop("_results")
                part.pop("_blocks")
        if planned:
            self.sheet_plan(sections, by_n, due, due_ids)
        for section in sections:
            self.sheet_removed_parts(section, by_n, listed)
        self.sheet_totals(sections, info)
        comparable = any(entry["state"] in ("same", "marked", "new") for section in sections for part in section["parts"]
                         for entry in part["since"])
        seen, sources = set(), []
        for section in sections:
            for part in section["parts"]:
                n = part["thread"]
                if n in seen or n not in by_n:
                    continue
                seen.add(n)
                for row in by_n[n]["sources"]:
                    sources.append({"thread": n, "statement": row["statement"], "source": row["source"]})
        mode = "changes" if info["list"] and comparable else "plain"
        message = (self.plan or {}).get("message") if planned else None
        line = info["line"] if mode == "plain" else None
        if mode == "plain" and line is None and info["count"] > 1:
            line = "unchecked"   # two or more kept versions and no part could be compared with any of them
        return {
            "profile": profile["profile"], "noun": noun, "title": noun.capitalize(), "name": self.repo.split("/")[1],
            "plan": planned, "message": message["plain"] if message else None, "mode": mode,
            "versions": {"kept": info["kept"], "count": info["count"], "numbered": info["numbered"],
                         "list": info["list"] if mode == "changes" else [],
                         "default": info["list"][0]["n"] if mode == "changes" else None, "line": line,
                         "dropped": info["dropped"] and mode == "changes",
                         "omitted": info["omitted"] if mode == "changes" else 0},
            "unfetched": len(ctx["unfetched"]), "sections": sections, "sources": sources,
            "then_figures": ctx["then_figures"],
        }

    # ------------------------------------------------------------ sections and parts

    def sheet_sections(self, group, by_n, planned):
        """The sections of the sheet: those of the deliverable's profile in the order of the outline, and the sections of the plan
        that have planned parts and no text yet, each after the section that comes before it in the plan."""
        out = []
        for section in (group or {}).get("sections", []):
            out.append({"n": section["n"], "label": section["label"], "key": section["key"], "parts": [
                {"thread": p["thread"], "ordinal": p["ordinal"], "outline": p} for p in section["parts"]],
                        "text": False, "untold": False, "slots": None, "more": 0, "plan": None})
        if planned:
            order = [h for h in self.plan_state["plan"]["sections"]]
            have = {s["label"].lower() for s in out}
            for heading in order:
                if heading.lower() in have:
                    continue
                if not any((p["section"] or "").lower() == heading.lower() and p["standing"] and p["part"] != "message"
                           for p in self.plan_parts.values()):
                    continue
                before = [s for s in order[:order.index(heading)] if s.lower() in have]
                at = 0
                if before:
                    at = next(i for i, s in enumerate(out) if s["label"].lower() == before[-1].lower()) + 1
                out.insert(at, {"n": None, "label": heading, "key": None, "parts": [], "text": False, "untold": False,
                                "slots": None, "more": 0, "plan": None})
                have.add(heading.lower())
        return out

    def sheet_part(self, part, section, by_n):
        """The part's own data: its thread's name, whether its text is told, and one row for each block of its current text, which
        sheet_merge fills with what each version marks. The blocks are the ones mind_text reads from the section, which are
        the blocks of the thread model's doc, in the same order."""
        thread = by_n.get(part["thread"])
        ordinal = part["ordinal"]
        outline = part.pop("outline")
        doc = None
        if thread is not None and isinstance(ordinal, int) and 0 <= ordinal < len(thread["sections"]):
            doc = thread["sections"][ordinal]["doc"]
        shown = bool(thread and thread["shown"] and doc is not None)
        newest = self.newest_text(thread["topic"], section["label"]) if thread and thread["topic"] is not None else None
        last = {"day": self.day_of(newest["at"]), "how": newest["form"] if newest["form"] in mind_plan.HOW else None} if newest \
            else {"day": thread["text"]["changed"], "how": None} if thread and thread["text"] else None
        part.update(last=last, name=thread["name"] if thread else "", topic=thread["topic"] if thread else None, shown=shown,
                    heading=section["label"], long=bool(shown and doc["cut"]), empty=bool(shown and not doc["blocks"]),
                    rows=None, removed=[], since=[], marks=[], slots=None, planned=[], by_hand=bool(outline.get("by_hand")),
                    _results={}, _blocks=[])
        if not shown:
            section["untold"] = section["untold"] or thread is not None   # its text is there, and may not be told
            return
        section["text"] = section["text"] or not part["empty"]
        if part["long"]:
            part["rows"] = []
            return
        from mind_model import clean  # noqa: E402
        text = record_format.section_text(self.issues[part["topic"]]["body"], section["label"])
        blocks = mind_text.blocks(clean(text or "").strip("\n"))
        if len(blocks) != len(doc["blocks"]):
            part["long"] = True   # the body and the index differ: the part is drawn as it is, and nothing is compared
            part["rows"] = []
            return
        part["_blocks"] = blocks
        part["rows"] = [{"i": i, "added": [], "reworded": [], "changed": [], "marks": [], "then": [], "who": None, "day": None}
                        for i in range(len(blocks))]

    # ------------------------------------------------------------ the comparison of every part with each listed version

    def sheet_compare(self, sections, info, ctx):
        """Compare every part with each listed version, newest first, within MAX_SINCE_BYTES of marked variants. A version that
        does not fit is left out, with the older ones, and `info["dropped"]` says so; when even the newest does not fit, the parts
        that do not get the state "budget" and no marks. Returns the versions kept (also in info["list"]). A part's result for a
        version is {"state", "variant"}, kept in the part's `_results`; the variants are in ctx["cache"][id(part)]."""
        parts = [p for s in sections for p in s["parts"]]
        kept, limit = [], mind_versions.MAX_SINCE_BYTES
        for version in info["list"]:
            fresh, results = {}, {}
            for part in parts:
                results[id(part)] = self.sheet_result(part, version, ctx, fresh)
            cost = sum(v["size"] for per in fresh.values() for v in per.values())
            if ctx["spent"] + cost > limit:
                if kept:
                    info["dropped"] = True
                    break
                total = ctx["spent"]
                for part in parts:
                    added = sum(v["size"] for v in fresh.get(id(part), {}).values())
                    if added and total + added > limit:
                        results[id(part)] = {"state": "budget", "variant": None}
                        fresh.pop(id(part))
                    else:
                        total += added
                cost = total - ctx["spent"]
            for key, per in fresh.items():
                ctx["cache"].setdefault(key, {}).update(per)
            ctx["spent"] += cost
            for part in parts:
                part["_results"][version["n"]] = results[id(part)]
                if results[id(part)]["state"] == "unfetched":
                    ctx["unfetched"].add(id(part))
            kept.append(version)
        info["list"] = kept
        return kept

    def sheet_result(self, part, version, ctx, fresh):
        """The state of one part against one version: {"state", "variant"}. A variant is the rows of the part's current text marked
        against one earlier text; a text that an earlier version already had is compared once. A new one goes into
        `fresh[id(part)]`."""
        if not part["shown"] or part["rows"] is None:
            return {"state": "none", "variant": None}
        if part["long"]:
            return {"state": "long", "variant": None}
        body, state = self.text_at_version(part["topic"], part["heading"], version)
        if state == "same":
            return {"state": "same", "variant": None}
        if state != "ok":
            return {"state": state, "variant": None}
        text = record_format.section_text(body, part["heading"])
        then_blocks, then_entries, key = [], {}, "new"
        if text is not None:
            from mind_model import clean  # noqa: E402
            cleaned = clean(text).strip("\n")
            if len(cleaned) > self.sheet_cap:
                return {"state": "long", "variant": None}
            key = digest(cleaned)
            then_blocks, then_entries = mind_text.blocks(cleaned), self.sheet_entries(part["topic"], body)
        known = ctx["cache"].get(id(part), {})
        variant = known.get(key) or fresh.get(id(part), {}).get(key)
        if variant is None:
            now_entries = {(b["kind"], b["id"]): b for b in self.blocks_of(part["topic"]).values()}
            rows = mind_versions.since(then_blocks, part["_blocks"], then_entries, now_entries)
            if key == "new":
                rows = [dict(r, k="added") for r in rows if r["k"] != "removed"]
            same = all(r["k"] == "same" for r in rows)
            variant = {"rows": rows, "same": same, "size": 0 if same else mind_versions.since_size(rows, then_entries),
                       "entries": then_entries, "new": key == "new"}
            fresh.setdefault(id(part), {})[key] = variant
        return {"state": "same" if variant["same"] else "new" if variant["new"] else "marked", "variant": key}

    def sheet_entries(self, topic, body):
        """{(kind, id): entry} of the figures and tables of a body of an earlier version."""
        import mind_paper  # noqa: E402 - it imports mind_model, which imports this module
        memo = self.__dict__.setdefault("_sheet_entries", {})
        key = (topic, digest(body))
        if key not in memo:
            memo[key] = {(e["kind"], e["id"]): e for e in mind_paper.entries(body)}
        return memo[key]

    def sheet_since(self, part, cache):
        """The states of a part against the listed versions, grouped by what they say: [{"for": [versions, newest first], "state",
        "counts"}], the groups in the order of their newest version."""
        groups, order = {}, []
        for n in sorted(part["_results"], reverse=True):
            result = part["_results"][n]
            counts = None
            if result["variant"] is not None and result["state"] in ("marked", "new"):
                counts = mind_versions.counts(cache[result["variant"]]["rows"])
            key = (result["state"], tuple(sorted(counts.items())) if counts else None)
            if key not in groups:
                groups[key] = {"for": [], "state": result["state"], "counts": counts}
                order.append(key)
            groups[key]["for"].append(n)
        return [groups[key] for key in order]

    # ------------------------------------------------------------ the rows of a part, with what each version marks

    def sheet_merge(self, part, ctx):
        """Fold the variants of a part into its rows: each row says in which versions it is added, reworded or changed, with the
        marked phrases of a reworded paragraph and the earlier block of a changed one; the blocks that only an earlier text had are
        `removed`, each at the place of the next current block. Sets the grouped `since`."""
        cache = ctx["cache"].get(id(part), {})
        serves = {}
        for n, result in part["_results"].items():
            if result["variant"] is not None:
                serves.setdefault(result["variant"], []).append(n)
        removed = {}
        for key, fors in serves.items():
            variant = cache[key]
            before, run = 0, 0
            for item in variant["rows"]:
                kind = item["k"]
                if kind == "removed":
                    # each version keeps its own ordered run of removed blocks: a block is shared with another version only
                    # when its place, its turn in the run and the whole block (a figure or table with its entry) are the same
                    place = removed.setdefault((before, run, mind_versions.block_key(item["then"], variant["entries"])), {
                        "at": before, "turn": run, "for": [], "block": item["then"],
                        "entry": self.sheet_then(item["then"], variant["entries"], ctx)})
                    place["for"] += fors
                    run += 1
                    continue
                run = 0
                row = part["rows"][item["i"]]
                before = item["i"] + 1
                if kind in ("added", "reworded", "changed"):
                    row[kind] += fors
                if kind == "reworded":
                    mark = next((m for m in row["marks"] if m["runs"] == item["runs"]), None)
                    if mark is None:
                        mark = {"for": [], "runs": item["runs"]}
                        row["marks"].append(mark)
                    mark["for"] += fors
                elif kind == "changed":
                    same_key = mind_versions.block_key(item["then"], variant["entries"])
                    mark = next((m for m in row["then"] if m["key"] == same_key), None)
                    if mark is None:
                        mark = {"for": [], "key": same_key, "block": item["then"], "cells": item.get("cells"),
                                "entry": self.sheet_then(item["then"], variant["entries"], ctx)}
                        row["then"].append(mark)
                    mark["for"] += fors
        part["removed"] = sorted(removed.values(), key=lambda r: (r["at"], r["turn"], -max(r["for"])))
        for row in part["rows"]:
            for kind in ("added", "reworded", "changed"):
                row[kind] = sorted(set(row[kind]), reverse=True)
            for mark in row["marks"] + row["then"]:
                mark["for"] = sorted(set(mark["for"]), reverse=True)
                mark.pop("key", None)
        for place in part["removed"]:
            place["for"] = sorted(set(place["for"]), reverse=True)
        part["since"] = self.sheet_since(part, cache)

    def sheet_then(self, block, entries, ctx):
        """The figure or table entry that an earlier block points at, or None. A figure's picture is asked of the build."""
        if mind_versions.block_kind(block) != "block" or not isinstance(block["block"], dict):
            return None
        entry = entries.get((block["block"].get("kind"), block["block"].get("id")))
        if entry is not None and entry.get("kind") == "figure":
            where = (entry.get("repo"), entry.get("ref"), entry.get("path"))
            if where not in ctx["figures"]:
                ctx["figures"].add(where)
                ctx["then_figures"].append({"kind": "figure", "repo": entry.get("repo"), "ref": entry.get("ref"),
                                            "path": entry.get("path")})
        return entry

    # ------------------------------------------------------------ who wrote each paragraph

    def sheet_blame_rows(self, part):
        """Put the origin of each paragraph on its row: `who` (a key of the words "who.*") and `day`, on the first of the rows that
        share one, from the versions this machine kept; nothing is told when versions are not kept."""
        if self.writing == "off" or part["topic"] is None or not part["_blocks"]:
            return
        last = None
        for row, found in zip(part["rows"], self.sheet_blame(part["topic"], part["heading"], part["_blocks"])):
            key = found[0] if found else None
            if found is not None and key != last:
                row["who"], row["day"] = found
            last = key

    def sheet_blame(self, topic, heading, blocks):
        """[(key, day) or None] for each current block. Take the part's told text changes, newest first (at most
        mind_versions.MAX_PARTS), each with its checked file. The origin of a block is the oldest version of the unbroken run,
        counted back from the newest, that holds an equal block. The walk stops at the first version that cannot be read; the
        blocks still in the run there get no note. The key is one of auto, chat, merged, restored (the origin's form), hand (an
        edit by hand that no later change of the plugin to the part followed), kept (one it did), handnow (the newest kept version
        does not hold the block) and before (the run reaches the oldest kept version, which had a text before it)."""
        from mind_model import clean  # noqa: E402
        out = [None] * len(blocks)
        lower = heading.lower()
        every = [t for t in self.texts if t["issue"] == topic and lower in [h.lower() for h in t["sections"]]]
        changes = list(reversed(every))[:mind_versions.MAX_PARTS]
        now = [mind_versions.block_key(b) for b in blocks]
        origin, alive, stopped = [None] * len(blocks), set(range(len(blocks))), False
        for place, change in enumerate(changes):
            body, state = self.version_text(change["version"], topic, change["after"]) if change["version"] else (None, "unchecked")
            if state != "ok":
                stopped = True
                break
            text = record_format.section_text(body, heading)
            here = {mind_versions.block_key(b) for b in mind_text.blocks(clean(text or "").strip("\n"))}
            for i in list(alive):
                if now[i] in here:
                    origin[i] = place
                else:
                    alive.discard(i)
            if place == 0:
                for i in alive ^ set(range(len(blocks))):
                    out[i] = ("handnow", None)
            if not alive:
                break
        for i, place in enumerate(origin):
            if place is None or out[i] is not None or (stopped and i in alive):
                continue
            change, day = changes[place], self.day_of(changes[place]["at"])
            if i in alive and place == len(changes) - 1 and len(every) <= mind_versions.MAX_PARTS and change["before"]:
                out[i] = ("before", day)
            elif change["form"] in PLAIN or change["form"] == "restored":
                out[i] = ({"autonomous": "auto"}.get(change["form"], change["form"]), day)
            elif change["form"] == "edited":
                later = [c for c in changes[:place] if c["form"] in PLAIN]
                out[i] = ("kept" if later else "hand", day)
        return out

    # ------------------------------------------------------------ the plan: marks and slots

    def sheet_plan(self, sections, by_n, due, due_ids):
        """Per section how many planned parts are backed, per part the marks of its planned parts, and the slots: the planned parts
        that the text does not say yet, each at the end of the part its plan names (at the end of the section when it names none)."""
        write = {h.lower() for h in self.plan_state["plan"]["write"]}
        for section in sections:
            heading = section["label"]
            here = [p for p in self.plan_parts.values() if p["standing"] and p["part"] != "message"
                    and (p["section"] or "").lower() == heading.lower()]
            section["plan"] = {"planned": len(here), "backed": sum(1 for p in here if p["state"] in ("backed", "text"))}
            section["write"] = heading.lower() in write
            for part in section["parts"]:
                if part["topic"] is not None:
                    part["marks"] = self.sheet_marks(part, due, heading, by_n)
            open_slots = [p for p in here if not p["text"] and p["state"] != "text"]
            where = {}
            for index, part in enumerate(section["parts"]):
                where.setdefault(part["thread"], index)
            shown = open_slots[:SLOTS]
            lists = {}
            for slot in shown:
                index = where.get(slot["thread"]) if slot["thread"] is not None else None
                lists.setdefault(index, []).append(self.sheet_slot(slot, heading, due_ids))
            for index, items in lists.items():
                told = {"items": items, "margin": {"count": len(items), "kinds": kinds_phrase([i["part"] for i in items])}}
                if index is None:
                    section["slots"] = told
                else:
                    section["parts"][index]["slots"] = told
            section["more"] = len(open_slots) - len(shown)

    @staticmethod
    def sheet_slot(slot, heading, due_ids):
        backs = len(slot["backs"])
        if slot["state"] == "backed":
            tail = PHRASES["tail.backed.one"] if backs == 1 else PHRASES["tail.backed"] % backs
        else:
            tail = PHRASES.get("tail." + slot["state"], PHRASES["tail.stands"])
        return {"id": slot["id"], "part": slot["part"], "plain": slot["plain"], "state": slot["state"], "tail": tail,
                "css": slot["css"], "write": slot["id"] in due_ids, "heading": heading}

    def sheet_marks(self, part, due, heading, by_n):
        """The marks of a part: [{"k", "n", "text", "explain", "m"}] from the states of its planned parts, the text that is due and
        what is open on its thread; each is fixed words with a count."""
        topic, lower = part["topic"], heading.lower()
        here = [self.plan_parts[i] for i in self.planned_for_part(topic, heading)]
        part["planned"] = [p["id"] for p in here]
        marks = []

        def add(kind, count, one, many, explain=None, m=None):
            if count:
                marks.append({"k": kind, "n": count, "text": PHRASES[one] if count == 1 else PHRASES[many] % count,
                              "explain": explain, "m": m})

        against = [p for p in here if p["state"] == "doubt"]
        add("against", len(against), "mark.against.one", "mark.against", "state-plan-doubt",
            against[0]["against"][0] if against and against[0]["against"] else None)
        add("moved", sum(1 for p in here if p["state"] == "moved"), "mark.moved.one", "mark.moved", "state-plan-moved")
        add("planned", sum(1 for p in here if p["state"] in ("planned", "failed", "waits")), "mark.planned.one", "mark.planned",
            "state-plan-planned")
        add("now", sum(1 for p in here if p["state"] == "can be made"), "mark.now.one", "mark.now", "state-plan-can-be-made")
        add("backed", sum(1 for p in here if p["state"] in ("backed", "text")), "mark.backed.one", "mark.backed", "state-plan-backed")
        for row in due:
            if row["topic"] == topic and row["heading"].lower() == lower:
                if row["why"] == "claim":
                    add("now", len(row["parts"]), "mark.claim.one", "mark.claim", "state-plan-can-be-made")
                else:
                    add("now", 1, "mark.changed.text" if row["why"] == "changed" else "mark.methods.text",
                        "mark.changed.text", "state-plan-can-be-made")
        thread = by_n.get(part["thread"])
        text = thread["text"] if thread else None
        if text:
            questions, failures = text["questions"], text["failures"]
            add("open", len(questions), "mark.open.one", "mark.open", None, questions[0] if len(questions) == 1 else None)
            add("failures", len(failures), "mark.failures.one", "mark.failures")
        return marks

    # ------------------------------------------------------------ parts that were removed, and the totals

    def sheet_removed_parts(self, section, by_n, listed):
        """The parts of a section that an earlier text had and the topic's text no longer has (the heading is gone from the topic),
        each with the versions that held it. It needs the files of those versions, so only versions kept here are told."""
        section["gone"] = []
        if not listed:
            return
        for row in self.plan_rows:
            if row["heading"].lower() != section["label"].lower():
                continue
            for n in row["threads"]:
                thread = by_n.get(n)
                if thread is None or not thread["shown"] or thread["topic"] is None or \
                        any(p["thread"] == n for p in section["parts"]):
                    continue
                fors = []
                for version in listed:
                    body, state = self.text_at_version(thread["topic"], section["label"], version)
                    if state == "ok" and record_format.section_text(body, section["label"]):
                        fors.append(version["n"])
                if fors:
                    section["gone"].append({"thread": n, "name": thread["name"], "for": fors})

    @staticmethod
    def sheet_totals(sections, info):
        """Per listed version: how many changes in how many parts (the line under the bar), how many parts have no earlier
        version to compare ("before") and how many could not be compared for another reason (not kept here, not checked, too long,
        past the budget), so that a line that says nothing changed says it only of the parts compared."""
        for entry in info["list"]:
            n, changes, parts, before, other = entry["n"], 0, 0, 0, 0
            for section in sections:
                for part in section["parts"]:
                    if part["rows"] is None:
                        continue
                    here = sum(1 for row in part["rows"] if n in row["added"] or n in row["reworded"] or n in row["changed"]) \
                        + sum(1 for place in part["removed"] if n in place["for"])
                    changes += here
                    parts += 1 if here else 0
                    states = {group["state"] for group in part["since"] if n in group["for"]}
                    before += 1 if "before" in states else 0
                    other += 1 if states & {"unfetched", "unchecked", "long", "budget"} else 0
            entry["changed"] = {"changes": changes, "parts": parts, "before": before, "other": other}
