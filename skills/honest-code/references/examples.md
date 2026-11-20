# Examples from Tico

Paths and lines are `origin/main` at `7e7842e5a2` (2026-11-18). Lines move; search for the quoted text. The `ui/` paths
are the old web app's, removed since (#4660): read them at that commit. Lesson ids
(`L02`) point at `../LESSONS.md`.

## Cause of a flaky test, the fix that held, the helper to use

Counts are from 20 flaky-test incidents between 2026-10-14 and 2026-11-18.

| Cause (incidents of 20) | Fix that held | Tico helper or model |
|---|---|---|
| The browser reads state before it lands (4) | Wait for the drawn or written state, and read it in the same call | `until`, `drawn` (`ui/tests/support/e2e.cts`); `page.waitForFunction` that returns the value (`ui/tests/stage-column-menu.cts:178`); `pick()` (`ui/tests/finance-campaigns.cts:84`); `page.waitForResponse` registered before the action (`ui/tests/changelog.cts:110`) |
| A fixed sleep, drain or poll where a signal exists (3) | The fake emits an event and the test waits on it | `threading.Event` set by the fake (`backend/tests/test_topic_change.py:31`, awaited at `:408`); `Stream.until` (`backend/tests/helpers/events.py:79`); `Notify` (`hold_first`, `runner-rs/src/upload_pace/streamed.rs`) |
| The wall clock (6) | Pin or move the clock; measure from the record's own timestamp; count instead of timing | `monkeypatch.setattr(H, "now", lambda: at)` (`backend/tests/test_gateway_connection.py:766`); `time_machine.travel(..., tick=False)`; `window.__draws` (`ui/tests/thread-redraw.cts:89`) |
| State left by another test or thread (2) | Reset process-wide caches around every test; count only what the measured thread runs | `no_cached_google_tokens` (`conftest.py:223`); `recorder(seen)` (`backend/tests/helpers/statement_scope.py`) |
| A race in the product that the test exposed (2) | Fix the product; the test holds one side until the other lands | `BEGIN IMMEDIATE` around the baseline load (`backend/hubdb.py`, #3891); `HL_NEWEST` (`ui/health.ts`, #3894) |
| A background worker writes after the test (seen in #809, #1212) | Hold the worker until the test releases it, or wait until nothing is pending | `refresh_after_grant` (`backend/tests/test_bot_product_repos.py:79`); `media_settled(api)` (`backend/tests/helpers/visuals.py:58`) |
| The outside network (seen in #2859, #3060, #3103) | Refuse every host but loopback; use a recorded fixture | `block_outside_network(monkeypatch, reason)` (`backend/tests/live.py:11`) |
| A leaked file descriptor in Rust tests (1) | A child process writes the script, so the test never holds it open for writing | `crate::util::write_script` (`runner-rs/src/util.rs:280`) |
| A count that depends on random row ids (1) | None yet. The merged change is a tolerance (L27). Fix the ids, or delete the test. | `backend/tests/test_status_reads.py:222` |
| Not a flake: the file was left out of the selection (1) | Run the file alone with `-n 0`. It failed 3 of 3, and the bug was in the product (#3980). | L28 |

How the fixes were proved: 20 of 20 runs on RunsOn (#3893), 10 of 10 (#3894, #4125), 2000 stress runs with 0 failures
(#609). How the causes were confirmed before fixing: a 300 ms delay in the fake (#3548), a route held for 1.5 s (#3696),
a 1 s sleep before the first read (#3672). Each made the old test fail on every run.

## Changes that looked like fixes

| Change | Where | Why it is not a fix |
|---|---|---|
| An empty commit to rerun the checks | `e9dece9583`, before #3548 | The race was still there. The real fix came next. |
| Retry failed tests once and pass the leg | #4124, `scripts/pr_checks.py` | The flaky test merges green. Its record is a 14-day artifact. |
| Wait for the element, then measure it | #4002 | The header redrew between the two steps. #4125 made it one call. |
| `abs(more - steps) <= 5` | #4018, `backend/tests/test_status_reads.py:222` | It hides drift of up to 5 steps. |
| Seven subprocess timeouts of 10 to 30 s became `HANG = 120` | #503 | The wait is still there. The pull request called them hang guards, which is the honest name. |
| Delete 1,919 test cases with an empty commit body | `c59f968542` | Nobody can say which were flaky or what is unchecked. One test came back 32 minutes later. |

## Waiting in Python

Bad: a sleep stands in for a clock tick. `backend/tests/test_webhooks.py:40`

```python
due(api)
time.sleep(1.1)  # a second tick of the clock the timestamp reads
assert run(api, net) == 1
```

Good: move the clock the product reads. The stamp comes from `time.time()` in `backend/webhook_delivery.py`, not
`H.now()`, so the module gets a `_now` seam and the test advances it.

```python
stamp = [1614265330]
monkeypatch.setattr(D, "_now", lambda: stamp[0])
...
stamp[0] += 2
assert run(api, net) == 1
```

Which clock: `H.now()` (`backend/hubdb.py`) for rows; `time.time()` and `datetime.now()` for the stdlib wall clock
(`time_machine.travel(..., tick=False)`); `time.monotonic()` for deadlines inside the product (a module seam). Patch
the one the code under test reads.

Good: wait for N arrivals, not for half a second. `backend/tests/test_needs_lane.py`. Count where every request
passes (the lane), not inside the held build: only two builds run at once, so a count there never reaches 45.

```python
async def counted_run(*args, **kwargs):  # a spy: it counts, then runs the real lane
    with seen:
        count[0] += 1
        if count[0] == 45:
            all_arrived.set()
    return await real_run(*args, **kwargs)
...
assert all_arrived.wait(10), f"only {count[0]} of 45 Needs you requests reached the lane"
```

Good: prove the race the test is named for happened. `backend/tests/test_mcp_connect.py` spies on
`oauth_refresh.take`; the fake vendor holds its answer until a second caller finds the refresh in flight, and the test
asserts that it did. A sleep there let the test pass when the second caller came after the refresh.

Bad: a loop that stops quietly at its deadline. After 10 s the assertions run on state that never arrived.
`backend/tests/test_flight.py:279-284`

```python
deadline = time.time() + 10
while time.time() < deadline:
    with app.state.store.transaction() as c:
        if flight.last_start(c):
            break
    time.sleep(0.05)
```

Bad: a sleep proves that nothing more happens. `backend/tests/test_heartbeat.py:526`

```python
time.sleep(0.3)  # a third pass, if one were coming, would start here
assert worker.passes == 2
```

Good: the fake says when the call arrived. `backend/tests/test_topic_change.py:31` and `:408`. Before #3404 this was
a 1.0 s poll on `model.calls`.

```python
self.asked = threading.Event()  # set when a call arrives, so a test waits for the call itself
...
assert model.asked.wait(10), "the check is asking Decide"
```

Good: a deadline that raises and names what it saw. `backend/tests/helpers/events.py:79-85`

```python
def until(self, match, timeout=30.0):  # a deadline, not a wait: a busy computer can take seconds per event
    ...
    if left <= 0:
        raise AssertionError("no matching event; saw " + json.dumps([e.get("event") for e in self.seen]))
```

Good: wait for a sequence number, not for half a second (#3672, `e2e/scenario.py`).

```python
# before: stream.until(ready); stream.drain(0.5)
newest = stream.until(ready)["data"]["seq"]
stream.until(lambda e: e["event"] == "reset" or e["id"] >= newest)
```

Also bad, same shapes: `backend/tests/test_finance_sync.py:609` (a sleep before a counter snapshot; needs a "first
pass done" event in the worker), `conftest.py:285-294` (`inside_one_half_hour` sleeps on the real clock for up to about 20 s).

Good: a port from the OS, not a retry on collision: bind port 0 and read the port back.

Good: order between two Rust tasks is a `Notify` the first side sets (`hold_first` in
`runner-rs/src/upload_pace/streamed.rs`), not a sleep.

Keep a pause the code under test needs, with its reason: the stall test in `backend/tests/test_flight.py` sleeps
0.1 s because the classifier names lock time only after some has passed. Delete a pause that hopes.

"Nothing happened" is read from the decision the action writes as it runs, not waited for. Check that the product
writes that record before you wait on it: `runner_support/tests/test_threads_e2e_turns.py` first waited for a delivery
row the server never writes for a bot that does not follow the thread.

## Waiting in the browser

Bad: a fixed wait, then a check that is also true while the page is still loading.
`ui/tests/ask-screen-content.cts:118`

```ts
await page.waitForTimeout(400);
assert.equal(await approve.isDisabled(), true, 'Approve waits for the proof');
```

Bad: a sleep written as a loop. `ui/tests/ask-filter.cts:67`

```ts
for (let i = 0; i < 50 && !saved.length; i++) await page.waitForTimeout(20);
```

Good: the same wait with a name and a failure, using `until` from `ui/tests/support/e2e.cts` (a proposal; not run).

```ts
await until(() => saved.length, 'the filter save reached the fake');
```

Good: wait and measure in one call. `ui/tests/stage-column-menu.cts:178` (L25)

```ts
const measured = await phone.page.waitForFunction(() => {
  const r = document.querySelector('.pv-phone-head [data-pv-col-menu]')?.getBoundingClientRect();
  return r && r.width > 0 ? {x: r.x, width: r.width, height: r.height} : null;
});
```

Good: register the response wait before the action. `ui/tests/changelog.cts:110-112`

```ts
const searched = page.waitForResponse(r => r.url().includes('q=restore'));
await search.fill('restore');
await searched;
```

`isVisible()`, `isHidden()`, `isEnabled()` and `isDisabled()` return at once and do not wait. Inside an `if` they make
the assertion conditional. Use `locator.waitFor()` or `page.waitForFunction`.

## Doubles

Bad: our own function replaced by a canned answer. The test checks only the caller.
`backend/tests/test_bookkeeper_watch.py:35`

```python
monkeypatch.setattr(FR, "watch_findings", lambda c, viewer, at: [dict(FINDING)])
```

Bad: `sleep` removed from the `time` module for the whole process. `company_apps.time` is the `time` module itself,
so every thread in the run stops sleeping. The same goes for `time.time` and `time.monotonic`: a frozen `monotonic`
stops every deadline in the process. `no-global-time-patch` bans all of them, in every spelling (`setattr`,
`patch.object`, `patch("x.time.sleep")`).

```python
monkeypatch.setattr(company_apps.time, "sleep", lambda seconds: None)
```

Good: the module owns a seam, and the test patches that. `clients/tico.py`, `scripts/company_apps.py`

```python
def _sleep(seconds):
    """Every pause goes through here, so a test replaces this and not time.sleep for the whole process."""
    time.sleep(seconds)
```

Bad: the same seam as an assignment, `_sleep = time.sleep`. It binds the real function at import, so a test that swaps
the module's `time` for a fake clock (`wait_clock` in `backend/tests/test_updater_safety.py`) never reaches it and the
test hangs (L31). `no-assigned-clock-seam` bans the shape.

Good: a seam for anything else the process shares. `_run`, `_which` and `_platform` in `clients/hermes_agent.py` wrap
`subprocess.run`, `shutil.which` and `sys.platform`; `_open` in `clients/judge.py` wraps `os.open`, so a test can swap a
file between its check and its open (`test_registry_file_swap_to_an_outside_symlink_is_refused`); `_free_bytes` in
`backend/operations.py` wraps `shutil.disk_usage`.

Good: pass the function in. `resolve_op_refs(env, run=None)` (`runner_support/op.py:24`) takes the runner, so the
test hands it a fake `op` with no patch at all.

Allowed with a reason on the line: forcing a failure in our own code that cannot be produced from outside.
`backend/tests/test_automation_due.py:250` does this (`monkeypatch.setattr(hubdb, "task_create", broken)`) and has no
reason yet.

A patch that is never reached checks nothing. Two `advance` patches in
`backend/tests/test_automation_reclaimed_worker.py` never ran: `go()` returned before it reached `advance`. Break the
patched path once and watch the test fail through it.

Bad: a fake shaped like the code under test instead of the contract. The fake judge answered `value`, as
`backend/email_sending.py` `classify` read it; the judge answers `choice` (`clients/judge.py` `_project`). The test
passed while every real email was held for a person (L07).

Allowed: a spy that wraps the real function and still calls it (`backend/tests/test_billing_check.py:174`).

Good: fake HTTP at the transport, so the real client builds every request. A module that sends with httpx has a
`TRANSPORT = None` that it passes to `httpx.Client`; a test sets an `httpx.MockTransport` (`backend/mcp_connect.py`,
`backend/api_tools.py`). A test of what `clients/tico.Client` sends uses `Wire` or `cli_wire`
(`clients/tests/helpers/wire.py`): it records each request and refuses a route it has no answer for.

Bad: a hand-written class with our client's method names (`Api`, `FakeClient` in `clients/tests/test_hubcli.py`, set as
`remotecli.Client`). It is not `unittest.mock`, but it is still a double of our code: the request the client builds is
never made. A new test uses `cli_wire`; convert an old one when you touch it.

Good: only what is outside Tico is a double. `backend/tests/helpers/ticket_journey.py:6-8` says so, and uses
`backend/tests/helpers/fake_github.py`.

Before you write a fake, check that the code calls the service. `connectors/slack.py` calls only Tico, so its tests
need `Wire`, not a Slack fake.

Fakes that exist: GitHub `backend/tests/helpers/fake_github.py`; Stripe `scripts/fake_stripe.py`; QuickBooks, Xero,
Mercury `scripts/fake_quickbooks.py`, `scripts/fake_xero.py`, `scripts/fake_mercury.py`; S3
`backend/tests/helpers/backup_bundle.py:ObjectStore`; a model `backend/tests/helpers/judge.py:FakeJudge`; HQ
`backend/tests/helpers/cloud_machines.py:FakeHQ` (five more copies live in single test files; use this one). None yet:
Slack, CloudWatch. For a CLI, put a fake executable on PATH
(`runner_support/tests/isolation/fake_cli.py`) or pass the runner in.

A fake that lacks a failure gets it, one `_check("list")` call, instead of a `Mock(side_effect=...)` on its method.

Allowed: an assertion on what a recording fake received, when that request is what leaves the process (the metric
payload sent to AWS). Not allowed: `mock.call_args` on a `Mock` of our own function.

Allowed with a reason: pointing an import-time path constant (`PROJECTS` in `connectors/browser.py`, `ROOT` in
`clients/repo_safety.py`) at a temp directory. Better: read the path per call from the environment.

A browser fake for a host Tico does not own (telemetry, a vendor script) is a `page.route` to that host with the shape
the test asserts. A fake for a Tico route returns what the route returns, and a refusal is `{error: {code, detail}}`
(`Problem` in `backend/store.py`), not `{}`. A catch-all answers unknown paths with `route.abort()` so the test fails
on the URL; `ui/tests/web-timings.cts:42` (`401 {}`) and `ui/tests/sign-in-redirect.cts:23` (`200 {}`) do not. A save
fake returns the saved value, and the test asserts the page shows it, not only the posted body.

Good: a check that a file is never read makes the read fail. `path.chmod(0)` before the call
(`clients/tests/test_task_attach_stream.py`), not a patch of `Path.read_bytes` that raises. CI runs as a user that is
not root, so the mode holds there.

Put a `# double:` reason on its own line above a `with mock.patch...(` that spans lines. `ruff format` moves a reason at
the end of the first line onto the closing parenthesis.

Fake drift to watch: the `EMPTY` table in `ui/tests/support/mailboxes-fake.cts` copies the response shapes of
`backend/postal_mail.py` by hand. No check compares them (L07).

## Assertions

Bad: both sides come from the code under test, so the test is always true.

```ts
const expected = buildSearchQuery({tag: 'urgent'});
assert.equal(buildSearchQuery({tag: 'urgent'}), expected);
```

Good: a literal worked out by hand.

```ts
assert.equal(buildSearchQuery({tag: 'urgent'}), 'tag:"urgent"');
```

Bad: a test that only notices an edit: `assert MAX_RETRIES == 5`. Good: "a failing call is retried 5 times and the 6th
attempt never happens."

Good: a cost test that counts reads, not seconds (`backend/tests/test_never_stuck_rig.py`, `assert big["reads"] < 60`).

Good: a known failure that must keep failing, with its reason. `backend/tests/test_never_stuck_rig.py:36`

```python
pytest.mark.xfail(strict=True, raises=SC.ScenarioFailed, reason=SC.FAILING[number])
```

Review proof (L10): remove the guarded line and say what happened. "With the members check removed, it fails" (#3741).

Bad: an `or` in the assertion passes on either of two outcomes, so it cannot tell which one the code took.
Bad: `assert "saved" in message` passes on "not saved". Bad: comparing the response's `at` with the response's own
`at`. Bad: a test called `test_..._refuses_every_writer` that sends one request.

Good: read a signal the way its reader does. What a computer is told comes from `runner_read`, the function behind
`GET /api/v2/runners/me/events`, not from a count of `changes` rows (`backend/tests/test_heartbeat_reply.py`). When
only a table shows a signal and nothing reads it, delete the check and record it as unchecked.

Before you delete a test as a duplicate, list its cases and find each one in the test that stays. The "duplicate" in
`backend/tests/test_model_key_store.py` had one case, a key nobody took, that the other test lacked; it moved there.

Bad: `assert took < 2.0`. A generous bound is still a timing assertion: it flips on a loaded runner and never catches
a 10% regression. Keep the statement count next to it and delete the bound.

## Skips

Good: the skip says what is missing and where the test runs. `# the pinned Litestream binary is not installed in CI;
these restore tests run only where it is installed by hand`. Better still, CI installs it or the test is deleted.

Bad: a premise that fails turns into a skip. `backend/tests/test_threads_core.py` skipped when a mention queued no
run, which is the behaviour under test. Use `assert found, "a mention queued no run"`.

Bad: Docker is up, the container refused to start, and the test skipped. Once the service is known to be there, a
container that will not start or a database that never answers fails with the stderr.

## Suppressions

Good: `<tool comment> - <reason>`, where the reason names the case.

- `backend/api_tools.py:978`: `except Exception as exc:  # noqa: BLE001 - every network failure is one answer: the check failed`
- `backend/mail_lib/gmail.py:64`: `# pragma: no cover - latin-1 decodes every byte, so the loop always returns`
- `runner-rs/src/client.rs:227`: `#[allow(dead_code)] // the status and doctor reads with a query come in step 2`

Bad: no reason.

- `backend/answers.py:59`: `raise AssertionError("refuse() always raises")  # pragma: no cover` (the same line is in six
  files)
- `backend/stage_pick.py:84`: `return dict(value)  # type: ignore[arg-type]`
- `backend/app.py:77`: `from .bot_status_routes import INBOX_LIMIT  # noqa: F401` (it is a re-export; say so)
- `app/src/windows.rs:1082`: `#[allow(dead_code)]`

## Comments

Keep: a reason the code cannot show.

- `backend/hubdb.py:277`: "ISO-8601 UTC to the microsecond: sorts lexicographically, which every query relies on, and
  two writes in one loop never collide."
- `backend/store.py:386`: "the pauses go through this so tests can run without waiting"
- `backend/tests/helpers/events.py:79`: "a deadline, not a wait: a busy computer can take seconds per event"

Delete or rewrite:

- A date, a version or a pull request number: `backend/company_file/draft.py:44` ("Nov 11 (#3265, v0.6.0-rc.6): slugs
  match card names..."). Keep "slugs match card names".
- An incident story: `backend/execution.py:82` ("Oct 17-18 eight finished turns reached the hub 15 to 76 minutes
  after..."). Keep the rule it led to, in the present tense.
- A person's name and a date: `backend/bookkeeper.py:466`.
- A restatement of the next line: `backend/tests/test_assistant_routines.py:151` ("Saving Draft remains possible while
  sending is disabled.").
- A banner: `backend/access.py:198` (`# ------... per-bot access`).

## A deletion record

#3255 (`d1ca9968a2`) deleted a test that waited up to 10 s on a file watcher. Its commit body gave the run and what
still covers the build. It did not say what is now unchecked. The same deletion with the full record:

```
Delete flaky <test id> (scripts/tests/test_recorder_build_ui.py)

Flipped: merge-queue run 42967757665 failed on a loaded runner and removed an unrelated pull request.
Cause: the test waits up to 10 s of real time on a file watcher.
Deterministic fix tried: <what you tried, or "none: the watcher gives the test no event to wait on">
Now unchecked: watch mode rebuilds the UI when a source file changes.
Still covered: the one-shot build tests in the same file check what the build writes.
Security, privacy, money, approval or data-safety path: no.
```

A bulk deletion gets one such block per test or per group with one cause. "Low value" is a reason only with what the
test checked and what still checks it.

## Judgment lessons with no code pair

- **L13.** #3840 removed the migration writers and, with them, read paths that older databases still need. A
  second-model review found 4 privacy regressions. #3832 repeated it for ask and answer reads. When you remove code,
  list the paths that stay and run their tests.
- **L05.** A test that passes alone and fails inside a worker is reading state it did not make. Look for a
  module-level cache, a background thread, or ids that every test reuses (`wsk-1` in #3851).
- **L06.** #3060: a demo seed called the real model gateway. It got `auth` on CI and `unavailable` on a laptop, so two
  builds for one date differed.
- Re-read each audit row at HEAD before you act on it. Pass-3 rows went stale (`FIRST_PASS` in finance sync was already
  fixed), and one called an in-process read "outside".
- Before you change a test helper, find its callers. Delete an uncalled one in the same change, with a record
  (`restart_rust`, `standard_startup`).
- **L29.** #3891: two starts on a new database both loaded the baseline (`table humans already exists`). The test was
  right. The fix was a write lock in the product.
