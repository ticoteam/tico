"""Cloud coding agents on the Recruit module's Cloud agents tab (ui/app/cloud-agents.js).

Tico connects the agents marked supported: a person adds one and it becomes a Tico bot on the harness it runs on, placed
under the line the person picks, or in the seat's place. Any other agent is a request. Requesting one adds a Recruiting
seat for it (backend/lifecycle.py) that holds its place on the chart, and files the request with Tico's team through the
support path (backend/support.py), so the team builds the connection. The first task a person gives the agent waits on
the seat, as a task they own, until the agent connects. Cancelling a request tells Tico and declines that task.

Nothing here runs an agent or sends it work: a seat holds no work (backend/lifecycle.py).
"""
import time
import uuid
from typing import Annotated

from pydantic import Field

from . import hubdb as H
from . import lifecycle
from . import task_privacy as privacy
from .models import BotDefinitionCreate, Contract
from .providers import MODEL_CATALOG
from .store import Problem, digest, encode
from .support import CONTROL, SupportTicket, require_on, require_person

AGENTS = (
    {"id": "claude", "name": "Claude Code", "maker": "Anthropic", "site": "claude.com",
     "line": "Codes in your repo on a Tico computer or in its cloud", "supported": True, "connect_id": "claude"},
    {"id": "codex", "name": "Codex", "maker": "OpenAI", "site": "openai.com",
     "line": "Takes a task, works in a sandbox, opens a PR", "supported": True, "connect_id": "codex"},
    {"id": "devin", "name": "Devin", "maker": "Cognition", "site": "devin.ai",
     "line": "Works in its own VM end to end, opens PRs", "supported": False, "connect_id": None},
    {"id": "droids", "name": "Droids", "maker": "Factory", "site": "factory.ai",
     "line": "Turns tickets into PRs; reviews and refactors", "supported": False, "connect_id": None},
    {"id": "jules", "name": "Jules", "maker": "Google", "site": "jules.google",
     "line": "Works on a repo in a cloud VM, opens PRs", "supported": False, "connect_id": None},
    {"id": "copilot", "name": "Copilot coding agent", "maker": "GitHub", "site": "github.com",
     "line": "Assign it an issue, it opens a PR", "supported": False, "connect_id": None},
    {"id": "background", "name": "Background agents", "maker": "Cursor", "site": "cursor.com",
     "line": "Runs coding tasks in the cloud from a prompt", "supported": False, "connect_id": None},
    {"id": "openhands", "name": "OpenHands", "maker": "All Hands AI", "site": "all-hands.dev",
     "line": "Open source; runs in its cloud or yours", "supported": False, "connect_id": None},
)
BY_ID = {agent["id"]: agent for agent in AGENTS}


def listing():
    """The catalog as the route serves it, as copies: a caller that changes one cannot change the agents Tico lists."""
    return [dict(agent) for agent in AGENTS]


OTHER = "other"                              # an agent not on the list: its name or website, typed by the person
STEPS = ("requested", "building", "ready")   # the stepper on the seat's Connecting page
CANCELLED = "cancelled"                      # a seat's request_state once the person withdrew it
NAME_MAX = 120                               # an `other` name becomes the seat's title
FIRST_TASK_MAX = 8000
TASK_TITLE_MAX = 200
FILING_WAIT_SECONDS = 30                     # how long a repeat of a request in flight waits for its answer


class CloudAgentRequest(Contract):
    team: Annotated[str, Field(max_length=100)] = ""
    reports_to: Annotated[str, Field(max_length=200)] = ""
    first_task: Annotated[str, Field(max_length=FIRST_TASK_MAX)] = ""
    name: Annotated[str, Field(max_length=NAME_MAX)] = ""     # `other` only


class SeatAgentRequest(Contract):
    agent: Annotated[str, Field(min_length=1, max_length=40)]
    first_task: Annotated[str, Field(max_length=FIRST_TASK_MAX)] = ""
    name: Annotated[str, Field(max_length=NAME_MAX)] = ""     # `other` only


class CloudAgentAdd(Contract):
    reports_to: Annotated[str, Field(max_length=200)] = ""   # the line the bot goes under, when no seat is named
    seat_id: Annotated[str, Field(max_length=200)] = ""      # the seat the bot fills


def _other(name):
    return {"id": OTHER, "name": name, "maker": "", "site": "", "line": "", "supported": False, "connect_id": None}


def agent_for(agent_id, name=""):
    """The catalog's agent, or the one the person named for `other`. A supported agent is added, not requested."""
    if agent_id == OTHER:
        name = " ".join(CONTROL.sub(" ", str(name or "")).split())[:NAME_MAX]
        if not name:
            raise Problem("name", "Name the agent or give its website", 422)
        return _other(name)
    agent = BY_ID.get(agent_id)
    if agent is None:
        raise Problem("agent", "Pick a cloud agent from the list", 422)
    if agent["supported"]:
        raise Problem("supported", f"{agent['name']} connects directly, so add it instead of requesting it", 422)
    return dict(agent)


def addable(agent_id):
    """The supported agent a person adds as a bot. Any other agent is requested instead."""
    agent = BY_ID.get(agent_id)
    if agent is None or not agent["supported"]:
        raise Problem("supported", "Tico does not connect this agent yet: request it instead", 422)
    return dict(agent)


def _agent_of(row):
    """The agent a seat asked for, as the catalog lists it (or the name typed for `other`)."""
    agent = BY_ID.get(row["agent"])
    return dict(agent) if agent else _other(row["agent_name"])


def _ticket_message(agent):
    if agent["id"] == OTHER:
        return f"Cloud agent request: {agent['name']}"
    return f"Cloud agent request: {agent['name']} by {agent['maker']} ({agent['site']})"


def _task_title(text):
    """The first line of the first task, as its title."""
    return " ".join(text.splitlines()[0].split())[:TASK_TITLE_MAX]


def _waiting(c, who, row):
    """The first task, when the caller could read it anyway: the task is never shown past its own visibility."""
    if not row["first_task"]:
        return []
    task = H.task(c, row["first_task"])
    if not task or not privacy.task_readable(c, who, task):
        return []
    return [{"id": task["id"], "title": task["title"], "status": task["status"]}]


def _model_for(harness):
    """The current model that runs this harness: the one Tico recommends for it, else its first current one."""
    runs = [m for m in MODEL_CATALOG if m.get("runtime") == harness and m.get("provider") and not m.get("deprecated")]
    if not runs:
        raise Problem("harness", f"No current model runs {harness}", 409)
    return next((m for m in runs if m.get("recommended")), runs[0])


def _first_task_check(c, who, first_task):
    """Refuse what Tico's task rules would refuse for the first task, before Tico hears of the request. The task is made
    and undone inside a savepoint, so the same rules decide it as decide the real one (H.task_create)."""
    c.execute("SAVEPOINT cloud_first_task")
    try:
        H.task_create(c, who.actor, _task_title(first_task), first_task, who.actor, deduplicate=False)
    except H.Refused as exc:
        if exc.rule == "duplicate":
            raise Problem("duplicate", "You already have an open task with this first line. Start the agent on a "
                          "different one.", 409) from None
        raise Problem(exc.rule, exc.detail, 422) from None
    finally:
        c.execute("ROLLBACK TO cloud_first_task")
        c.execute("RELEASE cloud_first_task")


def _check(c, auth, who, seat_id, reports_to, first_task):
    """What Tico would refuse once the request is kept, refused before Tico hears of it."""
    if seat_id:
        row = lifecycle.seat(c, seat_id)
        if row["request_state"]:
            raise Problem("requested", "A cloud agent is already requested for this seat", 409)
        if lifecycle.filing(c, seat_id):
            raise Problem("filing", "A cloud agent is already being requested for this seat", 409, retryable=True)
        lifecycle.placement(auth, c, who, row["reports_to"], seat_id)
    else:
        lifecycle.placement(auth, c, who, reports_to)
    if first_task:
        _first_task_check(c, who, first_task)


def _reserve(store, auth, who, key, digested, seat_id, reports_to, first_task):
    """Reserve the request under its key in the same write transaction that checks it, before Tico hears of it. A repeat
    of a request in flight waits for it and gets the seat it made; a repeat of a finished one gets that seat again.
    Returns (True, seat) for a finished request, else (False, the reservation's id)."""
    deadline = time.monotonic() + FILING_WAIT_SECONDS
    while True:
        with store.transaction() as c:
            row = c.execute("SELECT * FROM seat_requests WHERE actor=? AND request_key=?", (who.actor, key)).fetchone()
            if row and row["request_hash"] != digested:
                raise Problem("idempotency_conflict", "This key was used for different content", 409)
            if row and row["state"] == "filed":
                return True, lifecycle.seat_record(c, row["seat_id"])
            fresh = bool(row) and row["created"] >= H.shift(H.now(), seconds=-lifecycle.FILING_STALE_SECONDS)
            if not fresh:
                _check(c, auth, who, seat_id, reports_to, first_task)
                now = H.now()
                if row:                       # its first try never came back: this one takes the key
                    c.execute("UPDATE seat_requests SET created=? WHERE id=?", (now, row["id"]))
                    return False, row["id"]
                reservation = uuid.uuid4().hex
                c.execute("INSERT INTO seat_requests(id,actor,request_key,request_hash,seat_id,state,created) "
                          "VALUES(?,?,?,?,?,?,?)", (reservation, who.actor, key, digested, seat_id, "filing", now))
                return False, reservation
        if time.monotonic() > deadline:
            raise Problem("requesting", "This request is still being filed. Try again in a moment.", 409, retryable=True)
        time.sleep(0.2)


def _request(request, store, auth, settings, who, agent_id, *, name="", first_task="", team="", reports_to="",
             seat_id=""):
    """File the request with Tico, then keep the seat and the first task in the transaction that keeps the ticket. The
    request is reserved under its Idempotency-Key first and checked before Tico hears of it, so no ticket is filed for a
    seat that is not kept, and two identical requests file once."""
    require_person(who)
    require_on(settings)
    agent = agent_for(agent_id, name)
    first_task = str(first_task or "").strip()
    key = request.headers.get("idempotency-key") or ""
    if not key or len(key) > 200:
        raise Problem("idempotency_key", "Provide an Idempotency-Key of 1–200 characters", 422)
    digested = digest(encode({"agent": agent["id"], "name": agent["name"], "first_task": first_task, "team": team,
                              "reports_to": reports_to, "seat": seat_id}))
    done, value = _reserve(store, auth, who, key, digested, seat_id, reports_to, first_task)
    if done:
        return value
    reservation = value

    def keep(c, ticket_id):
        """Inside the transaction that keeps Tico's ticket: the seat and its first task. The line is checked again, since
        the request was reserved before the ticket was sent and the line may have moved meanwhile."""
        if seat_id:
            row = lifecycle.seat(c, seat_id)
            if row["request_state"]:
                raise Problem("requested", "A cloud agent is already requested for this seat", 409)
            lifecycle.placement(auth, c, who, row["reports_to"], seat_id)
            kept = seat_id
        else:
            placed = lifecycle.placement(auth, c, who, reports_to)
            kept = lifecycle.create_seat(auth, c, who, agent["name"], "", placed, team)["id"]
        waiting = (H.task_create(c, who.actor, _task_title(first_task), first_task, who.actor,
                                 deduplicate=False)["id"] if first_task else "")
        c.execute("UPDATE seats SET agent=?,agent_name=?,request_state=?,request_ticket=?,first_task=? WHERE id=?",
                  (agent["id"], agent["name"], STEPS[0], ticket_id, waiting, kept))
        c.execute("UPDATE seat_requests SET state='filed',seat_id=?,ticket_id=? WHERE id=?", (kept, ticket_id, reservation))
        H.event(c, who.actor, "seat.requested", kept, {"agent": agent["id"], "ticket": ticket_id})

    try:
        # The ticket goes without the request's key: the reservation above is the key's record.
        request.app.state.support.file(who, SupportTicket(message=_ticket_message(agent), kind="feedback",
                                                          include_ids=False), "", on_filed=keep)
    except BaseException:
        with store.transaction() as c:      # nothing was kept, so the key may be used again
            c.execute("DELETE FROM seat_requests WHERE id=? AND state='filing'", (reservation,))
        raise
    with store.read() as c:
        row = c.execute("SELECT seat_id FROM seat_requests WHERE id=?", (reservation,)).fetchone()
        return lifecycle.seat_record(c, row["seat_id"])


def _requested(c, seat_id):
    """A seat that has a cloud agent request, open or cancelled. A closed one still answers, so a repeat cancel agrees."""
    row = c.execute("SELECT * FROM seats WHERE id=? AND agent<>''", (seat_id,)).fetchone()
    if not row:
        raise Problem("not_found", "No cloud agent is requested for this seat", 404)
    return row


def _cancelled(c, seat_id):
    return {**lifecycle.seat_record(c, seat_id), "closed": True, "request_state": CANCELLED}


def cancel_request(request, store, auth, settings, who, seat_id):
    """Withdraw a seat's cloud agent request. Tico hears of it on the request's ticket first, so a failure there changes
    nothing and the person can try again; then the seat closes and the first task is declined, since no agent will come
    for it. A repeat answers the same."""
    require_person(who)
    require_on(settings)
    with store.read() as c:
        row = _requested(c, seat_id)
        if row["request_state"] == CANCELLED:
            return _cancelled(c, seat_id)
        lifecycle.may_place_seat(auth, c, who, row)
        ticket = (c.execute("SELECT * FROM support_tickets WHERE id=?", (row["request_ticket"],)).fetchone()
                  if row["request_ticket"] else None)
        name = _agent_of(row)["name"]
    if ticket is not None:
        request.app.state.support.note(ticket, f"Cancelled: the {name} request was withdrawn. Nothing more is needed.")
    with store.transaction() as c:
        row = _requested(c, seat_id)
        if row["request_state"] == CANCELLED:
            return _cancelled(c, seat_id)
        lifecycle.may_place_seat(auth, c, who, row)     # checked again inside the write
        c.execute("UPDATE seats SET request_state=? WHERE id=?", (CANCELLED, seat_id))
        if row["closed_at"] is None:
            c.execute("UPDATE seats SET closed_at=?,closed_by=? WHERE id=?", (H.now(), who.actor, seat_id))
            from .recruiting import end_draft
            end_draft(c, row, "The seat was taken off the org chart")
        task = H.task(c, row["first_task"]) if row["first_task"] else None
        if task and task["status"] in H.ACTIVE_STATUSES:
            H.task_update(c, H.KEEPER, task["id"], status="declined",
                          note=f"Cancelled: the {name} request was withdrawn")
        H.event(c, who.actor, "seat.request_cancelled", seat_id, {"ticket": row["request_ticket"]})
        return _cancelled(c, seat_id)


def _add_bot(c, auth, settings_admin, who, agent, body):
    """Add the agent as a Tico bot through the bot editor's own create, so the same rules apply: who may add a bot, under
    whom, on which model. The bot runs on the agent's harness, under the line the seat names (or `reports_to`), and fills
    the seat when one is named."""
    model = _model_for(agent["connect_id"])
    if body.seat_id:
        row = lifecycle.seat(c, body.seat_id)
        if row["request_state"]:
            raise Problem("requested", "This seat has a cloud agent request: cancel it before adding an agent", 409)
        title, description = row["title"], row["description"]
        placed = lifecycle.placement(auth, c, who, row["reports_to"], body.seat_id)
    else:
        title, description = agent["name"], ""
        placed = lifecycle.placement(auth, c, who, body.reports_to)
    created = settings_admin.create_bot(c, who, BotDefinitionCreate(
        display_name=title[:100], description=str(description or "")[:2000],
        reports_to=lifecycle.bot_parent(c, placed) or None, status="planned", model=model["id"],
        harness=agent["connect_id"], owners=[H.actor_id(who.actor)]))
    if body.seat_id:
        lifecycle.fill_seat(c, who, body.seat_id, created["slug"])
    return {"bot": created["slug"], "harness": agent["connect_id"], "model": model["id"], "seat": body.seat_id}


def _seat_agent(c, who, row):
    return {"seat": lifecycle.seat_view(c, row["id"]), "agent": _agent_of(row), "steps": list(STEPS),
            "tasks": _waiting(c, who, row)}


def install(app, store, auth, settings, mutate, settings_admin):
    from fastapi import Request

    @app.get("/api/v2/cloud-agents")
    def cloud_agents(request: Request):
        """The cloud coding agents: the supported ones are added to the team, the others are requested from Tico."""
        auth.domain(request.state.identity)
        return {"agents": listing()}

    @app.post("/api/v2/cloud-agents/{agent_id}/add")
    def cloud_agent_add(request: Request, agent_id: str, body: CloudAgentAdd):
        """Add an agent Tico connects as a Tico bot on the harness it runs on, placed under `reports_to`, or in the seat's
        place when `seat_id` names one, which the bot then fills."""
        who = request.state.identity
        auth.domain(who)
        require_person(who)
        agent = addable(agent_id)
        return mutate(request, body, lambda c: _add_bot(c, auth, settings_admin, who, agent, body))

    @app.post("/api/v2/cloud-agents/{agent_id}/request")
    def cloud_agent_request(request: Request, agent_id: str, body: CloudAgentRequest):
        """Request an agent Tico does not connect yet: a seat for it goes on the chart now and Tico's team builds the
        connection. `other` takes the agent's name or website in `name`."""
        who = request.state.identity
        auth.domain(who)
        return _request(request, store, auth, settings, who, agent_id, name=body.name, first_task=body.first_task,
                        team=body.team, reports_to=body.reports_to)

    @app.post("/api/v2/seats/{seat_id}/request-agent")
    def seat_request_agent(request: Request, seat_id: str, body: SeatAgentRequest):
        """Request a cloud agent for a seat that has none. The seat keeps its role and holds its place until the agent
        connects."""
        who = request.state.identity
        auth.domain(who)
        return _request(request, store, auth, settings, who, body.agent, name=body.name, first_task=body.first_task,
                        seat_id=seat_id)

    @app.post("/api/v2/seats/{seat_id}/cancel-request")
    def seat_cancel_request(request: Request, seat_id: str):
        """Withdraw a seat's cloud agent request: Tico hears of it, the seat closes and its first task is declined."""
        who = request.state.identity
        auth.domain(who)
        return cancel_request(request, store, auth, settings, who, seat_id)

    @app.get("/api/v2/seats/{seat_id}/request")
    def seat_agent_request(request: Request, seat_id: str):
        """A seat's cloud agent: where its request stands and the first task waiting for the agent."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            row = lifecycle.seat(c, seat_id)
            if not row["agent"]:
                raise Problem("not_found", "No cloud agent is requested for this seat", 404)
            return _seat_agent(c, who, row)
