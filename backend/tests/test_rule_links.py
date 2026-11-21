"""Rules › Links (backend/rule_links.py): an agent's write that leaves the company meets the org rules and leaves one
outcome event on its link, the Assistant's and BotOps's for a person included; a person's own write is never gated;
nothing is stopped yet, except mail, whose held path is older; Rules › Links reads each link's mode and 30-day counts.
The proxy's case (checked before the key goes in, no credential read) is in test_tool_proxy.py."""

import base64
import json

import pytest

from backend import email_sending as S
from backend import org_rule_check as K
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
    answer = post(api, "release/now", {"note": "Our favorite color is gold"}, token="ops-test", key="k1")
    assert answer["sent"] is True
    [(actor, action, detail)] = link_events(api, "release")
    assert (actor, action) == ("bot:ops", "rules.held")
    assert detail == {"operation": "release", "rules": [escalate["id"]], "broken": [escalate["id"]], "would": True}
    # A replay of the same key, and an ask while one is open, write nothing: no call is spent and no event written.
    assert post(api, "release/now", {"note": "Our favorite color is gold"}, token="ops-test", key="k1") == answer
    assert post(api, "release/now", {"note": "Again"}, token="ops-test")["sent"] is False
    assert len(judge.read) == 1 and len(link_events(api, "release")) == 1
    store = api.app.state.store
    # A person's own write is never checked; BotOps acting for them is, as the person, with one event.
    assert L.check(store, judge, Identity("human:ana", "human"), "webhook", "webhook", "favorite color is") is None
    assert len(judge.read) == 1
    found = L.check(store, judge, Identity("human:ana", "human", via="botops"), "webhook", "webhook", "Hello")
    with store.transaction() as c:
        L.record(c, found)
    [(actor, action, detail)] = link_events(api, "webhook")
    assert (actor, action, detail["for"]) == ("bot:botops", "rules.passed", "human:ana") and "would" not in detail


def test_a_webhook_send_that_would_be_refused_is_never_judged(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}, "if_broken": "block"})
    # ops has no endpoint and no sending switch: the send is refused, and no call was spent on it.
    r = api.post(
        "/api/v2/webhooks/missing/send",
        json={"type": "custom.color", "data": {"say": "Our favorite color is gold"}},
        headers=headers("ops-test"),
    )
    assert r.status_code == 404 and judge.read == [] and link_events(api, "webhook") == []


def test_a_link_check_makes_one_call_at_most_and_no_part_call(ruled, monkeypatch):
    api, judge = ruled
    made = rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}, "if_broken": "block"})
    store = api.app.state.store
    found = L.check_as(store, judge, "bot:ops", "bot:ops", "", "tool:x", "tool_call", "Our favorite color is gold")
    assert (found["outcome"], found["broken"], len(judge.read)) == ("refused", [made["id"]], 1)
    # The same check through org_rule_check with no budget marks the held verdict for its part call; a link's never.
    marked = K.check_as(store, judge, "bot:ops", "bot:ops", "", "tool_call", "Hello there. Our favorite color is blue.")
    plain = K.check_as(store, judge, "bot:ops", "bot:ops", "", "tool_call", "Our favorite color is red", parts=False)
    assert "parts_later" in marked and "parts_later" not in plain
    # Rules that need two calls: unchecked, nothing asked.
    real = K.J.pack
    monkeypatch.setattr(K.J, "pack", lambda rules, build: (real(rules, build) or []) * 2)
    asked = len(judge.read)
    found = L.check_as(store, judge, "bot:ops", "bot:ops", "", "tool:x", "tool_call", "Our favorite color is green")
    assert (found["outcome"], found["unchecked"], len(judge.read)) == ("passed", "too_many_calls", asked)


def test_a_failure_to_log_leaves_the_write_as_it_was(ruled, monkeypatch):
    api, _ = ruled
    store = api.app.state.store
    found = {"link": "webhook", "actor": "bot:ops", "for": "bot:ops", "operation": "webhook"}
    found.update(outcome="passed", rules=[], broken=[], unchecked="")
    real = H.event

    def failing(c, actor, action, *rest, **kw):
        if action.startswith("rules."):
            raise RuntimeError("disk full")
        return real(c, actor, action, *rest, **kw)

    monkeypatch.setattr(L.H, "event", failing)
    with store.transaction() as c:
        H.event(c, "bot:ops", "webhook.sent", "w1", {})
        assert L.record(c, found) is None
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='webhook.sent'").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM events WHERE action LIKE 'rules.%'").fetchone()[0] == 0


TOKEN = "tpx1.eyJ0IjoiY2xvc2UifQ.c2lnbmF0dXJlLXZhbHVlLTEy"


@pytest.mark.parametrize(
    "rest,query,body,kind",
    [
        ("leads/" + TOKEN.replace(".", "%2E"), "", b"", ""),  # percent-encoded in the path
        ("leads", "q=" + TOKEN.replace(".", "%252E"), b"", ""),  # twice percent-encoded in a value
        ("leads", TOKEN + "=1", b"", ""),  # a query name
        ("leads", "basic=" + base64.b64encode(("u:" + TOKEN).encode()).decode().replace("+", "%2B"), b"", ""),
        ("leads", "", ('{"note": "\\u0074' + TOKEN[1:] + '"}').encode(), ""),  # a JSON escape
        (
            "leads",
            "",
            json.dumps({TOKEN: "key", "b": base64.b64encode(TOKEN.encode()).decode()}).encode(),
            "application/json",
        ),
        ("leads", "", ("a=" + TOKEN.replace(".", "%2E")).encode(), "application/x-www-form-urlencoded"),
        ("leads", "", ("see " + TOKEN).encode(), "text/plain; charset=utf-8"),
    ],
)
def test_what_the_check_reads_holds_no_credential_in_any_spelling(rest, query, body, kind):
    read = L.proxy_output("POST", rest, query, body, (), kind)
    assert L.clean(read) and "tpx1" not in read.lower() and "[token]" in read


def test_a_body_that_cannot_be_read_safely_is_named_not_read():
    read = L.proxy_output("POST", "files", "", b"\x89PNG" + TOKEN.encode(), (), "image/png")
    assert read.endswith(f"[a body of {4 + len(TOKEN)} bytes, not read]") and "tpx1" not in read
    assert "[key]" in L.proxy_output("POST", "x", "api_key=" + TOKEN, b"", {"api_key"})


def test_a_held_or_warned_mail_is_logged_on_the_email_link_with_the_rules_its_decision_applied(ruled):
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
    assert (actor, action, detail["rules"], detail["broken"]) == (
        "bot:ops",
        "rules.held",
        [blocked["id"]],
        [blocked["id"]],
    )
    assert "would" not in detail  # the mail really is held


def test_rules_links_lists_each_link_with_its_mode_and_30_day_counts(ruled, monkeypatch):
    api, _ = ruled
    store = api.app.state.store
    with store.transaction() as c:
        now = H.now()
        for tid, kind, delivery in (
            ("close", "api", "proxy"),
            ("acme", "api", "computer"),
            ("shop", "website", "proxy"),
            ("quiet", "api", "proxy"),
        ):
            c.execute(
                "INSERT INTO tools(id,name,kind,delivery,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (tid, tid.title(), kind, delivery, "human:ana", now, now),
            )
        # Only listed: the owner sees it, an Admin it does not name does not.
        c.execute("UPDATE tools SET audience=? WHERE id='quiet'", (json.dumps({"people": ["cara"]}),))
        H.event(c, "bot:ops", "rules.passed", "link:tool:close", {})
        H.event(c, "bot:ops", "rules.refused", "link:tool:close", {})
        old = H.event(c, "bot:ops", "rules.refused", "link:tool:close", {})
        c.execute("UPDATE events SET created_at=? WHERE id=?", (H.shift(now, days=-31), old["id"]))
    links = {row["id"]: row for row in get(api, "rule-links")["links"]}
    # Email is the one path that really stops a write; nothing relabels another.
    assert {k: v["mode"] for k, v in links.items()} == {
        "email": "enforced",
        "webhook": "informed",
        "support": "informed",
        "release": "informed",
        "tool:close": "informed",
        "tool:quiet": "informed",
        "tool:acme": "not_covered",
        "tool:shop": "not_covered",
        "computer": "not_covered",
    }
    assert links["tool:close"]["counts"] == {"passed": 1, "refused": 1, "held": 0}
    assert links["tool:acme"]["counts"] is None
    assert "tool:quiet" not in {row["id"] for row in get(api, "rule-links", token="ben-test")["links"]}
    assert api.get("/api/v2/rule-links", headers=headers("cara-test")).status_code == 403
    from backend import support_access

    monkeypatch.setattr(support_access, "check_live", lambda c, who: None)  # a live support session, as opened
    store.settings.test_identities["support-app"] = Identity("human:tico-support", "human", support="app")
    assert api.get("/api/v2/rule-links", headers=headers("support-app")).status_code == 403
