# Tools redesign: build contract (Nov 5, 2026)

The approved plan: "Tico Tools and Access", draft 4 (https://claude.ai/artifact/BFT1iMVSDd4LKZo5rWaiAh). Mockups:
https://claude.ai/artifact/6hBKJmYdQXRj4CYTtw8VJd. This file is the contract the build tracks share. One branch,
`chris/tools-redesign`, merged to main once as a whole after Chris reviews it. Tracks work on their own branches off
it and are merged into it, never into main.

## What ships

1. **Everything is a Tool.** Tools moves to the top level of the sidebar. Library keeps Docs, Files, Meetings.
   Library → Connections and Settings → Inboxes go away; their rows become Tools rows (homes below).
2. **API tools hold several keys, one per level.** A key is `read` or `read_write`. A caller at Read gets the read
   key; with no read key, a Read caller may only make GET/HEAD/OPTIONS calls through the proxy.
3. **Every API tool call can go through the Tico proxy** (`delivery = 'proxy'`): pass-through, logged, the key
   chosen by the caller's level, the key never leaves the server. `delivery = 'computer'` keeps today's behavior
   (the value goes to the run's environment). New tools with a known service default to `proxy`; existing ones are
   migrated as `computer` and show "Key on computers" under Needs you with a one-click "Move to proxy".
4. **Real checks.** A check is an authenticated GET to `check_path` with the key. 2xx = Working; 401/403 = Key
   rejected; other = Check failed. A 401 is never "reachable". Every 30 minutes for proxied tools with a check path,
   and on "Check now".
5. **Limits** card on every API tool: placeholder only ("Not available yet"). Mailboxes' Limits card is the live
   sending policy.
6. **Mail: one send system.** The server sending policy (`backend/email_sending.py`) is the only one; the server
   sends Workspace mailboxes' mail itself; the old connector chain (`connectors/mail/policy.py` check_send and its
   caps, `registry/mail-policy.yaml`, standing allowances, GitHub Issue approvals) is deleted, its useful checks
   folded into the server policy.
7. Every refusal names the one setting that would change it.

Out of this branch, by the plan's own order: browsers on the Tico server and home-exit proxying (needs the 2-week
trial), the website broker, and the Limits rule engine.

## Schema (Rails naming: plural tables, `*_id`, `*_at`, `*_on`)

Migration `backend/migrations/<timestamp>_tools_and_keys.sql` (track A creates it with `scripts/new_migration.py`):

```sql
CREATE TABLE IF NOT EXISTS tools(
 id TEXT PRIMARY KEY,                       -- slug, e.g. 'close-crm', unique per install
 name TEXT NOT NULL,
 service TEXT NOT NULL DEFAULT '',          -- backend/bot_tools.py SERVICE_NAMES key when known
 base_url TEXT NOT NULL DEFAULT '',         -- https://api.close.com ; '' = cannot be proxied
 auth_scheme TEXT NOT NULL DEFAULT 'bearer',-- bearer | basic_user | basic_password | header:<Name> | query:<param>
 check_path TEXT NOT NULL DEFAULT '',       -- GET path for the check, e.g. /api/v1/me/
 delivery TEXT NOT NULL DEFAULT 'proxy',    -- proxy | computer
 created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
ALTER TABLE credentials ADD COLUMN tool_id TEXT REFERENCES tools(id);
ALTER TABLE credentials ADD COLUMN access_level TEXT NOT NULL DEFAULT 'read_write';  -- read | read_write
ALTER TABLE credentials ADD COLUMN expires_on TEXT;                                   -- YYYY-MM-DD or NULL
ALTER TABLE credential_grants ADD COLUMN access_level TEXT NOT NULL DEFAULT 'read_write';
CREATE TABLE IF NOT EXISTS tool_checks(
 id TEXT PRIMARY KEY, tool_id TEXT NOT NULL REFERENCES tools(id), credential_id TEXT,
 ok INTEGER NOT NULL, status_code INTEGER, detail TEXT NOT NULL DEFAULT '', checked_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS tool_checks_by_tool ON tool_checks(tool_id, checked_at DESC);
CREATE TABLE IF NOT EXISTS tool_calls(
 id TEXT PRIMARY KEY, tool_id TEXT NOT NULL REFERENCES tools(id), credential_id TEXT,
 actor TEXT NOT NULL, bot TEXT NOT NULL DEFAULT '', access_level TEXT NOT NULL,
 method TEXT NOT NULL, path TEXT NOT NULL,        -- path only; query keys kept, query values dropped
 status_code INTEGER, result TEXT NOT NULL,       -- sent | refused | failed
 detail TEXT NOT NULL DEFAULT '', duration_ms INTEGER, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS tool_calls_by_tool ON tool_calls(tool_id, created_at DESC);
```

A Python data migration right after it (track A) creates one `tools` row per existing tool-type credential (API keys
and tokens a person stored for a service: NOT model keys, computer keys, subscriptions, gateway keys, files), sets
`credentials.tool_id`, fills `base_url/auth_scheme/check_path` from the known-services table when the env name or
credential name matches, and sets `delivery = 'computer'` for every migrated tool so nothing changes for running bots.
Existing grants keep `access_level = 'read_write'`. Retention: `tool_calls` and `tool_checks` older than 90 days are
trimmed by the existing storage trim job.

Known services table: `backend/tool_services.py` (track A), e.g.
close (https://api.close.com, basic_user, /api/v1/me/), stripe (https://api.stripe.com, bearer, /v1/balance),
github (https://api.github.com, bearer, /user), openai-style, posthog, notion, linear, calendly, brex, mercury,
cloudflare, hugging-face, elevenlabs, heygen. Twilio (basic: Account SID as user + token as password) uses
`basic_password` with the SID kept as the credential `username`.

## Level of a subject on a tool

`level(subject, tool)` = the highest `credential_grants.access_level` over active grants on that tool's credentials
(`read_write` > `read`), or none. Setting a level on a tool (`PUT /api/v2/api-tools/{id}/access/{subject}`):

- `read`: if the tool has a `read` key, grant that key (grant level read) and revoke grants on the others; else
  grant the `read_write` key with grant level `read`.
- `read_write`: grant the `read_write` key (grant level read_write), revoke the rest.
- `none`: revoke all.

Subjects are `bot:<id>` and `human:<id>` (the existing credential_grants subjects). The Assistant acts as its person.

## API (all under /api/v2; `/api/v2/tools...` is rewritten to /integrations by route_renames, so do not use it)

Track A:
- `GET /tool-index` → `{rows:[{id, kind, name, detail, way, status:{tone, text}, who:[{kind:'bot'|'person', id,
  name, initials}], who_total, needs_you, href}], counts:{all, needs_you, api, mail, websites}}`.
  `kind`: `api` (a tools row), `mailbox`, `number`, `address` (mailing address), `slack`, `github`, `meetings`,
  `listening`, `webhook` (outgoing), `declared` (a bot.yaml tool with no tools row). Rows other than `api` come from
  `backend/connections.py`'s gatherers (keep them, drop the `/connections` endpoint). Incoming webhooks and
  triggers are not rows (they live in Automations). `tone`: ok | wait | fail | run | idle. `href` is the UI address.
  Visibility: the same rules connections.py and the credential vault use today.
- `GET /api-tools/{id}` → `{tool:{id,name,service,base_url,auth_scheme,check_path,delivery}, keys:[{id, name,
  preview, access_level, expires_on, created_by, created_at, last_used_at, last_used_by}], access:[{subject, kind,
  name, initials, level, last_used_at}], checks:[{ok, status_code, detail, checked_at}] (last 24), recent:[tool_calls
  last 5], status:{tone,text}, can_manage}`.
- `POST /api-tools` (create, `{name, service?, base_url?, auth_scheme?, check_path?}`), `PATCH /api-tools/{id}`
  (same fields + `delivery`), `DELETE /api-tools/{id}`.
- `POST /api-tools/{id}/keys` `{name, value, username?, access_level, expires_on?}` (stores a credential with
  `tool_id`), `PATCH /api-tools/{id}/keys/{cid}` (`access_level`, `expires_on`, rotate `value`), `DELETE` a key.
- `PUT /api-tools/{id}/access/{subject}` `{level: 'read'|'read_write'|'none'}`.
- `POST /api-tools/{id}/check` → the check row; background loop every 30 min.
- `GET /api-tools/{id}/activity?cursor=&limit=&result=` → tool_calls + credential uses, newest first.
- Who may manage: whoever may manage credentials today (`require_admin`/vault admins). Who may read a tool's page:
  vault openers plus anyone granted on it.
- `hub tools list` replaces `hub connections list`; `hub tools check <id>`.

Track B:
- `ANY /tool-proxy/{tool_id}/{path:path}`: the caller puts its proxy token where the vendor expects the key
  (Bearer, Basic user, Basic password, the scheme's header or query param). The server verifies it, picks the key by
  level, rewrites auth per `auth_scheme`, forwards to `base_url + path` with the query string, streams the response
  back, and logs one `tool_calls` row. Refusals are JSON `{error, detail}` 403 with `detail` naming the setting, e.g.
  "Sam Ortiz's level on Close CRM is Read: only GET calls. Add a read key or set Read and write."
- Proxy token: signed, bound to (attempt id, tool id, subject), valid only while the attempt is leased/running.
- `GET credential-runtime` and `runner-watcher-credentials`: for a held credential whose tool is `delivery='proxy'`,
  return `{env, value: <proxy token>, proxy_url: <server>/api/v2/tool-proxy/<tool_id>, url_env:
  'TICO_TOOL_<TOOL_ID_UPPER_UNDERSCORE>_URL'}` instead of the value; the runner sets both env vars. No key value is
  sent for a proxied tool. A computer credential is unchanged.
- The bot's prompt tools section says, per proxied tool: call `$TICO_TOOL_X_URL/<path>` with `$ENV` where the key
  goes. `hub tools call <tool> <METHOD> <path> [--data JSON]` does it for the bot.
- SECURITY.md and docs/credentials (whichever exists) updated.

Track C (mail): see its brief. Endpoints for the mailbox page's Limits card already exist
(`/email/inboxes/{mailbox}/sending-policy`, `/bots/{bot}/email-sending-policy`, `/email/sending-policy`).

Track D (UI):
- Sidebar: top-level **Tools** (`#/tools`, data-nav="tools") after Automations; Library keeps Docs, Files, Meetings.
- `#/tools` list: filters All / Needs you / APIs / Mail and numbers / Websites; columns Tool | Way in | Who can use it
  (avatar stack, title hover, "+N more") | Status.
- `#/tools/api/<id>` Overview (Who can use it with Read / Read and write segments, Recent activity, Keys, Check with 24
  squares, Limits placeholder) and Activity tab (`#/tools/api/<id>/activity`, a plain log with filters).
- `#/tools/mailbox/<address>`, `#/tools/number/<id>`, `#/tools/address/<id>`: the existing Settings → Inboxes pages
  moved here, mailbox Overview with Who can use it, Limits (the sending policy, per bot), Keys, Automations; an
  Activity tab.
- Old addresses redirect: `#/library/tools…`, `#/library/connections…`, `#/settings/inboxes…`, `#/integrations`,
  `#/credentials`. Personal phone alerts move to Settings → Notifications.
- Minimal copy: short labels, no right-side card subtitles, no fluff. Dark + light, phone width.

## Tests (RunsOn only: `python3 scripts/remote.py --setup python|browser --minutes 4 -- …` on a pushed commit)

High-ROI only: migration on an old-main schema keeps every grant and delivery; level → key choice; a Read caller
refused a POST with only a full key; proxy never returns or logs the key; proxy token for another tool/attempt or an
ended attempt is refused; credential-runtime sends no value for a proxied tool; check marks 401 as Key rejected;
tool-index visibility (a non-admin sees only rows they may see); one send system decisions; the UI pages render with
screenshots (`TICO_UI_SHOTS`).
