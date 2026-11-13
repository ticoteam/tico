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
                code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
                busy = code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or str(exc) == "database is locked"
                if not busy or time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        yield c


def run(store, onboarding, telemetry):
    """Each job has its own transaction; a failure is captured and the next job still runs."""
    settings = store.settings
    # A company from before the Librarian was built in gets it on update, once it can run it. Only an update
    # adds them; until this runs, a page shows the company as it was before the update.
    try:
        # What completes a company (the built-ins it can run, the Assistant's routines, General's manager, BotOps'
        # sweep): the same function the end of a first run calls. A failed step is captured and the next one runs.
        with _transaction(store) as c:
            onboarding.complete_company(c, failed=telemetry.capture)
    except Exception as exc:  # noqa: BLE001 - one failed job must not prevent the remaining jobs
        telemetry.capture("complete_company", exc)
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
    # Production runs another release: a bot's snooze in a stage that checks the release production runs (Product's
    # Validate) ends now, not at the clock time it guessed the release would land (task_engine.end_release_snoozes).
    try:
        from . import releases, task_engine

        with _transaction(store) as c:
            task_engine.end_release_snoozes(c, releases.version(), settings.release_repo)
    except Exception as exc:  # noqa: BLE001 - one failed job must not prevent the remaining jobs
        telemetry.capture("release_snoozes", exc)
    # A task that depends on another until released (a Validate task on its Engineering task) goes on, or its owner is
    # woken once, when the release this server runs holds that task's merged PRs (task_depends.on_release).
    try:
        from . import releases, task_depends

        with _transaction(store) as c:
            task_depends.on_release(c, releases.version(), settings.release_repo)
    except Exception as exc:  # noqa: BLE001 - one failed job must not prevent the remaining jobs
        telemetry.capture("depends_released", exc)
    # Once, at the release that brought Validate's proof gate: a bot's bare ask of a person on a task already in
    # Validate is withdrawn, and a task whose passed page was all else it waited for is Done (validate_gate.sweep).
    try:
        from . import validate_gate

        with _transaction(store) as c:
            validate_gate.sweep(c)
    except Exception as exc:  # noqa: BLE001 - one failed job must not prevent the remaining jobs
        telemetry.capture("validate_sweep", exc)
