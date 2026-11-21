#!/usr/bin/env python3
"""Draws the two README diagrams, light and dark, in the brand palette. Invented content only."""
import html, os
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets")
PAL = {
    "light": dict(bg="#FCF8F1", sheet="#FFFFFF", ink="#302927", quiet="#6E625D", rule="#E7DDCE", brand="#D76744", deep="#B04A2A", wash="#FAEAE1"),
    "dark": dict(bg="#231D1B", sheet="#302927", ink="#F6EFE3", quiet="#B9ABA1", rule="#4B403B", brand="#D76744", deep="#F0997A", wash="#4D2F24"),
}
FONT = 'font-family="system-ui,-apple-system,Segoe UI,Roboto,Noto Sans,Liberation Sans,sans-serif"'
SERIF = 'font-family="Charter,Bitstream Charter,Cambria,Noto Serif,Georgia,serif"'

def T(x, y, s, size=14, fill="ink", weight=400, anchor="start", serif=False, italic=False, p=None):
    return '<text x="%s" y="%s" font-size="%s" font-weight="%s" text-anchor="%s" fill="%s" %s%s>%s</text>' % (
        x, y, size, weight, anchor, p[fill], SERIF if serif else FONT, ' font-style="italic"' if italic else "", html.escape(s))

def box(x, y, w, h, p, fill="sheet", stroke="rule", r=12, sw=1.5):
    return '<rect x="%s" y="%s" width="%s" height="%s" rx="%s" fill="%s" stroke="%s" stroke-width="%s"/>' % (x, y, w, h, r, p[fill], p[stroke], sw)

def arrow(x1, y1, x2, y2, p, color="brand"):
    return '<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="2.5" marker-end="url(#a)"/>' % (x1, y1, x2, y2, p[color])

def frame(w, h, p, body, label):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" aria-label="%s">'
            '<defs><marker id="a" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            '<path d="M0,0 L10,5 L0,10 Z" fill="%s"/></marker>'
            '<marker id="q" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            '<path d="M0,0 L10,5 L0,10 Z" fill="%s"/></marker></defs>'
            '<rect width="%d" height="%d" rx="18" fill="%s"/>%s</svg>\n') % (w, h, w, h, html.escape(label), p["brand"], p["quiet"],
                                                                         w, h, p["bg"], "".join(body))

def head(x, word, rest, p):
    """A column heading: the stage word in the deep brand color, what it is in the quiet one."""
    return '<text x="%s" y="44" font-size="12" font-weight="600" text-anchor="start" %s><tspan fill="%s">%s</tspan><tspan fill="%s">%s</tspan></text>' % (
        x, FONT, p["deep"], word, p["quiet"], html.escape(rest))

def tick(x, y, done, p):
    """A small checkbox, ticked or empty."""
    out = ['<rect x="%s" y="%s" width="11" height="11" rx="2.5" fill="none" stroke="%s" stroke-width="1.5"/>' % (x, y, p["brand"] if done else p["quiet"])]
    if done:
        out.append('<path d="M%s,%s L%s,%s L%s,%s" fill="none" stroke="%s" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>' % (
            x + 2.6, y + 5.8, x + 4.9, y + 8.2, x + 8.6, y + 3, p["brand"]))
    return out

LABEL = ("An optional observer follows each session at the level you choose, keeps notes and a to-do list with checkmarks, "
         "and asks you before anything it should not do alone. GitHub issues are the record. The cortex works from it: "
         "a brief, the project dashboard, the finished text and a board.")

def how(p):
    b = []
    top, h = 60, 254
    see, rec, rcl, act = (32, 244), (316, 158), (514, 194), (748, 180)   # x and width of the four columns, 40 apart
    mid = top + h // 2
    # See: the observer, optional, drawn with a dashed edge and a tab on it
    x, w = see
    b += [head(x, "SEE", ": THE OBSERVER", p),
          '<rect x="%s" y="%s" width="%s" height="%s" rx="12" fill="%s" stroke="%s" stroke-width="1.5" stroke-dasharray="6 5"/>' % (x, top, w, h, p["sheet"], p["quiet"]),
          '<rect x="%s" y="%s" width="132" height="1" fill="%s"/><rect x="%s" y="%s" width="132" height="1" fill="%s"/>' % (x + 13, top - 1, p["bg"], x + 13, top, p["sheet"]), T(x + 20, top + 4, "optional, if enabled", 11.5, "quiet", p=p)]
    b += [T(x + 16, top + 36, "Follows each session", 13.5, weight=600, p=p), T(x + 16, top + 52, "at the level you choose", 11.5, "quiet", p=p)]
    px = x + 16
    for name in ("Notes only", "Assistant", "Everything"):
        pw = round(len(name) * 5.6 + 14)
        b += [box(px, top + 60, pw, 22, p, fill="wash", stroke="brand", r=11, sw=1), T(px + pw / 2, top + 75, name, 11, "ink", anchor="middle", p=p)]
        px += pw + 5
    b += [T(x + 16, top + 108, "Keeps notes", 13.5, weight=600, p=p), T(x + 16, top + 124, "memories of what was said", 11.5, "quiet", p=p)]
    b += [T(x + 16, top + 150, "Keeps a to-do list", 13.5, weight=600, p=p)] + tick(x + 16, top + 157, True, p) + tick(x + 31, top + 157, True, p) + tick(x + 46, top + 157, False, p)
    b += [T(x + 66, top + 167, "with checkmarks", 11.5, "quiet", p=p)]
    b += [T(x + 16, top + 193, "Waits for your yes", 13.5, weight=600, p=p), T(x + 16, top + 209, "on a held memory, a draft of the", 11.5, "quiet", p=p),
          T(x + 16, top + 223, "paper's text and, at Assistant, a", 11.5, "quiet", p=p), T(x + 16, top + 237, "task to open or close", 11.5, "quiet", p=p)]
    # arrow 1
    b += [arrow(see[0] + see[1] + 4, mid, rec[0] - 4, mid, p)]
    # Record: GitHub issues
    x, w = rec
    b += [head(x, "RECORD", ": GITHUB ISSUES", p), box(x, top, w, h, p)]
    b += [box(x + 16, top + 16, w - 32, 48, p, fill="wash", stroke="brand", r=9), T(x + 28, top + 45, "Hub", 15, weight=600, p=p), T(x + w - 28, top + 45, "pinned", 12, "quiet", anchor="end", p=p)]
    y = top + 76
    b += [box(x + 16, y, w - 32, 72, p, fill="bg", r=9), T(x + 28, y + 25, "Topic", 13.5, weight=600, p=p), T(x + 28, y + 42, "the text so far", 11.5, "quiet", p=p)]
    b += ['<circle cx="%d" cy="%d" r="4.5" fill="%s"/>' % (x + 33 + k * 15, y + 57, p["brand"]) for k in range(3)] + [T(x + 82, y + 61, "memories", 11.5, "quiet", p=p)]
    y = top + 160
    b += [box(x + 16, y, w - 32, 72, p, fill="bg", r=9), T(x + 28, y + 25, "Task", 13.5, weight=600, p=p), T(x + 28, y + 42, "steps, checked off", 11.5, "quiet", p=p)]
    b += tick(x + 28, y + 52, True, p) + tick(x + 43, y + 52, True, p) + tick(x + 58, y + 52, False, p)
    # arrow 2
    b += [arrow(rec[0] + rec[1] + 4, mid, rcl[0] - 4, mid, p)]
    # Recall: the cortex works from the record
    x, w = rcl
    b += [head(x, "RECALL", ": THE CORTEX", p)]
    items = (("A brief", "at the start of every session"), ("Project dashboard", "work, history and deliverables"),
             ("The finished text", "compiled from the topics"), ("A board", "kept current by the record"))
    for i, (name, sub) in enumerate(items):
        y = top + i * 50
        b += [box(x, y, w, 43, p, r=9), T(x + 14, y + 19, name, 14.5, weight=600, p=p), T(x + 14, y + 36, sub, 12, "quiet", p=p)]
    y = top + 199
    b += [box(x, y, w, 62, p, r=9), T(x + 14, y + 19, "Project commands", 14.5, weight=600, p=p), T(x + 14, y + 32, "/recortex:record, :ask, :todo,", 11, "quiet", p=p),
          T(x + 14, y + 45, ":status, :usage, :dashboard, :map,", 11, "quiet", p=p),
          T(x + 14, y + 57, ":guide, :impact", 11, "quiet", p=p)]
    # arrow 3
    b += [arrow(rcl[0] + rcl[1] + 4, mid, act[0] - 4, mid, p)]
    # Act: the session
    x, w = act
    b += [head(x, "ACT", ": YOUR SESSION", p), box(x, top, w, h, p)]
    for i, line in enumerate(("finds a result", "makes a decision", "hits a failure", "has an idea")):
        y = top + 46 + i * 38
        b += ['<circle cx="%d" cy="%d" r="6" fill="%s"/>' % (x + 24, y - 5, p["brand"]), T(x + 40, y, line, 15, serif=True, p=p)]
    b += ['<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="1"/>' % (x + 16, top + 186, x + w - 16, top + 186, p["rule"]),
          T(x + 16, top + 208, "Without the observer,", 11.5, "quiet", p=p), T(x + 16, top + 223, "the session records its", 11.5, "quiet", p=p),
          T(x + 16, top + 238, "own memories.", 11.5, "quiet", p=p)]
    # the loop back
    bottom = top + h
    b += ['<path d="M%d,%d C%d,%d 640,%d 480,%d C320,%d %d,%d %d,%d" fill="none" stroke="%s" stroke-width="2.5" stroke-dasharray="6 6" marker-end="url(#a)"/>' % (
              x + w // 2, bottom + 16, x + w // 2, bottom + 54, bottom + 60, bottom + 60, bottom + 60, see[0] + see[1] // 2, bottom + 54, see[0] + see[1] // 2, bottom + 12, p["brand"]),
          T(480, bottom + 50, "so the next session starts where this one stopped", 13.5, "deep", anchor="middle", serif=True, italic=True, p=p)]
    return frame(960, bottom + 82, p, b, LABEL)

def board(p):
    b = [T(32, 42, "sample-project record", 17, weight=600, p=p), T(928, 42, "Private", 12, "quiet", anchor="end", p=p),
         T(32, 66, "Board", 13, "deep", 600, p=p), T(86, 66, "Waiting on you", 13, "quiet", p=p), T(196, 66, "By thread", 13, "quiet", p=p),
         '<line x1="32" y1="72" x2="68" y2="72" stroke="%s" stroke-width="2.5"/>' % p["brand"]]
    cols = (("Todo", [("Task: three trial boxes", "Thread: Dates", None)]),
            ("In Progress", [("Task: pages with two dates", "Thread: Dates", "a checkpoint, March 11")]),
            ("Waiting for a yes", [("Task: tag release 1.4.0", "Thread: Dates", "the last pause")]),
            ("Done", [("Add the question step", "Pull request, merged", None), ("Task: ask, never guess", "Thread: Dates", "a result, March 9")]))
    for i, (name, cards) in enumerate(cols):
        x = 32 + i * 226
        b += [box(x, 88, 216, 186, p, fill="sheet", r=12, sw=1), T(x + 14, 114, name, 13.5, weight=600, p=p), T(x + 202, 114, str(len(cards)), 12, "quiet", anchor="end", p=p)]
        y = 126
        for title, thread, why in cards:
            h = 74 if why else 56
            b += [box(x + 10, y, 196, h, p, fill="bg", r=8, sw=1), T(x + 22, y + 22, title if len(title) < 27 else title[:25] + "…", 12.5, weight=600, p=p),
                  T(x + 22, y + 41, thread, 11.5, "quiet", p=p)]
            if why:
                b += [T(x + 22, y + 59, "Why: " + why, 11.5, "deep", p=p)]
            y += h + 8
    b += [T(32, 302, "Each card's column follows from the record, and the record stays the authority.", 13, "quiet", p=p)]
    return frame(960, 324, p, b, "A private GitHub Projects board whose cards and columns follow the record")

os.makedirs(OUT, exist_ok=True)
for mode, p in PAL.items():
    for name, draw in (("how-it-works", how), ("board", board)):
        with open(os.path.join(OUT, "%s-%s.svg" % (name, mode)), "w") as handle:
            handle.write(draw(p))
print("ok")
