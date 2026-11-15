<!-- ghim:event type=failure at=2026-03-06T11:05-08:00 checks=9103 -->
### Failure: 2026-03-06 11:05 PST - an open quote across lines breaks mixed line endings

**In plain words, what went wrong:** remembering an open quote from one line to the next broke files with mixed line endings: 4 of 6 failed

**What failed:** 4 of the 6 conformance files with mixed line endings returned too few rows.

**What ran:** `python tests/conformance/run.py --summary` on branch `quote-state`, conformance set v2.

**Cause:** The reader cuts the file into lines before it knows whether a quote is open, so a carriage return inside a quoted field ends the row. Shown with a 3-line file.

**Impact:** The patch cannot be merged: 4 of the 6 files with mixed line endings that pass today would fail.

**Not established:** Whether a smaller patch could avoid it.

**Next step:** Decide whether to rewrite the reader, owned by the project lead
