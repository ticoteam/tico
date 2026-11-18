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
- **A tool's Settings** (key on computers and through the proxy): its address and region arrive beside the key, the
  address reaches the vendor, and a bot saying the region is not masked (a setting is not a secret).
- **Remote MCP server in bot.yaml** with `Bearer ${VAR}`, key on computers (Claude and Codex): initialize, tools/list
  and a tools/call succeed, and the value is never on Claude Code's command line.
- **The same MCP server with its key Tico holds** (a proxied tool, and a computer tool at Read with only a Read and
  write key): the run holds a proxy token, the server is reached through the proxy and the vendor gets the real key;
  at Read only the set-up, the listing and the read-only tool go through.
- **Website login with password and 2FA**: `tico browser open`, `tico login fill` (username, password, code) and a
  submit click sign in to a stub form that checks an RFC 6238 code; the password and the 2FA key are in no turn
  environment, command output or record. The company browser host is the test standing in for Chrome (pytest launches
  no browser); the real fill into Chrome is `runner-rs/src/browser/live.rs` (`cargo test -- --ignored`).
- **One key granted to two bots**: both use it; taking one bot's level away removes it from that bot's next turn only.
- **Revoke**: the next turn has neither the variable nor the MCP server.

Run it locally (one cargo build of `runner-rs`, then about two minutes):

    python3 -m pytest -q -n 0 -m slow runner_support/tests/test_credential_delivery.py

## Live auth matrix

`runner_support/tests/test_credential_live.py` runs the same machinery against real public test services instead of
the stub: one scenario per kind of auth Tico can hand a bot today (bearer, Basic and Digest on httpbin, Basic through
the proxy on postman-echo, API keys in a header and the query through the proxy on reqres.in and NASA, a client
certificate file on client.badssl.com, an AWS SigV4 key pair on play.min.io, OAuth 1.0a and Hawk on postman-echo,
GitHub's documented webhook signature, an IMAP login on test.rebex.net, a session cookie on DummyJSON, and DeepWiki's
MCP server with no auth). Each checks that the good credential works, that the service refuses a wrong one, and
that the value shows nowhere. Only published test credentials or synthetic values; a service that is down or rate
limited skips its scenario. It calls the internet, so it never runs unless asked:

    TICO_LIVE_AUTH=1 python3 -m pytest -q -n 2 -m slow runner_support/tests/test_credential_live.py

[docs/auth-types.md](../../docs/auth-types.md) maps every auth type to how a person hands it over and its scenario.

## Live git check

`runner_support/tests/test_github_git.py` is a bot's git push with a GitHub App token, end to end: the hub mints an
installation token as it does for a bot, the real runner hands it to the turn as its credential helper, and plain git
clones, commits, pushes a branch and deletes it. Offline, a fake GitHub mints GitHub's stateless tokens
(`ghs_<app id>_<JWT>`) and a local HTTPS git server takes a push only with the minted one; it runs in every release.
Live, against the real GitHub, it needs a GitHub App installed on one scratch repository with what a bot's turn token
asks for (Contents, Pull requests and Issues, each read and write, accepted on the installation: GitHub refuses a token
naming a permission the installation lacks, so with Contents alone the turn gets no credential), and skips, naming
them, without:

    TICO_GIT_CHECK_APP_ID=<id> TICO_GIT_CHECK_PRIVATE_KEY="$(cat app.pem)" TICO_GIT_CHECK_REPOSITORY=<owner>/<name> \
        python3 -m pytest -q -n 0 -m slow runner_support/tests/test_github_git.py

Release validation passes repository secrets of the same names (and `TICO_GIT_CHECK_INSTALLATION_ID`, optional) to its
Python VMs, so a GitHub change that breaks bots' pushes fails the release instead of every bot.
