---
description: Show the project's to-do list with its checkmarks, add an item, or check one off
argument-hint: "[add <text> | done <n>]"
disable-model-invocation: true
---

Load the recortex skill first; it gives the scripts folder. Argument: $ARGUMENTS

With no argument, run `tasks.py status` and show the tasks with their checkmarks, then the items of the standing "To do"
task. For `add <text>`, write the user's words to a file under `.tmp` and run `tasks.py add --text-file` on it, with
`--dry-run` first. For `done <n>`, the number is the item's number in the "To do" list that `tasks.py status` shows:
write the user's words to a file under `.tmp` and run `tasks.py mark --task` with the number of the "To do" task,
`--steps` with the item's number and `--words-file` on that file, with `--dry-run` first. Say what changed, with its
address.
