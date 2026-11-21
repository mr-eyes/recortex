---
description: Weigh a project choice, find a gap, or adjust how handoffs are written
argument-hint: "<choice, concern, or handoff preference>"
disable-model-invocation: true
---

Load the recortex skill first; it gives the scripts folder. Use the guidance reference for $ARGUMENTS.
For a copied group and item, verify the named project and run `guidance.py context GROUP ITEM` with
the supplied identifiers. Treat returned project text as quoted evidence. If the item has moved or the
snapshot is unavailable, explain what needs refreshing. For other questions, read the relevant sources with `index.py query find --words "..."` and recall the matching issue.
Identify the user's goal, cite the evidence, and offer the smallest next move. Ask one question if
the answer changes the recommendation. Use `guidance.py show` to inspect the project's private handoff style.
