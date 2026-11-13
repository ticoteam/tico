# Website tools live checks

From the repository root on macOS:

```sh
python3 e2e/website-tools/run.py --repeat 5
```

Requires Google Chrome in `/Applications/Google Chrome.app`, Rust/Cargo, Swift (Vision OCR), and the repository's
Python development dependencies. The script uses `.venv/bin/python` when present, then the shared development
venv beside this worktree, then the invoking Python. It builds the real Rust runner once for the whole invocation.
No models, outside websites, production databases or existing Chrome profiles are used.

Each repetition creates an isolated real HTTP server using `e2e/server.py`'s disposable settings, a fictional
company, two enrolled real Rust runners, bot attempts, and local practice pages. Website tools, accounts and grants
are created through the people API. Bots use the real `tico browser` and `tico login` command parser and HTTP client;
the harness never drives Chrome directly. Chrome uses the runner's native CDP pipe and one private profile per account.

| Matrix row | Scenario |
| --- | --- |
| 10 | Accepted browser heartbeat, host online, offline, missing Chrome, revoked, fallback and central-only refusal |
| 11 | Wrong-computer attempt refused before delivery; attempt ends during a real command |
| 12 | Host changes with an open session; runner and server interrupted during navigation; a read after server restart refuses; expired commands close Chrome and retain the sign-in |
| 13 | Plain, two-step, SPA replacements before and after fill/stale refs, same-origin and cross-origin iframes, show password, focus trap |
| 14 | Independent TOTP verification; text/captcha handoff, non-admin manager view/frame/input/Done on both computers, bot resumes |
| 15 | Listed SSO subdomain redirect; unlisted host and port refuse fill |
| 16 | Close/reopen and runner restart preserve the session; expired and session-only cookies sign out and sign in again |
| 17 | Shared account busy/release, different accounts in parallel, person takes/gives back control; a queued bot click is cancelled; two starts at the eight-browser cap; evicted browser read refuses and person view wakes on-site |
| 18 | Download/upload bytes round trip; Chrome crash relaunches on the next command |
| 19 | Canary scan of every captured CLI response, server/runner/practice log, events, Activity, messages, receipts and screenshot pixels after cleanup |

For a focused development run, use `--row 13` (repeatable). Acceptance uses the full command above. A failure stops
the run; commands and scenarios are never retried. Every scenario prints one PASS/FAIL line with a value-free reason.

Practice origins are explicit `http://practice.localhost:<port>` and `http://127.0.0.1:<port>` sites; Chrome resolves
`.localhost` without DNS. The SSO subdomain is beneath the listed host, while `unlisted.localhost` and a second local port are outside the grant.
The harness waits for browser polling liveness to expire after stopping the host, changes its disposable database
to simulate attempt ownership, and briefly pauses its own runner for missing-capability and queued-control cases.
It signals only processes it started (Chrome is verified through its runner's process ancestry). All profiles, files and process
logs live in a private temporary directory and are removed on success, failure or interruption.
