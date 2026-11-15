# Dates: reading the date on a page

This body is the release-notes text for the sections below: the current version only, ready to copy. The comments on this issue are its history (dated results, decisions, failures and corrections) and are never release-notes text.

## Changed

- A page with no readable date goes to its own pile, named "undated", and is never given a guessed date. The report lists each such page.

## Fixed

- Handwritten dates are read, with the one exception listed under known issues. 41 of the 53 practice pages get the date a person reads on them.
- Dates written as words ("the third of May") are read: 6 of 6 such practice pages get the right date.

### Table: practice - Practice pages read correctly, by kind of page

Practice pages for which the tool reads the same date as a person, by what the page tests.

| Kind of page | Read correctly | Pages |
|---|---|---|
| Date written as words | 6 | 6 |
| Two dates on one page | 0 | 12 |
| All other pages | 35 | 35 |
| All pages | 41 | 53 |

## Known issues

- A page that carries two dates is not supported. Each of the 12 practice pages that fail, of 53, carries two.
- Typed pages and pages in languages other than English have not been tested; they are untested, not known to fail.

## Sources

| Statement or number | Source |
|---|---|
| 41 of 53 pages read correctly; 12 fail, each with two dates | `pages/practice/report-7b3d0e2.txt`, practice set v2, commit 7b3d0e2 |
| 6 of 6 pages with a date written as words read correctly | `pages/practice/report-7b3d0e2.txt`, commit 7b3d0e2 |
| Undated pages get their own pile | `sorter/dates.py`, commit 7b3d0e2 |
| Table practice | data `pages/practice/report-7b3d0e2.txt`; command `python pages/practice/run.py --summary`; commit 7b3d0e2 |
