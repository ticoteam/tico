# The anonymous usage count

Tico's goal is 100 teams trying it and 100 still using it 30 days later. To know, it counts installs. This page is
the design and its research; [PRIVACY.md](../PRIVACY.md) is what an owner reads.

## What other projects do

The rule was to follow established open-source practice, not invent one. Every project below does the same few things,
and Tico does all of them.

| Project | Practice | Source |
|---|---|---|
| **Homebrew** | Anonymous aggregate analytics, on by default, opt out with `brew analytics off`. Shows a notice first, and does not send anything until it has been shown. The payload has no user identifier or IP field. Retention is 365 days. The aggregate numbers are public. | [docs.brew.sh/Analytics](https://docs.brew.sh/Analytics) |
| **Next.js** | Anonymous telemetry, opt out with `next telemetry disable` or `NEXT_TELEMETRY_DISABLED=1`. Prints a notice the first time. `NEXT_TELEMETRY_DEBUG=1` prints each payload to stderr and sends nothing. Says what it collects. | [nextjs.org/telemetry](https://nextjs.org/telemetry) |
| **Astro** | Anonymous, opt out with `astro telemetry disable` or `ASTRO_TELEMETRY_DISABLED=1`; a public page lists the data. | [astro.build/telemetry](https://astro.build/telemetry/) |
| **Gatsby** | Opt out with `gatsby telemetry --disable` or `GATSBY_TELEMETRY_DISABLED=1`; a public page lists the data. | [gatsbyjs.com/docs/telemetry](https://www.gatsbyjs.com/docs/telemetry/) |
| **VS Code** | One setting, `telemetry.telemetryLevel`, and `off` silences everything. Documents what is collected. | [code.visualstudio.com/docs/configure/telemetry](https://code.visualstudio.com/docs/configure/telemetry) |
| **`DO_NOT_TRACK`** | A convention for command-line tools: one environment variable that turns off telemetry and other non-essential phone-home. Adopted by Homebrew and Syncthing, among others. | [consoledonottrack.com](https://consoledonottrack.com/) |
| **Plausible** | Counts visitors without storing IP addresses or user agents: the address is hashed with a salt that is rotated and deleted every 24 hours, so nothing identifies a human and nothing is kept that could. | [plausible.io/data-policy](https://plausible.io/data-policy) |

The common practice: **opt-out and anonymous; a notice on first run; several ways to switch it off, including an
environment variable and a standard one; a debug mode that shows the payload; a public statement of the fields; public or
aggregate results; no IP address kept.** Tico follows each. Where it differs it is stricter: a random ID rather than a
computer hash, five fields, three of them booleans, and the count is invisible to a team that turns it off (the update
check goes straight to GitHub).

## How it works

```
Tico server ── GET /v1/latest?install_id&version&active_people&active_bots&first_result ──> HQ ──> installs table (8 columns)
     │                                                                      └──> GitHub release, cached 15 minutes
     └── counting off, HQ down, or TICO_RELEASES_URL set ──> GitHub, no ID
```

- **Client:** `backend/census.py` decides whether anything may be sent and builds the payload; `backend/releases.py`
  makes the request. HQ answers with the same body GitHub does, so the update notice works the same either way.
  `TICO_HQ_URL` moves the base URL (default `https://updates.tico.team`); `TICO_RELEASES_URL` still replaces the whole check
  with a GitHub-shaped mirror and sends nothing extra.
- **`active_people`** is set from the last time a human (not an API token, not the Assistant acting for someone) made a
  request; it is written at most once an hour. **`active_bots`** is a query over finished bot runs.
- **The ID** is `uuid4()`, stored in the `usage-count` row of `registry_metadata`. It is generated on first use, not derived
  from `TICO_ENVIRONMENT_ID` or anything else, and rotates on request.
- **A note on flags:** they are yes/no over 7 days rather than counts, so HQ cannot learn how large a team is.
- **Demo mode** never sends.

## HQ, the collector

`hq/` is a separate service: its own FastAPI app (`hq/app.py`), SQLite database (`hq/db.py`), image (`hq/Dockerfile`) and
compose file (`hq/compose.yaml`). It is not in the Tico server image and refuses to start without `TICO_HQ_KEY` (16 or
more characters), so it cannot be turned on by accident. The key is an enable switch: it is not sent by installs and does
not gate the public endpoints.

**Storage** (`installs`): `install_id`, `first_seen`, `last_seen`, `last_version`, `last_people`, `last_bots`. Dates are UTC
days. Nothing else is stored for the count, and rows older than 13 months (by `last_seen`) are deleted daily. Support tickets, which
a human sends on purpose, are in two other tables (`tickets`, `ticket_replies`; `hq/support.py`) and are kept until deleted: see
[Support tickets](#support-tickets).

**Endpoints**

- `GET /v1/latest?install_id=&version=&active_people=&active_bots=&first_result=` records the ping and returns the latest
  release, cached from GitHub for 15 minutes. Any of the four required fields present but invalid, missing or repeated, or
  `first_result` invalid or repeated: `422`, nothing stored. `first_result` may be left out (a release from before it). No fields: the release only, nothing recorded. Over 60 requests an hour from one address: `429`. The answer also carries Tico's per-version messages and known issues, the same for every install
  ([tico-hq.md](tico-hq.md#messages-known-issues-and-fixed-in-notices)); nothing more is sent or stored for them.
- `GET /v1/stats`, public: `tried` (IDs whose two flags have both been true at some time), `active_7d` (heard from in the
  last 7 days), `retained_30d` (first seen at least 30 days ago and a bot active in the last 7 days), `first_result` (IDs that
  have reached a first bot result), `first_result_median_days` (the median days from first seen to first result, over IDs
  that sent the flag from their first request; `null` under 5 such IDs) and `by_version`
  (active installs by major version; before 1.0 by `0.minor`). The totals are exact. A version count under 5 is folded into
  `other`, and `other` is left out if it is still under 5.
- `GET /healthz`.

**No addresses.** The rate limiter keeps a salted hash of the address in memory, with a random salt made at start, and
forgets it. The server runs with `access_log=False`, so no address or query string is logged. Behind a proxy set
`HQ_CLIENT_IP_HEADER` (`X-Forwarded-For` behind Caddy, `CF-Connecting-IP` behind Cloudflare) so the limit is per client;
the proxy in front must not log query strings either (Caddy does not by default). Do not put Cloudflare Access in front:
installs call HQ without signing in.

### Support tickets

`hq/support.py`. A human files a ticket from the app (Help > Contact support; [support.md](support.md)); the team works it with a
staff key. Bodies are JSON, at most 16 KB (48 KB for staff), with strict fields; a refusal names the field, never echoes a value
(`422 {"error": "invalid", "field": "message"}`). Nothing here logs a request, a body or an address.

| Route | Auth | |
|---|---|---|
| `POST /v1/support` | none | `{message, email?, install_id?, version?, diagnostics?}` (no other field; `diagnostics` is a JSON object with `format: 1`, up to 256 KB, and makes the request limit larger than a message's 16 KB). `201 {ticket_id, secret, status}`. The secret is shown once and HQ keeps its SHA-256. 5 an hour per address, 10 a day per install ID, 1000 a day in all: `429`. |
| `POST /v1/support` with `kind: "setup-website"` | none; `Origin: https://tico.team` only | Talk to a person on tico.team/setup. `{kind, email, step: "company"\|"chart"\|"goals"\|"team"\|"rules"\|"file", mode: "browser"\|"server", turns: 0..1000, session_id: 16-64 of [A-Za-z0-9_-], note?: up to 1000 characters, plan?: tico.company.yaml text with a top-level `version: 0.2`, up to 32 KB}`; anything else is `422 {"error": "invalid"}` with no field named, another origin `403`. The ticket's body is fixed text (the email, step, mode and turns); the note and plan are its `attachment`, labelled Untrusted public input, with key-shaped strings removed (`hq/company_file/keys.py`, the draft engine's scrubber) before the judge or the database see them. `200 {ticket_id, reply_url, keys_removed}`: `reply_url` is `https://<HQ_DOMAIN>/support/{id}#s=<secret>` (`https://updates.tico.team` when `HQ_DOMAIN` is unset). One per `session_id`, 3 a day per address and per email address, `HQ_SETUP_TICKETS_DAILY_CAP` a day in all (default 50; 0 turns the kind off): `429 {"busy": true}`. The route answers the browser's preflight and carries `Access-Control-Allow-Origin: https://tico.team` for that origin only (`hq/cors.py`). |
| `GET /v1/support/{id}` | ticket secret | Header `X-Ticket-Secret` (or `?secret=`). `{ticket_id, status, created, updated, messages: [{id, created, from: "staff"\|"person", body}]}`. An unknown ticket and a wrong secret are the same `404`. 600 an hour per address. |
| `POST /v1/support/{id}/messages` | ticket secret | `{message}` from the human; reopens an answered ticket; `409` when closed or at 60 messages. |
| `DELETE /v1/support/{id}` | ticket secret | The human deletes their ticket. |
| `GET /v1/staff/stats` | staff key | `{by_version: {"0.2.24": 2, ...}, window_days: 7}`: installs heard from in the last 7 days on each exact release, with no suppression (the public `/v1/stats` still leaves out counts under 5). |
| `GET /v1/staff/tickets?status=open\|answered\|closed\|all&since=<UTC time>&limit=` | staff key | Oldest activity first. `since` is `YYYY-MM-DDTHH:MM:SSZ` and matches `updated`, which moves when the human writes or the team replies. Each ticket has `body`, `email`, `version`, `install_id`, `email_pending`, `signature` (a Health Monitor `hm-...`, or empty), `has_diagnostics`, `kind` and `messages`, and on a
setup-website ticket `attachment: {label, parts}` (`show` adds the `note` and `plan` text). `&signature=hm-...` lists only tickets with one signature, so the team works them together. |
| `GET /v1/staff/tickets/{id}` | staff key | One ticket, with its `diagnostics` bundle when the human attached one (support.md, PRIVACY.md). No other route returns it. |
| `POST /v1/staff/tickets/{id}/reply` | staff key | `{body}` (up to 8000 characters). Sets the ticket to `answered`; with an email on the ticket it is marked `email_pending`. HQ sends no email. `409` when closed. |
| `POST /v1/staff/tickets/{id}/status` | staff key | `{status: "open"\|"answered"\|"closed", email_sent?: true}`. |
| `POST /v1/staff/tickets/by-signature/close` | staff key | `{signature, body}`: when a release ships a Health Monitor fix, the same reply on every open or answered ticket with that signature, then each closed. A ticket with an email keeps `email_pending` (HQ sends no mail; the Support bot drafts it). Running it again closes nothing new. |
| `DELETE /v1/staff/tickets/{id}` | staff key | Delete on request. The rows are overwritten in the file. |

The staff key is `HQ_STAFF_KEY` (24+ characters; unset turns every staff route into `404`), sent as `Authorization: Bearer ...` and
compared in constant time. 20 wrong keys an hour from an address are answered `429`. Ticket ids look like `TK-AB12CD34`. Statuses
are `open`, `answered` and `closed`. Tickets are never deleted by HQ itself.

### Deploy HQ

On the server (next to an existing Tico is fine: HQ has its own compose project, volume and hostname). This is the Tico
team's `updates.tico.team`; anyone can run their own the same way.

```
git clone https://github.com/ticoteam/tico && cd tico
cp hq/.env.example hq/.env
$EDITOR hq/.env          # TICO_HQ_KEY=$(openssl rand -hex 24); HQ_DOMAIN; pick a front door; optional backup;
                         # HQ_STAFF_KEY=$(openssl rand -hex 24) if you will work support tickets
docker compose -f hq/compose.yaml --env-file hq/.env up -d --build
curl -fsS http://127.0.0.1:8770/healthz
```

Front door, one of (set in `hq/.env`):

- `COMPOSE_PROFILES=cloudflared`, `HQ_TUNNEL_TOKEN=...`, `HQ_DOMAIN=updates.tico.team`, `HQ_CLIENT_IP_HEADER=CF-Connecting-IP`:
  an HQ-only Cloudflare tunnel, and the right choice on a server whose Tico already holds ports 80 and 443. Do not add an
  Access application. HQ writes the tunnel's route at every start (`hq/tunnel.py`, into the `hq-tunnel` volume) and
  cloudflared runs with it (`--config /tunnel/cloudflared.yml`): `HQ_DOMAIN` to `http://hq:8770`, anything else a 404.
  So a tunnel with no route of its own (a locally managed one, or a token reused from elsewhere) serves HQ instead of
  logging "No ingress rules" and answering 503. A Public Hostname set in the tunnel's dashboard still wins: cloudflared
  prefers the configuration Cloudflare holds, so with one there, route `updates.tico.team` to `http://hq:8770` there too. The
  file is world-readable (0644) because cloudflared runs as its own non-root user; it holds no secret, the token stays
  in cloudflared's environment. A changed `HQ_DOMAIN` needs `up -d` for both services; with `HQ_DOMAIN` empty the route
  answers only 404s, and a value that is not a plain hostname stops HQ with the reason.
- `COMPOSE_PROFILES=caddy`, `HQ_DOMAIN=updates.tico.team`, `HQ_CLIENT_IP_HEADER=X-Forwarded-For`: HTTPS on ports 80 and 443 of a
  host of its own.

Then check `curl -fsS https://updates.tico.team/v1/stats` and `curl -fsS https://updates.tico.team/v1/latest`. Through the
tunnel, a 404 means `HQ_DOMAIN` is empty or names another host, and a 503 means cloudflared runs without the route:
`docker compose -f hq/compose.yaml logs cloudflared` says "No ingress rules" (it was started without `--config`) or
"permission denied" (the file it reads is not world-readable).

**Backups** are optional: add `,backup` to `COMPOSE_PROFILES` and set `HQ_BACKUP_URL` (and `HQ_BACKUP_ENDPOINT`,
`LITESTREAM_ACCESS_KEY_ID`, `LITESTREAM_SECRET_ACCESS_KEY` for R2 or MinIO). Litestream copies `hq.db` continuously;
the backup service runs as the HQ image's user (`10005:10005`) so it can read `hq.db`; as root it fails with "stat /data/hq.db: permission denied". Restoring is the command at the top of `hq/litestream.yml`. Update HQ with `git pull` and the same `up -d --build`.

## Testing

`backend/tests/test_usage_count.py` (the payload has exactly the five fields; nothing is sent when off by
`TICO_TELEMETRY`, `DO_NOT_TRACK`, the toggle, demo mode or before the notice; HQ down falls back to GitHub; debug sends
nothing; owner-only controls), `hq/tests/test_hq.py` (no address stored or logged; bad input refused; the stats math,
suppression and retention; no start without the key), `hq/tests/test_support.py` (tickets: strict input, per-ticket secrets, staff
routes need the key, replies reach the install, nothing logged, nothing deleted by itself), `hq/tests/test_support_bot.py` (the Support
Agent's `hq-tickets` against the real routes) and `hq/tests/test_tunnel.py` (the tunnel's route names `HQ_DOMAIN`,
ends in a 404, is world-readable, and refuses a domain that is not a plain hostname). They add about ten seconds to the
suite.
