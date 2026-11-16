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

def how(p):
    b = []
    # column 1: the session
    b += [T(40, 44, "YOUR SESSION", 12, "quiet", 600, p=p), box(40, 60, 230, 208, p)]
    for i, (mark, line) in enumerate((("found", "finds a result"), ("decided", "makes a decision"), ("wrong", "hits a failure"), ("idea", "has an idea"))):
        y = 100 + i * 42
        b += ['<circle cx="68" cy="%d" r="6" fill="%s"/>' % (y - 5, p["brand"]), T(86, y, line, 16, serif=True, p=p)]
    # arrow 1
    b += [arrow(282, 164, 352, 164, p), T(317, 150, "memories", 12, "deep", 600, "middle", p=p)]
    # the optional observer: a quiet dashed path under arrow 1
    b += ['<line x1="282" y1="236" x2="352" y2="236" stroke="%s" stroke-width="2" stroke-dasharray="5 5" marker-end="url(#q)"/>' % p["quiet"],
          T(317, 224, "observer", 11.5, "quiet", 600, "middle", p=p), T(317, 256, "if enabled", 10.5, "quiet", anchor="middle", p=p)]
    # column 2: the record
    b += [T(366, 44, "GITHUB ISSUES: THE RECORD", 12, "quiet", 600, p=p), box(366, 60, 250, 208, p)]
    b += [box(384, 76, 214, 44, p, fill="wash", stroke="brand", r=9), T(398, 103, "Hub", 15, weight=600, p=p), T(584, 103, "pinned", 12, "quiet", anchor="end", p=p)]
    for i, name in enumerate(("Topic: the text so far", "Topic: the text so far")):
        y = 134 + i * 62
        b += [box(384, y, 214, 50, p, fill="bg", r=9), T(398, y + 21, name, 13.5, weight=600, p=p)]
        b += ['<circle cx="%d" cy="%d" r="4.5" fill="%s"/>' % (403 + k * 15, y + 36, p["brand"]) for k in range(5 - i * 2)]
        b += [T(403 + (5 - i * 2) * 15 + 4, y + 40, "dated memories", 11.5, "quiet", p=p)]
    # arrow 2
    b += [arrow(628, 164, 698, 164, p), T(663, 150, "read only", 12, "deep", 600, "middle", p=p)]
    # column 3: what comes back
    b += [T(712, 44, "WHAT COMES BACK", 12, "quiet", 600, p=p)]
    for i, (head, sub) in enumerate((("A brief", "at the start of every session"), ("The mind page", "the memory, told in sentences"),
                                     ("The finished text", "compiled from the topics"), ("A board", "kept current by the record"))):
        y = 60 + i * 54
        b += [box(712, y, 208, 46, p, r=9), T(726, y + 20, head, 14.5, weight=600, p=p), T(726, y + 37, sub, 12, "quiet", p=p)]
    # the loop back
    b += ['<path d="M816,284 C816,322 640,326 480,326 C320,326 155,322 155,280" fill="none" stroke="%s" stroke-width="2.5" stroke-dasharray="6 6" marker-end="url(#a)"/>' % p["brand"],
          T(480, 316, "so the next session starts where this one stopped", 13.5, "deep", anchor="middle", serif=True, italic=True, p=p)]
    return frame(960, 348, p, b, "How recortex works: a session writes memories to GitHub issues, and the record gives back a brief, the mind page, the finished text and a board; an optional observer, off unless enabled, can also queue and post reviewed memories")

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
