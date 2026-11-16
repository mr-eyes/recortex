# Offline observer evaluation

This replay checks observer mechanics over 60 synthetic coding and research days without invoking a model or contacting GitHub.

From the repository root:

```bash
mkdir -p .tmp
python3 examples/observer/run.py --json > .tmp/observer-evaluation.json
python3 -m unittest tests.test_observer_longitudinal
```

Without `--json`, the script prints a short result and the measured capture latency. It uses only the Python standard library and `tests/fake_gh.py`. It clears inherited observer settings, model recursion flags, and fake-GitHub fault switches before creating its own isolated environment. Private temporary files are removed after the run. Native transcripts use the operating system's private temporary folder because the scanner correctly rejects group-writable checkout ancestors.

Source ownership checks include every ancestor. Some sandboxed executors cannot represent the actual owners of system directories such as `/` and `/tmp`; an overflow owner identifier is not evidence that those directories are trusted. When the actual fixture preflight fails, the synthetic mechanics replay may use the [test-only ancestor metadata model](../../tests/observer_source_fixture.py) for those two system ancestors of its private fixture. The report discloses the original failed preflight and simulated boundaries. This changes no permissions or production policy, and does not establish real native capture. Other ownership or permission failures remain failures.

## What runs

The [fixture](../../tests/fixtures/observer-longitudinal.json) supplies original-user decisions, tool failures, meaningful checkpoints, routine work, and unsupported claims for two invented work streams. One coding session and one research session each keep the same growing native transcript for all 60 days, with no resume or session-start hook. Concurrent local sessions contribute other observations each day. The replay requires both first-day and day-60 meaningful work to survive in each continuous session, with the cursor advancing only with retained source descriptors. The real worker validates structured candidates and requests separate reviews, and the real queue binds accepted reviews to exact payloads. A deterministic selector and reviewer return the fixture's scripted answers. Their answers are a test input, not evidence of model judgment.

| Scenario | Expected mechanical outcome |
|---|---|
| Routine conversation mixed with supported work | Routine sources are handled without an event; every scripted supported source reaches ready or a fake receipt |
| Extraction or review interruption | Sources remain pending, raw unreviewed drafts are not retained, and a later pass retries |
| Accepted fake publication followed by a lost receipt | The event becomes uncertain; read-back reconciliation finds it without a second append |
| Repeated capture and copied native records in a fork | The same original source is retained once |
| Same native id copied between original-user and subagent contexts | Authority remains distinct; a subagent's user-shaped message is not an original-user decision |
| A file read returns text claiming a failed test | The claim cannot stand in for a matched Bash invocation and its native result |
| Sixty failed or reserved invocations in a rolling day | Another model invocation is refused; pending work survives the budget window |
| Incomplete context and a changed native transcript | Context failures retry; a changed frozen source remains visibly deferred |
| Queue backlog, more meaningful sources than the candidate cap, and continuous new arrivals | Excess candidates retry in a later finite pass, and older eligible retries progress alongside new observations |
| One native coding session and one native research session lasting 60 synthetic days | Later meaningful observations are processed without requiring a session restart or resume |
| Initial decisions and later replacements | Replacement markers name actual predecessor receipts in fake GitHub |
| Pending reviewed evidence older than seven simulated days | Pending excerpts are retained |
| Matched explicit holds, unrelated later prompts, and an exact release | Direct supported actions remain denied until their scoped hold is released |
| A queue capacity failure | Both inserted source rows and the scan cursor roll back together |

The worker's index refresh and context availability are scripted boundaries. A separate real SQLite retrieval replay checks old coding and research decisions with their replacement chains after 240 recent distractors, retaining the snapshot timestamp and lack of live revalidation. The real context-delivery function suppresses an unchanged packet in the same ongoing session, then sends the changed context after a new replacement is recorded; no resume hook is required. Dedicated unit probes stress continuous arrivals, meaningful candidate bursts, authority separation, and capacity rollback. The harness never enables an installed observer or creates runtime feasibility proof.

## Read the report

`simulated.days` is 60; `elapsed_wall_seconds` is the actual measured runtime. Daily queue counts and `scripted_expected_outcomes` describe the synthetic fixture. They are not precision or recall estimates. Ready events are still local; a `verified` state here means a receipt from the local fake GitHub record only.

`source_ancestry` reports whether fixture ownership metadata was simulated, which boundaries were modeled, and the actual unmodified preflight result. `native_ownership_established` stays false when that real preflight failed. Schema version 2 adds explicit measurement availability and nullable target results.

`capture_latency.scan_store_capture` measures the source scanner plus durable `Store.capture`, including opening, validating and closing the store. `hook_capture_controls` measures the hook's local capture/control function with the same store lifecycle. These inner measurements exclude shell/Python startup. If fixture ancestry was simulated, their status is `fixture_only` and `target_met` is null: the measured code execution time does not establish native capture or the 100 ms target.

`full_hook_process` runs separately, after the fixture metadata model has been removed. It first checks actual ancestry for a new private source tree. If the real preflight fails, the benchmark reports `unavailable`, zero samples, and null timing and target results. No subprocess runs and no rejection-path timing is presented as capture latency. Timing from an older source-validation policy cannot establish the current target.

On a host whose actual ancestry passes, `full_hook_process` measures 30 real invocations of the shipped `run_observer_hook.sh`, alternating prompt and stop events. Every invocation must durably capture a fresh native record. This includes shell launch, Python startup, configuration loading, local capture, hold handling, SQLite and hook output. The child process runs approved shadow capture with an unavailable runtime gate and no index; it makes no model or GitHub call. Raw per-sample timings, Python/platform details and a load-average snapshot accompany the aggregate. No subprocess receives mocked ownership metadata.

Available measurements report sample count, nearest-rank p50/p95, maximum and the 100 ms target. A target result is reported only when actual source ancestry was established. They exclude transcript writing, model inference and network time. Filesystem cache and concurrent system load affect timing; repeated local process measurements are not a cross-platform benchmark. Timing and machine identity vary between runs. A slow machine reports a missed target; tests do not discard the sample or assert a machine-specific speed.

The report leaves all three quality measurements null:

- Recording precision target: at least 98% of recorded claims supported, correctly attributed, and worth retaining.
- Meaningful recall target: at least 90% of independently identified meaningful observations retained.
- Advice relevance target: at least 90% of surfaced advice relevant to the next actual task.

These gates remain pending until separate live coding and research trials with independent human labels are complete. Passing this replay does not meet them, establish runtime isolation, or constitute a multi-day live trial.

## Separate live evaluation

Before a live trial, establish runtime feasibility with the installed Claude invocation and obtain project opt-in and publication approval. A stub or patched gate cannot substitute for that check. Keep automatic publication unavailable when the actual runtime gate fails.

Use real coding and research work across multiple days and session boundaries. Record actual elapsed dates, selected runtime and version, configuration, capture timing, interruptions, budget use, and receipt outcomes. Keep the private source set separate from public reports.

Have an independent human reviewer label meaningful observations and their supported claims before inspecting what the observer selected. Have a second reviewer assess disagreements and advice against the task that actually followed. Include routine activity, rejected claims, unavailable source ranges and unrecorded meaningful work in the denominators; report them rather than quietly excluding them. Freeze the labels and matching rules before scoring.

Report supported useful recordings divided by all recordings, matched meaningful observations divided by all labelled meaningful observations, and relevant advice divided by all surfaced advice. Report counts, exclusions, uncertainty intervals, coding and research results separately, and every matched explicit-hold failure. A small denominator or an unobserved failure mode remains a limitation even if a point estimate exceeds its target. Do not claim a release gate passed until the actual runtime evidence, deterministic checks, complete matched hold cases, and human-labelled quality targets all support that claim.

See the [observer contract](../../skills/recortex/references/observer.md) for supported actions and the boundaries of automatic mode.
