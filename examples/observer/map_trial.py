#!/usr/bin/env python3
"""Run invented, held-out map cases with the installed observer, then score the saved decisions separately."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/recortex/scripts"))
import observer_map
import observer_runtime as runtime
import project_map

REPO = "octo-org/sample-project"


def simulate(case, options):
    state = project_map.empty()
    for key, title, parent in case["nodes"]:
        operation = project_map.prepare(state, {"action": "add", "id": key, "title": title, "parent": parent})
        state = project_map.apply(state, operation, "2026-01-01T10:00:00Z")
    for key in case.get("deleted", []):
        operation = project_map.prepare(state, {"action": "delete", "id": key, "children": "subtree"})
        state = project_map.apply(state, operation, "2026-01-02T10:00:00Z")
    memories = {100 + i: {"comment": 100 + i, "issue": i + 5, "kind": "decision", "text": title + ". " + body}
                for i, (title, body) in enumerate(case["records"])}
    sources = [{"text": msg, "at": "2026-01-03T10:00:00Z", "role": "user", "direct_user": True,
                "source_id": runtime.candidate_hash([case["case"], i])} for i, msg in enumerate(case["sources"])]
    calls = []

    def invoke(purpose, payload, schema):
        result = runtime.invoke(payload, schema, purpose, options)
        calls.append({k: result.get(k) for k in ("ok", "code", "model", "elapsed", "usage")})
        return result["structured"] if result["ok"] else None

    status, proposal = observer_map.assess(REPO, 4, state, memories, sources, [], {}, True, invoke)
    return {"case": case["case"], "domain": case["domain"], "status": status, "calls": calls,
            "change": proposal["change"] if proposal else None, "routine": bool(proposal and proposal["routine"])}


def run(cases, model, output):
    if output.exists():
        raise ValueError("choose a new report path for each trial")
    options = runtime.complete({"level": "assistant", "model": model, "map_mode": "shadow"})
    proof = runtime.check_runtime(options)
    if not proof.get("ok"):
        raise ValueError("the installed observer runtime did not pass its isolation check (%s)" % proof["code"])
    options[runtime.gate_control("middle", options)] = proof
    with output.open("x", encoding="utf-8") as out:
        for case in cases:
            row = simulate(case, options)
            out.write(json.dumps(row) + "\n")
            out.flush()
            if row["status"] not in ("shadow", "quiet") or any(not c["ok"] for c in row["calls"]):
                raise ValueError("model evaluation stopped at %s after a failed call; report is partial" % row["case"])


def matches(label, change):
    change = change or {"action": "none"}
    if change["action"] not in label["actions"]:
        return False
    if "node" in label and change.get("id") != label["node"]:
        return False
    if "title" in label and change.get("title", "").casefold() != label["title"].casefold():
        return False
    if "target" in label and (change.get("parent") or change.get("into") or "") not in label["target"]:
        return False
    return True


def score(labels, rows):
    by_case = {row["case"]: row for row in rows}
    if len(rows) != len(by_case) or set(labels) != set(by_case):
        raise ValueError("one decision per labeled case is required, including cases where the observer stayed quiet")
    result = {"domains": {}, "cases": []}
    for name, label in labels.items():
        row = by_case[name]
        if row["status"] not in ("shadow", "quiet") or not row["calls"] or any(not call["ok"] for call in row["calls"]):
            raise ValueError("incomplete model result for %s" % name)
        correct = matches(label, row["change"])
        expected = "none" not in label["actions"]
        routine = row["routine"]
        violation = routine and (label.get("review", False) or not correct and not expected or
                                 row["change"]["action"] not in ("add", "rename"))
        counts = result["domains"].setdefault(row["domain"], {
            "cases": 0, "matched": 0, "meaningful": 0, "missed": 0, "automatic": 0, "correct_automatic": 0,
            "unauthorized": 0, "calls": 0, "tokens": 0})
        counts["cases"] += 1
        counts["matched"] += int(correct)
        counts["meaningful"] += int(expected)
        counts["missed"] += int(expected and not correct)
        counts["automatic"] += int(routine)
        counts["correct_automatic"] += int(routine and correct and not violation)
        counts["unauthorized"] += int(violation)
        counts["calls"] += len(row["calls"])
        counts["tokens"] += sum(sum(call["usage"].get(key, 0) for key in
                                   ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
                                    "output_tokens")) for call in row["calls"])
        result["cases"].append({"case": name, "matched": correct, "action": (row["change"] or {}).get("action", "none"),
                                "eligible_for_automatic": routine, "required_review_violation": violation})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    live = subs.add_parser("run", help="uses the installed model on the supplied invented cases")
    live.add_argument("cases", type=Path)
    live.add_argument("output", type=Path)
    live.add_argument("--model", default="sonnet")
    marks = subs.add_parser("score", help="score a completed run against independent, prewritten labels")
    marks.add_argument("labels", type=Path)
    marks.add_argument("report", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        run(json.loads(args.cases.read_text(encoding="utf-8")), args.model, args.output)
    else:
        labels = json.loads(args.labels.read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in args.report.read_text(encoding="utf-8").splitlines()]
        print(json.dumps(score(labels, rows), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        sys.exit(str(error))
