# Profiles per project type

A profile sets three things: the deliverable and its sections (which become section labels, each with the `## ` heading a topic body uses for that section's text), what counts as an event, and what the templates should emphasize. The model itself does not change: a topic body with a section label is the deliverable's text for that section, and the label follows the heading. A project may combine profiles; list them all in `profiles` in `.recortex.json` and `setup_labels.py` creates the union.

Label definitions live in `profiles/*.json`. The `core` profile (role labels) is always applied.

The kinds of memory are the same in every profile: result, decision, failure, checkpoint, correction, idea and question; a profile with a plan (below) adds two more, the plan and the text. A profile's "Events" line says which happenings count; ideas and questions raised inside a thread's work are memories in any profile.

## Software development (`software`)

Deliverable: a release, meaning its changelog, documentation and known issues.

| Section label | Body heading | The body is the text for |
|---|---|---|
| `rel:added` | `## Added` | Release notes: new behavior |
| `rel:changed` | `## Changed` | Release notes: changed or removed behavior |
| `rel:fixed` | `## Fixed` | Release notes: fixes |
| `rel:docs` | `## Documentation` | Documentation |
| `rel:known-issues` | `## Known issues` | Known issues and limits |

Topics are components or features (`topic:parser`, `topic:cli`). Use GitHub milestones for versions, since that is what they are for; the hub's deliverable tables are then per release.

Events: a merged pull request; a release or tag; a design decision; a benchmark or test campaign; an incident or regression; a change of dependency or public interface.

Emphasis: each section is release-notes text, one line per change in the voice of the notes; "Known issues" lists untested platforms, inputs and scales as well as known faults. A change enters a body when its pull request merges; until then it lives in an `active` issue. The commit or tag a statement was checked at goes under "Sources".

## Research and development (`research`)

Deliverable: a report or internal write-up of what was learned.

| Section label | Body heading | The body is the text for |
|---|---|---|
| `rd:question` | `## Question` | Question and background |
| `rd:methods` | `## Methods` | Methods and settings |
| `rd:results` | `## Results` | Results |
| `rd:limitations` | `## Limitations` | Limits and negative results |
| `rd:next` | `## Recommendations` | Recommended next work |

Topics are questions or lines of work. Events: a finished experiment or analysis; a decision on direction or method; a failed or abandoned approach; a new version of data or of a resource; a merged change to analysis code.

Emphasis: the "Configuration" field of result comments and the "Sources" of each body. Research projects rerun things; every rerun gets its own result comment and never overwrites an earlier one, while the body says only what the report says now. "Recommendations" is report text; open work that is not a recommendation stays in `active` issues.

## Scientific prototyping (`prototyping`)

Deliverable: a go or no-go recommendation with the evidence behind it.

| Section label | Body heading | The body is the text for |
|---|---|---|
| `proto:hypothesis` | `## Hypothesis` | What the prototype is meant to show |
| `proto:setup` | `## Setup` | Setup and settings |
| `proto:results` | `## Measurements` | Measurements |
| `proto:limitations` | `## Limitations` | Limits and untested cases |
| `proto:go-no-go` | `## Recommendation` | The recommendation |

Topics are the prototype's parts or the alternatives being compared. Events: a prototype run with its measurements; a go, no-go or pivot decision; a failed attempt and its cause; a change of test data or of the acceptance criterion.

Emphasis: write the acceptance criterion into the body's "Setup" section before the first run, and record a change of criterion as a decision comment, then edit the body. Prototypes are small-sample by nature, so "Limitations" carries much of the weight: what was tried once, on what, and what was not tried.

## Publication preparation (`manuscript`)

Deliverable: a paper, with main text, figures, tables and supplement.

| Section label | Body heading | The body is the text for |
|---|---|---|
| `ms:introduction` | `## Introduction` | Introduction |
| `ms:background` | `## Background` | Background |
| `ms:methods` | `## Methods` | Methods |
| `ms:results` | `## Results` | Results, figures and tables |
| `ms:discussion` | `## Discussion` | Discussion |
| `ms:limitations` | `## Limitations` | Limitations |
| `ms:supplement` | `## Supplement` | Supplement |

Topics are analyses, figures, tables or claims. Events: a result that enters a figure, table or sentence; a decision on a claim, an analysis or wording; a reviewer or co-author comment and its response; a frozen version of data or code; a submission, revision or resubmission.

Emphasis: each body is manuscript text, ready to paste. Each figure and table has a topic whose "Sources" names the script, commit and input files that produce it, and each number in the text has its source there. Reviewer comments are quoted word for word in comments, like decisions.

### The plan of a deliverable

A profile may name the plan of its deliverable in a `plan` key of its JSON file; the manuscript profile is the only one that does in 1.3.0 (the thesis is next). The key says what the linter and the plan accept:

| Key | Holds | The manuscript's |
|---|---|---|
| `noun` | what the deliverable is called in the plan's title | paper |
| `parts` | the kinds of planned part | message, claim, figure, table, interpretation, limitation, comparison, implication |
| `evidence` | the parts a finding can back | claim |
| `display` | the parts that carry the id of their block | figure, table |
| `write` | the headings the plugin may write for the author ([writing.md](writing.md)) | Methods, Results, Discussion, Limitations |
| `write_from_record` | the headings among them written from decisions and results directly, with no planned part | Methods |

`recortex.plan_profile` returns the first shipped profile of the project that has the key; a project entry with sections of its own keeps its profile's parts, and `write` is cut to the headings the project has. Only shipped profiles are read: a project file cannot name a part. The `core` profile has the role label `plan`: the plan of the deliverable, whose comments are the planned parts ([issue-model.md](issue-model.md), "The plan of the deliverable").

## Thesis (`thesis`), dataset (`dataset`), proposal (`proposal`), documentation (`docs`)

| Profile | Deliverable | Section labels and body headings |
|---|---|---|
| `thesis` | A thesis or a report made of chapters | `th:introduction` Introduction, `th:background` Background, `th:methods` Methods, `th:results` Results, `th:discussion` Discussion, `th:appendix` Appendix |
| `dataset` | A dataset or resource with its documentation | `ds:description` Description, `ds:collection` Collection, `ds:processing` Processing, `ds:quality` Quality, `ds:access` Access |
| `proposal` | A grant or project proposal | `gp:aims` Aims, `gp:significance` Significance, `gp:approach` Approach, `gp:timeline` Timeline, `gp:resources` Resources |
| `docs` | Documentation or a website | `doc:overview` Overview, `doc:guides` Guides, `doc:reference` Reference, `doc:questions` Questions and answers |
| `custom` | A deliverable with sections the user names at setup | prefix `sec:` |

## Issue types

Every project has the core `type:` labels of the `core` profile (task, bug, question, docs, idea, data, decision needed, duplicate, out of scope). Profiles add their own: `type:reviewer-comment` (manuscript, proposal), `type:release` (software), `type:analysis` (research), `type:experiment` (prototyping), `type:supervisor-comment` (thesis), `type:data-request` (dataset), `type:docs-request` (docs). See [triage.md](triage.md).

## Sections chosen at setup

The sections of a deliverable can be renamed, added and reordered at setup ([setup.md](setup.md)). The project's configuration then holds the profile as an entry with its sections instead of its name:

```json
"profiles": ["software", {"name": "manuscript", "sections": [["ms:background", "Background"], ["ms:methods", "Methods"], ["ms:results", "Results"], ["ms:data-availability", "Data availability"]]}]
```

Each label carries the profile's prefix and a plain name; each heading has a plain form; at most twenty sections. An entry that does not pass these checks is not used, so a project file cannot name role, topic or type labels or carry other text into the brief.

## Mixed projects

A method paper with a software package uses `manuscript` and `software`; an exploratory project that may become a paper starts with `research` and adds `manuscript` when writing begins. An issue may carry section labels from more than one profile; its body then has a section for each. The hub has one deliverable table per deliverable.

Where two profiles use the same heading (`## Methods` in `research` and `manuscript`), the labels the issue carries decide which one a heading means. The scripts report a heading they cannot assign; pass `--profile` to name the profile, or label the issue first.

## Fields and profiles

A profile says what the project produces; a field says how proof is handled in the work ([fields.md](fields.md)). Any field goes with any profile, and a field adds no label, section or heading.

## Projects without section labels

With only the `core` profile, no label names a section. A topic body is then still the current text of its topic and nothing else, under plain `## ` headings followed by "Sources", and its comments are the history.

## Adding a profile

Copy a file in `profiles/`, change the name, deliverable, events and labels, and pass its path or name to `setup_labels.py --profile`. Give every section label a `heading`, the `## ` heading of its text in a body, and a description that says so ("Issue body is report text for: results"). Keep headings distinct within a profile and away from history words (decisions, open work, provenance, next). Label names are at most 50 characters and descriptions at most 100.
