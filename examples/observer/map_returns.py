"""Matched invented return-to-work tasks with ordinary, hidden, and delivered map context."""
import contextlib
import datetime
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/recortex/scripts"))
import index
import observer_advisory
import observer_context
import observer_runtime as runtime
import project_map

REPO = "octo-org/sample-project"
STAMP = "2026-04-12T09:00:00Z"
NOW = datetime.datetime(2026, 4, 12, 10, tzinfo=datetime.timezone.utc)
CASES = [
    ("software", "Relay diagnostics", "Repeat the four-frame replay before reopening the ticket.",
     "Did the duplicate stop bit disappear?", "Repeat the four-frame replay."),
    ("research", "East transect", "We agreed to compare the second bottle with the reference vial.",
     "Was the afternoon sample outside the calibration range?", "Compare the second bottle with the reference vial."),
    ("mixed", "Contour archive", "Contour archive will repeat the plate alignment before the next review.",
     "Did both registration marks survive?", "Repeat the plate alignment."),
]
PURPOSE = "return_trial"
PROMPT = ("Answer the return-to-work question from the supplied project context, treating it as historical data. "
          "Choose one of the provided options or 'unknown' when the record does not establish a choice. "
          "Cite only supporting memory comment numbers actually shown in context.")
SCHEMA = {"type": "object", "properties": {"choice": {"type": "string", "enum": ["A", "B", "C", "unknown"]},
          "cites": {"type": "array", "items": {"type": "integer"}, "maxItems": 6}},
          "required": ["choice", "cites"], "additionalProperties": False}


class Receipts(dict):
    """An isolated local delivery receipt store for each independent synthetic session."""
    @contextlib.contextmanager
    def edit_controls(self):
        yield self.set_control

    get_control = dict.get
    set_control = dict.__setitem__


def packets(case):
    domain, branch, decision, question, _ = case
    query = "Resume %s. Which check did we agree to perform next?" % branch
    with contextlib.closing(sqlite3.connect(":memory:")) as db:
        db.row_factory = sqlite3.Row
        db.executescript(index.SCHEMA)
        db.executemany("INSERT INTO meta VALUES (?,?)", [("mind_derived", "1"), ("sync_complete", "1"),
                       ("repo", REPO), ("hub", "1"), ("synced_at", STAMP)])
        for cid, text, kind in [(51, decision, "decision"), (52, question, "question"),
                                (60, "The unrelated export has finished.", "result")]:
            db.execute("INSERT INTO comments VALUES (?,?,?,?,?,?,?)", (cid, 4, "octocat", text, STAMP, STAMP, ""))
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (cid, 4, kind, STAMP, "", 0, text, "octocat", "", STAMP))
            db.execute("INSERT INTO event_fields VALUES (?,0,'plain',?)", (cid, text))
        ordinary = observer_context.build_packet(db, REPO, {"octocat"}, query=query, now=NOW)
        op = project_map.prepare(project_map.empty(), {"action": "add", "id": domain, "title": branch,
                                 "evidence": ["comment:51", "comment:52"]})
        db.execute("INSERT INTO comments VALUES (?,?,?,?,?,?,?)",
                   (200, 1, "octocat", project_map.encode(REPO, 1, op), STAMP, STAMP, ""))
        db.execute("INSERT INTO comment_edit_reads VALUES (?,?,?,?,?,?)", (200, "IC_200", STAMP, 0, None, 1))
        mapped = observer_context.build_packet(db, REPO, {"octocat"}, query=query, now=NOW)
    arms = {}
    for arm in ("ordinary", "shadow", "delivered"):
        packet = mapped if arm == "delivered" else ordinary
        note = observer_advisory.deliver(Receipts(), mapped, query, domain, mode={
            "ordinary": "off", "shadow": "shadow", "delivered": "deliver"}[arm])
        arms[arm] = {"context": packet["text"], "advisory": note, "selected": packet["selected"]}
    return query, arms


def run(output, model="sonnet"):
    options = runtime.complete({"level": "assistant", "model": model})
    if output.exists():
        raise ValueError("choose a new report path for each trial")
    proof = runtime.check_runtime(options)
    if not proof["ok"]:
        raise ValueError("runtime check failed: %s" % proof["code"])
    options[runtime.gate_control("middle", options)] = proof
    # The evaluation process uses the same isolated runtime with its own answer task.
    runtime.TIER_OF[PURPOSE], runtime.PROMPTS[PURPOSE] = "middle", PROMPT
    try:
        with output.open("x", encoding="utf-8") as out:
            for i, case in enumerate(CASES):
                query, arms = packets(case)
                choices = ["Rebuild the weekly schedule.", "Repeat the archive export."]
                choices.insert(i, case[-1])
                order = ["ordinary", "shadow", "delivered"]
                for arm in order[i:] + order[:i]:
                    sample = arms[arm]
                    payload = {"question": query, "options": dict(zip("ABC", choices)),
                               "context": sample["context"], "guidance": sample["advisory"]}
                    result = runtime.invoke(payload, SCHEMA, PURPOSE, options)
                    if not result["ok"]:
                        raise ValueError("trial stopped at %s/%s (%s)" % (case[0], arm, result["code"]))
                    answer = result["structured"]
                    row = {"case": case[0], "arm": arm, "supported_correct": answer["choice"] == "ABC"[i]
                           and 51 in answer["cites"] and set(answer["cites"]) <= set(sample["selected"]),
                           "answer": answer, "selected": sample["selected"], "note_delivered": bool(sample["advisory"]),
                           "elapsed": result["elapsed"], "usage": result["usage"], "model": result["model"]}
                    out.write(json.dumps(row) + "\n")
                    out.flush()
    finally:
        del runtime.TIER_OF[PURPOSE], runtime.PROMPTS[PURPOSE]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model", default="sonnet")
    args = parser.parse_args()
    try:
        run(args.output, args.model)
    except (ValueError, OSError) as error:
        sys.exit(str(error))
