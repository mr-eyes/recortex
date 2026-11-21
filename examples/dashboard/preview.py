#!/usr/bin/env python3
"""Build offline dashboard examples from the checkout's invented records."""

import argparse
import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock


def add_field_backups(fixture):
    """Ongoing project operations with their own topic outside the manuscript."""
    import manuscript_record as record

    issue = 14
    state = fixture.repo()
    state["labels"].append({"name": "topic:backups", "color": "ededed", "description": ""})
    state["issues"][str(issue)] = record.issue(
        "Task: maintain field logger backups",
        "## Goal\n\nKeep a recoverable copy of every field logger export after collection.",
        ["active", "topic:backups"], "2026-11-17T13:00:00Z", parent=fixture.config["hub"])
    state["next_issue"] = issue + 1
    fixture.save(state)

    failed = "2026-11-19T09:00-05:00"
    fixture.put(9401, issue, record.failure(
        failed, "three logger exports missing from backup",
        "the backup missed three field logger exports saved in nested folders",
        "Only 21 of the 24 exports in the collection manifest could be restored.",
        "The field technician restored the nightly backup into an empty directory.",
        "The backup job copied only files in the top-level directory.",
        "Three exports were recoverable only from the field laptop.",
        "Whether older backups also skipped files.",
        "Include nested folders and repeat the restore drill.",
        folds=record.fold_thread("keeping recoverable copies of the field logger exports",
                                 "The field laptop is reused for the next collection.")), created=record.utc(failed))
    restored = "2026-11-23T11:00-05:00"
    fixture.put(9402, issue, record.result(
        restored, "all field logger exports restored",
        "all 24 field logger exports were restored after the backup began including nested folders",
        "Restored the new backup to an empty directory and compared checksums with the collection manifest.",
        "One export per field plot, including files in nested folders.",
        record.table([("Matching checksums", "24", "24 exports", "checks/restore-manifest.tsv")]),
        "No missing files in this drill.", "One restore drill.",
        "Older backups still need a separate check.",
        "Repeat the restore check after the next field collection.", links="because=9401"),
        created=record.utc(restored))


def add_software_handoff(fixture):
    """An explicit handoff, its later checkpoint, and the follow-up agreed for the same task."""
    import manuscript_record as record
    fixture.put(9110, fixture.config["hub"], """### Resume block: 2026-04-10 17:00 UTC

<!-- ghim:resume:start -->
**In short:** The reader now carries open quotes between lines. The full conformance rerun is next.

**Last updated:** 2026-04-10 17:00 UTC by the observer
**Current focus:** Verify the quoted-newline fix before tagging the release.
**Active issues:** #7, 1 of 2 steps
**Last decisions:** On March 7, the team chose a character-by-character reader.
**Next actions:**

| Action | Owner | Where |
|---|---|---|
| Step 2: Rerun the conformance set | not stated | #7 |

**Holds:** Wait for the project lead before tagging the release.
<!-- ghim:resume:end -->
""", created="2026-04-10T17:00:00Z")
    fixture.put(9111, 7, """<!-- ghim:event type=checkpoint at=2026-04-10T18:30+00:00 done=9108:2 -->
### Checkpoint: 2026-04-10 18:30 UTC - conformance rerun complete

**In plain words, we paused here:** the conformance rerun finished and 9 of 12 quoted-newline files passed

**State:** All 12 files ran and 9 passed. The three failures involve carriage returns inside quoted fields.

**Done since the last checkpoint:** Reran the conformance set. The counts are in `tests/conformance/out/summary.tsv`.

**In progress:** None.

**Next actions:** Bring the three failures to the project lead.

**Open questions:** The cause of the failures is still unknown.
""", created="2026-04-10T18:30:00Z")
    fixture.put(9112, 7, """<!-- ghim:event type=steps at=2026-04-10T18:40+00:00 -->
### Steps: 2026-04-10 18:40 UTC - investigate the remaining failures

**In plain words, we agreed to:** find the cause of the remaining quoted-newline failures

**Agreed:** By the project lead in the chat, 2026-04-10 18:40 UTC:

> Please investigate the three failures and record what causes them.

**Steps:**
1. Investigate the three remaining quoted-newline failures

**Done when:** A reproducing input and the cause are recorded for each failure.
""", created="2026-04-10T18:40:00Z")
    failed = "2026-04-10T18:50+00:00"
    fixture.put(9113, 3, record.failure(
        failed, "input file missing from benchmark", "the benchmark stopped before the first timing",
        "The benchmark expected a reference file that was missing.", "Ran the reader benchmark.",
        "The reference data directory was empty.", "There is no timing for this reader version.",
        "Whether the new reader is faster.", "Restore the reference file and rerun the benchmark."),
        created=record.utc(failed))


def add_decision_change(fixture, name):
    """A changed default, an intentional comparison, and unrelated work in the same project."""
    import manuscript_record as record

    if name == "software":
        topic, title = "topic:release-checks", "Release checks"
        section, profile = "Documentation", "rel:docs"
        old = "use the cobalt delimiter corpus as the release gate"
        new = "use the amber delimiter corpus as the release gate"
        instruction = "Choose the release corpus according to the current release-gate decision"
        comparison = "Retain the cobalt delimiter corpus as a historical baseline"
        possible = "Generate the cobalt delimiter summary for each release"
        quiet = "Check the spelling of translator credits"
        goal = "Produce a release packet meeting the signed-off corpus policy"
    else:
        topic, title = "topic:reporting", "Field reporting"
        section, profile = ("Methods", "ms:methods") if name == "manuscript" else ("Methods", "rd:methods")
        old = "use the ridge transect as the primary reporting population"
        new = "use the valley transect as the primary reporting population"
        instruction = "Choose the reporting population according to the current sampling decision"
        comparison = "Retain the ridge transect as a historical baseline"
        possible = "Prepare the ridge transect summary for each field report"
        quiet = "Check the equipment inventory"
        goal = "Deliver a field report that meets the adopted sampling policy"
    repo, hub = fixture.config["repo"], fixture.config["hub"]
    old_url = "https://github.com/%s/issues/16#issuecomment-9701" % repo
    state = fixture.repo()
    state["labels"].append({"name": topic, "color": "ededed", "description": ""})
    section_text = "%s (%s)." % (instruction, old_url)
    body = "# %s\n\n## %s\n\n%s\n\n## Sources\n\n| Statement | Source |\n| --- | --- |\n| %s | %s |" % (
        title, section, section_text, comparison, old_url)
    state["issues"]["16"] = record.issue(title, body, ["final-record", topic, profile], "2026-04-09T08:00:00Z", parent=hub)
    state["issues"]["15"] = record.issue("Task: " + title, "## Goal\n\n%s (%s)." % (goal, old_url),
                                         ["active", topic], "2026-04-09T08:00:00Z", parent=16)
    state["next_issue"] = max(17, state["next_issue"])
    fixture.save(state)
    for cid, plain, minute, links in ((9701, old, 10, ""), (9703, new, 30, "replaces=9701")):
        at = "2026-04-10T14:%02d+00:00" % minute
        fixture.put(cid, 16, record.decision(at, plain, plain, plain.capitalize() + ".", "The project lead",
                                             plain.capitalize() + ".", "The planning chat", "Sets the default.",
                                             "Check the current work.", links=links), created=record.utc(at))
    steps = "%s (%s).\n2. %s.\n3. %s." % (instruction, old_url, possible, quiet)
    fixture.put(9702, 15, """<!-- ghim:event type=steps at=2026-04-10T14:20+00:00 -->
### Steps: 2026-04-10 14:20 UTC - prepare the next review

**In plain words, we agreed to:** prepare the next review

**Agreed:** By the project lead in the planning chat.

**Steps:**
1. %s

**Done when:** The review packet is ready.
""" % steps, created="2026-04-10T14:20:00Z")


def add_project_map(fixture, name, impacts=True):
    """Post invented accepted changes to the fixture's hub using the production format."""
    import project_map

    def branch(key, title, parent, description, evidence=(), links=()):
        return {"action": "add", "id": key, "title": title, "parent": parent, "description": description,
                "evidence": list(evidence), "links": list(links)}

    if name == "software":
        changes = [
            branch("reader", "Delimited file reader", None, "Read delimited text without losing the boundaries between fields."),
            branch("quotes", "Quoted fields", "reader", "Carry quote state across physical lines.", ["comment:9105"]),
            branch("newlines", "Embedded newlines", "quotes", "Preserve newline characters inside a quoted field.", ["issue:7"]),
            branch("checks", "Conformance checks", None, "Exercise known edge cases against the reader.", ["comment:9111"], ["reader"]),
            branch("carriage", "Carriage returns", "checks", "Investigate why three quoted-newline cases still fail.", ["comment:9112"], ["newlines"]),
            branch("release", "Release preparation", None, "Wait for the project lead to review the conformance results."),
            {"action": "rename", "id": "quotes", "title": "Quoted field handling"}]
    else:
        changes = [
            branch("plots", "Field measurements", None, "Compare moisture readings across the cover-crop plots."),
            branch("probes", "Probe calibration", "plots", "Trace the high readings after storms.", ["issue:5"]),
            branch("wet", "Wet-corner plots", "probes", "Four probes affect which plots enter the moisture analysis.", ["issue:5"]),
            branch("analysis", "Dry-spell analysis", None, "Compare the moisture response across seasons.", ["issue:9"], ["plots"]),
            branch("logistics", "Operations", None, "Keep field data recoverable between collections."),
            branch("backup", "Logger export backups", "logistics", "Restore every export and verify its checksum.",
                   ["comment:9402", "issue:14"], ["plots"]),
            {"action": "rename", "id": "logistics", "title": "Field operations"}]
        if name == "manuscript":
            changes.extend([branch("paper", "Manuscript", None, "Explain the study and its results.", ["issue:13"], ["analysis"]),
                            branch("methods", "Methods section", "paper", "Document the plot exclusions.", ["issue:5"], ["wet"])])
    if impacts:
        changes.append(branch("review-inputs", "Review inputs", None, "Choose the default inputs for each review.",
                              ["comment:9701"]))
    state = project_map.empty()
    for offset, change in enumerate(changes):
        source = "observer" if change.get("id") in ("backup", "carriage") else "user"
        op = project_map.prepare(state, change, source=source, op_id="sample-%d" % offset)
        body = project_map.encode(fixture.config["repo"], fixture.config["hub"], op)
        fixture.put(9600 + offset, fixture.config["hub"], body, created="2026-11-24T12:%02d:00Z" % offset)
        state = project_map.apply(state, op)


def build_examples(checkout, destination):
    sys.path.insert(0, str(checkout / "tests"))
    from test_manuscript_plan import PaperSandbox
    from test_scripts import MindSandbox, chain_text

    sys.path.insert(0, str(checkout / "skills" / "recortex" / "scripts"))
    has_map = (checkout / "skills" / "recortex" / "scripts" / "project_map.py").is_file()
    has_impacts = (checkout / "skills/recortex/scripts/decision_impact.py").is_file()
    for name, fixture in (("software", MindSandbox()), ("research", PaperSandbox()),
                          ("manuscript", PaperSandbox()), ("sparse", MindSandbox())):
        try:
            fixture.setUp()
            if name == "software":
                fixture.config["profiles"] = ["software"]
                fixture.write_config(fixture.config)
                fixture.add_chain()
                fixture.put(9108, 7, chain_text("6-steps.md"))
                fixture.put(9109, 7, chain_text("7-result.md"))
                add_software_handoff(fixture)
            elif name in ("research", "manuscript"):
                if name == "research":
                    import manuscript_record
                    state, fixture.config, _ = manuscript_record.build()
                    fixture.save(state)
                    fixture.write_config(fixture.config)
                add_field_backups(fixture)
            elif name == "sparse":
                fixture.config["profiles"] = []
                fixture.write_config(fixture.config)
                state = fixture.repo()
                hub = str(fixture.config["hub"])
                state["issues"] = {hub: state["issues"][hub]}
                state["comments"] = {}
                fixture.save(state)
            if has_map and name != "sparse":
                if has_impacts:
                    add_decision_change(fixture, name)
                add_project_map(fixture, name, impacts=has_impacts)
            fixture.ready()
            if has_map:
                import mind
                fixture.in_process(extra={"CLAUDE_PROJECT_DIR": fixture.project})
                render = mind.render
                def sample(model, pictures):
                    sample_model = dict(model, sample_map_edits=True)
                    if name != "sparse" and (model.get("decision_impacts") or {}).get("groups"):
                        from impact_example import write_example as write_impact
                        write_impact(destination, checkout, name, model, fixture)
                        sample_model["sample_impact_editor"] = name
                    return render(sample_model, pictures)
                with contextlib.ExitStack() as settings:
                    settings.enter_context(mock.patch.object(mind, "render", sample))
                    if (checkout / "skills/recortex/scripts/decision_impact.py").is_file():
                        import decision_impact
                        settings.enter_context(mock.patch.object(decision_impact, "suggestions_enabled", return_value=True))
                    settings.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    if mind.main([]):
                        raise RuntimeError("Could not build the sample dashboard")
            else:
                built = fixture.tool("mind.py")
                if built.returncode:
                    raise RuntimeError(built.stderr or built.stdout)
            page = Path(fixture.index_file()).with_name("mind.html")
            shutil.copyfile(str(page), str(destination / (name + ".html")))
        finally:
            fixture.doCleanups()
    if (checkout / "skills/recortex/page/dashboard_suggestions.html").is_file():
        from suggestion_example import write_example
        write_example(destination, checkout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new directory for standalone HTML files")
    parser.add_argument("--checkout", type=Path, default=Path(__file__).resolve().parents[2],
                        help="trusted local Recortex checkout whose fixtures and renderer to run")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("choose a new output directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(output.parent)) as tmp:
        pages = Path(tmp) / "pages"
        pages.mkdir()
        build_examples(args.checkout.resolve(), pages)
        pages.rename(output)
    for page in sorted(output.glob("*.html")):
        print(page.as_uri())


if __name__ == "__main__":
    main()
