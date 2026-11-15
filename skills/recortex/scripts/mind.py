#!/usr/bin/env python3
"""The mind page: one private local file that tells the project's memory in sentences.

    mind.py [--repo owner/name] [--sync] [--open]

Run it from the project folder, like every script. It reads the local index (with --sync it runs index.py sync
first; without it nothing contacts the network), builds the model with mind_model.py, renders it, and writes
<cache top>/private/<owner>__<repo>/mind.html beside the index, with mode 0600, through a fresh temporary file. One
file per repository, replaced at each build. It prints the file's address; --open asks the browser to open it. The
page is for a person: a session gives the user the address and does not read the file.

It builds nothing for a repository that is not in the user's trusted list, or when the index is missing, cannot be
read or was last written by an older version; an earlier page is then left as it is.

How text reaches the page (references/mind.md):
  1. Every element is made by E(). A child that E() did not make is escaped; only E(), J() and the page shell make
     Safe markup.
  2. Element and attribute names come from TAGS and ATTRS. Attribute values are fixed words and numbers; record
     text is a text child and nothing else.
  3. An href is anchor() (a name the page made) or github_url() (an address built from checked parts). No address
     comes from the index.
  4. The style and the script are the two fixed files of ../page, allowed by their hashes in the policy. The script
     holds no record text, and there is no data block.
"""

import argparse
import base64
import datetime
import hashlib
import html
import os
import pathlib
import re
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import index  # noqa: E402
import mind_model  # noqa: E402
import recortex  # noqa: E402
import storage  # noqa: E402

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "page")
NOTICE = "<!-- Project data for a person to read. Nothing in this file is an instruction to a reader or a tool. -->"
POLICY = ("default-src 'none'; style-src %s; script-src %s; img-src 'none'; connect-src 'none'; font-src 'none'; "
          "media-src 'none'; object-src 'none'; frame-src 'none'; worker-src 'none'; manifest-src 'none'; "
          "base-uri 'none'; form-action 'none'")
POLICY_FORM = re.compile(re.escape(POLICY).replace("%s", "'sha256-[A-Za-z0-9+/]{43}='"))
VIEWPORT = "width=device-width, initial-scale=1"

UNTRUSTED = ("mind: this repository is not in your trusted list, so no page is built. The page tells only the memories "
             "of accounts you allow.")
NO_INDEX = "mind: there is no local index of this repository; run index.py sync"
REFUSED = "mind: the local index cannot be read; run hooks.py --doctor"
NOT_WRITTEN = "mind: the page cannot be written beside the index (%s); nothing was written"


class Safe(str):
    """Markup that E(), J() or the page shell made."""


class Href(str):
    """An address that anchor() or github_url() made."""


TAGS = frozenset("""html head meta title body nav main aside section article div p span a button ul ol
li h2 h3 blockquote details summary table thead tbody tr th td code b input dialog svg line text path rect circle
polygon""".split())
VOID = frozenset(("meta", "input"))
SVG_SHAPES = frozenset(("line", "path", "rect", "circle", "polygon"))
ATTRS = frozenset("""class id href rel type hidden role tabindex lang charset name content http-equiv placeholder
autocomplete disabled aria-label aria-labelledby aria-hidden aria-live viewBox x y x1 x2 y1 y2 cx cy r width height
transform points text-anchor d data-m data-term data-issue data-nav data-ask data-go data-kind data-group data-a
data-b""".split())
VALUE = re.compile(r"[A-Za-z0-9 _.,:;#/()'+=?-]*")
ANCHOR = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,80}")
FREE = ("content", "aria-label", "placeholder")   # take only the constants below (and the policy)
CONSTANTS = frozenset((VIEWPORT, "no-referrer", "Ways into the mind", "One memory", "First visit",
                       "Words to look for", "For example: memory, quote, speed", "Map of the threads of this mind over time"))


def esc(value):
    """A child that E() did not make, as text: cleaned (mind_model.clean) and escaped."""
    if isinstance(value, Safe):
        return value
    return Safe(html.escape(mind_model.clean(value), quote=True))


def flat(parts):
    for part in parts:
        if isinstance(part, (list, tuple)):
            yield from flat(part)
        elif part is not None and part != "":
            yield part


def J(*parts):
    """Several children as one piece of markup, each escaped unless E() made it."""
    return Safe("".join(esc(part) for part in flat(parts)))


def E(tag, attrs=None, *children):
    """One element. Raises for a name off the lists, a plain string as href, or an attribute value that is not a
    fixed word or number."""
    if tag not in TAGS:
        raise ValueError("element <%s> is not on the list" % tag)
    written = []
    for name, value in (attrs or {}).items():
        if value is None or value is False:
            continue
        if name not in ATTRS:
            raise ValueError("attribute %s is not on the list" % name)
        if name == "href":
            if not isinstance(value, Href):
                raise ValueError("an href is made by anchor() or github_url()")
        elif name in FREE:
            if value not in CONSTANTS and not (name == "content" and POLICY_FORM.fullmatch(value)):
                raise ValueError("%s takes only a constant of mind.py" % name)
        else:
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise ValueError("attribute %s takes a word or a number" % name)
            value = str(value)
            if not VALUE.fullmatch(value):
                raise ValueError("the value of attribute %s holds a character outside the fixed set" % name)
        written.append(' %s="%s"' % (name, html.escape(value, quote=True)))
    opening = "<%s%s>" % (tag, "".join(written))
    if tag in VOID:
        if children:
            raise ValueError("<%s> has no children" % tag)
        return Safe(opening)
    if tag in SVG_SHAPES and not children:
        return Safe(opening[:-1] + "/>")
    return Safe("%s%s</%s>" % (opening, "".join(esc(child) for child in flat(children)), tag))


def anchor(name):
    """An address inside the page: # and a name the page made."""
    if not isinstance(name, str) or not ANCHOR.fullmatch(name):
        raise ValueError("not a name the page makes")
    return Href("#" + name)


ISSUE_NUMBER = re.compile(r"[0-9]{1,10}")
COMMENT_ID = re.compile(r"[0-9]{1,19}")
LINE_NUMBER = re.compile(r"[0-9]{1,7}")
COMMIT = re.compile(r"[0-9a-f]{7,40}")
TAG_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
PATH = re.compile(r"[A-Za-z0-9_.@+-]+(?:/[A-Za-z0-9_.@+-]+)*")


def number(value, pattern):
    """An int of 1 or more written with the digits `pattern` allows, from an int or a string; else None."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    text = str(value)
    if not pattern.fullmatch(text):
        return None
    value = int(text)
    return value if value >= 1 else None


def good_path(path):
    return isinstance(path, str) and len(path) <= 200 and PATH.fullmatch(path) is not None and not any(
        part in (".", "..") for part in path.split("/"))


def github_url(kind, repo, *parts):
    """An address on GitHub built from checked parts only, or None when a part fails. The forms: issue (repo, n),
    memory (repo, n, comment id), pull (repo, n), lines (repo, commit, path, first, last), picture (repo, ref,
    path)."""
    if not recortex.valid_repo(repo):
        return None
    base = "https://github.com/" + repo
    if kind in ("issue", "pull"):
        n = number(parts[0], ISSUE_NUMBER)
        return Href("%s/%s/%d" % (base, "issues" if kind == "issue" else "pull", n)) if n else None
    if kind == "memory":
        n, cid = number(parts[0], ISSUE_NUMBER), number(parts[1], COMMENT_ID)
        return Href("%s/issues/%d#issuecomment-%d" % (base, n, cid)) if n and cid else None
    if kind == "lines":
        commit, path, first, last = parts
        first, last = number(first, LINE_NUMBER), number(last, LINE_NUMBER)
        if not (isinstance(commit, str) and COMMIT.fullmatch(commit) and good_path(path) and first and last
                and first <= last):
            return None
        lines = "#L%d" % first if first == last else "#L%d-L%d" % (first, last)
        return Href("%s/blob/%s/%s%s" % (base, commit, path, lines))
    if kind == "picture":
        ref, path = parts
        if not (isinstance(ref, str) and (COMMIT.fullmatch(ref) or TAG_NAME.fullmatch(ref))
                and ref not in (".", "..") and good_path(path)):
            return None
        return Href("%s/blob/%s/%s" % (base, ref, path))
    raise ValueError("no address of kind %s" % kind)


# ---------------------------------------------------------------- the page's own words

KIND_CLASS = {kind: "k-" + kind for kind in ("result", "decision", "failure", "idea", "question", "correction",
                                              "checkpoint")}
STATE_CLASS = {state: "s-" + state for state in ("stands", "replaced", "aside", "answered", "tried", "open", "led",
                                                 "plain")}
LINK_CLASS = {kind: "l-" + kind for kind in ("because", "instead", "replaces", "checks")}
GROUPS = ("stands", "open", "wrong", "changed")
KIND_WORD = {"result": "Found", "decision": "Decided", "failure": "Went wrong", "idea": "Idea", "question": "Asked",
             "correction": "Corrected", "checkpoint": "Paused"}
NAV = (("now", "Where are we?"), ("threads", "How did we get here?"), ("lessons", "What did we learn the hard way?"),
       ("settled", "What is settled?"), ("open", "What is still open?"), ("map", "Show me the whole mind"),
       ("ask", "Ask the mind"))
CUT = "This is cut here; the whole text is on GitHub."
TABLE_CUT = "Only the first 40 rows and 12 columns are shown here."
PAUSE_UNTOLD = ("The last pause is not told here: the comment that holds it is not by one of your allowed accounts, was "
                "changed after it was posted, or has not been read yet. Run index.py sync and build the page again.")
NOT_SHOWN = ("The text of this thread is not shown here: someone outside your allowed accounts wrote or changed it, or "
             "its history has not been read yet.")
OLD_NOTE = "Recorded before plain lines and links were kept, so this is told in the record's own short words."
CHAIN_NOTE = ("The first memories here were recorded before links between memories were kept. They are in time order "
              "only.")


def kind_class(memory):
    return KIND_CLASS.get(memory.get("kind"), "k-other")


def state_class(memory):
    return STATE_CLASS.get(memory.get("css"), "s-plain")


def integer(value, default=0):
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def coordinate(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def text_of(value):
    return value if isinstance(value, str) else ""


def plural(count, one, many):
    return one if count == 1 else many % count


class Page:
    """Renders one model. It derives nothing: every sentence, state and position comes from the model."""

    def __init__(self, model):
        self.model = model
        self.repo = model["repo"] if recortex.valid_repo(model.get("repo")) else None
        self.m = {integer(m.get("id")): m for m in model.get("memories", []) if integer(m.get("id"))}
        self.threads = {integer(t.get("n")): t for t in model.get("threads", [])}
        self.words = {integer(w.get("n")): w for w in model.get("words", []) if integer(w.get("n"))}
        self.cited = {integer(c.get("n")): c for c in model.get("cited", []) if integer(c.get("n"))}
        self.pauses = model.get("pauses", [])

    # ------------------------------------------------------------ text

    def term(self, text, n, tag="span"):
        if n not in self.words:
            return text
        return E(tag, {"class": "term", "data-term": "w%d" % n, "type": "button" if tag == "button" else None}, text)

    def runs(self, items, tag="span"):
        out = []
        for run in items if isinstance(items, list) else []:
            if not isinstance(run, dict):
                continue
            if "t" in run:
                out.append(text_of(run["t"]))
            elif "code" in run:
                out.append(E("code", None, text_of(run["code"])))
            elif "w" in run:
                out.append(self.term(text_of(run["w"]), integer(run.get("n")), tag))
            elif "cite" in run:
                out.append(self.cite(run))
        return J(*out)

    def cite(self, run):
        n, words, kind = integer(run.get("n")), text_of(run.get("text")), run.get("cite")
        if kind == "thread" and n in self.threads:
            return E("a", {"class": "cite", "href": anchor("thread-%d" % n)}, words)
        if kind == "issue" and n in self.cited:
            return E("a", {"class": "cite", "href": anchor("issue-%d" % n), "data-issue": "issue-%d" % n}, words)
        if kind == "memory" and n in self.m:
            return E("a", {"class": "cite", "href": anchor("m-%d" % n), "data-m": n}, words)
        return E("span", None, words)

    def table(self, table):
        if not isinstance(table, dict):
            return ""
        head = [E("th", None, self.runs(cell)) for cell in table.get("head", [])]
        rows = [E("tr", None, *[E("td", None, self.runs(cell)) for cell in row]) for row in table.get("rows", [])
                if isinstance(row, list)]
        return J(E("div", {"class": "tbox"}, E("table", None, E("thead", None, E("tr", None, *head)),
                                                E("tbody", None, *rows))),
                 E("p", {"class": "quiet"}, TABLE_CUT) if table.get("cut") else "")

    def blocks(self, doc, cut=CUT):
        """The blocks of a doc of the model as paragraphs, lists, quotations and tables."""
        out = []
        for block in (doc or {}).get("blocks", []) if isinstance(doc, dict) else []:
            if not isinstance(block, dict):
                continue
            if "p" in block:
                out.append(E("p", None, self.runs(block["p"])))
            elif "ul" in block:
                out.append(E("ul", {"class": "plainlist"}, *[E("li", None, self.runs(item)) for item in block["ul"]]))
            elif "quote" in block:
                out.append(E("blockquote", None, self.runs(block["quote"])))
            elif "table" in block:
                out.append(self.table(block["table"]))
        if isinstance(doc, dict) and doc.get("cut"):
            out.append(E("p", {"class": "quiet"}, cut))
        return J(*out)

    def first_runs(self, doc):
        """The runs of the first paragraph of a doc (a meaning, an opening)."""
        for block in (doc or {}).get("blocks", []) if isinstance(doc, dict) else []:
            if isinstance(block, dict) and "p" in block:
                return block["p"]
        return []

    def sentence(self, memory, tag="span"):
        """A memory's sentence: the fixed lead, the record's words with the project words marked, the page's stop."""
        told = memory.get("sentence") or {}
        words, quoted = text_of(told.get("words")), told.get("quoted") is True
        out, at = [text_of(told.get("lead"))], 0
        if quoted:
            out.append("“")
        for mark in told.get("marks", []) if isinstance(told.get("marks"), list) else []:
            if not (isinstance(mark, list) and len(mark) == 3 and all(integer(v, -1) >= 0 for v in mark)):
                continue
            start, end, n = mark
            if start < at or end <= start or end > len(words):
                continue
            out += [words[at:start], self.term(words[start:end], n, tag)]
            at = end
        out.append(words[at:])
        out.append(".”" if quoted else ".")
        return J(*out)

    def said(self, memory, cls="said", text=None):
        """A memory as a sentence that opens the memory when chosen."""
        mid = integer(memory.get("id"))
        return E("a", {"class": cls, "href": anchor("m-%d" % mid), "data-m": mid},
                 text if text is not None else self.sentence(memory))

    def said_id(self, mid, cls="inl"):
        memory = self.m.get(integer(mid))
        return self.said(memory, cls) if memory else mind_model.PHRASES["not told"]

    def thread_link(self, n):
        thread = self.threads.get(integer(n))
        if not thread:
            return mind_model.PHRASES["thread.unnamed"]
        return E("a", {"href": anchor("thread-%d" % integer(n))}, text_of(thread.get("name")))

    def thread_name(self, n):
        thread = self.threads.get(integer(n))
        return text_of(thread.get("name")) if thread else mind_model.PHRASES["thread.unnamed"]

    # ------------------------------------------------------------ pieces

    @staticmethod
    def mark(memory_or_kind):
        kind = memory_or_kind if isinstance(memory_or_kind, str) else memory_or_kind.get("kind")
        return E("span", {"class": "mk " + KIND_CLASS.get(kind, "k-other"), "aria-hidden": "true"})

    @staticmethod
    def chip(memory):
        text = text_of(memory.get("latest")) or text_of(memory.get("chip"))
        return E("span", {"class": "chip " + state_class(memory)}, text) if text else ""

    def meta(self, memory, thread=False, date=False):
        return E("div", {"class": "meta"}, self.mark(memory),
                 E("span", {"class": "kw"}, KIND_WORD.get(memory.get("kind"), "Noted")),
                 E("span", {"class": "in"}, "in ", self.thread_name(memory.get("thread"))) if thread else "",
                 E("span", {"class": "date"}, text_of(memory.get("day"))) if date else "", self.chip(memory))

    def card(self, memory, thread=True):
        return E("div", {"class": "card %s %s" % (kind_class(memory), state_class(memory))},
                 self.meta(memory, thread=thread), self.said(memory))

    @staticmethod
    def view(ident, title, lede, *children):
        return E("section", {"class": "view", "id": ident, "aria-labelledby": ident + "-h"},
                 E("h2", {"id": ident + "-h"}, title), E("p", {"class": "lede"}, lede) if lede else "", *children)

    @staticmethod
    def show(summary, *children):
        return E("details", {"class": "ev"}, E("summary", None, summary), E("div", {"class": "evbody"}, *children))

    @staticmethod
    def made_from(block):
        if not text_of(block.get("data")):
            return ""
        return E("p", {"class": "quiet"}, "Made from ", E("code", None, block["data"]),
                 J(" with ", E("code", None, text_of(block.get("command")))) if text_of(block.get("command")) else "",
                 ".")

    def table_ref(self, block):
        return self.show("Show the table", E("p", {"class": "evtitle"}, text_of(block.get("title"))),
                         E("p", None, text_of(block.get("caption"))), self.table(block.get("table")),
                         self.made_from(block),
                         E("p", {"class": "quiet"}, "This is the table as it stands in what we say now."))

    def figure_ref(self, block):
        address = github_url("picture", text_of(block.get("repo")) or self.repo, block.get("ref"), block.get("path"))
        return self.show("Show the figure", E("p", {"class": "evtitle"}, text_of(block.get("title"))),
                         E("p", None, text_of(block.get("caption"))),
                         E("p", {"class": "quiet"}, "The picture is ", E("code", None, text_of(block.get("path"))),
                           " at ", text_of(block.get("ref")), ".") if text_of(block.get("path")) else "",
                         self.made_from(block),
                         E("a", {"href": address, "rel": "noopener noreferrer"}, "Open the picture on GitHub")
                         if address else E("p", {"class": "nothing"}, "This memory names a figure that this page cannot "
                                                                       "link to."))

    def code_ref(self, item):
        first, last = integer(item.get("from")), integer(item.get("to"))
        address = github_url("lines", text_of(item.get("repo")), item.get("ref"), item.get("path"), first, last)
        span = "line %d" % first if first == last else "lines %d to %d" % (first, last)
        return self.show("Show the code", E("p", {"class": "evtitle"}, E("code", None, text_of(item.get("path"))),
                                            ", %s, as they were at the change " % span, text_of(item.get("ref"))[:12]),
                         E("a", {"href": address, "rel": "noopener noreferrer"}, "Open these lines on GitHub")
                         if address else E("p", {"class": "nothing"}, "This memory names code that this page cannot "
                                                                       "link to."))

    def pull_ref(self, item):
        address = github_url("pull", text_of(item.get("repo")), item.get("number"))
        return self.show("Show the change that was merged", E("p", {"class": "evtitle"}, text_of(item.get("title"))),
                         E("a", {"href": address, "rel": "noopener noreferrer"}, "Open the pull request on GitHub")
                         if address else E("p", {"class": "nothing"}, "This memory names a change that this page "
                                                                       "cannot link to."))

    def references(self, memory):
        out = []
        for item in memory.get("evidence", []):
            kind = item.get("kind")
            if kind in ("table", "figure") and not item.get("found"):
                out.append(E("p", {"class": "quiet"}, (
                    "This memory pointed to a %s that is no longer in what we say now." if item.get("shown") else
                    "This memory points to a %s in a text that this page does not show.") % kind))
            elif kind == "table":
                out.append(self.table_ref(item))
            elif kind == "figure":
                out.append(self.figure_ref(item))
            elif kind == "code":
                out.append(self.code_ref(item))
            elif kind == "pull":
                out.append(self.pull_ref(item))
        return out

    # ------------------------------------------------------------ the opened memory

    def remind(self, memory):
        rows, remind = [], memory.get("remind") or {}
        thread = self.threads.get(integer(memory.get("thread"))) or {}
        opening = thread.get("opening")
        if opening:
            rows.append(E("div", {"class": "f"}, E("span", {"class": "lab"}, "What this thread is about"),
                          E("p", None, "This thread is about ", self.runs(self.first_runs(opening.get("about")),
                                                                           "button"), "."),
                          self.blocks(opening.get("why"))))
        pause = remind.get("pause")
        if isinstance(pause, int) and not isinstance(pause, bool) and 0 <= pause < len(self.pauses):
            before = self.pauses[pause]
            rows.append(E("div", {"class": "f"}, E("span", {"class": "lab"}, "How things stood at the pause before, on "
                                                   + text_of(before.get("day"))),
                          E("p", None, text_of(before.get("focus")))))
        if integer(remind.get("root")) in self.m:
            rows.append(E("div", {"class": "f"}, E("span", {"class": "lab"}, "Where this line of thought started"),
                          E("p", None, self.said_id(remind["root"]))))
        before = [mid for mid in remind.get("before", []) if integer(mid) in self.m]
        if before:
            rows.append(E("div", {"class": "f"}, E("span", {"class": "lab"}, "Just before this, in the same thread"),
                          *[E("p", None, self.said_id(mid)) for mid in before]))
        if not rows:
            return ""
        return E("details", {"class": "remind"}, E("summary", None, "Remind me what was going on"), *rows)

    def field(self, entry):
        if entry.get("type") == "people":
            body = E("p", {"class": "people"}, text_of(entry.get("people")))
        else:
            body = self.blocks(entry.get("doc"))
        return E("div", {"class": "f"}, E("span", {"class": "lab"}, text_of(entry.get("name"))), body)

    def links(self, title, pairs):
        items = []
        for pair in pairs:
            other = self.m.get(integer(pair.get("id")))
            items.append(E("li", None, E("span", {"class": "ph " + LINK_CLASS.get(pair.get("kind"), "l-because")},
                                         text_of(pair.get("phrase"))),
                           self.said(other, cls="inl") if other else mind_model.PHRASES["not told"]))
        if not items:
            return ""
        return E("div", {"class": "f"}, E("span", {"class": "lab"}, title), E("ul", {"class": "rel"}, *items))

    def detail(self, memory):
        mid = integer(memory.get("id"))
        written = memory.get("written") or {}
        who = text_of(memory.get("author")) or mind_model.PHRASES["account"]
        when = text_of(written.get("day"))
        if text_of(written.get("time")):
            when = "%s on %s" % (written["time"], when)
        address = github_url("memory", self.repo, memory.get("issue"), mid)
        refs = self.references(memory)
        group = memory.get("group") if memory.get("group") in GROUPS else "stands"
        return E("article", {"class": "mem %s %s" % (kind_class(memory), state_class(memory)), "id": "m-%d" % mid,
                             "data-kind": kind_class(memory)[2:], "data-group": group, "tabindex": "-1"},
                 self.meta(memory, date=True),
                 E("p", {"class": "big"}, self.sentence(memory, "button")),
                 self.remind(memory),
                 E("p", {"class": "ans"}, text_of(memory.get("passage"))),
                 E("p", {"class": "inthread"}, "Part of the thread ", self.thread_link(memory.get("thread")), "."),
                 *[self.field(entry) for entry in memory.get("fields", [])],
                 self.links("What led here", memory.get("out", [])),
                 self.links("What came of it", memory.get("in", [])),
                 E("div", {"class": "f refs"}, E("span", {"class": "lab"}, "If you want to see for yourself"), *refs)
                 if refs else "",
                 E("p", {"class": "quiet oldnote"}, OLD_NOTE) if memory.get("old") is True else "",
                 E("details", {"class": "where"}, E("summary", None, "Where this was said"),
                   *[self.field(entry) for entry in memory.get("where", [])],
                   E("p", None, "Written by ", E("span", {"class": "people"}, who), " at %s, as a comment in the "
                                                                                    "project's record on GitHub." % when),
                   E("a", {"href": address, "rel": "noopener noreferrer"}, "Open the original comment on GitHub")
                   if address else ""))

    # ------------------------------------------------------------ the views

    def now_view(self):
        last, out = self.model.get("last"), []
        if last:
            day = text_of(last.get("day"))
            out.append(E("p", {"class": "paused"}, "We last paused on %s. This is how things stood:" % day if day else
                         "We last paused here. This is how things stood:"))
            out.append(E("blockquote", {"class": "focus"}, self.blocks(last.get("focus"))))
            steps = [E("li", None, E("span", {"class": "who people"}, text_of(row.get("owner"))),
                       E("span", None, self.runs(row.get("action")),
                         J(" (", self.runs(row.get("where")), ")") if row.get("where") else ""))
                     for row in last.get("next", [])]
            out.append(E("div", {"class": "two"},
                         E("div", None, E("h3", None, "What we meant to do next"),
                           E("ul", {"class": "next"}, *steps) if steps else
                           E("p", {"class": "quiet"}, "Nothing was written down for next.")),
                         E("div", None, E("h3", None, "Wait for a yes before"),
                           E("div", {"class": "hold"}, self.blocks(last.get("holds"))) if last.get("holds") else
                           E("p", {"class": "quiet"}, "Nothing waits for a yes."))))
            if last.get("day"):
                since = [self.card(self.m[mid]) for mid in self.model.get("since", []) if mid in self.m]
                out += [E("h3", None, "Since that pause"),
                        E("div", {"class": "stack"}, *since) if since else
                        E("p", {"class": "quiet"}, "Nothing has been recorded since.")]
        else:
            out.append(E("p", {"class": "nothing"}, PAUSE_UNTOLD if (self.model.get("not_told") or {}).get("pause")
                         is True else "No pause has been recorded yet."))
        latest = []
        for kind, label in (("decision", "The last thing we decided"), ("result", "The last thing we found"),
                            ("failure", "The last thing that went wrong"), ("question", "The last thing we asked")):
            mid = integer((self.model.get("latest_of") or {}).get(kind))
            if mid in self.m:
                latest.append(E("li", None, E("span", {"class": "lab"}, label), self.card(self.m[mid])))
        if latest:
            out += [E("h3", None, "The latest of each kind"), E("ul", {"class": "latest"}, *latest)]
        return self.view("now", "Where we are", None, *out)

    def chain(self, thread):
        n = integer(thread.get("n"))
        items = []
        for step in thread.get("steps", []):
            memory = self.m.get(integer(step.get("id")))
            if memory is None:
                continue
            conn = step.get("conn")
            if conn:
                label, to = [text_of(conn.get("phrase"))], conn.get("to") or {}
                if integer(to.get("id")) in self.m:
                    label.append(E("a", {"href": anchor("m-%d" % to["id"]), "data-m": to["id"]}, text_of(to.get("day"))))
                elif integer(to.get("thread")) in self.threads:
                    label.append(self.thread_link(to["thread"]))
                items.append(E("li", {"class": "conn time" if conn.get("time") else "conn"}, *label))
            if step.get("note"):
                items.append(E("li", {"class": "note"}, CHAIN_NOTE))
            items.append(E("li", {"class": "station %s %s%s" % (kind_class(memory), state_class(memory),
                                                                 " deadend" if memory.get("side") is True else "")},
                           self.meta(memory, date=True), self.said(memory)))
        opening = thread.get("opening")
        began = thread.get("began")
        story = ("It began on %s and holds %s; the latest is from %s." % (
            text_of(began), plural(len(thread.get("memories", [])), "1 memory", "%d memories"),
            text_of(thread.get("ended")))) if began else "It holds no memories yet."
        about = E("div", {"class": "about"}, E("span", {"class": "lab"}, "What this thread is about"),
                  E("p", {"class": "aboutsay"}, "This thread is about ", self.runs(self.first_runs(opening["about"])), ".")
                  if opening else E("p", {"class": "aboutsay"}, "Nobody has written what this thread is about yet."),
                  self.blocks(opening.get("why")) if opening else "", E("p", None, story))
        return E("article", {"class": "thread", "id": "thread-%d" % n},
                 E("h3", None, text_of(thread.get("name")), " ",
                   E("span", {"class": "chip " + STATE_CLASS.get(thread.get("status_css"), "s-plain")},
                     text_of(thread.get("status")))),
                 about, E("div", {"class": "nowsay"}, E("span", {"class": "lab"}, "What we say now"), self.outcome(thread)),
                 *[self.task_note(task) for task in thread.get("tasks", [])],
                 E("ol", {"class": "chain"}, *items))

    def outcome(self, thread):
        if thread.get("loose") is True:
            return E("p", None, "These memories belong to no thread of what we deliver.")
        if thread.get("set_aside") is True:
            return E("p", None, "This thread is not part of what we deliver. The story below says why.")
        if thread.get("shown") is not True:
            address = github_url("issue", self.repo, thread.get("topic"))
            return J(E("p", None, NOT_SHOWN), E("a", {"href": address, "rel": "noopener noreferrer"},
                                                "Open the thread on GitHub") if address else "")
        sections = thread.get("sections", [])
        if not sections:
            return E("p", None, "The text of this thread has no sections yet.")
        first = sections[0]
        return J(E("p", {"class": "quiet"}, text_of(first.get("heading"))), self.blocks(first.get("doc")),
                 E("p", {"class": "quiet"}, "The rest is under “What is settled?”.") if len(sections) > 1 else "")

    def threads_view(self):
        threads = self.model.get("threads", [])
        tabs = E("div", {"class": "tabs", "role": "tablist"},
                 *[E("a", {"href": anchor("thread-%d" % integer(t.get("n"))), "class": "tab"}, text_of(t.get("name")))
                   for t in threads])
        return self.view("threads", "How we got here", "Each thread is one line of thought, told from its first memory "
                         "to its latest. Choose a sentence to open that memory.", tabs,
                         *[self.chain(t) for t in threads])

    def lessons_view(self):
        rows = []
        failures = [m for m in self.model.get("memories", []) if m.get("kind") == "failure"]
        for memory in reversed(failures):
            after = [self.m[integer(pair.get("id"))] for pair in memory.get("in", []) if integer(pair.get("id")) in self.m]
            if after:
                right = [self.card(other, thread=False) for other in after]
                for other in after:
                    for css, key, lead in (("replaced", "replaces", "That changed again later: "),
                                           ("aside", "instead", "That was set aside. Instead: ")):
                        newer = [pair for pair in other.get("in", []) if pair.get("kind") == key]
                        if other.get("css") == css and newer and integer(newer[0].get("id")) in self.m:
                            right.append(E("p", {"class": "quiet"}, lead, self.said_id(newer[0]["id"])))
            elif memory.get("old") is True:
                thread = self.threads.get(integer(memory.get("thread"))) or {}
                ids = [mid for mid in thread.get("memories", []) if mid in self.m]
                later = ids[ids.index(memory["id"]) + 1:] if memory.get("id") in ids else []
                right = [E("p", {"class": "quiet"}, "What followed was not recorded as a link. The next memory in this "
                           "thread was:")] + ([self.card(self.m[later[0]], thread=False)] if later else [])
            else:
                right = [E("p", {"class": "nothing"}, "Nothing has followed from this yet.")]
            cause = next((entry for entry in memory.get("fields", []) if entry.get("label") == "Cause"), None)
            rows.append(E("li", {"class": "lesson"},
                          E("div", {"class": "wrong"}, E("span", {"class": "lab"}, "What went wrong, in ",
                                                         self.thread_name(memory.get("thread"))),
                            self.said(memory),
                            E("div", {"class": "why"}, E("span", {"class": "lab"}, "Why it happened"),
                              self.blocks(cause.get("doc"))) if cause else ""),
                          E("div", {"class": "arrow", "aria-hidden": "true"}),
                          E("div", {"class": "did"}, E("span", {"class": "lab"}, "What we did about it"), *right)))
        return self.view("lessons", "What we learned the hard way", "Every failure in this mind, newest first, with "
                         "what it led to.", E("ol", {"class": "lessons"}, *rows) if rows else
                         E("p", {"class": "quiet"}, "Nothing has gone wrong in this record yet."))

    def settled_view(self):
        blocks, left = [], []
        for thread in self.model.get("threads", []):
            if thread.get("loose") is True:
                continue
            if thread.get("set_aside") is True:
                left.append(E("li", None, E("b", None, text_of(thread.get("name"))), ". ",
                              self.said_id(thread["aside_by"]) if integer(thread.get("aside_by")) in self.m
                              else "Left out."))
                continue
            says = []
            if thread.get("shown") is True:
                by_key = {(b.get("kind"), b.get("id")): b for b in thread.get("blocks", [])}
                for section in thread.get("sections", []):
                    refs = []
                    for key in section.get("blocks", []):
                        block = by_key.get(tuple(key)) if isinstance(key, list) else None
                        if block:
                            refs.append(self.table_ref(block) if block.get("kind") == "table" else
                                        self.figure_ref(block))
                    says.append(E("div", {"class": "sec"}, E("span", {"class": "lab"}, text_of(section.get("heading"))),
                                  self.blocks(section.get("doc")), *refs))
                sources = thread.get("sources", [])
                if sources:
                    says.append(self.show("Show where these numbers come from", E("ul", {"class": "srcs"}, *[
                        E("li", None, E("b", None, self.runs(row.get("statement"))), ": ", self.runs(row.get("source")))
                        for row in sources])))
            else:
                says.append(self.outcome(thread))
            rests = []
            for rest in thread.get("rests", []):
                memory = self.m.get(integer(rest.get("id")))
                if memory is None:
                    continue
                older = [mid for mid in rest.get("replaced", []) if integer(mid) in self.m]
                extra = E("details", {"class": "older"},
                          E("summary", None, "This is answer %d. See what it replaced" % (len(older) + 1)),
                          *[E("p", {"class": "struck"}, self.said_id(mid)) for mid in older]) if older else ""
                rests.append(E("li", {"class": "rest " + kind_class(memory)}, self.mark(memory),
                               E("div", None, self.said(memory), extra)))
            blocks.append(E("article", {"class": "settled"}, E("h3", None, text_of(thread.get("name"))),
                            E("div", {"class": "says"}, *says),
                            E("div", {"class": "restson"}, E("span", {"class": "lab"}, "This rests on"),
                              E("ul", None, *rests) if rests else
                              E("p", {"class": "quiet"}, "No standing decision or result yet."))))
        return self.view("settled", "What is settled", "What the project says today, thread by thread, and the "
                         "memories it rests on. Anything replaced is folded under what replaced it.", *blocks,
                         E("h3", None, "Left out on purpose"),
                         E("ul", {"class": "leftout"}, *left) if left else
                         E("p", {"class": "quiet"}, "Nothing has been set aside."))

    @staticmethod
    def age(days):
        if not isinstance(days, int) or isinstance(days, bool):
            return ""
        return "open since today" if days == 0 else "open for 1 day" if days == 1 else "open for %d days" % days

    def task_note(self, task):
        if task.get("goal"):
            words = J("We are working on this: ", self.runs(task["goal"]))
        elif task.get("shown") is True:
            words = "An open task has no goal written yet."
        else:
            words = "An open task whose text is not shown here."
        address = github_url("issue", self.repo, task.get("issue"))
        return E("div", {"class": "onote task k-checkpoint"},
                 E("p", {"class": "said"}, words),
                 E("a", {"class": "quiet", "href": address, "rel": "noopener noreferrer"}, "Open the task on GitHub")
                 if address else "")

    def open_view(self):
        opened = self.model.get("open") or {}

        def group(title, hint, rows):
            return E("section", {"class": "ogroup"}, E("h3", None, title), E("p", {"class": "quiet"}, hint),
                     E("ul", {"class": "notes"}, *rows) if rows else E("p", {"class": "nothing"}, "Nothing here."))

        def note(entry):
            memory = self.m.get(integer(entry.get("id")))
            if memory is None:
                return ""
            return E("li", {"class": "onote " + kind_class(memory)},
                     E("div", {"class": "meta"}, self.mark(memory), E("span", {"class": "in"},
                                                                      self.thread_name(memory.get("thread"))),
                       E("span", {"class": "age"}, self.age(entry.get("age")))), self.said(memory))

        unknown = []
        for entry in opened.get("unknown", []):
            memory = self.m.get(integer(entry.get("id")))
            if memory is None:
                continue
            unknown.append(E("li", {"class": "onote k-result"},
                             E("div", {"class": "meta"}, self.mark("result"),
                               E("span", {"class": "in"}, self.thread_name(memory.get("thread")))),
                             E("div", {"class": "said"}, E("span", {"class": "lab"}, "We have not established"),
                               self.blocks(entry.get("doc"))),
                             E("p", {"class": "quiet"}, "From ", self.said(memory, "inl", "what we found on %s"
                                                                           % text_of(memory.get("day"))), ".")))
        holds = opened.get("holds")
        return self.view(
            "open", "What is still open", "Thoughts nobody has closed. Each one stays here until a later memory answers "
            "it, tries it or sets it aside.",
            group("Questions without an answer", "Asked, and nothing recorded since answers them.",
                  [note(entry) for entry in opened.get("questions", [])]),
            group("Ideas we have not tried", "Written down, never put to a test.",
                  [note(entry) for entry in opened.get("ideas", [])]),
            group("Went wrong, and nothing followed", "Failures that no decision or fix points back to.",
                  [note(entry) for entry in opened.get("failures", [])]),
            group("What our results do not show", "Said by the results that still stand, in their own words.", unknown),
            group("Waiting for a yes", "From the last pause.",
                  [E("li", {"class": "onote k-checkpoint"}, E("div", {"class": "said"}, self.blocks(holds)))]
                  if holds else []),
            group("Work in progress", "Open tasks, from their goal.",
                  [E("li", None, self.task_note(task)) for task in opened.get("tasks", [])]))

    def map_view(self):
        layout = self.model.get("map")
        if not layout:
            return self.view("map", "The whole mind", None, E("p", {"class": "quiet"}, "There is nothing to draw yet."))
        width, height, top = integer(layout.get("width"), 1080), integer(layout.get("height"), 300), \
            integer(layout.get("top"), 64)
        parts = []
        for tick in layout.get("ticks", []):
            x = coordinate(tick.get("x"))
            parts.append(E("line", {"class": "tick", "x1": "%.1f" % x, "x2": "%.1f" % x, "y1": top - 30,
                                    "y2": height - 26}))
            parts.append(E("text", {"class": "axis", "x": "%.1f" % x, "y": height - 8, "text-anchor": "middle"},
                           text_of(tick.get("day"))))
        for pause in layout.get("pauses", []):
            x = coordinate(pause.get("x"))
            parts.append(E("line", {"class": "pause", "x1": "%.1f" % x, "x2": "%.1f" % x, "y1": top - 34,
                                    "y2": height - 26}))
            parts.append(E("text", {"class": "pauselab", "x": "%.1f" % x, "y": top - 40, "text-anchor": "middle"},
                           "paused"))
        for row in layout.get("rows", []):
            y = coordinate(row.get("y"))
            css = {"aside": "r-aside", "open": "r-open", "stands": "r-stands"}.get(row.get("status_css"), "r-stands")
            parts.append(E("path", {"class": "river " + css, "d": "M%.1f %.1f H%.1f" % (
                coordinate(row.get("x1")), y, coordinate(row.get("x2")))}))
            n = row.get("n")
            if integer(n) in self.threads and n is not None:
                thread = self.threads[integer(n)]
                parts.append(E("a", {"href": anchor("thread-%d" % integer(n))},
                               E("text", {"class": "lane", "x": 12, "y": "%.1f" % (y - 2)}, text_of(row.get("name"))),
                               E("text", {"class": "lanestate", "x": 12, "y": "%.1f" % (y + 15)},
                                 text_of(thread.get("status")).lower())))
            else:
                parts.append(E("text", {"class": "lane", "x": 12, "y": "%.1f" % (y - 2)},
                               plural(integer(row.get("folded")), "1 other thread", "%d other threads")))
        for dot in layout.get("dots", []):
            if dot.get("side") is True:
                x, y, row_y = coordinate(dot.get("x")), coordinate(dot.get("y")), coordinate(dot.get("row_y"))
                parts.append(E("path", {"class": "branch", "d": "M%.1f %.1f C%.1f %.1f %.1f %.1f %.1f %.1f" % (
                    x - 20, row_y, x - 8, row_y, x - 12, y, x, y)}))
        for link in layout.get("links", []):
            x1, y1, x2, y2 = (coordinate(link.get(k)) for k in ("x1", "y1", "x2", "y2"))
            if abs(y1 - y2) < 1:
                lift = min(34, 10 + (x2 - x1) * 0.12)
                d = "M%.1f %.1f Q%.1f %.1f %.1f %.1f" % (x1, y1 - 9, (x1 + x2) / 2, y1 - 9 - lift, x2, y2 - 9)
            else:
                d = "M%.1f %.1f C%.1f %.1f %.1f %.1f %.1f %.1f" % (x1, y1, (x1 + x2) / 2, y1, (x1 + x2) / 2, y2, x2, y2)
            parts.append(E("path", {"class": "link " + LINK_CLASS.get(link.get("kind"), "l-because"), "d": d,
                                    "data-a": integer(link.get("a")), "data-b": integer(link.get("b"))}))
        for dot in layout.get("dots", []):
            memory = self.m.get(integer(dot.get("id")))
            if memory is None:
                continue
            x, y, kind = coordinate(dot.get("x")), coordinate(dot.get("y")), memory.get("kind")
            if kind in ("decision", "correction"):
                shape = E("rect", {"x": "%.1f" % (x - 6), "y": "%.1f" % (y - 6), "width": 12, "height": 12,
                                   "transform": "rotate(45 %.1f %.1f)" % (x, y)})
            elif kind == "failure":
                shape = E("path", {"d": "M%.1f %.1f l12 12 m0 -12 l-12 12" % (x - 6, y - 6), "class": "x"})
            elif kind == "idea":
                shape = E("polygon", {"points": "%.1f,%.1f %.1f,%.1f %.1f,%.1f" % (x, y - 8, x + 8, y + 6, x - 8,
                                                                                   y + 6)})
            elif kind == "question":
                shape = E("circle", {"cx": "%.1f" % x, "cy": "%.1f" % y, "r": 6, "class": "ring"})
            else:
                shape = E("circle", {"cx": "%.1f" % x, "cy": "%.1f" % y, "r": 7})
            mid = integer(memory.get("id"))
            parts.append(E("a", {"href": anchor("m-%d" % mid), "data-m": mid,
                                 "class": "dot %s %s" % (kind_class(memory), state_class(memory))},
                           E("title", None, text_of(memory.get("passage"))),
                           E("circle", {"class": "hit", "cx": "%.1f" % x, "cy": "%.1f" % y, "r": 13}), shape))
        legend = E("ul", {"class": "legend"},
                   *[E("li", None, self.mark(k), KIND_WORD[k]) for k in ("result", "decision", "failure", "idea",
                                                                          "question")],
                   E("li", None, E("span", {"class": "ln l-because"}), "so"),
                   E("li", None, E("span", {"class": "ln l-replaces"}), "replaced"),
                   E("li", None, E("span", {"class": "ln l-instead"}), "set aside; instead"),
                   E("li", None, E("span", {"class": "ln l-checks"}), "tried, checked or answered"),
                   E("li", None, E("span", {"class": "fade"}), "faded: replaced or set aside"))
        return self.view("map", "The whole mind", "Each line is a thread, running left to right through time. A branch "
                         "that drops away is something we set aside. Point at a mark to read it, choose it to open it.",
                         E("div", {"class": "mapbox"},
                           E("svg", {"viewBox": "0 0 %d %d" % (width, height), "role": "img", "class": "mindmap",
                                     "aria-label": "Map of the threads of this mind over time"}, *parts)),
                         E("p", {"class": "reading", "id": "reading", "aria-live": "polite"},
                           "Point at a mark on the map to read that memory here."), legend)

    def ask_view(self):
        chips = (("decided", "What have we decided?"), ("wrong", "What went wrong?"), ("open", "What is still open?"),
                 ("changed", "Where did we change our mind?"))
        return self.view("ask", "Ask the mind", "Type a word or two. The answers are the memories that mention it, each "
                         "told with what led to it or what came of it.",
                         E("div", {"class": "askbar"},
                           E("input", {"type": "search", "id": "q", "placeholder": "For example: memory, quote, speed",
                                       "aria-label": "Words to look for", "autocomplete": "off"})),
                         E("div", {"class": "qchips"}, *[E("button", {"type": "button", "data-ask": key}, label)
                                                          for key, label in chips]),
                         E("div", {"id": "answers", "aria-live": "polite"}))

    # ------------------------------------------------------------ the stores behind the box

    def word_store(self):
        entries = []
        for word in self.model.get("words", []):
            n = integer(word.get("n"))
            if not n:
                continue
            uses = [self.m[mid] for mid in word.get("uses", []) if integer(mid) in self.m]
            first = word.get("first") or {}
            story = [E("li", None, E("span", {"class": "when"}, self.mark(memory), " ", text_of(memory.get("day"))), " ",
                       self.said(memory, cls="inl")) for memory in uses]
            earlier = [E("li", None, E("span", {"class": "when"}, "Until a later memory, from ",
                                       text_of(entry.get("day"))), " we used it to mean ",
                         text_of(entry.get("meaning")), ".") for entry in word.get("earlier", [])]
            also = [text_of(name) for name in word.get("also", []) if text_of(name)]
            entries.append(E("div", {"class": "entry", "id": "w%d" % n},
                             E("p", {"class": "wt"}, text_of(word.get("word"))),
                             E("p", {"class": "def"}, "We use “", E("b", None, text_of(word.get("word"))),
                               "” to mean ", text_of(word.get("meaning")), "."),
                             E("div", {"class": "why"}, self.blocks(word.get("why"))) if word.get("why") else "",
                             E("p", {"class": "quiet"}, "Also called: ", ", ".join(also), ".") if also else "",
                             E("div", {"class": "more"}, E("p", {"class": "lab"}, "The longer story"),
                               self.blocks(word.get("story"))) if word.get("story") else "",
                             E("div", {"class": "uses"}, E("p", {"class": "lab"}, "What it meant before"),
                               E("ol", None, *earlier)) if earlier else "",
                             E("div", {"class": "uses"},
                               E("p", {"class": "lab"}, "Where it came up (%d), oldest first" % len(story)),
                               E("ol", None, *story)) if story else "",
                             E("p", {"class": "quiet"}, "First explained on %s" % text_of(first.get("day")),
                               J(", in the thread ", self.thread_link(first["thread"]))
                               if integer(first.get("thread")) in self.threads else "", ".")))
        return E("div", {"id": "words", "hidden": "hidden"}, *entries)

    def issue_store(self):
        entries = []
        for issue in self.model.get("cited", []):
            n = integer(issue.get("n"))
            if not n:
                continue
            who = text_of(issue.get("author")) or mind_model.PHRASES["account"]
            state = "still open" if issue.get("open") is True else (
                "closed on %s" % text_of(issue.get("closed")) if issue.get("closed") else "closed")
            address = github_url("pull" if issue.get("pull") is True else "issue", self.repo, n)
            entries.append(E("div", {"class": "entry", "id": "issue-%d" % n},
                             E("p", {"class": "wt"}, text_of(issue.get("title"))),
                             E("p", {"class": "quiet"}, "Opened by ", E("span", {"class": "people"}, who),
                               " on %s; %s." % (text_of(issue.get("opened")), state)),
                             E("div", {"class": "why"}, self.blocks(issue.get("first"))) if issue.get("first") else "",
                             E("p", None, E("a", {"href": address, "rel": "noopener noreferrer"},
                                            "Open the whole issue on GitHub")) if address else ""))
        return E("div", {"id": "issues", "hidden": "hidden"}, *entries)

    @staticmethod
    def word_box():
        return J(E("div", {"id": "wordtip", "class": "wordtip", "role": "tooltip", "hidden": "hidden"}),
                 E("dialog", {"id": "wordbox", "class": "wordbox", "aria-labelledby": "wordhead"},
                   E("div", {"class": "wordtop"}, E("span", {"id": "wordkind", "class": "lab"},
                                                    "A word from this project"),
                     E("h2", {"id": "wordhead", "class": "wordhead"})),
                   E("div", {"id": "wordbody", "class": "wordbody", "tabindex": "0"}),
                   E("div", {"class": "wordbar"}, E("button", {"type": "button", "id": "wordclose", "class": "primary"},
                                                    "Close"))))

    def tour(self):
        threads = [t for t in self.model.get("threads", []) if t.get("memories")]
        longest = max(threads, key=lambda t: len(t.get("memories", []))) if threads else None
        counts = self.model.get("counts") or {}
        first, latest = text_of(self.model.get("first")), text_of(self.model.get("latest"))
        steps = [
            ("now", J("This is the memory of ", text_of(self.model.get("name")), ": ",
                      plural(integer(counts.get("memories")), "1 memory", "%d memories"), " in ",
                      plural(integer(counts.get("threads")), "1 thread", "%d threads"),
                      ", from %s to %s. " % (first, latest) if first else ". ",
                      "Nothing here is a list of tickets. It is what was thought, tried, decided and learned, in "
                      "sentences.")),
            ("now", "Start with where things stand. This page is the last pause: what was in focus, what was meant to "
                    "happen next, and what has happened since."),
            ("thread-%d" % integer(longest.get("n")) if longest else "threads",
             "A thread is one line of thought. Read it top to bottom. The words between two memories say how one led "
             "to the next: so, we tried it, later that changed. Indented memories are dead ends."),
            ("lessons", "Every failure sits next to what it led to. A failure with nothing beside it is one nobody has "
                        "acted on."),
            ("settled", "This is what the project says today, and the memories that hold it up. When an answer "
                        "replaced an earlier one, the earlier one is folded underneath."),
            ("open", "These thoughts are still open: questions without an answer, ideas nobody tried, and what the "
                     "results do not show."),
            ("map", "The map shows every thread at once. To find something by a word, ask the mind. Choose any "
                    "sentence, anywhere, to open that memory. A word with a dotted line is one of the project's own "
                    "words: point at it to see what it means, or choose it for the whole story. Inside a memory, "
                    "“Remind me” tells what was going on then, and the “Show” lines say what the "
                    "table, code or figure behind it is and where to open it."),
        ]
        return E("div", {"id": "tour", "class": "tour", "role": "dialog", "aria-label": "First visit", "hidden": "hidden"},
                 *[E("p", {"class": "step", "data-go": "#" + go, "hidden": "hidden"}, words) for go, words in steps],
                 E("div", {"class": "tourbar"}, E("span", {"id": "tourn", "class": "quiet"}),
                   E("button", {"type": "button", "id": "tourback"}, "Back"),
                   E("button", {"type": "button", "id": "tournext", "class": "primary"}, "Next"),
                   E("button", {"type": "button", "id": "tourend"}, "Leave the tour")))

    def untold(self):
        """The page's last lines: what is not told, and why."""
        missing, lines = self.model.get("not_told") or {}, []
        if (self.model.get("built") or {}).get("complete") is False:
            lines.append(E("p", None, "The record was still being read when this page was built."))
        others = integer(missing.get("others"))
        if others:
            lines.append(E("p", None, plural(others, "1 memory by an account outside your allowed list is not told "
                                                     "here.",
                                             "%d memories by accounts outside your allowed list are not told here.")))
        edited = integer(missing.get("edited"))
        if edited:
            links = [github_url("memory", self.repo, entry.get("issue"), entry.get("id"))
                     for entry in missing.get("edited_ids", [])]
            lines.append(E("p", None, plural(edited, "1 memory was changed on GitHub after it was posted, so it is not "
                                                     "told here.",
                                             "%d memories were changed on GitHub after they were posted, so they are "
                                             "not told here.")))
            lines.append(E("ul", {"class": "plainlist"}, *[E("li", None, E("a", {"href": address, "rel":
                                                                                  "noopener noreferrer"},
                                                                         "Open it on GitHub"))
                                                            for address in links if address]))
        older = integer(missing.get("older"))
        if older:
            lines.append(E("p", None, plural(older, "1 older memory is not on this page; compile.py writes the whole "
                                                    "timeline.",
                                             "%d older memories are not on this page; compile.py writes the whole "
                                             "timeline.")))
        if integer(missing.get("words")):
            lines.append(E("p", None, plural(integer(missing["words"]), "1 more word is explained in the record than "
                                                                         "this page shows.",
                                             "%d more words are explained in the record than this page shows.")))
        for body in missing.get("bodies", []):
            address = github_url("issue", self.repo, body.get("topic"))
            lines.append(E("p", None, "The text of the thread ", self.thread_link(body.get("thread")),
                           " is not shown: someone outside your allowed accounts wrote or changed it, or its history "
                           "has not been read yet. ",
                           E("a", {"href": address, "rel": "noopener noreferrer"}, "Open it on GitHub") if address
                           else ""))
        if not lines:
            lines.append(E("p", None, "Every memory of the record by your allowed accounts is told here."))
        return E("section", {"class": "untold", "id": "untold"}, E("h3", None, "What this page leaves out"), *lines)

    def page(self, style, script):
        digest = lambda text: "'sha256-%s'" % base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode()
        policy = POLICY % (digest(style), digest(script))
        built = self.model.get("built") or {}
        head = E("head", None, E("meta", {"charset": "utf-8"}),
                 E("meta", {"http-equiv": "Content-Security-Policy", "content": policy}),
                 E("meta", {"name": "referrer", "content": "no-referrer"}),
                 E("meta", {"name": "viewport", "content": VIEWPORT}),
                 E("title", None, "The mind of ", text_of(self.model.get("name"))), Safe("<style>%s</style>" % style))
        side = E("nav", {"class": "side", "aria-label": "Ways into the mind"},
                 E("p", {"class": "mindname"}, text_of(self.model.get("name"))),
                 E("p", {"class": "mindsub"}, "the memory of one project"),
                 E("ul", None, *[E("li", None, E("a", {"href": anchor(key), "data-nav": key}, label))
                                 for key, label in NAV]),
                 E("div", {"class": "sidefoot"},
                   E("button", {"type": "button", "id": "tourstart"}, "First visit? Take the tour"),
                   E("button", {"type": "button", "id": "theme"}, "Switch light and dark"),
                   E("p", {"class": "quiet"}, "Read from the record on %s. This page never changes the record."
                     % text_of(built.get("words")))))
        main = E("main", {"id": "main"}, self.now_view(), self.threads_view(), self.lessons_view(), self.settled_view(),
                 self.open_view(), self.map_view(), self.ask_view(), self.untold())
        panel = E("aside", {"id": "panel", "class": "panel", "aria-label": "One memory"},
                  E("button", {"type": "button", "id": "close", "class": "close"}, "Close"),
                  E("h2", {"class": "noscript-h"}, "Every memory"),
                  *[self.detail(memory) for memory in self.model.get("memories", []) if integer(memory.get("id"))])
        body = E("body", None, side, main, panel, self.tour(), self.word_store(), self.issue_store(), self.word_box(),
                 Safe("<script>%s</script>" % script))
        return "<!doctype html>\n%s\n<html lang=\"en\">%s%s</html>\n" % (NOTICE, head, body)


def fixed_files():
    """The style and the script, read from ../page; refused when either could end its element or open a comment."""
    with open(os.path.join(PAGE, "mind.css"), encoding="utf-8") as handle:
        style = handle.read()
    with open(os.path.join(PAGE, "mind.js"), encoding="utf-8") as handle:
        script = handle.read()
    for text in (style, script):
        if "</" in text or "<!--" in text:
            raise ValueError("the fixed style or script holds </ or <!--")
    return style, script


def render(model):
    """The whole page for a model, as a string. Pure: the same model gives the same text."""
    style, script = fixed_files()
    return Page(model).page(style, script)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", help="owner/name (default: from .recortex.json)")
    parser.add_argument("--sync", action="store_true", help="run index.py sync first (reads GitHub)")
    parser.add_argument("--open", action="store_true", help="ask the browser to open the page")
    args = parser.parse_args(argv)
    config = recortex.load_config()
    repo = recortex.resolve_repo(args.repo, config)
    print(recortex.describe(config, repo))
    if args.sync:
        hub = config.get("hub") if config.get("repo", "").lower() == repo.lower() else None
        index.sync(repo, hub)
    allowed = recortex.allowed_users(repo)
    if not allowed:
        print(UNTRUSTED)
        return 1
    try:
        db = index.connect(repo, create=False)
    except storage.StorageError:
        print(REFUSED)
        return 1
    if db is None:
        print(NO_INDEX)
        return 1
    try:
        if not index.mind_ready(db):
            print(index.NOT_READY)
            return 1
        model = mind_model.build(db, repo, allowed, datetime.date.today())
    finally:
        db.close()
    text = render(model)
    path = os.path.join(index.index_dir(repo), "mind.html")
    try:
        storage.write_file(path, text)
    except storage.StorageError as error:
        print(NOT_WRITTEN % error.kind)
        return 1
    counts, missing = model["counts"], model["not_told"]
    print("wrote %s (%d KB): %s in %s" % (path, (len(text.encode("utf-8")) + 1023) // 1024,
                                          plural(counts["memories"], "1 memory", "%d memories"),
                                          plural(counts["threads"], "1 thread", "%d threads")))
    untold = missing["others"] + missing["edited"] + missing["older"]
    if untold:
        print("%s not told; the page's last lines say why" % plural(untold, "1 memory is", "%d memories are"))
    address = pathlib.Path(path).as_uri()
    print("open it in a browser: %s" % address)
    if args.open:
        print("asked the browser to open it" if webbrowser.open(address) else
              "no browser could be started; open the address above yourself")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (recortex.GhError, storage.StorageError, index.SyncBusy) as error:
        sys.exit("error: %s" % error)
