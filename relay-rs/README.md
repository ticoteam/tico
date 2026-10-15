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
never leave loopback. Direct remote clients cannot spoof trusted proxy identity headers. Existing
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

## Startup and rollback

Native release binaries are checksummed and checked against the checkout's source fingerprint.
Development checkouts build with Cargo; Docker copies the release binary into the standard server
image. There is no separate relay image or router required for normal installations. A relay crash
stops the API, and an API stop terminates its owned relay process; launchd, systemd or Docker restarts
the complete server. Screen tickets cannot survive a process restart.

Rollback uses the previous complete server release. There is no Python relay switch or fallback.
The existing durable modes and audit history remain in the same database.
