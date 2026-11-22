"""Close opportunity links use the normal task API and a fake Close REST transport."""

import base64
import ipaddress
from urllib.parse import quote

import httpx
import pytest

from backend import egress
from backend import hubdb as H
from backend import item_metadata as IM
from backend import mailbox_access as MA
from backend import task_engine
from backend.entities import store as ES
from backend.item_sources import close as CS
from backend.item_sources import jira as JS
from backend.tests.helpers.api import get, post, put
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.jira_intake import PUBLIC, _patch_tool
from backend.tests.helpers.mcp import call
from backend.tests.helpers.org_rule_check import COLOR, NEEDLE, color_rule, ruled  # noqa: F401 - ruled is a fixture
from backend.tests.helpers.org_rules import edit

DEAL = "oppo_one"
LEAD = "lead_one"


class FakeClose:
    def __init__(self, api):
        self.api = api
        self.keys = {}
        self.deals = {}
        self.replies = {}
        self.seen = []
        self.during = None

    def connect(self, name, *, record=True, level="read"):
        tool = post(self.api, "api-tools", {"name": name, "service": "close-crm"})["id"]
        key = f"close-synthetic-{tool}-11114444"
        post(self.api, f"api-tools/{tool}/keys", {"name": "Close", "value": key, "access_level": level})
        if record:
            _patch_tool(self.api, tool, {"record_owner": "outside"})
        self.keys["Basic " + base64.b64encode(f"{key}:".encode()).decode()] = tool
        return tool

    def set(self, tool, key=DEAL, status="active", value=50000):
        self.deals[(tool, key)] = {
            "id": key,
            "lead_id": LEAD,
            "status_type": status,
            "status_label": {"active": "Proposal", "won": "Signed", "lost": "Passed"}[status],
            "value": value,
            "value_currency": "USD",
            "value_period": "one_time",
        }

    def __call__(self, request):
        assert request.method == "GET"
        assert request.headers["host"] == "api.close.com"
        assert request.url.params["_fields"] == "id,status_type,status_label,value,value_currency,value_period,lead_id"
        tool = self.keys[request.headers["Authorization"]]
        key = request.url.path.split("/")[-2]
        assert request.url.path == f"/api/v1/opportunity/{key}/"
        self.seen.append((tool, key))
        if self.during:
            self.during()
        if (tool, key) in self.replies:
            return self.replies[tool, key]
        fields = self.deals.get((tool, key))
        return httpx.Response(200 if fields else 404, json=fields)


@pytest.fixture
def close(api, monkeypatch):
    setup(api)
    fake = FakeClose(api)
    monkeypatch.setattr(egress, "resolve", lambda host: [ipaddress.ip_address(PUBLIC)])
    monkeypatch.setattr(CS, "TRANSPORT", httpx.MockTransport(fake))
    return fake


def _ref(tool, key=DEAL):
    return f"close:opportunity:{key}@{tool}"


def _create(api, link, *, expected=200, token="ana-test", **fields):
    result = post(
        api,
        "tasks",
        {"owner": "ben", "title": "Follow the deal", "primary_link": link, **fields},
        token,
        expected=expected,
    )
    return result.get("task", result)


def _refresh(api, now=None):
    return CS.refresh(api.app.state.store, api.app.state.vault, now=now)


def _meta(api, tool, key=DEAL):
    with api.app.state.store.read() as c:
        return IM.get(c, "close", "opportunity", key, tool)


def _asks(api, task):
    with api.app.state.store.read() as c:
        return [
            tuple(r)
            for r in c.execute(
                "SELECT r.person_id, r.who_oks, r.withdrawn_at IS NOT NULL FROM requirements r "
                "JOIN visits v ON v.id=r.visit_id WHERE v.task_id=? AND r.line LIKE '% in Close:%' ORDER BY r.rowid",
                (task,),
            )
        ]


def _record(api, tool, name, *, restricted=False):
    with api.app.state.store.transaction() as c:
        actor = "human:ana"
        entity = ES.upsert(c, actor, "organization", match={"domain": f"{name.lower()}.example"}, name=name)["id"]
        if restricted:
            box = MA.ensure_mailbox(c, "ana@acme.example", person_id="ana", created_by=actor)
            ES.add_source(c, actor, entity, "mailbox", box["id"], visibility="restricted")
    post(
        api,
        "entities/external-refs",
        {"ref": entity, "system": "close", "external_type": "lead", "external_id": LEAD, "connection_id": tool},
    )
    return entity


def test_close_url_files_and_finds_a_typed_task_without_borrowing_jira_slots(api, close, monkeypatch):
    tool = close.connect("Close Acme")
    close.set(tool)
    task = _create(api, CS.url_of(DEAL))["id"]
    error, again = call(api, "tico_task_create", {"owner": "ben", "title": "Duplicate", "primary_link": _ref(tool)})
    assert (error, again["found"], again["task"]["id"]) == (False, True, task)
    [link] = get(api, f"tasks/{task}/links")["links"]
    assert (link["source"], link["item_type"], link["item_key"], link["connection_id"], link["is_primary"]) == (
        "close",
        "opportunity",
        DEAL,
        tool,
        True,
    )
    assert [row["id"] for row in get(api, f"tasks?link={quote(_ref(tool))}")["tasks"]] == [task]
    monkeypatch.setattr(JS, "MAX_IN_FLIGHT", 0)
    assert _refresh(api) == 1
    saved = _meta(api, tool)
    assert saved["fields"] == {
        "state": "open",
        "status": "Proposal",
        "status_type": "active",
        "value": 50000,
        "value_currency": "USD",
        "value_period": "one_time",
        "lead_id": LEAD,
        "connection_id": tool,
    }
    assert _refresh(api, H.shift(saved["read_at"], seconds=299)) == 0
    assert _refresh(api, H.shift(saved["read_at"], seconds=301)) == 1
    assert close.seen == [(tool, DEAL)] * 2
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE credentials SET ciphertext=NULL WHERE tool_id=?", (tool,))
    assert [row["id"] for row in get(api, f"tasks?link={quote(CS.url_of(DEAL))}")["tasks"]] == [task]


def test_same_ids_on_two_close_connections_use_their_own_key_status_value_and_entity(api, close):
    acme, beta = close.connect("Close Acme"), close.connect("Close Beta")
    a, b = _record(api, acme, "Acme"), _record(api, beta, "Beta")
    close.set(acme)
    close.set(beta, status="lost", value=12000)
    assert _create(api, CS.url_of(DEAL), expected=422)["error"]["code"] == "primary_link"
    first, second = _create(api, _ref(acme)), _create(api, _ref(beta))
    assert first["id"] != second["id"]
    assert (first["entity_id"], second["entity_id"]) == (a, b)
    assert close.seen == [(acme, DEAL), (beta, DEAL)]  # lead looked up on the first creates
    assert _refresh(api) == 0
    assert (_meta(api, acme)["fields"]["value"], _meta(api, beta)["fields"]["value"]) == (50000, 12000)
    assert _asks(api, first["id"]) == [] and _asks(api, second["id"]) == [("human:ben", "people_only", 0)]


def test_close_needs_a_record_integration_with_a_shared_read_key(api, close):
    unlisted = close.connect("Close Unlisted", record=False)
    write = close.connect("Close Write", level="read_write")
    personal = close.connect("Close Personal")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE credentials SET holder='human:ana' WHERE tool_id=?", (personal,))
    for tool in (unlisted, write, personal):
        close.set(tool)
        assert _create(api, _ref(tool), expected=422)["error"]["code"] == "primary_link"
    assert _refresh(api) == 0
    assert close.seen == []


def test_a_company_task_never_inherits_a_restricted_lead_even_when_the_filer_can_see_it(api, close):
    tool = close.connect("Close Acme")
    private = _record(api, tool, "Private", restricted=True)
    close.set(tool)
    assert not _create(api, _ref(tool), owner="cara", token="cara-test")["entity_id"]
    second = "oppo_two"
    close.set(tool, second)
    with api.app.state.store.transaction() as c:
        ES.set_owner(c, "human:ana", private, "human:ben")
        c.execute("UPDATE humans SET availability='on' WHERE id='ben'")
    routed = _create(api, _ref(tool, second), owner=None, route=True)
    assert not routed["entity_id"] and routed["owner_id"] == "human:ana"
    assert _create(api, _ref(tool, "oppo_explicit"), entity=private)["entity_id"] == private


def test_routed_close_task_checks_privacy_before_fetching_and_caps_and_rules_for_the_lead_owner(ruled, close):
    api, _ = ruled
    tool = close.connect("Close Acme")
    entity = _record(api, tool, "Acme")
    close.set(tool)
    with api.app.state.store.transaction() as c:
        ES.set_owner(c, "human:ana", entity, "human:ben")
        c.execute("UPDATE humans SET availability='on' WHERE id='ben'")
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='ops'")
    color_rule(api, **{"with": {"people": ["ben"]}})
    _create(api, _ref(tool), owner=None, route=True, private=True, expected=422)
    assert close.seen == []
    _create(api, _ref(tool), owner=None, route=True, token="ops-test", body="Our favorite color is gold.", expected=422)
    assert close.seen == [(tool, DEAL)]
    put(api, "task-caps", {"sender": "bot:ops", "receiver": "person:ben", "per_day": 0})
    capped = _create(api, _ref(tool), owner=None, route=True, token="ops-test", expected=409)
    assert capped["error"] == "task_cap"
    assert close.seen == [(tool, DEAL)] * 2


@pytest.mark.parametrize("fallback_block", ["cap", "rule"])
def test_a_fallback_cap_or_rule_cannot_block_routing_to_the_close_lead_owner(ruled, close, fallback_block):
    api, _ = ruled
    tool = close.connect("Close Acme")
    entity = _record(api, tool, "Acme")
    close.set(tool)
    with api.app.state.store.transaction() as c:
        ES.set_owner(c, "human:ana", entity, "human:ben")
        c.execute("UPDATE humans SET availability='on' WHERE id='ben'")
    if fallback_block == "cap":
        put(api, "task-caps", {"sender": "bot:ops", "receiver": "person:ana", "per_day": 0})
    else:
        color_rule(api, **{"with": {"people": ["ana"]}})
    made = _create(api, _ref(tool), owner=None, route=True, token="ops-test", body="Our favorite color is gold.")
    assert (made["entity_id"], made["owner_id"]) == (entity, "human:ben")
    assert close.seen == [(tool, DEAL)] and _refresh(api) == 0


def test_a_held_create_matches_the_close_lead_when_the_owner_releases_it(ruled, close):
    api, _ = ruled
    tool = close.connect("Close Acme")
    entity = _record(api, tool, "Acme")
    close.set(tool)
    color_rule(api, reviewer="ben")
    held = _create(api, _ref(tool), token="ops-test", body="Our favorite color is gold.")["held"]
    assert close.seen == []
    view = get(api, f"rule-holds/{held['id']}", token="ben-test")
    sent = post(api, f"rule-holds/{held['id']}/send", {"nonce": view["nonce"]}, token="ben-test")
    assert sent["result"]["task"]["entity_id"] == entity
    assert close.seen == [(tool, DEAL)]


def test_a_held_create_checks_the_nonce_and_fresh_rules_before_reading_close(ruled, close):
    api, judge = ruled
    tool = close.connect("Close Acme")
    _record(api, tool, "Acme")
    close.set(tool)
    rule = color_rule(api, reviewer="ben")
    held = _create(api, _ref(tool), token="ops-test", body="Our favorite color is gold.")["held"]
    path = f"rule-holds/{held['id']}"
    view = get(api, path, token="ben-test")
    wrong = post(api, f"{path}/send", {"nonce": "wrong"}, token="ben-test", expected=409)
    assert wrong["error"]["code"] == "conflict" and close.seen == []
    revised = COLOR + " Ever."
    edit(api, rule["id"], {"version": rule["version"], "text": revised})
    judge.needles[revised] = NEEDLE
    blocked = post(api, f"{path}/send", {"nonce": view["nonce"]}, token="ben-test", expected=409)
    assert blocked["error"]["code"] == "rule_changed" and close.seen == []
    assert get(api, path, token="ben-test")["nonce"] == view["nonce"]


def test_a_primary_deal_asks_per_standing_and_withdraws_on_reopen_or_unlink(api, close):
    tool = close.connect("Close Acme")
    close.set(tool)
    task = _create(api, _ref(tool))["id"]
    secondary = _create(api, None, title="The same deal as a reference")["id"]
    post(api, f"tasks/{secondary}/links", {"url": CS.url_of(DEAL), "connection_id": tool})
    assert _refresh(api) == 1
    close.set(tool, status="won")
    assert _refresh(api, H.shift(H.now(), minutes=6)) == 1
    assert _meta(api, tool)["fields"]["status_type"] == "won" and _meta(api, tool)["final_at"] is None
    assert _asks(api, task) == [("human:ben", "people_only", 0)]
    assert get(api, f"tasks/{task}")["task"]["status"] == "open"
    assert _asks(api, secondary) == []
    assert _refresh(api, H.shift(H.now(), minutes=6)) == 1
    assert _asks(api, task) == [("human:ben", "people_only", 0)]
    close.set(tool, status="lost")
    assert _refresh(api, H.shift(H.now(), minutes=6)) == 1
    assert _asks(api, task) == [("human:ben", "people_only", 1), ("human:ben", "people_only", 0)]
    del close.deals[tool, DEAL]
    assert _refresh(api, H.shift(H.now(), minutes=6)) == 0
    assert _meta(api, tool)["read_error"] == "Close answered 404"
    assert _asks(api, task) == [("human:ben", "people_only", 1)] * 2 + [("human:ben", "people_only", 0)]
    close.set(tool)
    assert _refresh(api, H.shift(H.now(), minutes=6)) == 1
    assert _asks(api, task) == [("human:ben", "people_only", 1)] * 3
    close.set(tool, status="won")
    assert _refresh(api, H.shift(H.now(), minutes=6)) == 1
    [link] = get(api, f"tasks/{task}/links")["links"]
    post(api, f"tasks/{task}/links", {"remove": link["id"]}, token="ben-test")
    assert _asks(api, task) == [("human:ben", "people_only", 1)] * 4
    assert _asks(api, secondary) == []


def test_a_failed_close_ask_withdrawal_retries_on_the_next_read(api, close, monkeypatch):
    tool = close.connect("Close Acme")
    close.set(tool, status="won")
    task = _create(api, _ref(tool))["id"]
    assert _refresh(api) == 1
    assert _asks(api, task) == [("human:ben", "people_only", 0)]

    def fail_advance(_conn, _task_id):
        raise RuntimeError("Cannot advance the task")

    close.set(tool)
    with monkeypatch.context() as patch:
        patch.setattr(task_engine, "advance", fail_advance)
        assert _refresh(api, H.shift(H.now(), minutes=6)) == 1
    assert _asks(api, task) == [("human:ben", "people_only", 0)]
    assert _refresh(api, H.shift(H.now(), minutes=6)) == 1
    assert _asks(api, task) == [("human:ben", "people_only", 1)]


def test_a_response_from_a_rotated_close_key_is_discarded(api, close):
    tool = close.connect("Close Acme")
    first = _create(api, _ref(tool))["id"]
    _record(api, tool, "Acme")
    other = "oppo_two"
    close.set(tool, status="won")
    close.set(tool, other, status="lost")

    def rotate():
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE credentials SET revision=revision+1 WHERE tool_id=?", (tool,))

    close.during = rotate
    second = _create(api, _ref(tool, other))
    assert not second["entity_id"] and _meta(api, tool, other) is None and _asks(api, second["id"]) == []
    assert _refresh(api) == 0
    assert _meta(api, tool) is None and _asks(api, first) == []


@pytest.mark.parametrize(("retry_after", "cooldown"), [("600", 600), ("1e100", 3600)])
def test_429_pauses_one_close_connection_including_create_lookups(api, close, time_machine, retry_after, cooldown):
    time_machine.move_to("2026-11-22T00:00:00Z", tick=False)
    cooldown_ends = H.shift(H.now(), seconds=cooldown)
    acme, beta = close.connect("Close Acme"), close.connect("Close Beta")
    _create(api, _ref(acme))
    _create(api, _ref(acme, "oppo_two"))
    _create(api, _ref(beta))
    close.set(beta)
    close.replies[acme, DEAL] = httpx.Response(429, headers={"Retry-After": retry_after})
    assert _refresh(api) == 1
    assert set(close.seen) == {(acme, DEAL), (beta, DEAL)}
    assert _meta(api, acme)["read_error"] == "Close answered 429"
    _record(api, acme, "Acme")
    close.set(acme, "oppo_three")
    time_machine.move_to(H.shift(cooldown_ends, seconds=-1), tick=False)
    assert _refresh(api) == 1 and all(key == DEAL for _, key in close.seen)
    assert not _create(api, _ref(acme, "oppo_three"))["entity_id"]
    assert (acme, "oppo_three") not in close.seen
    del close.replies[acme, DEAL]
    close.set(acme)
    time_machine.move_to(H.shift(cooldown_ends, seconds=1), tick=False)
    assert _refresh(api) == 2
    assert (acme, "oppo_three") in close.seen


def test_stale_or_failed_reads_are_not_inherited_and_create_respects_item_backoff(api, close, time_machine):
    time_machine.move_to("2026-11-22T00:00:00Z", tick=False)
    tool = close.connect("Close Acme")
    _record(api, tool, "Acme")
    close.set(tool)
    close.set(tool, "oppo_two")
    secondary = _create(api, None)["id"]
    for key in (DEAL, "oppo_two"):
        post(api, f"tasks/{secondary}/links", {"url": CS.url_of(key)})
    assert _refresh(api) == 2
    time_machine.move_to("2026-11-22T00:06:00Z", tick=False)
    del close.deals[tool, DEAL]
    del close.deals[tool, "oppo_two"]
    close.replies[tool, "oppo_two"] = httpx.Response(503)
    failed = _create(api, _ref(tool, "oppo_two"))
    assert not failed["entity_id"] and close.seen.count((tool, "oppo_two")) == 2
    assert _refresh(api) == 0
    assert not _create(api, _ref(tool))["entity_id"]
    assert close.seen.count((tool, DEAL)) == 2


def test_never_attempted_deals_go_before_previously_failed_deals(api, close, monkeypatch, time_machine):
    time_machine.move_to("2026-11-22T00:00:00Z", tick=False)
    monkeypatch.setattr(CS, "PER_CONNECTION", 1)
    tool = close.connect("Close Acme")
    _create(api, _ref(tool))
    assert _refresh(api) == 0
    assert close.seen == [(tool, DEAL)]
    close.set(tool, "oppo_two")
    _create(api, _ref(tool, "oppo_two"))
    time_machine.move_to("2026-11-22T00:06:00Z", tick=False)
    assert _refresh(api) == 1
    assert close.seen[-1] == (tool, "oppo_two")
