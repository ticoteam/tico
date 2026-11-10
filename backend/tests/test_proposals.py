"""Proposals (redesign/CONTRACT.md §5, backend/proposals.py): a bot's change to a spend limit or a task cap is an OK the
top of the org gives from its preview (backend/task_asks.py); on that OK the hub applies it as that person, audited
with via approval:<id>, uses the OK and sends BotOps one notice. An org change is no proposal (test_org_by_request.py).
On a store with no app."""

import pytest

from backend import proposals, task_asks
from backend.auth import Auth, Identity
from backend.store import H, Problem

OPS = Identity("bot:ops", "bot")


@pytest.fixture
def store(company):
    company.auth = Auth(company)
    return company


def _ok(store, c, asked, person=None, ctx=True):
    """The person opens the proposal's preview and clicks OK on it."""
    person = person or proposals.decider(c)
    shown = task_asks.preview(c, person, asked["requirement_id"], own_click=True)["shown_hash"]
    context = (
        task_asks.Context(auth=store.auth, settings=store.settings, who=Identity(person, "human")) if ctx else None
    )
    task_asks.give_ok(c, person, asked["requirement_id"], shown, own_click=True, ctx=context)


def _audit(c, obj):
    return [dict(r) for r in c.execute("SELECT * FROM audit_log WHERE object=? ORDER BY rowid", (obj,))]


def test_a_payload_the_hub_could_not_apply_is_refused_when_asked(store):
    with store.transaction() as c:
        for kind, payload in (
            ("spend_limit", {"daily_usd": 5}),  # no bot, no pool
            ("spend_limit", {"bot": "ops", "pool": "company", "daily_usd": 5}),
            ("spend_limit", {"pool": "team", "daily_usd": 5}),
            ("org", {"bot": "bot:ops"}),
            ("mail_alert", {"bot": "ops", "outbound_send": True}),
        ):
            with pytest.raises(Problem):
                proposals.propose(c, OPS, kind, payload)
        assert c.execute("SELECT count(*) FROM ok_grants").fetchone()[0] == 0


def test_an_okd_spend_limit_applies_as_the_person_with_its_audit_row(store):
    with store.transaction() as c:
        asked = proposals.propose(c, OPS, "spend_limit", {"bot": "ops", "per_30m_usd": 1.5})
        assert asked["status"] == "pending" and asked["approval"] == asked["requirement_id"]
        task = H.task(c, asked["task_id"])
        assert (task["owner_id"], task["requester_id"]) == ("bot:ops", "bot:ops")
        _ok(store, c, asked)
        assert task_asks.standing(c, asked["requirement_id"]) == task_asks.USED
        assert H.task(c, asked["task_id"])["status"] == "done"
        [row] = _audit(c, "spend:ops")
        assert row["via"] == f"approval:{asked['requirement_id']}"


def test_an_ok_with_no_preview_route_behind_it_applies_nothing(store):
    with store.transaction() as c:
        asked = proposals.propose(c, OPS, "spend_limit", {"bot": "ops", "daily_usd": 9})
    with pytest.raises(H.Refused, match="own click"):
        with store.transaction() as c:
            _ok(store, c, asked, ctx=False)
    with store.read() as c:
        assert task_asks.standing(c, asked["requirement_id"]) == task_asks.WAITING
        assert _audit(c, "spend:ops") == []


def test_a_sent_back_proposal_changes_nothing(store):
    with store.transaction() as c:
        asked = proposals.propose(c, OPS, "spend_limit", {"bot": "ops", "daily_usd": 9})
        H.task_answer(c, proposals.decider(c), asked["task_id"], asked["requirement_id"], "no", "Too much.")
        assert task_asks.standing(c, asked["requirement_id"]) == task_asks.SENT_BACK
        assert _audit(c, "spend:ops") == [] and proposals.waiting(c, "spend_limit") == []
