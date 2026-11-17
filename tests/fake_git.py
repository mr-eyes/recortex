"""The part of the fake gh that answers GitHub's git data, contents and pull request endpoints (used by fake_gh.py).

The fake follows GitHub's REST documentation as it was read for 1.3.0 (the git data API, the contents API with the raw
media type, pull requests). What the documentation does not say is marked below and fixed by a test.

The state lives in the fake repository's JSON file:
  state["git"]   {"refs": {"heads/<branch>" or "tags/<tag>": commit sha}, "commits": {sha: {"tree", "parents", "message"}},
                  "trees": {sha: {path: text}}, "updates": [[ref, sha, force], ...] (what each ref update asked for)}
  state["issues"][n]  a pull request is an issue with "pull_request": true, "head": {"ref", "sha"}, "base": {"ref"},
                  "merged": bool, "merged_by": login or None, "merge_commit_sha"
A request that has a body gets it as JSON on standard input (`gh api --input -`).
Fault switches (environment):
  FAKE_GH_GIT_NO_PUSH=403|404    the account may not write: every write of the git data API is refused with this status;
                                 `api repos/<owner>/<name>` answers permissions.push false (404 versus 403 for a token
                                 without contents permission is not documented; the plugin treats both alike)
  FAKE_GH_BRANCH_RULE=1          a rule protects branches: creating or moving a branch is refused (422 on create, 403 on
                                 move); a tag is created
  FAKE_GH_MOVE_HEAD=once|always  another commit lands on the branch just before an update of it, so that the update is not a
                                 fast forward: 422 "Update is not a fast forward" (observed, not documented), once or every time
  FAKE_GH_MOVE_HEAD_FILES=json   the files ({path: text}) that the other commit of FAKE_GH_MOVE_HEAD also writes, as another
                                 session that kept a version of another topic would have
  FAKE_GH_LOGIN=name             the login that merges a pull request and opens one (default octocat)
"""

import base64
import hashlib
import json
import os
import re
import sys

ME = "octocat"


class Refused(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def sha_of(*parts):
    return hashlib.sha1("\0".join(parts).encode("utf-8")).hexdigest()


def git(state):
    return state.setdefault("git", {"refs": {}, "commits": {}, "trees": {}, "updates": []})


def body_json():
    raw = sys.stdin.read()
    return json.loads(raw) if raw.strip() else {}


def tree_sha(flat):
    return sha_of("tree", *["%s=%s" % (path, flat[path]) for path in sorted(flat)])


def descends(store, new, old):
    """True when commit `new` has commit `old` among its ancestors (or is it)."""
    seen, todo = set(), [new]
    while todo:
        current = todo.pop()
        if current == old:
            return True
        if current in seen or current not in store["commits"]:
            continue
        seen.add(current)
        todo += store["commits"][current]["parents"]
    return False


def writes_refused():
    value = os.environ.get("FAKE_GH_GIT_NO_PUSH")
    if value in ("403", "404"):
        raise Refused(int(value), "Resource not accessible by personal access token" if value == "403" else "Not Found")


def flat_of(store, commit):
    return dict(store["trees"][store["commits"][commit]["tree"]]) if commit in store["commits"] else {}


def resolve_ref(store, ref):
    """The commit a branch, a tag or a commit sha names, or None."""
    if re.fullmatch(r"[0-9a-f]{40}", ref) and ref in store["commits"]:
        return ref
    for prefix in ("heads/", "tags/"):
        if prefix + ref in store["refs"]:
            return store["refs"][prefix + ref]
    return None


def handle(args, path, state, repo, login):
    """Answer the request if it is one of this module's; return True when it printed an answer (the caller saves the
    state), False when the path is not this module's. Raises Refused with GitHub's status."""
    method = (args[args.index("-X") + 1] if "-X" in args else args[args.index("--method") + 1] if "--method" in args
              else "GET").upper()
    base = re.escape(repo)
    store = git(state)
    login = os.environ.get("FAKE_GH_LOGIN", login or ME)

    if re.fullmatch(r"repos/[^/]+/[^/?]+", path):
        jq = args[args.index("--jq") + 1] if "--jq" in args else None
        if jq == ".permissions.push":
            print("false" if os.environ.get("FAKE_GH_GIT_NO_PUSH") in ("403", "404") else "true")
            return True
        if jq == ".default_branch":
            print("main")
            return True
        return False   # `.private` and the object without --jq are fake_gh's own answers

    found = re.fullmatch(r"repos/[^/]+/[^/]+/git/ref/(.+)", path)
    if found and method == "GET":
        sha = store["refs"].get(found.group(1))
        if sha is None:
            raise Refused(404, "Not Found")
        print(json.dumps({"ref": "refs/" + found.group(1), "object": {"type": "commit", "sha": sha}}))
        return True

    if re.fullmatch(r"repos/[^/]+/[^/]+/git/refs", path) and method == "POST":
        writes_refused()
        data = body_json()
        ref, sha = str(data.get("ref", "")), str(data.get("sha", ""))
        match = re.fullmatch(r"refs/((?:heads|tags)/[A-Za-z0-9._/-]+)", ref)
        if not match or sha not in store["commits"]:
            raise Refused(422, "Validation Failed")
        key = match.group(1)
        if key in store["refs"]:
            raise Refused(422, "Reference already exists")
        if key.startswith("heads/") and os.environ.get("FAKE_GH_BRANCH_RULE"):
            raise Refused(422, "Repository rule violations found")
        store["refs"][key] = sha
        store["updates"].append([key, sha, False])
        print(json.dumps({"ref": ref, "object": {"type": "commit", "sha": sha}}))
        return True

    found = re.fullmatch(r"repos/[^/]+/[^/]+/git/refs/(.+)", path)
    if found and method == "PATCH":
        writes_refused()
        data = body_json()
        key, sha, force = found.group(1), str(data.get("sha", "")), bool(data.get("force", False))
        if key not in store["refs"]:
            raise Refused(422, "Reference does not exist")
        if sha not in store["commits"]:
            raise Refused(422, "Object does not exist")
        if key.startswith("heads/") and os.environ.get("FAKE_GH_BRANCH_RULE"):
            raise Refused(403, "Protected branch update failed")
        move = os.environ.get("FAKE_GH_MOVE_HEAD")
        if move and key.startswith("heads/") and (move == "always" or not store.get("moved_once")):
            store["moved_once"] = True  # someone else's commit lands on the branch first
            flat = dict(flat_of(store, store["refs"][key]), **json.loads(os.environ.get("FAKE_GH_MOVE_HEAD_FILES") or "{}"))
            other = {"tree": tree_sha(flat), "parents": [store["refs"][key]], "message": "another commit"}
            store["trees"].setdefault(other["tree"], flat)
            other_sha = sha_of("commit", other["tree"], *other["parents"], other["message"])
            store["commits"][other_sha] = other
            store["refs"][key] = other_sha
        store["updates"].append([key, sha, force])
        if not force and not descends(store, sha, store["refs"][key]):
            raise Refused(422, "Update is not a fast forward")
        store["refs"][key] = sha
        print(json.dumps({"ref": "refs/" + key, "object": {"type": "commit", "sha": sha}}))
        return True

    if re.fullmatch(r"repos/[^/]+/[^/]+/git/trees", path) and method == "POST":
        writes_refused()
        data = body_json()
        flat = dict(store["trees"].get(data.get("base_tree"), {})) if data.get("base_tree") else {}
        if data.get("base_tree") and data["base_tree"] not in store["trees"]:
            raise Refused(422, "Validation Failed")
        for entry in data.get("tree", []):
            name = entry.get("path")
            if not isinstance(name, str) or not name or name.startswith("/") or ".." in name.split("/"):
                raise Refused(422, "Validation Failed")
            if "content" in entry:
                flat[name] = str(entry["content"])
            elif "sha" in entry and entry["sha"] is None:
                flat.pop(name, None)
            else:
                raise Refused(422, "Validation Failed")
        sha = tree_sha(flat)
        store["trees"][sha] = flat
        print(json.dumps({"sha": sha, "truncated": False}))
        return True

    if re.fullmatch(r"repos/[^/]+/[^/]+/git/commits", path) and method == "POST":
        writes_refused()
        data = body_json()
        tree, parents = data.get("tree"), list(data.get("parents") or [])
        if tree not in store["trees"] or any(p not in store["commits"] for p in parents):
            raise Refused(422, "Validation Failed")
        sha = sha_of("commit", tree, *parents, str(data.get("message", "")))
        store["commits"][sha] = {"tree": tree, "parents": parents, "message": str(data.get("message", ""))}
        print(json.dumps({"sha": sha, "tree": {"sha": tree}, "parents": [{"sha": p} for p in parents]}))
        return True

    found = re.fullmatch(r"repos/[^/]+/[^/]+/git/commits/([0-9a-f]{40})", path)
    if found and method == "GET":
        commit = store["commits"].get(found.group(1))
        if commit is None:
            raise Refused(404, "Not Found")
        print(json.dumps({"sha": found.group(1), "tree": {"sha": commit["tree"]}, "message": commit["message"],
                          "parents": [{"sha": p} for p in commit["parents"]]}))
        return True

    found = re.fullmatch(r"repos/[^/]+/[^/]+/git/trees/([0-9a-f]{40})(\?.*)?", path)
    if found and method == "GET":
        flat = store["trees"].get(found.group(1))
        if flat is None:
            raise Refused(404, "Not Found")
        print(json.dumps({"sha": found.group(1), "truncated": False, "tree": [
            {"path": name, "mode": "100644", "type": "blob", "size": len(flat[name].encode("utf-8")),
             "sha": sha_of("blob", flat[name])} for name in sorted(flat)]}))
        return True

    found = re.fullmatch(r"repos/[^/]+/[^/]+/contents/([^?]+)\?ref=([^&]+)", path)
    if found and method == "GET":
        commit = resolve_ref(store, found.group(2))
        if commit is not None:
            flat = flat_of(store, commit)
            if found.group(1) not in flat:
                raise Refused(404, "Not Found")
            data = flat[found.group(1)].encode("utf-8")
            if any("vnd.github.raw" in args[i + 1] for i, a in enumerate(args[:-1]) if a == "-H"):
                sys.stdout.flush()
                sys.stdout.buffer.write(data)
            else:
                print(json.dumps({"type": "file", "encoding": "base64", "size": len(data),
                                  "content": base64.b64encode(data).decode("ascii")}))
            return True
        return False   # a commit the git state does not hold: the earlier answer, from state["files"]

    if re.fullmatch(r"repos/[^/]+/[^/]+/pulls", path) and method == "POST":
        writes_refused()
        data = body_json()
        head, into = str(data.get("head", "")), str(data.get("base", ""))
        if "heads/" + head not in store["refs"] or "heads/" + into not in store["refs"]:
            raise Refused(422, "Validation Failed")
        number = state["next_issue"]
        state["next_issue"] += 1
        state["clock"] = state.get("clock", 0) + 1
        stamp = "%sT%02d:%02d:00Z" % (state.get("date", "2026-04-02"), *divmod(state["clock"], 60))
        state["issues"][str(number)] = {
            "title": str(data.get("title", "")), "body": str(data.get("body", "")), "labels": [], "state": "open",
            "created_at": stamp, "updated_at": stamp, "user": login, "pull_request": True, "merged": False,
            "merged_by": None, "merge_commit_sha": None, "head": {"ref": head, "sha": store["refs"]["heads/" + head]},
            "base": {"ref": into}}
        print(json.dumps({"number": number, "state": "open", "html_url": "https://github.com/%s/pull/%d" % (repo, number)}))
        return True

    found = re.fullmatch(r"repos/[^/]+/[^/]+/pulls/([0-9]+)(/merge)?", path)
    if found:
        number = found.group(1)
        issue = state["issues"].get(number)
        if issue is None or not issue.get("pull_request"):
            raise Refused(404, "Not Found")
        if found.group(2) and method == "PUT":
            writes_refused()
            if issue.get("merged") or issue["state"] != "open":
                raise Refused(405, "Pull Request is not mergeable")
            data = body_json()
            into = "heads/" + issue["base"]["ref"]
            head_sha = store["refs"]["heads/" + issue["head"]["ref"]]
            if data.get("sha") and data["sha"] != head_sha:
                raise Refused(409, "Head branch was modified. Review and try the merge again.")
            flat = flat_of(store, store["refs"][into])
            flat.update(flat_of(store, head_sha))
            sha_tree = tree_sha(flat)
            store["trees"][sha_tree] = flat
            parents = [store["refs"][into]] + ([head_sha] if data.get("merge_method", "merge") == "merge" else [])
            message = "Merge pull request #%s" % number
            commit = sha_of("commit", sha_tree, *parents, message)
            store["commits"][commit] = {"tree": sha_tree, "parents": parents, "message": message}
            store["refs"][into] = commit
            issue.update(merged=True, merged_by=login, merge_commit_sha=commit, state="closed", merged_at="2026-12-05T10:00:00Z",
                         closed_at="2026-12-05T10:00:00Z")
            print(json.dumps({"sha": commit, "merged": True, "message": "Pull Request successfully merged"}))
            return True
        if method == "PATCH":
            writes_refused()
            data = body_json()
            if data.get("state") == "closed" and not issue.get("merged"):
                issue["state"] = "closed"
                issue["closed_at"] = "2026-12-05T10:00:00Z"
            print(json.dumps({"number": int(number), "state": issue["state"]}))
            return True
        if method == "GET":
            print(json.dumps({
                "number": int(number), "state": issue["state"], "merged": bool(issue.get("merged")),
                "merged_at": issue.get("merged_at"), "merged_by": {"login": issue["merged_by"]} if issue.get("merged_by") else None,
                "merge_commit_sha": issue.get("merge_commit_sha"), "title": issue["title"], "user": {"login": issue["user"]},
                "head": {"ref": issue["head"]["ref"], "sha": store["refs"].get("heads/" + issue["head"]["ref"])},
                "base": {"ref": issue["base"]["ref"]}}))
            return True

    return False
