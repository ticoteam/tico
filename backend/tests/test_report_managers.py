"""A manager bot changes its direct reports as itself (backend/report_managers.py, docs/permissions.md): its model, its task
model, and its name and description, each recorded with via `report_manager`. The owner's team rule and the manager's own
switch turn that off. A person who manages the report sees and undoes such a change. Routines and the hub tools follow it.
"""

import uuid
from contextlib import contextmanager

from backend import automations
from backend import hubdb as H
from backend import report_managers
from backend.auth import Identity
from backend.store import encode
from backend.tests.test_api import api, claim, get, headers, post, put  # noqa: F401  (api is a fixture)
from backend.tests.test_botops_parity import act
from backend.tests.test_mcp import call
from backend.tests.test_member_bots import botops, finish  # noqa: F401  (botops is a fixture)

HAIKU = {"harness": "claude", "model": "claude-haiku-5-5", "effort": "low"}


def bot(api, slug, reports_to=None, operator="ana"):
    """A bot the owner makes; the returned token is its agent credential, which acts as the bot."""
    post(api, "bots", {"slug": slug, "display_name": slug.title(), "description": "A test bot.",
                       "reports_to": reports_to, "status": "active", "repo": "emp-" + slug, "thread_mode": "personal",
                       "model": "hermes-profile", "effort": "as-configured", "harness": "hermes", "operator": operator,
                       "owners": ["ana"], "runner_id": None})
    return post(api, f"bots/{slug}/agent-credential", {})["token"]


def revision(api, slug, token="ana-test"):
    return get(api, f"bots/{slug}", token=token)["revision"]


def bot_id(api, slug):
    """The id the store keys a bot by. A test names a bot by its slug, which is only its label."""
    with api.app.state.store.read() as c:
        return H.resolve_bot(c, slug)["id"]


def task_model(api, token, slug="counsel", expected=200):
    return post(api, f"bots/{slug}/task-model", {"task_model": HAIKU, "expected_revision": revision(api, slug)},
                token=token, expected=expected)


def changes_to(api, bot_slug, field):
    bid = bot_id(api, bot_slug)
    return [row for row in get(api, "settings/history")["changes"] if row["bot"] == bid and row["field"] == field]


def test_a_manager_bot_changes_its_reports_task_model_as_itself(api):
    lead = bot(api, "lead")
    bot(api, "counsel", reports_to="lead")
    task_model(api, lead)
    [row] = changes_to(api, "counsel", "task_model")
    assert row["via"] == "report_manager" and row["actor"] == "bot:" + bot_id(api, "lead")
    assert row["after"]["model"] == "claude-haiku-5-5"


def test_a_manager_bot_changes_only_its_reports_name_and_description(api):
    lead = bot(api, "lead")
    bot(api, "counsel", reports_to="lead")
    post(api, "bots/counsel/definition", {"display_name": "Counsel Two", "description": "Reviews the NDAs.",
                                          "expected_revision": revision(api, "counsel")}, token=lead)
    [row] = changes_to(api, "counsel", "identity")
    assert row["via"] == "report_manager"
    assert row["after"] == {"display_name": "Counsel Two", "description": "Reviews the NDAs."}
    assert get(api, "bots/counsel")["display_name"] == "Counsel Two"
    # Its status and its place in the org stay the people's to change.
    post(api, "bots/counsel/definition", {"status": "paused", "expected_revision": revision(api, "counsel")},
         token=lead, expected=403)
    post(api, "bots/counsel/definition", {"reports_to": None, "expected_revision": revision(api, "counsel")},
         token=lead, expected=403)


def test_a_manager_bot_changes_only_its_direct_reports(api):
    lead = bot(api, "lead")
    bot(api, "counsel", reports_to="lead")
    bot(api, "junior", reports_to="counsel")
    bot(api, "stranger")
    for slug in ("junior", "lead", "stranger"):
        task_model(api, lead, slug=slug, expected=403)
    assert changes_to(api, "junior", "task_model") == []


def test_the_owners_team_rule_turns_the_managers_off_for_every_bot(api):
    lead = bot(api, "lead")
    bot(api, "counsel", reports_to="lead")
    put(api, "access/rules", {"managers_manage_reports": False})
    task_model(api, lead, expected=403)
    put(api, "access/rules", {"managers_manage_reports": False}, token="ben-test", expected=403)
    put(api, "access/rules", {"managers_manage_reports": True})
    task_model(api, lead)


def test_a_managers_own_switch_is_set_by_a_person_who_manages_it(api):
    lead = bot(api, "lead")
    bot(api, "counsel", reports_to="lead")

    def switch(value, token="ana-test", expected=200):
        return post(api, "bots/lead/manages-reports", {"manages_reports": value,
                    "expected_revision": revision(api, "lead")}, token=token, expected=expected)

    assert switch(False)["manages_reports"] is False
    task_model(api, lead, expected=403)
    switch(True, token=lead, expected=403)              # a bot cannot set its own switch
    switch(True, token="cara-test", expected=403)       # nor can someone who does not manage it
    switch(True)
    task_model(api, lead)
    assert [row["after"] for row in changes_to(api, "lead", "manages_reports")] == [True, False]   # newest first


def test_a_person_who_manages_the_report_sees_and_undoes_the_managers_change(api):
    lead = bot(api, "lead", reports_to="human:ben")
    bot(api, "counsel", reports_to="lead")
    task_model(api, lead)
    counsel = bot_id(api, "counsel")
    [row] = [c for c in get(api, "settings/history", token="ben-test")["changes"] if c["bot"] == counsel]
    assert row["via"] == "report_manager" and row["can_undo"] is True
    assert get(api, "settings/history", token="ben-test")["transitions"] == []
    assert get(api, "settings/history", token="cara-test")["changes"] == []
    undo = f"settings/history/{row['id']}/undo"
    post(api, undo, {"expected_revision": revision(api, "counsel")}, token="cara-test", expected=403)
    post(api, undo, {"expected_revision": revision(api, "counsel", "ben-test")}, token="ben-test")
    assert get(api, "bots/counsel")["task_model"] is None
    assert [r["undone_at"] is not None for r in changes_to(api, "counsel", "task_model") if r["id"] == row["id"]] == [True]


def test_a_routine_a_manager_bot_reaches_follows_both_switches(api):
    lead = bot(api, "lead")
    bot(api, "counsel", reports_to="lead")
    store = api.app.state.store
    flow, who = {"owner": "bot:" + bot_id(api, "counsel")}, Identity("bot:" + bot_id(api, "lead"), "bot")
    with store.read() as c:
        assert automations.may_change(c, who, flow)
    post(api, "bots/lead/manages-reports", {"manages_reports": False, "expected_revision": revision(api, "lead")})
    with store.read() as c:
        assert not automations.may_change(c, who, flow)
    post(api, "bots/lead/manages-reports", {"manages_reports": True, "expected_revision": revision(api, "lead")})
    team_off = {"managers_manage_reports": False}
    put(api, "access/rules", team_off)
    with store.read() as c:
        assert not automations.may_change(c, who, flow)


def test_the_hub_tools_and_the_model_route_work_for_a_manager_bot(api):
    lead = bot(api, "lead")
    bot(api, "counsel", reports_to="lead")
    err, out = call(api, "hub_bot_update", {"slug": "counsel", "display_name": "Counsel Lead",
                                             "description": "Reviews contracts."}, token=lead)
    assert not err, out
    assert changes_to(api, "counsel", "identity")[0]["via"] == "report_manager"
    # The model route: the manager changes its report's model (no computer yet, so nothing else blocks it). A bot that
    # manages no one else is refused.
    stranger = bot(api, "stranger")
    body = {"model": "claude-haiku-5-5", "expected_revision": revision(api, "counsel")}
    r = api.post("/api/v2/bots/counsel/model", json=body, headers=headers(stranger))
    assert r.status_code == 403, r.text
    r = api.post("/api/v2/bots/counsel/model", json=body, headers=headers(lead))
    assert r.status_code == 200, r.text
    assert any(row["via"] == "report_manager" and row["after"]["model"] == "claude-haiku-5-5"
               for row in changes_to(api, "counsel", "model"))
    err, out = call(api, "hub_bot_model", {"bot": "counsel", "model": "claude-sonnet-5-5"}, token=lead)
    assert not err, out
    assert any(row["after"]["model"] == "claude-sonnet-5-5" and row["via"] == "report_manager"
               for row in changes_to(api, "counsel", "model"))
    # Its task model too, through the tool; a task's own model stays a person's, so the manager cannot set one.
    err, out = call(api, "hub_bot_task_model", {"bot": "counsel", "model": "claude-haiku-5-5"}, token=lead)
    assert not err, out
    assert any(row["via"] == "report_manager" for row in changes_to(api, "counsel", "task_model"))
    task = post(api, "tasks", {"owner": "counsel", "title": "Review the lease", "body": "Check the break clause."})
    err, out = call(api, "hub_task_model", {"id": task["id"], "model": "claude-haiku-5-5"}, token=lead)
    assert err, out
    assert get(api, "tasks/" + task["id"])["run_model"] is None


def test_a_manager_bot_follows_its_reports_model_change_without_reading_its_checkpoints(api):
    lead = bot(api, "lead")
    bot(api, "counsel", reports_to="lead")
    task = post(api, "tasks", {"owner": "counsel", "title": "Review the lease", "body": "Check the break clause."})
    store = api.app.state.store
    counsel = bot_id(api, "counsel")
    with store.transaction() as c:
        room = H.task(c, task["id"])["conversation_id"]
        message = c.execute("SELECT id FROM messages WHERE conversation_id=? LIMIT 1", (room,)).fetchone()[0]
        c.execute("INSERT INTO bot_transitions(id,bot,kind,target,requested_by,expected_revision,state,created_at,"
                  "updated_at) VALUES('tr-1',?,'model','{}','human:ana',1,'checkpointing',?,?)", (counsel, H.now(), H.now()))
        c.execute("INSERT INTO bot_transition_checkpoints(transition_id,conversation_id,message_id,state,checkpoint)"
                  " VALUES('tr-1',?,?,'prepared',?)", (room, message, encode({"decisions": ["Drop the break clause"]})))
    [seen] = get(api, "settings/transitions/tr-1", token=lead)["checkpoints"]
    assert seen == {"conversation_id": None, "state": "prepared", "checkpoint": None, "error": None}
    [full] = get(api, "settings/transitions/tr-1")["checkpoints"]
    assert full["conversation_id"] == room and full["checkpoint"] == {"decisions": ["Drop the break clause"]}


def newer_changes(api, bot_slug, n, created):
    """`n` task-model changes a manager bot made to `bot_slug`, written as the app writes them; `created(i)` is when."""
    bid = bot_id(api, bot_slug)
    with api.app.state.store.transaction() as c:
        c.executemany("INSERT INTO settings_changes(id,bot,field,before,after,actor,transition_id,created_at,"
                      "via) VALUES(?,?,?,?,?,?,?,?,?)",
                      [(H.new_id(), bid, "task_model", encode(None), encode(HAIKU), "bot:lead", None, created(i),
                        report_managers.VIA) for i in range(n)])


def test_a_bots_history_keeps_its_change_when_other_bots_fill_the_newest_page(api):
    lead = bot(api, "lead", reports_to="human:ben")
    bot(api, "counsel", reports_to="lead")
    bot(api, "junior", reports_to="lead")
    task_model(api, lead)
    [mine] = changes_to(api, "counsel", "task_model")
    newer_changes(api, "junior", 150, lambda i: H.shift(mine["created_at"], seconds=i + 1))
    # The bot's own card asks for the bot: its change is there for the owner and for the person who manages it.
    for token in ("ana-test", "ben-test"):
        listed = get(api, "settings/history?bot=counsel", token=token)["changes"]
        found = [row for row in listed if row["id"] == mine["id"]]
        assert found and found[0]["can_undo"] is True, token
        assert {row["bot"] for row in listed} == {mine["bot"]}, token


def test_a_history_page_is_bounded_and_the_next_page_neither_skips_nor_repeats(api):
    lead = bot(api, "lead", reports_to="human:ben")
    bot(api, "counsel", reports_to="lead")
    task_model(api, lead)
    stamp = H.now()
    newer_changes(api, "counsel", 250, lambda i: stamp)     # one time for all: the cursor must not skip a tie
    counsel = bot_id(api, "counsel")
    with api.app.state.store.read() as c:
        total = c.execute("SELECT count(*) FROM settings_changes WHERE bot=? AND via=?",
                          (counsel, report_managers.VIA)).fetchone()[0]
    seen, before = [], ""
    for _ in range(10):
        page = get(api, f"settings/history?bot=counsel&limit=100&before={before}", token="ben-test")
        assert len(page["changes"]) <= 100
        seen += [row["id"] for row in page["changes"]]
        before = page["next_before"]
        if not before:
            break
    assert total > 250 and len(seen) == len(set(seen)) == total


def test_a_caller_who_manages_no_bot_reads_no_settings_change(api, monkeypatch):
    lead = bot(api, "lead", reports_to="human:ben")
    bot(api, "counsel", reports_to="lead")
    task_model(api, lead)
    newer_changes(api, "counsel", 300, lambda i: H.shift(H.now(), seconds=i))
    statements, read = [], api.app.state.store.read

    @contextmanager
    def traced():
        with read() as c:
            c.set_trace_callback(statements.append)
            try:
                yield c
            finally:
                c.set_trace_callback(None)

    monkeypatch.setattr(api.app.state.store, "read", traced)
    assert get(api, "settings/history", token="cara-test")["changes"] == []
    # Nothing reads the install's settings changes for her: no statement touches the table at all.
    assert not [sql for sql in statements if "settings_changes" in sql]


def test_a_person_sees_only_the_manager_changes_to_the_bots_they_manage(api):
    bot(api, "lead", reports_to="human:ben")
    bot(api, "counsel", reports_to="lead")
    bot(api, "helper", operator="cara")
    newer_changes(api, "counsel", 300, lambda i: H.shift(H.now(), seconds=i))
    newer_changes(api, "helper", 2, lambda i: H.shift(H.now(), seconds=i))
    listed = get(api, "settings/history", token="cara-test")["changes"]
    helper = bot_id(api, "helper")
    assert [row["bot"] for row in listed] == [helper, helper]


def settings_reads(api, monkeypatch, path, token):
    """The answer to a GET of the settings history, and the query plan of each statement it runs on settings_changes."""
    store = api.app.state.store
    read, statements = store.read, []

    @contextmanager
    def traced():
        with read() as c:
            c.set_trace_callback(statements.append)
            try:
                yield c
            finally:
                c.set_trace_callback(None)

    monkeypatch.setattr(store, "read", traced)
    answer = get(api, path, token=token)
    with read() as c:
        plans = [" ".join(row[3] for row in c.execute("EXPLAIN QUERY PLAN " + sql))
                 for sql in statements if "FROM settings_changes" in sql]
    return answer, plans


def test_a_persons_view_of_a_bots_changes_is_an_index_seek_whatever_the_bots_history(api, monkeypatch):
    bot(api, "lead", reports_to="human:ben")
    bot(api, "counsel", reports_to="lead")
    # Counsel's history is 3,000 people's changes and no manager's: Ben's reads of it find nothing, and seek past it.
    counsel = bot_id(api, "counsel")
    with api.app.state.store.transaction() as c:
        c.executemany("INSERT INTO settings_changes(id,bot,field,before,after,actor,transition_id,created_at,"
                      "via) VALUES(?,?,?,?,?,?,?,?,?)",
                      [(H.new_id(), counsel, "owners", encode([]), encode(["human:ana"]), "human:ana", None,
                        H.shift(H.now(), seconds=-i), None) for i in range(3000)])
    for path in ("settings/history?bot=counsel", "settings/history"):
        answer, plans = settings_reads(api, monkeypatch, path, "ben-test")
        assert answer["changes"] == [] and plans, path
        assert all("USING INDEX settings_changes_bot_via_id" in plan and "SCAN settings_changes" not in plan
                   for plan in plans), (path, plans)
    # The owner's view of one bot reads it by its own index: the newest 100 of its 3,000.
    answer, plans = settings_reads(api, monkeypatch, "settings/history?bot=counsel", "ana-test")
    assert len(answer["changes"]) == 100 and plans
    assert all("USING INDEX settings_changes_bot_id (" in plan for plan in plans), plans


def test_a_tie_of_changes_at_one_time_pages_in_id_order_and_no_read_sorts_it(api, monkeypatch):
    """Newest first is (created, id). Each history read's index ends in id, so SQLite reads a tie group in that order;
    a sort would read every change at one time before its first page, and a `before` page would sort them again."""
    bot(api, "lead", reports_to="human:ben")
    bot(api, "counsel", reports_to="lead")
    cid = bot_id(api, "counsel")
    with api.app.state.store.transaction() as c:
        c.executemany("INSERT INTO settings_changes(id,bot,field,before,after,actor,transition_id,created_at,"
                      "via) VALUES(?,?,?,?,?,?,?,?,?)",
                      [(H.new_id(), cid, "task_model", encode(None), encode(HAIKU), "bot:lead", None,
                        "2026-10-28T12:00:00.000000Z", report_managers.VIA if i % 2 else None) for i in range(600)])
    store = api.app.state.store
    real, statements = store.read, []

    @contextmanager
    def traced():
        with real() as c:
            c.set_trace_callback(statements.append)
            try:
                yield c
            finally:
                c.set_trace_callback(None)

    monkeypatch.setattr(store, "read", traced)

    def read_page(path, token):
        """One page of a history read, and the query plan of each statement it runs on settings_changes."""
        del statements[:]
        answer = get(api, path, token=token)
        with real() as c:
            plans = [" ".join(row[3] for row in c.execute("EXPLAIN QUERY PLAN " + sql))
                     for sql in statements if "FROM settings_changes" in sql]
        return answer, plans

    with real() as c:
        rows = c.execute("SELECT id, created_at, bot, via FROM settings_changes").fetchall()

    def newest_first(keep):
        return [row[0] for row in sorted(rows, key=lambda row: (row[1], row[0]), reverse=True) if keep(row)]

    cases = [("settings/history?bot=counsel&limit=100", "ana-test",
              newest_first(lambda row: row[2] == cid)),
             ("settings/history?bot=counsel&limit=100", "ben-test",
              newest_first(lambda row: row[2] == cid and row[3] == report_managers.VIA)),
             ("settings/history?limit=100", "ana-test", newest_first(lambda row: True))]
    for path, token, expected in cases:
        seen, before = [], ""
        for _ in range(20):
            answer, plans = read_page(f"{path}&before={before}", token)
            assert plans and not [plan for plan in plans if "TEMP B-TREE" in plan], (path, token, plans)
            seen += [row["id"] for row in answer["changes"]]
            before = answer["next_before"]
            if not before:
                break
        assert seen == expected and len(seen) == len(set(seen)) > 100, (path, token)


# ---- creating, archiving and restoring a direct report (report_managers.LIFECYCLE_OPS) ----------------------------------
NEW_BOT = {"display_name": "Junior", "description": "Drafts the memos.", "status": "planned", "model": "gpt-6.1-sol",
           "effort": "high", "harness": None, "runner_id": None}


def ask_botops(api, botops, manager, text=None):
    """A manager bot asks BotOps with its own token, flagged needs-action so it wakes BotOps now (bot_to_bot.py);
    the attempt is BotOps' run for that message. Each ask has its own text: the wake preset (wake_presets.py) cancels a
    job whose message repeats the room's last one soon after that run finished."""
    text = text or f"Add a report {uuid.uuid4().hex}"
    sent = api.post("/api/v2/messages", json={"to": "botops", "text": text, "needs_action": True}, headers=headers(manager))
    assert sent.status_code == 200, sent.text
    return claim(api, botops, "botops")


def lifecycle_changes(api, bot_slug):
    bid = bot_id(api, bot_slug)
    return [row for row in get(api, "settings/history")["changes"] if row["bot"] == bid and row["field"] == "lifecycle"]


def test_a_manager_bot_with_its_own_token_cannot_add_or_archive_a_report(api):
    lead = bot(api, "lead", reports_to="human:cara")
    bot(api, "counsel", reports_to="lead")
    post(api, "bots", {**NEW_BOT, "slug": "junior", "reports_to": "lead"}, token=lead, expected=403)
    post(api, "bots/counsel/archive", {"expected_revision": revision(api, "counsel")}, token=lead, expected=403)
    assert get(api, "bots/counsel")["state"] != "archived"


def test_botops_adds_a_report_for_the_manager_as_a_members_bot_under_the_person_above(api, botops):
    lead = bot(api, "lead", reports_to="human:cara")
    attempt = ask_botops(api, botops, lead)
    made = act(api, attempt, "POST", "bots", {**NEW_BOT, "slug": "junior", "reports_to": "lead"})
    assert made.status_code == 200, made.text
    finish(api, botops, attempt)
    jid, lid = bot_id(api, "junior"), bot_id(api, "lead")
    row = get(api, "bots/junior")
    assert row["reports_to"] == lid
    with api.app.state.store.read() as c:
        config = c.execute("SELECT created_by,owner_id FROM bot_config WHERE bot=?", (jid,)).fetchone()
        assert (config["created_by"], config["owner_id"]) == ("human:cara", "cara")    # a member's bot, made by the person above
        events = c.execute("SELECT actor,detail FROM events WHERE action='bot.report_lifecycle' AND target=?", (jid,)).fetchall()
    assert [(e["actor"], '"op": "create"' in e["detail"]) for e in events] == [("bot:" + lid, True)]
    [change] = lifecycle_changes(api, "junior")
    assert change["via"] == "report_manager" and change["actor"] == "bot:" + lid and change["before"] is None
    assert change["after"] == {"state": "planned"}


def test_botops_refuses_a_report_that_is_not_the_managers(api, botops):
    lead = bot(api, "lead", reports_to="human:cara")
    bot(api, "stranger", reports_to="human:cara")
    bot(api, "counsel", reports_to="lead")
    attempt = ask_botops(api, botops, lead)
    # Not under the manager: a new bot placed under someone else, or an existing report of another manager.
    assert act(api, attempt, "POST", "bots", {**NEW_BOT, "slug": "junior", "reports_to": "stranger"}).status_code == 403
    assert act(api, attempt, "POST", "bots/stranger/archive", {"expected_revision": revision(api, "stranger")}).status_code == 403
    assert act(api, attempt, "POST", "bots/counsel/archive", {"expected_revision": revision(api, "counsel")}).status_code == 200
    finish(api, botops, attempt)
    assert get(api, "bots/stranger")["state"] != "archived"


def test_the_switches_refuse_botops_for_the_manager(api, botops):
    lead = bot(api, "lead", reports_to="human:cara")
    bot(api, "counsel", reports_to="lead")
    post(api, "bots/lead/manages-reports", {"manages_reports": False, "expected_revision": revision(api, "lead")})
    attempt = ask_botops(api, botops, lead)
    assert act(api, attempt, "POST", "bots", {**NEW_BOT, "slug": "junior", "reports_to": "lead"}).status_code == 403
    assert act(api, attempt, "POST", "bots/counsel/archive", {"expected_revision": revision(api, "counsel")}).status_code == 403
    finish(api, botops, attempt)
    post(api, "bots/lead/manages-reports", {"manages_reports": True, "expected_revision": revision(api, "lead")})
    put(api, "access/rules", {"managers_manage_reports": False})
    attempt = ask_botops(api, botops, lead)
    assert act(api, attempt, "POST", "bots/counsel/archive", {"expected_revision": revision(api, "counsel")}).status_code == 403
    finish(api, botops, attempt)


def test_the_weekly_cap_refuses_and_asks_the_person_above_the_manager(api, botops):
    lead = bot(api, "lead", reports_to="human:cara")
    for n in range(3):
        bot(api, f"junior{n}", reports_to="lead")
    for n in range(3):
        attempt = ask_botops(api, botops, lead)
        assert act(api, attempt, "POST", "bots/junior%d/archive" % n,
                   {"expected_revision": revision(api, f"junior{n}")}).status_code == 200
        finish(api, botops, attempt)
    bot(api, "junior3", reports_to="lead")
    attempt = ask_botops(api, botops, lead)
    over = act(api, attempt, "POST", "bots/junior3/archive", {"expected_revision": revision(api, "junior3")})
    assert over.status_code == 429 and over.json()["error"]["code"] == "report_cap"
    assert "Do not ask again" in over.json()["error"]["detail"]
    assert get(api, "bots/junior3")["state"] != "archived"
    finish(api, botops, attempt)
    with api.app.state.store.read() as c:
        asks = c.execute("SELECT owner_id,requester_id FROM tasks WHERE title LIKE 'lead is at its weekly limit%'").fetchall()
    assert [(a["owner_id"], a["requester_id"]) for a in asks] == [("human:cara", "bot:" + bot_id(api, "lead"))]


def test_botops_archives_a_report_and_the_change_is_recorded_and_restorable(api, botops):
    lead = bot(api, "lead", reports_to="human:cara")
    # Restoring an archived bot takes its owner or operator, or a person above it who is one of them: the org chart
    # leaves an archived bot out (Auth.manages), so the person above alone cannot bring it back.
    bot(api, "counsel", reports_to="lead", operator="cara")
    attempt = ask_botops(api, botops, lead)
    archived = act(api, attempt, "POST", "bots/counsel/archive", {"expected_revision": revision(api, "counsel")})
    assert archived.status_code == 200, archived.text
    finish(api, botops, attempt)
    [change] = lifecycle_changes(api, "counsel")
    assert change["via"] == "report_manager" and change["after"] == {"state": "archived"}
    assert change["before"] == {"state": "active"}
    # The manager's ask can bring it back; the person above can undo the archive as well.
    attempt = ask_botops(api, botops, lead)
    back = act(api, attempt, "POST", "bots/counsel/restore", {})
    assert back.status_code == 200, back.text
    finish(api, botops, attempt)
    assert get(api, "bots/counsel")["state"] != "archived"
    # The restore is the newest change, so it is the one the person above can undo: back to archived.
    [restore_row] = [r for r in lifecycle_changes(api, "counsel") if r["after"] != {"state": "archived"}][:1]
    assert restore_row["can_undo"] is True
    post(api, f"settings/history/{restore_row['id']}/undo", {"expected_revision": revision(api, "counsel", "cara-test")},
         token="cara-test")
    assert get(api, "bots/counsel")["state"] == "archived"


def test_a_manager_bot_files_an_instruction_change_as_a_next_run_task_for_its_report(api):
    lead = bot(api, "lead", reports_to="human:cara")
    bot(api, "counsel", reports_to="lead")
    bot(api, "stranger", reports_to="human:cara")
    post(api, "bots/stranger/definition", {"bot_contact": "replies", "expected_revision": revision(api, "stranger")})
    filed = post(api, "tasks", {"owner": "counsel", "title": "Change the NDA steps",
                                "body": "Update your AGENT.md: ask for the counterparty's name first.", "next_run": True},
                 token=lead)
    row = get(api, f"tasks/{filed['id']}")["task"]
    assert row["owner_id"] == "bot:" + bot_id(api, "counsel") and row["next_run"] is True
    post(api, "tasks", {"owner": "stranger", "title": "Change the steps", "body": "Do it.", "next_run": True},
         token=lead, expected=403)

