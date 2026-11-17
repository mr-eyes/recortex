"""The plan and the changes of the text, for the mind page: the part of the model that tells what the paper will claim, how
each planned part stands, which parts of the text the plugin or the author changed and when.

Python 3 standard library only. No network, no write. A mixin of mind_model.Build: every sentence, state and count comes
from here, as fixed words (PHRASES) around the record's own words, and the page derives nothing. A planned statement is
record text: it reaches the page as a run of text and is drawn as text only (through E()). Only what the index tells is
here: told memories (index.memories), told bodies (index.body_told), the plan's rules (plan_rules) and the told text
changes (write_rules). The page shows a version's text only after its hash matched a told memory; that is mind_versions.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import index  # noqa: E402
import mind_versions  # noqa: E402
import plan_rules  # noqa: E402
import recortex  # noqa: E402
import record_format  # noqa: E402
import storage  # noqa: E402
import write_rules  # noqa: E402

PHRASES = {
    "kind.plan": "Planned", "kind.text": "Text change",
    "lead.text": "On %s we changed the text to say that ",
    "old.text": "On %s we changed the text: ",
    "thread.plan": "The plan of the paper",
    # the states of a planned part (references/planning.md); the chip words
    "plan.dropped": "Dropped on %s", "plan.revised": "Revised on %s", "plan.doubt": "A finding speaks against it",
    "plan.moved": "What it rested on has changed", "plan.waits": "Waits for the part it follows",
    "plan.text": "In the text", "plan.text.since": "In the text since %s", "plan.can be made": "Can be made now",
    "plan.backed.one": "Backed by 1 finding", "plan.backed": "Backed by %d findings",
    "plan.failed": "An attempt to show it failed", "plan.planned": "Planned; nothing backs it yet", "plan.stands": "Stands",
    "plan.agreed": "Agreed on %s", "plan.added": "Added by the session",
    "plan.not.text": "Not in the text yet", "plan.before": ", before that finding",
    "plan.for.one": "1 finding for it", "plan.for": "%d findings for it", "plan.none.for": "Nothing backs it yet",
    "plan.against.on": "A finding speaks against it (%s)",
    "plan.table.head": ("Planned", "Evidence", "Text"),
    "plan.here": "What we plan to say here",
    "plan.summary": "The plan against the text",
    "plan.message": "The message of the paper",
    # how a change of the text came about, from the fixed form of its Approved field
    "how.autonomous": "by the plugin without asking", "how.chat": "by the plugin with your approval",
    "how.merged": "by the plugin, approved by you on GitHub", "how.edited": "by you by hand",
    "how.restored": "by the plugin, restoring an earlier version at your request",
    "how.kept": "by the plugin; this file was kept after the first try was refused", "how.unknown": "in a way this page cannot tell",
    "hist.days": "Versions with their differences are kept once you choose review or autonomous writing.",
    "hist.first": "This is the first version kept.",
    "hist.restore": "To go back to a version, ask in the chat, for example: restore the Results of %s.",
    "hist.nodiff.days": "Only the day the text changed is known here.",
    "hist.nodiff.unfetched": "The text of this version has not been fetched yet; build the page again with --sync.",
    "hist.nodiff.unchecked": "The text of this version could not be checked, so it is not shown.",
    "hist.nodiff.before": "The text before this change is not kept as a version, so no difference is shown.",
    "hist.nodiff.budget": "This page shows the differences of the newest versions only; older ones keep their entries.",
    "hist.changed": "What changed",
    "diff.same.one": "1 paragraph is the same", "diff.same": "%d paragraphs are the same",
    "diff.cut": "The difference goes on; it is cut here.",
    "ver.title": "Versions of the paper", "ver.none": "No version of the paper has been named yet.",
    "ver.named": "Named on %s.", "ver.since": "What changed since %s",
    "ver.same.one": "1 part did not change.", "ver.same": "%d parts did not change.",
    "ver.unfetched": "The text of some versions has not been fetched yet; build the page again with --sync.",
}

STATE_CSS = {"dropped": "aside", "revised": "replaced", "doubt": "open", "moved": "open", "waits": "open",
             "text": "stands", "can be made": "taken", "backed": "stands", "failed": "open", "planned": "open",
             "stands": "plain"}
EXPLAIN_KEY = {state: "state-plan-" + state.replace(" ", "-") for state in plan_rules.STATES}
EVIDENCE_PARTS = ("claim",)
HOW = ("autonomous", "chat", "merged", "edited", "restored", "kept")
CAP_ROWS = 60


class PlanMixin:
    """Methods of Build that tell the plan. They need `self.config` (None: nothing is told, and the model is what it was
    before the plan existed)."""

    # ------------------------------------------------------------ reading

    def read_plan(self, found):
        """Fill self.plan_state (index.planned, or None), self.plan_issue, self.texts (the told text changes) and
        self.writing (the mode of this machine, off when none was chosen)."""
        self.plan_state, self.plan_issue, self.texts, self.writing = None, None, [], "off"
        if not self.config:
            return
        state = index.planned(self.db, self.config, self.allowed, found=found)
        self.plan_state = state
        self.plan_issue = state["issue"] if state else None
        if state is not None:
            self.texts = write_rules.text_memories(self.db, found["items"])
        import writing
        self.writing = writing.mode(self.repo)

    def plan_part_day(self, cid):
        return self.day_of(self.plan_state["found"]["items"][cid]["at"])

    def day_of(self, at):
        from mind_model import day_words  # noqa: E402 - the model's own words for a day
        return day_words(at, self.years)

    def full(self, at):
        from mind_model import full_day  # noqa: E402
        return full_day(at)

    def sentence_words(self, cid):
        """The plain line of a planned part, as record text capped like every other line of the model."""
        from mind_model import short  # noqa: E402
        item = self.plan_state["found"]["items"][cid]
        return short(item["plain"] or item["title"] or "").rstrip(".").strip()

    def page_ids(self, ids):
        """The ids among `ids` that are memories the page tells."""
        return [cid for cid in ids if cid in self.m]

    # ------------------------------------------------------------ states and chips

    def state_chip(self, cid, found):
        """(words, css, explain key) of a planned part's state, from fixed words."""
        state = found["state"]
        if state == "dropped":
            by = self.plan_state["found"]["items"].get(found["dropped_by"][0]) if found["dropped_by"] else None
            words = PHRASES["plan.dropped"] % (self.day_of(by["at"]) if by else "an unknown day")
        elif state == "revised":
            by = self.plan_state["found"]["items"].get(found["revised_by"][0]) if found["revised_by"] else None
            words = PHRASES["plan.revised"] % (self.day_of(by["at"]) if by else "an unknown day")
        elif state == "backed":
            words = PHRASES["plan.backed.one"] if len(found["backs"]) == 1 else PHRASES["plan.backed"] % len(found["backs"])
        elif state == "text":
            text = self.plan_state["found"]["items"].get(found["text"]) if found["text"] else None
            words = PHRASES["plan.text.since"] % self.day_of(text["at"]) if text else PHRASES["plan.text"]
        else:
            words = PHRASES["plan." + state]
        return words, STATE_CSS[state], EXPLAIN_KEY[state]

    def agreed_day(self, found, items):
        """The day the author agreed a part: the day of the decision that adopted the plan, which is a decision on the
        plan issue that the part links, and the earliest such decision when there are several. A part that links no
        decision there takes the day of the earliest decision it links. None when it links none."""
        decisions = found["decisions"]
        adopting = [d for d in decisions if items[d]["issue"] == self.plan_state["issue"]]
        chosen = adopting or decisions
        return self.day_of(items[min(chosen, key=lambda d: (items[d]["time"], d))]["at"]) if chosen else None

    def part_model(self, cid, found):
        """One planned part for the page: its words, its state, who agreed it and what bears on it."""
        items = self.plan_state["found"]["items"]
        words, css, explain = self.state_chip(cid, found)
        topic = found["topic"]
        thread = self.topic_thread.get(topic) if topic is not None else None
        text = items.get(found["text"]) if found["text"] else None
        newest_against = max([items[a]["time"] for a in found["against"]], default="")
        return {
            "id": cid, "part": found["part"], "section": found["section"], "thread": thread["n"] if thread else None,
            "block": found["block"], "agreed": found["agreed"], "standing": found["standing"], "state": found["state"],
            "chip": words, "css": css, "explain": explain, "plain": self.sentence_words(cid),
            "day": self.plan_part_day(cid), "issue": items[cid]["issue"],
            "agreed_day": self.agreed_day(found, items),
            "backs": self.page_ids(found["backs"]), "against": self.page_ids(found["against"]),
            "against_days": [self.day_of(items[a]["at"]) for a in found["against"] if a in self.m],
            "rests": self.page_ids(found["rests"]), "waits_for": found["waits_for"],
            "text": found["text"], "text_day": self.day_of(text["at"]) if text else None,
            "before_finding": bool(text and newest_against and newest_against > text["time"]),
        }

    # ------------------------------------------------------------ the plan of the model

    def plan_model(self):
        """{"issue", "parts", "summary", "message", "gaps"} or None for a project with no plan."""
        if self.plan_state is None:
            return None
        parts = [self.part_model(cid, found) for cid, found in sorted(
            self.plan_state["parts"].items(), key=lambda row: (self.plan_state["found"]["items"][row[0]]["time"], row[0]))]
        self.plan_parts = {p["id"]: p for p in parts}
        standing = [p for p in parts if p["standing"]]
        message = next((p for p in reversed(standing) if p["part"] == "message"), None)
        sections = {}
        for entry in self.outline_rows():
            sections[entry["heading"].lower()] = entry
        summary = []
        for heading in self.plan_state["plan"]["sections"]:
            here = [p for p in standing if (p["section"] or "").lower() == heading.lower() and p["part"] != "message"]
            if not here and heading.lower() not in sections:
                continue
            summary.append({"section": heading, "planned": len(here),
                            "backed": sum(1 for p in here if p["state"] in ("backed", "text")),
                            "doubt": sum(1 for p in here if p["state"] in ("doubt", "moved")),
                            "has_text": heading.lower() in sections})
        gaps = index.gaps(self.db, self.config, self.allowed, self.plan_state)
        return {"issue": self.plan_issue, "parts": parts, "summary": summary, "message": message,
                "gaps": {name: len(value) for name, value in gaps.items()}, "noun": self.plan_state["plan"]["noun"]}

    def outline_rows(self):
        """[{"heading", "n"}] of the sections the outline will hold (self.plan_rows is read before the outline model)."""
        return [{"heading": row["heading"]} for row in self.plan_rows]

    # ------------------------------------------------------------ the block before the parts of a section

    def plan_before(self, heading):
        """The doc of the block "What we plan to say here" under a section heading (None when no part is planned for it):
        a heading and a table, one row per planned part, three cells, each a list of segments the page draws as a
        sentence link or fixed words."""
        if self.plan_state is None:
            return None
        rows = []
        for part in self.plan_parts.values():
            if not part["standing"] or part["part"] == "message" or (part["section"] or "").lower() != heading.lower():
                continue
            rows.append(self.plan_row(part))
            if len(rows) >= CAP_ROWS:
                break
        if not rows:
            return None
        return {"blocks": [{"h": {"level": 1, "r": [{"t": PHRASES["plan.here"]}]}}, {"plan": {"rows": rows}}]}

    def plan_row(self, part):
        agreed = PHRASES["plan.agreed"] % part["agreed_day"] if part["agreed"] and part["agreed_day"] else \
            PHRASES["plan.added"] if not part["agreed"] else PHRASES["plan.agreed"] % part["day"]
        first = [{"gh": part["id"], "issue": part["issue"], "t": "%s: %s" % (part["part"].capitalize(), part["plain"])},
                 {"t": ". " + agreed}]
        second = []
        if part["against"]:
            second.append({"m": part["against"][0], "t": PHRASES["plan.against.on"] % part["against_days"][0]})
            if part["backs"]:
                second += [{"t": ". "}, {"m": part["backs"][0], "t": PHRASES["plan.for.one"] if len(part["backs"]) == 1
                                         else PHRASES["plan.for"] % len(part["backs"])}]
        elif part["backs"]:
            second.append({"m": part["backs"][0], "t": part["chip"]})
        else:
            second.append({"t": part["chip"]})
        third = []
        if part["text"] and part["text"] in self.m:
            third.append({"m": part["text"], "t": PHRASES["plan.text.since"] % part["text_day"]})
            if part["before_finding"]:
                third.append({"t": PHRASES["plan.before"]})
        elif part["state"] == "text":
            third.append({"t": PHRASES["plan.text"]})
        else:
            third.append({"t": PHRASES["plan.not.text"]})
        return {"cells": [first, second, third], "state": part["css"], "explain": part["explain"]}

    # ------------------------------------------------------------ what a part of the text says about itself

    def planned_for_part(self, topic, heading):
        """The ids of the standing parts planned for a topic and a heading."""
        if self.plan_state is None or topic is None:
            return []
        return [p["id"] for p in self.plan_parts.values() if p["standing"] and p["part"] != "message"
                and p["section"].lower() == heading.lower() and p["thread"] == self.topic_thread.get(topic, {}).get("n")]

    def part_plan_sentences(self, topic, heading):
        """The sentences a part's status line gains (a list of {"key", "segs"}): the planned parts that belong here, the
        newest change of the text and whether the author changed it by hand after that."""
        out = []
        if self.plan_state is None:
            return out
        here = [self.plan_parts[i] for i in self.planned_for_part(topic, heading)]
        if here:
            n_back = sum(1 for p in here if p["state"] in ("backed", "text"))
            n_doubt = sum(1 for p in here if p["state"] == "doubt")
            n_moved = sum(1 for p in here if p["state"] == "moved")
            n_open = sum(1 for p in here if p["state"] in ("planned", "failed", "waits", "can be made"))
            said = []
            if n_back:
                said.append("%d backed" % n_back)
            if n_doubt:
                said.append("%d with a finding against it" % n_doubt)
            if n_moved:
                said.append("%d resting on something that changed" % n_moved)
            if n_open:
                said.append("%d not backed yet" % n_open)
            out.append({"key": "planned", "segs": [{"t": "%d planned part%s belong%s here%s." % (
                len(here), "" if len(here) == 1 else "s", "s" if len(here) == 1 else "",
                ": " + ", ".join(said) if said else "")}]})
        newest = self.newest_text(topic, heading)
        if newest is not None:
            how = PHRASES["how." + newest["form"]] if newest["form"] in HOW else PHRASES["how.unknown"]
            out.append({"key": "textchange", "segs": [{"t": "Last changed on %s, %s. " % (self.full(newest["at"]), how)},
                                                      {"m": newest["id"], "t": "See what changed."}]
                        if newest["id"] in self.m else [{"t": "Last changed on %s, %s." % (self.full(newest["at"]), how)}]})
            if self.by_hand(topic, newest):
                out.append({"key": "byhand", "segs": [{"t": "You changed it by hand after that."}]})
        return out

    def newest_text(self, topic, heading):
        """The newest told text change of a topic that names `heading`, or None."""
        found = [t for t in self.texts if t["issue"] == topic and heading.lower() in [h.lower() for h in t["sections"]]]
        return found[-1] if found else None

    def by_hand(self, topic, newest):
        """True when the body is not the text that the newest change of the topic says it left."""
        issue = self.issues.get(topic)
        last = write_rules.newest_text(self.texts, topic)
        if not issue or last is None or not last["after"] or not self.body_shown(topic):
            return False
        return record_format.body_sha(issue["body"]) != last["after"]

    # ------------------------------------------------------------ the history of a part

    def version_names(self):
        """The named versions told by the record: [{"name", "commit", "id", "time", "day", "words"}], oldest first, from the
        told decisions whose Effect starts with the fixed line (record_format.version_name)."""
        if getattr(self, "_names", None) is not None:
            return self._names
        out = []
        if self.plan_state is not None:
            for cid, item in self.plan_state["found"]["items"].items():
                found = record_format.version_name(index.event_field(self.db, cid, "Effect")) if item["kind"] == "decision" else None
                if found and cid in self.m:
                    from mind_model import short  # noqa: E402
                    out.append({"name": found[0], "commit": found[1], "id": cid, "time": item["time"], "day": self.m[cid]["day"],
                                "words": short(index.event_field(self.db, cid, "Exact words").lstrip("> ").replace("\n> ", " "))})
        self._names = sorted(out, key=lambda row: (row["time"], row["id"]))
        return self._names

    def version_text(self, commit, issue, after):
        """(text, state) of the file of a topic at a commit kept on this machine: state "ok" when its hash is the "after" value
        of the told change that names the commit, "unfetched" when it is not kept, "unchecked" when it has another hash. The
        text is never used without "ok"."""
        if not commit or not after:
            return None, "unfetched"
        memo = getattr(self, "_texts_of", None)
        if memo is None:
            memo = self._texts_of = {}
        key = (commit, issue)
        if key not in memo:
            try:
                text = storage.read_cached(self.repo, (storage.VERSIONS, commit), "%d.md" % issue)
            except (storage.StorageError, OSError, UnicodeDecodeError):
                text = None
            memo[key] = text
        text = memo[key]
        if text is None:
            return None, "unfetched"
        if record_format.body_sha(text) != after:
            return None, "unchecked"
        return text, "ok"

    def section_diff(self, before, after, heading):
        """The operations between the text of `heading` in two bodies (either may be None for none), with the budget of the
        page: {"diff": ops or None, "no_diff": a key or None}."""
        old = record_format.section_text(before, heading) if before is not None else ""
        new = record_format.section_text(after, heading)
        if new is None and old is None:
            return {"diff": None, "no_diff": "unchecked"}
        ops = mind_versions.diff_text(old or "", new or "")
        used = getattr(self, "_diff_bytes", 0) + mind_versions.size(ops)
        if used > mind_versions.MAX_BYTES:
            return {"diff": None, "no_diff": "budget"}
        self._diff_bytes = used
        return {"diff": ops, "no_diff": None}

    def history_model(self, topic, heading):
        """The History of a part, newest first: [{"id", "day", "how", "plain", "why", "names", "diff", "no_diff"}] from the
        told text changes of the topic that name the heading. With versions kept (a mode of review or autonomous) each entry
        carries the difference from the version before it when both texts were fetched and checked, else a key that says why
        not; with the mode off the entries are the days the body changed, from the edit history the index holds."""
        if self.plan_state is None or topic is None:
            return {"kept": False, "entries": []}
        kept = self.writing != "off"
        if not kept:
            days = []
            for row in self.db.execute("SELECT at FROM timeline WHERE issue = ? AND event = 'edited' ORDER BY at DESC", (topic,)):
                day = self.day_of(row["at"])
                if day not in days:
                    days.append(day)
            return {"kept": False, "entries": [{"id": None, "day": day, "how": None, "plain": None, "why": None, "names": [],
                                                 "diff": None, "no_diff": "days"} for day in days]}
        entries, made = [], 0
        topic_texts = [t for t in self.texts if t["issue"] == topic]
        names = self.version_names()
        for text in reversed([t for t in topic_texts if heading.lower() in [h.lower() for h in t["sections"]]]):
            if text["id"] not in self.m:
                continue
            memory = self.m[text["id"]]
            entry = {"id": text["id"], "day": memory["day"], "how": text["form"], "plain": memory["plain"],
                     "why": self.field(text["id"], "Why"), "diff": None, "no_diff": "unfetched",
                     "names": [{"name": n["name"], "day": n["day"]} for n in names if text["version"] and n["commit"] == text["version"]]}
            if made >= mind_versions.MAX_PARTS:
                entry["no_diff"] = "budget"
            else:
                after, state = self.version_text(text["version"], topic, text["after"])
                if state != "ok":
                    entry["no_diff"] = state
                elif text["before"] is None:
                    entry.update(self.section_diff(None, after, heading))
                else:
                    earlier = next((t for t in topic_texts if t["after"] == text["before"] and t["version"]), None)
                    before, state = self.version_text(earlier["version"], topic, earlier["after"]) if earlier else (None, "before")
                    if state != "ok":
                        entry["no_diff"] = state
                    else:
                        entry.update(self.section_diff(before, after, heading))
                if entry["diff"] is not None:
                    made += 1
            entries.append(entry)
        return {"kept": True, "entries": entries}

    # ------------------------------------------------------------ the versions of the paper

    def versions_model(self, outline):
        """The versions block: {"kept", "names": [{"name", "day", "id", "words", "since"}], "unfetched"} or
        None when the project has no plan. "since" is, for a name after the first, what changed in each part of the text
        between the name before and this one: {"name", "parts": [{"thread", "ordinal", "label", "diff", "no_diff"}], "same": n}."""
        if self.plan_state is None:
            return None
        kept = self.writing != "off"
        names = self.version_names() if kept else []
        commits = {t["version"] for t in self.texts if t["version"]} | {n["commit"] for n in names}
        out, unfetched = [], 0
        parts = []
        for group in outline.get("deliverables", []):
            for section in group.get("sections", []):
                for part in section.get("parts", []):
                    if isinstance(part.get("ordinal"), int):
                        parts.append((part["thread"], part["ordinal"], section["label"]))
        topic_of = {m["n"]: m.get("topic") for m in self.threads_for_versions}
        previous = None
        for number, row in enumerate(names):
            entry = {"name": row["name"], "day": row["day"], "id": row["id"], "words": row["words"], "since": None}
            if previous is not None:
                changed, same = [], 0
                for thread, ordinal, label in parts:
                    topic = topic_of.get(thread)
                    if not [t for t in self.texts if t["issue"] == topic and self.after_name(t, previous) and not self.after_name(t, row)
                            and label.lower() in [h.lower() for h in t["sections"]]]:
                        same += 1   # every version is one told text change: none between the two names, nothing changed
                        continue
                    before, after = (self.text_at_name(topic, label, n) for n in (previous, row))
                    if before[1] != "ok" or after[1] != "ok":
                        unfetched += 1
                        changed.append({"thread": thread, "ordinal": ordinal, "label": label, "diff": None,
                                        "no_diff": ("before" if before[1] == "unfetched" and self.has_no_text_before(topic, previous)
                                                    else after[1] if before[1] == "ok" else before[1])})
                        continue
                    found = self.section_diff(before[0], after[0], label)
                    if found["diff"] is not None and not mind_versions.changed(found["diff"]):
                        same += 1
                    else:
                        changed.append(dict(found, thread=thread, ordinal=ordinal, label=label))
                entry["since"] = {"name": previous["name"], "parts": changed, "same": same}
            out.append(entry)
            previous = row
        return {"kept": kept, "names": list(reversed(out)), "unfetched": unfetched}

    @staticmethod
    def after_name(text, row):
        """True when a text change came after a named version: later by its time, or at the same minute with a later id
        (the record's comments are numbered in the order they were made)."""
        return (text["time"], text["id"]) > (row["time"], row["id"])

    def has_no_text_before(self, topic, row):
        """True when no told text change of the topic stands at or before the name: the text before that name is not a
        version this record vouches for."""
        return not [t for t in self.texts if t["issue"] == topic and not self.after_name(t, row) and t["version"]]

    def text_at_name(self, topic, heading, row):
        """(body, state) of a topic as it stood at a named version: the file of the newest told change of the topic up to the
        name, checked against that change's hash."""
        if topic is None:
            return None, "unfetched"
        found = [t for t in self.texts if t["issue"] == topic and not self.after_name(t, row) and t["version"]]
        if not found:
            return None, "unfetched"
        newest = found[-1]
        return self.version_text(newest["version"], topic, newest["after"])

    # ------------------------------------------------------------ what is still open

    def plan_open(self):
        """The three groups of "What is still open?" that the plan adds: claims nothing backs (with the idea that links
        each), claims a finding speaks against, findings the plan does not use. Page memories only for the findings."""
        if self.plan_state is None:
            return None
        gaps = index.gaps(self.db, self.config, self.allowed, self.plan_state)
        parts = self.plan_parts
        unbacked = [{"part": row["part"], "ideas": self.page_ids(row["ideas"])} for row in gaps["unbacked"]]
        doubt = [{"part": cid, "against": parts[cid]["against"][:1]} for cid in gaps["doubt"] if cid in parts]
        return {"unbacked": unbacked, "doubt": doubt, "unused": self.page_ids(gaps["unused"])}

    # ------------------------------------------------------------ the map of knowledge

    def plan_branch(self, heading):
        """{"ids": [part ids], "count": n} for the branch "We plan to say (n)" under a section of the map."""
        if self.plan_state is None:
            return None
        ids = [p["id"] for p in self.plan_parts.values() if p["standing"] and p["part"] != "message"
               and (p["section"] or "").lower() == heading.lower()]
        return {"ids": ids[:6], "count": len(ids)} if ids else None
