# Service keys

A service key lets another system, such as your product's backend, put work in front of a person or a bot in Tico,
change it, and take it away when the work is done over there. An [update key](#update-keys) instead lets a release bot
on another install update this one. A weekly report is ready, so its account manager gets a
task to review it; the report is sent, so the task closes. The key files, updates and closes tasks and does nothing
else. Do not put a personal API token in a product backend: it carries everything its person may do.

## Make one

The owner and the admins make, list and revoke keys:

```sh
hub service-key create --label "Billing backend"
hub service-key list
hub service-key revoke <id>
```

`create` answers with the key (`tico_sk_...`) this once; Tico keeps only a hash. Put it in the other system's secret
store. The label names that system on every task it files. `list` shows every key, when it was last used and whether it
is revoked, never the secret. The same routes are `POST /api/v2/service-keys` `{"label": "..."}`,
`GET /api/v2/service-keys` and `POST /api/v2/service-keys/{id}/revoke`, from a signed-in session or a personal API
token, with an `Idempotency-Key` like other writes. Retrying a create acknowledges the same key id without
returning its secret again; the retry cache stores only metadata. There is no Settings page for them yet.

## The one route

`POST /api/v2/inbound/tasks` with `Authorization: Bearer tico_sk_...` and a JSON body:

| Field | |
|---|---|
| `key` | Your own id for this piece of work, 1 to 200 characters. Required. The pair (this service key, `key`) is one task. |
| `owner` | `human:<id>`, `bot:<slug>`, or the email of a person on the roster. |
| `title`, `body` | The task's title and description. |
| `type`, `step` | A task type and one of its steps, by id or name ([Tasks](tasks.md)). |
| `labels` | The task's labels; they replace the ones it has. |
| `links` | Links to attach, such as the report's page. Added, never removed. |
| `due` | ISO 8601 with a timezone. |
| `close` | `true` when the work is done over there. |
| `note` | What a close says. |

Each call says what the work looks like now, and the route makes the task match it. It is idempotent and
order-independent: no call depends on an earlier one, the same call twice changes nothing the second time, and the last
call wins. No `Idempotency-Key` header is needed.

- The first call for a `key` files the task. It needs `owner`, `title` and `body`.
- A later call changes what differs. A field you leave out stays as it is. The title is set when the task is filed;
  later calls leave it alone.
- `"close": true` closes the task, with `note`. On a closed task it does nothing. For a `key` that never had a task it
  files nothing.
- A call without `close` on a closed task reopens it, at its type's first open step or the `step` you send, and applies
  the rest.

The answer is `{"task": {...}, "created": true|false, "changed": true|false}`, with only `{"id": "..."}` acknowledging the task, or
`"task": null` for a close that filed nothing. A new task gets the checks any new task gets: one for a person must read
as a request (a title that starts with a verb, the ask first, under 120 words; see [How Tico works](how-it-works.md)),
and a bot must be active. A refusal is `422` with a code naming what to fix: `owner`, `type`, `step`, `date`,
`validation` or `lint`. An unknown or revoked key is `401`.

```sh
curl -sS https://tico.acme.example/api/v2/inbound/tasks \
  -H "Authorization: Bearer $TICO_SERVICE_KEY" -H "Content-Type: application/json" \
  -d '{"key": "weekly-report-1042", "owner": "sam@acme.example",
       "title": "Review the weekly report for Acme",
       "body": "The report for the week of 28 September is ready.",
       "links": ["https://app.acme.example/reports/1042"]}'

curl -sS https://tico.acme.example/api/v2/inbound/tasks \
  -H "Authorization: Bearer $TICO_SERVICE_KEY" -H "Content-Type: application/json" \
  -d '{"key": "weekly-report-1042", "close": true, "note": "Sent to Acme."}'
```

## Update keys

An update key lets a release bot on another Tico install update this one, which is how one team's Release Manager rolls
a release out to several installs ([Releasing](releasing.md#the-release-managers-rollout)). Only the owner makes one,
because only the owner updates the install:

```sh
hub service-key create --label "Release Manager" --scope update
```

(`POST /api/v2/service-keys` with `{"label": "...", "scope": "update"}`.) It reaches these routes, the same ones the
owner's **Check for updates** and **Update now** use, and nothing else:

| Route | |
|---|---|
| `POST /api/v2/system/update/check` | Look for a new release now (at most once a minute); answers `current`, `latest`, `available` |
| `POST /api/v2/system/update` `{"version": "X.Y.Z"}` | Start the update to that release through the install's updater |
| `GET /api/v2/system/update` | How the update stands (`state`, `from`, `to`, `message`), the release `running`, and `computers`: online computers counted by state (`current`, `updating`, `needs_update`, `incompatible`, `unknown`) |

`GET /healthz` needs no key; its `release` is the release the server runs. Every other route answers `403`, the task
routes included, and a tasks key answers `403` on these. The update is recorded as started by the key
(`system.update.started` with actor `service:<id>`). The updater checks, snapshots and rolls back exactly as for the
owner's click ([Updates](updates.md#the-servers-own-updater)). `hub service-key list` shows each key's `scope`.

## What a person sees

The task arrives like any other, added by Tico, which files it as it files a routine's tasks. Its description ends
with a line naming the key: *Filed by Billing backend (service key).* The task's history shows each change the key made
as made by Tico.

## Security

- A tasks key works on `POST /api/v2/inbound/tasks` only, an update key on the three update routes only. Anywhere else that needs a sign-in it is refused with `403`, the
  SQL page, MCP and the service key routes included. It returns only a task id and write flags; task contents, comments, links and attachments are never returned.
- A task marked private is refused with `403` before any update, reopen or close, even when this key filed it.
- Tico keeps a hash of the key, never the key; SQL cannot read either.
- Revoking a key stops it at once. The tasks it filed stay. A new key has its own `key`s: work filed with the old key is
  not found with the new one, so close those tasks first, or let people close them.
- Tasks are filed by Tico itself, so a key does not carry its maker's access: it can give work to any active bot and any
  person on the roster. Keep it as you would any credential with that reach.
- The audit log has every key made (`service_key.create`), every call (`service_key.use`, with the `key` and the task)
  and every revocation (`service_key.revoke`). A call the task rules refuse (a request's wording, an inactive bot) is
  recorded as a refusal under the key.
- The owner's and admins' personal API tokens can make keys, so `hub` can. A leaked token can make a key that outlives
  the token: when you revoke a token, check `hub service-key list` too.
