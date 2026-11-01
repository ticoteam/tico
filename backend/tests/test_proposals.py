"""Proposals (redesign/CONTRACT.md §5, backend/proposals.py): a bot's change to a spend limit is an approval the top of
the org decides; on approve the hub applies it as that person, audited with via approval:<id>, spends the approval and
sends BotOps one notice. An org change is no proposal (test_org_by_request.py)."""
from backend.store import H
from backend.tests.test_api import api, get, headers, post  # noqa: F401  (the api fixture)
from backend.tests.test_groups import add_bots, send
from backend.tests.test_groups_leads import as_bot


def setup_org(api):
    """Engineering (ops) and Release (shipper), BotOps on hand, ops signing in as itself."""
    with api.app.state.store.transaction() as c:
        add_bots(c, botops=({}, "ana"), shipper=({}, "ana"))
    eng = send(api, "POST", "groups", {"name": "Engineering", "add": {"bots": ["ops"]}})["id"]
    release = send(api, "POST", "groups", {"name": "Release", "add": {"bots": ["shipper"]}})["id"]
    as_bot(api, "ops", "ops-test")
    return eng, release


def propose(api, kind, payload, token="ops-test", expected=200):
    r = api.post("/api/v2/approvals", json={"kind": kind, "payload": payload}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def audit(api, obj):
    return api.get("/api/v2/audit", params={"object": obj}, headers=headers()).json()["rows"]


def botops_notices(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT body, refs FROM messages WHERE from_actor=? AND to_actor=? "
                                           "AND kind='notice'", (H.KEEPER, "bot:botops"))]


def test_a_payload_the_hub_could_not_apply_is_refused_when_filed(api):
    setup_org(api)
    propose(api, "spend_limit", {"daily_usd": 5}, expected=422)                         # no bot, no pool
    propose(api, "spend_limit", {"bot": "ops", "pool": "company", "daily_usd": 5}, expected=422)
    propose(api, "spend_limit", {"pool": "team", "daily_usd": 5}, expected=422)


def test_an_org_or_group_change_is_no_proposal_any_more(api):
    eng, release = setup_org(api)
    propose(api, "org", {"bot": "bot:ops", "from": eng, "to": release}, expected=422)
    propose(api, "group", {"id": release, "name": "Shipping"}, expected=422)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0


def test_an_approved_spend_limit_applies_as_the_decider_with_its_audit_row(api):
    eng, _ = setup_org(api)
    aid = propose(api, "spend_limit", {"bot": "ops", "per_30m_usd": 1.5})["approval"]
    pool = propose(api, "spend_limit", {"pool": f"group:{eng}", "daily_usd": 40})["approval"]
    for each in (aid, pool):
        post(api, f"approvals/{each}", {"decision": "approved"})
    limits = get(api, "usage/limits")
    assert limits["bots"]["ops"]["per_30m_usd"] == 1.5
    assert limits["pools"]["groups"][eng]["daily_usd"] == 40
    [row] = audit(api, "spend:ops")
    assert row["via"] == f"approval:{aid}" and row["before"] is None and row["after"]["per_30m_usd"] == 1.5
    assert audit(api, f"spend:pool:group:{eng}")[0]["via"] == f"approval:{pool}"
    assert len(botops_notices(api)) == 2


def test_a_declined_proposal_changes_nothing_and_tells_nobody(api):
    setup_org(api)
    aid = propose(api, "spend_limit", {"bot": "ops", "daily_usd": 9})["approval"]
    post(api, f"approvals/{aid}", {"decision": "declined"})
    assert get(api, "usage/limits")["bots"]["ops"]["daily_usd"] is None and botops_notices(api) == []


def test_spend_and_group_changes_by_hand_are_audited(api):
    send(api, "PUT", "usage/limits/ops", {"daily_usd": 3})
    send(api, "PUT", "usage/limits/ops", {"daily_usd": 4})
    send(api, "PUT", "usage/limits/ops", {"daily_usd": 4})                              # no change, no row
    row, first = audit(api, "spend:ops")
    assert first["after"]["daily_usd"] == 3 and row["before"]["daily_usd"] == 3 and row["after"]["daily_usd"] == 4 and row["via"] is None
    gid = send(api, "POST", "groups", {"name": "Loud"})["id"]
    send(api, "DELETE", f"groups/{gid}")
    created, deleted = reversed(audit(api, f"group:{gid}"))
    assert created["before"] is None and created["after"]["name"] == "Loud" and deleted["after"] is None


def test_an_approved_spend_limit_changes_only_what_it_names(api):
    eng, _ = setup_org(api)
    for payload in ({"bot": "ops", "per_30m_usd": 1.5}, {"bot": "ops", "daily_usd": 20},
                    {"pool": f"group:{eng}", "per_30m_usd": 3}, {"pool": f"group:{eng}", "daily_usd": 40}):
        post(api, f"approvals/{propose(api, 'spend_limit', payload)['approval']}", {"decision": "approved"})
    limits = get(api, "usage/limits")
    assert limits["bots"]["ops"]["per_30m_usd"] == 1.5 and limits["bots"]["ops"]["daily_usd"] == 20
    pool = limits["pools"]["groups"][eng]
    assert pool["per_30m_usd"] == 3 and pool["daily_usd"] == 40
