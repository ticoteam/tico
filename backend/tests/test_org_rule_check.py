"""Org rule checks (backend/org_rule_check.py, docs/rules-plan.md §3): a bot's output is checked against the rules that
apply to it before it lands. One Decide call outside the write's transaction; a held write answers 200 with `held`
and writes nothing but its hold; Escalate files one review task for a person; Warn delivers with a line; any
failure lets the output through with `rules.unchecked`, except an output too large to check, which is held.

Acme is the fictional company (backend/tests/test_org_rules.py's org): Ana owns it, Ben is the person above Ops."""
import json
import threading

import pytest

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
        text = state["output"].lower()
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
    return sql(api, "SELECT actor, target, detail_json FROM events WHERE action=? ORDER BY rowid", (action,))


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
    assert [json.loads(e["detail_json"])["hold"] for e in events(api, "rules.blocked")] == [got["held"]["id"]]
    assert "blue" not in json.dumps(events(api, "rules.blocked"))


def test_the_end_of_a_long_output_is_judged_whole(ruled):
    api, judge = ruled
    color_rule(api)
    text = "The weekly numbers look fine. " * 3000 + "By the way, my favorite color is blue."
    assert say(api, text)["held"]
    assert judge.calls[0]["state"]["output"] == text


def test_too_large_to_check_is_held_not_passed(ruled, monkeypatch):
    api, judge = ruled
    color_rule(api, if_broken="warn")
    from clients import judge as J
    monkeypatch.setattr(J, "MAX_STATE_CHARS", 2000)
    got = say(api, "All good here. " * 400)
    assert got["held"]["too_large"] and judge.calls == []
    assert bodies(api, "All good here") == []


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
        c.execute("INSERT INTO bots(slug, display_name, state) VALUES('late-bot', 'Late', 'active')")
        c.execute("INSERT INTO bot_config(bot,config_json,team,operator) VALUES('late-bot','{}','', 'ana')")
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
    owners = {row["id"]: row["owner"] for row in sql(api, "SELECT id, owner FROM tasks WHERE id IN (?,?)", (a["id"], b["id"]))}
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
    for query in ("SELECT final_text AS t FROM attempts WHERE id=?", "SELECT result_json AS t FROM attempts WHERE id=?",
                  "SELECT payload_json AS t FROM attempt_events WHERE attempt_id=?"):
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
    task = sql(api, "SELECT owner, body FROM tasks WHERE id=?", (hold["task_id"],))[0]
    assert task["owner"] == "human:ben" and hold["id"] in task["body"] and "gold" not in task["body"]


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


def test_keep_blocked_and_only_the_reviewer_or_author_reads_a_hold(ruled):
    api, _ = ruled
    color_rule(api, reviewer="ben")
    held = say(api, "Our favorite color is gold")["held"]
    get(api, f"rule-holds/{held['id']}", token="cara-test", expected=404)
    get(api, f"rule-holds/{held['id']}", token="ana-test", expected=404)        # the owner too: not the reviewer
    assert "nonce" not in get(api, f"rule-holds/{held['id']}", token="ops-test")
    # The held text is in no conversation, comment or inbox, and makes no work.
    assert bodies(api, "gold") == [] and sql(api, "SELECT id FROM jobs WHERE bot='ana'") == []
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
        monkeypatch.setattr(K, "DEADLINE", 0.2)
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
    reasons = [json.loads(e["detail_json"])["reason"] for e in events(api, "rules.unchecked")]
    assert reasons == [{"timeout": "timeout", "budget": "budget", "error": "error"}[how]]


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
