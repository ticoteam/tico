# Bots end-to-end

`backend/tests/test_e2e_bots.py` (all slow):

- **Chat turn through the real runner**: a message to `ops` becomes a job; the runner built from `runner-rs/` leases it,
  runs it through the fake `claude` harness (`runner_support/tests/conformance/fake_claude.py`), and the reply is visible in the
  conversation. The job, its attempt and the started and complete events are checked on the server.
- **Hand-off between bots**: a bot's message to another bot that needs action queues a job for that bot at once. The
  message is sent with the leased identity of the sending bot, and the other bot's claim gets it.
- **Approval**: a bot asks for an approval, a person who is not the decider is refused, the owner approves, and the bot
  consumes the approval once (a repeat is a replay, a different payload hash is refused).
- **Rename**: skipped. There is no bot slug rename path on main; a bot's slug is its frozen id. The skip names the reason
  and the scenario is added when a rename route lands.

The runner's harness is the fake from `runner_support/tests/conformance`; the Rust binary is built once per test run with cargo,
so the chat turn runs on RunsOn, not on a shared computer.

## Run

On RunsOn, from a pushed branch:

    python3 scripts/remote.py --setup rust -- python3 -m pytest -q -o addopts='' -m slow backend/tests/test_e2e_bots.py

The approval and hand-off tests need no runner and run anywhere:

    python3 -m pytest -q -o addopts='' -m slow backend/tests/test_e2e_bots.py -k "hands or approval"

## Credential delivery

`runner_support/tests/test_credential_delivery.py` (all slow) proves a bot receives each kind of credential in a real
run and that using it works against the service. Each scenario sets the credential up through the API as a person does
(Tools > Add, a key, a level, delivery), runs a chat turn through the Rust runner and a fake harness that uses what it
was given (prompt markers, `runner_support/tests/conformance/credential_use.py`), and checks a stub vendor
(`runner_support/tests/helpers/vendor.py`, 127.0.0.1) that refuses anything but the right credential. Every scenario
also checks the raw value is in no transcript, turn event, runner log or table; a proxied one that the run held only a
`tpx1.` token and that the call is on the tool's Activity (`tool_calls`).

- **Key on computers, bearer** (Claude and Codex) and **X-Api-Key header**: the value is in the turn's environment and
  the vendor accepts it; a bot that says it in its reply is masked.
- **Key through the proxy** as bearer, header and query parameter: the run gets a token and the proxy's address, the
  vendor gets the real key, and the vendor's echo of it never reaches the run.
- **Username and password key through the proxy as Basic**: the vendor gets `user:key`.
- **File credential**: the turn gets a 0600 file, the vendor accepts its bytes, and the file is gone after the turn.
- **Remote MCP server in bot.yaml** with `Bearer ${VAR}`, key on computers (Claude and Codex): initialize, tools/list
  and a tools/call succeed, and the value is never on Claude Code's command line.
- **The same MCP server with its key on the proxy**: expected to fail (strict xfail) until a proxied key works for
  MCP. Today the header carries the run's `tpx1.` token and the vendor refuses it.
- **Website login with password and 2FA**: `tico browser open`, `tico login fill` (username, password, code) and a
  submit click sign in to a stub form that checks an RFC 6238 code; the password and the 2FA key are in no turn
  environment, command output or record. The company browser host is the test standing in for Chrome (pytest launches
  no browser); the real fill into Chrome is `runner-rs/src/browser/live.rs` (`cargo test -- --ignored`).
- **One key granted to two bots**: both use it; taking one bot's level away removes it from that bot's next turn only.
- **Revoke**: the next turn has neither the variable nor the MCP server.

Run it locally (one cargo build of `runner-rs`, then about two minutes):

    python3 -m pytest -q -n 0 -m slow runner_support/tests/test_credential_delivery.py
