"""Updates: every bot reports in once a day, one bot asked at a time; Friday is
the week in review; read state is per person; a reply is both a comment on the update and a
message in the bot's chat."""

import json
from datetime import datetime, timezone

import pytest

from backend import threads, updates
from backend.auth import Identity
from backend.store import H, Problem, digest
from backend.tests.helpers.api import get, headers, post, setup_attempt  # noqa: F401

MORNING = datetime(2026, 9, 29, 13, 0, tzinfo=timezone.utc)  # Tuesday 06:00 Pacific
FRIDAY = datetime(2026, 10, 2, 13, 0, tzinfo=timezone.utc)
NIGHT = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)  # 03:00 Pacific, before the queue
BEFORE_FIVE = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)  # Monday 03:00 Pacific: no queue of its own yet


def out(c):
    return [dict(r) for r in c.execute("SELECT bot, state, kind FROM update_queue WHERE state='sent'")]


def bot_id(c, slug):
    """A bot's id by its slug: updates are written and read by the bot's id."""
    return H.resolve_bot(c, slug)["id"]


def bid(api, slug):
    with api.app.state.store.read() as c:
        return bot_id(c, slug)


@pytest.mark.parametrize("kind", ["daily", "weekly"])
def test_update_requests_never_wake_counsel_which_cannot_publish(api, kind):
    from backend.tests.helpers.counsel import counsel_on

    counsel_on(api)
    with api.app.state.store.transaction() as c:
        # Older installs may have both switches on. The posting boundary still applies.
        c.execute("INSERT OR REPLACE INTO update_settings(bot,daily,weekly) VALUES(?,1,1)", (H.RULES_OWNER,))
        updates.build_queue(c, "2026-09-29", kind)
        assert not c.execute("SELECT 1 FROM update_queue WHERE bot=?", (H.RULES_OWNER,)).fetchone()
        assert c.execute("SELECT 1 FROM update_queue WHERE bot='ops'").fetchone()
        assert H.RULES_OWNER not in updates.redo(c, kind, "2026-09-29")
        with pytest.raises(Problem):
            updates.redo(c, kind, "2026-09-29", [H.RULES_OWNER])
        assert updates.settings(c, H.RULES_OWNER) == {"daily": False, "weekly": False}
        with pytest.raises(Problem):
            updates.set_settings(c, H.RULES_OWNER, **{kind: True})


def test_an_old_queued_counsel_update_is_skipped_without_sending_a_request(api):
    from backend.tests.helpers.counsel import counsel_on

    counsel_on(api)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO update_queue(id,bot,kind,day,rank,state) VALUES(?,?,'daily',?,0,'queued')",
            ("old-counsel-update", H.RULES_OWNER, MORNING.date().isoformat()),
        )
        assert updates.dispatch(c, MORNING) is None
        row = c.execute("SELECT state,message_id FROM update_queue WHERE id='old-counsel-update'").fetchone()
        assert tuple(row) == ("skipped", None)
        assert not c.execute("SELECT 1 FROM jobs WHERE bot_id=?", (H.RULES_OWNER,)).fetchone()


def test_the_queue_asks_one_bot_at_a_time_and_moves_on_when_it_posts(api):
    store = api.app.state.store
    with store.transaction() as c:
        assert updates.dispatch(c, NIGHT) is None and not c.execute("SELECT 1 FROM update_queue").fetchone()
        first = updates.dispatch(c, MORNING)
        queued = c.execute("SELECT count(*) FROM update_queue WHERE day='2026-09-29'").fetchone()[0]
        active = c.execute("SELECT count(*) FROM bots WHERE state='active'").fetchone()[0]
    assert first and queued == active, "every active bot is queued, and the first one asked"
    with store.transaction() as c:
        assert updates.dispatch(c, MORNING) is None, "one at a time: nothing more while it is out"
        request = c.execute(
            "SELECT m.body, m.refs FROM update_queue q JOIN messages m ON m.id=q.message_id WHERE q.state='sent'"
        ).fetchone()
        assert "tico_update_create" in request["body"] and "Finished since your last update" in request["body"]
        assert c.execute("SELECT 1 FROM jobs WHERE bot_id=?", (first,)).fetchone(), "the request wakes the bot"
        updates.post(c, first, "- Shipped the pricing page", day="2026-09-29")
        second = updates.dispatch(c, MORNING)
        assert second and second != first
        assert (
            c.execute("SELECT state FROM update_queue WHERE bot=? AND day='2026-09-29'", (first,)).fetchone()[0]
            == "posted"
        )


@pytest.mark.parametrize("delivery", [False, True])
def test_update_requests_keep_their_readers_through_thread_migration(api, delivery):
    store, auth = api.app.state.store, api.app.state.auth
    with store.transaction() as c:
        recipient = bot_id(c, "ops")
        updates.build_queue(c, "2026-09-29", "daily")
        row = c.execute("SELECT * FROM update_queue WHERE bot=?", (recipient,)).fetchone()
        message = updates.send_request(c, row, delivery=delivery)
        owner = auth.owner_identity(c)
        people = [
            owner,
            Identity("human:ben", "human", "ben@acme.example"),
            Identity("human:cara", "human", "cara@acme.example"),
        ]
        bots = [Identity(H.bot_actor(bot["id"]), "bot", agent="standing") for bot in H.bots(c)]
        expected = {owner.actor, H.bot_actor(recipient)}

        # A request can carry Learn's sources and task facts. Read access to another bot's activity must not
        # turn its request into a company conversation, either before or after the migration.
        before = set()
        for who in people + bots:
            try:
                auth.conversation(c, who, message["conversation_id"])
                before.add(who.actor)
            except Problem:
                pass
        assert before == expected
        c.execute(
            "DELETE FROM registry_metadata WHERE key IN (?,?)", (threads.MIGRATION_KEY, threads.MIGRATION_PROGRESS_KEY)
        )
        assert threads.migrate(c)
        for target in (message["conversation_id"], H.message(c, message["id"])):
            after = {who.actor for who in people + bots if threads.readable(c, threads.viewer(auth, who), target)}
            assert after == expected


def test_a_bot_posts_once_a_day_and_each_person_has_their_own_read_state(api, monkeypatch):
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")  # a Tuesday: a daily, whatever today is
    r, msg, attempt = setup_attempt(api, "ops")
    bot = {"Authorization": "Bearer " + attempt["token"]}
    first = api.post(
        "/api/v2/updates", json={"body": "- Drafted the checklist"}, headers={**bot, "Idempotency-Key": "u1"}
    )
    assert first.status_code == 200, first.text
    again = api.post(
        "/api/v2/updates", json={"body": "- Drafted and sent the checklist"}, headers={**bot, "Idempotency-Key": "u2"}
    ).json()["update"]
    assert again["id"] == first.json()["update"]["id"], "posting again replaces the day's update"
    labelled = api.post(
        "/api/v2/updates",
        json={"body": "- Drafted and sent the checklist\n- Next: send it"},
        headers={**bot, "Idempotency-Key": "u3"},
    )
    assert labelled.status_code == 200 and "section label" in labelled.json()["update"]["warning"], (
        "a writing slip is a warning"
    )
    empty = api.post("/api/v2/updates", json={"body": ""}, headers={**bot, "Idempotency-Key": "u4"})
    assert empty.status_code == 422, "an empty update is still refused"
    again = api.post(
        "/api/v2/updates", json={"body": "- Drafted and sent the checklist"}, headers={**bot, "Idempotency-Key": "u5"}
    ).json()["update"]
    feed = get(api, "updates")
    assert [u["headline"] for u in feed["updates"]] == ["Drafted and sent the checklist"]
    assert feed["updates"][0]["read"] is False and feed["unread"] == 1
    post(api, "updates/read", {"ids": [again["id"]]})
    assert get(api, "updates")["unread"] == 0
    assert get(api, "updates/unread") == {"unread": 0, "meetings_pending": 0}
    assert get(api, "updates", token="ben-test")["unread"] == 1, "Ana reading it is not Ben reading it"
    post(api, "updates/read", {"ids": [again["id"]], "read": False})
    assert get(api, "updates?unread=true")["updates"][0]["id"] == again["id"]
    post(api, "updates/read", {"all": True})
    assert get(api, "updates")["unread"] == 0


SLIDES = {
    "goal": "Grow organic signups to 400 a month; at 310 and on pace.",
    "kpis": [{"name": "Signups", "value": "310", "series": [240, 262, 281, 310], "note": "Two new pages ranked."}],
    "done": ["Shipped four landing pages", "- Linked them from six older posts"],
    "focus": ["Pitch the checklist to three newsletters"],
    "blockers": [],
}


def test_a_week_in_review_is_five_slides_and_an_owner_can_have_last_week_redone(api):
    """Owner, 2026-10-05: the weekly is a few swipeable slides: Goal, KPIs, done last week, focus next
    week, biggest blockers; and last week's can be run again in the new shape."""
    r, msg, attempt = setup_attempt(api, "ops")
    bot = {"Authorization": "Bearer " + attempt["token"]}
    send = lambda body, n: api.post("/api/v2/updates", json=body, headers={**bot, "Idempotency-Key": f"w-{n}"})
    ok = send({"kind": "weekly", "slides": SLIDES}, 3)
    assert ok.status_code == 200, ok.text
    week = ok.json()["update"]
    assert week["slides"]["done"] == ["Shipped four landing pages", "Linked them from six older posts"]

    friday = "2026-10-02"
    back = send({"kind": "weekly", "slides": SLIDES, "day": friday}, 4)
    assert back.status_code == 422, "a bot never back-dates a post on its own"
    post(api, "updates/redo", {"day": friday}, token="ben-test", expected=403)
    asked = post(api, "updates/redo", {"day": friday, "bots": ["ops"]})
    assert asked["bots"] == ["ops"]
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='ops'")  # its run is over, so it is free
        assert updates.dispatch(c, BEFORE_FIVE) == "ops", "a redo of a past day is sent like today's requests"
        request = c.execute(
            "SELECT m.body FROM update_queue q JOIN messages m ON m.id=q.message_id WHERE q.state='sent' AND q.redo=1"
        ).fetchone()["body"]
    assert "with day 2026-10-02" in request
    redone = send({"kind": "weekly", "slides": SLIDES, "day": friday}, 5)
    assert redone.status_code == 200 and redone.json()["update"]["day"] == friday, redone.text
    with store.transaction() as c:
        updates.dispatch(c, BEFORE_FIVE)
        assert (
            c.execute("SELECT state FROM update_queue WHERE bot='ops' AND day=?", (friday,)).fetchone()[0] == "posted"
        )


def test_people_post_their_own_update_and_alerts_come_only_from_direct_reports(api, monkeypatch):
    """A person posts in the same shape as a bot, as `human:<id>`, and everyone on the chart sees it;
    an Assistant draft stays labelled when its person edits it and goes when they replace it; the
    direct scope keeps one level of the chart, so a report's report never alerts."""
    from backend.tests.helpers.bot_contact import bot

    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")
    bot(api, "boss", "Manager", reports_to="human:ana")
    bot(api, "analyst", "Analyst", reports_to="boss")
    store = api.app.state.store
    with store.transaction() as c:
        boss, analyst = bot_id(c, "boss"), bot_id(c, "analyst")
        updates.post(c, boss, "- Planned the launch", day="2026-09-29")
        updates.post(c, analyst, "- Sized the market", day="2026-09-29")
        updates.post(c, "human:ben", "- Met two customers", day="2026-09-29", drafted_by="assistant")

    def redraft():
        with store.transaction() as c:
            try:
                updates.post(c, "human:ben", "- Redrafted", day="2026-09-29", drafted_by="assistant")
                raise AssertionError("a draft landed over the person's own words")
            except Problem as e:
                assert e.status == 409

    edited = post(api, "updates", {"body": "- Met three customers"}, token="ben-test")["update"]
    assert edited["bot"] == "human:ben" and edited["drafted_by"] == "assistant" and edited["edited_by"] == "human:ben"
    redraft()  # over the words they edited
    own = post(api, "updates", {"body": "- Met three customers", "replace_draft": True}, token="ben-test")["update"]
    assert own["drafted_by"] is None and own["edited_by"] is None
    redraft()  # over their own words
    seen = {u["bot"] for u in get(api, "updates")["updates"]}
    assert {boss, analyst, "human:ben"} <= seen, "a person's update is for the whole company"
    post(api, f"updates/{own['id']}/reply", {"text": "Nice"}, expected=422)
    direct = {u["bot"] for u in get(api, "updates?scope=direct")["updates"]}
    assert boss in direct and analyst not in direct, "only one level down alerts"
    assert get(api, "updates/unread?scope=direct")["unread"] == len(direct)
    get(api, "updates?scope=everyone", expected=422)
    # An external agent is outside the company: it sees its own bot's update, no person's.
    with store.transaction() as c:
        c.execute(
            "INSERT INTO agents(bot,harness,token_hash,created_at,created_by,last_seen_at) VALUES(?,?,?,?,?,?)",
            (analyst, "hermes", digest("agent-test"), H.now(), "human:ana", H.now()),
        )
    outside = {u["bot"] for u in get(api, "updates", token="agent-test")["updates"]}
    assert analyst in outside and not any(updates.is_person(a) for a in outside)
    # The Assistant acting for ben in his chat posts no update for him: his update is his own.
    from backend.tests.helpers.assistant import asked, assistant_turn

    _, attempt = assistant_turn(api, "ben-test", "Post my update")
    asked(api, "updates", {"body": "- From my chat"}, attempt["token"], "turn", expected=403)
    # Nor as a card for him to confirm: posting an update is not a route the Assistant can propose.
    post(
        api,
        "assistant/actions",
        {"summary": "Post my update", "path": "/api/v2/updates", "body": {"body": "- From my chat"}},
        token=attempt["token"],
        expected=422,
    )


def test_updates_roll_up_the_org_chart_with_reports_updates_in_the_managers_request(api):
    """The deepest bots are asked first; a manager's request carries its reports' updates and names
    who had none, and its update links the ones it was written from."""
    from backend.tests.helpers.bot_contact import bot

    bot(api, "boss", "Manager", reports_to="human:ana")
    bot(api, "analyst", "Analyst", reports_to="boss")
    bot(api, "writer", "Writer", reports_to="boss")
    store = api.app.state.store
    with store.transaction() as c:
        analyst, writer, boss = bot_id(c, "analyst"), bot_id(c, "writer"), bot_id(c, "boss")
        first = updates.dispatch(c, MORNING)
        ranks = {r["bot"]: r["rank"] for r in c.execute("SELECT bot, rank FROM update_queue")}
        assert first in (analyst, writer) and ranks[boss] > max(ranks[analyst], ranks[writer])
        updates.post(c, first, "- Sized the market", day="2026-09-29")
        other_name = "Writer" if first == analyst else "Analyst"
        text = updates.request_text(c, boss, "daily", "2026-09-29")
        assert "Sized the market" in text and "No update from: " + other_name in text
        posted = updates.post(c, boss, "- The team sized the market", day="2026-09-29")
        mine = c.execute("SELECT id FROM updates WHERE bot=?", (first,)).fetchone()["id"]
        assert posted["inputs"] == [{"id": mine, "author": first}], (
            "the manager's update links the reports' updates it was written from"
        )
        assert updates.redo(c, "daily", "2026-09-29", ["analyst"], managers=True) == [analyst, boss], (
            "a redo with managers rewrites the roll-up above, deepest first"
        )


def test_a_report_on_retry_and_a_stacked_manager_keep_the_90_minute_wait(api, monkeypatch):
    """Through the scheduler's own steps: a report whose run ends without a post is asked again, and its
    manager waits 90 minutes from when it was first held, not from the day's first request; the
    manager above waits for that manager rather than starting its own clock."""
    from backend.tests.helpers.api import assign, ready, runner

    chain = {"cpo": "human:ana", "ops": "cpo", "finance": "ops", "product-design": "ops"}
    r = runner(api)
    for slug in chain:
        assign(api, r, slug)
    ready(api, r, list(chain))
    store = api.app.state.store
    with store.transaction() as c:
        for slug, boss in chain.items():
            c.execute("UPDATE bot_config SET reports_to=? WHERE bot=?", (boss, slug))
        for (slug,) in c.execute("SELECT slug FROM bots").fetchall():
            if slug not in chain:
                c.execute("INSERT OR REPLACE INTO update_settings(bot,daily,weekly) VALUES(?,0,0)", (slug,))
    clock = [H.now()]
    monkeypatch.setattr(H, "now", lambda: clock[0])

    def tick(minutes=0):
        clock[0] = H.shift(clock[0], minutes=minutes)
        with store.transaction() as c:
            return updates.dispatch(c, MORNING)

    def job(slug, state):
        with store.transaction() as c:
            c.execute(
                "UPDATE jobs SET state=? WHERE message_id=(SELECT message_id FROM update_queue WHERE bot=?)",
                (state, slug),
            )

    first = tick()
    other = "product-design" if first == "finance" else "finance"
    with store.transaction() as c:
        updates.post(c, first, "- Closed the books", day="2026-09-29")
    job(first, "completed")
    assert tick() == other
    job(other, "completed")  # its run ended without a post: asked once more,
    post(api, "chat/" + other, {"text": "One more thing"})  # once the run it is busy with now is over
    assert tick(100) is None, "the day's first request is 100 minutes old, and the manager still waits"
    with store.transaction() as c:
        held = dict(c.execute("SELECT bot, held_at FROM update_queue WHERE bot IN ('ops','cpo')").fetchall())
    assert held["ops"] and not held["cpo"], "the manager above has not started its clock"
    assert tick(60) is None, "an hour after the manager was held"
    post(api, "chat/ops", {"text": "Check the close"})  # the manager is busy with a run when its wait is up
    assert tick(31) is None
    with store.transaction() as c:
        assert c.execute("SELECT held_at FROM update_queue WHERE bot='cpo'").fetchone()[0], (
            "a report whose own wait is over counts as released: the clock above starts, not a day's wait"
        )
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='ops'")
    assert tick() == "ops", "91 minutes after it was held, the manager writes with what is in"
    with store.transaction() as c:
        assert (
            "No update from: " + updates._label(c, other)
            in c.execute(
                "SELECT m.body FROM update_queue q JOIN messages m ON m.id=q.message_id WHERE q.bot='ops'"
            ).fetchone()[0]
        )
        updates.post(c, "ops", "- The team closed the books", day="2026-09-29")
    job("ops", "completed")
    assert tick() == "cpo", "the manager above is asked once its report posts"
    with store.transaction() as c:  # a reports_to loop is followed once, never forever
        c.execute("UPDATE bot_config SET reports_to='product-design' WHERE bot='cpo'")
        c.execute("UPDATE update_queue SET state='queued', held_at=NULL WHERE bot IN ('cpo','ops','product-design')")
        updates.waits_on_reports(c, c.execute("SELECT * FROM update_queue WHERE bot='cpo'").fetchone())


def test_a_persons_draft_is_written_from_only_what_everyone_can_see_and_never_over_their_own(api, monkeypatch):
    """After the bots, each person whose drafting is on is drafted, deepest first, by one call to the
    company's model. The call carries their shared tasks, goals, decisions, answers and comments, the
    shared meetings they were in and open reports' updates; a private task (open, finished, asked of
    someone, decided or commented on), a restricted bot's task (as owner or requester) or update, a
    private, pending or deleted meeting, an approval and their private Assistant chat never reach it.
    The draft posts labelled, is tried once more on any failure, and never lands over or beside their
    own words; on Friday it is the week in review, which they edit or replace."""
    from backend.tests.helpers.api import restrict
    from backend.tests.helpers.assistant import say

    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")
    post(api, "humans/ben", {"reports_to": "ana"})
    post(api, "humans/cara", {"update_draft": False})
    post(api, "tasks", {"owner": "human:ana", "title": "Ship the pricing page", "body": "x"})
    raise_plan = post(api, "tasks", {"owner": "human:ana", "title": "Secret raise plan", "body": "x", "private": True})
    done = post(api, "tasks", {"owner": "human:ana", "title": "Secret finished offer", "body": "x", "private": True})
    post(api, "tasks", {"owner": "human:ben", "title": "Secret ask of Ben", "body": "x", "private": True})
    audit = post(api, "tasks", {"owner": "bot:finance", "title": "Restricted audit", "body": "x"})
    budget = post(api, "tasks", {"owner": "bot:ops", "title": "Approve the ad budget", "body": "x"})
    say(api, "My salary talks are next week")  # her private Assistant chat
    store = api.app.state.store
    with store.transaction() as c:
        # Her answers are the engine's: one on the shared budget, one on her private raise plan, which stays out.
        ask = H.task_ask(
            c,
            "bot:ops",
            budget["id"],
            "human:ana",
            "Search or social first?",
            seen_version=H.task(c, budget["id"])["version"],
        )
        H.task_answer(c, "human:ana", budget["id"], ask["requirement_id"], "Search ads first")
        on_raise = H.task_ask(c, H.KEEPER, raise_plan["id"], "human:ana", "Can the raise wait?")
        H.task_answer(c, "human:ana", raise_plan["id"], on_raise["requirement_id"], "Mixed answer about the raise")
    for task in (budget, raise_plan, audit):  # her decisions and comments, on each kind of task
        post(api, f"tasks/{task['id']}/comments", {"text": "Comment on " + task["title"]})
        post(api, f"tasks/{task['id']}/done", {"version": get(api, f"tasks/{task['id']}")["task"]["version"]})
    meeting = lambda mid, notes, review="live", **meta: c.execute(
        "INSERT INTO meetings(id,title,owner,metadata,notes,created_at,updated_at,review_state) VALUES(?,?,?,?,?,?,?,?)",
        (mid, mid, "ben@acme.example", json.dumps({"kind": "meeting", **meta}), notes, H.now(), H.now(), review),
    )
    with store.transaction() as c:
        H.task_done(c, H.KEEPER, done["id"], "Finished")
        restrict(c, "finance", people=["ana"])
        H.task_create(c, "bot:finance", "Restricted request", "x", "human:ana")
        attended = {"calendar": {"attendees": ["ANA@acme.example"]}}
        meeting("Launch sync", "Agreed the launch date. " + "More detail. " * 80, **attended)
        meeting("Board prep", "Private board notes", private=True, **attended)
        meeting("Pending import", "Pending import notes", "pending", **attended)
        meeting("Old sync", "Deleted meeting notes", **attended)
        c.execute("INSERT INTO media_control(meeting_id,deleted_at) VALUES('Old sync',?)", (H.now(),))
        meeting("Ben's one-to-one", "Not her meeting notes", participants=[{"email": "cara@acme.example"}])
        c.execute("UPDATE bot_config SET reports_to='human:ana' WHERE bot IN ('finance','ops')")
        updates.post(c, "ops", "- Ops shipped the runbook", day="2026-09-29")
        updates.post(c, "finance", "- Restricted numbers moved", day="2026-09-29")
        updates.build_queue(c, "2026-09-29", "daily", "assistant")
        people = [r["bot"] for r in c.execute("SELECT bot FROM update_queue WHERE bot LIKE 'human:%' ORDER BY rank")]
        assert "human:cara" not in people, "drafting turns off per person"
        assert people.index("human:ben") < people.index("human:ana"), "a person's reports are drafted first"
        c.execute("UPDATE update_queue SET state='posted' WHERE bot NOT LIKE 'human:%'")
    post(api, "updates", {"body": "- Met two customers"}, token="ben-test")
    with store.transaction() as c:
        assert updates.dispatch(c, MORNING, "assistant") == "human:ana", "ben wrote his own; ana's draft is next"
        assert updates.dispatch(c, MORNING, "assistant") is None, "one at a time: nothing more while her draft is out"
    seen = []

    def write(system, user):
        seen.append(system + "\n" + user)
        return {"body": "- Shipped the pricing page"}

    draft = updates.write_draft(store, "human:ana", write, "assistant")
    assert draft["bot"] == "human:ana" and draft["drafted_by"] == "assistant"
    assert "Ship the pricing page" in seen[0] and "Ops shipped" in seen[0] and "Met two customers" in seen[0]
    for shared in (
        "Approve the ad budget: Done",
        "Comment on Approve the ad budget",
        "Search ads first",
        "Launch sync: Agreed the launch date",
    ):
        assert shared in seen[0], shared + " is shared and belongs in the draft"
    assert len(seen[0].split("Launch sync: ")[1].split("\n")[0]) <= updates.MEETING_CHARS + 1, "a summary is trimmed"
    for secret in (
        "Secret raise",
        "Secret finished",
        "Secret ask",
        "Restricted audit",
        "Restricted request",
        "Restricted numbers",
        "salary",
        "Private board",
        "Pending import",
        "Deleted meeting",
        "Not her meeting",
        "Mixed answer",
    ):
        assert secret not in seen[0], secret + " reached a company-wide draft"
    with store.transaction() as c:
        assert c.execute("SELECT state FROM update_queue WHERE bot='human:ana'").fetchone()[0] == "posted"

    def fails(system, user):
        raise OSError("down")

    with store.transaction() as c:
        c.execute("UPDATE update_queue SET state='sent', sent_at=? WHERE bot='human:ana'", (H.now(),))
    assert updates.write_draft(store, "human:ana", fails, "assistant") is None
    with store.transaction() as c:
        assert c.execute("SELECT state, tries FROM update_queue WHERE bot='human:ana'").fetchone()[:] == ("queued", 1)
    post(api, "updates", {"body": "- Signed the lease", "replace_draft": True})
    with store.transaction() as c:
        c.execute("UPDATE update_queue SET state='sent', sent_at=? WHERE bot='human:ana'", (H.now(),))
    assert updates.write_draft(store, "human:ana", write, "assistant") is None, "never over her own words"
    with store.read() as c:
        assert c.execute("SELECT body FROM updates WHERE bot='human:ana'").fetchone()[0] == "- Signed the lease"

    # Friday: a person's row is the week in review. Their own words, in either kind, close it.
    friday = "2026-10-02"
    monkeypatch.setattr(updates, "today", lambda at=None: friday)
    with store.transaction() as c:
        updates.build_queue(c, friday, "weekly", "assistant")
        c.execute("UPDATE update_queue SET state='posted' WHERE day=? AND bot NOT LIKE 'human:%'", (friday,))
    post(api, "updates", {"body": "- Met the auditors", "kind": "daily"}, token="ben-test")
    with store.transaction() as c:
        c.execute("UPDATE update_queue SET state='sent', sent_at=? WHERE bot='human:ben' AND day=?", (H.now(), friday))
    calls = len(seen)
    assert updates.write_draft(store, "human:ben", write, "assistant") is None and len(seen) == calls, (
        "not even a model call"
    )
    with store.transaction() as c:
        assert c.execute(
            "SELECT state, reason FROM update_queue WHERE bot='human:ben' AND day=?", (friday,)
        ).fetchone()[:] == ("posted", updates.OWN)
        assert not c.execute("SELECT 1 FROM updates WHERE bot='human:ben' AND kind='weekly'").fetchone(), (
            "no week in review is drafted beside his own daily"
        )
        try:
            updates.post(c, "human:ben", None, kind="weekly", day=friday, slides=SLIDES, drafted_by="assistant")
            raise AssertionError("a draft landed beside his own words")
        except Problem as e:
            assert e.status == 409
        c.execute("UPDATE update_queue SET state='queued' WHERE bot='human:ben' AND day=?", (friday,))
        assert updates.dispatch(c, FRIDAY, "assistant") == "human:ana", "the queue closes his row the same way"
        assert (
            c.execute("SELECT reason FROM update_queue WHERE bot='human:ben' AND day=?", (friday,)).fetchone()[0]
            == updates.OWN
        )
    # With no words of her own, a model answer of any shape (slides as a string) is asked again...
    assert updates.write_draft(store, "human:ana", lambda system, user: {"slides": "Shipped it"}, "assistant") is None
    with store.transaction() as c:
        assert c.execute("SELECT state, tries FROM update_queue WHERE bot='human:ana' AND day=?", (friday,)).fetchone()[
            :
        ] == ("queued", 1)
        c.execute("UPDATE update_queue SET state='sent', sent_at=? WHERE bot='human:ana' AND day=?", (H.now(), friday))
    # ...and her draft is a week in review she edits, then replaces with her own words.
    week = updates.write_draft(store, "human:ana", lambda system, user: {"slides": SLIDES}, "assistant")
    assert week["kind"] == "weekly" and week["drafted_by"] == "assistant" and week["slides"]["goal"] == SLIDES["goal"]
    edited = post(api, "updates", {"kind": "weekly", "slides": {**SLIDES, "goal": "Grow signups; at 320."}})["update"]
    assert edited["id"] == week["id"] and edited["drafted_by"] == "assistant" and edited["edited_by"] == "human:ana"
    own = post(api, "updates", {"kind": "weekly", "slides": SLIDES, "replace_draft": True})["update"]
    assert own["id"] == week["id"] and own["drafted_by"] is None and own["edited_by"] is None


def test_the_tree_leaves_out_the_helpers_behind_the_assistant(api, monkeypatch):
    """BotOps, the Strategy Owner, the Librarian and the General Manager get no node (so no empty "No update yet" row) in
    either kind or scope; a bot below one hangs from the next node up, and an update one posted stays in the feed."""
    from backend.tests.helpers.bot_contact import bot

    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")
    for slug, name in (
        ("botops", "BotOps"),
        ("strategy-owner", "Strategy Owner"),
        ("librarian", "Librarian"),
        ("general-manager", "General Manager"),
    ):
        bot(api, slug, name, reports_to="human:ana")
    bot(api, "scout", "Scout", reports_to="librarian")
    with api.app.state.store.transaction() as c:
        posted = updates.post(c, bot_id(c, "librarian"), "- Mapped the market", day="2026-09-29")
    keys = lambda nodes: [k for n in nodes for k in (n["key"], *keys(n["children"]))]
    node = lambda slug: "b:" + bid(api, slug)
    for path in ("updates/tree", "updates/tree?scope=all", "updates/tree?kind=weekly&scope=all"):
        tree = get(api, path)
        assert not {node("botops"), node("strategy-owner"), node("librarian"), node("general-manager")} & set(
            keys(tree["roots"])
        ), path
        assert node("scout") in [n["key"] for r in tree["roots"] for n in r["children"]], path + ": under the owner"
        assert "Mapped the market" not in json.dumps(tree), path
    assert posted["id"] in [u["id"] for u in get(api, "updates")["updates"]], "its own update stays in the feed"


def test_the_tree_nests_a_days_updates_by_the_org_chart_and_only_direct_reports_alert(api, monkeypatch):
    from backend.tests.helpers.bot_contact import bot

    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")
    bot(api, "boss", "Manager", reports_to="human:ana")
    bot(api, "analyst", "Analyst", reports_to="boss")
    bot(api, "writer", "Writer", reports_to="boss")
    with api.app.state.store.transaction() as c:
        updates.post(c, bot_id(c, "analyst"), "- Sized the market", day="2026-09-29")
        updates.post(c, bot_id(c, "writer"), "- Wrote the launch post", day="2026-09-29")
        updates.post(c, bot_id(c, "boss"), "- Planned the launch", day="2026-09-29")
    t = get(api, "updates/tree")
    assert t["scope"] == "below" and [r["key"] for r in t["roots"]] == ["p:ana"], "below you, by default"
    find = lambda nodes, key: (
        next((n for n in nodes if n["key"] == key), None)
        or next((hit for n in nodes if (hit := find(n["children"], key))), None)
    )
    boss, analyst = find(t["roots"], "b:" + bid(api, "boss")), find(t["roots"], "b:" + bid(api, "analyst"))
    assert boss["direct"] and boss["update"]["body"] == "- Planned the launch"
    assert analyst in boss["children"] and not analyst["direct"], "a report's report nests under it and never alerts"
    assert t["unread"] == 1
    assert get(api, "updates/tree?scope=all")["scope"] == "all"
    get(api, "updates/tree?scope=mine", expected=422)
    # The manager's card links the reports' updates it was written from, only those the reader may read.
    analyst_id, writer_id = bid(api, "analyst"), bid(api, "writer")
    links = {i["author"]: i["id"] for i in boss["update"]["inputs"]}
    assert set(links) == {analyst_id, writer_id} and links[analyst_id] == analyst["update"]["id"]
    from backend.tests.helpers.api import restrict

    with api.app.state.store.transaction() as c:
        restrict(c, "writer", people=["ana"])
    uid = boss["update"]["id"]
    for path in ("updates/tree?scope=all", "updates", f"updates/{uid}"):
        seen = get(api, path, token="cara-test")
        assert links[analyst_id] in json.dumps(seen) and links[writer_id] not in json.dumps(seen), path
        assert f'"{writer_id}"' not in json.dumps(seen), path + ": no hidden author either"
    assert get(api, f"updates/{uid}", token="cara-test")["update"]["inputs"] == [
        {"id": links[analyst_id], "author": analyst_id}
    ]
    assert len(get(api, f"updates/{uid}")["update"]["inputs"]) == 2, "the owner still sees both"
    direct = next(u for u in get(api, "updates?scope=direct")["updates"] if u["id"] == uid)
    assert len(direct["inputs"]) == 2, "showing only direct reports narrows the cards, not a card's links"
    # A bot Cara may not read is not in her tree at all; its report moves up under the next one she can see.
    with api.app.state.store.transaction() as c:
        restrict(c, "boss", people=["ana"])
    hers = get(api, "updates/tree?scope=all", token="cara-test")
    assert find(hers["roots"], "b:" + bid(api, "boss")) is None and "Planned the launch" not in str(hers)
    assert find(hers["roots"], "b:" + bid(api, "analyst"))["update"]["body"] == "- Sized the market"


def test_the_feed_counts_replies_in_one_read_and_homes_card_shows_the_same_updates(api, monkeypatch):
    """Nov 4 web pass: the feed counted each update's replies with its own scan of every message (40 a page, ~500 ms on
    a busy hub), and Home's Updates card built the whole feed to show five headlines."""
    from backend.tests.helpers.health_queries import counted

    store = api.app.state.store
    with store.transaction() as c:
        bots = [bot_id(c, slug) for slug in ("ops", "finance", "product-design")]
        made = [
            updates.post(c, b, f"- Shipped part {n}", kind="daily", day=f"2026-10-0{n + 1}")["id"]
            for n, b in enumerate(bots)
        ]
    assert len(made) == 3
    for uid, n in zip(made, (2, 1, 0)):
        for k in range(n):
            post(api, f"updates/{uid}/reply", {"text": f"Thanks {k}"})
    page = "/api/v2/updates?limit=40&include_archive=true"  # with history: older days of the same bots
    feed, few = counted(api, monkeypatch, page)
    assert {u["id"]: u["replies"] for u in feed["updates"]} == dict(zip(made, (2, 1, 0)))
    get(api, "places/person:me")  # the first open saves the place
    _, card_few = counted(api, monkeypatch, "/api/v2/places/person:me")
    with store.transaction() as c:
        now = H.now()
        c.executemany(
            "INSERT INTO updates(id,bot,kind,day,headline,body,created_at,updated_at,inputs,meetings) "
            "VALUES(?,?,'daily',?,?,'- x',?,?,'[]',0)",
            [
                (
                    H.new_id(),
                    bots[n % 3],
                    f"2026-08-{n + 1:02d}",
                    f"Older {n}",
                    H.shift(now, days=-40 - n),
                    H.shift(now, days=-40 - n),
                )
                for n in range(30)
            ],
        )
    history, many = counted(api, monkeypatch, page)
    assert len(history["updates"]) == 33 and many == few, "thirty more updates on the page take no more statements"
    assert {u["id"]: u["replies"] for u in history["updates"] if u["id"] in made} == dict(zip(made, (2, 1, 0)))
    feed = get(api, "updates")
    home, card_many = counted(api, monkeypatch, "/api/v2/places/person:me")
    assert card_many == card_few
    card = next(p["card"]["data"] for p in home["pins"] if p.get("card", {}).get("template") == "updates")
    shown = len(card["updates"])
    assert shown and card["unread"] == feed["unread"]
    assert card["updates"] == [
        {
            "id": u["id"],
            "author": u["bot"],
            "kind": u["kind"],
            "day": u["day"],
            "headline": u["headline"],
            "created_at": u["created_at"],
            "read": u["read"],
        }
        for u in feed["updates"][:shown]
    ]


def test_an_updates_replies_are_found_by_index_not_by_scanning_every_message(api, monkeypatch):
    """Perf I02: the thread and the feed's reply count read messages by `refs.update`. Without an index on that
    expression both scan every message in the company, so their cost grew with all chat, not with Updates. Both must
    search `messages_refs_update` and still find the same replies."""
    import contextlib

    store = api.app.state.store
    with store.transaction() as c:
        uid = updates.post(c, bot_id(c, "ops"), "- Shipped the index", kind="daily", day="2026-10-01")["id"]
    for k in range(2):
        post(api, f"updates/{uid}/reply", {"text": f"Thanks {k}"})
    seen = []

    def traced(opened):
        @contextlib.contextmanager
        def opening():
            with opened() as c:
                c.set_trace_callback(seen.append)
                try:
                    yield c
                finally:
                    c.set_trace_callback(None)

        return opening

    with monkeypatch.context() as patched:
        for name in ("read", "read_transaction"):
            patched.setattr(store, name, traced(getattr(store, name)))
        feed = get(api, "updates?limit=40&include_archive=true")
        with store.read() as c:
            thread = updates.thread(c, uid)
    assert {u["id"]: u["replies"] for u in feed["updates"]}[uid] == 2
    assert [m["body"].endswith(f"Thanks {k}") for k, m in enumerate(thread)] == [True, True]
    reads = {s for s in seen if "FROM messages" in s and "$.update" in s}
    assert len(reads) == 2, reads  # the feed's count and the thread
    with store.read() as c:
        for sql in reads:
            plan = [str(r[-1]) for r in c.execute("EXPLAIN QUERY PLAN " + sql)]
            assert any("messages_refs_update" in step for step in plan), (sql, plan)
            assert not any(step.startswith("SCAN messages") for step in plan), (sql, plan)


def test_a_bot_with_nothing_new_skips_and_its_request_is_neither_asked_again_nor_missed(api, monkeypatch):
    """`skip` posts nothing and finishes the day's request skipped with its reason; a run that then ends without a
    post does not send the request again."""
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")
    _, _, attempt = setup_attempt(api, "ops")
    bot = {"Authorization": "Bearer " + attempt["token"]}
    store = api.app.state.store
    with store.transaction() as c:
        updates.build_queue(c, "2026-09-29", "daily")
        ops = bot_id(c, "ops")
        updates.send_request(c, c.execute("SELECT * FROM update_queue WHERE bot=?", (ops,)).fetchone())
    skipped = api.post("/api/v2/updates", json={"skip": True}, headers={**bot, "Idempotency-Key": "skip-1"})
    assert skipped.status_code == 200 and skipped.json()["reason"] == updates.NOTHING_NEW, skipped.text
    with store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id=?", (ops,))  # its run ends without a post
        updates.dispatch(c, MORNING, build=False)
        assert tuple(
            c.execute("SELECT state, reason FROM update_queue WHERE bot=? AND day='2026-09-29'", (ops,)).fetchone()
        ) == ("skipped", updates.NOTHING_NEW)
        assert not c.execute("SELECT 1 FROM updates WHERE bot=?", (ops,)).fetchone(), "nothing was posted"


def test_a_report_with_nothing_new_is_nothing_new_to_its_manager_not_missing(api, monkeypatch):
    from backend.tests.helpers.bot_contact import bot

    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")
    bot(api, "boss", "Manager", reports_to="human:ana")
    bot(api, "analyst", "Analyst", reports_to="boss")
    with api.app.state.store.transaction() as c:
        boss, analyst = bot_id(c, "boss"), bot_id(c, "analyst")
        updates.build_queue(c, "2026-09-29", "daily")
        updates.skip(c, analyst, "daily", "2026-09-29")
        text = updates.request_text(c, boss, "daily", "2026-09-29")
    assert "Nothing new from: Analyst" in text and "No update from" not in text


def test_a_persons_draft_with_nothing_real_to_say_posts_nothing(api):
    store = api.app.state.store
    with store.transaction() as c:
        c.execute(
            "INSERT INTO update_queue(id,bot,kind,day,rank,state,sent_at) "
            "VALUES('draft-skip','human:ana','daily','2026-09-29',0,'sent',?)",
            (H.now(),),
        )
    assert updates.write_draft(store, "human:ana", lambda system, user: {"skip": True}, "assistant") is None
    with store.read() as c:
        assert tuple(c.execute("SELECT state, reason FROM update_queue WHERE id='draft-skip'").fetchone()) == (
            "skipped",
            updates.NOTHING_NEW,
        )
        assert not c.execute("SELECT 1 FROM updates WHERE bot='human:ana'").fetchone(), "no draft was posted"
