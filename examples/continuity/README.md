# A fresh session can recover what was recorded

This example checks the storage and retrieval path after the earlier session's local files disappear. It also checks the boundary: a note that was never posted does not come back. It does not run Claude or demonstrate that a model will follow the recovered record.

## Run the offline demonstration

From the repository root, with Python 3.8 or newer:

```bash
python3 examples/continuity/run.py
```

For machine-readable checks and measured wall times:

```bash
python3 examples/continuity/run.py --json
```

The script uses the existing [invented reader project](../octo-repo/chain/README.md) and [fake GitHub CLI](../../tests/fake_gh.py). It needs no credentials, invokes no real `gh`, and removes its temporary files when it finishes. It ignores inherited plugin configuration and fake-CLI fault settings.

1. In session A, the real label, issue-creation and verified-posting scripts write the example record to the fake GitHub store. Five linked memories record an idea, its failed test, the decision it caused, an open question and a replacement decision. A task checkpoint and a new resume comment are posted too.
2. A separate local note is written but never posted. The script removes session A's whole temporary folder, including that note, its drafts, its index and its local state.
3. Session B has only fresh configuration and an explicit trust entry. Each command runs in a new process. The real index script rebuilds from the fake GitHub store; recall, timeline, resume and SessionStart commands read it back.
4. Assertions check the failed result, causal links, superseded decision, open question, checkpoint, next action and release hold. The unposted note must be absent from both the remote record and every inspected recovery output.

The script prints one `PASS` per assertion and exits with an error if one fails. Its timings describe this machine and the local fake, not GitHub latency or Claude performance. `claude_evaluated` is `false` and `token_usage` is `null` in the JSON output. The same demonstration runs under unittest:

```bash
python3 -m unittest discover -s tests -p test_continuity.py
```

## Evaluate Claude in two real sessions

This is a procedure to run, not a report of results. Use an invented project in a disposable private GitHub repository. Keep the evaluator's prompts, scoring sheet and receipts outside both Claude workspaces. Do not use confidential project material.

Record the date, Claude Code version, exact model, recortex version and commit, enabled plugins, relevant memory settings, and repository visibility. Keep these fixed across repetitions. Run at least five trials with fresh records, and report each trial rather than selecting the best. A useful comparison uses the same task and model with recortex disabled; document any other persistence mechanism enabled in that comparison.

### Session A: establish and verify the record

Set up recortex in the scratch repository, including its trust entry. Tell Claude that the following is a **fictional evaluation scenario**, not an experiment it actually ran. Present the facts in order, in separate turns:

1. The project reads CSV files. Initially, the owner chooses strict quoting as the default and a permissive mode for users who request it.
2. An idea is to preserve the current line splitter and carry an open-quote flag between lines. The supplied fictional test result is that **4 of 6 mixed-line-ending cases fail** with that patch. It is not an acceptable implementation.
3. The owner says: **"Stop patching the splitter. Write the state machine."** The next implementation should read one character at a time. Record this decision as caused by the failed test and chosen instead of the old idea.
4. The owner revises the quoting policy: **"Keep strict as the default. Lenient mode must warn for each stray quote."** Explicitly replace the earlier decision rather than presenting both as current.
5. Whether pipes work is still unknown. The next task is to write three pipe tests: a small input, an input larger than the read buffer, and a quoted field split across reads. **No passing result exists. Do not tag version 0.3.0 without the owner's approval.**

Ask Claude to record the events, checkpoint and resume block. Verify the returned GitHub issue/comment URLs yourself: read the posted text and its authorship, confirm the failure denominator, links and replacement, and retain the receipts in the evaluator's log. A claim that something was saved is not a receipt. Record any manual corrections needed.

After that checkpoint, give session A a random, trial-specific phrase and explicitly say: **"This is an unposted scratch note. Do not put it in any issue, file or memory."** Keep the phrase in the evaluator's log. Inspect the record to confirm it was not posted; if it was, that trial cannot test missing capture. Do not include the phrase in session B's prompt.

### Session B: recover without the conversation

Close session A. Create a different, empty working directory with only the recortex configuration pointing to the same scratch repository, plus the trust needed to read it. Do not copy drafts, source notes, transcripts, cache, auto-memory or a conversation summary. Use fresh cache/state directories when testing index reconstruction. Start a **new Claude process and a new conversation**, without resume/continue, and record that it began without A's chat history. Inspect any other enabled memory mechanism and disclose it; an automatically supplied summary invalidates the clean-restart condition.

Give this prompt without supplying the answer:

> Read this project's record. Before doing any work, tell me the implementation we chose and why, the failed test result, the current quoting policy, what is still untested, the exact next task, and the release restriction. Cite the issue or comment behind each answer. What was my final unposted scratch phrase? If the record cannot establish an answer, say so. Do not modify the record or code yet.

Score the **first response**, before any correction or follow-up. Read its citations and check that they support the adjacent answers. A plausible answer without a supporting record does not pass the evidence column.

| Assertion | Correct answer | Supporting receipt |
|---|---|---|
| Failure | 4 of 6 mixed-line-ending cases failed | Failure comment |
| Chosen implementation | Character-by-character state machine; stop patching the line splitter | Decision and its cause/instead links |
| Current policy | Strict by default; lenient mode warns for each stray quote; earlier policy replaced | Replacement decision |
| Unknown result | Pipe support has not been demonstrated | Open question/checkpoint |
| Next task | The three specified pipe tests | Question/checkpoint/resume |
| Release hold | No 0.3.0 tag without the owner approving it | Resume comment |
| Unposted boundary | Says the scratch phrase is not available; does not invent it | Verified absence from the record |

For each trial, report correct answers out of seven, supported answers out of the six positive assertions, any invented results, any recommendation to repeat the rejected patch, and manual corrections. Report posting time and recovery time separately. Start the recovery timer when sending B's prompt and stop it when the first response completes. Record actual input, output and cache tokens or costs from the session's usage report, naming its source; if unavailable, leave them blank. Character counts are not token measurements. Report medians and ranges only after collecting the raw trials.

Run compaction as a **separate condition**: stay in A, trigger compaction, then use the same recovery prompt. A compaction summary can retain unposted information, so success there is not evidence of durable capture. For interruption testing, stop before a verified post and record exactly which receipts existed; missing unposted facts are an expected boundary, not a successful recovery. These conditions must not be pooled with the clean-restart results.
