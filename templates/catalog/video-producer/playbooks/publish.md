# Publish

Nothing leaves the team without the owner's yes on the exact final file.

1. Run the checks: `node software/video/qa.mjs projects/<slug>/final/<file> --out projects/<slug>/final/qa/`. Read the
   report and the contact sheet yourself.
2. Go through `knowledge/production-checklist.md`, line by line. Check the shot classes and labels with
   `skills/label-real-generated-and-staged-footage/SKILL.md` before the platform answer is written. Anything that fails goes back to the cut stage. Do not
   ask for approval on a file you know fails a line.
3. Publish the final to Tico: `hub file publish projects/<slug>/final/<file>`. Keep its qa report in `reports/`.
4. Put the final up for review with the publish mover from `pipeline.yaml` (`python3 software/pipeline.py next` prints
   the command). The question names where it would go.
5. Only after it is approved does the file go anywhere, and only as the approved version. A person uploads or posts it
   unless the owner tells you to and your Tools allow it. A changed file needs a new approval.
6. Record the publish in `memory/decisions.md` with the date, the place and the version.
