#!/usr/bin/env python3
"""Builds the recortex documentation site into _site/ and runs check.py on it.

Pages are HTML fragments in src/pages/. This script puts each one inside the shared shell (header, navigation,
footer), copies the static files, and writes the search index, the sitemap and .nojekyll. Python 3 standard library
only, and it runs on Python 3.8.

usage: python3 build.py [--out _site] [--plugin DIR | --plugin-git REPO] [--no-check]
"""
import argparse
import html
import json
import os
import re
import shutil
import sys
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def slug(text):
    text = re.sub(r"<[^>]+>", "", html.unescape(text)).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "section"


def add_ids(body):
    """Give every h2 and h3 an id, so that a section can be linked and found by search."""
    used = set(re.findall(r'\bid="([^"]+)"', body))

    def fix(match):
        level, attrs, inner = match.group(1), match.group(2), match.group(3)
        if re.search(r"\bid=", attrs):
            return match.group(0)
        base = slug(inner)
        name, n = base, 2
        while name in used:
            name = "%s-%d" % (base, n)
            n += 1
        used.add(name)
        return '<h%s id="%s"%s>%s</h%s>' % (level, name, attrs, inner, level)

    return re.sub(r"<h([23])([^>]*)>(.*?)</h\1>", fix, body, flags=re.S)


def command_block(lines, prompt):
    rows = "\n".join('<span class="prompt">%s</span><span class="line">%s</span>' % (prompt, html.escape(line))
                     for line in lines)
    return '<div class="cmd"><button type="button" class="copy">Copy</button><pre>%s</pre></div>' % rows


def fill_blocks(body):
    body = re.sub(r"\{\{cmd\n(.*?)\n\}\}", lambda m: command_block(m.group(1).split("\n"), "$ "), body, flags=re.S)
    body = re.sub(r"\{\{say\n(.*?)\n\}\}", lambda m: command_block(m.group(1).split("\n"), "&gt; "), body, flags=re.S)
    return body


class Text(HTMLParser):
    """Plain text of a fragment, cut into sections at each h2."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections = [{"id": "", "heading": "", "text": []}]
        self.skip = 0
        self.in_h2 = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag == "h2":
            attrs = dict(attrs)
            self.sections.append({"id": attrs.get("id", ""), "heading": "", "text": []})
            self.in_h2 = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1
        if tag == "h2":
            self.in_h2 = False
        if tag in ("p", "li", "div", "h1", "h2", "h3", "tr", "pre"):
            self.sections[-1]["text"].append(" ")

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_h2:
            self.sections[-1]["heading"] += data
        self.sections[-1]["text"].append(data)


def plain(fragment):
    parser = Text()
    parser.feed(fragment)
    return parser.sections


def words(fragment):
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'.:/_-]*", " ".join(" ".join(s["text"]) for s in plain(fragment))))


def build(out, plugin_args, run_checks):
    config = json.loads(read(os.path.join(HERE, "site.json")))
    pages = json.loads(read(os.path.join(SRC, "pages.json")))
    shell = read(os.path.join(SRC, "shell.html"))
    logo = read(os.path.join(SRC, "logo.svg.txt")).strip()
    tokens = {"version": config["version"], "ref": config["plugin_ref"], "repo": config["plugin_repo"]}

    if os.path.isdir(out):
        for name in os.listdir(out):
            path = os.path.join(out, name)
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    os.makedirs(out, exist_ok=True)

    listed = [p for p in pages if p.get("listed", True)]
    top = [p for p in listed if p.get("top")]
    index, counts = [], {}

    for page in pages:
        body = read(os.path.join(SRC, "pages", page["file"]))
        for key, value in tokens.items():
            body = body.replace("{{%s}}" % key, value)
        body = fill_blocks(add_ids(body))
        left = re.findall(r"\{\{[^}]*\}\}", body)
        if left:
            sys.exit("%s: not filled: %s" % (page["file"], left[:3]))
        counts[page["id"]] = words(body)

        if page.get("listed", True):
            for section in plain(body):
                text = re.sub(r"\s+", " ", "".join(section["text"])).strip()
                heading = re.sub(r"\s+", " ", section["heading"]).strip()
                if section["id"] or not heading:
                    index.append({"p": page["nav"], "h": heading, "u": page["path"] + ("#" + section["id"] if section["id"] else ""),
                                  "x": text[:320] if heading else text[:200], "d": page["description"] if not heading else ""})

        def link(p, current=page):
            on = ' class="on" aria-current="page"' if p["id"] == current["id"] else ""
            return '<a href="%s"%s>%s</a>' % (p["path"], on, html.escape(p["nav"]))

        pager = ""
        if page.get("listed", True) and page["id"] != "home":
            at = listed.index(page)
            prev_page, next_page = listed[at - 1], listed[at + 1] if at + 1 < len(listed) else None
            cells = ['<a class="door" href="%s"><span class="dd">Back</span><span class="dt">%s</span></a>'
                     % (prev_page["path"], html.escape(prev_page["nav"]))]
            if next_page:
                cells.append('<a class="door nx" href="%s"><span class="dd">Next</span><span class="dt">%s</span></a>'
                             % (next_page["path"], html.escape(next_page["nav"])))
            pager = '<div class="pager">%s</div>' % "".join(cells)

        title = page["title"] if page["id"] in ("home", "404") else "%s · %s" % (page["title"], config["name"])
        canonical = config["url"] + page["path"] if page["id"] != "404" else ""
        values = {
            "title": html.escape(title),
            "description": html.escape(page["description"], quote=True),
            "canonical": '<link rel="canonical" href="%s">' % canonical if canonical else '<meta name="robots" content="noindex">',
            "og_url": canonical or config["url"] + "/",
            "image": config["url"] + "/assets/social-preview.png",
            "logo": logo,
            "toplinks": "".join(link(p) for p in top),
            "pagelinks": "".join("<li>%s</li>" % link(p) for p in listed),
            "footlinks": "".join("<li>%s</li>" % link(p) for p in listed),
            "body": body,
            "pager": pager,
            "bodyclass": "home" if page["id"] == "home" else "inner",
            "mainclass": "home" if page["id"] == "home" else "doc",
            "version": config["version"],
            "repo": config["plugin_repo"],
            "copyright": html.escape(config["copyright"]),
        }
        text = shell
        for key, value in values.items():
            text = text.replace("{{%s}}" % key, value)
        left = re.findall(r"\{\{[^}]*\}\}", text)
        if left:
            sys.exit("shell: not filled: %s" % left[:3])
        target = "404.html" if page["path"].endswith(".html") else os.path.join(page["path"].strip("/"), "index.html")
        write(os.path.join(out, target), text)

    # static files, the search index, the sitemap, and the marker that keeps Jekyll away from the files
    static = os.path.join(SRC, "static")
    for folder, _dirs, files in os.walk(static):
        for name in files:
            rel = os.path.relpath(os.path.join(folder, name), static)
            os.makedirs(os.path.dirname(os.path.join(out, rel)), exist_ok=True)
            shutil.copyfile(os.path.join(folder, name), os.path.join(out, rel))
    write(os.path.join(out, "search-index.js"), "window.SITE_INDEX=" + json.dumps(index, separators=(",", ":"), ensure_ascii=False) + ";\n")
    write(os.path.join(out, ".nojekyll"), "")
    write(os.path.join(out, "robots.txt"), "User-agent: *\nAllow: /\nSitemap: %s/sitemap.xml\n" % config["url"])
    urls = "".join("<url><loc>%s%s</loc></url>\n" % (config["url"], p["path"]) for p in listed)
    write(os.path.join(out, "sitemap.xml"),
          '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n%s</urlset>\n' % urls)

    print("built %d pages into %s" % (len(pages), out))
    for page in pages:
        print("  %-24s %5d words" % (page["path"], counts[page["id"]]))

    if run_checks:
        sys.path.insert(0, HERE)
        import check
        return check.run(out, config, pages, counts, plugin_args)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default=os.path.join(HERE, "_site"))
    parser.add_argument("--plugin", help="a checkout of the plugin to check the pages against")
    parser.add_argument("--plugin-git", help="a local clone of the plugin; the ref of site.json is taken from it")
    parser.add_argument("--no-check", action="store_true")
    args = parser.parse_args()
    sys.exit(build(os.path.abspath(args.out), {"plugin": args.plugin, "plugin_git": args.plugin_git}, not args.no_check))


if __name__ == "__main__":
    main()
