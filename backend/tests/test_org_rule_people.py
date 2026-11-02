"""Org rules check people's writes (docs/rules-plan.md): a person's message, and what their Assistant or BotOps writes
for them, is checked as that person and held as theirs. Acme is test_org_rules' fictional company: Ana owns it, Ben is
above Ops, Cara is on Support, and Eve is on no team."""
import json
import threading

import pytest

from backend import org_rule_check as K
from backend import org_rules as R
from backend import threads as T
from backend import views
from backend.auth import Identity
from backend.store import H, Problem
from backend.tests.test_api import get, headers, post  # noqa: F401
from backend.tests.test_org_rule_check import COLOR, bodies, chat, color_rule, events, hold_row, ruled  # noqa: F401
from backend.tests.test_org_rules import api, as_bot, org, rule  # noqa: F401
from backend.tests.test_thread_api import new_thread, say  # noqa: F401

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
    place = json.loads(hold_row(api, hold_id)["payload_json"])["provenance"]["place"]
    assert place["thread"] == thread                                    # the place names the chat
    listed = get(api, f"rule-holds?thread={thread}", token="cara-test")
    assert [h["id"] for h in listed["holds"]] == [hold_id]
    assert get(api, "rule-holds?thread=some-other-chat", token="cara-test")["holds"] == []


def test_a_where_thread_rule_holds_a_task_comment_in_that_task_and_not_in_another(ruled):
    api, _ = ruled
    task = post(api, "tasks", {"owner": "ops", "title": "Write the launch note", "body": "Draft it."})
    other = post(api, "tasks", {"owner": "ops", "title": "Write the other note", "body": "Draft it."})
    thread = sql(api, "SELECT conversation_id FROM tasks WHERE id=?", (task["id"],))[0]["conversation_id"]
    rule(api, {"kind": "internal", "text": COLOR, "who": EVERYONE, "where": [f"thread:{thread}"]})
    held = post(api, f"tasks/{task['id']}/comments", {"text": "Our favorite color is red."}, token="ops-test")
    assert held["held"], held
    assert bodies(api, "favorite color is red") == []
    post(api, f"tasks/{other['id']}/comments", {"text": "Our favorite color is red."}, token="ops-test")
    assert len(bodies(api, "favorite color is red")) == 1          # the other task's thread is not the rule's


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


def test_where_kind_dm_holds_direct_chats_and_group_dms_and_not_a_thread(ruled):
    api, judge = ruled
    block_everyone(api, where=["kind:dm"])
    assert write(api, "cara-test", "Our favorite color is gold.")["held"]["outcome"] == "blocked"
    # A group conversation from the old route is a personal DM, as `threads.dm` makes one.
    group = post(api, "conversations", {"participants": ["ben", "ana"], "subject": "Palette"}, token="cara-test")
    assert write(api, "cara-test", "Our favorite hue is gold.", conversation_id=group["id"])["held"]
    assert len(judge.calls) == 2
    # A thread with members is no DM, whoever is in it: no call.
    thread = post(api, "threads", {"title": "Palette", "audience": "members", "members": ["human:ben"]}, token="cara-test")
    assert "held" not in write(api, "cara-test", "Our favorite shade is gold.", conversation_id=thread["id"])
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


def test_send_anyway_posts_a_held_thread_message_once_as_the_person(ruled):
    api, judge = ruled
    block_everyone(api)
    store, text = api.app.state.store, "Our favorite color is gold."
    with store.transaction() as c:
        thread = T.create(c, Identity("human:cara", "human"), title="Colors", audience="members",
                          members=["human:ben"])

    def place(c, author, recipients):
        return K.place_for(c, author, recipients, thread["id"])
    verdict = K.preflight(store, judge, Identity("human:cara", "human"), "threads", text, ["human:ben"],
                          place=place)
    assert verdict["outcome"] == "broken" and K.holds(verdict)
    replay = {"thread_id": thread["id"], "body": text, "kind": "say", "refs": None, "parent_message_id": None,
              "to_actor": None, "via": None, "origin": None, "attachments": None}
    with store.transaction() as c:
        held = K.apply(c, verdict, text, replay)["held"]
    assert bodies(api, "favorite color is gold") == []                    # held: nothing is written yet
    calls = len(judge.calls)
    view = get(api, f"rule-holds/{held['id']}")
    assert view["can_send"] and view["text"] == text
    sent = post(api, f"rule-holds/{held['id']}/send", {"nonce": view["nonce"]})
    assert sent["hold"]["state"] == "sent"
    assert len(bodies(api, "favorite color is gold")) == 1 and len(judge.calls) == calls   # no second check
    with store.read() as c:
        row = c.execute("SELECT from_actor, conversation_id FROM messages WHERE body=?", (text,)).fetchone()
    assert (row[0], row[1]) == ("human:cara", thread["id"])               # as the person, into the thread
    post(api, f"rule-holds/{held['id']}/send", {"nonce": view["nonce"]}, expected=409)


def test_a_held_first_message_makes_no_dm_until_its_send_anyway_posts_it(ruled):
    api, judge = ruled
    block_everyone(api)
    store, text = api.app.state.store, "Kickoff: our favorite color is gold."
    cara = Identity("human:cara", "human")
    verdict = K.preflight(store, judge, cara, "threads", text, ["human:ben"])
    with store.read() as c:
        conversations = c.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    with store.transaction() as c:
        held = T.post(c, cara, None, text, checked=verdict, start={"dm": ["human:ben"], "title": "Kickoff"})
    assert held["held"]["id"] and K.holds(verdict)
    with store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == conversations   # no DM yet
    assert bodies(api, "Kickoff:") == []
    view = get(api, f"rule-holds/{held['held']['id']}")
    assert view["can_send"]
    sent = post(api, f"rule-holds/{held['held']['id']}/send", {"nonce": view["nonce"]})
    assert sent["hold"]["state"] == "sent"
    with store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == conversations + 1
        row = c.execute("SELECT from_actor, conversation_id FROM messages WHERE body=?", (text,)).fetchone()
    assert (row[0], row[1] is not None) == ("human:cara", True)           # one message, as the person, in the new DM
    assert len(bodies(api, "Kickoff:")) == 1


def test_a_non_member_gets_404_for_a_held_message_and_no_hold_names_the_participants(ruled):
    api, judge = ruled
    block_everyone(api)
    add_fay(api)
    store = api.app.state.store
    with store.transaction() as c:
        conv = H.open_conversation(c, "human:cara", ["human:cara", "human:ben"], kind="chat", subject="Pay talk")
    text = "Pay talk: our favorite color is gold."
    r = api.post("/api/v2/messages", json={"to": "ben", "text": text, "conversation_id": conv["id"]},
                 headers=headers("fay-test"))
    assert r.status_code == 404, r.text
    assert sql(api, "SELECT COUNT(*) AS n FROM org_rule_holds")[0]["n"] == 0
    assert judge.calls == [] and bodies(api, "Pay talk") == []


def test_a_hold_is_made_only_for_the_audience_the_check_named(ruled):
    api, judge = ruled
    block_everyone(api)
    store, text = api.app.state.store, "Our favorite color is gold."
    cara = Identity("human:cara", "human")
    verdict = K.preflight(store, judge, cara, "threads", text, ["human:ben"])
    with store.transaction() as c:
        assert K.apply(c, verdict, text, {}, audience_now=["human:ben", "human:fay"]) == {"moved": True}
        assert c.execute("SELECT COUNT(*) FROM org_rule_holds").fetchone()[0] == 0
        held = K.apply(c, verdict, text, {}, audience_now=["human:ben", "human:cara"])
    assert held["held"]["id"]


def test_a_held_chat_upload_keeps_its_file_and_is_written_with_it_once_when_sent_anyway(ruled):
    api, judge = ruled
    block_everyone(api)                                         # Ana wrote the rule
    text = "Our favorite color is gold."
    response = api.post("/api/v2/uploads/chat/ops", data={"text": text},
                        files={"files": ("notes.txt", b"gold notes", "text/plain")}, headers=headers("cara-test"))
    assert response.status_code == 200, response.text
    held = response.json()["held"]
    assert held["outcome"] == "blocked" and held["can_withdraw"] and "nonce" not in held
    assert bodies(api, "favorite color is gold") == [] and len(judge.calls) == 1
    assert hold_row(api, held["id"])["operation"] == "chat_upload"
    view = get(api, f"rule-holds/{held['id']}", token="ana-test")
    assert post(api, f"rule-holds/{held['id']}/send", {"nonce": view["nonce"]}, token="ana-test")["hold"]["state"] == "sent"
    rows = sql(api, "SELECT from_actor, refs_json FROM messages WHERE body=?", (text,))
    assert len(rows) == 1 and rows[0]["from_actor"] == "human:cara"
    assert [a["name"] for a in json.loads(rows[0]["refs_json"])["attachments"]] == ["notes.txt"]
    assert len(judge.calls) == 1                                # no second check


def test_a_message_to_the_assistant_is_held_as_its_person_and_posted_only_when_sent_anyway(ruled):
    api, judge = ruled
    block_everyone(api)
    settings = api.app.state.auth.settings
    as_bot(api, settings.assistant_bot, "coo-test")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug=?", (settings.assistant_bot,))
    text = "Our favorite color is gold."
    held = post(api, "assistant/messages", {"text": text}, token="cara-test")["held"]
    assert held["outcome"] == "blocked" and len(judge.calls) == 1
    assert bodies(api, "favorite color is gold") == []
    hold = hold_row(api, held["id"])
    assert hold["author"] == "human:cara" and hold["operation"] == "assistant_message"
    view = get(api, f"rule-holds/{held['id']}", token="ana-test")
    assert post(api, f"rule-holds/{held['id']}/send", {"nonce": view["nonce"]}, token="ana-test")["hold"]["state"] == "sent"
    [row] = sql(api, "SELECT from_actor, to_actor FROM messages WHERE body=?", (text,))
    assert (row["from_actor"], row["to_actor"]) == ("human:cara", "bot:" + settings.assistant_bot)


def test_a_held_answer_is_the_hold_its_sender_can_act_on_and_the_rule_author_can_send_her_own(ruled):
    api, _ = ruled
    block_everyone(api)                                         # Ana wrote the rule
    held = write(api, "cara-test", "Our favorite color is gold.")["held"]
    assert held["can_withdraw"] and not held["can_send"] and "nonce" not in held
    assert held["text"] == "Our favorite color is gold." and held["reason"]
    own = write(api, "ana-test", "Our favorite color is gold.")["held"]
    assert own["can_send"] and own["can_withdraw"] and own["nonce"]
    assert post(api, f"rule-holds/{own['id']}/send", {"nonce": own["nonce"]}, token="ana-test")["hold"]["state"] == "sent"


def test_a_where_thread_rule_holds_a_post_to_that_thread_and_a_root_rule_holds_its_sub_threads(ruled):
    api, _ = ruled
    root = new_thread(api, title="Colors", members=("human:ben",))
    rule(api, {"kind": "internal", "text": COLOR, "who": EVERYONE, "where": [f"thread:{root['id']}"]})
    posted = api.post(f"/api/v2/threads/{root['id']}/messages", json={"text": "Our favorite color is gold."},
                      headers=headers("ben-test"))
    assert posted.status_code == 200, posted.text
    assert posted.json()["held"]["outcome"] == "blocked"
    assert bodies(api, "favorite color is gold") == []
    parent = say(api, root["id"], "Hello team.", token="ben-test")
    sub = new_thread(api, title="Sub", members=("human:ben",), parent_message_id=parent["id"])
    under = api.post(f"/api/v2/threads/{sub['id']}/messages", json={"text": "Our favorite color is gold."},
                     headers=headers("ben-test"))
    assert under.status_code == 200 and "held" in under.json(), under.text
    assert bodies(api, "favorite color is gold") == []


def test_a_thread_post_rechecks_its_audience_inside_the_write(ruled):
    """A rule With Ben holds a post while Ben reads the thread. Ben taken out between the check and the write: the check
    no longer describes the post, so nothing is held or written and the sender checks again."""
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": EVERYONE, "with": {"people": ["ben"]}})
    store, text = api.app.state.store, "Our favorite color is gold."
    cara = Identity("human:cara", "human")
    with store.transaction() as c:
        thread = T.create(c, cara, title="Colors", audience="members", members=["human:ben", "human:ana"])
        people = K.thread_people(c, {"thread_id": thread["id"]})
    verdict = K.preflight(store, judge, cara, "threads", text, people)
    assert K.holds(verdict) and verdict["recipients"] == ["human:ana", "human:ben"]
    with store.transaction() as c:
        gone = dict(c.execute("SELECT * FROM thread_members WHERE thread_id=? AND actor='human:ben'",
                              (thread["id"],)).fetchone())
        c.execute("DELETE FROM thread_members WHERE id=?", (gone["id"],))
    before = len(bodies(api, "favorite color is gold"))
    with store.transaction() as c, pytest.raises(Problem) as moved:
        T.post(c, cara, thread["id"], text, checked=verdict)
    assert moved.value.status == 409 and moved.value.code == "moved"
    with store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM org_rule_holds").fetchone()[0] == 0
    assert len(bodies(api, "favorite color is gold")) == before
    # The audience the check named still holds it, and the answer is the hold as its sender reads it.
    with store.transaction() as c:
        c.execute(f"INSERT INTO thread_members({','.join(gone)}) VALUES({','.join('?' * len(gone))})", tuple(gone.values()))
    with store.transaction() as c:
        held = T.post(c, cara, thread["id"], text, checked=verdict)
    assert held["held"]["can_withdraw"] and held["held"]["text"] == text and held["held"]["reason"]
