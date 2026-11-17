"""The plugin's own manuscript in Manubot's layout: the files of `content/` that the branch of versions holds.

Python 3 standard library only. Pure: text in, text out, no network, no file, and the same bytes for the same input. One
way only: nothing is ever read back from `content/` into the record (a restore reads `topics/`, which holds each body byte
for byte). The files:

  content/metadata.yaml        title, keywords, lang, authors; every value a quoted string, so no record text is read as YAML
  content/00.front-matter.md   the plugin's own front matter template (templates/manubot-front-matter.md), passed in
  content/NN.<section>.md      one file per section in the outline's order, numbered from 10, each topic's text for it under
                               a heading of the topic's name, every heading one level lower
  content/90.back-matter.md    the References heading and Manubot's <div id="refs"></div>
  content/manual-references.json  []
  content/images.tsv           per figure: its id, the repository, the commit and the path of its image

A figure block becomes `![Title. Caption](images/<id>.<ext>){#fig:<id>}` with a blank line before it; a table block its table
followed by `Table: Title. Caption {#tbl:<id>}`; a display formula keeps its form; citations, and @fig:, @tbl: and @eq:
references, pass through unchanged. "Sources" is not laid out: it stays in `topics/`.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recortex  # noqa: E402
import record_format  # noqa: E402

FRONT_MATTER = "{## The plugin's front matter template is templates/manubot-front-matter.md. ##}\n"
BACK_MATTER = "## References\n\n<div id=\"refs\"></div>\n"
IMAGE_TYPES = ("png", "jpg", "jpeg", "gif", "webp")
HEADING = re.compile(r"^(#{1,6})( +)(.*)$")
BLOCK = re.compile(r"^### (Figure|Table): ([A-Za-z0-9][A-Za-z0-9_.-]*) - (.+?)\s*$")


def yaml_string(value):
    """A value as a double-quoted YAML string: JSON's quoting, which YAML reads, with every non-ASCII character and control
    character escaped, so that no record text can be read as YAML structure (a colon, a quote and a line break stay inside
    the string)."""
    return json.dumps("" if value is None else str(value), ensure_ascii=True)


def metadata(title, authors=(), keywords=(), lang="en-US"):
    lines = ["title: %s" % yaml_string(title), "keywords: [%s]" % ", ".join(yaml_string(k) for k in keywords),
             "lang: %s" % yaml_string(lang)]
    if authors:
        lines.append("authors:")
        lines += ["  - name: %s" % yaml_string(name) for name in authors]
    else:
        lines.append("authors: []")
    return "\n".join(lines) + "\n"


def read_metadata(text):
    """(authors, keywords) from a metadata.yaml this module wrote; ([], []) for anything else."""
    authors, keywords = [], []
    try:
        for line in (text or "").split("\n"):
            if line.startswith("keywords: ["):
                keywords = json.loads(line[len("keywords: "):])
            elif line.startswith("  - name: "):
                authors.append(json.loads(line[len("  - name: "):]))
    except ValueError:
        return [], []
    return ([a for a in authors if isinstance(a, str)][:50], [k for k in keywords if isinstance(k, str)][:30])


def slug(heading):
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-") or "section"


def lower(line):
    """A heading of a body one level lower (at most six)."""
    match = HEADING.match(line)
    return "#" * min(6, len(match.group(1)) + 1) + match.group(2) + match.group(3) if match else line


def block_end(lines, start):
    end = start + 1
    while end < len(lines) and not re.match(r"^#{1,3} ", lines[end]):
        end += 1
    return end


def lay_block(lines, start, images, issue):
    """The lines of one figure or table block from `start`, as Manubot writes it; and where the block ends."""
    end = block_end(lines, start)
    match = BLOCK.match(lines[start])
    kind, ident, title = match.group(1).lower(), match.group(2), match.group(3)
    body = lines[start + 1:end]
    caption = " ".join(line.strip() for line in body if line.strip() and not line.strip().startswith(("|", "!["))
                       and not record_format.IMAGE.search(line))
    heading = "%s. %s" % (title.rstrip("."), caption) if caption else title
    heading = re.sub(r"\s+", " ", heading).replace("{", "(").replace("}", ")")
    if kind == "figure":
        found = next((record_format.IMAGE.search(line) for line in body if record_format.IMAGE.search(line)), None)
        extension = (found.group(4).rsplit(".", 1)[-1].lower() if found and "." in found.group(4) else "png")
        extension = extension if extension in IMAGE_TYPES else "png"
        if found:
            images.append((ident, found.group(2), found.group(3), found.group(4)))
        return ["", "![%s](images/%s.%s){#fig:%s}" % (heading.replace("]", ")").replace("[", "("), ident, extension, ident), ""], end
    table = [line for line in body if line.strip().startswith("|")] or [line for line in body if line.strip()
                                                                       and not caption.startswith(line.strip())]
    return [""] + table + ["", "Table: %s {#tbl:%s}" % (heading, ident), ""], end


def lay_text(text, images, issue):
    """The text of one section of a body, laid out: headings one level lower, blocks in Manubot's forms."""
    lines, out, fence, index = text.replace("\r\n", "\n").split("\n"), [], None, 0
    while index < len(lines):
        line = lines[index]
        opening = re.match(r"^\s*(```+|~~~+)", line)
        if fence is None and opening:
            fence = opening.group(1)
        elif fence is not None and line.strip().startswith(fence) and line.strip().strip(fence[0]) == "":
            fence = None
            out.append(line)
            index += 1
            continue
        if fence is None and BLOCK.match(line):
            laid, index = lay_block(lines, index, images, issue)
            out += laid
            continue
        out.append(lower(line) if fence is None else line)
        index += 1
    return "\n".join(out).strip("\n")


def layout(repo, hub, title, sections, authors=(), keywords=(), lang="en-US", front_matter=None):
    """({path: text}, images). `sections` is [(heading, [(issue number, topic name, text of that section)])] in the outline's
    order; `images` is the rows of content/images.tsv. The sections that hold no text are left out."""
    files, images = {}, []
    held = [(heading, topics) for heading, topics in sections if any(text.strip() for _, _, text in topics)]
    step = 10 if len(held) <= 8 else 1
    for position, (heading, topics) in enumerate(held):
        parts = ["## %s" % heading, ""]
        for number, name, text in topics:
            if text.strip():
                parts += ["### %s" % (name or "Topic %d" % number), "", lay_text(text, images, number), ""]
        files["content/%02d.%s.md" % (10 + position * step, slug(heading))] = "\n".join(parts).rstrip("\n") + "\n"
    files["content/metadata.yaml"] = metadata(title, authors, keywords, lang)
    files["content/00.front-matter.md"] = front_matter if front_matter is not None else FRONT_MATTER
    files["content/90.back-matter.md"] = BACK_MATTER
    files["content/manual-references.json"] = "[]\n"
    seen, rows = set(), []
    for ident, owner_repo, ref, path in images:
        if (ident, ref, path) not in seen:
            seen.add((ident, ref, path))
            rows.append("%s\t%s\t%s\t%s" % (ident, owner_repo, ref, path))
    files["content/images.tsv"] = "\n".join(["id\trepository\tcommit\tpath"] + rows) + "\n"
    return files, images
