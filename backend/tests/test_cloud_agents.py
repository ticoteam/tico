"""Cloud agents (backend/cloud_agents.py): a seat asks Tico's team to connect an agent it does not connect yet, and a
supported agent is added as a Tico bot. A request is reserved under its key, checked before Tico hears of it, and kept in
the transaction that keeps its ticket; canceling it tells Tico and lets go of the first task.

The stories run related checks against ONE app each (one `environment()` per story, not per check). Each check starts
with the fake Tico reset (`_fresh`) and counts seats by what it changed (`_open_ids` before and after), since earlier
checks of the same story leave their seats behind. Checks that must start from nothing are first in their story."""

import json
import threading
import uuid
from pathlib import Path

import pytest

from backend import cloud_agents, lifecycle, support
from backend import hubdb as H
from backend.auth import Identity
from backend.store import Problem
from backend.tests.helpers.onboarding import as_person, environment, signed_in  # noqa: F401
from backend.tests.helpers.support import hq  # noqa: F401
from backend import bot_settings as BS

SHIPPED = Path(__file__).resolve().parents[2] / "templates" / "catalog"  # Recruit's candidates


def filed(hq):
    return [json.loads(r.content) for r in hq.seen if r.method == "POST" and r.url.path == "/v1/support"]


def seats(api):
    return api.get("/api/v2/seats", headers=signed_in()).json()["seats"]


def _open_ids(api):
    return [s["id"] for s in seats(api)]


def _fresh(hq):
    """Each check starts with Tico's fake as a new one would have it: nothing filed, nothing held, nothing down."""
    hq.seen.clear()
    hq.tickets.clear()
    hq.down = False


def ask(api, path, body, headers=None, expected=200):
    r = api.post("/api/v2/" + path, json=body, headers=headers or signed_in())
    assert r.status_code == expected, r.text
    return r.json()


def hold_call(monkeypatch, match, entered, release, during=None):
    """Hold the first call to Tico that `match(method, path)` picks until `release` is set; `during` runs while it is held."""
    real = support._ask_hq

    def held(method, path, **kwargs):
        if match(method, path) and not entered.is_set():
            entered.set()
            if during:
                during()
            release.wait(10)
        return real(method, path, **kwargs)

    monkeypatch.setattr(support, "_ask_hq", held)


def hold_tico(monkeypatch, entered, release, during=None):
    """Hold the first ticket sent to Tico until `release` is set; `during` runs while it is held."""
    hold_call(monkeypatch, lambda method, path: method == "POST" and path == "/v1/support", entered, release, during)


def bot_count(api):
    with api.app.state.store.read() as c:
        return c.execute("SELECT COUNT(*) FROM bot_config").fetchone()[0]


def bot_reports_to(api, bot):
    with api.app.state.store.read() as c:
        return c.execute("SELECT reports_to FROM bot_config WHERE bot=?", (bot,)).fetchone()[0]


def plant_stale_filing(api, seat_id, actor="dead-process", key=None, request_hash="x", made=0):
    stale = H.shift(H.now(), seconds=-(lifecycle.FILING_STALE_SECONDS + 1))
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE seats SET request_state=?,agent='devin',agent_name='Devin' WHERE id=?", (lifecycle.FILING, seat_id)
        )
        c.execute(
            "INSERT INTO seat_requests(id,actor,request_key,request_hash,seat_id,state,created_at,made) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, actor, key or "key-" + seat_id, request_hash, seat_id, "filing", stale, made),
        )


def onboarding_artifacts(api, bot):
    """What Onboarding leaves on a bot a seat is filled with: its state, its template, whether its computer builds its
    repository, and the BotOps setup task that builds it when the template is not a starter."""
    with api.app.state.store.read() as c:
        bot = H.resolve_bot(c, bot)["id"]  # the bot's id or its slug: config is keyed by the id
        state, config_json = c.execute(
            f"SELECT onboarding_state, {BS.CONFIG} AS config FROM bot_config bc {BS.JOIN} WHERE bot=?", (bot,)
        ).fetchone()
        config = json.loads(config_json)
        task = H.task(c, config["setup_task_id"]) if config.get("setup_task_id") else None
    return {
        "state": state,
        "template": config.get("template"),
        "materialize": bool(config.get("materialize")),
        "template_version": bool(config.get("template_version")),
        "setup": task["title"] if task else None,
    }


# --- checks: each takes (api, hq) and may rely on the story's earlier checks only where the story says so ---


def _a_supported_agent_is_added_not_requested_and_an_unknown_one_is_refused(api, hq):
    before = _open_ids(api)
    ask(api, "cloud-agents/claude/request", {}, expected=422)
    ask(api, "cloud-agents/codex/request", {}, expected=422)
    ask(api, "cloud-agents/nobody/request", {}, expected=422)
    assert hq.seen == [] and _open_ids(api) == before


def _requesting_an_agent_puts_a_seat_on_the_chart_and_files_one_ticket_without_ids(api, hq):
    seat = ask(api, "cloud-agents/devin/request", {"team": "engineering"})
    assert seat["title"] == "Devin" and seat["agent"] == "devin" and seat["agent_name"] == "Devin"
    assert seat["request_state"] == "requested" and seat["team"] == "engineering" and seat["stage"] == "recruiting"
    assert seat["id"] in [s["id"] for s in seats(api)]
    [ticket] = filed(hq)
    assert "Devin" in ticket["message"] and ticket["kind"] == "feedback"
    # The company's install and version stay at home: the request goes as the message, its kind and who filed it.
    assert set(ticket) == {"message", "kind", "source"} and ticket["source"] == "person"


def _the_first_task_waits_on_the_seat_as_a_task_its_person_owns_and_never_reaches_tico(api, hq):
    seat = ask(
        api, "cloud-agents/jules/request", {"first_task": "Port the billing export to CSV\nIt needs the new columns."}
    )
    detail = api.get(f"/api/v2/seats/{seat['id']}/request", headers=signed_in()).json()
    [task] = detail["tasks"]
    assert task["title"] == "Port the billing export to CSV" and task["status"] == "open"
    assert all("Port the billing export" not in r.content.decode() for r in hq.seen)


def _the_request_serves_its_steps_and_the_agent_it_asked_for(api, hq):
    seat = ask(api, "cloud-agents/openhands/request", {})
    detail = api.get(f"/api/v2/seats/{seat['id']}/request", headers=signed_in()).json()
    assert detail["steps"] == ["requested", "building", "ready"]
    assert detail["agent"]["id"] == "openhands" and detail["agent"]["supported"] is False
    assert detail["seat"]["request_state"] == "requested" and detail["tasks"] == []


def _a_seat_asks_for_an_agent_and_keeps_its_role_and_a_second_request_is_refused(api, hq):
    seat = ask(api, "seats", {"title": "Support lead"})
    asked = ask(api, f"seats/{seat['id']}/request-agent", {"agent": "copilot"})
    assert (
        asked["title"] == "Support lead"
        and asked["agent"] == "copilot"
        and asked["agent_name"] == "Copilot coding agent"
    )
    ask(api, f"seats/{seat['id']}/request-agent", {"agent": "droids"}, expected=409)
    assert len(filed(hq)) == 1


def _other_takes_the_name_the_person_typed_and_refuses_an_empty_one(api, hq):
    ask(api, "cloud-agents/other/request", {}, expected=422)
    seat = ask(api, "cloud-agents/other/request", {"name": "Acme Agent (acme-agent.example)"})
    assert seat["agent"] == "other" and seat["title"] == "Acme Agent (acme-agent.example)"
    assert seat["agent_name"] == "Acme Agent (acme-agent.example)"
    assert "Acme Agent" in filed(hq)[0]["message"]


def _canceling_tells_tico_and_closes_the_first_task(api, hq):
    seat = ask(api, "cloud-agents/devin/request", {"first_task": "Map the sign-in errors"})
    task = api.get(f"/api/v2/seats/{seat['id']}/request", headers=signed_in()).json()["tasks"][0]
    [(hq_id, secret)] = [(tid, t["secret"]) for tid, t in hq.tickets.items()]
    before = _open_ids(api)
    mark = len(hq.seen)
    canceled = ask(api, f"seats/{seat['id']}/cancel-request", {})
    assert canceled["closed"] is True and canceled["request_state"] == "canceled"
    [note] = [r for r in hq.seen[mark:] if r.method == "POST"]
    assert note.url.path == f"/v1/support/{hq_id}/messages" and note.headers["x-ticket-secret"] == secret
    message = hq.tickets[hq_id]["messages"][-1]["body"]
    assert "Canceled" in message and "Devin" in message
    assert _open_ids(api) == [i for i in before if i != seat["id"]]
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["status"] == "closed"
    # A repeat answers the same and tells Tico nothing more.
    again = ask(api, f"seats/{seat['id']}/cancel-request", {})
    assert again["request_state"] == "canceled" and len([r for r in hq.seen[mark:] if r.method == "POST"]) == 1
    assert (
        api.post(
            f"/api/v2/seats/{seat['id']}/request-agent", json={"agent": "copilot"}, headers=signed_in()
        ).status_code
        == 404
    )


def _a_seat_with_a_request_is_canceled_not_closed(api, hq):
    seat = ask(api, "cloud-agents/devin/request", {})
    before = _open_ids(api)
    ask(api, f"seats/{seat['id']}/close", {}, expected=409)
    assert _open_ids(api) == before and seat["id"] in before


def _a_request_tico_accepted_is_kept_even_if_the_line_changed_meanwhile(api, hq):
    """Once Tico has the request, refusing to keep it would leave a request at Tico this install cannot track or cancel.
    The line is checked when the request is reserved, and the kept request stays cancellable."""
    seat = ask(api, "seats", {"title": "Support lead"})
    real, checks = lifecycle.placement, []

    def placement(auth, c, who, reports_to, seat_id=""):
        checks.append(seat_id)
        if len(checks) > 1:  # anything after the reservation: the line no longer answers to this person
            raise Problem("forbidden", "You may add or change seats only under yourself or someone below you", 403)
        return real(auth, c, who, reports_to, seat_id)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(lifecycle, "placement", placement)
        kept = ask(api, f"seats/{seat['id']}/request-agent", {"agent": "copilot"})
    assert checks == [seat["id"]] and kept["request_state"] == "requested" and len(filed(hq)) == 1
    assert ask(api, f"seats/{seat['id']}/cancel-request", {})["request_state"] == "canceled"


def _adding_a_supported_agent_hires_a_bot_on_its_harness_and_fills_the_seat(api, hq):
    before = _open_ids(api)
    seat = ask(api, "seats", {"title": "Support lead", "description": "Runs the support queue"})
    added = ask(api, "cloud-agents/claude/add", {"seat_id": seat["id"]})
    assert added["harness"] == "claude" and added["seat"] == seat["id"] and added["bot"]
    with api.app.state.store.read() as c:
        bot = H.bot(c, added["bot"])
        config = BS.read(c, added["bot"])
        closed = c.execute("SELECT closed_at, filled_by FROM seats WHERE id=?", (seat["id"],)).fetchone()
    assert bot["display_name"] == "Support lead" and config["harness"] == "claude" and config["runtime"] == "claude"
    assert config["model"] == added["model"] and config["reports_to"] == seat["reports_to"]
    assert closed["filled_by"] == added["bot"] and closed["closed_at"]
    assert _open_ids(api) == before and hq.seen == []


def _an_added_supported_agent_lands_in_onboarding_like_a_template_hire(api, hq):
    before = _open_ids(api)
    hired_seat = ask(api, "seats", {"title": "Support lead", "description": "Runs the support queue"})
    added_seat = ask(api, "seats", {"title": "Support lead", "description": "Runs the support queue"})
    top = ask(api, f"seats/{hired_seat['id']}/recruit", {})["candidates"][0]["template"]
    hired = ask(api, f"seats/{hired_seat['id']}/hire", {"template": top})
    added = ask(api, "cloud-agents/claude/add", {"seat_id": added_seat["id"]})
    assert added["template"] == hired["template"] == top
    assert onboarding_artifacts(api, added["bot"]) == onboarding_artifacts(api, hired["slug"])
    assert onboarding_artifacts(api, added["bot"])["state"] == lifecycle.statuses.NEEDS_SETUP
    assert _open_ids(api) == before


def _adding_without_a_seat_opens_a_seat_in_the_team_and_fills_it(api, hq):
    before = _open_ids(api)
    team = ask(api, "groups", {"name": "Engineering"})["id"]
    added = ask(api, "cloud-agents/codex/add", {"team": team})
    assert added["seat"] and added["template"] and added["harness"] == "codex"
    with api.app.state.store.read() as c:
        assert H.bot(c, added["bot"])["display_name"] == "Codex"
        assert c.execute("SELECT team FROM bot_config WHERE bot=?", (added["bot"],)).fetchone()[0] == team
        config = BS.read(c, added["bot"])
        assert config["team"] == team  # group membership reads the bot's own config
        assert c.execute("SELECT filled_by FROM seats WHERE id=?", (added["seat"],)).fetchone()[0] == added["bot"]
    assert onboarding_artifacts(api, added["bot"])["state"] == lifecycle.statuses.NEEDS_SETUP
    assert _open_ids(api) == before


def _a_seatless_add_no_template_fits_leaves_no_seat_and_no_bot(api, hq):
    before, seats_before = bot_count(api), _open_ids(api)
    assert ask(api, "cloud-agents/codex/add", {}, expected=409)["error"]["code"] == "template"
    assert bot_count(api) == before and _open_ids(api) == seats_before


def _a_seat_no_template_fits_refuses_the_add_and_builds_nothing(api, hq):
    seat = ask(api, "seats", {"title": "Support lead"})
    before = bot_count(api)
    assert ask(api, "cloud-agents/claude/add", {"seat_id": seat["id"]}, expected=409)["error"]["code"] == "template"
    assert bot_count(api) == before and [s["id"] for s in seats(api)] == [seat["id"]]


def _only_connected_agents_are_added_and_a_seat_with_a_request_takes_no_bot(api, hq):
    before = _open_ids(api)
    ask(api, "cloud-agents/devin/add", {}, expected=422)
    seat = ask(api, "cloud-agents/devin/request", {})
    ask(api, "cloud-agents/claude/add", {"seat_id": seat["id"]}, expected=409)
    assert _open_ids(api) == before + [seat["id"]]


def _a_person_placing_a_bot_under_someone_else_is_refused(api, hq):
    seat = ask(api, "seats", {"title": "Owner's seat"})
    ask(api, "cloud-agents/claude/add", {"seat_id": seat["id"]}, headers=as_person(api, "riley"), expected=403)


def _a_member_cannot_request_an_agent_for_a_seat_in_a_team_they_cannot_add_to(api, hq):
    team = ask(api, "groups", {"name": "Engineering"})["id"]
    quinn = as_person(api, "quinn")
    seat = ask(api, "seats", {"title": "Engineer", "team": team, "reports_to": "human:quinn"})
    refused = api.post(f"/api/v2/seats/{seat['id']}/request-agent", json={"agent": "copilot"}, headers=quinn)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "forbidden"
    assert filed(hq) == [] and api.get(f"/api/v2/seats/{seat['id']}/request", headers=signed_in()).status_code == 404
    # The check is the team's, not the seat's: the owner may request for the same seat.
    assert ask(api, f"seats/{seat['id']}/request-agent", {"agent": "copilot"})["agent"] == "copilot"
    assert len(filed(hq)) == 1


def _an_added_bot_joins_the_seats_team(api, hq):
    seat = ask(api, "seats", {"title": "Support lead", "team": "support"})
    added = ask(api, "cloud-agents/claude/add", {"seat_id": seat["id"]})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT team FROM bot_config WHERE bot=?", (added["bot"],)).fetchone()[0] == "support"


def _a_team_add_or_request_needs_the_rule_every_add_to_a_team_obeys(api, hq):
    team = ask(api, "groups", {"name": "Platform"})["id"]
    quinn = as_person(api, "quinn")  # a plain member: not a bot admin, and leads no team
    # The seat is placed under Quinn, so placement passes and the team's rule is what refuses.
    seat = ask(api, "seats", {"title": "Engineer", "team": team, "reports_to": "human:quinn"})
    before, seats_before = bot_count(api), _open_ids(api)
    assert (
        ask(api, "cloud-agents/claude/add", {"seat_id": seat["id"]}, headers=quinn, expected=403)["error"]["code"]
        == "forbidden"
    )
    assert (
        ask(
            api, "cloud-agents/devin/request", {"reports_to": "human:quinn", "team": team}, headers=quinn, expected=403
        )["error"]["code"]
        == "forbidden"
    )
    assert bot_count(api) == before and _open_ids(api) == seats_before and filed(hq) == []
    # The owner may add into the team, and the bot joins it.
    added = ask(api, "cloud-agents/claude/add", {"seat_id": seat["id"]})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT team FROM bot_config WHERE bot=?", (added["bot"],)).fetchone()[0] == team


def _a_seat_with_no_team_under_a_bot_in_a_team_asks_that_teams_rule(api, hq):
    """A bot with no team of its own takes its manager's, so adding under a teamed bot is an add to that team."""
    team = ask(api, "groups", {"name": "Design"})["id"]
    quinn = as_person(api, "quinn")  # a plain member who manages the bot below, but leads no team
    lead_seat = ask(api, "seats", {"title": "Engineer", "team": team, "reports_to": "human:quinn"})
    manager = ask(api, "cloud-agents/claude/add", {"seat_id": lead_seat["id"]})["bot"]
    seat = ask(api, "seats", {"title": "Engineer", "reports_to": manager})
    before = bot_count(api)
    assert (
        ask(api, "cloud-agents/codex/add", {"seat_id": seat["id"]}, headers=quinn, expected=403)["error"]["code"]
        == "forbidden"
    )
    assert bot_count(api) == before
    added = ask(api, "cloud-agents/codex/add", {"seat_id": seat["id"]})
    with api.app.state.store.read() as c:
        assert (
            json.loads(
                c.execute(
                    f"SELECT {BS.CONFIG} AS config FROM bot_config bc {BS.JOIN} WHERE bot=?", (added["bot"],)
                ).fetchone()[0]
            )["team"]
            == team
        )


def _a_seat_only_unmatched_templates_rank_for_refuses_the_add(api, hq):
    before = _open_ids(api)
    seat = ask(api, "seats", {"title": "Qzxv wplk"})
    assert all(not c["match"] for c in ask(api, f"seats/{seat['id']}/recruit", {})["candidates"])
    count = bot_count(api)
    assert ask(api, "cloud-agents/claude/add", {"seat_id": seat["id"]}, expected=409)["error"]["code"] == "template"
    assert bot_count(api) == count and _open_ids(api) == before + [seat["id"]]


def _a_request_for_a_seat_with_no_team_under_a_teamed_bot_asks_that_teams_rule(api, hq):
    team = ask(api, "groups", {"name": "Data"})["id"]
    quinn = as_person(api, "quinn")
    lead_seat = ask(api, "seats", {"title": "Engineer", "team": team, "reports_to": "human:quinn"})
    manager = ask(api, "cloud-agents/claude/add", {"seat_id": lead_seat["id"]})["bot"]
    seat = ask(api, "seats", {"title": "Engineer", "reports_to": manager})
    assert (
        ask(api, f"seats/{seat['id']}/request-agent", {"agent": "devin"}, headers=quinn, expected=403)["error"]["code"]
        == "forbidden"
    )
    assert (
        ask(api, "cloud-agents/devin/request", {"reports_to": manager}, headers=quinn, expected=403)["error"]["code"]
        == "forbidden"
    )
    assert filed(hq) == []


def _a_seat_under_an_open_seat_adds_its_bot_above_it_and_moves_it_under_the_manager_when_that_fills(api, hq):
    before_open = _open_ids(api)
    head = ask(api, "seats", {"title": "Head of Engineering"})
    seat = ask(api, "seats", {"title": "Engineer", "reports_to": "seat:" + head["id"]})
    before = bot_count(api)
    added = ask(api, "cloud-agents/codex/add", {"seat_id": seat["id"]})
    assert bot_count(api) == before + 1
    assert not bot_reports_to(api, added["bot"]).startswith("seat:")  # a bot never reports to a seat
    # The manager seat fills: the bot added under it moves under the manager's bot.
    manager = ask(api, "cloud-agents/claude/add", {"seat_id": head["id"]})["bot"]
    assert bot_reports_to(api, added["bot"]) == manager
    assert _open_ids(api) == before_open


def _an_ordinary_seat_action_recovers_a_stale_filing_and_goes_ahead(api, hq):
    seat = ask(api, "seats", {"title": "Engineer"})
    plant_stale_filing(api, seat["id"])
    ask(api, f"seats/{seat['id']}/close", {})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM seat_requests WHERE seat_id=?", (seat["id"],)).fetchone()[0] == 0
        assert c.execute("SELECT closed_at FROM seats WHERE id=?", (seat["id"],)).fetchone()[0]


def _a_refused_retry_of_a_stale_seatless_request_still_releases_it(api, hq):
    team = ask(api, "groups", {"name": "Research"})["id"]
    quinn = as_person(api, "quinn")  # a plain member: the team's rule refuses the retry
    seat = ask(api, "seats", {"title": "Devin", "reports_to": "human:quinn"})
    from backend.store import digest, encode

    hashed = digest(
        encode(
            {"agent": "devin", "name": "Devin", "first_task": "", "team": team, "reports_to": "human:quinn", "seat": ""}
        )
    )
    with api.app.state.store.read() as c:
        actor = c.execute("SELECT created_by FROM seats WHERE id=?", (seat["id"],)).fetchone()[0]
    plant_stale_filing(api, seat["id"], actor="human:quinn", key="k-retry", request_hash=hashed, made=1)
    ask(
        api,
        "cloud-agents/devin/request",
        {"team": team, "reports_to": "human:quinn"},
        headers={**quinn, "Idempotency-Key": "k-retry"},
        expected=403,
    )
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM seat_requests WHERE request_key='k-retry'").fetchone()[0] == 0
    assert actor and filed(hq) == []


def _a_refused_seat_action_still_keeps_the_recovery_it_made(api, hq):
    """A dead request's own seat (made by it) is recovered, which closes it: the hire on it is then refused, and the
    recovery holds rather than rolling back with the refusal."""
    seat = ask(api, "seats", {"title": "Devin"})
    plant_stale_filing(api, seat["id"], made=1)
    ask(api, f"seats/{seat['id']}/hire", {"template": "anything"}, expected=404)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM seat_requests WHERE seat_id=?", (seat["id"],)).fetchone()[0] == 0


def _a_person_cannot_place_a_request_under_someone_else_and_nothing_is_filed(api, hq):
    headers = as_person(api, "riley")
    agents_before = [s["id"] for s in seats(api) if s["agent"]]
    owner = ask(api, "seats", {"title": "Owner's seat"})["reports_to"]  # the owner's own line
    ask(api, "cloud-agents/devin/request", {"reports_to": owner}, headers=headers, expected=403)
    assert hq.seen == [] and [s["id"] for s in seats(api) if s["agent"]] == agents_before


def _a_token_is_not_a_person_and_cannot_request_add_or_cancel(api, hq):
    api.app.state.store.settings.test_identities["viaa"] = Identity(
        "human:riley", "human", email="riley@acme.example", via_token=True
    )
    before = _open_ids(api)
    seat = ask(api, "cloud-agents/devin/request", {})
    ask(api, "cloud-agents/devin/request", {}, headers=signed_in("viaa"), expected=403)
    ask(api, "cloud-agents/claude/add", {}, headers=signed_in("viaa"), expected=403)
    ask(api, f"seats/{seat['id']}/cancel-request", {}, headers=signed_in("viaa"), expected=403)
    assert len(filed(hq)) == 1 and _open_ids(api) == before + [seat["id"]]


def _a_private_first_task_is_listed_only_to_the_people_who_can_read_it(api, hq):
    seat = ask(api, "cloud-agents/devin/request", {"first_task": "Draft the board memo"})
    task = api.get(f"/api/v2/seats/{seat['id']}/request", headers=signed_in()).json()["tasks"][0]
    with api.app.state.store.transaction() as c:
        H.task_update(c, seat["created_by"], task["id"], private=True)
    owner = api.get(f"/api/v2/seats/{seat['id']}/request", headers=signed_in()).json()
    assert [t["id"] for t in owner["tasks"]] == [task["id"]]
    riley = api.get(f"/api/v2/seats/{seat['id']}/request", headers=as_person(api, "riley")).json()
    assert riley["tasks"] == [] and riley["agent"]["id"] == "devin"


def _a_manager_who_cancels_does_not_close_a_private_first_task_they_cannot_read(api, hq):
    riley = as_person(api, "riley")  # on the roster from the environment's people
    seat = ask(api, "cloud-agents/devin/request", {"first_task": "Draft the launch memo", "reports_to": "human:riley"})
    task = api.get(f"/api/v2/seats/{seat['id']}/request", headers=signed_in()).json()["tasks"][0]
    with api.app.state.store.transaction() as c:
        H.task_update(c, seat["created_by"], task["id"], private=True)
    assert api.get(f"/api/v2/seats/{seat['id']}/request", headers=riley).json()["tasks"] == []
    assert ask(api, f"seats/{seat['id']}/cancel-request", {}, headers=riley)["request_state"] == "canceled"
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["status"] == "open"
        assert c.execute("SELECT first_task FROM seats WHERE id=?", (seat["id"],)).fetchone()[0] == ""


def _a_repeated_key_files_once_and_returns_the_seat_it_made(api, hq):
    key = str(uuid.uuid4())
    before = len(_open_ids(api))
    first = ask(
        api,
        "cloud-agents/devin/request",
        {},
        headers={"Authorization": signed_in()["Authorization"], "Idempotency-Key": key},
    )
    again = ask(
        api,
        "cloud-agents/devin/request",
        {},
        headers={"Authorization": signed_in()["Authorization"], "Idempotency-Key": key},
    )
    assert again["id"] == first["id"]
    assert len(filed(hq)) == 1 and len(_open_ids(api)) == before + 1


def _a_key_used_for_other_content_is_refused(api, hq):
    key = str(uuid.uuid4())
    before = len(_open_ids(api))
    ask(api, "cloud-agents/devin/request", {}, headers={**signed_in(), "Idempotency-Key": key})
    ask(api, "cloud-agents/jules/request", {}, headers={**signed_in(), "Idempotency-Key": key}, expected=409)
    assert len(filed(hq)) == 1 and len(_open_ids(api)) == before + 1


def _a_first_task_whose_title_is_already_open_is_refused_before_tico_hears_of_it(api, hq):
    before = len(_open_ids(api))
    ask(api, "cloud-agents/devin/request", {"first_task": "Trace the export failures"})
    refused = ask(
        api, "cloud-agents/jules/request", {"first_task": "Trace the export failures\nMore detail"}, expected=409
    )
    assert refused["error"]["code"] == "duplicate"
    assert len(filed(hq)) == 1 and len(_open_ids(api)) == before + 1


def _support_off_keeps_no_seat_and_sends_nothing(api, hq):
    before = _open_ids(api)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TICO_SUPPORT", "off")
        ask(api, "cloud-agents/devin/request", {}, expected=409)
    assert hq.seen == [] and _open_ids(api) == before


def _tico_down_keeps_no_seat_so_a_retry_starts_clean(api, hq):
    before = _open_ids(api)
    hq.down = True
    ask(api, "cloud-agents/devin/request", {"first_task": "Map the sign-in errors"}, expected=502)
    assert _open_ids(api) == before
    hq.down = False
    seat = ask(api, "cloud-agents/devin/request", {"first_task": "Map the sign-in errors"})
    assert _open_ids(api) == before + [seat["id"]]


def _canceling_while_tico_is_down_changes_nothing_so_it_can_be_tried_again(api, hq):
    seat = ask(api, "cloud-agents/devin/request", {"first_task": "Chart the refund questions"})
    task = api.get(f"/api/v2/seats/{seat['id']}/request", headers=signed_in()).json()["tasks"][0]
    before = _open_ids(api)
    hq.down = True
    ask(api, f"seats/{seat['id']}/cancel-request", {}, expected=502)
    assert _open_ids(api) == before
    [still] = [s for s in seats(api) if s["id"] == seat["id"]]
    assert still["request_state"] == "requested"
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["status"] == "open"
    hq.down = False
    assert ask(api, f"seats/{seat['id']}/cancel-request", {})["request_state"] == "canceled"


def _a_cancel_whose_last_step_fails_leaves_the_request_requested_so_it_can_be_tried_again(api, hq):
    seat = ask(api, "cloud-agents/devin/request", {"first_task": "Sketch the onboarding emails"})
    task = api.get(f"/api/v2/seats/{seat['id']}/request", headers=signed_in()).json()["tasks"][0]
    before = _open_ids(api)
    real, calls = cloud_agents._canceled, []

    def failing_once(c, seat_id):
        calls.append(seat_id)
        if len(calls) == 1:
            raise Problem("unavailable", "The cancel could not be finished. Try again.", 503, retryable=True)
        return real(c, seat_id)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cloud_agents, "_canceled", failing_once)
        ask(api, f"seats/{seat['id']}/cancel-request", {}, expected=503)
        [still] = [s for s in seats(api) if s["id"] == seat["id"]]
        assert _open_ids(api) == before and still["request_state"] == "requested"
        with api.app.state.store.read() as c:
            assert H.task(c, task["id"])["status"] == "open"
        assert ask(api, f"seats/{seat['id']}/cancel-request", {})["request_state"] == "canceled"


# --- stories: one app each ---


def test_the_agent_catalog_and_its_requests_story(environment, hq, subtests):
    """The catalog, then the request path on the fixture catalog: each check reports on its own."""
    api = environment()
    for check in (
        _a_supported_agent_is_added_not_requested_and_an_unknown_one_is_refused,
        _requesting_an_agent_puts_a_seat_on_the_chart_and_files_one_ticket_without_ids,
        _the_first_task_waits_on_the_seat_as_a_task_its_person_owns_and_never_reaches_tico,
        _the_request_serves_its_steps_and_the_agent_it_asked_for,
        _a_seat_asks_for_an_agent_and_keeps_its_role_and_a_second_request_is_refused,
        _other_takes_the_name_the_person_typed_and_refuses_an_empty_one,
        _canceling_tells_tico_and_closes_the_first_task,
        _a_seat_with_a_request_is_canceled_not_closed,
        _a_request_tico_accepted_is_kept_even_if_the_line_changed_meanwhile,
    ):
        with subtests.test(check.__name__):
            _fresh(hq)
            check(api, hq)


def test_adding_a_supported_agent_on_the_shipped_catalog_story(environment, hq, subtests):
    """Adding a supported agent on the shipped catalog: it is hired like a template and its seat fills."""
    api = environment(catalog_dir=SHIPPED)
    for check in (
        _adding_a_supported_agent_hires_a_bot_on_its_harness_and_fills_the_seat,
        _an_added_supported_agent_lands_in_onboarding_like_a_template_hire,
        _adding_without_a_seat_opens_a_seat_in_the_team_and_fills_it,
        _an_added_bot_joins_the_seats_team,
        _a_seat_under_an_open_seat_adds_its_bot_above_it_and_moves_it_under_the_manager_when_that_fills,
        _a_team_add_or_request_needs_the_rule_every_add_to_a_team_obeys,
        _a_seat_with_no_team_under_a_bot_in_a_team_asks_that_teams_rule,
        _a_request_for_a_seat_with_no_team_under_a_teamed_bot_asks_that_teams_rule,
        _a_seat_only_unmatched_templates_rank_for_refuses_the_add,
    ):
        with subtests.test(check.__name__):
            _fresh(hq)
            check(api, hq)


def test_refused_adds_requests_and_recoveries_story(environment, hq, subtests):
    """Adds, requests and recoveries on the fixture catalog (no template fits any seat): each refusal builds nothing."""
    api = environment()
    for check in (
        _a_seatless_add_no_template_fits_leaves_no_seat_and_no_bot,
        _a_seat_no_template_fits_refuses_the_add_and_builds_nothing,
        _only_connected_agents_are_added_and_a_seat_with_a_request_takes_no_bot,
        _a_person_placing_a_bot_under_someone_else_is_refused,
        _a_member_cannot_request_an_agent_for_a_seat_in_a_team_they_cannot_add_to,
        _a_token_is_not_a_person_and_cannot_request_add_or_cancel,
        _a_private_first_task_is_listed_only_to_the_people_who_can_read_it,
        _a_manager_who_cancels_does_not_close_a_private_first_task_they_cannot_read,
        _a_person_cannot_place_a_request_under_someone_else_and_nothing_is_filed,
        _an_ordinary_seat_action_recovers_a_stale_filing_and_goes_ahead,
        _a_refused_retry_of_a_stale_seatless_request_still_releases_it,
        _a_refused_seat_action_still_keeps_the_recovery_it_made,
    ):
        with subtests.test(check.__name__):
            _fresh(hq)
            check(api, hq)


def test_support_off_duplicates_retries_and_tico_down_story(environment, hq, subtests):
    """Requests as Tico is off, refused or down: each one keeps no seat, and each retry starts clean."""
    api = environment()
    for check in (
        _support_off_keeps_no_seat_and_sends_nothing,
        _a_repeated_key_files_once_and_returns_the_seat_it_made,
        _a_key_used_for_other_content_is_refused,
        _a_first_task_whose_title_is_already_open_is_refused_before_tico_hears_of_it,
        _tico_down_keeps_no_seat_so_a_retry_starts_clean,
        _canceling_while_tico_is_down_changes_nothing_so_it_can_be_tried_again,
        _a_cancel_whose_last_step_fails_leaves_the_request_requested_so_it_can_be_tried_again,
    ):
        with subtests.test(check.__name__):
            _fresh(hq)
            check(api, hq)


# --- kept as their own tests: threads and holds, a dead process, or a store start ---


def test_a_request_left_by_a_dead_process_is_recovered_by_the_next_guarded_action(environment, hq):
    """A process that died mid-request leaves its seat filing (or canceling). Past the window, the next request or cancel
    on that seat recovers it on the spot, without a restart; a fresh one still refuses, since a live process owns it."""
    api = environment()
    stale = H.shift(H.now(), seconds=-(lifecycle.FILING_STALE_SECONDS + 1))
    filing = ask(api, "seats", {"title": "Engineer"})
    fresh = ask(api, "seats", {"title": "Designer"})
    with api.app.state.store.transaction() as c:
        for seat_id, created in ((filing["id"], stale), (fresh["id"], H.now())):
            c.execute(
                "UPDATE seats SET request_state=?,agent='devin',agent_name='Devin' WHERE id=?",
                (lifecycle.FILING, seat_id),
            )
            c.execute(
                "INSERT INTO seat_requests(id,actor,request_key,request_hash,seat_id,state,created_at,made) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, "dead-process", "key-" + seat_id, "x", seat_id, "filing", created, 0),
            )
    assert (
        ask(api, f"seats/{fresh['id']}/request-agent", {"agent": "copilot"}, expected=409)["error"]["code"] == "filing"
    )
    assert ask(api, f"seats/{filing['id']}/request-agent", {"agent": "copilot"})["agent"] == "copilot"
    assert len(filed(hq)) == 1
    canceling = ask(api, "seats", {"title": "Analyst"})
    ask(api, f"seats/{canceling['id']}/request-agent", {"agent": "copilot"})
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE seats SET request_state=?,updated_at=? WHERE id=?", (lifecycle.CANCELING, stale, canceling["id"])
        )
    assert ask(api, f"seats/{canceling['id']}/cancel-request", {})["request_state"] == "canceled"
    # A cancel that recovers a stale filing is then refused (there is no request left to cancel), and the recovery holds.
    orphan = ask(api, "seats", {"title": "Writer"})
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE seats SET request_state=?,agent='devin',agent_name='Devin' WHERE id=?",
            (lifecycle.FILING, orphan["id"]),
        )
        c.execute(
            "INSERT INTO seat_requests(id,actor,request_key,request_hash,seat_id,state,created_at,made) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, "dead-process", "key-o", "x", orphan["id"], "filing", stale, 0),
        )
    ask(api, f"seats/{orphan['id']}/cancel-request", {}, expected=404)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM seat_requests WHERE seat_id=?", (orphan["id"],)).fetchone()[0] == 0
        assert c.execute("SELECT request_state FROM seats WHERE id=?", (orphan["id"],)).fetchone()[0] in ("", None)


def test_a_request_a_stopped_process_left_filing_is_released_when_the_store_starts(environment, hq):
    api = environment()
    key = str(uuid.uuid4())
    headers = {**signed_in(), "Idempotency-Key": key}
    seat = ask(api, "cloud-agents/devin/request", {"first_task": "Map the sign-in errors"}, headers=headers)
    task = api.get(f"/api/v2/seats/{seat['id']}/request", headers=signed_in()).json()["tasks"][0]
    with api.app.state.store.transaction() as c:  # the process stopped before Tico answered
        c.execute("UPDATE seats SET request_state=?,request_ticket='' WHERE id=?", (lifecycle.FILING, seat["id"]))
        c.execute(
            "UPDATE seat_requests SET state='filing',ticket_id='',created_at=? WHERE seat_id=?",
            ("2000-01-01T00:00:00+00:00", seat["id"]),
        )
    cloud_agents.release_abandoned(api.app.state.store)
    assert seats(api) == []
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["status"] == "closed"
        assert c.execute("SELECT COUNT(*) FROM seat_requests").fetchone()[0] == 0
    again = ask(api, "cloud-agents/devin/request", {"first_task": "Map the sign-in errors"}, headers=headers)
    assert again["id"] != seat["id"] and [s["id"] for s in seats(api)] == [again["id"]]


def test_a_seat_cannot_be_closed_or_renamed_while_its_request_is_being_filed(environment, hq, monkeypatch):
    api = environment()
    seat = ask(api, "seats", {"title": "Support lead"})
    entered, release, during = threading.Event(), threading.Event(), []

    def refused_meanwhile():
        during.append(api.post(f"/api/v2/seats/{seat['id']}/close", json={}, headers=signed_in()).status_code)
        during.append(
            api.patch(f"/api/v2/seats/{seat['id']}", json={"title": "Renamed"}, headers=signed_in()).status_code
        )

    hold_tico(monkeypatch, entered, release, during=refused_meanwhile)
    answer = {}
    asking = threading.Thread(
        target=lambda: answer.update(
            r=api.post(f"/api/v2/seats/{seat['id']}/request-agent", json={"agent": "copilot"}, headers=signed_in())
        )
    )
    asking.start()
    assert entered.wait(10)
    release.set()
    asking.join(20)
    assert during == [409, 409]
    assert answer["r"].status_code == 200 and answer["r"].json()["agent"] == "copilot"
    assert answer["r"].json()["title"] == "Support lead"


def test_a_seat_being_filed_or_requested_takes_no_bot_and_no_hire(environment, hq, monkeypatch):
    api = environment()
    seat = ask(api, "seats", {"title": "Support lead"})
    before = bot_count(api)
    entered, release, during = threading.Event(), threading.Event(), []

    def refused_meanwhile():
        for path, body in (
            ("cloud-agents/claude/add", {"seat_id": seat["id"]}),
            (f"seats/{seat['id']}/hire", {"template": "any"}),
        ):
            r = api.post("/api/v2/" + path, json=body, headers=signed_in())
            during.append((r.status_code, r.json()["error"]["code"]))

    hold_tico(monkeypatch, entered, release, during=refused_meanwhile)
    answer = {}
    asking = threading.Thread(
        target=lambda: answer.update(
            r=api.post(f"/api/v2/seats/{seat['id']}/request-agent", json={"agent": "copilot"}, headers=signed_in())
        )
    )
    asking.start()
    assert entered.wait(10)
    release.set()
    asking.join(20)
    assert answer["r"].status_code == 200 and during == [(409, "filing"), (409, "filing")]
    assert bot_count(api) == before
    added = api.post("/api/v2/cloud-agents/claude/add", json={"seat_id": seat["id"]}, headers=signed_in())
    assert added.status_code == 409 and added.json()["error"]["code"] == "requested"
    hired = api.post(f"/api/v2/seats/{seat['id']}/hire", json={"template": "any"}, headers=signed_in())
    assert hired.status_code == 409 and hired.json()["error"]["code"] == "requested"
    assert bot_count(api) == before


def test_two_requests_with_the_same_first_task_at_once_file_one_ticket(environment, hq, monkeypatch):
    api = environment()
    entered, release, answers = threading.Event(), threading.Event(), {}
    hold_tico(monkeypatch, entered, release)

    def send(name):
        answers[name] = api.post(
            "/api/v2/cloud-agents/devin/request",
            json={"first_task": "Map the sign-in errors"},
            headers={**signed_in(), "Idempotency-Key": str(uuid.uuid4())},
        )

    first = threading.Thread(target=send, args=("first",))
    first.start()
    assert entered.wait(10)
    send("second")  # the first task is already open, so this one is refused before Tico is called
    release.set()
    first.join(20)
    assert answers["first"].status_code == 200
    assert answers["second"].status_code == 409 and answers["second"].json()["error"]["code"] == "duplicate"
    assert len(filed(hq)) == 1 and len(seats(api)) == 1


def test_a_cancel_holds_its_seat_so_no_change_lands_while_tico_is_told(environment, hq, monkeypatch):
    api = environment()
    seat = ask(api, "cloud-agents/devin/request", {"first_task": "Map the sign-in errors"})
    other = ask(api, "seats", {"title": "Ops lead"})
    entered, release, during = threading.Event(), threading.Event(), []

    def refused_meanwhile():
        during.append(
            api.patch(f"/api/v2/seats/{seat['id']}", json={"title": "Renamed"}, headers=signed_in()).status_code
        )
        during.append(
            api.patch(
                f"/api/v2/seats/{seat['id']}", json={"reports_to": f"seat:{other['id']}"}, headers=signed_in()
            ).status_code
        )
        during.append(api.post(f"/api/v2/seats/{seat['id']}/close", json={}, headers=signed_in()).status_code)

    hold_call(
        monkeypatch,
        lambda method, path: method == "POST" and path.endswith("/messages"),
        entered,
        release,
        during=refused_meanwhile,
    )
    answer = {}
    canceling = threading.Thread(
        target=lambda: answer.update(
            r=api.post(f"/api/v2/seats/{seat['id']}/cancel-request", json={}, headers=signed_in())
        )
    )
    canceling.start()
    assert entered.wait(10)
    release.set()
    canceling.join(20)
    assert during == [409, 409, 409]
    assert answer["r"].status_code == 200 and answer["r"].json()["request_state"] == "canceled"
    [hq_id] = list(hq.tickets)
    assert len([m for m in hq.tickets[hq_id]["messages"] if "Canceled" in m["body"]]) == 1
