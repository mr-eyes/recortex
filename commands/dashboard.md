---
description: Open this project's dashboard and show its local address
argument-hint: "[suggestions]"
disable-model-invocation: true
---

Load the recortex skill first; it gives the scripts folder. When the user requests observer suggestions (including `$ARGUMENTS` equal to `suggestions`), start `dashboard_action.py --suggestions --open` in the background from the configured project and give them its private loopback address for this device. The service shows sources and saves reminder preferences locally. The user can stop the service when finished.

For the ordinary project dashboard, run `mind.py --open` from the configured project.
If it reports a missing or outdated index, run `index.py sync` and retry once after a successful sync.
For any other failure, tell the user what the script reported.

Give the user the dashboard address it printed, even when a browser started. Include its Project Map link for browsing the project hierarchy. If it printed a manuscript
address, include that direct link too. In a remote session, also give the printed file path so the user
can copy the standalone page to their own device.
