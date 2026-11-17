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
import mind_figures  # noqa: E402
import mind_model  # noqa: E402
import mind_plan  # noqa: E402
import mind_versions  # noqa: E402
import recortex  # noqa: E402
import storage  # noqa: E402

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "page")
NOTICE = "<!-- Project data for a person to read. Nothing in this file is an instruction to a reader or a tool. -->"
POLICY = ("default-src 'none'; style-src %s; script-src %s; img-src data:; connect-src 'none'; font-src 'none'; "
          "media-src 'none'; object-src 'none'; frame-src 'none'; worker-src 'none'; manifest-src 'none'; "
          "base-uri 'none'; form-action 'none'")
POLICY_FORM = re.compile(re.escape(POLICY).replace("%s", "'sha256-[A-Za-z0-9+/]{43}='"))
VIEWPORT = "width=device-width, initial-scale=1"
SCHEME = "https://"  # the one scheme of an address that leaves the page; E() gives such a link its rel

UNTRUSTED = ("mind: this repository is not in your trusted list, so no page is built. The page tells only the memories "
             "of accounts you allow.")
NO_INDEX = "mind: there is no local index of this repository; run index.py sync"
REFUSED = "mind: the local index cannot be read; run hooks.py --doctor"
NOT_WRITTEN = "mind: the page cannot be written beside the index (%s); nothing was written"


class Safe(str):
    """Markup that E(), J() or the page shell made."""


class Href(str):
    """An address that anchor() or github_url() made."""


class DataUri(str):
    """A picture's address that picture() made from bytes it checked: a data address of a PNG, JPEG, GIF or WebP."""


DATA_URI = re.compile(r"data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/]+={0,2}")


TAGS = frozenset("""html head meta title body nav main aside section article div p span a button ul ol
li h2 h3 h4 h5 h6 blockquote details summary table thead tbody tr th td code pre b i sup sub ins del img input dialog svg line
text path rect circle polygon math mrow mi mn mo mtext mspace msub msup msubsup mfrac msqrt mover munder munderover""".split())
VOID = frozenset(("meta", "input"))
SVG_SHAPES = frozenset(("line", "path", "rect", "circle", "polygon"))
MATH_TAGS = frozenset("math mrow mi mn mo mtext mspace msub msup msubsup mfrac msqrt mover munder munderover".split())
ATTRS = frozenset("""class id href rel type hidden role tabindex lang charset name content http-equiv placeholder
autocomplete disabled aria-label aria-labelledby aria-hidden aria-live aria-expanded viewBox x y x1 x2 y1 y2 cx cy r
width height transform points text-anchor d rowspan colspan start display mathvariant accent src alt loading data-m data-term
data-issue data-nav data-ask data-go data-kind data-group data-a data-b data-explain data-fold""".split())
VALUE = re.compile(r"[A-Za-z0-9 _.,:;#/()'+=?-]*")
ANCHOR = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,80}")
KEY = re.compile(r"[a-z][a-z0-9-]{0,40}")   # a key the page made: of an explanation, of a fold
FREE = ("content", "aria-label", "placeholder")   # take only the constants below (and the policy)
CONSTANTS = frozenset((VIEWPORT, "no-referrer", "Ways into the mind", "One memory", "First visit",
                       "Words to look for", "For example: memory, quote, speed", "Map of the threads of this mind over time",
                       "The parts of the text, in order", "The threads, by section", "The map of what we know",
                       "Open or close every branch", "Which map to show"))
MSPACE_WIDTHS = frozenset(("0.1667em", "0.2222em", "0.2778em", "-0.1667em", "1em", "2em", "0.5em"))


def _whole(low, high):
    def accepts(value):
        if isinstance(value, bool):
            return False
        if isinstance(value, str):
            value = int(value) if re.fullmatch(r"[0-9]{1,6}", value) else None
        return isinstance(value, int) and low <= value <= high
    return accepts


# an attribute that only some elements may carry, and with which values: attribute -> (tags, accepts a value)
ATTR_RULES = {
    "rowspan": (frozenset(("td", "th")), _whole(1, 40)),
    "colspan": (frozenset(("td", "th")), _whole(1, 12)),
    "start": (frozenset(("ol",)), _whole(1, 9999)),
    "display": (frozenset(("math",)), lambda value: value in ("block", "inline")),
    "mathvariant": (frozenset(("mi",)), lambda value: value == "normal"),
    "accent": (frozenset(("mover",)), lambda value: value == "true"),
    "aria-expanded": (frozenset(("button",)), lambda value: value in ("true", "false")),
    "data-explain": (frozenset(("button",)), lambda value: isinstance(value, str) and bool(KEY.fullmatch(value))),
    "data-fold": (frozenset(("button",)), lambda value: isinstance(value, str) and bool(KEY.fullmatch(value))),
    "src": (frozenset(("img",)), lambda value: isinstance(value, DataUri) and DATA_URI.fullmatch(value) is not None
            and len(value) <= 8 * 1024 * 1024),
    "alt": (frozenset(("img",)), lambda value: isinstance(value, str)),
    "loading": (frozenset(("img",)), lambda value: value == "lazy"),
}


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
    fixed word or number. A link that leaves the page gets rel="noopener noreferrer" here, not from its caller."""
    if tag not in TAGS:
        raise ValueError("element <%s> is not on the list" % tag)
    attrs = dict(attrs or {})
    if isinstance(attrs.get("href"), Href) and attrs["href"].startswith(SCHEME):
        attrs["rel"] = "noopener noreferrer"  # every link that leaves the page, whatever the call site says
    written = []
    for name, value in attrs.items():
        if value is None or value is False:
            continue
        if name not in ATTRS:
            raise ValueError("attribute %s is not on the list" % name)
        rule = ATTR_RULES.get(name)
        if rule is not None and (tag not in rule[0] or not rule[1](value)):
            raise ValueError("attribute %s is not allowed on <%s> or not with that value" % (name, tag))
        if name == "width" and tag == "mspace" and value not in MSPACE_WIDTHS:
            raise ValueError("an mspace takes one of the fixed widths")
        if name in ("width", "height") and tag == "img" and not _whole(1, 20000)(value):
            raise ValueError("a picture's size is a whole number from 1 to 20000")
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


def picture(raw):
    """(address, width, height) of a picture the page may embed, from its bytes, judged again here from the magic
    bytes (PNG, JPEG, GIF, WebP; never SVG), or None. The address is the only kind of `src` that E() accepts."""
    if not isinstance(raw, bytes):
        return None
    found = mind_figures.inspect(raw)
    if len(found) != 3 or found[0] is None:
        return None
    kind, width, height = found
    return DataUri("data:image/%s;base64,%s" % (kind, base64.b64encode(raw).decode("ascii"))), width, height


ALT_UNSAFE = re.compile(r"[^A-Za-z0-9 _.,:;#/()'+=?-]+")


def alt_text(words):
    """A picture's alternative text from the figure's title: the one record text that goes into an attribute, so it
    is cut to the characters every attribute value of the page may hold (no quote, angle bracket or line break)."""
    return " ".join(ALT_UNSAFE.sub(" ", mind_model.clean(words if isinstance(words, str) else "")).split())[:150]


def anchor(name):
    """An address inside the page: # and a name the page made."""
    if not isinstance(name, str) or not ANCHOR.fullmatch(name):
        raise ValueError("not a name the page makes")
    return Href("#" + name)


ISSUE_NUMBER = re.compile(r"[0-9]{1,10}")
COMMENT_ID = re.compile(r"[0-9]{1,19}")
LINE_NUMBER = re.compile(r"[0-9]{1,7}")
RUN_ID = re.compile(r"[1-9][0-9]{0,14}")
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
    path), run (repo, run id: the latest build of the paper)."""
    if not recortex.valid_repo(repo):
        return None
    base = SCHEME + "github.com/" + repo
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
    if kind == "run":   # the latest build of the paper (references/versions.md): the only address that is not an issue's
        n = number(parts[0], RUN_ID)
        return Href("%s/actions/runs/%d" % (base, n)) if n else None
    raise ValueError("no address of kind %s" % kind)


# ---------------------------------------------------------------- the page's own words
# The kinds of memory, the states of a memory and the states of a thread are data: a row here, and a color in the
# style, adds one; the script reads only the keys. Each row has one fixed explanation, opened by a button.

KIND_TABLE = (  # (kind, the word, what it records)
    ("result", "Found", "A finding: something we measured or checked, told with what was done and what the numbers say."),
    ("decision", "Decided", "A decision: a choice the project made, in the words of the person who made it."),
    ("failure", "Went wrong", "Something that went wrong: what failed and, when it is known, why."),
    ("idea", "Idea", "An idea written down. It may be tested later, taken up in a decision, or set aside."),
    ("question", "Asked", "A question the project asked. It stays open until a later result, failure or decision "
                          "answers it."),
    ("correction", "Corrected", "A correction: an earlier statement was wrong, and this says what is right."),
    ("checkpoint", "Pause", "A pause is where a stretch of work ended and a note was left for whoever came next. It is "
                            "not a claim, so it has no state. What came after it follows by time."),
    ("plan", "Planned", "A part of the plan of the paper: something it will claim, show or concede. Its state is worked "
                        "out from the findings that bear on it, never typed."),
    ("text", "Text change", "A change of the paper's text: what changed, why, and how it came about: written by the plugin "
                            "with your word or without asking, approved by you on GitHub, or changed by you by hand."),
)
STATE_TABLE = (  # (state, the key of its explanation, its title, what it means)
    ("stands", "state-stands", "Still stands", "Nothing recorded later replaces it or sets it aside. It is what we hold "
                                               "today; it can still turn out wrong."),
    ("replaced", "state-replaced", "Replaced", "A later memory of the same kind says something else. The newer one is one "
                                              "step away."),
    ("aside", "state-aside", "Set aside", "We chose not to go this way. The memory that says what we did instead is "
                                          "linked."),
    ("open", "state-open", "Still open", "Asked, and no later memory answers it."),
    ("answered", "state-answered", "Answered", "A later result, failure or decision answers it."),
    ("untested", "state-untested", "Not tested yet", "Written down; nothing took it up and no result tested it."),
    ("taken", "state-taken", "Taken up", "A later decision or finding was made because of this idea. No result has put "
                                         "the idea itself to a test."),
    ("tried", "state-tried", "Tried", "A result or a failure put it to a test."),
    ("nothing", "state-nothing", "Nothing followed yet", "No decision or fix points back to this failure."),
    ("led", "state-led", "Led to a change", "A later memory was made because of it."),
    ("plan-dropped", "state-plan-dropped", "Dropped", "A decision, or a later part of the plan, set this part aside."),
    ("plan-revised", "state-plan-revised", "Revised", "A later part of the plan says it again, narrowed, reworded, widened or "
                                                      "reversed. The newer one is the plan now."),
    ("plan-doubt", "state-plan-doubt", "A finding speaks against it", "A finding that still stands was recorded as "
                                                                     "speaking against this part."),
    ("plan-moved", "state-plan-moved", "What it rested on has changed", "A finding or decision this part follows from was "
                                                                       "replaced, corrected or set aside."),
    ("plan-waits", "state-plan-waits", "Waits for another part", "It depends on a part of the plan that nothing backs yet."),
    ("plan-text", "state-plan-text", "In the text", "The text says it: its block is in a topic's text, or a change of the "
                                                    "text followed from it."),
    ("plan-can-be-made", "state-plan-can-be-made", "Can be made now", "Every claim it shows is backed, so the figure or "
                                                                      "table can be made."),
    ("plan-backed", "state-plan-backed", "Backed", "A finding that still stands is linked to it. That does not make it "
                                                   "true: the finding can still turn out wrong."),
    ("plan-failed", "state-plan-failed", "An attempt to show it failed", "An attempt to show it failed. Not tested is not "
                                                                         "negative, so this does not put it in doubt."),
    ("plan-planned", "state-plan-planned", "Planned", "Planned, and nothing backs it yet."),
    ("plan-stands", "state-plan-stands", "Stands", "A part that is no claim: it stands as planned."),
)
THREAD_TABLE = (  # (the key of its explanation, its title, what it means)
    ("thread-open", "Open points", "Its open questions, untested ideas and failures nothing followed."),
    ("thread-clear", "Nothing open", "Every question in it has an answer and every failure a follow-up. Its text can "
                                     "still change."),
    ("thread-aside", "Set aside", "Its topic was closed out; the story says why."),
)
KIND_CLASS = {kind: "k-" + kind for kind, _, _ in KIND_TABLE}
STATE_CLASS = {css: "s-" + css for css in ("stands", "replaced", "aside", "answered", "tried", "taken", "open", "led",
                                           "pause", "plain")}
LINK_CLASS = {kind: "l-" + kind for kind in ("because", "instead", "replaces", "checks")}
GROUPS = ("stands", "open", "wrong", "changed", "done", "pause")
KIND_WORD = {kind: word for kind, word, _ in KIND_TABLE}
EXPLAIN = tuple(("kind-" + kind, word, text) for kind, word, text in KIND_TABLE) + \
    tuple((key, title, text) for _, key, title, text in STATE_TABLE) + THREAD_TABLE
EXPLAIN_TEXT = {key: (title, text) for key, title, text in EXPLAIN}
HOW_TO_READ = (
    ("A memory", "One thing the project recorded: a finding, a decision, something that went wrong, an idea, a question, "
                 "a correction or a pause. Each is written once and never changed."),
    ("A thread", "One line of thought, told from its first memory to its latest. It is one topic of the work."),
    ("A part", "The current text of one section for one topic, with the day it last changed and what is still open on "
               "it. The record keeps one current version of each part; it is not marked draft or final."),
    ("A pause", EXPLAIN_TEXT["kind-checkpoint"][1]),
)
NAV = (("now", "Where are we?"), ("text", None), ("threads", "How did we get here?"),
       ("settled", "What is settled?"), ("lessons", "What did we learn the hard way?"), ("open", "What is still open?"),
       ("map", "Show me the whole mind"), ("ask", "Ask the mind"))
CUT = "This part is long, so the page stops here."
CUT_LINK = "Read the rest on GitHub."
TABLE_CUT = "Only the first 40 rows and 12 columns are shown here."
TEX_NOTE = "A formula the page cannot draw is shown as it was written."
HTML_TABLE_NOTE = "This table is written in a form the page cannot draw. Open it on GitHub."
PAUSE_UNTOLD = ("The last pause is not told here: the comment that holds it is not by one of your allowed accounts, was "
                "changed after it was posted, or has not been read yet. Run index.py sync and build the page again.")
NOT_SHOWN = ("The text of this thread is not shown here: someone outside your allowed accounts wrote or changed it, or "
             "its history has not been read yet.")
OLD_NOTE = "Recorded before plain lines and links were kept, so this is told in the record's own short words."
CHANGED_NOTE = "Changed on GitHub after it was posted."
CHAIN_NOTE = ("The first memories here were recorded before links between memories were kept. They are in time order "
              "only.")
TEXT_LEDE = ("This is the current text of each part, in the order of the deliverable. The record keeps one current "
             "version of each part; it is not marked draft or final. Beside each part is the day it last changed and what "
             "is still open on it.")
MAX_MATH_NODES, MAX_MATH_DEPTH = 300, 8
MATH_TOKENS = frozenset(("mi", "mn", "mo", "mtext"))
MATH_PAIR = frozenset(("msub", "msup", "mfrac", "mover", "munder"))
MATH_TRIPLE = frozenset(("msubsup", "munderover"))
MATH_MANY = frozenset(("msqrt", "mrow"))


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


def feeds_first(thread, feeds):
    """The address in the page of the first part of the text that a thread feeds."""
    feed = next((f for f in thread.get("feeds", []) if isinstance(f, dict)), {})
    ordinal = feed.get("ordinal")
    if isinstance(ordinal, int) and not isinstance(ordinal, bool):
        return anchor("text-%d-%d" % (integer(thread.get("n")), ordinal))
    return anchor("sec-%d" % integer(feed.get("n")))


class Page:
    """Renders one model. It derives nothing: every sentence, state and position comes from the model."""

    def __init__(self, model, pictures=None):
        self.model = model
        self.pictures = pictures if isinstance(pictures, dict) else {}
        self.repo = model["repo"] if recortex.valid_repo(model.get("repo")) else None
        self.m = {integer(m.get("id")): m for m in model.get("memories", []) if integer(m.get("id"))}
        self.threads = {integer(t.get("n")): t for t in model.get("threads", [])}
        self.words = {integer(w.get("n")): w for w in model.get("words", []) if integer(w.get("n"))}
        self.cited = {integer(c.get("n")): c for c in model.get("cited", []) if integer(c.get("n"))}
        self.pauses = model.get("pauses", [])
        self.outline = model.get("outline") if isinstance(model.get("outline"), dict) else {}
        self.stores = {n: {(b.get("kind"), b.get("id")): b for b in t.get("blocks", []) if isinstance(b, dict)}
                       for n, t in self.threads.items()}
        self.fallbacks = 0

    # ------------------------------------------------------------ text

    def term(self, text, n, tag="span"):
        if n not in self.words:
            return text
        return E(tag, {"class": "term", "data-term": "w%d" % n, "type": "button" if tag == "button" else None}, text)

    @staticmethod
    def styled(node, run):
        """`node` inside the inline elements that the run's flags name."""
        for flag, tag in (("sup", "sup"), ("sub", "sub"), ("i", "i"), ("b", "b")):
            if run.get(flag) is True:
                node = E(tag, None, node)
        return node

    def runs(self, items, tag="span", nested=False):
        out = []
        for run in items if isinstance(items, list) else []:
            if not isinstance(run, dict):
                continue
            if "t" in run:
                out.append(self.styled(text_of(run["t"]), run))
            elif "code" in run:
                out.append(E("code", None, text_of(run["code"])))
            elif "w" in run:
                out.append(self.styled(self.term(text_of(run["w"]), integer(run.get("n")), tag), run))
            elif "cite" in run:
                out.append(self.cite(run))
            elif "math" in run:
                out.append(self.math(run["math"], False))
            elif "link" in run:
                out.append(self.link_run(run, nested))
        return J(*out)

    def link_run(self, run, nested):
        """A link of the record's text: an anchor only when its address is one of the forms of github_url, built from
        checked parts; else the words and the address written out as text, never an href."""
        words = self.runs(run.get("link"), nested=True)
        to = run.get("to") if isinstance(run.get("to"), dict) else None
        address = None if nested or to is None else self.address(to)
        if address:
            return E("a", {"href": address}, words)
        said = text_of(run.get("said"))
        return J(words, " (", said, ")") if said else words

    def address(self, to):
        kind, repo = to.get("kind"), text_of(to.get("repo"))
        if kind == "issue":
            return github_url("issue", repo, to.get("n"))
        if kind == "pull":
            return github_url("pull", repo, to.get("n"))
        if kind == "memory":
            return github_url("memory", repo, to.get("n"), to.get("cid"))
        if kind == "lines":
            return github_url("lines", repo, to.get("commit"), to.get("path"), to.get("first"), to.get("last"))
        if kind == "picture":
            return github_url("picture", repo, to.get("ref"), to.get("path"))
        return None

    def cite(self, run):
        n, words, kind = integer(run.get("n")), text_of(run.get("text")), run.get("cite")
        if kind == "thread" and n in self.threads:
            return E("a", {"class": "cite", "href": anchor("thread-%d" % n)}, words)
        if kind == "issue" and n in self.cited:
            return E("a", {"class": "cite", "href": anchor("issue-%d" % n), "data-issue": "issue-%d" % n}, words)
        if kind == "memory" and n in self.m:
            return E("a", {"class": "cite", "href": anchor("m-%d" % n), "data-m": n}, words)
        return E("span", None, words)

    # ------------------------------------------------------------ formulas

    def math(self, data, display):
        """A formula as MathML when its tree keeps every rule of the contract (fifteen element names, text only in
        tokens, the number of children each name takes, at most 300 nodes and 8 levels); else its source as text."""
        data = data if isinstance(data, dict) else {}
        tree = data.get("m")
        if isinstance(tree, dict):
            try:
                return E("math", {"display": "block" if display else "inline"}, self.math_node(tree, 1, [0]))
            except ValueError:
                pass
        self.fallbacks += 1
        source = text_of(data.get("s"))
        return E("pre", {"class": "tex"}, source) if display else E("code", {"class": "tex"}, source)

    def math_node(self, node, depth, count):
        count[0] += 1
        if depth > MAX_MATH_DEPTH or count[0] > MAX_MATH_NODES or not isinstance(node, dict):
            raise ValueError("a formula over the limits")
        name = node.get("m")
        if name in MATH_TOKENS:
            text = node.get("t")
            if "c" in node or not isinstance(text, str) or len(text) > 80:
                raise ValueError("a token holds text only")
            return E(name, {"mathvariant": "normal"} if name == "mi" and node.get("v") == "normal" else None, text)
        if name == "mspace":
            if "c" in node or "t" in node:
                raise ValueError("a space holds nothing")
            return E("mspace", {"width": node.get("w")})
        children = node.get("c")
        if not isinstance(children, list) or "t" in node:
            raise ValueError("a layout element holds elements")
        wanted = 2 if name in MATH_PAIR else 3 if name in MATH_TRIPLE else None
        if name not in MATH_PAIR | MATH_TRIPLE | MATH_MANY or (wanted is None and not children) or \
                (wanted is not None and len(children) != wanted):
            raise ValueError("an element with the wrong number of children")
        return E(name, {"accent": "true"} if name == "mover" and node.get("acc") is True else None,
                 *[self.math_node(child, depth + 1, count) for child in children])

    # ------------------------------------------------------------ tables and blocks

    def cell(self, cell, head):
        cell = cell if isinstance(cell, dict) else {}
        rs, cs = integer(cell.get("rs"), 1), integer(cell.get("cs"), 1)
        shade = {"l": "al", "c": "ac", "r": "ar"}.get(cell.get("a"))
        return E("th" if head or cell.get("h") is True else "td",
                 {"rowspan": rs if rs > 1 else None, "colspan": cs if cs > 1 else None, "class": shade},
                 self.runs(cell.get("r")))

    def table(self, table):
        if not isinstance(table, dict):
            return ""
        rows = lambda key, head: [E("tr", None, *[self.cell(c, head) for c in row]) for row in table.get(key, [])
                                  if isinstance(row, list)]
        caption = self.runs(table.get("caption")) if table.get("caption") else ""
        return J(E("p", {"class": "quiet"}, caption) if caption else "",
                 E("div", {"class": "tbox"}, E("table", None, E("thead", None, *rows("head", True)),
                                               E("tbody", None, *rows("rows", False)))),
                 E("p", {"class": "quiet"}, TABLE_CUT) if table.get("cut") else "")

    def blocks(self, doc, cut_to=None, store=None, issue=None):
        """The blocks of a doc of the model. `cut_to` is the address where a doc that was cut goes on (the topic or the
        comment); `store` holds the figure and table blocks that stand in the text, by (kind, id); `issue` is the
        topic's number, for the link of a table the page cannot draw."""
        out, before = [], self.fallbacks
        for block in (doc or {}).get("blocks", []) if isinstance(doc, dict) else []:
            if isinstance(block, dict):
                out.append(self.block(block, store, issue))
        if self.fallbacks > before:
            out.append(E("p", {"class": "quiet"}, TEX_NOTE))
        if isinstance(doc, dict) and doc.get("cut"):
            out.append(E("p", {"class": "quiet"}, CUT, " ", E("a", {"href": cut_to}, CUT_LINK) if cut_to else ""))
        return J(*out)

    def block(self, block, store, issue):
        if "p" in block:
            return E("p", None, self.runs(block["p"]))
        if "h" in block:
            data = block["h"] if isinstance(block["h"], dict) else {}
            return E({1: "h4", 2: "h5", 3: "h6"}.get(integer(data.get("level")), "h6"), {"class": "sub"},
                     self.runs(data.get("r")))
        for key, tag in (("ul", "ul"), ("ol", "ol")):
            if key in block:
                return self.list_block(block, key, tag)
        if "quote" in block:
            return E("blockquote", None, self.runs(block["quote"]))
        if "code" in block:
            return E("pre", None, E("code", None, text_of(block["code"])))
        if "math" in block:
            return E("div", {"class": "formula"}, self.math(block["math"], True))
        if "table" in block:
            return self.table(block["table"])
        if "plan" in block:
            return self.plan_table(block["plan"])
        if "note" in block:
            address = github_url("issue", self.repo, issue)
            return E("p", {"class": "quiet"}, HTML_TABLE_NOTE, " ", E("a", {"href": address}, "Open it on GitHub")
                     if address else "")
        if "block" in block and store is not None:
            data = block["block"] if isinstance(block["block"], dict) else {}
            found = store.get((data.get("kind"), data.get("id")))
            if found:
                return self.table_block(found) if found.get("kind") == "table" else self.figure_block(found)
        return ""

    def list_block(self, block, key, tag):
        items = []
        for item in block[key] if isinstance(block[key], list) else []:
            item = item if isinstance(item, dict) else {}
            sub = item.get("sub") if isinstance(item.get("sub"), dict) else None
            items.append(E("li", None, self.runs(item.get("r")),
                           self.list_block(sub, "ul" if "ul" in sub else "ol", "ul" if "ul" in sub else "ol")
                           if sub and ("ul" in sub or "ol" in sub) else ""))
        start = integer(block.get("start"), 1) if tag == "ol" else 1
        return E(tag, {"class": "plainlist", "start": start if start > 1 else None}, *items)

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

    def section_link(self, n, label):
        return E("a", {"href": anchor("sec-%d" % integer(n))}, text_of(label))

    # ------------------------------------------------------------ pieces

    @staticmethod
    def explain_button(key, text, cls):
        """A word that explains itself when chosen: a button whose key names a fixed explanation."""
        if key in EXPLAIN_TEXT or key == "how":
            return E("button", {"type": "button", "class": cls, "data-explain": key}, text)
        return E("span", {"class": cls}, text)

    @staticmethod
    def mark(memory_or_kind):
        kind = memory_or_kind if isinstance(memory_or_kind, str) else memory_or_kind.get("kind")
        return E("span", {"class": "mk " + KIND_CLASS.get(kind, "k-other"), "aria-hidden": "true"})

    def chip(self, memory, thread=False):
        words = text_of(memory.get("latest")) + ("" if thread else text_of(memory.get("latest_note"))) \
            or text_of(memory.get("chip"))
        if not words:
            return ""
        return self.explain_button(text_of(memory.get("explain")), words, "chip " + state_class(memory))

    def meta(self, memory, thread=False, date=False):
        """A card's first line: the kind, where, the day and the state. A latest decision or result says so in its
        chip; on a page about the whole project it names its thread, as a link, after the chip."""
        latest, note = text_of(memory.get("latest")), text_of(memory.get("latest_note"))
        tail = E("span", {"class": "in"}, " in ", self.thread_link(memory.get("thread")), note) \
            if latest and thread else ""
        return E("div", {"class": "meta"}, self.mark(memory),
                 self.explain_button("kind-" + text_of(memory.get("kind")), KIND_WORD.get(memory.get("kind"), "Noted"),
                                     "kw"),
                 E("span", {"class": "in"}, "in ", self.thread_link(memory.get("thread"))) if thread and not latest
                 else "", E("span", {"class": "date"}, text_of(memory.get("day"))) if date else "",
                 self.chip(memory, thread), tail)

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

    def table_block(self, block):
        """A table block drawn open where the author put it."""
        return E("div", {"class": "blockfig"}, E("p", {"class": "evtitle"}, text_of(block.get("title"))),
                 E("p", None, text_of(block.get("caption"))), self.table(block.get("table")), self.made_from(block))

    def table_ref(self, block):
        place = block.get("place") if isinstance(block.get("place"), dict) else {}
        ordinal = place.get("ordinal")
        where = E("a", {"href": anchor("text-%d-%d" % (integer(place.get("thread")), ordinal))}, "See it in the text") \
            if isinstance(ordinal, int) and not isinstance(ordinal, bool) and integer(place.get("thread")) in self.threads \
            else ""
        return self.show("Show the table", E("p", {"class": "evtitle"}, text_of(block.get("title"))),
                         E("p", None, text_of(block.get("caption"))), self.table(block.get("table")),
                         self.made_from(block),
                         E("p", {"class": "quiet"}, "This is the table as it is in the current text. ", where))

    def picture_of(self, block):
        """The embedded picture of a figure block: an img built from bytes that mind.picture checked, else nothing."""
        key = (text_of(block.get("repo")) or self.repo, text_of(block.get("ref")), text_of(block.get("path")))
        made = picture(self.pictures.get(key))
        if made is None:
            return ""
        uri, width, height = made
        return E("img", {"src": uri, "alt": alt_text(block.get("title")), "width": width, "height": height,
                         "loading": "lazy", "class": "figpic"})

    def figure_body(self, block):
        address = github_url("picture", text_of(block.get("repo")) or self.repo, block.get("ref"), block.get("path"))
        return [E("p", {"class": "evtitle"}, text_of(block.get("title"))), E("p", None, text_of(block.get("caption"))),
                self.picture_of(block),
                E("p", {"class": "quiet"}, "The picture is ", E("code", None, text_of(block.get("path"))),
                  " at ", text_of(block.get("ref")), ".") if text_of(block.get("path")) else "",
                self.made_from(block),
                E("a", {"href": address}, "Open the picture on GitHub")
                if address else E("p", {"class": "nothing"}, "This memory names a figure that this page cannot link to.")]

    def figure_block(self, block):
        return E("div", {"class": "blockfig"}, *self.figure_body(block))

    def figure_ref(self, block):
        return self.show("Show the figure", *self.figure_body(block))

    def code_ref(self, item):
        first, last = integer(item.get("from")), integer(item.get("to"))
        address = github_url("lines", text_of(item.get("repo")), item.get("ref"), item.get("path"), first, last)
        span = "line %d" % first if first == last else "lines %d to %d" % (first, last)
        return self.show("Show the code", E("p", {"class": "evtitle"}, E("code", None, text_of(item.get("path"))),
                                            ", %s, as they were at the change " % span, text_of(item.get("ref"))[:12]),
                         E("a", {"href": address}, "Open these lines on GitHub")
                         if address else E("p", {"class": "nothing"}, "This memory names code that this page cannot "
                                                                       "link to."))

    def pull_ref(self, item):
        address = github_url("pull", text_of(item.get("repo")), item.get("number"))
        return self.show("Show the change that was merged", E("p", {"class": "evtitle"}, text_of(item.get("title"))),
                         E("a", {"href": address}, "Open the pull request on GitHub")
                         if address else E("p", {"class": "nothing"}, "This memory names a change that this page "
                                                                       "cannot link to."))

    def references(self, memory):
        out = []
        for item in memory.get("evidence", []):
            kind = item.get("kind")
            if kind in ("table", "figure") and not item.get("found"):
                out.append(E("p", {"class": "quiet"}, (
                    "This memory pointed to a %s that is no longer in the current text." if item.get("shown") else
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

    def field(self, entry, cut_to=None):
        if entry.get("type") == "people":
            body = E("p", {"class": "people"}, text_of(entry.get("people")))
        else:
            body = self.blocks(entry.get("doc"), cut_to)
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
                 E("p", {"class": "quiet plannote"}, {"bore": "This bore out a planned claim.",
                                                     "against": "This spoke against a planned claim."}[memory["plan_note"]])
                 if memory.get("plan_note") in ("bore", "against") else "",
                 E("p", {"class": "ans"}, text_of(memory.get("passage"))),
                 E("p", {"class": "inthread"}, "Part of the thread ", self.thread_link(memory.get("thread")), "."),
                 *[self.field(entry, address) for entry in memory.get("fields", [])],
                 self.links("What led here", memory.get("out", [])),
                 self.links("What came of it", memory.get("in", [])),
                 E("div", {"class": "f refs"}, E("span", {"class": "lab"}, "If you want to see for yourself"), *refs)
                 if refs else "",
                 E("p", {"class": "quiet oldnote"}, OLD_NOTE) if memory.get("old") is True else "",
                 E("p", {"class": "quiet changednote"}, CHANGED_NOTE) if memory.get("changed") is True else "",
                 E("details", {"class": "where"}, E("summary", None, "Where this was said"),
                   *[self.field(entry) for entry in memory.get("where", [])],
                   E("p", None, "Written by ", E("span", {"class": "people"}, who), " at %s, as a comment in the "
                                                                                    "project's record on GitHub." % when),
                   E("a", {"href": address}, "Open the original comment on GitHub")
                   if address else ""))

    # ------------------------------------------------------------ the views

    def untold_line(self):
        """The last line of every view but the first: how many things the page does not tell, and where it says why."""
        count = integer((self.model.get("not_told") or {}).get("count"))
        if not count:
            return ""
        return E("p", {"class": "quiet untoldline"}, plural(count, "1 thing is not told on this page. ",
                                                            "%d things are not told on this page. "),
                 E("a", {"href": anchor("untold")}, "See why"))

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
        return self.view("now", "Where we are", None, *out, self.untold())

    # ------------------------------------------------------------ the text

    OPEN_GROUPS = ("questions", "ideas", "failures", "unknown", "plan-unbacked", "plan-doubt", "plan-unused", "holds", "tasks")

    def sentences(self, items):
        """The sentences of a part's status line, each a list of segments, as plain nodes."""
        out = []
        for sentence in items if isinstance(items, list) else []:
            for seg in sentence.get("segs", []) if isinstance(sentence, dict) and isinstance(sentence.get("segs"), list) \
                    else []:
                if not isinstance(seg, dict):
                    continue
                words = text_of(seg.get("t"))
                if "gh" in seg:
                    out.append(self.plan_link(seg, words))
                elif "thread" in seg and integer(seg["thread"]) in self.threads:
                    out.append(self.thread_link(seg["thread"]))
                elif "m" in seg and integer(seg["m"]) in self.m:
                    out.append(E("a", {"class": "inl", "href": anchor("m-%d" % integer(seg["m"])),
                                       "data-m": integer(seg["m"])}, words))
                elif "open" in seg and seg["open"] in self.OPEN_GROUPS:
                    out.append(E("a", {"href": anchor("open-%s" % seg["open"])}, words))
                else:
                    out.append(words)
            out.append(" ")
        return out

    # ------------------------------------------------------------ the plan and the history of a part

    def plan_link(self, seg, words=None):
        """A planned part as a sentence that opens its comment on GitHub: the address is built from checked numbers."""
        words = words if words is not None else text_of(seg.get("t"))
        address = github_url("memory", self.repo, seg.get("issue"), seg.get("gh"))
        return E("a", {"class": "inl", "href": address}, words) if address else words

    def plan_cell(self, segs):
        out = []
        for seg in segs if isinstance(segs, list) else []:
            if not isinstance(seg, dict):
                continue
            words = text_of(seg.get("t"))
            if "gh" in seg:
                out.append(self.plan_link(seg, words))
            elif "m" in seg and integer(seg["m"]) in self.m:
                out.append(E("a", {"class": "inl", "href": anchor("m-%d" % integer(seg["m"])), "data-m": integer(seg["m"])},
                             words))
            else:
                out.append(words)
        return J(*out)

    def plan_table(self, data):
        """"What we plan to say here": one row per planned part, three cells, each a sentence."""
        rows = [row for row in (data.get("rows") if isinstance(data, dict) else []) or [] if isinstance(row, dict)]
        head = mind_model.PHRASES["plan.table.head"]
        body = []
        for row in rows:
            cells = row.get("cells") if isinstance(row.get("cells"), list) else []
            body.append(E("tr", {"class": "p-" + (row.get("state") if row.get("state") in STATE_CLASS else "plain")},
                          *[E("td", None, self.plan_cell(cells[i] if i < len(cells) else [])) for i in range(3)]))
        return E("div", {"class": "plantable"}, E("table", None, E("thead", None, E("tr", None, *[E("th", None, w) for w in head])),
                                                   E("tbody", None, *body)))

    def plan_summary(self):
        """"The plan against the text": the message, then one row per section with its counts, each a link."""
        plan = self.model.get("plan")
        if not isinstance(plan, dict):
            return ""
        number = {}
        for group in (self.model.get("outline") or {}).get("deliverables", []):
            for section in group.get("sections", []) if isinstance(group, dict) else []:
                if isinstance(section, dict):
                    number.setdefault(text_of(section.get("label")).lower(), integer(section.get("n")))
        rows = []
        for entry in plan.get("summary", []) if isinstance(plan.get("summary"), list) else []:
            if not isinstance(entry, dict):
                continue
            label = text_of(entry.get("section"))
            n = number.get(label.lower())
            words = []
            if integer(entry.get("planned")):
                words.append(plural(integer(entry.get("planned")), "1 planned part", "%d planned parts"))
                words.append("%d backed" % integer(entry.get("backed")))
                if integer(entry.get("doubt")):
                    words.append(plural(integer(entry.get("doubt")), "1 with a finding against it or resting on something "
                                        "that changed", "%d with a finding against them or resting on something that changed"))
            else:
                words.append("nothing planned for it")
            words.append("text written" if entry.get("has_text") is True else "no text yet")
            rows.append(E("li", None, E("a", {"href": anchor("sec-%d" % n)}, label) if n else label, ": " + "; ".join(words) + "."))
        message = plan.get("message") if isinstance(plan.get("message"), dict) else None
        return E("section", {"class": "plansum", "id": "plan-against-text"},
                 E("h3", None, mind_model.PHRASES["plan.summary"]),
                 E("p", None, E("span", {"class": "lab"}, mind_model.PHRASES["plan.message"] + ": "),
                   self.plan_link({"gh": message.get("id"), "issue": message.get("issue")}, text_of(message.get("plain")))
                   if message else "not planned yet", "."),
                 E("ul", {"class": "plansummary"}, *rows) if rows else "")

    def history(self, part):
        """The History of a part: a fold, newest version first, each with its day, how it came about, the reason and the
        names it carries; the difference is drawn by Page.diff() (mind_versions) when the model holds one."""
        data = part.get("history") if isinstance(part.get("history"), dict) else None
        entries = [e for e in (data.get("entries") if data else []) or [] if isinstance(e, dict)]
        if not entries:
            return ""
        kept = data.get("kept") is True
        items = []
        for entry in entries:
            how = text_of(entry.get("how"))
            words = mind_model.PHRASES.get("how." + how) if how in mind_plan.HOW else None
            lines = [E("p", {"class": "histhead"}, E("b", None, text_of(entry.get("day"))), ", " + words if words else "")]
            memory = self.m.get(integer(entry.get("id")))
            if memory is not None:
                lines.append(E("p", None, self.said(memory, "inl"), "  ", text_of(entry.get("why"))))
            for name in entry.get("names", []) if isinstance(entry.get("names"), list) else []:
                if isinstance(name, dict):
                    lines.append(E("p", {"class": "quiet"}, "Named %s on %s." % (text_of(name.get("name")), text_of(name.get("day")))))
            note = {"days": "hist.nodiff.days", "unfetched": "hist.nodiff.unfetched", "unchecked": "hist.nodiff.unchecked",
                    "before": "hist.nodiff.before", "budget": "hist.nodiff.budget"}.get(entry.get("no_diff"))
            diff = entry.get("diff")
            if isinstance(diff, list) and diff:
                lines.append(self.show(mind_model.PHRASES["hist.changed"], self.diff(diff)))
            elif note:
                lines.append(E("p", {"class": "quiet"}, mind_model.PHRASES[note]))
            items.append(E("li", {"class": "hist"}, *lines))
        title = plural(len(entries), "History (1 version)", "History (%d versions)") if kept else "History"
        first = E("p", {"class": "quiet"}, mind_model.PHRASES["hist.first"]) if kept else ""
        guide = E("p", {"class": "quiet"}, mind_model.PHRASES["hist.restore"] % text_of(entries[0].get("day"))) if kept else \
            E("p", {"class": "quiet"}, mind_model.PHRASES["hist.days"])
        return self.show(title, guide, E("ol", {"class": "histlist"}, *items), first)

    def diff(self, ops):
        """A difference between two versions of a section's text, drawn from the model's operations. This is the one place
        that makes an `ins` or a `del` element, and only from a run of type add or del: an operation or a run type that is not
        on the lists of mind_versions is drawn as plain text, and every text is text (through E())."""
        out = []
        for op in ops if isinstance(ops, list) else []:
            if not isinstance(op, dict):
                continue
            kind = op.get("op")
            if kind == "same" and "n" in op:
                count = integer(op.get("n"))
                out.append(E("p", {"class": "dsame"}, mind_model.PHRASES["diff.same.one"] if count == 1 else
                             mind_model.PHRASES["diff.same"] % count))
            elif kind == "cut":
                out.append(E("p", {"class": "quiet"}, mind_model.PHRASES["diff.cut"]))
            else:
                runs = op.get("runs") if isinstance(op.get("runs"), list) else []
                parts = []
                for run in runs:
                    if not isinstance(run, dict):
                        continue
                    words = text_of(run.get("text"))
                    if kind in mind_versions.OPS and run.get("t") == "add":
                        parts.append(E("ins", None, words))
                    elif kind in mind_versions.OPS and run.get("t") == "del":
                        parts.append(E("del", None, words))
                    else:
                        parts.append(words)
                cls = "d-" + kind if kind in ("add", "del", "change") else "d-plain"
                out.append(E("p", {"class": cls}, *parts))
        return E("div", {"class": "diff"}, *out)

    def versions_block(self):
        """"Versions of the paper": each named version with its day and the author's words, under it what changed since the
        name before (a fold per part that changed, and a count of the parts that did not), and the line of the latest build."""
        data = self.model.get("versions")
        if not isinstance(data, dict) or data.get("kept") is not True:
            return ""
        names = [n for n in data.get("names", []) if isinstance(n, dict)] if isinstance(data.get("names"), list) else []
        items = []
        for entry in names:
            lines = [E("p", {"class": "histhead"}, E("b", None, text_of(entry.get("name"))), "  ",
                       mind_model.PHRASES["ver.named"] % (text_of(entry.get("name")), text_of(entry.get("day"))))]
            if text_of(entry.get("words")):
                lines.append(E("p", {"class": "quiet"}, "\u201c" + text_of(entry.get("words")) + "\u201d"))
            build = entry.get("build") if isinstance(entry.get("build"), dict) else None
            address = github_url("run", self.repo, build.get("run")) if build else None
            if address:
                lines.append(E("p", {"class": "quiet"}, mind_model.PHRASES["ver.build.name"] % text_of(build.get("day")), "  ",
                               E("a", {"class": "inl", "href": address}, mind_model.PHRASES["ver.build.open"])))
            since = entry.get("since") if isinstance(entry.get("since"), dict) else None
            if since is not None:
                inner = []
                for part in since.get("parts", []) if isinstance(since.get("parts"), list) else []:
                    if not isinstance(part, dict):
                        continue
                    label = "%s: %s" % (self.thread_name(integer(part.get("thread"))), text_of(part.get("label")))
                    if isinstance(part.get("diff"), list) and part["diff"]:
                        inner.append(self.show(label, self.diff(part["diff"])))
                    else:
                        note = {"unfetched": "hist.nodiff.unfetched", "unchecked": "hist.nodiff.unchecked",
                                "budget": "hist.nodiff.budget"}.get(part.get("no_diff"))
                        inner.append(E("p", {"class": "quiet"}, label + ". " + (mind_model.PHRASES[note] if note else "")))
                same = integer(since.get("same"))
                if same:
                    inner.append(E("p", {"class": "quiet"}, mind_model.PHRASES["ver.same.one"] if same == 1 else
                                   mind_model.PHRASES["ver.same"] % same))
                lines.append(self.show(mind_model.PHRASES["ver.since"] % text_of(since.get("name")), *inner))
            items.append(E("li", {"class": "hist"}, *lines))
        build = data.get("build") if isinstance(data.get("build"), dict) else None
        address = github_url("run", self.repo, build.get("run")) if build else None
        latest = E("p", {"class": "quiet"}, mind_model.PHRASES["ver.build"] % text_of(build.get("day")), "  ",
                   E("a", {"class": "inl", "href": address}, mind_model.PHRASES["ver.build.open"])) if address else ""
        return E("section", {"class": "versions", "id": "versions-of-the-paper"}, E("h3", None, mind_model.PHRASES["ver.title"]),
                 E("ol", {"class": "histlist"}, *items) if items else E("p", {"class": "quiet"}, mind_model.PHRASES["ver.none"]),
                 latest)

    def open_plan_group(self, key, title, hint, rows, empty):
        return E("section", {"class": "ogroup", "id": "open-%s" % key}, E("h3", None, title),
                 E("p", {"class": "quiet"}, hint), E("ul", {"class": "notes"}, *rows) if rows else
                 E("p", {"class": "nothing"}, empty))

    def part_link(self, part):
        n, ordinal = integer(part.get("thread")), part.get("ordinal")
        if isinstance(ordinal, int) and not isinstance(ordinal, bool):
            return E("a", {"href": anchor("text-%d-%d" % (n, ordinal))}, self.thread_name(n))
        return self.thread_link(n)

    def part(self, part):
        n, ordinal = integer(part.get("thread")), part.get("ordinal")
        thread = self.threads.get(n)
        if not thread:
            return ""
        sections = thread.get("sections", [])
        text = sections[ordinal] if isinstance(ordinal, int) and not isinstance(ordinal, bool) and \
            0 <= ordinal < len(sections) and isinstance(sections[ordinal], dict) else None
        topic = thread.get("topic")
        address = github_url("issue", self.repo, topic)
        if thread.get("shown") is True and text is not None:
            body = self.blocks(text.get("doc"), address, self.stores.get(n), topic)
            ident = "text-%d-%d" % (n, ordinal)
        else:
            body = J(E("p", None, NOT_SHOWN), E("a", {"href": address}, "Open it on GitHub") if address else "")
            ident = None
        return E("article", {"class": "part", "id": ident},
                 E("p", {"class": "status"}, *self.sentences(part.get("status"))), E("div", {"class": "parttext"}, body),
                 self.history(part),
                 E("p", {"class": "partlinks"}, E("a", {"href": anchor("settled-%d" % n)}, "Why it says this"), "  ",
                   E("a", {"href": anchor("thread-%d" % n)}, "How we got here")))

    def text_view(self):
        outline = self.outline
        groups = [g for g in outline.get("deliverables", []) if isinstance(g, dict)] \
            if isinstance(outline.get("deliverables"), list) else []
        other = [o for o in outline.get("other", []) if isinstance(o, dict)] if isinstance(outline.get("other"), list) \
            else []
        title = text_of(outline.get("title")) or "What we deliver"
        noun = text_of(outline.get("noun")) or "the deliverable"
        if not groups and not other:
            return self.view("text", title, TEXT_LEDE,
                             E("p", {"class": "quiet"}, "No part of %s has been written into the record yet." % noun),
                             self.untold_line())
        contents, body, sourced = [], [], []
        for group in groups:
            sections = [s for s in group.get("sections", []) if isinstance(s, dict)]
            if len(groups) > 1:
                body.append(E("h3", {"class": "deliv"}, text_of(group.get("title"))))
            for section in sections:
                parts = [p for p in section.get("parts", []) if isinstance(p, dict)]
                n = integer(section.get("n"))
                contents.append(E("li", None, self.section_link(n, section.get("label")),
                                  E("ul", None, *[E("li", None, self.part_link(p)) for p in parts])))
                body.append(E("section", {"class": "sec", "id": "sec-%d" % n}, E("h3", None, text_of(section.get("label"))),
                              self.blocks(section.get("before")), *[self.part(p) for p in parts]))
                sourced += [integer(p.get("thread")) for p in parts]
        by_thread = []
        for entry in other:
            n = integer(entry.get("thread"))
            if not by_thread or by_thread[-1][0] != n:
                by_thread.append((n, []))
            by_thread[-1][1].append(entry)
        for n, entries in by_thread:
            heading = E("h3", None, text_of((self.threads.get(n) or {}).get("name"))) if not groups else \
                E("h3", None, "Other text of ", self.thread_link(n))
            if not groups:
                contents.append(E("li", None, E("a", {"href": anchor("other-%d" % n)}, self.thread_name(n))))
            body.append(E("section", {"class": "sec", "id": "other-%d" % n}, heading, *[self.part(p) for p in entries]))
            sourced.append(n)
        numbers = []
        for n in dict.fromkeys(sourced):
            sources = (self.threads.get(n) or {}).get("sources")
            if sources:
                numbers.append(self.show(J("Where the numbers of ", self.thread_name(n), " come from"), E("ul", {
                    "class": "srcs"}, *[E("li", None, E("b", None, self.runs(row.get("statement"))), ": ",
                                          self.runs(row.get("source"))) for row in sources])))
        return self.view("text", title, TEXT_LEDE, self.plan_summary(), self.versions_block(),
                         E("ul", {"class": "contents", "aria-label": "The parts of the text, in order"}, *contents),
                         *body, E("h3", None, "Where the numbers come from") if numbers else "", *numbers,
                         self.untold_line())

    # ------------------------------------------------------------ the story

    def story_line(self, thread):
        began = thread.get("began")
        return "It began on %s; the latest memory is from %s." % (text_of(began), text_of(thread.get("ended"))) \
            if began else "It holds no memories yet."

    def station(self, memory, mark_id=None):
        return E("li", {"class": "station %s %s%s" % (kind_class(memory), state_class(memory),
                                                        " deadend" if memory.get("side") is True else ""),
                        "id": mark_id}, self.meta(memory, date=True), self.said(memory))

    def divider(self, memory, step, mark_id=None):
        pause = step.get("pause") if isinstance(step.get("pause"), dict) else {}
        after = self.m.get(integer(pause.get("next")))
        return E("li", {"class": "pause", "id": mark_id},
                 E("p", {"class": "pausehead"}, "We paused here on %s." % text_of(pause.get("day"))),
                 E("p", {"class": "pausefocus"}, text_of(pause.get("focus"))),
                 E("p", {"class": "quiet"}, J("The work in this thread picked up again on ",
                                              self.said(after, "inl", text_of(after.get("day"))), ".")
                   if after else "Nothing has been recorded in this thread since this pause.", "  ",
                   self.explain_button("kind-checkpoint", "What is a pause?", "ask")))

    def group_node(self, thread, index, nodes):
        groups = thread.get("groups", [])
        group = groups[index] if 0 <= index < len(groups) and isinstance(groups[index], dict) else {}
        task, opened = integer(group.get("task")), group.get("open") is True
        first, last = text_of(group.get("first")), text_of(group.get("last"))
        span = first if first == last else "%s to %s" % (first, last)
        state = "still open" if opened else ("closed on %s" % text_of(group.get("closed")) if group.get("closed")
                                             else "closed")
        return E("li", {"class": "group fold" + ("" if opened else " closed"),
                        "id": "task-%d" % task if group.get("anchor") is True and task else None},
                 E("button", {"type": "button", "class": "foldbtn", "data-fold": "g%d-%d" % (integer(thread.get("n")), index),
                              "aria-expanded": "true"},
                   E("span", {"class": "gt"}, "While working on: ", text_of(group.get("title")) or "a task"),
                   E("span", {"class": "gm"}, "%s · %s · %s" % (plural(integer(group.get("count")), "1 memory",
                                                                        "%d memories"), span, state))),
                 E("ol", {"class": "chain foldbody"}, *nodes))

    def chain(self, thread):
        n = integer(thread.get("n"))
        steps = [s for s in thread.get("steps", []) if isinstance(s, dict) and integer(s.get("id")) in self.m]
        items, nodes, current = [], [], None

        def flush():
            if nodes and current is not None:
                items.append(self.group_node(thread, current, list(nodes)))
            del nodes[:]

        for at, step in enumerate(steps):
            memory = self.m[integer(step.get("id"))]
            mark_id = "latest-%d" % n if at == len(steps) - 1 else None
            made = []
            conn = step.get("conn")
            if conn:
                label, to = [text_of(conn.get("phrase"))], conn.get("to") or {}
                if integer(to.get("id")) in self.m:
                    label.append(E("a", {"href": anchor("m-%d" % to["id"]), "data-m": to["id"]}, text_of(to.get("day"))))
                elif "thread" in to and integer(to["thread"]) in self.threads:  # the loose thread is number 0
                    label.append(self.thread_link(to["thread"]))
                made.append(E("li", {"class": "conn time" if conn.get("time") else "conn"}, *label))
            if step.get("note"):
                made.append(E("li", {"class": "note"}, CHAIN_NOTE))
            made.append(self.divider(memory, step, mark_id) if memory.get("kind") == "checkpoint"
                        else self.station(memory, mark_id))
            group = step.get("group")
            group = group if isinstance(group, int) and not isinstance(group, bool) else None
            if group != current:
                flush()
                current = group
            if group is None:
                items += made
            else:
                nodes += made
        flush()
        return E("ol", {"class": "chain"}, *items)

    def feeds_line(self, thread):
        links = []
        for feed in thread.get("feeds", []):
            if not isinstance(feed, dict):
                continue
            ordinal = feed.get("ordinal")
            links.append(E("a", {"href": anchor("text-%d-%d" % (integer(thread.get("n")), ordinal))}
                           if isinstance(ordinal, int) and not isinstance(ordinal, bool) else
                           {"href": anchor("sec-%d" % integer(feed.get("n")))}, text_of(feed.get("label"))))
        return links

    @staticmethod
    def join(nodes, last=" and "):
        out = []
        for i, node in enumerate(nodes):
            out += [", " if 0 < i < len(nodes) - 1 else last if i else "", node]
        return out

    def thread_article(self, thread):
        n = integer(thread.get("n"))
        feeds = self.feeds_line(thread)
        opening = thread.get("opening")
        stands = thread.get("stands") if isinstance(thread.get("stands"), dict) else {}
        cards = [E("li", None, E("span", {"class": "lab"}, label), self.card(self.m[integer(stands.get(key))], thread=False))
                 for key, label in (("decision", "The latest decision"), ("result", "The latest result"))
                 if integer(stands.get(key)) in self.m]
        points = [E("li", None, self.said_id(mid)) for mid in stands.get("open", []) if integer(mid) in self.m]
        tasks = [self.task_note(task, with_story=True) for task in stands.get("tasks", []) if isinstance(task, dict)]
        noun = text_of(self.outline.get("noun")) or "the deliverable"
        where = [E("a", {"href": anchor("text-%d-%d" % (n, f["ordinal"])) if isinstance(f.get("ordinal"), int)
                         and not isinstance(f.get("ordinal"), bool) else anchor("sec-%d" % integer(f.get("n")))},
                   text_of(f.get("label"))) for f in thread.get("feeds", []) if isinstance(f, dict)]
        return E("article", {"class": "thread", "id": "thread-%d" % n},
                 E("h3", None, text_of(thread.get("name")), " ",
                   self.explain_button(text_of(thread.get("status_explain")), text_of(thread.get("status")),
                                       "chip " + STATE_CLASS.get(thread.get("status_css"), "s-plain"))),
                 E("p", {"class": "feeds quiet"}, "Feeds ", *self.join(feeds)) if feeds else "",
                 E("div", {"class": "about"}, E("h4", {"class": "sh"}, "What this thread is about"),
                   E("p", {"class": "aboutsay"}, "This thread is about ", self.runs(self.first_runs(opening["about"])), ".")
                   if opening else E("p", {"class": "aboutsay"}, "Nobody has written what this thread is about yet."),
                   self.blocks(opening.get("why")) if opening else "", E("p", None, self.story_line(thread))),
                 E("div", {"class": "standing"}, E("h4", {"class": "sh"}, "Where it stands"),
                   E("ul", {"class": "latest"}, *cards) if cards else "",
                   E("p", {"class": "lab"}, "Still open here") if points else "",
                   E("ul", {"class": "plainlist"}, *points) if points else "", *tasks,
                   E("p", {"class": "quiet"}, "Its text is in %s under " % noun, *self.join(where), ".") if where else ""),
                 E("h4", {"class": "sh"}, "The story, oldest first"),
                 E("p", None, E("a", {"class": "jump", "href": anchor("latest-%d" % n)}, "Jump to the latest"))
                 if thread.get("steps") else "", self.chain(thread))

    def task_note(self, task, with_story=False):
        if task.get("goal"):
            words = J("We are working on this: ", self.runs(task["goal"]))
        elif task.get("shown") is True:
            words = "An open task has no goal written yet."
        else:
            words = "An open task whose text is not shown here."
        address = github_url("issue", self.repo, task.get("issue"))
        story = with_story and bool(task.get("memories")) and integer(task.get("thread")) in self.threads
        return E("div", {"class": "onote task k-checkpoint"},
                 E("p", {"class": "said"}, words),
                 E("a", {"class": "quiet", "href": anchor("task-%d" % integer(task.get("issue")))}, "See it in the story")
                 if story else "", " " if story else "",
                 E("a", {"class": "quiet", "href": address}, "Open the task on GitHub") if address else "")

    def outline_entry(self, thread):
        n = integer(thread.get("n"))
        also = [E("a", {"href": anchor("sec-%d" % integer(f.get("n")))}, text_of(f.get("label")))
                for f in thread.get("feeds", [])[1:] if isinstance(f, dict)]
        tasks = []
        for task in thread.get("tasks", []):
            if not isinstance(task, dict):
                continue
            title = text_of(task.get("title")) or "a task"
            state = "still open" if task.get("open") is True else "closed"
            tasks.append(E("li", None, E("a", {"href": anchor("task-%d" % integer(task.get("issue")))}, title)
                           if task.get("memories") else title, " ", E("span", {"class": "quiet"}, state)))
        return E("li", None, self.thread_link(n), " ",
                 self.explain_button(text_of(thread.get("status_explain")), text_of(thread.get("status")),
                                     "chip " + STATE_CLASS.get(thread.get("status_css"), "s-plain")),
                 E("span", {"class": "quiet"}, " also feeds ", *self.join(also)) if also else "",
                 E("ul", {"class": "outline"}, *tasks) if tasks else "")

    def threads_view(self):
        threads = self.model.get("threads", [])
        groups = [g for g in self.outline.get("deliverables", []) if isinstance(g, dict)] \
            if isinstance(self.outline.get("deliverables"), list) else []
        out = []
        for group in groups:
            if len(groups) > 1:
                out.append(E("h3", {"class": "deliv"}, text_of(group.get("title"))))
            for section in group.get("sections", []):
                firsts = [self.threads[integer(p.get("thread"))] for p in section.get("parts", [])
                          if isinstance(p, dict) and p.get("first") is True and integer(p.get("thread")) in self.threads]
                if firsts:
                    out.append(E("section", {"class": "outl"}, E("p", {"class": "lab"}, self.section_link(
                        section.get("n"), section.get("label"))), E("ul", {"class": "outline"}, *[
                            self.outline_entry(t) for t in firsts])))
        unplaced = [self.threads[n] for n in self.outline.get("unplaced", []) if integer(n) in self.threads]
        if unplaced:
            out.append(E("section", {"class": "outl"}, E("p", {"class": "lab"}, "Topics"),
                         E("ul", {"class": "outline"}, *[self.outline_entry(t) for t in unplaced])))
        aside = [t for t in threads if t.get("set_aside") is True]
        if aside:
            out.append(E("section", {"class": "outl"}, E("p", {"class": "lab"}, "Set aside"),
                         E("ul", {"class": "outline"}, *[self.outline_entry(t) for t in aside])))
        loose = [t for t in threads if t.get("loose") is True]
        if loose:
            out.append(E("section", {"class": "outl"}, E("ul", {"class": "outline"}, *[self.outline_entry(t)
                                                                                      for t in loose])))
        return self.view("threads", "How we got here", "Each thread is one line of thought, told from its first memory "
                         "to its latest. Choose a sentence to open that memory.",
                         E("div", {"class": "outlines", "aria-label": "The threads, by section"}, *out),
                         *[self.thread_article(t) for t in threads], self.untold_line())

    # ------------------------------------------------------------ lessons, what is settled, what is open

    def lessons_view(self):
        rows = []
        failures = [m for m in self.model.get("memories", []) if m.get("kind") == "failure"]
        for memory in reversed(failures):
            after = [self.m[integer(pair.get("id"))] for pair in memory.get("in", []) if integer(pair.get("id")) in self.m]
            told = {integer(other.get("id")) for other in after}
            if after:
                right = [self.card(other, thread=False) for other in after]
                for other in after:
                    for css, key, lead in (("replaced", "replaces", "That changed again later: "),
                                           ("aside", "instead", "That was set aside. Instead: ")):
                        newer = [pair for pair in other.get("in", []) if pair.get("kind") == key]
                        if other.get("css") == css and newer and integer(newer[0].get("id")) in self.m \
                                and integer(newer[0].get("id")) not in told:  # a decision is told once, not twice
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
                                                         self.thread_link(memory.get("thread"))),
                            self.said(memory),
                            E("div", {"class": "why"}, E("span", {"class": "lab"}, "Why it happened"),
                              self.blocks(cause.get("doc"))) if cause else ""),
                          E("div", {"class": "arrow", "aria-hidden": "true"}),
                          E("div", {"class": "did"}, E("span", {"class": "lab"}, "What we did about it"), *right)))
        return self.view("lessons", "What we learned the hard way", "Every failure in this mind, newest first, with "
                         "what it led to.", E("ol", {"class": "lessons"}, *rows) if rows else
                         E("p", {"class": "quiet"}, "Nothing has gone wrong in this record yet."), self.untold_line())

    REPLACED_WORD = {"decision": "decision", "result": "finding", "correction": "correction"}

    def standing(self, thread, heading=True):
        """What stands in one thread, with its links to the story and to the text. No text of the topic is here."""
        n = integer(thread.get("n"))
        rests = []
        for rest in thread.get("rests", []):
            memory = self.m.get(integer(rest.get("id")))
            if memory is None:
                continue
            older = [mid for mid in rest.get("replaced", []) if integer(mid) in self.m]
            word = self.REPLACED_WORD.get(memory.get("kind"), "memory")
            extra = E("details", {"class": "older"},
                      E("summary", None, ("It replaced an earlier %s. See it" % word) if len(older) == 1 else
                        "It replaced %d earlier %ss. See them" % (len(older), word)),
                      *[E("p", {"class": "struck"}, self.said_id(mid)) for mid in older]) if older else ""
            rests.append(E("li", {"class": "rest " + kind_class(memory)}, self.mark(memory),
                           E("div", None, self.said(memory), extra)))
        stands = thread.get("stands") if isinstance(thread.get("stands"), dict) else {}
        opened = len([mid for mid in stands.get("open", []) if integer(mid) in self.m])
        nothing = E("p", {"class": "quiet"}, "Nothing here stands yet.", " ",
                    E("a", {"href": anchor("thread-%d" % n)}, plural(opened, "1 point is open", "%d points are open"))
                    if opened else "")
        feeds = self.feeds_line(thread)
        return E("article", {"class": "settled", "id": "settled-%d" % n},
                 E("h4", {"class": "sh"}, self.thread_link(n)) if heading else "",
                 E("p", {"class": "quiet"}, "Also feeds ", *self.join(feeds[1:])) if len(feeds) > 1 else "",
                 E("ul", {"class": "restson"}, *rests) if rests else nothing,
                 E("p", {"class": "partlinks"}, E("a", {"href": anchor("thread-%d" % n)}, "How we got here"),
                   "  " if feeds else "", E("a", {"href": feeds_first(thread, feeds)}, "The text") if feeds else ""))

    def settled_view(self):
        out = []
        groups = [g for g in self.outline.get("deliverables", []) if isinstance(g, dict)] \
            if isinstance(self.outline.get("deliverables"), list) else []
        for group in groups:
            if len(groups) > 1:
                out.append(E("h3", {"class": "deliv"}, text_of(group.get("title"))))
            for section in group.get("sections", []):
                firsts = [self.threads[integer(p.get("thread"))] for p in section.get("parts", [])
                          if isinstance(p, dict) and p.get("first") is True and integer(p.get("thread")) in self.threads]
                if firsts:
                    out.append(E("section", {"class": "setsec"}, E("h3", None, self.section_link(section.get("n"),
                                                                                                   section.get("label"))),
                                 *[self.standing(t) for t in firsts]))
        unplaced = [self.threads[n] for n in self.outline.get("unplaced", []) if integer(n) in self.threads]
        if unplaced:
            out.append(E("section", {"class": "setsec"}, E("h3", None, "Other topics"),
                         *[self.standing(t) for t in unplaced]))
        loose = [t for t in self.model.get("threads", []) if t.get("loose") is True and t.get("rests")]
        if loose:
            out.append(E("section", {"class": "setsec"}, E("h3", None, "About the project as a whole"),
                         *[self.standing(t, heading=False) for t in loose]))
        left = []
        for thread in self.model.get("threads", []):
            if thread.get("set_aside") is True:
                left.append(E("li", None, self.thread_link(thread.get("n")), ". ",
                              self.said_id(thread["aside_by"]) if integer(thread.get("aside_by")) in self.m
                              else "Left out."))
        text_link = E("a", {"href": anchor("text")}, text_of(self.outline.get("title")) or "the text")
        return self.view("settled", "What is settled", J(
            "What we hold today: the decisions, findings and corrections that nothing recorded later replaces or sets "
            "aside. The text they hold up is under ", text_link, "."), *out,
                         E("h3", None, "Set aside"),
                         E("ul", {"class": "leftout"}, *left) if left else
                         E("p", {"class": "quiet"}, "No thread has been set aside."), self.untold_line())

    @staticmethod
    def age(days):
        if not isinstance(days, int) or isinstance(days, bool):
            return ""
        return "open since today" if days == 0 else "open for 1 day" if days == 1 else "open for %d days" % days

    def open_plan_groups(self, opened):
        """The three groups the plan adds to "What is still open?" (none for a project without a plan)."""
        plan = opened.get("plan") if isinstance(opened.get("plan"), dict) else None
        if plan is None:
            return []
        parts = {integer(p.get("id")): p for p in (self.model.get("plan") or {}).get("parts", []) if isinstance(p, dict)}

        def sentence(part_id):
            part = parts.get(integer(part_id))
            return self.plan_link({"gh": part.get("id"), "issue": part.get("issue")}, "%s: %s" % (
                text_of(part.get("part")).capitalize(), text_of(part.get("plain")))) if part else ""

        unbacked = []
        for entry in plan.get("unbacked", []):
            if not isinstance(entry, dict):
                continue
            ideas = [self.said_id(i) for i in entry.get("ideas", [])]
            unbacked.append(E("li", {"class": "onote"}, sentence(entry.get("part")),
                              J(" The experiment we would run: ", *ideas) if ideas else ""))
        doubt = []
        for entry in plan.get("doubt", []):
            if not isinstance(entry, dict):
                continue
            against = [self.said_id(i) for i in entry.get("against", [])]
            doubt.append(E("li", {"class": "onote"}, sentence(entry.get("part")),
                           J(" Against it: ", *against) if against else ""))
        unused = [E("li", {"class": "onote"}, self.said_id(i)) for i in plan.get("unused", [])]
        return [
            self.open_plan_group("plan-unbacked", "Planned claims nothing backs yet", "No finding that still stands is linked "
                                 "to them. Where we thought of an experiment, it is named.", unbacked,
                                 "Every planned claim has a finding behind it."),
            self.open_plan_group("plan-doubt", "Planned claims a finding speaks against", "A finding that still stands was "
                                 "recorded against them.", doubt, "No finding speaks against a planned claim."),
            self.open_plan_group("plan-unused", "Findings the plan does not use", "Recorded since the plan last changed, and "
                                 "linked to no part of it.", unused, "Every recent finding is used by the plan."),
        ]

    def open_view(self):
        opened = self.model.get("open") or {}

        def group(key, title, hint, rows, empty):
            return E("section", {"class": "ogroup", "id": "open-%s" % key}, E("h3", None, title),
                     E("p", {"class": "quiet"}, hint),
                     E("ul", {"class": "notes"}, *rows) if rows else E("p", {"class": "nothing"}, empty))

        def note(entry):
            memory = self.m.get(integer(entry.get("id")))
            if memory is None:
                return ""
            return E("li", {"class": "onote " + kind_class(memory)},
                     E("div", {"class": "meta"}, self.mark(memory), E("span", {"class": "in"},
                                                                      self.thread_link(memory.get("thread"))),
                       E("span", {"class": "age"}, self.age(entry.get("age")))), self.said(memory))

        unknown = []
        for entry in opened.get("unknown", []):
            memory = self.m.get(integer(entry.get("id")))
            if memory is None:
                continue
            after = self.m.get(integer(entry.get("followed")))
            unknown.append(E("li", {"class": "onote k-result"},
                             E("div", {"class": "meta"}, self.mark("result"),
                               E("span", {"class": "in"}, self.thread_link(memory.get("thread")))),
                             E("div", {"class": "said"}, E("span", {"class": "lab"}, "We have not established"),
                               self.blocks(entry.get("doc"))),
                             E("p", {"class": "quiet"}, "From ", self.said(memory, "inl", "what we found on %s"
                                                                           % text_of(memory.get("day"))), ". ",
                               J("A later memory follows from this result: ", self.said(after, "inl", text_of(
                                   after.get("day"))), ".") if after else "")))
        holds = opened.get("holds")
        return self.view(
            "open", "What is still open", "Thoughts nobody has closed. Each one stays here until a later memory answers "
            "it, tries it or sets it aside.",
            group("questions", "Questions without an answer", "Asked, and nothing recorded since answers them. Oldest "
                  "first.", [note(entry) for entry in opened.get("questions", [])], "No question is waiting for an answer."),
            group("ideas", "Ideas we have not tested", "Written down; nothing took them up and no result tested them. "
                  "Oldest first.", [note(entry) for entry in opened.get("ideas", [])],
                  "Every idea has been tested, taken up or set aside."),
            group("failures", "Went wrong, and nothing followed", "Failures that no decision or fix points back to. "
                  "Oldest first.", [note(entry) for entry in opened.get("failures", [])],
                  "Everything that went wrong has been followed up."),
            group("unknown", "What our results do not show", "Said by the results that still stand, in their own words. "
                  "What nothing followed comes first.", unknown, "No result that still stands leaves a gap open."),
            *self.open_plan_groups(opened),
            group("holds", "Waiting for a yes", "From the last pause.",
                  [E("li", {"class": "onote k-checkpoint"}, E("div", {"class": "said"}, self.blocks(holds)))]
                  if holds else [], "Nothing waits for a yes."),
            group("tasks", "Work in progress", "Open tasks, from their goal.",
                  [E("li", None, self.task_note(task, with_story=True)) for task in opened.get("tasks", [])],
                  "No task is open."), self.untold_line())


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
            in_text = [E("li", None, self.part_link(use)) for use in word.get("in_text", [])
                       if isinstance(use, dict) and isinstance(use.get("ordinal"), int)
                       and integer(use.get("thread")) in self.threads]
            used = {integer(m.get("id")) for m in uses}
            in_fields = [E("li", None, self.said(self.m[mid], cls="inl")) for mid in word.get("in_fields", [])
                         if integer(mid) in self.m and integer(mid) not in used]
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
                             E("div", {"class": "uses"}, E("p", {"class": "lab"}, "In the text:"),
                               E("ol", None, *in_text)) if in_text else "",
                             E("div", {"class": "uses"}, E("p", {"class": "lab"}, "In what other memories say:"),
                               E("ol", None, *in_fields)) if in_fields else "",
                             E("p", {"class": "quiet"}, "First explained on %s" % text_of(first.get("day")),
                               J(", in the thread ", self.thread_link(first["thread"]))
                               if "thread" in first and integer(first["thread"]) in self.threads else "", ".")))
        return E("div", {"id": "words", "hidden": "hidden"}, *entries)

    def explain_store(self):
        """The fixed explanations behind every kind, state and mark, and the box "How to read this page". No record
        text is in it."""
        entries = [E("div", {"class": "entry", "id": "x-%s" % key}, E("p", {"class": "wt"}, title),
                     E("p", {"class": "def"}, text)) for key, title, text in EXPLAIN]
        how = [E("p", {"class": "wt"}, "How to read this page"),
               E("p", {"class": "def"}, "Every sentence here comes from what the project recorded. Words that explain "
                                        "themselves are buttons: choose one to read what it means."),
               *[E("p", None, E("b", None, name), ": ", text) for name, text in HOW_TO_READ],
               E("p", {"class": "lab"}, "What each kind of memory records"),
               E("ul", {"class": "plainlist"}, *[E("li", None, E("b", None, word), ": ", text)
                                                   for _, word, text in KIND_TABLE]),
               E("p", {"class": "lab"}, "What each state means"),
               E("ul", {"class": "plainlist"}, *[E("li", None, E("b", None, title), ": ", text)
                                                   for _, _, title, text in STATE_TABLE]),
               E("p", {"class": "lab"}, "What a thread's mark means"),
               E("ul", {"class": "plainlist"}, *[E("li", None, E("b", None, title), ": ", text)
                                                   for _, title, text in THREAD_TABLE])]
        return E("div", {"id": "explain", "hidden": "hidden"}, *entries, E("div", {"class": "entry", "id": "x-how"}, *how))


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
                             E("p", None, E("a", {"href": address},
                                            "Open the whole issue on GitHub")) if address else ""))
        return E("div", {"id": "issues", "hidden": "hidden"}, *entries)

    def tree_topic(self, entry):
        n = integer(entry.get("thread"))
        thread = self.threads.get(n)
        if not thread:
            return ""
        branches = []
        for key, title in (("found", "We found"), ("decided", "We decided"), ("open", "Still open")):
            data = entry.get(key) if isinstance(entry.get(key), dict) else {}
            count = integer(data.get("count"))
            ids = [mid for mid in data.get("ids", []) if integer(mid) in self.m]
            if not count:
                continue
            items = [E("li", None, self.said(self.m[integer(mid)], "inl")) for mid in ids]
            if count > len(ids):
                items.append(E("li", None, E("a", {"href": anchor("thread-%d" % n)},
                                             "and %d more, in the story" % (count - len(ids)))))
            branches.append(E("li", {"class": "fold closed lf"},
                              E("button", {"type": "button", "class": "foldbtn", "data-fold": "b%d-%s" % (n, key),
                                           "aria-expanded": "true"}, "%s (%d)" % (title, count)),
                              E("ul", {"class": "foldbody"}, *items)))
        return E("li", {"class": "fold tp"},
                 E("button", {"type": "button", "class": "foldbtn", "data-fold": "t%d" % n, "aria-expanded": "true"},
                   text_of(thread.get("name"))), " ",
                 self.explain_button(text_of(thread.get("status_explain")), text_of(thread.get("status")),
                                     "chip " + STATE_CLASS.get(thread.get("status_css"), "s-plain")), " ",
                 E("a", {"class": "quiet", "href": anchor("thread-%d" % n)}, "the story"),
                 E("ul", {"class": "foldbody"}, *branches) if branches else "")

    def tree_plan(self, data, number):
        """The branch "We plan to say (4)" under a section of the map: the planned parts as sentences."""
        if not isinstance(data, dict) or not integer(data.get("count")):
            return ""
        parts = {integer(p.get("id")): p for p in (self.model.get("plan") or {}).get("parts", []) if isinstance(p, dict)}
        items = []
        for pid in data.get("ids", []):
            part = parts.get(integer(pid))
            if part:
                items.append(E("li", None, self.plan_link({"gh": part.get("id"), "issue": part.get("issue")}, "%s: %s" % (
                    text_of(part.get("part")).capitalize(), text_of(part.get("plain"))))))
        if integer(data.get("count")) > len(items):
            items.append(E("li", None, E("a", {"href": anchor("sec-%d" % integer(number))}, "and %d more, in the text" % (
                integer(data.get("count")) - len(items)))))
        return E("li", {"class": "fold closed lf"},
                 E("button", {"type": "button", "class": "foldbtn", "data-fold": "p%s" % (
                     integer(number) if number is not None else "x"), "aria-expanded": "true"},
                   "We plan to say (%d)" % integer(data.get("count"))), E("ul", {"class": "foldbody"}, *items))

    def tree_side(self, sections, cls):
        out = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            number = section.get("n")
            out.append(E("li", {"class": "fold sc"},
                         E("button", {"type": "button", "class": "foldbtn", "data-fold": "s%s" % (
                             integer(number) if number is not None else "x"), "aria-expanded": "true"},
                           text_of(section.get("label"))), " ",
                         E("a", {"class": "quiet", "href": anchor("sec-%d" % integer(number))}, "its text")
                         if number is not None else "",
                         E("ul", {"class": "foldbody"}, self.tree_plan(section.get("plan"), number),
                           *[self.tree_topic(t) for t in section.get("topics", []) if isinstance(t, dict)])))
        return E("ul", {"class": "tside " + cls}, *out)

    def tree_pane(self):
        tree = self.model.get("tree") if isinstance(self.model.get("tree"), dict) else {}
        question = tree.get("question")
        middle = [E("p", {"class": "lab"}, "What we are finding out" if question else "The project"),
                  E("div", {"class": "question"}, self.blocks(question)) if question else
                  E("p", {"class": "question"}, text_of(self.model.get("name")))]
        aside = [self.threads[integer(n)] for n in tree.get("aside", []) if integer(n) in self.threads]
        extra = []
        if aside:
            extra.append(E("li", {"class": "fold closed sc"},
                           E("button", {"type": "button", "class": "foldbtn", "data-fold": "aside", "aria-expanded": "true"},
                             "Set aside (%d)" % len(aside)),
                           E("ul", {"class": "foldbody"}, *[E("li", None, self.thread_link(t.get("n"))) for t in aside])))
        other = tree.get("other") if isinstance(tree.get("other"), dict) else None
        if other:
            extra.append(E("li", {"class": "fold sc"},
                           E("button", {"type": "button", "class": "foldbtn", "data-fold": "other", "aria-expanded": "true"},
                             mind_model.PHRASES["thread.loose"]),
                           E("ul", {"class": "foldbody"}, self.tree_topic(other))))
        return E("div", {"class": "tree"},
                 E("div", {"class": "treebar"}, E("button", {"type": "button", "data-fold": "open-all"}, "Open everything"),
                   E("button", {"type": "button", "data-fold": "close-all"}, "Close everything")),
                 E("div", {"class": "treecols", "aria-label": "The map of what we know"},
                   self.tree_side(tree.get("left", []), "tleft"),
                   E("div", {"class": "hubnode"}, *middle, E("ul", {"class": "tside tmid"}, *extra) if extra else ""),
                   self.tree_side(tree.get("right", []), "tright")))

    def time_pane(self):
        layout = self.model.get("map")
        if not layout:
            return E("p", {"class": "quiet"}, "There is nothing to draw yet.")
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
            if pause.get("label") is True:
                parts.append(E("text", {"class": "pauselab", "x": "%.1f" % x, "y": top - 40, "text-anchor": "middle"},
                               "pause"))
        for row in layout.get("rows", []):
            y = coordinate(row.get("y"))
            css = {"aside": "r-aside", "open": "r-open", "stands": "r-stands"}.get(row.get("status_css"), "r-stands")
            parts.append(E("path", {"class": "river " + css, "d": "M%.1f %.1f H%.1f" % (
                coordinate(row.get("x1")), y, coordinate(row.get("x2")))}))
            n = row.get("n")
            if integer(n) in self.threads and n is not None:
                thread = self.threads[integer(n)]
                parts.append(E("a", {"href": anchor("thread-%d" % integer(n))}, E("title", None, text_of(row.get("name"))),
                               E("text", {"class": "lane", "x": 12, "y": "%.1f" % (y - 2)},
                                 text_of(row.get("name_short")) or text_of(row.get("name"))),
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
                                                                          "question", "correction")],
                   E("li", None, E("span", {"class": "ln pauseln"}), "a pause: the work stopped and a note was left"),
                   E("li", None, E("span", {"class": "ln l-because"}), "so"),
                   E("li", None, E("span", {"class": "ln l-replaces"}), "replaced"),
                   E("li", None, E("span", {"class": "ln l-instead"}), "set aside; instead"),
                   E("li", None, E("span", {"class": "ln l-checks"}), "tried, checked or answered"),
                   E("li", None, E("span", {"class": "fade"}), "faded: replaced or set aside"))
        return J(E("p", {"class": "lede"}, "Each line is a thread, running left to right through time. A branch that drops "
                   "away is something we set aside. Point at a mark, or choose it, to read it."),
                 E("p", {"class": "quiet hintnarrow"}, "Scroll sideways to follow time."),
                         E("div", {"class": "mapbox"},
                           E("svg", {"viewBox": "0 0 %d %d" % (width, height), "role": "img", "class": "mindmap",
                                     "aria-label": "Map of the threads of this mind over time"}, *parts)),
                         E("p", {"class": "reading", "id": "reading", "aria-live": "polite"},
                           "Point at a mark on the map to read that memory here."), legend)

    def map_view(self):
        return self.view(
            "map", "The whole mind", "Two pictures of the same record: what we know, as a tree, and the threads over time.",
            E("div", {"class": "switch", "aria-label": "Which map to show"},
              E("button", {"type": "button", "class": "on", "data-fold": "pane-know"}, "What we know"),
              E("button", {"type": "button", "data-fold": "pane-time"}, "Over time")),
            E("div", {"class": "mappane on", "id": "map-know"}, self.tree_pane()),
            E("div", {"class": "mappane", "id": "map-time"}, self.time_pane()), self.untold_line())

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
                         E("div", {"id": "answers", "aria-live": "polite"}), self.untold_line())

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
            lines.append(E("ul", {"class": "plainlist"}, *[E("li", None, E("a", {"href": address},
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
                           E("a", {"href": address}, "Open it on GitHub") if address
                           else ""))
        if integer(missing.get("kinds")):
            lines.append(E("p", None, plural(integer(missing["kinds"]), "1 comment is of a kind this version of the "
                                                                          "plugin does not know, so it is not told here.",
                                             "%d comments are of a kind this version of the plugin does not know, so they "
                                             "are not told here.")))
        if not lines:
            lines.append(E("p", None, "Every memory of the record by your allowed accounts is told here."))
        return E("section", {"class": "untold", "id": "untold"}, E("h3", None, "What this page leaves out"), *lines)

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
        first_thread = threads[0] if threads else None
        counts = self.model.get("counts") or {}
        first, latest = text_of(self.model.get("first")), text_of(self.model.get("latest"))
        steps = [
            ("now", J("This is the memory of ", text_of(self.model.get("name")), ": ",
                      plural(integer(counts.get("memories")), "1 memory", "%d memories"), " in ",
                      plural(integer(counts.get("threads")), "1 thread", "%d threads"),
                      ", from %s to %s. " % (first, latest) if first else ". ",
                      "Nothing here is a list of tickets. It is what was thought, tried, decided and learned, in "
                      "sentences.")),
            ("text", "This is the text itself, part by part, in the order of what the project delivers. Under each part "
                     "are the day it last changed and what is still open on it. Choose “Why it says this” to see what "
                     "holds it up."),
            ("thread-%d" % integer(first_thread.get("n")) if first_thread else "threads",
             "A thread is one line of thought. It opens with where it stands now. Below that is its story, oldest first. "
             "The words between two memories say how one led to the next: so, we tried it, later that changed."),
            ("settled", "This is what the project holds today, and the memories that hold it up. When an answer "
                        "replaced an earlier one, the earlier one is folded underneath."),
            ("lessons", "Every failure sits next to what it led to. A failure with nothing beside it is one nobody has "
                        "acted on."),
            ("open", "These thoughts are still open: questions without an answer, ideas nobody tested, and what the "
                     "results do not show."),
            ("map", "The map shows what we know, as a tree, and every thread over time. To find something by a word, ask "
                    "the mind. Choose any sentence, anywhere, to open that memory. A word with a dotted line is one of "
                    "the project's own words: point at it to see what it means, or choose it for the whole story. Every "
                    "word that explains itself is a button. “How to read this page” lists them all."),
        ]
        return E("div", {"id": "tour", "class": "tour", "role": "dialog", "aria-label": "First visit", "hidden": "hidden"},
                 *[E("p", {"class": "step", "data-go": "#" + go, "hidden": "hidden"}, words) for go, words in steps],
                 E("div", {"class": "tourbar"}, E("span", {"id": "tourn", "class": "quiet"}),
                   E("button", {"type": "button", "id": "tourback"}, "Back"),
                   E("button", {"type": "button", "id": "tournext", "class": "primary"}, "Next"),
                   E("button", {"type": "button", "id": "tourend"}, "Leave the tour")))

    def page(self, style, script, icon):
        digest = lambda text: "'sha256-%s'" % base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode()
        policy = POLICY % (digest(style), digest(script))
        built = self.model.get("built") or {}
        head = E("head", None, E("meta", {"charset": "utf-8"}),
                 E("meta", {"http-equiv": "Content-Security-Policy", "content": policy}),
                 E("meta", {"name": "referrer", "content": "no-referrer"}),
                 E("meta", {"name": "viewport", "content": VIEWPORT}),
                 E("title", None, "The mind of ", text_of(self.model.get("name"))), Safe("<style>%s</style>" % style))
        logo = E("svg", {"viewBox": "144 144 968 968", "class": "logo", "aria-hidden": "true"},
                 E("path", {"class": "lg-a", "d": icon[0]}), E("path", {"class": "lg-b", "d": icon[1]}))
        labels = dict(NAV)
        labels["text"] = text_of(self.outline.get("nav")) or "What do we deliver?"
        side = E("nav", {"class": "side fold", "aria-label": "Ways into the mind"}, logo,
                 E("p", {"class": "mindname"}, text_of(self.model.get("name"))),
                 E("p", {"class": "mindsub"}, "the memory of one project"),
                 E("button", {"type": "button", "class": "foldbtn waysbtn", "data-fold": "ways",
                              "aria-expanded": "true"}, "Ways in"),
                 E("ul", {"class": "foldbody"}, *[E("li", None, E("a", {"href": anchor(key), "data-nav": key}, labels[key]))
                                                  for key, _ in NAV]),
                 E("div", {"class": "sidefoot"},
                   self.explain_button("how", "How to read this page", "howbtn"),
                   E("button", {"type": "button", "id": "tourstart"}, "First visit? Take the tour"),
                   E("button", {"type": "button", "id": "theme"}, "Switch light and dark"),
                   E("p", {"class": "quiet"}, "Read from the record on %s. This page never changes the record."
                     % text_of(built.get("words")))))
        main = E("main", {"id": "main"}, self.now_view(), self.text_view(), self.threads_view(), self.settled_view(),
                 self.lessons_view(), self.open_view(), self.map_view(), self.ask_view())
        panel = E("aside", {"id": "panel", "class": "panel", "aria-label": "One memory"},
                  E("button", {"type": "button", "id": "close", "class": "close"}, "Close"),
                  E("h2", {"class": "noscript-h"}, "Every memory"),
                  *[self.detail(memory) for memory in self.model.get("memories", []) if integer(memory.get("id"))])
        body = E("body", None, side, main, panel, self.tour(), self.word_store(), self.explain_store(), self.issue_store(),
                 self.word_box(), Safe("<script>%s</script>" % script))
        return "<!doctype html>\n%s\n<html lang=\"en\">%s%s</html>\n" % (NOTICE, head, body)


ICON_LINE = re.compile(r"[MCZ0-9 ,-]{1,8000}")


def fixed_files():
    """The style, the script and the icon, read from ../page; refused when the style or the script could end its
    element or open a comment, or when the icon is not exactly two lines of path data made of M, C, Z, digits,
    spaces, commas and minus signs."""
    with open(os.path.join(PAGE, "mind.css"), encoding="utf-8") as handle:
        style = handle.read()
    with open(os.path.join(PAGE, "mind.js"), encoding="utf-8") as handle:
        script = handle.read()
    for text in (style, script):
        if "</" in text or "<!--" in text:
            raise ValueError("the fixed style or script holds </ or <!--")
    with open(os.path.join(PAGE, "icon.txt"), encoding="utf-8") as handle:
        icon = handle.read().split("\n")
    if len(icon) != 3 or icon[2] != "" or not all(ICON_LINE.fullmatch(line) for line in icon[:2]):
        raise ValueError("the fixed icon is not two lines of path data")
    return style, script, icon[:2]


def render(model, pictures=None):
    """The whole page for a model, as a string. Pure: the same model and pictures give the same text. `pictures` maps
    (repository, ref, path) to the bytes of a figure that mind_figures loaded; the page checks them again."""
    style, script, icon = fixed_files()
    return Page(model, pictures).page(style, script, icon)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", help="owner/name (default: from .recortex.json)")
    parser.add_argument("--sync", action="store_true", help="run index.py sync first (reads GitHub)")
    parser.add_argument("--figures", action="store_true", help="fetch the pictures of figures not yet kept (--sync does "
                                                              "too); otherwise only the ones kept are shown")
    parser.add_argument("--versions", action="store_true", help="bring the versions of the text that the record vouches for "
                                                                "into the cache first (--sync does too)")
    parser.add_argument("--open", action="store_true", help="ask the browser to open the page")
    args = parser.parse_args(argv)
    config = recortex.load_config()
    repo = recortex.resolve_repo(args.repo, config)
    print(recortex.describe(config, repo))
    if args.sync:
        hub = None  # a project file with no hub syncs without the resume block, as index.py sync does
        if config.get("repo", "").lower() == repo.lower() and "hub" in config:
            hub = recortex.resolve_hub(None, config)  # a hub that is no issue number stops with its fixed line
        index.sync(repo, hub)
    allowed = recortex.allowed_users(repo)
    if not allowed:
        print(UNTRUSTED)
        return 1
    if args.sync or args.versions:
        try:
            import versions  # noqa: E402 - the page reads what this brings (references/versions.md); a failure never stops it
            versions.fetch(config, repo)
        except Exception as error:  # noqa: BLE001
            print("versions: the versions of the text could not be fetched (%s); the page shows what is kept." % type(error).__name__)
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
        model = mind_model.build(db, repo, allowed, datetime.date.today(),
                                 recortex.shipped_profiles(config.get("profiles")), config)
    finally:
        db.close()
    wanted = mind_figures.wanted(model)
    pictures, reasons = mind_figures.load(wanted, repo, None, fetch=bool(args.sync or args.figures))
    text = render(model, pictures)
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
    for line in mind_figures.lines(reasons):
        print(line)
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
