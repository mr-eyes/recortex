<!-- ghim:event type=failure at=2026-03-06T11:05-08:00 checks=9103 -->
### Failure: 2026-03-06 11:05 PST - borrowed dates put pages in the wrong year

**In plain words, what went wrong:** borrowing the date from the pages around it put 4 of 6 test pages in the wrong year

**What failed:** 4 of the 6 undated test pages were given a year that a person reading the page would not give.

**What ran:** `python pages/practice/run.py --summary` on branch `borrow-date`, practice set v2.

**Cause:** A box is not always scanned in the order it was written. A reply kept beside the letter it answers can be years younger. Shown with a box of 3 pages.

**Impact:** The patch cannot be merged: a borrowed date looks as certain in the sorted box as a date that was read.

**Not established:** Whether borrowing is safe inside a single bundle of pages.

**Next step:** Decide whether to keep guessing at all, owner the project owner
