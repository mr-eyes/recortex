#!/usr/bin/env python3
"""Follows every link, stylesheet, script, icon and image of the served site over HTTP, from the home page and the
404 page, and fails on anything that does not answer 200, on an anchor that is not on its page, and on a link that
leaves the base path. Links to other hosts are listed and not fetched.

usage: python3 tools/crawl.py http://127.0.0.1:8080/recortex/        (serve the folder above recortex/ first)
"""
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found, self.ids = [], set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "link" and a.get("rel") == "canonical":
            return                      # the full address of the page itself, not something the page loads
        for key in ("href", "src", "action"):
            if a.get(key):
                self.found.append((tag, a[key]))


def fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as reply:
            return reply.status, reply.headers.get("Content-Type", ""), reply.read()
    except urllib.error.HTTPError as error:
        return error.code, "", b""


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080/recortex/"
    parts = urllib.parse.urlsplit(start)
    origin, base = "%s://%s" % (parts.scheme, parts.netloc), parts.path
    queue, seen, ids, failures, external = [start, origin + base + "404.html"], set(), {}, [], set()
    checked = 0
    while queue:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        status, kind, body = fetch(url)
        checked += 1
        if status != 200:
            failures.append("%s answered %d" % (url, status))
            continue
        if "html" not in kind:
            continue
        page = Links()
        page.feed(body.decode("utf-8", "replace"))
        ids[url] = page.ids
        for tag, value in page.found:
            target = urllib.parse.urljoin(url, value)
            t = urllib.parse.urlsplit(target)
            if t.scheme not in ("http", "https"):
                failures.append("%s: unexpected address %s" % (url, value))
            elif "%s://%s" % (t.scheme, t.netloc) != origin:
                external.add(target)
                if tag != "a":
                    failures.append("%s: <%s> loads from %s" % (url, tag, target))
            elif not t.path.startswith(base):
                failures.append("%s: link leaves the base %s: %s" % (url, base, value))
            else:
                queue.append(urllib.parse.urlunsplit((t.scheme, t.netloc, t.path, t.query, "")))
                if t.fragment:
                    queue.append(urllib.parse.urlunsplit((t.scheme, t.netloc, t.path, t.query, t.fragment)))
    # anchors: a link with a fragment must name an id of the page it points at
    for url in sorted(seen):
        if "#" in url:
            page_url, fragment = url.split("#", 1)
            if page_url in ids and fragment not in ids[page_url]:
                failures.append("%s: no such anchor" % url)
    pages = [u for u in seen if "#" not in u]
    print("crawl: %d addresses fetched, %d with an anchor checked, %d other-host links not fetched"
          % (len(pages), len([u for u in seen if "#" in u]), len(external)))
    for message in failures:
        print("FAIL " + message)
    print("crawl: %s" % ("failed (%d)" % len(failures) if failures else "every link resolves"))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
