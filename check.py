#!/usr/bin/env python3
"""Checks the built site (_site/) and fails when it says something the plugin does not back.

The build runs it. It fails on:
  - a command, script, subcommand, flag, file or environment name that the plugin does not have at the ref pinned in
    site.json (the plugin is read from a local checkout, a local clone, or a shallow fetch from GitHub);
  - a banned word, an emoji, or a British spelling of a short list of words;
  - a project or account name that is not on the list of invented names;
  - a broken internal link or anchor, a page with no title, and a page over its word limit;
  - anything a page loads from another server, an inline script or style, or a missing strict policy.

Python 3 standard library only, and it runs on Python 3.8.

usage: python3 check.py [--site _site] [--plugin DIR | --plugin-git REPO]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))

# the only names the site may show: invented projects and accounts, and the plugin's own repository
ALLOWED_REPOS = {"octo-org/billing-service", "octo-org/octo-repo", "octo-org/sample-project", "recortex/recortex"}
ALLOWED_OWNERS = {"octo-org", "recortex", "octocat", "hubot"}
ALLOWED_AT = {"agent-recortex"}
ALLOWED_LINK_HOSTS = {"github.com", "cli.github.com", "code.claude.com"}
PLUGIN_DIRS = {"docs", "skills", "agents", "commands", "hooks", "assets", "templates", "references", "scripts",
               "profiles", "examples", "tests", "fields", "page"}
BANNED = ("comprehensive", "robust", "crucial")
BRITISH = ("colour", "behaviour", "organis", "recognis", "favour", "centre", "catalogue", "analyse", "grey",
           "neighbour", "honour", "labour", "licence", "programme", "whilst", "customis", "optimis", "summaris")
PLACEHOLDERS = {"HOLD_ID", "EVENTKEY", "ACT", "ID", "N"}
CLAUDE_LINE = re.compile(r"^claude plugin (marketplace (add|remove|list|update) |install |uninstall |list|update )", re.I)
GH_LINE = re.compile(r"^gh auth (login|status|refresh)")
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")


class Page(HTMLParser):
    """One built page: its title, text, code spans, links and ids."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.h1, self.lang, self.canonical = "", [], "", ""
        self.meta, self.links, self.ids = {}, [], set()
        self.text, self.code, self.lines = [], [], []
        self.inline_script, self.inline_style, self.handlers = False, False, []
        self._skip, self._in_title, self._h1, self._code, self._line = 0, False, None, None, None
        self._prompt = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag == "html":
            self.lang = a.get("lang", "")
        if "id" in a:
            self.ids.add(a["id"])
        if "style" in a:
            self.inline_style = True
        self.handlers += [k for k in a if k.startswith("on")]
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = a.get("name") or a.get("property") or a.get("http-equiv")
            if key:
                self.meta[key.lower()] = a.get("content", "")
        elif tag == "script":
            self._skip += 1
            if "src" not in a:
                self.inline_script = True
            else:
                self.links.append((tag, "src", a["src"]))
        elif tag == "style":
            self._skip += 1
            self.inline_style = True
        elif tag == "a" and "href" in a:
            self.links.append(("a", "href", a["href"]))
        elif tag == "link" and "href" in a:
            if a.get("rel") == "canonical":
                self.canonical = a["href"]
            else:
                self.links.append(("link", "href", a["href"]))
        elif tag in ("img", "source", "iframe", "video", "audio", "embed", "object") and ("src" in a or "data" in a):
            self.links.append((tag, "src", a.get("src") or a.get("data")))
        elif tag == "form" and "action" in a:
            self.links.append(("form", "action", a["action"]))
        if tag == "h1":
            self._h1 = []
        if tag == "code":
            self._code = []
        if tag == "span" and "line" in cls:
            self._line = []
        if tag == "span" and "prompt" in cls:
            self._prompt += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in ("script", "style"):
            self._skip -= 1
        elif tag == "h1" and self._h1 is not None:
            self.h1.append("".join(self._h1).strip())
            self._h1 = None
        elif tag == "code" and self._code is not None:
            self.code.append("".join(self._code).strip())
            self._code = None
        elif tag == "span" and self._prompt:
            self._prompt -= 1
        elif tag == "span" and self._line is not None:
            self.lines.append("".join(self._line).strip())
            self._line = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        self.text.append(data)
        if self._h1 is not None:
            self._h1.append(data)
        if self._code is not None:
            self._code.append(data)
        if self._line is not None and not self._prompt:
            self._line.append(data)


def parse(path):
    page = Page()
    with open(path, encoding="utf-8") as handle:
        page.feed(handle.read())
    page.visible = re.sub(r"\s+", " ", " ".join(page.text))
    return page


# ---------------------------------------------------------------- the plugin at the pinned ref

def load_plugin(config, plugin, plugin_git):
    """Returns (directory, is_temporary). The plugin is a checkout, a local clone read at the ref, or a shallow fetch."""
    if plugin:
        return os.path.abspath(plugin), False
    plugin = os.environ.get("RECORTEX_PLUGIN_DIR")
    if plugin:
        return os.path.abspath(plugin), False
    tmp = tempfile.mkdtemp(prefix="recortex-plugin-")
    ref = config["plugin_ref"]
    if plugin_git:
        proc = subprocess.Popen(["git", "-C", plugin_git, "archive", "--format=tar", ref], stdout=subprocess.PIPE)
        with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
            for member in archive:
                if member.name.split("/")[0] in ("tests", "examples", ".tmp") or member.name.startswith("docs/pictures"):
                    continue
                archive.extract(member, tmp)
        proc.wait()
        if proc.returncode:
            sys.exit("check: git archive %s failed in %s" % (ref, plugin_git))
        return tmp, True
    url = "https://github.com/%s.git" % config["plugin_repo"]
    for cmd in (["git", "init", "-q", tmp], ["git", "-C", tmp, "remote", "add", "origin", url],
                ["git", "-C", tmp, "fetch", "-q", "--depth", "1", "origin", ref],
                ["git", "-C", tmp, "checkout", "-q", "FETCH_HEAD"]):
        if subprocess.call(cmd):
            sys.exit("check: could not fetch %s at %s from GitHub" % (config["plugin_repo"], ref))
    return tmp, True


class Plugin:
    def __init__(self, root):
        self.root = root
        self.files = []
        for folder, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in (".git", "tests", "examples", ".tmp", "node_modules")]
            for name in names:
                self.files.append(os.path.relpath(os.path.join(folder, name), root).replace(os.sep, "/"))
        self.basenames = {f.rsplit("/", 1)[-1] for f in self.files}
        self.commands = {f[len("commands/"):-3] for f in self.files if f.startswith("commands/") and f.endswith(".md")}
        self.agents = {f[len("agents/"):-3] for f in self.files if f.startswith("agents/") and f.endswith(".md")}
        self.skills = {f.split("/")[1] for f in self.files if f.startswith("skills/") and f.endswith("/SKILL.md")}
        self.scripts = {}
        parts = []
        for f in self.files:
            text = self._read(f)
            if f.startswith("skills/recortex/scripts/"):
                self.scripts[f.rsplit("/", 1)[-1]] = text
            if f.endswith((".py", ".sh", ".md", ".json")) and not f.startswith(("skills/recortex/fields/", "skills/recortex/page/")):
                parts.append(text)
        self.corpus = "\n".join(parts)
        self.all_scripts = "\n".join(self.scripts.values())
        try:
            self.manifest = json.loads(self._read(".claude-plugin/plugin.json"))
            self.market = json.loads(self._read(".claude-plugin/marketplace.json"))
        except ValueError:
            self.manifest, self.market = {}, {}

    def _read(self, rel):
        try:
            with open(os.path.join(self.root, rel), encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return ""

    def has_path(self, rel):
        rel = rel.strip("/")
        return rel in self.files or any(f.startswith(rel + "/") for f in self.files)


# ---------------------------------------------------------------- the checks

class Report:
    def __init__(self):
        self.failures, self.warnings, self.counts = [], [], {}

    def fail(self, check, where, message):
        self.failures.append("[%s] %s: %s" % (check, where, message))
        self.counts.setdefault(check, [0, 0])[1] += 1

    def ok(self, check, n=1):
        self.counts.setdefault(check, [0, 0])[0] += n

    def warn(self, message):
        self.warnings.append(message)


def check_structure(rep, where, page):
    if not page.title.strip():
        rep.fail("title", where, "the page has no title")
    else:
        rep.ok("title")
    if len(page.h1) != 1 or not page.h1[0]:
        rep.fail("title", where, "the page needs exactly one h1 with text (found %d)" % len(page.h1))
    if not page.meta.get("description", "").strip():
        rep.fail("title", where, "the page has no description")
    if page.lang != "en":
        rep.fail("title", where, "the html element needs lang=\"en\"")


def check_words(rep, where, page):
    whole = " ".join([page.title, page.meta.get("description", ""), page.visible]).lower()
    for word in BANNED:
        if re.search(r"\b%s" % word, whole):
            rep.fail("words", where, "banned word: %s" % word)
    if EMOJI.search(page.visible + page.title):
        rep.fail("words", where, "an emoji or pictograph in the text")
    for word in BRITISH:
        if re.search(r"\b%s" % word, whole):
            rep.fail("words", where, "British spelling: %s" % word)
    rep.ok("words")


def check_loads(rep, where, page):
    if "content-security-policy" not in page.meta:
        rep.fail("loads", where, "no Content-Security-Policy meta")
    else:
        policy = page.meta["content-security-policy"]
        if "default-src 'none'" not in policy or "unsafe-inline" in policy or "unsafe-eval" in policy or "*" in policy:
            rep.fail("loads", where, "the policy is not strict")
        if re.search(r"https?:", policy):
            rep.fail("loads", where, "the policy allows another server")
    if page.inline_script or page.inline_style or page.handlers:
        rep.fail("loads", where, "inline script, style or event handler (the policy would block it)")
    for tag, attr, value in page.links:
        if tag != "a" and (re.match(r"(?i)(https?:)?//", value) or value.lower().startswith(("data:", "javascript:"))):
            rep.fail("loads", where, "<%s %s=%s> loads from outside the site" % (tag, attr, value[:60]))
    rep.ok("loads")


def check_links(rep, site, where, page, pages, plugin, config):
    base = where

    def ids_of(file):
        if file not in pages:
            pages[file] = parse(file)
        return pages[file].ids

    for tag, attr, value in page.links:
        value = value.strip()
        if not value:
            rep.fail("links", base, "empty %s" % attr)
        elif value.startswith("#"):
            if value != "#" and value[1:] not in page.ids:
                rep.fail("links", base, "no anchor %s on this page" % value)
        elif re.match(r"(?i)https?://", value):
            m = re.match(r"(?i)https?://([^/:?#]+)([^?#]*)", value)
            host, path = m.group(1).lower(), m.group(2)
            if value.lower().startswith("http://"):
                rep.fail("links", base, "an http link, use https: %s" % value)
            if tag != "a":
                continue
            if host not in ALLOWED_LINK_HOSTS:
                rep.fail("links", base, "link to a host that is not on the list: %s" % host)
            if host == "github.com":
                check_github_url(rep, base, path, plugin, config)
        elif value.startswith("/") and not value.startswith("//"):
            target, _, frag = value.partition("#")
            target = target.split("?")[0]
            rel = target.lstrip("/")
            file = os.path.join(site, rel, "index.html") if (target.endswith("/") or not rel) else os.path.join(site, rel)
            if os.path.isdir(file):
                file = os.path.join(file, "index.html")
            if not os.path.isfile(file):
                rep.fail("links", base, "broken link: %s" % value)
            elif frag and file.endswith(".html") and frag not in ids_of(file):
                rep.fail("links", base, "no anchor #%s at %s" % (frag, target))
            else:
                rep.ok("links")
        elif value.lower().startswith(("mailto:", "tel:")):
            rep.fail("links", base, "contact link not expected: %s" % value)
        else:
            rep.fail("links", base, "link is not root-absolute: %s" % value)


def check_github_url(rep, where, path, plugin, config):
    parts = [p for p in path.split("/") if p]
    if not parts:
        return
    if len(parts) == 1:
        if parts[0] not in ALLOWED_OWNERS:
            rep.fail("names", where, "GitHub account not on the list: %s" % parts[0])
        return
    slug = "%s/%s" % (parts[0], parts[1])
    if slug not in ALLOWED_REPOS:
        rep.fail("names", where, "GitHub repository not on the list: %s" % slug)
        return
    if slug == config["plugin_repo"] and len(parts) >= 4 and parts[2] in ("blob", "tree"):
        ref, rel = parts[3], "/".join(parts[4:])
        if ref != config["plugin_ref"]:
            rep.fail("plugin", where, "link names ref %s, site.json pins %s" % (ref, config["plugin_ref"]))
        if rel and not plugin.has_path(rel):
            rep.fail("plugin", where, "linked file is not in the plugin at the pinned ref: %s" % rel)
        else:
            rep.ok("plugin")


def check_names(rep, where, page):
    text = page.visible
    for m in re.finditer(r"(?<![\w@./:~-])([A-Za-z][A-Za-z0-9_-]{0,38})/([A-Za-z0-9][A-Za-z0-9._-]{0,99})(?![\w/])", text):
        owner, name = m.group(1), m.group(2)
        slug = "%s/%s" % (owner, name)
        if slug in ALLOWED_REPOS or owner.lower() in PLUGIN_DIRS or (owner.isupper() and name.isupper()):
            continue
        looks_like_repo = bool(re.search(r"[-_0-9]", owner) or re.search(r"[-_0-9.]", name)) or slug in " ".join(page.code)
        if looks_like_repo:
            rep.fail("names", where, "a project name that is not on the list of invented names: %s" % slug)
    for m in re.finditer(r"(?<![\w.])@([A-Za-z0-9][A-Za-z0-9-]{0,38})", text):
        if m.group(1) not in ALLOWED_AT and m.group(1) not in ALLOWED_OWNERS:
            rep.fail("names", where, "an account name that is not on the list: @%s" % m.group(1))
    rep.ok("names")


def flags_of(tokens):
    return [re.split(r"[=,]", t)[0] for t in tokens if t.startswith("--") and len(t) > 2]


def check_plugin_facts(rep, where, page, plugin, config):
    text = page.visible
    for m in re.finditer(r"(?<![\w/])/recortex:([a-z][a-z-]*)", text):
        if m.group(1) not in plugin.commands and m.group(1) not in plugin.skills:
            rep.fail("plugin", where, "no such command in the plugin: /recortex:%s" % m.group(1))
        else:
            rep.ok("plugin")
    for m in re.finditer(r"@agent-recortex:([a-z][a-z-]*)|(?<![\w/@-])recortex:([a-z][a-z-]*)", text):
        name = m.group(1) or m.group(2)
        if name not in plugin.agents:
            rep.fail("plugin", where, "no such agent in the plugin: %s" % name)
        else:
            rep.ok("plugin")

    units = list(page.code) + list(page.lines)
    for unit in units:
        unit = unit.strip()
        if not unit:
            continue
        for sm in re.finditer(r"\b([a-z][a-z0-9_]*\.(?:py|sh))\b((?: [^\s<>`]+)*)", unit):
            script, rest = sm.group(1), sm.group(2).split()
            if script not in plugin.scripts:
                rep.fail("plugin", where, "no such script: %s" % script)
                continue
            body = plugin.scripts[script]
            if rest and re.match(r"^[a-z][a-z-]*$", rest[0]):
                if not re.search(r"""["']%s["']""" % re.escape(rest[0]), body):
                    rep.fail("plugin", where, "%s has no subcommand %s" % (script, rest[0]))
            for flag in flags_of(rest):
                if flag not in body and flag not in plugin.all_scripts:
                    rep.fail("plugin", where, "%s has no flag %s" % (script, flag))
            rep.ok("plugin")
        if unit.startswith("claude "):
            if not CLAUDE_LINE.match(unit):
                rep.fail("plugin", where, "a claude command the site does not know: %s" % unit)
            add = re.match(r"claude plugin marketplace add (\S+)$", unit)
            if add and add.group(1) != config["plugin_repo"]:
                rep.fail("plugin", where, "marketplace add names %s, site.json says %s" % (add.group(1), config["plugin_repo"]))
            inst = re.match(r"claude plugin install (\S+)$", unit)
            if inst:
                want = "%s@%s" % (plugin.manifest.get("name", "?"), plugin.market.get("name", "?"))
                if inst.group(1) != want:
                    rep.fail("plugin", where, "install names %s, the plugin's manifests say %s" % (inst.group(1), want))
        elif unit.startswith("gh ") and not GH_LINE.match(unit):
            rep.fail("plugin", where, "a gh command the site does not know: %s" % unit)
        elif unit.startswith("/recortex ") and unit not in plugin.corpus:
            rep.fail("plugin", where, "the plugin never shows this line: %s" % unit)
        if not re.search(r"\b[a-z0-9_]+\.(py|sh)\b", unit):
            for flag in flags_of(unit.split()):
                if flag not in plugin.corpus:
                    rep.fail("plugin", where, "no such flag anywhere in the plugin: %s" % flag)
        for fm in re.finditer(r"(?<![\w-])((?:~/|\./|/)?(?:[\w.-]+/)*[\w.-]*[\w-]\.(?:json|md|html|yml|yaml|db|sqlite|toml|txt))(?![\w])", unit):
            base = fm.group(1).rsplit("/", 1)[-1]
            if base not in plugin.basenames and base not in plugin.corpus:
                rep.fail("plugin", where, "no such file name in the plugin: %s" % base)
            else:
                rep.ok("plugin")
        for pm in re.finditer(r"(?<![\w./~-])((?:skills|docs|agents|commands|hooks)/[\w./-]+)", unit):
            if not plugin.has_path(pm.group(1)):
                rep.fail("plugin", where, "no such path in the plugin: %s" % pm.group(1))
        for tm in re.finditer(r"~/[\w./-]+", unit):
            leaf = tm.group(0).rstrip("/").rsplit("/", 1)[-1]
            if leaf not in plugin.corpus:
                rep.fail("plugin", where, "no such name in the plugin: %s" % leaf)

    for code in page.code:
        if re.match(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$", code) and code not in plugin.corpus:
            rep.fail("plugin", where, "no such name in the plugin: %s" % code)
        if re.match(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$", code) and code not in PLACEHOLDERS and code not in plugin.corpus:
            rep.fail("plugin", where, "no such variable in the plugin: %s" % code)


def check_static(rep, site):
    css = os.path.join(site, "site.css")
    if os.path.isfile(css):
        with open(css, encoding="utf-8") as handle:
            text = handle.read()
        if re.search(r"@import|url\(\s*['\"]?(https?:)?//", text):
            rep.fail("loads", "site.css", "loads something from another server")
    for name in ("site.js", "theme.js", "search-index.js"):
        path = os.path.join(site, name)
        if not os.path.isfile(path):
            rep.fail("loads", name, "missing")
            continue
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        if re.search(r"\bfetch\(|XMLHttpRequest|WebSocket|sendBeacon|\bimport\(|importScripts|document\.cookie|https?://", text):
            rep.fail("loads", name, "makes a request, sets a cookie or names another server")
        keys = re.findall(r"localStorage\.\w+Item\(\s*'([^']+)'", text)
        if any(k != "theme" for k in keys):
            rep.fail("loads", name, "stores something besides the theme choice")
        if "localStorage" in text and "try" not in text:
            rep.fail("loads", name, "localStorage is used outside try/catch")
    for must in (".nojekyll", "robots.txt", "sitemap.xml", "favicon.ico", "404.html", "assets/social-preview.png"):
        if not os.path.isfile(os.path.join(site, must)):
            rep.fail("loads", must, "missing from the built site")
    rep.ok("loads")


def check_version(rep, config, plugin):
    shown, have = config["version"], plugin.manifest.get("version", "?")
    if shown == have:
        rep.ok("plugin")
    elif config["plugin_ref"] in ("main", "master", "HEAD"):
        rep.warn("site.json describes %s but %s of the plugin is %s; pin plugin_ref to the %s release before you publish"
                 % (shown, config["plugin_ref"], have, shown))
    else:
        rep.fail("plugin", "site.json", "describes %s but the plugin at %s is %s" % (shown, config["plugin_ref"], have))


def run(site, config, pages_cfg, counts, plugin_args):
    plugin_dir, temporary = load_plugin(config, plugin_args.get("plugin"), plugin_args.get("plugin_git"))
    try:
        plugin = Plugin(plugin_dir)
        if not plugin.files:
            sys.exit("check: no plugin files found in %s" % plugin_dir)
        rep = Report()
        parsed = {}
        for entry in pages_cfg:
            target = "404.html" if entry["path"].endswith(".html") else os.path.join(entry["path"].strip("/"), "index.html")
            file = os.path.join(site, target)
            where = entry["path"]
            if not os.path.isfile(file):
                rep.fail("title", where, "the page was not built")
                continue
            parsed[file] = page = parse(file)
            page.path = where
            check_structure(rep, where, page)
            check_words(rep, where, page)
            check_loads(rep, where, page)
            check_names(rep, where, page)
            check_plugin_facts(rep, where, page, plugin, config)
            n, limit = (counts or {}).get(entry["id"]), entry.get("max_words")
            if n is not None and limit and n > limit:
                rep.fail("length", where, "%d words, over the limit of %d" % (n, limit))
            else:
                rep.ok("length")
        for page in list(parsed.values()):
            check_links(rep, site, page.path, page, parsed, plugin, config)
        check_static(rep, site)
        check_version(rep, config, plugin)
        return report(rep, plugin_dir)
    finally:
        if temporary:
            shutil.rmtree(plugin_dir, ignore_errors=True)


def report(rep, plugin_dir):
    print("check: plugin read from %s" % plugin_dir)
    for name in ("title", "words", "length", "loads", "links", "names", "plugin"):
        ok, bad = rep.counts.get(name, [0, 0])
        print("  %-8s %4d ok, %d failed" % (name, ok, bad))
    for message in rep.warnings:
        print("warning: %s" % message)
    for message in rep.failures:
        print("FAIL " + message)
    print("check: %s" % ("failed (%d)" % len(rep.failures) if rep.failures else "passed"))
    return 1 if rep.failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--site", default=os.path.join(HERE, "_site"))
    parser.add_argument("--plugin")
    parser.add_argument("--plugin-git")
    args = parser.parse_args()
    with open(os.path.join(HERE, "site.json"), encoding="utf-8") as handle:
        config = json.load(handle)
    with open(os.path.join(HERE, "src", "pages.json"), encoding="utf-8") as handle:
        pages = json.load(handle)
    sys.exit(run(os.path.abspath(args.site), config, pages, None, {"plugin": args.plugin, "plugin_git": args.plugin_git}))


if __name__ == "__main__":
    main()
