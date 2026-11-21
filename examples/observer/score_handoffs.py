#!/usr/bin/env python3
"""Summarize independently reviewed handoff trials. Reads JSON lines from stdin."""

import json
import math
import statistics
import sys
from collections import Counter, defaultdict


STAGES = ("missed", "selected", "drafted", "reviewed", "queued", "published", "verified", "retrieved")
ARMS = ("ordinary", "shadow", "delivered")


def ratio(count, total):
    return {"count": count, "total": total, "rate": count / total if total else None}


def validate(row):
    for key in ("purpose", "case", "trial"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError(key + " must be a nonempty identifier")
    if row["arm"] not in ARMS:
        raise ValueError("unknown arm")
    facts = row["facts"]
    if not isinstance(facts, dict) or any(not key or value not in STAGES for key, value in facts.items()):
        raise ValueError("facts must map independent fact IDs to their last confirmed stage")
    # Each Boolean is one human judgment on an actual claim or visible intervention.
    for key in ("claims", "advice"):
        if not isinstance(row[key], list) or any(type(value) is not bool for value in row[key]):
            raise ValueError(key + " must be a list of Boolean judgments")
    if row["arm"] != "delivered" and row["advice"]:
        raise ValueError("only the delivered arm can have visible advice")
    if type(row["correct"]) is not bool:
        raise ValueError("correct must be a Boolean")
    for key in ("interruptions", "calls", "tokens"):
        if type(row[key]) is not int or row[key] < 0:
            raise ValueError(key + " must be a nonnegative integer")
    time = row["seconds"]
    if type(time) not in (int, float) or not math.isfinite(time) or time < 0:
        raise ValueError("seconds must be finite and nonnegative")


def summarize(rows):
    reached = Counter(stage for row in rows for stage in row["facts"].values())
    total = sum(reached.values())
    stages = {stage: ratio(sum(reached[s] for s in STAGES[i:]), total)
              for i, stage in enumerate(STAGES) if stage != "missed"}
    claims = [valid for row in rows for valid in row["claims"]]
    advice = [relevant for row in rows for relevant in row["advice"]]
    return {
        "distinct_cases": len({row["case"] for row in rows}),
        "trials": len(rows),
        "fact_coverage": stages,
        "recording_precision": ratio(sum(claims), len(claims)),
        "meaningful_recall": stages["verified"],
        "advice_relevance": ratio(sum(advice), len(advice)),
        "correct_next_decision": ratio(sum(row["correct"] for row in rows), len(rows)),
        "median_seconds_to_answer": statistics.median(row["seconds"] for row in rows),
        "unnecessary_interruptions": sum(row["interruptions"] for row in rows),
        "calls": sum(row["calls"] for row in rows),
        "tokens": sum(row["tokens"] for row in rows),
    }


def score(lines):
    groups = defaultdict(lambda: defaultdict(list))
    seen = set()
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("expected an object")
            validate(row)
            identity = tuple(row[key] for key in ("purpose", "case", "trial", "arm"))
            if identity in seen:
                raise ValueError("duplicate trial in the same arm")
            seen.add(identity)
            groups[row["purpose"]][row["arm"]].append(row)
        except (TypeError, ValueError, KeyError) as error:
            raise ValueError("line %d: %s" % (n, error)) from error
    if not seen:
        raise ValueError("no reviewed trials")
    return {purpose: {arm: summarize(arms[arm]) if arm in arms else None for arm in ARMS}
            for purpose, arms in groups.items()}


if __name__ == "__main__":
    try:
        print(json.dumps(score(sys.stdin), indent=2, sort_keys=True, allow_nan=False))
    except ValueError as error:
        sys.exit(str(error))
