<!-- ghim:event type=decision at=2026-03-07T09:30-08:00 because=9104 instead=9103 -->
### Decision: 2026-03-07 09:30 PST - rewrite the reader as a state machine

**In plain words, we decided to:** rewrite the reader so that it reads one character at a time, and stop patching the line splitter

**Decision:** The reader is rewritten as a state machine that reads one character at a time; the line splitter is removed.

**Decided by:** the project lead, 2026-03-07 09:30 PST

**Exact words:**

> Stop patching the splitter. Write the state machine, it is the only version I will trust.

**Where recorded:** Chat with the Claude session on 2026-03-07.

**Effect:** Quoting and line endings are handled in one place. The task in #7 continues on the new reader; the failure that led here is https://github.com/octo-org/octo-repo/issues/5#issuecomment-9104.

**Next step:** Port the 53 conformance files to the new reader, owner Claude session

**Evidence:**
- table conformance
- code octo_repo/reader.py#L41-L59 at 9c3e1ab
- pull 2

<details>
<summary>We use "line splitter" to mean the first version of octo-repo's reader, which cut the file into lines before looking at quotes</summary>

**Why it matters here:** Its failures are why the reader was rewritten.

**Also called:** the splitter

**The longer story:**

octo-repo began as a few lines of code: split the file at every line break, then split each line at every comma. That is the line splitter.

It breaks on quoting. A field wrapped in quotes may hold a line break, and the line splitter has already cut the row in two before it sees the quote. That is what #7 is about.

</details>
