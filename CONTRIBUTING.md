# Contributing

## Tests

```bash
mkdir -p .tmp
python3 -m unittest tests.test_scripts.{LintTests,HelperTests,LabelTests,CreateIssuesTests,PostVerifiedTests,ConvertBodyTests,SyncTests,RecoveryTests,CompileTests,TriageAndFormatTests,CatchUpTests,SnapshotTests,PackagingTests,CompatTests,MemoryFormatTests,DocsRecordTests,DocsTests,SetupTests}
python3 -m unittest tests.test_scripts.{ResumeBlockTests,IndexTests,HookTests,StatusLineTests,StorageTests,GuardTests,BackgroundTests,DiscoveryTests,EarlierNameTests}
python3 -m unittest tests.test_scripts.{EditHistoryTests,MindIndexTests,MindModelTests,MindPageTests,MindTextTests,MindFigureTests,FieldFormTests,FieldTests}
python3 -m unittest tests.test_scripts.{GraphqlReaderTests,FakeProjectsTests,SubIssueTests,BoardRulesTests,BoardCompareTests,BoardWriteTests,BoardSyncTests,BoardTrustTests}
python3 -m unittest discover -s tests -p 'test_observer_*.py'
python3 -m unittest discover -s tests -p test_continuity.py
python3 -m unittest discover -s tests -p test_manuscript_plan.py
python3 -m unittest discover -s tests -p test_manuscript_write.py
python3 -m unittest discover -s tests -p test_manuscript_versions.py
```

The nine commands hold every test between them; each takes under five minutes. The first four hold every class in `tests/test_scripts.py`, the fifth the observer's tests, the sixth the continuity demonstration and the last three the tests of the paper's plan, its writing and its versions. `python3 -m unittest discover -s tests` runs them all in one command of about twelve minutes, which CI uses. A new class in `test_scripts.py` goes into one of the four lists; a new `test_*.py` file needs a command whose `-p` pattern matches it. `mkdir -p .tmp` makes the folder that a test needs when its class runs first in a fresh clone.

[CI](.github/workflows/tests.yml) runs complete test discovery on every pull request and push to `main`, with Python 3.8 on Ubuntu 22.04 and Python 3.14 on Ubuntu 24.04. Discovery includes new test classes and `test*.py` files automatically; the nine commands above are the local batches. The job's limit is 45 minutes. The workflow has read-only repository permissions, does not retain checkout credentials, and puts a failing `gh` command ahead of the real CLI during tests so accidental live GitHub calls fail. Its actions are pinned to release commit hashes.

The tests run the scripts against `tests/fake_gh.py`, a local stand-in for `gh` selected with the `RECORTEX_GH` environment variable. They work in temporary folders under `.tmp/` and remove them afterwards; set `RECORTEX_TEST_KEEP=1` to keep them for inspection. The observer's tests that read native transcripts use the system's temporary folder instead, because the observer refuses a transcript below a group-writable folder, which a checkout made with a loose umask is; on macOS `/tmp` is a symlink, which the same checks refuse. No test may contact GitHub.

The suite passes on Python 3.14 on Linux. The lock race test starts its workers with the `fork` start method and is skipped where that method does not exist.

Check the packaging with `claude plugin validate .`.

## Repository layout

```text
.claude-plugin/           plugin.json and marketplace.json
hooks/hooks.json          four brief events and optional observer handlers on six events
agents/                   record-drafter, record-reviewer, record-auditor, record-planner and its session twin
skills/recortex/
  SKILL.md                the skill
  references/             detail loaded on demand
  templates/              hub, topic body, active task, comments, snippets
  profiles/               label sets per project type
  fields/                 the five model field sheets
  scripts/                the helpers
  page/                   the fixed style, script and icon of the mind page
docs/                     the five pages the README links to
docs/pictures/            the invented record behind the pictures, and the scripts that remake them
assets/                   the lockups, the drawings and the story pictures the README uses
examples/octo-repo/         the built-in example project, used by the tests
tests/                    offline tests with a local stand-in for gh
```

## Trying the built-in example

```bash
cd examples/octo-repo
S=../../skills/recortex/scripts
export RECORTEX_CONFIG=$PWD/.recortex.json   # the example is not at a project root

python3 $S/setup_labels.py --dry-run
python3 $S/lint_drafts.py --manifest manifest.json
python3 $S/create_issues.py --manifest manifest.json --dry-run
```

The dry run prints the commands issue by issue and makes no network call. In the drafts, issues refer to each other as `NEW-reader`, `NEW-speed` and so on; a real run replaces them with issue numbers. To try a real run, change `repo` in `manifest.json` and `.recortex.json` to a scratch repository of your own, run `setup_labels.py` and `create_issues.py` without `--dry-run`, and set `hub` in `.recortex.json` to the number printed for `NEW-hub`. The run leaves `manifest.json.state.json` beside the manifest, which is what makes a rerun safe; `.gitignore` excludes it.

## The README and the docs

- The README stays under 1,000 words. A section has a picture, a command or a link; a new feature gets one line under "What you get" and its detail goes into `docs/`.
- The five pages in `docs/` are for a person deciding whether and how to use the plugin; each opens with one sentence on what it is for. The pages under `skills/recortex/references/` are written for the session and stay the place for detail.
- The docs and the pictures use the invented record in `docs/pictures/record/`, never a real project and never the built-in example. A test lints that record and builds its page. `docs/pictures/make.sh` remakes the pictures offline through the test fake; it needs Node and Google Chrome and is not run by the tests. Remake them whenever the mind page's look changes.
- A test checks that every relative link and picture resolves.

## Rules for changes

- Scripts use bash, the Python 3 standard library and the `gh` CLI, nothing else.
- A script that writes to GitHub has a `--dry-run` that prints the exact commands. A dry run of `create_issues.py` makes no network call.
- No script deletes issues, comments or labels, or edits a posted comment. The only labels a script changes on an existing issue are section labels that follow its body's headings.
- A new lint rule comes with a test that shows it firing and a test that shows it staying quiet.
- A change to a template's sections is also a change to `KINDS` in `lint_drafts.py`, to `references/issue-model.md` and to the example drafts.
- A link kind, or a change to who may link to what, is a change to `LINK_RULES` in `record_format.py`, to the linter's tests (`test_link_rules_are_exactly_the_table` holds the table as a literal), to the link tables in `references/issue-model.md`, and to its phrases in `PHRASES` of `mind_model.py` (the joins of a passage and the link words of a chain).
- A plain-line label or a fold stem is a change to `record_format.py` (`PLAIN_LABELS`, `WORD_STEM`, `THREAD_STEM`), to the template, to `references/templates.md` and `references/issue-model.md`, and to the examples, and the example memories must still read under `tests/fixtures/compat-0.5.0/` (`CompatTests`).
- `tests/fixtures/compat-0.5.0/` is a frozen copy of the 0.5.0 parser and linter, written with `git show 6979c65:skills/recortex/scripts/<name>` and never edited; a test checks its SHA-256 values. A change to the record format that an older reader cannot read turns `CompatTests` red: change the format, not the fixture.
- Marker keys are lowercase letters only (`[a-z]+`): a key with a hyphen or an underscore makes the marker fail to parse on every version, and the comment loses its type and time. The columns of `events` and `FORMAT` in `index.py` stay as they are, since an older version on the same index writes ten values and refuses another format; new tables go into `SCHEMA` as `CREATE TABLE IF NOT EXISTS`, and triggers into `MIND_TRIGGERS`, which only `run_sync` runs.
- A profile's `plan` key (the parts, the headings the plugin may write) is a change to `profiles/*.json`, to `references/profiles.md`, to `KINDS` and the checks of the plan in `lint_drafts.py`, to `plan_rules.py` (the states, which `references/issue-model.md` and `references/planning.md` repeat), to the templates `comment-plan.md`, `comment-text.md` and `comment-proposal.md`, and to the two planner files in `agents/`, whose bodies stay equal below the frontmatter (a test holds that). `examples/octo-repo/` uses the software and research profiles and has no plan draft; the filled examples are in `references/planning.md` and in the tests' paper record (`tests/manuscript_record.py`, `paper()`).
- A topic body is deliverable text: a change to the final-record template or to a profile's section labels keeps the headings in `profiles/*.json`, the label descriptions and `references/profiles.md` in step.
- A GraphQL query uses only fields listed in `tests/fixtures/github-schema-excerpt.graphql`, which is copied from GitHub's public schema. Add the field's signature there, unchanged, when a query needs a new one; `tests/fake_gh.py` rejects anything else, as GitHub would. The reader (`tests/graphql_check.py`) also checks mutations, inline fragments and literal inputs against the `input`, `enum` and `union` definitions of the excerpt, which are copied the same way; `tests/fake_projects.py` answers the Projects and sub-issue operations.
- The resume block is an append-only comment on the hub; no script edits one, and an edited one is never shown. Text printed by the hook stays inside its data frame, includes an issue title only when the issue was opened by an allowed account and never renamed by another, and is printed only for repositories the user trusted.
- The index and the hook state are never written in the project tree, and every entry point (hooks, the session's Bash tool, a user's shell) resolves the same folders: `$XDG_CACHE_HOME` and `$XDG_STATE_HOME`. Do not read `CLAUDE_PLUGIN_DATA`, which Claude Code sets only for hooks. Tests may set `RECORTEX_DATA_HOME` together with `RECORTEX_TESTING=1` to put the index and the state in one sandbox folder (`$RECORTEX_DATA_HOME/recortex`); nothing else uses them. Folders from the environment (`XDG_*`, `RECORTEX_TRUSTED_FILE`) are used only when they are absolute paths. Storage goes through `storage.py`: everything in a `private/` folder with mode 0700 inside the top folder, files written through a temporary file with mode 0600, no symlinks followed, and an index read only when it carries the plugin's mark for this user. A lock of another process is judged and removed only by the process that holds its claim, `sync.lock.claim`, the hard link that only one process can make at a time; the claim gets a new time stamp at once, the lock is read through its path, and that descriptor stays open until the lock is removed; no lock is renamed away. In a project that is set up and trusted, a hook that cannot show the brief says so in one line instead of staying silent. Text from GitHub or from the project reaches a hook payload only inside the data frame and through `brief.defuse()`; outside it, only the plugin's own text, numbers and values checked against a strict pattern.
- A pattern that checks an untrusted name, number or time uses explicit ASCII classes (`[0-9]`, `[A-Za-z0-9]`, not `\d`, `\w` or `\s`) and `fullmatch`, or `re.ASCII` where a class must stay as it is. The scripts write `[0-9]` and never `\d`, in every pattern; a test checks it. A repository name goes through `recortex.valid_repo`, GitHub's form of `owner/name`.
- The agents in `agents/` never post to GitHub and never start another agent. The drafter's and the planners' tools are file tools only. The reviewer and the auditor are read-only by instruction: they have Bash for read commands, so do not describe them as unable to write. New hook text is fixed wording plus validated numbers or names, or sits inside the data frame.
- The optional observer, experimental and off by default, is a separate boundary: its settings and approval are private per machine and folder, and the project file can only switch it off. Command hooks persist only bounded native source descriptors and explicit original-user controls, with no model/network calls; a hold is released only from the user's own terminal. Independent selector and reviewer processes have no tools; only the separately approved publisher appends event comments. Mutable bodies, hub, resumes and boards stay with the main session. Use `observer_store.py` for its private durable queue, never the disposable index. Preserve cursor transactions, source identity, payload hashes, uncertain-send reconciliation and visible degraded states. No exactly-once or lossless-transcript guarantee. See [the observer contract](skills/recortex/references/observer.md). Offline tests are in `tests/test_observer_*.py`; runtime feasibility needs separate live evidence and cannot be established by a stub.
- `SKILL.md` stays short. Detail goes into `references/`, and every reference file is linked from `SKILL.md`.
- Names live in `names.py`: the plugin's name, the configuration file, the folder, the variable prefix and the workflow, current and from before the rename (`gh-issues-manager`, `.gh-issues-manager.json`, `GHIM_`, `ghim-triage.yml`). Plugin variables are read only through `names.env`, and no other module spells an earlier name, except `run_hook.sh`, which reads the earlier budget name itself; a test scans the tree for them. An earlier name that is still read has a test for the earlier name alone, the current one alone, and both. `renames` in `.claude-plugin/marketplace.json` is append-only history. The `ghim:` markers are the record format and are not renamed. The shared module is `recortex.py`, and the tests also honor `GHIM_TEST_KEEP`.
- The mind page (`mind.py`, `mind_model.py`, `mind_text.py`, `page/mind.css`, `page/mind.js`, `page/icon.txt`): every element goes through `E()`; record text is a text child only, never an attribute value, an element or attribute name, or part of the style or the script; an `href` is `anchor()` or `github_url()`, and no `url` or `image` column of the index is read; the script holds no record text and none of the calls that turn text into markup or reach the network (the page test lists them), and checks every id it reads from the page before a selector or a hash uses it; the style loads nothing. A new element or attribute name goes into `TAGS` or `ATTRS` (an attribute that only some elements may carry goes into `ATTR_RULES` with its values) and into the page test, a math element only through `Page.math()`, the kinds and states of a memory are rows of `KIND_TABLE`, `STATE_TABLE` and `THREAD_TABLE` with one fixed explanation each, and fixed wording of the model goes into `PHRASES` or `FIELD_NAMES`. What the page tells follows `index.memories` and the rules of `references/mind.md`, "Who is told".
- The field sheet's labels, sizes and characters live in `field_sheet.py` and move together with `references/fields.md`, the model sheets in `fields/` (a test holds their SHA-256 values), `tests/fixtures/field-sheets/` and the paragraph on field guidance in each of the three agent files. Approved text reaches a payload only through `field_sheet.state`, from the configuration the hook parsed once, and only in its own frame; no hook line outside that frame holds text or a name from a sheet, and `--doctor` prints field names only for an approved sheet. A sheet that breaks the form is refused, never cleaned. No sheet, model or form is loaded from a path a project names. The approvals go through `storage.py`, and the hooks only read them.
- Sub-issue links are computed from the record (`sub_issues.py`: the hub's topics under the hub, each `active` task under the topic its "Destination topic" section names) and never believed from GitHub; a link another parent already holds is reported and left alone, and a GitHub that refuses a link costs one fixed line, never a failed post. The parent of each issue is read in the same GraphQL call as its body edits, and a GitHub that lacks the field is read without it.
- The board (`board_api.py`, `board_rules.py`, `board.py`) is a copy of the record, never part of it. Where a card belongs is one table, `board_rules.place()`, which moves together with `references/board.md` and `BoardRulesTests`; the status names are `board_api.STATUSES`. Everything on the board is untrusted: nothing but item ids, issue numbers, the repository, the plugin's own status names (found by exact option name; any other is "other") and the Why text (compared, never shown) is read, and no text from the board reaches a payload or a terminal line; a test plants hostile text in every field. The `board` entry of the project file is used only in a trusted repository, only for the owner of the record's repository, and only as the user approved it (`board-approvals.json`, through `storage.py`). A project without a board makes no board call and its payloads do not change; a board that cannot be read is one fixed line (`board_api.REASONS`), never a failed sync or post. Hooks never write to GitHub; `board.py apply` is the main session's. A new reason is a change to `REASONS`, to `references/board.md` and to the tests of each reason.
- The workflow of the branch of versions (`templates/recortex-manuscript.yaml`) is fixed text with its own rules, held by `BuildWorkflowTests` and `references/versions.md` together: `contents: read` and `actions: read` only, a push to the branch or to a `paper-*` tag and nothing else, every action pinned to a full commit hash, no deploy. A new action version is a change to the pins in that test, the file and the document, and its commit hash is read from the tag, not from memory. The layout of the branch (`paper_layout.py`, `templates/manubot-front-matter.md`), the shape of `VERSION.json` and the fixed refusal lines in `versions.py` are part of the same contract with `references/versions.md`.
- Examples use invented projects only.

## Releases

`version` in `.claude-plugin/plugin.json` pins installed copies to that version. Raise it with every change that users should receive.
