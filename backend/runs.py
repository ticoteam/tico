"""A bot's run, as the public contract names it.

Storage calls one try at a job an attempt (`attempts`, and an `attempt_id` column on jobs, messages, files, events
and the rest) until Release 2 moves it. API v2, the live events, webhooks, the MCP tools, the tico CLI and the web app
call it a run: a field is `run_id`, an object is `run`, a list of them is `runs`, and `run.state` is the value map
below (docs/status-and-state.md, Runs).

Two layers keep that one name:
- the producers that build a run object or list name it `run`/`runs` themselves, with `state(...)` for its state;
- `public_json` renames every `attempt_id` key in an answer's JSON as it leaves the server (app.py's route class, the
  event stream, webhook bodies), so a row read straight from a table with an `attempt_id` column reads `run_id`.
  It touches keys only: a string value that mentions attempt_id is escaped inside the JSON and stays as it is.

The runner's wire protocol is not the public contract and keeps its own names (contracts/runner): `RUNNER_WIRE`
lists the paths `public_json` leaves alone.
"""

import re

from .status_values import ATTEMPT_STATES

# attempts.state -> run.state. One run state per stored state: `expired` (its lease ran out with no word from the
# runner, so nothing is known about what it did) and `interrupted` (the runner stopped it: an update, a drain, a
# person's Stop) stay distinct, because recovery treats them differently.
STATE = {
    "leased": "leased",  # a runner holds the lease and has not started the harness yet
    "running": "running",  # the harness is working
    "input": "input",  # paused for input the run asked for
    "completed": "completed",  # finished and reported its result
    "failed": "failed",  # ended on an error it reported
    "interrupted": "interrupted",  # the runner stopped it before it finished
    "expired": "expired",  # the lease ran out without a report
}
assert set(STATE) == set(ATTEMPT_STATES), "every stored attempt state needs a run state"
STATES = tuple(dict.fromkeys(STATE.values()))
LIVE = ("leased", "running", "input")


def state(attempt_state):
    """The run state for a stored attempts.state; None stays None (a run with no attempt row)."""
    if attempt_state is None:
        return None
    return STATE[attempt_state]


# A failed run, as Health counts it and Runs lists it: a try that ended failed, or one that started and was lost. A try
# its computer never started (a lease that lapsed before the start, or a hand-back) went back in the queue, so it is a
# retry: its job counts once, by the try that failed it, if it fails in the end (Nov 12: Health said 352 failed runs,
# about 310 of them retries of one job).
FAILED = "(state='failed' OR (state='expired' AND started_at IS NOT NULL))"


def failed(c, since, limit=200):
    """(how many jobs failed since `since`, the newest failed try of each of them, newest first, up to `limit`).
    A run with no job (an older run record) counts as its own."""
    total = c.execute(
        f"SELECT count(DISTINCT coalesce(job_id,id)) FROM attempts WHERE {FAILED} AND finished_at>?", (since,)
    ).fetchone()[0]
    # SQLite takes the bare columns from the row that holds max(finished_at): each job's newest failed try.
    rows = c.execute(
        "SELECT id,bot_id,state,max(finished_at) AS finished_at FROM attempts "
        f"WHERE {FAILED} AND finished_at>? GROUP BY coalesce(job_id,id) ORDER BY finished_at DESC LIMIT ?",
        (since, limit),
    ).fetchall()
    return total, rows


# The runner software's own calls (runner-rs, contracts/runner): enrollment, heartbeats, claims, leases, a run's
# events and completion, its uploads and credentials. A person's or bot's other calls are the public contract.
RUNNER_WIRE = re.compile(
    r"/api/v2/(?:runners/(?:enroll|errors|heartbeat|desired|assignments|eligible|adopt|me|connectors|importers"
    r"|performance|watchers)(?:/.*)?|runner-[^/]+(?:/.*)?|attempts/.+|jobs/claim|files/uploads|enrollments(?:/.*)?"
    r"|bots/[^/]+/(?:memory/report|template/refresh)|imports/sources/[^/]+/status|github/token|connectors(?:/.*)?)"
)

_KEY, _PUBLIC_KEY = b'"attempt_id":', b'"run_id":'


def public_json(body, path=None):
    """`body` (JSON bytes or text) with each `attempt_id` key named `run_id`; the runner wire's paths are left alone."""
    if path is not None and RUNNER_WIRE.fullmatch(path):
        return body
    if isinstance(body, str):
        return body.replace('"attempt_id":', '"run_id":') if '"attempt_id":' in body else body
    return body.replace(_KEY, _PUBLIC_KEY) if _KEY in body else body
