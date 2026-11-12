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
from backend.tests.helpers.api import assign, claim, get, post, ready, runner  # noqa: F401  (api is a fixture)
from backend.tests.helpers.wake_rules import jobs, news
from backend import bot_settings as BS
from backend.tests.helpers.profiles import add_bot, profile_of, put


def test_migration_gives_each_bot_a_profile_by_name_once_and_tells_its_owner(api):
    slugs = {
        "engineering-manager": P.LEAD,
        "cmo": P.LEAD,
        "release-manager": P.LEAD,
        "sentry-triage": P.MONITOR,
        "uptime-monitor": P.MONITOR,
        "qa-web": P.REVIEWER,
        "security-audit": P.REVIEWER,
        "pr-reviewer-sol": P.REVIEWER,
        "botops": P.CUSTOM,
        "general-manager": P.CUSTOM,
        "writer": P.DOER,
    }
    with api.app.state.store.transaction() as c:
        for slug in slugs:
            add_bot(c, slug)
        add_bot(c, "saver", wake_preset="token_saver")
        add_bot(c, "grok-designer", harness="grokbot")
        add_bot(c, "reviewer")
        add_bot(c, "builder")
        BS.update(c, 'cpo', {'profile': 'doer'})
        told = P.migrate(c)
        got = {slug: profile_of(c, slug) for slug in (*slugs, "saver", "grok-designer", "reviewer", "cpo")}
        assert {slug: got[slug]["profile"] for slug in slugs} == slugs
        assert got["grok-designer"]["profile"] == P.RESPONDER and got["cpo"]["profile"] == "doer"
        assert got["reviewer"]["profile"] == P.REVIEWER
        assert profile_of(c, "builder")["profile"] == P.DOER
        # A former Token saver bot keeps its 30-minute window as a value of its own; Performance is the Doer's.
        assert got["saver"]["profile"] == P.DOER and got["saver"]["wake_preset"] == "token_saver"
        assert (
            got["saver"]["profile_options"]["batch_seconds"] == 1800 and got["writer"]["wake_preset"] == "performance"
        )
        assert told > 12
        notes = [m for m in H.notices(c, "human:ana") if "gave each of your bots a profile" in m["body"]]
        assert len(notes) == 1 and "engineering-manager" in notes[0]["body"] and "Lead" in notes[0]["body"]
        # Run again: nothing changes and nobody is told twice.
        revision = c.execute("SELECT sum(revision) FROM bot_config").fetchone()[0]
        assert P.migrate(c) == 0 and c.execute("SELECT sum(revision) FROM bot_config").fetchone()[0] == revision
        assert len([m for m in H.notices(c, "human:ana") if "gave each of your bots a profile" in m["body"]]) == 1


def turn_for(api, slug):
    """What a routine for `slug` is today: an automation whose Bot turn step is the bot's. It runs on a task made for
    another bot; returns its run's status once no task for `slug` was opened by it."""
    aid = post(
        api,
        "automations",
        {
            "name": "Notes",
            "owner": "person:ana",
            "trigger": {"type": "event", "name": "task.created"},
            "steps": [{"id": "notes", "type": "bot_turn", "bot": "bot:" + slug, "prompt": "Write the notes."}],
        },
    )["id"]
    post(api, "tasks", {"owner": "cpo", "title": "Start the notes", "body": "Please."})
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


WIDE_GAP_NOT_RUNS = [
    "Finish writing production acceptance tests",
    "Complete building production acceptance tests",
    "Check and update production acceptance tests",
    "Verify and add production acceptance tests",
    "Run writing production acceptance tests",
]
# Review 1 (PR 2599): a change verb in the gap is work on the acceptance tests, not a run, at either scope.
# Each verb the gap rejects in Review 1 fixes, in its base and -ing form, so removing any one from the gap fails a test.
GAP_VERBS_ADDED = ["change", "edit", "modify", "fix", "remove", "delete", "rewrite", "revise", "extend", "refactor"]
GAP_VERB_FORMS = [
    form for verb in GAP_VERBS_ADDED for form in (verb, verb[:-1] + "ing" if verb.endswith("e") else verb + "ing")
]
CHANGE_VERB_NOT_RUNS = [f"Finish {form} production acceptance tests" for form in GAP_VERB_FORMS] + [
    "Run changing production acceptance tests",
    "Verify and modify production acceptance tests",
    "Complete fixing production acceptance tests",
]


_ORDINARY = [
    ("Build the thing", False),
    ("QA: orphan review", False),
    ("Implement acceptance criteria", False),
    ("Implement acceptance criteria", True),
    ("Release checklist planning", False),
    ("Post-release documentation", True),
    pytest.param("Release QA dashboard redesign", False, id="qa-dashboard"),
    pytest.param("Release QA checklist planning", False, id="qa-planning"),
    pytest.param("Post Release QA dashboard redesign", False, id="post-qa-dashboard"),
    pytest.param("Add acceptance tests for CSV export", True, id="acceptance-implementation"),
    pytest.param("Implement production acceptance tests", True, id="production-acceptance-implementation"),
    pytest.param("Implement production acceptance tests", False, id="production-acceptance-top-level-implement"),
    pytest.param(
        "Develop production acceptance tests for the waitlist", False, id="production-acceptance-top-level-develop"
    ),
    pytest.param("Add production acceptance tests for CSV export", False, id="production-acceptance-top-level-add"),
    pytest.param(
        "Write production acceptance tests for the waitlist", False, id="production-acceptance-top-level-write"
    ),
    pytest.param("Build the production acceptance suite", False, id="production-acceptance-top-level-build"),
    pytest.param("Update the production acceptance checklist", False, id="production-acceptance-top-level-update"),
    pytest.param("Waitlist production acceptance tests", False, id="production-acceptance-top-level-no-run-verb"),
    pytest.param("Production acceptance checklist update", False, id="production-acceptance-top-level-prefix-update"),
    pytest.param(
        "Production acceptance tests for the waitlist", False, id="production-acceptance-top-level-prefix-tests"
    ),
    pytest.param("Check the production acceptance checklist", False, id="production-acceptance-top-level-check-list"),
    pytest.param("Verify production acceptance checklist", False, id="production-acceptance-top-level-verify-list"),
    pytest.param("Complete the production acceptance plan", False, id="production-acceptance-top-level-complete-plan"),
    pytest.param("Run production acceptance dashboard", False, id="production-acceptance-top-level-run-dashboard"),
    pytest.param("Build the production acceptance suite", True, id="production-acceptance-child-build"),
    pytest.param("Update the production acceptance checklist", True, id="production-acceptance-child-update"),
    pytest.param("Run production acceptance dashboard", True, id="production-acceptance-child-run-dashboard"),
    pytest.param("Production acceptance checklist update", True, id="production-acceptance-child-prefix-update"),
    # Product (34f2faaf): build, write, add, update and the other work verbs, and "and"/"or", may not sit between the
    # run verb and the phrase; these five match the old two-word gap, so they stay ordinary, top-level and parent-linked.
    *[
        pytest.param(t, child, id=f"production-acceptance-gap-{i}-{'child' if child else 'top'}")
        for i, t in enumerate(WIDE_GAP_NOT_RUNS)
        for child in (False, True)
    ],
    *[
        pytest.param(t, child, id=f"production-acceptance-change-{i}-{'child' if child else 'top'}")
        for i, t in enumerate(CHANGE_VERB_NOT_RUNS)
        for child in (False, True)
    ],
]


RELEASE_QA_TITLES = [
    ("Post Release QA pass: v1.2.3", False),
    ("Post Launch QA: runtime installation", False),
    ("Post Release QA sweep", False),
    ("Post-release checks: shared question card", True),
    ("Hosting preview: carry remaining installed-build and live acceptance", True),
    ("Review 2: legacy request", True),
    ("Security: legacy request", True),
]
ORDINARY_TASKS = [p.values if hasattr(p, "values") else p for p in _ORDINARY]
KEPT = [
    ("Build the thing", False),
    ("Implement acceptance criteria", True),
    ("Run production acceptance dashboard", True),
]


TOP_LEVEL_RUNS = [
    "Run waitlist production acceptance after existing release and admin gates clear",
    "Execute production acceptance for the waitlist",
    "Re-run production acceptance on the release candidate",
    "Production acceptance run for the waitlist",
    "Production acceptance run: waitlist release",
    "Run production acceptance tests for the waitlist",
    "Run the production acceptance suite",
]
PARENT_LINKED_RUNS = [
    "Run production acceptance for the waitlist",
    "Run the production acceptance suite",
    "Run waitlist production acceptance after existing release and admin gates clear",
    "Execute production acceptance for the waitlist",
    "Production acceptance run for the waitlist",
]


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
        # A person's message wakes it at once.
        assert R.judge(c, "human:ana", "bot:boss", "message", {}, "Status?", None).outcome == "wake"
        # The digest itself is held news let go: the lead's batch-everything row must not hold it again.
        digest = R.judge(c, H.KEEPER, "bot:boss", "message", {"wake_digest": [job["id"]]}, "Held updates", None)
        assert (digest.outcome, digest.rule) == ("wake", "batch_digest")
        c.execute("DELETE FROM jobs")
        # A task it asked for that ended is news for its next run, as for every bot: nothing wakes a bot about a Done
        # or Closed task (task_effects._tell_requester).
        H.task_close(c, "bot:ops", mine["id"], "Nobody to ask.", H.task(c, mine["id"])["version"])
        told = last_to(c, "boss")
        assert (told["rule"], told.get("quiet"), jobs(c, "boss")) == ("caller_quiet", True, 0)
        assert task["id"]


def test_digest_time_is_the_next_hour_in_team_time():
    # 2026-10-24 12:00 UTC is 05:00 in Los Angeles (PDT): the digest at 09:00 is the same day, 16:00 UTC.
    assert W.digest_time("2026-10-24T12:00:00.000000Z", 9) == "2026-10-24T16:00:00.000000Z"
    assert W.digest_time("2026-10-24T17:00:00.000000Z", 9) == "2026-10-25T16:00:00.000000Z"


def set_profile(api, bot, name, token="ana-test", expected=200, **extra):
    revision = get(api, "bots/" + bot)["revision"]
    return post(
        api,
        f"bots/{bot}/profile",
        {"profile": name, "expected_revision": revision, **extra},
        token=token,
        expected=expected,
    )


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
    post(
        api,
        "bots/ops/wake-preset",
        {"wake_preset": "token_saver", "expected_revision": get(api, "bots/ops")["revision"]},
    )
    assert get(api, "bots/ops")["profile"]["name"] == "lead"


def test_custom_copies_the_rows_and_leaving_it_needs_a_confirm(api):
    set_profile(api, "ops", "reviewer")
    custom = set_profile(api, "ops", "custom")
    assert custom["rules"] >= 3 and custom["profile"]["name"] == "custom"
    with api.app.state.store.read() as c:
        # The copied rows are in the bot's What wakes automation, where the wake engine reads them.
        assert redesign_migration.wake_rules_by(c, "bot", "ops", "profile") == custom["rules"]
        assert (
            len([r for r in redesign_migration.wake_rows(c) if r["scope_id"] == "ops" and r["created_by"] == "profile"])
            == custom["rules"]
        )
    set_profile(api, "ops", "doer", expected=409)
    back = set_profile(api, "ops", "doer", confirm_drop_rules=True)
    assert back["rules"] == 0 and back["profile"]["name"] == "doer"


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
    tool = hubtools.BY_NAME["tico_bot_profile"]["fn"]
    assert tool(api, {"bot": "ops"})["choices"] == [{"name": "doer"}]
    tool(api, {"bot": "ops", "profile": "lead", "digest_hour": 7})
    assert api.posts == [("bots/ops/profile", {"digest_hour": 7, "profile": "lead", "expected_revision": 4})]


def test_the_profiles_catch_all_never_beats_a_team_or_type_rule_and_a_leads_own_writes_are_not_digested(api):
    from backend.tests.helpers.wake_rules import as_cause

    with api.app.state.store.transaction() as c:
        add_bot(c, "checker")
        put(c, "checker", P.REVIEWER)
        add_bot(c, "boss")
        put(c, "boss", P.LEAD)
        c.execute(
            "INSERT INTO rules(id,scope_kind,scope_id,event,condition,then_outcome,params,enabled,position,"
            "created_by,created_at,updated_at,revision) VALUES('team-wake','team','','status_or_relation','{}','wake','{}',"
            "1,1,'human:ana',?,?,1)",
            (H.now(), H.now()),
        )
        parent = H.task_create(c, "bot:ops", "Build 1/9", "x", "bot:cpo")
        review = H.task_create(c, "bot:ops", "Live acceptance: check it", "x", "bot:checker", parent_id=parent["id"])
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
        child = H.task_create(c, "bot:ops", "Live acceptance: check it", "x", "bot:cpo", parent_id=parent["id"])
        plain = H.task_create(c, "bot:ops", "Fix the thing", "x", "bot:cpo")
        H.task_update(c, "bot:ops", child["id"], owner="bot:checker", seen_version=H.task(c, child["id"])["version"])
        with pytest.raises(H.Refused) as raised:
            H.task_update(c, "bot:ops", plain["id"], owner="bot:checker", seen_version=plain["version"])
        assert raised.value.rule == "profile"
        assert routines.create(c, "human:ana", "checker", {"title": "Weekly sweep", "cron": "0 9 * * 1"}) is not None


@pytest.mark.parametrize(("title", "child"), RELEASE_QA_TITLES)
def test_a_reviewer_is_given_each_documented_review_and_qa_request(api, title, child):
    with api.app.state.store.transaction() as c:
        add_bot(c, "checker")
        put(c, "checker", P.REVIEWER)
        parent = H.task_create(c, "bot:ops", "Build checkout", "x", "bot:cpo")["id"] if child else None
        made = H.task_create(c, "bot:ops", title, "x", "bot:checker", parent_id=parent)
        assert P.is_review_task(c, H.task(c, made["id"])) and not P.mismatches(c)


def test_review_work_is_the_review_label_not_the_title(api):
    """Review 1/2, QA and Security subtasks are filed with the review label; from then on the label, not the title,
    says the task is review work: at creation, reassignment and in Health."""
    with api.app.state.store.transaction() as c:
        add_bot(c, "checker")
        put(c, "checker", P.REVIEWER)
        parent = H.task_create(c, "bot:ops", "Build checkout", "x", "bot:cpo")["id"]
        for title in ("Review 1: checkout", "QA: checkout", "Security: checkout"):
            made = H.task_create(c, "bot:ops", title, "x", "bot:checker", parent_id=parent)
            assert P.REVIEW_LABEL in H.task_labels(H.task(c, made["id"]))
        # Any title, explicitly labelled: created for the reviewer, or given to it later.
        H.task_create(c, "bot:ops", "Look over checkout", "x", "bot:checker", labels=[P.REVIEW_LABEL])
        later = H.task_create(c, "bot:ops", "Check the checkout copy", "x", "bot:cpo", labels=[P.REVIEW_LABEL])
        H.task_update(c, "bot:ops", later["id"], owner="bot:checker", seen_version=later["version"])
        assert not P.mismatches(c)
        # The title is a label for people: the same words without a parent, or unlabelled, are ordinary work.
        with pytest.raises(H.Refused):
            H.task_create(c, "bot:ops", "QA: orphan review", "x", "bot:checker")
        plain = H.task_create(c, "bot:ops", "Look over billing", "x", "bot:cpo", parent_id=parent)
        with pytest.raises(H.Refused):
            H.task_update(c, "bot:ops", plain["id"], owner="bot:checker", seen_version=plain["version"])


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
        aid = post(
            api,
            "automations",
            {
                "name": "Sweep " + slug,
                "owner": "person:ana",
                "trigger": {"type": "event", "name": "task.created"},
                "steps": [{"id": "sweep", "type": "bot_turn", "bot": "bot:" + slug, "prompt": "Check the watchlist."}],
            },
        )["id"]
        post(api, "tasks", {"owner": "cpo", "title": "Start " + slug, "body": "Please."})
        api.app.state.store.automations.drain()
        runs = get(api, f"automations/{aid}/runs")["runs"]
        assert runs and runs[0]["status"] != "failed", runs
        with api.app.state.store.read() as c:
            assert c.execute(
                "SELECT 1 FROM tasks WHERE owner_id=? AND title=?", ("bot:" + slug, "Sweep " + slug)
            ).fetchone()


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
        return c.execute(
            "SELECT id FROM settings_changes WHERE bot=? AND field='profile' ORDER BY rowid DESC", (bot,)
        ).fetchone()[0]


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
    post(
        api,
        f"settings/history/{last_change(api)}/undo",
        {"expected_revision": get(api, "bots/ops")["revision"]},
        expected=409,
    )
    assert get(api, "bots/ops")["profile"]["name"] == "doer"


def last_to(c, slug):
    row = c.execute(
        "SELECT refs FROM messages WHERE recipient_id=? ORDER BY rowid DESC LIMIT 1", ("bot:" + slug,)
    ).fetchone()
    return json.loads(row[0])
