"""The part of the fake gh that answers GitHub Projects and sub-issue operations (used by fake_gh.py only).

The state lives in the fake repository's JSON file:
  state["projects"]  {"<owner>/<number>": project}, a project being a dict shaped like GitHub's answer: id, number,
                     title, closed, public, url, readme, shortDescription, fields, workflows, views, items, repos
  state["owners"]    {"<owner>": {"id": ..., "__typename": "User" or "Organization"}}, made on first use
  state["issues"][n]["parent"]  the number of the parent issue (sub-issues)
Fault switches (environment):
  FAKE_GH_NO_PROJECT_SCOPE=1     every operation on a project fails as GitHub words a missing scope
  FAKE_GH_PROJECT_GONE=1         a project query finds no project
  FAKE_GH_FAIL_MUTATION=name     the mutation fails (exit 1) before acting; a comma list works
  FAKE_GH_FAIL_AFTER_MUTATION=name  the mutation acts, then fails (a timeout after GitHub acted)
  FAKE_GH_IGNORE_FIELD_WRITE=1   updateProjectV2ItemFieldValue answers as if it worked and changes nothing
  FAKE_GH_SUBISSUES=refuse       addSubIssue is refused (an older GitHub Enterprise Server)
  FAKE_GH_IGNORE_SUBISSUE=1      addSubIssue answers as if it worked and changes nothing
  FAKE_GH_BOARD_PAGE_FAIL=2      exit 1 when page 2 of the items is read
  FAKE_GH_DEFAULT_PUBLIC=1       a new project is public (GitHub's default is not verified)
  FAKE_GH_OWNER_KIND=User        the owner of the repository is a user (default Organization)
"""

import json
import os
import sys

import graphql_check

DEFAULT_REPO = "example-org/tinycsv"
DEFAULT_STATUS = ("Todo", "In Progress", "Done")
SUB_ISSUE_LIMIT = 100


class Refused(Exception):
    """An error as GitHub answers it: the message, and the exit code of gh."""

    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def wanted(query):
    """True when this fake answers the operation: a mutation, or a query about projects, the viewer, a node, the
    repository's id or an issue's parent (the body-edits query, which names userContentEdits, is the older branch)."""
    if graphql_check.operation(query) == "mutation":
        return True
    if "userContentEdits" in query:
        return False
    return any(word in query for word in ("rojectV2", "rojectsV2", "viewer", "node(", "isPrivate", "parent"))


def owner_of(state, login):
    owners = state.setdefault("owners", {})
    if login not in owners:
        kind = os.environ.get("FAKE_GH_OWNER_KIND", "Organization")
        owners[login] = {"id": "O_%d" % (len(owners) + 1), "__typename": kind, "login": login}
    return owners[login]


def scope_problem():
    return ("Your token has not been granted the required scopes to execute this query. The 'projectsV2' field "
            "requires one of the following scopes: ['read:project'], but your token has only been granted the: "
            "['repo', 'read:org'] scopes. Please modify your token's scopes at: https://github.com/settings/tokens.")


def issue_node(state, repo, number):
    issue = state["issues"].get(str(number))
    if issue is None:
        return None
    parent = issue.get("parent")
    kind = "PullRequest" if issue.get("pull_request") else "Issue"
    node = {"__typename": kind, "id": "I_%s" % number, "number": int(number), "state": issue.get("state", "open").upper(),
            "repository": {"nameWithOwner": repo, "id": "R_1"}}
    if kind == "Issue":
        children = [n for n, i in state["issues"].items() if i.get("parent") == int(number)]
        node["parent"] = None if parent is None else {"__typename": "Issue", "id": "I_%s" % parent, "number": int(parent),
                                                      "repository": {"nameWithOwner": repo, "id": "R_1"}}
        node["subIssuesSummary"] = {"total": len(children), "completed": len(
            [n for n in children if state["issues"][n].get("state") == "closed"]), "percentCompleted": 0}
        node["subIssues"] = {"totalCount": len(children), "nodes": [
            {"__typename": "Issue", "number": int(n)} for n in sorted(children, key=int)]}
    return node


def item_view(state, project, item):
    """The item as GitHub shows it: the content from the issue, the field values by name."""
    out = {"__typename": "ProjectV2Item", "id": item["id"], "isArchived": item.get("isArchived", False),
           "type": item["type"], "content": item.get("content"), "_values": item.get("values", {})}
    return out


def evaluate(state, repo, selection, obj, typename, ctx):
    """Evaluate a selection against a dict. Inline fragments apply when the dict's __typename is that type (or the
    fragment names an interface or union the type belongs to, which the checker already verified)."""
    out = {}
    for alias, name, args, children in selection:
        if name == "...":
            if obj is not None and args["on"] == obj.get("__typename"):
                out.update(evaluate(state, repo, children, obj, args["on"], ctx))
            elif obj is not None and args["on"] in ("Node", "RepositoryOwner", "ProjectV2Owner"):
                out.update(evaluate(state, repo, children, obj, args["on"], ctx))
            continue
        if name == "__typename":
            out[alias] = obj.get("__typename")
            continue
        value = field_value(state, repo, obj, name, args, ctx)
        if children and value is not None:
            if isinstance(value, list):
                value = [evaluate(state, repo, children, v, v.get("__typename") if v else None, ctx) if v is not None
                         else None for v in value]
            else:
                value = evaluate(state, repo, children, value, value.get("__typename"), ctx)
        out[alias] = value
    return out


def field_value(state, repo, obj, name, args, ctx):
    if name == "fieldValueByName":
        wanted = graphql_check.literal(args["name"])
        found = obj.get("_values", {}).get(wanted)
        if found is None:
            return None
        return dict(found, __typename=found["__typename"])
    if name in ("fields", "workflows", "views"):
        return {"nodes": obj.get(name, []), "totalCount": len(obj.get(name, []))}
    if name == "items":
        rows = [item_view(state, obj, i) for i in obj.get("items", [])
                if not i.get("isArchived") or "ARCHIVED" in json.dumps(args.get("archivedStates", ""))]
        first = int(args.get("first", "100"))
        after = graphql_check.literal(args["after"]) if "after" in args else None
        start = int(after) if after else 0
        page = start // first + 1
        if os.environ.get("FAKE_GH_BOARD_PAGE_FAIL") == str(page):
            raise Refused("simulated failure on page %d of the items (HTTP 502)" % page, 1)
        chunk = rows[start:start + first]
        more = start + first < len(rows)
        return {"nodes": chunk, "totalCount": len(rows),
                "pageInfo": {"hasNextPage": more, "endCursor": str(start + first) if more else None}}
    if name == "options":
        return obj.get("options", [])
    if name == "subIssues":
        return obj.get("subIssues")
    return obj.get(name)


def project_of(state, login, number):
    return state.setdefault("projects", {}).get("%s/%s" % (login, number))


def project_object(project):
    return dict(project, __typename="ProjectV2")


def project_by_id(state, ident):
    for project in state.get("projects", {}).values():
        if project["id"] == ident:
            return project
    return None


def run_query(state, repo, fields, selection):
    out = {}
    for alias, name, args, children in selection:
        if name == "viewer":
            out[alias] = evaluate(state, repo, children, {"__typename": "User", "login": "octo-dev",
                                                          "projectsV2": {"totalCount": 0}}, "User", {})
        elif name == "repositoryOwner":
            login = graphql_check.literal(args["login"])
            owner = dict(owner_of(state, login))
            number = None
            for kid in children:
                if kid[1] == "...":
                    for sub in kid[3]:
                        if sub[1] == "projectV2":
                            number = graphql_check.literal(sub[2]["number"])
            if number is not None:
                project = project_of(state, login, number)
                if os.environ.get("FAKE_GH_PROJECT_GONE"):
                    project = None
                owner["projectV2"] = project_object(project) if project else None
            out[alias] = evaluate(state, repo, children, owner, owner["__typename"], {})
        elif name == "node":
            node = project_by_id(state, graphql_check.literal(args["id"]))
            if node is None:
                out[alias] = None
            else:
                out[alias] = evaluate(state, repo, children, project_object(node), "ProjectV2", {})
        elif name == "repository":
            owner_login, name_ = graphql_check.literal(args["owner"]), graphql_check.literal(args["name"])
            repo_object = {"__typename": "Repository", "id": "R_1", "isPrivate": bool(state.get("private", True)),
                           "nameWithOwner": "%s/%s" % (owner_login, name_), "owner": dict(owner_of(state, owner_login))}
            for kid in children:
                if kid[1] == "issue":
                    repo_object[kid[0]] = None
            result = {}
            for kid in children:
                kalias, kname, kargs, kkids = kid
                if kname == "issue":
                    node = issue_node(state, "%s/%s" % (owner_login, name_), graphql_check.literal(kargs["number"]))
                    result[kalias] = None if node is None else evaluate(state, repo, kkids, node, node["__typename"], {})
                else:
                    result[kalias] = evaluate(state, repo, [kid], repo_object, "Repository", {})[kalias]
            out[alias] = result
        else:
            raise Refused("fake gh: unsupported query field %s" % name, 3)
    return out


def default_fields():
    return [
        {"__typename": "ProjectV2Field", "id": "F_title", "name": "Title", "dataType": "TITLE"},
        {"__typename": "ProjectV2SingleSelectField", "id": "F_status", "name": "Status", "dataType": "SINGLE_SELECT",
         "options": [{"id": "OPT_%d" % i, "name": name} for i, name in enumerate(DEFAULT_STATUS, 1)]},
    ]


def new_project(state, owner_id, login, title):
    projects = state.setdefault("projects", {})
    number = 1 + max([p["number"] for k, p in projects.items() if k.startswith(login + "/")] or [0])
    project = {"id": "PVT_%d" % (len(projects) + 1), "number": number, "title": title, "closed": False,
               "public": bool(os.environ.get("FAKE_GH_DEFAULT_PUBLIC")),
               "url": "https://github.com/orgs/%s/projects/%d" % (login, number), "readme": "", "shortDescription": "",
               "fields": default_fields(), "workflows": [
                   {"__typename": "ProjectV2Workflow", "name": "Item closed", "number": 1, "enabled": True},
                   {"__typename": "ProjectV2Workflow", "name": "Pull request merged", "number": 2, "enabled": True},
                   {"__typename": "ProjectV2Workflow", "name": "Auto-add to project", "number": 3, "enabled": False}],
               "views": [], "items": [], "repos": []}
    projects["%s/%d" % (login, number)] = project
    return project


def find_field(project, field_id):
    for field in project["fields"]:
        if field["id"] == field_id:
            return field
    raise Refused("Could not resolve to a node with the global id of '%s'." % field_id, 1)


def content_of(state, repo, content_id):
    number = content_id.split("_", 1)[1]
    node = issue_node(state, repo, number)
    if node is None:
        raise Refused("Could not resolve to a node with the global id of '%s'." % content_id, 1)
    return node, ("PULL_REQUEST" if node["__typename"] == "PullRequest" else "ISSUE")


def run_mutation(state, repo, name, args):
    """Apply one mutation; returns the payload as a dict."""
    given = args["input"]
    text = lambda key: graphql_check.literal(given[key]) if key in given else None
    if name == "createProjectV2":
        login = next((o["login"] for o in state.get("owners", {}).values() if o["id"] == text("ownerId")), None)
        if login is None:
            raise Refused("Could not resolve to a node with the global id of '%s'." % text("ownerId"), 1)
        project = new_project(state, text("ownerId"), login, text("title"))
        if text("repositoryId"):
            project["repos"].append(text("repositoryId"))
        return {"projectV2": project_object(project)}
    project = project_by_id(state, text("projectId")) if "projectId" in given else None
    if "projectId" in given and project is None:
        raise Refused("Could not resolve to a ProjectV2 with the id '%s'." % text("projectId"), 1)
    if name == "updateProjectV2":
        for key in ("title", "shortDescription", "readme"):
            if key in given:
                project[key] = text(key)
        for key in ("public", "closed"):
            if key in given:
                project[key] = given[key] == "true"
        return {"projectV2": project_object(project)}
    if name == "createProjectV2Field":
        field = {"__typename": "ProjectV2Field", "id": "F_%d" % (len(project["fields"]) + 1), "name": text("name"),
                 "dataType": text("dataType")}
        project["fields"].append(field)
        return {"projectV2Field": field}
    if name == "updateProjectV2Field":
        field = find_field(project_holding_field(state, text("fieldId")), text("fieldId"))
        if "singleSelectOptions" in given:
            old = {o["id"]: o for o in field.get("options", [])}
            options = []
            for item in given["singleSelectOptions"]:
                ident = graphql_check.literal(item["id"]) if "id" in item else None
                if ident is not None and ident not in old:
                    raise Refused("Option %s does not exist" % ident, 1)
                if ident is None:
                    state["_options"] = state.get("_options", 100) + 1
                    ident = "OPT_%d" % state["_options"]
                options.append({"id": ident, "name": graphql_check.literal(item["name"]),
                                "color": item["color"], "description": graphql_check.literal(item["description"])})
            field["options"] = options
        return {"projectV2Field": field}
    if name == "linkProjectV2ToRepository":
        project["repos"].append(text("repositoryId"))
        return {"repository": {"__typename": "Repository", "id": text("repositoryId")}}
    if name == "createProjectV2View":
        view = {"__typename": "ProjectV2View", "id": "V_%d" % (len(project["views"]) + 1), "name": text("name"),
                "layout": given["layout"], "filter": "", "number": len(project["views"]) + 1}
        project["views"].append(view)
        return {"projectV2View": view}
    if name == "updateProjectV2View":
        for view in project_views(state, text("viewId")):
            if view["id"] == text("viewId"):
                for key in ("filter", "name"):
                    if key in given:
                        view[key] = text(key)
                return {"projectV2View": view}
        raise Refused("Could not resolve to a ProjectV2View with the id '%s'." % text("viewId"), 1)
    if name == "addProjectV2ItemById":
        node, kind = content_of(state, repo, text("contentId"))
        for item in project["items"]:
            if item["content"]["number"] == node["number"]:
                return {"item": item_view(state, project, item)}
        item = {"id": "PVTI_%d" % (len(project["items"]) + 1), "isArchived": False, "type": kind, "values": {},
                "content": {"__typename": node["__typename"], "number": node["number"],
                            "repository": {"nameWithOwner": repo}}}
        project["items"].append(item)
        return {"item": item_view(state, project, item)}
    if name == "updateProjectV2ItemFieldValue":
        field = find_field(project, text("fieldId"))
        for item in project["items"]:
            if item["id"] == text("itemId"):
                value = given["value"]
                if not os.environ.get("FAKE_GH_IGNORE_FIELD_WRITE"):
                    if "singleSelectOptionId" in value:
                        option = graphql_check.literal(value["singleSelectOptionId"])
                        if option not in [o["id"] for o in field.get("options", [])]:
                            raise Refused("Option %s does not exist on the field" % option, 1)
                        item["values"][field["name"]] = {"__typename": "ProjectV2ItemFieldSingleSelectValue",
                                                         "optionId": option}
                    elif "text" in value:
                        item["values"][field["name"]] = {"__typename": "ProjectV2ItemFieldTextValue",
                                                         "text": graphql_check.literal(value["text"])}
                return {"projectV2Item": item_view(state, project, item)}
        raise Refused("Could not resolve to a ProjectV2Item with the id '%s'." % text("itemId"), 1)
    if name == "addSubIssue":
        if os.environ.get("FAKE_GH_SUBISSUES") == "refuse":
            raise Refused("Field 'addSubIssue' doesn't exist on type 'Mutation'", 1)
        parent_number = int(text("issueId").split("_", 1)[1])
        child_number = int((text("subIssueId") or "I_0").split("_", 1)[1])
        parent, child = state["issues"].get(str(parent_number)), state["issues"].get(str(child_number))
        if parent is None or child is None:
            raise Refused("Could not resolve to an Issue with the global id of '%s'." % text("issueId"), 1)
        if child.get("parent") not in (None, parent_number) and given.get("replaceParent") != "true":
            raise Refused("Issue may not contain duplicate sub-issues and may only have one parent", 1)
        if len([i for i in state["issues"].values() if i.get("parent") == parent_number]) >= SUB_ISSUE_LIMIT:
            raise Refused("Sub-issues limit reached: an issue can have at most 100 sub-issues", 1)
        if not os.environ.get("FAKE_GH_IGNORE_SUBISSUE"):
            child["parent"] = parent_number
        return {"issue": issue_node(state, repo, parent_number)}
    raise Refused("fake gh: unsupported mutation %s" % name, 3)


def project_holding_field(state, field_id):
    for project in state.get("projects", {}).values():
        if any(f["id"] == field_id for f in project["fields"]):
            return project
    raise Refused("Could not resolve to a node with the global id of '%s'." % field_id, 1)


def project_views(state, view_id):
    for project in state.get("projects", {}).values():
        if any(v["id"] == view_id for v in project["views"]):
            return project["views"]
    return []


def payload_for(state, repo, name, selection, result):
    """Evaluate the mutation's payload selection against the result dict."""
    return evaluate(state, repo, selection, dict(result, __typename="Payload"), "Payload", {})


def handle(state, repo, query):
    """Answer one operation. Returns (response dict). Raises Refused for an error the real API would answer."""
    kind = graphql_check.operation(query)
    selection = graphql_check.parse_query(query)
    touches_projects = "rojectV2" in query or "rojectsV2" in query or "node(" in query
    if os.environ.get("FAKE_GH_NO_PROJECT_SCOPE") and (touches_projects or kind == "mutation"
                                                      and "SubIssue" not in query):
        raise Refused(scope_problem(), 1)
    if kind == "query":
        return {"data": run_query(state, repo, {}, selection)}
    data = {}
    for alias, name, args, children in selection:
        if name in [n for n in (os.environ.get("FAKE_GH_FAIL_MUTATION") or "").split(",") if n]:
            raise Refused("simulated failure of %s (HTTP 502)" % name, 1)
        result = run_mutation(state, repo, name, args)
        data[alias] = payload_for(state, repo, name, children, result)
        if name in [n for n in (os.environ.get("FAKE_GH_FAIL_AFTER_MUTATION") or "").split(",") if n]:
            state["_fail_after"] = "simulated timeout after %s was carried out" % name
    return {"data": data}
