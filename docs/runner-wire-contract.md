# Runner wire contract

What a runner and its server send each other on the core turn path: heartbeat, claim, start-place, started, events,
renew, complete and the `GET runners/me/events` stream. Everything else a runner calls is not contracted yet.

- `contracts/runner/wire.json`: the endpoints, every answer, the error envelope and the stream frames (JSON Schema).
- `contracts/runner/requests.json`: the request bodies, generated from the server's models in `backend/models.py`.
- `contracts/runner/fixtures/`: real exchanges recorded by `backend/tests/test_runner_wire_contract.py`. The Python
  test and the Rust runner (`runner-rs/src/wire.rs`) both read them.
- `contracts/runner/releases/<version>/`: the contract and fixtures as each kept release shipped them.

A runner reports `harness_allocated: true` on `start_place` and `started` only after a process actually launches for
that turn, including a live process acquired from a pool. Host and thread preparation alone do not count. The hub
retains an allocated turn's admission under memory pressure. The `harness_allocation_v1` heartbeat capability
opts into memory rechecks on unallocated grants; without it, the hub preserves existing grants under memory
pressure because the runner cannot report whether it already has a process. A 422 naming the field makes the
runner retry the request without it for an older hub; a refused capability is dropped by `lanes::refused_field`.
The allocation signal remains true through fallback within the same turn. Start acknowledgement is tracked
separately: after `/started` succeeds, an unallocated fallback checks memory only locally and keeps its running
place, without calling `/start-place` or acknowledging the same start again.

## Supported skew

A runner and a server work together when each is on the current or the previous minor release, in either direction:
a 0.5 runner with a 0.6 server, and a 0.6 runner with a 0.5 server. The server's floor (`MIN_RUNNER_RELEASE`,
0.6.0-rc.1) pauses any runner below it, so 0.5 runners are paused, not served, and the supported window starts at
0.6.0-rc.1. `scripts/check_wire_contract.py` checks the current contract against each kept release copy in both
directions and fails on any break.

## Compatibility rules

Answers are read leniently: a runner ignores fields it does not know. Requests are read strictly: the server's models
forbid unknown fields (a 422 `validation` error naming the field).

Claim attempts may include `task_context`: up to ten pages and ten meetings as ids, titles, versions and dates.
It contains no source content. An older server omits it; the runner then renders no context block. The bot pulls
content with its normal read commands and its own permissions, the same during task work as in any other turn.
Claim retries rebuild the reference list using current access ([Pages](pages.md#publishing)).
Every saved runner receipt for a Make-page attempt uses the person stored on that attempt, including after it
finishes. Replays require that person's current task access and the same computer assignment and generation;
they do not require a live execution lease.

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
paused. The floor is 0.6.0-rc.1, the first release candidate of 0.6, the oldest version Tico supports. Raise it with
the window at each new minor release.

## Changing the contract

1. Change the server models or handlers, then run `python3 scripts/check_wire_contract.py --write-requests`.
2. Edit `wire.json` for answer changes and bump its `version`: the minor version for an additive change, the major
   version for a break, which also needs `MIN_RUNNER_RELEASE` raised.
3. Re-record the fixtures:
   `TICO_WRITE_WIRE_FIXTURES=1 python3 -m pytest -q backend/tests/test_runner_wire_contract.py`.
4. Run `python3 scripts/check_wire_contract.py` and `cargo test --locked wire::` in `runner-rs`.

At each release cut, keep that release's copy with `python3 scripts/check_wire_contract.py --snapshot X.Y.Z`. This
also removes copies older than the previous minor.

Make-page requests are claimed only by runners advertising `make_page`. The optional `attempt.make_page.task`
names the target task; `task_context` remains references only. The runner uses a fresh session and disposable scratch,
skips automatic file publication, and discards scratch after the turn. Later chat inputs and queued work stay queued.
Older servers negotiate the flag away through the heartbeat 422 fallback.


A runner advertises `voice_sessions_v1` to take a call's resident model job. A harness can require an additional
runner capability in `voice_session_runner`. Codex requires `voice_session_resume_v1` for resident sessions and
keeps a warm thread across a computer restart. An interrupted question is settled without replay.
The claim's
`message.refs.live_meeting.voice_call` names that call. The same attempt's `inputs` reply has a `call_voice` field
with `closed` and, when a question is ready, its turn ID, text and notes. The runner sends that turn's ID as
`voice_turn` on its delta and final message events. The lease and the attachment must still belong to that attempt
when the server accepts output. The optional `voice=busy` query parameter checks the current utterance without
claiming another. `voice=restart` returns a fresh primer and notes stamp with a bounded transcript recap.
After a call closes, the runner can acknowledge discarded output and settle the stopped lease with usage counts.
Older hubs negotiate the capability away through the heartbeat 422 fallback.
With `voice_inputs_v1`, the runner stream's `voice_inputs` hint carries only `attempt_id` and `call_id`, and goes only to the computer still holding that attempt. It is sent for a new turn and when the active turn is skipped (its deadline passed). The runner fetches inputs on hints and reconnects, with a 3-second backup poll; while the server refuses the capability, or the stream is down or quiet past 25 seconds, it keeps the 400 ms poll.

## Resident sessions

A runner advertises `resident_threads_v1` to keep a session's provider process between its turns, only where
`GET runners/desired` lists `features.resident_threads_v1`. Each turn is still an ordinary claim, start-place, started,
events, renew and complete; only the process is reused. With this capability the runner's longest capability list is
32 values, the cap of the current hub and of the 0.6.0 hub, so a further capability needs that cap raised first and
must wait out the window (a 0.6.0 hub stays at 32). The routes are outside the core contract, take the runner's
credential, and answer 409 `resident_threads_off` to a runner that has not advertised the capability:

- `POST attempts/{aid}/resident` `{provider_session_id, binding?, session_key?, replaces?}` registers the process of
  this ordinary, authorized turn. The server keeps the session key the turn's privacy scope admits: a key outside it is
  409 `session_key_mismatch`. One live process per bot, key and login binding: another is 409 `resident_taken`, unless
  `replaces` names the incarnation this runner lost.
- `POST residents/{rid}/renew` `{state: live|dormant, provider_session_id?}` about every `renew_seconds` (60); a
  resident unheard from for 5 minutes lapses. `POST residents/{rid}/release` `{reason}` ends one incarnation and never
  touches its replacement.
- `POST attempts/{aid}/resident-bind` `{resident_id}` hands a claimed turn to a live resident of its session; 409
  `resident_stopped`, `resident_mismatch` or `session_key_mismatch` means start the turn the ordinary way.
- `POST runners/me/residents/reconcile` `{residents: [{id, state}]}` after a reconnect: the answer lists the residents
  to keep (`residents`), to stop (`stops`, with `stop_reason` and `revoked`) and the turns a revocation fenced
  (`fenced_attempts`).

Every resident answer has `stop`; when true, end the process. A revocation (a bot paused or archived, moved or
unassigned, a revoked computer, a removed thread member, a narrowed thread or task audience, a removed credential,
sign-in or mailbox access) ends the residents it touches in its own write and fences their live turns: `started` and
`background-done` answer 409 `stale_lease`, the turn's own token is refused, events are acknowledged as `historical`
and dropped, `inputs` hands nothing, renew answers `stop`, and `complete` is taken with its usage but posts no answer.
A cleared session ends its residents without fencing a turn.
