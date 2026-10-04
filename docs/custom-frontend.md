# Build your own frontend on Tico

Start here if your team wants its own web app on top of a running Tico: a customer portal, an internal tool, a
different look for the same data. Tico's server is an HTTP API, and a separate web app can sign your humans in and
use it from any address you allow. A working example that needs no build step is in
[`examples/custom-frontend/`](../examples/custom-frontend/); the full contract is in [api.md](api.md) and
[`openapi/v2.json`](openapi/v2.json).

In ten minutes you can have the example talking to your server:

1. On the Tico server, in `/opt/tico/.env`, allow the address your frontend runs on, then restart:

   ```
   TICO_CORS_ORIGINS=https://app.example.com,http://localhost:5173
   ```

   ```
   cd /opt/tico && docker compose up -d
   ```

   Use the exact origin (scheme, host, port; no path, no `*`). `http://localhost:5173` is for development
   on a laptop; drop it from a production server once the frontend is live.
2. In `examples/custom-frontend/config.js`, put your server's address: `window.TICO_URL = "https://tico.example.com";`
3. In that directory run `python3 -m http.server 5173` and open <http://localhost:5173>. Sign in with your work
   account. You see the team chart, chat with a bot, tasks and what needs you.

The rest of this page is what the example does, and what you decide when you build your own.

## Contents

- [Options: how a frontend authenticates](#options)
- [Sign-in (option B)](#sign-in-option-b), the one to use for a separate address
- [Same origin (option A)](#same-origin-option-a) and [servers and scripts (option C)](#servers-and-scripts-option-c)
- [Key endpoints](#key-endpoints)
- [Streaming live replies](#streaming)
- [Errors](#errors)
- [Rate limits](#rate-limits)
- [Versioning and what is stable](#versioning)
- [Security notes](#security)
- [The example app](#the-example-app)

## Options

A human's browser session to Tico is a cookie, `__Host-tico_session` on an https server. That cookie is bound to
Tico's own host (`__Host-` forbids a `Domain`), and it is `SameSite=Lax`, so a browser sends it only to that host and,
for scripts, only from the same site. Three things follow, and they decide the options:

| Option | Works when | Credential | Notes |
|---|---|---|---|
| **A. Same origin** | Your frontend is served from Tico's own host, under a path such as `/app/` | The session cookie | Nothing to configure for CORS. Simplest and most secure, but your frontend and Tico share a host, so it is a reverse-proxy job ([below](#same-origin-option-a)). |
| **B. Separate origin** (recommended) | Your frontend has its own address: `app.example.com`, `localhost:5173`, a Vercel preview | A short-lived **bearer session**, earned through Tico's own sign-in with PKCE | One setting on the server (`TICO_CORS_ORIGINS`). Works from any origin, including `localhost` and other sites, where a `SameSite=Lax` cookie would not be sent. Not affected by CSRF. |
| **C. Server to server** | A backend, script or tool | A **personal API token** | Never for a browser. |

Why B is a bearer session and not "the cookie, with CORS": `localhost:5173` and `app.example.com` are different
*sites* from `tico.example.com` (or, for `localhost`, entirely different hosts), and a `Lax` cookie is not sent on a
cross-site `fetch`; making it `SameSite=None` would send Tico's cookie on every cross-site request and hand cross-site
forgery back a door that `Lax` closes. A bearer session is sent only by the code that holds it, so there is nothing
ambient to forge. A sibling subdomain (`app.example.com` calling `tico.example.com`) can in principle reuse the Tico
cookie with `fetch(..., {credentials: "include"})` once the human has signed in on Tico's own page, and the server
answers the CORS credential headers that needs; but the human has to visit Tico first and the cookie cannot be
obtained from your frontend. Use B.

## Sign-in (option B)

Requires the built-in sign-in (`TICO_AUTH_PROXY=oidc`, Google, Microsoft or any OpenID provider). Your frontend never
sees a password, a provider token or a client secret; the provider only ever redirects to Tico.

```
your app                       Tico                              identity provider
   | 1 GET /auth/login?next=<your page>&code_challenge=<S256>       |
   |------------------------->|  2 redirect ------------------------>|
   |                          |<---------------- 3 the human signs in, returns to /auth/callback
   |<-- 4 302 <your page>#tico_code=<one-time code> (60 s, once)      |
   | 5 POST /auth/token {code, code_verifier}                        |
   |------------------------->|                                       |
   |<-- 6 {"access_token": "tico_st_...", ...}                        |
   | 7 GET /api/v2/... with Authorization: Bearer tico_st_...         |
```

1. **Make a PKCE pair.** `code_verifier` is a random string of 43 to 128 characters from `A-Z a-z 0-9 - . _ ~`.
   `code_challenge` is `base64url(SHA-256(code_verifier))` without padding (43 characters). Keep the verifier
   (in `sessionStorage`, say).
2. **Send the browser to Tico:** `GET https://tico.example.com/auth/login?next=<your page URL>&code_challenge=<challenge>`.
   `next` is your page's absolute URL. Its origin must be exactly one of `TICO_CORS_ORIGINS`; a path and query are
   allowed, a `#fragment` is not. Anything else is refused with a page saying so (an open redirect is impossible: no
   other address is ever redirected to). Add `&fresh=1` to make the provider show its account chooser.
3. **The human signs in** at the provider. They must be on your Tico roster (or admitted by its allow list); otherwise
   Tico shows "You're not on the list" and no code is issued.
4. **Tico redirects to your page** with `#tico_code=<code>` in the fragment, so it appears in no server log. The code is
   good for 60 seconds and one use. Read it and remove it from the address bar at once (`history.replaceState`).
5. **Exchange it:** `POST /auth/token` with `{"code": "...", "code_verifier": "..."}`. Only a browser on the
   page's origin can do this (Tico checks the `Origin` header against the one that started the sign-in), and only with
   the verifier that matches the challenge. A wrong verifier or origin burns the code.
6. **You get a session:**

   ```json
   {"access_token": "tico_st_...", "token_type": "Bearer", "expires_in": 7776000, "idle_timeout": 2592000, "person": "ana"}
   ```

7. **Call the API** with `Authorization: Bearer <access_token>`. The session ends after 30 days without use or 90 days
   in all (the same as a browser session on Tico's own page). An expired or revoked session answers
   `401` with `{"error": {"code": "identity", "sign_in": "/auth/login"}}`: start again at step 1.
   Sign out with `POST /auth/token/revoke` (with the bearer), which ends it on the server at once.

Tico stores only a hash of the session. Removing a human from the roster ends their sessions.

On the wire, in a browser with `fetch`, that is:

```js
const res = await fetch("https://tico.example.com/api/v2/me", { headers: { Authorization: "Bearer " + token } });
```

The browser sends a CORS preflight first (`OPTIONS`) for the `Authorization` header, and for a write the
`Idempotency-Key` header; the server answers it for the origins in `TICO_CORS_ORIGINS` with
`Access-Control-Allow-Origin: <that origin>`, `Access-Control-Allow-Credentials: true`, the methods
`GET, POST, PATCH, PUT, DELETE, OPTIONS` and the headers `Authorization, Content-Type, Idempotency-Key, Last-Event-ID`.
Any other origin gets no CORS header at all, and the value is never `*`. With `TICO_CORS_ORIGINS` unset, none of this
exists: the server adds no CORS behavior.

Where to keep the token: in memory, or `sessionStorage` as the example does (it is gone when the tab closes and is not
shared with other tabs). Not in a cookie you set yourself, not in `localStorage`, and never in a URL. The token is what
a script injected into your page could steal, so keep your page free of injected script (see [security](#security)).

## Same origin (option A)

Put a reverse proxy in front that sends `/app/*` to your static files and everything else to Tico. Your page and the
API are then one origin: no CORS, no token, `fetch("/api/v2/me")` carries the cookie, and when it answers
`401` you send the browser to `/auth/login?next=/app/`. This is a sketch for the Docker install's Caddy (test it before
you rely on it): replace the caddy service's command in a `compose.override.yaml` next to `compose.yaml`,

```yaml
services:
  caddy:
    command: caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./frontend:/srv/frontend:ro
```

with a `Caddyfile` that keeps the domain from `.env` and adds the path:

```
tico.example.com {
	handle_path /app/* {
		root * /srv/frontend
		try_files {path} /index.html
		file_server
	}
	reverse_proxy server:8765
}
```

Your frontend's files go in `/opt/tico/frontend`. Cookie mode needs no `Authorization` header (drop it from the
example's `api()`), and writes from the same origin pass Tico's origin check. A frontend on a sibling *subdomain* is
option B, not A.

## Servers and scripts (option C)

A **personal API token** is a long random string that is the human who made it, for scripts and services:
`Authorization: Bearer tico_pt_...`. Make one signed in on Tico's own page, or with `POST /api/v2/me/tokens` from a
cookie session:

```
POST /api/v2/me/tokens   {"label": "portal-backend", "expires_in_days": 90}
-> {"id": "...", "token": "tico_pt_...", "label": "portal-backend", "expires_at": "..."}
```

The token is shown once and only its hash is kept. `GET /api/v2/me/tokens` lists yours (without the tokens) and
`POST /api/v2/me/tokens/{id}/revoke` ends one at once. Know what you are creating:

- **There are no scopes.** A personal token acts with its human's own rights, including a member's.
  Members can create tokens by default; the owner can turn off member tokens in **Settings → Humans**.
  A token grants no extra rights and cannot create or revoke tokens. See
  [External agent permissions](connect-an-agent.md).
- It lasts 90 days unless you say otherwise, at most a year. Rotate before it expires. It ends when the human leaves
  the roster.
- **Never put one in a browser**, in a mobile app, or in frontend source code. Anything a browser holds, a human can read.
  If your frontend needs server-held credentials, run a small backend of your own (a "backend for frontend") that keeps
  the token in its environment, signs the human in itself, and calls Tico on their behalf.
- Keep it in a credential store, send it only over https, and give each tool its own labeled token so one can be
  revoked without the others.

A token in a `curl` on the server that made it:

```
curl -H "Authorization: Bearer $TICO_TOKEN" https://tico.example.com/api/v2/needs-you
```

## Key endpoints

All paths are under your server's address, and all need `Authorization: Bearer ...` except `/healthz` and the sign-in
routes. Every `POST` also needs an `Idempotency-Key` header. Answers below are trimmed to the fields a frontend uses;
[`openapi/v2.json`](openapi/v2.json) has the full list.

| Resource | Request | Answer |
|---|---|---|
| Who am I | `GET /api/v2/me` | `{"actor": "human:ana", "role": "owner", "email": "ana@example.com"}`. `role` is `owner` or `human`. |
| Team | `GET /api/v2/config` | `{"company_name", "app_name", "assistant_name", "public_url", "owner_email", "version", "update": {...}}` |
| Team chart | `GET /api/v2/org` | `{"people": [{"id", "name", "email", "title", "reports_to", "team": "marketing", "org_parent": "p:ana"}], "bots": [{"id", "display_name", "description", "reports_to", "team": "marketing", "department": "Marketing", "org_parent": "b:coo", "status", "onboarding_state", "access": {"see", "read", "write"}}], "org_groups": [{"id", "name", "parent", "org_parent": "g:marketing"}]}`. `org_parent` is `p:<person>`, `b:<bot>`, `g:<group>` or `""` (top) and follows `reports_to`; to draw groups, put each human or bot in the group its `team` names and nest a group under its `parent` (the web app hangs a teammate under its manager only when the manager is in the same group). Only bots the caller can see are in it, and the ones under a hidden bot hang from the nearest thing above it that is shown; `?can=read` or `?can=write` keeps the bots they hold that level on. |
| Groups | `GET /api/v2/groups`; `POST /api/v2/groups` `{"name", "parent", "add": {"people": [], "bots": []}}`; `PATCH /api/v2/groups/{id}` `{"name", "parent", "add", "remove"}`; `DELETE /api/v2/groups/{id}` | `[{"id", "name", "parent", "people": ["cara"], "bots": ["seo"], "order"}]`; a write answers with the group. Owners and admins write; everyone reads (the bots they may see). A teammate is in one group: adding it to another takes it out of the first. `"parent": ""` moves a group to the top; a delete moves what is in it up to its parent. |
| Bots and status | `GET /api/v2/bots` | `[{"slug", "display_name", "state", "online": true, "queued": 0, "status": {"state", "focus"}, "owners": [...], "onboarding_state": "", "access": {"see": true, "read": true, "write": true}}]`. `state` is `active`, `paused`...; `onboarding_state` is `needs_setup` for a starter bot that Finish setup created and that has not yet been set up (it does nothing on its own until then), `onboarded` once it has, and empty for every other bot (draw a "Needs setup" mark on the first); `online` says whether its computer is connected. Only the bots the caller can see are listed, each with the caller's own `access`; a bot they may see but not read has no `status`, `online` or `queued`. `?can=read` or `?can=write` keeps the bots they hold that level on. Archived bots are left out (add `?include_archived=1` for an admin view that needs them). |
| One bot | `GET /api/v2/bots/{bot}` | `{"slug", "display_name", "description", "state", "reports_to", "reports_to_name", "operator", "operator_name", "team", "owners": [...], "onboarding_state", "template", "template_version", "access": {"see", "read", "write"}}` for anyone who can see it (`template` and `template_version` are set for a bot built from a template; the version is the release whose templates it came from); for anyone who can also read it: `"online"`, `"queued"`, `"status": {"state", "focus"}`, `"assignment"` and `"goals"` (the text set on its page). `404` for a bot the caller cannot see. This replaces the web app's own `GET /api/employees`, which is internal: build on this route and `GET /api/v2/bots`. |
| Mark a starter bot as set up | `POST /api/v2/bots/{bot}/onboarded` `{}` | `{"bot", "onboarding_state": "onboarded", "changed": true}`. The bot's own call (`hub bot setup-done`, MCP `hub_bot_setup_done`) once its setup is done, or its manager's; repeating it answers `"changed": false`. The bot stops being parked: its routines may run and its work is claimed. `409 bot_limit` for a human's bot when they already hold their limit of active bots (a parked bot does not count until now). To start a parked bot's setup, send it a message as the human: `POST /api/v2/chat/{bot}` `{"text": "Let's set you up."}`; while it is `needs_setup` only a human's chat message wakes it. A message from someone who manages the bot also switches its first routine on. |
| A bot's routines | `GET /api/v2/bots/{bot}/routines` | `{"routines": [{"id", "bot", "key", "title", "cron", "on", "kind": "cron"\|"event", "timezone", "enabled", "active", "text", "last_fired", "next_due", "deleted_at"}]}`. Needs Read on the bot (`403 forbidden` otherwise, `404` if the caller cannot see it); `?include_deleted=true` adds removed ones. |
| Who can use a bot | `GET /api/v2/bots/{bot}/access` / `PUT` | `{"bot", "see": {"everyone": true, "people": [], "teams": [], "bots": []}, "read": {...}, "write": {...}, "revision", "you": {"see", "read", "write"}, "teams": [{"id", "name"}]}`. For the owner, bot administrators and the humans the bot reports up to; anyone else gets `403`. `PUT` takes `see`, `read`, `write` (each `{"everyone": true}` or lists of `people` ids, `teams` and `bots` slugs) and the `revision` you read; `409 version_conflict` if it moved. See [permissions.md](permissions.md). |
| My chats | `GET /api/v2/conversations?chat_with=ops` | `{"conversations": [{"id", "participants", "last_message_at"}]}`: my open chat with that bot (none yet is an empty list). |
| Messages | `GET /api/v2/conversations/{id}/messages` | `{"messages": [{"id", "from_actor": "human:ana", "from_name": "Ana", "to_actor": "bot:ops", "to_name": "Ops", "body", "created", "refs", "run": {"job_id", "attempt_id", "state": "started_run"\|"added_to_run"}}], "actors": {...}, "has_more", "next_before"}`. Oldest first, up to 200; `?before=<next_before>` pages back. `run` says which run took a message and is absent until one has; a bot's reply carries `run` and `answers` too ([below](#which-run-took-a-message)). |
| Send to a bot | `POST /api/v2/chat/ops` `{"text": "Hello"}` | `{"conversation": {"id"}, "message": {"id", "body"}}`. Opens the chat if needed; the reply arrives [live](#streaming). |
| Send in a chat | `POST /api/v2/conversations/{id}/messages` `{"text": "Again"}` | `{"message": {...}}` |
| Tasks | `GET /api/v2/tasks?status=open,doing&owner=human:ana&limit=100` | `{"tasks": [{"id", "title", "body", "owner", "owner_name", "requester", "requester_name", "status", "due", "version", "labels", "number", "step_rank"}], "actors": {...}, "next_offset": null}`. Statuses: `open doing waiting review ready done declined`. `?offset=` pages. `?type=` and `?step=` (ids or names) and `?number=` filter, and `?sort=step` lists a type's board in order ([tasks.md](tasks.md)). To poll, keep the largest `updated` you have seen and ask `?updated_since=<it, ISO 8601 with a timezone>`: only tasks changed after it come back, closed ones included. Closing, reopening, commenting, a question asked or answered on it, a link or an attachment added or removed, a linked pull request's state and any change to its fields all change `updated`. `?brief=true` leaves out each task's `body` and `acceptance_criteria`; read one task for them. |
| One task | `GET /api/v2/tasks/{id}` | `{"task": {...}, "events": [...], "comments": [...], "children": [...]}` |
| Create task | `POST /api/v2/tasks` `{"title": "Draft the launch plan", "body": "Two pages.", "owner": "bot:ops"}` | `{"task": {...}}`. Titles start with a verb (`422 lint` otherwise), except on a custom `type`, whose tasks are tickets ([tasks.md](tasks.md)). `owner` is `bot:<slug>` or `human:<id>`. `POST /api/v2/tasks/dry-run` runs the checks without writing. |
| Update task | `POST /api/v2/tasks/{id}` `{"version": 3, "status": "done"}` | `{"task": {...}}`. Send the `version` you read; `409 version_conflict` means someone changed it: read again. `"title"` renames it, checked as a new task's title would be. `{"version": 3, "close": true, "note": "..."}` closes it (a close carries no other field). |
| Comment | `POST /api/v2/tasks/{id}/comments` `{"text": "Looks good"}` | `{"comment": {...}, "comments": [...], "woke": true}`. Each comment carries `edited_at`, null until its author edits it. |
| Edit or delete a comment | `POST /api/v2/tasks/{id}/comments/{comment_id}` `{"text": "Looks good to me"}`; `POST /api/v2/tasks/{id}/comments/{comment_id}/delete` `{}` | `{"comment": {..., "edited_at"}, "comments": [...], "woke": false}`; after a delete the tombstone has an empty body and `deleted_at` and is no longer in `comments`. Only its author, signed in as themselves (`403` for anyone else, and for a question, an answer or a chat line); `404` for a comment not on the task. Wakes nobody. Only plain comments without attachments or structured review data can change. Prior delivery does not block editing the current record; existing external and provider copies cannot be recalled. Offer it when the spec has `editTaskComment` ([api.md](api.md#task-comments)). |
| Updates | `GET /api/v2/updates?kind=daily&unread=true` | `{"updates": [...], "unread": 2, "next_before": null}`. `GET /api/v2/updates/unread` is the count; `POST /api/v2/updates/read` `{"ids": [...]}` or `{"all": true}` marks them; `POST /api/v2/updates/{id}/reply` `{"text"}` answers the bot. |
| Tools | `GET /api/v2/bots/{bot}/tools` | `{"bot", "tools": [{"id", "service", "name", "logo_key", "identity", "can": [...], "scope": {...}, "env", "note", "status": "ready"\|"problem"\|"unknown", "problem", "detail", "url", "pending", "task_id"}], "computer", "online", "reported_at"}`: the tool icons beside a bot's name and the Tools list under its More tab. The model and harness first, then its repository, then each `access:` entry of its `bot.yaml` as its computer reports it. `scope` holds database, channels, project, mailbox and the like; `env` is a variable's name, never a value; `problem` is why a tool is not ready ("Credential missing on Test Mac"); `logo_key` is a [Simple Icons](https://simpleicons.org) slug or `null`, so draw the name's first two letters when you have no logo for it. A computer that predates the report yields the model and repository only. See [creating-bots.md](creating-bots.md#what-humans-see-about-a-bots-tools). |
| Register a tool | `POST /api/v2/bots/{bot}/tools` `{"service": "posthog", "identity": "PostHog project 340585 (US)", "can": ["read"], "scope": {"project": "340585"}, "env": "POSTHOG_KEY", "note": "funnels only"}` | `{"tool": {..., "status": "pending"}, "task_id", "yaml", "credentials"}`. For the bot's managers (the owner, a bot administrator who owns it, or someone above it on the team chart). The server holds no bot repository, so it validates the entry against the `bot.yaml` schema, keeps it as a pending request and opens a task for BotOps with the exact `access:` entry (external profiles receive their own configuration task and report their tools through `POST /api/v2/agents/heartbeat`); the tool shows as `pending` until the bot's computer reports it, then `ready` or `problem`. `env` is a variable's name: a value, or anything that looks like a key or a token, is refused (`422 secret`), and the owner installs the credential on the bot's computer ([install.md](install.md), [creating-bots.md](creating-bots.md#6-access-and-credentials)). `409 botops` while BotOps is not running; `409 duplicate` for a tool already there or requested. |
| Remove a tool | `DELETE /api/v2/bots/{bot}/tools/{id}` | `{"removal": true, "task_id"}`: a BotOps task to take a declared tool out of `bot.yaml` (the id is from the list; `model` and `repo` cannot be removed). A `pending-…` id withdraws a request instead: `{"cancelled": true}`. `POST /api/v2/bots/{bot}/tools/{id}/delete` does the same for clients that only send GET and POST (the `hub` CLI and MCP tools use it). |
| Files | `GET /api/v2/bots/{bot}/files?limit=3&cursor=...` | `{"bot", "files": [{"id", "title", "kind", "locator", "scope", "version", "state", "synced", "open": {"type": "tico"\|"external", "url"}, "note", "task_title", "working", "github_url", "actor", "actor_name", "last_activity_at"}], "total", "next_cursor", "has_more", "can_manage"}`. Newest activity first; only what the caller may see, the total included. A stored file's `open.url` is a Tico route that needs the bearer; an `external` one opens at its provider. `PATCH /api/v2/files/{id}` with `{"promote": true}` or `{"archived": true}` (owner and bot administrators). See [files.md](files.md). |
| Assistant | `GET /api/v2/assistant` | `{"available", "name", "can_turn_on", "room_id", "messages": [...], "execution", "actions": {...}, "pending": [...]}`: the caller's own private Assistant chat ([assistant.md](assistant.md)). `POST /api/v2/assistant/messages` `{"text"}` answers a lookup at once (`{"fast": true, "reply": {...}}`) or starts a bot run: poll `GET /api/v2/assistant` while `execution` is set (show "thinking"). A message with `refs.action` is a Confirm / Cancel card: `POST /api/v2/assistant/actions/{id}/confirm` or `/cancel` (the human's own session only). `POST /api/v2/assistant/turn-on` (owner) turns it on when `available` is false. Links in a reply are `[title](#/task/<id>)`-style in-app routes or `https:` links: map the `#/` ones to your own routes. |
| Needs you | `GET /api/v2/needs-you` | `{"actor", "items": [{"id", "kind": "task"\|"question"\|"declined"\|"approval", "title", "first_line", "requester", ...}]}`. `?count=true` gives `{"actor", "count"}` for a badge. |
| Answer a question | `POST /api/v2/messages/{message_id}/answer` `{"text": "Yes"}` | The question's `ask.id` on a `question` item is that message id. `{"text": "", "unknown": true}` says you do not know. |
| Decide an approval | `POST /api/v2/approvals/{id}` `{"decision": "approved", "note": ""}` | `decision` is `approved` or `declined`. The item's `id` is the approval id. |
| Meetings | `GET /api/v2/meetings/search?q=roadmap&limit=20` | `{"results": [{"id", "title", "created", "excerpt", "passages": [...]}], "next_offset"}`. No `q` lists the newest. `GET /api/v2/meetings/transcript?id=...` reads one. |
| Docs | `GET /api/v2/docs?path_prefix=sales/` | `{"docs": [{"id", "path", "title", "updated", "updated_by", "updated_by_name", "locked", "version"}], "next_cursor"}`: the team's internal docs (Markdown) without their text. `POST /api/v2/docs` `{"title", "body", "path"?}` writes one; `GET /api/v2/docs/{id}` reads it (`body`, `version`); `PATCH /api/v2/docs/{id}` `{"version": 3, "body": "..."}` edits it (`409 version_conflict` means someone saved first: read again; `403 locked` for a locked doc; `locked` itself is for owners and bot administrators); `GET /api/v2/docs/{id}/versions` and `POST /api/v2/docs/{id}/restore` `{"version": 1}` are the history; `POST /api/v2/docs/import` takes a multipart `file` (.md .txt .html .docx .pdf). `GET /api/v2/linked-docs` lists links to docs that live elsewhere (`POST`, and `PATCH /api/v2/linked-docs/{id}` to change or remove one). See [docs.md](docs.md). |
| Docs search | `GET /api/v2/docs/search?q=refund` | `{"results": [{"type": "internal", "id", "path", "title", "excerpt", "score"}, {"type": "linked", "id", "title", "url", "kind", "description", "score"}]}`: both kinds, best first. The Assistant's older `GET /api/v2/context/search?q=refund&source=docs` covers both too: `{"results": [{"kind": "document" or "linked_doc", "id", "title", "excerpt", "url", "score"}], "has_more"}`, and `GET /api/v2/context/document?id=...` reads one. |
| Goals | `GET /api/v2/goals/tree` | `{"goals": [{"id", "title", "owner", "owner_name", "parent_id", "status", "status_note", "status_by", "status_source": "auto"\|"person", "suggest_status", "suggest_note", "kpis": [{"id", "name", "unit", "latest", "freshness", "spark", "link", "target_label", "status", "reason"}], "checkin"}], "owners", "other_kpis": [...], "proposals": [...]}`. Every goal the caller may see with its KPIs; `status` is `red`, `yellow`, `green`, `gray`, `done`, `dropped` or null. `GET /api/v2/goals/{id}` is one goal with its tasks, history and check-ins; `GET /api/v2/goals?all=1` a flat list. [Goals and KPIs](goals-and-kpis.md) has the model. |
| Set a goal's colour | `POST /api/v2/goals/{id}/status` `{"status": "yellow", "note": "Backend is late."}` | `{"goal": {...}}`. Sets it by hand: it sticks (`status_source` is `person`) until `POST /api/v2/goals/{id}/status/auto` `{}` hands it back and it is worked out again. |
| KPIs | `GET /api/v2/kpis?unlinked=true` | `{"kpis": [{"id", "slug", "name", "definition", "unit", "direction", "cadence", "owner", "definition_version", "archived_at", "archived_by", "latest", "freshness", "spark", "status", "reason", "goals": [...]}]}`. Archived KPIs are omitted by default; `include_archived=true` is the historical view. `GET /api/v2/kpis/{id}` adds `links` (goal, target, colour), `readings`, `definitions`, `checkins`, `may_edit` and `may_log`. `POST /api/v2/kpis` makes one (with `goal_id` and target fields it is linked), `POST /api/v2/kpis/{id}` edits it, and `POST /api/v2/kpis/{id}/archive` or `/restore` hides or restores it without deleting its history. |
| Link a KPI | `POST /api/v2/goals/{id}/kpis` `{"kpi_id": "...", "kind": "improve", "baseline": 40, "target": 65, "deadline": "2026-12-31"}` | `{"kpi": {...}}`. `kind` is `improve`, `maintain` (`min`, `max`) or `none`. `POST /api/v2/goals/{id}/kpis/{kpi}` changes the target; `.../unlink` removes the link. |
| Readings | `GET /api/v2/kpis/{id}/readings` / `POST` `{"value": 52, "period_end": "2026-09-28", "evidence": "https://...", "quality": "measured"}` | `{"readings": [...]}` / `{"reading": {...}}`. Readings are never edited: post another with `"supersedes": "<reading id>"` and a note to correct one. A bot's automatic KPIs are `GET /api/v2/bots/{bot}/kpis`. |
| Proposals | `GET /api/v2/proposals` / `POST /api/v2/proposals/{id}/decide` `{"decision": "confirm"}` | `{"proposals": [{"id", "kind", "goal_id", "kpi_id", "payload", "reason", "proposed_by", "may_decide"}]}`. The Goal Manager proposes a definition or target change; the owner decides. `GET /api/v2/goals/needs-you` lists what waits on the caller. |
| Health | `GET /healthz` (no sign-in) / `GET /api/v2/health` | `{"ok": true, ...}` / `{"checks": [{"id", "label", "status", "summary"}], "attention": 0, ...}` (humans only) |

**Names next to ids.** Ids such as `human:ana` and `bot:ops` are stable keys; show humans the names. Answers on these routes
carry them beside the ids and never instead of them: an object with an `owner`, `requester`, `from_actor`, `to_actor` or
`actor` also has `owner_name`, `requester_name`, `from_name` and so on (`participant_names` and `owner_names` for lists), and a
`GET` answer that is an object has `actors`, `{"human:ana": "Ana Alvarez", "bot:ops": "Ops"}`, for every id it mentions, so a
list needs no extra lookups. Notices Tico writes ("New task from bot:ops: ...") are shown to humans with names in `body`;
the text as stored is in `body_raw`, and the ids stay in `from_actor`, `to_actor` and `refs`. A name is only a label: match on
the id. An id with no name (the owner, a removed human) has no `_name` field: show the id or your own fallback.

What a human may see or do is decided by the server, not the frontend: a bot they may not see is missing from the
list and answers `404`, a bot they may see but not read or write to answers `403 forbidden` with what is missing, a task
they may not read is left out of lists and refused by id, and an edit they may not make is a `403`. Use the `access` on each
bot to decide what to show (a chat box needs `write`, its activity needs `read`), and build for those answers. A human who
may write to a bot but not read it still sees their own conversations with it and the tasks they requested or own.

## Streaming

A bot answers in seconds to minutes, and the reply is produced piece by piece. After `POST /api/v2/chat/{bot}`, watch the
conversation:

```
GET /api/v2/conversations/{id}/watch        Accept: text/event-stream
```

It is [Server-Sent Events](https://developer.mozilla.org/docs/Web/API/Server-sent_events): a series of blocks separated
by a blank line.

```
event: snapshot
data: {"messages": [...newest 200...], "has_more": false, "next_before": null,
       "execution": {"state": "running", "label": "Working", "bot": "ops", "job_id": "...",
                     "text": "I'll file the task.\n\nFiled it. The reply so far",
                     "parts": [{"kind": "progress", "text": "I'll file the task.", "at": "2026-09-29T10:00:01.120Z"},
                               {"kind": "tool", "text": "Ran hub task create", "at": "2026-09-29T10:00:02.410Z"},
                               {"kind": "reply", "text": "Filed it. The reply so far", "at": "2026-09-29T10:00:04.870Z"}], ...}}

: keepalive
```

- Each **`snapshot`** is the whole picture: the newest messages and the bot's current run. `execution.state` is
  `queued`, `leased`, `running`, `completed`, `uncertain` and so on; `execution.label` is a sentence for a human
  ("Saved - waiting for a computer"); while the state is `leased` or `running`, `execution.text` is **the reply so
  far**. When the run completes, the final message is in `messages` and `execution.text` is no longer needed. Show
  `text` as a growing bubble, then let it be replaced by the stored message. `execution` is `null` before the first
  message.
- **`execution.text` and `execution.parts`.** A run can write several messages ("I'll look into it", then a tool call,
  then the answer). `text` joins them with a blank line (`"\n\n"`), so rendering it as Markdown gives one paragraph
  each, while the pieces of one message (the token deltas as it is typed) join with nothing between them. `parts`
  is the same run as a list, in order, for a frontend that wants to style them apart. Each is `{"kind", "text", "at"}`:
  - `reply`: what the bot wrote;
  - `progress`: what the bot wrote just before a tool call, as a note on what it is about to do;
  - `tool`: a short label such as `Ran hub task create`. It is the tool's name only; the call's arguments and output are never
    in a snapshot. A bot's tool calls appear here when its runtime reports them (Cursor, Gemini, Pi, Grok and Antigravity
    do; Claude and Codex do not, so their pieces are all `reply`).

  `text` leaves the tool labels out. Like `text`, `parts` is filled while the state is `leased` or `running`. `at` is when
  the piece began. Show a `tool` piece as a quiet line; it is not part of the answer.
- A new snapshot is sent when something changed (a new message, a new piece of the reply, a computer going offline);
  `: keepalive` comment lines fill the time between. They carry no data.
- **`event: expired`** means the session ended: sign in again.
- **The stream ends after about a minute** by design (so a revoked session cannot keep listening). This is normal:
  **reconnect**. The first thing a new connection sends is a full snapshot, so there is nothing to resume and no
  cursor to keep. Wait a moment between attempts and back off (0.5 s, 1 s, 2 s ... up to 15 s) while the network is
  down; reconnect at once when the tab becomes visible or the browser goes back online.
- **Do not use `EventSource` with a bearer session:** it cannot send an `Authorization` header. Read the stream with
  `fetch` and a `ReadableStream`, as `watch()` in [`examples/custom-frontend/app.js`](../examples/custom-frontend/app.js)
  does (about twenty lines). With the same-origin cookie option, `EventSource` works.
- Open one stream per visible conversation and close it (`AbortController`) when the human leaves the chat. Each
  costs the server a small read every second.

Reads that do not need a stream: `GET /api/v2/conversations/{id}/snapshot` is one snapshot; `GET
/api/v2/conversations/{id}/messages` is the message history.

**When you need a cursor.** `GET /api/v2/conversations/{id}/stream?after=<n>` is the lower-level feed: `event: output`
blocks with an `id:` line (the cursor) and the run's raw events as they are written (`kind` `delta` with
`payload_json` `{"text": "..."}`, `tool`, `status`...), plus `event: messages` every second with the message list. To
resume, pass the last `id` you saw as `after` (the `Last-Event-ID` header is not read by the server: a `fetch` client
sets `after` itself). It ends after about a minute as well. Use `/watch` unless you are building a step-by-step view of
what a bot is doing.

### Which run took a message

Messages sent while a bot is working are folded into its current run (`execution.label` reads "Working - follow-up
added"). The messages say so themselves, on the routes that return them (`/messages`, `/snapshot` and `/watch`):

```json
{"messages": [
  {"id": "m1", "from_actor": "human:ana", "body": "Draft the launch plan",
   "run": {"job_id": "j1", "attempt_id": "a1", "state": "started_run"}},
  {"id": "m2", "from_actor": "human:ana", "body": "Also cover pricing",
   "run": {"job_id": "j1", "attempt_id": "a1", "state": "added_to_run"}},
  {"id": "m3", "from_actor": "bot:ops", "in_reply_to": "m1", "body": "The plan is in the task.",
   "run": {"job_id": "j1", "attempt_id": "a1", "did": [...], "steps": 4, "tool_calls": 2, "took_s": 38},
   "answers": ["m1", "m2"]}]}
```

- A human's message that starts a run has `run.state` `started_run`; one delivered into a run that was already working
  has `added_to_run`. Either way `job_id` and `attempt_id` name that run, so `m2` above belongs to the run of `m1`.
- A message no run has taken yet (it is still queued) has no `run`. A reply written before this existed has `run` only when it was answering a run, and no `answers`.
- The reply the run wrote carries `run` (the same `job_id` and `attempt_id`, with a summary of what it did) and `answers`,
  the ids of every message in that conversation the run handled: the one that started it first, then the ones folded in.
  Use it to show one reply under both messages, and to tell "answered" from "waiting for the next run".
- These are stored with the run, so they are the same after a restart or a reconnect. If a run fails and its
  messages are queued again, they get a new `run` when the next run takes them.

## Errors

Most failures are a **Problem** with an HTTP status:

```json
{"error": {"code": "version_conflict", "detail": "Task changed; fetch it and retry your update", "retryable": false}}
```

`code` is for your code to branch on, `detail` is a sentence a human can read, and `retryable` says whether the same
request may succeed later. Some carry one extra fact beside them (a `sign_in` path on a 401). Codes you will meet:

| Status | `code` | Meaning |
|---|---|---|
| 401 | `identity` | Not signed in, or the session or token ended. With built-in sign-in the body has `"sign_in": "/auth/login"`. Start the [sign-in](#sign-in-option-b) again. |
| 403 | `identity` | Signed in, but not on this Tico's roster, or the human has left the team. |
| 403 | `forbidden` | The human may not do this, or may not see that bot. |
| 403 | `origin` | A browser write came from an origin that is not allowed: add it to `TICO_CORS_ORIGINS`. |
| 404 | `not_found` | Missing, or not visible to this human (the two look the same on purpose). |
| 409 | `version_conflict` | The task changed since you read it; read again and reapply. |
| 409 | `idempotency_conflict` | An `Idempotency-Key` was reused with a different body. Use a new key per action. |
| 413 | `too_large` | Over 2 MB (uploads 20 MB). |
| 422 | `idempotency_key` | A write without an `Idempotency-Key`. |
| 422 | `lint`, `date`, `hierarchy`... | A rule of the product refused the request. `detail` says what to change. |
| 429 | `cap`, `budget` | A daily allowance is used. |
| 503 | `storage_unavailable` | Try again shortly (`retryable: true`); back off. |

Request validation (a missing field, a wrong type or an unknown field) uses the same envelope:
`{"error": {"code": "validation", "detail": "body.text: Field required", "retryable": false}}` with status `422`.
The detail names the field and reason; it does not echo its input. MCP validates arguments against the tool's input schema
before making an API call and preserves these field details.

A network failure or CORS refusal has no body at all: `fetch` throws. In the browser console a CORS error names the
missing header; on the server the cause is nearly always an origin missing from `TICO_CORS_ORIGINS` (the origin is
scheme, host and port exactly, and has no trailing slash).

## Rate limits

Tico applies **no request-rate limit** to the API: it will answer as fast as it can. The limits that exist:

- Bodies: 2 MB for writes; 20 MB for uploads (`/api/v2/uploads/...`) and meeting imports (`413 too_large`).
- A few actions have a daily allowance (tool notes, the server's decisions) and answer `429` when it is used; the
  `detail` says which.
- Live streams: each open `/watch` or `/stream` costs one database read per second and holds a worker for up to a
  minute; keep to one per visible chat.
- Be a good client: use `/watch` instead of polling a chat; poll `GET /api/v2/needs-you?count=true` (a few bytes) no more
  than every 30 seconds for a badge; do not refetch `/api/v2/org` or `/api/v2/bots` on every render (cache them for a
  minute). Answers are marked `no-store`, so cache in your own code.
- If you expose your frontend to many humans, set a limit at the reverse proxy (Caddy, Cloudflare) in front of Tico.

## Versioning

The path carries the version: `/api/v2/`. The rules are in [api.md](api.md#stable-and-internal); in short, what is in
`openapi/v2.json` only grows within v2, everything else is internal, and unknown response fields are to be ignored.
`info.version` in the spec is the API version (`2.0.0`), not the server's; `GET /api/v2/config` has the server's `version`.
Pin your generated client to the spec you built against and watch [CHANGELOG.md](../CHANGELOG.md), where API additions
are listed under "Added".

To generate a typed client, feed `openapi/v2.json` to any OpenAPI 3.1 generator (`openapi-typescript`, for one). Answers
are described loosely on purpose (`additionalProperties` is open), so treat the generated types as a floor.

## Security

- **Bot text is untrusted.** A bot's reply, a task body or a document can contain anything, including HTML and links. Put
  it on the page as text (`textContent`), or through a sanitizer such as DOMPurify if you render Markdown. Never assign
  it to `innerHTML`. Tico's own page does the same. The example renders plain text only.
- **Set a Content-Security-Policy** on your frontend (`script-src 'self'`; no inline script; `connect-src` your Tico
  address). It is your defense for the bearer session in the page.
- **https everywhere** except `localhost` (Tico refuses `http` origins in `TICO_CORS_ORIGINS` for any other host).
- **`localhost` in production.** Listing `http://localhost:5173` lets whatever runs on that port on a colleague's laptop
  sign in as them and call the API. Fine for development and staging; remove it from the production server, or use a
  separate Tico for development.
- **CSRF.** A bearer session is sent only by your code, so a forged request from another site carries nothing. The
  cookie path stays protected as before: `SameSite=Lax` and an `Origin` check on every write, which allows only Tico's
  own address and the origins you listed.
- **Redirects.** Tico redirects a signed-in browser only to a path on its own address or to an origin you listed, with
  the code in the fragment; the code is single-use, expires in 60 seconds and is useless without your PKCE verifier.
- **Sign out** with `POST /auth/token/revoke`, not only by forgetting the token.
- **Personal API tokens** carry a human's full rights. Servers only; see [option C](#servers-and-scripts-option-c).
- Tico's own page cannot be put in a frame (`frame-ancestors 'self'`); embed by building the screens, not by iframe.
- The built-in sign-in is the only method that hands a separate frontend a session. With Cloudflare Access or an AWS load
  balancer in front (`TICO_AUTH_PROXY=cloudflare|aws-alb`), the proxy owns sign-in and its cookie is for Tico's own
  host: use option A (same host, behind the same proxy) or option C from a backend of your own.

## The example app

[`examples/custom-frontend/`](../examples/custom-frontend/) is `index.html`, `app.js`, `style.css` and `config.js`: about
280 lines of script, no build, no dependencies, and it is what this page describes. It signs in with the flow above, then shows
the team chart (humans and bots nested by `reports_to`), a chat with any bot with the reply streaming in through
`/watch`, the open tasks, and Needs you. It sets no cookies and puts everything on the page as text.

`ui/tests/custom-frontend.cjs` runs it in a real browser against a real server on another origin, once with a local
server (`TICO_AUTH_PROXY=none` with the owner token, the development shortcut the example offers only for a Tico on
`localhost`) and once with built-in sign-in and PKCE against a stand-in identity provider. Run it with `npm run test:ui`.
`backend/tests/test_frontend_access.py` covers the server side: CORS for allowed and other origins, preflights, the
redirect allowlist (open redirects refused), and the code exchange (single-use, verifier and origin bound, expiry).

Ideas for the next step: answer questions and approvals from Needs you (`/messages/{id}/answer`, `/approvals/{id}`), create
tasks, show updates, and attach files (`POST /api/v2/uploads/chat/{bot}`, `multipart/form-data`).

## Tag display and checklists

Task responses retain `labels` as keys and add `tags` with `id`, `key`, `label`, `metadata`,
`markdown`, `is_template`, `template_id`, `owner` and `version`. Show the label and metadata
on a chip, and use the key with existing label filters and task writes. Read a tag with
`GET /api/v2/tags/{id}` for its notes, `editable` flag and visible tasks. Save notes with
`POST /api/v2/tags/{id}` and its current version; preserve a draft when a `409 version_conflict`
requires loading newer notes. Create an instance with `POST /api/v2/tags/{id}/instances`
instead of attaching a template directly. [The tag API](api.md#tags) describes all fields.
