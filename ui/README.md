# ui/

The web UI: plain HTML, CSS and classic scripts. There is no module system yet: every script shares the page's
globals (`S`, `$`, `esc`, `get`, `post`, `route`, ...). A script is JavaScript or TypeScript (below).

    index.html        the markup skeleton, then <link> and <script> tags in load order; no inline script.
                      The tags between the `<!-- bundle:css:start/end -->` and `<!-- bundle:js:start/end -->`
                      markers are the styles and app scripts; the build bundles them (below)
    styles/*.css      the stylesheet, one file per area, linked in cascade order
    app/*.ts          the app: routing, state, sidebar and one file per page or feature
    *.ts              features that mount into a page through a small `window.*` API
                      (docs-page.ts, goals-kpis.ts, first-run.ts, support.ts, ...)
    assets/, vendor/  images, the text font (Inter) and the icon font, marked
    tests/*.cjs       browser tests (`npm run test:ui`)
    sw.ts             the service worker, compiled to build/ui/sw.js and served as /sw.js; it caches nothing (below)

The opening recovery card and its small stylesheet live in `index.html`, outside the bundles, so a failed asset
download still leaves a styled Reload and Contact support screen. `theme.ts` dismisses it only after the app's
`tico:app-loaded` event and all stylesheets have loaded without an asset error. Keep billing notices `hidden` in the
markup: only a confirmed cloud access state may reveal them. A failed page load is not evidence of exhausted credit.

## The build

The many files are for editing; the server serves only what `npm run build:ui` (scripts/build-ui.cjs, after `npm ci`)
writes to `build/ui/` (backend/ui_bundle_routes.py). The build:

- compiles each `.ts` file on its own to `build/ui/` at the same path (`build/ui/app/name.js`, with its map) with
  esbuild's per-file transform: types stripped, nothing bundled. The overview's modules and the service worker are
  served from there, and the unit tests read those files;
- minifies each file listed between the markers and concatenates them in index.html's order (each after a
  `// file: app/name.ts` comment), cut between files into parts of at most 512 KB (WebKit's disk cache keeps nothing
  larger than an eighth of its size). The page parts (`parts.json`, below) are cut out as parts of their own, and
  `theme.ts` (in the head, before the first paint) and `vendor/marked.min.js` are copied as files of their own;
- names every one of those files by its content (`build/ui/bundle/app.bundle.<hash>.js`, `app.bundle.<part>.<hash>.js`,
  `app.bundle.theme.<hash>.js`, ...), with an index source map (each file's own map placed at the line it starts on, so
  the developer tools show the `.ts` source) and a gzip copy beside it. The server sends them as
  `/tico/ui/app.bundle.…` with `Cache-Control: public, max-age=31536000, immutable`, and the gzip copy to a browser
  that accepts it. An older build's file is gone: a 404;
- writes `build/ui/index.html`, the page with each marked region replaced by the parts' tags and the page parts' list
  (a JSON block, `#ui-parts`, holding `build`, this build's id). It is revalidated on every load;
- writes `build/ui/manifest.json` last: the build id, the scripts a cold open runs, and the sha256 of every file under
  `ui/` it read. backend/ui_build.py compares it with `ui/`: `python -m backend.server` refuses to start on a missing or
  stale build (it builds first when the checkout has Node, on PATH or where Homebrew, Volta or nvm put it, and `npm ci`; so do pytest and `npm run test:ui`),
  and the image builds the UI in its own stage (Dockerfile `ui-build`) and checks it there. The server image has no Node.

While editing, run `npm run watch:ui`: every change under `ui/` rebuilds (about a second), and a reload shows it. The
server never builds or checks the UI per request.

One script is one scope for `'use strict'` (each part opens with it once), so start every `app/` file and every feature
file (`ui/*.ts`) with it; the build refuses one that is not strict. The bundle's first statement marks
`tico:bundle-start` for the boot timing (ui/app/boot-timing.ts). `python -m backend.ui_build --where
app.bundle.<hash>.js:1:4567` reads a Sentry frame back to its source with the build's maps, and `--shell-size` prints
what a cold open of Home downloads (backend/tests/test_ui_shell_size.py holds it under its ceiling).

**Page parts** (`parts.json`). A cold open downloads only the shell; a page part's files are fetched the first time a
person goes to one of its pages (ui/app/parts.ts). This list is not the load order (index.html is): it says which
files wait for a click, and the names the rest of the app calls them by. The shell's first script declares a stand-in
for each entry: a page or a call loads the part (with its `deps`) and then calls the real function; an idle name does
nothing until the part is loaded. `events` are selectors of controls the shell draws whose click the part's own
listener answers. `scripts/check_ui_parts.cjs` checks every use from outside a part.

## TypeScript

Write a new file as TypeScript, `app/name.ts`, and list it in index.html by that name: strict types, no `any`, and
only syntax that erases (no `enum`, `namespace` or constructor parameter properties), since it stays a classic
script whose top-level names are globals like any other file's. `bash scripts/check_ui_types.sh` type-checks the
`.ts` files with the rest.

The service worker, `sw.ts`, is compiled the same way (to `build/ui/sw.js`, a classic script: no `import` or
`export`) and served at the root as `/sw.js` with its map at `/sw.js.map` (backend/ui_bundle_routes.py). That URL never changes: `app/boot.ts` registers `/sw.js?v=…` with scope `/`, and an installed worker
checks the same URL for updates (`updateViaCache: 'none'`, `Cache-Control: private, no-cache`). Its global is a `ServiceWorkerGlobalScope`, which the page's DOM lib
contradicts, so it is its own program: `ui/tsconfig.worker.json` (lib `WebWorker`, every strict flag) checks it alone,
and the page configs exclude it. Any finding there fails, with no baseline (`python3 scripts/check_ui_strict.py`, and
`--worker-only` in `scripts/check_ui_types.sh`). The worker caches nothing: it lets the hashed bundle through to the
browser cache and fetches the page itself past every cache.

An open tab does not pick up a new release by itself. The config carries the release (`version`) and the served build
(`ui_build`, the build id the page's `#ui-parts` list holds); `app/notices.ts` compares both with what the page loaded with, on the
config poll and when a hidden tab is shown, and shows "New version · Reload" (`#stale-banner`).

## The rule

**One feature per file; load order is in index.html.** Add a page or feature as its own file and add its
`<script>` (or `<link>`) tag where it belongs. Nothing else lists the load order: the build reads index.html, the
icon-font scan (`scripts/build-icon-font.py`) reads `app/` and `styles/`, and the tests load whatever
`index.html` asks for.

Load order matters in two ways:

- **Scripts.** Function declarations are global once their file has loaded, so functions may call each other
  across files. A top-level statement that runs at load (`const x = f()`, `el.onclick = ...`,
  `addEventListener(...)`) may use only what earlier files define. Keep those statements in the order
  index.html lists the files: listeners on the same event fire in the order they were registered. `boot.js`
  is last; it starts the app.
- **Styles.** Later rules win at equal specificity, so `styles/*.css` keep the order they were cut in.
  `tokens.css` first, then base, then the areas.

Every file in `app/` and every feature file starts with `'use strict'`, as the old inline script did. Use absolute URLs
(`/assets/...`) inside CSS: a stylesheet resolves relative URLs against its own path.

## Where things are

| File(s) in `app/` | What |
| --- | --- |
| core, markdown, viewer, format, api, state, notices | routes, `$`/`esc`/`md`, `safeMd`, the file viewer, formatting, `get`/`post`, the state `S` and team names, the new-version and usage notices |
| avatars, liquid-dot, sidebar, heartbeat, tooltip | bot avatars and blobs, the Liquid Dot (Tico's own mark, `styles/liquid-dot.css`), the team tree, heartbeat and account menu, hover status |
| issues, hub-v2, needs-you, bot-tasks | shared request rows and tables, hub v2 helpers, Needs you, a bot's tasks |
| chat, pill, bot-page, bot-conversation, person-page, assistant-page | bot chat and its live reply, the composer, the bot and human pages, your Assistant |
| tasks, task-board, task-modal, task-code, task-runs, tasks-page, pull-refresh, decide, recurring | Tasks: rows and cards, dragging on the board and its folds, the modal and its conversation (the session as a chat), the modal's Code and Subtasks rail and the PR badge, its Runs section (checkpoint, attempts, subagents, background work), the page and its pull to refresh, Decide (one card at a time), routines |
| tools, tools-api, inboxes, inboxes-* | Tools: the list and its router, one API tool, and the inbox kinds' pages under it (mailbox, numbers, addresses, letters out, their settings) |
| meetings, mail, messaging, sql, integrations, help, computer-screen, computer-performance | one page each (computer-screen loads noVNC from `vendor/novnc/` with `import()`) |
| settings, settings-*, subscriptions, vault, catalog | Settings: shell, one file per tab, the bot editor, access editor, subscriptions (computers, groups, a bot's), credential vault, template cards |
| welcome, updates, goals, overview | setup, Updates, Goals, and the optional building overview |
| company-page | the Company page for owners and admins: Overview's Company page tab and its share panel, and the section behind Manage with transparency, editions and approvals, audiences, disclosure, the stats board, readers, previews (`styles/company-page.css`; the reader's page itself is rendered by `backend/company_page/render.py`) |
| router, drawer, search, org-fan, nav-events, viewport, refresh, native, boot-timing, boot-snap, boot | `route()`, account menu and phone drawer, search, mobile team switcher, keyboard viewport, the refresh loop, the desktop bridge, how long opening took (marks, the desktop app's log line), the last screen shown again while the app opens (IndexedDB, same person only, read-mostly pages), startup |

Files under about 1,500 lines; split a file along its section comments when it grows past that.

The [building overview](../docs/overview.md) mounts an isolated, lazy iframe under `overview/`. Its
Three.js renderer uses native modules from `vendor/three/`; it is deliberately outside the classic
app bundle so it loads only when the person opens Overview.

## Browser tests on a real hub

Most browser tests answer the API from fixtures (`support/page.cts`, `support/routes.cts`). A test that needs the real
API shapes starts a hub instead: `support/real-hub.cts` runs `support/real_hub_server.py`, which boots the scenario org
(`evals/redesign/harness.py`) on a fresh temporary database and applies the named seeds in `evals/redesign/seeds.py`.

```js
const {startHub, pageAs} = require('./support/real-hub.cts');
const hub = await startHub({seeds: ['org']});
const page = await pageAs(browser, hub, 'ana', {phone: true});   // 390×844 with touch; the default is 1440×900
await page.context().close();
await hub.stop();                                                 // stops the hub process this test started
```

- `hub.tokens` names each teammate: `ana` (the owner), `chris`, `engineering-manager`, `builder-claude`, `builder-openai`
  and `release-manager`. A page signed in as one sends that teammate's token as its Bearer.
- To start a screen from some state, add a seed. It makes the state through the API as a teammate would, never by
  writing the database. A seed whose route is not built yet is skipped and listed in `hub.skipped`.
- The clock is the scenario's fake clock, frozen at its start, so a seeded runner's heartbeat does not age out during a test.
- Write the phone journey first; `real-hub-smoke.cts` is the reference. The hub needs the backend requirements, in the
  Python named by `TICO_PYTHON` as for the other support servers.
