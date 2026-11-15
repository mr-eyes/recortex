<!-- ghim:event type=failure at={{YYYY-MM-DDTHH:MM+HH:MM}} -->
### Failure: {{YYYY-MM-DD HH:MM ZONE}} - {{short title}}

**In plain words, what went wrong:** {{one line that completes the label; no period, no issue number, no path}}

**What failed:** {{the observed failure, with the error text or symptom}}

**What ran:** {{job ids, commit, command, configuration}}

**Cause:** {{the cause if shown, and how it was shown; otherwise "Not established" and the leading guess, marked as a guess}}

**Impact:** {{what is lost or unusable: outputs, numbers, time; counts with denominators; when it blocks a task, name the task as #N}}

**Not established:** {{what is still unknown about the failure}}

**Next step:** {{step}}, owner {{owner}}

**Evidence:**
- {{table <id>, figure <id>, code <path>#L<a>-L<b> at <commit> or pull <n>; delete the field when there is nothing to show}}

<!-- Optional parts, explained in references/templates.md. Links: add because=, replaces= or checks= to the marker line, each with one to four comment ids taken from index.py query recall, only when the link is true. Evidence: up to six lines in the four forms shown; delete the field when there is nothing to show. Folds: after the last field, explain a word that cannot be understood without this project, and write what this thread is about when recall says nobody has yet. Delete this note: {{delete this note}} -->
