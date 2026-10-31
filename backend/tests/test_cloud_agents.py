"""Cloud agents (backend/cloud_agents.py): a seat asks Tico's team to connect an agent it does not connect yet. The
request keeps the seat and the first task in the transaction that files the ticket, and nothing reaches Tico for a
request that cannot be kept."""
import json
import uuid

from backend import hubdb as H
from backend.auth import Identity
from backend.tests.test_onboarding import as_person, environment, signed_in  # noqa: F401
from backend.tests.test_support import hq  # noqa: F401


def filed(hq):
    return [json.loads(r.content) for r in hq.seen if r.method == "POST" and r.url.path == "/v1/support"]


def seats(api):
    return api.get("/api/v2/seats", headers=signed_in()).json()["seats"]


def ask(api, path, body, headers=None, expected=200):
    r = api.post("/api/v2/" + path, json=body, headers=headers or signed_in())
    assert r.status_code == expected, r.text
    return r.json()


def test_the_catalog_lists_eight_agents_and_only_claude_code_and_codex_connect(environment, hq):
    api = environment()
    agents = api.get("/api/v2/cloud-agents", headers=signed_in()).json()["agents"]
    assert len(agents) == 8
    assert all(set(a) == {"id", "name", "maker", "site", "line", "supported", "connect_id"} for a in agents)
    assert {a["id"]: a["connect_id"] for a in agents if a["supported"]} == {"claude": "claude", "codex": "codex"}
    assert all(a["connect_id"] is None for a in agents if not a["supported"])
    assert hq.seen == []


def test_requesting_an_agent_puts_a_seat_on_the_chart_and_files_one_ticket_without_ids(environment, hq):
    api = environment()
    seat = ask(api, "cloud-agents/devin/request", {"team": "engineering"})
    assert seat["title"] == "Devin" and seat["agent"] == "devin" and seat["agent_name"] == "Devin"
    assert seat["request_state"] == "requested" and seat["team"] == "engineering" and seat["stage"] == "recruiting"
    assert seat["id"] in [s["id"] for s in seats(api)]
    [ticket] = filed(hq)
    assert "Devin" in ticket["message"] and ticket["kind"] == "feedback"
    # The company's install and version stay at home: the request goes as the message alone.
    assert set(ticket) == {"message", "kind"}


def test_the_first_task_waits_on_the_seat_as_a_task_its_person_owns_and_never_reaches_tico(environment, hq):
    api = environment()
    seat = ask(api, "cloud-agents/jules/request", {"first_task": "Port the billing export to CSV\nIt needs the new columns."})
    detail = api.get(f"/api/v2/seats/{seat['id']}/request", headers=signed_in()).json()
    [task] = detail["tasks"]
    assert task["title"] == "Port the billing export to CSV" and task["status"] == "open"
    assert all("Port the billing export" not in r.content.decode() for r in hq.seen)


def test_the_request_serves_its_steps_and_the_agent_it_asked_for(environment, hq):
    api = environment()
    seat = ask(api, "cloud-agents/openhands/request", {})
    detail = api.get(f"/api/v2/seats/{seat['id']}/request", headers=signed_in()).json()
    assert detail["steps"] == ["requested", "building", "ready"]
    assert detail["agent"]["id"] == "openhands" and detail["agent"]["supported"] is False
    assert detail["seat"]["request_state"] == "requested" and detail["tasks"] == []


def test_a_seat_asks_for_an_agent_and_keeps_its_role_and_a_second_request_is_refused(environment, hq):
    api = environment()
    seat = ask(api, "seats", {"title": "Support lead"})
    asked = ask(api, f"seats/{seat['id']}/request-agent", {"agent": "copilot"})
    assert asked["title"] == "Support lead" and asked["agent"] == "copilot" and asked["agent_name"] == "Copilot coding agent"
    ask(api, f"seats/{seat['id']}/request-agent", {"agent": "droids"}, expected=409)
    assert len(filed(hq)) == 1


def test_other_takes_the_name_the_person_typed_and_refuses_an_empty_one(environment, hq):
    api = environment()
    ask(api, "cloud-agents/other/request", {}, expected=422)
    seat = ask(api, "cloud-agents/other/request", {"name": "Acme Agent (acme-agent.example)"})
    assert seat["agent"] == "other" and seat["title"] == "Acme Agent (acme-agent.example)"
    assert seat["agent_name"] == "Acme Agent (acme-agent.example)"
    assert "Acme Agent" in filed(hq)[0]["message"]


def test_a_supported_agent_is_added_directly_and_an_unknown_one_is_refused(environment, hq):
    api = environment()
    ask(api, "cloud-agents/claude/request", {}, expected=422)
    ask(api, "cloud-agents/codex/request", {}, expected=422)
    ask(api, "cloud-agents/nobody/request", {}, expected=422)
    assert hq.seen == [] and seats(api) == []


def test_support_off_keeps_no_seat_and_sends_nothing(environment, hq, monkeypatch):
    api = environment()
    monkeypatch.setenv("TICO_SUPPORT", "off")
    ask(api, "cloud-agents/devin/request", {}, expected=409)
    assert hq.seen == [] and seats(api) == []


def test_tico_down_keeps_no_seat_so_a_retry_starts_clean(environment, hq):
    api = environment()
    hq.down = True
    ask(api, "cloud-agents/devin/request", {"first_task": "Map the sign-in errors"}, expected=502)
    assert seats(api) == []
    hq.down = False
    seat = ask(api, "cloud-agents/devin/request", {"first_task": "Map the sign-in errors"})
    assert [s["id"] for s in seats(api)] == [seat["id"]]


def test_a_repeated_key_files_once_and_returns_the_seat_it_made(environment, hq):
    api = environment()
    key = str(uuid.uuid4())
    first = ask(api, "cloud-agents/devin/request", {}, headers={"Authorization": signed_in()["Authorization"], "Idempotency-Key": key})
    again = ask(api, "cloud-agents/devin/request", {}, headers={"Authorization": signed_in()["Authorization"], "Idempotency-Key": key})
    assert again["id"] == first["id"]
    assert len(filed(hq)) == 1 and len(seats(api)) == 1


def test_a_token_is_not_a_person_and_cannot_request(environment, hq):
    api = environment()
    api.app.state.store.settings.test_identities["viaa"] = Identity("human:riley", "human", email="riley@acme.example",
                                                                   via_token=True)
    ask(api, "cloud-agents/devin/request", {}, headers=signed_in("viaa"), expected=403)
    assert hq.seen == []


def test_a_person_cannot_place_a_request_under_someone_else_and_nothing_is_filed(environment, hq, monkeypatch):
    api = environment()
    headers = as_person(api, "riley")
    owner = ask(api, "seats", {"title": "Owner's seat"})["reports_to"]     # the owner's own line
    ask(api, "cloud-agents/devin/request", {"reports_to": owner}, headers=headers, expected=403)
    assert hq.seen == [] and [s for s in seats(api) if s["agent"]] == []


def test_closing_the_seat_cancels_the_request(environment, hq):
    api = environment()
    seat = ask(api, "cloud-agents/devin/request", {})
    ask(api, f"seats/{seat['id']}/close", {})
    assert seats(api) == []
    assert api.get(f"/api/v2/seats/{seat['id']}/request", headers=signed_in()).status_code == 404


def test_a_private_first_task_is_listed_only_to_the_people_who_can_read_it(environment, hq):
    api = environment()
    seat = ask(api, "cloud-agents/devin/request", {"first_task": "Draft the board memo"})
    task = api.get(f"/api/v2/seats/{seat['id']}/request", headers=signed_in()).json()["tasks"][0]
    with api.app.state.store.transaction() as c:
        H.task_update(c, seat["created_by"], task["id"], private=True)
    owner = api.get(f"/api/v2/seats/{seat['id']}/request", headers=signed_in()).json()
    assert [t["id"] for t in owner["tasks"]] == [task["id"]]
    riley = api.get(f"/api/v2/seats/{seat['id']}/request", headers=as_person(api, "riley")).json()
    assert riley["tasks"] == [] and riley["agent"]["id"] == "devin"
