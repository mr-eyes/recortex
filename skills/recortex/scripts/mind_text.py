#!/usr/bin/env python3
"""Record text as structure, for the mind page: blocks, inline runs and formula trees.

Python 3 standard library only. Pure functions: no index, no network, no write, no import of the plugin's other
modules. The text comes from the record and is untrusted. This module never builds markup and never reads an address
from anywhere but the text it is given; it returns plain data (dicts, lists, strings, integers, booleans and None)
that json.dumps can print and that the page draws through its own element builder.

    blocks(text)              the blocks of a text (see below)
    inline(text)              the runs of one line or paragraph
    tex(source, display)      a formula tree, or None when the formula is outside the subset
    github_parts(address)     the checked parts of an address on GitHub, or None

Blocks, each a dict with one main key:
    {"p": runs}                           {"h": {"level": 1 to 3, "r": runs}}
    {"ul": [item]}                        {"ol": [item], "start": 1 to 9999}, item = {"r": runs, "sub": block or None}
    {"quote": runs}                       {"code": "text"}
    {"math": {"d": True, "m": tree}}      or {"math": {"d": True, "s": source}} when the formula is outside the subset
    {"table": {"head": [row], "rows": [row], "cut": bool, "caption": runs or None}}
        row = [cell], cell = {"r": runs, "rs": 1 to 40, "cs": 1 to 12, "h": bool, "a": "l", "c", "r" or None}
    {"block": {"kind": "figure" or "table", "id": id}}     a figure or table block that the page draws in place
    {"note": {"key": "html-table"}}       an HTML table outside the subset
Runs, each a dict with one main key:
    {"t": text} with "b", "i", "sup", "sub" set to True when they apply
    {"code": text}
    {"math": {"d": False, "m": tree}}     or {"math": {"d": False, "s": source}}
    {"link": runs, "to": parts or None, "said": address}

HTML is never passed through. A comment is dropped. Of an HTML table only the tags and attributes listed below are
read, in a table of the limits below; the text inside script and style is dropped; every other tag is dropped and its
text kept. Any other HTML in a text stays text exactly as written.
"""

import html.parser
import re

CAP_TABLE_ROWS = 40
CAP_TABLE_COLUMNS = 12
HTML_TABLE_CHARACTERS = 20000
MAX_LIST_DEPTH = 3
SCAN_LIMIT = 500           # characters a dollar sign looks ahead for its partner

# the limits of a formula
MAX_INLINE = 400           # characters of source
MAX_DISPLAY = 2000
MAX_DEPTH = 8              # levels of groups
MAX_NODES = 300

ASCII_SPACE = " \t\n\r\f\v\u00a0"

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*([^`\n]*)$")
HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
BLOCK_HEADING = re.compile(r"^### (Figure|Table): ([A-Za-z0-9][A-Za-z0-9_.-]*) - ")
BLOCK_END = re.compile(r"^#{1,3} ")
THEMATIC = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
IMAGE_LINE = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)")
LIST_ITEM = re.compile(r"^( *)([-*+]|[0-9]{1,9}[.)])[ \t]+(\S.*)$")
SEPARATOR = re.compile(r":?-+:?")
TABLE_TAG = re.compile(r"<(/?)table\b", re.I)
GITHUB_MATH_OR_CODE = re.compile(r"(?P<math>\$`[^`\n]{1,%d}`\$)|(?P<code>`[^`\n]+`)" % SCAN_LIMIT)
LINK = re.compile(r"\[([^\[\]\n]{1,300})\]\(([^\s()<>\"'`]{1,500})\)")
HTML_TAG = re.compile(r"<(/?)(b|strong|i|em|sup|sub)>", re.I)
ESCAPABLE = "*_$[]#"
HTML_STYLE = {"b": "b", "strong": "b", "i": "i", "em": "i", "sup": "sup", "sub": "sub"}


def space(char):
    return char == "" or char in ASCII_SPACE


def digit(char):
    return char != "" and char in "0123456789"


def letter(char):
    return char != "" and char in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def escaped(text, at):
    """True when the character at `at` follows an odd number of backslashes."""
    count = 0
    while at - 1 - count >= 0 and text[at - 1 - count] == "\\":
        count += 1
    return count % 2 == 1


# ---------------------------------------------------------------- the address of a link

ISSUE_NUMBER = re.compile(r"[0-9]{1,10}")
COMMENT_ID = re.compile(r"[0-9]{1,19}")
LINE_NUMBER = re.compile(r"[0-9]{1,7}")
COMMIT = re.compile(r"[0-9a-f]{7,40}")
TAG_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
PATH = re.compile(r"[A-Za-z0-9_.@+-]+(?:/[A-Za-z0-9_.@+-]+)*")
REPO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,38}/(?!\.{1,2}$)[A-Za-z0-9._-]{1,100}")
LINE_FRAGMENT = re.compile(r"L([0-9]{1,7})(?:-L([0-9]{1,7}))?")
COMMENT_FRAGMENT = re.compile(r"issuecomment-([0-9]{1,19})")
GITHUB = "https://github.com/"


def positive(text, pattern):
    """The integer of `text` when it fully matches `pattern` and is 1 or more, else None."""
    if not isinstance(text, str) or not pattern.fullmatch(text):
        return None
    value = int(text)
    return value if value >= 1 else None


def good_path(path):
    return len(path) <= 200 and PATH.fullmatch(path) is not None and not any(
        part in (".", "..") for part in path.split("/"))


def github_parts(address):
    """The checked parts of an address on GitHub in one of the forms the page can build, else None.

    issue          https://github.com/owner/name/issues/N               {"kind": "issue", "repo", "n"}
    memory         the same with #issuecomment-ID                       {"kind": "memory", "repo", "n", "cid"}
    pull request   https://github.com/owner/name/pull/N                 {"kind": "pull", "repo", "n"}
    lines          .../blob/COMMIT/path#L3 or #L3-L5                    {"kind": "lines", "repo", "commit", "path",
                                                                         "first", "last"}
    picture        .../blob/COMMIT-or-TAG/path, with ?raw=true or not   {"kind": "picture", "repo", "ref", "path"}
    Any other host or scheme, an empty or dotted part, a space, a quote or a character outside the sets is None."""
    if not isinstance(address, str) or not address.startswith(GITHUB) or len(address) > 700:
        return None
    rest, fragment, query = address[len(GITHUB):], None, None
    if "#" in rest:
        rest, fragment = rest.split("#", 1)
    if "?" in rest:
        rest, query = rest.split("?", 1)
    parts = rest.split("/")
    if len(parts) < 4:
        return None
    repo, kind = parts[0] + "/" + parts[1], parts[2]
    if REPO_NAME.fullmatch(repo) is None:
        return None
    if kind in ("issues", "pull"):
        n = positive(parts[3], ISSUE_NUMBER) if len(parts) == 4 else None
        if n is None or query is not None:
            return None
        if kind == "pull":
            return {"kind": "pull", "repo": repo, "n": n} if fragment is None else None
        if fragment is None:
            return {"kind": "issue", "repo": repo, "n": n}
        found = COMMENT_FRAGMENT.fullmatch(fragment)
        cid = positive(found.group(1), COMMENT_ID) if found else None
        return {"kind": "memory", "repo": repo, "n": n, "cid": cid} if cid else None
    if kind != "blob" or len(parts) < 5:
        return None
    ref, path = parts[3], "/".join(parts[4:])
    if not (COMMIT.fullmatch(ref) or TAG_NAME.fullmatch(ref)) or not good_path(path):
        return None
    if fragment is not None:
        found = LINE_FRAGMENT.fullmatch(fragment)
        if found is None or query is not None or not COMMIT.fullmatch(ref):
            return None
        first = positive(found.group(1), LINE_NUMBER)
        last = positive(found.group(2) or found.group(1), LINE_NUMBER)
        if not first or not last or first > last:
            return None
        return {"kind": "lines", "repo": repo, "commit": ref, "path": path, "first": first, "last": last}
    if query not in (None, "raw=true"):
        return None
    return {"kind": "picture", "repo": repo, "ref": ref, "path": path}


# ---------------------------------------------------------------- inline runs

def split_math(text):
    """`text` as a list of strings and math runs: $...$ opens at a dollar sign followed by a character that is not a
    space and closes at a dollar sign that follows one and is not followed by a digit, so "$5 and $10" stays text."""
    out, pos, start, size = [], 0, 0, len(text)
    while True:
        at = text.find("$", pos)
        if at < 0:
            break
        if escaped(text, at):
            pos = at + 1
            continue
        double = text.startswith("$$", at)
        begin = at + 2 if double else at + 1
        close, probe = None, begin
        while True:
            probe = text.find("$", probe)
            if probe < 0 or probe - at > SCAN_LIMIT:
                break
            if escaped(text, probe) or probe == begin:
                probe += 1
                continue
            if double:
                if text.startswith("$$", probe):
                    close = probe
                    break
            elif not space(text[probe - 1]) and not digit(text[probe + 1] if probe + 1 < size else ""):
                close = probe
                break
            probe += 1
        source = text[begin:close] if close is not None else ""
        if close is None or not source or space(source[0]) or space(source[-1]) or (double and source.count("$")):
            pos = at + (2 if double else 1)
            continue
        if start < at:
            out.append(text[start:at])
        out.append(math_run(source, False))
        pos = start = close + (2 if double else 1)
    if start < size:
        out.append(text[start:])
    return out


def math_run(source, display):
    tree = tex(source, display)
    return {"math": {"d": display, "m": tree}} if tree is not None else {"math": {"d": display, "s": source}}


def split_links(text):
    out, start = [], 0
    for found in LINK.finditer(text):
        if found.start() > 0 and text[found.start() - 1] == "!":
            continue  # an image written inside a line stays text
        if start < found.start():
            out.append(text[start:found.start()])
        address = found.group(2)
        out.append({"link": emphasis(atoms(found.group(1), False)), "to": github_parts(address), "said": address})
        start = found.end()
    if start < len(text):
        out.append(text[start:])
    return out


def atoms(text, links=True):
    """Code spans, math spans and links read out of `text`: a list of strings and runs."""
    out, start = [], 0
    for found in GITHUB_MATH_OR_CODE.finditer(text):
        plain = text[start:found.start()]
        out.append(plain)
        if found.group("math"):
            out.append(math_run(found.group("math")[2:-2], False))
        else:
            out.append({"code": found.group("code")[1:-1]})
        start = found.end()
    out.append(text[start:])
    result = []
    for piece in out:
        if not isinstance(piece, str):
            result.append(piece)
            continue
        for part in split_math(piece):
            if isinstance(part, str) and links:
                result.extend(split_links(part))
            else:
                result.append(part)
    return [piece for piece in result if piece != ""]


class Delimiter(object):
    def __init__(self, char, count, can_open, can_close, raw, style=None):
        self.char, self.count, self.can_open, self.can_close, self.raw, self.style = (
            char, count, can_open, can_close, raw, style)


def tokens(piece, before, after):
    """`piece` as plain strings and delimiters. `before` and `after` are the characters next to it ("." for a run)."""
    out, buf, i, size = [], [], 0, len(piece)
    while i < size:
        char = piece[i]
        if char == "\\" and i + 1 < size and piece[i + 1] in ESCAPABLE:
            buf.append(piece[i + 1])
            i += 2
            continue
        found = HTML_TAG.match(piece, i) if char == "<" else None
        if found:
            if buf:
                out.append("".join(buf))
                buf = []
            opening = not found.group(1)
            out.append(Delimiter("html:" + HTML_STYLE[found.group(2).lower()], 1, opening, not opening, found.group(0),
                                 HTML_STYLE[found.group(2).lower()]))
            i = found.end()
            continue
        if char in "*_":
            end = i
            while end < size and piece[end] == char:
                end += 1
            length = end - i
            prev = piece[i - 1] if i > 0 else before
            nxt = piece[end] if end < size else after
            if length > 3:
                buf.append(piece[i:end])
            else:
                can_open, can_close = not space(nxt), not space(prev)
                if char == "_":
                    can_open = can_open and not (prev != "" and prev.isalnum())
                    can_close = can_close and not (nxt != "" and nxt.isalnum())
                if can_open or can_close:
                    if buf:
                        out.append("".join(buf))
                        buf = []
                    out.append(Delimiter(char, length, can_open, can_close, piece[i:end]))
                else:
                    buf.append(piece[i:end])
            i = end
            continue
        buf.append(char)
        i += 1
    if buf:
        out.append("".join(buf))
    return out


def edge(item, end):
    """The character at the near end of a neighbor string or run, for the rules of emphasis."""
    if not isinstance(item, str) or item == "":
        return "."
    return item[-1] if end == "last" else item[0]


def emphasis(parts):
    """Runs from atoms: bold, italic, superscript and subscript read out of the strings, in the way of CommonMark but
    simpler. A mark with no partner stays text exactly as written."""
    items = []
    for index, part in enumerate(parts):
        if isinstance(part, str):
            before = edge(parts[index - 1], "last") if index > 0 else ""
            after = edge(parts[index + 1], "first") if index + 1 < len(parts) else ""
            items.extend(tokens(part, before, after))
        else:
            items.append(part)
    i = 0
    while i < len(items):
        closer = items[i]
        if isinstance(closer, Delimiter) and closer.can_close and closer.count > 0:
            j = i - 1
            while j >= 0:
                opener = items[j]
                if isinstance(opener, Delimiter) and opener.char == closer.char and opener.can_open and opener.count > 0:
                    break
                j -= 1
            if j >= 0:
                if closer.style is not None:
                    use, style = 1, closer.style
                else:
                    use = 2 if opener.count >= 2 and closer.count >= 2 else 1
                    style = "b" if use == 2 else "i"
                items[j + 1:i] = [("node", style, items[j + 1:i])]
                opener.count -= use
                closer.count -= use
                i = j + 2
                if closer.count == 0:
                    del items[i]
                if opener.count == 0:
                    del items[j]
                    i -= 1
                continue
        i += 1
    return merge(flatten(items, ()))


def flatten(items, styles):
    out = []
    for item in items:
        if isinstance(item, str):
            out.append(styled({"t": item}, styles))
        elif isinstance(item, Delimiter):
            out.append(styled({"t": item.raw if item.style else item.char * item.count}, styles))
        elif isinstance(item, tuple):
            out.extend(flatten(item[2], styles + (item[1],)))
        else:
            out.append(styled(item, styles))
    return out


def styled(run, styles):
    if not styles:
        return run
    if "t" in run:
        out = {"t": run["t"]}
        for name in ("b", "i", "sup", "sub"):
            if name in styles:
                out[name] = True
        return out
    if "link" in run:
        return {"link": [styled(inner, styles) for inner in run["link"]], "to": run["to"], "said": run["said"]}
    return run


def merge(runs):
    out = []
    for run in runs:
        if "t" in run and run["t"] == "":
            continue
        if out and "t" in run and "t" in out[-1] and {k: v for k, v in run.items() if k != "t"} == {
                k: v for k, v in out[-1].items() if k != "t"}:
            out[-1] = dict(out[-1], t=out[-1]["t"] + run["t"])
        else:
            out.append(run)
    return out


def inline(text):
    """The runs of one line or paragraph."""
    if not isinstance(text, str) or text == "":
        return []
    return emphasis(atoms(text))


# ---------------------------------------------------------------- blocks

def expand(line):
    """A line with the tabs of its indent as four spaces."""
    stripped = line.lstrip(" \t")
    return line[:len(line) - len(stripped)].replace("\t", "    ") + stripped


def fence_close(line, char, length):
    stripped = line.strip()
    return len(stripped) >= length and stripped == char * len(stripped)


def without_comments(lines):
    """The lines without HTML comments, outside fenced code and outside a code span on the line."""
    out, comment, fence = [], False, None
    for line in lines:
        if comment:
            end = line.find("-->")
            if end < 0:
                out.append("")
                continue
            line, comment = line[end + 3:], False
        elif fence is not None:
            out.append(line)
            if fence_close(line, fence[0], len(fence)):
                fence = None
            continue
        else:
            found = FENCE.match(line)
            if found:
                fence = found.group(1)
                out.append(line)
                continue
        while True:
            at = line.find("<!--")
            if at < 0 or line[:at].count("`") % 2 == 1:
                break
            end = line.find("-->", at + 4)
            if end < 0:
                line, comment = line[:at], True
                break
            line = line[:at] + line[end + 3:]
        out.append(line)
    return out


def split_cells(line):
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def is_table_line(line):
    stripped = line.strip()
    return len(stripped) > 1 and stripped.startswith("|") and stripped.endswith("|")


def is_separator(line):
    if not is_table_line(line):
        return False
    cells = split_cells(line)
    return bool(cells) and all(SEPARATOR.fullmatch(cell) for cell in cells if cell) and any(cells)


def pipe_table(lines, i):
    """(table, next line) for the pipe table that starts at line i, else None."""
    if not (is_table_line(lines[i]) and i + 1 < len(lines) and is_separator(lines[i + 1])):
        return None
    end = i + 2
    while end < len(lines) and is_table_line(lines[end]):
        end += 1
    alignment = []
    for cell in split_cells(lines[i + 1]):
        alignment.append("c" if cell.startswith(":") and cell.endswith(":") else "r" if cell.endswith(":") else
                         "l" if cell.startswith(":") else None)
    head = split_cells(lines[i])
    body = [split_cells(line) for line in lines[i + 2:end]]
    cut = len(body) > CAP_TABLE_ROWS or len(head) > CAP_TABLE_COLUMNS or any(len(r) > CAP_TABLE_COLUMNS for r in body)

    def row(cells, is_head):
        return [{"r": inline(cell.replace("\\|", "|")), "rs": 1, "cs": 1, "h": is_head,
                 "a": alignment[at] if at < len(alignment) else None}
                for at, cell in enumerate(cells[:CAP_TABLE_COLUMNS])]

    return {"head": [row(head, True)], "rows": [row(cells, False) for cells in body[:CAP_TABLE_ROWS]], "cut": cut,
            "caption": None}, end


class TableReader(html.parser.HTMLParser):
    """The subset of an HTML table: table, thead, tbody, tfoot, tr, th, td and caption; rowspan, colspan and align on
    a cell; b, strong, i, em, code, sup, sub and br inside a cell. Nothing else is kept, and the text of script and
    style is dropped."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth, self.bad, self.closed = 0, False, False
        self.rows, self.row, self.cell = [], None, None
        self.in_head, self.saw_head, self.skip = False, False, 0
        self.caption, self.in_caption, self.open_tags, self.in_code = None, False, [], False

    def target(self):
        if self.in_caption:
            return self.caption
        return self.cell["parts"] if self.cell is not None else None

    def span(self, value, top):
        if value is None:
            return 1
        value = value.strip()
        if not re.fullmatch(r"[0-9]{1,2}", value) or not 1 <= int(value) <= top:
            self.bad = True
            return 1
        return int(value)

    def finish_text(self, parts):
        if self.in_code:
            parts.append("`")
            self.in_code = False
        while self.open_tags:
            parts.append("</%s>" % self.open_tags.pop())
        return inline(re.sub(r"[ \t\r\n\f]+", " ", "".join(parts)).strip())

    def end_cell(self):
        if self.cell is not None:
            cell, self.cell = self.cell, None
            parts = cell.pop("parts")
            cell["r"] = self.finish_text(parts)
            self.row["cells"].append(cell)

    def end_row(self):
        self.end_cell()
        if self.row is not None:
            if self.row["cells"]:
                self.rows.append(self.row)
            self.row = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
            return
        if self.skip or self.closed:
            return
        if tag == "table":
            self.depth += 1
            if self.depth > 1:
                self.bad = True
            return
        if self.depth != 1:
            return
        attributes = dict(attrs)
        if tag == "caption":
            self.end_row()
            self.in_caption, self.caption = True, []
        elif tag in ("thead", "tbody", "tfoot"):
            self.end_row()
            self.in_head = tag == "thead"
            self.saw_head = self.saw_head or self.in_head
        elif tag == "tr":
            self.end_row()
            self.row = {"head": self.in_head, "cells": []}
        elif tag in ("td", "th"):
            self.end_cell()
            if self.row is None:
                self.row = {"head": self.in_head, "cells": []}
            align = (attributes.get("align") or "").strip().lower()
            self.cell = {"rs": self.span(attributes.get("rowspan"), CAP_TABLE_ROWS),
                         "cs": self.span(attributes.get("colspan"), CAP_TABLE_COLUMNS), "h": tag == "th",
                         "a": {"left": "l", "center": "c", "right": "r"}.get(align), "parts": []}
        elif self.target() is not None:
            parts = self.target()
            if tag in HTML_STYLE:
                self.open_tags.append(HTML_STYLE[tag])
                parts.append("<%s>" % HTML_STYLE[tag])
            elif tag == "code" and not self.in_code:
                self.in_code = True
                parts.append("`")
            elif tag == "br":
                parts.append(" ")

    def handle_startendtag(self, tag, attrs):
        if tag == "br" and self.depth == 1 and not self.skip and self.target() is not None:
            self.target().append(" ")
        elif tag not in ("script", "style", "table"):
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or self.closed:
            return
        if tag == "table":
            self.depth -= 1
            if self.depth <= 0:
                self.end_row()
                self.closed = True
            return
        if self.depth != 1:
            return
        if tag == "caption" and self.in_caption:
            self.in_caption = False
            self.caption = self.finish_text(self.caption)
        elif tag == "tr":
            self.end_row()
        elif tag in ("td", "th"):
            self.end_cell()
        elif tag in ("thead", "tbody", "tfoot"):
            self.end_row()
            self.in_head = False
        elif self.target() is not None:
            parts = self.target()
            if tag in HTML_STYLE and HTML_STYLE[tag] in self.open_tags:
                while self.open_tags:
                    name = self.open_tags.pop()
                    parts.append("</%s>" % name)
                    if name == HTML_STYLE[tag]:
                        break
            elif tag == "code" and self.in_code:
                self.in_code = False
                parts.append("`")

    def handle_data(self, data):
        if self.skip or self.closed or self.depth != 1:
            return
        parts = self.target()
        if parts is not None:
            parts.append(data.replace("`", "") if self.in_code else data)

    def finish(self):
        self.end_row()
        if self.in_caption:
            self.in_caption = False
            self.caption = self.finish_text(self.caption)


def html_table(text):
    """The table block of an HTML table, or None when it is outside the subset."""
    if len(text) > HTML_TABLE_CHARACTERS:
        return None
    reader = TableReader()
    try:
        reader.feed(text)
        reader.close()
    except Exception:
        return None
    reader.finish()
    if reader.bad or not reader.closed or not reader.rows or len(reader.rows) > CAP_TABLE_ROWS:
        return None
    if any(sum(cell["cs"] for cell in row["cells"]) > CAP_TABLE_COLUMNS for row in reader.rows):
        return None
    rows = reader.rows
    if reader.saw_head:
        head_rows = [row for row in rows if row["head"]]
        body = [row for row in rows if not row["head"]]
    else:
        count = 0
        while count < len(rows) and all(cell["h"] for cell in rows[count]["cells"]):
            count += 1
        head_rows, body = rows[:count], rows[count:]

    def shaped(row):
        return [{"r": cell["r"], "rs": cell["rs"], "cs": cell["cs"], "h": cell["h"], "a": cell["a"]}
                for cell in row["cells"]]

    return {"head": [shaped(row) for row in head_rows], "rows": [shaped(row) for row in body], "cut": False,
            "caption": reader.caption if reader.caption else None}


def html_region(lines, i):
    """(text of the table, next line, rest of its last line, closed) for the HTML table that starts at line i."""
    depth, j = 0, i
    while j < len(lines):
        line = lines[j]
        for found in TABLE_TAG.finditer(line):
            depth += -1 if found.group(1) else 1
            if depth <= 0:
                gt = line.find(">", found.end())
                cut = gt + 1 if gt >= 0 else len(line)
                text = "\n".join(lines[i:j] + [line[:cut]])
                return text, j, line[cut:], True
        j += 1
    return "\n".join(lines[i:]), len(lines), "", False


def display_math(lines, i):
    """(source, next line) of the $$ ... $$ formula that starts at line i, on one line or several, else None."""
    first = lines[i].strip()[2:]
    if first.rstrip().endswith("$$"):
        parts, end = [first.rstrip()[:-2]], i
    else:
        parts, end = [first], i + 1
        while end < len(lines) and end - i <= 60:
            line = lines[end].strip()
            if line.endswith("$$"):
                parts.append(line[:-2])
                break
            parts.append(line)
            end += 1
        else:
            return None
    source = "\n".join(parts).strip()
    if not source or "$$" in source:
        return None
    return source, end + 1


def block_start(lines, i):
    """True when line i starts a block of its own, so that a paragraph or a list item stops before it."""
    line = lines[i]
    stripped = line.strip()
    if not stripped:
        return True
    if FENCE.match(line) or stripped.startswith("$$") or BLOCK_HEADING.match(line) or HEADING.match(line) \
            or THEMATIC.match(line) or IMAGE_LINE.fullmatch(stripped) or stripped.startswith(">") \
            or stripped.lower().startswith("<table"):
        return True
    if is_table_line(stripped) and i + 1 < len(lines) and is_separator(lines[i + 1]):
        return True
    found = LIST_ITEM.match(expand(line))
    return bool(found) and (not found.group(2)[0] in "0123456789" or found.group(2)[:-1] == "1")


def read_list(lines, i):
    """(block, next line) of the list that starts at line i; see the module's notes on nesting."""
    flat, size = [], len(lines)
    base = top_ordered = None
    while i < size:
        line = expand(lines[i])
        if not line.strip():
            j = i + 1
            while j < size and not lines[j].strip():
                j += 1
            if flat and j < size and LIST_ITEM.match(expand(lines[j])) and not THEMATIC.match(lines[j]):
                i = j
                continue
            break
        found = LIST_ITEM.match(line) if not THEMATIC.match(line) else None
        if found:
            indent, marker = len(found.group(1)), found.group(2)
            ordered = marker[0] in "0123456789"
            if base is None:
                base, top_ordered = indent, ordered
            if indent <= base + 1 and ordered != top_ordered:
                break
            flat.append((max(0, indent - base), ordered, int(marker[:-1]) if ordered else 0, found.group(3).strip()))
            i += 1
        elif flat and not block_start(lines, i):
            kept = flat[-1]
            flat[-1] = (kept[0], kept[1], kept[2], kept[3] + " " + line.strip())
            i += 1
        else:
            break
    levels, last, top = [], {}, None

    def new_list(ordered, number):
        if ordered:
            return {"ol": [], "start": number if 1 <= number <= 9999 else 1}
        return {"ul": []}

    for indent, ordered, number, text in flat:
        depth = 0
        while depth < len(levels) and indent >= levels[depth] + 2:
            depth += 1
        if depth >= MAX_LIST_DEPTH:
            last[MAX_LIST_DEPTH - 1]["text"] += " " + text
            del levels[MAX_LIST_DEPTH:]
            continue
        del levels[depth:]
        levels.append(indent)
        item = {"text": text, "sub": None}
        if depth == 0:
            if top is None:
                top = new_list(ordered, number)
            top["ol" if "ol" in top else "ul"].append(item)
        else:
            parent = last[depth - 1]
            if parent["sub"] is None:
                parent["sub"] = new_list(ordered, number)
            parent["sub"]["ol" if "ol" in parent["sub"] else "ul"].append(item)
        last[depth] = item

    def finish(block):
        if block is None:
            return None
        key = "ol" if "ol" in block else "ul"
        block[key] = [{"r": inline(item["text"]), "sub": finish(item["sub"])} for item in block[key]]
        return block

    return finish(top), i


def blocks(text):
    """The blocks of a text, see the module's notes."""
    if not isinstance(text, str) or not text.strip():
        return []
    lines = without_comments(text.split("\n"))
    out, paragraph, i, size = [], [], 0, len(lines)

    def flush():
        if paragraph:
            runs = inline(" ".join(paragraph))
            if runs:
                out.append({"p": runs})
            del paragraph[:]

    while i < size:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush()
            i += 1
            continue
        found = FENCE.match(line)
        if found:
            flush()
            char, length, info = found.group(1)[0], len(found.group(1)), found.group(2).split()
            body, i = [], i + 1
            while i < size and not fence_close(lines[i], char, length):
                body.append(lines[i])
                i += 1
            i += 1  # the closing fence, or the end of a text that was cut
            source = "\n".join(body)
            if info and info[0].lower() == "math" and source.strip():
                out.append({"math": math_run(source.strip(), True)["math"]})
            else:
                out.append({"code": source})
            continue
        if stripped.startswith("$$"):
            read = display_math(lines, i)
            if read:
                flush()
                out.append({"math": math_run(read[0], True)["math"]})
                i = read[1]
                continue
        found = BLOCK_HEADING.match(line)
        if found:
            flush()
            end = i + 1
            while end < size and not BLOCK_END.match(lines[end]):
                end += 1
            out.append({"block": {"kind": found.group(1).lower(), "id": found.group(2)}})
            i = end
            continue
        found = HEADING.match(line)
        if found:
            flush()
            out.append({"h": {"level": min(3, max(1, len(found.group(1)) - 2)), "r": inline(found.group(2))}})
            i += 1
            continue
        if THEMATIC.match(line):
            flush()
            i += 1
            continue
        if IMAGE_LINE.fullmatch(stripped):
            flush()
            i += 1
            continue
        if stripped.lower().startswith("<table"):
            flush()
            region, end, rest, closed = html_region(lines, i)
            table = html_table(region) if closed else None
            out.append({"table": table} if table else {"note": {"key": "html-table"}})
            if rest.strip():
                lines[end] = rest
                i = end
            else:
                i = end + 1
            continue
        if is_table_line(stripped):
            read = pipe_table(lines, i)
            if read:
                flush()
                out.append({"table": read[0]})
                i = read[1]
                continue
        if stripped.startswith(">"):
            flush()
            quoted = []
            while i < size and lines[i].strip().startswith(">"):
                quoted.append(lines[i].strip()[1:].strip())
                i += 1
            runs = inline(" ".join(part for part in quoted if part))
            if runs:
                out.append({"quote": runs})
            continue
        item = LIST_ITEM.match(expand(line))
        if item and (not paragraph or block_start(lines, i)):
            flush()
            block, i = read_list(lines, i)
            if block is not None:
                out.append(block)
            continue
        paragraph.append(stripped)
        i += 1
    flush()
    return out


# ---------------------------------------------------------------- formulas

GREEK = {"alpha": "\u03b1", "beta": "\u03b2", "gamma": "\u03b3", "delta": "\u03b4", "epsilon": "\u03f5",
         "varepsilon": "\u03b5", "zeta": "\u03b6", "eta": "\u03b7", "theta": "\u03b8", "iota": "\u03b9",
         "kappa": "\u03ba", "lambda": "\u03bb", "mu": "\u03bc", "nu": "\u03bd", "xi": "\u03be", "pi": "\u03c0",
         "rho": "\u03c1", "sigma": "\u03c3", "tau": "\u03c4", "upsilon": "\u03c5", "phi": "\u03d5", "varphi": "\u03c6",
         "chi": "\u03c7", "psi": "\u03c8", "omega": "\u03c9", "Gamma": "\u0393", "Delta": "\u0394", "Theta": "\u0398",
         "Lambda": "\u039b", "Xi": "\u039e", "Pi": "\u03a0", "Sigma": "\u03a3", "Upsilon": "\u03a5", "Phi": "\u03a6",
         "Psi": "\u03a8", "Omega": "\u03a9"}
RELATIONS = {"sim": "\u223c", "le": "\u2264", "leq": "\u2264", "ge": "\u2265", "geq": "\u2265", "ne": "\u2260",
             "neq": "\u2260", "pm": "\u00b1", "times": "\u00d7", "cdot": "\u22c5", "approx": "\u2248", "to": "\u2192",
             "rightarrow": "\u2192", "in": "\u2208", "propto": "\u221d", "ldots": "\u2026", "dots": "\u2026",
             "cdots": "\u22ef"}
IDENTIFIERS = {"infty": "\u221e", "partial": "\u2202"}
BIG = {"sum": "\u2211", "prod": "\u220f", "int": "\u222b"}
FUNCTIONS = ("log", "ln", "exp", "sin", "cos", "tan", "max", "min", "lim", "Pr", "Var", "Cov")
ACCENTS = {"hat": "^", "bar": "\u00af", "tilde": "~", "dot": "\u02d9", "vec": "\u2192", "overline": "\u00af"}
SPACES = {",": "0.1667em", ":": "0.2222em", ";": "0.2778em", "!": "-0.1667em", "quad": "1em", "qquad": "2em",
          " ": "0.5em"}
ESCAPES = "%{}&#_$"
OPERATOR_CHARS = "+-=<>/()[]|,.;:!*'"
TEXT_CHARS = re.compile(r"[A-Za-z0-9 .,;:!?'()+=/\[\]-]+")
NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?")
COMMAND = re.compile(r"[A-Za-z]+")
DELIMITERS = "()[]|"


def alphabet(capital, small, digits, holes):
    table = {}
    for offset in range(26):
        table[chr(65 + offset)] = chr(capital + offset) if capital else None
        table[chr(97 + offset)] = chr(small + offset) if small else None
    for key, value in holes.items():
        table[key] = value
    for offset in range(10):
        table[str(offset)] = chr(digits + offset) if digits else None
    return {key: value for key, value in table.items() if value}


ALPHABETS = {
    "mathbf": alphabet(0x1D400, 0x1D41A, 0x1D7CE, {}),
    "mathcal": alphabet(0x1D49C, 0x1D4B6, 0, {"B": "\u212c", "E": "\u2130", "F": "\u2131", "H": "\u210b",
                                              "I": "\u2110", "L": "\u2112", "M": "\u2133", "R": "\u211b",
                                              "e": "\u212f", "g": "\u210a", "o": "\u2134"}),
    "mathbb": alphabet(0x1D538, 0x1D552, 0x1D7D8, {"C": "\u2102", "H": "\u210d", "N": "\u2115", "P": "\u2119",
                                                    "Q": "\u211a", "R": "\u211d", "Z": "\u2124"}),
}


class FormulaStop(Exception):
    """The formula is outside the subset."""


class Formula(object):
    def __init__(self, source, display):
        self.s, self.n, self.i, self.display, self.count = source, len(source), 0, display, 0

    def make(self, name, c=None, t=None, **extra):
        self.count += 1
        if self.count > MAX_NODES:
            raise FormulaStop()
        node = {"m": name}
        if c is not None:
            node["c"] = c
        if t is not None:
            node["t"] = t
        node.update(extra)
        return node

    def peek(self):
        while self.i < self.n and self.s[self.i] in " \t\n":
            self.i += 1
        return self.s[self.i] if self.i < self.n else ""

    def at_command(self, name):
        end = self.i + 1 + len(name)
        return self.s.startswith("\\" + name, self.i) and not letter(self.s[end] if end < self.n else "")

    def row(self, depth, stop):
        if depth > MAX_DEPTH:
            raise FormulaStop()
        out = []
        while True:
            char = self.peek()
            if char == "":
                if stop is not None:
                    raise FormulaStop()
                break
            if char == "}":
                if stop == "}":
                    break
                raise FormulaStop()
            if char == "\\" and self.at_command("right"):
                if stop == "right":
                    break
                raise FormulaStop()
            out.append(self.scripted(depth))
        return out

    def scripted(self, depth):
        base, big = self.atom(depth)
        sub = sup = None
        while self.peek() in ("_", "^") and self.i < self.n:
            which = self.s[self.i]
            self.i += 1
            argument = self.argument(depth)
            if which == "_":
                if sub is not None:
                    raise FormulaStop()
                sub = argument
            else:
                if sup is not None:
                    raise FormulaStop()
                sup = argument
        if sub is None and sup is None:
            return base
        if base["m"] == "mspace":
            raise FormulaStop()
        under = big and self.display
        if sub is not None and sup is not None:
            return self.make("munderover" if under else "msubsup", c=[base, sub, sup])
        if sub is not None:
            return self.make("munder" if under else "msub", c=[base, sub])
        return self.make("mover" if under else "msup", c=[base, sup])

    def argument(self, depth):
        """The argument of a script, a fraction, a root or an accent: one group or one token."""
        char = self.peek()
        if char == "":
            raise FormulaStop()
        if char == "{":
            return self.group(depth)
        if digit(char):
            self.i += 1
            return self.make("mn", t=char)
        return self.atom(depth)[0]

    def group(self, depth):
        self.i += 1
        inner = self.row(depth + 1, "}")
        self.i += 1
        if not inner:
            raise FormulaStop()
        return inner[0] if len(inner) == 1 else self.make("mrow", c=inner)

    def atom(self, depth):
        """(node, True when it is a big operator that takes limits)."""
        char = self.peek()
        if char == "{":
            return self.group(depth), False
        found = NUMBER.match(self.s, self.i) if digit(char) else None
        if found:
            self.i = found.end()
            return self.make("mn", t=found.group(0)), False
        if letter(char):
            self.i += 1
            return self.make("mi", t=char), False
        if char == "\\":
            return self.command(depth)
        if char != "" and char in OPERATOR_CHARS:
            self.i += 1
            return self.make("mo", t={"-": "\u2212", "'": "\u2032"}.get(char, char)), False
        raise FormulaStop()

    def command(self, depth):
        self.i += 1
        if self.i >= self.n:
            raise FormulaStop()
        found = COMMAND.match(self.s, self.i)
        if found:
            name, self.i = found.group(0), found.end()
        else:
            name, self.i = self.s[self.i], self.i + 1
        if name in GREEK:
            return self.make("mi", t=GREEK[name]), False
        if name in RELATIONS:
            return self.make("mo", t=RELATIONS[name]), False
        if name in IDENTIFIERS:
            return self.make("mi", t=IDENTIFIERS[name]), False
        if name in BIG:
            return self.make("mo", t=BIG[name]), True
        if name in FUNCTIONS:
            return self.make("mi", t=name, v="normal"), False
        if name in SPACES:
            return self.make("mspace", w=SPACES[name]), False
        if len(name) == 1 and name in ESCAPES:
            return self.make("mo", t=name), False
        if name == "frac":
            return self.make("mfrac", c=[self.argument(depth), self.argument(depth)]), False
        if name == "sqrt":
            if self.peek() in ("[", ""):
                raise FormulaStop()
            return self.make("msqrt", c=[self.argument(depth)]), False
        if name in ACCENTS:
            base = self.argument(depth)
            return self.make("mover", c=[base, self.make("mo", t=ACCENTS[name])], acc=True), False
        if name == "left":
            return self.fenced(depth), False
        if name in ("mathrm", "operatorname"):
            return self.upright(depth), False
        if name in ("text", "textrm"):
            return self.text(), False
        if name in ALPHABETS:
            return self.alphabetic(name), False
        raise FormulaStop()

    def delimiter(self):
        char = self.peek()
        self.i += 1 if char != "" else 0
        if char == ".":
            return None
        if char == "" or char not in DELIMITERS:
            raise FormulaStop()
        return self.make("mo", t=char)

    def fenced(self, depth):
        opening = self.delimiter()
        inner = self.row(depth + 1, "right")
        self.i += len("\\right")
        closing = self.delimiter()
        parts = ([opening] if opening else []) + inner + ([closing] if closing else [])
        if not parts:
            raise FormulaStop()
        return self.make("mrow", c=parts)

    def upright(self, depth):
        if self.peek() == "{":
            self.i += 1
            inner = self.row(depth + 1, "}")
            self.i += 1
        else:
            inner = [self.argument(depth)]
        if not inner:
            raise FormulaStop()
        if all(node["m"] == "mi" and len(node["t"]) == 1 and letter(node["t"]) and "v" not in node for node in inner):
            return self.make("mi", t="".join(node["t"] for node in inner), v="normal")

        def mark(node):
            if node["m"] == "mi" and "v" not in node:
                node["v"] = "normal"
            for child in node.get("c", []):
                mark(child)

        for node in inner:
            mark(node)
        return inner[0] if len(inner) == 1 else self.make("mrow", c=inner)

    def text(self):
        if self.peek() != "{":
            raise FormulaStop()
        end = self.s.find("}", self.i + 1)
        if end < 0:
            raise FormulaStop()
        words = self.s[self.i + 1:end]
        if not TEXT_CHARS.fullmatch(words):
            raise FormulaStop()
        self.i = end + 1
        # a space at either end would be trimmed by the browser, so it becomes a no-break space
        edge_left = len(words) - len(words.lstrip(" "))
        edge_right = len(words) - len(words.rstrip(" "))
        words = "\u00a0" * edge_left + words.strip(" ") + "\u00a0" * edge_right if words.strip(" ") else words
        return self.make("mtext", t=words)

    def alphabetic(self, name):
        table = ALPHABETS[name]
        if self.peek() == "{":
            end = self.s.find("}", self.i + 1)
            if end < 0:
                raise FormulaStop()
            chars, self.i = self.s[self.i + 1:end], end + 1
        else:
            chars = self.s[self.i:self.i + 1]
            self.i += 1
        if chars == "" or any(char not in table for char in chars):
            raise FormulaStop()
        nodes = [self.make("mn" if digit(char) else "mi", t=table[char]) for char in chars]
        return nodes[0] if len(nodes) == 1 else self.make("mrow", c=nodes)


def formula(source, display):
    """The tree of a formula; raises FormulaStop when it is outside the subset."""
    if not isinstance(source, str) or len(source) > (MAX_DISPLAY if display else MAX_INLINE):
        raise FormulaStop()
    reader = Formula(source, bool(display))
    nodes = reader.row(0, None)
    if not nodes:
        raise FormulaStop()
    return reader.make("mrow", c=nodes)


def tex(source, display):
    """The tree of a formula written in common LaTeX: {"m": name, "c": [nodes]} for a layout element and
    {"m": name, "t": text} for a token, with "v": "normal" on an upright identifier, "w": width on a space and
    "acc": True on an accent. The root is an mrow. None when the formula is outside the subset (a command that is not
    listed, an unclosed group, a second subscript or superscript on one base, or a limit of the size: 400 characters
    inline and 2,000 in a display formula, 8 levels of groups, 300 nodes), and never an exception."""
    try:
        return formula(source, display)
    except FormulaStop:
        return None
    except Exception:  # whatever the text holds, the page shows the source
        return None
