# Order: how close the sorted box is to a hand-sorted one

This body is the accuracy-note text for the sections below: the current version only, ready to copy. The comments on this issue are its history (dated results, decisions, failures and corrections) and are never accuracy-note text.

## Methods

The order was checked on three boxes of 20, 60 and 200 scanned pages. Each box was sorted once by a person and 5 times by the tool, each run from an empty folder so that no run reuses the work of an earlier one, and the median share of pages that land in the same place is reported. All runs used one laptop and the practice set v1.

## Results

The tool put 184 of the 200 pages of the large box in the same place as the person, in the median of 5 runs. It matched the person on 3 of 3 boxes for the first and the last page.

### Figure: same-place - Pages in the same place, by size of box

Median share of pages, over 5 runs per box, that the tool puts in the same place as a person, for boxes of 20, 60 and 200 pages.

![Share of pages the tool puts in the same place as a person, by size of box](https://github.com/octo-org/sample-project/blob/7b3d0e2/check/out/run-0304/same-place.png?raw=true)

## Limitations

The comparison comes from one person and one set of boxes. How long the sort takes was not measured. The tool compared does not support pages with two dates, so boxes that contain them are not represented.

## Sources

| Statement or number | Source |
|---|---|
| 184 of 200 pages in the same place, median of 5 runs; first and last page right on 3 of 3 boxes | `check/out/run-0304/summary.tsv`, commit 7b3d0e2 |
| Boxes | practice set v1 under `pages/boxes/` |
| Comparison method | `check/run.py`, commit 7b3d0e2 |
| Figure same-place | data `check/out/run-0304/summary.tsv`; command `python check/plot.py check/out/run-0304/summary.tsv`; commit 7b3d0e2 |
