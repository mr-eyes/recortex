# Tasks, steps and checkmarks

A plan the user agreed to becomes a task with numbered steps, and a step the agent reports done is checked. The record stays the authority: everything shown as a checkmark is computed from memories, and a box on GitHub is only a copy.

## The pieces

- A task is an `active` issue, as before ([issue-model.md](issue-model.md), "Tasks, steps and marks").
- A `steps` memory is a comment of the kind `steps` on the task. It holds the plain line ("In plain words, we agreed to: ..."), **Agreed** (`Approved in the plan dialog, <date time zone>.` or `By <name or role> in the chat, <date time zone>:` and the user's quoted words), **Steps** (1 to 20 numbered one-line steps) and **Done when**. A later `steps` memory on the same task adds steps. One with `replaces=` (a `steps` memory of the same task) restates the list it replaces and takes its place in the numbering. The template is `templates/comment-steps.md`.
- The standing "To do" task: one `active` issue titled "To do" per project, opened with the first item. Each item is a `steps` memory of one step, with the user's words quoted. If two issues carry that title, the lower number is used and `tasks.py status` says so.
- A mark is a marker key on a later memory. `done=<steps comment id>:<n>[,<n>...]` goes on a result, a failure, a correction or a checkpoint; `dropped=<...>` goes on a decision. One to four groups joined by `;`, step numbers 1 to 20 (`done=9108:1,2;9112:3`). A mark is not a link.
- Steps are numbered 1 to N across a task's standing `steps` memories, oldest first. People see those numbers; the record stores the comment and the number inside it.

## What a step is

The first rule that applies:

1. dropped: a told decision marks it with `dropped=`;
2. done: a told memory marks it with `done=`;
3. blocked: a failure names the task and nothing followed;
4. open.

Told means posted by an allowed account and never edited by another. A mark on a memory that a correction replaces counts for nothing. A `steps` memory that is not told, or that a later one replaces, gives no steps, and a mark that points at one, or at a step number it does not have, counts for nothing; `index.py query consistency` reports it as `mark-target`. A step that is only started, in progress or partly done is never marked.

## Where the checkmarks show

| Place | What shows |
|---|---|
| The task issue on GitHub | Under `## Plan`, a task list, `- [x] 2. step text`. GitHub shows "3 of 5". Written by `tasks.py render` |
| The session brief | One fixed line, "Tasks: 2 open, 7 of 12 steps done; next: step 4 of #14.", and inside the data frame at most 5 lines "- #14 step 4: (text)" |
| The prompt reminder | A segment of integers, "; steps: 7 of 12 done", dropped first if the payload would pass 400 characters |
| The status line | " · steps 7/12" after the age |
| The mind page | "What is there to do?": each open task, its steps as a checked or open list, each done step with the plain line of the memory that marked it and a link; then the tasks closed in the last 30 days and the observer's acts of the last 7 days |
| The board | The Why mark gains the count, for example `In Progress 3 of 5 steps` |
| `/recortex:todo` | The same list in the chat, from `tasks.py status` |

## Rendering, closing and boxes changed by hand

`tasks.py render --issue N` replaces only the text under `## Plan`, up to the next `## ` heading, with the computed list. It writes through `post_verified.py body --section "## Plan" --expected-body-file BASE` (BASE is the body it just read), which refuses a body that changed meanwhile, keeps a backup and reads back. A body with no `## Plan` heading is left alone and reported. After the list the plugin writes `<!-- ghim:list sha256 ... -->`, a mark of what it last wrote, so that a list changed by hand is told from one that is only behind.

A box ticked or unticked on GitHub is not believed. Code does not render a task whose list was changed by hand: it counts it, the brief says "Tasks: 1 list was changed by hand on GitHub. Load the recortex skill and run its tasks.py status.", and the session asks the user: "Record it" (a checkpoint with the mark, by the user's word, through `tasks.py mark`) or "Put it back" (`tasks.py render --issue N --restore`).

`tasks.py close --issue N` works when every step is done or dropped and at least one is done: it writes the "Result" section (one line per memory that checked a step, with its address), renders, and closes the issue as completed. A task with no steps is never closed by code. `tasks.py reopen --issue N` reopens it and puts "Not finished" back. The observer closes finished tasks at the level Everything, asks first at Assistant and never at Notes only ([observer.md](observer.md)).

## The commands

Every command that writes has `--dry-run`, which lints and prints the exact `gh` commands and writes nothing to GitHub. After a post, `add`, `mark` and `drop` bring the index up to date and render the task, unless `--no-render`.

| Command | What it does |
|---|---|
| `tasks.py status [--json]` | The tasks, their steps with the memory that checked each, and what is out of step. Read-only, from the index |
| `tasks.py render --issue N [--restore]` | The list under `## Plan` made the computed one |
| `tasks.py close --issue N` | Result, render, close as completed |
| `tasks.py reopen --issue N` | Reopen and put "Not finished" back |
| `tasks.py open --dir D` | Open a task from a drafted `steps` memory: `D` holds `steps.md` and `task.json` (`{"title", "topic"}`); one `active` issue and the memory as its first comment, through `create_issues.py`, so a rerun opens no second task |
| `tasks.py add --text-file F` | Put a to-do item, the user's words, on the "To do" task, opened first when there is none |
| `tasks.py mark --task N --steps 2,3 --words-file F` | A checkpoint on the task with `done=` for those steps, by the user's word |
| `tasks.py drop --task N --steps 4 --words-file F` | A decision on the task with `dropped=` for those steps, by the user's word |

`/recortex:todo` runs these for the user (`add <text>`, `done <n>`: item `n` of the "To do" task). When the user approved a plan in plan mode and the observer is off, `/recortex:record` drafts the `steps` memory and runs `tasks.py open`.

## Who writes what

With the observer on, the observer drafts and posts `steps` memories and marks within its level, and code renders every list and Result section. Without it, the main session does the same through these commands, after the user's yes. A model never writes a list, a Result section or a `done=` value into GitHub: the observer's models give task and step numbers, and code composes the marker from the index when the comment is posted. An older version of the plugin reads a `steps` comment as an ordinary comment and ignores `done=` ([upgrading.md](upgrading.md)).
