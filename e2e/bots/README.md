# Bots end-to-end

`backend/tests/test_e2e_bots.py` (all slow):

- **Chat turn through the real runner**: a message to `ops` becomes a job; the runner built from `runner-rs/` leases it,
  runs it through the fake `claude` harness (`runner/tests/conformance/fake_claude.py`), and the reply is visible in the
  conversation. The job, its attempt and the started and complete events are checked on the server.
- **Hand-off between bots**: a bot's message to another bot that needs action queues a job for that bot at once. The
  message is sent with the leased identity of the sending bot, and the other bot's claim gets it.
- **Approval**: a bot asks for an approval, a person who is not the decider is refused, the owner approves, and the bot
  consumes the approval once (a repeat is a replay, a different payload hash is refused).
- **Rename**: skipped. There is no bot slug rename path on main; a bot's slug is its frozen id. The skip names the reason
  and the scenario is added when a rename route lands.

The runner's harness is the fake from `runner/tests/conformance`; the Rust binary is built once per test run with cargo,
so the chat turn runs on RunsOn, not on a shared computer.

## Run

On RunsOn, from a pushed branch:

    python3 scripts/remote.py --setup rust -- python3 -m pytest -q -o addopts='' -m slow backend/tests/test_e2e_bots.py

The approval and hand-off tests need no runner and run anywhere:

    python3 -m pytest -q -o addopts='' -m slow backend/tests/test_e2e_bots.py -k "hands or approval"
