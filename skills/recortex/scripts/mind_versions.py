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

The manuscript page (1.4.0) compares the blocks of the text, not its lines: words(block) gives the plain words of a block,
phrases(runs) joins the word runs of a reworded paragraph into phrases, and since(old, new, old_entries, new_entries) gives
one row per block of the new text, and one per block that only the old text had, each marked same, added, removed,
reworded or changed. History and the versions of the paper keep their word-by-word look through diff_text and word_runs.
"""

import collections
import difflib
import json
import re

CUT_CHARS = 20000
MAX_PARTS = 30           # differences drawn per part
MAX_BYTES = 3 * 1024 * 1024   # of differences per page
MAX_SINCE_BYTES = 2 * 1024 * 1024   # of the marked variants of the manuscript view
MAX_PAIRS = 6000         # pairs of blocks looked at in one since(); when they are spent the rest are plain additions and removals
MAX_CELLS = 4000000      # words of one block times words of the other, summed over the pairs that since() matches by words
MAX_WORDS = 1500         # a paragraph of more words than this is paired with another by its words' overlap, never matched by words
MAX_TOKENS = 1500        # a pair of paragraphs with more words than this in either is one deletion and one addition
SAME_WORDS, SAME_CHARS = 2, 16   # a run of unchanged words this short between two changes joins them
WHOLE_SHARE = 0.6        # more of the longer side changed than this: the paragraph is one deletion and one addition
REWORD_RATIO = 0.5       # paragraphs whose words match at least this much are one paragraph reworded
OPS = ("same", "add", "del", "change", "cut")
RUNS = ("same", "add", "del")
TOKEN = re.compile(r"\s+|\S+\s*")   # a word with the whitespace after it; whitespace alone only at the start
JUNK_SHARE = 5           # autojunk works only where one item is more than a fifth of what is matched


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


def matcher(first, second):
    """difflib.SequenceMatcher over two lists of strings, with autojunk only where one item is more than a fifth of the second
    list (and the list is long enough for difflib to apply it at all): that is text made to be slow ("a a a ..."). On prose
    autojunk would drop the common words and the spaces as anchors, and a reworded paragraph of 100 words or more would
    become a whole deletion and a whole addition."""
    top = max(collections.Counter(second).values()) if second else 0
    return difflib.SequenceMatcher(None, first, second, autojunk=len(second) > 200 and top * JUNK_SHARE > len(second))


def word_runs(old, new):
    """The runs of a changed paragraph, matched by words (each word with the whitespace after it; the whitespace that two changed
    stretches share goes back to the unchanged text after them, so that a stretch ends at its last word)."""
    a, b = TOKEN.findall(old), TOKEN.findall(new)
    if len(a) > MAX_TOKENS or len(b) > MAX_TOKENS:   # too long to match by words: the whole paragraph is replaced
        return merge_runs([("del", old), ("add", new)])
    runs, carry = [], ""
    for tag, i1, i2, j1, j2 in matcher(a, b).get_opcodes():
        if tag == "equal":
            runs.append(("same", carry + "".join(a[i1:i2])))
            carry = ""
            continue
        gone, came = "".join(a[i1:i2]), "".join(b[j1:j2])
        space = gone[len(gone.rstrip()):]
        if gone.rstrip() and came.rstrip() and space and came[len(came.rstrip()):] == space and i2 < len(a) and j2 < len(b):
            gone, came, carry = gone[:len(gone) - len(space)], came[:len(came) - len(space)], space
        if gone:
            runs.append(("del", gone))
        if came:
            runs.append(("add", came))
    return merge_runs(runs)


def diff_text(before, after, collapse=True):
    """The operations that turn the paragraphs of `before` into those of `after`. With collapse the unchanged paragraphs are
    a count ({"op": "same", "n"}); without it each is {"op": "same", "n": 1, "text": ...}, for the tests that rebuild both
    sides. A difference past CUT_CHARS of changed text ends with {"op": "cut"}."""
    a, b = paragraphs(before), paragraphs(after)
    ops, spent, same = [], 0, 0
    differ = matcher(a, b)

    def flush():
        nonlocal same
        if same and collapse:
            ops.append({"op": "same", "n": same})
        same = 0

    for tag, i1, i2, j1, j2 in differ.get_opcodes():
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


# ---------------------------------------------------------------- the manuscript view: blocks, phrases and rows

def block_kind(block):
    """The one main key of a block of mind_text ("p", "h", "ul", "ol", "quote", "code", "math", "table", "block", "note"),
    or None for anything else."""
    if not isinstance(block, dict):
        return None
    for key in ("p", "h", "ul", "ol", "quote", "code", "math", "table", "block", "note"):
        if key in block:
            return key
    return None


def run_text(runs):
    """The text of inline runs as it reads: the text of a `t` run, the words of a `code` or `cite` run, the source of a
    formula and the words of a link (never its address)."""
    out = []
    for run in runs if isinstance(runs, list) else []:
        if not isinstance(run, dict):
            continue
        if isinstance(run.get("t"), str):
            out.append(run["t"])
        elif isinstance(run.get("code"), str):
            out.append(run["code"])
        elif isinstance(run.get("w"), str):
            out.append(run["w"])
        elif "cite" in run:
            out.append(run["text"] if isinstance(run.get("text"), str) else "")
        elif isinstance(run.get("math"), dict):
            out.append(run["math"]["s"] if isinstance(run["math"].get("s"), str) else "")
        elif "link" in run:
            out.append(run_text(run["link"]))
    return "".join(out)


def block_text(block):
    """The plain text of a block, its pieces joined by one space: runs as run_text gives them; for a list its items, for a
    table its cells, for code and formulas their source. A figure or table pointer and a note have none."""
    kind = block_kind(block)
    if kind in ("p", "quote"):
        return run_text(block[kind])
    if kind == "h":
        return run_text(block["h"].get("r") if isinstance(block["h"], dict) else None)
    if kind in ("ul", "ol"):
        out = []
        for item in block[kind] if isinstance(block[kind], list) else []:
            if isinstance(item, dict):
                out.append(run_text(item.get("r")))
                if isinstance(item.get("sub"), dict):
                    out.append(block_text(item["sub"]))
        return " ".join(part for part in out if part)
    if kind == "code":
        return block["code"] if isinstance(block["code"], str) else ""
    if kind == "math":
        return block["math"].get("s", "") if isinstance(block["math"], dict) and isinstance(block["math"].get("s"), str) else ""
    if kind == "table":
        table = block["table"] if isinstance(block["table"], dict) else {}
        cells = [cell for key in ("head", "rows") for row in (table.get(key) or []) if isinstance(row, list) for cell in row]
        return " ".join(run_text(cell.get("r")) for cell in cells if isinstance(cell, dict))
    return ""


def words(block):
    """The plain words of a block: the words of its text runs, its code, its formulas' source and its links' words; for a
    table its cells. A figure or table pointer has none."""
    return block_text(block).split()


def structure(value):
    """A value as a string that is equal for two equal values, whatever their keys' order."""
    try:
        return json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        return repr(value)


def entry_key(entry):
    """What makes a figure or table entry the same entry: its title, caption, data and command, its place for a figure, and
    for a table its whole structure (rows, cells, spans, alignment and the formatting inside a cell), so that a cell boundary
    that moved is a change although the words are the same."""
    if not isinstance(entry, dict):
        return None
    table = entry.get("table")
    return json.dumps([entry.get("kind"), entry.get("title"), entry.get("caption"), entry.get("data"), entry.get("command"),
                       entry.get("repo"), entry.get("ref"), entry.get("path"),
                       structure(table) if isinstance(table, dict) else None])


def block_key(block, entries=None):
    """A string that is equal for two blocks that say the same. A figure or table pointer's key includes its entry, so that a
    changed caption is a change."""
    if block_kind(block) == "block" and isinstance(block["block"], dict):
        entry = (entries or {}).get((block["block"].get("kind"), block["block"].get("id")))
        return json.dumps(["block", block["block"].get("kind"), block["block"].get("id"), entry_key(entry)])
    return structure(block)


def phrases(runs):
    """The runs of a reworded paragraph (word_runs) joined into phrases: a run of unchanged words that is at most
    SAME_WORDS words and SAME_CHARS characters long between two changes is absorbed into them, each changed stretch is then
    one deletion followed by one addition, and a paragraph of which more than WHOLE_SHARE of the longer side changed is one
    deletion and one addition. Joining the "same" and "del" runs still gives the old text, and joining "same" and "add" the
    new."""
    runs = [(run["t"], run["text"]) for run in runs if run.get("t") in RUNS and isinstance(run.get("text"), str) and run["text"]]
    segs = []   # ["same", text] or ["chg", old, new]
    for kind, text in runs:
        if kind == "same":
            segs.append(["same", text])
        elif segs and segs[-1][0] == "chg":
            segs[-1][1 if kind == "del" else 2] += text
        else:
            segs.append(["chg", text if kind == "del" else "", text if kind == "add" else ""])
    joined = []
    for seg in segs:
        joined.append(seg)
        if len(joined) >= 3 and joined[-1][0] == "chg" and joined[-2][0] == "same" and joined[-3][0] == "chg" and \
                len(joined[-2][1].split()) <= SAME_WORDS and len(joined[-2][1]) <= SAME_CHARS:
            last, mid, first = joined.pop(), joined.pop(), joined.pop()
            joined.append(["chg", first[1] + mid[1] + last[1], first[2] + mid[1] + last[2]])
    old = "".join(seg[1] for seg in joined)
    new = "".join(seg[2] if seg[0] == "chg" else seg[1] for seg in joined)
    changed_old = sum(len(seg[1]) for seg in joined if seg[0] == "chg")
    changed_new = sum(len(seg[2]) for seg in joined if seg[0] == "chg")
    longer, share = (len(old), changed_old) if len(old) >= len(new) else (len(new), changed_new)
    if longer and share / longer > WHOLE_SHARE:
        return merge_runs([("del", old), ("add", new)])
    out = []
    for seg in joined:
        if seg[0] == "same":
            out.append(("same", seg[1]))
        else:
            out += [("del", seg[1]), ("add", seg[2])]
    return merge_runs(out)


def inline_shape(runs):
    """(signature, math) of the inline runs of a paragraph: the signature lists, in order, every run that is more than plain
    words (code with its text, a link with its target, a citation with its target, the emphasis, superscript or subscript a
    text run carries with its text, a formula with its source), and `math` says that the paragraph holds a formula, a
    superscript or a subscript anywhere (the marks of a reworded paragraph are drawn as plain words, which would flatten
    them)."""
    signature, math = [], False
    for run in runs if isinstance(runs, list) else []:
        if not isinstance(run, dict):
            continue
        if "math" in run:
            math = True
            signature.append(("math", structure(run["math"])))
        elif "code" in run:
            signature.append(("code", run["code"]))
        elif "link" in run:
            inner, inside = inline_shape(run["link"])
            math = math or inside
            signature.append(("link", structure(run.get("to")), inner))
        elif "cite" in run:
            signature.append(("cite", run.get("cite"), run.get("n")))
        elif "t" in run or "w" in run:
            flags = tuple(flag for flag in ("sup", "sub", "i", "b") if run.get(flag) is True)
            if "sup" in flags or "sub" in flags:
                math = True   # drawn as words, a superscript or a subscript reads as another number: shown as it was
            if flags:
                signature.append(("styled", flags, run.get("t") if isinstance(run.get("t"), str) else run.get("w")))
    return signature, math


def reword_or_change(old, new):
    """"reworded" for two paragraphs that differ in plain words only, else "changed": a formula on either side (the marks of a
    reworded paragraph are drawn as words, which would show the formula's source), a superscript or a subscript on either side,
    or a run that is more than plain words (code, a link's target, a citation, emphasis) that the two sides do not share, in
    its kind or in its text."""
    first, math_first = inline_shape(old.get("p"))
    second, math_second = inline_shape(new.get("p"))
    if math_first or math_second or first != second:
        return "changed"
    return "reworded"


def pairs(old, new, budget, first=None, second=None):
    """"reworded" or "changed" when an old block and a new block are one block that changed, else None. Two paragraphs are
    one when the ratio of their words is REWORD_RATIO or more (reworded, unless a formula or another run that is more than
    plain words differs: changed); two blocks of one other kind are, and for a figure or table pointer only when they name the
    same figure or table. `budget` is a list [pairs left, cells left] that every call spends; `first` and `second` are the
    words of the two blocks when the caller has them."""
    budget[0] -= 1
    kind = block_kind(old)
    if kind != block_kind(new) or kind is None:
        return None
    if kind == "p":
        first = words(old) if first is None else first
        second = words(new) if second is None else second
        if not first or not second:
            return None
        both = matcher(first, second)
        if both.real_quick_ratio() < REWORD_RATIO or both.quick_ratio() < REWORD_RATIO:
            return None
        if len(first) > MAX_WORDS or len(second) > MAX_WORDS:   # too long to match by words: the overlap of the words decides
            return "changed"
        budget[1] -= len(first) * len(second)
        return reword_or_change(old, new) if both.ratio() >= REWORD_RATIO else None
    if kind == "block":
        left, right = old["block"], new["block"]
        same = isinstance(left, dict) and isinstance(right, dict) and left.get("kind") == right.get("kind") \
            and left.get("id") == right.get("id")
        return "changed" if same else None
    return "changed"


def row(kind, now=None, then=None, runs=None, i=None, j=None, cells=None):
    return {"k": kind, "now": now, "then": then, "runs": runs, "i": i, "j": j, "cells": cells}


def table_of(block, entries):
    """The table a block is or points at (a pipe table, or the entry of a named table), or None."""
    kind = block_kind(block)
    if kind == "table" and isinstance(block["table"], dict):
        return block["table"]
    if kind == "block" and isinstance(block["block"], dict):
        entry = (entries or {}).get((block["block"].get("kind"), block["block"].get("id")))
        if isinstance(entry, dict) and isinstance(entry.get("table"), dict):
            return entry["table"]
    return None


def shape(table):
    """The rows of a table as a list of cell sizes, [[(rowspan, colspan), ...], ...] for the head and the body, or None."""
    out = []
    for key in ("head", "rows"):
        lines = table.get(key) or []
        if not isinstance(lines, list):
            return None
        out.append([[(cell.get("rs"), cell.get("cs")) if isinstance(cell, dict) else None for cell in line]
                    if isinstance(line, list) else None for line in lines])
    return out


MAX_CELL_MARKS = 60      # the changed cells of one table that the page marks one by one


def cell_changes(old, new, old_entries=None, new_entries=None):
    """The cells that differ between an old table and a new one of the same shape, [{"k": "head" or "rows", "r": row, "c": column,
    "was": the cell's old words}] in reading order (at most MAX_CELL_MARKS), or None when either block is no table, the shapes
    differ (the page then shows the whole block as it was) or no cell differs."""
    first, second = table_of(old, old_entries), table_of(new, new_entries)
    if first is None or second is None or shape(first) != shape(second):
        return None
    out = []
    for key in ("head", "rows"):
        for r, (line_a, line_b) in enumerate(zip(first.get(key) or [], second.get(key) or [])):
            for c, (cell_a, cell_b) in enumerate(zip(line_a, line_b)):
                if structure(cell_a) != structure(cell_b) and len(out) < MAX_CELL_MARKS:
                    out.append({"k": key, "r": r, "c": c, "was": run_text(cell_a.get("r")) if isinstance(cell_a, dict) else ""})
    return out or None


def since(old_blocks, new_blocks, old_entries=None, new_entries=None):
    """The rows that turn the blocks of an old text into those of a new one: [{"k", "now", "then", "runs", "i", "j"}], one
    for each block of the new text in its order (k "same", "added", "reworded" or "changed"; i is its place among the new
    blocks, j its place among the old) and one for each block only the old text had (k "removed", now and i None), between
    the new blocks it stood between. Blocks are matched as wholes; inside a stretch that differs, the two sides are walked in
    order: paragraphs that are mostly the same words are one paragraph reworded (its runs are phrases(word_runs)), other
    blocks of one kind are one block changed, and the rest are removed or added. The search for a partner is bounded: every
    pair looked at spends MAX_PAIRS and every matching by words spends MAX_CELLS, and once either is spent the blocks still
    unpaired are removed and added, in one pass."""
    old_blocks = [b for b in old_blocks if block_kind(b)]
    new_blocks = [b for b in new_blocks if block_kind(b)]
    first = [block_key(b, old_entries) for b in old_blocks]
    second = [block_key(b, new_entries) for b in new_blocks]
    old_words, new_words = {}, {}
    out, budget = [], [MAX_PAIRS, MAX_CELLS]

    def words_of(blocks, cache, n):
        if n not in cache:
            cache[n] = words(blocks[n]) if block_kind(blocks[n]) == "p" else None
        return cache[n]

    for tag, i1, i2, j1, j2 in matcher(first, second).get_opcodes():
        if tag == "equal":
            out += [row("same", new_blocks[j], None, None, j, i1 + j - j1) for j in range(j1, j2)]
            continue
        j = j1
        for i in range(i1, i2):
            found = None
            for later in range(j, j2):
                if budget[0] <= 0 or budget[1] <= 0:
                    break
                if first[i] == second[later]:   # the same block again (one that autojunk or a long part left unmatched): no change
                    budget[0] -= 1
                    found = (later, "same")
                    break
                how = pairs(old_blocks[i], new_blocks[later], budget, words_of(old_blocks, old_words, i),
                            words_of(new_blocks, new_words, later))
                if how:
                    found = (later, how)
                    break
            if found is None:
                out.append(row("removed", None, old_blocks[i], None, None, i))
                continue
            later, how = found
            out += [row("added", new_blocks[n], None, None, n, None) for n in range(j, later)]
            if how == "same":
                out.append(row("same", new_blocks[later], None, None, later, i))
                j = later + 1
                continue
            runs = None
            if how == "reworded":
                runs = phrases(word_runs(block_text(old_blocks[i]), block_text(new_blocks[later])))
            cells = cell_changes(old_blocks[i], new_blocks[later], old_entries, new_entries) if how == "changed" else None
            out.append(row(how, new_blocks[later], old_blocks[i], runs, later, i, cells))
            j = later + 1
        out += [row("added", new_blocks[n], None, None, n, None) for n in range(j, j2)]
    return out


def entry_weight(entry):
    """What an earlier figure or table entry costs on the page: its words, and for a table every cell's words and a little
    markup for each cell."""
    if not isinstance(entry, dict):
        return 0
    total = 160 + sum(len(entry[key]) for key in ("title", "caption", "data", "command", "repo", "ref", "path")
                      if isinstance(entry.get(key), str))
    table = entry.get("table")
    if isinstance(table, dict):
        total += len(run_text(table.get("caption")))
        for key in ("head", "rows"):
            for line in table.get(key) or []:
                for cell in line if isinstance(line, list) else []:
                    total += 56 + (len(run_text(cell.get("r"))) if isinstance(cell, dict) else 0)
    return total


def since_size(rows, entries=None):
    """The characters a list of rows costs on the page: the runs of reworded paragraphs and the blocks of removed and changed
    rows (what the page draws of them, as the model encodes them, and for a figure or table pointer the entry it names, from
    `entries`: {(kind, id): entry} of the earlier text), and a little for each row."""
    total = 0
    for item in rows:
        total += 24 + sum(len(run["text"]) for run in item.get("runs") or [])
        then = item.get("then")
        if then is not None:
            total += 40 + len(block_text(then)) + len(structure(then))
            if block_kind(then) == "block" and isinstance(then["block"], dict):
                total += entry_weight((entries or {}).get((then["block"].get("kind"), then["block"].get("id"))))
    return total


def counts(rows):
    """{"reworded", "added", "removed", "changed"} of a list of rows."""
    return {kind: sum(1 for item in rows if item["k"] == kind) for kind in ("reworded", "added", "removed", "changed")}
