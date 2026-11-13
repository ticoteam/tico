"""Listening (backend/listening.py): sources save posts, each pass marks copies, scores against the rules, expires
and sends within the rate limits as one run of the listening's automation."""

import importlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import time_machine

from backend import automations
from backend import listening as L
from backend.store import H
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.automation_api import patch
from backend.tests.helpers.automation_runs import sql
from backend.tests.helpers.listening import NOTE, SEARCH, FakeJudge, item, listening, save, source_id

START = datetime(2026, 11, 2, 7, 30, tzinfo=timezone.utc)  # a Monday, half an hour before the send time
KEEP = {"do": "keep", "tag": "leads", "text": "A team lead who might use the product"}
KEEP_NEWS = {"do": "keep", "tag": "news", "text": "News about a competitor"}
DROP = {"do": "drop", "text": "A vendor selling something"}
WORDS = [f"word{n}" for n in range(200)]


def at(hours=0.0, days=0):
    return START + timedelta(days=days, hours=hours)


def started(api, **extra):
    """A listening made at START, and its one source's id."""
    with time_machine.travel(START, tick=False):
        made = listening(api, **extra)
    return made["id"], source_id(api, made["id"])


def saved(api, source, items, when=START, **extra):
    with time_machine.travel(when, tick=False):
        return save(api, source, items, **extra)


def run(api, when, judge=None):
    store = api.app.state.store
    L.run_pass(store, when, judge)
    store.automations.drain()


def statuses(api, aid):
    rows = sql(api, "SELECT status, count(*) AS n FROM listening_items WHERE automation_id=? GROUP BY status", (aid,))
    return {r["status"]: r["n"] for r in rows}


def runs(api, aid):
    return get(api, f"automations/{aid}/runs")["runs"][::-1]  # oldest first


def story(n):
    return f"Post number {n} about how a team runs its daily standup meeting"


# ---------------------------------------------------------------------------------------------- copies
def test_exact_copies_are_duplicates_and_count_on_their_originals(api):
    aid, source = started(api)
    posts = [item(n, story(n % 150)) for n in range(200)]  # 150 texts, 50 of them said twice
    assert saved(api, source, posts)["saved"] == 200
    run(api, at(0.1))
    assert statuses(api, aid)["duplicate"] == 50
    copies = sql(
        api, "SELECT sum(copies) AS n FROM listening_items WHERE automation_id=? AND status<>'duplicate'", (aid,)
    )
    assert copies[0]["n"] == 50
    assert sql(api, "SELECT count(*) AS n FROM listening_items WHERE duplicate_of_id IS NOT NULL")[0]["n"] == 50


def test_posts_96_percent_alike_are_copies_and_80_percent_alike_are_not(api):
    aid, source = started(api)
    base = " ".join(WORDS[:100])
    close = " ".join(WORDS[:98] + WORDS[100:102])  # 98 shared of 102 in all
    far = " ".join(WORDS[:89] + WORDS[110:121])  # 89 shared of 111 in all
    saved(api, source, [item(1, base), item(2, close), item(3, far)])
    run(api, at(0.1))
    rows = {r["native_id"]: r for r in sql(api, "SELECT * FROM listening_items WHERE automation_id=?", (aid,))}
    assert rows["2"]["status"] == "duplicate" and rows["2"]["duplicate_of_id"] == rows["1"]["id"]
    assert rows["1"]["copies"] == 1 and rows["1"]["status"] == "kept" and rows["3"]["status"] == "kept"


# ---------------------------------------------------------------------------------------------- rules
def test_keep_and_drop_rules_tag_keep_and_drop(api):
    aid, source = started(api, rules=[KEEP, KEEP_NEWS, DROP])
    saved(
        api,
        source,
        [
            item(1, "A lead asks about standups [r0]"),
            item(2, "Both, news more [r0:0.8] [r1:0.95]"),
            item(3, "A lead, but a vendor [r0] [r2]"),
            item(4, "Neither"),
        ],
    )
    run(api, at(0.1), FakeJudge())
    rows = {r["native_id"]: (r["status"], r["tag"]) for r in sql(api, "SELECT * FROM listening_items")}
    assert rows == {"1": ("kept", "leads"), "2": ("kept", "news"), "3": ("dropped", ""), "4": ("dropped", "")}


def test_with_no_keep_rules_everything_not_dropped_is_kept(api):
    aid, source = started(api, rules=[DROP])
    saved(api, source, [item(1, "A vendor pitch [r0]"), item(2, "Anything else at all")])
    run(api, at(0.1), FakeJudge())
    rows = {r["native_id"]: r["status"] for r in sql(api, "SELECT * FROM listening_items")}
    assert rows == {"1": "dropped", "2": "kept"}


# ---------------------------------------------------------------------------------------------- save
def test_saving_the_same_body_twice_adds_no_rows(api):
    aid, source = started(api)
    posts = [item(1, story(1)), item(2, story(2))]
    assert save(api, source, posts, key="k1")["saved"] == 2
    assert save(api, source, posts, key="k1")["saved"] == 2  # the same key: the first answer again
    assert save(api, source, posts)["saved"] == 0
    assert statuses(api, aid) == {"new": 2}


def test_only_a_bot_or_whoever_may_change_the_listening_saves(api):
    _, source = started(api)
    r = api.post(f"/api/v2/listening/sources/{source}/items", json={"items": []}, headers=headers("cara-test"))
    assert r.status_code == 403
    save(api, "nope", [], expected=404)
    save(api, source, [], status="asleep", expected=422)


# ---------------------------------------------------------------------------------------------- sending
def test_three_an_hour_ten_a_day_sends_3_3_3_1_then_waits_for_the_next_day(api):
    limits = [{"count": 3, "per": "hour"}, {"count": 10, "per": "day"}]
    aid, source = started(api, rules=[KEEP], limits=limits)
    saved(api, source, [item(n, f"{story(n)} [r0:{0.76 + n * 0.005:.3f}]") for n in range(40)])
    judge = FakeJudge()
    run(api, at(0.1), judge)
    assert statuses(api, aid) == {"kept": 40} and runs(api, aid) == []
    for hour in range(1, 8):
        run(api, at(hour - 0.4), judge)  # just after each hour's send time
    done = runs(api, aid)
    assert [r["listening"]["count"] for r in done] == [3, 3, 3, 1]
    assert [r["listening"]["waiting"] for r in done] == [37, 34, 31, 30]
    assert done[0]["summary"] == "3 sent" and done[0]["status"] == "ok"
    assert done[0]["listening"]["counts"]["sent"] == 3 and done[0]["listening"]["counts"]["kept"] == 40
    order = [done[0]["listening"]["text"].index(story(n)) for n in (39, 38, 37)]
    assert order == sorted(order)  # the best first
    first = get(api, f"listening/items?automation={aid}&run={done[0]['id']}")["items"]
    assert sorted(i["native_id"] for i in first) == ["37", "38", "39"]
    run(api, at(0.6, days=1), judge)
    assert [r["listening"]["count"] for r in runs(api, aid)] == [3, 3, 3, 1, 3]


def test_five_a_day_sends_once_at_its_time_and_once_after_the_server_was_down(api):
    aid, source = started(api)
    saved(api, source, [item(n, story(n)) for n in range(12)])
    run(api, at(0.4))  # before 08:00
    assert runs(api, aid) == []
    run(api, at(1.5, days=2))  # two send times went by
    run(api, at(1.6, days=2))
    done = runs(api, aid)
    assert [r["listening"]["count"] for r in done] == [5]
    assert get(api, f"automations/{aid}")["listening"]["waiting"] == 7


def test_nothing_kept_queues_no_run(api):
    aid, source = started(api, rules=[KEEP])
    saved(api, source, [item(1, "Nothing a rule keeps")])
    run(api, at(1), FakeJudge())
    assert statuses(api, aid) == {"dropped": 1} and runs(api, aid) == []


# ---------------------------------------------------------------------------------------------- the decision model
def test_a_failing_model_is_retried_and_the_post_fails_after_three_tries(api):
    aid, source = started(api, rules=[KEEP])
    saved(api, source, [item(1, "A lead [r0]")])
    down = FakeJudge(fail=True)
    run(api, at(0.1), down)
    run(api, at(0.2), down)
    assert statuses(api, aid) == {"new": 1}
    run(api, at(0.3), down)
    assert statuses(api, aid) == {"failed": 1} and len(down.calls) == 3


def test_with_decisions_off_posts_stay_new_show_hollow_and_expire_in_a_week(api, monkeypatch):
    from backend import judge

    monkeypatch.setattr(judge, "server_engine", lambda *a, **k: None)
    aid, source = started(api, rules=[KEEP])
    saved(api, source, [item(1, "A lead [r0]")])
    run(api, at(1))
    assert statuses(api, aid) == {"new": 1}
    with time_machine.travel(at(1), tick=False):
        assert get(api, f"automations/{aid}")["listening"]["dot"] == "hollow"
    run(api, at(1, days=8))
    assert statuses(api, aid) == {"expired": 1}


def test_a_pass_out_of_time_releases_its_claims_still_sends_and_the_next_pass_scores_them(api, monkeypatch):
    clock = {"now": 0.0, "step": 6.0}
    monkeypatch.setattr(L, "CLOCK", lambda: clock["now"])
    monkeypatch.setattr(L, "GROUP", 1)  # one post per call, as a listening kept off grouped calls asks
    judge = FakeJudge()

    def slow(state, questions, label=None):
        clock["now"] += clock["step"]
        return judge(state, questions, label)

    busy, source = started(api, rules=[KEEP])
    other, other_source = started(api, rules=[KEEP])
    saved(api, source, [item(n, f"{story(n)} [r0]") for n in range(10)], when=at(0.2))
    saved(api, other_source, [item(1, f"{story(1)} [r0]")], when=at(0.1))  # waiting longest, so it goes first
    run(api, at(0.6), slow)  # after the send time; the fourth call uses up the 20 seconds
    assert statuses(api, other) == {"sent": 1}
    assert statuses(api, busy) == {"sent": 3, "new": 7} and len(judge.calls) == 4
    assert sql(api, "SELECT count(*) AS n FROM listening_items WHERE claimed_at IS NOT NULL")[0]["n"] == 0
    assert [r["listening"]["count"] for r in runs(api, busy)] == [3]
    clock["step"] = 0.0
    run(api, at(0.7), slow)
    assert statuses(api, busy) == {"sent": 3, "kept": 7} and len(judge.calls) == 11


def test_the_newest_posts_are_scored_first(api, monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(L, "CLOCK", lambda: clock["now"])
    judge = FakeJudge()

    def slow(state, questions, label=None):
        clock["now"] += 6.0
        return judge(state, questions, label)

    aid, source = started(api, rules=[KEEP])
    saved(api, source, [item(n, f"{story(n)} [r0]") for n in range(25)])
    monkeypatch.setattr(L, "GROUP", 3)
    run(api, at(0.1), slow)  # the fourth call uses up the 20 seconds
    assert [[text.split()[2] for text in call] for call in judge.calls] == [
        ["24", "23", "22"],
        ["21", "20", "19"],
        ["18", "17", "16"],
        ["15", "14", "13"],
    ]


def test_copies_of_a_copy_count_on_the_one_original(api):
    aid, source = started(api)
    base = " ".join(WORDS[:60])
    saved(api, source, [item(n, f"{base} ending{n}") for n in (1, 2, 3)])  # near copies, none word for word
    run(api, at(0.1))
    rows = {r["native_id"]: r for r in sql(api, "SELECT * FROM listening_items WHERE automation_id=?", (aid,))}
    assert [rows[n]["status"] for n in "123"] == ["kept", "duplicate", "duplicate"]
    assert rows["2"]["duplicate_of_id"] == rows["3"]["duplicate_of_id"] == rows["1"]["id"]
    assert [rows[n]["copies"] for n in "123"] == [2, 0, 0]


def test_long_posts_that_only_open_alike_are_not_copies(api):
    aid, source = started(api)
    opening = " ".join(f"open{n}" for n in range(300))  # longer than what the first comparison reads
    one = opening + " " + " ".join(f"alpha{n}" for n in range(400))
    other = opening + " " + " ".join(f"bravo{n}" for n in range(400))
    saved(api, source, [item(1, one), item(2, other), item(3, one + " and one more thing")])
    run(api, at(0.1))
    rows = {r["native_id"]: r for r in sql(api, "SELECT * FROM listening_items WHERE automation_id=?", (aid,))}
    assert [rows[n]["status"] for n in "123"] == ["kept", "kept", "duplicate"]
    assert rows["3"]["duplicate_of_id"] == rows["1"]["id"]


def test_the_same_post_with_another_link_is_a_copy(api):
    aid, source = started(api)
    said = "Our team finally dropped the daily standup and nobody misses it"
    links = ("https://acme.example/a/very/long/path?utm_source=one&ref=abcdef", "www.acme.example/other/long/path/2")
    saved(api, source, [item(n, f"{said} {link} today") for n, link in enumerate(links)])
    run(api, at(0.1))
    assert statuses(api, aid) == {"kept": 1, "duplicate": 1}


def test_scoring_stops_when_the_pass_itself_reaches_the_spend_cap(api, monkeypatch):
    """Each post of a call carries its share of the call's cost, so the cap counts what the calls cost."""
    monkeypatch.setattr(L, "GROUP", 2)
    judge = FakeJudge()

    def paid(state, questions, label=None):
        return {**judge(state, questions, label), "usage": {"cost_usd": 0.02}}

    aid, source = started(api, rules=[KEEP], spend_cap={"per_30m": 0.05})
    saved(api, source, [item(n, f"{story(n)} [r0]") for n in range(10)])
    run(api, at(0.1), paid)
    assert len(judge.calls) == 3 and statuses(api, aid) == {"kept": 6, "new": 4}
    assert sql(api, "SELECT round(sum(cost), 6) AS c FROM listening_items")[0]["c"] == 0.06
    assert sql(api, "SELECT count(*) AS n FROM listening_items WHERE claimed_at IS NOT NULL")[0]["n"] == 0


def test_a_listening_turned_off_while_a_call_runs_scores_no_more_and_keeps_no_result(api):
    judge = FakeJudge()
    aid, source = started(api, rules=[KEEP])

    def off(state, questions, label=None):
        patch(api, aid, {"enabled": False})
        return judge(state, questions, label)

    saved(api, source, [item(n, f"{story(n)} [r0]") for n in range(5)])
    run(api, at(0.1), off)
    assert len(judge.calls) == 1 and statuses(api, aid) == {"new": 5}
    assert sql(api, "SELECT count(*) AS n FROM listening_items WHERE claimed_at IS NOT NULL")[0]["n"] == 0


def test_posts_waiting_on_a_listening_that_is_off_still_expire(api):
    aid, source = started(api)
    saved(api, source, [item(1, story(1))])
    patch(api, aid, {"enabled": False})
    run(api, at(1, days=8))
    assert statuses(api, aid) == {"expired": 1}


def test_two_passes_at_once_score_no_post_twice(api, monkeypatch):
    monkeypatch.setattr(L, "GROUP", L.DG.MOST)
    aid, source = started(api, rules=[KEEP])
    saved(api, source, [item(n, f"{story(n)} [r0]") for n in range(5)])
    store = api.app.state.store
    judge = FakeJudge()
    answer = FakeJudge()

    def racing(state, questions, label=None):
        if not judge.calls:  # a second pass starts while the first is asking about its first posts
            judge.calls.append(state)
            L.run_pass(store, at(0.1), answer)
        return answer(state, questions, label)

    L.run_pass(store, at(0.1), racing)
    assert sorted(answer.posts) == sorted(f"{story(n)} [r0]" for n in range(5)) and len(answer.calls) == 1
    assert statuses(api, aid) == {"kept": 5}


# ---------------------------------------------------------------------------------------------- sources
def test_a_blocked_read_shows_on_the_source_the_list_and_tools(api):
    aid, source = started(api)
    out = saved(api, source, [], status="blocked", error="Sign in again")
    assert out["source"]["status"] == "blocked"
    listed = get(api, f"listening/sources?automation={aid}")["sources"]
    assert [(s["key"], s["status"], s["last_error"]) for s in listed] == [("s1", "blocked", "Sign in again")]
    rows = get(api, "automations?trigger=listening")["automations"]
    assert next(r for r in rows if r["id"] == aid)["listening"]["dot"] == "hollow"
    with api.app.state.store.read() as c:
        assert L.worst(c) == "blocked"
    saved(api, source, [])
    with time_machine.travel(START, tick=False):
        assert get(api, f"automations/{aid}")["listening"]["dot"] == "filled"


def test_due_sources_are_the_search_sources_not_read_in_the_last_day(api):
    page = {"id": "p1", "kind": "page", "site": "", "target": "https://acme.example/pricing"}
    aid, source = started(api, sources=[SEARCH, page])
    with time_machine.travel(START, tick=False):
        due = get(api, f"listening/sources?due=1&automation={aid}")["sources"]
        assert [(s["id"], s["kind"], s["target"]) for s in due] == [(source, "search", "standup tools")]
        save(api, source, [])
        assert get(api, f"listening/sources?due=1&automation={aid}")["sources"] == []


def test_a_listening_that_is_off_saves_scores_and_sends_nothing(api):
    aid, source = started(api)
    saved(api, source, [item(1, story(1))])
    patch(api, aid, {"enabled": False})
    save(api, source, [item(2, story(2))], expected=409)
    run(api, at(1))
    assert statuses(api, aid) == {"new": 1} and runs(api, aid) == []


def test_a_source_taken_out_of_the_trigger_stops_taking_posts(api):
    aid, source = started(api)
    trigger = get(api, f"automations/{aid}")["trigger"]
    patch(api, aid, {"trigger": {**trigger, "sources": []}})
    save(api, source, [item(1, story(1))], expected=404)


# ---------------------------------------------------------------------------------------------- push
def test_a_push_address_takes_posts_without_sign_in_and_shows_its_token_once(api):
    with time_machine.travel(START, tick=False):
        made = listening(api, sources=[{"id": "in", "kind": "push", "site": "syften"}])
    aid = made["id"]
    address = made["push_addresses"]["in"]
    path = address[address.index(L.PUSH_PATH) :]
    r = api.post(path, json={"items": [item(1, story(1))]})
    assert r.status_code == 200 and r.json() == {"seen": 1, "saved": 1}
    assert api.post(L.PUSH_PATH + "wrong", json={"items": []}).status_code == 404
    token = path.rsplit("/", 1)[1]
    assert token not in str(get(api, f"automations/{aid}")) and token not in str(
        sql(api, "SELECT * FROM listening_sources")
    )
    assert "push_addresses" not in patch(api, aid, {"name": "Standups again"})
    again = post(api, f"listening/sources/{source_id(api, aid, 'in')}/address", {})["address"]
    assert again != address and api.post(path, json={"items": []}).status_code == 404
    assert api.post(again[again.index(L.PUSH_PATH) :], json={"items": []}).status_code == 200


def test_a_replayed_save_answers_without_the_push_address(api):
    body = {"id": "in", "kind": "push", "site": "syften"}
    with time_machine.travel(START, tick=False):
        made = listening(api, sources=[body], key="make-1")
        again = listening(api, sources=[body], key="make-1")
    assert made["push_addresses"]["in"] and again["id"] == made["id"] and "push_addresses" not in again
    more = {"trigger": {**made["trigger"], "sources": [body, {**body, "id": "two"}]}}
    first = api.patch(f"/api/v2/automations/{made['id']}", json=more, headers=headers("ana-test", "edit-1")).json()
    replay = api.patch(f"/api/v2/automations/{made['id']}", json=more, headers=headers("ana-test", "edit-1")).json()
    assert first["push_addresses"]["two"] and "push_addresses" not in replay
    route = f"listening/sources/{source_id(api, made['id'], 'in')}/address"
    new, same = post(api, route, {}, key="addr-1"), post(api, route, {}, key="addr-1")
    assert L.PUSH_PATH in new["address"] and "address" not in same and same["source"] == "in"
    assert L.PUSH_PATH not in str(sql(api, "SELECT response FROM idempotency"))


# ---------------------------------------------------------------------------------------------- paths
def path(tag):
    return {
        "name": tag,
        "when": [{"field": "item.tag", "op": "is", "value": tag}],
        "steps": [
            {"id": "say_" + tag.lower(), "type": "action", "do": "note", "args": {"text": tag + ": {{item.count}}"}}
        ],
    }


def notes(run_row):
    return sorted(s["outputs"]["note"] for s in run_row["steps"] if s["type"] == "action")


def test_paths_by_tag_run_one_thread_per_tag_and_the_rest_on_otherwise(api):
    other = [{"id": "say_rest", "type": "action", "do": "note", "args": {"text": "rest: {{item.tag}}"}}]
    steps = [{"id": "split", "type": "paths", "by": "tag", "paths": [path("leads")], "otherwise": {"steps": other}}]
    aid, source = started(api, rules=[KEEP, KEEP_NEWS], steps=steps)
    saved(api, source, [item(1, "One [r0]"), item(2, "Two [r0]"), item(3, "Three [r1]")])
    run(api, at(1), FakeJudge())
    done = runs(api, aid)
    assert len(done) == 1 and done[0]["status"] == "ok"
    assert notes(done[0]) == ["leads: 2", "rest: news"]


def test_run_now_with_nothing_sent_ends_with_no_thread(api):
    for by in ("tag", "item"):
        steps = [{"id": "split", "type": "paths", "by": by, "paths": [path("leads")], "otherwise": {"steps": []}}]
        aid, _ = started(api, rules=[KEEP], steps=steps)
        post(api, f"automations/{aid}/run", {})
        api.app.state.store.automations.drain()
        done = runs(api, aid)
        assert len(done) == 1 and done[0]["status"] == "empty" and notes(done[0]) == []


def test_paths_by_item_run_one_thread_per_item(api):
    each = {
        "name": "All",
        "when": [],
        "steps": [{"id": "say", "type": "action", "do": "note", "args": {"text": "{{item.title}}"}}],
    }
    steps = [{"id": "split", "type": "paths", "by": "item", "paths": [each]}]
    aid, source = started(api, steps=steps)
    saved(api, source, [item(1, "One", title="First"), item(2, "Two", title="Second")])
    run(api, at(1))
    assert notes(runs(api, aid)[0]) == ["First", "Second"]


def test_paths_by_tag_are_refused_outside_a_listening(api):
    steps = [{"id": "split", "type": "paths", "by": "tag", "paths": [path("leads")]}]
    body = {"name": "T", "owner": "person:ana", "trigger": {"type": "schedule", "cron": "0 9 * * *", "tz": "UTC"}}
    post(api, "automations", {**body, "steps": steps}, expected=422)


def test_a_run_whose_only_bot_turn_hit_the_spend_cap_fails(api):
    turn = {"id": "look", "type": "bot_turn", "bot": "ops", "prompt": "{{listening.text}}"}
    aid, source = started(api, steps=[turn], spend_cap={"per_30m": 0.2})
    saved(api, source, [item(1, story(1))])
    with api.app.state.store.transaction() as c:  # a turn still going holds more than the cap
        c.execute(
            "INSERT INTO automation_holds(ref,automation_id,run_id,amount,created_at) VALUES('t_old',?,'run_old',0.25,?)",
            (aid, H.now()),
        )
    run(api, at(1))
    done = runs(api, aid)
    assert [s["status"] for s in done[0]["steps"]] == ["spend_cap"] and done[0]["status"] == "failed"


# ---------------------------------------------------------------------------------------------- the trigger
def test_the_trigger_is_checked_on_save(api):
    def refused(**trigger):
        base = {
            "type": "listening",
            "sources": [SEARCH],
            "rules": [],
            "limits": [{"count": 5, "per": "day"}],
            "at": "08:00",
            "tz": "UTC",
        }
        body = {"name": "T", "owner": "person:ana", "trigger": {**base, **trigger}, "steps": [NOTE]}
        post(api, "automations", body, expected=422)

    refused(limits=[])
    refused(limits=[{"count": 5, "per": "day"}, {"count": 9, "per": "day"}])
    refused(limits=[{"count": 5, "per": "year"}])
    refused(sources=[{"id": "p", "kind": "page", "site": "", "target": "not a link"}])
    refused(sources=[SEARCH, SEARCH])
    refused(rules=[{"do": "keep", "text": "No tag"}])
    refused(at="8am")


def test_a_listening_reads_back_with_its_sources_and_next_send(api):
    aid, source = started(api)
    with time_machine.travel(START, tick=False):
        seen = get(api, f"automations/{aid}")
    assert seen["trigger"]["sources"] == [SEARCH]
    heard = seen["listening"]
    assert heard["next_send_at"].startswith("2026-11-02T08:00") and heard["waiting"] == 0
    assert [(s["id"], s["key"], s["status"]) for s in heard["sources"]] == [(source, "s1", "never")]
    assert aid not in {a["id"] for a in get(api, "automations/stats?range=7d")["automations"]}


# ---------------------------------------------------------------------------------------------- upkeep
def test_a_kept_post_unsent_for_a_week_expires_and_maintenance_deletes_old_rows(api):
    aid, source = started(api, limits=[{"count": 1, "per": "week"}])
    saved(api, source, [item(1, story(1), posted_at="2026-11-02T07:00:00Z"), item(2, story(2))])
    run(api, at(1))
    assert statuses(api, aid) == {"sent": 1, "kept": 1}
    run(api, at(1, days=7))  # the week's one is not yet allowed again when the other turns a week old
    assert statuses(api, aid) == {"sent": 1, "expired": 1}
    with api.app.state.store.transaction() as c:
        L.prune(c, H.shift(H.now(), days=L.KEEP_DAYS + 1))
    assert statuses(api, aid) == {"sent": 1}
    with api.app.state.store.transaction() as c:
        L.prune(c, H.shift(H.now(), days=L.KEEP_SENT_DAYS + 1))
    assert statuses(api, aid) == {}


def test_a_new_company_has_one_market_listening_and_deleting_it_keeps_it_gone(api):
    def named():
        rows = get(api, "automations?trigger=listening")["automations"]
        return [r for r in rows if r["name"] == automations.LISTENING_NAME]

    automations.tick(api.app.state.store)
    made = named()
    assert len(made) == 1 and not made[0].get("builtin")
    r = api.delete(f"/api/v2/automations/{made[0]['id']}", headers=headers())
    assert r.status_code == 200, r.text
    with api.app.state.store.transaction() as c:
        automations.ensure_builtins(c)
    assert named() == []


# ---------------------------------------------------------------------------------------------- the old model is gone
def test_the_old_routes_and_tools_are_gone(api):
    for route in ("listening/runs", "listening/items/x", "intake", "listening/destinations"):
        get(api, route, expected=404)
    for route in ("listening/runs", "listening/decide", "listening/judge", "listening/judgments", "intake/x/resolve"):
        assert api.post("/api/v2/" + route, json={}, headers=headers()).status_code in (404, 405)
    from clients import hubtools

    names = {t["name"] for t in hubtools.TOOLS if "listening" in t["name"]}
    assert names == {"tico_listening_sources", "tico_listening_save"}


def test_the_migration_drops_the_old_tables_and_the_automations_on_the_old_model():
    import json

    up = importlib.import_module("backend.migrations.20261111190425_listening_feed").up
    conn = sqlite3.connect(":memory:")
    old = ("intake_items", "listen_judgments", "listen_items", "listen_runs", "listening_destinations")
    for table in old:
        conn.execute(f"CREATE TABLE {table} (id TEXT)")
    conn.execute("CREATE TABLE automations (id TEXT, trigger_type TEXT, flow TEXT, enabled INTEGER)")
    conn.execute("CREATE TABLE automation_versions (automation_id TEXT, n INTEGER, flow TEXT)")
    for table in ("automation_runs", "automation_daily", "automation_holds"):
        conn.execute(f"CREATE TABLE {table} (automation_id TEXT)")
    find = {"id": "f", "type": "find", "source": "listening"}
    inner = {"id": "p", "type": "paths", "paths": [{"name": "b", "steps": [find]}], "otherwise": {"steps": [find]}}
    fork = {"id": "k", "type": "paths", "paths": [{"name": "a", "steps": [inner, NOTE]}], "otherwise": {"steps": []}}
    flows = {
        "old": ("listening", {"trigger": {"type": "listening", "destination": "leads"}, "steps": [NOTE]}),
        "finds": ("schedule", {"trigger": {"type": "schedule"}, "steps": [find, NOTE], "enabled": True}),
        "plain": ("schedule", {"trigger": {"type": "schedule"}, "steps": [NOTE], "enabled": True}),
        "nested": ("schedule", {"trigger": {"type": "schedule"}, "steps": [NOTE, fork], "enabled": True}),
    }
    for aid, (kind, flow) in flows.items():
        conn.execute("INSERT INTO automations VALUES (?,?,?,1)", (aid, kind, json.dumps(flow)))
    conn.execute("INSERT INTO automation_runs VALUES ('old')")
    up(conn)
    up(conn)  # again changes nothing
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not tables & set(old) and {"listening_sources", "listening_items"} <= tables
    rows = {r[0]: (json.loads(r[1]), r[2]) for r in conn.execute("SELECT id, flow, enabled FROM automations")}
    assert (
        set(rows) == {"finds", "plain", "nested"}
        and conn.execute("SELECT count(*) FROM automation_runs").fetchone()[0] == 0
    )
    assert rows["finds"][0]["steps"] == [NOTE] and rows["finds"][0]["enabled"] is False and rows["finds"][1] == 0
    assert rows["plain"] == (flows["plain"][1], 1)
    assert '"find"' not in json.dumps(rows["nested"][0]) and rows["nested"][1] == 0
    assert rows["nested"][0]["steps"][1]["paths"][0]["steps"][1] == NOTE


def test_a_migrated_automation_runs_without_its_listening_find_when_turned_back_on(api):
    import json

    up = importlib.import_module("backend.migrations.20261111190425_listening_feed").up
    say = {"id": "say", "type": "action", "do": "note", "args": {"text": "hello"}}
    body = {
        "name": "Daily",
        "owner": "person:ana",
        "trigger": {"type": "schedule", "cron": "0 9 * * *"},
        "steps": [say],
    }
    aid = post(api, "automations", body)["id"]
    find = {"id": "f", "type": "find", "source": "listening"}
    with api.app.state.store.transaction() as c:  # as the old server saved it: the step in the flow and its version
        for table, where in (("automations", "id"), ("automation_versions", "automation_id")):
            for row in c.execute(f"SELECT rowid AS r, flow FROM {table} WHERE {where}=?", (aid,)).fetchall():
                flow = json.loads(row["flow"])
                flow["steps"] = [find, *flow["steps"]]
                c.execute(f"UPDATE {table} SET flow=? WHERE rowid=?", (json.dumps(flow), row["r"]))
        up(c)
    assert '"find"' not in str(sql(api, "SELECT flow FROM automation_versions WHERE automation_id=?", (aid,)))
    assert get(api, f"automations/{aid}")["enabled"] is False
    patch(api, aid, {"enabled": True})
    post(api, f"automations/{aid}/run", {})
    api.app.state.store.automations.drain()
    done = runs(api, aid)
    assert len(done) == 1 and done[0]["status"] == "ok" and notes(done[0]) == ["hello"]


def test_ten_small_posts_with_three_rules_take_one_call_and_each_is_decided_as_alone(api, monkeypatch):
    """AC-6: grouped posts get the verdicts they would get one at a time."""
    marks = ["[r0]", "[r1]", "[r2]", "[r0] [r2]", "", "[r1:0.8]", "[r0:0.5]", "[r1] [r2:0.6]", "[r0] [r1]", "[r2]"]
    posts = [item(n, f"{story(n)} {mark}") for n, mark in enumerate(marks)]

    def decided(group):
        aid, source = started(api, rules=[KEEP, KEEP_NEWS, DROP])
        saved(api, source, posts)
        monkeypatch.setattr(L, "GROUP", group)
        judge = FakeJudge()
        run(api, at(0.1), judge)
        rows = sql(api, "SELECT native_id, status, tag FROM listening_items WHERE automation_id=?", (aid,))
        return judge, {r["native_id"]: (r["status"], r["tag"]) for r in rows}

    alone, one_by_one = decided(1)
    grouped, together = decided(L.DG.MOST)
    assert len(alone.calls) == 10 and len(grouped.calls) <= 2
    assert together == one_by_one and len(together) == 10
    calls = sql(api, "SELECT detail FROM events WHERE action='judge.call' ORDER BY rowid DESC LIMIT 1")
    assert '"items": 10' in calls[0]["detail"]


# ---------------------------------------------------------------------------------------------- delivery
# An item counts as sent only once a step delivered it; what a stopped or failed step missed goes back to waiting
# (backend/automation_engine.py `delivery`, backend/listening.py `settle`).
TURN = {"id": "look", "type": "bot_turn", "bot": "ops", "prompt": "{{listening.text}}"}
CAP = {"per_30m": 0.2}


def cap_full(api, aid):
    """A turn still going holds more than the cap: the next turn is stopped."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO automation_holds(ref,automation_id,run_id,amount,created_at) VALUES('t_old',?,'run_old',0.25,?)",
            (aid, H.now()),
        )


def cap_free(api):
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM automation_holds WHERE ref='t_old'")


def words(n):
    return " ".join(WORDS[n * 10 : n * 10 + 10])


def by_status(api, aid):
    rows = sql(api, "SELECT native_id, status FROM listening_items WHERE automation_id=?", (aid,))
    return {r["native_id"]: r["status"] for r in rows}


def room(api, aid, when):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT flow FROM automations WHERE id=?", (aid,)).fetchone()
        return L.allowance(c, aid, json.loads(row["flow"])["trigger"], when)


def test_a_send_the_spend_cap_stopped_fails_and_its_items_wait_without_using_the_limit(api):
    aid, source = started(api, steps=[TURN], spend_cap=CAP, limits=[{"count": 10, "per": "day"}])
    saved(api, source, [item(n, story(n)) for n in range(10)])
    cap_full(api, aid)
    run(api, at(1))
    first = runs(api, aid)
    assert first[0]["status"] == "failed" and [s["status"] for s in first[0]["steps"]] == ["spend_cap"]
    assert first[0]["summary"] == "Nothing delivered: spend cap · 10 back to waiting"
    assert first[0]["listening"]["counts"]["sent"] == 0 and first[0]["listening"]["counts"]["back"] == 10
    assert first[0]["steps"][0]["missed"] == 10
    assert statuses(api, aid) == {"kept": 10}
    assert room(api, aid, at(1)) == 10
    assert get(api, f"automations/{aid}")["listening"]["waiting"] == 10
    # The next send, with room in the cap, sends the same ten in the same order, each to the bot once.
    cap_free(api)
    run(api, at(1, days=1))
    second = runs(api, aid)
    assert second[1]["status"] == "ok" and second[1]["summary"] == "10 sent"
    lines = [line for line in first[0]["listening"]["text"].split("\n")[2:]]
    assert second[1]["listening"]["text"].split("\n")[2:] == lines
    assert [s["status"] for s in second[1]["steps"]] == ["ok"]
    assert statuses(api, aid) == {"sent": 10}


def test_paths_where_the_cap_stops_one_tag_deliver_the_other_and_read_partly_delivered(api):
    turn_path = {**path("leads"), "steps": [TURN]}
    steps = [{"id": "split", "type": "paths", "by": "tag", "paths": [turn_path, path("news")]}]
    aid, source = started(
        api, rules=[KEEP, KEEP_NEWS], steps=steps, spend_cap=CAP, limits=[{"count": 10, "per": "day"}]
    )
    leads = [item(n, f"{words(n)} [r0]") for n in range(4)]
    news = [item(n, f"{words(n)} [r1]") for n in range(4, 10)]
    saved(api, source, leads + news)
    with api.app.state.store.transaction() as c:  # scored as the rules say, without spending toward the cap
        for n in range(10):
            tag = "leads" if n < 4 else "news"
            c.execute(
                "UPDATE listening_items SET status='kept',tag=?,score=0.9,scored_at=? WHERE automation_id=? "
                "AND native_id=?",
                (tag, H.now(), aid, str(n)),
            )
    cap_full(api, aid)
    run(api, at(1), FakeJudge())
    done = runs(api, aid)[0]
    assert done["status"] == "partial"
    assert done["summary"] == "Partly delivered · 4 of 10 not delivered (spend cap)"
    assert done["listening"]["counts"]["sent"] == 6 and done["listening"]["counts"]["back"] == 4
    looked = [s for s in done["steps"] if s["id"] == "look"]
    assert [(s["status"], s["missed"]) for s in looked] == [("spend_cap", 4)]
    assert by_status(api, aid) == {**{str(n): "kept" for n in range(4)}, **{str(n): "sent" for n in range(4, 10)}}
    sent = get(api, f"listening/items?automation={aid}&run={done['id']}")["items"]
    assert sorted(i["native_id"] for i in sent) == [str(n) for n in range(4, 10)]
    # The next send takes the four, they reach the bot turn, and nothing is noted again for the news path.
    cap_free(api)
    run(api, at(1, days=1), FakeJudge())
    again = runs(api, aid)[1]
    assert again["status"] == "ok" and again["listening"]["count"] == 4
    assert [(s["id"], s["status"]) for s in again["steps"] if s["type"] != "paths"] == [("look", "ok")]
    assert statuses(api, aid) == {"sent": 10}


def test_a_step_that_delivered_items_before_gets_only_the_new_ones_when_they_come_back(api):
    note = {"id": "tell", "type": "action", "do": "note", "args": {"text": "{{listening.count}}: {{listening.text}}"}}
    aid, source = started(api, steps=[note, TURN], spend_cap=CAP, limits=[{"count": 10, "per": "day"}])
    saved(api, source, [item(n, story(n)) for n in range(3)])
    cap_full(api, aid)
    run(api, at(1))
    first = runs(api, aid)[0]
    assert first["status"] == "failed" and statuses(api, aid) == {"kept": 3}
    cap_free(api)
    saved(api, source, [item(n, story(n)) for n in range(3, 5)], when=at(2))
    run(api, at(1, days=1))
    second = runs(api, aid)[1]
    assert second["status"] == "ok" and second["listening"]["count"] == 5
    told = next(s for s in second["steps"] if s["id"] == "tell")["outputs"]["note"]
    assert told.startswith("2: ") and story(3) in told and story(4) in told and story(0) not in told
    assert [s["status"] for s in second["steps"]] == ["ok", "ok"]
    assert statuses(api, aid) == {"sent": 5}


def test_a_run_whose_only_action_fails_sends_nothing_and_says_why(api, monkeypatch):
    def refuse(c, auto, run_id, args, values):
        return {"ok": False, "detail": "the post was refused"}

    monkeypatch.setitem(automations.A.ACTIONS, "note", refuse)
    aid, source = started(api)
    saved(api, source, [item(n, story(n)) for n in range(3)])
    run(api, at(1))
    done = runs(api, aid)[0]
    assert done["status"] == "failed" and done["error"] == "nothing delivered: the post was refused"
    assert done["summary"] == "Nothing delivered: the post was refused · 3 back to waiting"
    assert statuses(api, aid) == {"kept": 3}


def test_a_second_send_does_not_take_the_items_a_running_send_holds(api):
    brief = {"id": "brief", "type": "bot_turn", "bot": "ops", "prompt": "{{listening.text}}"}
    post_it = {"id": "post", "type": "action", "do": "note", "args": {"text": "{{brief.text}}"}}
    aid, source = started(api, steps=[brief, post_it], limits=[{"count": 3, "per": "day"}])
    saved(api, source, [item(n, story(n)) for n in range(6)])
    run(api, at(1))
    first = runs(api, aid)[0]
    assert first["status"] == "waiting" and first["listening"]["counts"]["sending"] == 3
    assert first["summary"] == "3 sending"
    assert statuses(api, aid) == {"sending": 3, "kept": 3}
    assert room(api, aid, at(1.5)) == 0  # what a running send holds counts toward the limit
    run(api, at(1, days=1))
    second = runs(api, aid)[1]
    taken = [i["native_id"] for i in get(api, f"listening/items?automation={aid}&run={second['id']}")["items"]]
    held = [i["native_id"] for i in get(api, f"listening/items?automation={aid}&run={first['id']}")["items"]]
    assert len(taken) == 3 and not set(taken) & set(held)


def test_a_send_that_ends_outside_its_save_puts_its_items_back_once(api):
    brief = {"id": "brief", "type": "bot_turn", "bot": "ops", "prompt": "{{listening.text}}"}
    post_it = {"id": "post", "type": "action", "do": "note", "args": {"text": "{{brief.text}}"}}
    aid, source = started(api, steps=[brief, post_it], limits=[{"count": 3, "per": "day"}])
    saved(api, source, [item(n, story(n)) for n in range(3)])
    run(api, at(1))
    assert statuses(api, aid) == {"sending": 3}
    rid = runs(api, aid)[0]["id"]
    with api.app.state.store.transaction() as c:  # dropped by a guard, or given up on: no save settles it
        c.execute("UPDATE automation_runs SET status='skipped' WHERE id=?", (rid,))
    run(api, at(2))  # the pass settles it
    assert statuses(api, aid) == {"kept": 3}
    with api.app.state.store.transaction() as c:
        assert L.settle(c, rid) == 0  # once
    delivered = sql(api, "SELECT delivered FROM listening_items WHERE automation_id=?", (aid,))
    assert {r["delivered"] for r in delivered} == {'["brief"]'}  # its turn started: the next send does not ask again


def test_deleting_a_listening_while_it_sends_expires_what_the_send_held(api):
    brief = {"id": "brief", "type": "bot_turn", "bot": "ops", "prompt": "{{listening.text}}"}
    post_it = {"id": "post", "type": "action", "do": "note", "args": {"text": "{{brief.text}}"}}
    aid, source = started(api, steps=[brief, post_it], limits=[{"count": 3, "per": "day"}])
    saved(api, source, [item(n, story(n)) for n in range(4)])
    run(api, at(1))
    assert statuses(api, aid) == {"sending": 3, "kept": 1}
    api.delete(f"/api/v2/automations/{aid}", headers=headers("ana-test"))
    assert statuses(api, aid) == {"expired": 4}


def test_an_item_back_in_waiting_still_expires_a_week_after_it_arrived(api):
    aid, source = started(api, steps=[TURN], spend_cap=CAP)
    saved(api, source, [item(1, story(1))])
    cap_full(api, aid)
    run(api, at(1, days=6))
    assert statuses(api, aid) == {"kept": 1}
    run(api, at(1, days=7))
    assert statuses(api, aid) == {"expired": 1}
