# Optional active observation

The observer selects durable results, failures, decisions, corrections, ideas and questions from coding or research work. It is off until separately enabled and approved on this machine. Repository trust alone does not enable it. The ordinary briefs and manual recording workflow continue to work without it.

GitHub remains the shared record. A private local queue holds work that is pending; queued, reviewed and posted are different states. A local capture receipt does not mean a result has reached GitHub. The queue is not a transcript backup: capture stores source pointers and hashes, and the native Claude transcript must still exist when a worker resolves them. Missing, changed or oversized sources defer work visibly. No lossless-session or exactly-once claim is made.

## Enable and inspect

Run from the project root, using the installed skill's `scripts/` directory:

```bash
python3 scripts/observer.py status
python3 scripts/observer.py enable --mode shadow
python3 scripts/observer.py enable --mode shadow --yes
python3 scripts/observer.py runtime-check
python3 scripts/observer.py pending
```

The first `enable` previews the scope; `--yes` records local approval for the exact settings. Shadow mode evaluates candidates without publishing. Automatic publication requires `enable --mode automatic --yes` and a successful runtime check for the current installed Claude executable and configured model. Changing settings requires renewed approval. This uses the existing Claude account; there is no alternate provider or API-key fallback.

To choose the observer's model explicitly, preview `enable --mode shadow --model opus` (or another supported Claude alias or model ID), then repeat it with `--yes`. Omitting `--model` preserves an existing observer choice. With no choice, the observer uses the isolated CLI default: user and project Claude model settings are not loaded, so this may differ from your interactive session. The preview names this case as `isolated_cli_default`; `--model ""` explicitly returns to it. The runtime check reports both `requested_model` and the actual model announced by Claude, and `status` retains them under `runtime_last`. A changed model needs renewed approval and a new successful runtime check. These controls do not edit your Claude account or global settings. See the [Claude CLI model and settings flags](https://code.claude.com/docs/en/cli-reference).

The runtime check must establish structured output, disabled tools and MCP, isolated settings, and absence of hook recursion. If that proof is unavailable, captures remain queued and independent model checks do not run. Merely finding `claude` or passing a unit test does not establish runtime compatibility. Verify `status`; do not describe automatic operation as active when the gate is unavailable.

Capture also requires a safe native transcript path. The transcript and its project directory must belong to the current user, and every ancestor must belong to that user or root. Symlink components and group- or world-writable paths are refused. A sticky ancestor outside the source tree is allowed when owned by root or the current user, such as a trusted shared temporary directory. A directory's owner can replace its children even with mode `0755`; matching group names or empty group-member lists do not prove that a writable group is private.

`status` reports the metadata-only `capture_preflight` check, fixed reasons such as `source-permissions`, and boundary names such as `native-config`, `native-projects` or `ancestor`. `status --source-details` additionally displays the failing directory paths, modes and ownership, without reading any transcript contents or changing permissions. Review only those exact paths in your own terminal. For a confirmed directory owned by you that is not intentionally shared, `chmod go-w -- '/exact/reported/directory'` removes group/other write bits without changing read access. Do not run that placeholder literally, use recursive chmod, change another owner's directory, or change shared storage merely to bypass this check. Rerun the diagnostic afterwards. A failed check leaves capture unavailable; the observer does not alter Claude's files or directory permissions.

A user namespace or execution sandbox may expose a system ancestor's owner as an unmapped identity. The observer reports `source-owner`; it does not guess that an unknown owner is root. Repeat the diagnostic in the normal host terminal where ownership can be established. Do not change ownership of system directories to satisfy the check. Offline fixtures may explicitly simulate trusted system metadata, but that simulation does not establish real capture readiness.

```bash
python3 scripts/observer.py run-worker --dry-run
python3 scripts/observer.py context --query "pipe reader" --issue 7
python3 scripts/observer.py holds
python3 scripts/observer.py pause publication
python3 scripts/observer.py resume publication
```

Pause and resume accept `capture`, `publication`, or `all`. A capture pause keeps established holds and their explicit release controls active. `record --source SOURCE_ID` retries an existing queued observation through the same review path; it does not accept arbitrary model-provided file paths. `reconcile EVENTKEY --dry-run` previews receipt reconciliation. Status and pending output distinguish local backlog, deferred evidence, budget exhaustion, uncertain publication and verified receipts.

## Capture, review and publication

Command hooks collect bounded source descriptors from the native transcript. They make no model or network calls. A cursor and its descriptors commit together to the private state database. Session and subagent starts also provide local context; prompts, tool completions and failures, stops and compaction provide collection opportunities. Before supported tool actions, a synchronous transcript scan checks for original user controls. `SubagentStop` is an event; `SubagentHandback` is a tool whose report is collected from its recorded message. See the [Claude hook contracts](https://code.claude.com/docs/en/hooks).

The hook can launch a finite worker after the runtime gate passes. The worker exits when idle; later hooks can restart it. It uses separate bounded calls for selection and a fresh evidence/privacy review. Models receive source roles, direct tool results and current record context as data; an assistant's report remains a report. They cannot execute tools or publish. Existing issue destinations, event templates, restrictions and recorded links still apply. Ambiguous destinations, overlap or evidence defer a candidate.

Only the independent publisher appends reviewed event comments. It freezes the exact payload and source identities, records the sending state before the request, reads the stored body back and retains the receipt. An interrupted send becomes uncertain and is reconciled before retry. Ambiguous or conflicting receipts require investigation. Source identities coordinate the main session's event comments with observer candidates; overlapping unresolved claims are refused. Mutable topic bodies, hub text, labels, resume comments and boards remain the main session's responsibility.

In a locally approved observer project, `post_verified.py comment` requires `--eventkey KEY` for a memory comment. Use `observer.py pending` to find the reviewed event, and `observer.py pending --eventkey KEY --show-body` to explicitly display its reviewed body. The issue, kind and draft file must match that event exactly; the script uses the shared publisher and receipt, so retrying a verified event does not append another comment. It does not accept a changed draft as a new review. Shadow mode retains the event locally; publication requires a successful real runtime check and renewed local approval for automatic mode. Projects without local observer approval keep the ordinary posting workflow.

`create_issues.py` refuses a manifest containing memory comments for an approved observer before it writes any issues or comments. Create the destination issues first, then record those memories through the shared queue. Non-memory comments keep their existing workflow. These checks coordinate the repository's posting helpers; they do not intercept arbitrary `gh` commands or browser posts.

The first release binds approval and publishing to one installed machine per repository. Several sessions on that machine share the queue and publisher lock. Automatic failover and distributed publishing are outside this contract. This is a single approved installation assumption, not distributed fencing: cloned user keys and machine identity can undermine that assumption. Do not copy an approval or its private state to another host.

## Task context and explicit holds

Task context uses the same allowed authors and memory interpretation as the mind page, ranked for the task and its thread. Recorded decisions, failures, support and replacements travel together. It preserves full selected fields, including qualifications and denominators; a group that cannot fit is omitted with a source pointer. Excluded support is identified without exposing its text. Resume text is rechecked against the current allowlist and matching unedited comment. The packet states its cache age and missing evidence. It is quoted data, never permission to act. Repeated identical packets and operational notices are suppressed within a session.

Only a recognized whole original user message creates a local hold. Supported messages are:

```text
/recortex hold git-tag
/recortex hold git-push
/recortex hold gh-release
/recortex hold command EXACT_COMMAND
/recortex release HOLD_ID
```

These are control text entered as a whole message; the hook interprets the persisted original message. A small set of whole phrases such as `do not push` is also recognized. Arbitrary natural language is not a policy parser. Holds apply to this repository unless an explicit scoped control is supplied through the API. Only an explicit release of the hold ID clears one; compaction, elapsed time or omission from a summary does not release it.

The local check can deny direct supported Bash calls for Git push, Git tag mutation, GitHub release mutation, and an exact command string. Read-only commands and commands explicitly targeting another repository do not inherit this repository's hold. Shell aliases, compound programs, scripts, unknown directory overrides and arbitrary tool families are outside the check. It does not enforce a universal shell policy. Ordinary context and advice never deny a tool, and an asynchronous warning cannot stop a completed action.

UserPromptSubmit can run before its transcript line is written and can represent an initiated turn rather than a person. Hook prompt text alone therefore cannot prove user authority. PreToolUse rescans persisted original sources; if those sources lag, a new hold may not yet be enforceable and a fixed warning says so. Check the acknowledged hold with `holds` before relying on enforcement. Existing Claude permissions remain in force.

Recognized explicit control quotes are a narrow local-storage exception: they are stored without model review so local enforcement can work, after mechanical secret and restricted-pattern checks. They are not sent to a model for classification. Narrative source text is not archived by capture. Independent review may retain the minimum evidence needed by a candidate; the reviewer must reject unnecessary sensitive text. Pattern checks and framing are limited defenses, not proof that arbitrary text is harmless.

## Limits and recovery

Defaults are 60 actual model invocations per project in a rolling 24-hour window, including failed calls and both selection and review, at most 48,000 input characters per call and four candidates per batch. Configured resource limits may reduce those ceilings. A full budget leaves pending work queued. The default backlog cap is 128 MiB or 10,000 pending observations. Reaching a cap stops accepting new capture without advancing its cursor and leaves a persistent degraded status. Pending evidence is never silently expired.

The pending database lives under the private state directory, separate from the rebuildable index in the cache directory. It uses synchronous SQLite transactions, private permissions and checked paths. It must not be deleted as a cache repair. Corruption is reported; it is not silently reset. Reviewed excerpts of handled events become eligible for cleanup after seven days by default, configurable to a shorter interval; pending evidence is never silently expired. A finite worker checks cleanup daily while the plugin is active, including when capture is paused or the model runtime is unavailable. This is not a wall-clock retention daemon. Identity metadata, explicit holds and publication receipts remain. Native Claude transcript retention is managed by Claude, independently of this queue.

After interruption, inspect `status` and `pending`, restore source availability where possible, and reconcile uncertain sends before retrying. A verified receipt establishes the stored comment matched the frozen draft at read-back; it does not establish that the underlying research claim is true. Local queued work still needs review and publication before another machine can recover it from GitHub.
