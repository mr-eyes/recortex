<!-- ghim:event type=decision at=2026-03-04T15:55-08:00 -->
### Decision: 2026-03-04 15:55 PST - strict quoting is the default

**In plain words, we decided to:** make strict quoting the default mode of the reader

**Decision:** Strict quoting is the default mode of the reader.

**Decided by:** the project lead, 2026-03-04 15:55 PST

**Exact words:**

> Make strict the default. A file with a stray quote should fail, not load wrong rows. People who want the sloppy mode can ask for it.

**Where recorded:** Chat with the Claude session on 2026-03-04.

**Effect:** `strict=True` becomes the default argument, and the release-notes text on this issue lists it under Changed. The conformance numbers on this issue are for strict quoting.

**Next step:** Document the lenient mode in the README, owner Claude session

<details>
<summary>We use "strict quoting" to mean octo-repo's rule that a quote character inside an unquoted field stops the read with an error</summary>

**Why it matters here:** It is the reader's default mode, and the conformance numbers on this issue are measured with it.

</details>

<details>
<summary>This thread is about how octo-repo reads a field that is wrapped in quotes, including commas, quotes and line breaks inside it</summary>

**Why we care:** Most CSV files that break a reader break on quoting.

</details>
