"""Cloud coding agents on the Recruit module's Cloud agents tab (ui/app/cloud-agents.js).

Tico connects the agents marked supported: a person adds them directly and they join the team as bots. Any other agent
is a request. Requesting one adds a Recruiting seat for it (backend/lifecycle.py) that holds its place on the chart, and
files the request with Tico's team through the support path (backend/support.py), so the team builds the connection.
The first task a person gives the agent waits on the seat, as a task they own, until the agent connects.

Nothing here connects to an agent, runs one or sends it work: a seat holds no work (backend/lifecycle.py).
"""
from typing import Annotated

from pydantic import Field

from . import hubdb as H
from . import lifecycle
from . import task_privacy as privacy
from .models import Contract
from .store import Problem
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
OTHER = "other"                              # an agent not on the list: its name or website, typed by the person
STEPS = ("requested", "building", "ready")   # the stepper on the seat's Connecting page
NAME_MAX = 120                               # an `other` name becomes the seat's title
FIRST_TASK_MAX = 8000
TASK_TITLE_MAX = 200


class CloudAgentRequest(Contract):
    team: Annotated[str, Field(max_length=100)] = ""
    reports_to: Annotated[str, Field(max_length=200)] = ""
    first_task: Annotated[str, Field(max_length=FIRST_TASK_MAX)] = ""
    name: Annotated[str, Field(max_length=NAME_MAX)] = ""     # `other` only


class SeatAgentRequest(Contract):
    agent: Annotated[str, Field(min_length=1, max_length=40)]
    first_task: Annotated[str, Field(max_length=FIRST_TASK_MAX)] = ""
    name: Annotated[str, Field(max_length=NAME_MAX)] = ""     # `other` only


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


def _seat_for_ticket(c, ticket_id):
    row = c.execute("SELECT id FROM seats WHERE request_ticket=?", (ticket_id,)).fetchone()
    return lifecycle.seat_view(c, row["id"]) if row else None


def _waiting(c, who, row):
    """The first task, when the caller could read it anyway: the task is never shown past its own visibility."""
    if not row["first_task"]:
        return []
    task = H.task(c, row["first_task"])
    if not task or not privacy.task_readable(c, who, task):
        return []
    return [{"id": task["id"], "title": task["title"], "status": task["status"]}]


def _request(request, store, auth, settings, who, agent_id, *, name="", first_task="", team="", reports_to="",
             seat_id=""):
    """File the request with Tico, then keep the seat and the first task in the same transaction as the ticket that
    asked for them. Everything that can refuse is checked before Tico hears of it, so no ticket is filed for a seat
    that is not kept. A repeated Idempotency-Key gets the seat its first request made."""
    require_person(who)
    require_on(settings)
    agent = agent_for(agent_id, name)
    first_task = str(first_task or "").strip()
    support = request.app.state.support
    key = (request.headers.get("idempotency-key") or "")[:200]
    with store.read() as c:
        if key:
            # support.file answers a repeated key with the ticket it filed first, without sending it again.
            filed = c.execute("SELECT id FROM support_tickets WHERE actor=? AND request_key=?", (who.actor, key)).fetchone()
            if filed:
                seat = _seat_for_ticket(c, filed["id"])
                if seat is None:
                    raise Problem("idempotency", "That Idempotency-Key was used for another request", 409)
                return seat
        if seat_id:
            row = lifecycle.seat(c, seat_id)
            if row["request_state"]:
                raise Problem("requested", "A cloud agent is already requested for this seat", 409)
            placed = lifecycle.placement(auth, c, who, row["reports_to"], seat_id)
        else:
            placed = lifecycle.placement(auth, c, who, reports_to)

    def keep(c, ticket_id):
        if seat_id:
            row = lifecycle.seat(c, seat_id)
            if row["request_state"]:
                raise Problem("requested", "A cloud agent is already requested for this seat", 409)
            kept = seat_id
        else:
            kept = lifecycle.create_seat(auth, c, who, agent["name"], "", placed, team)["id"]
        waiting = H.task_create(c, who.actor, _task_title(first_task), first_task, who.actor,
                                deduplicate=False)["id"] if first_task else ""
        c.execute("UPDATE seats SET agent=?,agent_name=?,request_state=?,request_ticket=?,first_task=? WHERE id=?",
                  (agent["id"], agent["name"], STEPS[0], ticket_id, waiting, kept))
        H.event(c, who.actor, "seat.requested", kept, {"agent": agent["id"], "ticket": ticket_id})

    ticket = support.file(who, SupportTicket(message=_ticket_message(agent), kind="feedback", include_ids=False),
                          key, on_filed=keep)
    with store.read() as c:
        return _seat_for_ticket(c, ticket["id"])


def install(app, store, auth, settings):
    from fastapi import Request

    @app.get("/api/v2/cloud-agents")
    def cloud_agents(request: Request):
        """The cloud coding agents: the supported ones are added to the team, the others are requested from Tico."""
        auth.domain(request.state.identity)
        return {"agents": [dict(agent) for agent in AGENTS]}

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

    @app.get("/api/v2/seats/{seat_id}/request")
    def seat_agent_request(request: Request, seat_id: str):
        """A seat's cloud agent: where its request stands and the first task waiting for the agent."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            row = lifecycle.seat(c, seat_id)
            if not row["agent"]:
                raise Problem("not_found", "No cloud agent is requested for this seat", 404)
            return {"seat": lifecycle.seat_view(c, seat_id), "agent": _agent_of(row), "steps": list(STEPS),
                    "tasks": _waiting(c, who, row)}
