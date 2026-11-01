"""Bot profiles (backend/profiles.py): the migration by name, what a Responder and a Reviewer refuse, what each profile
wakes for, a Lead's daily digest, and who may set a profile."""

import json

import pytest

from backend import hubdb as H
from backend import profiles as P
from backend import redesign_migration
from backend import rules as R
from backend import wake_presets as W
from backend.store import encode
from backend.tests.test_api import api, assign, claim, get, post, ready, runner  # noqa: F401  (api is a fixture)
from backend.tests.test_wake_rules import jobs, news


def add_bot(c, slug, **config):
    H.sync_registry(c, {slug: {"name": slug, "runtime": "fake", "status": "active"}}, {"people": []})
    c.execute("INSERT INTO bot_config(bot,config,team,owner_id) VALUES(?,?,?,?)",
              (slug, encode({"name": slug, "runtime": "fake", "status": "active", **config}), None, "ana"))


def profile_of(c, slug):
    return json.loads(c.execute("SELECT config FROM bot_config WHERE bot=?", (slug,)).fetchone()[0])


def put(c, slug, name, **options):
    """Give an existing bot a profile the way the API does (without the Strategy Bot gate or audit)."""
    declared = profile_of(c, slug)
    c.execute("UPDATE bot_config SET config=? WHERE bot=?", (encode(P.apply(declared, name, options)), slug))


def test_migration_gives_each_bot_a_profile_by_name_once_and_tells_its_owner(api):
    slugs = {"engineering-lead": P.LEAD, "cmo": P.LEAD, "release-manager": P.LEAD, "sentry-triage": P.MONITOR,
             "uptime-monitor": P.MONITOR, "qa-web": P.REVIEWER, "security-audit": P.REVIEWER, "pr-reviewer-sol": P.REVIEWER,
             "assistant": P.RESPONDER, "botops": P.CUSTOM, "task-manager": P.CUSTOM, "writer": P.DOER}
    with api.app.state.store.transaction() as c:
        for slug in slugs:
            add_bot(c, slug)
        add_bot(c, "saver", wake_preset="token_saver")
        add_bot(c, "grok-designer", harness="grokbot")
        # The starter factory's Builder is called pr-reviewer and owns the Dev tickets: it stays a doer.
        add_bot(c, "pr-reviewer")
        add_bot(c, "pr-reviewer-2")
        kind = c.execute("SELECT id FROM task_types WHERE name='Dev tickets'").fetchone()
        if kind is None:
            c.execute("INSERT INTO task_types(id,name,created_at,updated_at) VALUES('dev','Dev tickets',?,?)", (H.now(), H.now()))
        H.task_create(c, "human:ana", "Fix login", "x", "bot:pr-reviewer", type="Dev tickets")
        c.execute("UPDATE bot_config SET config=json_set(config,'$.profile','doer') WHERE bot='cpo'")
        told = P.migrate(c)
        got = {slug: profile_of(c, slug) for slug in (*slugs, "saver", "grok-designer", "pr-reviewer", "cpo")}
        assert {slug: got[slug]["profile"] for slug in slugs} == slugs
        assert got["grok-designer"]["profile"] == P.RESPONDER and got["cpo"]["profile"] == "doer"
        assert got["pr-reviewer"]["profile"] == P.DOER and profile_of(c, "pr-reviewer-2")["profile"] == P.REVIEWER
        # A former Token saver bot keeps its 30-minute window as a value of its own; Performance is the Doer's.
        assert got["saver"]["profile"] == P.DOER and got["saver"]["wake_preset"] == "token_saver"
        assert got["saver"]["profile_options"]["batch_seconds"] == 1800 and got["writer"]["wake_preset"] == "performance"
        assert told > 12
        notes = [m for m in H.notices(c, "human:ana") if "gave each of your bots a profile" in m["body"]]
        assert len(notes) == 1 and "engineering-lead" in notes[0]["body"] and "Lead" in notes[0]["body"]
        # Run again: nothing changes and nobody is told twice.
        revision = c.execute("SELECT sum(revision) FROM bot_config").fetchone()[0]
        assert P.migrate(c) == 0 and c.execute("SELECT sum(revision) FROM bot_config").fetchone()[0] == revision
        assert len([m for m in H.notices(c, "human:ana") if "gave each of your bots a profile" in m["body"]]) == 1


def turn_for(api, slug):
    """What a routine for `slug` is today: an automation whose Bot turn step is the bot's. It runs on a task made for
    another bot; returns its run's status once no task for `slug` was opened by it."""
    aid = post(api, "automations", {"name": "Notes", "owner_id": "person:ana",
                                    "trigger": {"type": "event", "name": "task.created"},
                                    "steps": [{"id": "notes", "type": "bot_turn", "bot": "bot:" + slug,
                                               "prompt": "Write the notes."}]})["id"]
    post(api, "tasks", {"owner_id": "cpo", "title": "Start the notes", "body": "Please."})
    api.app.state.store.automations.drain()
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE owner_id=?", ("bot:" + slug,)).fetchone()
    found = get(api, f"automations/{aid}/runs")["runs"]
    assert len(found) == 1
    return found[0]["status"]


def test_a_responder_takes_no_task_and_runs_no_routine(api):
    with api.app.state.store.transaction() as c:
        add_bot(c, "helper")
        put(c, "helper", P.RESPONDER)
        for owner_change in (False, True):
            with pytest.raises(H.Refused) as raised:
                if owner_change:
                    mine = H.task_create(c, "human:ana", "Something", "x", "bot:ops")
                    H.task_update(c, "human:ana", mine["id"], owner="bot:helper")
                else:
                    H.task_create(c, "human:ana", "Redo the logo", "x", "bot:helper")
            assert raised.value.rule == "external-bot" and "takes no tasks" in str(raised.value)
        from backend import routines
        with pytest.raises(H.Refused):
            routines.create(c, "human:ana", "helper", {"title": "Notes", "on": "meeting.ready"})
        # People can still write to it: the chat is how a Responder is used.
        H.say(c, "human:ana", "bot:helper", "Hello")
        assert jobs(c, "helper") == 1
    assert turn_for(api, "helper") == "failed"


def test_a_reviewer_takes_review_children_only_and_wakes_for_its_request_and_people(api):
    with api.app.state.store.transaction() as c:
        add_bot(c, "checker")
        put(c, "checker", P.REVIEWER)
        with pytest.raises(H.Refused) as raised:
            H.task_create(c, "bot:ops", "Build the thing", "x", "bot:checker")
        assert raised.value.rule == "profile" and "reviewer" in str(raised.value)
        parent = H.task_create(c, "bot:ops", "Build 1/9", "x", "bot:cpo")
        review = H.task_create(c, "bot:ops", "QA: check the build", "x", "bot:checker", parent_id=parent["id"])
        assert review["owner_id"] == "bot:checker"
        # Its own work is not pulled by itself unless it is a review (auto_tasks), and nothing else stirs it.
        c.execute("DELETE FROM jobs")
        mine = H.task(c, review["id"])
        assert news(c, mine)["refs"]["rule"] == "profile_reviewer_note" and jobs(c, "checker") == 0
        # A task it asked for handed back to it (hubdb.task_update stamps the hand-back off a Dev ticket).
        found = R.judge(c, H.KEEPER, "bot:checker", "message", {"task": review["id"], "wake": "review_handback"},
                        "Ready for review: x", None)
        assert found.outcome == "wake"
        person = R.judge(c, "human:ana", "bot:checker", "message", {}, "Did you look?", None)
        assert person.outcome == "wake"
        # A review is startable on its own; another task owned by the reviewer is not pulled.
        assert P.pull_refusal(c, mine, profile_of(c, "checker")) is None
        other = dict(mine, id="x", title="Something else")
        assert P.pull_refusal(c, other, profile_of(c, "checker")) == "auto_start_off"
        assert P.mismatches(c) == []
        c.execute("INSERT INTO tasks(id,title,body,owner_id,requester_id,status,created_at,updated_at) "
                  "VALUES('t-odd','Odd job','x','bot:checker','human:ana','open',?,?)", (H.now(), H.now()))
        assert P.mismatches(c) == [("reviewer_holds_tasks", "checker", 1)]


@pytest.mark.parametrize("title,parent_step,child", [
    ("Post Release QA pass: v1.2.3", None, False),
    ("Release QA: v1.2.3", None, False),
    ("Post Launch QA: runtime installation", None, False),
    ("Post Release QA sweep", None, False),
    ("Post-release checks: shared question card", None, True),
    ("Hosting preview: carry remaining installed-build and live acceptance", None, True),
    ("Verify shipped behavior", "Post Release QA", True),
    ("Verify the change", "Review 1", True),
    ("Verify the change", "Review 2", True),
    ("Verify the change", "In QA", True),
    ("Verify the change", "Second opinion", True),       # a renamed review step: its flow says it is a check
    ("Review 1: legacy request", None, True),
    ("Review 2: legacy request", None, True),
    ("Security: legacy request", None, True),
])
def test_release_qa_uses_one_classification_for_creation_pull_wake_and_health(api, title, parent_step, child):
    with api.app.state.store.transaction() as c:
        add_bot(c, "checker")
        put(c, "checker", P.REVIEWER)
        config = profile_of(c, "checker")
        revision = c.execute("SELECT revision FROM bot_config WHERE bot='checker'").fetchone()[0]
        parent = H.task_create(c, "bot:ops", "Build it", "x", "bot:cpo") if child else None
        if parent_step:
            # Synthetic step: no production task, factory gate or PR is involved.
            kind = {"Post Release QA": "release_qa", "In QA": "qa", "Review 1": "review", "Review 2": "review",
                    "Second opinion": "review"}.get(parent_step)
            c.execute("INSERT INTO task_steps(id,type_id,name,position,status,flow) "
                      "VALUES('qa-stage','general',?,99,'review',?)",
                      (parent_step, json.dumps({"check": kind}) if kind else None))
            c.execute("UPDATE tasks SET step_id='qa-stage' WHERE id=?", (parent["id"],))
        c.execute("DELETE FROM jobs")
        made = H.task_create(c, "bot:ops", title, "x", "bot:checker",
                             parent_id=parent["id"] if parent else None)
        row = H.task(c, made["id"])
        assert P.is_review_task(c, row)
        assert P.pull_refusal(c, row, config) is None
        assert last_to(c, "checker")["rule"] == "profile_reviewer_request"
        assert jobs(c, "checker") == 1
        assert P.mismatches(c) == []
        assert profile_of(c, "checker") == config
        assert c.execute("SELECT revision FROM bot_config WHERE bot='checker'").fetchone()[0] == revision
        H.task_update(c, "bot:ops", made["id"], owner="bot:cpo")
        c.execute("DELETE FROM jobs")
        H.task_update(c, "bot:ops", made["id"], owner="bot:checker")
        assert last_to(c, "checker")["rule"] == "profile_reviewer_assignment"
        assert jobs(c, "checker") == 1


@pytest.mark.parametrize("title,child", [
    ("Build the thing", False), ("QA: orphan review", False),
    ("Implement acceptance criteria", False), ("Implement acceptance criteria", True),
    ("Release checklist planning", False), ("Post-release documentation", True),
    pytest.param("Release QA dashboard redesign", False, id="qa-dashboard"),
    pytest.param("Release QA checklist planning", False, id="qa-planning"),
    pytest.param("Post Release QA dashboard redesign", False, id="post-qa-dashboard"),
    pytest.param("Add acceptance tests for CSV export", True, id="acceptance-implementation"),
])
def test_ordinary_tasks_remain_outside_the_reviewer_profile(api, title, child):
    with api.app.state.store.transaction() as c:
        add_bot(c, "checker")
        put(c, "checker", P.REVIEWER)
        parent = H.task_create(c, "bot:ops", "Build it", "x", "bot:cpo") if child else None
        ordinary = H.task_create(c, "bot:ops", title, "QA is mentioned only in the body", "bot:cpo",
                                 parent_id=parent["id"] if parent else None)
        assert ordinary["type_id"] == H.GENERAL_TYPE
        assert not P.is_review_task(c, ordinary)
        assert P.pull_refusal(c, ordinary, profile_of(c, "checker")) == "auto_start_off"
        with pytest.raises(H.Refused) as raised:
            H.task_update(c, "bot:ops", ordinary["id"], owner="bot:checker")
        assert raised.value.rule == "profile"


def test_health_counts_a_keepers_ordinary_task_for_a_reviewer_but_not_its_pool_review(api):
    """The reviewer_holds_tasks finding for Muse (2026-10-28): a Review 1 pool request filed under a general-type ticket
    is review work, however its type reads; the nightly learnings task the keeper routes to the same reviewer is not."""
    with api.app.state.store.transaction() as c:
        add_bot(c, "checker")
        put(c, "checker", P.REVIEWER)
        add_bot(c, "pr-reviewer")
        ticket = H.task_create(c, "bot:pr-reviewer", "Goals: read-back", "x", "bot:pr-reviewer")
        review = H.task_create(c, "bot:pr-reviewer", "Review 1: PR #1801", "x", "bot:checker", parent_id=ticket["id"],
                               wake_refs={"check_pool": "Review 1"})
        assert review["type_id"] == H.GENERAL_TYPE and P.is_review_task(c, H.task(c, review["id"]))
        assert P.mismatches(c) == []
        nightly = H.task_create(c, H.KEEPER, "Nightly learnings: Oct 28", "x", "bot:checker", deduplicate=False,
                                lint=False, private=True)
        assert not P.is_review_task(c, H.task(c, nightly["id"]))
        assert P.mismatches(c) == [("reviewer_holds_tasks", "checker", 1)]


def test_sweeps_follow_the_profile_and_a_custom_bot_reads_its_own_setting(api):
    with api.app.state.store.transaction() as c:
        for slug, name, options in (("d", P.DOER, {}), ("r", P.REVIEWER, {}), ("l", P.LEAD, {}), ("m", P.MONITOR, {}),
                                    ("c1", P.CUSTOM, {}), ("c2", P.CUSTOM, {"sweeps": False})):
            add_bot(c, slug)
            put(c, slug, name, **options)
        assert [R.sweeps_on(c, "bot:" + s) for s in ("d", "r", "l", "m", "c1", "c2", "ops")] == [
            True, False, False, False, True, False, True]


def test_a_lead_is_woken_by_people_and_hears_the_rest_once_a_day(api):
    with api.app.state.store.transaction() as c:
        add_bot(c, "boss")
        put(c, "boss", P.LEAD)
        task = H.task_create(c, "bot:ops", "Ship it", "x", "bot:cpo")
        mine = H.task_create(c, "bot:boss", "Ask around", "x", "bot:ops")
        c.execute("DELETE FROM jobs")
        # News about its task is held for the digest: the job waits, then a tick plans it for the next 09:00.
        held = news(c, H.task(c, mine["id"]) | {"owner_id": "bot:boss"}, "Step moved: Open -> Doing")
        assert held["refs"]["rule"] == "profile_lead_digest" and held["refs"]["wake_batch"] == "rule"
        job = c.execute("SELECT id,held,created_at FROM jobs WHERE bot_id='boss'").fetchone()
        assert job["held"] == 1
        W.tick(c, job["created_at"])
        due = c.execute("SELECT hold_until FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
        assert due == W.digest_time(job["created_at"], 9) and due > job["created_at"]
        W.tick(c, due)
        assert c.execute("SELECT held FROM jobs WHERE id=?", (job["id"],)).fetchone()[0] == 0
        # A person's message, and a declined ticket it asked for, wake it at once.
        assert R.judge(c, "human:ana", "bot:boss", "message", {}, "Status?", None).outcome == "wake"
        # The digest itself is held news let go: the lead's batch-everything row must not hold it again.
        digest = R.judge(c, H.KEEPER, "bot:boss", "message", {"wake_digest": [job["id"]]}, "Held updates", None)
        assert (digest.outcome, digest.rule) == ("wake", "batch_digest")
        c.execute("DELETE FROM jobs")
        H.task_update(c, "bot:ops", mine["id"], status="declined", note="Nobody to ask.")
        assert last_to(c, "boss")["rule"] == "profile_lead_task_declined" and jobs(c, "boss") == 1
        assert task["id"]


def test_digest_time_is_the_next_hour_in_team_time():
    # 2026-10-24 12:00 UTC is 05:00 in Los Angeles (PDT): the digest at 09:00 is the same day, 16:00 UTC.
    assert W.digest_time("2026-10-24T12:00:00.000000Z", 9) == "2026-10-24T16:00:00.000000Z"
    assert W.digest_time("2026-10-24T17:00:00.000000Z", 9) == "2026-10-25T16:00:00.000000Z"


def set_profile(api, bot, name, token="ana-test", expected=200, **extra):
    revision = get(api, "bots/" + bot)["revision"]
    return post(api, f"bots/{bot}/profile", {"profile": name, "expected_revision": revision, **extra},
                token=token, expected=expected)


def test_setting_a_profile_needs_the_bots_manager_and_the_current_revision(api):
    first = set_profile(api, "ops", "reviewer")
    assert first["profile"]["name"] == "reviewer" and first["profile"]["takes"] == "review"
    shown = get(api, "bots/ops")
    assert shown["profile"]["name"] == "reviewer" and shown["wake"]["profile"] == "reviewer"
    # Cara owns nothing and manages nothing here.
    set_profile(api, "ops", "doer", token="cara-test", expected=403)
    # A stale revision is a conflict, not an overwrite.
    post(api, "bots/ops/profile", {"profile": "doer", "expected_revision": 1}, expected=409)
    # The numbers belong to the profile that reads them.
    set_profile(api, "ops", "doer", pulls="none", expected=422)
    set_profile(api, "ops", "doer", digest_hour=7, expected=422)
    assert set_profile(api, "ops", "lead", digest_hour=7)["profile"]["digest_hour"] == 7
    # The old wake-preset route still works and leaves the profile where it is.
    post(api, "bots/ops/wake-preset", {"wake_preset": "token_saver", "expected_revision": get(api, "bots/ops")["revision"]})
    assert get(api, "bots/ops")["profile"]["name"] == "lead"


def test_custom_copies_the_rows_and_leaving_it_needs_a_confirm(api):
    set_profile(api, "ops", "reviewer")
    custom = set_profile(api, "ops", "custom")
    assert custom["rules"] >= 3 and custom["profile"]["name"] == "custom"
    with api.app.state.store.read() as c:
        # The copied rows are in the bot's What wakes automation, where the wake engine reads them.
        assert redesign_migration.wake_rules_by(c, "bot", "ops", "profile") == custom["rules"]
        assert len([r for r in redesign_migration.wake_rows(c) if r["scope_id"] == "ops"
                    and r["created_by"] == "profile"]) == custom["rules"]
    set_profile(api, "ops", "doer", expected=409)
    back = set_profile(api, "ops", "doer", confirm_drop_rules=True)
    assert back["rules"] == 0 and back["profile"]["name"] == "doer"


def test_the_choices_describe_each_profile(api):
    listed = get(api, "profiles")["profiles"]
    assert [p["name"] for p in listed] == list(P.NAMES) and all(p["description"] for p in listed)


def test_the_cli_and_mcp_tool_read_the_choices_and_send_the_revision():
    from clients import hubtools

    class Stub:
        def __init__(self):
            self.posts = []
            self.__dict__["_tico_is_botops"] = False

        def get(self, path):
            return {"profiles": [{"name": "doer"}]} if path == "profiles" else {"bot": "ops", "revision": 4}

        def post(self, path, body, key=None):
            self.posts.append((path, body))
            return {"ok": True}
    api = Stub()
    tool = hubtools.BY_NAME["hub_bot_profile"]["fn"]
    assert tool(api, {"bot": "ops"})["choices"] == [{"name": "doer"}]
    tool(api, {"bot": "ops", "profile": "lead", "digest_hour": 7})
    assert api.posts == [("bots/ops/profile", {"digest_hour": 7, "profile": "lead", "expected_revision": 4})]


def test_the_profiles_catch_all_never_beats_a_team_or_type_rule_and_a_leads_own_writes_are_not_digested(api):
    from backend.tests.test_wake_rules import as_cause
    with api.app.state.store.transaction() as c:
        add_bot(c, "checker")
        put(c, "checker", P.REVIEWER)
        add_bot(c, "boss")
        put(c, "boss", P.LEAD)
        c.execute("INSERT INTO rules(id,scope_kind,scope_id,event,condition,then_outcome,params,enabled,position,"
                  "created_by,created_at,updated_at,revision) VALUES('team-wake','team','','status_or_relation','{}','wake','{}',"
                  "1,1,'human:ana',?,?,1)", (H.now(), H.now()))
        parent = H.task_create(c, "bot:ops", "Build 1/9", "x", "bot:cpo")
        review = H.task_create(c, "bot:ops", "QA: check it", "x", "bot:checker", parent_id=parent["id"])
        c.execute("DELETE FROM jobs")
        assert news(c, H.task(c, review["id"]))["refs"]["rule"] == "team-wake" and jobs(c, "checker") == 1
        c.execute("DELETE FROM rules")
        task = H.task_create(c, "bot:ops", "Ask around", "x", "bot:boss")
        c.execute("DELETE FROM jobs")
        with as_cause("bot:boss"):
            mine = news(c, H.task(c, task["id"]))
        # The lead caused this itself: it is not held for tomorrow's digest, and not woken either.
        assert "wake_batch" not in mine["refs"] and jobs(c, "boss") == 0


def test_the_old_wake_preset_route_moves_only_the_window_and_the_label_the_hold_reads(api):
    set_profile(api, "ops", "doer")
    revision = lambda: get(api, "bots/ops")["revision"]  # noqa: E731
    saver = post(api, "bots/ops/wake-preset", {"wake_preset": "token_saver", "expected_revision": revision()})
    assert saver["wake"]["batch_seconds"] == 1800 and saver["wake"]["profile"] == "doer"
    with api.app.state.store.read() as c:
        assert profile_of(c, "ops")["wake_preset"] == "token_saver"
    back = post(api, "bots/ops/wake-preset", {"wake_preset": "performance", "expected_revision": revision()})
    assert back["wake"]["batch_seconds"] == 900 and get(api, "bots/ops")["profile"]["options"] == {}
    with api.app.state.store.read() as c:
        assert profile_of(c, "ops")["wake_preset"] == "performance"


def test_a_reviewer_may_be_handed_a_review_child_but_not_an_ordinary_task_and_still_runs_routines(api):
    from backend import routines
    with api.app.state.store.transaction() as c:
        add_bot(c, "checker")
        put(c, "checker", P.REVIEWER)
        parent = H.task_create(c, "bot:ops", "Build 1/9", "x", "bot:cpo")
        child = H.task_create(c, "bot:ops", "QA: check it", "x", "bot:cpo", parent_id=parent["id"])
        plain = H.task_create(c, "bot:ops", "Fix the thing", "x", "bot:cpo")
        H.task_update(c, "bot:ops", child["id"], owner="bot:checker")
        with pytest.raises(H.Refused) as raised:
            H.task_update(c, "bot:ops", plain["id"], owner="bot:checker")
        assert raised.value.rule == "profile"
        assert routines.create(c, "human:ana", "checker", {"title": "Weekly sweep", "cron": "0 9 * * 1"}) is not None


def test_a_monitor_and_a_reviewer_still_run_their_automation_turns(api):
    """A routine moved to Automations runs on a task its automation files; a Monitor or Reviewer refusing it would stop
    the bot's own scheduled check (TIDY's hourly stuck-bot check failed this way on v0.5.0)."""
    with api.app.state.store.transaction() as c:
        for slug, name in (("watch", P.MONITOR), ("checker", P.REVIEWER)):
            add_bot(c, slug)
            put(c, slug, name)
        with pytest.raises(H.Refused):
            H.task_create(c, "human:ana", "Fix the thing", "x", "bot:watch")
    for slug in ("watch", "checker"):
        aid = post(api, "automations", {"name": "Sweep " + slug, "owner_id": "person:ana",
                                        "trigger": {"type": "event", "name": "task.created"},
                                        "steps": [{"id": "sweep", "type": "bot_turn", "bot": "bot:" + slug,
                                                   "prompt": "Check the watchlist."}]})["id"]
        post(api, "tasks", {"owner_id": "cpo", "title": "Start " + slug, "body": "Please."})
        api.app.state.store.automations.drain()
        runs = get(api, f"automations/{aid}/runs")["runs"]
        assert runs and runs[0]["status"] != "failed", runs
        with api.app.state.store.read() as c:
            assert c.execute("SELECT 1 FROM tasks WHERE owner_id=? AND title=?", ("bot:" + slug, "Sweep " + slug)).fetchone()


def test_a_custom_bot_that_takes_no_task_runs_no_routine_and_the_migration_copies_rows_once(api):
    from backend import routines
    with api.app.state.store.transaction() as c:
        add_bot(c, "quiet")
        put(c, "quiet", P.CUSTOM, takes="none")
        with pytest.raises(H.Refused):
            H.task_create(c, "human:ana", "Anything", "x", "bot:quiet")
        with pytest.raises(H.Refused):
            routines.create(c, "human:ana", "quiet", {"title": "Notes", "on": "meeting.ready"})
        # The Librarian becomes a Custom bot with the Monitor's clock rows, copied once however often the server starts,
        # into its What wakes automation (none stays in the table).
        add_bot(c, "librarian")
        P.migrate(c)
        P.migrate(c)
        copied = redesign_migration.wake_rules_by(c, "bot", "librarian", "profile")
        assert copied == len(R.PROFILE_RULES[P.MONITOR]) and profile_of(c, "librarian")["profile"] == P.CUSTOM
        assert not c.execute("SELECT 1 FROM rules WHERE scope_id='librarian' AND created_by='profile'").fetchone()
    assert turn_for(api, "quiet") == "failed"


def last_change(api, bot="ops"):
    with api.app.state.store.read() as c:
        return c.execute("SELECT id FROM settings_changes WHERE bot=? AND field='profile' ORDER BY rowid DESC",
                         (bot,)).fetchone()[0]


def test_undoing_a_profile_change_restores_it_but_not_rules_that_were_dropped(api):
    set_profile(api, "ops", "reviewer")
    set_profile(api, "ops", "lead", digest_hour=7)
    listed = {ch["id"]: ch for ch in get(api, "settings/history")["changes"]}
    assert listed[last_change(api)]["can_undo"], "the owner's Changes offer Undo on a profile change"
    post(api, f"settings/history/{last_change(api)}/undo", {"expected_revision": get(api, "bots/ops")["revision"]})
    assert get(api, "bots/ops")["profile"]["name"] == "reviewer"
    # Leaving Custom deletes the bot's own rules; undoing that cannot bring them back, so it says so.
    set_profile(api, "ops", "custom")
    set_profile(api, "ops", "doer", confirm_drop_rules=True)
    post(api, f"settings/history/{last_change(api)}/undo", {"expected_revision": get(api, "bots/ops")["revision"]}, expected=409)
    assert get(api, "bots/ops")["profile"]["name"] == "doer"


def last_to(c, slug):
    row = c.execute("SELECT refs FROM messages WHERE recipient_id=? ORDER BY rowid DESC LIMIT 1", ("bot:" + slug,)).fetchone()
    return json.loads(row[0])


def test_a_lead_wakes_for_a_task_given_to_it_a_reviewer_for_a_review_handed_back_and_neither_for_its_own_write(api):
    from backend.tests.test_wake_rules import as_cause
    with api.app.state.store.transaction() as c:
        add_bot(c, "boss")
        put(c, "boss", P.LEAD)
        add_bot(c, "checker")
        put(c, "checker", P.REVIEWER)
        c.execute("DELETE FROM jobs")
        # An escalation filed for the lead is work now, not tomorrow's digest.
        H.task_create(c, "bot:ops", "Decide the export plan", "x", "bot:boss")
        assert last_to(c, "boss")["rule"] == "profile_lead_assignment" and jobs(c, "boss") == 1
        parent = H.task_create(c, "bot:ops", "Build 2/9", "x", "bot:cpo")
        review = H.task_create(c, "bot:ops", "QA: check it", "x", "bot:checker", parent_id=parent["id"])
        H.task_update(c, "bot:checker", review["id"], status="done", note="VERDICT: fail")
        c.execute("DELETE FROM jobs")
        # The Builder hands the review back: that is an assignment, and the reviewer wakes to look again.
        H.task_update(c, "bot:ops", review["id"], status="open", note="Fixed, please look again")
        assert last_to(c, "checker")["rule"] == "profile_reviewer_assignment" and jobs(c, "checker") == 1
        c.execute("DELETE FROM jobs")
        with as_cause("bot:checker"):
            mine = news(c, H.task(c, review["id"]) | {"status": "doing"})
        # Its own move is never a wake, whatever a profile row says.
        assert jobs(c, "checker") == 0


def test_a_lead_runs_the_task_given_to_it_but_does_not_pull_its_other_open_tasks(api):
    # `pulls: none` limits what Tico starts by itself; a task handed to the lead is a wake, claimed and started.
    with api.app.state.store.transaction() as c:
        add_bot(c, "boss")
        put(c, "boss", P.LEAD)
        idle = H.task_create(c, "bot:ops", "Someday plan", "x", "bot:boss")
        c.execute("UPDATE jobs SET state='canceled'")
        given = H.task_create(c, "bot:ops", "Decide the export plan", "x", "bot:boss")
        assert last_to(c, "boss")["rule"] == "profile_lead_assignment"
    machine = runner(api)
    assign(api, machine, "boss")
    ready(api, machine, ["boss"])
    attempt = claim(api, machine, "boss")
    assert attempt and attempt["task"]["id"] == given["id"]
    assert get(api, "tasks/" + given["id"])["task"]["status"] == "doing"
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": attempt["lane"]}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."},
         machine["token"])
    # Nothing woke it for the other task, so the lead's own pull stays off.
    assert claim(api, machine, "boss") is None
    assert get(api, "tasks/" + idle["id"])["task"]["status"] == "open"
