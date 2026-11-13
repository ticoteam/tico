"""Org rule checks (backend/org_rule_check.py, docs/rules-plan.md §3): a bot's output is checked against the rules that
apply to it before it lands. One Decide call outside the write's transaction; a held write answers 200 with `held`
and writes nothing but its hold; Escalate files one review task for a person; Warn delivers with a line; any
failure lets the output through with `rules.unchecked`, except an output too large to check, which is held.

Acme is the fictional company (backend/tests/test_org_rules.py's org): Ana owns it, Ben is the person above Ops."""

import json
import threading
import time
from types import SimpleNamespace

import pytest

from backend import call_slots as CS
from backend import org_rule_check as K
from backend import thread_attention
from backend import threads
from backend import updates
from backend.auth import Identity
from backend.names import SUPPORT_ACTOR, SUPPORT_NAME
from backend.store import H, Problem
from backend.tests.helpers.api import claim, get, headers, post, setup_attempt  # noqa: F401
from backend.tests.helpers.org_rules import as_bot, edit, org, rule  # noqa: F401
from backend.tests.helpers.org_rule_check import LAUNCH, hand_over
from backend import bot_settings as BS


@pytest.fixture(autouse=True)
def _not_a_friday(monkeypatch):
    """A Friday's update must be a week in review (updates.kind_for); these tests post daily ones, so pin a Tuesday."""
    from backend import updates as _updates

    monkeypatch.setattr(_updates, "today", lambda at=None: "2026-09-29")


COLOR = "Never tell anyone Acme's favorite color."
NEEDLE = "favorite color is"


GATEWAY_MOST = 40  # the Gateway answers a call of 41 or more questions with an error (production, 2026-11-13)


class Judge:
    """Answers rule-check@1, refusing more than GATEWAY_MOST questions as the Gateway does: a rule is broken when the output holds that rule's needle. It notes whether the store's
    write lock was held when it was asked, which must never happen."""

    def __init__(self, store, needles=None):
        self.store, self.needles = store, dict(needles or {COLOR: NEEDLE})
        self.calls, self.locked, self.fail, self.hold = [], [], None, None

    def __call__(self, state, questions, label=None):
        self.locked.append(bool(self.store.writer.queue))
        self.calls.append({"state": state, "questions": questions, "label": label})
        if len(questions) > GATEWAY_MOST:
            from clients import judge as J

            raise J.JudgeError("unavailable", "The decision model answered with an error")
        if self.hold:
            self.hold.wait(5)
        if self.fail:
            raise self.fail
        text = (state["output"] + " " + json.dumps(state.get("context") or {})).lower()
        answers = {}
        for rule_row in state["rules"]:
            broken = any(key in rule_row["text"] and needle.lower() in text for key, needle in self.needles.items())
            answers[rule_row["id"]] = {"type": "noul", "noul": 0.95 if broken else 0.05}
        return {"model": "jev-1.14", "usage": {"input_tokens": 300, "output_tokens": 0}, "ms": 40, "answers": answers}


@pytest.fixture
def ruled(api):
    org(api)
    judge = Judge(api.app.state.store)
    api.app.state.judge = judge
    return api, judge


def color_rule(api, if_broken="block", **more):
    return rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}, "if_broken": if_broken, **more})


def say(api, text, to="ana", token="ops-test", **more):
    r = api.post("/api/v2/messages", json={"to": to, "text": text, **more}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def sql(api, query, args=()):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(query, args)]


def events(api, action):
    return sql(api, "SELECT actor, target, detail FROM events WHERE action=? ORDER BY rowid", (action,))


def bodies(api, text):
    return sql(api, "SELECT id FROM messages WHERE body LIKE ?", (f"%{text}%",))


def hold_row(api, hold_id):
    return sql(api, "SELECT * FROM org_rule_holds WHERE id=?", (hold_id,))[0]


# ---- what is checked ----------------------------------------------------------------------------------------------


def test_no_matching_rule_makes_no_call(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["finance"]}})
    assert "held" not in say(api, "My favorite color is blue")
    assert judge.calls == []


def test_a_short_break_is_held_with_a_200_and_never_a_refusal(ruled):
    api, judge = ruled
    color_rule(api)
    got = say(api, "My favorite color is blue")
    assert got["held"]["outcome"] == "blocked" and "tico rules appeal" in got["held"]["reason"]
    assert bodies(api, "favorite color") == []
    assert len(judge.calls) == 1 and judge.locked == [False]
    # A hold is an answer, not a refusal: the bot is not quarantined and keeps working.
    assert sql(api, "SELECT state FROM bots WHERE slug='ops'")[0]["state"] == "active"
    assert "held" not in say(api, "The deploy is green.")
    assert [json.loads(e["detail"])["hold"] for e in events(api, "rules.blocked")] == [got["held"]["id"]]
    assert "blue" not in json.dumps(events(api, "rules.blocked"))


def test_the_end_of_a_long_output_is_judged_whole(ruled):
    api, judge = ruled
    color_rule(api)
    text = "The weekly numbers look fine. " * 3000 + "By the way, my favorite color is blue."
    assert say(api, text)["held"]
    assert judge.calls[0]["state"]["output"] == text


def test_too_large_to_check_is_held_when_any_rule_it_meets_is_block(ruled, monkeypatch):
    api, judge = ruled
    color_rule(api)
    rule(api, {"kind": "internal", "text": "Keep replies short.", "who": {"bots": ["ops"]}, "if_broken": "warn"})
    from clients import judge as J

    monkeypatch.setattr(J, "MAX_STATE_CHARS", 2000)
    got = say(api, "All good here. " * 400)
    assert got["held"]["too_large"] and judge.calls == []
    assert bodies(api, "All good here") == []


def test_too_large_under_warn_rules_only_is_delivered_with_its_line_and_counted_unchecked(ruled, monkeypatch):
    api, judge = ruled
    color_rule(api, if_broken="warn")
    from clients import judge as J

    monkeypatch.setattr(J, "MAX_STATE_CHARS", 2000)
    text = "All good here. " * 400
    got = say(api, text)
    assert "held" not in got and got["rule_warning"]["too_large"] and judge.calls == []
    [row] = sql(api, "SELECT body FROM messages WHERE body LIKE ?", ("%All good here%",))
    assert row["body"] != text and row["body"].endswith(text.rstrip())  # the warning line, then the text whole
    assert [json.loads(e["detail"])["reason"] for e in events(api, "rules.unchecked")] == ["too_large"]
    assert events(api, "rules.warned") == [] and get(api, "rules/stats")["unchecked_today"] == 1


def test_a_persons_writes_and_their_botops_and_assistant_writes_are_checked_as_that_person(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"everyone": True}})
    store = api.app.state.store
    as_botops = Identity("human:ana", "owner", "ana@acme.example", via="botops", task_actor="bot:botops")
    verdict = K.preflight(store, judge, as_botops, "message", "Acme's favorite color is green", ["human:ben"])
    assert verdict["author"] == "human:ana" and verdict["sender"] == "bot:botops" and verdict["via"] == "botops"
    assert verdict["outcome"] == "broken" and K.holds(verdict)
    # Her own write, and her Assistant's, are held as her too: the same text is one check, the Assistant's the same
    # verdict from the cache.
    own = Identity("human:ana", "owner", "ana@acme.example")
    assert (
        K.preflight(store, judge, own, "message", "Acme's favorite color is green", ["human:ben"])["author"]
        == "human:ana"
    )
    assisted = Identity("human:ana", "owner", "ana@acme.example", via="assistant")
    assert (
        K.preflight(store, judge, assisted, "message", "Acme's favorite color is green", ["human:ben"])["author"]
        == "human:ana"
    )
    assert "held" in say(api, "My favorite color is green", to="ben", token="ana-test")


def test_an_all_bots_rule_covers_a_bot_made_later(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"all_bots": True}})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id, slug, display_name, state) VALUES('late-bot', 'late-bot', 'Late', 'active')")
        c.execute("INSERT INTO bot_config(bot,team,owner_id) VALUES('late-bot','','ana')")
        BS.write(c, 'late-bot', {})
    as_bot(api, "late-bot", "late-test")
    assert say(api, "Our favorite color is teal", token="late-test")["held"]


# ---- every hook ---------------------------------------------------------------------------------------------------


def task_for_ops(api, title="Write the launch note"):
    return post(api, "tasks", {"owner": "ops", "title": title, "body": "Draft it."})


def test_a_safe_comment_then_a_forbidden_edit_keeps_the_old_text(ruled):
    api, _ = ruled
    color_rule(api)
    task = task_for_ops(api)
    made = post(api, f"tasks/{task['id']}/comments", {"text": "Draft is in progress."}, token="ops-test")
    mid = made["comment"]["id"]
    r = api.post(
        f"/api/v2/tasks/{task['id']}/comments/{mid}",
        json={"text": "Our favorite color is red."},
        headers=headers("ops-test"),
    )
    assert r.status_code == 200 and r.json()["held"], r.text
    assert sql(api, "SELECT body FROM messages WHERE id=?", (mid,))[0]["body"] == "Draft is in progress."
    held = post(api, f"tasks/{task['id']}/comments", {"text": "Fun fact: our favorite color is red."}, token="ops-test")
    assert held["held"] and bodies(api, "Fun fact") == []


def test_a_task_body_done_note_or_hand_off_why_held_leaves_the_task_unchanged(ruled):
    api, _ = ruled
    color_rule(api)
    task = task_for_ops(api)
    for path, change in (
        (f"tasks/{task['id']}", {"body": "Note: our favorite color is red."}),
        (f"tasks/{task['id']}/done", {"note": "Done; favorite color is red"}),
        (f"tasks/{task['id']}/owner", {"owner": "ana", "why": "Over to you: our favorite color is red"}),
    ):
        got = post(api, path, {"version": task["version"], **change}, token="ops-test")
        assert got["held"], got
    row = sql(api, "SELECT body, status, owner_id, version FROM tasks WHERE id=?", (task["id"],))[0]
    assert row["body"] == "Draft it." and row["status"] != "done" and row["owner_id"] == "bot:ops"
    assert row["version"] == task["version"]


def test_a_new_task_from_a_bot_is_held(ruled):
    api, _ = ruled
    color_rule(api)
    got = post(
        api,
        "tasks",
        {"owner": "ana", "title": "Pick a palette", "body": "Our favorite color is red."},
        token="ops-test",
    )
    assert got["held"] and sql(api, "SELECT id FROM tasks WHERE title='Pick a palette'") == []


def test_a_note_to_a_bot_and_a_wake_bot_step_are_checked(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}})
    got = post(api, "notes", {"to": "finance", "text": "FYI our favorite color is red."}, token="ops-test")
    assert got["held"] and bodies(api, "FYI our favorite") == []
    aid = post(
        api,
        "automations",
        {
            "name": "Tell",
            "owner": "bot:ops",
            "trigger": {"type": "event", "name": "x.y"},
            "steps": [
                {
                    "id": "w",
                    "type": "action",
                    "do": "wake_bot",
                    "args": {"bot": "finance", "text": "Wake: favorite color is red"},
                }
            ],
        },
    )["id"]
    post(api, f"automations/{aid}/run", {})
    assert bodies(api, "Wake: favorite") == []
    assert len(events(api, "rules.blocked")) == 2


def test_a_held_completion_shows_a_note_and_the_text_nowhere(ruled, monkeypatch):
    api, judge = ruled
    color_rule(api)
    r, msg, attempt = setup_attempt(api)
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "local-thread"}, token=r["token"])
    secret = "Sure: Acme's favorite color is blue."
    stream = {"events": [{"seq": 1, "kind": "delta", "payload": {"text": secret}}]}
    post(api, f"attempts/{aid}/events", stream, token=r["token"])
    # Live: the run shows it works, never the reply text before it is checked.
    page = f"/api/v2/conversations/{msg['conversation_id']}/snapshot"
    assert "favorite color" not in api.get(page, headers=headers()).text
    assert "favorite color" not in api.get(f"/api/runs/{aid}/log", headers=headers()).text
    with monkeypatch.context() as m:  # the same page shows live text for a bot no rule names
        m.setattr(K, "watched", lambda c, bot: False)
        assert "favorite color" in api.get(page, headers=headers()).text
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": secret, "last_seq": 1}, token=r["token"])
    messages = get(api, f"conversations/{msg['conversation_id']}/messages")
    assert "reply was held" in messages[-1]["body"] and "blue" not in json.dumps(messages)
    for query in (
        "SELECT final_text AS t FROM attempts WHERE id=?",
        "SELECT result AS t FROM attempts WHERE id=?",
        "SELECT payload AS t FROM attempt_events WHERE attempt_id=?",
    ):
        assert all("blue" not in str(row["t"] or "") for row in sql(api, query, (aid,))), query
    log = api.get(f"/api/runs/{aid}/log", headers=headers())
    assert "blue" not in log.text
    assert "blue" not in json.dumps(sql(api, "SELECT * FROM attempt_captures"))


# ---- outcomes -----------------------------------------------------------------------------------------------------


def test_escalate_goes_to_the_person_above_when_ops_reports_to_a_bot(ruled):
    api, _ = ruled
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='cpo' WHERE bot='ops'")
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='cpo'")
    color_rule(api, if_broken="escalate")
    got = say(api, "Our favorite color is gold")
    hold = hold_row(api, got["held"]["id"])
    assert hold["reviewer"] == "human:ben"
    task = sql(api, "SELECT owner_id, body FROM tasks WHERE id=?", (hold["task_id"],))[0]
    assert task["owner_id"] == "human:ben" and hold["id"] in task["body"] and "gold" not in task["body"]


def test_the_third_block_in_a_day_escalates_with_one_review_task(ruled):
    api, _ = ruled
    color_rule(api)
    outcomes = [say(api, f"Try {n}: our favorite color is gold")["held"]["outcome"] for n in range(4)]
    assert outcomes == ["blocked", "blocked", "escalated", "escalated"]
    tasks = {row["task_id"] for row in sql(api, "SELECT task_id FROM org_rule_holds WHERE task_id IS NOT NULL")}
    assert len(tasks) == 1


def test_an_old_rule_review_row_takes_the_plain_title_and_no_other_held_row_does(ruled):
    api, _ = ruled
    color_rule(api)
    [say(api, f"Try {n}: our favorite color is gold") for n in range(3)]
    [review] = sql(
        api,
        "SELECT id, conversation_id FROM tasks WHERE id IN "
        "(SELECT task_id FROM org_rule_holds WHERE task_id IS NOT NULL)",
    )
    with api.app.state.store.transaction() as c:
        # Rows as main wrote them: the review's thread took the task's "Held: ..." title, and a screening hold keeps its own.
        c.execute(
            "UPDATE conversations SET subject=? WHERE id=?", ("Held: ops and the rule 'old'", review["conversation_id"])
        )
        screening = c.execute(
            "INSERT INTO conversations(id, kind, subject, participants, scope, created_at) "
            "VALUES('screen-held', 'task', 'Held: Order', '[]', 'task', '2026-11-01')"
        ).rowcount
        assert screening == 1
        assert threads.rename_rule_reviews(c) == 1
    subjects = {
        r["id"]: r["subject"]
        for r in sql(
            api, "SELECT id, subject FROM conversations WHERE id IN (?, 'screen-held')", (review["conversation_id"],)
        )
    }
    assert subjects == {review["conversation_id"]: "Rule review", "screen-held": "Held: Order"}


def test_cache_hits_count_toward_repeats(ruled):
    api, judge = ruled
    color_rule(api)
    outcomes = [say(api, "Our favorite color is gold")["held"]["outcome"] for _ in range(3)]
    assert outcomes[-1] == "escalated" and len(judge.calls) == 1


def test_warn_delivers_with_a_line_and_a_named_reviewer_round_trips(ruled):
    api, _ = ruled
    made = color_rule(api, if_broken="warn", reviewer="ben")
    read = get(api, f"rules/{made['id']}")
    assert (read.get("rule") or read)["reviewer"]["id"] in ("ben", "human:ben")
    got = say(api, "Our favorite color is gold")
    assert "held" not in got and got["rule_warning"]
    body = sql(api, "SELECT body FROM messages WHERE body LIKE '%favorite color is gold%'")[0]["body"]
    assert body.startswith("Warning:")
    assert len(events(api, "rules.warned")) == 1


def test_send_anyway_delivers_once_with_no_second_call(ruled):
    api, judge = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    held = say(api, "Our favorite color is gold")["held"]
    view = get(api, f"rule-holds/{held['id']}", token="ben-test")
    assert view["text"] == "Our favorite color is gold" and view["can_send"]
    sent = post(api, f"rule-holds/{held['id']}/send", {"nonce": view["nonce"]}, token="ben-test")
    assert sent["hold"]["state"] == "sent"
    post(api, f"rule-holds/{held['id']}/send", {"nonce": view["nonce"]}, token="ben-test", expected=409)
    assert len(bodies(api, "favorite color is gold")) == 1 and len(judge.calls) == 1
    task = hold_row(api, held["id"])["task_id"]
    assert sql(api, "SELECT status FROM tasks WHERE id=?", (task,))[0]["status"] in ("done", "closed")


def _nobody_near_ops(api):
    """Ops with no person up its chain, no operator, and a default person (Dan) who has left the team."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id='', reports_to=NULL WHERE bot='ops'")
        BS.remove(c, 'ops', 'reports_to')
        c.execute("INSERT INTO humans(id, name, email) VALUES('dan', 'Dan', 'dan@acme.example')")
        raw = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        raw["default_user"] = "dan"
        raw["people"].append({"id": "dan", "email": "dan@acme.example", "hidden": True})
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(raw),))
    with api.app.state.store.read() as c:
        from backend.report_managers import person_above

        assert not person_above(c, "ops") and H.default_human(c) == "dan"


def test_the_owner_lists_and_decides_a_hold_whose_reviewer_lost_sign_in_and_a_member_sees_none(ruled):
    api, _ = ruled
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id, name, email) VALUES('dan', 'Dan', 'dan@acme.example')")
        raw = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        raw["people"].append({"id": "dan", "email": "dan@acme.example"})
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(raw),))
    color_rule(api, if_broken="escalate", reviewer="dan")
    row = hold_row(api, say(api, "Our favorite color is gold")["held"]["id"])
    assert row["reviewer"] == "human:dan"
    listed = f"rule-holds?task={row['task_id']}"
    assert get(api, listed, token="cara-test")["holds"] == []  # Dan reviews it while Dan can sign in, the owner too
    assert [h["id"] for h in get(api, listed, token="ana-test")["holds"]] == [row["id"]]
    with api.app.state.store.transaction() as c:
        raw = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        next(p for p in raw["people"] if p["id"] == "dan")["sign_in"] = False
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(raw),))
    assert get(api, listed, token="cara-test")["holds"] == []
    [card] = get(api, listed, token="ana-test")["holds"]
    assert card["id"] == row["id"] and card["can_send"]
    nonce = get(api, f"rule-holds/{row['id']}", token="ana-test")["nonce"]
    post(api, f"rule-holds/{row['id']}/send", {"nonce": nonce}, token="ana-test")
    assert hold_row(api, row["id"])["state"] == "sent"


def test_an_escalate_with_nobody_near_the_bot_files_its_review_task_for_the_owner(ruled):
    api, _ = ruled
    _nobody_near_ops(api)
    color_rule(api, if_broken="escalate", reviewer="dan")  # the named reviewer has left too
    held = say(api, "Our favorite color is gold")["held"]
    assert held["outcome"] == "escalated"
    row = hold_row(api, held["id"])
    assert row["reviewer"] == "human:ana"
    assert sql(api, "SELECT owner_id FROM tasks WHERE id=?", (row["task_id"],))[0]["owner_id"] == "human:ana"
    view = get(api, f"rule-holds/{held['id']}")
    assert post(api, f"rule-holds/{held['id']}/send", {"nonce": view["nonce"]})["hold"]["state"] == "sent"


def test_a_hold_with_no_reviewer_is_a_block_the_owner_or_an_admin_releases(ruled, monkeypatch):
    api, _ = ruled
    _nobody_near_ops(api)
    monkeypatch.setattr(K, "owner_or_admin", lambda c: None)  # no person on the team at all
    color_rule(api, if_broken="escalate")
    held = say(api, "Our favorite color is gold")["held"]
    row = hold_row(api, held["id"])
    assert held["outcome"] == "blocked" and row["reviewer"] is None and row["task_id"] is None
    get(api, f"rule-holds/{held['id']}", token="cara-test", expected=404)  # a plain member never reads it
    view = get(api, f"rule-holds/{held['id']}", token="ben-test")  # an admin decides it
    assert view["can_send"] and post(api, f"rule-holds/{held['id']}/keep", {}, token="ben-test")["state"] == "kept"
    again = say(api, "Our favorite color is teal")["held"]
    view = get(api, f"rule-holds/{again['id']}")  # the owner sends one anyway
    assert post(api, f"rule-holds/{again['id']}/send", {"nonce": view["nonce"]})["hold"]["state"] == "sent"
    assert len(bodies(api, "favorite color is teal")) == 1


def _hire(api, pid):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id, name, email) VALUES(?, ?, ?)", (pid, pid.title(), f"{pid}@acme.example"))
        raw = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        raw["people"].append({"id": pid, "email": f"{pid}@acme.example"})
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(raw),))


def _leave(api, pid):
    """The person's sign-in is turned off: they no longer count as a reviewer who is present."""
    with api.app.state.store.transaction() as c:
        raw = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        next(p for p in raw["people"] if p["id"] == pid)["sign_in"] = False
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(raw),))


def _app_support():
    """Tico support in an app-only session: a human with no roster row, reading as an Admin does everywhere else."""
    return Identity(SUPPORT_ACTOR, "human", via_token=True, token_label=SUPPORT_NAME, token_id="sa_test", support="app")


def test_with_the_reviewer_gone_app_only_tico_support_reads_no_bot_hold_and_the_owner_and_admin_do(ruled):
    api, _ = ruled
    _hire(api, "dan")
    color_rule(api, if_broken="escalate", reviewer="dan")
    held = say(api, "Our favorite color is gold")["held"]
    assert hold_row(api, held["id"])["reviewer"] == "human:dan"
    _leave(api, "dan")
    app, auth = _app_support(), api.app.state.auth
    with api.app.state.store.read() as c:
        hold = K._hold(c, held["id"])
        assert not K._is_reviewer(app, hold, c, auth)
        assert K.held_for(c, app, ("message",)) == []
        assert K.holds_listed(c, app, auth=auth) == ([], None)
        with pytest.raises(Problem) as seen:
            K.view_hold(c, app, hold, auth=auth)
        assert seen.value.status == 404
    with api.app.state.store.transaction() as c:
        with pytest.raises(Problem) as kept:
            K.keep(c, app, held["id"], auth)
        assert kept.value.status == 404
    with api.app.state.store.transaction() as c:
        with pytest.raises(Problem) as sent:
            K.send(c, app, held["id"], hold_row(api, held["id"])["nonce"], auth=auth)
        assert sent.value.status == 404
    assert hold_row(api, held["id"])["state"] == "held" and bodies(api, "favorite color is gold") == []
    get(api, f"rule-holds/{held['id']}", token="cara-test", expected=404)
    listed = get(api, "rule-holds", token="ben-test")["holds"]  # an Admin reads it, the reviewer being gone
    assert [h["id"] for h in listed] == [held["id"]]
    view = get(api, f"rule-holds/{held['id']}", token="ana-test")  # the owner too
    assert view["can_send"]
    assert post(api, f"rule-holds/{held['id']}/keep", {}, token="ben-test")["state"] == "kept"


def test_with_the_reviewer_gone_app_only_tico_support_reads_no_person_hold_and_the_owner_and_admin_do(ruled):
    api, _ = ruled
    _hire(api, "dan")
    made = color_rule(api, reviewer="dan")
    with api.app.state.store.transaction() as c:  # a person's held post, held by a rule whose editors can release it
        c.execute(
            "INSERT INTO org_rule_holds(id, operation, payload, author, destinations, rule_ids, "
            "verdict, nonce, state, reviewer, task_id, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "h-cara-app",
                "message",
                json.dumps({"text": "Our favorite color is gold", "replay": {}}),
                "human:cara",
                "[]",
                json.dumps([made["id"]]),
                "{}",
                "n-cara-app",
                "held",
                "human:dan",
                None,
                H.now(),
            ),
        )
    _leave(api, "dan")
    app, auth = _app_support(), api.app.state.auth
    with api.app.state.store.read() as c:
        hold = K._hold(c, "h-cara-app")
        assert not K._is_reviewer(app, hold, c, auth)
        assert K.holds_listed(c, app, auth=auth) == ([], None)
        with pytest.raises(Problem) as seen:
            K.view_hold(c, app, hold, auth=auth)
        assert seen.value.status == 404
    with api.app.state.store.transaction() as c:
        with pytest.raises(Problem) as kept:
            K.keep(c, app, "h-cara-app", auth)
        assert kept.value.status == 404
    assert hold_row(api, "h-cara-app")["state"] == "held"
    assert "nonce" in get(api, "rule-holds/h-cara-app", token="ben-test")  # an Admin reads and releases it
    assert "nonce" in get(api, "rule-holds/h-cara-app", token="ana-test")  # the owner too
    kept = post(api, "rule-holds/h-cara-app/keep", {}, token="ben-test")
    assert kept["state"] == "kept" and kept["decided_by"] == "human:ben"


def _app_reads_nothing(api, hold_id):
    """App-only Tico support sees no hold, lists none, keeps none and sends none: the hold stays held."""
    app, auth = _app_support(), api.app.state.auth
    with api.app.state.store.read() as c:
        hold = K._hold(c, hold_id)
        assert not K._is_reviewer(app, hold, c, auth)
        assert K.holds_listed(c, app, auth=auth) == ([], None)
        with pytest.raises(Problem) as seen:
            K.view_hold(c, app, hold, auth=auth)
        assert seen.value.status == 404
    with api.app.state.store.transaction() as c:
        with pytest.raises(Problem) as kept:
            K.keep(c, app, hold_id, auth)
        assert kept.value.status == 404
        with pytest.raises(Problem) as sent:
            K.send(c, app, hold_id, hold_row(api, hold_id)["nonce"], auth=auth)
        assert sent.value.status == 404
    assert hold_row(api, hold_id)["state"] == "held"


def test_with_no_reviewer_app_only_tico_support_reads_no_bot_hold_and_the_owner_and_admin_do(ruled, monkeypatch):
    api, _ = ruled
    _nobody_near_ops(api)
    monkeypatch.setattr(K, "owner_or_admin", lambda c: None)  # no person on the team to name as reviewer
    color_rule(api, if_broken="escalate")
    held = say(api, "Our favorite color is gold")["held"]
    assert held["outcome"] == "blocked" and hold_row(api, held["id"])["reviewer"] is None
    _app_reads_nothing(api, held["id"])
    get(api, f"rule-holds/{held['id']}", token="cara-test", expected=404)
    assert "nonce" in get(api, f"rule-holds/{held['id']}", token="ben-test")  # an Admin reads it
    assert [h["id"] for h in get(api, "rule-holds", token="ben-test")["holds"]] == [held["id"]]
    assert "nonce" in get(api, f"rule-holds/{held['id']}", token="ana-test")  # the owner too
    assert post(api, f"rule-holds/{held['id']}/keep", {}, token="ben-test")["state"] == "kept"


def test_with_no_reviewer_app_only_tico_support_reads_no_person_hold_and_the_owner_and_admin_do(ruled):
    api, _ = ruled
    made = color_rule(api)
    with api.app.state.store.transaction() as c:  # a person's held post whose rule names no reviewer
        c.execute(
            "INSERT INTO org_rule_holds(id, operation, payload, author, destinations, rule_ids, "
            "verdict, nonce, state, reviewer, task_id, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "h-cara-none",
                "message",
                json.dumps({"text": "Our favorite color is gold", "replay": {}}),
                "human:cara",
                "[]",
                json.dumps([made["id"]]),
                "{}",
                "n-cara-none",
                "held",
                None,
                None,
                H.now(),
            ),
        )
    _app_reads_nothing(api, "h-cara-none")
    assert "nonce" in get(api, "rule-holds/h-cara-none", token="ben-test")  # an Admin reads and releases it
    assert "nonce" in get(api, "rule-holds/h-cara-none", token="ana-test")  # the owner too
    assert post(api, "rule-holds/h-cara-none/keep", {}, token="ben-test")["state"] == "kept"


def test_with_the_reviewer_present_a_bot_hold_is_not_read_by_app_only_tico_support(ruled):
    api, _ = ruled
    color_rule(api, reviewer="ben")
    held = say(api, "Our favorite color is gold")["held"]
    app, auth = _app_support(), api.app.state.auth
    with api.app.state.store.read() as c:
        assert not K._is_reviewer(app, K._hold(c, held["id"]), c, auth)
    assert "nonce" in get(api, f"rule-holds/{held['id']}", token="ben-test")  # the reviewer reads it as before
    assert post(api, f"rule-holds/{held['id']}/keep", {}, token="ben-test")["state"] == "kept"


def test_keep_blocked_and_only_the_reviewer_author_or_owner_reads_a_hold(ruled):
    api, _ = ruled
    color_rule(api, reviewer="ben")
    held = say(api, "Our favorite color is gold")["held"]
    get(api, f"rule-holds/{held['id']}", token="cara-test", expected=404)
    assert "nonce" in get(
        api, f"rule-holds/{held['id']}", token="ana-test"
    )  # the owner too, with Ben named as reviewer
    assert "nonce" not in get(api, f"rule-holds/{held['id']}", token="ops-test")
    # The held text is in no conversation, comment or inbox, and makes no work.
    assert bodies(api, "gold") == [] and sql(api, "SELECT id FROM jobs WHERE bot_id='ana'") == []
    assert "gold" not in api.get("/api/v2/inbox", headers=headers()).text
    kept = post(api, f"rule-holds/{held['id']}/keep", {}, token="ben-test")
    assert kept["state"] == "kept"
    post(api, f"rule-holds/{held['id']}/keep", {}, token="ben-test", expected=409)


def read_by_run(c, aid, task):
    """The run `aid` read `task`: a quiet notice naming it is one of the run's inputs (task_privacy.attempt_tasks)."""
    room = c.execute(
        "SELECT m.conversation_id FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
        "WHERE a.id=?",
        (aid,),
    ).fetchone()[0]
    note = H._write_message(
        c,
        H.KEEPER,
        "bot:ops",
        "Plan notes",
        H.conversation(c, room),
        "notice",
        {"task": task["id"], "quiet": True},
        None,
        None,
    )
    c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (aid, note["id"]))


def age_holds(api, hours):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE org_rule_holds SET created_at=?", (H.shift(H.now(), hours=-hours),))


def sweep_holds(api):
    with api.app.state.store.transaction() as c:
        return K.expire_unreviewed(c, H.now())


def ben_needs_you(api):
    """The held writes Ben's Needs you lists (thread attention reads the same rows)."""
    who = SimpleNamespace(actor="human:ben", via="", task_actor="", role="person")
    with api.app.state.store.read() as c:
        return [row["id"] for row in K.held_for(c, who, thread_attention.HELD_OPERATIONS)]


def test_a_held_write_nobody_decides_for_a_day_is_kept_unsent_and_its_bot_is_told(ruled):
    api, _ = ruled
    color_rule(api, reviewer="ben")
    held = say(api, "Our favorite color is gold")["held"]
    assert ben_needs_you(api) == [held["id"]]
    age_holds(api, 25)
    assert sweep_holds(api) == [held["id"]]
    row = hold_row(api, held["id"])
    assert (row["state"], row["decided_by"]) == ("kept", H.KEEPER)
    assert ben_needs_you(api) == []
    assert bodies(api, "gold") == []  # the held text is written nowhere
    # The bot that wrote it is told, not the person it was for.
    told = sql(api, "SELECT body FROM notes WHERE to_actor='bot:ops' AND from_actor=?", (H.KEEPER,))
    assert len(told) == 1 and "was not sent" in told[0]["body"] and "24 hours" in told[0]["body"]
    assert sweep_holds(api) == []  # decided once


def test_a_held_write_under_a_day_old_is_left_for_its_reviewer(ruled):
    api, _ = ruled
    color_rule(api, reviewer="ben")
    held = say(api, "Our favorite color is gold")["held"]
    age_holds(api, 23)
    assert sweep_holds(api) == []
    assert hold_row(api, held["id"])["state"] == "held"
    assert ben_needs_you(api) == [held["id"]]


def test_a_hold_a_person_decided_is_not_touched_by_the_sweep(ruled):
    api, _ = ruled
    color_rule(api, reviewer="ben")
    held = say(api, "Our favorite color is gold")["held"]
    post(api, f"rule-holds/{held['id']}/keep", {}, token="ben-test")
    age_holds(api, 48)
    assert sweep_holds(api) == []
    row = hold_row(api, held["id"])
    assert (row["state"], row["decided_by"]) == ("kept", "human:ben")
    assert sql(api, "SELECT body FROM notes WHERE to_actor='ana' AND body LIKE '%not sent%'") == []


def test_the_sweep_expires_the_oldest_holds_up_to_its_batch_cap(ruled):
    api, _ = ruled
    color_rule(api, reviewer="ben")
    ids = [say(api, f"Our favorite color is gold, number {n}")["held"]["id"] for n in range(3)]
    with api.app.state.store.transaction() as c:
        for hours, hold_id in zip((30, 27, 26), ids):
            c.execute("UPDATE org_rule_holds SET created_at=? WHERE id=?", (H.shift(H.now(), hours=-hours), hold_id))
    with api.app.state.store.transaction() as c:
        assert K.expire_unreviewed(c, H.now(), limit=2) == ids[:2]
    assert [hold_row(api, i)["state"] for i in ids] == ["kept", "kept", "held"]
    assert sweep_holds(api) == [ids[2]]


def test_the_owner_lists_keeps_and_sends_every_bot_hold_even_when_its_named_reviewer_is_on_the_team(ruled):
    api, _ = ruled
    color_rule(api, reviewer="ben")
    gold, teal = say(api, "Our favorite color is gold")["held"], say(api, "Our favorite color is teal")["held"]
    assert hold_row(api, gold["id"])["reviewer"] == "human:ben"  # Ben is on the team and can sign in
    both = sorted([gold["id"], teal["id"]])
    owner = get(api, "rule-holds", token="ana-test")["holds"]
    assert sorted(h["id"] for h in owner) == both and all(h["can_send"] for h in owner)
    assert sorted(h["id"] for h in get(api, "rule-holds", token="ben-test")["holds"]) == both
    assert get(api, "rule-holds", token="cara-test")["holds"] == []
    assert (
        post(api, f"rule-holds/{gold['id']}/keep", {}, token="ana-test")["state"] == "kept"
    )  # a call Ben has not made
    view = get(api, f"rule-holds/{teal['id']}", token="ana-test")
    assert (
        post(api, f"rule-holds/{teal['id']}/send", {"nonce": view["nonce"]}, token="ana-test")["hold"]["state"]
        == "sent"
    )
    assert len(bodies(api, "favorite color is teal")) == 1


def test_the_person_a_bot_reports_to_reads_and_withdraws_its_held_post_but_cannot_release_it(ruled):
    api, _ = ruled
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='ops'")
    color_rule(api, reviewer="ana")
    held = say(api, "Our favorite color is gold")["held"]
    view = get(api, f"rule-holds/{held['id']}", token="ben-test")  # the person ops works for reads its post
    assert view["can_withdraw"] and not view["can_send"] and "nonce" not in view and "gold" in view["text"]
    post(api, f"rule-holds/{held['id']}/send", {"nonce": "not-the-nonce"}, token="ben-test", expected=404)
    get(api, f"rule-holds/{held['id']}", token="cara-test", expected=404)  # a peer does not read it
    assert "nonce" in get(api, f"rule-holds/{held['id']}", token="ana-test")  # the reviewer still releases it
    kept = post(api, f"rule-holds/{held['id']}/keep", {}, token="ben-test")
    assert kept["state"] == "kept" and kept["decided_by"] == "human:ben"
    assert bodies(api, "gold") == []


def test_a_person_reads_and_withdraws_a_hold_on_their_own_post_and_a_peer_does_not(ruled):
    api, _ = ruled
    with api.app.state.store.transaction() as c:  # rules-people's person checks write holds with a person author
        c.execute(
            "INSERT INTO org_rule_holds(id, operation, payload, author, destinations, rule_ids, "
            "verdict, nonce, state, reviewer, task_id, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "h-cara",
                "message",
                json.dumps({"text": "Our favorite color is gold", "replay": {}}),
                "human:cara",
                "[]",
                "[]",
                "{}",
                "n-cara",
                "held",
                "human:ana",
                None,
                H.now(),
            ),
        )
    view = get(api, "rule-holds/h-cara", token="cara-test")
    assert view["can_withdraw"] and not view["can_send"] and "nonce" not in view and "gold" in view["text"]
    assert view["author_name"] == "Cara"  # the person's name, not the actor id
    get(api, "rule-holds/h-cara", token="ben-test", expected=404)
    post(api, "rule-holds/h-cara/send", {"nonce": "n-cara"}, token="cara-test", expected=404)
    assert "nonce" in get(api, "rule-holds/h-cara", token="ana-test")
    kept = post(api, "rule-holds/h-cara/keep", {}, token="cara-test")
    assert kept["state"] == "kept" and kept["decided_by"] == "human:cara"


def test_an_appeal_reaches_the_reviewer_even_when_a_rule_blocks_messaging_them(ruled):
    api, judge = ruled
    color_rule(api, reviewer="ben")
    rule(api, {"kind": "internal", "text": "Never message Ben.", "who": {"bots": ["ops"]}, "with": {"people": ["ben"]}})
    judge.needles["Never message Ben"] = ""  # every output to Ben breaks it
    held = say(api, "Our favorite color is gold")["held"]
    calls = len(judge.calls)
    appealed = post(api, f"rule-holds/{held['id']}/appeal", {"why": "Gold is public on our site."}, token="ops-test")
    assert appealed["task_id"] and len(judge.calls) == calls
    assert get(api, f"rule-holds/{held['id']}", token="ben-test")["why"] == "Gold is public on our site."
    assert "why" not in get(api, f"rule-holds/{held['id']}", token="ops-test")


# ---- cache, drafts, budget, failing open --------------------------------------------------------------------------


def test_a_checked_draft_then_its_send_is_one_call(ruled):
    api, judge = ruled
    color_rule(api)
    checked = post(api, "rules/check", {"text": "Launch moves to Tuesday.", "to": "ana"}, token="ops-test")
    assert checked["outcome"] == "pass"
    assert "held" not in say(api, "Launch moves to Tuesday.")
    assert len(judge.calls) == 1


def test_the_cache_splits_on_operation_and_rule_version(ruled):
    api, judge = ruled
    made = color_rule(api)
    post(api, "rules/check", {"text": "Launch moves to Tuesday.", "to": "ana"}, token="ops-test")
    post(api, "rules/check", {"text": "Launch moves to Tuesday.", "to": "ana", "as": "assign"}, token="ops-test")
    assert len(judge.calls) == 2
    edit(api, made["id"], {"version": made["version"], "text": COLOR + " Ever."})
    post(api, "rules/check", {"text": "Launch moves to Tuesday.", "to": "ana"}, token="ops-test")
    assert len(judge.calls) == 3


@pytest.mark.parametrize("how", ["timeout", "budget", "error"])
def test_failing_open_delivers_and_writes_unchecked(ruled, monkeypatch, how):
    api, judge = ruled
    color_rule(api)
    if how == "timeout":
        monkeypatch.setitem(K.DEADLINES, "low", 0.2)
        judge.hold = threading.Event()
    elif how == "budget":
        api.app.state.store.settings.decisions_monthly_usd = 1.0
        with api.app.state.store.transaction() as c:
            H.event(c, H.KEEPER, "judge.spend", "person-ask-ingest@1", {"cost_usd": 5.0})
    else:
        judge.fail = RuntimeError("provider down")
    got = say(api, "Our favorite color is gold")
    if judge.hold:
        judge.hold.set()
    assert "held" not in got and len(bodies(api, "favorite color is gold")) == 1
    reasons = [json.loads(e["detail"])["reason"] for e in events(api, "rules.unchecked")]
    assert reasons == [{"timeout": "timeout", "budget": "budget", "error": "error"}[how]]


def test_with_no_rule_in_force_a_bot_write_reads_no_roster_and_asks_nothing(ruled, monkeypatch):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}}, token="ops-test")  # suggested only
    built = []
    real = K.R._ctx
    monkeypatch.setattr(K.R, "_ctx", lambda c: built.append(1) or real(c))
    assert "held" not in say(api, "Our favorite color is gold")
    task = task_for_ops(api)
    post(api, f"tasks/{task['id']}/comments", {"text": "Our favorite color is red."}, token="ops-test")
    assert built == [] and judge.calls == []


def test_a_rule_checks_spend_lands_in_the_decisions_budget(ruled):
    api, judge = ruled
    color_rule(api)
    store = api.app.state.store
    store.settings.decisions_monthly_usd = 50.0
    assert say(api, "Our favorite color is gold")["held"]
    assert events(api, "rules.spend") == []  # one budget: no spend rows of its own
    [spend] = events(api, "judge.spend")
    assert spend["target"].startswith("rule-check@") and json.loads(spend["detail"])["cost_usd"] > 0
    with store.read() as c:
        assert K.B.spent_this_month(c) == json.loads(spend["detail"])["cost_usd"]
    [call] = events(api, "judge.call")
    assert json.loads(call["detail"])["rules"]  # per-rule counts still read the rule ids off the call
    assert not hasattr(K, "BUDGET")


def test_a_spent_decisions_cap_stops_rule_checks(ruled):
    api, judge = ruled
    color_rule(api)
    store = api.app.state.store
    store.settings.decisions_monthly_usd = 1.0
    with store.transaction() as c:
        H.event(c, H.KEEPER, "judge.spend", "person-ask-ingest@1", {"cost_usd": 5.0})
    got = say(api, "Our favorite color is gold")
    assert "held" not in got and judge.calls == [] and len(bodies(api, "favorite color is gold")) == 1
    assert [json.loads(e["detail"])["reason"] for e in events(api, "rules.unchecked")] == ["budget"]
    assert get(api, "rules/stats")["usd_left"] == 0


def test_a_decisions_cap_spent_while_a_check_waits_its_turn_is_the_budget_too(ruled):
    api, judge = ruled
    color_rule(api)
    store = api.app.state.store
    store.settings.decisions_monthly_usd = 1.0
    with store.transaction() as c:  # under the cap, but with no room for one more call's most
        H.event(c, H.KEEPER, "judge.spend", "person-ask-ingest@1", {"cost_usd": 0.999995})
    got = say(api, "Our favorite color is gold")
    assert "held" not in got and judge.calls == [] and len(bodies(api, "favorite color is gold")) == 1
    assert [json.loads(e["detail"])["reason"] for e in events(api, "rules.unchecked")] == ["budget"]
    assert events(api, "judge.call") == []  # nothing was asked


def test_stats_and_the_team_setting_turn_checks_off(ruled):
    api, judge = ruled
    color_rule(api)
    say(api, "Our favorite color is gold")
    stats = get(api, "rules/stats")
    assert stats["checks_today"] == 1 and stats["blocked_today"] == 1 and stats["on"] and stats["usd_left"] is not None
    with api.app.state.store.transaction() as c:
        from backend import team_rules

        team_rules.save(c, "human:ana", {K.TEAM_RULE: False})
    assert "held" not in say(api, "Our favorite color is teal")
    assert len(judge.calls) == 1


def test_rules_are_never_in_a_bots_prompt(ruled):
    api, _ = ruled
    color_rule(api)
    r, msg, attempt = setup_attempt(api)
    assert "favorite color" not in json.dumps(attempt)


def test_no_engine_call_happens_under_the_write_lock(ruled):
    api, judge = ruled
    color_rule(api)
    task = task_for_ops(api)
    say(api, "Our favorite color is gold")
    post(api, f"tasks/{task['id']}/comments", {"text": "Our favorite color is red."}, token="ops-test")
    post(api, f"tasks/{task['id']}", {"version": task["version"], "note": "Favorite color is red"}, token="ops-test")
    post(api, "rules/check", {"text": "Something else entirely.", "to": "ana"}, token="ops-test")
    assert len(judge.locked) >= 4 and not any(judge.locked)


def test_hub_rules_check_and_appeal_over_the_cli_and_mcp(ruled):
    from backend.tests.helpers.org_rules_surfaces import cli, mcp  # noqa: F401
    from clients import cli_spec, hubcli
    from backend.tests.helpers.automation_cli import Client  # noqa: F401

    api, judge = ruled
    color_rule(api, reviewer="ben")
    checked = cli(api, "ops-test", "check", "Our favorite color is gold", "--to", "ana")
    assert checked["outcome"] == "broken" and checked["rules"][0]["text"] == COLOR
    held = say(api, "Our favorite color is gold")["held"]
    assert len(judge.calls) == 1
    args = hubcli.parser().parse_args(["rules", "appeal", held["id"], "Gold is on our public site."])
    appealed = cli_spec.of(args).call(Client(api, "ops-test"), args)
    assert appealed["task_id"]
    error, _ = mcp(api, "tico_rules_check", {"text": "Hello", "to": "ana"}, token="ops-test")
    assert not error


# ---- every route, the run's provenance, live text -----------------------------------------------------------------


def test_a_conversation_message_is_checked(ruled):
    api, _ = ruled
    color_rule(api)
    origin = post(api, "chat/ops", {"text": "What is new?"})
    cid = origin["conversation_id"]
    held = post(api, f"conversations/{cid}/messages", {"text": "Our favorite color is red."}, token="ops-test")
    assert held["held"] and bodies(api, "favorite color is red") == []
    assert (
        post(api, f"conversations/{cid}/messages", {"text": "All clear."}, token="ops-test")["message"]["body"]
        == "All clear."
    )


def test_the_assistant_writing_as_itself_is_checked(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"all_bots": True}})
    bot = "bot:" + api.app.state.store.settings.assistant_bot
    verdict = K.preflight(
        api.app.state.store, judge, Identity(bot, "bot"), "message", "Our favorite color is red", ["human:ben"]
    )
    assert verdict["author"] == bot and K.holds(verdict)


def test_a_public_task_is_judged_on_its_public_text_for_the_whole_company(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}, "with": {"people": ["cara"]}})
    got = post(
        api,
        "tasks",
        {
            "owner": "ops",
            "title": "Ship the palette",
            "body": "Internal draft.",
            "visibility": "public",
            "public_title": "New palette",
            "public_summary": "Our favorite color is red.",
        },
        token="ops-test",
    )
    assert got["held"], got
    assert "human:cara" in judge.calls[0]["state"]["to"] or "Cara" in judge.calls[0]["state"]["to"]


def test_a_task_change_warning_rides_on_the_change_itself(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    got = post(
        api, f"tasks/{task['id']}", {"version": task["version"], "body": "Our favorite color is red."}, token="ops-test"
    )
    assert got["rule_warning"]
    row = sql(api, "SELECT body FROM tasks WHERE id=?", (task["id"],))[0]
    assert row["body"].startswith("Warning:") and row["body"].endswith("Our favorite color is red.")
    assert sql(api, "SELECT id FROM messages WHERE sender_id=? AND body LIKE 'Warning:%'", (H.KEEPER,)) == []


def test_automation_assign_judges_the_task_handed_over_and_replies_reach_their_people(ruled):
    api, judge = ruled
    color_rule(api)
    task = post(api, "tasks", {"owner": "ops", "title": "Palette", "body": "Our favorite color is red."})
    for args in ({"owner": "ana"}, {"owner": "ana", "note": "Over to you."}):
        aid = post(
            api,
            "automations",
            {
                "name": "Hand " + str(len(args)),
                "owner": "bot:ops",
                "trigger": {"type": "event", "name": "x.y"},
                "steps": [{"id": "a", "type": "action", "do": "assign", "args": args}],
            },
        )["id"]
        post(api, f"automations/{aid}/run", {"input": {"task": {"id": task["id"]}}})
        assert sql(api, "SELECT owner_id FROM tasks WHERE id=?", (task["id"],))[0]["owner_id"] == "bot:ops"
    assert len(events(api, "rules.blocked")) == 2
    from backend import automations as AU

    with api.app.state.store.read() as c:
        slack = {"message": {"source": "slack", "from": "person:cara", "text": "hi"}}
        assert "human:cara" in AU._rule_audience(
            c, {"id": "x", "flow": {"owner": "bot:ops"}}, "send_reply", {"bot": "ops"}, slack
        )
        mail = {
            "message": {
                "source": "mail",
                "mailbox": "ops@acme.example",
                "from": "ben@acme.example",
                "to": ["ops@acme.example"],
                "cc": ["vendor@outside.example"],
            }
        }
        assert AU._rule_audience(c, {"id": "x", "flow": {"owner": "bot:ops"}}, "send_reply", {}, mail) == ["human:ben"]
        assert AU._reply_addresses(mail) == ["ben@acme.example", "vendor@outside.example"]
        everyone = AU._rule_audience(c, {"id": "x", "flow": {"owner": "bot:ops"}}, "post_update", {}, {})
        assert {"human:ana", "human:ben", "human:cara"} <= set(everyone)


def review_turn(api):
    """A review subtask for ops in its own room, claimed and started: its reply waits until the task is done."""
    from backend.tests.helpers.api import assign, ready, runner  # noqa: F401

    r = runner(api, "ana")
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, "human:ana", "Ship the palette", "Build it.", "human:ana", lint=False)
        review = H.task_create(
            c, "human:ana", "Review: palette", "Review it.", "bot:ops", parent_id=parent["id"], lint=False
        )
    attempt = claim(api, r)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "held-thread"}, r["token"])
    return r, review, attempt


def test_a_task_room_reply_held_before_done_keeps_its_text_nowhere(ruled):
    api, _ = ruled
    color_rule(api)
    revision = next(b for b in get(api, "bots") if b["slug"] == "ops")["revision"]
    post(api, "bots/ops/definition", {"capture_stream": "on", "expected_revision": revision})
    r, review, attempt = review_turn(api)
    aid = attempt["id"]
    secret = "VERDICT: approve\nAcme's favorite color is blue."
    post(
        api,
        f"attempts/{aid}/events",
        {
            "events": [
                {"seq": 1, "kind": "delta", "payload": {"text": "thinking about blue", "delta_kind": "thought"}},
                {"seq": 2, "kind": "message", "payload": {"text": secret}},
            ]
        },
        token=r["token"],
    )
    frames = {"provider": "claude", "frames": '{"dir":"in","ms":5,"frame":{"text":"favorite color is blue"}}'}
    post(api, f"attempts/{aid}/capture", frames, r["token"])
    # Live: no said or thought text in the steps, and no raw stream, before the reply is checked.
    assert "blue" not in api.get(f"/api/v2/turns/{aid}/steps", headers=headers()).text
    get(api, f"attempts/{aid}/capture", expected=404)
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": secret, "last_seq": 2}, token=r["token"])
    for query in (
        "SELECT final_text AS t FROM attempts WHERE id=?",
        "SELECT result AS t FROM attempts WHERE id=?",
        "SELECT payload AS t FROM attempt_events WHERE attempt_id=?",
        "SELECT summary AS t FROM attempts WHERE id=?",
    ):
        assert all("blue" not in str(row["t"] or "") for row in sql(api, query, (aid,))), query
    assert "blue" not in str(
        sql(api, "SELECT checkpoint FROM tasks WHERE id=?", (review["id"],))[0]["checkpoint"] or ""
    )
    # The provider thread that wrote it is never resumed.
    assert sql(api, "SELECT thread_id FROM attempts WHERE id=?", (aid,))[0]["thread_id"] is None
    assert sql(api, "SELECT 1 FROM bot_sessions WHERE thread_id='held-thread'") == []
    # A later upload of the same stream is not kept, and the export stays shut.
    assert post(api, f"attempts/{aid}/capture", frames, r["token"])["kept"] is False
    assert sql(api, "SELECT 1 FROM attempt_captures WHERE attempt_id=?", (aid,)) == []
    get(api, f"attempts/{aid}/capture", expected=404)
    assert "blue" not in api.get(f"/api/v2/turns/{aid}/steps", headers=headers()).text


def test_sql_shows_a_watched_live_run_tool_steps_but_not_its_said_text(ruled):
    from backend import hubdb

    api, _ = ruled
    color_rule(api)
    r, _, attempt = review_turn(api)
    aid = attempt["id"]
    post(
        api,
        f"attempts/{aid}/events",
        {
            "events": [
                {"seq": 1, "kind": "tool", "payload": {"name": "read"}},
                {"seq": 2, "kind": "delta", "payload": {"text": "thinking about blue"}},
                {"seq": 3, "kind": "message", "payload": {"text": "Acme's favorite color is blue."}},
            ]
        },
        token=r["token"],
    )
    with api.app.state.store.transaction() as c:
        hubdb.delta_append(c, "bot:ops", aid, "favorite color is blue")

    def read(sql):
        res = api.post("/api/v2/sql", json={"sql": sql}, headers=headers())
        assert res.status_code == 200, res.text
        return json.dumps(res.json())

    events_sql = f"SELECT kind, payload FROM attempt_events WHERE attempt_id='{aid}'"
    assert "blue" not in read(events_sql) and '"tool"' in read(events_sql)
    assert "blue" not in read(f"SELECT text FROM deltas WHERE attempt_id='{aid}'")
    # Once the run hands in a reply that passes, its stream reads again.
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": "Done.", "last_seq": 3}, token=r["token"])
    assert "thinking about blue" in read(events_sql)


def test_a_late_result_a_block_rule_holds_is_filed_as_its_note_and_sql_masks_a_live_reply(ruled):
    api, _ = ruled
    color_rule(api)
    r, _, attempt = setup_attempt(api)
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "late-thread"}, r["token"])
    # Its lease lapsed and another attempt took the job: the result that comes in now is only filed.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='expired' WHERE id=?", (aid,))
        c.execute("UPDATE jobs SET attempt_id='another-attempt' WHERE id=?", (attempt["job_id"],))
    done = post(
        api,
        f"attempts/{aid}/complete",
        {"outcome": "completed", "text": "Acme's favorite color is blue.", "last_seq": 0},
        token=r["token"],
    )
    assert done["filed"] is True
    for query in (
        "SELECT final_text AS t FROM attempts WHERE id=?",
        "SELECT result AS t FROM attempts WHERE id=?",
        "SELECT summary AS t FROM attempts WHERE id=?",
    ):
        assert all("blue" not in str(row["t"] or "") for row in sql(api, query, (aid,))), query
    assert sql(api, "SELECT thread_id FROM attempts WHERE id=?", (aid,))[0]["thread_id"] is None

    # A watched run not settled yet keeps its reply columns out of SQL; the rest of the run reads as it is.
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE attempts SET final_text=?, result=? WHERE id=?",
            ("favorite color is blue", json.dumps({"text": "favorite color is blue", "outcome": "completed"}), aid),
        )
        c.execute("UPDATE attempts SET summary='favorite color is blue' WHERE id=?", (aid,))
    res = api.post(
        "/api/v2/sql",
        json={"sql": f"SELECT a.state, a.final_text, a.result, a.summary FROM attempts a WHERE a.id='{aid}'"},
        headers=headers(),
    )
    assert res.status_code == 200, res.text
    assert "blue" not in res.text and "expired" in res.text and "completed" in res.text


def test_send_anyway_of_a_reply_rechecks_where_it_may_go(ruled):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    r, msg, attempt = setup_attempt(api)
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "t"}, r["token"])
    post(
        api,
        f"attempts/{aid}/complete",
        {"outcome": "completed", "text": "Acme's favorite color is blue.", "last_seq": 0},
        token=r["token"],
    )
    hold = sql(api, "SELECT * FROM org_rule_holds")[0]
    assert json.loads(hold["payload"])["provenance"]["attempt_id"] == aid
    # The run read a private task Ana cannot read: the reply may not go to her now.
    with api.app.state.store.transaction() as c:
        private = H.task_create(c, "human:ben", "Private plan", "Secret.", "human:ben", lint=False, private=True)
        read_by_run(c, aid, private)
    view = get(api, f"rule-holds/{hold['id']}", token="ben-test")
    post(api, f"rule-holds/{hold['id']}/send", {"nonce": view["nonce"]}, token="ben-test", expected=403)
    assert hold_row(api, hold["id"])["state"] == "held" and bodies(api, "favorite color is blue") == []


def test_send_anyway_after_the_rule_changed_checks_again_and_keeps_the_nonce(ruled):
    api, judge = ruled
    made = color_rule(api, if_broken="escalate", reviewer="ben")
    held = say(api, "Our favorite color is gold")["held"]
    view = get(api, f"rule-holds/{held['id']}", token="ben-test")
    edit(api, made["id"], {"version": made["version"], "text": COLOR + " Ever."})
    judge.needles[COLOR + " Ever."] = NEEDLE
    r = api.post(f"/api/v2/rule-holds/{held['id']}/send", json={"nonce": view["nonce"]}, headers=headers("ben-test"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "rule_changed", r.text
    assert len(judge.calls) == 2 and not any(judge.locked)
    assert hold_row(api, held["id"])["state"] == "held"
    edit(api, made["id"], {"version": made["version"] + 1, "text": "Never share the launch date."})
    sent = post(api, f"rule-holds/{held['id']}/send", {"nonce": view["nonce"]}, token="ben-test")
    assert sent["hold"]["state"] == "sent" and len(bodies(api, "favorite color is gold")) == 1


def test_a_mail_approval_is_spent_once(ruled):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    mail = {"to": ["vendor@outside.example"], "subject": "Hi", "body": "Our favorite color is gold."}
    held = post(api, "bots/ops/mail-check", mail, token="ops-test")
    assert held["outcome"] == "held"
    hold_id = sql(api, "SELECT id FROM org_rule_holds WHERE operation='mail'")[0]["id"]
    view = get(api, f"rule-holds/{hold_id}", token="ben-test")
    post(api, f"rule-holds/{hold_id}/send", {"nonce": view["nonce"]}, token="ben-test")
    with api.app.state.store.transaction() as c:
        assert K.claim_mail(c, hold_id) and not K.claim_mail(c, hold_id)
        c.execute("UPDATE org_rule_holds SET verdict=json_remove(verdict,'$.used') WHERE id=?", (hold_id,))
    outcomes = []
    threads = [
        threading.Thread(
            target=lambda: outcomes.append(post(api, "bots/ops/mail-check", mail, token="ops-test")["outcome"])
        )
        for _ in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["held", "held", "pass"]


def test_botops_mail_for_a_person_finds_its_own_hold_and_spends_its_approval_once(ruled):
    api, _ = ruled
    rule(
        api, {"kind": "internal", "text": COLOR, "who": {"everyone": True}, "if_broken": "escalate", "reviewer": "ben"}
    )
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT OR IGNORE INTO bots(id, slug, display_name, state) VALUES('botops', 'botops', 'BotOps', 'active')"
        )
    as_bot(api, "botops", "botops-test")
    api.app.state.auth.settings.test_identities["ana-botops"] = Identity(
        "human:ana", "owner", "ana@acme.example", via="botops", task_actor="bot:botops", agent="test"
    )
    mail = {"to": ["vendor@outside.example"], "subject": "Hi", "body": "Our favorite color is gold."}
    first = post(api, "bots/botops/mail-check", mail, token="ana-botops")
    again = post(api, "bots/botops/mail-check", mail, token="ana-botops")
    assert first["outcome"] == again["outcome"] == "held" and first["held"]["id"] == again["held"]["id"]
    [hold] = sql(api, "SELECT id, author FROM org_rule_holds WHERE operation='mail'")
    assert hold["author"] == "human:ana"
    view = get(api, f"rule-holds/{hold['id']}", token="ben-test")
    post(api, f"rule-holds/{hold['id']}/send", {"nonce": view["nonce"]}, token="ben-test")
    sent = post(api, "bots/botops/mail-check", mail, token="ana-botops")
    assert sent["outcome"] == "pass" and sent["approved"] == hold["id"]
    assert post(api, "bots/botops/mail-check", mail, token="ana-botops")["outcome"] == "held"


def test_a_mail_check_for_another_bot_is_refused_and_counts_nothing(ruled):
    api, judge = ruled
    color_rule(api)
    mail = {"to": ["vendor@outside.example"], "subject": "Hi", "body": "Our favorite color is gold."}
    # Another bot's token: refused. The send queue checks each bot's own mail itself, so nothing is counted here.
    post(api, "bots/ops/mail-check", mail, token="finance-test", expected=403)
    assert events(api, "rules.unchecked") == [] and judge.calls == []


def test_an_assistant_mail_is_checked_as_the_person_it_is_written_for(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"everyone": True}})
    settings = api.app.state.auth.settings
    as_bot(api, settings.assistant_bot, "coo-test")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug=?", (settings.assistant_bot,))
    # As `auth.chat_person` builds it: the person, acting through their Assistant bot's credential.
    settings.test_identities["ben-assistant"] = Identity(
        "human:ben",
        "human",
        "ben@acme.example",
        via="assistant",
        task_actor="bot:" + settings.assistant_bot,
        agent="test",
    )
    mail = {"to": ["vendor@outside.example"], "subject": "Hi", "body": "Our favorite color is gold."}
    assert post(api, f"bots/{settings.assistant_bot}/mail-check", mail, token="ben-assistant")["outcome"] == "held"
    [hold] = sql(api, "SELECT author FROM org_rule_holds WHERE operation='mail'")
    assert hold["author"] == "human:ben" and len(judge.calls) == 1


def test_a_warned_mail_carries_its_line_and_external_addresses_split_the_cache(ruled):
    api, judge = ruled
    color_rule(api, if_broken="warn")
    mail = {"to": ["vendor@outside.example"], "subject": "Hi", "body": "Our favorite color is gold."}
    got = post(api, "bots/ops/mail-check", mail, token="ops-test")
    assert got["outcome"] == "warn" and got["warning_line"].startswith("Warning:")
    post(api, "bots/ops/mail-check", mail, token="ops-test")
    post(api, "bots/ops/mail-check", {**mail, "to": ["other@outside.example"]}, token="ops-test")
    assert len(judge.calls) == 2


# ---- holds list, cache, caps --------------------------------------------------------------------------------------


def test_a_review_tasks_holds_page_by_cursor_within_a_byte_budget(ruled, monkeypatch):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    first = say(api, "Our favorite color is gold")["held"]
    task_id = hold_row(api, first["id"])["task_id"]
    big = "x" * (K.HOLD_TEXT_CHARS + 500)
    with api.app.state.store.transaction() as c:
        row = dict(hold_row(api, first["id"]))
        for n in range(60):
            c.execute(
                "INSERT INTO org_rule_holds(id, operation, payload, author, destinations, rule_ids, "
                "reviewer, task_id, state, nonce, verdict, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"h-{n:03d}",
                    "message",
                    json.dumps({"text": big, "replay": {}}),
                    row["author"],
                    row["destinations"],
                    row["rule_ids"],
                    row["reviewer"],
                    task_id,
                    "held",
                    f"n{n}",
                    row["verdict"],
                    f"2026-01-01T00:00:{n:02d}Z",
                ),
            )
    seen, cursor, pages = [], None, 0
    while True:
        page = get(api, f"rule-holds?task={task_id}" + (f"&cursor={cursor}" if cursor else ""), token="ben-test")
        assert len(json.dumps(page)) <= K.HOLD_LIST_BYTES + 200
        assert all("text" not in h and "nonce" not in h for h in page["holds"])
        seen += [h["id"] for h in page["holds"]]
        pages += 1
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 61 == len(set(seen)) and pages >= 2
    one = get(api, "rule-holds/h-000", token="ben-test")
    assert len(one["text"]) == K.HOLD_TEXT_CHARS and one["text_cut"] == len(big) and one["nonce"] == "n0"
    assert get(api, f"rule-holds?task={task_id}", token="cara-test")["holds"] == []


def test_the_cache_splits_on_context_and_rules_too_big_for_one_call_are_judged_in_more_calls(ruled, monkeypatch):
    api, judge = ruled
    color_rule(api)
    store, ops = api.app.state.store, Identity("bot:ops", "bot")
    for step in ("a", "a", "b"):
        K.preflight(store, judge, ops, "automation", "Launch is Tuesday.", ["human:ana"], {"step_id": step})
    assert len(judge.calls) == 2
    from clients import judge as J

    monkeypatch.setattr(J, "fits", lambda state, questions: len(questions) <= 1)  # one rule per call fits
    rule(api, {"kind": "internal", "text": "Never share the launch date.", "who": {"bots": ["ops"]}})
    first = K.preflight(store, judge, ops, "message", "Lunch at noon.", ["human:ana"])
    assert len(judge.calls) == 4 and "unchecked_rules" not in first and first["outcome"] == "pass"
    again = K.preflight(store, judge, ops, "message", "Lunch at noon.", ["human:ana"])
    assert again["cached"] and "unchecked_rules" not in again and len(judge.calls) == 4


def test_a_draft_matching_45_rules_is_checked_in_2_calls_each_rule_once(ruled):
    """Past the Gateway's 40 questions a call: the fewest calls of at most 40, every matching rule judged once."""
    api, judge = ruled
    for n in range(44):
        rule(api, {"kind": "internal", "text": f"Keep note {n} short.", "who": {"bots": ["ops"]}})
    color_rule(api)
    store, ops = api.app.state.store, Identity("bot:ops", "bot")
    got = K.preflight(store, judge, ops, "message", "Our favorite color is red.", ["human:ana"])
    assert [len(call["questions"]) for call in judge.calls] == [40, 5]
    texts = [r["text"] for call in judge.calls for r in call["state"]["rules"]]
    assert len(texts) == len(set(texts)) == 45 and COLOR in texts
    assert got["outcome"] == "broken" and "unchecked_rules" not in got and len(got["broken"]) == 1
    assert [json.loads(e["detail"])["questions"] for e in events(api, "judge.call")] == [40, 5]


def test_an_outside_edition_with_45_rules_is_checked_in_2_calls_not_refused(ruled):
    api, judge = ruled
    for n in range(44):
        rule(api, {"kind": "internal", "text": f"Keep note {n} short.", "who": {"outside_editions": True}})
    rule(api, {"kind": "internal", "text": COLOR, "who": {"outside_editions": True}})
    store = api.app.state.store
    clean = K.outside_edition(store, judge, "Our launch went well.", to="press@example.com")
    assert clean["outcome"] == "pass" and len(clean["rules"]) == 45
    assert [len(call["questions"]) for call in judge.calls] == [40, 5]
    texts = [r["text"] for call in judge.calls for r in call["state"]["rules"]]
    assert len(texts) == len(set(texts)) == 45
    broken = K.outside_edition(store, judge, "Our favorite color is red.", to="press@example.com")
    assert broken["outcome"] == "held" and broken["reason"] == "broken"
    color = next(r["id"] for r in broken["rules"] if r["text"] == COLOR)
    assert broken["broken"] == [color] and len(judge.calls) == 4
    again = K.outside_edition(store, judge, "Our favorite color is red.", to="press@example.com")
    assert again["broken"] == [color] and len(judge.calls) == 4, "the verdict is cached"
    audit = [json.loads(e["detail"]) for e in events(api, "judge.call")]
    assert [(a["operation"], a["questions"], a["broken"]) for a in audit] == [
        ("outside_edition", 40, 0),
        ("outside_edition", 5, 0),
        ("outside_edition", 40, 0),
        ("outside_edition", 5, 1),
    ]


def test_rule_checks_and_ask_ingest_count_their_own_daily_calls(ruled):
    api, _ = ruled
    from backend import judge as B

    with api.app.state.store.transaction() as c:
        for _ in range(3):
            H.event(c, H.KEEPER, "judge.call", "rule-check@1", {})
        H.event(c, H.KEEPER, "judge.call", "person-ask-ingest@1", {})
    with api.app.state.store.read() as c:
        assert K.calls_today(c) == 3
        assert B.used_today(c, H.KEEPER) == 1


# ---- Send anyway: privacy, snapshot, full inputs, a new Warn ------------------------------------------------------


def started_turn(api):
    r, msg, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, r["token"])
    return r, msg.get("message", msg), attempt


def send_anyway(api, hold_id, expected=200):
    view = get(api, f"rule-holds/{hold_id}", token="ben-test")
    r = api.post(f"/api/v2/rule-holds/{hold_id}/send", json={"nonce": view["nonce"]}, headers=headers("ben-test"))
    assert r.status_code == expected, r.text
    return r.json()


def test_send_anyway_runs_each_routes_privacy_checks_as_the_run(ruled):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    r, msg, attempt = started_turn(api)
    aid, run = attempt["id"], attempt["token"]
    task = task_for_ops(api)
    held = [post(api, f"tasks/{task['id']}/comments", {"text": "Our favorite color is red."}, token=run)["held"]]
    assert all(held)
    # The run now carries a private task neither Ana nor the company task's readers may read.
    with api.app.state.store.transaction() as c:
        private = H.task_create(c, "human:ben", "Private plan", "Secret.", "human:ben", lint=False, private=True)
        read_by_run(c, aid, private)
    for h in held:
        send_anyway(api, h["id"], expected=403)
        assert hold_row(api, h["id"])["state"] == "held"
    assert bodies(api, "favorite color is red") == [] and bodies(api, "favorite color is teal") == []
    paths = [json.loads(hold_row(api, h["id"])["payload"])["provenance"]["path"] for h in held]
    assert paths == [f"/api/v2/tasks/{task['id']}/comments"]


def test_send_anyway_refuses_once_the_task_changed(ruled):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    # A hand-off is checked on the task it hands over: its body breaks the rule.
    task = post(api, "tasks", {"owner": "ops", "title": "Palette", "body": "Our favorite color is red."})
    held = post(api, f"tasks/{task['id']}/owner", {"version": task["version"], "owner": "ana"}, token="ops-test")[
        "held"
    ]
    post(api, f"tasks/{task['id']}", {"version": task["version"], "body": "Launch is Tuesday."})
    r = send_anyway(api, held["id"], expected=409)
    assert r["error"]["code"] == "changed"
    assert hold_row(api, held["id"])["state"] == "held"
    assert sql(api, "SELECT owner_id FROM tasks WHERE id=?", (task["id"],))[0]["owner_id"] == "bot:ops"


def edit_after_check(api, monkeypatch, task_id, body, times=1):
    """The automation's check of its step runs, then the task changes (version bumped) before the step's write."""
    from backend import automations as AU

    real, left = AU.HubServices._rule_check, [times]

    def then_edit(self, *args, **kw):
        verdict = real(self, *args, **kw)
        if left[0]:
            left[0] -= 1
            with api.app.state.store.transaction() as c:
                c.execute("UPDATE tasks SET body=?, version=version+1 WHERE id=?", (body, task_id))
        return verdict

    monkeypatch.setattr(AU.HubServices, "_rule_check", then_edit)


def test_a_version_bump_never_releases_a_block_on_an_assign(ruled, monkeypatch):
    api, _ = ruled
    color_rule(api)
    task = post(api, "tasks", {"owner": "ops", "title": "Palette", "body": "Our favorite color is red."})
    edit_after_check(api, monkeypatch, task["id"], "Our favorite color is red.")
    post(api, f"automations/{hand_over(api)}/run", {"input": {"task": {"id": task["id"]}}})
    assert sql(api, "SELECT owner_id FROM tasks WHERE id=?", (task["id"],))[0]["owner_id"] == "bot:ops"
    assert len(events(api, "rules.blocked")) == 1 and events(api, "rules.unchecked") == []


def test_an_assign_whose_task_changed_after_a_pass_checks_again_before_it_runs(ruled, monkeypatch):
    api, judge = ruled
    color_rule(api)
    task = post(api, "tasks", {"owner": "ops", "title": "Palette", "body": "Pick one."})
    edit_after_check(api, monkeypatch, task["id"], "Our favorite color is red.")
    post(api, f"automations/{hand_over(api)}/run", {"input": {"task": {"id": task["id"]}}})
    assert sql(api, "SELECT owner_id FROM tasks WHERE id=?", (task["id"],))[0]["owner_id"] == "bot:ops"
    assert len(judge.calls) == 2 and len(events(api, "rules.blocked")) == 1
    assert events(api, "rules.unchecked") == []


def test_a_task_that_keeps_changing_fails_the_step_and_writes_nothing(ruled, monkeypatch):
    api, _ = ruled
    color_rule(api)
    task = post(api, "tasks", {"owner": "ops", "title": "Palette", "body": "Pick one."})
    edit_after_check(api, monkeypatch, task["id"], "Pick two.", times=5)
    aid = hand_over(api)
    post(api, f"automations/{aid}/run", {"input": {"task": {"id": task["id"]}}})
    assert sql(api, "SELECT owner_id FROM tasks WHERE id=?", (task["id"],))[0]["owner_id"] == "bot:ops"
    run = sql(api, "SELECT id FROM automation_runs WHERE automation_id=?", (aid,))[0]["id"]
    assert sql(api, "SELECT key FROM automation_effects WHERE run_id=?", (run,)) == []


def test_a_rule_added_since_an_approval_is_checked_on_the_mails_addresses_and_holds_once(ruled):
    api, judge = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    mail = {"to": ["vendor@outside.example"], "subject": "Hi", "body": "Our favorite color is gold."}
    assert post(api, "bots/ops/mail-check", mail, token="ops-test")["outcome"] == "held"
    first = sql(api, "SELECT id FROM org_rule_holds WHERE operation='mail'")[0]["id"]
    send_anyway(api, first)
    rule(
        api,
        {
            "kind": "internal",
            "text": "Never write to the outside vendor.",
            "who": {"bots": ["ops"]},
            "if_broken": "escalate",
            "reviewer": "ben",
        },
    )
    judge.needles["outside vendor"] = "vendor@outside.example"
    outcomes = [post(api, "bots/ops/mail-check", mail, token="ops-test")["outcome"] for _ in range(3)]
    assert outcomes == ["held"] * 3
    assert "vendor@outside.example" in judge.calls[-1]["state"]["context"]["to"]
    rows = sql(
        api,
        "SELECT id, state, json_extract(verdict,'$.used') AS used FROM org_rule_holds "
        "WHERE operation='mail' ORDER BY created_at",
    )
    assert [(r["state"], r["used"]) for r in rows] == [("sent", "superseded"), ("held", None)]
    assert len({r["task_id"] for r in sql(api, "SELECT task_id FROM org_rule_holds WHERE task_id IS NOT NULL")}) == 2
    send_anyway(api, rows[1]["id"])
    calls = len(judge.calls)
    assert post(api, "bots/ops/mail-check", mail, token="ops-test")["outcome"] == "pass"
    assert len(judge.calls) == calls
    assert post(api, "bots/ops/mail-check", mail, token="ops-test")["outcome"] == "held"


def test_a_warn_rule_added_since_the_hold_puts_its_line_on_the_sent_copy(ruled):
    api, judge = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    task = task_for_ops(api)
    held = [
        say(api, "Our favorite color is gold and launch is Tuesday.")["held"],
        post(
            api,
            f"tasks/{task['id']}/comments",
            {"text": "Our favorite color is gold; launch is Tuesday."},
            token="ops-test",
        )["held"],
    ]
    rule(api, {"kind": "internal", "text": LAUNCH, "who": {"bots": ["ops"]}, "if_broken": "warn"})
    judge.needles[LAUNCH] = "launch is"
    for h in held:
        send_anyway(api, h["id"])
    sent = sql(api, "SELECT body FROM messages WHERE body LIKE '%launch is Tuesday%' ORDER BY rowid")
    assert len(sent) == 2 and all(r["body"].startswith("Warning:") for r in sent)
    assert "launch date" in sent[0]["body"]


def test_retries_of_one_blocked_mail_keep_one_hold_and_the_third_escalates_it(ruled):
    api, _ = ruled
    color_rule(api, reviewer="ben")
    mail = {"to": ["vendor@outside.example"], "subject": "Hi", "body": "Our favorite color is gold."}
    outcomes = [post(api, "bots/ops/mail-check", mail, token="ops-test")["held"]["outcome"] for _ in range(3)]
    assert outcomes == ["blocked", "blocked", "escalated"]
    rows = sql(api, "SELECT id, task_id FROM org_rule_holds")
    assert len(rows) == 1 and rows[0]["task_id"]
    assert len(events(api, "rules.blocked")) == 2 and len(events(api, "rules.escalated")) == 1
    assert get(api, f"rule-holds/{rows[0]['id']}", token="ben-test")["outcome"] == "escalated"


def test_send_anyway_refuses_a_comment_whose_task_went_to_someone_else(ruled):
    api, judge = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    task = post(api, "tasks", {"owner": "ana", "title": "Palette notes", "body": "Collect ideas."}, token="ops-test")
    task = task.get("task", task)
    held = post(api, f"tasks/{task['id']}/comments", {"text": "Our favorite color is red."}, token="ops-test")["held"]
    nonce = get(api, f"rule-holds/{held['id']}", token="ben-test")["nonce"]
    version = sql(api, "SELECT version FROM tasks WHERE id=?", (task["id"],))[0]["version"]
    post(api, f"tasks/{task['id']}/owner", {"version": version, "owner": "ben"})
    calls = len(judge.calls)
    r = send_anyway(api, held["id"], expected=409)
    assert r["error"]["code"] == "changed" and len(judge.calls) == calls
    assert hold_row(api, held["id"])["state"] == "held"
    assert get(api, f"rule-holds/{held['id']}", token="ben-test")["nonce"] == nonce
    assert bodies(api, "favorite color is red") == []


def test_a_hold_that_kept_no_route_is_refused_for_a_new_check(ruled):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    held = say(api, "Our favorite color is gold")["held"]
    stored = json.loads(hold_row(api, held["id"])["payload"])
    stored["provenance"].pop("path")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE org_rule_holds SET payload=? WHERE id=?", (json.dumps(stored), held["id"]))
    assert send_anyway(api, held["id"], expected=409)["error"]["code"] == "recheck"
    assert hold_row(api, held["id"])["state"] == "held" and bodies(api, "favorite color is gold") == []


def test_a_breaking_final_text_after_a_safe_tool_reply_is_still_held(ruled):
    api, _ = ruled
    color_rule(api)
    r, msg, attempt = started_turn(api)
    aid = attempt["id"]
    post(
        api,
        "messages",
        {"to": "ana", "text": "On it.", "in_reply_to": msg["id"], "conversation_id": msg["conversation_id"]},
        token=attempt["token"],
    )
    post(
        api,
        f"attempts/{aid}/complete",
        {"outcome": "completed", "text": "Our favorite color is blue.", "last_seq": 0},
        token=r["token"],
    )
    assert len(sql(api, "SELECT id FROM org_rule_holds")) == 1
    row = sql(api, "SELECT final_text, result FROM attempts WHERE id=?", (aid,))[0]
    assert "blue" not in row["final_text"] and json.loads(row["result"])["held_reply"] is True
    assert bodies(api, "favorite color is blue") == []
    thread = get(api, f"conversations/{msg['conversation_id']}/messages")
    assert any("reply was held" in m["body"] for m in thread)


# ---- where a warning goes -----------------------------------------------------------------------------------------


def test_a_warning_goes_on_each_field_a_task_write_sets_and_never_on_a_title(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    task = post(api, f"tasks/{task['id']}", {"version": task["version"], "note": "Waiting on the vendor."})
    task = task.get("task", task)
    title = ("Our favorite color is red, " + "x" * 300)[:299] + "Z"
    got = post(api, f"tasks/{task['id']}", {"version": task["version"], "title": title}, token="ops-test")
    assert got["rule_warning"]
    row = sql(api, "SELECT title, body, note FROM tasks WHERE id=?", (task["id"],))[0]
    assert row["title"] == title and row["body"] == "Draft it."
    assert row["note"].startswith("Warning:") and row["note"].endswith("Waiting on the vendor.")
    post(api, "roadmaps/settings", {"bot_public_approval": False})
    made = post(
        api,
        "tasks",
        {
            "owner": "ops",
            "title": "Ship the palette",
            "body": "Internal draft.",
            "visibility": "public",
            "public_title": "Our favorite color is red",
        },
        token="ops-test",
    )
    made = sql(
        api, "SELECT title, body, public_title, public_summary FROM tasks WHERE id=?", (made.get("task", made)["id"],)
    )[0]
    assert made["public_title"] == "Our favorite color is red" and made["title"] == "Ship the palette"
    assert made["body"].startswith("Warning:") and made["body"].endswith("Internal draft.")
    # Published with no summary, the public roadmap shows the line as its summary, the title kept whole.
    assert made["public_summary"].startswith("Warning:")


def test_a_warned_hand_off_puts_its_line_before_the_note_already_there(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    task = post(api, "tasks", {"owner": "ops", "title": "Palette", "body": "Our favorite color is red."})
    task = post(api, f"tasks/{task['id']}", {"version": task["version"], "note": "Waiting on the vendor."})
    task = task.get("task", task)
    got = post(api, f"tasks/{task['id']}/owner", {"version": task["version"], "owner": "ana"}, token="ops-test")
    assert got["rule_warning"]
    row = sql(api, "SELECT owner_id, note FROM tasks WHERE id=?", (task["id"],))[0]
    assert row["owner_id"] == "human:ana"
    assert row["note"].startswith("Warning:") and row["note"].endswith("Waiting on the vendor.")


def test_automation_warnings_land_on_the_posted_text_and_the_hand_off_note(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    from backend import automations as AU

    gate = {"warn": "Warning: careful."}
    values = {"turn": {"ref": "t1", "turn": {}, "text": "Our favorite color is red."}}
    key = AU._rule_key("post_update", {})
    assert key == "text"
    posted = AU._warned_args(None, None, gate, "post_update", {}, key, values)["text"]
    assert posted == "Warning: careful.\n\nOur favorite color is red."
    assert AU._rule_key("create_task", {"title": "T", "body": "B"}) == "body"
    task = post(api, "tasks", {"owner": "ops", "title": "Palette", "body": "Our favorite color is red."})
    task = post(api, f"tasks/{task['id']}", {"version": task["version"], "note": "Waiting on the vendor."})
    task = task.get("task", task)
    aid = hand_over(api)
    post(api, f"automations/{aid}/run", {"input": {"task": {"id": task["id"]}}})
    row = sql(api, "SELECT owner_id, body, note FROM tasks WHERE id=?", (task["id"],))[0]
    assert row["owner_id"] == "human:ana" and row["body"] == "Our favorite color is red."
    assert row["note"].startswith("Warning:") and row["note"].endswith("Waiting on the vendor.")


# ---- the hold list, a moved recipient ------------------------------------------------------------------------------


def test_one_hold_naming_many_long_rules_stays_within_the_list_budget(ruled):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    first = say(api, "Our favorite color is gold")["held"]
    row = hold_row(api, first["id"])
    ids = [f"r-{n:03d}" for n in range(110)]
    with api.app.state.store.transaction() as c:
        for rid in ids:
            c.execute(
                "INSERT INTO org_rules(id, kind, text, who, status, suggested_by, created_at, updated_at) "
                "VALUES(?, 'internal', ?, '{}', 'retired', 'human:ana', ?, ?)",
                (rid, "y" * 1000, H.now(), H.now()),
            )
        c.execute(
            "UPDATE org_rule_holds SET rule_ids=?, payload=? WHERE id=?",
            (json.dumps(ids), json.dumps({"text": "z" * 200_000, "replay": {}}), first["id"]),
        )
    page = get(api, f"rule-holds?task={row['task_id']}", token="ben-test")
    assert len(json.dumps(page)) <= K.HOLD_LIST_BYTES
    one = page["holds"][0]
    assert one["rules_count"] == 110 and len(one["rules"]) == K.SUMMARY_RULES
    assert all(len(r["text"]) <= K.SUMMARY_CHARS for r in one["rules"])


def test_a_conversation_post_whose_recipient_moved_needs_a_new_check(ruled, monkeypatch):
    api, judge = ruled
    color_rule(api)
    cid = post(api, "chat/ops", {"text": "What is new?"})["conversation_id"]
    real = K.preflight

    def then_move(*args, **kw):
        verdict = real(*args, **kw)
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE conversations SET participants=? WHERE id=?", (json.dumps(["bot:ops", "human:ben"]), cid))
        return verdict

    monkeypatch.setattr(K, "preflight", then_move)
    text = "Our favorite color is red."
    refused = post(api, f"conversations/{cid}/messages", {"text": text}, token="ops-test", expected=409)
    assert refused["error"]["code"] == "moved" and bodies(api, text) == []
    assert events(api, "rules.unchecked") == []
    monkeypatch.setattr(K, "preflight", real)
    held = post(api, f"conversations/{cid}/messages", {"text": text}, token="ops-test")["held"]
    assert json.loads(hold_row(api, held["id"])["destinations"]) == ["human:ben"]
    assert bodies(api, text) == []


# ---- chat and updates; the author of a replay; public task text; the fast path ----------------------


def chat(api, text, to="finance", token="ops-test"):
    r = api.post(f"/api/v2/chat/{to}", json={"text": text}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def test_chat_and_updates_are_held_under_a_block_rule(ruled, monkeypatch):
    api, judge = ruled
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")  # a Tuesday: a daily, whatever today is
    color_rule(api)
    assert chat(api, "Our favorite color is blue.")["held"]
    assert post(api, "updates", {"kind": "daily", "body": "- Our favorite color is blue."}, token="ops-test")["held"]
    weekly = {
        "goal": "Ship the palette",
        "done": ["Our favorite color is teal"],
        "focus": ["Launch"],
        "blockers": ["None"],
    }
    assert post(api, "updates", {"slides": weekly}, token="ops-test")["held"]
    assert sql(api, "SELECT id FROM messages WHERE sender_id='bot:ops'") == []
    assert sql(api, "SELECT id FROM updates WHERE bot='ops'") == []
    held = sql(api, "SELECT operation FROM org_rule_holds ORDER BY created_at")
    assert [h["operation"] for h in held] == ["message", "update", "update"]
    # An update is judged for the whole company, which reads it.
    assert "Cara" in judge.calls[1]["state"]["to"]
    assert "Our favorite color is teal" in judge.calls[2]["state"]["output"]


def test_the_update_tool_answers_with_the_hold_or_the_warning(ruled, monkeypatch):
    from backend.tests.helpers.org_rules_surfaces import mcp  # noqa: F401

    api, _ = ruled
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")  # a Tuesday: a daily, whatever today is
    made = color_rule(api)
    error, held = mcp(
        api, "tico_update_create", {"kind": "daily", "body": "- Our favorite color is blue."}, token="ops-test"
    )
    assert not error and held["held"]["outcome"] == "blocked"
    edit(api, made["id"], {"if_broken": "warn", "version": made["version"]})
    error, posted = mcp(
        api, "tico_update_create", {"kind": "daily", "body": "- Our favorite color is blue."}, token="ops-test"
    )
    assert not error and posted["rule_warning"] and posted["body"].startswith("Warning:")


def test_chat_and_updates_carry_a_warn_rules_line(ruled, monkeypatch):
    api, _ = ruled
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")  # a Tuesday: a daily, whatever today is
    color_rule(api, if_broken="warn")
    got = chat(api, "Our favorite color is blue.")
    assert got["rule_warning"] and got["message"]["body"].startswith("Warning:")
    update = post(api, "updates", {"kind": "daily", "body": "- Our favorite color is blue."}, token="ops-test")
    assert update["rule_warning"] and update["update"]["body"].startswith("Warning:")


def test_send_anyway_delivers_a_held_chat_and_update_once(ruled, monkeypatch):
    api, judge = ruled
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")  # a Tuesday: a daily, whatever today is
    color_rule(api, if_broken="escalate", reviewer="ben")
    holds = [
        chat(api, "Our favorite color is blue.")["held"]["id"],
        post(api, "updates", {"kind": "daily", "body": "- Our favorite color is blue."}, token="ops-test")["held"][
            "id"
        ],
    ]
    for hold_id in holds:
        assert send_anyway(api, hold_id)["hold"]["state"] == "sent"
    assert len(bodies(api, "favorite color is blue")) == 1  # the chat message
    assert [r["body"] for r in sql(api, "SELECT body FROM updates WHERE bot='ops'")] == [
        "- Our favorite color is blue."
    ]
    assert len([call for call in judge.calls if call["label"].startswith("rule-check")]) == 2


def test_send_anyway_refuses_an_automation_whose_actor_changed(ruled):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    step = {
        "id": "t",
        "type": "action",
        "do": "create_task",
        "args": {"owner": "ana", "title": "Palette", "body": "Our favorite color is red."},
    }
    aid = post(
        api,
        "automations",
        {"name": "File", "owner": "bot:ops", "trigger": {"type": "event", "name": "x.y"}, "steps": [step]},
    )["id"]
    post(api, f"automations/{aid}/run", {})
    [hold] = sql(api, "SELECT id FROM org_rule_holds WHERE operation='automation'")
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE automations SET owner_id='bot:finance', flow=json_set(flow,'$.owner','bot:finance') WHERE id=?",
            (aid,),
        )
    assert send_anyway(api, hold["id"], expected=409)["error"]["code"] == "changed"
    assert hold_row(api, hold["id"])["state"] == "held"
    assert sql(api, "SELECT id FROM tasks WHERE title='Palette'") == []


def test_public_task_text_is_checked_for_the_whole_company(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}, "with": {"people": ["cara"]}})
    task = task_for_ops(api)
    got = post(
        api,
        f"tasks/{task['id']}",
        {"version": task["version"], "public_summary": "Our favorite color is red."},
        token="ops-test",
    )
    assert got["held"], got
    assert "Cara" in judge.calls[-1]["state"]["to"]
    # Making a task public alone publishes the public text written before: that text is judged.
    drafted = post(
        api,
        "tasks",
        {
            "owner": "ops",
            "title": "Palette",
            "body": "Draft it.",
            "visibility": "private",
            "public_title": "New palette",
            "public_summary": "Our favorite color is red.",
        },
    )
    got = post(api, f"tasks/{drafted['id']}", {"version": drafted["version"], "visibility": "public"}, token="ops-test")
    assert got["held"], got
    assert "favorite color is red" in judge.calls[-1]["state"]["output"]
    assert sql(api, "SELECT visibility FROM tasks WHERE id=?", (drafted["id"],))[0]["visibility"] == "private"


def test_with_no_rule_in_force_bot_writes_resolve_no_audience(ruled, monkeypatch):
    api, judge = ruled
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")  # a Tuesday: a daily, whatever today is
    rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}}, token="ops-test")  # suggested only
    resolved = []
    monkeypatch.setattr(K, "company", lambda c: resolved.append("company") or [])
    monkeypatch.setattr(K, "mail_recipients", lambda c, addresses: resolved.append("mail") or [])
    monkeypatch.setattr(threads, "mentioned_people", lambda *a, **k: resolved.append("mentions") or [])
    task = task_for_ops(api)
    post(api, f"tasks/{task['id']}/comments", {"text": "Ask @ana or ben@acme.example about it."}, token="ops-test")
    assert "held" not in post(api, "updates", {"kind": "daily", "body": "- Shipped the launch note."}, token="ops-test")
    aid = post(
        api,
        "automations",
        {
            "name": "Post",
            "owner": "bot:finance",
            "trigger": {"type": "event", "name": "x.y"},
            "steps": [{"id": "p", "type": "action", "do": "post_update", "args": {"text": "- Closed the books."}}],
        },
    )["id"]
    post(api, f"automations/{aid}/run", {})
    assert sql(api, "SELECT body FROM updates WHERE bot='finance'")[0]["body"] == "- Closed the books."
    assert (
        post(api, "bots/ops/mail-check", {"to": ["ben@acme.example"], "body": "Hi Ben."}, token="ops-test")["outcome"]
        == "pass"
    )
    assert resolved == [] and judge.calls == []


# ---- a warning on the copy its reader reads: the public text, a daily with slides --------------


def public_output(api, task_id):
    """The task's public title and summary as stored: what a public roadmap shows once it is back."""
    [row] = sql(api, "SELECT public_title AS title, public_summary AS summary FROM tasks WHERE id=?", (task_id,))
    return row


def test_a_warned_publish_puts_its_line_on_the_public_summary_the_roadmap_shows(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    post(api, "roadmaps/settings", {"bot_public_approval": False})
    drafted = post(
        api,
        "tasks",
        {
            "owner": "ops",
            "title": "Palette",
            "body": "Draft it.",
            "visibility": "private",
            "public_title": "New palette",
            "public_summary": "Our favorite color is red.",
        },
    )
    got = post(api, f"tasks/{drafted['id']}", {"version": drafted["version"], "visibility": "public"}, token="ops-test")
    assert got["rule_warning"]
    shown = public_output(api, drafted["id"])
    assert shown["title"] == "New palette"
    assert shown["summary"].startswith("Warning:") and shown["summary"].endswith("Our favorite color is red.")
    # A new public title on a public task with no summary: the line alone is the summary, the title kept whole.
    bare = post(
        api,
        "tasks",
        {"owner": "ops", "title": "Logo", "body": "Draft it.", "visibility": "public", "public_title": "New logo"},
    )
    got = post(
        api,
        f"tasks/{bare['id']}",
        {"version": bare["version"], "public_title": "Our favorite color is red"},
        token="ops-test",
    )
    assert got["rule_warning"]
    shown = public_output(api, bare["id"])
    assert shown["title"] == "Our favorite color is red" and shown["summary"].startswith("Warning:")


def test_publishing_while_clearing_a_summary_judges_only_what_is_published(ruled):
    api, judge = ruled
    color_rule(api)
    post(api, "roadmaps/settings", {"bot_public_approval": False})
    drafted = post(
        api,
        "tasks",
        {
            "owner": "ops",
            "title": "Palette",
            "body": "Draft it.",
            "visibility": "private",
            "public_title": "Safe title",
            "public_summary": "Our favorite color is red.",
        },
    )
    got = post(
        api,
        f"tasks/{drafted['id']}",
        {"version": drafted["version"], "visibility": "public", "public_summary": ""},
        token="ops-test",
    )
    assert "held" not in got, got
    assert "favorite color" not in judge.calls[-1]["state"]["output"]
    shown = public_output(api, drafted["id"])
    assert shown["title"] == "Safe title" and not shown["summary"]


def test_a_warned_daily_with_slides_carries_its_line_on_the_stored_bullets(ruled):
    api, judge = ruled
    color_rule(api, if_broken="warn")
    weekly = {"goal": "Ship the palette", "done": ["Launched"], "focus": ["Launch"], "blockers": ["None"]}
    got = post(
        api, "updates", {"kind": "daily", "body": "- Our favorite color is blue.", "slides": weekly}, token="ops-test"
    )
    assert got["rule_warning"]
    [row] = sql(api, "SELECT kind, body FROM updates WHERE bot='ops'")
    assert row["kind"] == "daily"
    assert row["body"].startswith("Warning:") and row["body"].endswith("- Our favorite color is blue.")
    # The slides a daily drops are not judged.
    assert "Ship the palette" not in judge.calls[-1]["state"]["output"]


def test_a_warned_summary_at_the_cap_publishes_with_its_line(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    post(api, "roadmaps/settings", {"bot_public_approval": False})
    summary = ("Our favorite color is red. " * 400)[:7999] + "Z"
    drafted = post(
        api,
        "tasks",
        {
            "owner": "ops",
            "title": "Palette",
            "body": "Draft it.",
            "visibility": "private",
            "public_title": "New palette",
            "public_summary": summary,
        },
    )
    got = post(api, f"tasks/{drafted['id']}", {"version": drafted["version"], "visibility": "public"}, token="ops-test")
    assert got["rule_warning"], got
    shown = public_output(api, drafted["id"])
    assert shown["summary"].startswith("Warning:") and shown["summary"].endswith(summary)


def test_send_anyway_posts_the_held_days_update_or_nothing(ruled, monkeypatch):
    from backend import updates

    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-10-29")  # a Thursday
    thursday = post(api, "updates", {"body": "- Our favorite color is blue."}, token="ops-test")["held"]["id"]
    payload = json.loads(hold_row(api, thursday)["payload"])["replay"]["body"]
    assert payload["day"] == "2026-10-29" and payload["kind"] == "daily"
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-10-30")  # Friday: a week in review's day
    got = send_anyway(api, thursday, expected=409)
    assert got["error"]["code"] == "changed"
    assert hold_row(api, thursday)["state"] == "held" and sql(api, "SELECT id FROM updates WHERE bot='ops'") == []
    # Sent the day it was held, it is that day's daily.
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-10-29")
    send_anyway(api, thursday)
    assert [(r["kind"], r["day"]) for r in sql(api, "SELECT kind, day FROM updates WHERE bot='ops'")] == [
        ("daily", "2026-10-29")
    ]


def upload(api, name, text="# Draft", token="ops-test"):
    r = api.post("/api/v2/files/uploads", json={"name": name, "text": text, "scope": "bot"}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()["file"]


def test_a_second_warned_publish_of_a_summary_at_the_cap_keeps_one_line(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    post(api, "roadmaps/settings", {"bot_public_approval": False})
    summary = ("Our favorite color is red. " * 400)[:7999] + "Z"
    drafted = post(
        api,
        "tasks",
        {
            "owner": "ops",
            "title": "Palette",
            "body": "Draft it.",
            "visibility": "private",
            "public_title": "New palette",
            "public_summary": summary,
        },
    )
    got = post(api, f"tasks/{drafted['id']}", {"version": drafted["version"], "visibility": "public"}, token="ops-test")
    task = got.get("task", got)
    got = post(
        api,
        f"tasks/{drafted['id']}",
        {"version": task["version"], "public_title": "Our favorite color is blue"},
        token="ops-test",
    )
    assert got["rule_warning"], got
    shown = public_output(api, drafted["id"])
    assert shown["title"] == "Our favorite color is blue"
    assert shown["summary"].count("Warning:") == 1 and shown["summary"].startswith("Warning:")
    assert shown["summary"].endswith("\n\n" + summary)


def test_a_warned_update_passes_refusing_writing_checks_and_keeps_its_line(ruled, monkeypatch):
    from backend import hubdb, updates

    api, _ = ruled
    color_rule(api, if_broken="warn")
    monkeypatch.setattr(hubdb, "STYLE_LINT", "refuse")
    daily = post(api, "updates", {"kind": "daily", "body": "- Our favorite color is blue."}, token="ops-test")
    assert daily["rule_warning"] and daily["update"]["body"].startswith("Warning:")
    assert daily["update"]["body"].endswith("\n\n- Our favorite color is blue.")
    # The hub's start-up sweep lints the update's own words, so the line does not get it deleted.
    with api.app.state.store.transaction() as c:
        assert updates.purge_rejected(c) == []
    weekly = {"goal": "Our favorite color is blue", "done": ["Launched"], "focus": ["Launch"], "blockers": ["None"]}
    got = post(api, "updates", {"kind": "weekly", "slides": weekly}, token="ops-test")
    assert got["rule_warning"] and got["update"]["slides"]["goal"].startswith("Warning:")
    assert sql(api, "SELECT count(*) AS n FROM updates WHERE bot='ops'")[0]["n"] == 2


def test_an_impossible_update_day_is_refused_as_a_day(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    r = api.post("/api/v2/updates", json={"day": "2026-02-30", "body": "- x"}, headers=headers("ops-test"))
    assert r.status_code == 422 and r.json()["error"]["code"] == "day", r.text


def move_day_during_check(api, monkeypatch, before, after):
    """`updates.today` says `before` until the rule check has run, then `after`: the check ran past midnight."""
    from backend import updates

    days = {"today": before}
    monkeypatch.setattr(updates, "today", lambda at=None: days["today"])
    judge = api.app.state.judge

    def past_midnight(*args, **kwargs):
        days["today"] = after
        return judge(*args, **kwargs)

    api.app.state.judge = past_midnight


def test_a_live_update_naming_no_day_is_for_the_day_it_is_written_and_keeps_its_kind(ruled, monkeypatch):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    move_day_during_check(api, monkeypatch, "2026-10-29", "2026-10-30")  # a Thursday's daily, written Friday
    got = post(api, "updates", {"body": "- Our favorite color is blue."}, token="ops-test")
    assert got["rule_warning"]
    assert [(r["kind"], r["day"]) for r in sql(api, "SELECT kind, day FROM updates WHERE bot='ops'")] == [
        ("daily", "2026-10-30")
    ]


def test_a_live_update_naming_its_day_is_still_refused_once_the_day_has_moved_on(ruled, monkeypatch):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    move_day_during_check(api, monkeypatch, "2026-10-29", "2026-10-30")
    r = api.post(
        "/api/v2/updates",
        json={"day": "2026-10-29", "body": "- Our favorite color is blue."},
        headers=headers("ops-test"),
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "day", r.text
    assert sql(api, "SELECT id FROM updates WHERE bot='ops'") == []


def test_a_held_live_update_naming_no_day_is_held_for_the_day_it_is_written(ruled, monkeypatch):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    move_day_during_check(api, monkeypatch, "2026-10-29", "2026-10-30")
    held = post(api, "updates", {"body": "- Our favorite color is blue."}, token="ops-test")["held"]["id"]
    payload = json.loads(hold_row(api, held)["payload"])["replay"]["body"]
    assert payload["day"] == "2026-10-30" and payload["kind"] == "daily"
    # The hold keeps the day it was written for, so Send anyway posts that daily.
    send_anyway(api, held)
    assert [(r["kind"], r["day"]) for r in sql(api, "SELECT kind, day FROM updates WHERE bot='ops'")] == [
        ("daily", "2026-10-30")
    ]


def test_a_warned_automation_update_passes_refusing_writing_checks(ruled, monkeypatch):
    from backend import hubdb

    api, _ = ruled
    color_rule(api, if_broken="warn")
    monkeypatch.setattr(hubdb, "STYLE_LINT", "refuse")
    aid = post(
        api,
        "automations",
        {
            "name": "Post",
            "owner": "bot:ops",
            "trigger": {"type": "event", "name": "x.y"},
            "steps": [
                {"id": "p", "type": "action", "do": "post_update", "args": {"text": "- Our favorite color is blue."}}
            ],
        },
    )["id"]
    post(api, f"automations/{aid}/run", {})
    body = sql(api, "SELECT body FROM updates WHERE bot='ops'")[0]["body"]
    assert body.startswith("Warning:") and body.endswith("\n\n- Our favorite color is blue."), body


def test_the_market_pointer_keeps_a_warned_daily_line_on_top(ruled, monkeypatch):
    from backend import automation_actions as AA, hubdb

    api, _ = ruled
    color_rule(api, if_broken="warn")
    monkeypatch.setattr(hubdb, "STYLE_LINT", "refuse")
    daily = post(api, "updates", {"kind": "daily", "body": "- Our favorite color is blue."}, token="ops-test")
    line = daily["update"]["body"].split("\n\n")[0]
    with api.app.state.store.transaction() as c:
        pointed = AA._pointer(c, "ops", "Northwind moved")
    assert pointed.startswith(line + "\n\n- Our favorite color is blue.\n- Market briefing:"), pointed


@pytest.mark.parametrize("if_broken", ["block", "warn"])
def test_the_send_queue_checks_a_bots_own_email_against_the_rules(ruled, monkeypatch, if_broken):
    # The mail CLI only queues: POST email/sends runs the org rule check itself (backend/email_sending.py rule_gate).
    import base64
    from backend import email_sending as S, mailbox_access as MA
    from backend.tests.helpers.email_sending import BOX, mime  # noqa: F401

    api, judge = ruled
    monkeypatch.setattr(
        S, "classify", lambda *a, **k: {"legal_or_money": False, "review": True, "status": "classified"}
    )
    with api.app.state.store.transaction() as c:
        box = MA.ensure_mailbox(c, BOX, person_id="ana", created_by="human:ana")
        MA.grant(c, "human:ana", box["id"], "bot", "ops", "read_write")
    color_rule(api, if_broken=if_broken)
    sent = post(
        api, "email/sends", {"mailbox": BOX, "raw": mime(body="Our favorite color is gold.")}, token="ops-test"
    )["send"]
    assert len(judge.calls) == 1
    if if_broken == "block":
        assert sent["state"] == "blocked" and sent["decision"]["reasons"][0] == "org_rule"
        [hold] = sql(api, "SELECT author FROM org_rule_holds WHERE operation='mail'")
        assert hold["author"] == "bot:ops"
        return
    [row] = sql(api, "SELECT raw, content_sha256, classification FROM email_sends WHERE id=?", (sent["id"],))
    text = base64.urlsafe_b64decode(row["raw"] + "=" * (-len(row["raw"]) % 4)).decode()
    assert "Warning:" in text and "favorite color is gold" in text
    assert json.loads(row["classification"])["source_sha256"] != row["content_sha256"]
    # A person's own email is not checked.
    post(api, "email/sends", {"mailbox": BOX, "raw": mime(body="Our favorite color is gold, says Ana.")})
    assert len(judge.calls) == 1


def test_a_rule_check_whose_jev_fails_is_unchecked_and_never_asks_the_company_model(ruled, monkeypatch):
    from clients import judge as J

    from backend import judge as B

    api, judge = ruled
    color_rule(api)
    judge.fail = J.JudgeError("unavailable", "Jev did not answer", retryable=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic")  # a company model on the server is never asked
    with api.app.state.store.read() as c:
        assert B.server_engine(api.app.state.store, c, None) is None
    assert "held" not in say(api, "Our favorite color is gold")
    assert [json.loads(e["detail"])["reason"] for e in events(api, "rules.unchecked")] == ["unavailable"]


def test_a_rule_checks_judge_call_names_the_rules_it_asked(ruled):
    api, judge = ruled
    made = color_rule(api)
    say(api, "Our favorite color is gold")
    with api.app.state.store.read() as c:
        details = [
            json.loads(r[0])
            for r in c.execute("SELECT detail FROM events WHERE action='judge.call' AND target LIKE 'rule-check%'")
        ]
    assert details and details[-1]["rules"] == [made["id"]] and details[-1]["level"] == "low"


def test_a_bots_ask_a_rule_covers_is_refused_with_the_rules_words(ruled):
    """docs/pipelines-v3.md, "How a rule stops the next one": the rule check that reads what bots send also reads their
    asks; an ask a rule covers is refused back to the bot with the rule's words (tick list 49)."""
    api, judge = ruled
    color_rule(api)
    task = task_for_ops(api)
    r = api.post(
        f"/api/v2/tasks/{task['id']}/asks",
        json={"to": "ana", "line": "May I say our favorite color is blue?", "version": task["version"]},
        headers=headers("ops-test"),
    )
    assert r.status_code >= 400 and COLOR in r.text, r.text
    assert judge.calls and "favorite color is blue" in judge.calls[-1]["state"]["output"]
    # Refused, not held: nothing is asked and nothing waits for a reviewer.
    assert sql(api, "SELECT id FROM org_rule_holds") == []
    assert sql(api, "SELECT version FROM tasks WHERE id=?", (task["id"],))[0]["version"] == task["version"]
    # With the rule check down, the ask goes through: better one extra ask than a lost one.
    judge.fail = TimeoutError("down")
    r = api.post(
        f"/api/v2/tasks/{task['id']}/asks",
        json={"to": "ana", "line": "May I say our favorite color is green?", "version": task["version"]},
        headers=headers("ops-test"),
    )
    assert r.status_code == 200 and r.json()["requirement_id"], r.text
