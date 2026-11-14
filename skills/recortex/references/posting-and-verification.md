# Posting and verification

> Commands on this page write `scripts/<name>` for short. That is the `scripts` folder of this skill, next to `SKILL.md`; call it by its full path.

Every post goes through the same five steps. The scripts do steps 3 to 5; steps 1 and 2 are yours.

## 1. Draft to a file

Write the draft from its template into the drafts folder. One file per issue body or comment. Do not compose a body inside a shell command: quoting damages backticks, dollar signs and tables, and the text cannot be reviewed or linted.

Keep the drafts folder clean. Notes, extracted tables and other scratch files go somewhere else. Posting commands take explicit file names (`--body-file`, or the manifest), never a folder or a wildcard, so a stray file cannot be posted by accident; a clean folder also keeps a reviewer from reading the wrong file.

## 2. Check against the sources, then get an independent review

For every number in the draft, open the file named as its source and read the value there. For every quote, find the original message. For every identifier (commit, job, path), check that it exists. Memory, including a compaction summary, is not a source.

For a new topic body, a change to the deliverable text, a hub edit, or anything the deliverable will cite, ask a reviewer that does not share your context (a fresh agent, or a person) to try to break the draft. Give it the draft and the source paths, not your reasoning. Ask it to look hardest at: numbers against sources; denominators; mixed runs; exclusions and failures; claims that go beyond the evidence; things stated as negative that were only not tested. Fix what it finds, and if the review did not return, the draft is not reviewed.

Routine result and checkpoint comments need the source check but not always the reviewer; decide by what a wrong post would cost.

## 3. Lint

```bash
python3 scripts/lint_drafts.py --kind result --repo owner/name --online draft.md
python3 scripts/lint_drafts.py --kind decision --index --issue 5 draft.md     # a memory, against the local index
```

| Check | Level |
|---|---|
| Required sections present, in order, not empty | error |
| Event header with date, time and zone | error |
| Decision carries a quote block, or says the words are not available | error |
| `{{template fields}}` or `NEW-<key>` left in the text | error |
| Bare `#N` that does not exist in the target repository | error (`--online` or `--known-numbers`), otherwise warning |
| Body over 65,536 characters | error; warning when close |
| Mermaid: `#`, parentheses, unquoted labels, nodes undefined or unused | error |
| Resume comment: heading with timestamp, markers, all six fields, block length; resume markers inside a hub body | error |
| Final record: title line, at least one section, every section filled, "Sources" last | error |
| Final record: an "As of" line with a date, month or year, a date and time in the text, a `<details>` block, a heading for decisions, open work, provenance, next steps or the other sections of the older record form (current state, not established, code and evidence, related topics, history, status), a dated heading outside "Sources" | error |
| Event comment: the marker line `<!-- ghim:event type=<type> at=<ISO time> -->` first, with the type of the comment and the same local time as the heading | error |
| Final record: a figure block without a caption, an embedded image or a Sources row; a table block without a caption, a table or a link, or a Sources row | error |
| Final record: a figure or table linked from a branch such as `main` instead of a commit or tag | warning |
| Final record: a line that looks like a section heading but is not one (`### Results`, `##Results`, indented, underlined, inside a code block) | error |
| Final record: a `## ` heading that is not a section heading of the project's profiles, or that two profiles share and the labels do not settle | error |
| Final record: section labels that disagree with the headings (`--labels`, or the labels in a manifest) | error |
| Memory: the marker holds only `type`, `at`, `recorded`, `source` and the link keys, each once; a link value is one to four comment ids; an id stands under one key; a link only on a kind that may carry it, never on a checkpoint, a triage comment or a reconstructed memory (`marker-key`, `link-value`, `link-kind`) | error |
| Memory: the plain line, first, one line of 20 to 200 characters, without a final period, backtick, `#N`, URL, path or commit, not repeating its label, a question starting with a question word, every number also in a field, as written or rounded (`plain`) | error |
| Memory: Evidence on a result, decision, failure or correction only, last, at most six lines in the four forms, a line range of at most 120 lines, a commit and not a tag (`evidence`) | error |
| Memory: folds after the last field, each well formed, within the limits, a word used outside its fold and explained once; no `<details>` or `<summary>` outside a fold; no fold on a triage comment (`fold-format`) | error |
| Memory, with `--index` or `--online --issue N`: each link target exists in this repository, is a memory, is not later, may be linked, and was written by an allowed account (`link-target`); a cited comment exists on its issue (`citation`); a table or figure is a block of the topic body and a pull request is one (`evidence`); a thread fold stands on an issue with at most one `topic:` label (`thread-issue`); "We use" and "This thread is about" only for what is not explained yet, "We now use" and "is now about" only for what is (`word-known`, `word-new`, `thread-known`, `thread-new`) | error |
| Memory, without a lookup, or where the lookup could not tell: one warning that names what was not checked (`not-checked`) | warning |
| Secrets (common token formats) and the project's `restricted_patterns` | error |
| The project's `banned_words` | error |

The linter does not judge content. It cannot know whether a number is right.

## 4. Post

| Task | Command |
|---|---|
| Several new issues and comments | `create_issues.py --manifest <file>` |
| One comment | `post_verified.py comment --issue N --kind <kind> --body-file <draft>` |
| Replace an issue body | `post_verified.py body --issue N --kind <kind> --body-file <draft> --backup <file>`; for a final record it also moves the section labels to follow the headings, and removes one only with `--allow-label-removal` |
| Convert a record body of an older form, keeping it as a comment | `convert_body.py --issue N --body-file <draft> --expected <saved live body> --note "<when and why>"` |
| A new resume comment on the hub | `resume_block.py update --file <draft>` |

`post_verified.py comment` lints a memory against GitHub, so a dangling link, a missing cited comment or a wrong evidence line stops the post; a failure of GitHub other than "Not Found" stops it too. With `--dry-run --offline` it checks against the local index instead and reads no network; without a usable index it says so in one line and gives the single `not-checked` warning. A memory of a kind that may link but carries no link gets one note before it is posted: "note: this memory links to no earlier one; ...". It is a note, not an error: add the link first if it follows from, replaces or tests an earlier memory. After a verified post of a memory it prints `memory id: <id>`, the id to link to it. `create_issues.py` lints offline first and checks each comment against GitHub just before posting it; a comment of a manifest cannot link to another comment of the same manifest, so post such a memory afterwards with `post_verified.py`.

Run each with `--dry-run` first; it prints the exact `gh` commands. `post_verified.py body` prints a diff against the body now on GitHub and saves that body to the backup file before editing. To edit a body, start from the current one: `gh issue view N --json body --jq .body > draft.md`, or `gh api repos/owner/name/issues/N --jq .body`.

## 5. Read back and compare

After each write the scripts fetch the stored text from the API and compare it with the draft, ignoring only line-ending style and trailing whitespace. `verified:` means equal. `MISMATCH:` shows the first differing line; the usual causes are a body changed by someone else in between, or text altered on the way in. Resolve it before going on, and do not report the post as done until it verifies.

If you ever post by hand, do the same check by hand:

```bash
gh issue comment N --body-file draft.md          # prints the comment URL, ending in the comment id
gh api repos/owner/name/issues/comments/<id> --jq .body > back.md
diff <(sed 's/[[:space:]]*$//' draft.md) <(sed 's/\r$//; s/[[:space:]]*$//' back.md)
```

## What the scripts never do

They never delete an issue, comment or label, change no label on an issue except section labels that follow its headings, never edit a posted comment, never close or reopen issues, and never post a file that was not named on the command line or in the manifest. Closing a finished task is a deliberate `gh issue close N` by the session or the owner.
