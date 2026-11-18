---
description: Record what just happened in this conversation, or what you name, in the project record
argument-hint: "[what to record]"
disable-model-invocation: true
---

Load the recortex skill first; it gives the scripts folder. Record what just happened in this conversation, or what
the argument names: $ARGUMENTS

A plan the user approved becomes a task. Write its steps memory from the skill's `comment-steps.md` template to
`steps.md` in a new folder under `.tmp`, put `task.json` beside it with the task's title and the number of its topic,
and run `tasks.py open --dir` on that folder with `--dry-run` first. A result, a decision or a failure follows the
skill's "Record an event": draft the comment, lint it, show the dry run, then post it with `post_verified.py`. Say
what was recorded, with the address of each comment.
