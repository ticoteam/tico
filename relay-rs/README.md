# Computer screen relay

Rust is the only screen relay. The standard server image includes this binary; `python -m backend.server`
starts it, gives it the public listener and supervises both processes. Ordinary HTTP and streaming
responses go through Rust to a private loopback Python API. Rust handles the two existing screen
WebSocket paths directly, while Python retains authentication, permissions, durable modes and audit
writes. No persistent session table or engine selection exists.

- `/api/v2/runners/{rid}/screen/ws?ticket=...` (browser)
- `/api/v2/runners/me/screen/{session_id}/ws` (runner)

The parent creates a fresh private key and allocates loopback API/control ports on every start.
Public ingress blocks `/control/*` and `/_tico/*`; policy callbacks and versioned control requests
never leave loopback. `TICO_TRUSTED_PROXIES` selects the ingress IPs/CIDRs whose forwarding headers
the relay preserves; the binary's default is loopback only, and the standard Compose file adds its two
front doors' fixed addresses. IPv4-mapped IPv6 peers are compared as IPv4. Other peers get a fresh
X-Forwarded-For chain. `TICO_CLIENT_IP_HEADER` (`x-forwarded-for` or `cf-connecting-ip`) selects the header that
names the client behind a trusted proxy, read as the server reads it.
Direct remote clients cannot spoof trusted proxy identity headers. Existing
TLS termination, cookies, Cloudflare origins and public hostnames remain unchanged. See the
installation matrix and native job migration in `docs/install.md`.

## Version 1 control protocol

All JSON responses identify the Rust `boot` UUID. Commands carry a monotonic sequence number; stale sequences fail and the most recent exact retry is
idempotent, so delayed grant/control requests cannot resurrect closed sessions. All commands require the shared secret in
`Authorization: Bearer ...`. Commands are serialized in Python and applied atomically in Rust.

1. `GET /control/v1/hello` returns a one-use `challenge` and boot ID. `POST /control/v1/start`
   consumes the challenge with a fresh Python `lease` UUID, closes every previous session, clears
   mode generations and returns the boot ID. A delayed start with a used challenge cannot take over
   a newer lease. Repeating the currently active start is harmless.
2. `POST /control/v1/command` carries `lease` and a tagged `op`. Every operation refuses an expired
   or superseded lease. `heartbeat` renews a six-second monotonic lease, acknowledges durably
   recorded close IDs and returns at most 256 unacknowledged reports. Python renews every two
   seconds. Lease loss closes all sockets; a new lease cannot revive old tickets or sessions.
3. `mode` carries `runner_id`, a monotonically increasing `generation`, `mode` and `reason`.
   Stale generations are refused. Disallowed sessions are removed atomically and cancellation
   reaches sockets within the bounded close deadline. Allowed view sessions survive an upgrade.
4. `grant` carries a Python-generated session `id`, runner/actor/control, current generation and
   mode. Rust creates the temporary ticket. Matching retries return the same unused ticket;
   conflicting retries fail. Grants are prepared and invisible until `activate` follows Python's
   audit transaction commit. Failed commits use `end`. Mode changes fence stale activation.
5. `pending` lists only active, unanswered sessions for that runner. `end` is duplicate-safe.
   A ticket is consumed atomically once, even if subsequent pairing fails. Runner claims are
   atomic and require Python to validate the original bearer against the exact existing path.
   Origin permission is also checked by Python, keeping local aliases/demo/CORS semantics intact.
6. Close reports contain session ID, actor, runner, control, seconds and close code. Python inserts
   at most one close event per open session, under its SQLite write lock, then acknowledges it.
   Reports are retried until acknowledged. Audit events carrying the process lease allow Python to
   reconcile unclosed opens after either process restarts; they do not confer session authority.

Bounds: three sessions per computer (unchanged); 1 MiB pre-browser backlog (unchanged), one-minute
unused ticket, 30-second answer and one-hour session deadlines (unchanged). The relay additionally
caps global live sessions at 1,024, retained close reports at 8,192, individual messages at 16 MiB (matching uvicorn),
and stalled writes at five seconds. Saturation refuses admission instead of growing memory.
Cancellation interrupts stalled pumps; close handshakes have a one-second deadline per socket.
A failed revocation RPC stops lease renewal, so durable revocation still closes sockets within the
six-second lease plus close deadline. HTTP control calls have two-second deadlines.

## Browser exits

The same relay carries browser-exit tunnels (`src/exit.rs`, `docs/computers.md`): a bot's browser
reaching the web from another Tico computer whose owner shares its internet with that bot. Python
decides who may use which exit; Rust moves opaque bytes (the browser's own TLS) and counts them.

- `/api/v2/runners/me/browser-exit/ws` (exit computer's link, runner bearer; Python's policy callback
  answers `{"kind": "exit_link"}` with the runner id). One link per exit; a newer one replaces it.
- `/api/v2/runners/me/browser-exit/seen` (GET, same bearer and check): `{"seen": ip}`, the address the request
  came from. The exit asks over IPv4 and IPv6 and refuses each answer as its own address.
- `/api/v2/runners/me/browser-exit/tunnels/{id}/ws?ticket=...` (bot computer's end)
- `/api/v2/runners/me/browser-exit/tunnels/{id}/exit?ticket=...` (exit computer's end)

Control operations, under the same lease and sequence rules:

- `exit_policy` carries `runner_id` and `policy` (`generation`, `sharing`, `bots`, `cap` in bytes per
  UTC day). Older generations are refused. Tunnels of bots no longer allowed close (`exit_not_shared`),
  and turning sharing off drops the link.
- `exit_grant` carries the tunnel `id`, the bot's `runner_id`, the `exit` runner id, `bot`, `host`,
  `port` (80 or 443), the UTC `day`, the bytes Python has recorded for that exit today (`used`) and
  the policy as Python read it (a stale generation fails `exit_stale`). Refusals: `exit_not_shared`
  (403), `exit_offline` (409, no link), `exit_over_cap` (429), `exit_busy` (429). An admitted grant
  sends `{"op": "open", "id", "host", "port", "ticket"}` down the exit's link and returns the bot
  computer's ticket. Both tickets are one-use.
- `exit_close` closes the tunnels of a `bot` and/or an `exit`, with a `reason`.
- `heartbeat` also takes `exit_ack` and returns `exit_usage`, one batch (`id`, `seq`, `rows` of `exit`,
  `runner_id`, `bot`, `host`, `day`, `bytes`, `tunnels`; at most 2,048 rows) resent until its `id` is
  acknowledged, and `exit_backlog`, the rows not yet acknowledged. `seq` grows by one per batch within
  a relay `boot`: Python records a batch only above the highest `seq` it recorded for that boot.
- `stop` closes every socket (counting the open tunnels' last bytes) and returns `exit_backlog`. While
  that is above zero the lease stays for heartbeats only, and ends with the heartbeat that finds
  nothing left; anything left when the lease lapses waits for the next one.

Python's heartbeat (every 2 s) keeps asking within the same tick while `exit_backlog` is more than the
batch in hand, up to 16 batches, so no backlog builds up. Usage lives only in the relay's memory: a relay
stopped on its own (SIGTERM) waits up to 6 s for Python to collect it, and loses at most about one tick's
bytes otherwise.

Both ends must connect within 15 seconds or the tunnel closes (`exit_no_answer` when the exit did
not). The exit's first message may be a short text status (`{"ok": true}` or `{"ok": false,
"error"}`); everything else is binary and counted both ways. Crossing the day's cap closes every
tunnel of that exit (4429 `exit_over_cap`), so the cap can be overshot only by bytes in flight. The
per-exit day count survives a Python restart and starts from Python's recorded bytes after a relay
restart. Bounds: 4,096 live tunnels, 256 per exit, 16,384 retained reports, 1 MiB messages, a
one-hour tunnel deadline and 60-second stalled writes.

## Startup and rollback

Native release binaries are checksummed and checked against the checkout's source fingerprint.
Development checkouts build with Cargo; Docker copies the release binary into the standard server
image. There is no separate relay image or router required for normal installations. A relay crash
stops the API, and an API stop terminates its owned relay process; launchd, systemd or Docker restarts
the complete server. Screen tickets cannot survive a process restart.

Rollback uses the previous complete server release. There is no Python relay switch or fallback.
The existing durable modes and audit history remain in the same database.
