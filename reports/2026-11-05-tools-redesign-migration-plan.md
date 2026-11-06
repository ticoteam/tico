# Tools redesign: migration and deploy plan (Nov 5, 2026)

Branch `chris/tools-redesign`, merged to main once, as a whole, after review. Build contract:
`reports/2026-11-05-tools-redesign-build.md`. This page covers what changes for a running install when the release
that carries it starts, and what to do before and after.

## What the upgrade does by itself

Three migrations run on the server's first start, in this order:

| Migration | What it does | Data loss |
|---|---|---|
| `20261105220549_tools_and_keys` | Adds `tools`, `tool_checks`, `tool_calls`. Adds `credentials.tool_id`, `credentials.access_level` (default `read_write`), `credentials.expires_on`, `credential_grants.access_level` (default `read_write`). Guarded: an ALTER on a column that exists is skipped. | None (additive) |
| `20261105220550_tools_from_credentials` | One `tools` row per service API key a person stored (not model keys, computer keys, subscriptions, gateway keys or files), linked by `credentials.tool_id`. The address, auth scheme and check path come from `backend/tool_services.py` when the service is known. **Every migrated tool gets `delivery = 'computer'`**, so every bot keeps getting its keys exactly as before. Every grant stays `read_write`. | None |
| `20261105222012_one_send_policy` | Folds each bot's old "send without approval" switch into its layer of the one sending policy, then drops `bot_mail_settings` and `org_rule_misses`. | `bot_mail_settings.forward_to` and the `org_rule_misses` log are dropped (see Mail) |

At startup, `email_sending.import_registry` also reads `registry/mail-policy.yaml` **once**, if the file exists, and
carries its safeguards into the company sending policy. It records `email_sending.registry_imported` in
`registry_metadata` and never reads the file again.

Nothing here widens what any bot may do. Every rule below either keeps a right or narrows it.

## Mail: how the old settings map

| Before | After |
|---|---|
| `outbound_send: false`, bot layer On their own or unset | bot layer **Ask first** |
| `outbound_send: false`, bot layer Ask first or Never | unchanged |
| `outbound_send: true` | unchanged (the layer already said On their own, or the bot inherits Ask first) |
| the bot layer's `legacy` and `legacy_allowances` (the pre-cutover exception) | dropped. They only ever lifted asks, so dropping them means more sends ask first, never fewer. |
| `forward_to` | dropped. A forward is a send, decided and approved like any other. Saving a non-empty `forward_to` now returns 422. |
| registry `global.send_enabled` not `true` | company mode **Never** |
| registry blocklist and owner-handles-personally lists | company `blocked_recipients` and `owner_handles` |
| registry paused inbox | that inbox's mode **Never** |
| registry `max_sends_per_day`, `max_external_recipients` | company `daily_cap`, `max_outside_recipients`, applied only when lower than today's value |
| (new) outside Cc, writing check, second review of a bot's own email | on by default; a mailbox or bot layer can turn one on, never off |
| registry per-recipient cooldown, standing allowances | dropped. They only ever lifted asks or rate-limited them. |
| GitHub Issue approvals (`--approval-issue`) | gone. The only approvals are a Tico send approval card, or a person's Tico message after a verified preview. |
| a pending send whose approval predates the queue (text-hash approval, no `email_send_id`) | no longer approved: it asks again with a new card |

All of these show, and can be changed, on Tools › Settings › Email › Sending (company) and each mailbox's Limits card:
Never send to, Owner handles personally, Outside people per email, and the Always ask for checks.

**Who sends.** A Workspace mailbox (one the server can mint a Gmail token for) is now sent by the server itself on
its scheduler tick, after the undo window and pacing. A computer that tries to claim one of those sends gets 403
`workspace_owned`. A mailbox only a computer holds a grant for (a person's own Google) is still sent from that computer.

**Expect after the upgrade:** some bots that used to send on their own will ask first. They are the ones whose
old switch was off, or whose permission came only from the legacy exception. Each will show up under Needs you as a
send approval. Turn a bot back to On their own on its mailbox page (Tools › the mailbox › Limits) if that is what you
want.

## Tools and keys

- **Existing keys don't move.** Every migrated tool is `delivery = 'computer'`. Under Needs you it shows as "Key on
  computers", with a one-click **Move to proxy**. Moving one is safe once every computer running a bot that holds it
  is on this release (see Mixed versions).
- **New keys** for a known service (Close, Stripe, GitHub, ...) default to `proxy`. The key stays on the server, and
  the run gets a short-lived proxy token plus `TICO_TOOL_<ID>_URL`. A key for an unknown service (no address) stays
  `computer` until someone sets the tool's address.
- **Levels.** Every existing grant is Read and write. To make a bot read-only, set it to Read on the tool page. Add a
  read key if the vendor issues one; without one, a Read caller can make only GET, HEAD and OPTIONS calls through
  the proxy.
- **Checks.** Every 30 minutes, an authenticated GET runs for each proxied tool that has a check path. A 401 or 403
  is "Key rejected". A `computer` tool is checked only on Check now.
- **Retention.** `tool_calls` and `tool_checks` are trimmed after 90 days.

## Mixed versions (server new, a computer old)

- **Proxied tools:** an old runner ignores `proxy_url`. It puts the proxy token in the env var, and the bot calls the
  vendor directly with it and gets a 401. Only proxied tools are affected, which right after the upgrade means only
  keys added after the upgrade. Runners update to the server's desired release on their own, so the window is the
  update lag. Don't Move a tool to proxy while one of its bots runs on a computer that is still updating
  (Computers shows the version).
- **Mail:** an old computer's mail tool still queues through `POST /api/v2/email/sends`, which hasn't changed. Its
  local checks no longer exist on the new side but are now a subset of the server's. An old computer that tries to
  dispatch a Workspace mailbox's send gets `workspace_owned`, and the server sends that email instead.
- **CLI:** `hub connections list` is gone (`/api/v2/connections` is removed). Use `hub tool index` and
  `hub tool check <id>`. Bots on the new release get `hub tool call` and MCP `hub_tool_call`.

## Before the upgrade (Chris)

1. Read the branch diff and the screenshots.
2. Check that the install's backup is current (cell stats `backup.stale` is false).
3. On TIDY and Tico Team, make a note of bots that send on their own today (Settings › Inboxes › Sending), so you can
   tell an expected new "ask first" from a surprise.

## Deploy

1. Merge `chris/tools-redesign` into main as one merge commit, then cut the release the usual way.
2. Hosted installs: set the staff target release. Runners follow the server's desired release.
3. TIDY: Chris runs `tidy-update.sh` (Claude is blocked from TIDY installs).

## After the upgrade (check on each install)

- `SELECT version FROM schema_migrations WHERE version LIKE '20261105%'` returns all three.
- Tools in the sidebar: the list shows every former Connections and Inboxes row, and the old addresses redirect.
- `SELECT count(*) FROM credentials WHERE tool_id IS NULL AND kind IN (<service kinds>)` is 0. Every service key has a
  tool.
- `SELECT delivery, count(*) FROM tools GROUP BY 1`: all `computer` right after the upgrade.
- Send one test email from a Workspace mailbox. It's sent by the server, and the mailbox's Activity shows it.
- Move one low-risk tool to proxy, run a bot turn that calls it, then check the tool's Activity row and that the
  run's environment had no key.

## Rollback

- **Before anyone uses the new pages:** restore the pre-upgrade backup (Litestream point-in-time) and redeploy the
  previous release. The tools migrations are additive and harmless to the old code. `one_send_policy` drops
  `bot_mail_settings`, though, so the old release's mail settings code needs that table. A point-in-time restore is
  the clean way back.
- **After use:** fix forward. Moving a tool back to `computer` (`PATCH /api/v2/api-tools/<id>` with `{"delivery": "computer"}`; there is no button for it) restores today's key delivery
  for that tool. Setting a bot's mailbox mode back to On their own restores its sending.

## Not in this branch (follow-ups, by the plan's own order)

- Browsers on the Tico server, exiting through the account's home machine (needs the 2-week trial first).
- The website broker: one shared session per account, merged requests, a write queue.
- The Limits rule engine. The API tool's Limits card is a placeholder; the mailbox Limits card is the live sending
  policy.
