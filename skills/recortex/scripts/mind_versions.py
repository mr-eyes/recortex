"""The differences between two versions of a section's text, for the History of a part on the mind page. Pure.

Python 3 standard library only: no network, no file, no database. The text of a section is cut into paragraphs, the
paragraphs of the two versions are matched with difflib.SequenceMatcher, and a changed pair is matched again by words. The
output is data, a list of operations, which the page draws (Page.diff makes the only `ins` and `del` elements of the page):

  {"op": "same", "n": count}                          unchanged paragraphs, collapsed to a count
  {"op": "add", "runs": [{"t": "add", "text": ...}]}  a paragraph the new version has and the old one does not
  {"op": "del", "runs": [{"t": "del", "text": ...}]}  a paragraph the old version had and the new one does not
  {"op": "change", "runs": [{"t": "same" | "add" | "del", "text": ...}, ...]}   a paragraph that changed, by words
  {"op": "cut"}                                       a difference longer than CUT_CHARS stops here

The difference is of the text as written (its Markdown), shown as text; formulas are not drawn inside it. Joining the
"same" and "del" runs of a change gives the old paragraph and joining its "same" and "add" runs gives the new one, so
that both sides can always be rebuilt from the runs (diff_text with collapse=False keeps every unchanged paragraph).
"""

import difflib
import re

CUT_CHARS = 20000
MAX_PARTS = 30           # differences drawn per part
MAX_BYTES = 3 * 1024 * 1024   # of differences per page
OPS = ("same", "add", "del", "change", "cut")
RUNS = ("same", "add", "del")
TOKEN = re.compile(r"\s+|\S+")


def paragraphs(text):
    """The paragraphs of a text: runs of lines between blank lines, trailing spaces removed, no empty one."""
    out, current = [], []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if line.strip():
            current.append(line.rstrip())
        elif current:
            out.append("\n".join(current))
            current = []
    if current:
        out.append("\n".join(current))
    return out


def merge_runs(runs):
    """Neighboring runs of one type joined."""
    out = []
    for kind, text in runs:
        if not text:
            continue
        if out and out[-1][0] == kind:
            out[-1] = (kind, out[-1][1] + text)
        else:
            out.append((kind, text))
    return [{"t": kind, "text": text} for kind, text in out]


def word_runs(old, new):
    """The runs of a changed paragraph, matched by words (whitespace kept with the words around it)."""
    a, b = TOKEN.findall(old), TOKEN.findall(new)
    runs = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            runs.append(("same", "".join(a[i1:i2])))
        else:
            if i2 > i1:
                runs.append(("del", "".join(a[i1:i2])))
            if j2 > j1:
                runs.append(("add", "".join(b[j1:j2])))
    return merge_runs(runs)


def diff_text(before, after, collapse=True):
    """The operations that turn the paragraphs of `before` into those of `after`. With collapse the unchanged paragraphs are
    a count ({"op": "same", "n"}); without it each is {"op": "same", "n": 1, "text": ...}, for the tests that rebuild both
    sides. A difference past CUT_CHARS of changed text ends with {"op": "cut"}."""
    a, b = paragraphs(before), paragraphs(after)
    ops, spent, same = [], 0, 0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)

    def flush():
        nonlocal same
        if same and collapse:
            ops.append({"op": "same", "n": same})
        same = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            if collapse:
                same += i2 - i1
            else:
                ops += [{"op": "same", "n": 1, "text": text} for text in a[i1:i2]]
            continue
        flush()
        old, new = a[i1:i2], b[j1:j2]
        for position in range(max(len(old), len(new))):
            if spent > CUT_CHARS:
                ops.append({"op": "cut"})
                return ops
            if position < len(old) and position < len(new):
                ops.append({"op": "change", "runs": word_runs(old[position], new[position])})
                spent += len(old[position]) + len(new[position])
            elif position < len(old):
                ops.append({"op": "del", "runs": [{"t": "del", "text": old[position]}]})
                spent += len(old[position])
            else:
                ops.append({"op": "add", "runs": [{"t": "add", "text": new[position]}]})
                spent += len(new[position])
    flush()
    return ops


def rebuild(ops, side):
    """The paragraphs of the old ("before") or the new ("after") side from operations made with collapse=False."""
    keep = ("same", "del") if side == "before" else ("same", "add")
    out = []
    for op in ops:
        if op["op"] == "same":
            out.append(op["text"])
        elif op["op"] == "cut":
            continue
        elif (side == "before" and op["op"] == "add") or (side == "after" and op["op"] == "del"):
            continue
        else:
            out.append("".join(run["text"] for run in op["runs"] if run["t"] in keep))
    return out


def changed(ops):
    """True when the operations hold any change."""
    return any(op["op"] != "same" for op in ops)


def size(ops):
    """The characters the operations hold, for the budget of a page."""
    return sum(len(run["text"]) for op in ops for run in op.get("runs", [])) + 16 * len(ops)
