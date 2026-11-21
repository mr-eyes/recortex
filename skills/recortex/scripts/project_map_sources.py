"""Machine-approved project files whose headings may inform a map proposal."""

import hashlib
import json
import os
import re
import stat
import subprocess

import recortex
import storage

MAX_FILES = 8
MAX_BYTES = 128 * 1024
PART = re.compile(r"[\w][\w .-]{0,100}")
EXTENSIONS = (".md", ".markdown", ".rst", ".txt")
SUGGESTED_NAMES = {"readme", "overview", "outline", "roadmap", "architecture", "project-plan", "research-plan"}
ATX = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$")
UNDERLINE = re.compile(r"^(={3,}|-{3,})\s*$")


def valid(path):
    if not isinstance(path, str) or len(path) > 240 or not path.lower().endswith(EXTENSIONS):
        return False
    return all(PART.fullmatch(part) for part in path.split("/"))


def check_selection(paths):
    if not isinstance(paths, list) or len(paths) > MAX_FILES or not all(valid(p) for p in paths):
        raise ValueError("choose up to eight relative Markdown, reStructuredText, or text file paths")
    return sorted(set(paths))


def tracked(root, paths):
    if not paths:
        return set()
    try:
        result = subprocess.run(["git", "--literal-pathspecs", "-C", root, "ls-files", "--cached", "-z", "--"] + paths,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return set()
    return set(paths).intersection(os.fsdecode(result.stdout).split("\0"))


def candidates(root):
    import setup
    names = [path for path, _ in setup.names_in(root)
             if valid(path) and os.path.splitext(os.path.basename(path))[0].lower() in SUGGESTED_NAMES]
    return sorted(tracked(root, names), key=lambda path: (path.count("/"), path.lower()))[:MAX_FILES]


def scope_path(root, repo, create=False):
    if not recortex.valid_repo(repo):
        raise ValueError("the project needs a valid record repository")
    folder = storage.folder(storage.config_top(), "map-sources", storage.repo_folder(repo.lower()),
                            create=create, tighten=create)
    if folder:
        ident = hashlib.sha256(os.fsencode(os.path.realpath(root))).hexdigest()
        return os.path.join(folder, ident + ".json")


def selection(root, repo):
    path = scope_path(root, repo)
    if not path:
        return []
    info = storage.check_path(path, "file")
    if info and info.st_size > 16384:
        raise ValueError("the saved map file selection is too large")
    saved = storage.read_file(path)
    return check_selection(json.loads(saved)) if saved is not None else []


def approve(root, repo, paths):
    paths = check_selection(paths)
    if set(paths) != tracked(root, paths):
        raise ValueError("each selected file must be tracked by this project")
    for path in paths:
        inspect(root, path, contents=False)
    storage.write_file(scope_path(root, repo, create=True), json.dumps(paths) + "\n")


def inspect(root, path, contents):
    """Open each component without following links. Refuse devices, pipes, oversized files, and hard links."""
    if not valid(path):
        raise ValueError("invalid project file path")
    parts = path.split("/")
    if not all(hasattr(os, flag) for flag in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")) or os.open not in os.supports_dir_fd:
        raise ValueError("this platform cannot safely open selected map documents")
    directory = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(os.path.realpath(root), directory)
    try:
        for part in parts[:-1]:
            child = os.open(part, directory, dir_fd=fd)
            os.close(fd)
            fd = child
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        info = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("selected project file must be a regular file")
        with os.fdopen(os.open(parts[-1], flags, dir_fd=fd), "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
                raise ValueError("selected file must be a regular project document of at most 128 KiB")
            if contents:
                data = stream.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES or b"\x00" in data:
                    raise ValueError("selected file must be a text document of at most 128 KiB")
                return data
    finally:
        os.close(fd)


def headings(data):
    found = []
    previous = ""
    fence = ""
    lines = data.decode("utf-8-sig").splitlines()
    front = lines[0].strip() if lines and lines[0].strip() in ("---", "+++") else ""
    comment = False
    for number, line in enumerate(lines):
        if front:
            if number and (line.strip() == front or (front == "---" and line.strip() == "...")):
                front = ""
            continue
        if comment or (not fence and "<!--" in line):
            comment = "-->" not in line.partition("<!--")[2] if not comment else "-->" not in line
            previous = ""
            continue
        mark = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if mark:
            if not fence:
                fence = mark.group(1)
            elif mark.group(1).startswith(fence) and not line[mark.end():].strip():
                fence = ""
            previous = ""
            continue
        if fence:
            continue
        atx = ATX.fullmatch(line)
        if atx:
            level, title = len(atx.group(1)), re.sub(r"\s+#+$", "", atx.group(2))
        elif previous and UNDERLINE.fullmatch(line):
            level, title = (1 if line.startswith("=") else 2), previous
        else:
            previous = line.strip() if line and not line[0].isspace() else ""
            continue
        previous = ""
        if 1 <= len(title) <= 120 and title.isprintable() and "<" not in title and ">" not in title:
            found.append({"level": level, "title": title})
            if len(found) == 32:
                break
    return found


def snapshot(root, repo):
    """A fresh bounded outline. An unsafe scope or unavailable file contributes no evidence."""
    try:
        paths = sorted(tracked(root, selection(root, repo)))
    except (ValueError, OSError, storage.StorageError):
        return []
    result = []
    for path in paths:
        try:
            data = inspect(root, path, contents=True)
            result.append({"path": path, "digest": hashlib.sha256(data).hexdigest(), "headings": headings(data)})
        except (ValueError, OSError):
            continue
    return result
