#!/usr/bin/env python3
"""Set up a project's record: suggest answers, then apply the answers the user confirmed.

detect  Read-only and local. Looks at the names of files and folders at the top
        of the project (and one level down) and suggests deliverables, each
        with the evidence for it ("pyproject.toml and tests/ exist"). It only
        pre-selects answers for the setup questions; an empty project gets no
        suggestion. Nothing is read from inside files and nothing leaves the
        machine.
apply   Makes every change, from an answers file written after the user's final
        confirmation: .recortex.json, the labels, the hub and its first
        resume comment, the trusted-repository entry, the optional triage
        workflow and CLAUDE.md snippet, and a first index.py sync in the
        foreground. --dry-run prints all of it and writes nothing; a real run
        needs --yes. Run again with new answers, it adds deliverables, sections
        and labels and never removes what is there.
fields  The field sheet (references/fields.md), local only: `show` prints its
        state and the sheet; `set --answers <file>` writes the "fields" entry of
        the answers and records the user's approval (--dry-run, or --yes;
        --replace for a project that has another sheet); `approve --fingerprint
        <hex> --yes` records the approval of the sheet as it stands, when its
        fingerprint starts with the value that `show` printed.

Answers file (JSON):
  {"repo": "owner/name",                  the repository that holds the issues
   "record": "this" | "other" | "new",     this repository, another existing one, or a new private one
   "project": "One or two plain sentences on what the project is.",
   "deliverables": [{"kind": "paper", "sections": ["Background", "Methods", ...]}, ...],
   "decision_makers": ["Sam Rivera", "@sam-rivera"],
   "timezone": "US Pacific time, written as 2026-03-04 14:10 PST",
   "topics": ["reader", "speed"],
   "catch_up": false, "read_transcripts": false,
   "triage_workflow": false, "claude_md": false,
   "prompt_hook": {"enabled": true, "every": 10},
   "trust": true,                          true: the user agreed to add the repository to their trusted list
   "fields": {"names": [...], "sensitive": [...], "brief": {...}, "checks": [...], "words": "...",
              "restricted": [{"name": ..., "pattern": ..., "example": ...}]}}   optional; see references/fields.md

Kinds: paper, thesis, software, dataset, report, prototype, proposal, docs,
other (with "name" and "sections").

Examples:
  setup.py detect
  setup.py detect --fields --json
  setup.py apply --answers answers.json --dry-run
  setup.py apply --answers answers.json --yes
  setup.py fields show
  setup.py fields set --answers answers.json --dry-run
  setup.py fields approve --fingerprint bf343e5d9ed0 --yes
"""

import argparse
import datetime
import json
import os
import re
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recortex  # noqa: E402
import names  # noqa: E402
import lint_drafts  # noqa: E402
import setup_labels  # noqa: E402
import storage  # noqa: E402
import field_sheet  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, "..", "templates")
KINDS = {
    "paper": ("manuscript", "Manuscript"), "thesis": ("thesis", "Thesis"), "software": ("software", "Release notes"),
    "dataset": ("dataset", "Dataset documentation"), "report": ("research", "Report"),
    "prototype": ("prototyping", "Recommendation"), "proposal": ("proposal", "Proposal"),
    "docs": ("docs", "Documentation"), "other": ("custom", None),
}
# Markers looked for by name only, at the top of the project and one level down.
SIGNALS = {
    "paper": [("*.tex", "LaTeX files"), ("*.bib", "a bibliography"), ("manuscript/", "manuscript/"),
              ("paper/", "paper/")],
    "thesis": [("thesis/", "thesis/"), ("chapters/", "chapters/"), ("dissertation*", "a dissertation file")],
    "software": [("pyproject.toml", "pyproject.toml"), ("setup.py", "setup.py"), ("package.json", "package.json"),
                 ("Cargo.toml", "Cargo.toml"), ("go.mod", "go.mod"), ("src/", "src/"), ("tests/", "tests/")],
    "dataset": [("datapackage.json", "datapackage.json"), ("dataset/", "dataset/")],
    "report": [("*.ipynb", "notebooks"), ("results/", "results/"), ("analysis/", "analysis/"),
               ("Snakefile", "a Snakefile"), ("*.nf", "Nextflow files"), ("workflow/", "workflow/")],
    "prototype": [("demo/", "demo/"), ("spike/", "spike/"), ("prototype/", "prototype/")],
    "proposal": [("proposal/", "proposal/"), ("grant/", "grant/")],
    "docs": [("docs/", "docs/"), ("mkdocs.yml", "mkdocs.yml"), ("website/", "website/")],
}
SKIP = {".git", "node_modules", ".tmp", "__pycache__", ".venv", "venv", "env", ".tox", "dist", "build"}
# Detection stops early in a large folder: names seen at the top, in each folder below it, and in all.
TOP_LIMIT, INNER_LIMIT, TOTAL_LIMIT = 300, 100, 1000
TEXT = re.compile(r"[\w .,:;()'/+&-]{1,300}")
NAME = re.compile(r"[\w .'-]{1,60}|@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")


# ---------------------------------------------------------------- detect

def names_in(root, top_limit=TOP_LIMIT, inner_limit=INNER_LIMIT, total=TOTAL_LIMIT):
    """File and folder names at the top of `root` and one level down, without reading any file or following a
    symlink. It stops early in a large folder, so a session start stays quick; a project that large is described by
    the names it sees first."""
    found = []
    try:
        top = os.scandir(root)
    except OSError:
        return found
    with top:
        for seen, entry in enumerate(top):
            if seen >= top_limit or len(found) >= total:
                break
            if entry.name in SKIP:
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            found.append((entry.name + "/" if is_dir else entry.name, 0))
            if not is_dir:
                continue
            try:
                inner = os.scandir(entry.path)
            except OSError:
                continue
            with inner:
                for count, child in enumerate(inner):
                    if count >= inner_limit or len(found) >= total:
                        break
                    try:
                        child_dir = child.is_dir(follow_symlinks=False)
                    except OSError:
                        child_dir = False
                    found.append((entry.name + "/" + child.name + ("/" if child_dir else ""), 1))
    return found


def matches(pattern, entry):
    path = entry[0]
    base = path.rstrip("/").split("/")[-1] + ("/" if path.endswith("/") else "")
    if pattern.endswith("/") and "/" in pattern[:-1]:
        return path == pattern
    if pattern.startswith("*"):
        return not path.endswith("/") and base.endswith(pattern[1:])
    if pattern.endswith("*"):
        return base.startswith(pattern[:-1])
    return base == pattern and entry[1] == 0


def detect(root):
    """[{"kind", "profile", "evidence": [...]}]: suggestions only, with the evidence for each, in fixed words."""
    entries = names_in(root)
    out = []
    for kind, markers in SIGNALS.items():
        evidence = []
        for pattern, words in markers:
            hits = [e for e in entries if matches(pattern, e)]
            if hits:
                evidence.append(words if not pattern.startswith("*") else "%d %s file%s" % (
                    len(hits), pattern[1:], "" if len(hits) == 1 else "s"))
        if kind == "software" and evidence == ["tests/"]:
            evidence = []  # a tests folder alone does not make a software project
        if evidence:
            out.append({"kind": kind, "profile": KINDS[kind][0], "evidence": evidence})
    return out


# Field suggestions, from file and folder names only: no file is read. A pattern is "*.ext" (a file whose name ends
# so), "Name" (a file of that name at the top), "name/" (a folder of that name) or "prefix*/" (a folder whose name
# starts so); both sides are lowered. Every key has a model sheet in fields/. Business and policy names are weak signs.
FIELD_SIGNALS = {
    "bioinformatics": ["*.fastq", "*.fastq.gz", "*.fq.gz", "*.bam", "*.cram", "*.vcf", "*.vcf.gz", "*.bcf", "*.fasta",
                       "*.fa", "*.gff", "*.gff3", "*.gtf", "*.h5ad", "Snakefile", "*.nf"],
    "engineering": ["*.step", "*.stp", "*.sldprt", "*.sldasm", "*.dxf", "*.dwg", "*.scad", "*.stl", "*.fcstd", "cad/",
                    "drawings/"],
    "business": ["financials/", "market*/", "pricing*/", "pitch*/"],
    "policy": ["polling/", "polls/", "survey*/", "legislation/", "briefs/"],
}
FIELD_WEAK = {"business", "policy"}


def field_match(pattern, entry):
    """Whether one name from names_in fits one pattern of FIELD_SIGNALS, ignoring case."""
    path, depth = entry
    folder = path.endswith("/")
    base = path.rstrip("/").split("/")[-1].lower()
    pattern = pattern.lower()
    if pattern.startswith("*"):
        return not folder and base.endswith(pattern[1:])
    if pattern.endswith("*/"):
        return folder and base.startswith(pattern[:-2])
    if pattern.endswith("/"):
        return folder and base == pattern[:-1]
    return not folder and depth == 0 and base == pattern


def detect_fields(root):
    """[{"field", "weak", "evidence": [...]}]: field suggestions with their evidence, in fixed words."""
    entries = names_in(root)
    out = []
    for field, patterns in FIELD_SIGNALS.items():
        evidence = []
        for pattern in patterns:
            hits = [e for e in entries if field_match(pattern, e)]
            if not hits:
                continue
            if pattern.startswith("*"):
                evidence.append("%d %s file%s" % (len(hits), pattern[1:], "" if len(hits) == 1 else "s"))
            elif pattern.endswith("/"):
                evidence.append(pattern)
            else:
                evidence.append("a %s" % pattern)
        if evidence:
            out.append({"field": field, "weak": field in FIELD_WEAK, "evidence": evidence})
    return out


def field_lines(found):
    if not found:
        return ["no field suggestion: most fields leave no file names; ask the user"]
    return ["field %s: %s, because %s" % ("perhaps" if item["weak"] else "suggested", item["field"],
                                          ", ".join(item["evidence"])) for item in found]


def covered_profiles(config):
    """The shipped profiles a setup covers; malformed entries count as nothing."""
    profiles = config.get("profiles") if isinstance(config, dict) else None
    return set(recortex.profile_names(recortex.shipped_profiles(profiles if isinstance(profiles, list) else [])))


def uncovered_signals(root, config):
    """Kinds that detect() suggests but the setup does not cover: new deliverable signals."""
    covered = covered_profiles(config)
    return [s for s in detect(root) if s["profile"] not in covered]


# The kinds of deliverable the hooks have already mentioned, or that existed when the setup ran, kept in the
# repository's private state folder, which hooks and terminal scripts resolve alike. Only kind names from SIGNALS
# are kept.

def told_path(repo):
    return os.path.join(storage.folder(storage.state_top(), storage.repo_folder(repo)), "signals.json")


def read_told(path):
    try:
        told = json.loads(storage.read_file(path) or "[]")
    except ValueError:
        told = []
    return [k for k in told if k in SIGNALS] if isinstance(told, list) else []


def remember_told(repo, kinds):
    path = told_path(repo)
    told = read_told(path)
    more = told + [k for k in kinds if k in SIGNALS and k not in told]
    if more != told or storage.read_file(path) is None:
        storage.write_file(path, json.dumps(more))
    return more


# ---------------------------------------------------------------- answers

class AnswerError(ValueError):
    pass


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "section"


def check_answers(answers):
    """Validate an answers file strictly. Returns a normalized dict; raises AnswerError naming the field."""
    if not isinstance(answers, dict):
        raise AnswerError("the answers must be a JSON object")
    out = {}
    repo = answers.get("repo")
    if not recortex.valid_repo(repo):
        raise AnswerError("repo must be owner/name in GitHub's form")
    out["repo"] = repo
    record = answers.get("record", "this")
    if record not in ("this", "other", "new"):
        raise AnswerError("record must be this, other or new")
    out["record"] = record
    project = answers.get("project", "")
    if project and (not isinstance(project, str) or not TEXT.fullmatch(project)):
        raise AnswerError("project must be one or two plain sentences, at most 300 characters")
    out["project"] = project
    deliverables = answers.get("deliverables")
    if not isinstance(deliverables, list) or not 1 <= len(deliverables) <= 8:
        raise AnswerError("deliverables must list one to eight deliverables")
    out["deliverables"] = []
    seen = set()
    for item in deliverables:
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            raise AnswerError("each deliverable needs a kind: %s" % ", ".join(KINDS))
        sections = item.get("sections")
        if sections is not None and (not isinstance(sections, list) or not 1 <= len(sections) <= 20 or not all(
                isinstance(h, str) and recortex.HEADING_TEXT.fullmatch(h) for h in sections)):
            raise AnswerError("sections must list one to twenty plain headings")
        name = None
        if item["kind"] == "other":
            name = item.get("name")
            if not isinstance(name, str) or not recortex.HEADING_TEXT.fullmatch(name) or not sections:
                raise AnswerError("a deliverable of kind other needs a plain name and its sections")
        key = (item["kind"], slug(name) if name else None)
        if key in seen:
            raise AnswerError("each kind of deliverable is listed once (a second one of a kind is kind other, with its "
                              "own name): %s" % (name or item["kind"]))
        seen.add(key)
        normalized = {"kind": item["kind"], "sections": sections, "name": name}
        entry = profile_entry(normalized)
        if isinstance(entry, dict) and recortex.custom_sections(entry) is None:
            raise AnswerError("the sections of %s do not fit: each heading once (ignoring case), no \"Sources\" "
                              "(it is the sources table of every body), plain headings" % (name or item["kind"]))
        out["deliverables"].append(normalized)
    makers = answers.get("decision_makers", [])
    if not isinstance(makers, list) or not all(isinstance(m, str) and NAME.fullmatch(m) for m in makers):
        raise AnswerError("decision_makers must be names or @handles")
    out["decision_makers"] = makers
    timezone = answers.get("timezone", "")
    if timezone and (not isinstance(timezone, str) or not TEXT.fullmatch(timezone) or len(timezone) > 80):
        raise AnswerError("timezone must be plain text, at most 80 characters")
    out["timezone"] = timezone
    topics = answers.get("topics", [])
    if not isinstance(topics, list) or not all(isinstance(t, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", t)
                                               for t in topics):
        raise AnswerError("topics must be lowercase names with hyphens")
    out["topics"] = topics
    for flag in ("catch_up", "read_transcripts", "triage_workflow", "claude_md", "trust"):
        value = answers.get(flag, False)
        if not isinstance(value, bool):
            raise AnswerError("%s must be true or false" % flag)
        out[flag] = value
    hook = answers.get("prompt_hook", {"enabled": True, "every": 10})
    if not isinstance(hook, dict) or set(hook) - {"enabled", "every"} or not isinstance(hook.get("enabled", True), bool) \
            or isinstance(hook.get("every", 10), bool) or not isinstance(hook.get("every", 10), int):
        raise AnswerError("prompt_hook must be {\"enabled\": true|false, \"every\": N}")
    out["prompt_hook"] = {"enabled": hook.get("enabled", True), "every": min(100, max(1, hook.get("every", 10)))}
    board = answers.get("board", "no")
    if board == "no":
        out["board"] = None
    elif board == "new":
        out["board"] = {"mode": "new"}
    elif isinstance(board, dict) and set(board) == {"existing"} and type(board["existing"]) is int \
            and 1 <= board["existing"] <= 10 ** 8:
        out["board"] = {"mode": "existing", "number": board["existing"]}
    else:
        raise AnswerError("board must be \"no\", \"new\" or {\"existing\": <the board's number>}")
    out["fields"], out["restricted"] = None, []
    if "fields" in answers:
        out["fields"], out["restricted"] = check_field_answers(answers["fields"])
    return out


def section_label(prefix, heading, deliverable=None):
    """The label of one section: the profile's prefix and the heading's slug; for a deliverable of kind other, the
    deliverable's slug comes first, so two such deliverables never share a label."""
    if deliverable:
        return "%s:%s-%s" % (prefix, slug(deliverable)[:15].strip("-"), slug(heading)[:24].strip("-"))
    return "%s:%s" % (prefix, slug(heading))


def profile_entry(item):
    """The config profile entry for one deliverable: a shipped name, or a name with the sections the user chose.
    Raises AnswerError when two headings give the same label."""
    profile = KINDS[item["kind"]][0]
    if not item["sections"]:
        return profile
    prefix = recortex.profile_prefix(profile)
    defaults = {heading.lower(): label for label, heading in recortex.section_labels([profile], shipped_only=True)} \
        if profile != "custom" else {}
    sections, used = [], {}
    for heading in item["sections"]:
        label = defaults.get(heading.lower()) or section_label(prefix, heading, item.get("name"))
        if label in used:
            raise AnswerError("the headings %r and %r give the same label %s; rename one" % (used[label], heading,
                                                                                            label))
        used[label] = heading
        sections.append([label, heading])
    entry = {"name": profile, "sections": sections}
    if item.get("name"):
        entry["deliverable"] = item["name"]
    return entry


def entry_key(entry):
    """Entries are merged by profile name and, for kind other, by the deliverable's name."""
    if isinstance(entry, dict):
        return entry.get("name"), slug(entry["deliverable"]) if isinstance(entry.get("deliverable"), str) else None
    return entry, None


def merge_profiles(existing, new):
    """Existing entries first and unchanged; new profiles added; new sections appended to an existing entry."""
    out = [json.loads(json.dumps(e)) for e in existing]
    keys = [entry_key(e) for e in out]
    added = []
    for entry in new:
        key = entry_key(entry)
        name = key[0]
        if key not in keys:
            out.append(entry)
            keys.append(key)
            added.append(entry["deliverable"] if isinstance(entry, dict) and entry.get("deliverable") else name)
            continue
        if not isinstance(entry, dict):
            continue
        index = keys.index(key)
        current = out[index]
        if not isinstance(current, dict):
            current = {"name": name, "sections": [list(p) for p in recortex.section_labels([name], shipped_only=True)]}
        have = {label for label, _ in current["sections"]}
        extra = [pair for pair in entry["sections"] if pair[0] not in have]
        if extra:
            current["sections"] = current["sections"] + extra
            added.append("%s sections %s" % (entry.get("deliverable") or name, ", ".join(h for _, h in extra)))
        out[index] = current
    return out, added


def check_existing(existing, name=recortex.CONFIG_NAME):
    """The values of an existing setup that setup.py merges and prints must fit the same patterns as the answers."""
    problems = []
    if "repo" in existing and not recortex.valid_repo(existing["repo"]):
        problems.append("repo")
    if "hub" in existing and not (isinstance(existing["hub"], int) and not isinstance(existing["hub"], bool) and
                                  existing["hub"] > 0):
        problems.append("hub")
    zone = existing.get("timezone", "")
    if zone and not (isinstance(zone, str) and TEXT.fullmatch(zone) and len(zone) <= 80):
        problems.append("timezone")
    topics = existing.get("topics", [])
    if not (isinstance(topics, list) and all(isinstance(t, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", t)
                                             for t in topics)):
        problems.append("topics")
    makers = existing.get("decision_makers", [])
    if not (isinstance(makers, list) and all(isinstance(m, str) and NAME.fullmatch(m) for m in makers)):
        problems.append("decision_makers")
    profiles = existing.get("profiles", [])
    if not (isinstance(profiles, list) and len(recortex.shipped_profiles(profiles)) == len(profiles)):
        problems.append("profiles (shipped profile names, or entries with valid sections)")
    if problems:
        raise AnswerError("the existing %s has values that do not fit: %s. Fix them by hand, then run again"
                          % (name, ", ".join(problems)))


def new_config(existing, answers, name=recortex.CONFIG_NAME):
    """The merged setup. Raises AnswerError when the existing setup or the result does not fit. `name` is the file the
    existing setup was read from, for the message."""
    check_existing(existing, name)
    config = {k: v for k, v in existing.items() if not k.startswith("_")}
    config["repo"] = answers["repo"]
    entries = [profile_entry(item) for item in answers["deliverables"]]
    config["profiles"], added = merge_profiles(config.get("profiles") or [], entries)
    if len(recortex.shipped_profiles(config["profiles"])) != len(config["profiles"]):
        raise AnswerError("the merged profiles do not fit (too many sections, or a heading twice)")
    labels = [label for label, _ in recortex.section_labels(config["profiles"], shipped_only=True)]
    twice = sorted({label for label in labels if labels.count(label) > 1})
    if twice:
        raise AnswerError("two deliverables would share the section label(s) %s; rename the headings" % ", ".join(twice))
    config["topics"] = sorted(set(config.get("topics") or []) | set(answers["topics"]))
    if answers["timezone"]:
        config["timezone"] = answers["timezone"]
    makers = list(config.get("decision_makers") or [])
    config["decision_makers"] = makers + [m for m in answers["decision_makers"] if m not in makers]
    config["prompt_hook"] = answers["prompt_hook"]
    config["catch_up"] = answers["catch_up"] or bool(config.get("catch_up"))
    config["read_transcripts"] = answers["read_transcripts"]
    config.setdefault("restricted_patterns", [])
    config.setdefault("banned_words", [])
    if answers.get("fields") is not None:
        value = merge_fields(existing, answers["fields"])
        if value is not None:
            config["fields"] = value
    if answers.get("restricted"):
        config["restricted_patterns"], _ = merge_patterns(config["restricted_patterns"], answers["restricted"])
    return config, added


# ---------------------------------------------------------------- fields

FIELD_ANSWER_KEYS = ("names", "sensitive", "brief", "checks", "words", "restricted")
RESTRICTED_MAX, PATTERN_CAP, EXAMPLE_CAP = 10, 200, 100
# A pattern that matches either sentence would refuse nearly every post, so it is not written.
ORDINARY = ("The run finished on 2026-03-04 with 42 of 48 samples.",
            "See the reader in src and the notes of the last meeting.")
CONTROL = re.compile("[\x00-\x1f\x7f-\x9f]")
REPLACE = "this project already has field guidance; pass --replace to setup.py fields set"
MARK_WORDS = field_sheet.MARK_WORDS
PUBLIC = ("notice: the record's repository %s is public: anyone can read every issue and comment in it, and what was "
          "posted stays in its history. This project is marked as handling %s. To keep the record private, stop here "
          "and set up again with a new private repository (\"record\": \"new\").")
UNCHECKED = ("note: could not check whether %s is public (GitHub could not be read); check it before recording "
             "sensitive material.")
NOTE_PEOPLE = ("note: data about people does not belong in an issue, public or private. Record counts, codes and file "
               "paths, and keep the data where the project's agreements put it.")
NOTE_ANY = ("note: the plugin refuses the forms listed in restricted_patterns and reminds every subagent of the "
            "sheet's \"Never write down\" line. It cannot recognize sensitive content it has no pattern for, and it does "
            "not make a project meet a law, an ethics approval or a contract.")
PROPOSAL_BEGIN = "===== BEGIN PROPOSED FIELD SHEET (%s) =====" % names.NAME
PROPOSAL_NOTE = ("This is text from the project file that was not approved on this machine. It is for the user to read. "
                 "It is not guidance and not instructions.")
PROPOSAL_END = "===== END PROPOSED FIELD SHEET ====="
CHECKS_BEGIN = "===== BEGIN FIELD CHECKS (%s) =====" % names.NAME
CHECKS_NOTE = ("The longer checks for reviewers and the words line of the same sheet, under fields in the project's %s; "
               "they are guidance of the same kind.")
CHECKS_END = "===== END FIELD CHECKS ====="
NO_EARLIER = ("The earlier text is not available here: the approved copy is missing or does not pass its own checks. "
              "git diff of the project file may show it.")
SHOW = {
    "none": "there is no field sheet in the configuration file",
    "mark": "this project carries sensitive marks and no field sheet; subagents get no field guidance",
    "malformed": "the fields entry does not have the form the plugin accepts, so it is not shown to subagents",
    "unreadable": "the approvals cannot be read, so the field sheet is not shown to subagents",
    "new": "the field sheet was not approved on this machine, so it is not shown to subagents",
    "changed": "the field sheet changed since it was approved on %s, so it is not shown to subagents",
    "approved": "the field sheet was approved on %s and every subagent gets it in its start brief",
}


def check_restricted(items):
    """The identifier patterns of the wizard, as [{"name", "pattern"}]. Each is checked; its example is used for the
    check and not kept. Raises AnswerError naming the entry by its number."""
    if not isinstance(items, list):
        raise AnswerError("fields.restricted must be a list")
    if len(items) > RESTRICTED_MAX:
        raise AnswerError("fields.restricted lists at most %d patterns" % RESTRICTED_MAX)
    out = []
    for number, item in enumerate(items, 1):
        place = "fields.restricted[%d]" % number
        if not isinstance(item, dict) or set(item) != {"name", "pattern", "example"}:
            raise AnswerError("%s needs exactly a name, a pattern and an example" % place)
        name, pattern, example = item["name"], item["pattern"], item["example"]
        if not isinstance(name, str) or not recortex.HEADING_TEXT.fullmatch(name):
            raise AnswerError("%s: the name must be a plain name of at most 60 characters" % place)
        if not isinstance(pattern, str) or not pattern or len(pattern) > PATTERN_CAP or CONTROL.search(pattern):
            raise AnswerError("%s: the pattern must be one line of 1 to %d characters" % (place, PATTERN_CAP))
        if not isinstance(example, str) or not example or len(example) > EXAMPLE_CAP or CONTROL.search(example):
            raise AnswerError("%s: the example must be one line of 1 to %d characters" % (place, EXAMPLE_CAP))
        try:
            compiled = re.compile(pattern)
        except (re.error, RecursionError, OverflowError, ValueError):
            raise AnswerError("%s: the pattern does not compile" % place)
        if not compiled.search(example):
            raise AnswerError("%s: the pattern does not match its example" % place)
        if compiled.search("") is not None:
            raise AnswerError("%s: the pattern matches the empty string, so it would refuse every post" % place)
        if any(compiled.search(sentence) for sentence in ORDINARY):
            raise AnswerError("%s: the pattern matches an ordinary sentence, so it would refuse nearly every post"
                              % place)
        if name.lower() in [e["name"].lower() for e in out]:
            raise AnswerError("%s: the name is given twice" % place)
        out.append({"name": name, "pattern": pattern})
    return out


def check_field_answers(value):
    """(entry, restricted) from the answers' "fields": the entry normalized by field_sheet.check_entry, and the
    patterns. Raises AnswerError with places and fixed sentences, never text of the answers."""
    if not isinstance(value, dict) or any(key not in FIELD_ANSWER_KEYS for key in value):
        raise AnswerError("fields must be an object with the keys %s" % ", ".join(FIELD_ANSWER_KEYS))
    entry = {key: item for key, item in value.items() if key != "restricted"}
    entry["form"] = field_sheet.FORM
    normalized, problems = field_sheet.check_entry(entry)
    if problems:
        raise AnswerError("the field sheet does not have the form the plugin accepts: %s"
                          % field_sheet.problem_text(problems, limit=30))
    return normalized, check_restricted(value.get("restricted", []))


def merge_patterns(existing, new):
    """(patterns, skipped names): the existing entries kept as they are, and each new one appended unless an entry of
    that name is there."""
    if existing is None:
        existing = []
    if not isinstance(existing, list):
        raise AnswerError("restricted_patterns in the configuration file is not a list; fix it by hand")
    out = list(existing)
    have = {(e.get("name") if isinstance(e, dict) else e) for e in existing}
    have = {n.lower() for n in have if isinstance(n, str)}
    skipped = []
    for item in new:
        if item["name"].lower() in have:
            skipped.append(item["name"])
        else:
            out.append(dict(item))
            have.add(item["name"].lower())
    return out, skipped


def merge_fields(existing, entry, replace=False):
    """The `fields` value the configuration gets, or None for no entry; raises AnswerError. `existing` is the
    configuration as read; `entry` the normalized entry of the answers. Without replace, a project that has another
    sheet keeps it and the run is refused; an equal sheet is carried over as it is."""
    if "fields" in existing:
        if field_sheet.check_entry(existing["fields"])[0] == entry:
            return existing["fields"]
        if not replace:
            raise AnswerError(REPLACE)
    return None if is_empty(entry) else entry


def is_empty(entry):
    """"No particular field" and no mark: nothing to keep."""
    return not entry["names"] and not entry["sensitive"]


def print_patterns(existing, restricted, dry):
    """Each pattern of the answers: added (it passed its checks and matches its example), or already there."""
    try:
        _, skipped = merge_patterns(existing, restricted)
    except AnswerError:
        skipped = []
    for item in restricted:
        if item["name"] in skipped:
            print("pattern: %s is already in restricted_patterns; not added" % item["name"])
        else:
            print("pattern: %s: %s   # matches its example; %s" % (item["name"], item["pattern"],
                                                                  "would add" if dry else "added"))


def not_recorded(error):
    """The file a failed approval names: the lock file when the lock is the problem, else the approvals file."""
    if isinstance(error, field_sheet.LockError):
        return "the lock file %s cannot be used (%s)" % (error.path, error)
    return "the approvals file %s cannot be written (%s)" % (field_sheet.approvals_path(), error)


def record_fields(repo, entry, dry):
    """After the configuration file is written: the user's approval of the sheet (none for "No particular field"),
    and that the field question was asked, so the session start does not offer it again."""
    value = field_sheet.fingerprint(entry, repo)
    if dry:
        if not is_empty(entry):
            print("would record: your approval of this field guidance, fingerprint %s" % value[:12])
        print("would record: that the field question was asked, so the session start does not offer it again")
        return
    try:
        if is_empty(entry):
            field_sheet.forget_approval(repo)
        else:
            field_sheet.record_approval(repo, entry, datetime.date.today().isoformat())
            print("recorded: your approval of this field guidance, fingerprint %s" % value[:12])
    except (OSError, storage.StorageError, ValueError) as error:
        raise SystemExit("error: the approval was not recorded: %s. The configuration is written; %s"
                         % (not_recorded(error), "run again to record it once the lock file is fixed or free"
                            if isinstance(error, field_sheet.LockError) else
                            "move the file aside by hand and run again to record it"))
    record_offer(repo)


def approvals_readable():
    """Stop before any write when the approvals cannot be read: the plugin does not replace the file on its own."""
    try:
        field_sheet.read_approvals(create=False)
    except (storage.StorageError, ValueError) as error:
        raise SystemExit("error: the approvals file %s cannot be read (%s); move it aside by hand and run again"
                         % (field_sheet.approvals_path(), error))


def mark_words(marks):
    return field_sheet.mark_words(marks)


def notices(repo, marks, created=False):
    """The fixed lines of a sensitive-material mark. One read of GitHub (is the record's repository private?), only for
    data about people or confidential information, and none for a repository that this run creates private."""
    lines = []
    if ("people" in marks or "confidential" in marks) and not created:
        try:
            value = recortex.gh(["api", "repos/%s" % repo, "--jq", ".private"], timeout=30).strip()
        except recortex.GhError:
            value = ""
        if value == "false":
            lines.append(PUBLIC % (repo, mark_words(marks)))
        elif value != "true":
            lines.append(UNCHECKED % repo)
    if "people" in marks:
        lines.append(NOTE_PEOPLE)
    if marks:
        lines.append(NOTE_ANY)
    return lines


def sheet_lines(entry, pointer, day):
    """The sheet as every subagent gets it, then its checks and words line in a frame of the same kind."""
    if not entry["names"]:
        return []
    out = field_sheet.frame(entry, day, pointer).split("\n")
    out += [CHECKS_BEGIN, CHECKS_NOTE % pointer]
    out += ["- Check %d: %s" % (number, text) for number, text in enumerate(entry["checks"], 1)]
    return out + ["- Project words: %s" % entry["words"], CHECKS_END]


def difference(was, now):
    """[(place, earlier text, text now)] for each place where two checked entries differ."""
    def flat(entry):
        out = {"names": ", ".join(entry["names"]) or "(none)", "sensitive": ", ".join(entry["sensitive"]) or "(none)"}
        for key, value in (entry.get("brief") or {}).items():
            if key == "parts":
                out.update(("brief.parts[%d]" % n, v) for n, v in enumerate(value, 1))
            else:
                out["brief.%s" % key] = value
        out.update(("checks[%d]" % n, v) for n, v in enumerate(entry.get("checks") or [], 1))
        if entry.get("words") is not None:
            out["words"] = entry["words"]
        return out
    before, after = flat(was), flat(now)
    order = list(after) + [place for place in before if place not in after]
    return [(place, before.get(place, "(none)"), after.get(place, "(none)")) for place in order
            if before.get(place) != after.get(place)]


def proposal_lines(entry, earlier=None, changed=False):
    """Text of the project file that this machine has not approved, framed as a proposal for the user to read."""
    out = [PROPOSAL_BEGIN, PROPOSAL_NOTE] + field_sheet.lines(entry)
    out += ["- Check %d: %s" % (number, text) for number, text in enumerate(entry["checks"], 1)]
    out.append("- Project words: %s" % entry["words"])
    if changed:
        out.append("What differs from the sheet approved on this machine:")
        if earlier is None:
            out.append(NO_EARLIER)
        for place, was, now in difference(earlier, entry) if earlier is not None else []:
            out += [place, "was: %s" % was, "is: %s" % now]
    return out + [PROPOSAL_END]


def print_state(config, repo):
    """What `fields show` prints after its first line; returns the state."""
    info = field_sheet.state(config, repo, honor_off=False)
    kind = info["state"]
    text = SHOW[kind] % info["approved"] if "%s" in SHOW[kind] else SHOW[kind]
    print("field guidance: %s." % text)
    value = info["fingerprint"] or (field_sheet.fingerprint(info["entry"], repo) if info["entry"] else None)
    print("fingerprint: %s" % (value[:12] if value else "none"))
    print("sensitive marks: %s" % (", ".join(field_sheet.sensitive_marks(config, repo)) or "none"))
    if field_sheet.off():
        print("note: field guidance is off on this machine (\"field_guidance\": false in %s); subagents get no field "
              "sheet in any project" % recortex.settings_file())
    if kind == "malformed":
        print("refused: %s" % field_sheet.problem_text(info["problems"], limit=len(info["problems"])))
    elif kind == "unreadable":
        print("approvals: %s cannot be read: %s. The file's text is not shown" % (field_sheet.approvals_path(),
                                                                                info["reason"]))
    elif kind == "approved":
        print("\n".join(sheet_lines(info["entry"], field_sheet.pointer_name(config), info["approved"])))
    elif kind in ("new", "changed"):
        earlier = None
        if kind == "changed":
            try:
                earlier = field_sheet.approved_copy(repo, field_sheet.approvals_with(repo))
            except (storage.StorageError, ValueError):
                earlier = None
        print("\n".join(proposal_lines(info["entry"], earlier, kind == "changed")))
        print("To approve it as it stands, after the user has read all of it: setup.py fields approve --fingerprint "
              "%s --yes" % info["fingerprint"])  # all 64 characters: the session copies this line
    return info


def fields_set(config, repo, answers_path, dry, replace):
    try:
        with open(answers_path, encoding="utf-8") as handle:
            answers = json.load(handle)
    except (OSError, ValueError) as error:
        sys.exit("error: %s" % error)
    if not isinstance(answers, dict) or "fields" not in answers:
        sys.exit("error: the answers file has no \"fields\" entry")
    try:
        entry, restricted = check_field_answers(answers["fields"])
    except AnswerError as error:
        sys.exit("error: %s" % error)
    path = config["_path"]
    base = os.path.dirname(os.path.abspath(path))
    refuse_unsafe(base, path)
    approvals_readable()
    try:
        data = json.loads(recortex.read_text(path))  # every key as the file has it; only two are changed
    except ValueError as error:
        sys.exit("error: %s is not valid JSON (%s); fix it by hand" % (path, error))
    if not isinstance(data, dict) or data.get("repo") != repo:
        sys.exit("error: %s changed while it was read; run again" % path)
    try:
        value = merge_fields(data, entry, replace)
        patterns, _ = merge_patterns(data.get("restricted_patterns"), restricted)
    except AnswerError as error:
        sys.exit("error: %s" % error)
    if "fields" in data and value is not data["fields"]:  # only with --replace: the one place setup replaces
        print("current field sheet:")
        print_state(config, repo)
        print("new field sheet:")
    if is_empty(entry):
        print("field guidance: none (no particular field and no sensitive mark); no fields entry is kept")
    elif not entry["names"]:
        print("field guidance: no field sheet; sensitive marks only")
    print("\n".join(sheet_lines(entry, field_sheet.pointer_name(config), datetime.date.today().isoformat())))
    print("sensitive marks: %s" % (", ".join(entry["sensitive"]) or "none"))
    print_patterns(data.get("restricted_patterns"), restricted, dry)
    if not is_empty(entry):
        print("fingerprint: %s" % field_sheet.fingerprint(entry, repo)[:12])
    for line in notices(repo, entry["sensitive"]):
        print(line)
    updated = dict(data)  # the same keys in the same order; only these two change
    if value is None:
        updated.pop("fields", None)
    else:
        updated["fields"] = value
    if restricted:
        updated["restricted_patterns"] = patterns
    if updated != data:
        write_text(path, json.dumps(updated, indent=2) + "\n", dry, "the fields entry%s" % (
            " and restricted patterns" if restricted else ""), base)
    else:
        print("unchanged: %s   # the same field sheet is already there" % path)
    record_fields(repo, entry, dry)
    if dry:
        print("dry run: nothing was written")
    else:
        print("field guidance set and approved" if not is_empty(entry) else
              "field guidance removed: no fields entry, and no approval is kept")
    return 0


def record_offer(repo):
    """The hooks' offer to set the field is not shown again once the question was asked."""
    try:
        field_sheet.mark_offered(repo)
    except (OSError, storage.StorageError) as error:
        print("not recorded: that the field question was asked, because the local storage was refused: %s" % error)


def fields_approve(config, repo, prefix, yes):
    prefix = (prefix or "").lower()
    if not re.fullmatch(r"[0-9a-f]{12,64}", prefix):
        sys.exit("error: --fingerprint takes 12 to 64 characters of the fingerprint that setup.py fields show printed")
    if not yes:
        sys.exit("error: pass --yes once the user has read the sheet that setup.py fields show printed and approved it")
    info = field_sheet.state(config, repo, honor_off=False)
    kind = info["state"]
    if kind == "approved":
        print("approved already: the field sheet was approved on this machine on %s" % info["approved"])
        return 0
    if kind == "malformed":
        sys.exit("error: the fields entry does not have the form the plugin accepts, so it cannot be approved: %s"
                 % field_sheet.problem_text(info["problems"]))
    if kind == "unreadable":
        sys.exit("error: the approvals file %s cannot be read (%s); move it aside by hand and run again"
                 % (field_sheet.approvals_path(), info["reason"]))
    if kind == "none":
        sys.exit("error: there is no field sheet in the configuration file to approve")
    if kind == "mark":
        print("nothing to approve: this project carries sensitive marks and no field sheet")
        return 0
    if not info["fingerprint"].startswith(prefix):
        sys.exit("error: the sheet in the project file is not the one that was shown; run setup.py fields show again")
    try:
        field_sheet.record_approval(repo, info["entry"], datetime.date.today().isoformat())
    except (OSError, storage.StorageError, ValueError) as error:
        sys.exit("error: the approval was not recorded: %s" % not_recorded(error))
    print("recorded: your approval of this field guidance, fingerprint %s" % info["fingerprint"][:12])
    print("\n".join(sheet_lines(info["entry"], field_sheet.pointer_name(config), datetime.date.today().isoformat())))
    return 0


def fields_command(args):
    try:
        config = recortex.load_config()  # read once: what is checked, fingerprinted and shown is this object
    except (OSError, ValueError) as error:
        sys.exit("error: the configuration file cannot be read (%s)" % error)
    repo = config.get("repo")
    if not config.get("_path") or not recortex.valid_repo(repo):
        sys.exit("error: this project is not set up yet (no configuration file with a valid repo at %s); run setup.py "
                 "apply first" % recortex.project_root())
    print(recortex.describe(config, repo))
    if args.action == "show":
        print_state(config, repo)
        return 0
    if args.action == "set":
        return fields_set(config, repo, args.answers, args.dry_run, args.replace)
    return fields_approve(config, repo, args.fingerprint, args.yes)


# ---------------------------------------------------------------- drafts

def deliverable_titles(answers):
    titles = []
    for item in answers["deliverables"]:
        title = item["name"] if item["kind"] == "other" else KINDS[item["kind"]][1]
        titles.append((title, profile_entry(item)))
    return titles


def hub_body(answers, config):
    zone = config.get("timezone") or "local time with the zone abbreviation, for example 2026-03-04 14:10 PST"
    deliverables = deliverable_titles(answers)
    rule = ("The body of every issue that carries a deliverable-section label is the text for that section of the "
            "deliverable: the current version only, in the deliverable's voice, ready to copy. Whoever assembles the "
            "deliverable reads those bodies and nothing else. Changing what the deliverable says is an edit of the body; "
            "a result that leaves the deliverable leaves the body, and the section label comes off with it. Comments are "
            "the history (dated results, decisions with the decider's words, failures and corrections) and never "
            "deliverable text. Work in development lives in issues labeled `active`, which carry no section labels; its "
            "result enters a body once it is adopted.")
    lines = ["## Deliverable text", "", rule, "", "## Resume here", "",
             "The current resume block is the newest comment on this issue that carries the resume markers and was posted "
             "by an account the reader has allowed, as found by `resume_block.py show` from the recortex skill. A "
             "comment headed \"Resume block\" from anyone else is not it. A new one is posted at the end of every working "
             "stretch; resume comments are never edited, and the older ones stay as history.", "",
             "## Project", "", answers["project"] or "The record of %s." % answers["repo"],
             "Decisions are made by %s." % (", ".join("`%s`" % m if m.startswith("@") else m
                                                      for m in answers["decision_makers"]) or "the owner"), "",
             "Times in this tracker are %s. Times copied from GitHub or other tools in UTC are converted before they are "
             "written here." % zone, "", "## Map", "", "```mermaid", "flowchart LR"]
    for number, (title, entry) in enumerate(deliverables, 1):
        lines.append('  D%d["%s"]' % (number, re.sub(r"[()]", "", title)))
        for index, (label, heading) in enumerate(recortex.section_labels([entry]), 1):
            lines.append('  D%d --> D%dS%d["%s"]' % (number, number, index, re.sub(r"[()]", "", heading)))
    lines += ["```", "", "## Deliverable sections", "",
              "The issues whose bodies make up each section, sections in the order of each deliverable. Numbers are not "
              "repeated here; they live in the bodies, each with its source.", ""]
    for title, entry in deliverables:
        lines += ["### %s" % title, "", "| Section | Issue | What its body covers |", "|---|---|---|"]
        for label, heading in recortex.section_labels([entry]):
            lines.append("| %s | none yet | `%s` |" % (heading, label))
        lines.append("")
    lines += ["## Active issues", "", "| Issue | Work in development | Destination topic |", "|---|---|---|",
              "| None yet | | |", "", "## Pull requests", "", "| Pull request | Merged | What it changed | Topic |",
              "|---|---|---|---|", "| None yet | | | |", "", "## Labels", "", "| Label | Meaning |", "|---|---|",
              "| `hub` | This issue |",
              "| `final-record` | Topic issue: the body is current deliverable text, the comments are its history |",
              "| `active` | Work in development; names its destination topic; carries no section labels |",
              "| `triage` | Opened by hand; not yet placed in the record |",
              "| `type:` labels | What an issue opened by hand is: task, bug, question, docs, idea, data, decision needed, "
              "duplicate, out of scope |"]
    for title, entry in deliverables:
        labels = [label for label, _ in recortex.section_labels([entry])]
        lines.append("| %s | The body is %s text for that section |" % (", ".join("`%s`" % l for l in labels), title.lower()))
    lines += ["", "## Older tracker", "", "None. The record starts here.", "", "## Recording rules", "",
              "1. Every finished experiment, decision, failure and merged change becomes a dated comment on its topic "
              "issue, written from the comment templates.",
              "2. A decision is quoted word for word with date, time and zone, in a decision comment.",
              "3. Every number carries its denominator, is checked against its source before posting, and in a body has "
              "its file, table, commit or tag under \"Sources\".",
              "4. When what the deliverable says changes, the topic's body is edited in place, with no folded old text, "
              "and the event comment that caused the change is posted. Section labels follow the body's headings.",
              "5. Work in development gets an issue labeled `active` that names its destination topic.",
              "6. Every issue and pull request is labeled when opened; an issue opened by hand is triaged.",
              "7. References outside this repository are written as `owner/repo#N` or a full URL.",
              "8. A new resume comment is posted on this issue at the end of every working stretch. Resume comments are "
              "never edited.", "9. Secrets and tokens are never posted."]
    return "\n".join(lines) + "\n"


def resume_comment(answers):
    stamp = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    if not re.fullmatch(lint_drafts.STAMP, stamp):
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    actions = "| Draft the first topic issues | the owner and the session | this hub |\n"
    if answers["catch_up"]:
        actions += "| Catch up on earlier work: catchup.py inventory | the session | this hub |\n"
    return ("### Resume block: %s\n\n%s\n**Last updated:** %s by the setup\n**Current focus:** Setting up the record.\n"
            "**Active issues:** none\n**Last decisions:** none\n**Next actions:**\n\n| Action | Owner | Where |\n"
            "|---|---|---|\n%s\n**Holds:** none\n%s\n" % (stamp, recortex.RESUME_START, stamp, actions, recortex.RESUME_END))


def snippet(config):
    text = recortex.read_text(os.path.join(TEMPLATES, "claude-md-snippet.md"))
    hub = config.get("hub")
    text = text.replace("{{owner/repo}}", config["repo"]).replace("{{N}}", str(hub) if hub else "(see .recortex.json)")
    text = text.replace("{{zone}}", config.get("timezone") or "the zone named in the hub")
    return text.replace('{{project rule, for example "never post participant identifiers"}}',
                        "the project's restricted patterns in .recortex.json are never posted")


# ---------------------------------------------------------------- apply

def run(command, dry, what, cwd=None):
    shown = " ".join(command)
    if dry:
        print("would run: %s   # %s" % (shown, what))
        return ""
    print("run: %s   # %s" % (shown, what))
    result = subprocess.run(command, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise SystemExit("error: %s failed:\n%s%s" % (what, result.stdout[-2000:], result.stderr[-2000:]))
    return result.stdout


def unsafe_part(base, path):
    """The first of the folders from `base` down to `path`, and `path` itself, that is a symlink or not the kind of
    file expected, or None. `base` itself may be a symlink (a project folder reached through one)."""
    base, path = os.path.abspath(base), os.path.abspath(path)
    relative = os.path.relpath(path, base)
    if relative == os.curdir or relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return path
    current = base
    parts = relative.split(os.sep)
    for number, part in enumerate(parts):
        current = os.path.join(current, part)
        if os.path.islink(current):
            return current
        last = number == len(parts) - 1
        if os.path.lexists(current) and not (os.path.isfile(current) if last else os.path.isdir(current)):
            return current
    return None


def refuse_unsafe(base, path, hint=""):
    found = unsafe_part(base, path)
    if found:
        raise SystemExit("error: not written: %s is a symlink, or not a plain file or folder; setup.py writes only plain "
                         "files in plain folders. Nothing was changed by this step%s" % (found, hint))


def outside(path, base):
    """True when `path` is not `base` or a path below it (compares whole path components)."""
    relative = os.path.relpath(os.path.abspath(path), os.path.abspath(base))
    return relative == os.pardir or relative.startswith(os.pardir + os.sep)


def new_file_mode():
    """The mode a new file gets from the user's umask: 0o666 without the umask bits."""
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


def write_text(path, text, dry, what, base):
    """Write a project file: no symlink on the way, a fresh temporary file in the same folder, then a move. An
    existing file keeps its mode; a new one gets the mode the user's umask gives."""
    refuse_unsafe(base, path)
    if dry:
        print("would write: %s   # %s, %d characters" % (path, what, len(text)))
        return
    folder = os.path.dirname(os.path.abspath(path))
    current = os.path.abspath(base)
    for part in os.path.relpath(folder, current).split(os.sep):
        if part in (os.curdir, ""):
            continue
        current = os.path.join(current, part)
        if not os.path.lexists(current):
            os.mkdir(current)
    refuse_unsafe(base, path)
    mode = stat.S_IMODE(os.lstat(path).st_mode) if os.path.lexists(path) else new_file_mode()
    descriptor, temporary = tempfile.mkstemp(dir=folder, prefix=".part-")
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        refuse_unsafe(base, path)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)  # only the temporary file this call made
        raise
    print("wrote: %s   # %s" % (path, what))


def add_trusted(repo):
    """Add a repository to the user's trusted list through a temporary file; the file keeps its mode. The list is
    the one recortex.trusted_target() names: an existing list of the earlier name is added to, not copied."""
    path = recortex.trusted_target()
    if os.path.islink(path) or os.path.lexists(path) and not os.path.isfile(path):
        raise SystemExit("error: %s is a symlink or not a plain file; add %s to it by hand" % (path, repo))
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, mode=0o700, exist_ok=True)
    current = recortex.read_text(path) if os.path.isfile(path) else ""
    if current and not current.endswith("\n"):
        current += "\n"
    mode = stat.S_IMODE(os.lstat(path).st_mode) if os.path.isfile(path) else 0o600  # a new list is private
    descriptor, temporary = tempfile.mkstemp(dir=folder, prefix=".part-")
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(current + repo + "\n")
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)  # only the temporary file this call made
        raise
    return path


def config_target(root):
    """The configuration file `apply` writes: the file under its name from before the rename when only that one is
    there, so a rerun in an older project adds to it and makes no second file; else .recortex.json."""
    new, old = os.path.join(root, names.CONFIG_NAME), os.path.join(root, names.OLD_CONFIG_NAME)
    return old if os.path.lexists(old) and not os.path.lexists(new) else new


def board_step(board, root, python, dry):
    """The optional board: board.py setup, then the first fill. A board that cannot be set up never stops the rest of
    the setup, which is done by now: the scope that is missing, or any other reason, is said in the fixed lines that
    board.py prints, with the command to run again."""
    command = [python, os.path.join(HERE, "board.py"), "setup", "--mode", board["mode"]]
    if board.get("number"):
        command += ["--number", str(board["number"])]
    if dry:
        run(command + ["--dry-run"], True, "create or link the board of the work")
        run([python, os.path.join(HERE, "board.py"), "apply"], True, "fill the board from the record")
        return
    print("run: %s   # create or link the private board of the work" % " ".join(command + ["--yes"]))
    result = subprocess.run(command + ["--yes"], capture_output=True, text=True, cwd=root)
    print(result.stdout.rstrip())
    if result.stderr.strip():
        print(result.stderr.strip()[-1000:])
    if result.returncode != 0:
        made = re.search(r"^created: a new board, number ([0-9]+),", result.stdout, re.M)
        again = "existing --number %s" % made.group(1) if made else board["mode"] + (
            " --number %d" % board["number"] if board.get("number") else "")
        print("board: not set up; the rest of the setup is done. Run `board.py setup --mode %s --yes` when the "
              "reason above is dealt with." % again)
        return
    print("run: board.py apply   # fill the board from the record")
    filled = subprocess.run([python, os.path.join(HERE, "board.py"), "apply"], capture_output=True, text=True, cwd=root)
    print(filled.stdout.rstrip())
    if filled.stderr.strip():
        print(filled.stderr.strip()[-1000:])
    if filled.returncode != 0:
        print("board: not filled yet; run `board.py apply` when the reason above is dealt with.")


def apply(answers, root, dry, drafts):
    config_path = config_target(root)
    drafts_base = os.path.dirname(drafts) if outside(drafts, root) else root
    workflow = os.path.join(root, ".github", "workflows", names.WORKFLOW)
    labeler = answers["triage_workflow"] and answers["record"] == "this"
    # A labeler installed under its name from before the rename does the same work: it is kept, and no second one is
    # written beside it.
    installed = os.path.join(root, ".github", "workflows", names.OLD_WORKFLOW)
    keep_installed = labeler and os.path.isfile(installed)
    claude_md = os.path.join(root, "CLAUDE.md")
    # Every file this run may write is checked before anything is changed.
    refuse_unsafe(root, config_path)
    for name in ("hub.md", "resume-1.md", "manifest.json"):
        refuse_unsafe(drafts_base, os.path.join(drafts, name), "; pass --drafts <folder> to keep the hub drafts in a "
                      "plain folder elsewhere")
    if labeler and not keep_installed:
        refuse_unsafe(root, workflow)
    if answers["claude_md"]:
        refuse_unsafe(root, claude_md)
    existing = {}
    if os.path.isfile(config_path):
        try:
            existing = json.loads(recortex.read_text(config_path))
        except ValueError as error:
            raise SystemExit("error: %s is not valid JSON (%s); fix it by hand" % (config_path, error))
        if not isinstance(existing, dict):
            raise SystemExit("error: %s is not a JSON object; fix it by hand" % config_path)
        if isinstance(existing.get("repo"), str) and existing["repo"].lower() != answers["repo"].lower():
            raise SystemExit("error: %s already names %s; the answers name %s. Change the setup by hand if the record "
                             "moved" % (config_path, existing["repo"], answers["repo"]))
    try:
        config, added = new_config(existing, answers, os.path.basename(config_path))
    except AnswerError as error:
        raise SystemExit("error: %s" % error)
    if answers["fields"] is not None:
        approvals_readable()  # before anything is written
    print("profiles: %s" % ", ".join(recortex.profile_names(config["profiles"])))
    if existing:
        kept = [n for n in recortex.profile_names(existing.get("profiles") or [])]
        print("kept from the existing setup: %s" % (", ".join(kept) or "nothing"))
        print("added: %s" % (", ".join(added) or "nothing"))
    if answers["fields"] is not None:
        print_patterns(existing.get("restricted_patterns"), answers["restricted"], dry)
        created = answers["record"] == "new" and not existing.get("repo")
        for line in notices(answers["repo"], answers["fields"]["sensitive"], created):
            print(line)
    python = sys.executable
    scripts = HERE

    if answers["record"] == "new" and not existing.get("repo"):
        run(["gh", "repo", "create", answers["repo"], "--private", "--description", "Project record"], dry,
            "create the private repository that holds the record")

    labels = setup_labels.wanted_labels(config["profiles"], config["topics"])  # checked before anything is written
    print("config (%s):" % os.path.basename(config_path))
    shown = dict(config)
    if "fields" in shown and not (answers["fields"] is not None and "fields" not in existing):
        # a sheet this run does not write is not printed: it may be text nobody approved on this machine
        shown["fields"] = "(field guidance: %s; see setup.py fields show)" % field_sheet.state(
            {"fields": config["fields"]}, answers["repo"])["state"]
    print(json.dumps(shown, indent=2))
    write_text(config_path, json.dumps(config, indent=2) + "\n", dry, "the project's setup", root)
    if answers["fields"] is not None:
        record_fields(answers["repo"], answers["fields"], dry)

    print("labels: %d (%s)" % (len(labels), ", ".join(l["name"] for l in labels)))
    run([python, os.path.join(scripts, "setup_labels.py"), "--repo", answers["repo"]], dry,
        "create the labels that are missing (%d wanted)" % len(labels), cwd=root)

    if not existing.get("hub"):
        hub = hub_body(answers, config)
        resume = resume_comment(answers)
        findings = lint_drafts.lint_text(hub, kind="hub", config=config) + \
            lint_drafts.lint_text(resume, kind="resume", config=config)
        errors = [str(f) for f in findings if f.level == "error"]
        if errors:
            raise SystemExit("error: the hub draft does not pass lint:\n  " + "\n  ".join(errors))
        manifest = {"repo": answers["repo"],
                    "issues": [{"key": "hub", "kind": "hub", "title": "Project record hub", "labels": ["hub"],
                                "body_file": "hub.md", "pin": True}],
                    "comments": [{"issue": "NEW-hub", "kind": "resume", "body_file": "resume-1.md"}]}
        print("hub draft (%d characters):" % len(hub))
        print("\n".join("  | " + line for line in hub.rstrip("\n").split("\n")))
        write_text(os.path.join(drafts, "hub.md"), hub, dry, "the hub", drafts_base)
        write_text(os.path.join(drafts, "resume-1.md"), resume, dry, "the first resume comment", drafts_base)
        write_text(os.path.join(drafts, "manifest.json"), json.dumps(manifest, indent=2) + "\n", dry, "the manifest",
                   drafts_base)
        out = run([python, os.path.join(scripts, "create_issues.py"), "--manifest", os.path.join(drafts, "manifest.json"),
                   "--repo", answers["repo"]], dry, "create and pin the hub, post the first resume comment", cwd=root)
        if not dry:
            match = re.search(r"NEW-hub -> #([0-9]+)", out)
            if not match:
                raise SystemExit("error: the hub number was not found in the output of create_issues.py")
            config["hub"] = int(match.group(1))
            write_text(config_path, json.dumps(config, indent=2) + "\n", dry, "the hub number", root)
    else:
        print("hub: #%d exists; add rows for new sections to its deliverable tables with post_verified.py body" %
              existing["hub"])
        for title, entry in deliverable_titles(answers):
            for label, heading in recortex.section_labels([entry]):
                print("  | %s | none yet | `%s` |   (%s)" % (heading, label, title))

    if answers["trust"]:
        trusted = recortex.trusted_target()
        present = answers["repo"].lower() in recortex.trusted_repos()
        if present:
            print("trusted: %s is already in %s" % (answers["repo"], ", ".join(recortex.trusted_sources(answers["repo"]))))
        elif dry:
            print("would add: %s to %s   # the user agreed" % (answers["repo"], trusted))
        else:
            print("added: %s to %s" % (answers["repo"], add_trusted(answers["repo"])))
    else:
        print("trusted: not changed; the hooks stay silent until the user adds %s to %s" % (answers["repo"],
                                                                                          recortex.trusted_target()))

    if answers["triage_workflow"] and answers["record"] != "this":
        print("not written: the triage labeler runs in the repository that holds the issues (%s), not in this project; "
              "commit templates/recortex-triage.yml there as .github/workflows/recortex-triage.yml" % answers["repo"])
    elif keep_installed:
        print("kept: %s already labels new issues; no second labeler is written" % installed)
    elif answers["triage_workflow"]:
        text = recortex.read_text(os.path.join(TEMPLATES, "recortex-triage.yml"))
        if os.path.exists(workflow) and recortex.read_text(workflow) != text:
            print("not written: %s exists with other content; compare it with templates/recortex-triage.yml" % workflow)
        else:
            write_text(workflow, text, dry, "the triage labeler workflow; commit it to turn it on", root)
    if answers["claude_md"]:
        current = recortex.read_text(claude_md) if os.path.isfile(claude_md) else ""
        if "## Project record" in current:
            print("not written: CLAUDE.md already has a Project record section")
            section = current.split("## Project record", 1)[1].split("\n## ", 1)[0]
            if names.OLD_NAME in section:
                print("note: that section names the %s skill, which is now called %s; change the name there by hand"
                      % (names.OLD_NAME, names.NAME))
        else:
            write_text(claude_md, (current.rstrip("\n") + "\n\n" if current else "") + snippet(config), dry,
                       "the CLAUDE.md snippet", root)

    run([python, os.path.join(scripts, "index.py"), "--repo", answers["repo"], "sync"], dry,
        "build the local index in the foreground, so the first session has a brief", cwd=root)
    if answers["board"] and existing.get("board"):
        print("board: this project already has a board entry (%s/%s); no second board is made. To change it, edit "
              "the entry by hand and run board.py setup --mode existing --number N --yes"
              % (str(existing["board"].get("owner"))[:40] if isinstance(existing["board"], dict) else "?",
                 str(existing["board"].get("number"))[:10] if isinstance(existing["board"], dict) else "?"))
    elif answers["board"]:
        board_step(answers["board"], root, python, dry)
    present = [item["kind"] for item in detect(root)]
    if dry:
        print("would record as known: %s   # the hooks suggest only deliverables that appear later" %
              (", ".join(present) or "nothing"))
    else:
        try:
            remember_told(answers["repo"], present)
            print("recorded as known: %s" % (", ".join(present) or "nothing"))
        except (OSError, storage.StorageError) as error:
            print("not recorded: the kinds present now (%s), because the local storage was refused: %s. The hooks may "
                  "suggest them once; hooks.py --doctor shows the storage" % (", ".join(present), error))
    if answers["catch_up"]:
        print("next: catch up on earlier work (references/catch-up.md), starting with catchup.py inventory%s"
              % (" --transcripts" if answers["read_transcripts"] else ""))
    print("dry run: nothing was written" if dry else "setup applied")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("detect", help="suggest deliverables and fields from file and folder names (read-only, local)")
    p.add_argument("--root", help="project folder (default: the project root)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--fields", action="store_true", help="the field suggestions only")
    p = sub.add_parser("apply", help="apply confirmed answers")
    p.add_argument("--answers", required=True, help="answers file (JSON)")
    p.add_argument("--root", help="project folder (default: the project root)")
    p.add_argument("--drafts", help="folder for the hub drafts (default: <root>/.tmp/recortex-setup)")
    p.add_argument("--dry-run", action="store_true", help="print every change; write nothing")
    p.add_argument("--yes", action="store_true", help="the user confirmed the summary; make the changes")
    p = sub.add_parser("fields", help="show, set or approve the field sheet (local; nothing on GitHub)")
    actions = p.add_subparsers(dest="action", required=True)
    actions.add_parser("show", help="the state of the field sheet, and the sheet (read-only)")
    a = actions.add_parser("set", help="write the fields entry of an answers file and record the user's approval")
    a.add_argument("--answers", required=True, help="answers file (JSON) with a \"fields\" entry")
    mode = a.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="print every change; write nothing")
    mode.add_argument("--yes", action="store_true", help="the user approved the sheet; make the changes")
    a.add_argument("--replace", action="store_true", help="replace the sheet the project has")
    a = actions.add_parser("approve", help="record the approval of the sheet as it stands in the project file")
    a.add_argument("--fingerprint", required=True, help="the fingerprint that fields show printed (12 to 64 hex)")
    a.add_argument("--yes", action="store_true", help="the user read the sheet and approved it")
    args = parser.parse_args(argv)
    if args.command == "fields":
        return fields_command(args)
    root = os.path.abspath(args.root or recortex.project_root())
    if args.command == "detect":
        fields = detect_fields(root)
        if args.fields:
            if args.json:
                print(json.dumps(fields, indent=1))
            else:
                print("\n".join(field_lines(fields)))
            return 0
        found = detect(root)
        if args.json:
            print(json.dumps(found, indent=1))  # the deliverables only, as before: the hooks' signal line reads it
            return 0
        if not found:
            print("no suggestion: nothing in this folder points to a kind of deliverable; ask the user")
        for item in found:
            print("suggested: %s (profile %s), because %s" % (item["kind"], item["profile"], ", ".join(item["evidence"])))
        print("\n".join(field_lines(fields)))
        return 0
    try:
        with open(args.answers, encoding="utf-8") as handle:
            answers = check_answers(json.load(handle))
    except (OSError, ValueError) as error:
        sys.exit("error: %s" % error)
    if not args.dry_run and not args.yes:
        sys.exit("error: pass --dry-run to see the changes, or --yes once the user has confirmed them")
    return apply(answers, root, args.dry_run, os.path.abspath(args.drafts or os.path.join(root, ".tmp",
                                                                                           "recortex-setup")))


if __name__ == "__main__":
    sys.exit(main())
