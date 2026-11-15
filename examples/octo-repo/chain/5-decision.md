<!-- ghim:event type=decision at=2026-03-12T10:10-08:00 replaces=9102 -->
### Decision: 2026-03-12 10:10 PST - strict quoting stays, the lenient mode warns

**In plain words, we decided to:** keep strict quoting as the default and make the lenient mode warn about each stray quote it accepts

**Decision:** Strict quoting stays the default. The lenient mode now warns once per stray quote, with its line and column.

**Decided by:** the project lead, 2026-03-12 10:10 PST

**Exact words:**

> Keep strict as the default. Lenient mode should still load the file, but it has to say where it guessed.

**Where recorded:** Review of the state machine pull request, 2026-03-12.

**Effect:** The release-notes text on this issue gains a line on the warnings under Changed.

**Next step:** Add the warning and its test, owner Claude session

<details>
<summary>We now use "strict quoting" to mean the reader's default mode, which stops at a stray quote and names its line and column</summary>

**Why it matters here:** The error now says where the stray quote is, which the earlier meaning did not include.

</details>
