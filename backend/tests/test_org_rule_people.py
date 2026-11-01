"""Org rules check people's writes (docs/rules-plan.md): a person's message, and what their Assistant or BotOps writes
for them, is checked as that person and held as theirs. Acme is test_org_rules' fictional company: Ana owns it, Ben is
above Ops, Cara is on Support, and Eve is on no team."""
import json
import threading

import pytest

from backend import org_rule_check as K
from backend import org_rules as R
from backend import views
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import get, headers, post  # noqa: F401
from backend.tests.test_org_rule_check import COLOR, bodies, chat, color_rule, events, hold_row, ruled  # noqa: F401
from backend.tests.test_org_rules import api, as_bot, org, rule  # noqa: F401

EVERYONE = {"everyone": True}


def block_everyone(api, **more):
    return rule(api, {"kind": "internal", "text": COLOR, "who": EVERYONE, **more})


def write(api, token, text, to="ben", **more):
    r = api.post("/api/v2/messages", json={"to": to, "text": text, **more}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def status(api, path, token):
    return api.get("/api/v2/" + path, headers=headers(token)).status_code


def sql(api, query, args=()):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(query, args)]


def add_fay(api):
    """Fay is on the roster and on no team: she is neither the author, an editor nor an admin of the rule."""
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO humans(id, name, email) VALUES('fay', 'Fay', 'fay@acme.example')")
        raw = json.loads(c.execute("SELECT value_json FROM registry_metadata WHERE key='people'").fetchone()[0])
        raw["people"].append({"id": "fay", "email": "fay@acme.example", "name": "Fay"})
        c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'", (json.dumps(raw),))


def test_a_persons_message_is_held_by_a_block_rule_and_its_author_reads_it_with_the_reason(ruled):
    api, _ = ruled
    block_everyone(api)
    got = write(api, "cara-test", "Our favorite color is gold.")
    assert got["held"]["outcome"] == "blocked"
    hold = hold_row(api, got["held"]["id"])
    assert hold["author"] == "human:cara" and hold["state"] == "held"
    assert not bodies(api, "favorite color is gold")          # nothing is delivered
    mine = get(api, f"rule-holds/{hold['id']}", token="cara-test")
    assert mine["text"] == "Our favorite color is gold." and mine["can_withdraw"] and not mine["can_send"]
    assert [r["text"] for r in mine["rules"]] == [COLOR]        # the reason it was held
    assert "nonce" not in mine                                  # only a reviewer can send it
    # An admin reads it and may send it; a teammate with no part in the rule reads nothing.
    assert get(api, f"rule-holds/{hold['id']}", token="ben-test")["can_send"]
    add_fay(api)
    assert status(api, f"rule-holds/{hold['id']}", "fay-test") == 404


def test_a_persons_chat_message_held_in_their_direct_chat_is_listed_by_that_thread(ruled):
    api, _ = ruled
    chat(api, "Hello there.", to="finance", token="cara-test")          # the direct chat exists before the rule does
    block_everyone(api)
    hold_id = chat(api, "Our favorite color is gold.", to="finance", token="cara-test")["held"]["id"]
    with api.app.state.store.read() as c:
        thread = views.direct_chat(c, "human:cara", "finance")["id"]
    assert hold_row(api, hold_id)["payload_json"].count(thread) == 1     # the place names the chat
    listed = get(api, f"rule-holds?thread={thread}", token="cara-test")
    assert [h["id"] for h in listed["holds"]] == [hold_id]
    assert get(api, "rule-holds?thread=some-other-chat", token="cara-test")["holds"] == []


def test_send_anyway_is_for_the_rule_author_and_refused_to_the_author_and_to_others(ruled):
    api, _ = ruled
    block_everyone(api)                                         # Ana wrote the rule
    add_fay(api)
    hold_id = write(api, "cara-test", "Our favorite color is gold.")["held"]["id"]
    post(api, f"rule-holds/{hold_id}/send", {"nonce": "guess"}, token="cara-test", expected=404)
    post(api, f"rule-holds/{hold_id}/send", {"nonce": "guess"}, token="fay-test", expected=404)
    view = get(api, f"rule-holds/{hold_id}", token="ana-test")
    assert view["can_send"]
    sent = post(api, f"rule-holds/{hold_id}/send", {"nonce": view["nonce"]}, token="ana-test")
    assert sent["hold"]["state"] == "sent"
    assert len(bodies(api, "favorite color is gold")) == 1


def test_send_anyway_writes_the_message_as_its_person_not_as_a_bot(ruled):
    api, _ = ruled
    block_everyone(api)
    hold_id = write(api, "cara-test", "Our favorite color is gold.")["held"]["id"]
    view = get(api, f"rule-holds/{hold_id}", token="ana-test")
    post(api, f"rule-holds/{hold_id}/send", {"nonce": view["nonce"]}, token="ana-test")
    [row] = sql(api, "SELECT from_actor FROM messages WHERE body LIKE ?", ("%favorite color is gold%",))
    assert row["from_actor"] == "human:cara"


def test_edit_and_resend_takes_the_hold_back_and_the_resend_is_checked_again(ruled):
    api, judge = ruled
    block_everyone(api)
    hold_id = write(api, "cara-test", "Our favorite color is gold.")["held"]["id"]
    add_fay(api)
    post(api, f"rule-holds/{hold_id}/withdraw", {}, token="fay-test", expected=404)   # not the author
    assert post(api, f"rule-holds/{hold_id}/withdraw", {}, token="cara-test")["state"] == "kept"
    post(api, f"rule-holds/{hold_id}/withdraw", {}, token="cara-test", expected=409)  # already decided
    # Resent unchanged, it is checked and held again as a new hold; the same text and rules answer from the cache, so
    # the model is not asked a second time.
    again = write(api, "cara-test", "Our favorite color is gold.")["held"]["id"]
    assert again != hold_id and hold_row(api, again)["state"] == "held"
    # Edited, the new text passes and is delivered once; the first text is not.
    assert "held" not in write(api, "cara-test", "Our favorite shade is gold.")
    assert len(bodies(api, "favorite shade is gold")) == 1 and not bodies(api, "favorite color is gold")
    assert len(judge.calls) == 2 and hold_row(api, hold_id)["state"] == "kept"


def test_an_assistant_message_is_checked_and_held_as_the_person_it_is_written_for(ruled):
    api, judge = ruled
    block_everyone(api)
    settings = api.app.state.auth.settings
    bot = settings.assistant_bot
    as_bot(api, bot, "coo-test")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug=?", (bot,))
    # As `auth.chat_person` builds it: Ana, acting through her Assistant bot's credential.
    settings.test_identities["ana-assistant"] = Identity("human:ana", "owner", "ana@acme.example", via="assistant",
                                                         task_actor="bot:" + bot, agent="test", confirmed=True)
    got = write(api, "ana-assistant", "Our favorite color is gold.")
    assert got["held"]["outcome"] == "blocked" and len(judge.calls) == 1
    hold = hold_row(api, got["held"]["id"])
    assert hold["author"] == "human:ana"
    provenance = json.loads(hold["payload_json"])["provenance"]
    assert provenance["via"] == "assistant" and provenance["authenticated"] == "bot:" + bot
    assert get(api, f"rule-holds/{hold['id']}", token="ana-test")["text"] == "Our favorite color is gold."
    add_fay(api)
    assert status(api, f"rule-holds/{hold['id']}", "fay-test") == 404


@pytest.mark.parametrize("how", ["timeout", "budget", "error"])
def test_a_persons_message_goes_through_unchecked_when_the_check_fails(ruled, monkeypatch, how):
    api, judge = ruled
    block_everyone(api)
    if how == "timeout":
        monkeypatch.setitem(K.DEADLINES, "medium", 0.2)
        judge.hold = threading.Event()
    elif how == "budget":
        api.app.state.store.settings.rules_monthly_usd = 1.0
        with api.app.state.store.transaction() as c:
            H.event(c, H.KEEPER, "rules.spend", "rule-check@1", {"cost_usd": 5.0})
    else:
        judge.fail = RuntimeError("provider down")
    got = write(api, "cara-test", "Our favorite color is gold.")
    if judge.hold:
        judge.hold.set()
    assert "held" not in got and len(bodies(api, "favorite color is gold")) == 1
    [event] = events(api, "rules.unchecked")
    assert event["actor"] == "human:cara" and json.loads(event["detail_json"])["reason"] == how


def test_a_persons_message_with_no_rule_for_it_makes_no_call(ruled):
    api, judge = ruled
    color_rule(api)                                             # a rule for Ops' writes only
    assert "held" not in write(api, "cara-test", "Our favorite color is gold.")
    assert judge.calls == [] and len(bodies(api, "favorite color is gold")) == 1


def test_where_kind_dm_holds_direct_chats_and_not_a_group_conversation(ruled):
    api, judge = ruled
    block_everyone(api, where=["kind:dm"])
    assert write(api, "cara-test", "Our favorite color is gold.")["held"]["outcome"] == "blocked"
    group = post(api, "conversations", {"participants": ["ben", "ana"], "subject": "Palette"}, token="cara-test")
    assert "held" not in write(api, "cara-test", "Our favorite shade is gold.", conversation_id=group["id"])
    assert len(judge.calls) == 2


def test_where_team_holds_the_writes_of_that_group_and_not_anyone_else(ruled):
    api, _ = ruled
    block_everyone(api, where=["team:support"])                 # Cara is on Support; Ana is on no team
    assert write(api, "cara-test", "Our favorite color is gold.")["held"]["outcome"] == "blocked"
    assert "held" not in write(api, "ana-test", "Our favorite color is gold.")


def test_where_thread_holds_writes_in_that_conversation_only(ruled):
    api, _ = ruled
    group = post(api, "conversations", {"participants": ["ben", "ana"], "subject": "Palette"}, token="cara-test")
    block_everyone(api, where=[f"thread:{group['id']}"])
    assert write(api, "cara-test", "Our favorite color is gold.", conversation_id=group["id"])["held"]
    assert "held" not in write(api, "cara-test", "Our favorite shade is gold.")


def test_where_has_outside_members_matches_only_a_place_with_an_outside_member(ruled):
    api, _ = ruled
    made = block_everyone(api, where=["has_outside_members"])
    rule_id = (made.get("rule") or made)["id"]
    outside = {"thread": "t", "kind": "thread", "teams": [], "outside": True}
    inside = {"thread": "t", "kind": "thread", "teams": [], "outside": False}
    with api.app.state.store.read() as c:
        assert [r["id"] for r in R.matching(c, "human:cara", (), outside)] == [rule_id]
        assert R.matching(c, "human:cara", (), inside) == []
        assert R.matching(c, "human:cara", (), None) == []      # no place to read: the where rule does not match


def test_where_takes_the_four_targets_and_names_a_real_team_or_thread(ruled):
    api, _ = ruled
    base = {"kind": "internal", "text": COLOR, "who": EVERYONE}
    rule(api, {**base, "where": ["color:red"]}, expected=422)
    rule(api, {**base, "where": ["team:nobody"]}, expected=404)
    made = rule(api, {**base, "where": ["has_outside_members", "kind:dm", "team:support"]})
    assert set((made.get("rule") or made)["where"]) == {"has_outside_members", "kind:dm", "team:support"}


def test_people_have_their_own_daily_call_cap(ruled):
    api, judge = ruled
    block_everyone(api)
    api.app.state.store.settings.decisions_daily_calls_human = 0
    got = write(api, "cara-test", "Our favorite color is gold.")
    assert "held" not in got and judge.calls == []              # over the people's cap: delivered unchecked
    assert get(api, "rules/stats")["unchecked_today"] == 1
    # The bots' cap is their own: Ops' write is still checked.
    assert write(api, "ops-test", "Our favorite color is gold.", to="ana")["held"]["outcome"] == "blocked"
    assert len(judge.calls) == 1


def test_people_calls_and_spend_are_counted_apart_from_the_bots(ruled):
    api, _ = ruled
    with api.app.state.store.transaction() as c:
        H.event(c, H.KEEPER, "judge.call", "rule-check@1", {"people": True, "cost_usd": 2.5})
        H.event(c, H.KEEPER, "judge.call", "rule-check@1", {"cost_usd": 1.0})
        H.event(c, H.KEEPER, "judge.call", "person-ask-ingest@1", {"people": True, "cost_usd": 4.0})
    with api.app.state.store.read() as c:
        assert K.calls_today(c, people=True) == 1 and K.calls_today(c) == 1
    assert get(api, "rules/stats")["people_usd"] == 2.5


def test_an_inbound_message_is_held_with_its_source_and_origin(ruled):
    api, judge = ruled
    block_everyone(api)
    store, text = api.app.state.store, "Our favorite color is gold."
    verdict = K.inbound(store, judge, "human:cara", "message", text, ["human:ben"], source="slack",
                        authenticated="bot:slack", origin="C024BE91")
    assert verdict["outcome"] == "broken" and K.holds(verdict)
    assert verdict["author"] == "human:cara" and verdict["sender"] == "bot:slack" and verdict["via"] == "slack"
    assert verdict["origin"] == {"source": "slack", "origin": "C024BE91"}
    with store.transaction() as c:
        gate = K.apply(c, verdict, text, {"body": {}})
    provenance = json.loads(hold_row(api, gate["held"]["id"])["payload_json"])["provenance"]
    assert provenance["origin"] == {"source": "slack", "origin": "C024BE91"} and provenance["via"] == "slack"


def test_an_inbound_message_with_no_principal_is_held_as_its_source(ruled):
    api, judge = ruled
    block_everyone(api)
    verdict = K.inbound(api.app.state.store, judge, None, "message", "Our favorite color is gold.", ["human:ben"],
                        source="federation", origin="hub-b.example")
    assert verdict["author"] == "federation:hub-b.example" and K.holds(verdict)
