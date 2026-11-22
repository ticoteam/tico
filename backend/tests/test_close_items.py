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
from backend.entities import store as ES
from backend.item_sources import close as CS
from backend.item_sources import jira as JS
from backend.tests.helpers.api import get, post
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.jira_intake import PUBLIC, _patch_tool
from backend.tests.helpers.mcp import call
from backend.tests.helpers.org_rule_check import color_rule, ruled  # noqa: F401 - ruled is a fixture

DEAL = "oppo_one"
LEAD = "lead_one"


class FakeClose:
    def __init__(self, api):
        self.api = api
        self.keys = {}
        self.deals = {}
        self.seen = []

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
        fields = self.deals.get((tool, key))
        return httpx.Response(200 if fields else 404, json=fields)


@pytest.fixture
def close(api, monkeypatch):
    setup(api)
    fake = FakeClose(api)
    monkeypatch.setattr(egress, "resolve", lambda host: [ipaddress.ip_address(PUBLIC)])
    monkeypatch.setattr(JS, "TRANSPORT", httpx.MockTransport(fake))
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


def test_close_url_files_and_finds_a_typed_task_and_reads_metadata_every_five_minutes(api, close):
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
    assert _refresh(api) == 2
    assert (_meta(api, acme)["fields"]["value"], _meta(api, beta)["fields"]["value"]) == (50000, 12000)
    assert _asks(api, first["id"]) == [] and _asks(api, second["id"]) == [("human:ben", "people_only", 0)]


def test_close_needs_a_record_integration_and_unreadable_leads_do_not_set_entity(api, close):
    tool = close.connect("Close Acme", record=False)
    close.set(tool)
    assert _create(api, _ref(tool), expected=422)["error"]["code"] == "primary_link"
    assert close.seen == []
    _patch_tool(api, tool, {"record_owner": "outside"})
    private = _record(api, tool, "Private", restricted=True)
    assert not _create(api, _ref(tool), owner="cara", token="cara-test")["entity_id"]
    second = "oppo_two"
    close.set(tool, second)
    assert _create(api, _ref(tool, second))["entity_id"] == private


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


def test_a_won_primary_deal_asks_once_then_withdraws_on_reopen(api, close):
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
    close.set(tool)
    assert _refresh(api, H.shift(H.now(), minutes=6)) == 1
    assert _asks(api, task) == [("human:ben", "people_only", 1)]
    close.set(tool, status="lost")
    assert _refresh(api, H.shift(H.now(), minutes=6)) == 1
    assert _asks(api, task) == [("human:ben", "people_only", 1), ("human:ben", "people_only", 0)]
