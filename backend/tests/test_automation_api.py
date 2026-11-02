"""Automations over the API (backend/automations.py): save and edit as versions with an audit row each, restore,
delete, built-ins, run now and its run record, and paged lists."""
import json

from backend.tests.test_api import api, get, headers, post  # noqa: F401

NOTE_STEP = {"id": "tell", "type": "action", "enabled": True, "do": "wake_bot", "args": {"bot": "ops", "text": "Look"}}


def flow(**extra):
    return {"name": "Morning check", "owner": "person:ana", "trigger": {"type": "schedule", "cron": "0 9 * * *", "tz": "UTC"},
            "steps": [NOTE_STEP], **extra}


def make(api, token="ana-test", expected=200, **extra):
    return post(api, "automations", flow(**extra), token, expected=expected)


def patch(api, aid, body, token="ana-test", expected=200):
    r = api.patch(f"/api/v2/automations/{aid}", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def audit_rows(api, aid):
    """The automation's audit rows, oldest first, with before and after read back."""
    from backend import audit_log
    with api.app.state.store.read() as c:
        return audit_log.rows(c, obj="automation:" + aid, limit=200)["rows"][::-1]


def builtin(api, key="triage", owner="bot:ops"):
    """A built-in as the hub makes it."""
    from backend import automations
    spec = {"key": key, "name": "Triage", "owner": owner, "trigger": {"type": "event", "name": "task.created"},
            "steps": [NOTE_STEP]}
    with api.app.state.store.transaction() as c:
        automations.ensure_builtins(c, [spec])
        automations.ensure_builtins(c, [spec])          # idempotent
    return "auto_builtin_" + key


def as_bot(api, slug):
    """A test token that signs in as the bot named by slug (its id, or its slug if it is new), as bot:<id>."""
    from backend.auth import Identity
    from backend.store import H
    with api.app.state.store.transaction() as c:
        found = H.resolve_bot(c, slug)
        bot_id = found["id"] if found else slug
        if not found:
            c.execute("INSERT INTO bots(id,slug,display_name,created_at) VALUES(?,?,?,?)",
                      (bot_id, slug, slug, H.now()))
            c.execute("INSERT INTO bot_config(bot,config,operator) VALUES(?,?,'ana')", (bot_id, '{"runtime":"fake"}'))
        c.execute("INSERT INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
                  (bot_id, "test", "x-" + bot_id, H.now(), "human:ana"))
    api.app.state.auth.settings.test_identities[slug + "-test"] = Identity(H.bot_actor(bot_id), "bot", agent="test")
    return slug + "-test"


EVENING = {"type": "schedule", "cron": "0 18 * * *", "tz": "UTC"}


def test_a_change_to_what_it_does_is_a_version_a_rename_or_pause_is_not(api):
    made = make(api)
    aid = made["id"]
    assert aid.startswith("auto_") and made["version"] == 1 and made["owner"] == "person:ana" and made["can_edit"]
    edited = patch(api, aid, {"name": "Morning check · weekdays", "enabled": False, "spend_cap": {"per_30m": 2}})
    assert edited["version"] == 1 and edited["enabled"] is False and edited["spend_cap"] == {"per_30m": 2}
    assert "spend_cap" not in patch(api, aid, {"spend_cap": None})          # null: back to the bot's own limit
    assert patch(api, aid, {"trigger": EVENING, "summary": "Evenings"})["version"] == 2
    assert patch(api, aid, {"trigger": EVENING})["version"] == 2           # nothing changed: no new version
    versions = get(api, f"automations/{aid}/versions")["versions"]
    assert [(v["n"], v["status"]) for v in versions] == [(2, "active"), (1, "active")]
    assert versions[0]["summary"] == "Evenings" and versions[1]["flow"]["trigger"]["cron"] == "0 9 * * *"
    restored = post(api, f"automations/{aid}/versions/1/restore", {})
    assert restored["version"] == 3 and restored["trigger"]["cron"] == "0 9 * * *"
    assert restored["name"] == "Morning check · weekdays" and restored["enabled"] is False   # not part of a version
    post(api, f"automations/{aid}/versions/9/restore", {}, expected=404)
    rows = audit_rows(api, aid)
    assert len(rows) == 5 and rows[0]["before"] is None and rows[1]["before"]["enabled"] is True
    assert rows[1]["after"]["enabled"] is False and rows[4]["after"]["trigger"]["cron"] == "0 9 * * *"
    assert all(r["actor"] == "human:ana" for r in rows)


def test_a_bot_proposes_and_a_person_restores_or_discards(api):
    aid = make(api)["id"]
    bot = as_bot(api, "finance")
    get(api, f"automations/{aid}", bot, expected=403)                            # not its own, so not shown
    patch(api, aid, {"enabled": False}, bot, expected=403)                      # and not changed
    proposed = patch(api, aid, {"trigger": EVENING, "summary": "Evenings instead"}, bot)
    assert proposed == {"id": aid, "pending": 2, "can_edit": False}
    assert get(api, f"automations/{aid}")["trigger"]["cron"] == "0 9 * * *"
    second = patch(api, aid, {"steps": [NOTE_STEP | {"args": {"bot": "ops", "text": "Again"}}]}, bot)["pending"]
    pending = get(api, f"automations/{aid}/versions?status=pending")["versions"]
    assert [(v["n"], v["actor"], v["status"]) for v in pending] == [(3, "bot:finance", "pending"),
                                                                   (2, "bot:finance", "pending")]
    post(api, f"automations/{aid}/versions/2/restore", {}, bot, expected=403)
    live = post(api, f"automations/{aid}/versions/2/restore", {})
    assert live["version"] == 2 and live["trigger"]["cron"] == "0 18 * * *"   # it keeps its number
    assert api.delete(f"/api/v2/automations/{aid}/versions/{second}", headers=headers(bot)).status_code == 403
    r = api.delete(f"/api/v2/automations/{aid}/versions/{second}", headers=headers())
    assert r.status_code == 200 and r.json()["version"] == 2
    assert get(api, f"automations/{aid}/versions?status=pending")["versions"] == []
    assert api.delete(f"/api/v2/automations/{aid}/versions/1", headers=headers()).status_code == 409
    assert [r["actor"] for r in audit_rows(api, aid)] == ["human:ana", "human:ana"]   # proposals change nothing


def test_a_bot_without_rights_proposes_and_a_bot_with_them_saves(api):
    aid = make(api, owner="bot:ops")["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot IN ('ops','finance')")
        c.execute("UPDATE bot_config SET reports_to='bot:ops' WHERE bot='coo'")
    peer, boss = as_bot(api, "finance"), as_bot(api, "coo")
    get(api, f"automations/{aid}", peer, expected=403)
    assert patch(api, aid, {"trigger": EVENING}, peer) == {"id": aid, "pending": 2, "can_edit": False}
    patch(api, aid, {"name": "Mine now"}, peer, expected=403)
    assert patch(api, aid, {"trigger": EVENING}, as_bot(api, "ops"))["version"] == 3   # its own: saved, not proposed
    assert patch(api, aid, {"trigger": EVENING}, boss)["pending"] == 4                  # coo is under ops, not above


def test_decide_steps_show_their_sets_questions_and_the_sets_are_listed(api):
    decide = {"id": "d", "type": "decide", "set": "mail-triage", "out": "d1"}
    shown = make(api, steps=[decide])["steps"][0]
    assert shown["set"] == "mail-triage" and "bucket" in shown["questions"]
    assert "questions" not in get(api, "automations")["automations"][0]["steps"][0]
    make(api, expected=422, steps=[decide | {"questions": shown["questions"]}])     # a write sends one, not both
    sets = {s["id"]: s for s in get(api, "automations/question-sets")["sets"]}
    assert "mail-triage@1" in sets and "bucket" in sets["mail-triage@1"]["questions"]


def test_a_bad_flow_is_refused_with_why(api):
    body = make(api, expected=422, steps=[{"id": "x", "type": "paths", "paths": [
        {"name": f"p{i}", "when": [], "steps": []} for i in range(11)]}])
    assert "1 to 10 paths" in json.dumps(body)
    assert "not on the team" in json.dumps(make(api, expected=422, owner="bot:nobody"))


def test_delete_and_builtins(api):
    aid = make(api)["id"]
    post(api, f"automations/{aid}/delete", {})
    get(api, f"automations/{aid}", expected=404)
    assert audit_rows(api, aid)[-1]["after"] is None
    make(api, expected=403, builtin=True)                                   # the hub makes built-ins, not POST
    shipped = builtin(api)
    listed = get(api, "automations")["automations"]
    assert [a["builtin"] for a in listed if a["id"] == shipped] == [True]
    r = api.delete(f"/api/v2/automations/{shipped}", headers=headers())
    assert r.status_code == 409
    paused = patch(api, shipped, {"enabled": False})
    assert paused["enabled"] is False and "warning" in paused


def test_only_the_top_pauses_a_builtin(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='ops'")
    shipped = builtin(api)
    shown = get(api, f"automations/{shipped}", "ben-test")
    assert shown["can_edit"] is True and shown["can_pause"] is False      # he edits it, but only the top pauses it
    patch(api, shipped, {"name": "Renamed by Ben"}, "ben-test")          # ops reports to Ben: he may edit it
    patch(api, shipped, {"enabled": False}, "ben-test", expected=403)


def test_what_wakes_bots_is_the_hubs_read_only_builtin_the_top_may_only_pause(api):
    from backend import automations
    automations.tick(api.app.state.store)
    automations.tick(api.app.state.store)                                  # made once
    wakes = [a for a in get(api, "automations")["automations"] if a["name"] == "What wakes bots"]
    assert len(wakes) == 1 and wakes[0]["builtin"] is True and wakes[0]["can_edit"] is False
    assert wakes[0]["can_pause"] is True                                   # the page shows the top its switch
    shown = get(api, f"automations/{wakes[0]['id']}")
    assert shown["owner"] == "person:ana" and shown["trigger"]["type"] == "event" and shown["readonly"] is True
    assert shown["steps"] and all(s["type"] == "filter" for s in shown["steps"])
    aid = shown["id"]
    patch(api, aid, {"name": "Mine now"}, expected=403)                     # nobody edits it
    patch(api, aid, {"enabled": False}, "ben-test", expected=403)          # only the top pauses it
    assert api.delete(f"/api/v2/automations/{aid}", headers=headers()).status_code == 409
    assert post(api, f"automations/{aid}/run", {}, expected=409)["error"]["code"] == "readonly"
    paused = patch(api, aid, {"enabled": False})
    assert paused["enabled"] is False and "keep working" in paused["warning"]
    with api.app.state.store.transaction() as c:                           # the engine never runs it
        assert automations.fire(c, "event", {"event": {"name": "tico.builtin.wakes"}}) == []
    assert patch(api, aid, {"enabled": True})["enabled"] is True


def test_run_now_runs_the_flow_and_records_each_step(api):
    aid = make(api, steps=[NOTE_STEP, {"id": "off", "type": "action", "enabled": False, "do": "note", "args": {}}])["id"]
    run = post(api, f"automations/{aid}/run", {"input": {"task": {"title": "Sample"}}})["run"]
    assert run["status"] == "ok" and run["version"] == 1 and run["finished_at"]
    assert [(s["id"], s["status"]) for s in run["steps"]] == [("tell", "ok"), ("off", "skipped")]
    with api.app.state.store.read() as c:
        woke = c.execute("SELECT body, refs FROM messages WHERE to_actor='bot:ops' ORDER BY created_at DESC").fetchone()
    assert woke["body"] == "Look" and json.loads(woke["refs"])["automation"] == aid
    runs = get(api, f"automations/{aid}/runs")
    assert runs["runs"][0]["id"] == run["id"] and runs["daily"][0]["runs"] == 1 and runs["next"] is None
    assert get(api, "automations")["automations"][0]["last_run"]["id"] == run["id"]
    shown = get(api, f"automations/{aid}")
    assert shown["counts"] == {"today": {"runs": 1, "dropped": 0}} and shown["next_run"].endswith("T09:00:00.000000Z")


def test_a_run_that_fails_says_which_step(api):
    aid = make(api, steps=[{"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "{{who}}"}}])["id"]
    run = post(api, f"automations/{aid}/run", {"input": {"who": "nobody"}})["run"]
    assert run["status"] == "failed" and run["steps"][0]["status"] == "failed"
    assert "nobody" in run["steps"][0]["outputs"]["detail"]


def test_lists_are_paged(api):
    for n in range(3):
        make(api, name=f"Check {n}")
    first = get(api, "automations?limit=2")
    assert len(first["automations"]) == 2 and first["next"] == 2
    rest = get(api, f"automations?limit=2&cursor={first['next']}")
    assert len(rest["automations"]) == 1 and rest["next"] is None


def test_runs_are_paged_newest_first_by_run_id(api):
    aid = make(api)["id"]
    made = [post(api, f"automations/{aid}/run", {})["run"]["id"] for _ in range(3)]
    first = get(api, f"automations/{aid}/runs?limit=2")
    assert [r["id"] for r in first["runs"]] == made[:0:-1] and first["next"] == made[1]
    rest = get(api, f"automations/{aid}/runs?limit=2&before={first['next']}")
    assert [r["id"] for r in rest["runs"]] == [made[0]] and rest["next"] is None
    get(api, f"automations/{aid}/runs?before=run_nope", expected=404)


def test_the_answers_match_the_published_contract(api):
    """Each automation read and write against its docs/openapi/v2.json shape."""
    from backend.tests.test_openapi_v2 import conforms
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    shape = {op["operationId"]: op["responses"]["200"].get("content", {}).get("application/json", {}).get("schema")
             for ops in document["paths"].values() for op in ops.values() if op["tags"] == ["Automations"]}

    def check(operation, answer):
        assert conforms(answer, shape[operation], document) is None, (operation, conforms(answer, shape[operation], document))
        return answer
    aid = check("createAutomation", make(api))["id"]
    check("getAutomation", get(api, f"automations/{aid}"))
    check("listAutomations", get(api, "automations"))
    check("updateAutomation", patch(api, aid, {"steps": []}))
    check("runAutomation", post(api, f"automations/{aid}/run", {}))
    check("listAutomationRuns", get(api, f"automations/{aid}/runs"))
    check("listAutomationVersions", get(api, f"automations/{aid}/versions"))
    check("restoreAutomationVersion", post(api, f"automations/{aid}/versions/1/restore", {}))
    check("listQuestionSets", get(api, "automations/question-sets"))
    check("getInboxSend", get(api, "inboxes/ana@acme.example"))
    check("getPersonAssistant", get(api, "people/ana/assistant"))
    check("deleteAutomationPost", post(api, f"automations/{aid}/delete", {}))


def test_the_librarian_gets_the_market_builtins_which_stand_down_for_the_teams_own(api):
    """Market briefing (daily 07:00 team time, posting to Market and Updates) and one Market listening per destination
    the Librarian receives; the top pauses them, nobody deletes them, and the team's own Librarian automation for the
    same work runs instead of them, never beside them."""
    from backend import automations
    from backend.tests.test_api import put
    from backend.tests.test_market import _librarian
    _librarian(api)
    put(api, "listening/destinations/tools", {"category": "lead", "threshold": 0.5, "receiver": "bot:librarian",
                                               "readers": [], "what": "Dev tools"})
    automations.tick(api.app.state.store)
    automations.tick(api.app.state.store)                                  # made once
    brief = get(api, "automations/auto_builtin_market_briefing")
    assert (brief["builtin"], brief["enabled"], brief["owner"], brief["trigger"]) == (
        True, True, "bot:librarian", {"type": "schedule", "cron": "0 7 * * *", "tz": "America/Los_Angeles"})
    assert [s["type"] for s in brief["steps"]] == ["bot_turn", "action"]
    assert brief["steps"][1]["args"] == {"to": ["market", "updates"]}
    listen = get(api, "automations/auto_builtin_market_listening_tools")
    assert listen["trigger"] == {"type": "listening", "destination": "tools"} and listen["owner"] == "bot:librarian"
    assert api.delete("/api/v2/automations/auto_builtin_market_briefing", headers=headers()).status_code == 409
    patch(api, "auto_builtin_market_briefing", {"enabled": False}, "ben-test", expected=403)
    assert patch(api, "auto_builtin_market_briefing", {"enabled": False})["warning"]

    def heard():
        with api.app.state.store.transaction() as c:
            ids = automations.fire(c, "listening", {"listening": {"destination": "tools"}}, "listening:x",
                                   lambda t: t["destination"] == "tools")
            return [c.execute("SELECT automation_id FROM automation_runs WHERE id=?", (i,)).fetchone()[0] for i in ids]
    assert heard() == ["auto_builtin_market_listening_tools"]
    own = make(api, owner="bot:librarian", trigger={"type": "listening", "destination": "tools"})["id"]
    assert heard() == [own]
    # The briefing stands down while the Librarian's own automation posts to Market.
    patch(api, "auto_builtin_market_briefing", {"enabled": True})
    make(api, owner="bot:librarian", steps=[{"id": "post", "type": "action", "do": "post_update",
                                             "args": {"to": ["market"], "text": "Acme week"}}])
    with api.app.state.store.transaction() as c:
        assert automations.fire(c, "schedule", {"schedule": {}}, "schedule:x", only="auto_builtin_market_briefing") == []


def test_a_bot_may_not_make_an_automation_it_may_not_change(api):
    """A bot's own call to make an automation it may not change is refused (only BotOps, acting for it, saves a draft:
    test_botops_automations). One it may make (its own) is saved as asked."""
    bot = as_bot(api, "finance")
    post(api, "automations", flow(), bot, expected=403)
    post(api, "automations", flow(builtin=True), bot, expected=403)
    assert [a["id"] for a in get(api, "automations")["automations"] if a["owner"] == "person:ana"
            and not a["builtin"]] == []
    own = post(api, "automations", flow(owner="bot:finance"), bot)
    assert own["enabled"] is True and own["can_edit"] is True                   # a bot's own: no BotOps, so on
