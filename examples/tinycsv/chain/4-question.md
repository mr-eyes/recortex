<!-- ghim:event type=question at=2026-03-10T09:15-08:00 because=9105 -->
### Question: 2026-03-10 09:15 PST - reading from a pipe

**In plain words, we asked:** whether the new reader still works when the input is a pipe and not a file

**Why it matters:** A pipe cannot be read twice, and the line splitter used to look ahead.

**What would answer it:** 3 pipe tests that pass: a small file, a file larger than the read buffer, and a quoted field split across two reads.

**Next step:** Write the 3 pipe tests, owner Claude session
