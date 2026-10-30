# Screen demo

For showing the product working. Recorded in demo mode, with fictional data, never a live customer account.

1. Use the demo workspace only: fictional names, and any number on screen marked as sample. Do not log in to a live
   customer account, and do not use real customer data, even blurred. A live product tour needs the owner's yes on
   the task first.
2. Write the steps in `projects/<slug>/shots/shots.json`: a list of `goto`, `click`, `type`, `wait`, `scroll`, `zoom`,
   `highlight` and `pause` steps, each with a line on what the viewer should see.
3. Record: `node software/video/record.mjs projects/<slug>/shots/shots.json --out projects/<slug>/shots/demo.mp4
   --size 1920x1080`. The cursor is drawn on the recording, so a viewer can follow it.
4. Time compression is a decision, not a fix. If you shorten a wait or skip a load, label it on screen ("sped up") and
   on the task. Never show a loading time shorter than the real one as if it were real.
5. Check the recording with `qa.mjs` and the contact sheet. Re-record a step that failed; do not cut around it quietly.
6. Write the sidecar: `kind` video, `tool` record.mjs, `parents` the steps file and the app version or URL it ran against.
