### Resume block: 2026-03-13 16:05 PST

<!-- ghim:resume:start -->
**In short:** The library reads files of separated values, and its reader is being rewritten to go through the text one character at a time. Strict quoting stays the default.

Lately the rewrite was chosen over patching the line splitter. Next are the pipe tests for the new reader; how it behaves on very long lines is in doubt.

**Last updated:** 2026-03-13 16:05 PST by the maintainer's Claude session
**Current focus:** Write the three pipe tests for the character-by-character reader.
**Active issues:** #7 (reader rewrite and pipe tests).
**Last decisions:** Rewrite the reader instead of patching the line splitter; strict quoting remains the default and lenient mode warns for each stray quote. See #5.
**Next actions:**

| Action | Owner | Where |
|---|---|---|
| Write the three pipe tests before reporting pipe support | Claude session | #7 |

**Holds:** Do not tag 0.3.0 until the project lead says so.
<!-- ghim:resume:end -->
