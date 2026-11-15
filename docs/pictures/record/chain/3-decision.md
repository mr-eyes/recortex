<!-- ghim:event type=decision at=2026-03-07T09:30-08:00 because=9104 instead=9103 -->
### Decision: 2026-03-07 09:30 PST - ask the person, never guess

**In plain words, we decided to:** show each undated page to the person and ask for its date, and stop borrowing dates from other pages

**Decision:** The tool asks the person for the date of each page in the undated pile; the borrowing pass is removed.

**Decided by:** the project owner, 2026-03-07 09:30 PST

**Exact words:**

> Stop guessing. Show me the page and ask. I would rather answer twelve questions than find one letter in the wrong decade.

**Where recorded:** Chat with the Claude session on 2026-03-07.

**Effect:** Every date in a sorted box was either read from the page or given by a person. The task in #7 continues on that rule; the failure that led here is https://github.com/octo-org/sample-project/issues/5#issuecomment-9104.

**Next step:** Add the question step and run the 53 practice pages through it, owner Claude session

**Evidence:**
- table practice
- code sorter/dates.py#L41-L59 at 9c3e1ab
- pull 2

<details>
<summary>We use "borrowing pass" to mean the second pass that gave an undated page the date of its neighbors</summary>

**Why it matters here:** Its wrong years are why the tool now asks.

**Also called:** borrowing

**The longer story:**

The first version read each page on its own. A page it could not read went to the undated pile, and the pile grew to 12 of the 53 practice pages.

The borrowing pass was the cheap answer: look at the page before and the page after, and take a date between them. It works when a box was scanned in the order it was written, and nothing on the page says whether it was. Pages with two dates, the subject of #7, were never helped by it.

</details>
