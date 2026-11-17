### Resume block: 2026-03-04 16:40 PST

<!-- ghim:resume:start -->
**In short:** The library reads files of separated values into typed columns, and the reader now handles quoted fields with strict quoting as the default. The release is close.

Lately the quoting default was decided and the reader was changed to follow it. Next are the newlines inside quoted fields and the choice of a license; whether the speed of the reader is good enough to release is in doubt.

**Last updated:** 2026-03-04 16:40 PST by the maintainer's Claude session
**Current focus:** Release 0.3.0. The reader handles quoted fields; newlines inside quoted fields are the last open item.
**Active issues:** NEW-task-newlines (newlines inside quoted fields).
**Last decisions:** 2026-03-04 15:55 PST, strict quoting is the default, see NEW-reader.
**Next actions:**

| Action | Owner | Where |
|---|---|---|
| Finish the state machine for quoted newlines and add the 12 test files | Claude session | NEW-task-newlines |
| Rerun the speed benchmark after the reader change | Claude session | NEW-speed |
| Choose a license before the release | the project lead | this hub |

**Holds:** Do not tag 0.3.0 until the project lead says so.
<!-- ghim:resume:end -->
