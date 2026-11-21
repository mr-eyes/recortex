---
description: Review which parts of this project rely on a changed decision
argument-hint: "[decision group and item]"
disable-model-invocation: true
---

Load the recortex skill first; it gives the scripts folder. If $ARGUMENTS identifies a decision group and its 64-character hexadecimal item, combine them with a hyphen and pass that identifier immediately after `--impacts`. Use only the checked identifier from the project. Run `dashboard_action.py --impacts --open` from the configured project in the background. Started from a session it prints no address: it opens the user's browser on a private launch file and prints only that file's path. Give the user that path, never an address or token, and do not read the file or call the service yourself; the user's own confirmation in the page is what authorizes a change. If no browser opened, the user opens the file. The service stops after 30 idle minutes, or when the user chooses Stop on any of its pages. Opening the review only reads project records.

Point out the earlier decision, its replacement and the cited target. The user can choose Still correct, Needs update, Unrelated or Defer. A saved review lives on the project hub. A supported target edit requires another explicit confirmation. For a copied follow-up request, read its cited records in this project, show the exact proposed change, and use the target's existing approval and verification workflow. A saved request by itself leaves that change pending.
