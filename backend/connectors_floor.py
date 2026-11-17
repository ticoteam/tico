# pyright: strict
"""Inert answers for the routes a 0.6 computer's retired `connectors` job still polls (v0.6.0-rc.1 through v0.6.19).

These routes exist only until the floor moves past those releases. Then this module and its install line in
backend/connectors.py go.

Mail and calendar run on the server now (docs/mail.md), but a computer still on one of those releases keeps polling
every 45 seconds (runner_support/connectors.py at the tag). A computer that holds a Google key starts that job without
asking `runners/connectors` first. Each route answers the shape that client reads as "nothing to do": no
connectors, no targets, no action, no send, and an accepted report. The job then idles until the computer updates, and
the update removes the job (runner_support/release_update.py).

A result or report is accepted and dropped. The migration that retired the computer side
(20261117062123_drop_mail_copies) already settled every lease a computer held. The routes a computer reaches only
after claiming work (send preflight and dispatch, the Workspace token, the Google sign-in exchange) stay gone: a claim
here hands out nothing.
"""

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request

from .store import Problem

Json = dict[str, Any]

GETS: dict[str, Json] = {
    "/api/v2/runners/connectors": {"connectors": []},
    # `hours` is what an rc.1 client reads for each listed person; none are listed.
    "/api/v2/connectors/calendar/targets": {"people": [], "hours": 24},
    # rc.1 reads `mailboxes`, later 0.6 releases `acts_for`.
    "/api/v2/connectors/mail/targets": {"mailboxes": [], "acts_for": []},
}
CLAIMS: dict[str, Json] = {
    "/api/v2/connectors/calendar/actions/claim": {"action": None},
    "/api/v2/connectors/email/sends/claim": {"send": None},
    "/api/v2/automations/mail-actions/claim": {"actions": []},
    "/api/v2/connectors/mail/google/claim": {"connect": None},  # rc.1 only
}
REPORTS = (
    "/api/v2/connectors/calendar/actions/{item_id}/result",
    "/api/v2/connectors/email/sends/{item_id}/result",
    "/api/v2/automations/mail-actions/{item_id}/result",
    "/api/v2/connectors/mail/google/{item_id}/result",
    "/api/v2/connectors/calendar/snapshots",
    "/api/v2/connectors/mail/messages",
    "/api/v2/connectors/health",
)


def install(app: FastAPI, store: Any, execution: Any) -> None:
    def computer(request: Request) -> None:
        """Only a registered computer polls these; anyone else is refused as the old routes refused them."""
        with store.read() as c:
            if not execution.runner(c, request.state.identity):
                raise Problem("identity", "A registered runner is required", 403)

    def answer(shape: Json) -> Callable[[Request], Json]:
        def route(request: Request) -> Json:
            computer(request)
            return dict(shape)

        return route

    def accepted(request: Request) -> Json:
        computer(request)
        return {"ok": True}

    def accepted_item(item_id: str, request: Request) -> Json:
        return accepted(request)

    for path, shape in GETS.items():
        app.get(path, include_in_schema=False)(answer(shape))
    for path, shape in CLAIMS.items():
        app.post(path, include_in_schema=False)(answer(shape))
    for path in REPORTS:
        app.post(path, include_in_schema=False)(accepted_item if "{item_id}" in path else accepted)
