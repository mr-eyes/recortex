<!-- ghim:event type=result at=2026-03-12T15:30-08:00 done=9108:1 -->
### Result: 2026-03-12 15:30 PST - the reader carries a quote across lines

**In plain words, we found that:** the reader now carries an open quote from one line to the next, and 9 of the 12 quoted-newline files pass

**What ran:** `python tests/conformance/run.py --summary` on branch `quote-state` at commit 4f2a9c1, conformance set v2.

**Configuration:** Python 3.12, one thread, one laptop.

**Numbers:**

| Measure | Value | Denominator | Source path |
|---|---|---|---|
| Quoted-newline files that pass | 9 | 12 | `tests/conformance/out/summary.tsv` |
| Files that passed before and fail now | 0 | 40 | `tests/conformance/out/summary.tsv` |

**Exclusions and failures:** None. All 52 files ran.

**Cost:** About 2 minutes of laptop time.

**Not established:** Why the other 3 quoted-newline files still fail.

**Next step:** Rerun the conformance set after the carriage-return fix, owner Claude session
