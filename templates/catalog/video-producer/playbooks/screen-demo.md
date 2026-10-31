# Screen demo

For showing the product working. Recorded in demo mode, with fictional data, never a live customer account.

1. Use the demo workspace only: fictional names, and any number on screen marked as sample. Do not log in to a live
   customer account, and do not use real customer data, even blurred. A live product tour needs the owner's yes on
   the task first. A bot cannot record its own company's Tico: the web app needs a person to sign in, so
   `record.mjs --as-bot` stops with one line before it starts. A customer's account never.
2. Start demo mode. On a Linux machine without Docker, run this from outside the repository (for example `~/tico-demo`),
   with `vX.Y.Z` the Tico server's release tag (the `release` in `GET /api/v2/config`):

   ```sh
   git clone --depth 1 --branch vX.Y.Z https://github.com/ticoteam/tico ~/tico-demo
   cd ~/tico-demo && python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
   .venv/bin/python -m backend.demo > ~/tico-demo/demo.log 2>&1 &
   ```

   Wait until `demo.log` says `Tico demo is ready`. It serves http://localhost:8765, signed in as Ana, Acme's owner, and
   keeps its data in a temporary folder outside this repository. It needs Python 3.12 or newer. If it stops with
   "No matching Rust screen relay", install Rust 1.92 or newer (rustup) and start it again. Stop it with `kill` on the
   PID you started (`$!`), never by name. With Docker, the same release in one command, from `docs/demo.md`:
   `docker run --rm -p 127.0.0.1:8765:8765 ghcr.io/ticoteam/tico:vX.Y.Z demo`.
   The "Demo — sample data" strip stays across the top of every page: keep it in every shot.
3. Write the steps in `projects/<slug>/shots/shots.json`: a list of `goto`, `click`, `type`, `wait`, `scroll`, `zoom`,
   `highlight` and `pause` steps, each with a line on what the viewer should see. Tico's pages are hash routes, so a
   `goto` is `http://localhost:8765/#/tasks`, never `/tasks` (a `/` path is a file next to the shots file, and 404s).
   Start with `http://localhost:8765/`: that first visit signs in as Ana.
4. Record without `--as-bot`: `node software/video/record.mjs projects/<slug>/shots/shots.json --out
   projects/<slug>/shots/demo.mp4 --size 1920x1080`. The cursor is drawn on the recording, so a viewer can follow it.
5. Time compression is a decision, not a fix. If you shorten a wait or skip a load, label it on screen ("sped up") and
   on the task. Never show a loading time shorter than the real one as if it were real.
6. Check the recording with `qa.mjs` and the contact sheet. Re-record a step that failed; do not cut around it quietly.
7. Write the sidecar: `kind` video, `tool` record.mjs, `parents` the steps file and the release tag and URL it ran against.
