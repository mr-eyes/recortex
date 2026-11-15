# A linked chain of memories

These five drafts show how memories link. They are posted one at a time on the reader topic (`NEW-reader`, which
is #5 in the example record), in the order of their names, each with `post_verified.py comment --issue 5 --kind
<kind>`. They are not in `manifest.json`: a memory names earlier memories by their comment ids, which exist only
once those memories are posted.

The ids in their markers are the ids the example record gives them in order: the decision of `manifest.json` is
9102, and these drafts become 9103 to 9107. In a real session the ids come from `index.py query recall --issue N`,
or from the `memory id:` line that `post_verified.py` printed for a memory posted in the same session.

| Draft | Kind | Marker links | What it shows |
|---|---|---|---|
| `1-idea.md` | idea | none | an idea raised inside the thread's work |
| `2-failure.md` | failure | `checks=9103` | a failure that tested the idea |
| `3-decision.md` | decision | `because=9104 instead=9103` | a word explained in a fold, with a longer story that cites an issue; Evidence |
| `4-question.md` | question | `because=9105` | a question the decision raised |
| `5-decision.md` | decision | `replaces=9102` | a new decision in place of an earlier one; "We now use" for a word whose meaning changed |

After the five are posted, `index.py query recall --issue 5` lists the idea as set aside, the question as open and
the first decision as replaced.
