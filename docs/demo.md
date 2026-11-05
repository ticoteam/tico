# Try Tico in a minute

Demo mode runs Tico on your own computer with a made-up company, Acme Lights, already in it: a holiday projector
maker with a consumer side (families buy kits online) and a business side (shops, hotels and towns buy installs).
Four people run it, Ana, Ben, Cara and Eli, with 55 AI employees on the team chart. It comes with a week of
Updates, tasks in every state, a decision that needs you, strategy and goals, growth numbers, rules, automations,
learnings, chats, meetings with transcripts, docs, files with versions, sample email and Slack threads, and two
connected computers. No domain, no DNS, no sign-in, no model account.

```bash
docker run --rm -p 127.0.0.1:8765:8765 ghcr.io/ticoteam/tico:latest demo
```

Open <http://localhost:8765>. You are signed in as Ana, Acme's owner. Press Ctrl+C and everything is
gone. (Use a version tag instead of `latest` to pin one.) Docker is the simplest way because the image
already holds everything; there is nothing to configure, so no compose file is needed. Without Docker,
from a checkout with `pip install -r backend/requirements.txt`: `python3 -m backend.demo`.

To publish it on another port, pass the browser address with `demo --url`:

```bash
docker run --rm -p 127.0.0.1:18765:8765 ghcr.io/ticoteam/tico:latest demo --url http://localhost:18765
```

The startup message prints the supplied `--url`; without it, it prints the local listening port.
Open <http://localhost:18765>. Private demos accept browser writes from loopback addresses on any
published port. Ordinary local installs still check their configured port.

![Updates](images/updates-desktop-light.png)

## It cannot be mistaken for a real install

- A **Demo — sample data** strip stays across the top of every page and cannot be closed.
- Everything is fictional: humans are `@acme.example`, and dates are relative to when the demo is built, so its
  week ends on the day it starts.
- Nothing leaves the computer. The update check is off, there is no telemetry, and the process
  refuses every connection to a non-local address.
- Bots do not run. There is no scheduler; **Run now** says so; a message to a bot is answered with one
  line saying it is a demo. Settings > Health shows the two sample computers as online and mostly green.
- It listens on loopback only. Started with `--host 0.0.0.0` it exits with an explanation. In the container it binds
  inside, and answers only requests addressed to `localhost`, so publish the port on `127.0.0.1` as above.
- `--public-demo --url https://demo.example.com` serves other humans on purpose. Everyone is Ana, so a public
  demo is read-only, and its strip reads **Read only demo**.

Poking around is safe: changes go to a temporary database.

`--data-dir PATH` keeps the sample company and your edits between starts. Its dates keep their original build
time; use an empty directory to start a fresh week.

## Tico's public demo

Tico runs one public, read-only copy for people who want to look without Docker, unlisted: every answer carries
`X-Robots-Tag: noindex, nofollow` and `robots.txt` disallows everything. It lives on the cell host
([cell mode](cell.md)) beside the companies and apart from them, set up by `deploy/demo/`:

```sh
deploy/demo/deploy-demo <sha> [--url https://demo.tico.dev] [--image vX.Y.Z] [--tunnel]
```

- The commit (pushed to GitHub) runs as `python -m backend.demo --public-demo`, in the systemd unit
  `tico-demo@blue` or `tico-demo@green`: its own user, the release image's files as its root (the same Python,
  SQLite and screen relay; the deploy refuses an image whose `backend/requirements.txt` or `relay-rs` differ), the
  source read-only, a private `/tmp` for the company, 2 GB, and loopback only (`IPAddressDeny=any`), so it cannot
  reach the network or see the host's companies.
- A deploy builds the idle copy while the other one serves, then stops the old one. The router's `demo` route
  ([Routes](cell.md#routes)) sends visitors to the first copy that answers, so there is no gap.
- The seed is dated from when the copy starts, so `tico-demo-rotate.timer` builds it again every day at 10:00 UTC.
  `tico-demo-route.timer` puts the route back within 5 minutes if a core without routes rewrote the router.
- On the host: `/srv/tico-demo/tools/current/deploy/demo/tico-demo status`. Sources are in
  `/srv/tico-demo/releases/<sha>`, image roots in `/srv/tico-demo/runtime/<tag>`, settings in `/etc/tico-demo`.
- `--tunnel` puts `demo.tico.dev` (outside the tunnel's `*.tico.team`) into the tunnel's routes; that restarts the
  shared tunnel, so every company drops for a few seconds. Its DNS record is a proxied CNAME to the tunnel.

## Keeping the demo current

Each area of the app has a module in `backend/demo_sections/` with a `seed(builder)` function. The module owns its
words and its rows, and rows go through the same API a real install uses. A feature that adds or changes a page
adds or updates its module in the same change. `backend/tests/test_demo_coverage.py` maps every navigation page to
the list endpoint behind it and fails when a page has nothing to show, so a new page without demo data is caught
before it merges.

## Screenshots that stay current

The pictures in this repository's docs come from demo mode, not from a designer's laptop:

```bash
npm ci && npx playwright install chromium
TICO_BROWSER_CHANNEL= npm run screenshots        # everything, into docs/images
node scripts/screenshots --only updates,settings-health   # some pages
```

`scripts/screenshots` starts a demo, visits Updates, Tasks, Needs you, the team chart, a bot chat, Meetings, a
meeting, Settings > Health, Settings > Computers, Finish setup, a tour step, Docs, Market, Strategy, Growth, Finance,
Rules, Automations, Learnings, Threads, a Recruiting seat and Settings > Inboxes at a desktop and a phone
width, light and dark, and writes `<page>-<desktop|phone>-<light|dark>.png`. It waits for the icon font and
fails, instead of saving a bad picture, on any console error, failed request, error banner, unloaded icon or
page that never finished loading.

![Health](images/settings-health-desktop-light.png)
