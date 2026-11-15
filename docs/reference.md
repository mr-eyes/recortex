# Reference

The agents, the hooks and the scripts, one table each, and the commands for the mind page and the field sheet.

## The agents

The plugin adds three agents for bulk and audit work, one tier below the main session, which stays the only writer to GitHub: `record-drafter` drafts topic bodies in batches to files only; `record-reviewer` reviews drafts, per batch and across all bodies; `record-auditor` checks the record for drift, untriaged issues and new deliverable signals. The drafter has no shell and no GitHub access. The reviewer and the auditor are read-only by instruction: they need Bash for read commands such as `gh issue view` and `git log`, so their tool list does not enforce it, and the main session checks what they did. Each preloads the skill and uses your default model; to choose another, copy its file into the project's `.claude/agents/` and set `model:`.

## The hooks and the CLAUDE.md snippet

**Hooks.** Four hooks keep every session and subagent aware of the record, built from a local index of the issues (`scripts/index.py`, a SQLite file under `~/.cache/recortex` that can be rebuilt from GitHub at any time; hooks and terminal scripts use the same file):

| Hook | Prints |
|---|---|
| SessionStart (startup, resume, clear, compact, fork) | The record brief: the rules for writing to the record, the hub, the deliverable topics by section, the open active issues, the issues that need triage, and the newest allowed resume block. It syncs the index first; if GitHub does not answer in time it prints the brief from the index and says how old it is |
| SubagentStart | A short brief for the subagent: where the record is, the issues in play, and that it reads the record for facts and posts only when its task says so; with a field sheet you approved, its short part in its own frame |
| UserPromptSubmit | One line with the hub and the active issues, only when it changed or every tenth prompt (`"prompt_hook": {"enabled": false}` in `.recortex.json` turns it off) |
| PreCompact | Instructions for the compaction summary: keep the issue numbers in play and every result, decision or failure that is not yet posted |

They print nothing when the project has no configuration file or the repository is not in your trusted list, and never write to GitHub. Set-up builds the index once (`index.py sync`); otherwise the first session builds it in the background and the brief arrives with its next prompt. In a project that is set up and trusted, a brief that cannot be produced is replaced by one line naming the reason. `python3 scripts/hooks.py --print` shows each payload and its size; `python3 scripts/hooks.py --doctor` shows why a brief is or is not shown: configuration, trust, storage folders with owner and mode, the index and the last sync failure. The plugin installs them. For a plain skill install, merge [`templates/settings-hook-snippet.json`](../skills/recortex/templates/settings-hook-snippet.json) into `.claude/settings.json` or `~/.claude/settings.json` and adjust the paths.

**CLAUDE.md snippet.** [`templates/claude-md-snippet.md`](../skills/recortex/templates/claude-md-snippet.md) is a few lines for the project's CLAUDE.md that give every session the hub's location and the recording rules.

## Scripts

All are in `skills/recortex/scripts/`, use only the Python standard library and `gh`, and accept `--help`.

| Script | What it does | Writes to GitHub |
|---|---|---|
| `setup_labels.py` | Creates the labels of the chosen profiles. Existing labels are left alone unless `--update` | yes |
| `lint_drafts.py` | Checks drafts: required sections, leftover placeholders, bare `#N`, body length, Mermaid rules, secrets, project-defined restricted patterns; history in a topic body; section headings against labels; a memory's plain line, links, Evidence and folds, against the record with `--index` or `--online` | no |
| `create_issues.py` | Creates interlinked issues and comments from a manifest, replaces `NEW-<key>` placeholders with real numbers, reads every body and comment back, and can be rerun after a failure without duplicates or overwriting changed bodies | yes |
| `post_verified.py` | Posts one comment or replaces one issue body, with lint, a backup of the replaced body and read-back; for a topic body, the section labels follow the headings; for a memory, its links and evidence are checked against GitHub and its id is printed | yes |
| `convert_body.py` | Converts a record body of an older form into deliverable text: stops if the live body changed, keeps the former body as a comment, replaces the body and section labels, reads all back | yes |
| `resume_block.py` | `show` prints the newest resume comment written by an allowed account, for trusted repositories only. `update` posts a new resume comment on the hub and reads it back; it never edits the hub or an earlier comment, and warns when the posting login is not allowed | `update` only |
| `snapshot.py` | Saves all issues, pull requests and comments as local Markdown with an index, for offline reading and for surveying an old tracker | no |
| `index.py` | Keeps the local index of the record: `sync` reads what changed on GitHub; `query` answers consistency, impact, recall, mind, timeline, triage and brief questions without network | no |
| `mind.py` | Builds the mind page: one private local file that tells the project's memory in sentences, from the index | no |
| `hooks.py`, `run_hook.sh`, `session_start_hook.sh` | The four hooks; `hooks.py --print` shows their payloads | no |
| `setup.py` | `detect` suggests deliverables and fields from file names; `apply` makes the setup from answers you confirmed; `fields` shows, sets and approves the field sheet | `apply` |
| `catchup.py` | Lists earlier work and work done since the newest recorded event; drafts reconstructed event comments | no |
| `compile.py` | Writes the deliverable section by section in the hub's order, the figure, table and source lists, the full dated timeline of the project and a manifest | no |

Every script that writes has `--dry-run`, which prints the exact `gh` commands and changes nothing.

## The mind page and the field sheet

The mind page is built from the local index with `mind.py`; details are in [`references/mind.md`](../skills/recortex/references/mind.md).

```bash
python3 skills/recortex/scripts/index.py sync
python3 skills/recortex/scripts/mind.py --open
```

The field sheet tells every subagent what counts as proof in your work and what must never be written down. You read the whole sheet and approve it; one that changed since you approved it is not shown until you approve it again. Details are in [`references/fields.md`](../skills/recortex/references/fields.md).

```bash
python3 skills/recortex/scripts/setup.py fields show
python3 skills/recortex/scripts/setup.py fields set --answers answers.json --dry-run
python3 skills/recortex/scripts/setup.py fields approve --fingerprint <the fingerprint in the line fields show printed> --yes
```
