# Setting up a record: the wizard

> Commands on this page write `scripts/<name>` for short. That is the `scripts` folder of this skill, next to `SKILL.md`; call it by its full path.

A project's goal usually does not exist as files yet (a manuscript is written during the work), so setup is a short set of questions asked with the user, not a file scan. Detection only pre-selects answers.

## When it runs

- In the first session after install in a project without `.recortex.json`, after the one-line notice from the SessionStart hook, when the user agrees.
- Whenever the user asks to set up the record, and again when they ask to change the setup.

## How it runs

1. `python3 scripts/setup.py preflight`, before the first question. It checks that `gh` runs, is signed in to github.com and names its account, and that Python is 3.8 or newer. When a check fails it prints one line that says what the user runs (installing `gh`, or `gh auth login`) and exits nonzero: show that line and stop. Ask no question, and do not try to install anything or sign in for the user. `apply --yes` runs the same check before it writes anything.
2. `python3 scripts/setup.py detect` (read-only, local). It looks only at file and folder names at the top of the project and one level down, and prints suggestions with their evidence, for example "suggested: software (profile software), because pyproject.toml, tests/". An empty project gets no suggestion. In a large folder it stops after the first few hundred names.
3. Ask the questions below with the session's multiple-choice question tool, a few at a time, each with an "other" answer. Pre-select what detection suggested and show its evidence next to it.
4. Show a summary of all answers and ask for one final confirmation. Nothing is written before it. The field sheet has its own approval, in the last of the field questions below.
5. Write the answers to a file, then run `python3 scripts/setup.py apply --answers <file> --dry-run`, show its output, and run it again with `--yes`.

## Questions

| Question | Answer becomes |
|---|---|
| What will this project produce? (several allowed) Paper or manuscript; thesis or report chapters; software or library release; dataset or resource; analysis report; prototype or demo; grant or proposal; documentation or website; other | `deliverables`: `paper`, `thesis`, `software`, `dataset`, `report`, `prototype`, `proposal`, `docs`, `other`. Each maps to a profile (`manuscript`, `thesis`, `software`, `dataset`, `research`, `prototyping`, `proposal`, `docs`, `custom`) |
| For each deliverable: its sections, starting from the profile's default, editable (add, rename, reorder; for example a journal's required sections or thesis chapters) | `sections`: the `## ` headings of the topic bodies, and the section labels |
| What field is this work in? (several allowed) Each field that detection suggested, with its evidence; a field the user already named in this conversation; "No particular field: keep the general rules"; or, through the other answer, the field in the user's own words | `fields.names`, at most four. "No particular field" gives no sheet |
| Does this work handle any of these? (several allowed) Data about people; confidential business or partner information; a contested public topic; none of these | `fields.sensitive`: `people`, `confidential`, `contested` |
| Only with data about people or confidential information: which forms of identifier must never be posted, for example a participant code or a contract number? For each, a name and one made-up example of the same shape. With data about people: below which count could a number point at one person? | `fields.restricted`, each of which becomes an entry of `restricted_patterns`; the count becomes one of the sheet's checks |
| Here is how every subagent will be briefed for this field. (the drafted sheet, whole: the short part as the frame will show it, the checks, the words line, the marks) Approve it; change something; skip for now | The rest of `fields`, and the approval. "Skip for now" leaves `fields` out of the answers |
| Where does the record live: this repository's issues, another existing repository, a new private repository? | `record`: `this`, `other`, `new`, and `repo` |
| Who decides: the names or handles whose words count as decisions | `decision_makers` |
| Keep a board of the work on GitHub Projects? No (the default); a new board; an existing board (its number). Say first that the board needs the `project` scope, which lets `gh` read and write all of the user's projects, and that the user adds it themselves with `gh auth refresh -s project` | `board`: `"no"`, `"new"` or `{"existing": N}`; see [board.md](board.md) |
| Time zone for event times | `timezone` |
| Is there earlier work to catch up on? May this project's local Claude Code transcripts be read for it? | `catch_up`, `read_transcripts` (see [catch-up.md](catch-up.md)) |
| Optional: the GitHub triage labeler workflow; the CLAUDE.md snippet; the prompt reminder line and how often | `triage_workflow`, `claude_md`, `prompt_hook` |
| May this repository be added to your trusted list, so the hooks print its brief? | `trust` |

An answers file:

```json
{"repo": "owner/name", "record": "this",
 "project": "A small library that reads CSV files, and a short benchmark note.",
 "deliverables": [{"kind": "software"},
                  {"kind": "paper", "sections": ["Background", "Methods", "Results", "Data availability"]}],
 "decision_makers": ["the project lead"], "timezone": "US Pacific time, written as 2026-03-04 14:10 PST",
 "topics": ["reader"], "catch_up": false, "read_transcripts": false,
 "triage_workflow": true, "claude_md": true, "prompt_hook": {"enabled": true, "every": 10}, "trust": true}
```

The field questions:

- Draft the sheet between the second and the last field question, by [fields.md](fields.md). When the user picked a field that has a model sheet, read `${CLAUDE_SKILL_DIR}/fields/<name>.json` and start from it. Ask at most two follow-up questions.
- The multiple-choice tool shows a few options and always an "Other" row. Offer at most two suggested fields and "No particular field"; everything else is the other answer. More than four fields: ask the user to name the main ones. `setup.py detect` prints field suggestions from file names ("field suggested: bioinformatics, because 3 .fastq.gz files, a Snakefile"); most fields leave no file names, so the other answer is the main path.
- "Change something" takes the user's words, redrafts, and shows the whole sheet again. Nothing is written before "Approve it".
- The answers file gains:

  ```json
  "fields": {"names": ["beekeeping field trials"], "sensitive": ["people"],
             "brief": {"proof": "...", "numbers": "...", "failures": "...", "never": "..."},
             "checks": ["...", "Is any count below 5, so that it could point at one person?"],
             "words": "...",
             "restricted": [{"name": "apiary code", "pattern": "\\bAP-[0-9]{3}[A-Z]\\b", "example": "AP-204C"}]}
  ```

  "No particular field" with no mark is `"fields": {"names": [], "sensitive": []}`.

`setup.py` checks every value against a strict form (names, headings, numbers in range) and refuses the file otherwise. `repo` takes GitHub's form in ASCII: an owner of 1 to 39 letters, digits, `-` or `_` that starts with a letter or digit, then a name of 1 to 100 letters, digits, `.`, `-` or `_` other than `.` and `..`. Each kind is listed once; a second deliverable of a kind is kind `other` with its own name. Section headings must be distinct (ignoring case), give distinct labels, and may not be "Sources", which is the sources table of every body. A deliverable of kind `other` keeps its name in the setup, and its labels start with that name (`sec:lab-handbook-safety`), so two such deliverables never share a label. When `apply` runs again on an existing setup, the values it keeps (time zone, topics, decision makers, profiles, hub) must fit the same forms; otherwise it stops and names them.

## What `apply` does

- Checks first that none of the files it may write (the setup file, the hub drafts, the workflow, `CLAUDE.md`) or the folders on the way to them is a symlink, and stops before any change if one is. Each file is written through a fresh temporary file in its folder, then moved into place.
- Writes `.recortex.json`, or adds to the configuration file under the plugin's earlier name when only that one is there: the repository, the profiles (a profile with edited sections is stored with its section labels and headings), topics, decision makers, time zone and options.
- Creates the labels: role labels, the core `type:` labels, the chosen profiles' section and type labels, and topic labels.
- Creates the hub with its deliverable tables in the chosen order and posts its first resume comment, for a project that has no hub yet.
- Adds the repository to the user's trusted list when `trust` is true; with `false`, the list is not touched.
- Writes `.github/workflows/recortex-triage.yml` when asked for and the record is this repository's issues. A workflow acts only on the repository it is committed to, so for a record in another repository (`record`: `other` or `new`) `apply` writes nothing and says to commit `templates/recortex-triage.yml` in the record repository. Where the labeler of the plugin's earlier name is installed, it is kept and no second one is written ([upgrading.md](upgrading.md)).
- Writes the CLAUDE.md snippet when asked for.
- Runs the first `index.py sync` in the foreground, so the first session already has a brief.
- With a `board` answer other than `"no"`: runs `board.py setup` and then `board.py apply`. When the `project` scope is missing the rest of the setup is done and one fixed line says to run `gh auth refresh -s project` in a terminal and then `board.py setup` again; the plugin never runs that command. Any other board failure also leaves the setup done.
- Records the kinds of deliverable that the project's files already suggest, so the hooks mention only what appears later.
- With a `fields` entry in the answers: checks first that the approvals file can be read, and stops before any write if not; prints the notices of a sensitive mark (show them to the user, and when the first one says the repository is public, ask whether to go on, use a new private repository, or stop); writes the entry into the configuration with the rest, appends the checked identifier patterns to `restricted_patterns`, and right after the configuration file is written records the user's approval of the sheet on this machine. A project that already has another sheet is refused: change a sheet with `fields set --replace`. A sheet that `apply` does not write is printed as one line, `"fields": "(field guidance: <state>; see setup.py fields show)"`.

## Later changes

The field sheet: "set the field", "change the field" or "review the field guidance" runs `python3 scripts/setup.py fields show`, then the field questions with the current answers pre-selected, then `setup.py fields set --answers <file> --dry-run` and `--yes` (with `--replace` when a sheet is there). After a pull that changed the sheet, `fields show` prints what differs and `fields approve --fingerprint <shown> --yes` records the approval once the user read it and agreed. See [fields.md](fields.md).

The deliverable can change mid-project. Run `apply` again with the new answers: it adds profiles, sections and labels and never removes what is there; for an existing hub it prints the table rows to add, for the session to post with `post_verified.py body`. The SessionStart brief and the `record-auditor` agent notice when files suggest a deliverable the setup does not cover (for example the first `.tex` file in a project set up only for software) and ask once whether to add it. Files present when `apply` ran, and the plugin's own workflow file, never cause that line.
