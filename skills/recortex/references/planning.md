# Planning the paper

For a project whose profile has a plan (the manuscript profile): the paper's plan lives in the record, a plan issue with one dated comment per planned part. The session plans on its own, shows the author the plan once, and keeps it current afterwards. The format of a part and the rules that compute its state are in [issue-model.md](issue-model.md), "The plan of the deliverable"; the commands are `plan.py` and `index.py query plan`.

The author is one person. There are no coauthor flows: nothing here asks anyone but the author.

## Plan the paper

Triggers: the session brief says "Start the plan run in the background now"; or the user asks ("plan the paper", "what is missing for the paper", "review the plan").

1. `plan.py setup` if there is no plan issue (it makes the label and the issue, links it under the hub and writes `plans` into the project file). With no plan recorded in the project file it first makes one read for an open issue that already carries the label `plan`, as a project that joined a record with a plan, or whose file lost its `plans` entry, would meet: there it creates nothing, a dry run says the same, and the one fixed line names the issue's number and says to add `"plans": {"manuscript": N}` to the project file (or to take the label off that issue). It also stops, creating nothing, when GitHub cannot be read for it. Then `index.py sync` and `plan.py digest --out DIR`. The digest is data: what the record holds per section and topic, the figure and table ids that exist, the standing plan, at most 40 memories per thread.
2. Start the planner in the background, with a scratch folder for its drafts, and go on with what the user asked. Use the agent `record-planner` (it runs on Fable). Claude Code does not fall back to this session's model by itself: an agent whose model is not available ends at once with an error that says "There's an issue with the selected model ... It may not exist or you may not have access to it" (HTTP 404, `model_not_found`) and nothing runs on another model. If it does not start, or returns a model or permission error and no drafts, start `record-planner-session` once (the same instructions, on this session's model) and begin the page shown to the user with the fixed line "The planner ran on this session's model, because Fable was not available here." The same line goes into the adoption decision's "Where recorded" field. If you cannot tell which model the planner ran on, say which model this session runs on when you show the plan.
3. When the planner returns, start `record-reviewer` in plan mode: give it the drafts and the digest, never the planner's reasoning. Fix what it found; an objection the record cannot answer is a question for the author. Run `plan.py check --drafts DIR` until it passes.
4. At the next pause show the author one page: the message; the claims with what backs each; the figures and tables; the discussion in outline; the gaps; the planner's questions as multiple-choice prompts. Ask once: adopt, adopt with changes, or not now.
5. On yes: post the adoption decision with the author's exact words (a decision on the plan issue), then `plan.py post --drafts DIR --decision <the decision's comment id>`, then `plan.py render`. On "not now": keep the drafts folder and ask again at the next session start; `plan.py off` only when the user says to stop planning.

`plan.py post` refuses, each with one line that names the draft: a first plan with no adoption decision; a message that links no decision; a revision that widens or reverses a claim and links no decision; a plan that drops a part the author agreed (only a decision drops an agreed part); a decision that is not a told decision of an allowed account. Drafts name each other with `NEW-<file name>` (a draft's file name, without `.md`, is its key); `post` posts them in dependency order and replaces each key by the id just posted. A rerun goes on from `plan.state.json` in the folder.

## What the planner does

`agents/record-planner.md` is the brief. It states the one message the standing results can carry; names the fewest claims that carry it, each in its section and at the strength its results allow; chooses the one figure or table that would convince a skeptical reader of each; plans the discussion (an interpretation per main claim with the explanations it must rule out, every limitation the results state under "Not established" and every failure on a feeding topic, comparisons only with work the record or the author names, implications no further than the claims); lists the gaps, each with an experiment as an idea draft that links its claim; and writes one draft per part. A statement holds a number only when a finding it links holds it. It never names a paper from memory, and missing is not zero. The sheet's "What counts as proof" line decides what a claim needs.

A planned part is named by the id of its plan comment. The mind page's manuscript view puts that id, and no word of the plan's line, into the request that its "write this part" button copies ("Write this part of the Results: planned part 9302."); read the part with `index.py query plan` and follow "Write the text" in [writing.md](writing.md).

## Keep the plan current

Triggers: the session brief says the plan is behind the record; or a result was just recorded on a thread that feeds planned parts (`index.py query recall --issue N` lists them, with their states).

| What happened | The session | The author |
|---|---|---|
| A new result bears out a part | `checks=<part id>` in the result's marker | not asked |
| A new result speaks against a part | `against=<part id>` in the marker; then a revision that narrows the claim, with the session's reason, or the question below | asked only when the fitting change widens, reverses or drops an agreed claim, or touches the message: "revise to (wording), drop it, or keep it" |
| A part rests on something that changed | a revision that rewords or narrows; else as above | as above |
| A stated limit is not planned | a `limitation` part, added by the session | told at the next pause |
| An agreed claim has no figure or table | a display part, added by the session | told at the next pause |
| Three or more findings are used by no part | the planner in update mode in the background, then the reviewer; its additions are posted as added by the session; anything that needs the author is asked together; when nothing changes, a checkpoint on the plan issue says so and resets the count | asked only as above |

A revision is a new plan comment with `replaces=<the part>` and a **Change** field. The session may narrow or reword an agreed claim on its own; widening, reversing or dropping it needs the author. A narrowed or reworded revision of an agreed part is agreed itself. After any of these, `plan.py render`. What was added or revised is told to the author in one line each at the next pause, with "added by the session".

When `post_verified.py` prints "This thread feeds N planned parts and this result links none; check the recall block.", read the recall block and add `checks=` or `against=` if the result bears on a part.

## Before compiling

`plan.py status` before a deliverable is assembled, and a plan update when a part is in doubt or has moved. `compile.py` prints the gap counts and never refuses.

The mind page's "Read the manuscript" shows the plan beside the text as the user reads the paper: how many planned parts each section has backed, and, in dashed boxes under the part they name, the planned parts the text does not say yet ([mind.md](mind.md)).

## Turning planning off

`plan.py off` writes `"planning": false` in the project file, or the user's own `settings.json` can hold `"planning": false`; the hooks then say nothing about the plan. `plan.py on` removes the key. A project file can switch planning off; it can never switch the plugin's writing on (that is a setting of the user's machine, see `writing.md`).

## A filled example

The planned claim of the invented project `octo-org/sample-project`:

```text
<!-- ghim:event type=plan at=2026-11-16T10:10-05:00 because=2900000140 -->
### Plan: 2026-11-16 10:10 EST - claim: wetter topsoil in dry spells

**In plain words, we plan to:** claim that a rye cover keeps the topsoil wetter during dry spells

**Part:** claim

**Section:** Results

**Topic:** #5

**Statement:** In dry spells the plots with a rye cover stayed above the wilting point on more spells than the bare plots.

**It needs:** The dry-spell comparison in both seasons.

**We have:** The dry-spell result.

**If it does not hold:** The paper reports the season effect only.

**Next step:** Answer the texture question, owner the session
```
