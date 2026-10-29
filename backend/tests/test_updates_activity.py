"""A bot's update written from its activity (backend/updates.py write_from_activity): the facts it is written from keep a
person's draft's privacy rules, the Gateway's Summarize writes it, the company's model and then the bot itself are the
fallbacks, and each card shows its author's model spend for the day it covers."""

import json

from backend import updates
from backend.store import H
from backend.tests.test_api import api, get, headers, post, restrict  # noqa: F401
from backend.tests.test_updates import MORNING

DAY = "2026-09-29"


def chart(api):
    from backend.tests.test_bot_contact import bot
    bot(api, "boss", "Boss", reports_to="human:ana")
    bot(api, "analyst", "Analyst", reports_to="boss")
    bot(api, "writer", "Writer", reports_to="boss")


def out_for(c, slug):
    """Put `slug`'s request for the day out as the server's to write (no message), as `dispatch(server=True)` does."""
    updates.build_queue(c, DAY, "daily")
    c.execute("UPDATE update_queue SET state='posted' WHERE day=? AND bot<>?", (DAY, slug))
    c.execute("UPDATE update_queue SET state='sent', message_id=NULL, sent_at=? WHERE day=? AND bot=?", (H.now(), DAY, slug))


def row(c, slug):
    return c.execute("SELECT * FROM update_queue WHERE bot=? AND day=?", (slug, DAY)).fetchone()


def test_a_bots_facts_carry_its_shared_work_and_nothing_private_restricted_or_approved(api):
    chart(api)
    store = api.app.state.store
    post(api, "tasks", {"owner": "bot:analyst", "title": "Size the market", "body": "x"})
    brief = post(api, "tasks", {"owner": "bot:analyst", "title": "Write the brief", "body": "x"})
    post(api, f"tasks/{brief['id']}", {"version": get(api, f"tasks/{brief['id']}")["task"]["version"], "status": "done"})
    with store.transaction() as c:
        H.task_create(c, "bot:analyst", "Review the deck", "x", "bot:writer")
        H.task_create(c, "bot:analyst", "Secret pricing", "x", "bot:analyst", private=True)
        H.task_create(c, "bot:analyst", "Restricted audit ask", "x", "bot:finance")
        restrict(c, "finance", people=["ana"])
        c.execute("UPDATE bot_config SET reports_to='boss' WHERE bot='finance'")
        H.say(c, "bot:analyst", "human:ana", "Chat secret about the raise")
        c.execute("INSERT INTO approvals(id,kind,task_id,payload_json,requested_by,decided_by,decision,decided_at,created) "
                  "VALUES('ap1','send',?,?,'bot:analyst','human:ana','approved',?,?)",
                  (brief["id"], json.dumps({"text": "Approval payload to the vendor"}), H.now(), H.now()))
        facts, used = updates.bot_facts(c, "analyst", "daily", DAY)
        for shared in ("Write the brief", "Size the market (open)", "Review the deck (Writer)"):
            assert shared in facts, shared
        for secret in ("Secret pricing", "Restricted audit", "Chat secret", "Approval payload"):
            assert secret not in facts, secret + " reached a bot's update"
        assert used == []
        mine = updates.post(c, "analyst", "- Sized the market", day=DAY)
        flagged = updates.post(c, "writer", "- Met Acme about the launch", day=DAY)
        c.execute("UPDATE updates SET meetings=1 WHERE id=?", (flagged["id"],))
        updates.post(c, "finance", "- Restricted numbers moved", day=DAY)
        facts, used = updates.bot_facts(c, "boss", "daily", DAY)
        assert "Sized the market" in facts and "Writer: posted an update (not shown here)" in facts
        assert "Met Acme" not in facts and "Restricted numbers" not in facts and "Finance" not in facts
        assert used == [mine["id"]], "a manager links only the reports' updates it was written from"


def test_a_week_in_review_covers_the_week_not_the_last_daily_update(api):
    chart(api)
    store = api.app.state.store
    with store.transaction() as c:
        H.task_create(c, "bot:analyst", "Earlier in the week", "x", "bot:analyst")
        c.execute("UPDATE tasks SET status='done' WHERE title='Earlier in the week'")
        daily = updates.post(c, "analyst", "- Ran the daily", day=DAY)
        c.execute("UPDATE updates SET created=? WHERE id=?", (H.shift(H.now(), days=1), daily["id"]))
        facts, _ = updates.bot_facts(c, "analyst", "weekly", DAY)
    assert "Earlier in the week" in facts, "a daily update posted later in the week cut the week in review short"


def test_summarize_writes_the_update_and_the_model_then_the_bot_are_the_fallbacks(api):
    chart(api)
    store = api.app.state.store
    post(api, "tasks", {"owner": "bot:analyst", "title": "Size the market", "body": "x"})
    with store.transaction() as c:
        out_for(c, "analyst")
    calls = []

    def summarize(bot, text, instructions, max_words):
        calls.append((bot, text, instructions, max_words))
        return {"model": "tico-summarize-1.0", "summary": "- Sized the market\n- Next: the pricing study",
                "engine": "x", "usage": {"cost_usd": 0.0004}}
    posted = updates.write_from_activity(store, "analyst", summarize, None)
    assert posted["body"] == "- Sized the market\n- Next: the pricing study" and posted["written"] == "activity"
    assert calls[0][0] == "analyst" and "Size the market (open)" in calls[0][1] and calls[0][3] == updates.LIMITS["daily"]["words"]
    with store.read() as c:
        assert row(c, "analyst")["state"] == "posted" and row(c, "analyst")["message_id"] is None, "no bot run was spent"
        event = c.execute("SELECT detail_json FROM events WHERE action='update.written'").fetchone()
        assert json.loads(event[0]) == {"bot": "analyst", "route": "gateway", "cost_usd": 0.0004}

    # The Gateway down or its answer unusable: the company's model writes it, in the same shape.
    def down(*a):
        raise updates.Unwritten("the Gateway answered 503")
    seen = []

    def model(system, user):
        seen.append(user)
        return {"body": "- Sized the market again"}
    for broken in (down, lambda *a: {"summary": ""}):
        with store.transaction() as c:
            c.execute("UPDATE update_queue SET state='sent', redo=1, sent_at=? WHERE bot='analyst'", (H.now(),))
        assert updates.write_from_activity(store, "analyst", broken, model)["body"] == "- Sized the market again"
    assert len(seen) == 2 and "Size the market" in seen[0]

    # Neither gives one: the bot is asked in a run, as before, with the request it always had.
    with store.transaction() as c:
        c.execute("UPDATE update_queue SET state='sent', redo=1, message_id=NULL, sent_at=? WHERE bot='analyst'", (H.now(),))
    assert updates.write_from_activity(store, "analyst", down, lambda s, u: {"body": ""}) is None
    with store.read() as c:
        asked = row(c, "analyst")
        assert asked["state"] == "sent" and asked["message_id"], "the bot has the request"
        assert "hub_update_create" in c.execute("SELECT body FROM messages WHERE id=?", (asked["message_id"],)).fetchone()[0]


def test_a_bot_that_posted_its_own_keeps_it_and_a_friday_is_slides(api):
    chart(api)
    store = api.app.state.store
    with store.transaction() as c:
        out_for(c, "analyst")
        updates.post(c, "analyst", "- My own words", day=DAY)
    called = []
    assert updates.write_from_activity(store, "analyst", lambda *a: called.append(a), None) is None and not called
    with store.read() as c:
        assert row(c, "analyst")["state"] == "posted"
        assert c.execute("SELECT body, written FROM updates WHERE bot='analyst'").fetchone()[:] == ("- My own words", None)
    friday = "2026-10-02"
    with store.transaction() as c:
        updates.build_queue(c, friday, "weekly")
        c.execute("UPDATE update_queue SET state='sent', message_id=NULL, sent_at=? WHERE day=? AND bot='writer'",
                  (H.now(), friday))
    summary = ("Goal: Grow signups to 400 a month; at 310.\nDone:\n- Shipped four pages\n- Linked older posts\n"
               "Focus:\n- Pitch three newsletters\nBlockers:\n- None")
    week = updates.write_from_activity(store, "writer", lambda *a: {"summary": summary, "usage": {}}, None)
    assert week["kind"] == "weekly" and week["written"] == "activity"
    assert week["slides"]["goal"] == "Grow signups to 400 a month; at 310."
    assert week["slides"]["done"] == ["Shipped four pages", "Linked older posts"] and week["slides"]["blockers"] == []


def test_from_summary_reads_lines_paragraphs_and_json(api):
    assert updates.from_summary("Shipped the page. Next, the pricing study.", "daily") == \
        {"body": "- Shipped the page.\n- Next, the pricing study."}
    assert updates.from_summary("\n".join(f"- Item {i}" for i in range(8)), "daily")["body"].count("\n") == 4
    assert updates.from_summary('```json\n{"body": "- Done"}\n```', "daily") == {"body": "- Done"}
    assert updates.from_summary("", "daily") is None
    assert updates.from_summary("Done:\n- Shipped", "weekly") is None, "a week in review needs its goal"


def test_the_writer_follows_the_team_rule_unless_the_bot_sets_its_own(api):
    chart(api)
    store = api.app.state.store
    with store.transaction() as c:
        assert updates.settings(c, "analyst")["from_activity"] is True, "from activity by default"
        busy = H.say(c, "human:ana", "bot:analyst", "Look at this")       # the bot is busy with a run
        c.execute("INSERT INTO jobs(id,message_id,bot,state,created) VALUES('busy',?,'analyst','running',?)",
                  (busy["id"], H.now()))
        assert updates._busy(c, "analyst")
        for slug in [r[0] for r in c.execute("SELECT slug FROM bots").fetchall() if r[0] != "analyst"]:
            c.execute("INSERT OR REPLACE INTO update_settings(bot,daily,weekly) VALUES(?,0,0)", (slug,))
        assert updates.dispatch(c, MORNING, server=True) == "analyst", "written on the server: a busy bot is not waited on"
        assert updates.server_written(c, "analyst") and row(c, "analyst")["message_id"] is None
    r = api.put("/api/v2/access/rules", json={"updates_from_activity": False}, headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    with store.read() as c:
        assert updates.settings(c, "analyst")["from_activity"] is False
    mine = post(api, "bots/analyst/updates", {"writer": "activity"})
    assert mine["writer"] == "activity" and mine["from_activity"] is True
    post(api, "bots/analyst/updates", {"writer": "robot"}, expected=422)
    with store.read() as c:
        assert updates.summarizer_for(c, store.settings, store) is None, "no Gateway connected: no Summarize"


def test_summarize_posts_the_documented_body_with_the_key_and_keeps_only_its_fields():
    from clients import gateway as G
    sent = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, limit=-1):
            return json.dumps({"model": "tico-summarize-1.0", "summary": "- Done", "engine": "e",
                               "usage": {"cost_usd": 0.001}, "extra": "dropped"}).encode()

    def opener(request, timeout):
        sent.append(request)
        return Response()
    status, answer, code = G.summarize("https://gateway.example.test/v1/gateway", "k-1", "facts", "be brief", 5,
                                       {"X-Tico-Bot": "b1"}, opener=opener)
    assert (status, code) == (200, "") and answer == {"model": "tico-summarize-1.0", "summary": "- Done", "engine": "e",
                                                      "usage": {"cost_usd": 0.001}}
    assert sent[0].full_url.endswith("/summarize") and sent[0].get_header("Authorization") == "Bearer k-1"
    assert json.loads(sent[0].data) == {"text": "facts", "instructions": "be brief", "max_words": 20}   # held to the
    # Gateway's 20 to 400 words, and its 2,000 characters of instructions, so a call is never refused `invalid`
    G.summarize("https://gateway.example.test/v1/gateway", "k-1", "facts", "x" * 3000, 900, opener=opener)
    assert json.loads(sent[1].data)["max_words"] == 400 and len(json.loads(sent[1].data)["instructions"]) == 2000


def turn(c, tid, bot, started, tokens, usd, billing="api"):
    c.execute("INSERT INTO turns(id,bot,started,finished,input_tokens,cached_tokens,output_tokens,est_cost_usd,billing) "
              "VALUES(?,?,?,?,?,?,?,?,?)", (tid, bot, started, started, tokens, 0, 0, usd, billing))


def test_a_card_shows_its_authors_spend_for_its_day_and_a_manager_its_teams(api):
    from backend.tests.test_usage_limits import groups, put
    chart(api)
    store = api.app.state.store
    with store.transaction() as c:
        # The daily for 2026-09-29 covers 05:00 Pacific on the 28th to 05:00 on the 29th (12:00Z to 12:00Z).
        turn(c, "a1", "analyst", "2026-09-28T20:00:00Z", 1000, 1.5)
        turn(c, "a2", "analyst", "2026-09-29T13:00:00Z", 9000, 9.0)              # after the day it covers
        turn(c, "w1", "writer", "2026-09-29T02:00:00Z", 500, 0.5, "subscription")
        turn(c, "b1", "boss", "2026-09-25T15:00:00Z", 200, 0.25)                  # only in the week
        analyst = updates.post(c, "analyst", "- Sized the market", day=DAY)
        boss = updates.post(c, "boss", "- The team sized the market", day=DAY)
        week = updates.post(c, "boss", None, kind="weekly", day="2026-10-02",
                            slides={"goal": "Grow signups.", "kpis": [], "done": ["Shipped"], "focus": ["Pitch"],
                                    "blockers": []})
    groups(api, "eng")
    with store.transaction() as c:
        c.execute("UPDATE bot_config SET team='eng' WHERE bot IN ('boss','analyst','writer')")
    put(api, "usage/pools/groups/eng", {"daily_usd": 20})
    card = get(api, "updates/" + analyst["id"])["update"]["spend"]
    assert card == {"runs": 1, "tokens": 1000, "est_cost_usd": 1.5, "subscription_equiv_usd": 0.0, "low_estimate_runs": 0}
    spend = get(api, "updates/" + boss["id"])["update"]["spend"]
    assert spend["runs"] == 0 and "est_cost_usd" in spend
    team = spend["team"]
    assert (team["runs"], team["tokens"], team["est_cost_usd"], team["subscription_equiv_usd"], team["bots"]) == \
        (2, 1500, 1.5, 0.5, 3), "itself and everyone below it; a subscription run as its API-equivalent"
    assert team["limit"]["group"] == "Engineering" and team["limit"]["cap"] == 20 and team["limit"]["period"] == "daily"
    weekly = get(api, "updates/" + week["id"])["update"]["spend"]
    assert weekly["runs"] == 1 and weekly["team"]["runs"] == 4, "the week in review counts the seven days before"
    # Who sees it: a member who runs none of these bots sees no figures; a reader who sees only some, no team total.
    assert "spend" not in get(api, "updates/" + boss["id"], token="cara-test")["update"]
    with store.read() as c:
        item = updates.add_spend(c, [{"bot": "boss", "kind": "daily", "day": DAY}], {"boss", "analyst"})[0]
        assert "team" not in item["spend"] and item["spend"]["runs"] == 0
        item = updates.add_spend(c, [{"bot": "boss", "kind": "daily", "day": DAY}], {"boss", "analyst", "writer"})[0]
        assert "limit" not in item["spend"]["team"], "the group limit only to the owner and administrators"
