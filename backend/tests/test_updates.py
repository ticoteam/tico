"""Updates: every bot reports in once a day, one bot asked at a time; Friday is
the week in review; read state is per person; a reply is both a comment on the update and a
message in the bot's chat."""

import json
from datetime import datetime, timezone

from backend import updates
from backend.store import H, Problem, digest
from backend.tests.test_api import api, get, headers, post, setup_attempt  # noqa: F401

MORNING = datetime(2026, 9, 29, 13, 0, tzinfo=timezone.utc)      # Tuesday 06:00 Pacific
FRIDAY = datetime(2026, 10, 2, 13, 0, tzinfo=timezone.utc)
NIGHT = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)        # 03:00 Pacific, before the queue
BEFORE_FIVE = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)  # Monday 03:00 Pacific: no queue of its own yet


def out(c):
    return [dict(r) for r in c.execute("SELECT bot, state, kind FROM update_queue WHERE state='sent'")]


def bot_id(c, slug):
    """A bot's id by its slug: updates are written and read by the bot's id."""
    return H.resolve_bot(c, slug)["id"]


def bid(api, slug):
    with api.app.state.store.read() as c:
        return bot_id(c, slug)


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
        request = c.execute("SELECT m.body, m.refs FROM update_queue q JOIN messages m ON m.id=q.message_id "
                            "WHERE q.state='sent'").fetchone()
        assert "hub_update_create" in request["body"] and "Finished since your last update" in request["body"]
        assert c.execute("SELECT 1 FROM jobs WHERE bot_id=?", (first,)).fetchone(), "the request wakes the bot"
        updates.post(c, first, "- Shipped the pricing page", day="2026-09-29")
        second = updates.dispatch(c, MORNING)
        assert second and second != first
        assert c.execute("SELECT state FROM update_queue WHERE bot=? AND day='2026-09-29'", (first,)).fetchone()[0] == "posted"


def test_a_bot_posts_once_a_day_and_each_person_has_their_own_read_state(api, monkeypatch):
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")   # a Tuesday: a daily, whatever today is
    r, msg, attempt = setup_attempt(api, "ops")
    bot = {"Authorization": "Bearer " + attempt["token"]}
    first = api.post("/api/v2/updates", json={"body": "- Drafted the checklist"},
                     headers={**bot, "Idempotency-Key": "u1"})
    assert first.status_code == 200, first.text
    again = api.post("/api/v2/updates", json={"body": "- Drafted and sent the checklist"},
                     headers={**bot, "Idempotency-Key": "u2"}).json()["update"]
    assert again["id"] == first.json()["update"]["id"], "posting again replaces the day's update"
    labelled = api.post("/api/v2/updates", json={"body": "- Drafted and sent the checklist\n- Next: send it"},
                        headers={**bot, "Idempotency-Key": "u3"})
    assert labelled.status_code == 200 and "section label" in labelled.json()["update"]["warning"], "a writing slip is a warning"
    empty = api.post("/api/v2/updates", json={"body": ""}, headers={**bot, "Idempotency-Key": "u4"})
    assert empty.status_code == 422, "an empty update is still refused"
    again = api.post("/api/v2/updates", json={"body": "- Drafted and sent the checklist"},
                     headers={**bot, "Idempotency-Key": "u5"}).json()["update"]
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


SLIDES = {"goal": "Grow organic signups to 400 a month; at 310 and on pace.",
          "kpis": [{"name": "Signups", "value": "310", "series": [240, 262, 281, 310], "note": "Two new pages ranked."}],
          "done": ["Shipped four landing pages", "- Linked them from six older posts"],
          "focus": ["Pitch the checklist to three newsletters"], "blockers": []}


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
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='ops'")      # its run is over, so it is free
        assert updates.dispatch(c, BEFORE_FIVE) == "ops", "a redo of a past day is sent like today's requests"
        request = c.execute("SELECT m.body FROM update_queue q JOIN messages m ON m.id=q.message_id "
                            "WHERE q.state='sent' AND q.redo=1").fetchone()["body"]
    assert "with day 2026-10-02" in request
    redone = send({"kind": "weekly", "slides": SLIDES, "day": friday}, 5)
    assert redone.status_code == 200 and redone.json()["update"]["day"] == friday, redone.text
    with store.transaction() as c:
        updates.dispatch(c, BEFORE_FIVE)
        assert c.execute("SELECT state FROM update_queue WHERE bot='ops' AND day=?", (friday,)).fetchone()[0] == "posted"


def test_people_post_their_own_update_and_alerts_come_only_from_direct_reports(api, monkeypatch):
    """A person posts in the same shape as a bot, as `human:<id>`, and everyone on the chart sees it;
    an Assistant draft stays labelled when its person edits it and goes when they replace it; the
    direct scope keeps one level of the chart, so a report's report never alerts."""
    from backend.tests.test_bot_contact import bot
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")
    bot(api, "boss", "Manager", reports_to="human:ana")
    bot(api, "analyst", "Analyst", reports_to="boss")
    store = api.app.state.store
    with store.transaction() as c:
        boss, analyst = bot_id(c, "boss"), bot_id(c, "analyst")
        updates.post(c, boss, "- Planned the launch", day="2026-09-29")
        updates.post(c, analyst, "- Sized the market", day="2026-09-29")
        updates.post(c, "human:ben", "- Met two customers", day="2026-09-29", drafted_by="coo")

    def redraft():
        with store.transaction() as c:
            try:
                updates.post(c, "human:ben", "- Redrafted", day="2026-09-29", drafted_by="coo")
                raise AssertionError("a draft landed over the person's own words")
            except Problem as e:
                assert e.status == 409
    edited = post(api, "updates", {"body": "- Met three customers"}, token="ben-test")["update"]
    assert edited["bot"] == "human:ben" and edited["drafted_by"] == "coo" and edited["edited_by"] == "human:ben"
    redraft()                                   # over the words they edited
    own = post(api, "updates", {"body": "- Met three customers", "replace_draft": True}, token="ben-test")["update"]
    assert own["drafted_by"] is None and own["edited_by"] is None
    redraft()                                   # over their own words
    seen = {u["bot"] for u in get(api, "updates")["updates"]}
    assert {boss, analyst, "human:ben"} <= seen, "a person's update is for the whole company"
    post(api, f"updates/{own['id']}/reply", {"text": "Nice"}, expected=422)
    direct = {u["bot"] for u in get(api, "updates?scope=direct")["updates"]}
    assert boss in direct and analyst not in direct, "only one level down alerts"
    assert get(api, "updates/unread?scope=direct")["unread"] == len(direct)
    get(api, "updates?scope=everyone", expected=422)
    # An external agent is outside the company: it sees its own bot's update, no person's.
    with store.transaction() as c:
        c.execute("INSERT INTO agents(bot,harness,token_hash,created_at,created_by,last_seen_at) VALUES(?,?,?,?,?,?)",
                  (analyst, "hermes", digest("agent-test"), H.now(), "human:ana", H.now()))
    outside = {u["bot"] for u in get(api, "updates", token="agent-test")["updates"]}
    assert analyst in outside and not any(updates.is_person(a) for a in outside)
    # The Assistant acting for ben in his chat posts no update for him: his update is his own.
    from backend.tests.test_assistant import asked, assistant_turn
    _, attempt = assistant_turn(api, "ben-test", "Post my update")
    asked(api, "updates", {"body": "- From my chat"}, attempt["token"], "turn", expected=403)
    # Nor as a card for him to confirm: posting an update is not a route the Assistant can propose.
    post(api, "assistant/actions", {"summary": "Post my update", "path": "/api/v2/updates",
                                    "body": {"body": "- From my chat"}}, token=attempt["token"], expected=422)


def test_updates_roll_up_the_org_chart_with_reports_updates_in_the_managers_request(api):
    """The deepest bots are asked first; a manager's request carries its reports' updates and names
    who had none, and its update links the ones it was written from."""
    from backend.tests.test_bot_contact import bot
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
        assert posted["inputs"] == [{"id": mine, "author": first}], "the manager's update links the reports' updates it was written from"
        assert updates.redo(c, "daily", "2026-09-29", ["analyst"], managers=True) == [analyst, boss], \
            "a redo with managers rewrites the roll-up above, deepest first"


def test_a_report_on_retry_and_a_stacked_manager_keep_the_90_minute_wait(api, monkeypatch):
    """Through the scheduler's own steps: a report whose run ends without a post is asked again, and its
    manager waits 90 minutes from when it was first held, not from the day's first request; the
    manager above waits for that manager rather than starting its own clock."""
    from backend.tests.test_api import assign, ready, runner
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
            c.execute("UPDATE jobs SET state=? WHERE message_id=(SELECT message_id FROM update_queue WHERE bot_id=?)",
                      (state, slug))

    first = tick()
    other = "product-design" if first == "finance" else "finance"
    with store.transaction() as c:
        updates.post(c, first, "- Closed the books", day="2026-09-29")
    job(first, "completed")
    assert tick() == other
    job(other, "completed")                     # its run ended without a post: asked once more,
    post(api, "chat/" + other, {"text": "One more thing"})   # once the run it is busy with now is over
    assert tick(100) is None, "the day's first request is 100 minutes old, and the manager still waits"
    with store.transaction() as c:
        held = dict(c.execute("SELECT bot, held_at FROM update_queue WHERE bot IN ('ops','cpo')").fetchall())
    assert held["ops"] and not held["cpo"], "the manager above has not started its clock"
    assert tick(60) is None, "an hour after the manager was held"
    post(api, "chat/ops", {"text": "Check the close"})    # the manager is busy with a run when its wait is up
    assert tick(31) is None
    with store.transaction() as c:
        assert c.execute("SELECT held_at FROM update_queue WHERE bot='cpo'").fetchone()[0], \
            "a report whose own wait is over counts as released: the clock above starts, not a day's wait"
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='ops'")
    assert tick() == "ops", "91 minutes after it was held, the manager writes with what is in"
    with store.transaction() as c:
        assert "No update from: " + updates._label(c, other) in c.execute(
            "SELECT m.body FROM update_queue q JOIN messages m ON m.id=q.message_id WHERE q.bot='ops'").fetchone()[0]
        updates.post(c, "ops", "- The team closed the books", day="2026-09-29")
    job("ops", "completed")
    assert tick() == "cpo", "the manager above is asked once its report posts"
    with store.transaction() as c:                 # a reports_to loop is followed once, never forever
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
    from backend.tests.test_api import restrict
    from backend.tests.test_assistant import say
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")
    post(api, "humans/ben", {"reports_to": "ana"})
    post(api, "humans/cara", {"update_draft": False})
    post(api, "tasks", {"owner": "human:ana", "title": "Ship the pricing page", "body": "x"})
    raise_plan = post(api, "tasks", {"owner": "human:ana", "title": "Secret raise plan", "body": "x", "private": True})
    done = post(api, "tasks", {"owner": "human:ana", "title": "Secret finished offer", "body": "x", "private": True})
    post(api, "tasks", {"owner": "human:ben", "title": "Secret ask of Ben", "body": "x", "private": True})
    audit = post(api, "tasks", {"owner": "bot:finance", "title": "Restricted audit", "body": "x"})
    budget = post(api, "tasks", {"owner": "bot:ops", "title": "Approve the ad budget", "body": "x"})
    say(api, "My salary talks are next week")             # her private Assistant chat
    store = api.app.state.store
    with store.transaction() as c:
        ask = H.say(c, "bot:ops", "human:ana", "Search or social first?", kind="ask",
                    conversation_id=H.task(c, budget["id"])["conversation_id"], refs={"task": budget["id"]})
        H.answer(c, "human:ana", ask["id"], "Search ads first")
        H.answer(c, "human:ana", H.say(c, "bot:ops", "human:ana", "Lunch?", kind="ask")["id"], "Chat answer, no task")
        # Her answer names the shared budget but replies on the private raise plan: every task it names must be shared.
        on_raise = H.say(c, "bot:ops", "human:ana", "Can the raise wait?", kind="ask", refs={"task": raise_plan["id"]},
                         conversation_id=H.task(c, raise_plan["id"])["conversation_id"])
        H.answer(c, "human:ana", on_raise["id"], "Mixed answer about the raise", comment_refs={"task": budget["id"]})
    for task in (budget, raise_plan, audit):               # her decisions and comments, on each kind of task
        post(api, f"tasks/{task['id']}/comments", {"text": "Comment on " + task["title"]})
        post(api, f"tasks/{task['id']}", {"version": get(api, f"tasks/{task['id']}")["task"]["version"], "status": "done"})
    meeting = lambda mid, notes, review="live", **meta: c.execute(
        "INSERT INTO meetings(id,title,owner_id,metadata,notes,created_at,updated_at,review_state) VALUES(?,?,?,?,?,?,?,?)",
        (mid, mid, "ben@acme.example", json.dumps({"kind": "meeting", **meta}), notes, H.now(), H.now(), review))
    with store.transaction() as c:
        c.execute("UPDATE tasks SET status='done', updated_at=? WHERE id=?", (H.now(), done["id"]))
        restrict(c, "finance", people=["ana"])
        H.task_create(c, "bot:finance", "Restricted request", "x", "human:ana")
        attended = {"calendar": {"attendees": ["ANA@acme.example"]}}
        meeting("Launch sync", "Agreed the launch date. " + "More detail. " * 80, **attended)
        meeting("Board prep", "Private board notes", private=True, **attended)
        meeting("Pending import", "Pending import notes", "pending", **attended)
        meeting("Old sync", "Deleted meeting notes", **attended)
        c.execute("INSERT INTO media_control(meeting_id,deleted_at) VALUES('Old sync',?)", (H.now(),))
        meeting("Ben's one-to-one", "Not her meeting notes", participants=[{"email": "cara@acme.example"}])
        c.execute("INSERT INTO approvals(id,kind,task_id,payload,requested_by_id,decided_by_id,decision,decided_at,created_at) "
                  "VALUES('ap1','send',?,?,'bot:ops','human:ana','approved',?,?)",
                  (budget["id"], json.dumps({"text": "Approval payload to the vendor"}), H.now(), H.now()))
        c.execute("UPDATE bot_config SET reports_to='human:ana' WHERE bot IN ('finance','ops')")
        updates.post(c, "ops", "- Ops shipped the runbook", day="2026-09-29")
        updates.post(c, "finance", "- Restricted numbers moved", day="2026-09-29")
        updates.build_queue(c, "2026-09-29", "daily", "coo")
        people = [r["bot"] for r in c.execute("SELECT bot FROM update_queue WHERE bot LIKE 'human:%' ORDER BY rank")]
        assert "human:cara" not in people, "drafting turns off per person"
        assert people.index("human:ben") < people.index("human:ana"), "a person's reports are drafted first"
        c.execute("UPDATE update_queue SET state='posted' WHERE bot NOT LIKE 'human:%'")
    post(api, "updates", {"body": "- Met two customers"}, token="ben-test")
    with store.transaction() as c:
        assert updates.dispatch(c, MORNING, "coo") == "human:ana", "ben wrote his own; ana's draft is next"
        assert updates.dispatch(c, MORNING, "coo") is None, "one at a time: nothing more while her draft is out"
    seen = []

    def write(system, user):
        seen.append(system + "\n" + user)
        return {"body": "- Shipped the pricing page"}
    draft = updates.write_draft(store, "human:ana", write, "coo")
    assert draft["bot"] == "human:ana" and draft["drafted_by"] == "coo"
    assert "Ship the pricing page" in seen[0] and "Ops shipped" in seen[0] and "Met two customers" in seen[0]
    for shared in ("Approve the ad budget: done", "Comment on Approve the ad budget", "Search ads first",
                   "Launch sync: Agreed the launch date"):
        assert shared in seen[0], shared + " is shared and belongs in the draft"
    assert len(seen[0].split("Launch sync: ")[1].split("\n")[0]) <= updates.MEETING_CHARS + 1, "a summary is trimmed"
    for secret in ("Secret raise", "Secret finished", "Secret ask", "Restricted audit", "Restricted request",
                   "Restricted numbers", "salary", "Private board", "Pending import", "Deleted meeting",
                   "Not her meeting", "Approval payload", "Chat answer", "Mixed answer"):
        assert secret not in seen[0], secret + " reached a company-wide draft"
    with store.transaction() as c:
        assert c.execute("SELECT state FROM update_queue WHERE bot='human:ana'").fetchone()[0] == "posted"

    def fails(system, user):
        raise OSError("down")
    with store.transaction() as c:
        c.execute("UPDATE update_queue SET state='sent', sent_at=? WHERE bot='human:ana'", (H.now(),))
    assert updates.write_draft(store, "human:ana", fails, "coo") is None
    with store.transaction() as c:
        assert c.execute("SELECT state, tries FROM update_queue WHERE bot='human:ana'").fetchone()[:] == ("queued", 1)
    post(api, "updates", {"body": "- Signed the lease", "replace_draft": True})
    with store.transaction() as c:
        c.execute("UPDATE update_queue SET state='sent', sent_at=? WHERE bot='human:ana'", (H.now(),))
    assert updates.write_draft(store, "human:ana", write, "coo") is None, "never over her own words"
    with store.read() as c:
        assert c.execute("SELECT body FROM updates WHERE bot='human:ana'").fetchone()[0] == "- Signed the lease"

    # Friday: a person's row is the week in review. Their own words, in either kind, close it.
    friday = "2026-10-02"
    monkeypatch.setattr(updates, "today", lambda at=None: friday)
    with store.transaction() as c:
        updates.build_queue(c, friday, "weekly", "coo")
        c.execute("UPDATE update_queue SET state='posted' WHERE day=? AND bot NOT LIKE 'human:%'", (friday,))
    post(api, "updates", {"body": "- Met the auditors", "kind": "daily"}, token="ben-test")
    with store.transaction() as c:
        c.execute("UPDATE update_queue SET state='sent', sent_at=? WHERE bot='human:ben' AND day=?", (H.now(), friday))
    calls = len(seen)
    assert updates.write_draft(store, "human:ben", write, "coo") is None and len(seen) == calls, "not even a model call"
    with store.transaction() as c:
        assert c.execute("SELECT state, reason FROM update_queue WHERE bot='human:ben' AND day=?",
                         (friday,)).fetchone()[:] == ("posted", updates.OWN)
        assert not c.execute("SELECT 1 FROM updates WHERE bot='human:ben' AND kind='weekly'").fetchone(), \
            "no week in review is drafted beside his own daily"
        try:
            updates.post(c, "human:ben", None, kind="weekly", day=friday, slides=SLIDES, drafted_by="coo")
            raise AssertionError("a draft landed beside his own words")
        except Problem as e:
            assert e.status == 409
        c.execute("UPDATE update_queue SET state='queued' WHERE bot='human:ben' AND day=?", (friday,))
        assert updates.dispatch(c, FRIDAY, "coo") == "human:ana", "the queue closes his row the same way"
        assert c.execute("SELECT reason FROM update_queue WHERE bot='human:ben' AND day=?", (friday,)).fetchone()[0] == updates.OWN
    # With no words of her own, a model answer of any shape (slides as a string) is asked again...
    assert updates.write_draft(store, "human:ana", lambda system, user: {"slides": "Shipped it"}, "coo") is None
    with store.transaction() as c:
        assert c.execute("SELECT state, tries FROM update_queue WHERE bot='human:ana' AND day=?",
                         (friday,)).fetchone()[:] == ("queued", 1)
        c.execute("UPDATE update_queue SET state='sent', sent_at=? WHERE bot='human:ana' AND day=?", (H.now(), friday))
    # ...and her draft is a week in review she edits, then replaces with her own words.
    week = updates.write_draft(store, "human:ana", lambda system, user: {"slides": SLIDES}, "coo")
    assert week["kind"] == "weekly" and week["drafted_by"] == "coo" and week["slides"]["goal"] == SLIDES["goal"]
    edited = post(api, "updates", {"kind": "weekly", "slides": {**SLIDES, "goal": "Grow signups; at 320."}})["update"]
    assert edited["id"] == week["id"] and edited["drafted_by"] == "coo" and edited["edited_by"] == "human:ana"
    own = post(api, "updates", {"kind": "weekly", "slides": SLIDES, "replace_draft": True})["update"]
    assert own["id"] == week["id"] and own["drafted_by"] is None and own["edited_by"] is None


def test_the_tree_leaves_out_the_helpers_behind_the_assistant(api, monkeypatch):
    """BotOps, the Strategy Bot, the Librarian and the Task Manager get no node (so no empty "No update yet" row) in
    either kind or scope; a bot below one hangs from the next node up, and an update one posted stays in the feed."""
    from backend.tests.test_bot_contact import bot
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")
    for slug, name in (("botops", "BotOps"), ("goal-manager", "Strategy Bot"), ("librarian", "Librarian"),
                       ("task-manager", "Task Manager")):
        bot(api, slug, name, reports_to="human:ana")
    bot(api, "scout", "Scout", reports_to="librarian")
    with api.app.state.store.transaction() as c:
        posted = updates.post(c, bot_id(c, "librarian"), "- Mapped the market", day="2026-09-29")
    keys = lambda nodes: [k for n in nodes for k in (n["key"], *keys(n["children"]))]
    node = lambda slug: "b:" + bid(api, slug)
    for path in ("updates/tree", "updates/tree?scope=all", "updates/tree?kind=weekly&scope=all"):
        tree = get(api, path)
        assert not {node("botops"), node("goal-manager"), node("librarian"), node("task-manager")} & set(keys(tree["roots"])), path
        assert node("scout") in [n["key"] for r in tree["roots"] for n in r["children"]], path + ": under the owner"
        assert "Mapped the market" not in json.dumps(tree), path
    assert posted["id"] in [u["id"] for u in get(api, "updates")["updates"]], "its own update stays in the feed"


def test_the_tree_nests_a_days_updates_by_the_org_chart_and_only_direct_reports_alert(api, monkeypatch):
    from backend.tests.test_bot_contact import bot
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
    find = lambda nodes, key: next((n for n in nodes if n["key"] == key), None) or next(
        (hit for n in nodes if (hit := find(n["children"], key))), None)
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
    from backend.tests.test_api import restrict
    with api.app.state.store.transaction() as c:
        restrict(c, "writer", people=["ana"])
    uid = boss["update"]["id"]
    for path in ("updates/tree?scope=all", "updates", f"updates/{uid}"):
        seen = get(api, path, token="cara-test")
        assert links[analyst_id] in json.dumps(seen) and links[writer_id] not in json.dumps(seen), path
        assert f'"{writer_id}"' not in json.dumps(seen), path + ": no hidden author either"
    assert get(api, f"updates/{uid}", token="cara-test")["update"]["inputs"] == \
        [{"id": links[analyst_id], "author": analyst_id}]
    assert len(get(api, f"updates/{uid}")["update"]["inputs"]) == 2, "the owner still sees both"
    direct = next(u for u in get(api, "updates?scope=direct")["updates"] if u["id"] == uid)
    assert len(direct["inputs"]) == 2, "showing only direct reports narrows the cards, not a card's links"
    # A bot Cara may not read is not in her tree at all; its report moves up under the next one she can see.
    with api.app.state.store.transaction() as c:
        restrict(c, "boss", people=["ana"])
    hers = get(api, "updates/tree?scope=all", token="cara-test")
    assert find(hers["roots"], "b:" + bid(api, "boss")) is None and "Planned the launch" not in str(hers)
    assert find(hers["roots"], "b:" + bid(api, "analyst"))["update"]["body"] == "- Sized the market"
