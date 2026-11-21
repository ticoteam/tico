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
    """The org rule check's model: a rule is broken when what it reads says its needle ("favorite color is")."""

    def __init__(self, needle="favorite color is"):
        self.read, self.needle = [], needle

    def __call__(self, state, questions, label=None):
        self.read.append(state["output"])
        broken = self.needle in state["output"].lower()
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
    judge.needle = "release now"  # what a release ask is judged on: its name, never its note
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


def test_a_send_to_a_paused_endpoint_or_with_no_key_is_never_judged(ruled, monkeypatch):
    from backend import egress
    from backend.tests.helpers.webhooks import PUBLIC, make

    api, judge = ruled
    monkeypatch.setattr(egress, "resolve", lambda host: PUBLIC)
    rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}, "if_broken": "block"})
    hook = make(api, events=["custom.*"])
    post(api, f"webhooks/{hook['id']}/pause", {})
    with api.app.state.store.transaction() as c:  # ops may send outside on its own
        ana = api.app.state.auth.identity_for_actor(c, "human:ana")
        S.update_policy(c, ana, "bot:ops", S.PolicyUpdate(mode="on_their_own"))
    # ops sends to an endpoint that listens for it, but is paused: publish refuses it, and no call was spent.
    r = api.post(
        f"/api/v2/webhooks/{hook['id']}/send",
        json={"type": "custom.color", "data": {"say": "Our favorite color is gold"}},
        headers=headers("ops-test"),
    )
    assert r.status_code == 409, r.text
    assert judge.read == [] and link_events(api, "webhook") == []
    request = type("R", (), {"headers": {}, "url": type("U", (), {"path": "/api/v2/release/now"})()})()
    assert L.would_write(api.app.state.store, Identity("bot:ops", "bot"), request, lambda c: True) is False


def test_release_now_checks_again_when_the_open_task_went_between_its_read_and_its_write(ruled, monkeypatch):
    from backend import release_schedule_routes as RS

    api, judge = ruled
    assert api.patch("/api/v2/release-schedule", json={"bot": "ops"}, headers=headers()).status_code == 200
    rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}, "if_broken": "block"})
    judge.needle = "release now"
    real, reads = RS.would_file, []

    def stale_first(c, who):  # the first read still saw an open Release task, gone by the write
        reads.append(1)
        return len(reads) > 1 and real(c, who)

    monkeypatch.setattr(RS, "would_file", stale_first)
    assert post(api, "release/now", {"note": "Our favorite color is gold"}, token="ops-test")["sent"] is True
    assert len(judge.read) == 1 and [a for _, a, _ in link_events(api, "release")] == ["rules.refused"]


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


def test_the_check_reads_names_only_so_no_credential_can_reach_it():
    """A proxied write is judged on its names only: the tool, the method, MCP methods and tools, a JSON body's top-level
    key names. A body full of secrets (a token in a value, in base64, folded, or as a key) never reaches the judge."""
    basic = base64.b64encode(("u:" + TOKEN).encode()).decode()
    body = {
        "note": TOKEN,
        "auth": "Basic " + basic,
        "folded": TOKEN[:20] + "\r\n " + TOKEN[20:],
        "escaped": TOKEN.replace(".", "%252E"),
        TOKEN: "x",
        basic: 1,
        "name": "Acme",
    }
    read = L.proxy_output("Close CRM", "POST", json.dumps(body).encode())
    assert read == "POST to Close CRM\nFields: auth, escaped, folded, name, note"
    calls = [("tools/call", {"name": "create_lead", "arguments": {"q": TOKEN}}), ("tools/call", {"name": TOKEN})]
    assert (
        L.proxy_output("Close CRM", "POST", None, calls)
        == "POST to Close CRM\nMCP tools/call create_lead\nMCP tools/call"
    )
    for secret in (TOKEN, basic, TOKEN[20:], "c2lnbmF0dXJl"):
        assert secret not in read


def test_a_link_check_asks_one_provider_and_never_falls_back(ruled):
    api, _ = ruled
    from clients import judge as J

    rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}, "if_broken": "block"})
    asked = []

    def down(state, questions, label=None):
        asked.append("first")
        raise J.JudgeError("unavailable", "down")

    def up(state, questions, label=None):
        asked.append("second")
        return Judge()(state, questions, label)

    two = type("Two", (), {"steps": [down, up]})()
    found = L.check_as(api.app.state.store, two, "bot:ops", "bot:ops", "", "tool:x", "tool_call", "Our favorite color")
    assert (found["outcome"], found["unchecked"], asked) == ("passed", "unavailable", ["first"])


def test_a_send_that_became_eligible_after_its_preflight_is_logged_unchecked(ruled, monkeypatch):
    from backend import egress, webhooks as W
    from backend.tests.helpers.webhooks import PUBLIC, make

    api, judge = ruled
    monkeypatch.setattr(egress, "resolve", lambda host: PUBLIC)
    rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}, "if_broken": "block"})
    hook = make(api, events=["custom.*"])
    with api.app.state.store.transaction() as c:
        ana = api.app.state.auth.identity_for_actor(c, "human:ana")
        S.update_policy(c, ana, "bot:ops", S.PolicyUpdate(mode="on_their_own"))
    real, calls = W.publishable, []

    def paused_at_first(*a, **kw):  # the endpoint resumed between the preflight and the write
        calls.append(1)
        if len(calls) == 1:
            raise W.Refused("That endpoint is paused")
        return real(*a, **kw)

    monkeypatch.setattr(W, "publishable", paused_at_first)
    r = api.post(
        f"/api/v2/webhooks/{hook['id']}/send",
        json={"type": "custom.color", "data": {"say": "Our favorite color is gold"}},
        headers=headers("ops-test"),
    )
    assert r.status_code == 200, r.text
    [(actor, action, detail)] = link_events(api, "webhook")
    assert (actor, action, detail["unchecked"]) == ("bot:ops", "rules.unchecked", "changed_during_check")
    assert judge.read == []


def test_an_approved_mails_unchecked_recheck_keeps_its_reason_on_the_link():
    result = {"outcome": "pass", "rules": ["r1"], "unchecked": "too_many_calls", "approved": "h1"}
    found = L.mailed(("bot:ops", "bot:ops", ""), result)
    assert (found["outcome"], found["unchecked"], found["rules"]) == ("passed", "too_many_calls", ["r1"])


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
    with api.app.state.store.read() as c:
        [hold] = [json.loads(r[0]) for r in c.execute("SELECT rule_ids FROM org_rule_holds")]
    [(actor, action, detail)] = link_events(api, "email")
    assert detail["rules"] == hold
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
        # Before this tool was made: an earlier tool's under the same id, not counted.
        H.event(c, "bot:ops", "rules.unchecked", "link:tool:close", {"unchecked": "no_key"})  # counted apart
        earlier = H.event(c, "bot:ops", "rules.held", "link:tool:close", {})
        c.execute("UPDATE events SET created_at=? WHERE id=?", (H.shift(now, days=-1), earlier["id"]))
    links = {row["id"]: row for row in get(api, "rule-links")["links"]}
    # Email is the one path that really stops a write; nothing relabels another.
    assert {k: v["mode"] for k, v in links.items()} == {
        "email": "enforced",
        "webhook": "informed",
        "release": "informed",
        "tool:close": "informed",
        "tool:quiet": "informed",
        "tool:acme": "not_covered",
        "tool:shop": "not_covered",
        "support": "informed",
        "computer": "not_covered",
    }
    assert links["tool:close"]["counts"] == {"passed": 1, "refused": 1, "held": 0, "unchecked": 1}
    assert links["tool:acme"]["counts"] is None
    assert "tool:quiet" not in {row["id"] for row in get(api, "rule-links", token="ben-test")["links"]}
    assert api.get("/api/v2/rule-links", headers=headers("cara-test")).status_code == 403
    from backend import support_access

    monkeypatch.setattr(support_access, "check_live", lambda c, who: None)  # a live support session, as opened
    store.settings.test_identities["support-app"] = Identity("human:tico-support", "human", support="app")
    assert api.get("/api/v2/rule-links", headers=headers("support-app")).status_code == 403


def test_email_keeps_its_own_judge_and_falls_back_to_a_second_provider(ruled):
    """Email is the one Enforced link: its check is not a link check's (no one-call budget, no first provider only). A
    first provider down, the second answers, and the mail is held as before."""
    from clients import judge as J

    api, _ = ruled
    blocked = rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}, "if_broken": "block"})
    asked = []

    def down(state, questions, label=None):
        asked.append("first")
        raise J.JudgeError("unavailable", "down")

    def up(state, questions, label=None):
        asked.append("second")
        if str(label or "").startswith("rule-part@"):  # the held mail's part call: point at the part with the needle
            hit = next(p["id"] for p in state["parts"] if "favorite color is" in p["text"].lower())
            answers = {qid: {"type": "choice", "choice": hit, "confidence": 0.9} for qid in questions}
            return {"model": "jev-1.14", "usage": {"input_tokens": 10, "output_tokens": 0}, "ms": 1, "answers": answers}
        return Judge()(state, questions, label)

    api.app.state.judge = type("Two", (), {"steps": [down, up]})()
    snap = {
        "recipient_headers": {"to": ["vendor@outside.example"], "cc": [], "bcc": []},
        "subject": "Hi",
        "body": "Our favorite color is gold.",
    }
    gate = S.rule_gate(api.app, api.app.state.store, Identity("bot:ops", "bot", agent="test"), snap)
    with api.app.state.store.transaction() as c:
        assert gate(c)["outcome"] == "held"
    # The held mail's part call (org_rule_check._start_parts) asks the same chain on its own thread after the write:
    # wait for it, so both calls are counted, each falling back from the first provider to the second.
    K.settle_parts()
    assert asked == ["first", "second", "first", "second"]
    assert link_events(api, "email")[0][2]["rules"] == [blocked["id"]]
    # Both calls answered from the second provider, none failed: the part call's answer is the held part on the hold.
    with api.app.state.store.read() as c:
        calls = [
            json.loads(r[0]) for r in c.execute("SELECT detail FROM events WHERE action='judge.call' ORDER BY rowid")
        ]
        [verdict] = [json.loads(r[0]) for r in c.execute("SELECT verdict FROM org_rule_holds WHERE operation='mail'")]
    assert len(calls) == 2 and all(call.get("fallback") and "error" not in call for call in calls)
    assert verdict["parts"] == {blocked["id"]: "Our favorite color is gold."} and "parts_pending" not in verdict


def test_a_mail_decision_keeps_its_unchecked_reason_beside_a_directory_warning(ruled):
    """The judged check out (timeout) and a Directory Warn rule met: the mail goes with its warning, and the link event
    keeps both, the warned rule and why the judged check could not run."""
    api, _ = ruled
    verdict = {
        "author": "bot:ops",
        "operation": "mail",
        "recipients": [],
        "rules": [],
        "broken": [],
        "matched": [],
        "outcome": "unchecked",
        "error": "timeout",
        "draft": False,
        "obj": "",
        "structured_warned": [{"id": "r9", "if_broken": "warn", "text": "Say please."}],
    }
    with api.app.state.store.transaction() as c:
        out = K.mail_decide(c, {"author": "bot:ops", "key": "k1", "output": "Hi", "approved": None, "verdict": verdict})
    assert (out["outcome"], out["rules"], out["unchecked"]) == ("warn", ["r9"], "timeout")
    found = L.mailed(("bot:ops", "bot:ops", ""), out)
    assert (found["rules"], found["unchecked"]) == (["r9"], "timeout")


def test_a_large_mcp_batch_is_read_up_to_its_first_calls():
    calls = [("tools/call", {"name": f"tool_{n}"}) for n in range(L.CALLS_READ + 10)]
    read = L.proxy_output("Close CRM", "POST", None, calls).split("\n")
    assert read[1] == f"(the first {L.CALLS_READ} of {L.CALLS_READ + 10} MCP calls)"
    assert read[2:] == [f"MCP tools/call tool_{n}" for n in range(L.CALLS_READ)]  # calls 0-49, in order


def test_a_write_after_many_reads_in_one_batch_still_reaches_the_check():
    reads = [("tools/call", {"name": f"search_{n}"}) for n in range(60)]
    batch = [*reads, ("tools/call", {"name": "delete_issue"})]
    read = L.proxy_output("Close CRM", "POST", None, batch, lambda call: call[1]["name"] == "delete_issue")
    lines = read.split("\n")
    assert lines[2] == "MCP tools/call delete_issue" and lines[3:] == [f"MCP tools/call search_{n}" for n in range(49)]
