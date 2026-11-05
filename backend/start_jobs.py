"""Idempotent startup work that runs beside the serving server, in order."""

import sqlite3
import time
from contextlib import ExitStack, contextmanager

# Shutdown waits for the start jobs (app.py), so a database that stays busy ends the wait: the job is captured.
ADMISSION_WAIT = 120


@contextmanager
def _transaction(store):
    # A request can answer busy and be retried by its client; a start job has no client to retry it.
    # Retry admission only: once the job starts, its errors must still be captured without replaying its work.
    deadline = time.monotonic() + ADMISSION_WAIT
    with ExitStack() as stack:
        while True:
            try:
                c = stack.enter_context(store.transaction())
                break
            except sqlite3.OperationalError as exc:
                code = getattr(exc, "sqlite_errorcode", 0) & 0xff
                busy = code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or str(exc) == "database is locked"
                if not busy or time.monotonic() >= deadline:
                    raise
                time.sleep(.1)
        yield c


def run(store, onboarding, telemetry):
    """Each job has its own transaction; a failure is captured and the next job still runs."""
    settings = store.settings
    # A company from before the Librarian was built in gets it on update, once it can run it. Only an update
    # adds them; until this runs, a page shows the company as it was before the update.
    try:
        with _transaction(store) as c:
            onboarding.ensure_librarian(c)
            onboarding.ensure_goal_manager(c)
            onboarding.ensure_task_manager(c)
            onboarding.ensure_counsel(c)
    except Exception as exc:  # noqa: BLE001 - one failed job must not prevent the remaining jobs
        telemetry.capture("librarian", exc)
    # The Librarian gets the market posts from Listening, once (Settings > Listening). After the Librarian exists.
    try:
        from . import listening
        with _transaction(store) as c:
            listening.seed_market(c, settings)
    except Exception as exc:  # noqa: BLE001 - one failed job must not prevent the remaining jobs
        telemetry.capture("listening", exc)
    # A message bot made before the server linked it to its person is linked now, when there is no doubt whose
    # it is. A backfill: the bot works unlinked meanwhile, as it did before.
    try:
        from . import message_bots
        with _transaction(store) as c:
            message_bots.backfill(c)
    except Exception as exc:  # noqa: BLE001 - one failed job must not prevent the remaining jobs
        telemetry.capture("message_bots", exc)
    # This release may contain merged pull requests attached to tasks: their links become shipped
    # (backend/github.py). It reads every pull request link, the slowest of these on a big company; a link
    # still shows merged until it runs.
    try:
        from .github import ship_deployed
        with _transaction(store) as c:
            ship_deployed(c, settings)
    except Exception as exc:  # noqa: BLE001 - one failed job must not prevent the remaining jobs
        telemetry.capture("github", exc)
