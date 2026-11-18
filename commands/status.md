---
description: Tell where the project stands, what is open, what is due in the text and what waits for a yes
disable-model-invocation: true
---

Load the recortex skill first; it gives the scripts folder. Run `status.py` and tell the user, in this order: the "In
short" paragraphs, the open tasks and their next steps, what is due in the paper's text, and what waits for a yes. Then
ask what waits: run `status.py --waiting` and ask each item it lists as one multiple-choice question from the template
and options it prints. Do not offer them in prose.
