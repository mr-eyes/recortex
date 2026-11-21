---
description: Tell where the project stands, what is open, what is due in the text and what waits for a yes
disable-model-invocation: true
---

Load the recortex skill first; it gives the scripts folder. Run `status.py --sync`, which first reads GitHub into the local copy, so that a step or a memory recorded elsewhere since the session began (for example a step reported in the dashboard) counts; a done step is listed with the memory that checked it, so cite that rather than doubt it. If it says GitHub could not be read, tell the user the status is from the local copy and how old that is. Lead with the relevant change since the last handoff, any open judgment and one next move with a source. Mention open tasks or due output work when relevant. Use `status.py --waiting` to inspect a pending approval and explain why it matters. When the user chooses to act, use the exact printed multiple-choice question and options. Honor the saved handoff style from `guidance.py show`.
