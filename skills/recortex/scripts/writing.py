"""The writing mode of this machine, per repository: review, autonomous or off.

Python 3 standard library only. The mode is the user's own. It is kept in the private folder of the plugin's
configuration top (storage.config_top), in writing-modes.json, written through storage.py under a lock exactly as the
field approvals are, and the project file holds nothing of it: a pulled .recortex.json cannot switch a machine to
autonomous writing. No entry means "not chosen", and the plugin writes nothing. A file that cannot be read (a symlink,
loose permissions, not JSON, not of the form) counts as off, and the hooks say so in one fixed line that points to
hooks.py --doctor. The hooks and write.py only read it; setup.py writing set is the one writer, and the session runs
it only on the user's own answer in the conversation.

The mode is also the consent to keep the versions of the plugin's own manuscript in the record's repository
(references/writing.md has the lines the user is shown).
"""

import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import field_sheet  # noqa: E402
import names  # noqa: E402
import recortex  # noqa: E402
import storage  # noqa: E402

FORM = 1
MODES = ("review", "autonomous", "off")
FILE_CAP = 20000
LOCK = "writing-modes.lock"
DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
NOT_READABLE = {"storage": "the folder or the file is not safe to read", "form": "the file is not in the form the plugin "
                "writes", "large": "the file is larger than the plugin writes"}


def path():
    return os.path.join(storage.config_top(), storage.PRIVATE, names.WRITING_MODES)


def empty():
    return {"form": FORM, "modes": {}}


def real_day(value):
    if not isinstance(value, str) or not DAY.fullmatch(value):
        return False
    try:
        datetime.datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def load(create=False):
    """The validated file: {"form": 1, "modes": {"owner/name": {"mode": ..., "set": day}}}. Raises ValueError with a
    key of NOT_READABLE for a file that does not fit, and storage.StorageError for refused storage. With create=False
    nothing is made or tightened (the hooks)."""
    private = storage.folder(storage.config_top(), create=create, tighten=create)
    if private is None:
        return empty()
    target = os.path.join(private, names.WRITING_MODES)
    info = storage.check_path(target, "file")
    if not info:
        return empty()
    if info.st_size > FILE_CAP:
        raise ValueError("large")
    try:
        data = json.loads(storage.read_file(target) or "")
    except (ValueError, RecursionError):
        raise ValueError("form")
    if not isinstance(data, dict) or set(data) != {"form", "modes"} or type(data["form"]) is not int \
            or data["form"] != FORM or not isinstance(data["modes"], dict):
        raise ValueError("form")
    for repo, entry in data["modes"].items():
        if not recortex.valid_repo(repo) or not isinstance(entry, dict) or set(entry) != {"mode", "set"} \
                or entry["mode"] not in MODES or not real_day(entry["set"]):
            raise ValueError("form")
    return data


def read(repo):
    """{"state": "none" | "chosen" | "unreadable", "mode": one of MODES (off unless chosen), "day": the day it was
    set or "", "why": a key of NOT_READABLE or ""} for `repo` on this machine. Read only."""
    try:
        data = load()
    except storage.StorageError:
        return {"state": "unreadable", "mode": "off", "day": "", "why": "storage"}
    except ValueError as error:
        return {"state": "unreadable", "mode": "off", "day": "", "why": str(error) if str(error) in NOT_READABLE
                else "form"}
    entry = data["modes"].get(repo.lower())
    if entry is None:
        return {"state": "none", "mode": "off", "day": "", "why": ""}
    return {"state": "chosen", "mode": entry["mode"], "day": entry["set"], "why": ""}


def mode(repo):
    """The mode in force: review, autonomous or off; off when none was chosen and when the file cannot be read."""
    return read(repo)["mode"]


def record(repo, new_mode, day=None):
    """Record the user's choice for `repo` on this machine; other repositories' entries stay. A file that cannot be
    read stops it and is never replaced."""
    if new_mode not in MODES:
        raise ValueError("form")
    day = day or datetime.date.today().isoformat()
    with field_sheet.approvals_lock(name=LOCK):
        data = load(create=True)
        data["modes"][repo.lower()] = {"mode": new_mode, "set": day}
        private = storage.folder(storage.config_top())
        storage.write_file(os.path.join(private, names.WRITING_MODES), json.dumps(data, indent=1, sort_keys=True) + "\n")
    return day


def sentence(repo, found=None):
    """One fixed sentence for what is chosen here."""
    found = found or read(repo)
    if found["state"] == "unreadable":
        return ("writing: the setting of this machine cannot be read (%s), so recortex writes nothing. Run hooks.py "
                "--doctor." % NOT_READABLE[found["why"]])
    if found["state"] == "none":
        return "writing: no mode has been chosen for %s on this machine, so recortex writes nothing." % repo
    return "writing: the mode for %s on this machine is %s (chosen on %s)." % (repo, found["mode"], found["day"])


def consent_lines(repo):
    """The plain lines that end the mode question and are printed by setup.py writing set: what review or autonomous also
    does (it keeps the versions). Fixed wording."""
    return ["With review or autonomous, recortex also keeps every version of the manuscript for you in %s, and shows "
            "them on the mind page. Nothing is built or run there." % repo]