"""What the Strategy Bot does alone at level 3 and what it never does (backend/strategy_acts.py; spec: Head of Product,
Strategy Bot, tests 9, 12 and 15). A decision changes bot tasks under one action id, logged with its reason; the owner
undoes it without overwriting a later edit; anything on the owner's list, at any level, is refused on the server and
becomes a proposal in their Needs you, and so does a change a bot offline can't take."""

from urllib.parse import quote

from backend.store import H, encode
from backend.tests.test_api import api, get, headers, post  # noqa: F401
from backend.tests.test_strategy_bot_level import level
from backend.tests.test_strategy_bot_suggests import bot_task
from backend.tests.test_company import bot, with_strategy_bot
from backend.tests.test_kpis import goal_with_kpi

OWNER, STRATEGY_BOT = "human:ana", "bot:goal-manager"


def decide(api, token, changes, reason="Activation is red; setup work goes first", expected=200):
    return post(api, "strategy-bot/decisions", {"reason": reason, "changes": changes}, token=token, expected=expected)


def task(api, task_id):
    with api.app.state.store.read() as c:
        return H.task(c, task_id)


def proposals(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM tasks WHERE requester_id=? AND owner_id=? AND title LIKE 'Decide %' "
                                           "ORDER BY created_at", (STRATEGY_BOT, OWNER))]


def test_at_level_3_one_decision_changes_bot_tasks_under_one_action_id_and_the_owner_undoes_it(api):
    """Spec test 12: a decision made alone is one entry in the log with its reason, every task event it wrote carries
    its action id, and Undo puts the tasks back and tells their bots; a field a person changed since keeps their
    change, and the owner is told what wasn't restored. Below level 3, or for any other bot, nothing is decided."""
    strategy_token = with_strategy_bot(api)
    bot(api, "ops")
    bot(api, "finance")
    first = bot_task(api, "ops", "Write the onboarding email")
    second = bot_task(api, "ops", "Clean up old reports")
    stale = bot_task(api, "ops", "Draft the old pricing page")
    changes = [{"do": "create", "owner": "ops", "title": "Fix the setup step that drops new accounts"},
               {"do": "reorder", "task_id": second},
               {"do": "reassign", "task_id": first, "owner": "finance"},
               {"do": "close", "task_id": stale, "note": "Pricing moved to next quarter"}]
    level(api, 2)
    decide(api, strategy_token, changes, expected=422)                     # level 2 suggests; it decides nothing alone
    level(api, 3)
    decide(api, bot(api, "ops"), changes, expected=403)        # only the Strategy Bot decides alone
    made = decide(api, strategy_token, changes)
    assert made["state"] == "done" and not made["left"] and len(made["done"]) == 4
    assert task(api, first)["owner_id"] == "bot:finance" and task(api, stale)["status"] == "closed"
    new = made["done"][0]["task_id"]
    with api.app.state.store.read() as c:
        stamped = {r[0] for r in c.execute("SELECT DISTINCT task_id FROM task_events WHERE action_id=?", (made["id"],))}
    assert {first, second, stale} <= stamped

    log = get(api, "strategy-bot/log")
    assert log["items"][0]["id"] == made["id"] and log["items"][0]["reason"].startswith("Activation is red")
    assert log["can_undo"] is True
    post(api, f"strategy-bot/log/{made['id']}/undo", {}, token=strategy_token, expected=403)     # never the bot itself

    # A person moves the reassigned task on after the decision: Undo keeps their change and says so.
    with api.app.state.store.transaction() as c:
        H.task_update(c, OWNER, first, owner="bot:ops", mover=True)
        H.task_update(c, OWNER, first, owner="bot:finance", mover=True)
    undone = post(api, f"strategy-bot/log/{made['id']}/undo", {})
    assert undone["state"] == "undone"
    assert task(api, stale)["status"] == "open" and task(api, new)["status"] == "closed"
    assert task(api, first)["owner_id"] == "bot:finance"                  # the person's later edit stays
    assert "owner was changed since" in undone["undo"]["text"]
    with api.app.state.store.read() as c:
        told = c.execute("SELECT COUNT(*) FROM messages m WHERE m.sender_id=? AND m.body LIKE '%undid a decision%'",
                         (H.KEEPER,)).fetchone()[0]
    assert told >= 2
    post(api, f"strategy-bot/log/{made['id']}/undo", {}, expected=422)       # once
    # The same reorder or close again is the owner's: their Undo stands, as a proposal, not a second decision.
    again = decide(api, strategy_token, [{"do": "reorder", "task_id": second}, {"do": "close", "task_id": stale}])
    assert again["state"] == "proposed" and not again["done"] and len(again["left"]) == 2
    assert "owner undid this same change" in again["left"][0]["why"] and task(api, stale)["status"] == "open"


def test_a_change_a_bot_offline_or_a_person_would_take_becomes_one_proposal_never_dropped(api):
    """Spec test 15 and the person's task of test 9: what the decision can't make alone is one proposal in the owner's
    Needs you with the reason in plain words, and the rest still happens."""
    strategy_token = with_strategy_bot(api)
    bot(api, "ops")
    level(api, 3)
    paused = bot_task(api, "ops", "Rewrite the welcome page")
    with api.app.state.store.transaction() as c:
        H.task_create(c, "bot:finance", "Close the books", "Work it.", "bot:finance", lint=False)
        c.execute("UPDATE bots SET state='paused' WHERE slug='finance'")
    made = decide(api, strategy_token, [{"do": "reorder", "task_id": paused},
                             {"do": "create", "owner_id": "finance", "title": "Pause the signup campaign"},
                             {"do": "create", "owner_id": OWNER, "title": "Call the three biggest customers"}])
    assert made["state"] == "done" and len(made["done"]) == 1 and len(made["left"]) == 2
    asked = proposals(api)
    assert len(asked) == 1 and asked[0]["id"] == made["proposal_task"]
    assert "is paused right now" in asked[0]["body"] and "a task for a person stays the person's" in asked[0]["body"]


def test_everything_on_the_owners_list_is_refused_on_the_server_and_proposed_instead(api):
    """Spec test 9, one case per kind, at level 3 where the Strategy Bot does most: a cost, a publish, an outside send's
    invitation, a bot added or removed, a goal or target change, the vision, a bot's instructions and a person's
    task. Each is refused (409 `proposed`), changes nothing, is logged and offered to the owner, and is not counted as
    one of the bot's mistakes."""
    strategy_token = with_strategy_bot(api)
    bot(api, "ops")
    level(api, 3)
    goal, kpi = goal_with_kpi(api)
    with api.app.state.store.transaction() as c:          # a task it asked for, so it may otherwise change it
        mine = H.task_create(c, STRATEGY_BOT, "Write the onboarding email", "Work it.", "bot:ops", lint=False)["id"]
    tries = [
        ("cost", "bots/ops/model", {"model": "opus", "expected_revision": 1}),
        ("publish", "tasks", {"title": "Share the roadmap", "body": "Publish it.", "owner_id": "bot:ops",
                              "visibility": "public", "public_title": "Our roadmap"}),
        ("send", "calendar/appointments", {"calendar": "ana@acme.example", "title": "Intro call",
                                           "start": "2099-01-01T09:00:00+00:00", "end": "2099-01-01T09:30:00+00:00",
                                           "attendees": ["buyer@other.example"]}),
        ("bots", "bots/ops/archive", {"expected_revision": 1}),
        ("target", f"goals/{goal['id']}/kpis/{kpi['id']}", {"kind": "improve", "baseline": 40, "target": 50,
                                                           "deadline": "2099-12-31"}),
        ("vision", "company", {"vision": "Rule the world"}),
        ("instructions", "bots/ops/definition", {"description": "Only ever work on growth",
                                                   "expected_revision": 1}),
        ("person_task", f"tasks/{mine}", {"owner_id": OWNER, "version": task(api, mine)["version"]}),
    ]
    for kind, path, body in tries:
        r = api.post("/api/v2/" + path, json=body, headers=headers(strategy_token))
        assert r.status_code == 409 and "proposal" in r.text, (kind, r.text)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM bots WHERE slug='ops'").fetchone()[0] == "active"
        assert H.task(c, mine)["owner_id"] == "bot:ops"
        assert not c.execute("SELECT 1 FROM calendar_actions").fetchone()
        assert not c.execute("SELECT 1 FROM refusals WHERE actor=?", (STRATEGY_BOT,)).fetchone()
    log = get(api, "strategy-bot/log")["items"]
    assert [x["kind"] for x in log][::-1] == [k for k, _, _ in tries] and all(x["state"] == "proposed" for x in log)
    # Each is in the owner's Needs you: there is no daily limit.
    assert proposals(api) and all(x["proposal_task"] for x in log)
    # Each item is private to the owner (and the Strategy Bot that asks): it names tasks by their titles, so a person
    # who reads the owner's work but is not the owner can't open it, find it in a list, or read it in the log.
    asked = proposals(api)
    assert asked and all(x["private"] for x in asked)
    listed = str(get(api, "tasks?owner=ana", token="cara-test"))
    for item in asked:
        r = api.get(f"/api/v2/tasks/{item['id']}", headers=headers("cara-test"))
        assert r.status_code == 404 and item["title"] not in r.text, r.text
        assert item["id"] not in listed and item["title"] not in listed
    assert not any(x["undoable"] for x in log)


def test_undo_never_overwrites_a_later_edit_and_puts_back_everything_else(api):
    """Spec test 12, Undo's edges: a later move in the queue (even one back to where the decision put it), a task it
    made that a person took on, two changes to one task, a close on a steps-only ticket from its second open step with
    a note, and a task whose restore is refused. Each later edit stays and is named; the rest goes back; a form holding
    the task from before the decision or the Undo gets a version conflict."""
    strategy_token = with_strategy_bot(api)
    for slug in ("ops", "finance", "coo"):
        bot(api, slug)
    level(api, 3)
    typ = post(api, "task-types", {"name": "Dev ticket", "steps_only": True, "bots": "work", "steps": [
        {"name": "Backlog", "status": "open"}, {"name": "On Deck", "status": "open"},
        {"name": "Done", "status": "closed"}]})["type"]
    ticket = post(api, "tasks", {"owner_id": "ops", "title": "Fix the guest message times", "body": "Please.",
                                 "type": typ["id"], "step": "On Deck"})["id"]
    with api.app.state.store.transaction() as c:
        H.task_update(c, OWNER, ticket, note="Waiting on the copy")
    moved, back, twice, refused = (bot_task(api, "ops", t) for t in (
        "Write the onboarding email", "Clean up old reports", "Draft the pricing page", "Plan the launch"))
    bot_task(api, "ops", "Answer the support inbox")
    bot_task(api, "coo", "Review the budget")
    with api.app.state.store.transaction() as c:
        H.task_update(c, OWNER, refused, owner="bot:coo", mover=True)
    start = {t: task(api, t) for t in (moved, back, twice, refused, ticket)}
    made = decide(api, strategy_token, [{"do": "reassign", "task_id": moved, "owner_id": "finance"},
                             {"do": "reassign", "task_id": back, "owner_id": "finance"},
                             {"do": "reorder", "task_id": twice}, {"do": "reorder", "task_id": twice},
                             {"do": "reassign", "task_id": refused, "owner_id": "finance"},
                             {"do": "close", "task_id": ticket},
                             {"do": "create", "owner_id": "ops", "title": "Fix the setup step that drops new accounts"}])
    assert made["state"] == "done" and len(made["done"]) == 7, made["left"]
    post(api, f"tasks/{moved}", {"version": start[moved]["version"], "due_at": "2099-01-01T00:00:00+00:00"},
         expected=409)
    new = made["done"][-1]["task_id"]
    with api.app.state.store.transaction() as c:
        H.task_update(c, OWNER, moved, rank=-50, mover=True)                       # a person reorders it
        decided = H.task(c, back)["rank"]
        H.task_update(c, OWNER, back, rank=-60, mover=True)                        # ...and away and back
        H.task_update(c, OWNER, back, rank=decided, mover=True)
        H.task_update(c, OWNER, new, owner=OWNER, status="doing", mover=True)      # a person takes on what it made
        c.execute("UPDATE bot_config SET config=? WHERE bot='coo'", ('{"harness": "dots"}',))
    held = task(api, ticket)["version"]
    post(api, f"strategy-bot/log/{made['id']}/undo", {}, token="ben-test", expected=403)        # an admin doesn't undo
    undone = post(api, f"strategy-bot/log/{made['id']}/undo", {})
    post(api, f"tasks/{ticket}", {"version": held, "due_at": "2099-01-01T00:00:00+00:00"}, expected=409)
    text = undone["undo"]["text"]
    now = {t: task(api, t) for t in (moved, back, twice, refused, ticket, new)}
    # The person's place in the queue stays, and the owner still goes back.
    assert now[moved]["owner_id"] == "bot:ops" and now[moved]["rank"] == -50
    assert now[back]["owner_id"] == "bot:ops" and now[back]["rank"] == decided
    assert '"Write the onboarding email": its place in the queue was changed since by' in text
    assert '"Clean up old reports": its place in the queue was changed since by' in text
    # Two moves of one task go back to where it started, with no later edit claimed.
    assert now[twice]["rank"] == start[twice]["rank"] and '"Draft the pricing page": its' not in text.split("Not")[-1]
    # The ticket is back in its own step, place and note.
    assert (now[ticket]["status"], now[ticket]["step_id"], now[ticket]["note"]) == (
        "open", start[ticket]["step_id"], "Waiting on the copy")
    assert now[ticket]["step_rank"] == start[ticket]["step_rank"] and not now[ticket]["closed_at"]
    # What a person took on stays theirs and open; a refused restore is said and the rest still went back.
    assert (now[new]["owner_id"], now[new]["status"]) == (OWNER, "doing") and "which it made, was changed since" in text
    assert now[refused]["owner_id"] == "bot:finance" and '"Plan the launch" could not be put back' in text
    with api.app.state.store.read() as c:
        told = {r[0] for r in c.execute("SELECT recipient_id FROM messages WHERE sender_id=? AND body LIKE "
                                        "'%undid a decision%'", (H.KEEPER,))}
    assert {"bot:ops", "bot:finance"} <= told


def test_a_private_task_never_reaches_the_strategy_bot_log_or_the_owners_proposal(api):
    """A decision naming a private task the Strategy Bot can't read is refused before anything about that task is
    written; a private task it can read is changed, but its title and body stay out of the company-wide log and the
    owner's proposal, which people who can't read the task also read."""
    strategy_token = with_strategy_bot(api)
    bot(api, "ops")
    level(api, 3)
    from backend.tests.test_api import unrelated_task_managers
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
    with api.app.state.store.transaction() as c:
        hidden = H.task_create(c, OWNER, "Fire the vendor quietly", "Secret body", OWNER, private=True,
                               lint=False)["id"]
        unseen = H.task_create(c, "bot:ops", "Acquire Initech", "Secret plan", "bot:ops", private=True,
                               lint=False)["id"]
        mine = H.task_create(c, STRATEGY_BOT, "Merge with Globex", "Secret terms", "bot:ops", private=True,
                             lint=False)["id"]
    since = H.now()
    for name in (hidden, unseen):
        r = api.post("/api/v2/strategy-bot/decisions", headers=headers(strategy_token), json={
            "reason": "Activation is red", "changes": [{"do": "reorder", "task_id": name},
                                                       {"do": "create", "owner_id": "ops", "title": "Fix setup"}]})
        assert r.status_code == 422 and r.json()["error"]["code"] == "not-found", r.text
        assert "Fire the vendor" not in r.text and "Acquire Initech" not in r.text
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM strategy_actions").fetchone()
        assert not c.execute("SELECT 1 FROM tasks WHERE title='Fix setup'").fetchone()
        for table in ("events", "refusals"):
            dump = " ".join(str(tuple(r)) for r in c.execute(f"SELECT * FROM {table} WHERE ts>?", (since,)))
            assert "Fire the vendor" not in dump and "Acquire Initech" not in dump, table

    # A private task the Strategy Bot can read is still not changed alone: its close note would reach the company-wide
    # log, and Undo, made as the owner, could not write it back. Each change becomes the owner's proposal instead.
    before = task(api, mine)
    made = decide(api, strategy_token, [
        {"do": "reorder", "task_id": mine},
        {"do": "reassign", "task_id": mine, "owner_id": OWNER},
        {"do": "close", "task_id": mine, "note": "Globex walked away from the merger"}])
    assert made["state"] == "proposed" and not made["done"] and len(made["left"]) == 3
    after = task(api, mine)
    assert (after["status"], after["owner_id"], after["rank"]) == (before["status"], before["owner_id"], before["rank"])
    seen = str(get(api, "strategy-bot/log")) + str(proposals(api))
    for secret in ("Merge with Globex", "Secret terms", "Globex walked away"):
        assert secret not in seen, secret
    r = api.post(f"/api/v2/strategy-bot/log/{made['id']}/undo", headers=headers(), json={})
    assert r.status_code == 422 and "Merge with Globex" not in r.text, r.text


def test_undo_leaves_open_a_task_it_made_whose_title_or_body_was_edited_since(api):
    """A task the decision made and a person reworded since is theirs now: Undo keeps it open and says why."""
    strategy_token = with_strategy_bot(api)
    bot(api, "ops")
    level(api, 3)
    made = decide(api, strategy_token, [{"do": "create", "owner_id": "ops", "title": "Fix the setup step"},
                             {"do": "create", "owner_id": "ops", "title": "Write the release note"}])
    titled, bodied = (x["task_id"] for x in made["done"])
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:ops", titled, title="Fix the setup step and the signup form")
        H.task_update(c, "bot:ops", bodied, body="Cover the new billing page too.")
    undone = post(api, f"strategy-bot/log/{made['id']}/undo", {})
    assert task(api, titled)["status"] != "closed" and task(api, bodied)["status"] != "closed"
    assert undone["undo"]["text"].count("which it made, was changed since") == 2, undone["undo"]["text"]


def test_a_decision_never_makes_a_task_private_and_undo_puts_back_one_made_private_since(api):
    """Giving a company task to a bot that keeps its tasks private would make it private, out of the owner's reach:
    that reassign is the owner's proposal. A task made private after the decision (moved under a private task) is
    still put back by Undo, from what it was before the decision, not left with the bot the decision gave it to."""
    strategy_token = with_strategy_bot(api)
    for slug in ("ops", "finance"):
        bot(api, slug)
    level(api, 3)
    with api.app.state.store.transaction() as c:
        config = H._json(c.execute("SELECT config FROM bot_config WHERE bot='finance'").fetchone()[0], {})
        c.execute("UPDATE bot_config SET config=? WHERE bot='finance'",
                  (encode({**config, "private_tasks_default": True}), ))
    work = bot_task(api, "ops", "Reconcile the vendor invoices")
    held = decide(api, strategy_token, [{"do": "reassign", "task_id": work, "owner_id": "finance"}])
    assert held["state"] == "proposed" and "keeps its tasks private" in held["left"][0]["why"]
    assert task(api, work)["owner_id"] == "bot:ops" and not task(api, work)["private"]

    bot(api, "coo")
    made = decide(api, strategy_token, [{"do": "reassign", "task_id": work, "owner_id": "coo"}])
    assert made["state"] == "done" and "before" not in made["done"][0]        # the owner's log keeps the fields
    assert get(api, "strategy-bot/log")["items"][0]["done"][0]["before"]["private"] == 0
    with api.app.state.store.transaction() as c:          # made private since, by someone else
        c.execute("UPDATE tasks SET private=1, visibility='private' WHERE id=?", (work,))
    undone = post(api, f"strategy-bot/log/{made['id']}/undo", {})
    assert task(api, work)["owner_id"] == "bot:ops", undone["undo"]["text"]
    assert "its owner" in undone["undo"]["text"] and "could not be put back" not in undone["undo"]["text"]


def test_a_decision_never_creates_a_task_for_a_bot_that_keeps_its_tasks_private(api):
    """A new task for a bot that keeps its tasks private would be private from the start: the owner could neither read
    nor close it, and Undo could not take it back once the bot edited it. Refused as the reassign is, before anything
    is made, and proposed to the owner instead."""
    strategy_token = with_strategy_bot(api)
    bot(api, "finance")
    level(api, 3)
    with api.app.state.store.transaction() as c:
        config = H._json(c.execute("SELECT config FROM bot_config WHERE bot='finance'").fetchone()[0], {})
        c.execute("UPDATE bot_config SET config=? WHERE bot='finance'",
                  (encode({**config, "private_tasks_default": True}), ))
    held = decide(api, strategy_token, [{"do": "create", "owner_id": "finance", "title": "Review the vendor contract"}])
    assert held["state"] == "proposed" and not held["done"]
    assert "keeps its tasks private" in held["left"][0]["why"]
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE owner_id='bot:finance'").fetchone()


def test_the_strategy_bot_publishes_only_with_a_real_approval_whatever_the_team_setting(api):
    """With bot approvals for public changes off, any approval id once let the Strategy Bot publish: the id is
    checked."""
    strategy_token = with_strategy_bot(api)
    bot(api, "ops")
    level(api, 3)
    post(api, "roadmaps/settings", {"bot_public_approval": False})
    with api.app.state.store.transaction() as c:
        mine = H.task_create(c, STRATEGY_BOT, "Write the onboarding email", "Work it.", "bot:ops", lint=False)["id"]
    r = api.post(f"/api/v2/tasks/{mine}", headers=headers(strategy_token), json={
        "visibility": "public", "public_title": "Onboarding", "approval_id": "made-up",
        "version": task(api, mine)["version"]})
    assert r.status_code >= 400 and "approval" in r.text, r.text
    assert task(api, mine)["visibility"] != "public"


def test_only_the_owner_reads_the_log_and_it_names_a_task_as_it_is_now(api):
    """An entry shows bot task activity (owners, status, queue places) a person or bot may not be allowed to read, so
    only the owner reads the log: a member, an admin, a bot and the Strategy Bot itself are refused, and the Strategy
    Bot's own decision comes back without the task fields Undo keeps. The owner reads each line named as the task is
    now: a task turned private after the decision, which the owner is not a party to, shows no title, to them or in the
    Undo."""
    strategy_token = with_strategy_bot(api)
    bot(api, "ops")
    level(api, 3)
    from backend.tests.test_api import unrelated_task_managers
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
    with api.app.state.store.transaction() as c:
        merge = H.task_create(c, STRATEGY_BOT, "Merge with Globex", "Secret terms", "bot:ops", lint=False)["id"]
        report = H.task_create(c, STRATEGY_BOT, "Write the board report", "Numbers", "bot:ops", lint=False)["id"]
    made = decide(api, strategy_token, [{"do": "reorder", "task_id": merge},
                             {"do": "close", "task_id": report, "note": "Moved to next month"}])
    assert made["state"] == "done" and "Merge with Globex" in str(made)     # the Strategy Bot reads its own work
    assert not any("before" in x or "after" in x for x in made["done"])
    for token in ("ben-test", "cara-test", strategy_token, bot(api, "ops")):
        r = api.get("/api/v2/strategy-bot/log", headers=headers(token))
        assert r.status_code == 403 and "Merge with Globex" not in r.text, (token, r.text)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1, visibility='private' WHERE id=?", (merge,))
    owner = str(get(api, "strategy-bot/log"))
    assert "Merge with Globex" not in owner and "Secret terms" not in owner and "a task you can't open" in owner
    assert "Write the board report" in owner and "Moved to next month" in owner
    undone = str(post(api, f"strategy-bot/log/{made['id']}/undo", {}))
    assert "Merge with Globex" not in undone and "Write the board report" in undone


def test_the_log_pages_through_entries_made_in_the_same_instant(api):
    """Older pages start after the last entry shown, by time and then id: no entry is skipped or shown twice, so an
    entry past the first page is there to undo."""
    with_strategy_bot(api)
    at = H.now()
    with api.app.state.store.transaction() as c:
        for n in range(53):
            c.execute("INSERT INTO strategy_actions (id, created_at, actor, kind, reason, state, changes) "
                      "VALUES (?, ?, ?, 'decision', 'Same instant', 'proposed', '{}')", (f"a{n:02d}", at, STRATEGY_BOT))
    seen, before = [], None
    while True:
        page = get(api, "strategy-bot/log" + (f"?before={quote(before)}" if before else ""))
        seen += [x["id"] for x in page["items"]]
        before = page["next_before"]
        if not before:
            break
    assert sorted(seen) == [f"a{n:02d}" for n in range(53)] and len(seen) == len(set(seen))


def test_undo_keeps_a_guest_given_the_task_since_and_says_why_its_owner_stays(api):
    """A new owner drops a task's guest, so a reassign whose task has a guest now keeps its owner, and Undo says so."""
    strategy_token = with_strategy_bot(api)
    for slug in ("ops", "coo"):
        bot(api, slug)
    level(api, 3)
    work = bot_task(api, "ops", "Reconcile the vendor invoices")
    made = decide(api, strategy_token, [{"do": "reassign", "task_id": work, "owner_id": "coo"}])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1, visibility='private', guest='human:ben' WHERE id=?", (work,))
    undone = post(api, f"strategy-bot/log/{made['id']}/undo", {})
    after = task(api, work)
    assert after["owner_id"] == "bot:coo" and after["guest"] == "human:ben"
    assert "has a guest now, so its owner stays" in undone["undo"]["text"], undone["undo"]["text"]


def test_a_decision_never_touches_a_company_task_hidden_from_the_strategy_bot_by_its_bots_access(api):
    """A company task a person asked a bot for is hidden from the Strategy Bot when it may not read that bot
    (auth.task_sql). Reordering, reassigning or closing it is refused as a task that isn't there, before anything is
    written; giving a task to that bot, new or moved, is the owner's proposal and changes nothing."""
    strategy_token = with_strategy_bot(api)
    bot(api, "ops")
    bot(api, "finance")
    level(api, 3)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET access=? WHERE bot='finance'",
                  ('{"see":{"everyone":true},"read":{"people":["ana"]},"write":{"people":["ana"]}}',))
        hidden = H.task_create(c, OWNER, "Restructure the payroll", "Secret salaries", "bot:finance", lint=False)["id"]
    assert not task(api, hidden)["private"]
    before = task(api, hidden)
    for change in ({"do": "reorder", "task_id": hidden}, {"do": "reassign", "task_id": hidden, "owner_id": "ops"},
                   {"do": "close", "task_id": hidden, "note": "Not needed"}):
        r = api.post("/api/v2/strategy-bot/decisions", headers=headers(strategy_token),
                     json={"reason": "Activation is red", "changes": [change]})
        assert r.status_code == 422 and r.json()["error"]["code"] == "not-found", (change, r.text)
        assert "Restructure the payroll" not in r.text and "Secret salaries" not in r.text
    after = task(api, hidden)
    assert (after["owner_id"], after["status"], after["rank"]) == (before["owner_id"], before["status"], before["rank"])
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM strategy_actions").fetchone()

    work = bot_task(api, "ops", "Reconcile the vendor invoices")
    held = decide(api, strategy_token, [{"do": "reassign", "task_id": work, "owner_id": "finance"},
                             {"do": "create", "owner_id": "finance", "title": "Review the payroll"}])
    assert held["state"] == "proposed" and not held["done"] and len(held["left"]) == 2
    assert task(api, work)["owner_id"] == "bot:ops"
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE title='Review the payroll'").fetchone()


def test_a_bot_the_strategy_bot_cant_see_is_answered_as_a_bot_that_isnt_there(api):
    """docs/permissions.md: a bot the caller can't see does not exist for them. Giving it a task, new or moved, is
    answered word for word as for a slug no bot has, and neither the receipt nor the owner's Needs you item, which
    the Strategy Bot reads, carries its display name."""
    strategy_token = with_strategy_bot(api)
    bot(api, "ops")
    bot(api, "finance")
    level(api, 3)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET display_name='Payroll Vault' WHERE slug='finance'")
        c.execute("UPDATE bot_config SET access=? WHERE bot='finance'",
                  ('{"see":{"people":["ana"]},"read":{"people":["ana"]},"write":{"people":["ana"]}}',))
    work = bot_task(api, "ops", "Reconcile the vendor invoices")

    def left(owner):
        made = decide(api, strategy_token, [{"do": "reassign", "task_id": work, "owner_id": owner},
                                 {"do": "create", "owner_id": owner, "title": "Review the payroll"}])
        assert made["state"] == "proposed" and not made["done"], made
        body = task(api, made["proposal_task"])["body"]
        return made, [(x["text"], x["why"]) for x in made["left"]], body

    hidden, hidden_left, hidden_body = left("finance")
    assert "Payroll Vault" not in str(hidden) and "Payroll Vault" not in hidden_body
    assert "Unknown recipient" not in str(hidden) and "Unknown recipient" not in hidden_body
    with api.app.state.store.transaction() as c:          # answered, so the next one is asked again
        H.task_close(c, OWNER, hidden["proposal_task"], "Seen")
    _, missing_left, missing_body = left("nobody")       # the same words for a slug no bot has
    assert hidden_left == [(t.replace("nobody", "finance"), w.replace("nobody", "finance")) for t, w in missing_left]
    assert hidden_body == missing_body.replace("nobody", "finance"), (hidden_body, missing_body)
    assert task(api, work)["owner_id"] == "bot:ops"


def test_undo_puts_back_a_decision_logged_before_a_tasks_column_was_renamed(api, monkeypatch):
    # A decision keeps each task's fields before and after, keyed by the tasks columns of the release that made it.
    # After a column rename (a later wave of backend/migrations/renames_data.py), Undo reads them under the new name;
    # under the old one it could not find what to put back.
    import json
    from backend.migrations import renames
    strategy_token = with_strategy_bot(api)
    bot(api, "ops")
    bot(api, "finance")
    first = bot_task(api, "ops", "Write the onboarding email")
    level(api, 3)
    made = decide(api, strategy_token, [{"do": "reassign", "task_id": first, "owner_id": "finance"}])
    assert task(api, first)["owner_id"] == "bot:finance"
    monkeypatch.setattr(renames, "COLUMNS", (*renames.COLUMNS, ("tasks", "owner_before_rename", "owner", "9")))
    with api.app.state.store.transaction() as c:
        changes = json.loads(c.execute("SELECT changes FROM strategy_actions WHERE id=?", (made["id"],)).fetchone()[0])
        for side in ("before", "after"):
            changes["done"][0][side]["owner_before_rename"] = changes["done"][0][side].pop("owner_id")
        c.execute("UPDATE strategy_actions SET changes=? WHERE id=?", (json.dumps(changes), made["id"]))
    assert post(api, f"strategy-bot/log/{made['id']}/undo", {})["state"] == "undone"
    assert task(api, first)["owner_id"] == "bot:ops"
