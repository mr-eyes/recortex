"""The rules of the plan: which parts are agreed, and the state of each part. Pure.

Python 3 standard library only. No network, no database handle: assess() takes the told memories and their counted
links (what index.memories returns), as board_rules.py takes cards. index.py, plan.py, write.py, compile.py and
mind_model.py all use it, so the rules exist once. Who counts and what is told is decided before: a memory that is
not in `items` (another account's, or edited) backs nothing, agrees nothing and links nothing.

A part is a told plan memory. It is agreed when a told decision is linked to it with `because`, in either direction,
or when it narrows or rewords a part that is agreed (the session may do that on its own). Otherwise it was added by
the session. Both are told.

The state of a part, first match wins (references/planning.md):

  dropped      an `instead` points at it
  revised      a plan `replaces` it
  doubt        a standing result links it with `against`
  moved        a memory it rests on (a `because` that is no decision or part) was replaced, set aside or is not told
  waits        it links a planned part that is neither backed, in the text nor standing
  text         a display part whose id is a block in a topic body; or any part a told text change newer than it links
  can be made  a display part whose linked claims are all backed or in the text
  backed       an evidence part with a standing result linked either way (`because` from it, `checks` to it)
  failed       an evidence part that only a failure `checks`
  planned      an evidence or display part with none of the above
  stands       any other part

"Backed" means a standing finding is linked; it can still turn out wrong.
"""

STATES = ("dropped", "revised", "doubt", "moved", "waits", "text", "can be made", "backed", "failed", "planned", "stands")
NARROWING = ("narrows", "rewords")      # what the session may do to an agreed part on its own
UNTOLD_STATES = ("replaced", "set aside")


def standing(item):
    """A told memory that nothing replaced or set aside."""
    return item is not None and item["state"] not in UNTOLD_STATES


def standing_result(item):
    return item is not None and item["kind"] == "result" and item["state"] == "stands"


def assess(items, details, blocks, every, evidence=(), display=()):
    """{plan memory id: assessment} for every told plan memory that has `details`.

    items     {memory id: {"id", "kind", "time", "state", "links": [(key, target id)]}}, the told memories
    details   {plan memory id: {"part", "section", "topic" (int or None), "block" (id or ""), "change" ("" or one of
              the four)}}
    blocks    {(issue number, block id)} of the figure and table blocks the topic bodies hold
    every     {memory id: kind} of every memory in the index, told or not
    evidence  the parts a finding can back; display  the parts that carry a block id

    An assessment is a dict: part, section, topic, block, change, state, agreed, standing (neither dropped nor
    revised), decisions, backs (standing results that bear it out), against, failures, rests (what it rests on:
    memories it links with `because` that are no decision and no part), waits_for (a part id or None), text (the id
    of the oldest told text change newer than it that links it, or None), dropped_by, revised_by, replaces.
    """
    pointing = {}
    for item in items.values():
        for key, target in item["links"]:
            pointing.setdefault(target, []).append((key, item["id"]))
    order = sorted((cid for cid in details if cid in items and items[cid]["kind"] == "plan"),
                   key=lambda cid: (items[cid]["time"], cid))
    out = {}
    for cid in order:
        me, detail, sources = items[cid], details[cid], pointing.get(cid, [])
        kinds = lambda keys, kind: [src for key, src in sources if key in keys and items[src]["kind"] == kind]
        decisions = sorted(set(kinds(("because",), "decision")) | {
            target for key, target in me["links"] if key == "because" and target in items
            and items[target]["kind"] == "decision"})
        replaces = [target for key, target in me["links"] if key == "replaces" and target in out]
        agreed = bool(decisions) or (detail["change"] in NARROWING and any(out[t]["agreed"] for t in replaces))
        dropped_by = [src for key, src in sources if key == "instead" and items[src]["kind"] in ("decision", "plan")]
        revised_by = kinds(("replaces",), "plan")
        against = [src for src in kinds(("against",), "result") if standing_result(items[src])]
        backs = sorted({src for src in kinds(("checks",), "result") if standing_result(items[src])} | {
            target for key, target in me["links"] if key == "because" and standing_result(items.get(target))})
        failures = kinds(("checks",), "failure")
        rests = [target for key, target in me["links"] if key == "because"
                 and every.get(target, "") not in ("decision", "plan")]
        moved = any(target not in items or items[target]["state"] in UNTOLD_STATES for target in rests)
        linked = [target for key, target in me["links"] if key == "because" and target in out]
        waits_for = next((t for t in linked if out[t]["state"] not in ("backed", "text", "stands")), None)
        texts = sorted((src for src in kinds(("because",), "text") if items[src]["time"] > me["time"]),
                       key=lambda src: (items[src]["time"], src))
        topic = detail["topic"]
        block_here = bool(detail["block"]) and any(
            number == topic or topic is None for number, ident in blocks if ident == detail["block"])
        part = detail["part"]
        if dropped_by:
            state = "dropped"
        elif revised_by:
            state = "revised"
        elif against:
            state = "doubt"
        elif moved:
            state = "moved"
        elif waits_for is not None:
            state = "waits"
        elif part in display and block_here or texts:
            state = "text"
        elif part in display and linked and all(out[t]["state"] in ("backed", "text") for t in linked):
            state = "can be made"
        elif part in evidence and backs:
            state = "backed"
        elif part in evidence and failures:
            state = "failed"
        elif part in evidence or part in display:
            state = "planned"
        else:
            state = "stands"
        out[cid] = {"part": part, "section": detail["section"], "topic": topic, "block": detail["block"],
                    "change": detail["change"], "state": state, "agreed": agreed,
                    "standing": not dropped_by and not revised_by, "decisions": decisions, "backs": backs,
                    "against": sorted(against), "failures": sorted(failures), "rests": rests, "waits_for": waits_for,
                    "text": texts[0] if texts else None, "dropped_by": dropped_by, "revised_by": revised_by,
                    "replaces": replaces}
    return out


def agreed_ids(items, details):
    """The ids of the agreed parts, without the rest of the assessment (for the checks of a draft)."""
    every = {cid: item["kind"] for cid, item in items.items()}
    return {cid for cid, found in assess(items, details, set(), every).items() if found["agreed"]}
