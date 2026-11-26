# pyright: strict
"""Pipeline and stage notes over HTTP: read a pipeline's notes, a person's add, edit and retire, a bot's proposal, and
the accept or decline of one. Every check is backend/pipeline_notes.py's; this module only reads the request.

- Reading: every teammate reads every pipeline's notes, proposed and retired ones included (`state`).
- Writing: a person who may change the pipeline adds, edits and retires notes; an edit or retire needs a `line` on
  what changed and why. A bot proposes (`/propose`, with why and evidence); the pipeline's manager or such a person
  accepts or declines it with a `reason`, never their own.
"""

from typing import Any, Literal

from fastapi import Request
from pydantic import Field

from . import pipeline_notes as _pipeline_notes
from .api_contract import api_v2
from .models import Contract

N: Any = _pipeline_notes
Row = dict[str, Any]


class NoteText(Contract):
    """A note as written: the note, why we have it, since when, its evidence (a task, a pull request, a run), and the
    guard whose refusal quotes it."""

    note: str | None = Field(default=None, max_length=1000)
    why: str | None = Field(default=None, max_length=1000)
    since: str | None = Field(default=None, max_length=40, description="YYYY-MM-DD; today when absent")
    evidence: str | None = Field(default=None, max_length=500)
    guard: str | None = Field(default=None, max_length=100)
    line: str | None = Field(default=None, max_length=1000, description="What changed and why, shown under the note")


class NoteFields(NoteText):
    """A new note: its text, on the pipeline or one of its stages."""

    stage: str | None = Field(
        default=None, max_length=200, description="The stage's id or name; absent for the pipeline"
    )


class NoteEdit(NoteText):
    """A change to a note in force. Its stage stays: a note for another scope is a new note (retire this one)."""

    revision: int | None = Field(
        default=None, ge=1, description="The note's revision as you read it; another answers 409"
    )


class NoteDecide(Contract):
    reason: str = Field(default="", max_length=1000, description="Why, in one line: the proposer receives it")


def install(app: Any, store: Any, auth: Any, mutate: Any) -> None:
    def fields(body: Contract) -> Row:
        return {k: v for k, v in body.model_dump().items() if v is not None}

    @app.get(
        "/api/v2/pipelines/{pipeline_id}/notes",
        **api_v2(
            "Pipelines",
            "listPipelineNotes",
            "A pipeline's notes and each stage's, in order, with the lines on what changed and why; state active (default), proposed, retired, declined or any",
        ),
    )
    def notes_list(  # pyright: ignore[reportUnusedFunction]
        request: Request,
        pipeline_id: str,
        state: Literal["active", "proposed", "retired", "declined", "any"] = "active",
    ) -> Row:
        """A pipeline's notes: its own and each stage's, in order, each with its change lines."""
        who: Any = request.state.identity
        auth.domain(who)
        states = tuple(N.STATES) if state == "any" else (state,)
        with store.read() as c:
            return N.listing(c, pipeline_id, states=states)

    @app.post(
        "/api/v2/pipelines/{pipeline_id}/notes",
        **api_v2("Pipelines", "addPipelineNote", "A person adds a note in force on a pipeline or one of its stages"),
    )
    def notes_add(request: Request, pipeline_id: str, body: NoteFields) -> Row:  # pyright: ignore[reportUnusedFunction]
        """A person who may change the pipeline adds a note in force. A bot is told to propose instead."""
        who: Any = request.state.identity
        auth.domain(who)
        data = fields(body)

        def work(c: Any) -> Row:
            return N.add(c, who.actor, pipeline_id, data.get("stage"), data)

        return mutate(request, body, work)

    @app.post(
        "/api/v2/pipelines/{pipeline_id}/notes/propose",
        **api_v2(
            "Pipelines",
            "proposePipelineNote",
            "A bot proposes a note with its why and evidence; the pipeline's manager is woken to accept or decline it",
        ),
    )
    def notes_propose(request: Request, pipeline_id: str, body: NoteFields) -> Row:  # pyright: ignore[reportUnusedFunction]
        """Propose a note: it needs why and evidence, reaches no turn until accepted, and wakes the manager."""
        who: Any = request.state.identity
        auth.domain(who)
        data = fields(body)

        def work(c: Any) -> Row:
            return N.propose(c, who.actor, pipeline_id, data.get("stage"), data)

        return mutate(request, body, work)

    @app.post(
        "/api/v2/pipeline-notes/{note_id}",
        **api_v2("Pipelines", "editPipelineNote", "A person changes a note in force, with a line on what and why"),
    )
    def notes_edit(request: Request, note_id: str, body: NoteEdit) -> Row:  # pyright: ignore[reportUnusedFunction]
        """Change a note in force: only the fields sent, with `line` (required) on what changed and why."""
        who: Any = request.state.identity
        auth.domain(who)
        data = fields(body)

        def work(c: Any) -> Row:
            return N.edit(c, who.actor, note_id, data, data.get("line", ""), body.revision)

        return mutate(request, body, work)

    @app.post(
        "/api/v2/pipeline-notes/{note_id}/retire",
        **api_v2("Pipelines", "retirePipelineNote", "A person retires a note in force, saying why"),
    )
    def notes_retire(request: Request, note_id: str, body: NoteDecide) -> Row:  # pyright: ignore[reportUnusedFunction]
        """Retire a note: it reaches no turn and stays readable with its lines."""
        who: Any = request.state.identity
        auth.domain(who)

        def work(c: Any) -> Row:
            return N.retire(c, who.actor, note_id, body.reason)

        return mutate(request, body, work)

    @app.post(
        "/api/v2/pipeline-notes/{note_id}/accept",
        **api_v2("Pipelines", "acceptPipelineNote", "Accept a proposed note, with a reason the proposer receives"),
    )
    def notes_accept(request: Request, note_id: str, body: NoteDecide) -> Row:  # pyright: ignore[reportUnusedFunction]
        who: Any = request.state.identity
        auth.domain(who)

        def work(c: Any) -> Row:
            return N.decide(c, who.actor, note_id, True, body.reason)

        return mutate(request, body, work)

    @app.post(
        "/api/v2/pipeline-notes/{note_id}/decline",
        **api_v2("Pipelines", "declinePipelineNote", "Decline a proposed note, with a reason the proposer receives"),
    )
    def notes_decline(request: Request, note_id: str, body: NoteDecide) -> Row:  # pyright: ignore[reportUnusedFunction]
        who: Any = request.state.identity
        auth.domain(who)

        def work(c: Any) -> Row:
            return N.decide(c, who.actor, note_id, False, body.reason)

        return mutate(request, body, work)
