# Runner wire contract

What a runner and its server send each other on the core turn path: heartbeat, claim, start-place, started, events,
renew, complete and the `GET runners/me/events` stream. Everything else a runner calls is not contracted yet.

- `contracts/runner/wire.json`: the endpoints, every answer, the error envelope and the stream frames (JSON Schema).
- `contracts/runner/requests.json`: the request bodies, generated from the server's models in `backend/models.py`.
- `contracts/runner/fixtures/`: real exchanges recorded by `backend/tests/test_runner_wire_contract.py`. The Python
  test and the Rust runner (`runner-rs/src/wire.rs`) both read them.
- `contracts/runner/releases/<version>/`: the contract and fixtures as each kept release shipped them.

## Supported skew

A runner and a server work together when each is on the current or the previous minor release, in either direction:
a 0.5 runner with a 0.6 server, and a 0.6 runner with a 0.5 server. `scripts/check_wire_contract.py` checks the
current contract against each kept release copy in both directions and fails on any break.

## Compatibility rules

Answers are read leniently: a runner ignores fields it does not know. Requests are read strictly: the server's models
forbid unknown fields (a 422 `validation` error naming the field).

- **New answer field**: add it as optional. A new required field breaks the old server for a new runner.
- **Removing or retyping an answer field**: not inside the window. Stop reading it first, and drop it two minors later.
- **Nullability**: making a field nullable is a retype. Readers that do not expect `null` break.
- **New enum value in an answer**: only for a string marked `x-open-enum`, whose readers handle values they do not know
  (an unknown event-stream kind makes the runner read everything again). Any other enum is closed.
- **New request field, value or endpoint**: the runner sends it only once the server says it takes it, either through
  a heartbeat answer flag (as with `start_places`) or by dropping it when a 422 names it (`runner-rs/src/lanes.rs`
  `refused_field`). List it under `gated` in `wire.json`. The check then allows it in the new-runner, old-server
  direction. A new `Event.kind` counts as a new value: the server refuses the whole batch.
- **New required request field**: not inside the window. Add it as optional with a default.
- **Errors**: every refusal is `{"error": {"code", "detail", "retryable"}}`. `code` is an open set. A runner acts on
  the codes it knows (`stale_lease`, `start_wait`, `already_started`, `event_gap`, ...) and treats others by HTTP
  status. It retries 429 and 5xx with the same idempotency key (`runner-rs/src/client.rs`).
- An object with `additionalProperties: true` is not contracted below that level, for example the attempt's `message`
  or `config`. Readers keep it as opaque JSON.

The Rust runner reads each answer into a struct with only the fields it acts on. A required contract field is a plain
field. A field the runner has always done without, such as a heartbeat feature flag or renew's 60-second default, stays
an `Option` with that fallback. An answer it cannot read at all is a `protocol` error.

## A runner older than the server takes

`MIN_RUNNER_RELEASE` in `backend/runner_versions.py` is the oldest runner release the server gives work to. A runner
below it is paused: its claims answer `{"attempt": null, "paused": "<why>"}`, it logs the reason, and Computers shows it
as incompatible. It is not refused outright. A runner that does not report a release (a development checkout) is never
paused. Today the floor is 0.1.0, wider than the window. Raise it to the previous minor in any release that drops
something older runners still need.

## Changing the contract

1. Change the server models or handlers, then run `python3 scripts/check_wire_contract.py --write-requests`.
2. Edit `wire.json` for answer changes and bump its `version`: the minor version for an additive change, the major
   version for a break, which also needs `MIN_RUNNER_RELEASE` raised.
3. Re-record the fixtures:
   `TICO_WRITE_WIRE_FIXTURES=1 python3 -m pytest -q backend/tests/test_runner_wire_contract.py`.
4. Run `python3 scripts/check_wire_contract.py` and `cargo test --locked wire::` in `runner-rs`.

At each release cut, keep that release's copy with `python3 scripts/check_wire_contract.py --snapshot X.Y.Z`. This
also removes copies older than the previous minor.
