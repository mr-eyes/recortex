<!-- ghim:event type=result at=2026-03-04T08:40-08:00 -->
### Result: 2026-03-04 08:40 PST - first comparison with a hand-sorted box

**In plain words, we found that:** the tool put 184 of the 200 pages of the large box in the same place as a person did

**What ran:** `python check/run.py --repeat 5` at commit 7b3d0e2, output in `check/out/run-0304/`.

**Configuration:** practice set v1 (boxes of 20, 60 and 200 pages), one laptop, one person's hand-sorted order.

**Numbers:**

| Measure | Value | Denominator | Source path |
|---|---|---|---|
| Pages in the same place, large box, median | 184 | 200 pages | `check/out/run-0304/summary.tsv` |
| Runs of the large box | 5 | 5 runs | `check/out/run-0304/summary.tsv` |
| Boxes with the first and last page right | 3 | 3 | `check/out/run-0304/summary.tsv` |

**Exclusions and failures:** None. All 15 runs finished.

**Cost:** One run of the sorter over the box, under a minute.

**Not established:** How long a sort takes. Whether a second person would sort the boxes the same way.

**Next step:** Rerun after the two-date change, owner Claude session

**Evidence:**
- figure same-place

<details>
<summary>This thread is about how close the tool's order of a box is to the order a person would give it</summary>

**Why we care:** A sorted box is only useful if a person agrees with the order.

</details>
