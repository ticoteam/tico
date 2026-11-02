"""Cloud coding agents on the Recruit module's Cloud agents tab (ui/app/cloud-agents.js).

Tico connects the agents marked supported: a person adds one and it becomes a Tico bot on the harness it runs on, placed
under the line the person picks, or in the seat's place. Any other agent is a request. Requesting one adds a Recruiting
seat for it (backend/lifecycle.py) that holds its place on the chart, and files the request with Tico's team through the
support path (backend/support.py), so the team builds the connection. The first task a person gives the agent waits on
the seat, as a task they own, until the agent connects. Cancelling a request tells Tico, declines that task when the
person may update it, and closes the seat.

A request moves its seat through request_state: `filing` while Tico is being told (set in the transaction that makes the
seat and its first task, before Tico is called), `requested` once Tico's ticket is kept, `cancelling` while a cancel is
told to Tico, and `cancelled` when it is finished. lifecycle.refuse_if_requested keeps every change to the seat out of
those states.

Nothing here runs an agent or sends it work: a seat holds no work (backend/lifecycle.py).
"""
import time
import uuid
from typing import Annotated

from pydantic import Field

from . import groups as Groups
from . import hubdb as H
from . import lifecycle
from . import people as P
from . import task_privacy as privacy
from .models import BotDefinitionCreate, Contract
from .providers import MODEL_CATALOG
from . import recruiting
from .recruiting import end_draft
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


OTHER = "other"                                                      # an agent not on the list: typed by the person
STEPS = (lifecycle.REQUESTED, "building", "ready")                   # the stepper on the seat's Connecting page
NAME_MAX = 120                                                       # an `other` name becomes the seat's title
FIRST_TASK_MAX = 8000
TASK_TITLE_MAX = 200
FILING_WAIT_SECONDS = 30                                             # how long a repeat of a request in flight waits


class CloudAgentRequest(Contract):
    team: Annotated[str, Field(max_length=100)] = ""
    reports_to: Annotated[str, Field(max_length=200)] = ""
    first_task: Annotated[str, Field(max_length=FIRST_TASK_MAX)] = ""
    name: Annotated[str, Field(max_length=NAME_MAX)] = ""     # `other` only


class SeatAgentRequest(Contract):
    agent: Annotated[str, Field(min_length=1, max_length=40)]
    first_task: Annotated[str, Field(max_length=FIRST_TASK_MAX)] = ""
    name: Annotated[str, Field(max_length=NAME_MAX)] = ""     # `other` only


SEATLESS_TITLE = "Software engineer"   # the seat an add with no seat opens: every connected agent is a coding agent


class CloudAgentAdd(Contract):
    reports_to: Annotated[str, Field(max_length=200)] = ""   # the line the bot goes under, when no seat is named
    seat_id: Annotated[str, Field(max_length=200)] = ""      # the seat the bot fills
    team: Annotated[str, Field(max_length=100)] = ""         # the team the bot joins, when no seat is named


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


def _first_task(c, who, text):
    """The seat's waiting task, made for real inside the reservation's transaction: an open task with the same first line
    is refused here, before Tico hears of the request. If Tico is not reached, the reservation is released and the task
    declined with it."""
    if not text:
        return ""
    try:
        return H.task_create(c, who.actor, _task_title(text), text, who.actor, deduplicate=False)["id"]
    except H.Refused as exc:
        if exc.rule == "duplicate":
            raise Problem("duplicate", "You already have an open task with this first line. Start the agent on a "
                          "different one.", 409) from None
        raise Problem(exc.rule, exc.detail, 422) from None


def _retire_task(c, actor, task_id, note):
    """Decline a task the seat was waiting on, as `actor`, when the task's own rules let `actor` update it. Otherwise the
    task stays with its owner: the seat lets go of it either way."""
    task = H.task(c, task_id)
    if not task or task["status"] not in H.ACTIVE_STATUSES:
        return
    c.execute("SAVEPOINT cloud_task")
    try:
        H.task_update(c, actor, task_id, status="declined", note=note)
    except H.Refused:
        c.execute("ROLLBACK TO cloud_task")
    finally:
        c.execute("RELEASE cloud_task")


def _reserve_in(c, auth, who, agent, digested, key, seat_id, reports_to, team, first_task):
    """Inside the write transaction: check the request, make its seat and first task, mark the seat as being filed, and
    record the reservation under the key. Nothing else can take, move, close or fill the seat from here until it is kept
    or released, since every such change refuses a seat that is filing."""
    if seat_id:
        _recover_stale(c, seat_id)
        row = lifecycle.seat(c, seat_id)
        row = lifecycle.refuse_if_requested(row, c)
        placed = lifecycle.placement(auth, c, who, row["reports_to"], seat_id)
        joined = _joins(c, row, placed)
        if joined:
            Groups.may_add(c, auth, who, joined)        # the agent joins this team when it connects
        kept, made = seat_id, 0
    else:
        placed = lifecycle.placement(auth, c, who, reports_to)
        joined = _joins(c, {"team": team}, placed)
        if joined:
            Groups.may_add(c, auth, who, joined)        # a new seat the agent joins this team from
        kept = lifecycle.create_seat(auth, c, who, agent["name"], "", placed, team)["id"]
        made = 1
    waiting = _first_task(c, who, first_task)
    c.execute("UPDATE seats SET agent=?,agent_name=?,request_state=?,first_task=? WHERE id=?",
              (agent["id"], agent["name"], lifecycle.FILING, waiting, kept))
    reservation = uuid.uuid4().hex
    c.execute("INSERT INTO seat_requests(id,actor,request_key,request_hash,seat_id,state,created_at,made) "
              "VALUES(?,?,?,?,?,?,?,?)", (reservation, who.actor, key, digested, kept, "filing", H.now(), made))
    return reservation


def _reserve(store, auth, who, agent, key, digested, seat_id, reports_to, team, first_task):
    """Reserve the request under its key before Tico hears of it. A repeat of a request in flight waits for it and gets the
    seat it made; a repeat of a finished one gets that seat again. Returns (True, seat) for a finished request, else
    (False, the reservation's id)."""
    recover(store, seat_id)
    deadline = time.monotonic() + FILING_WAIT_SECONDS
    while True:
        with store.transaction() as c:
            row = c.execute("SELECT * FROM seat_requests WHERE actor=? AND request_key=?", (who.actor, key)).fetchone()
            if row and row["request_hash"] != digested:
                raise Problem("idempotency_conflict", "This key was used for different content", 409)
            if row and row["state"] == "filed":
                return True, lifecycle.seat_record(c, row["seat_id"])
            if row and row["created_at"] < H.shift(H.now(), seconds=-lifecycle.FILING_STALE_SECONDS):
                _release(c, who.actor, row)      # its first try never came back: this one takes the key
                continue                         # committed on its own, so a refusal of the retry cannot undo it
            if not row:
                return False, _reserve_in(c, auth, who, agent, digested, key, seat_id, reports_to, team, first_task)
        if time.monotonic() > deadline:
            raise Problem("requesting", "This request is still being filed. Try again in a moment.", 409, retryable=True)
        time.sleep(0.2)


def _release(c, actor, row):
    """Undo a reservation Tico never kept: the seat goes back as it was (a seat the request made is closed), its first task
    is declined by `actor` if the task's rules let them, else it stays with its owner, and the key is free again."""
    seat = c.execute("SELECT * FROM seats WHERE id=?", (row["seat_id"],)).fetchone() if row["seat_id"] else None
    if seat is not None and seat["request_state"] == lifecycle.FILING:
        if seat["first_task"]:
            _retire_task(c, actor, seat["first_task"], "Not filed: the cloud agent request did not reach Tico")
        c.execute("UPDATE seats SET request_state='',agent='',agent_name='',first_task='' WHERE id=?", (seat["id"],))
        if row["made"]:
            c.execute("UPDATE seats SET closed_at=?,closed_by=? WHERE id=?", (H.now(), actor, seat["id"]))
            end_draft(c, seat, "The seat was taken off the org chart")
        H.event(c, actor, "seat.request_released", seat["id"], {})
    c.execute("DELETE FROM seat_requests WHERE id=?", (row["id"],))


def _recover_stale(c, seat_id=""):
    """Inside a write transaction: recover what a process that died mid-request left behind, once it is older than any live
    request's window. A filing reservation is released, so its seat goes back to empty, and a cancelling seat goes back to
    requested, so its person can try again. Only `seat_id` is looked at when it is named, so a guarded action recovers the
    seat it touches without waiting for a restart."""
    stale = H.shift(H.now(), seconds=-lifecycle.FILING_STALE_SECONDS)
    scope = " AND seat_id=?" if seat_id else ""
    for row in c.execute("SELECT * FROM seat_requests WHERE state='filing' AND created_at<?" + scope,
                         (stale, seat_id) if seat_id else (stale,)).fetchall():
        _release(c, row["actor"], row)
    scope = " AND id=?" if seat_id else ""
    c.execute("UPDATE seats SET request_state=? WHERE request_state=? AND updated_at<?" + scope,
              (lifecycle.REQUESTED, lifecycle.CANCELLING, stale) + ((seat_id,) if seat_id else ()))


def recover(store, seat_id):
    """Recover the seat's stale request in a transaction of its own, so it holds even when the action that follows is
    refused and rolls its own transaction back."""
    if seat_id:
        with store.transaction() as c:
            _recover_stale(c, seat_id)


def release_abandoned(store):
    """At start: release what a process that died mid-request left behind (see _recover_stale)."""
    with store.transaction() as c:
        _recover_stale(c)


def _request(request, store, auth, settings, who, agent_id, *, name="", first_task="", team="", reports_to="",
             seat_id=""):
    """File the request with Tico, then keep the seat in the transaction that keeps Tico's ticket. The request is reserved
    under its Idempotency-Key first, so two identical requests file once, and a request that fails releases what it
    reserved."""
    require_person(who)
    require_on(settings)
    agent = agent_for(agent_id, name)
    first_task = str(first_task or "").strip()
    key = request.headers.get("idempotency-key") or ""
    if not key or len(key) > 200:
        raise Problem("idempotency_key", "Provide an Idempotency-Key of 1–200 characters", 422)
    digested = digest(encode({"agent": agent["id"], "name": agent["name"], "first_task": first_task, "team": team,
                              "reports_to": reports_to, "seat": seat_id}))
    done, value = _reserve(store, auth, who, agent, key, digested, seat_id, reports_to, team, first_task)
    if done:
        return value
    reservation = value

    def keep(c, ticket_id):
        """Inside the transaction that keeps Tico's ticket: the reserved seat becomes requested. Tico has accepted the
        request by now, so it is kept even if the person lost their place meanwhile (an owner can still cancel it): a
        refusal here would leave a request at Tico that this install could no longer track or cancel."""
        held = c.execute("SELECT * FROM seat_requests WHERE id=? AND state='filing'", (reservation,)).fetchone()
        if not held:
            raise Problem("filing", "This request was released before Tico answered", 409)
        row = lifecycle.seat(c, held["seat_id"])
        if row["request_state"] != lifecycle.FILING:
            raise Problem("filing", "This request was released before Tico answered", 409)
        c.execute("UPDATE seats SET request_state=?,request_ticket=? WHERE id=?", (STEPS[0], ticket_id, row["id"]))
        c.execute("UPDATE seat_requests SET state='filed',ticket_id=? WHERE id=?", (ticket_id, reservation))
        H.event(c, who.actor, "seat.requested", row["id"], {"agent": agent["id"], "ticket": ticket_id})

    try:
        # The ticket goes without the request's key: the reservation above is the key's record.
        request.app.state.support.file(who, SupportTicket(message=_ticket_message(agent), kind="feedback",
                                                          include_ids=False), "", on_filed=keep)
    except BaseException:
        with store.transaction() as c:
            held = c.execute("SELECT * FROM seat_requests WHERE id=?", (reservation,)).fetchone()
            if held and held["state"] == "filing":
                _release(c, who.actor, held)
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
    return {**lifecycle.seat_record(c, seat_id), "closed": True, "request_state": lifecycle.CANCELLED}


def _busy(state):
    if state == lifecycle.FILING:
        return Problem("filing", "A cloud agent request for this seat is being filed. Try again in a moment.", 409,
                       retryable=True)
    return Problem("cancelling", "This seat's cloud agent request is being cancelled. Try again in a moment.", 409,
                   retryable=True)


def _finish_cancel(c, who, row, name):
    """Inside the transaction after Tico was told: the seat is cancelled and closed, and the first task is let go. It is
    declined when the caller may update it, else it stays with its owner. Nothing here checks the caller's line again: the
    seat has been `cancelling` since before Tico was told, so it cannot have moved."""
    seat_id = row["id"]
    if row["first_task"]:
        _retire_task(c, who.actor, row["first_task"], f"Cancelled: the {name} request was withdrawn")
    c.execute("UPDATE seats SET request_state=?,first_task='' WHERE id=?", (lifecycle.CANCELLED, seat_id))
    if row["closed_at"] is None:
        c.execute("UPDATE seats SET closed_at=?,closed_by=? WHERE id=?", (H.now(), who.actor, seat_id))
        end_draft(c, row, "The seat was taken off the org chart")
    H.event(c, who.actor, "seat.request_cancelled", seat_id, {"ticket": row["request_ticket"]})


def cancel_request(request, store, auth, settings, who, seat_id):
    """Withdraw a seat's cloud agent request. The seat is marked cancelling first, so nothing changes it while Tico is
    told on the request's ticket. Then it closes and the first task is let go. If Tico cannot be reached, or the last step
    fails, the request is requested again and the person can try once more. A repeat answers the same."""
    require_person(who)
    require_on(settings)
    recover(store, seat_id)
    with store.transaction() as c:
        row = _requested(c, seat_id)
        if row["request_state"] == lifecycle.CANCELLED:
            return _cancelled(c, seat_id)
        if row["request_state"] in (lifecycle.FILING, lifecycle.CANCELLING):
            raise _busy(row["request_state"])
        lifecycle.may_place_seat(auth, c, who, row)
        ticket = (c.execute("SELECT * FROM support_tickets WHERE id=?", (row["request_ticket"],)).fetchone()
                  if row["request_ticket"] else None)
        name = _agent_of(row)["name"]
        c.execute("UPDATE seats SET request_state=?,updated_at=? WHERE id=?", (lifecycle.CANCELLING, H.now(), seat_id))
    try:
        if ticket is not None:
            request.app.state.support.note(ticket, f"Cancelled: the {name} request was withdrawn. Nothing more is needed.")
        with store.transaction() as c:
            _finish_cancel(c, who, _requested(c, seat_id), name)
            return _cancelled(c, seat_id)
    except BaseException:
        with store.transaction() as c:
            c.execute("UPDATE seats SET request_state=? WHERE id=? AND request_state=?",
                      (lifecycle.REQUESTED, seat_id, lifecycle.CANCELLING))
        raise


def _joins(c, row, placed):
    """The team an added bot ends up in: the seat's, else the one its manager bot is in, which a bot with no team of its
    own takes (people.team_of). Either way it is an add to that team, so the team's rule is asked of it."""
    if row["team"]:
        return row["team"]
    parent = lifecycle.bot_parent(c, placed)
    return (P.team_of(parent, Groups._configs(c), Groups._roster(c)) or "") if parent else ""


def _add_bot(c, auth, onboarding, settings_admin, who, agent, body):
    """Add the agent as a Tico bot that fills the seat it names, on the harness the agent runs on, under the line the seat
    names. Its role is the template Recruit ranks first for the seat, and it lands exactly where that template's hire does:
    in Onboarding, with its repository built by its computer or by BotOps (recruiting.onboard). An add with no seat opens
    one first, a software engineer's in `team` under `reports_to`, named for the agent; it is in the same transaction, so a
    refusal below leaves no seat behind."""
    from .onboarding import BOTOPS
    seat_id, name = body.seat_id, ""
    if not seat_id:
        team = body.team.strip()
        if team:
            Groups.may_add(c, auth, who, team)      # a new seat in this team, which the bot joins
        placed = lifecycle.placement(auth, c, who, body.reports_to)
        seat_id = lifecycle.create_seat(auth, c, who, SEATLESS_TITLE, agent["line"], placed, team)["id"]
        name = agent["name"]
    _recover_stale(c, seat_id)
    row = lifecycle.seat(c, seat_id)
    row = lifecycle.refuse_if_requested(row, c)
    placed = lifecycle.placement(auth, c, who, row["reports_to"], seat_id)
    if str(row["reports_to"] or "").startswith(lifecycle.SEAT_PREFIX):
        # A bot cannot report to a seat, and filling the seat above later would leave this bot under the wrong manager.
        raise Problem("manager_seat", "Fill the seat this one reports to first", 409)
    team = _joins(c, row, placed)
    if team:
        Groups.may_add(c, auth, who, team)          # the bot joins this team: the rule every add to a team obeys
    card = recruiting.top_card(auth, onboarding, c, who, row)
    if not card:
        raise Problem("template", "No Tico template fits this seat, so there is no repository to build the bot in. Hire "
                      "from Recruit instead.", 409)
    if not card["starter"] and not H.bot(c, BOTOPS):
        raise Problem("botops", "BotOps builds this bot, and this team has no BotOps; add it from Templates first", 409)
    model = _model_for(agent["connect_id"])
    name = (name or row["title"])[:100]
    slug = onboarding.admin.pick_slug(c, name)
    record, rendered, description, instructions = recruiting.brief(c, onboarding, card, row)
    settings_admin.create_bot(c, who, BotDefinitionCreate(
        slug=slug, display_name=name, description=description[:2000], status="planned", repo="bot-" + slug,
        thread_mode="personal", reports_to=lifecycle.bot_parent(c, placed) or None, shared=bool(card.get("shared")),
        template=card["template"], model=model["id"], harness=agent["connect_id"], owners=[H.actor_id(who.actor)]))
    setup_task = recruiting.onboard(c, who, onboarding, row, card, slug, name=name, description=description,
                                    instructions=instructions, rendered=rendered, answers=record["answers"],
                                    model=model["id"])
    if team:
        Groups._set_bot(c, slug, team)              # the team's group is the bot's own, as a team build's hire sets it
    return {"bot": slug, "harness": agent["connect_id"], "model": model["id"], "seat": seat_id,
            "template": card["template"], "setup_task_id": setup_task}


def _seat_agent(c, who, row):
    return {"seat": lifecycle.seat_view(c, row["id"]), "agent": _agent_of(row), "steps": list(STEPS),
            "tasks": _waiting(c, who, row)}


def install(app, store, auth, settings, mutate, settings_admin, onboarding):
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
        recover(store, body.seat_id)
        return mutate(request, body, lambda c: _add_bot(c, auth, onboarding, settings_admin, who, agent, body))

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
        """Withdraw a seat's cloud agent request: Tico hears of it, the seat closes and its first task is let go."""
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
