---
description: Answer a question about the project from its record, and post nothing
argument-hint: "<question>"
disable-model-invocation: true
---

Load the recortex skill first; it gives the scripts folder. Answer this question from the record only: $ARGUMENTS

Run `index.py query find --words "..."` with the key words of the question, then `index.py query recall --issue N` and
`index.py query timeline --issue N` for the issues it names. Start the answer with "From the record:", give the
address of each memory you use, and say what the record does not hold. Post nothing and write no file. For a long
question, start the `recortex:ask` agent instead.
