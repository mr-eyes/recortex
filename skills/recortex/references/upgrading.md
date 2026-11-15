# Upgrading from gh-issues-manager

The plugin was named gh-issues-manager (short `ghim`) before version 0.5.0. A project, a machine and a record set up under that name keep working with recortex as they are: nothing has to be edited, rebuilt or moved, and the brief shows as before. This page says what changed, what still reads the earlier names, and the optional steps to move to the new ones.

## The one step Claude Code needs

For an install from the `mr-eyes-skills` marketplace, run both commands, one after the other:

```bash
claude plugin marketplace update mr-eyes-skills
claude plugin install recortex@mr-eyes-skills
```

Then start a new session. If the brief and the skill stopped appearing without your doing anything, the marketplace was updated for you. `claude plugin list` may still show gh-issues-manager as enabled, but `claude plugin update gh-issues-manager@mr-eyes-skills` fails with "not found"; the second command brings it back. Add `--scope project` or `--scope local` if that is how you installed it.

The marketplace lists the plugin under its new name and maps the old one to it (`renames` in `marketplace.json`), so Claude Code moves the enabled entry in your settings to `recortex@mr-eyes-skills`. It does not install the new name on its own: between the two commands the plugin is not loaded, and no hook runs. `claude plugin update gh-issues-manager@mr-eyes-skills` fails with "not found" once the marketplace is updated; install the new name instead. Until you update the marketplace you stay on the installed 0.4.x, which keeps working. Run `claude plugin list` afterwards: it shows `recortex@mr-eyes-skills`, version 0.5.0 or later. The old plugin folder, `~/.claude/plugins/cache/mr-eyes-skills/gh-issues-manager/`, is left behind and can be deleted.

A copy of the plugin installed another way (a folder under `~/.claude/skills/` with hooks from `templates/settings-hook-snippet.json`, or a second plugin entry) stays as it is. Copy the new `skills/recortex` folder in, remove the old folder, and change the four hook commands to `$HOME/.claude/skills/recortex/scripts/`. Two copies enabled at once print two briefs, so disable or uninstall the other one.

## What keeps working with no action

| Thing | Current name | Earlier name, still read | When both are there |
|---|---|---|---|
| Configuration file at the project root | `.recortex.json` | `.gh-issues-manager.json` | The current file is read; the earlier one is ignored, and `hooks.py --doctor` says so |
| Index top folder | `$XDG_CACHE_HOME/recortex` | `$XDG_CACHE_HOME/gh-issues-manager`, used in place | The current folder; the earlier one is not touched |
| State and key top folder | `$XDG_STATE_HOME/recortex` | `$XDG_STATE_HOME/gh-issues-manager`, used in place | The current folder |
| Trusted list | `~/.config/recortex/trusted-repos` | `~/.config/gh-issues-manager/trusted-repos` | Both are read; a repository listed in either is trusted, with the logins of both |
| Settings (`"discovery": false`) | `~/.config/recortex/settings.json` | `~/.config/gh-issues-manager/settings.json` | The current file |
| Environment variables | `RECORTEX_GH`, `RECORTEX_CONFIG`, `RECORTEX_TRUSTED_FILE`, `RECORTEX_TRUSTED_REPOS`, `RECORTEX_HOOK_TIMEOUT`, `RECORTEX_DISCOVERY`, `RECORTEX_SYNC_PAGE` | the same with `GHIM_` | The `RECORTEX_` one when it is set and not empty |
| Triage workflow | `.github/workflows/recortex-triage.yml` | `.github/workflows/ghim-triage.yml`, left alone | `setup.py apply` writes no second labeler when the earlier one is there |
| Markers in comments | `<!-- ghim:event ... -->`, `<!-- ghim:resume:start -->`, `<!-- ghim:resume:end -->` | | Not renamed: they are the record format, and old and new comments are one format |

Everything on GitHub keeps working: labels, hub and topic bodies, event and resume comments. The plugin edits no posted text; a hub body that says "from the gh-issues-manager skill" can stay. `setup.py apply` run again in a project with `.gh-issues-manager.json` adds to that file and makes no second one.

Each storage folder is chosen by itself: the folder with the current name when anything is at that path, else the one with the earlier name when it exists, else a new folder with the current name. The earlier folders are used in place, never moved: an index carries a mark made over its path, so a moved index would be refused and built again, and an older copy of the plugin still running on the same machine shares the same index, lock and key. Every check on the storage applies to whichever folder is chosen; a symlink or a file at the current name is refused, and the earlier folder is then not used instead.

The session-start and subagent briefs carry one fixed sentence that the skill was named gh-issues-manager before 0.5.0, for projects whose CLAUDE.md still names the old skill. No brief says which earlier names this machine uses; `hooks.py --doctor` does.

## What to change by hand

- Names that point at the plugin's parts: the agents are now `recortex:record-drafter`, `recortex:record-reviewer` and `recortex:record-auditor`, and the skill is `/recortex:recortex` (plugin) or `/recortex` (folder install). Edit permission rules, your own agents and notes that use the old prefix; there is no alias for it. Permission rules that hold the plugin's path (under `~/.claude/plugins/cache/` or a folder install's folder) need the new path in the same way.
- A project's CLAUDE.md snippet that names the `gh-issues-manager` skill: replace the name. `setup.py apply` does not rewrite it and prints a note when it sees it.
- A folder install's folder and hook paths (see above).

## Optional renames

- The configuration file: rename `.gh-issues-manager.json` to `.recortex.json` once everyone on the project has recortex 0.5.0 or later. The file is committed, and a collaborator on 0.4.x cannot read the new name: their hooks would go silent and the setup line would appear. A record set up with 0.5.0 gets `.recortex.json` from the start, so collaborators on such a project need 0.5.0 or later too.
- The storage folders, with no session running and only once no copy of 0.4.x is used on this machine (a folder install or another Claude Code profile would make the earlier folders again with another key, and the two versions would then refuse each other's index): delete `~/.cache/gh-issues-manager` (the next session builds a new index under `~/.cache/recortex` in the background; moving it instead gains nothing, because an index is refused at a path other than the one it was made at), and move `~/.local/state/gh-issues-manager` to `~/.local/state/recortex` to keep the key and the hook state. A key in the earlier state folder also works with an index in the new cache folder.
- The trusted list and settings: move them to `~/.config/recortex/`. Until then both lists are read, so a repository is no longer trusted only once it is gone from both.
- Environment variables: rename `GHIM_*` to `RECORTEX_*`.
- An installed `.github/workflows/ghim-triage.yml` needs nothing; it labels new issues as before.

## How long the earlier names are read

Through every 0.x release, and at least twelve months from 0.5.0. Reading an earlier name is removed only in a major version, after one minor version in which `hooks.py --doctor` reports it as a problem with the fix. The storage folders used in place and the `ghim:` markers have no end date. Until then `hooks.py --doctor` lists, as notes, every earlier name in use: the configuration file, the storage folders, the trusted list that names the repository and each `GHIM_` variable that is set (by name, never its value).

The upgrade was checked on Claude Code 2.1.325. A client too old to know `renames` is likely to keep the installed 0.4.x under the old name and report that the marketplace no longer lists it; this is not documented by Claude Code.

## Teams on mixed versions from 0.6.0

From 0.6.0 a memory starts with a line in plain words, may link to earlier memories in its marker, may list Evidence and may end with folded explanations, and ideas and questions are kinds of memory ([issue-model.md](issue-model.md), "Events"). A teammate on 0.4.x or 0.5.0, on the same record or on the same machine, loses nothing:

| What 0.6.0 writes | What 0.4.x and 0.5.0 make of it |
|---|---|
| Links in the marker (`because=`, `replaces=`, `instead=`, `checks=`) | Kept in the marker and ignored; the comment keeps its type and time, and their linter is silent |
| The plain line and Evidence | Two more fields; their linter checks only the fields it requires, in order |
| Folds | Text after the last field; only a final-record body may not hold a `<details>` block |
| `#N` and comment addresses in fields | `#N` is checked as before; a comment address is a URL like any other |
| An idea or a question | A comment of type `other` at its marker time, never dropped; they cannot write or lint one. On an index shared with 0.6.0 an older version shows the type 0.6.0 wrote (`idea`, `question`) until its own full derive, and nothing breaks on it |
| Four more tables and five triggers in a shared index | Left alone by their sync; the triggers record that it wrote, and the next sync of 0.6.0 derives everything again |
| Hook payloads | The same: no payload reads the text of a comment other than the hub's checked resume block |

What an older version cannot do: write an idea or a question, have links, Evidence or folds checked, or run `index.py query recall`. A memory written by an older version has no plain line, and `query recall` shows its title in its place. When an older version synced last, `query recall` of 0.6.0 says "the index was last written by an older version of the plugin, or never by this one; run index.py sync", and one `index.py sync` of 0.6.0 brings it back.

## From 0.7.0

0.7.0 adds the mind page: `mind.py` builds one private local file that tells the project's memory in sentences, beside the index ([mind.md](mind.md)). Nothing in the index changes: no table, column, trigger or meta key is added, and 0.4.x, 0.5.0 and 0.6.0 sync, rebuild and print their briefs on the same index as before and ignore the page. The hook payloads are the same.

What changes for a memory edited on GitHub after it was posted (GitHub's `updated_at` differs from its `created_at`): when the repository is in your trusted list, `index.py query recall` of 0.7.0 no longer lists it, its links no longer count, its folds no longer explain a word or a thread, and the linter refuses a new link to it. `recall` closes with one line that says how many memories it left out for that reason. 0.6.0 on the same index keeps listing it.

Run `python3 scripts/index.py sync --rebuild` once after the update: it reads the edit history of every topic again, and an edit that another account made in the minute an issue was opened is now seen.

## From 0.8.0

0.8.0 adds field guidance: a project's `.recortex.json` can carry a `fields` entry, the field sheet the user approved, and every subagent gets its short part in its start brief on a machine where the user approved that exact text ([fields.md](fields.md)). Nothing changes in the index or in the record on GitHub. A project without a sheet gets the payloads of 0.7.0, and once the line that offers to set the field.

Older versions ignore the entry: their hooks, sync, linter and `--doctor` do not read it, and their `setup.py apply` keeps it as it is. Their `apply --dry-run` prints the whole configuration, the sheet included; 0.8.0 prints a sheet it does not write as one line. The identifier patterns the wizard adds go into `restricted_patterns` in the form every version reads. The approvals are kept per machine, in `private/field-approvals.json` of the plugin's folder in your configuration folder, which no older version reads: each machine, and each collaborator, approves a sheet once with `setup.py fields show` and `setup.py fields approve`.

## From 1.0.1

`post_verified.py body` now requires `--expected-body-file <base>` for a real replacement. Save the live body before drafting and edit a separate copy; the script refuses a live body that differs from the saved base and checks again before writing. Update hand-written commands and wrappers that replace bodies. A dry run can still preview without a base, and a draft already stored is a no-op. A retry after a label failure can finish the labels when the body still matches the draft. See [posting and verification](posting-and-verification.md#4-post).

There is no record, index or configuration migration. Older clients still read the same record, but do not have the new update check. Coordinate one writer per issue: the check and write are separate GitHub calls, not an atomic lock. CI runs the full offline suite, including the [continuity demonstration](../../../examples/continuity/README.md); the separate live Claude protocol has no reported results until someone runs it.

## From 1.0.0

1.0.0 brings the parts below, and a shorter README with pages in `docs/`. The built-in example is now `octo-org/octo-repo`, with names that GitHub itself holds.

### Sub-issues and the board

1.0.0 adds two views of the record on github.com ([board.md](board.md)): each topic becomes a sub-issue of the hub and each `active` task a sub-issue of its destination topic, and a project can opt into a private GitHub Projects board of its work. Nothing in the record changes. `create_issues.py` links what it creates; for an existing record run `sub_issues.py plan` and then `sub_issues.py apply`. A project that does not answer yes to the board question never makes a board call, never sees a board line and never needs the `project` scope.

The index gains three tables (`parents`, `pull_merge`, `board_cards`) and three meta keys, as `CREATE TABLE IF NOT EXISTS` and plain rows. Versions 0.4.0 to 0.8.0 on the same index ignore them, and ignore the `board` entry of `.recortex.json`. The first sync of 1.0.0 on an older index lists the issues and reads their body edits again once.

### Edited memories and whole edit histories

A memory that your allowed accounts edited, and nobody else, is told again, with the quiet note "Changed on GitHub after it was posted." (see [mind.md](mind.md), "Edited memories"). The sync reads, from GitHub, the edit history of each memory whose comment looks edited, and of those only; it also reads every page of the edit history of a topic's text, no longer the newest 100 only. The first sync after the update reads the histories of the memories that already look edited (one more call for 40 of them, and one read for each that this index has no node id for). Nothing has to be rebuilt for that.

A topic with more than 100 edits was read up to its newest 100 before. Run `python3 scripts/index.py sync --rebuild` once to read the rest: it reads every topic's history again.

The index gains two tables, `comment_edit_reads` and `comment_edits`; no column, trigger or format changes. Versions 0.4.0 to 0.8.0 on the same index ignore them (0.7.0 and 0.8.0 keep leaving every edited memory out, and 0.6.0 lists it as it always did), and nothing they write makes the new version tell a memory it should not: a comment they rewrite has a new `updated_at`, which the stored history does not match.
# Upgrading from gh-issues-manager

The plugin was named gh-issues-manager (short `ghim`) before version 0.5.0. A project, a machine and a record set up under that name keep working with recortex as they are: nothing has to be edited, rebuilt or moved, and the brief shows as before. This page says what changed, what still reads the earlier names, and the optional steps to move to the new ones.

## The one step Claude Code needs

For an install from the `mr-eyes-skills` marketplace, run both commands, one after the other:

```bash
claude plugin marketplace update mr-eyes-skills
claude plugin install recortex@mr-eyes-skills
```

Then start a new session. If the brief and the skill stopped appearing without your doing anything, the marketplace was updated for you. `claude plugin list` may still show gh-issues-manager as enabled, but `claude plugin update gh-issues-manager@mr-eyes-skills` fails with "not found"; the second command brings it back. Add `--scope project` or `--scope local` if that is how you installed it.

The marketplace lists the plugin under its new name and maps the old one to it (`renames` in `marketplace.json`), so Claude Code moves the enabled entry in your settings to `recortex@mr-eyes-skills`. It does not install the new name on its own: between the two commands the plugin is not loaded, and no hook runs. `claude plugin update gh-issues-manager@mr-eyes-skills` fails with "not found" once the marketplace is updated; install the new name instead. Until you update the marketplace you stay on the installed 0.4.x, which keeps working. Run `claude plugin list` afterwards: it shows `recortex@mr-eyes-skills`, version 0.5.0 or later. The old plugin folder, `~/.claude/plugins/cache/mr-eyes-skills/gh-issues-manager/`, is left behind and can be deleted.

A copy of the plugin installed another way (a folder under `~/.claude/skills/` with hooks from `templates/settings-hook-snippet.json`, or a second plugin entry) stays as it is. Copy the new `skills/recortex` folder in, remove the old folder, and change the four hook commands to `$HOME/.claude/skills/recortex/scripts/`. Two copies enabled at once print two briefs, so disable or uninstall the other one.

## What keeps working with no action

| Thing | Current name | Earlier name, still read | When both are there |
|---|---|---|---|
| Configuration file at the project root | `.recortex.json` | `.gh-issues-manager.json` | The current file is read; the earlier one is ignored, and `hooks.py --doctor` says so |
| Index top folder | `$XDG_CACHE_HOME/recortex` | `$XDG_CACHE_HOME/gh-issues-manager`, used in place | The current folder; the earlier one is not touched |
| State and key top folder | `$XDG_STATE_HOME/recortex` | `$XDG_STATE_HOME/gh-issues-manager`, used in place | The current folder |
| Trusted list | `~/.config/recortex/trusted-repos` | `~/.config/gh-issues-manager/trusted-repos` | Both are read; a repository listed in either is trusted, with the logins of both |
| Settings (`"discovery": false`) | `~/.config/recortex/settings.json` | `~/.config/gh-issues-manager/settings.json` | The current file |
| Environment variables | `RECORTEX_GH`, `RECORTEX_CONFIG`, `RECORTEX_TRUSTED_FILE`, `RECORTEX_TRUSTED_REPOS`, `RECORTEX_HOOK_TIMEOUT`, `RECORTEX_DISCOVERY`, `RECORTEX_SYNC_PAGE` | the same with `GHIM_` | The `RECORTEX_` one when it is set and not empty |
| Triage workflow | `.github/workflows/recortex-triage.yml` | `.github/workflows/ghim-triage.yml`, left alone | `setup.py apply` writes no second labeler when the earlier one is there |
| Markers in comments | `<!-- ghim:event ... -->`, `<!-- ghim:resume:start -->`, `<!-- ghim:resume:end -->` | | Not renamed: they are the record format, and old and new comments are one format |

Everything on GitHub keeps working: labels, hub and topic bodies, event and resume comments. The plugin edits no posted text; a hub body that says "from the gh-issues-manager skill" can stay. `setup.py apply` run again in a project with `.gh-issues-manager.json` adds to that file and makes no second one.

Each storage folder is chosen by itself: the folder with the current name when anything is at that path, else the one with the earlier name when it exists, else a new folder with the current name. The earlier folders are used in place, never moved: an index carries a mark made over its path, so a moved index would be refused and built again, and an older copy of the plugin still running on the same machine shares the same index, lock and key. Every check on the storage applies to whichever folder is chosen; a symlink or a file at the current name is refused, and the earlier folder is then not used instead.

The session-start and subagent briefs carry one fixed sentence that the skill was named gh-issues-manager before 0.5.0, for projects whose CLAUDE.md still names the old skill. No brief says which earlier names this machine uses; `hooks.py --doctor` does.

## What to change by hand

- Names that point at the plugin's parts: the agents are now `recortex:record-drafter`, `recortex:record-reviewer` and `recortex:record-auditor`, and the skill is `/recortex:recortex` (plugin) or `/recortex` (folder install). Edit permission rules, your own agents and notes that use the old prefix; there is no alias for it. Permission rules that hold the plugin's path (under `~/.claude/plugins/cache/` or a folder install's folder) need the new path in the same way.
- A project's CLAUDE.md snippet that names the `gh-issues-manager` skill: replace the name. `setup.py apply` does not rewrite it and prints a note when it sees it.
- A folder install's folder and hook paths (see above).

## Optional renames

- The configuration file: rename `.gh-issues-manager.json` to `.recortex.json` once everyone on the project has recortex 0.5.0 or later. The file is committed, and a collaborator on 0.4.x cannot read the new name: their hooks would go silent and the setup line would appear. A record set up with 0.5.0 gets `.recortex.json` from the start, so collaborators on such a project need 0.5.0 or later too.
- The storage folders, with no session running and only once no copy of 0.4.x is used on this machine (a folder install or another Claude Code profile would make the earlier folders again with another key, and the two versions would then refuse each other's index): delete `~/.cache/gh-issues-manager` (the next session builds a new index under `~/.cache/recortex` in the background; moving it instead gains nothing, because an index is refused at a path other than the one it was made at), and move `~/.local/state/gh-issues-manager` to `~/.local/state/recortex` to keep the key and the hook state. A key in the earlier state folder also works with an index in the new cache folder.
- The trusted list and settings: move them to `~/.config/recortex/`. Until then both lists are read, so a repository is no longer trusted only once it is gone from both.
- Environment variables: rename `GHIM_*` to `RECORTEX_*`.
- An installed `.github/workflows/ghim-triage.yml` needs nothing; it labels new issues as before.

## How long the earlier names are read

Through every 0.x release, and at least twelve months from 0.5.0. Reading an earlier name is removed only in a major version, after one minor version in which `hooks.py --doctor` reports it as a problem with the fix. The storage folders used in place and the `ghim:` markers have no end date. Until then `hooks.py --doctor` lists, as notes, every earlier name in use: the configuration file, the storage folders, the trusted list that names the repository and each `GHIM_` variable that is set (by name, never its value).

The upgrade was checked on Claude Code 2.1.325. A client too old to know `renames` is likely to keep the installed 0.4.x under the old name and report that the marketplace no longer lists it; this is not documented by Claude Code.

## Teams on mixed versions from 0.6.0

From 0.6.0 a memory starts with a line in plain words, may link to earlier memories in its marker, may list Evidence and may end with folded explanations, and ideas and questions are kinds of memory ([issue-model.md](issue-model.md), "Events"). A teammate on 0.4.x or 0.5.0, on the same record or on the same machine, loses nothing:

| What 0.6.0 writes | What 0.4.x and 0.5.0 make of it |
|---|---|
| Links in the marker (`because=`, `replaces=`, `instead=`, `checks=`) | Kept in the marker and ignored; the comment keeps its type and time, and their linter is silent |
| The plain line and Evidence | Two more fields; their linter checks only the fields it requires, in order |
| Folds | Text after the last field; only a final-record body may not hold a `<details>` block |
| `#N` and comment addresses in fields | `#N` is checked as before; a comment address is a URL like any other |
| An idea or a question | A comment of type `other` at its marker time, never dropped; they cannot write or lint one. On an index shared with 0.6.0 an older version shows the type 0.6.0 wrote (`idea`, `question`) until its own full derive, and nothing breaks on it |
| Four more tables and five triggers in a shared index | Left alone by their sync; the triggers record that it wrote, and the next sync of 0.6.0 derives everything again |
| Hook payloads | The same: no payload reads the text of a comment other than the hub's checked resume block |

What an older version cannot do: write an idea or a question, have links, Evidence or folds checked, or run `index.py query recall`. A memory written by an older version has no plain line, and `query recall` shows its title in its place. When an older version synced last, `query recall` of 0.6.0 says "the index was last written by an older version of the plugin, or never by this one; run index.py sync", and one `index.py sync` of 0.6.0 brings it back.

## From 0.7.0

0.7.0 adds the mind page: `mind.py` builds one private local file that tells the project's memory in sentences, beside the index ([mind.md](mind.md)). Nothing in the index changes: no table, column, trigger or meta key is added, and 0.4.x, 0.5.0 and 0.6.0 sync, rebuild and print their briefs on the same index as before and ignore the page. The hook payloads are the same.

What changes for a memory edited on GitHub after it was posted (GitHub's `updated_at` differs from its `created_at`): when the repository is in your trusted list, `index.py query recall` of 0.7.0 no longer lists it, its links no longer count, its folds no longer explain a word or a thread, and the linter refuses a new link to it. `recall` closes with one line that says how many memories it left out for that reason. 0.6.0 on the same index keeps listing it.

Run `python3 scripts/index.py sync --rebuild` once after the update: it reads the edit history of every topic again, and an edit that another account made in the minute an issue was opened is now seen.

## From 0.8.0

0.8.0 adds field guidance: a project's `.recortex.json` can carry a `fields` entry, the field sheet the user approved, and every subagent gets its short part in its start brief on a machine where the user approved that exact text ([fields.md](fields.md)). Nothing changes in the index or in the record on GitHub. A project without a sheet gets the payloads of 0.7.0, and once the line that offers to set the field.

Older versions ignore the entry: their hooks, sync, linter and `--doctor` do not read it, and their `setup.py apply` keeps it as it is. Their `apply --dry-run` prints the whole configuration, the sheet included; 0.8.0 prints a sheet it does not write as one line. The identifier patterns the wizard adds go into `restricted_patterns` in the form every version reads. The approvals are kept per machine, in `private/field-approvals.json` of the plugin's folder in your configuration folder, which no older version reads: each machine, and each collaborator, approves a sheet once with `setup.py fields show` and `setup.py fields approve`.

## From 1.0.0

1.0.0 brings the parts below, and a shorter README with pages in `docs/`. The built-in example is now `octo-org/octo-repo`, with names that GitHub itself holds.

### Sub-issues and the board

1.0.0 adds two views of the record on github.com ([board.md](board.md)): each topic becomes a sub-issue of the hub and each `active` task a sub-issue of its destination topic, and a project can opt into a private GitHub Projects board of its work. Nothing in the record changes. `create_issues.py` links what it creates; for an existing record run `sub_issues.py plan` and then `sub_issues.py apply`. A project that does not answer yes to the board question never makes a board call, never sees a board line and never needs the `project` scope.

The index gains three tables (`parents`, `pull_merge`, `board_cards`) and three meta keys, as `CREATE TABLE IF NOT EXISTS` and plain rows. Versions 0.4.0 to 0.8.0 on the same index ignore them, and ignore the `board` entry of `.recortex.json`. The first sync of 1.0.0 on an older index lists the issues and reads their body edits again once.

### Edited memories and whole edit histories

A memory that your allowed accounts edited, and nobody else, is told again, with the quiet note "Changed on GitHub after it was posted." (see [mind.md](mind.md), "Edited memories"). The sync reads, from GitHub, the edit history of each memory whose comment looks edited, and of those only; it also reads every page of the edit history of a topic's text, no longer the newest 100 only. The first sync after the update reads the histories of the memories that already look edited (one more call for 40 of them, and one read for each that this index has no node id for). Nothing has to be rebuilt for that.

A topic with more than 100 edits was read up to its newest 100 before. Run `python3 scripts/index.py sync --rebuild` once to read the rest: it reads every topic's history again.

The index gains two tables, `comment_edit_reads` and `comment_edits`; no column, trigger or format changes. Versions 0.4.0 to 0.8.0 on the same index ignore them (0.7.0 and 0.8.0 keep leaving every edited memory out, and 0.6.0 lists it as it always did), and nothing they write makes the new version tell a memory it should not: a comment they rewrite has a new `updated_at`, which the stored history does not match.
