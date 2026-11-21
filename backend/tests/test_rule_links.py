"""Rules › Links (backend/rule_links.py): an agent's write that leaves the company meets the org rules and leaves one
outcome event on its link, the Assistant's and BotOps's for a person included; a person's own write is never gated;
nothing is stopped yet, except mail, whose held path is older; Rules › Links reads each link's mode and 30-day counts.
The proxy's case (checked before the key goes in, no credential read) is in test_tool_proxy.py."""

import json

import pytest

from backend import email_sending as S
from backend import rule_links as L
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.org_rules import org, rule

COLOR = "Never tell anyone Acme's favorite color."


class Judge:
    """The org rule check's model: a rule is broken when what it reads says "favorite color is"."""

    def __init__(self):
        self.read = []

    def __call__(self, state, questions, label=None):
        self.read.append(state["output"])
        broken = "favorite color is" in state["output"].lower()
        answers = {qid: {"type": "noul", "noul": 0.95 if broken else 0.05} for qid in questions}
        return {"model": "jev-1.14", "usage": {"input_tokens": 10, "output_tokens": 0}, "ms": 1, "answers": answers}


@pytest.fixture
def ruled(api):
    org(api)
    judge = api.app.state.judge = Judge()
    return api, judge


def link_events(api, link):
    with api.app.state.store.read() as c:
        rows = c.execute(
            "SELECT actor, action, detail FROM events WHERE target=? ORDER BY rowid", ("link:" + link,)
        ).fetchall()
    return [(r["actor"], r["action"], json.loads(r["detail"])) for r in rows]


def test_an_agents_outside_write_logs_one_would_be_outcome_and_a_persons_own_is_never_gated(ruled):
    api, judge = ruled
    escalate = rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}, "if_broken": "escalate"})
    patched = api.patch("/api/v2/release-schedule", json={"bot": "ops"}, headers=headers())
    assert patched.status_code == 200, patched.text
    # The Release Owner asks for a release: an Escalate rule broken would hold it, and it is filed all the same.
    answer = post(api, "release/now", {"note": "Our favorite color is gold"}, token="ops-test")
    assert answer["sent"] is True
    [(actor, action, detail)] = link_events(api, "release")
    assert (actor, action) == ("bot:ops", "rules.held")
    assert detail == {"operation": "release", "rules": [escalate["id"]], "broken": [escalate["id"]], "would": True}
    # Asked again while it is open, nothing is filed: no write, no event.
    assert post(api, "release/now", {"note": "Again"}, token="ops-test")["sent"] is False
    assert len(link_events(api, "release")) == 1
    store = api.app.state.store
    # A person's own write is never checked; BotOps acting for them is, as the person, with one event.
    asked = len(judge.read)
    assert L.check(store, judge, Identity("human:ana", "human"), "webhook", "webhook", "favorite color is") is None
    assert len(judge.read) == asked
    found = L.check(store, judge, Identity("human:ana", "human", via="botops"), "webhook", "webhook", "Hello")
    with store.transaction() as c:
        L.record(c, found)
    [(actor, action, detail)] = link_events(api, "webhook")
    assert (actor, action, detail["for"]) == ("bot:botops", "rules.passed", "human:ana") and "would" not in detail


def test_a_held_mail_is_logged_held_on_the_email_link_as_it_happened(ruled):
    api, _ = ruled
    blocked = rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}, "if_broken": "block"})
    snap = {
        "recipient_headers": {"to": ["vendor@outside.example"], "cc": [], "bcc": []},
        "subject": "Hi",
        "body": "Our favorite color is gold.",
    }
    gate = S.rule_gate(api.app, api.app.state.store, Identity("bot:ops", "bot", agent="test"), snap)
    with api.app.state.store.transaction() as c:
        assert gate(c)["outcome"] == "held"
    [(actor, action, detail)] = link_events(api, "email")
    assert (actor, action, detail["broken"]) == ("bot:ops", "rules.held", [blocked["id"]])
    assert "would" not in detail  # the mail really is held


def test_rules_links_lists_each_link_with_its_mode_and_30_day_counts(ruled):
    api, _ = ruled
    store = api.app.state.store
    with store.transaction() as c:
        now = H.now()
        for tid, kind, delivery in (
            ("close", "api", "proxy"),
            ("acme", "api", "computer"),
            ("shop", "website", "proxy"),
        ):
            c.execute(
                "INSERT INTO tools(id,name,kind,delivery,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (tid, tid.title(), kind, delivery, "human:ana", now, now),
            )
        H.event(c, "bot:ops", "rules.passed", "link:tool:close", {})
        H.event(c, "bot:ops", "rules.refused", "link:tool:close", {})
        old = H.event(c, "bot:ops", "rules.refused", "link:tool:close", {})
        c.execute("UPDATE events SET created_at=? WHERE id=?", (H.shift(now, days=-31), old["id"]))
        c.execute("INSERT INTO registry_metadata VALUES(?,?)", (L.MODES, json.dumps({"webhook": "enforced"})))
    links = {row["id"]: row for row in get(api, "rule-links")["links"]}
    assert {k: v["mode"] for k, v in links.items()} == {
        "email": "enforced",
        "webhook": "enforced",
        "support": "informed",
        "release": "informed",
        "tool:close": "informed",
        "tool:acme": "not_covered",
        "tool:shop": "not_covered",
        "computer": "not_covered",
    }
    assert links["tool:close"]["counts"] == {"passed": 1, "refused": 1, "held": 0}
    assert links["tool:acme"]["counts"] is None
    assert api.get("/api/v2/rule-links", headers=headers("cara-test")).status_code == 403
