# The mind page

> Commands on this page write `scripts/<name>` for short. That is the `scripts` folder of this skill, next to `SKILL.md`; call it by its full path.

The mind page is one local file that tells a project's memory in sentences: what was thought, tried, decided and learned, and how one memory led to the next. It is built from the local index alone and read by a person in a browser. It is not a list of issues, it never changes the record, and the session never reads it.

## The command

```bash
python3 scripts/index.py sync
python3 scripts/mind.py --open
```

`mind.py` runs from the project folder like every script and prints the `config: ... repo: ...` line first. It builds the page from the index as it is; `--sync` runs `index.py sync` first, and without it nothing contacts the network. It prints `wrote <path> (N KB): M memories in T threads`, a line when anything is not told, and `open it in a browser: <file address>`. `--open` asks the browser to open that address and says whether one could be started; the address is printed either way.

It builds nothing, prints one line and exits 1 when:

| Case | Line |
|---|---|
| The repository is not in your trusted list | `mind: this repository is not in your trusted list, so no page is built. The page tells only the memories of accounts you allow.` |
| There is no local index | `mind: there is no local index of this repository; run index.py sync` |
| The index may not be read (another owner, loose permissions, not this plugin's) | `mind: the local index cannot be read; run hooks.py --doctor` |
| An older version of the plugin wrote the index last | `the index was last written by an older version of the plugin, or never by this one; run index.py sync` |

An earlier page is then left as it is. A page built while the last sync was unfinished says so in its last lines. `index.py query mind` prints the same counts in one line (`--json` prints the whole model the page is made from, for tests and for a person at a terminal).

## Where the file is

`$XDG_CACHE_HOME/recortex/private/<owner>__<repo>/mind.html` (by default under `~/.cache`), beside the index: the folder has mode 0700, the file mode 0600, and it is written through a fresh temporary file and moved into place; a symlink or anything but a plain file of yours at that path is refused. One file per repository, replaced at each build. No hook builds it, reads it or mentions it, and no script reads it back. Older versions of the plugin ignore it.

The page of a private repository holds private text. Do not copy it to a shared or public place.

## The ways in

| Way in | Shows |
|---|---|
| Where are we? | The last pause as a letter: its focus, what was meant to happen next and by whom, what waits for a yes; the memories since; the latest decision, result, failure and question, each with a link to its thread; the page's last lines in full |
| What does the manuscript say? | The deliverable as it is now, part by part, in the order of the hub's "Deliverable sections" table (else the profile's order). Its name follows the profile: "the manuscript", "the thesis", "the report", "the proposal", "the release notes", "the documentation", and "What do we deliver?" for a custom profile or several profiles, with a group for each deliverable. Beside each part: where it came from, the day its text last changed, what was recorded in its thread after that change, and what is still open on it |
| How did we get here? | An outline of sections, threads under them and each thread's tasks. One thread at a time: what it is about, where it stands, then its story from the first memory to the latest, with the link words between memories ("so", "we tried it", "later that changed"). A pause is a divider; the memories written on one task fold into a group |
| What is settled? | What holds the text up and none of the text: per section and topic, the decisions, findings and corrections that nothing recorded later replaces or sets aside, each with what it replaced, and links to its story and its text |
| What did we learn the hard way? | Every failure, newest first, beside what it led to |
| What is still open? | Open questions, untested ideas, failures nothing followed, what standing results do not show, what waits for a yes, work in progress |
| Show me the whole mind | Two pictures: a tree of what we know (the project's question, its sections and topics, and on each topic its findings, decisions and open questions), and the threads over time |
| Ask the mind | A search box that answers in sentences, and four ready questions |

"How to read this page" in the side bar opens a box that explains what a memory, a thread, a part and a pause are, and every kind and state. Every kind word and every state is a button that opens its own fixed explanation: the page never shows a state without saying what it means. The words are fixed wording of `mind.py`; no record text is in them.

Any sentence opens its memory in a side panel. A project word carries a dotted underline: pointing at it shows its meaning, choosing it opens a box with the meaning, why it matters, the longer story, the parts of the text and the memories that use it. A cited issue shows as words, never as a number. Every pointer, such as a thread name, a section or "the rest is on GitHub", is a link.

### What the states mean

| State | Meaning |
|---|---|
| Still stands | Nothing recorded later replaces a decision, finding or correction or sets it aside. It is what we hold today; it can still turn out wrong |
| Replaced, Set aside | A later memory of the same kind says something else; or we chose not to go this way and a later memory says what we did instead |
| Still open, Answered | A question that no later result, failure or decision answers; one that one does |
| Not tested yet, Taken up, Tried | An idea nothing took up and no result tested; one that a later decision or finding was made because of (no result has put the idea itself to a test); one that a result or a failure tested |
| Nothing followed yet, Led to a change | A failure that no decision or fix points back to; one that a later memory was made because of |
| A pause | Where a stretch of work ended and a note was left for whoever came next. It is not a claim, so it has no state; what came after it follows by time |

A thread is "N open points" when it has open questions, untested ideas or failures nothing followed, and "Nothing open" otherwise; its text can still change. The record keeps one current version of each part. It is not marked draft or final, and no status is added to the record: the page shows the day each part last changed and what was recorded in its thread after that.

### How the text is drawn

The text of a topic is read as structure and drawn from it: paragraphs, headings of the text's own (`###` to `######`), nested lists, numbered lists, quotations, code blocks, bold and italic, superscripts and subscripts, pipe tables and HTML tables, and the figure and table blocks where the author put them. An HTML table is read with its rows, cells, header cells, `rowspan` and `colspan`, `align` and the inline tags `b`, `strong`, `i`, `em`, `code`, `sup`, `sub`; every other tag and attribute is dropped and its text kept, the text of `script` and `style` is dropped, and a table in a table, a table over 40 rows or 12 columns or one the reader cannot take becomes one sentence and a link. Any other HTML stays text exactly as written. A link `[words](address)` is drawn as a link only when its address is an issue, pull request, comment or file of GitHub; any other address is written out as text beside its words.

Formulas are converted when the page is built, by a small converter in `scripts/mind_text.py`, to MathML, which current browsers draw themselves. The subset is common LaTeX: letters, numbers, `+ - = < > / ( ) [ ] | , . ; : ! * '`, Greek letters, the operators and relations `\sim \le \ge \ne \pm \times \cdot \approx \to \infty \partial \in \propto \ldots`, `\sum \prod \int` with limits, named functions (`\log \ln \exp \sin \cos \tan \max \min \lim \Pr \Var \Cov`), `_` and `^` with one token or one group, `\frac`, `\sqrt`, `\hat \bar \tilde \dot \vec \overline`, `\left` and `\right` with `( ) [ ] |`, `\mathrm`, `\operatorname`, `\text`, `\mathcal`, `\mathbf`, `\mathbb`, the spaces `\, \: \; \! \quad \qquad`, and the escapes `\% \{ \} \& \# \_ \$`. A formula of more than 400 characters inline (2,000 in a display), more than 8 levels of groups or 300 nodes, or one that uses anything else (environments, `\href`, `\def`, colors, alignment with `&`) is shown as the source as written, inside a block of its own, with the line "A formula the page cannot draw is shown as it was written." The look of a drawn formula depends on the math font the reader's system has; with none, it is still correct and legible. Nothing is loaded to draw it.

The text of a memory's plain line is one sentence and is not parsed. The text, its sub-headings, tables and formulas are shown in full: a part is cut only past 40,000 characters, and then it ends with a link to read the rest on GitHub.

`index.py query mind --json` prints the model the page is made from. Its shape follows the version of the plugin: it is a view for tests and for a person at a terminal, and no script reads it.

The page places memories before or after a pause by their clock times as written in the record, without converting time zones, since a resume block carries no offset. A memory written in another zone can therefore land on the wrong side of a pause; keep the record in its one time convention.

## Who is told

The page speaks as "we", so it tells only what your allowed accounts wrote: the repository's owner and the logins listed for it in your trusted list.

| Text | Told when | Otherwise |
|---|---|---|
| A memory, with its fields, links and folds | An allowed account wrote it and either it was never edited, or every edit was made by an allowed account (its edit history was read) | Not told; its links do not count and its folds explain nothing. Counted in the last lines. A memory told after edits carries a quiet note when opened |
| A word's meaning, what a thread is about | The memory that holds it is told | Not used |
| The text of a topic (its name line, sections, tables, formulas, figures, sources), and the order of the deliverable's sections from the hub's table | An allowed account opened the issue, its edit history has been read, and every edit was made by an allowed account | One sentence and the thread's address; the thread is named after its `topic:` label |
| The last pause | The resume block the sync checked (allowed author, never edited), when its comment is still one the page may tell | "No pause has been recorded yet", or one sentence that says to sync |
| Another issue's title | An allowed account opened it, its history is read, and nobody else renamed it | "an issue someone else opened"; the box gives who, when, open or closed and the address |
| Another issue's first lines, an open task's goal | As the text of a topic | Not shown |
| An account name | It has the form of a GitHub login | "an account" |

The last lines of the page count what is not told and why: memories by accounts outside your allowed list, memories changed on GitHub after they were posted (each with "Open it on GitHub"), older memories beyond the 2,000 newest (`compile.py` writes the whole timeline), and comments of a kind this version of the plugin does not know. They show in full under "Where are we?"; every other way in ends with one line that counts them and links there. A set-aside thread is named after its closed topic's title under the title rule, and after its label otherwise; a task's title is told under the same rule.

## Edited memories

A posted memory is never edited; a correction is a new memory. A memory whose comment GitHub says was changed after it was posted (its `updated_at` differs from its `created_at`) is checked: the sync reads, from GitHub, who made each edit. Only such memories are read, never all of them.

- Every edit was made by one of your allowed accounts: the memory is told as before, and its opened view says "Changed on GitHub after it was posted." The note also shows in `index.py query mind --json` (`changed`) and in `query recall` (a short mark after the line). Its links count, its folds explain, and the linter accepts a link to it.
- Any edit was made by an account outside your allowed list, or by an account that no longer exists, or the edit history has not been read (yet), is not whole, or is older than the comment's last change: the memory is not told, its links do not count and its folds explain nothing. `query recall` says how many it left out, the last lines of the page count them with "Open it on GitHub", and the linter refuses a link to it (`link-target`). A history that cannot be read counts as an edit by someone outside.
- A comment that looks changed but whose history shows no edit (a reaction, a stale answer) is not told either: nothing proves it is the text that was posted.

The allowed list is applied when the index is read, so adding a login to your trusted list tells that login's edits at once, with no new sync. Resume comments are not memories: an edited one stays ignored, whoever edited it. When the repository is not in your trusted list, every account counts and so does every edit.

An edit made in the same second as the post is not seen. It leaves the two times equal, and only comments whose two times differ are read, to keep the sync's cost small; such a memory is told as if it was never edited, whoever made the edit. This gap is small (`post_verified.py` reads each comment back right after posting), and it was already there before histories were read.

Every page of an edit history is read, for memories and for the text of a topic, 100 entries at a time. A history of more than 5,000 entries is not read to its end and counts as an edit by someone unknown, so that text is not shown.

## What the session does

When you ask to see or open the project's memory, the session runs `index.py sync`, then `mind.py --open`, and gives you the address. It does not read `mind.html`: to answer a question about the history it uses `index.py query recall`, `query timeline` and the issues.

## How the page is kept inert

The file is self-contained: one style and one script, both fixed files of the plugin allowed by their hashes in a Content-Security-Policy that loads nothing else (no pictures, fonts, frames or connections). Record text is only ever text in the page, never markup, an attribute or an address; every link out is an address on GitHub that the page built from checked parts. Every element is made by one function from a list of names and attributes; the list holds the inline elements the text needs (`h4` to `h6`, `pre`, `i`, `sup`, `sub`) and fifteen MathML elements, which only the formula builder makes, from a tree it checks again (the element names, text only inside tokens, the number of children each name takes, at most 300 nodes and 8 levels; a tree that breaks a rule is shown as its source). The attributes `rowspan`, `colspan`, `start`, `display`, `mathvariant`, `accent`, `aria-expanded`, `data-explain` and `data-fold` are accepted only on the elements and with the values listed in `mind.py`. The script routes by place, opens fixed explanations and folds branches; it holds no record text. The brand icon above the project's name is a third fixed file, `page/icon.txt`: two lines of path data that the build refuses unless they hold only `M`, `C`, `Z`, digits, spaces, commas and minus signs. It is drawn as an inline `svg` of the page, not loaded as a picture, so no picture is allowed. An allowed account can still write a misleading plain line; the fields and the original comment are one step away.

## If the browser says it cannot open the file

A browser installed as a snap (Firefox on Ubuntu, by default) may not read files under hidden folders in your home folder, such as `~/.cache`, and answers "Access to the file was denied". Open the address in a browser that can read it, such as Chrome, or one installed from a package rather than a snap.
