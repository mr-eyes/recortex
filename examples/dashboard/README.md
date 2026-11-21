# Try the project dashboard

Build standalone dashboards and review examples with Python and the test suite's fake GitHub. Each page uses invented project data. Run this from the repository root and open any of the printed file addresses.

```bash
python3 examples/dashboard/preview.py .tmp/dashboard-candidate
```

To compare two versions, run the same script with a second output directory and `--checkout /path/to/a/trusted/1.5.1-checkout`. Each build uses that checkout's own renderer and fixtures.

With Node, `playwright-core` and its Firefox binary available, run `node examples/dashboard/check.cjs .tmp/dashboard-candidate` on the new page. The check covers the software handoff, project topics, Project Map, and the manuscript reading view. Open `software.html#project-map`, `research.html#project-map`, or `manuscript.html#project-map` to explore a populated hierarchy. Select a branch to inspect its evidence or try a temporary sample rename. It also exercises narrow screens, the keyboard, browser Back and reading without JavaScript.

Open `software.html#impact-9701`, `research.html#impact-9701`, or `manuscript.html#impact-9701` to review a changed decision. Compare the quoted evidence for the task goal, steps, map branch, and output. Choose **Review change** to try a verdict, preview a draft, and inspect what requires another approval. The standalone examples reset when you leave the editor.

Run `python3 examples/dashboard/check_editor.py` to exercise all eight edits in Firefox against the loopback editor and fake GitHub. Add `--impact` to test saving a shared decision review, reopening it, applying the accepted citation change, and checking History. Set `PLAYWRIGHT_FIREFOX_EXECUTABLE` when using an existing Playwright Firefox binary.

Use the software or research Overview to open the changed decision under Needs your attention. Compare a directly cited step, an intentional historical comparison and a possible connection. Choose Review change, preview a verdict, save it and try a separate supported edit. The standalone editor simulates these actions in the browser and resets on reload. In a configured project, the authenticated loopback editor uses the project record. The mixed manuscript example routes author text through its approval workflow.

## Returning to a project

Give participants the questions without naming the navigation controls. Alternate which version they see first and use the other populated project on their second attempt to reduce the effect of remembering an answer.

| Project | Ask the reader | Check the answer against the record |
|---|---|---|
| Software | At handoff, the team planned a conformance rerun. What happened, how many files passed, and what work remains? Why did the team change the reader? | The team reran all 12 quoted-newline files and 9 passed. The next agreed step is to investigate the three remaining failures. An earlier splitter patch failed on four of six mixed-line-ending files, leading the team to choose a character-by-character reader. |
| Research project | What went wrong with the field logger backups? How did the team verify the fix? | Three of 24 exports were in nested folders that the backup job skipped. After the fix, all 24 restored files matched the collection manifest. The backups topic records the drill. |
| Manuscript in the same project | Why are four plots missing from the moisture analysis? Find the decision, its supporting observation, and the current Methods text. Return to your starting point. | Four wet-corner probes read high after storms. The decision excludes those four plots from moisture analysis. The failure left the difference in soil texture unresolved. |
| Sparse | What handoff, next step, or deliverable can you establish from this page? | There is no recorded handoff, task step, or configured deliverable. |

Record the first click, whether the answer was correct and sourced, time to the answer, backtracking, and whether Back restores the same location. For the software handoff, also record whether the reader finds the checkpoint and next step without leaving Overview. Repeat the evidence round trip using the keyboard and a narrow viewport. Keep results from automated browser checks and observations from actual people separate.
