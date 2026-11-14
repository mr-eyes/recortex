#!/usr/bin/env python3
"""Lint issue and comment drafts before they are posted to GitHub.

Checks: required sections for the kind of draft, placeholders left in the text,
bare #N references, body length, Mermaid rules, the resume block of a hub,
verbatim quotes in decisions, secrets and project-defined restricted patterns.
A final-record draft is deliverable text: it may hold no history (no "As of"
line, no <details> block, no headings for decisions, open work, provenance or
next steps, no dated headings), and its section headings must belong to the
project's profiles and agree with the labels the issue will carry.

A memory (result, decision, failure, checkpoint, correction, idea, question)
starts with its plain line; its marker may link to earlier memories; it may
list Evidence and end with folded explanations. What only the record can tell
(link targets, cited comments, the blocks named in Evidence, words and threads
already explained) is checked with --index (the local index, no network) or
--online (GitHub) with --issue N; without either, a draft that holds any of it
gets one "not-checked" warning.

Read-only. With --online it asks GitHub whether each bare #N exists; without it
no network access happens.

Examples:
  lint_drafts.py --kind final-record drafts/topic-parser.md
  lint_drafts.py --kind final-record --labels ms:methods,ms:results drafts/topic-parser.md
  lint_drafts.py --kind result --repo owner/name --online drafts/result.md
  lint_drafts.py --kind decision --index --issue 5 drafts/decision.md
  lint_drafts.py --manifest drafts/manifest.json
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recortex  # noqa: E402
import record_format  # noqa: E402

# A final record holds the deliverable's sections, named by the profile, then Sources.
FINAL_RECORD_SECTIONS = ["## " + recortex.SOURCES_HEADING]

# Headings that carry history or planning, which belong in comments and active issues.
# The whole heading must match, so "Next-generation sequencing" or "Provenance tracking" stay allowed.
HISTORY_HEADING = re.compile(
    r"^(?:decisions?|decision (?:record|log|table)|open (?:work|questions|items)|provenance"
    r"|next(?: (?:steps?|actions?|work))?|current state|not established|code and evidence|related topics"
    r"|history|status)\s*$", re.I)
DATE = re.compile(r"\b[0-9]{4}-[0-9]{2}-[0-9]{2}\b")
# "As of" followed by a date, a month or a year, at the start of a line or of a list item.
AS_OF = re.compile(r"^\s*(?:(?:[-*+]|[0-9]+\.)\s+)?[*_]*as of\s+[*_]*"
                   r"(?:the\s+)?(?:[0-9]{1,2}(?:st|nd|rd|th)?\s+)?(?:(?:19|20)[0-9]{2}\b|[0-9]{1,2}[/.][0-9]|(?:jan(?:uary)?"
                   r"|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?"
                   r"|nov(?:ember)?|dec(?:ember)?)\b)", re.I)

RESUME_FIELDS = [
    "**Last updated:**",
    "**Current focus:**",
    "**Active issues:**",
    "**Last decisions:**",
    "**Next actions:**",
    "**Holds:**",
]



def plain_field(kind):
    """The label of a memory's plain line, as it stands in the comment: **In plain words, ...:**"""
    return "**%s:**" % record_format.PLAIN_LABELS[kind]


KINDS = {
    "hub": {
        "sections": [
            "## Deliverable text",
            "## Resume here",
            "## Project",
            "## Map",
            "## Deliverable sections",
            "## Active issues",
            "## Pull requests",
            "## Labels",
            "## Older tracker",
            "## Recording rules",
        ],
    },
    "final-record": {"sections": FINAL_RECORD_SECTIONS},
    "active": {
        "sections": ["## Goal", "## Destination topic", "## Done when", "## Plan", "## Result"],
    },
    "result": {
        "header": "Result",
        "sections": [
            plain_field("result"),
            "**What ran:**",
            "**Configuration:**",
            "**Numbers:**",
            "**Exclusions and failures:**",
            "**Cost:**",
            "**Not established:**",
            "**Next step:**",
        ],
    },
    "decision": {
        "header": "Decision",
        "sections": [
            plain_field("decision"),
            "**Decision:**",
            "**Decided by:**",
            "**Exact words:**",
            "**Where recorded:**",
            "**Effect:**",
            "**Next step:**",
        ],
    },
    "failure": {
        "header": "Failure",
        "sections": [
            plain_field("failure"),
            "**What failed:**",
            "**What ran:**",
            "**Cause:**",
            "**Impact:**",
            "**Not established:**",
            "**Next step:**",
        ],
    },
    "checkpoint": {
        "header": "Checkpoint",
        "sections": [
            plain_field("checkpoint"),
            "**State:**",
            "**Done since the last checkpoint:**",
            "**In progress:**",
            "**Next actions:**",
            "**Open questions:**",
        ],
    },
    "correction": {
        "header": "Correction",
        "sections": [
            plain_field("correction"),
            "**Corrects:**",
            "**Was:**",
            "**Is:**",
            "**Why:**",
            "**Also updated:**",
        ],
    },
    "idea": {
        "header": "Idea",
        "sections": [
            plain_field("idea"),
            "**Why it might help:**",
            "**What it would take:**",
            "**Next step:**",
        ],
    },
    "question": {
        "header": "Question",
        "sections": [
            plain_field("question"),
            "**Why it matters:**",
            "**What would answer it:**",
            "**Next step:**",
        ],
    },
    "triage": {
        "header": "Triage",
        "sections": [
            "**Opened by:**",
            "**Their words:**",
            "**Type:**",
            "**Route:**",
            "**Reason:**",
            "**Needs confirmation:**",
        ],
    },
    "resume": {"header": "Resume block", "sections": RESUME_FIELDS},
    "continuation": {"sections": []},
    "pointer": {"sections": []},
    "generic": {"sections": []},
}

# Date, time and zone, for example "2026-03-04 14:10 PST" or "2026-03-04 14:10 UTC+1".
STAMP = r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} (?:[A-Z]{2,5}|UTC[+-][0-9]{1,2}(?::[0-9]{2})?)\b"

SECRET_PATTERNS = [
    ("GitHub token", r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    ("GitHub fine-grained token", r"\bgithub_pat_[A-Za-z0-9_]{30,}"),
    ("AWS access key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("private key block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("Slack token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    ("API key", r"\bsk-[A-Za-z0-9_-]{32,}"),
]

BARE_REF = re.compile(r"(?<![\w/&#])#([0-9]+)\b")
TEMPLATE_FIELD = re.compile(r"\{\{[^{}\n]*\}\}")
FENCE = re.compile(r"^\s*(```+|~~~+)(.*)$")

ARROW = re.compile(r"<?(?:-\.+->?|-{2,}[>ox]?|={2,}[>ox]?)")
NODE_ID = re.compile(r"^[A-Za-z0-9_]+$")
SKIP_PREFIXES = ("classDef ", "class ", "style ", "linkStyle ", "click ", "direction ")


class Finding:
    def __init__(self, level, code, line, message):
        self.level, self.code, self.line, self.message = level, code, line, message

    def __str__(self):
        where = "line %d" % self.line if self.line else "whole text"
        return "%s [%s] %s: %s" % (self.level.upper(), self.code, where, self.message)


def split_regions(text):
    """Yield (line_number, line, region) where region is prose, code or mermaid.

    Fence lines themselves are reported as region "fence".
    """
    fence, region = None, "prose"
    for number, line in enumerate(text.split("\n"), 1):
        match = FENCE.match(line)
        if fence is None and match:
            fence = match.group(1)
            region = "mermaid" if match.group(2).strip().lower() == "mermaid" else "code"
            yield number, line, "fence"
            continue
        if fence is not None and match and match.group(1).startswith(fence) and not match.group(2).strip():
            fence, region = None, "prose"
            yield number, line, "fence"
            continue
        yield number, line, region


def strip_inline_code(line):
    return re.sub(r"`[^`\n]*`", lambda m: " " * len(m.group(0)), line)


def lint_mermaid(block):
    """Check one Mermaid block. `block` is a list of (line_number, line)."""
    findings = []

    def add(level, code, line, message):
        findings.append(Finding(level, code, line, message))

    for number, line in block:
        if "#" in line:
            add("error", "mermaid-hash", number,
                "'#' inside a Mermaid block breaks rendering on GitHub; write 'Issue N'")
        if "(" in line or ")" in line:
            add("error", "mermaid-paren", number,
                "parentheses inside a Mermaid block; use [\"...\"] nodes and plain words")

    content = [(n, l.strip()) for n, l in block if l.strip() and not l.strip().startswith("%%")]
    if not content:
        add("error", "mermaid-empty", block[0][0] if block else 0, "empty Mermaid block")
        return findings
    if not re.match(r"^(flowchart|graph)\b", content[0][1]):
        return findings  # node rules below are written for flowcharts only

    defined, used, first_seen = {}, set(), {}
    for number, line in content[1:]:
        if line == "end" or line.startswith(SKIP_PREFIXES):
            continue
        if line.startswith("subgraph"):
            rest = line[len("subgraph"):].strip()
            if "[" in rest and not re.search(r'\["[^"]*"\]\s*$', rest):
                add("error", "mermaid-quote", number, "subgraph title must be double-quoted: id[\"Title\"]")
            continue
        for statement in line.split(";"):
            statement = statement.strip()
            if not statement:
                continue
            for label in re.findall(r"\|([^|]*)\|", statement):
                if not re.fullmatch(r'\s*"[^"]*"\s*', label):
                    add("error", "mermaid-quote", number, "edge label must be double-quoted: |\"%s\"|" % label.strip())
            statement = re.sub(r"\|[^|]*\|", " ", statement)
            for opener, closer in (("[", "]"), ("{", "}")):
                pattern = re.escape(opener) + r"+([^" + re.escape(opener + closer) + r"]*)" + re.escape(closer) + r"+"
                for label in re.findall(pattern, statement):
                    if not re.fullmatch(r'"[^"]*"', label.strip()):
                        add("error", "mermaid-quote", number,
                            "node label must be double-quoted: %s\"%s\"%s" % (opener, label.strip(), closer))
                statement = re.sub(pattern, "\x00", statement)
            statement = re.sub(r":::[A-Za-z0-9_-]+", "", statement)
            segments = [s.strip() for s in ARROW.split(statement)]
            is_edge = len(segments) > 1
            for segment in segments:
                for item in segment.split("&"):
                    item = item.strip()
                    if not item:
                        add("error", "mermaid-parse", number, "edge with a missing end")
                        continue
                    has_label = item.endswith("\x00")
                    node = item.rstrip("\x00").strip()
                    if not NODE_ID.match(node):
                        add("error", "mermaid-parse", number,
                            "cannot read node id %r; use letters, digits and underscores" % node)
                        continue
                    first_seen.setdefault(node, number)
                    if has_label:
                        defined.setdefault(node, number)
                    if is_edge:
                        used.add(node)
    for node, number in sorted(first_seen.items(), key=lambda kv: kv[1]):
        if node in used and node not in defined:
            add("error", "mermaid-undefined", number, "node %s is used in an edge but never given a label" % node)
        if node in defined and node not in used:
            add("error", "mermaid-unused", number, "node %s is defined but appears in no edge" % node)
    return findings


def check_sections(text, kind, template, add):
    spec = KINDS[kind]
    rows = [(n, l) for n, l, region in split_regions(text) if region == "prose"]
    header = spec.get("header")
    if header:
        nonblank = [(n, l) for n, l in rows if l.strip()]
        first = nonblank[0] if nonblank else (0, "")
        if record_format.parse_marker(first[1]) or first[1].lstrip().startswith("<!-- ghim:event"):
            first = nonblank[1] if len(nonblank) > 1 else (0, "")
        expected = r"^### %s(?: \(reconstructed\))?: " % header
        if not re.match(expected, first[1]):
            add("error", "header", first[0], "first line must start with '### %s: <date time zone> - <title>'" % header)
        elif not (template and "{{" in first[1]) and not re.match(expected + STAMP, first[1]):
            add("error", "timestamp", first[0],
                "header needs date, time and zone, for example '### %s: 2026-03-04 14:10 PST - title'" % header)

    positions, cursor = [], 0
    for marker in spec["sections"]:
        found = None
        for index in range(cursor, len(rows)):
            line = rows[index][1].strip()
            if line == marker or line.startswith(marker + " ") or (marker.startswith("**") and line.startswith(marker)):
                found = index
                break
        if found is None:
            elsewhere = any(l.strip().startswith(marker) for _, l in rows)
            add("error", "section-order" if elsewhere else "section-missing", 0,
                "section '%s' is %s" % (marker, "out of order" if elsewhere else "missing"))
            continue
        positions.append((marker, found))
        cursor = found + 1

    if template:
        return
    everything = list(split_regions(text))
    for order, (marker, index) in enumerate(positions):
        start = rows[index][0]
        stop = rows[positions[order + 1][1]][0] if order + 1 < len(positions) else len(everything) + 1
        own = rows[index][1].strip()[len(marker):].strip()
        filled = bool(own)
        for number, line, region in everything[start:stop - 1]:
            if marker.startswith("## ") and region == "prose" and re.match(r"^#{1,2} ", line):
                break
            if line.strip() and region != "fence":
                filled = True
                break
        if not filled:
            add("error", "section-empty", start,
                "section '%s' is empty; write 'None' and why rather than leaving it blank" % marker)


def check_final_record(text, template, labels, profiles, add):
    """Deliverable text only: a title line, the profile's sections, Sources last, and no history."""
    rows = [(n, l) for n, l, region in split_regions(text) if region == "prose"]
    first = next(((n, l) for n, l in rows if l.strip()), (0, ""))
    if not re.match(r"^# \S", first[1]):
        add("error", "title-line", first[0], "a final record starts with a title line: '# <topic as the deliverable names it>'")

    sections = recortex.body_sections(text)
    source = recortex.SOURCES_HEADING
    sources_at = next((n for n, title in sections if title.lower() == source.lower()), None)
    for number, line in rows:
        line = strip_inline_code(line)
        in_sources = sources_at is not None and number >= sources_at
        if AS_OF.match(line):
            add("error", "as-of", number,
                "no 'As of' line: the body is the current text only; dates belong in the comments")
        if re.search(r"<details\b", line, re.I):
            add("error", "details-block", number,
                "no <details> block: replaced text is not kept in the body; GitHub's edit history and the "
                "dated comment that caused the change are the trail")
        if not in_sources and re.search(STAMP, line):
            add("error", "dated-text", number,
                "a date and time in the text: the body holds the current text only; the time of a change "
                "belongs in the comment that records it")
        heading = re.match(r"^#{1,6} +(.*?)\s*#*\s*$", line)
        if heading:
            title = heading.group(1).strip().rstrip(":").strip()
            if HISTORY_HEADING.match(title):
                add("error", "history-heading", number,
                    "heading '%s' holds history or planning: decisions go in decision comments, open work and "
                    "next steps in active issues, provenance in comments or Sources" % title)
            if DATE.search(title) and not in_sources:
                add("error", "dated-heading", number,
                    "dated heading '%s': the body holds the current text only, without dates" % title)

    # A line that looks like a section heading but is not one would make its section, and its label, disappear.
    try:
        names = {heading.lower() for _, heading in recortex.section_labels(profiles)}
    except ValueError:
        names = set()  # reported below
    names.add(source.lower())
    for number, line, region in split_regions(text):
        if number == first[0] or region == "fence" or (region == "prose" and re.match(r"^## +\S", line)):
            continue
        if region == "code" and not line.lstrip().startswith("##"):
            continue
        candidate = re.sub(r"[\s#:]*$", "", re.sub(r"^\s*#*\s*", "", line))
        if candidate and candidate.lower() in names:
            where = "inside a code block; close the block before it" if region == "code" else \
                "write it as '## %s' at the start of a line, or reword the line" % candidate
            add("error", "near-heading", number,
                "'%s' looks like the section heading '## %s' but is not one, so the section and its label would "
                "be lost; %s" % (line.strip(), candidate, where))

    names = [title for _, title in sections]
    if not [title for title in names if title.lower() != source.lower()]:
        add("error", "no-sections", 0, "no '## ' section before '## %s'; write the text for at least one "
            "section of the deliverable" % source)
    if names and names[-1].lower() != source.lower() and any(t.lower() == source.lower() for t in names):
        add("error", "sources-last", 0, "'## %s' must be the last section" % source)
    if template:
        return

    everything = text.split("\n")
    for index, (number, title) in enumerate(sections):
        stop = sections[index + 1][0] if index + 1 < len(sections) else len(everything) + 1
        if title.lower() == source.lower():
            continue  # checked with the required sections
        if not any(line.strip() for line in everything[number:stop - 1]):
            add("error", "section-empty", number, "section '## %s' is empty; a section without text is "
                "removed, together with its label" % title)

    if not profiles:
        return
    try:
        wanted, problems = recortex.labels_from_headings(text, profiles, intended=labels)
    except ValueError as error:
        add("error", "profile", 0, str(error))
        return
    for number, message in problems:
        add("error", "section-heading", number, message)
    if labels is None or problems:
        return
    headings = dict(recortex.section_labels(profiles))
    for label in wanted:
        if label not in labels:
            add("error", "section-labels", 0,
                "the body has the section for %s, but the issue's labels do not include it" % label)
    for label in labels:
        if label in headings and label not in wanted:
            add("error", "section-labels", 0,
                "the issue is labeled %s, but the body has no '## %s' section; remove the label or add "
                "the section" % (label, headings[label]))


def check_event_marker(text, kind, template, add):
    """An event comment starts with `<!-- ghim:event type=<kind> at=<ISO time> -->`, then its heading; both agree."""
    first, second = record_format.first_lines(text)
    marker = record_format.parse_marker(first)
    if marker is None:
        add("error", "event-marker", 1, "an event comment starts with the line "
            "'<!-- ghim:event type=%s at=<YYYY-MM-DDTHH:MM+HH:MM> -->', then its heading" % kind)
        return
    if marker.get("type") != kind:
        add("error", "event-marker", 1, "the marker says type=%s, but this is a %s comment" % (marker.get("type"), kind))
    at = marker.get("at", "")
    if template and "{{" in at:
        return
    if not re.fullmatch(record_format.ISO, at):
        add("error", "event-marker", 1, "at=%s is not an ISO 8601 time with its offset, for example "
            "at=2026-03-04T14:10-08:00" % (at or "(missing)"))
        return
    stamp = re.search(STAMP, second)
    if stamp and stamp.group(0)[:16] != at[:16].replace("T", " "):
        add("error", "event-marker", 1, "the marker time %s and the heading time %s disagree; write the same local "
            "date and time in both" % (at, stamp.group(0)))
    reconstructed = "(reconstructed):" in second
    if reconstructed:
        if not re.fullmatch(record_format.ISO, marker.get("recorded", "")):
            add("error", "event-reconstructed", 1, "a reconstructed event's marker gives recorded=<ISO time it was "
                "recorded>")
        if not re.fullmatch(r"(commit|pr|issue|file|transcript):[\w./#:@+-]{1,200}", marker.get("source", "")):
            add("error", "event-reconstructed", 1, "a reconstructed event's marker gives its dated source: "
                "source=commit:<hash>, pr:<n>, issue:<n>, file:<path> or transcript:<path>")
    elif "recorded" in marker or "source" in marker:
        add("error", "event-reconstructed", 1, "recorded= and source= belong to a reconstructed event; write its "
            "heading as '### %s (reconstructed): <original time> - <title>'" % kind.capitalize())


ARTICLE = {"result": "a result", "decision": "a decision", "failure": "a failure", "correction": "a correction",
           "idea": "an idea", "question": "a question", "checkpoint": "a checkpoint", "triage": "a triage comment"}
EVIDENCE_KINDS = ("result", "decision", "failure", "correction")
STEM_REPEATS = {
    "result": ("that", "we found"),
    "decision": ("to", "we decided"),
    "idea": ("to", "we had"),
    "failure": ("what went wrong",),
    "correction": ("we corrected",),
    "checkpoint": ("we paused",),
    "question": ("we asked",),
}
QUESTION_WORDS = ("whether", "why", "how", "what", "which", "who", "when", "where", "if")
PLAIN_RANGE = (20, 200)
NOT_CHECKED = ("links", "comment addresses", "evidence", "explanations")


def is_reconstructed(text):
    first, second = record_format.first_lines(text)
    marker = record_format.parse_marker(first) or {}
    return "(reconstructed):" in second or "recorded" in marker


def listed(items):
    items = list(items)
    return items[0] if len(items) == 1 else "%s and %s" % (", ".join(items[:-1]), items[-1])


def starts_with_words(text, phrase):
    words = phrase.split()
    return re.match(r"%s(?![A-Za-z0-9])" % "[ \t]+".join(re.escape(w) for w in words), text, re.I) is not None


def check_marker_links(text, kind, add):
    """Marker keys, link values, and who may carry which link (record_format.LINK_RULES)."""
    first, second = record_format.first_lines(text)
    match = record_format.MARKER.match(first.strip())
    if not match:
        return
    pairs = [part.split("=", 1) for part in match.group(1).split()]
    names = [key for key, _ in pairs]
    for key in sorted({key for key in names if key not in record_format.MARKER_KEYS}):
        add("error", "marker-key", 1, "the marker key %s= is not one of %s; a misspelled key would be lost silently"
            % (key, ", ".join(k + "=" for k in record_format.MARKER_KEYS)))
    for key in sorted({key for key in names if names.count(key) > 1}):
        add("error", "marker-key", 1, "the marker key %s= appears more than once; write each key once" % key)
    under = {}
    for key, value in pairs:
        if key not in record_format.LINK_KINDS:
            continue
        if not record_format.LINK_VALUE.fullmatch(value):
            add("error", "link-value", 1, "%s=%s is not one to four comment ids separated by commas, for example "
                "%s=2900000124 or %s=2900000124,2900000125" % (key, value[:60], key, key))
            continue
        for ident in value.split(","):
            under.setdefault(ident, []).append(key)
    for ident, keys in sorted(under.items()):
        if len(keys) > 1:
            add("error", "link-value", 1, "comment %s stands %s; an id stands under one key, once"
                % (ident, "twice under %s=" % keys[0] if len(set(keys)) == 1 else "under %s"
                   % listed(k + "=" for k in keys)))
    carried = [key for key in record_format.LINK_KINDS if key in names]
    if not carried:
        return
    if kind == "checkpoint":
        add("error", "link-kind", 1, "a checkpoint carries no link (%s): a pause is a moment, not a step"
            % listed(k + "=" for k in carried))
    elif kind not in record_format.LINKABLE:
        add("error", "link-kind", 1, "%s carries no link (%s); links belong to memories"
            % (ARTICLE.get(kind, "this kind of comment"), listed(k + "=" for k in carried)))
    elif is_reconstructed(text):
        add("error", "link-kind", 1, "a reconstructed memory carries no link (%s) in this version; links for "
            "reconstructed history wait for the later owner-approved pass" % listed(k + "=" for k in carried))
    else:
        for key in carried:
            if kind not in record_format.LINK_RULES[key]:
                carriers = [ARTICLE[k] for k in record_format.LINKABLE if k in record_format.LINK_RULES[key]]
                add("error", "link-kind", 1, "%s does not carry %s=; it is carried by %s"
                    % (ARTICLE[kind], key, listed(carriers).replace(" and ", " or ")))


def check_plain(text, kind, template, add):
    """The plain line: the first field, one line that completes its label, in words a reader away for a month can
    follow, with every number also standing in a field."""
    label = record_format.PLAIN_LABELS[kind]
    lines = record_format.body_lines(text)
    scanned = record_format.scan_fields(text)
    position = next((index for index, field in enumerate(scanned) if field["label"] == label), None)
    if position is None:
        add("error", "plain", 0, "the first field is the plain line, '**%s:** <one line>', directly under the "
            "heading" % label)
        return
    field = scanned[position]
    number, value = field["line"] + 1, field["first"]
    if template and "{{" in lines[field["line"]]:
        return
    if position:
        add("error", "plain", number, "the plain line is the first field, directly under the heading")
    if not value:
        add("error", "plain", number, "the plain line is empty; it completes '%s'" % label)
        return
    following = next((index for index in range(field["line"] + 1, len(lines)) if lines[index].strip()), None)
    sections = KINDS[kind]["sections"]
    if field["line"] + 1 < len(lines) and lines[field["line"] + 1].strip() or following is not None and \
            len(sections) > 1 and not lines[following].strip().startswith(sections[1]):
        add("error", "plain", number, "the plain line is one line; a blank line and then '%s' follow it"
            % (sections[1] if len(sections) > 1 else "the next field"))
    if not PLAIN_RANGE[0] <= len(value) <= PLAIN_RANGE[1]:
        add("error", "plain", number, "the plain line is %d characters; write %d to %d" % ((len(value),) + PLAIN_RANGE))
    if value.endswith("."):
        add("error", "plain", number, "the plain line ends without a period")
    for what in record_format.line_problems(value):
        add("error", "plain", number, "the plain line holds %s; it is plain words only: cite issues, paths and "
            "commits in the fields below" % what)
    stem = label[len("In plain words, "):]
    for phrase in STEM_REPEATS[kind] + ("in plain words",):
        if starts_with_words(value, phrase):
            add("error", "plain", number, "the plain line repeats its label: '%s:' already says '%s'" % (label, stem))
            break
    if kind == "question" and not any(starts_with_words(value, word) for word in QUESTION_WORDS):
        add("error", "plain", number, "the plain line of a question starts with %s"
            % listed(QUESTION_WORDS).replace(" and ", " or "))
    others = set()
    for index, other in enumerate(scanned):
        if index != position:
            others |= record_format.field_numbers(other["text"])
    missing = []
    for written, token, power in record_format.plain_numbers(value):
        if written not in missing and not record_format.number_stands(token, power, others):
            missing.append(written)
    if missing:
        add("error", "plain", number, "the number%s %s in the plain line stand%s in no field of this memory, neither "
            "as written nor rounded; put each number in a field, with its source, or leave it out of the plain line"
            % ("s" if len(missing) > 1 else "", listed(missing), "" if len(missing) > 1 else "s"))


def check_quoted_fields(text, add):
    """A field label right after a quote line, with no blank line between, is part of the quote on GitHub."""
    lines = record_format.body_lines(text)
    code = record_format.code_lines(lines)
    for index in sorted(record_format.quote_lines(lines, code)):
        match = record_format.FIELD.match(lines[index])
        if match:
            add("error", "field-quote", index + 1, "'**%s:**' follows a quote line with no blank line between, so "
                "GitHub shows it inside the quote; leave a blank line before it" % match.group(1))


def check_evidence(text, kind, template, add):
    """The optional Evidence field: last, at most six lines, each one of the four forms."""
    scanned = record_format.scan_fields(text)
    found = [index for index, field in enumerate(scanned) if field["label"] == record_format.EVIDENCE_LABEL]
    if not found:
        return
    field = scanned[found[0]]
    number = field["line"] + 1
    if kind not in EVIDENCE_KINDS:
        add("error", "evidence", number, "only a result, a decision, a failure or a correction lists Evidence")
        return
    if is_reconstructed(text):
        add("error", "evidence", number, "a reconstructed memory lists no Evidence in this version")
        return
    if len(found) > 1:
        add("error", "evidence", scanned[found[1]]["line"] + 1, "a memory has one Evidence field")
    if found[0] != len(scanned) - 1:
        add("error", "evidence", number, "Evidence is the last field, after every field of the %s" % kind)
    if template:
        return
    count = 0
    for index, part in field["parts"]:
        if not part.strip():
            continue
        count += 1
        row, why = record_format.evidence_line(part)
        if row is None:
            add("error", "evidence", index + 1, "an Evidence line is %s" % why if why.startswith("not ") else why)
    if not count:
        add("error", "evidence", number, "Evidence is empty; delete the field when there is nothing to show")
    if count > record_format.EVIDENCE_MAX_LINES:
        add("error", "evidence", number, "Evidence has %d lines; at most %d" % (count, record_format.EVIDENCE_MAX_LINES))


def word_pattern(name):
    words = record_format.word_key(name).split()
    return re.compile(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % "[ \t\n]+".join(re.escape(w) for w in words), re.I) \
        if words else None


def check_folds(text, kind, add):
    """Folds: only on memories, only after the last field, each well formed, within the limits."""
    lines = record_format.body_lines(text)
    code = record_format.code_lines(lines)
    found = record_format.folds(text)
    covered = {index for item in found for index in range(item["first"], item["last"] + 1)}
    for index, line in enumerate(lines):
        if index not in code and index not in covered and record_format.FOLD_TAG.search(strip_inline_code(line)):
            add("error", "fold-format", index + 1, "a <details> or <summary> tag outside a fold; a fold starts with a "
                "line that holds only <details>")
    if not found:
        return
    if kind not in record_format.MEMORY_TYPES:
        add("error", "fold-format", found[0]["first"] + 1, "folds belong to memories; %s holds none"
            % ARTICLE.get(kind, "this kind of comment"))
        return
    for item in found:
        for index, message in item["problems"]:
            add("error", "fold-format", index + 1, message)
    stray = [index for index in range(found[0]["first"], len(lines)) if index not in covered and lines[index].strip()]
    if stray:
        if any(record_format.FIELD.match(lines[index]) and index not in code for index in stray):
            add("error", "fold-format", found[0]["first"] + 1, "a fold stands before a field; folds come after the "
                "last field")
        else:
            add("error", "fold-format", stray[0] + 1, "only folds follow the first fold; move this text into a field")
    explained = [item for item in found if item["explanation"]]
    words = [item for item in explained if item["explanation"]["kind"] == "word"]
    threads = [item for item in explained if item["explanation"]["kind"] == "thread"]
    if len(words) > 3:
        add("error", "fold-format", words[3]["first"] + 1, "at most three words are explained in one memory")
    if len(threads) > 1:
        add("error", "fold-format", threads[1]["first"] + 1, "a memory holds at most one fold about its thread")
    keys = {}
    for item in words:
        word = item["explanation"]["word"]
        key = record_format.word_key(word)
        if key in keys:
            add("error", "fold-format", item["first"] + 1, "\"%s\" is explained twice in this memory" % word)
        keys.setdefault(key, item)
        outside = "\n".join(line for index, line in enumerate(lines) if not item["first"] <= index <= item["last"])
        names = [word] + item["explanation"]["also"]
        if not any(word_pattern(name) and word_pattern(name).search(outside) for name in names):
            add("error", "fold-format", item["first"] + 1, "\"%s\" is not used in this memory outside its own fold; "
                "explain a word where the memory uses it" % word)


def needs_record(text, kind):
    """What a draft holds that only the record can check: the subset of NOT_CHECKED."""
    first, _ = record_format.first_lines(text)
    found = set()
    if record_format.links(record_format.parse_marker(first)):
        found.add("links")
    if any(item["kind"] == "memory" or item["kind"] == "elsewhere" and item["comment"] is None and
           "#issuecomment-" in text[item["start"]:item["end"]]
           for item in record_format.citations(text, None)):
        found.add("comment addresses")
    if any(row["kind"] in ("table", "figure") or row["kind"] == "pull" and not row["repo"]
           for row in record_format.evidence(text)):
        found.add("evidence")
    if record_format.explanations(text):
        found.add("explanations")
    return found


LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?")


def shown_login(login):
    return login if LOGIN.fullmatch(login or "") else "an account whose login is not shown"


def check_record(text, kind, lookup, add):
    """What only the record can tell, asked through `lookup` (lookup.py). Returns the names of NOT_CHECKED that the
    lookup could not answer."""
    unknown, skipped = lookup.UNKNOWN, set()
    repo, number = lookup.repo, lookup.number
    sync = "" if lookup.online else " (run index.py sync if it was posted since the last sync)"
    first, second = record_format.first_lines(text)
    marker = record_format.parse_marker(first) or {}
    at = marker.get("at", "")
    draft_time = record_format.sort_key(at) if re.fullmatch(record_format.ISO, at) else None
    linkable = kind in record_format.LINKABLE and not is_reconstructed(text)
    for key, target in record_format.links(marker) if linkable else []:
        found = lookup.memory(target)
        if found is unknown:
            skipped.add("links")
        elif found is None:
            add("error", "link-target", 1, "%s=%d: there is no comment %d in %s%s; take ids from index.py query recall "
                "or from the memory id: line of post_verified.py" % (key, target, target, repo, sync))
        elif found["type"] not in record_format.MEMORY_TYPES:
            add("error", "link-target", 1, "%s=%d: comment %d is not a memory (it is a comment of type %s); a link "
                "points at a result, decision, failure, correction, idea or question" % (key, target, target,
                                                                                         found["type"]))
        else:
            if draft_time and found["time"] > draft_time:
                add("error", "link-target", 1, "%s=%d: memory %d is later (%s) than this one (%s); a link points back in "
                    "time" % (key, target, target, found["at"][:40], at))
            if not record_format.link_allowed(kind, key, found["type"]):
                allowed = sorted(record_format.LINK_RULES[key].get(kind, ()))
                add("error", "link-target", 1, "%s=%d points at %s; %s %s= points at %s" % (
                    key, target, ARTICLE.get(found["type"], found["type"]), ARTICLE[kind], key,
                    listed(ARTICLE[k] for k in allowed).replace(" and ", " or ") if allowed else "nothing"))
            if not lookup.counts(found["author"]):
                add("error", "link-target", 1, "%s=%d: memory %d was written by %s, an account outside your allowed list, "
                    "so the link would lead nowhere; if that account belongs to the project, add its login to the line "
                    "of %s in your trusted list" % (key, target, target, shown_login(found["author"]), repo))
    for item in record_format.citations(text, repo):
        if item["kind"] != "memory":
            continue
        found = lookup.memory(item["comment"])
        if found is unknown:
            skipped.add("comment addresses")
        elif found is None or found["issue"] != item["issue"]:
            add("error", "citation", 0, "the address of comment %d on #%d: there is no such comment on that issue in "
                "%s%s" % (item["comment"], item["issue"], repo, sync))
    rows = record_format.evidence(text) if kind in EVIDENCE_KINDS and not is_reconstructed(text) else []
    for row in rows:
        name = "%s %s" % (row["kind"], row["ident"])
        if row["kind"] in ("table", "figure"):
            topics = lookup.topic_issue(number) if number is not None else unknown
            bodies = unknown if topics is unknown else [lookup.issue(n) for n in topics]
            if bodies is unknown or unknown in bodies:
                skipped.add("evidence")
            elif not topics:
                add("error", "evidence", 0, "%s: #%d shares no topic: label with an issue labeled final-record, so "
                    "there is no topic body to hold it" % (name, number))
            elif not any(body and row["ident"] in body["blocks"].get(row["kind"], ()) for body in bodies):
                add("error", "evidence", 0, "%s: the topic body (%s) has no '### %s: %s' block" % (
                    name, ", ".join("#%d" % n for n in topics), row["kind"].capitalize(), row["ident"]))
        elif row["kind"] == "pull" and not row["repo"]:
            found = lookup.issue(int(row["ident"]))
            if found is unknown:
                skipped.add("evidence")
            elif found is None:
                add("error", "evidence", 0, "%s: there is no pull request %s in %s%s" % (name, row["ident"], repo, sync))
            elif not found["pull"]:
                add("error", "evidence", 0, "%s: #%s is an issue, not a pull request" % (name, row["ident"]))
    if kind not in record_format.MEMORY_TYPES:
        return skipped
    explanations = record_format.explanations(text)
    threads = [item for item in explanations if item["kind"] == "thread"]
    if threads and number is None:
        skipped.add("explanations")
    elif threads:
        target = lookup.issue(number)
        topics = [] if target in (None, unknown) else [l for l in target["labels"] if l.lower().startswith("topic:")]
        if target is unknown:
            skipped.add("explanations")
        elif target is None:
            add("error", "thread-issue", 0, "there is no issue #%d in %s%s, so a fold cannot say what its thread is about"
                % (number, repo, sync))
        elif len(topics) > 1:
            add("error", "thread-issue", 0, "#%d carries %d topic: labels, so a fold cannot say which thread it opens; "
                "write what a thread is about in a memory on an issue with one topic: label" % (number, len(topics)))
        opening = lookup.opening(number)
        if opening is unknown:
            skipped.add("explanations")
        elif not threads[0]["revised"] and opening:
            add("error", "thread-known", 0, "memory %s (%s) already says what this thread is about; write 'This thread "
                "is now about' only if that changed, else leave the fold out" % (opening["comment"], opening["date"]))
        elif threads[0]["revised"] and not opening:
            add("error", "thread-new", 0, "nobody has written what this thread is about yet; write 'This thread is "
                "about', not 'is now about'")
    for item in explanations:
        if item["kind"] != "word":
            continue
        found = lookup.word(record_format.word_key(item["word"]))
        if found is unknown:
            skipped.add("explanations")
        elif not item["revised"] and found:
            add("error", "word-known", 0, "\"%s\" is already explained in memory %s (%s); use it as written there, or "
                "write 'We now use' if its meaning changed" % (item["word"], found["comment"], found["date"]))
        elif item["revised"] and not found:
            add("error", "word-new", 0, "\"%s\" was never explained; write 'We use', not 'We now use'" % item["word"])
    return skipped


def not_checked(skipped, add):
    if skipped:
        names = [name for name in NOT_CHECKED if name in skipped]
        add("warning", "not-checked", 0, "%s %s not checked against the record (use --index, or --online with --issue)"
            % (listed(names), "was" if len(names) == 1 and not names[0].endswith("s") else "were"))


def check_blocks(text, template, add):
    """Each figure block has a caption, an image at a ref and a Sources row; each table block a caption, a table
    or a linked file, and a Sources row."""
    for block in record_format.blocks(text):
        name = "%s %s" % (block["kind"].capitalize(), block["id"])
        if not block["caption"]:
            add("error", "block-caption", block["line"], "%s has no caption" % name)
        if block["kind"] == "figure" and not block["image"].startswith("https://") or \
                block["kind"] == "figure" and "?raw=true" not in block["image"]:
            add("error", "figure-image", block["line"], "%s embeds no image; write "
                "![text](https://github.com/<owner>/<repo>/blob/<commit or tag>/<path>?raw=true)" % name)
        if block["kind"] == "table" and not block["has_table"] and not block["image"]:
            add("error", "table-content", block["line"], "%s holds neither a Markdown table nor a link to a committed "
                "file" % name)
        if block["ref"] and block["ref"].lower() in record_format.BRANCH_REFS:
            add("warning", "block-ref", block["line"], "%s points at the branch '%s'; use a commit or tag so the "
                "file cannot change under the text" % (name, block["ref"]))
        if block["source"] is None:
            add("error", "block-source", block["line"], "%s has no row in Sources; add '| %s | data `<file>`; "
                "command `<command>` |'" % (name, name))


def lint_text(text, kind="generic", template=False, allow_placeholders=False,
              known_keys=None, known_numbers=None, number_checker=None, config=None,
              labels=None, profiles=None, lookup=None):
    """Return a list of Finding for one draft.

    known_numbers: set of issue numbers known to exist (offline check).
    number_checker: callable(int) -> bool used when a number is not in known_numbers.
    labels: the labels the issue will carry; for a final record its section labels
    must agree with its section headings. None skips that comparison.
    profiles: the profiles whose section headings a final record may use;
    default: "profiles" of the project configuration.
    lookup: a lookup.Lookup that answers what the record holds (link targets,
    cited comments, evidence, explanations); without one, a draft that holds
    any of these gets one "not-checked" warning.
    """
    config = config or {}
    if profiles is None:
        profiles = config.get("profiles") or []
    findings = []

    def add(level, code, line, message):
        findings.append(Finding(level, code, line, message))

    if kind not in KINDS:
        add("error", "kind", 0, "unknown kind %r; choose from %s" % (kind, ", ".join(sorted(KINDS))))
        return findings

    length = len(text)
    if length > recortex.BODY_LIMIT:
        add("error", "length", 0,
            "%d characters; GitHub accepts at most %d. Shorten it: a final record that needs more is split "
            "into two topics, a comment into two comments" % (length, recortex.BODY_LIMIT))
    elif length > recortex.BODY_LIMIT - 5000:
        add("warning", "length", 0, "%d characters; close to the %d limit" % (length, recortex.BODY_LIMIT))
    if not text.strip():
        add("error", "empty", 0, "draft is empty")
        return findings

    banned = [w for w in config.get("banned_words", []) if w]
    restricted = config.get("restricted_patterns", [])
    mermaid_blocks, current = [], None
    bare = {}

    for number, line, region in split_regions(text):
        if region == "mermaid":
            if current is None:
                current = []
                mermaid_blocks.append(current)
            current.append((number, line))
        else:
            current = None

        for name, pattern in SECRET_PATTERNS:
            if re.search(pattern, line):
                add("error", "secret", number, "looks like a %s; never post secrets" % name)
        for entry in restricted:
            pattern = entry.get("pattern") if isinstance(entry, dict) else entry
            label = entry.get("name", pattern) if isinstance(entry, dict) else entry
            if pattern and re.search(pattern, line):
                add("error", "restricted", number, "matches the project's restricted pattern '%s'" % label)
        for word in banned:
            if re.search(r"(?<!\w)%s(?!\w)" % re.escape(word), line, re.IGNORECASE):
                add("error", "banned-word", number, "banned word '%s'" % word)

        for match in recortex.PLACEHOLDER_RE.finditer(line):
            key = match.group(1)
            if not allow_placeholders:
                add("error", "placeholder", number, "placeholder NEW-%s is still in the text" % key)
            elif known_keys is not None and key not in known_keys:
                add("error", "placeholder-unknown", number, "NEW-%s is not a key in the manifest" % key)

        if region in ("prose", "mermaid") and not template:
            for match in TEMPLATE_FIELD.finditer(line):
                add("error", "template-field", number, "template field %s was not filled in" % match.group(0))

        if region == "prose":
            for match in BARE_REF.finditer(strip_inline_code(line)):
                if len(match.group(1)) == 6:
                    continue  # six digits after # is read as a hex color such as #123456, not as an issue
                bare.setdefault(int(match.group(1)), number)

    for block in mermaid_blocks:
        findings.extend(lint_mermaid(block))

    for ref, number in sorted(bare.items()):
        if known_numbers is not None and ref in known_numbers:
            continue
        if number_checker is not None:
            if not number_checker(ref):
                add("error", "bare-ref", number,
                    "#%d does not exist in the target repository; use owner/repo#%d or a full URL" % (ref, ref))
        elif known_numbers is not None:
            add("error", "bare-ref", number,
                "#%d is not among the known issue numbers of the target repository" % ref)
        else:
            add("warning", "bare-ref", number,
                "#%d not checked against the target repository (use --online or --known-numbers)" % ref)

    check_sections(text, kind, template, add)

    if kind == "final-record":
        check_final_record(text, template, labels, profiles, add)
        check_blocks(text, template, add)

    if KINDS[kind].get("header") and kind != "resume":
        text = text.replace("\r\n", "\n")
        check_event_marker(text, kind, template, add)
        check_marker_links(text, kind, add)
        if kind in record_format.MEMORY_TYPES:
            check_plain(text, kind, template, add)
            check_quoted_fields(text, add)
        check_evidence(text, kind, template, add)
        check_folds(text, kind, add)
        not_checked(needs_record(text, kind) if lookup is None else check_record(text, kind, lookup, add), add)

    if kind != "resume" and (recortex.RESUME_START in text or recortex.RESUME_END in text):
        add("error", "resume-markers-misplaced", 0,
            "resume markers belong only in a resume comment on the hub (kind 'resume'); remove them here")

    if kind == "hub":
        if not mermaid_blocks:
            add("warning", "hub-map", 0, "the hub has no Mermaid map")

    if kind == "resume":
        if text.count(recortex.RESUME_START) != 1 or text.count(recortex.RESUME_END) != 1 \
                or recortex.extract_resume_block(text) is None:
            add("error", "resume-markers", 0,
                "a resume comment needs exactly one '%s' followed by one '%s'" % (recortex.RESUME_START, recortex.RESUME_END))
        else:
            findings.extend(lint_resume_block(recortex.extract_resume_block(text), template))

    if kind == "triage" and not template:
        type_line = re.search(r"^\*\*Type:\*\*\s*(.*)$", text, re.M)
        if type_line and not re.match(r"`?type:[a-z0-9-]+`?\s*$", type_line.group(1).strip()):
            add("error", "triage-type", 0, "**Type:** names one type: label, for example type:bug")
        has_quote = any(l.lstrip().startswith(">") and l.lstrip(" >").strip()
                        for _, l, region in split_regions(text) if region == "prose")
        if not has_quote and "words not available" not in text.lower():
            add("error", "triage-quote", 0, "quote the words of the person who opened the issue in a '>' block, "
                "unchanged, or write 'Words not available' and why")

    if kind == "decision" and not template:
        has_quote = any(l.lstrip().startswith(">") and l.lstrip(" >").strip()
                        for _, l, region in split_regions(text) if region == "prose")
        if not has_quote and "exact words not available" not in text.lower() and \
                not ("(reconstructed):" in text and "decision inferred from" in text.lower()):
            add("error", "decision-quote", 0,
                "quote the decider's exact words in a '>' block, or write 'Exact words not available' and why")

    if kind == "pointer":
        if length > 1000:
            add("error", "pointer-length", 0, "a pointer comment stays short (at most 1000 characters)")
        if "https://github.com/" not in text and not re.search(r"[\w.-]+/[\w.-]+#[0-9]+", text) \
                and not (allow_placeholders and recortex.PLACEHOLDER_RE.search(text)):
            add("error", "pointer-link", 0, "a pointer comment must link to the new home with a full reference")

    return findings


def lint_resume_block(block, template=False):
    findings = []
    if len(block) > recortex.RESUME_LIMIT:
        findings.append(Finding("error", "resume-length", 0,
                                "resume block is %d characters; keep it under %d" % (len(block), recortex.RESUME_LIMIT)))
    for field in RESUME_FIELDS:
        if field not in block:
            findings.append(Finding("error", "resume-field", 0, "resume block lacks '%s' between the markers" % field))
    if not template:
        match = re.search(r"\*\*Last updated:\*\*\s*(.*)", block)
        if match and not re.match(STAMP, match.group(1).strip()):
            findings.append(Finding("error", "resume-timestamp", 0,
                                    "'Last updated' needs date, time and zone, for example 2026-03-04 14:10 PST"))
    return findings


def load_manifest(path):
    with open(path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    base = os.path.dirname(os.path.abspath(path))
    problems = []
    issues = manifest.get("issues", [])
    comments = manifest.get("comments", [])
    if not isinstance(issues, list) or not isinstance(comments, list):
        sys.exit("error: 'issues' and 'comments' in the manifest must be lists")
    keys = [item.get("key") for item in issues]
    for key in keys:
        if not key or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(key)):
            problems.append("issue key %r must be lowercase letters, digits and single hyphens" % key)
    if len(set(keys)) != len(keys):
        problems.append("issue keys must be unique")
    for item in issues:
        for field in ("title", "body_file", "kind", "labels"):
            if not item.get(field):
                problems.append("issue %r lacks '%s'" % (item.get("key"), field))
    for index, item in enumerate(comments):
        for field in ("issue", "body_file", "kind"):
            if not item.get(field):
                problems.append("comment %d lacks '%s'" % (index + 1, field))
        target = item.get("issue")
        if isinstance(target, str):
            match = recortex.PLACEHOLDER_RE.fullmatch(target)
            if not match or match.group(1) not in keys:
                problems.append("comment %d targets %r, which is not NEW-<key> of this manifest" % (index + 1, target))
        elif not isinstance(target, int):
            problems.append("comment %d: 'issue' must be a number or NEW-<key>" % (index + 1))
        if "repo" in item and not recortex.valid_repo(item["repo"]):
            problems.append("comment %d: 'repo' must be owner/name in GitHub's form" % (index + 1))
    for item in issues:
        for label in item.get("labels") or []:
            if "," in str(label):
                problems.append("label %r of issue %r contains a comma; gh would split it into two labels"
                                % (label, item.get("key")))
    files = [item.get("body_file") for item in issues + comments if item.get("body_file")]
    for name in sorted({f for f in files if files.count(f) > 1}):
        problems.append("body file %s is listed more than once; every draft is posted exactly once" % name)
    for item in issues + comments:
        name = item.get("body_file")
        if name:
            full = os.path.join(base, name)
            item["_path"] = full
            if not os.path.isfile(full):
                problems.append("body file not found: %s" % full)
    if problems:
        sys.exit("error: manifest %s\n  " % path + "\n  ".join(problems))
    return manifest


def report(path, findings, quiet=False):
    errors = [f for f in findings if f.level == "error"]
    warnings = [f for f in findings if f.level == "warning"]
    if findings or not quiet:
        print("%s: %d error(s), %d warning(s)" % (path, len(errors), len(warnings)))
    for finding in findings:
        print("  " + str(finding))
    return len(errors)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help="draft files to lint")
    parser.add_argument("--kind", default="generic", choices=sorted(KINDS), help="kind of draft (default: generic)")
    parser.add_argument("--manifest", help="lint every draft listed in a create_issues.py manifest")
    parser.add_argument("--repo", help="target repository, owner/name")
    parser.add_argument("--online", action="store_true", help="ask GitHub whether each bare #N exists, and check "
                        "links, cited comments, evidence and explanations against the record (read-only)")
    parser.add_argument("--index", action="store_true", help="check links, cited comments, evidence and explanations "
                        "against the local index, with no network (run index.py sync first)")
    parser.add_argument("--issue", type=int, help="the issue a comment draft goes to (for evidence and folds)")
    parser.add_argument("--known-numbers", help="comma-separated issue numbers that exist in the target repository")
    parser.add_argument("--allow-placeholders", action="store_true", help="accept NEW-<key> placeholders")
    parser.add_argument("--labels", help="comma-separated labels the issue will carry; a final record's section "
                        "labels must agree with its headings (the manifest gives each issue's labels)")
    parser.add_argument("--profile", help="comma-separated profiles that define the section headings "
                        "(default: profiles in .recortex.json)")
    parser.add_argument("--template", action="store_true", help="lint a template: {{fields}} and empty sections are fine")
    parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = parser.parse_args(argv)

    if not args.files and not args.manifest:
        parser.error("give draft files or --manifest")
    config = recortex.load_config()
    known = None
    if args.known_numbers:
        known = {int(x) for x in args.known_numbers.split(",") if x.strip()}
    checker = None
    if args.online:
        repo = recortex.resolve_repo(args.repo, config)
        cache = {}

        def checker(number):
            if number not in cache:
                cache[number] = recortex.number_exists(repo, number)
            return cache[number]

    make_lookup = None
    if args.index or args.online:
        import lookup as record_lookup
        repo = recortex.resolve_repo(args.repo, config)
        if args.index:
            try:
                record_lookup.local(repo, None).close()
            except record_lookup.Unavailable as error:
                sys.exit("error: --index: %s" % error)
            make_lookup = lambda number: record_lookup.local(repo, number)
        else:
            make_lookup = lambda number: record_lookup.github(repo, number)
    labels = [l.strip() for l in args.labels.split(",") if l.strip()] if args.labels else None
    profiles = [p.strip() for p in args.profile.split(",") if p.strip()] if args.profile else None
    jobs = [(path, args.kind, args.allow_placeholders, None, labels, args.issue) for path in args.files]
    if args.manifest:
        manifest = load_manifest(args.manifest)
        keys = {item["key"] for item in manifest.get("issues", [])}
        for item in manifest.get("issues", []):
            jobs.append((item["_path"], item["kind"], True, keys, item["labels"], None))
        for item in manifest.get("comments", []):
            target = item["issue"] if isinstance(item["issue"], int) and "repo" not in item else None
            jobs.append((item["_path"], item["kind"], True, keys, None, target))

    total = 0
    for path, kind, allow, keys, intended, number in jobs:
        findings = lint_text(recortex.read_text(path), kind=kind, template=args.template,
                             allow_placeholders=allow, known_keys=keys,
                             known_numbers=known, number_checker=checker, config=config,
                             labels=intended, profiles=profiles,
                             lookup=make_lookup(number) if make_lookup else None)
        if args.strict:
            for finding in findings:
                finding.level = "error"
        total += report(path, findings)
    return 1 if total else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except recortex.GhError as error:
        sys.exit("error: %s" % error)
