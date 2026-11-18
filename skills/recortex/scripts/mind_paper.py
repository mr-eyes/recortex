#!/usr/bin/env python3
"""The paper alone, for the page's own export: the data of paper.html (mind.py --paper [--version NAME]).

Python 3 standard library only. Pure: bodies in, plain data out; no index, no network, no file. The bodies are the topic
bodies of the paper, taken by the caller from the record (only topics whose body is told) or from a named version (only
what the record vouches for, `versions.vouched_version`). Nothing here builds markup: the page draws the data through its
own element builder, as it does the model, so record text stays text.

    paper(repo, title, note, order, bodies, names, name="")    the data of the paper

where `order` is [(label, heading, [issue numbers])] (the outline: the index's, or a named version's own), `bodies` is
{issue: body text}, `names` is {issue: a name for a topic whose text has no title of its own} and `name` is the subtitle (the
project's name). A part is titled by its text's own heading, the first "# " line of its body (mind_model.text_title, the
view's rule), and by its entry of `names` only when it has none. The result:

    {"repo", "title", "name", "note",
     "sections": [{"heading", "parts": [{"topic", "name", "doc"}]}],     doc as the model's: {"blocks": [...], "cut": False}
     "threads": [{"n": issue, "blocks": [figure and table entries]}],   what the page keys its figures and tables by
     "sources": [{"topic", "statement", "source"}]}                      "Where the numbers come from"; statement and
                                                                         source are the runs of mind_text.inline, so that
                                                                         a code span is code, not backticks

A section with no text in any of its topics is left out. "Sources" is never a section of the paper; its rows are listed
at the end. Text goes through the model's clean() and mind_text.blocks(), so the formulas, tables and lists are the
page's own.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mind_text  # noqa: E402
import record_format  # noqa: E402
import recortex  # noqa: E402
from mind_model import BLOCK_HEADING, CAP_FIELD, HEADING, clean, short, text_title  # noqa: E402


def entries(body):
    """The figure and table entries of a topic body, as the model keeps them (mind_model.Build.block_parts): kind, id,
    title, section, caption, data, command, and for a table its cells, for a figure its repository, ref and path."""
    text = clean(body)
    lines = text.split("\n")
    tables = {}
    for number, line in enumerate(lines):
        match = BLOCK_HEADING.match(line)
        if match:
            end = number + 1
            while end < len(lines) and not HEADING.match(lines[end]):
                end += 1
            tables[(match.group(1).lower(), match.group(2))] = "\n".join(lines[number + 1:end])
    out = []
    for block in record_format.blocks(text):
        entry = {"kind": block["kind"], "id": short(block["id"], 60), "title": short(block["title"]),
                 "section": clean(block["section"] or ""), "caption": short(block["caption"], CAP_FIELD),
                 "data": short(block["data"] or ""), "command": short(block["command"] or "")}
        if block["kind"] == "table":
            found = [item for item in mind_text.blocks(tables.get((block["kind"], block["id"]), "")) if "table" in item]
            entry["table"] = found[0]["table"] if found else None
        else:
            entry.update(repo=clean(block["repo"] or ""), ref=clean(block["ref"] or ""), path=clean(block["path"] or ""))
        out.append(entry)
    return out


def paper(repo, title, note, order, bodies, names, name=""):
    sections, threads, sources, seen = [], [], [], set()
    for _, heading, numbers in order:
        if heading.lower() == recortex.SOURCES_HEADING.lower():
            continue
        parts = []
        for number in numbers:
            if number not in bodies:
                continue
            text = record_format.section_text(bodies[number], heading)
            if text is None or not text.strip():
                continue
            parts.append({"topic": number, "name": text_title(bodies[number]) or short(clean(names.get(number) or "")),
                          "doc": {"blocks": mind_text.blocks(clean(text).strip("\n")), "cut": False}})
            if number not in seen:
                seen.add(number)
                threads.append({"n": number, "blocks": entries(bodies[number])})
                for row in record_format.sources(bodies[number]):
                    sources.append({"topic": number, "statement": mind_text.inline(short(clean(row["statement"]), CAP_FIELD)),
                                    "source": mind_text.inline(short(clean(row["source"]), CAP_FIELD))})
        if parts:
            sections.append({"heading": short(clean(heading)), "parts": parts})
    return {"repo": repo, "title": short(clean(title)), "name": short(clean(name)), "note": short(clean(note)), "sections": sections,
            "threads": threads, "sources": sources}
