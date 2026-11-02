"""Org rule checks (backend/org_rule_check.py, docs/rules-plan.md §3): a bot's output is checked against the rules that
apply to it before it lands. One Decide call outside the write's transaction; a held write answers 200 with `held`
and writes nothing but its hold; Escalate files one review task for a person; Warn delivers with a line; any
failure lets the output through with `rules.unchecked`, except an output too large to check, which is held.

Acme is the fictional company (backend/tests/test_org_rules.py's org): Ana owns it, Ben is the person above Ops."""
import json
import threading
import time
import uuid

import pytest

from backend import call_slots as CS
from backend import org_rule_check as K
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import claim, get, headers, post, setup_attempt  # noqa: F401
from backend.tests.test_org_rules import api, as_bot, edit, org, rule  # noqa: F401

COLOR = "Never tell anyone Acme's favorite color."
NEEDLE = "favorite color is"


class Judge:
    """Answers rule-check@1: a rule is broken when the output holds that rule's needle. It notes whether the store's
    write lock was held when it was asked, which must never happen."""

    def __init__(self, store, needles=None):
        self.store, self.needles = store, dict(needles or {COLOR: NEEDLE})
        self.calls, self.locked, self.fail, self.hold = [], [], None, None

    def __call__(self, state, questions, label=None, level=None):
        self.locked.append(bool(self.store.writer.queue))
        self.calls.append({"state": state, "questions": questions, "label": label, "level": level})
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
    assert got["held"]["outcome"] == "blocked" and "hub rules appeal" in got["held"]["text"]
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
    assert row["body"] != text and row["body"].endswith(text.rstrip())       # the warning line, then the text whole
    assert [json.loads(e["detail"])["reason"] for e in events(api, "rules.unchecked")] == ["too_large"]
    assert events(api, "rules.warned") == [] and get(api, "rules/stats")["unchecked_today"] == 1


def test_botops_acting_as_a_person_is_checked(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"all_bots": True}})
    store = api.app.state.store
    as_ana = Identity("human:ana", "owner", "ana@acme.example", via="botops", task_actor="bot:botops")
    verdict = K.preflight(store, judge, as_ana, "message", "Acme's favorite color is green", ["human:ben"])
    assert verdict["author"] == "bot:botops" and verdict["outcome"] == "broken" and K.holds(verdict)
    for own in (Identity("human:ana", "owner", "ana@acme.example"),
                Identity("human:ana", "owner", "ana@acme.example", via="assistant")):
        assert K.preflight(store, judge, own, "message", "Acme's favorite color is green", ["human:ben"]) is None
    # Ana's own message is never checked.
    assert "held" not in say(api, "My favorite color is green", to="ben", token="ana-test")
    assert len(judge.calls) == 1


def test_an_all_bots_rule_covers_a_bot_made_later(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"all_bots": True}})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id, slug, display_name, state) VALUES('late-bot', 'late-bot', 'Late', 'active')")
        c.execute("INSERT INTO bot_config(bot,config,team,owner_id) VALUES('late-bot','{}','', 'ana')")
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
    r = api.post(f"/api/v2/tasks/{task['id']}/comments/{mid}", json={"text": "Our favorite color is red."},
                 headers=headers("ops-test"))
    assert r.status_code == 200 and r.json()["held"], r.text
    assert sql(api, "SELECT body FROM messages WHERE id=?", (mid,))[0]["body"] == "Draft is in progress."
    held = post(api, f"tasks/{task['id']}/comments", {"text": "Fun fact: our favorite color is red."},
                token="ops-test")
    assert held["held"] and bodies(api, "Fun fact") == []


def test_a_task_body_or_close_note_held_leaves_the_task_unchanged(ruled):
    api, _ = ruled
    color_rule(api)
    task = task_for_ops(api)
    for change in ({"body": "Note: our favorite color is red."}, {"status": "done", "note": "Done; favorite color is red"}):
        got = post(api, f"tasks/{task['id']}", {"version": task["version"], **change}, token="ops-test")
        assert got["held"], got
    row = sql(api, "SELECT body, status, note, version FROM tasks WHERE id=?", (task["id"],))[0]
    assert row["body"] == "Draft it." and row["status"] != "done" and row["version"] == task["version"]


def test_a_new_task_from_a_bot_is_held(ruled):
    api, _ = ruled
    color_rule(api)
    got = post(api, "tasks", {"owner": "ana", "title": "Pick a palette", "body": "Our favorite color is red."},
               token="ops-test")
    assert got["held"] and sql(api, "SELECT id FROM tasks WHERE title='Pick a palette'") == []


def test_a_mixed_bulk_change_holds_only_the_breaking_task(ruled):
    api, _ = ruled
    color_rule(api)
    a = task_for_ops(api)
    b = post(api, "tasks", {"owner": "ops", "title": "Pick the palette", "body": "Our favorite color is red."})
    r = api.post("/api/v2/task-bulk", json={"filter": {"ids": [a["id"], b["id"]]}, "action": "owner", "owner": "ana"},
                 headers=headers("ops-test"))
    assert r.status_code == 200, r.text
    owners = {row["id"]: row["owner_id"] for row in sql(api, "SELECT id, owner_id FROM tasks WHERE id IN (?,?)", (a["id"], b["id"]))}
    assert owners == {a["id"]: "human:ana", b["id"]: "bot:ops"}
    assert [s.get("code") for s in r.json()["tasks"]["skipped"]] == ["held"]


def test_a_note_to_a_bot_and_a_wake_bot_step_are_checked(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}})
    got = post(api, "notes", {"to": "finance", "text": "FYI our favorite color is red."}, token="ops-test")
    assert got["held"] and bodies(api, "FYI our favorite") == []
    aid = post(api, "automations", {"name": "Tell", "owner": "bot:ops", "trigger": {"type": "event", "name": "x.y"},
                                    "steps": [{"id": "w", "type": "action", "do": "wake_bot",
                                               "args": {"bot": "finance", "text": "Wake: favorite color is red"}}]})["id"]
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
    with monkeypatch.context() as m:              # the same page shows live text for a bot no rule names
        m.setattr(K, "watched", lambda c, bot: False)
        assert "favorite color" in api.get(page, headers=headers()).text
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": secret, "last_seq": 1}, token=r["token"])
    messages = get(api, f"conversations/{msg['conversation_id']}/messages")
    assert "reply was held" in messages[-1]["body"] and "blue" not in json.dumps(messages)
    for query in ("SELECT final_text AS t FROM attempts WHERE id=?", "SELECT result AS t FROM attempts WHERE id=?",
                  "SELECT payload AS t FROM attempt_events WHERE attempt_id=?"):
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
        c.execute("UPDATE bot_config SET owner_id='', reports_to=NULL, config=json_remove(config, "
                  "'$.reports_to') WHERE bot='ops'")
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
    assert get(api, listed, token="ana-test")["holds"] == []        # Dan reviews it while Dan can sign in
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
    color_rule(api, if_broken="escalate", reviewer="dan")          # the named reviewer has left too
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
    monkeypatch.setattr(K, "owner_or_admin", lambda c: None)       # no person on the team at all
    color_rule(api, if_broken="escalate")
    held = say(api, "Our favorite color is gold")["held"]
    row = hold_row(api, held["id"])
    assert held["outcome"] == "blocked" and row["reviewer"] is None and row["task_id"] is None
    get(api, f"rule-holds/{held['id']}", token="cara-test", expected=404)          # a plain member never reads it
    view = get(api, f"rule-holds/{held['id']}", token="ben-test")                 # an admin decides it
    assert view["can_send"] and post(api, f"rule-holds/{held['id']}/keep", {}, token="ben-test")["state"] == "kept"
    again = say(api, "Our favorite color is teal")["held"]
    view = get(api, f"rule-holds/{again['id']}")                                  # the owner sends one anyway
    assert post(api, f"rule-holds/{again['id']}/send", {"nonce": view["nonce"]})["hold"]["state"] == "sent"
    assert len(bodies(api, "favorite color is teal")) == 1


def test_keep_blocked_and_only_the_reviewer_or_author_reads_a_hold(ruled):
    api, _ = ruled
    color_rule(api, reviewer="ben")
    held = say(api, "Our favorite color is gold")["held"]
    get(api, f"rule-holds/{held['id']}", token="cara-test", expected=404)
    get(api, f"rule-holds/{held['id']}", token="ana-test", expected=404)        # the owner too: not the reviewer
    assert "nonce" not in get(api, f"rule-holds/{held['id']}", token="ops-test")
    # The held text is in no conversation, comment or inbox, and makes no work.
    assert bodies(api, "gold") == [] and sql(api, "SELECT id FROM jobs WHERE bot_id='ana'") == []
    assert "gold" not in api.get("/api/v2/inbox", headers=headers()).text
    kept = post(api, f"rule-holds/{held['id']}/keep", {}, token="ben-test")
    assert kept["state"] == "kept"
    post(api, f"rule-holds/{held['id']}/keep", {}, token="ben-test", expected=409)


def test_an_appeal_reaches_the_reviewer_even_when_a_rule_blocks_messaging_them(ruled):
    api, judge = ruled
    color_rule(api, reviewer="ben")
    rule(api, {"kind": "internal", "text": "Never message Ben.", "who": {"bots": ["ops"]}, "with": {"people": ["ben"]}})
    judge.needles["Never message Ben"] = ""            # every output to Ben breaks it
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
        monkeypatch.setitem(K.DEADLINES, "medium", 0.2)
        judge.hold = threading.Event()
    elif how == "budget":
        api.app.state.store.settings.rules_monthly_usd = 1.0
        with api.app.state.store.transaction() as c:
            H.event(c, H.KEEPER, "rules.spend", "rule-check@1", {"cost_usd": 5.0})
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
    rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}}, token="ops-test")    # suggested only
    built = []
    real = K.R._ctx
    monkeypatch.setattr(K.R, "_ctx", lambda c: built.append(1) or real(c))
    assert "held" not in say(api, "Our favorite color is gold")
    task = task_for_ops(api)
    post(api, f"tasks/{task['id']}/comments", {"text": "Our favorite color is red."}, token="ops-test")
    assert built == [] and judge.calls == []


def test_the_check_runs_under_its_levels_deadline(ruled):
    api, judge = ruled
    color_rule(api)
    from clients import judge as J
    left = []

    def engine(state, questions, label=None, level=None):
        left.append(J.time_left(999))           # what the provider's own try may take: cut to the check's deadline
        return judge(state, questions, label, level)
    api.app.state.judge = engine
    assert say(api, "Our favorite color is gold")["held"]
    assert 0 < left[0] <= K.deadline("medium")


def test_a_full_pool_goes_through_unchecked_at_once(ruled):
    api, judge = ruled
    color_rule(api)
    api.app.state.store.settings.rule_check_concurrency = 1
    judge.hold = threading.Event()
    first = threading.Thread(target=say, args=(api, "Our favorite color is gold"))
    first.start()
    try:
        for _ in range(200):
            if judge.calls:
                break
            time.sleep(0.01)
        started = time.monotonic()
        got = say(api, "Our favorite color is teal")            # the one slot is taken: no waiting for it
        assert time.monotonic() - started < 2
    finally:
        judge.hold.set()
        first.join(10)
    assert "held" not in got and len(judge.calls) == 1 and len(bodies(api, "favorite color is teal")) == 1
    assert [json.loads(e["detail"])["reason"] for e in events(api, "rules.unchecked")] == ["busy"]


def test_an_open_breaker_goes_through_unchecked_at_once_and_a_probe_closes_it(ruled):
    api, judge = ruled
    color_rule(api)
    judge.fail = RuntimeError("provider down")
    for n in range(CS.BREAKER_FAILURES):
        assert "held" not in say(api, f"Our favorite color is gold {n}")
    assert len(judge.calls) == CS.BREAKER_FAILURES
    started = time.monotonic()
    assert "held" not in say(api, "Our favorite color is teal")
    assert time.monotonic() - started < 2 and len(judge.calls) == CS.BREAKER_FAILURES     # nothing was asked
    reasons = [json.loads(e["detail"])["reason"] for e in events(api, "rules.unchecked")]
    assert reasons == ["error"] * CS.BREAKER_FAILURES + ["breaker"]
    # After the cool-off the next check is the probe: it answers, and checks run again.
    calls = K.calls_for(api.app.state.store)
    calls.until = time.monotonic() - 1
    judge.fail = None
    assert say(api, "Our favorite color is red")["held"]
    assert calls.failures == 0 and say(api, "Our favorite color is blue")["held"]


def test_the_rules_budget_is_apart_from_decides(ruled):
    api, judge = ruled
    color_rule(api)
    settings = api.app.state.store.settings
    settings.decisions_monthly_usd, settings.rules_monthly_usd = 1.0, 50.0
    with api.app.state.store.transaction() as c:
        H.event(c, H.KEEPER, "judge.spend", "person-ask-ingest@1", {"cost_usd": 5.0})
    assert say(api, "Our favorite color is gold")["held"]           # Decide's cap is spent; the rules' is not
    with api.app.state.store.read() as c:
        assert K.B.spent_this_month(c, None, K.BUDGET) > 0
        assert K.B.over_spend_cap(c, settings) and not K.B.over_spend_cap(c, settings, None, K.BUDGET)


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
    from backend.tests.test_org_rules_surfaces import cli, mcp
    from clients import hubcli, remotecli
    from backend.tests.test_automation_cli import Client
    api, judge = ruled
    color_rule(api, reviewer="ben")
    checked = cli(api, "ops-test", "check", "Our favorite color is gold", "--to", "ana")
    assert checked["outcome"] == "broken" and checked["rules"][0]["text"] == COLOR
    held = say(api, "Our favorite color is gold")["held"]
    assert len(judge.calls) == 1
    args = hubcli.parser().parse_args(["rules", "appeal", held["id"], "Gold is on our public site."])
    appealed = remotecli.via_tool(Client(api, "ops-test"), remotecli._rules_args(args))
    assert appealed["task_id"]
    error, _ = mcp(api, "hub_rules_check", {"text": "Hello", "to": "ana"}, token="ops-test")
    assert not error


def test_rule_check_eval_cases_build_valid_requests():
    from evals.rule_check import run as E
    assert E.main([]) == 0
    assert E.engines(["low"], env={}) == []


# ---- every route, the run's provenance, live text -----------------------------------------------------------------

def test_a_conversation_message_and_both_answer_routes_are_checked(ruled):
    api, _ = ruled
    color_rule(api)
    origin = post(api, "chat/ops", {"text": "What is new?"})
    cid = origin["conversation_id"]
    held = post(api, f"conversations/{cid}/messages", {"text": "Our favorite color is red."}, token="ops-test")
    assert held["held"] and bodies(api, "favorite color is red") == []
    assert post(api, f"conversations/{cid}/messages", {"text": "All clear."}, token="ops-test")["message"]["body"] == \
        "All clear."
    ask = post(api, "messages", {"to": "ops", "kind": "ask", "text": "Which color?", "conversation_id": cid})
    got = post(api, f"messages/{ask['id']}/answer", {"text": "Our favorite color is teal."}, token="ops-test")
    assert got["held"] and bodies(api, "favorite color is teal") == []
    task = task_for_ops(api)
    got = post(api, f"tasks/{task['id']}/answers", {"target": {"comment": "none"}, "other": "Favorite color is jade."},
               token="ops-test")
    assert got["held"] and bodies(api, "Favorite color is jade") == []


def test_the_assistant_writing_as_itself_is_checked(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"all_bots": True}})
    bot = "bot:" + api.app.state.store.settings.assistant_bot
    verdict = K.preflight(api.app.state.store, judge, Identity(bot, "bot"), "message", "Our favorite color is red",
                          ["human:ben"])
    assert verdict["author"] == bot and K.holds(verdict)


def test_a_public_task_is_judged_on_its_public_text_for_the_whole_company(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}, "with": {"people": ["cara"]}})
    got = post(api, "tasks", {"owner": "ops", "title": "Ship the palette", "body": "Internal draft.",
                              "visibility": "public", "public_title": "New palette",
                              "public_summary": "Our favorite color is red."}, token="ops-test")
    assert got["held"], got
    assert "human:cara" in judge.calls[0]["state"]["to"] or "Cara" in judge.calls[0]["state"]["to"]


def test_a_task_change_warning_rides_on_the_change_itself(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    got = post(api, f"tasks/{task['id']}", {"version": task["version"], "body": "Our favorite color is red."},
               token="ops-test")
    assert got["rule_warning"]
    row = sql(api, "SELECT body FROM tasks WHERE id=?", (task["id"],))[0]
    assert row["body"].startswith("Warning:") and row["body"].endswith("Our favorite color is red.")
    assert sql(api, "SELECT id FROM messages WHERE sender_id=? AND body LIKE 'Warning:%'", (H.KEEPER,)) == []


def test_automation_assign_judges_the_task_handed_over_and_replies_reach_their_people(ruled):
    api, judge = ruled
    color_rule(api)
    task = post(api, "tasks", {"owner": "ops", "title": "Palette", "body": "Our favorite color is red."})
    for args in ({"owner": "ana"}, {"owner": "ana", "note": "Over to you."}):
        aid = post(api, "automations", {"name": "Hand " + str(len(args)), "owner": "bot:ops",
                                        "trigger": {"type": "event", "name": "x.y"},
                                        "steps": [{"id": "a", "type": "action", "do": "assign", "args": args}]})["id"]
        post(api, f"automations/{aid}/run", {"input": {"task": {"id": task["id"]}}})
        assert sql(api, "SELECT owner_id FROM tasks WHERE id=?", (task["id"],))[0]["owner_id"] == "bot:ops"
    assert len(events(api, "rules.blocked")) == 2
    from backend import automations as AU
    with api.app.state.store.read() as c:
        slack = {"message": {"source": "slack", "from": "person:cara", "text": "hi"}}
        assert "human:cara" in AU._rule_audience(c, {"id": "x", "flow": {"owner": "bot:ops"}}, "send_reply",
                                                 {"bot": "ops"}, slack)
        mail = {"message": {"source": "mail", "mailbox": "ops@acme.example", "from": "ben@acme.example",
                            "to": ["ops@acme.example"], "cc": ["vendor@outside.example"]}}
        assert AU._rule_audience(c, {"id": "x", "flow": {"owner": "bot:ops"}}, "send_reply", {}, mail) == ["human:ben"]
        assert AU._reply_addresses(mail) == ["ben@acme.example", "vendor@outside.example"]
        everyone = AU._rule_audience(c, {"id": "x", "flow": {"owner": "bot:ops"}}, "post_update", {}, {})
        assert {"human:ana", "human:ben", "human:cara"} <= set(everyone)


def review_turn(api):
    """A review subtask for ops in its own room, claimed and started: its reply waits until the task is done."""
    from backend.tests.test_api import assign, ready, runner
    r = runner(api, "ana")
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, "human:ana", "Ship the palette", "Build it.", "human:ana", lint=False)
        review = H.task_create(c, "human:ana", "Review: palette", "Review it.", "bot:ops", parent_id=parent["id"],
                               lint=False)
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
    post(api, f"attempts/{aid}/events", {"events": [
        {"seq": 1, "kind": "delta", "payload": {"text": "thinking about blue", "delta_kind": "thought"}},
        {"seq": 2, "kind": "message", "payload": {"text": secret}}]}, token=r["token"])
    frames = {"provider": "claude", "frames": '{"dir":"in","ms":5,"frame":{"text":"favorite color is blue"}}'}
    post(api, f"attempts/{aid}/capture", frames, r["token"])
    # Live: no said or thought text in the steps, and no raw stream, before the reply is checked.
    assert "blue" not in api.get(f"/api/v2/turns/{aid}/steps", headers=headers()).text
    get(api, f"attempts/{aid}/capture", expected=404)
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": secret, "last_seq": 2}, token=r["token"])
    for query in ("SELECT final_text AS t FROM attempts WHERE id=?", "SELECT result AS t FROM attempts WHERE id=?",
                  "SELECT payload AS t FROM attempt_events WHERE attempt_id=?",
                  "SELECT summary AS t FROM turns WHERE id=?"):
        assert all("blue" not in str(row["t"] or "") for row in sql(api, query, (aid,))), query
    assert "blue" not in str(sql(api, "SELECT checkpoint FROM tasks WHERE id=?", (review["id"],))[0]["checkpoint"] or "")
    # Not recorded from a held reply: the review's verdict stays unset.
    assert sql(api, "SELECT verdict FROM tasks WHERE id=?", (review["id"],))[0]["verdict"] is None
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
    post(api, f"attempts/{aid}/events", {"events": [
        {"seq": 1, "kind": "tool", "payload": {"name": "read"}},
        {"seq": 2, "kind": "delta", "payload": {"text": "thinking about blue"}},
        {"seq": 3, "kind": "message", "payload": {"text": "Acme's favorite color is blue."}}]}, token=r["token"])
    with api.app.state.store.transaction() as c:
        hubdb.delta_append(c, "bot:ops", aid, "favorite color is blue")

    def read(sql):
        res = api.post("/api/v2/sql", json={"sql": sql}, headers=headers())
        assert res.status_code == 200, res.text
        return json.dumps(res.json())
    events_sql = f"SELECT kind, payload FROM attempt_events WHERE attempt_id='{aid}'"
    assert "blue" not in read(events_sql) and '"tool"' in read(events_sql)
    assert "blue" not in read(f"SELECT text FROM deltas WHERE turn_id='{aid}'")
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
    done = post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": "Acme's favorite color is blue.",
                                                  "last_seq": 0}, token=r["token"])
    assert done["filed"] is True
    for query in ("SELECT final_text AS t FROM attempts WHERE id=?", "SELECT result AS t FROM attempts WHERE id=?",
                  "SELECT summary AS t FROM turns WHERE id=?"):
        assert all("blue" not in str(row["t"] or "") for row in sql(api, query, (aid,))), query
    assert sql(api, "SELECT thread_id FROM attempts WHERE id=?", (aid,))[0]["thread_id"] is None

    # A watched run not settled yet keeps its reply columns out of SQL; the rest of the run reads as it is.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET final_text=?, result=? WHERE id=?",
                  ("favorite color is blue", json.dumps({"text": "favorite color is blue", "outcome": "completed"}), aid))
        c.execute("UPDATE turns SET summary='favorite color is blue' WHERE id=?", (aid,))
    res = api.post("/api/v2/sql", json={"sql": f"SELECT a.state, a.final_text, a.result, t.summary FROM attempts a "
                                               f"JOIN turns t ON t.id=a.id WHERE a.id='{aid}'"}, headers=headers())
    assert res.status_code == 200, res.text
    assert "blue" not in res.text and "expired" in res.text and "completed" in res.text


def test_a_review_reply_that_passes_records_its_verdict_from_the_delivered_text(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    r, review, attempt = review_turn(api)
    text = "VERDICT: approve\nAcme's favorite color is blue."
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": text, "last_seq": 0},
         token=r["token"])
    assert sql(api, "SELECT verdict FROM tasks WHERE id=?", (review["id"],))[0]["verdict"] == "approve"
    assert sql(api, "SELECT final_text FROM attempts WHERE id=?", (attempt["id"],))[0]["final_text"].startswith("Warning:")


def test_send_anyway_of_a_reply_rechecks_where_it_may_go(ruled):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    r, msg, attempt = setup_attempt(api)
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "t"}, r["token"])
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": "Acme's favorite color is blue.",
                                           "last_seq": 0}, token=r["token"])
    hold = sql(api, "SELECT * FROM org_rule_holds")[0]
    assert json.loads(hold["payload"])["provenance"]["attempt_id"] == aid
    # The run read a private task Ana cannot read: the reply may not go to her now.
    with api.app.state.store.transaction() as c:
        private = H.task_create(c, "human:ben", "Private plan", "Secret.", "human:ben", lint=False, private=True)
        c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (aid, private["id"]))
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


def test_send_anyway_of_a_question_runs_ask_ingest_outside_the_write(ruled, monkeypatch):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    from backend import ask_ingest
    seen = []
    real = ask_ingest.preflight

    def spy(store, *args, **kw):
        seen.append(bool(store.writer.queue))
        return real(store, *args, **kw)
    monkeypatch.setattr(ask_ingest, "preflight", spy)
    held = say(api, "Our favorite color is gold, right?", kind="ask")["held"]
    assert seen == []                           # a held write skips ask ingest
    view = get(api, f"rule-holds/{held['id']}", token="ben-test")
    post(api, f"rule-holds/{held['id']}/send", {"nonce": view["nonce"]}, token="ben-test")
    assert seen == [False]


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
    threads = [threading.Thread(target=lambda: outcomes.append(
        post(api, "bots/ops/mail-check", mail, token="ops-test")["outcome"])) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["held", "held", "pass"]


def test_botops_mail_for_a_person_finds_its_own_hold_and_spends_its_approval_once(ruled):
    api, _ = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"all_bots": True}, "if_broken": "escalate", "reviewer": "ben"})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(id, slug, display_name, state) VALUES('botops', 'botops', 'BotOps', 'active')")
    as_bot(api, "botops", "botops-test")
    api.app.state.auth.settings.test_identities["ana-botops"] = Identity(
        "human:ana", "owner", "ana@acme.example", via="botops", task_actor="bot:botops", agent="test")
    mail = {"to": ["vendor@outside.example"], "subject": "Hi", "body": "Our favorite color is gold."}
    first = post(api, "bots/botops/mail-check", mail, token="ana-botops")
    again = post(api, "bots/botops/mail-check", mail, token="ana-botops")
    assert first["outcome"] == again["outcome"] == "held" and first["held"]["id"] == again["held"]["id"]
    [hold] = sql(api, "SELECT id, author FROM org_rule_holds WHERE operation='mail'")
    assert hold["author"] == "bot:botops"
    view = get(api, f"rule-holds/{hold['id']}", token="ben-test")
    post(api, f"rule-holds/{hold['id']}/send", {"nonce": view["nonce"]}, token="ben-test")
    sent = post(api, "bots/botops/mail-check", mail, token="ana-botops")
    assert sent["outcome"] == "pass" and sent["approved"] == hold["id"]
    assert post(api, "bots/botops/mail-check", mail, token="ana-botops")["outcome"] == "held"


def test_reported_misses_count_once_however_often_or_concurrently_they_come(ruled):
    api, _ = ruled
    color_rule(api)
    ids = [str(uuid.uuid4()) for _ in range(3)]
    assert post(api, "bots/ops/mail-check/misses", {"ids": ids[:2]}, token="ops-test") == {"recorded": 2, "known": []}
    # The reply was lost and the connector sends them again, with a new one.
    again = post(api, "bots/ops/mail-check/misses", {"ids": ids}, token="ops-test")
    assert again == {"recorded": 1, "known": ids[:2]}
    late = str(uuid.uuid4())
    answers = []
    threads = [threading.Thread(target=lambda: answers.append(
        post(api, "bots/ops/mail-check/misses", {"ids": [late]}, token="ops-test")["recorded"])) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(answers) == [0, 0, 0, 1]
    assert [json.loads(e["detail"])["reason"] for e in events(api, "rules.unchecked")] == ["connector"] * 4
    assert get(api, "rules/stats")["unchecked_today"] == 4
    # No rule covers Finance, a person's own mail was never checked, and a bot reports only its own.
    assert post(api, "bots/finance/mail-check/misses", {"ids": [str(uuid.uuid4())]}, token="finance-test")[
        "recorded"] == 0
    assert post(api, "bots/ops/mail-check/misses", {"ids": [str(uuid.uuid4())]}, token="ben-test")["recorded"] == 0
    post(api, "bots/ops/mail-check/misses", {"ids": [str(uuid.uuid4())]}, token="finance-test", expected=403)
    post(api, "bots/ops/mail-check/misses", {"ids": ["not-an-id"]}, token="ops-test", expected=422)
    post(api, "bots/ops/mail-check/misses", {"ids": [str(uuid.uuid4()) for _ in range(101)]}, token="ops-test",
         expected=422)
    assert get(api, "rules/stats")["unchecked_today"] == 4


@pytest.mark.parametrize("how", ["budget", "error", "stale_lease", "proxy_530", "forbidden"])
def test_a_send_after_a_failed_check_counts_once_whether_the_route_counted_it_or_never_saw_it(ruled, tmp_path,
                                                                                            monkeypatch, how):
    # The connector's own pieces (connectors/mail) against this server: a check with no verdict for the mail tool,
    # the mail sent and kept by the check's id, then Tico answering again.
    from connectors.mail import db as mail_db, policy as pl
    api, judge = ruled
    color_rule(api)
    conn = mail_db.connect(tmp_path / "mail.db")
    before = get(api, "rules/stats")["unchecked_today"]
    token = "finance-test" if how == "forbidden" else "ops-test"

    def check(path, body, key=None):
        if how == "budget":
            api.app.state.store.settings.rules_monthly_usd = 1.0
            with api.app.state.store.transaction() as c:
                H.event(c, H.KEEPER, "rules.spend", "rule-check@1", {"cost_usd": 5.0})
        judge.fail = RuntimeError("provider down") if how == "error" else None
        if how in ("budget", "error"):
            # The route counts it unchecked and commits; its answer is lost on the way back.
            assert post(api, path, body, token=token, key=key)["outcome"] == "unchecked"
            raise pl.HubFailed("unavailable")
        if how == "forbidden":
            post(api, path, body, token=token, key=key, expected=403)       # counted by the route, refused
            raise pl.HubFailed("forbidden", 403)
        raise pl.HubFailed(*{"stale_lease": ("http_409", 409), "proxy_530": ("http_530", 530)}[how])

    def tico(path, body, key=None):
        r = api.post("/api/v2/" + path, json=body, headers=headers(token, key))
        if r.status_code >= 300:
            raise pl.HubFailed(f"http_{r.status_code}", r.status_code)
        return r.json()
    monkeypatch.setattr(pl, "HUB_POST", check)
    mail = (["vendor@outside.example"], [], "Hi", "Thanks for the call.")
    check_id = str(uuid.uuid4())
    assert pl.rule_check("ops", *mail, key=check_id)["failed"]
    mail_db.add_mail_check_miss(conn, "ops", check_id)          # the mail went out with no verdict
    monkeypatch.setattr(pl, "HUB_POST", tico)
    judge.fail = None
    api.app.state.store.settings.rules_monthly_usd = 0
    if how == "forbidden":
        # The other bot's report is refused too; the route already counted that check.
        assert pl.report_misses("ops", [check_id]) == []
        assert get(api, "rules/stats")["unchecked_today"] == before + 1
        return
    pending = mail_db.mail_check_misses(conn, "ops")
    assert pl.report_misses("ops", pending) == pending == [check_id]
    assert pl.report_misses("ops", pending) == pending           # a repeat, as after a lost reply
    mail_db.drop_mail_check_misses(conn, pending)
    assert get(api, "rules/stats")["unchecked_today"] == before + 1


def _no_budget(api):
    api.app.state.store.settings.rules_monthly_usd = 1.0
    with api.app.state.store.transaction() as c:
        H.event(c, H.KEEPER, "rules.spend", "rule-check@1", {"cost_usd": 5.0})


@pytest.mark.parametrize("how", ["budget", "error"])
def test_a_report_that_lands_before_its_check_finishes_unchecked_counts_once(ruled, monkeypatch, how):
    # The mail tool gave up waiting, sent the mail, and reported it; then the check itself finishes as unchecked.
    api, _ = ruled
    color_rule(api)
    check_id = str(uuid.uuid4())
    assert post(api, "bots/ops/mail-check/misses", {"ids": [check_id]}, token="ops-test")["recorded"] == 1
    mail = {"to": ["vendor@outside.example"], "subject": "Hi", "body": "Thanks for the call."}
    if how == "budget":
        _no_budget(api)
        assert post(api, "bots/ops/mail-check", mail, token="ops-test", key=check_id)["outcome"] == "unchecked"
    else:
        monkeypatch.setattr(K, "mail_approved", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            api.post("/api/v2/bots/ops/mail-check", json=mail, headers=headers("ops-test", check_id))
    assert get(api, "rules/stats")["unchecked_today"] == 1


def test_a_report_not_counted_is_not_kept_so_its_check_still_counts(ruled):
    api, _ = ruled
    color_rule(api, **{"with": {"people": ["ben"]}})
    check_id = str(uuid.uuid4())
    assert post(api, "bots/ops/mail-check/misses", {"ids": [check_id]}, token="ops-test") == \
        {"recorded": 0, "known": []}
    _no_budget(api)
    mail = {"to": ["ben@acme.example"], "subject": "Hi", "body": "Thanks for the call."}
    assert post(api, "bots/ops/mail-check", mail, token="ops-test", key=check_id)["outcome"] == "unchecked"
    assert post(api, "bots/ops/mail-check/misses", {"ids": [check_id]}, token="ops-test")["known"] == [check_id]
    assert get(api, "rules/stats")["unchecked_today"] == 1


def test_a_report_counts_only_under_a_rule_with_no_with_and_up_to_a_days_cap(ruled, monkeypatch):
    api, _ = ruled
    color_rule(api, **{"with": {"people": ["ben"]}})          # only mail to Ben: an id cannot say where a mail went
    assert post(api, "bots/ops/mail-check/misses", {"ids": [str(uuid.uuid4())]}, token="ops-test")["recorded"] == 0
    color_rule(api)
    monkeypatch.setattr(K, "MISSES_DAY_MAX", 3)
    ids = [str(uuid.uuid4()) for _ in range(4)]
    assert post(api, "bots/ops/mail-check/misses", {"ids": ids}, token="ops-test") == {"recorded": 3, "known": []}
    assert post(api, "bots/ops/mail-check/misses", {"ids": [str(uuid.uuid4())]}, token="ops-test")["recorded"] == 0
    # The one over the cap was not kept: only the counted ones are known.
    assert post(api, "bots/ops/mail-check/misses", {"ids": ids}, token="ops-test")["known"] == ids[:3]
    assert get(api, "rules/stats")["unchecked_today"] == 3


def test_a_mail_check_that_cannot_run_is_counted_unchecked_and_a_persons_mail_is_not_checked(ruled, monkeypatch):
    api, judge = ruled
    color_rule(api)
    mail = {"to": ["vendor@outside.example"], "subject": "Hi", "body": "Our favorite color is gold."}
    # Another bot's token: refused, and the mail the connector then sends unchecked counts as unchecked.
    post(api, "bots/ops/mail-check", mail, token="finance-test", expected=403)
    [event] = events(api, "rules.unchecked")
    assert event["actor"] == "bot:finance" and json.loads(event["detail"])["reason"] == "forbidden"
    # A person's Assistant writing as them: their own mail, never checked, and no refusal.
    settings = api.app.state.auth.settings
    settings.test_identities["ben-assistant"] = Identity("human:ben", "human", "ben@acme.example", via="assistant")
    assert post(api, f"bots/{settings.assistant_bot}/mail-check", mail, token="ben-assistant") == \
        {"outcome": "not_checked"}
    assert post(api, f"bots/{settings.assistant_bot}/mail-check/misses", {"ids": [str(uuid.uuid4())]},
                token="ben-assistant") == {"recorded": 0, "known": []}
    # A server error inside the check: recorded before it surfaces.
    monkeypatch.setattr(K, "mail_approved", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        api.post("/api/v2/bots/ops/mail-check", json=mail, headers=headers("ops-test"))
    assert [json.loads(e["detail"])["reason"] for e in events(api, "rules.unchecked")] == ["forbidden", "error"]
    assert judge.calls == [] and get(api, "rules/stats")["unchecked_today"] == 2


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
            c.execute("INSERT INTO org_rule_holds(id, operation, payload, author, destinations, rule_ids, "
                      "reviewer, task_id, state, nonce, verdict, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (f"h-{n:03d}", "message", json.dumps({"text": big, "replay": {}}), row["author"],
                       row["destinations"], row["rule_ids"], row["reviewer"], task_id, "held", f"n{n}",
                       row["verdict"], f"2026-01-01T00:00:{n:02d}Z"))
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


def test_the_cache_splits_on_context_and_a_partial_check_stays_unchecked(ruled, monkeypatch):
    api, judge = ruled
    color_rule(api)
    store, ops = api.app.state.store, Identity("bot:ops", "bot")
    for step in ("a", "a", "b"):
        K.preflight(store, judge, ops, "automation", "Launch is Tuesday.", ["human:ana"], {"step_id": step})
    assert len(judge.calls) == 2
    from clients import judge as J
    monkeypatch.setattr(J, "MAX_QUESTIONS", 1)
    rule(api, {"kind": "internal", "text": "Never share the launch date.", "who": {"bots": ["ops"]}})
    first = K.preflight(store, judge, ops, "message", "Lunch at noon.", ["human:ana"])
    again = K.preflight(store, judge, ops, "message", "Lunch at noon.", ["human:ana"])
    assert first["unchecked_rules"] and again["cached"] and again["unchecked_rules"] == first["unchecked_rules"]


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

def hand_over(api):
    """An automation of ops that assigns its run's task to Ana."""
    step = {"id": "a", "type": "action", "do": "assign", "args": {"owner": "ana"}}
    return post(api, "automations", {"name": "Hand", "owner": "bot:ops", "trigger": {"type": "event", "name": "x.y"},
                                     "steps": [step]})["id"]


LAUNCH = "Never share the launch date."


def started_turn(api):
    r, msg, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, r["token"])
    return r, msg.get("message", msg), attempt


def send_anyway(api, hold_id, expected=200):
    view = get(api, f"rule-holds/{hold_id}", token="ben-test")
    r = api.post(f"/api/v2/rule-holds/{hold_id}/send", json={"nonce": view["nonce"]}, headers=headers("ben-test"))
    assert r.status_code == expected, r.text
    return r.json()


def pick_question(api, task_id, label, qid="q"):
    """Ana's options-only question to ops on a task (no Other)."""
    with api.app.state.store.transaction() as c:
        return H.task_comment(c, "human:ana", task_id, "Which line goes out?", wake=False, ask={
            "questions": [{"id": qid, "header": "Line", "question": "Which line goes out?", "multi": False,
                           "other": False, "options": [{"label": label, "description": ""},
                                                       {"label": "Something else", "description": ""}]}],
            "who": "bot:ops"})


def test_send_anyway_runs_each_routes_privacy_checks_as_the_run(ruled):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    r, msg, attempt = started_turn(api)
    aid, run = attempt["id"], attempt["token"]
    task = task_for_ops(api)
    held = [post(api, f"tasks/{task['id']}/comments", {"text": "Our favorite color is red."}, token=run)["held"]]
    ask = post(api, "messages", {"to": "ops", "kind": "ask", "text": "Which color?",
                                 "conversation_id": msg["conversation_id"]})
    held.append(post(api, f"messages/{ask['id']}/answer", {"text": "Our favorite color is teal."}, token=run)["held"])
    assert all(held)
    # The run now carries a private task neither Ana nor the company task's readers may read.
    with api.app.state.store.transaction() as c:
        private = H.task_create(c, "human:ben", "Private plan", "Secret.", "human:ben", lint=False, private=True)
        c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (aid, private["id"]))
    for h in held:
        send_anyway(api, h["id"], expected=403)
        assert hold_row(api, h["id"])["state"] == "held"
    assert bodies(api, "favorite color is red") == [] and bodies(api, "favorite color is teal") == []
    paths = [json.loads(hold_row(api, h["id"])["payload"])["provenance"]["path"] for h in held]
    assert paths == [f"/api/v2/tasks/{task['id']}/comments", f"/api/v2/messages/{ask['id']}/answer"]


def test_a_task_answer_replay_is_refused_once_the_run_turned_private(ruled):
    api, judge = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    r, msg, attempt = started_turn(api)
    task = task_for_ops(api)
    question = pick_question(api, task["id"], "Our favorite color is red")
    got = post(api, f"tasks/{task['id']}/answers", {"target": {"comment": question["id"]},
                                                    "answers": {"q": ["Our favorite color is red"]}},
               token=attempt["token"])
    assert got["held"]
    with api.app.state.store.transaction() as c:
        private = H.task_create(c, "human:ben", "Private plan", "Secret.", "human:ben", lint=False, private=True)
        c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (attempt["id"], private["id"]))
    send_anyway(api, got["held"]["id"], expected=403)
    assert hold_row(api, got["held"]["id"])["state"] == "held"


def test_send_anyway_refuses_once_the_task_changed(ruled):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    task = task_for_ops(api)
    held = post(api, f"tasks/{task['id']}", {"version": task["version"], "owner": "ana",
                                             "note": "Our favorite color is red."}, token="ops-test")["held"]
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
    rule(api, {"kind": "internal", "text": "Never write to the outside vendor.", "who": {"bots": ["ops"]},
               "if_broken": "escalate", "reviewer": "ben"})
    judge.needles["outside vendor"] = "vendor@outside.example"
    outcomes = [post(api, "bots/ops/mail-check", mail, token="ops-test")["outcome"] for _ in range(3)]
    assert outcomes == ["held"] * 3
    assert "vendor@outside.example" in judge.calls[-1]["state"]["context"]["to"]
    rows = sql(api, "SELECT id, state, json_extract(verdict,'$.used') AS used FROM org_rule_holds "
                    "WHERE operation='mail' ORDER BY created_at")
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
    held = [say(api, "Our favorite color is gold and launch is Tuesday.")["held"],
            post(api, f"tasks/{task['id']}/comments", {"text": "Our favorite color is gold; launch is Tuesday."},
                 token="ops-test")["held"]]
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
    post(api, f"tasks/{task['id']}", {"version": version, "owner": "ben"})
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
    post(api, "messages", {"to": "ana", "text": "On it.", "in_reply_to": msg["id"],
                           "conversation_id": msg["conversation_id"]}, token=attempt["token"])
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": "Our favorite color is blue.",
                                           "last_seq": 0}, token=r["token"])
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
    made = post(api, "tasks", {"owner": "ops", "title": "Ship the palette", "body": "Internal draft.",
                               "visibility": "public", "public_title": "Our favorite color is red"}, token="ops-test")
    made = sql(api, "SELECT title, body, public_title, public_summary FROM tasks WHERE id=?",
               (made.get("task", made)["id"],))[0]
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
    got = post(api, f"tasks/{task['id']}", {"version": task["version"], "owner": "ana"}, token="ops-test")
    assert got["rule_warning"]
    row = sql(api, "SELECT owner_id, note FROM tasks WHERE id=?", (task["id"],))[0]
    assert row["owner_id"] == "human:ana"
    assert row["note"].startswith("Warning:") and row["note"].endswith("Waiting on the vendor.")


def test_a_warned_answer_keeps_its_pick_and_its_reader_gets_the_line(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    label = "My favorite color is blue"
    first = pick_question(api, task["id"], label)
    got = post(api, f"messages/{first['id']}/answer", {"text": label}, token="ops-test")
    assert "decision_open" not in got and got["answer"]["answers"] == {"q": [label]}
    assert got["comment"]["body"].startswith("Warning:")
    second = pick_question(api, task["id"], label, qid="q2")
    got = post(api, f"tasks/{task['id']}/answers", {"target": {"comment": second["id"]}, "answers": {"q2": [label]}},
               token="ops-test")
    assert got["rule_warning"] and got["answer"]["answers"] == {"q2": [label]}
    assert got["comment"]["body"].startswith("Warning:")


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
            c.execute("INSERT INTO org_rules(id, kind, text, who, status, suggested_by, created_at, updated_at) "
                      "VALUES(?, 'internal', ?, '{}', 'retired', 'human:ana', ?, ?)",
                      (rid, "y" * 1000, H.now(), H.now()))
        c.execute("UPDATE org_rule_holds SET rule_ids=?, payload=? WHERE id=?",
                  (json.dumps(ids), json.dumps({"text": "z" * 200_000, "replay": {}}), first["id"]))
    page = get(api, f"rule-holds?task={row['task_id']}", token="ben-test")
    assert len(json.dumps(page)) <= K.HOLD_LIST_BYTES
    one = page["holds"][0]
    assert one["rules_count"] == 110 and len(one["rules"]) == K.SUMMARY_RULES
    assert all(len(r["text"]) <= K.SUMMARY_CHARS for r in one["rules"])


def test_a_conversation_post_whose_recipient_moved_goes_out_unchecked(ruled, monkeypatch):
    api, judge = ruled
    color_rule(api)
    cid = post(api, "chat/ops", {"text": "What is new?"})["conversation_id"]
    real = K.preflight

    def then_move(*args, **kw):
        verdict = real(*args, **kw)
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE conversations SET participants=? WHERE id=?",
                      (json.dumps(["bot:ops", "human:ben"]), cid))
        return verdict
    monkeypatch.setattr(K, "preflight", then_move)
    got = post(api, f"conversations/{cid}/messages", {"text": "Our favorite color is red."}, token="ops-test")
    assert "held" not in got and got["message"]["recipient_id"] == "human:ben"
    assert [json.loads(e["detail"])["reason"] for e in events(api, "rules.unchecked")] == ["changed"]


def ask_turn(api, label):
    """A started turn of ops answering Ana's options-only question on its task (the job's message is the question)."""
    r, msg, attempt = started_turn(api)
    task = task_for_ops(api)
    question = pick_question(api, task["id"], label)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET message_id=? WHERE id=(SELECT job_id FROM attempts WHERE id=?)",
                  (question["id"], attempt["id"]))
    return r, attempt, task


def answered(api):
    """The structured answers written: each one's text and its picks."""
    rows = sql(api, "SELECT body, refs FROM messages WHERE json_extract(refs,'$.answer') IS NOT NULL "
                    "ORDER BY rowid")
    return [(r["body"], json.loads(r["refs"])["answer"]["answers"]) for r in rows]


def test_a_warned_completion_answers_an_options_question_with_its_pick(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    label = "My favorite color is blue"
    r, attempt, task = ask_turn(api, label)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (task["id"],))
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": label, "last_seq": 0},
         token=r["token"])
    got = answered(api)
    assert len(got) == 1 and got[0][0].startswith("Warning:") and got[0][1] == {"q": [label]}


def test_send_anyway_of_a_held_answer_resolves_its_pick_before_a_new_warning(ruled):
    api, judge = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    label = "Our favorite color is blue and launch is Tuesday"
    r, attempt, task = ask_turn(api, label)
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": label, "last_seq": 0},
         token=r["token"])
    hold = sql(api, "SELECT id FROM org_rule_holds WHERE operation='reply'")[0]["id"]
    rule(api, {"kind": "internal", "text": LAUNCH, "who": {"bots": ["ops"]}, "if_broken": "warn"})
    judge.needles[LAUNCH] = "launch is"
    send_anyway(api, hold)
    got = answered(api)
    assert len(got) == 1 and got[0][0].startswith("Warning:") and got[0][1] == {"q": [label]}


# ---- chat, updates and task questions; the author of a replay; public task text; the fast path ----------------------

def chat(api, text, to="finance", token="ops-test"):
    r = api.post(f"/api/v2/chat/{to}", json={"text": text}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def ask(api, question, token="ops-test", **more):
    r = api.post("/api/v2/task-questions", json={"on": "human:ana", "question": question, **more},
                 headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def test_chat_updates_and_task_questions_are_held_under_a_block_rule(ruled):
    api, judge = ruled
    color_rule(api)
    assert chat(api, "Our favorite color is blue.")["held"]
    assert post(api, "updates", {"body": "- Our favorite color is blue."}, token="ops-test")["held"]
    weekly = {"goal": "Ship the palette", "done": ["Our favorite color is teal"], "focus": ["Launch"],
              "blockers": ["None"]}
    assert post(api, "updates", {"slides": weekly}, token="ops-test")["held"]
    got = ask(api, "Ship it?", title="Palette", body="Our favorite color is blue.", topic="palette")
    assert got["held"] and set(got) == {"held"}
    assert sql(api, "SELECT id FROM messages WHERE sender_id='bot:ops'") == []
    assert sql(api, "SELECT id FROM updates WHERE bot='ops'") == []
    assert sql(api, "SELECT id FROM tasks WHERE title='Palette' OR owner_id='bot:ops'") == []
    held = sql(api, "SELECT operation FROM org_rule_holds ORDER BY created_at")
    assert [h["operation"] for h in held] == ["message", "update", "update", "task_question"]
    # An update is judged for the whole company, which reads it; a question for the person asked.
    assert "Cara" in judge.calls[1]["state"]["to"] and judge.calls[3]["state"]["to"] == ["Ana"]
    assert "Our favorite color is teal" in judge.calls[2]["state"]["output"]


def test_the_update_tool_answers_with_the_hold_or_the_warning(ruled):
    from backend.tests.test_org_rules_surfaces import mcp
    api, _ = ruled
    made = color_rule(api)
    error, held = mcp(api, "hub_update_create", {"body": "- Our favorite color is blue."}, token="ops-test")
    assert not error and held["held"]["outcome"] == "blocked"
    edit(api, made["id"], {"if_broken": "warn", "version": made["version"]})
    error, posted = mcp(api, "hub_update_create", {"body": "- Our favorite color is blue."}, token="ops-test")
    assert not error and posted["rule_warning"] and posted["body"].startswith("Warning:")


def test_chat_updates_and_task_questions_carry_a_warn_rules_line(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    got = chat(api, "Our favorite color is blue.")
    assert got["rule_warning"] and got["message"]["body"].startswith("Warning:")
    update = post(api, "updates", {"body": "- Our favorite color is blue."}, token="ops-test")
    assert update["rule_warning"] and update["update"]["body"].startswith("Warning:")
    asked = ask(api, "Ship it?", title="Palette", body="Our favorite color is blue.")
    assert asked["rule_warning"] and asked["question"]["body"].startswith("Warning:")
    row = sql(api, "SELECT body, note, waiting_on FROM tasks WHERE id=?", (asked["task"]["id"],))[0]
    assert row["body"].startswith("Warning:") and row["body"].count("Warning:") == 1
    assert row["note"].startswith("Warning:") and row["waiting_on"] == "human:ana"


def test_send_anyway_delivers_a_held_chat_update_and_task_question_once(ruled):
    api, judge = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    holds = [chat(api, "Our favorite color is blue.")["held"]["id"],
             post(api, "updates", {"body": "- Our favorite color is blue."}, token="ops-test")["held"]["id"],
             ask(api, "Ship it?", title="Palette", body="Our favorite color is blue.")["held"]["id"]]
    for hold_id in holds:
        assert send_anyway(api, hold_id)["hold"]["state"] == "sent"
    assert len(bodies(api, "favorite color is blue")) == 1           # the chat message
    assert [r["body"] for r in sql(api, "SELECT body FROM updates WHERE bot='ops'")] == \
        ["- Our favorite color is blue."]
    [task] = sql(api, "SELECT id, waiting_on FROM tasks WHERE title='Palette'")
    assert task["waiting_on"] == "human:ana"
    assert len([call for call in judge.calls if call["label"].startswith("rule-check")]) == 3


def test_send_anyway_refuses_an_automation_whose_actor_changed(ruled):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    step = {"id": "t", "type": "action", "do": "create_task",
            "args": {"owner": "ana", "title": "Palette", "body": "Our favorite color is red."}}
    aid = post(api, "automations", {"name": "File", "owner": "bot:ops", "trigger": {"type": "event", "name": "x.y"},
                                    "steps": [step]})["id"]
    post(api, f"automations/{aid}/run", {})
    [hold] = sql(api, "SELECT id FROM org_rule_holds WHERE operation='automation'")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE automations SET owner_id='bot:finance', flow=json_set(flow,'$.owner','bot:finance') "
                  "WHERE id=?", (aid,))
    assert send_anyway(api, hold["id"], expected=409)["error"]["code"] == "changed"
    assert hold_row(api, hold["id"])["state"] == "held"
    assert sql(api, "SELECT id FROM tasks WHERE title='Palette'") == []


def test_public_task_text_is_checked_for_the_whole_company(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}, "with": {"people": ["cara"]}})
    task = task_for_ops(api)
    got = post(api, f"tasks/{task['id']}", {"version": task["version"], "public_summary": "Our favorite color is red."},
               token="ops-test")
    assert got["held"], got
    assert "Cara" in judge.calls[-1]["state"]["to"]
    # Making a task public alone publishes the public text written before: that text is judged.
    drafted = post(api, "tasks", {"owner": "ops", "title": "Palette", "body": "Draft it.", "visibility": "private",
                                  "public_title": "New palette", "public_summary": "Our favorite color is red."})
    got = post(api, f"tasks/{drafted['id']}", {"version": drafted["version"], "visibility": "public"},
               token="ops-test")
    assert got["held"], got
    assert "favorite color is red" in judge.calls[-1]["state"]["output"]
    assert sql(api, "SELECT visibility FROM tasks WHERE id=?", (drafted["id"],))[0]["visibility"] == "private"


def test_with_no_rule_in_force_bot_writes_resolve_no_audience(ruled, monkeypatch):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}}, token="ops-test")    # suggested only
    resolved = []
    monkeypatch.setattr(K, "company", lambda c: resolved.append("company") or [])
    monkeypatch.setattr(K, "mail_recipients", lambda c, addresses: resolved.append("mail") or [])
    monkeypatch.setattr(H, "mentioned_people", lambda *a, **k: resolved.append("mentions") or [])
    task = task_for_ops(api)
    post(api, f"tasks/{task['id']}/comments", {"text": "Ask @ana or ben@acme.example about it."}, token="ops-test")
    assert "held" not in post(api, "updates", {"body": "- Shipped the launch note."}, token="ops-test")
    aid = post(api, "automations", {"name": "Post", "owner": "bot:finance", "trigger": {"type": "event", "name": "x.y"},
                                    "steps": [{"id": "p", "type": "action", "do": "post_update",
                                               "args": {"text": "- Closed the books."}}]})["id"]
    post(api, f"automations/{aid}/run", {})
    assert sql(api, "SELECT body FROM updates WHERE bot='finance'")[0]["body"] == "- Closed the books."
    assert post(api, "bots/ops/mail-check", {"to": ["ben@acme.example"], "body": "Hi Ben."},
                token="ops-test")["outcome"] == "pass"
    assert resolved == [] and judge.calls == []


# ---- a warning on the copy its reader reads: the question card, the public roadmap, a daily with slides --------------

def question_card(api, task_id):
    """Ana's Needs you card for the question on `task_id`."""
    items = api.get("/api/v2/needs-you", headers=headers("ana-test")).json()["items"]
    return next(it for it in items if it.get("id") == task_id and it["kind"] == "question")


def test_a_warned_question_leads_the_card_new_and_folded(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    # Only the why line breaks the rule: the card shows the question and why, so the line leads the question there.
    asked = ask(api, "Ship it?", why="Our favorite color is blue.", topic="palette")
    q = asked["question"]["refs"]["questions"][0]
    assert q["question"].startswith("Warning:") and q["question"].endswith("Ship it?")
    assert asked["task"]["title"] == "Ship it?"
    card = question_card(api, asked["task"]["id"])
    assert card["ask"]["refs"]["questions"][0]["question"] == q["question"]
    assert card["why"] == "Our favorite color is blue."
    # Asked again on the topic, the folded card's question carries the line too.
    folded = ask(api, "Ship it on Monday?", body="Our favorite color is blue.", topic="palette")
    assert folded["folded"] and folded["rule_warning"]
    q = folded["question"]["refs"]["questions"][0]
    assert q["question"].startswith("Warning:") and q["question"].endswith("Ship it on Monday?")
    card = question_card(api, asked["task"]["id"])
    assert card["ask"]["refs"]["questions"][0]["question"] == q["question"] and card["why"] != q["question"]


def test_a_question_sent_anyway_under_a_new_warn_rule_leads_its_card_with_the_line(ruled):
    api, judge = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    hold = ask(api, "Ship the palette?", title="Palette", body="Our favorite color is red; launch is Tuesday.")
    rule(api, {"kind": "internal", "text": LAUNCH, "who": {"bots": ["ops"]}, "if_broken": "warn"})
    judge.needles[LAUNCH] = "launch is"
    send_anyway(api, hold["held"]["id"])
    [task] = sql(api, "SELECT id FROM tasks WHERE title='Palette'")
    [refs] = [json.loads(r["refs"]) for r in sql(api, "SELECT refs FROM messages WHERE kind='ask' AND "
                                                         "json_extract(refs,'$.task')=?", (task["id"],))]
    assert refs["questions"][0]["question"].startswith("Warning:")
    assert refs["questions"][0]["question"].endswith("Ship the palette?")


def public_output(api, task_id):
    from backend import public_roadmaps
    with api.app.state.store.read() as c:
        return public_roadmaps.output(c, H.task(c, task_id))


def test_a_warned_publish_puts_its_line_on_the_public_summary_the_roadmap_shows(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    post(api, "roadmaps/settings", {"bot_public_approval": False})
    drafted = post(api, "tasks", {"owner": "ops", "title": "Palette", "body": "Draft it.", "visibility": "private",
                                  "public_title": "New palette", "public_summary": "Our favorite color is red."})
    got = post(api, f"tasks/{drafted['id']}", {"version": drafted["version"], "visibility": "public"},
               token="ops-test")
    assert got["rule_warning"]
    shown = public_output(api, drafted["id"])
    assert shown["title"] == "New palette"
    assert shown["summary"].startswith("Warning:") and shown["summary"].endswith("Our favorite color is red.")
    # A new public title on a public task with no summary: the line alone is the summary, the title kept whole.
    bare = post(api, "tasks", {"owner": "ops", "title": "Logo", "body": "Draft it.", "visibility": "public",
                               "public_title": "New logo"})
    got = post(api, f"tasks/{bare['id']}", {"version": bare["version"], "public_title": "Our favorite color is red"},
               token="ops-test")
    assert got["rule_warning"]
    shown = public_output(api, bare["id"])
    assert shown["title"] == "Our favorite color is red" and shown["summary"].startswith("Warning:")


def approved_publish(api, task_id, changes):
    """Ops' publish approval for `changes` to a task, approved by Ana: its id."""
    preview = post(api, f"tasks/{task_id}/publication-preview", changes, token="ops-test")
    approval = post(api, "approvals", {"kind": "publish", "task_id": task_id, "payload": preview["payload"]},
                    token="ops-test")
    post(api, f"approvals/{approval['id']}", {"decision": "approved"})
    return approval["id"]


def test_a_warned_publish_a_person_approved_goes_out_exactly_as_approved(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    drafted = post(api, "tasks", {"owner": "ops", "title": "Palette", "body": "Draft it.", "visibility": "private",
                                  "public_title": "New palette", "public_summary": "Our favorite color is red."})
    approval = approved_publish(api, drafted["id"], {"visibility": "public"})
    got = post(api, f"tasks/{drafted['id']}", {"version": drafted["version"], "visibility": "public",
                                               "approval_id": approval}, token="ops-test")
    assert got["rule_warning"], got
    assert public_output(api, drafted["id"])["summary"] == "Our favorite color is red."
    assert sql(api, "SELECT note FROM tasks WHERE id=?", (drafted["id"],))[0]["note"].startswith("Warning:")
    # A public summary the bot writes itself, approved, is kept exactly too; the line goes on the note.
    task = get(api, f"tasks/{drafted['id']}")
    task = task.get("task", task)
    approval = approved_publish(api, drafted["id"], {"public_summary": "Our favorite color is teal."})
    got = post(api, f"tasks/{drafted['id']}", {"version": task["version"], "approval_id": approval,
                                               "public_summary": "Our favorite color is teal."}, token="ops-test")
    assert got["rule_warning"], got
    assert public_output(api, drafted["id"])["summary"] == "Our favorite color is teal."
    assert sql(api, "SELECT note FROM tasks WHERE id=?", (drafted["id"],))[0]["note"].startswith("Warning:")


def test_publishing_while_clearing_a_summary_judges_only_what_is_published(ruled):
    api, judge = ruled
    color_rule(api)
    post(api, "roadmaps/settings", {"bot_public_approval": False})
    drafted = post(api, "tasks", {"owner": "ops", "title": "Palette", "body": "Draft it.", "visibility": "private",
                                  "public_title": "Safe title", "public_summary": "Our favorite color is red."})
    got = post(api, f"tasks/{drafted['id']}", {"version": drafted["version"], "visibility": "public",
                                               "public_summary": ""}, token="ops-test")
    assert "held" not in got, got
    assert "favorite color" not in judge.calls[-1]["state"]["output"]
    shown = public_output(api, drafted["id"])
    assert shown["title"] == "Safe title" and not shown["summary"]


def test_a_warned_daily_with_slides_carries_its_line_on_the_stored_bullets(ruled):
    api, judge = ruled
    color_rule(api, if_broken="warn")
    weekly = {"goal": "Ship the palette", "done": ["Launched"], "focus": ["Launch"], "blockers": ["None"]}
    got = post(api, "updates", {"kind": "daily", "body": "- Our favorite color is blue.", "slides": weekly},
               token="ops-test")
    assert got["rule_warning"]
    [row] = sql(api, "SELECT kind, body FROM updates WHERE bot='ops'")
    assert row["kind"] == "daily"
    assert row["body"].startswith("Warning:") and row["body"].endswith("- Our favorite color is blue.")
    # The slides a daily drops are not judged.
    assert "Ship the palette" not in judge.calls[-1]["state"]["output"]


def test_a_task_questions_options_and_card_lines_are_judged_with_it(ruled):
    api, judge = ruled
    color_rule(api)
    options = [{"label": "Ship", "description": "Our favorite color is blue.", "then": "Ops ships it today"},
               {"label": "Wait", "then": "Ops waits a week"}]
    got = ask(api, "Ship it?", options=options, if_no_answer={"do": "Ops waits a week", "kind": "reversible"})
    assert got["held"] and set(got) == {"held"}
    said = judge.calls[-1]["state"]["output"]
    assert "Ops ships it today" in said and "Ops waits a week" in said
    assert sql(api, "SELECT id FROM tasks WHERE owner_id='bot:ops'") == []
    [hold] = sql(api, "SELECT payload FROM org_rule_holds WHERE operation='task_question'")
    assert "Our favorite color is blue." in hold["payload"]


def test_a_warned_summary_at_the_cap_publishes_with_its_line(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    post(api, "roadmaps/settings", {"bot_public_approval": False})
    summary = ("Our favorite color is red. " * 400)[:7999] + "Z"
    drafted = post(api, "tasks", {"owner": "ops", "title": "Palette", "body": "Draft it.", "visibility": "private",
                                  "public_title": "New palette", "public_summary": summary})
    got = post(api, f"tasks/{drafted['id']}", {"version": drafted["version"], "visibility": "public"},
               token="ops-test")
    assert got["rule_warning"], got
    shown = public_output(api, drafted["id"])
    assert shown["summary"].startswith("Warning:") and shown["summary"].endswith(summary)


def test_a_new_task_public_by_its_types_default_carries_the_line_on_its_summary(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    post(api, "roadmaps/settings", {"bot_public_approval": False})
    changelog = post(api, "task-types", {"name": "Changelog", "default_visibility": "public",
                                         "steps": [{"name": "Drafted", "status": "open"}]})["type"]
    made = post(api, "tasks", {"owner": "ops", "title": "Palette", "body": "Draft it.", "type": changelog["id"],
                               "public_title": "Our favorite color is blue"}, token="ops-test")
    made = made.get("task", made)
    assert made["visibility"] == "public"
    shown = public_output(api, made["id"])
    assert shown["title"] == "Our favorite color is blue" and shown["summary"].startswith("Warning:")


def test_send_anyway_posts_the_held_days_update_or_nothing(ruled, monkeypatch):
    from backend import updates
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-10-29")             # a Thursday
    thursday = post(api, "updates", {"body": "- Our favorite color is blue."}, token="ops-test")["held"]["id"]
    payload = json.loads(hold_row(api, thursday)["payload"])["replay"]["body"]
    assert payload["day"] == "2026-10-29" and payload["kind"] == "daily"
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-10-30")             # Friday: a week in review's day
    got = send_anyway(api, thursday, expected=409)
    assert got["error"]["code"] == "changed"
    assert hold_row(api, thursday)["state"] == "held" and sql(api, "SELECT id FROM updates WHERE bot='ops'") == []
    # Sent the day it was held, it is that day's daily.
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-10-29")
    send_anyway(api, thursday)
    assert [(r["kind"], r["day"]) for r in sql(api, "SELECT kind, day FROM updates WHERE bot='ops'")] == \
        [("daily", "2026-10-29")]


def upload(api, name, text="# Draft", token="ops-test"):
    r = api.post("/api/v2/files/uploads", json={"name": name, "text": text, "scope": "bot"},
                 headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()["file"]


def test_a_pick_questions_file_titles_and_the_picks_they_make_are_judged(ruled):
    api, judge = ruled
    color_rule(api)
    files = [upload(api, "Our favorite color is blue.md"), upload(api, "Wait.md")]
    got = ask(api, "Which cut should ship?", pick=True, review=[{"file": f["id"]} for f in files])
    assert got["held"] and set(got) == {"held"}
    said = judge.calls[-1]["state"]["output"]
    assert "Option: Our favorite color is blue" in said and "This one is approved" in said
    assert sql(api, "SELECT id FROM tasks WHERE owner_id='bot:ops'") == []
    [hold] = sql(api, "SELECT payload FROM org_rule_holds WHERE operation='task_question'")
    assert "Option: Our favorite color is blue" in hold["payload"]


def test_send_anyway_refuses_a_review_question_whose_files_changed_since_the_check(ruled):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    files = [upload(api, "Cut one.md"), upload(api, "Cut two.md")]
    hold = ask(api, "Which cut should ship?", body="Our favorite color is blue.", pick=True,
               review=[{"file": f["id"]} for f in files])["held"]["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_files SET title='Our favorite color is green' WHERE id=?", (files[0]["id"],))
    assert send_anyway(api, hold, expected=409)["error"]["code"] == "changed"
    assert hold_row(api, hold)["state"] == "held" and sql(api, "SELECT id FROM tasks WHERE owner_id='bot:ops'") == []


def test_a_second_warned_publish_of_a_summary_at_the_cap_keeps_one_line(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    post(api, "roadmaps/settings", {"bot_public_approval": False})
    summary = ("Our favorite color is red. " * 400)[:7999] + "Z"
    drafted = post(api, "tasks", {"owner": "ops", "title": "Palette", "body": "Draft it.", "visibility": "private",
                                  "public_title": "New palette", "public_summary": summary})
    got = post(api, f"tasks/{drafted['id']}", {"version": drafted["version"], "visibility": "public"},
               token="ops-test")
    task = got.get("task", got)
    got = post(api, f"tasks/{drafted['id']}", {"version": task["version"], "public_title": "Our favorite color is blue"},
               token="ops-test")
    assert got["rule_warning"], got
    shown = public_output(api, drafted["id"])
    assert shown["title"] == "Our favorite color is blue"
    assert shown["summary"].count("Warning:") == 1 and shown["summary"].startswith("Warning:")
    assert shown["summary"].endswith("\n\n" + summary)


def test_send_anyway_refuses_a_review_question_once_its_file_has_a_new_version_under_the_same_name(ruled):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    kept, moved = upload(api, "Cut one.md", "# One"), upload(api, "Cut two.md", "# Two")
    held = [ask(api, "Is this cut ready?", body="Our favorite color is blue.", review=[{"file": f["id"]}])["held"]["id"]
            for f in (kept, moved)]
    assert send_anyway(api, held[0])["hold"]["state"] == "sent"
    v2 = upload(api, "Cut two.md", "# Two, reworked")
    assert v2["id"] == moved["id"] and v2["version"] == moved["version"] + 1
    assert send_anyway(api, held[1], expected=409)["error"]["code"] == "changed"
    assert hold_row(api, held[1])["state"] == "held"


def test_send_anyway_refuses_a_held_review_question_with_no_seal_of_its_files(ruled):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    cut = upload(api, "Cut one.md")
    hold = ask(api, "Is this cut ready?", body="Our favorite color is blue.", review=[{"file": cut["id"]}])["held"]["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE org_rule_holds SET payload=json_remove(payload, '$.replay.review') WHERE id=?",
                  (hold,))
    assert send_anyway(api, hold, expected=409)["error"]["code"] == "changed"
    assert hold_row(api, hold)["state"] == "held" and sql(api, "SELECT id FROM tasks WHERE owner_id='bot:ops'") == []


def test_a_review_file_the_check_could_not_read_is_refused_once_it_resolves_in_the_write(ruled):
    api, judge = ruled
    color_rule(api, if_broken="warn")
    cut = upload(api, "Our favorite color is blue.md")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_files SET archived=1 WHERE id=?", (cut["id"],))

    def restored(*args, **kwargs):
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE bot_files SET archived=0 WHERE id=?", (cut["id"],))
        return judge(*args, **kwargs)
    api.app.state.judge = restored
    r = api.post("/api/v2/task-questions", json={"on": "human:ana", "question": "Is this cut ready?",
                                                  "review": [{"file": cut["id"]}]}, headers=headers("ops-test"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "changed", r.text
    [checked] = [call["state"]["output"] for call in judge.calls if "output" in call["state"]]
    assert "Our favorite color is blue" not in checked
    assert sql(api, "SELECT id FROM tasks WHERE owner_id='bot:ops'") == []


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
    move_day_during_check(api, monkeypatch, "2026-10-29", "2026-10-30")          # a Thursday's daily, written Friday
    got = post(api, "updates", {"body": "- Our favorite color is blue."}, token="ops-test")
    assert got["rule_warning"]
    assert [(r["kind"], r["day"]) for r in sql(api, "SELECT kind, day FROM updates WHERE bot='ops'")] == \
        [("daily", "2026-10-30")]


def test_a_live_update_naming_its_day_is_still_refused_once_the_day_has_moved_on(ruled, monkeypatch):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    move_day_during_check(api, monkeypatch, "2026-10-29", "2026-10-30")
    r = api.post("/api/v2/updates", json={"day": "2026-10-29", "body": "- Our favorite color is blue."},
                 headers=headers("ops-test"))
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
    assert [(r["kind"], r["day"]) for r in sql(api, "SELECT kind, day FROM updates WHERE bot='ops'")] == \
        [("daily", "2026-10-30")]


def test_a_warned_automation_update_passes_refusing_writing_checks(ruled, monkeypatch):
    from backend import hubdb
    api, _ = ruled
    color_rule(api, if_broken="warn")
    monkeypatch.setattr(hubdb, "STYLE_LINT", "refuse")
    aid = post(api, "automations", {"name": "Post", "owner": "bot:ops", "trigger": {"type": "event", "name": "x.y"},
                                    "steps": [{"id": "p", "type": "action", "do": "post_update",
                                               "args": {"text": "- Our favorite color is blue."}}]})["id"]
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
