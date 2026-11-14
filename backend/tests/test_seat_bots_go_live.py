"""A new company's seat bots go live with no person step (backend/setup_live.py; Acme on rc.3, bugs 1 and 2).

Staff create seeds the default org: every live seat gets a bot, `planned`, and a "Set up" task for BotOps. BotOps builds
each repository on the computer and takes the bot live. On Acme the computer had not reported the Builder's repository
yet when BotOps asked, so go-live answered "building" with nothing left to finish it (bug 1), and re-attaching the
template parked the Builder in Onboarding until a person marked it complete (bug 2). Here the whole setup runs with the
doubles the suite has: BotOps' turn token, the computer's heartbeat readiness report and its claim.
"""

import json
import uuid
from pathlib import Path

from backend import never_stuck
from backend.store import H
from backend.tests.helpers.botops_parity import act
from backend.tests.helpers.sample_company import booted, environment, machine, rows, signed_in  # noqa: F401  (fixtures)


def _post(api, path, body, token):
    return api.post(
        "/api/v2/" + path,
        json=body,
        headers={"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid.uuid4())},
    )


def _report(api, computer, present=()):
    """The computer's heartbeat: every bot assigned to it, BotOps' repository and those in `present` there, the rest
    not cloned yet."""
    have = {"botops", *present}
    readiness = {
        row["bot"]: {
            "ready": row["bot"] in have,
            "runtime": "codex",
            "repository_present": row["bot"] in have,
            "configuration_valid": True,
            "problems": [] if row["bot"] in have else ["Missing bot repository or AGENTS.md"],
        }
        for row in rows(api, "SELECT bot FROM assignments WHERE runner_id=?", (computer["runner_id"],))
    }
    done = _post(
        api,
        "runners/heartbeat",
        {"version": "test", "platform": "test", "capacity": 4, "readiness": {"schema_version": 1, "bots": readiness}},
        computer["token"],
    )
    assert done.status_code == 200, done.text


def _claim(api, computer, bot):
    found = _post(api, "jobs/claim", {"bot": bot, "start_places": True}, computer["token"])
    assert found.status_code == 200, found.text
    return found.json()["attempt"]


def _company(environment):
    """A company made the way staff create makes one: setup completed with no chart (the default org), then its
    computer. Returns the client, the computer and {bot id: (slug, setup task id)} for the seat bots."""
    api = environment()
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    computer = machine(api)
    found = rows(
        api,
        "SELECT b.id, b.slug, json_extract(s.extra,'$.setup_task_id') AS task FROM bots b "
        "JOIN bot_settings s ON s.bot_id=b.id WHERE json_extract(s.extra,'$.goes_live_on_setup')",
    )
    seats = {row["id"]: (row["slug"], row["task"]) for row in found}
    assert {"feature-owner", "product-manager", "success-manager"} <= {slug for slug, _ in seats.values()}
    assert all(task for _, task in seats.values())  # each one is BotOps' "Set up" task
    _report(api, computer)
    return api, computer, seats


def _seat(seats, slug):
    return next((bot, task) for bot, (name, task) in seats.items() if name == slug)


def _state(api, bot):
    return rows(
        api, "SELECT b.state, bc.onboarding_state FROM bots b JOIN bot_config bc ON bc.bot=b.id WHERE b.id=?", (bot,)
    )[0]


def _turn(api, task_id):
    return api.get(f"/api/v2/tasks/{task_id}", headers=signed_in()).json()["task"]["turn"]


def test_every_seat_bot_goes_live_by_itself_once_its_computer_has_the_repository_and_takes_work(environment):
    """Bugs 1 and 2: BotOps takes each seat bot live before the computer has reported its repository. Each go-live
    waits (202) and the computer's report finishes it: every seat bot ends active, not onboarding, and the Engineering
    task waiting on the Builder starts by itself, with nobody calling go-live again or marking onboarding complete."""
    api, computer, seats = _company(environment)
    builder, _ = _seat(seats, "feature-owner")
    with api.app.state.store.transaction() as c:
        # The Product pipeline files into Engineering while the Builder is still being set up (Acme's E-2).
        task = H.task_create(
            c, "human:owner", "Add negate", "negate(x) returns -x.", "bot:" + builder, allow_planned=True
        )
    attempt = _claim(api, computer, "botops")
    assert attempt, "BotOps claims its setup work"
    for bot, (slug, _) in seats.items():
        live = act(api, attempt, "POST", f"bots/{slug}/go-live", {})
        assert live.status_code == 202, (slug, live.text)
        assert live.json()["state"] == "waiting_for_repository" and _state(api, bot)["state"] == "planned"
    turn = _turn(api, task["id"])
    assert turn["state"] == "paused" and "still being set up" in turn["line"], turn  # Acme case 1, bug 2

    _report(api, computer, present=list(seats))
    for bot, (slug, _) in seats.items():
        state = _state(api, bot)
        assert state["state"] == "active" and not state["onboarding_state"], (slug, state)
    assert not rows(api, "SELECT 1 FROM events WHERE action='bot.needs_onboarding'")
    assert _turn(api, task["id"])["state"] == "bot"
    work = _claim(api, computer, builder)  # its own setup first (bug 12), then this task
    assert work and work["task"]["title"] == "Set yourself up", work


def test_botops_building_a_seat_bot_without_its_slug_gets_that_bot_and_hires_no_second_one(environment):
    """Tico Team on rc.7: BotOps ran `tico bot create --template growth-manager --name "Growth Manager"` on the Growth
    Manager's own "Set up" task, without the slug. The server picked `growth-manager-2`, made a second bot with the
    template's three routines, and BotOps archived it 14 seconds later. The bot the task is for answers instead; the
    task says its slug is registered; and filing the tasks while BotOps had not started recorded no refusal."""
    api, computer, seats = _company(environment)
    assert not rows(api, "SELECT 1 FROM events WHERE action='refused'") and not rows(api, "SELECT 1 FROM refusals")
    attempt = _claim(api, computer, "botops")
    task = attempt["task"]["id"]
    bot, slug = next((bot, slug) for bot, (slug, setup) in seats.items() if setup == task)
    template = rows(api, "SELECT template FROM bot_settings WHERE bot_id=?", (bot,))[0]["template"]
    assert f"`tico bot create {slug} --template {template}`" in attempt["task"]["body"]
    before = rows(api, "SELECT id FROM bots ORDER BY id")
    name = rows(api, "SELECT display_name FROM bots WHERE id=?", (bot,))[0]["display_name"]
    made = act(api, attempt, "POST", "bots/register", {"template": template, "display_name": name})
    assert made.status_code == 200, made.text
    assert (made.json()["slug"], made.json()["created"], made.json()["id"]) == (slug, False, bot)
    assert rows(api, "SELECT id FROM bots ORDER BY id") == before
    # Another template is not that bot: it is a new one, as before.
    other = next(t for t in ("builder", "qa-owner") if t != template)
    new = act(api, attempt, "POST", "bots/register", {"template": other, "display_name": "Second pair of hands"})
    assert new.status_code == 200 and new.json()["created"] and new.json()["slug"] == "second-pair-of-hands", new.text


def test_archiving_a_bot_removes_the_routines_it_owns(environment):
    """Tico Team on rc.7: the archived second Growth Manager left its three routines listed in Automations, off, owned
    by a bot that was gone. Archiving deletes a bot's routines (archive_bot), and a routine is an automation it owns."""
    api, computer, seats = _company(environment)
    attempt = _claim(api, computer, "botops")
    seeded = _seed(api, attempt, seats)
    release, _ = _seat(seats, "release-owner")
    mine = [aid for aid, bot in seeded.items() if bot == release]
    assert len(mine) == 3
    revision = rows(api, "SELECT revision FROM bot_config WHERE bot=?", (release,))[0]["revision"]
    gone = api.post("/api/v2/bots/release-owner/archive", json={"expected_revision": revision}, headers=signed_in())
    assert gone.status_code == 200, gone.text
    left = rows(api, "SELECT id, enabled FROM automations WHERE owner_id=? AND deleted_at IS NULL", ("bot:" + release,))
    assert left == []
    listed = api.get("/api/v2/automations", headers=signed_in()).text
    assert not [aid for aid in mine if aid in listed]
    assert all(aid in listed for aid, bot in seeded.items() if bot != release)


def test_botops_cannot_close_a_seat_bots_setup_task_until_the_bot_is_live(environment):
    """Acme case 1, bug 1: BotOps marked the Builder's setup done while it was still planned. Done is refused, naming
    the step, before go-live and while it waits for the repository, and taken once the bot is active."""
    api, computer, seats = _company(environment)
    attempt = _claim(api, computer, "botops")
    builder, task = _seat(seats, "feature-owner")
    version = api.get(f"/api/v2/tasks/{task}", headers=signed_in()).json()["task"]["version"]

    def done(note):
        return act(api, attempt, "POST", f"tasks/{task}/done", {"note": note, "version": version})

    early = done("Built.")
    assert early.status_code == 409, early.text
    assert (
        early.json()["error"]["code"] == "bot_not_live"
        and "tico bot go-live feature-owner" in early.json()["error"]["detail"]
    )
    assert act(api, attempt, "POST", "bots/feature-owner/go-live", {}).status_code == 202
    waiting = done("Built.")
    assert waiting.status_code == 409 and "by itself" in waiting.json()["error"]["detail"], waiting.text
    _report(api, computer, present=[builder])
    assert _state(api, builder)["state"] == "active"
    finished = done("Built and live.")
    assert finished.status_code == 200, finished.text
    assert rows(api, "SELECT status FROM tasks WHERE id=?", (task,))[0]["status"] == "done"


def _version(api, task_id):
    return api.get(f"/api/v2/tasks/{task_id}", headers=signed_in()).json()["task"]["version"]


def _open_snoozes(api, task_id):
    return rows(api, "SELECT id FROM snoozes WHERE task_id=? AND ended_at IS NULL", (task_id,))


def test_going_live_ends_the_wait_botops_set_on_the_setup_task_and_wakes_it_to_say_done(environment):
    """BotOps snoozes a seat's setup task while the bot waits for its repository ("waiting to go live"). When the bot
    goes live the wait ends at once, BotOps is woken on that task, and its done closes it."""
    api, computer, seats = _company(environment)
    attempt = _claim(api, computer, "botops")
    builder, task = _seat(seats, "feature-owner")
    assert act(api, attempt, "POST", "bots/feature-owner/go-live", {}).status_code == 202
    snoozed = act(
        api,
        attempt,
        "POST",
        f"tasks/{task}/snooze",
        {"why": "waiting to go live", "until": "2099-01-01T00:00:00Z", "version": _version(api, task)},
    )
    assert snoozed.status_code == 200, snoozed.text
    assert len(_open_snoozes(api, task)) == 1
    _report(api, computer, present=[builder])
    assert _state(api, builder)["state"] == "active"
    assert _open_snoozes(api, task) == []
    with api.app.state.store.transaction() as c:
        never_stuck.check(c)  # the pass that wakes whoever has the turn on a task that is due
    woken = rows(api, "SELECT recipient_id FROM messages WHERE wake_key LIKE ?", (task + ":%",))
    assert "bot:botops" in {row["recipient_id"] for row in woken}, woken
    finished = act(
        api, attempt, "POST", f"tasks/{task}/done", {"note": "Built and live.", "version": _version(api, task)}
    )
    assert finished.status_code == 200, finished.text
    assert rows(api, "SELECT status FROM tasks WHERE id=?", (task,))[0]["status"] == "done"


def test_botops_cannot_snooze_a_setup_task_once_its_bot_is_live(environment):
    """BotOps read the Builder before it went live and snoozed its setup task afterwards. Nothing would end that wait,
    so the snooze is refused, and BotOps marks the task done."""
    api, computer, seats = _company(environment)
    attempt = _claim(api, computer, "botops")
    builder, task = _seat(seats, "feature-owner")
    assert act(api, attempt, "POST", "bots/feature-owner/go-live", {}).status_code == 202
    _report(api, computer, present=[builder])
    assert _state(api, builder)["state"] == "active"
    refused = act(
        api,
        attempt,
        "POST",
        f"tasks/{task}/snooze",
        {"why": "waiting to go live", "until": "2099-01-01T00:00:00Z", "version": _version(api, task)},
    )
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "bot_live", refused.text
    assert _open_snoozes(api, task) == []
    finished = act(
        api, attempt, "POST", f"tasks/{task}/done", {"note": "Built and live.", "version": _version(api, task)}
    )
    assert finished.status_code == 200, finished.text


def test_going_live_leaves_a_snooze_a_person_set_on_the_setup_task(environment):
    """A snooze a person sets on the setup task is theirs to end: the bot going live does not end it."""
    api, computer, seats = _company(environment)
    attempt = _claim(api, computer, "botops")
    builder, task = _seat(seats, "feature-owner")
    assert act(api, attempt, "POST", "bots/feature-owner/go-live", {}).status_code == 202
    mine = api.post(
        f"/api/v2/tasks/{task}/snooze",
        json={"why": "I will look first", "version": _version(api, task)},
        headers=signed_in(),
    )
    assert mine.status_code == 200, mine.text
    _report(api, computer, present=[builder])
    assert _state(api, builder)["state"] == "active"
    assert len(_open_snoozes(api, task)) == 1


def test_a_go_live_for_a_template_bot_nothing_has_built_yet_ends_live_not_planned(environment):
    """A bot with a template, no build and no setup task: before, go-live re-attached the template, answered "building"
    and left the bot planned for good. A starter template now goes live at once and its computer builds the
    repository."""
    api, computer, seats = _company(environment)
    bot, _ = _seat(seats, "qa-owner")
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE bot_settings SET extra=json_remove(extra,'$.setup_task_id','$.goes_live_on_setup') WHERE bot_id=?",
            (bot,),
        )
    live = api.post("/api/v2/bots/qa-owner/go-live", json={"setup": False}, headers=signed_in())
    assert live.status_code == 200, live.text
    assert live.json()["building"] and live.json()["activated"]
    assert _state(api, bot)["state"] == "active"


def test_a_bot_held_by_onboarding_says_so_and_claims_the_moment_it_is_marked_complete(environment):
    """Acme case 1, bugs 2 and 3, and Acme bug 13: a bot a person must still mark onboarded shows its tasks as Paused
    with that cause, not as its turn, and Health does not call its held queue a late start; marking it complete tells
    its computer there is work at once instead of on its next poll."""
    from backend import task_wakes as W

    api, computer, seats = _company(environment)
    builder, _ = _seat(seats, "feature-owner")
    _report(api, computer, present=[builder])
    assert act(api, _claim(api, computer, "botops"), "POST", "bots/feature-owner/go-live", {}).status_code == 200
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:owner", "Add negate", "negate(x) returns -x.", "bot:" + builder)
        c.execute("UPDATE bot_config SET onboarding_state='needs_setup' WHERE bot=?", (builder,))
        before = c.execute("SELECT COALESCE(MAX(seq),0) FROM changes WHERE topic='runner'").fetchone()[0]
    turn = _turn(api, task["id"])
    assert turn["state"] == "paused" and "still onboarding" in turn["line"], turn
    health = api.get("/api/v2/health", headers=signed_in()).json()
    late = [c for c in health["checks"] if c["id"] == "queue"]
    assert not any("Not started" in str(check) for check in late), late

    marked = api.post("/api/v2/bots/feature-owner/onboarded", json={}, headers=signed_in())
    assert marked.status_code == 200, marked.text
    assert rows(
        api, "SELECT 1 FROM changes WHERE topic='runner' AND kind='work' AND bot=? AND seq>?", (builder, before)
    )
    with api.app.state.store.read() as c:
        assert W.cannot_run(c, "bot:" + builder) is None
    work = _claim(api, computer, builder)
    assert work and work["task"]["title"] in ("Set yourself up", "Add negate"), work


# What each default-org seat's template setup writes (its `playbooks/onboarding.md`, "## 4. Record").
SETUP_FILES = {
    "product-manager": [
        "knowledge/roadmap.md",
        "knowledge/team.md",
        "knowledge/decision-log.md",
        "knowledge/pipeline.md",
    ],
    "engineering-manager": ["knowledge/pipeline.md"],
    "growth-manager": [
        "knowledge/workstreams.md",
        "knowledge/team.md",
        "knowledge/pipeline-rules.md",
        "knowledge/routing.md",
        "knowledge/pipeline.md",
    ],
    "success-manager": ["knowledge/team.md", "knowledge/pipeline.md"],
    "feature-owner": [
        "knowledge/area.md",
        "knowledge/standards.md",
        "knowledge/spec-format.md",
        "knowledge/launch-checklist.md",
        "knowledge/problems.md",
        "knowledge/pitches.md",
        "knowledge/patterns.md",
    ],
    "reviewer": ["knowledge/standards.md", "knowledge/patterns.md"],
    "qa-owner": ["knowledge/thresholds.md", "knowledge/suites.md"],
    "release-owner": [
        "knowledge/thresholds.md",
        "knowledge/merge-gates.md",
        "knowledge/versioning.md",
        "knowledge/voice.md",
        "knowledge/labels.md",
    ],
    "legal-owner": [
        "knowledge/intake.md",
        "knowledge/escalation.md",
        "knowledge/obligations.md",
        "knowledge/policies.md",
        "knowledge/requests.md",
    ],
}


CATALOG = Path(__file__).resolve().parents[2] / "templates" / "catalog"


class _Turn:
    """BotOps' `tico` client in its setup turn, answered as the person it works for (`_as_person`)."""

    def __init__(self, api, attempt):
        self.api, self.attempt = api, attempt

    def post(self, path, body):
        done = act(self.api, self.attempt, "POST", path, body)
        assert done.status_code == 200, done.text
        return done.json()


def _seed(api, attempt, seats):
    """What `tico bot create` does on each seat's setup task: the template's routines become the bot's automations, off
    (`clients/remotecli.py seed_automations`). Returns {automation id: bot id}."""
    from clients.remotecli import seed_automations

    made = {}
    for bot, (slug, _) in seats.items():
        template = rows(api, "SELECT template FROM bot_settings WHERE bot_id=?", (bot,))[0]["template"]
        ids = seed_automations(_Turn(api, attempt), slug, CATALOG / template, bot)
        assert isinstance(ids, list), (slug, ids)
        made.update({aid: bot for aid in ids})
    return made


def _on(api, ids):
    return {aid: bool(rows(api, "SELECT enabled FROM automations WHERE id=?", (aid,))[0]["enabled"]) for aid in ids}


def test_going_live_turns_on_every_routine_its_template_seeded_and_says_which(environment):
    """Globex on rc.4, bug 1: BotOps' `tico bot create` seeds a seat's routines off, straight into automations, and going
    live looked for them in the old schedules table, so none was ever turned on, and BotOps told the owner one was.
    Going live now turns on every routine the template seeded, says which on the go-live, and leaves alone one a person
    turned off before it went live."""
    api, computer, seats = _company(environment)
    attempt = _claim(api, computer, "botops")
    seeded = _seed(api, attempt, seats)
    by_bot = {}
    for aid, bot in seeded.items():
        by_bot.setdefault(bot, []).append(aid)
    release, _ = _seat(seats, "release-owner")
    assert len(by_bot[release]) == 3  # its weekly report, release notes and merge queue watch
    assert not any(_on(api, seeded).values())
    # The cause: a seat's routines are automations only, with no schedules row for the old lookup to find.
    assert not rows(api, f"SELECT 1 FROM schedules WHERE bot IN ({','.join('?' * len(seats))})", tuple(seats))
    # The owner tries one of the Release Owner's and turns it off again before it goes live: that one stays off.
    kept = by_bot[release][1]
    for enabled in (True, False):
        switched = api.patch(f"/api/v2/automations/{kept}", json={"enabled": enabled}, headers=signed_in())
        assert switched.status_code == 200, switched.text

    for slug, _ in seats.values():
        assert act(api, attempt, "POST", f"bots/{slug}/go-live", {}).status_code == 202
    _report(api, computer, present=list(seats))

    on = _on(api, seeded)
    assert {aid for aid, enabled in on.items() if not enabled} == {kept}, on
    finished = {
        row["target"]: json.loads(row["detail"])
        for row in rows(api, "SELECT target, detail FROM events WHERE action='bot.go_live_finished'")
    }
    for bot, ids in by_bot.items():
        armed = finished[bot]["routine_armed"]
        names = {rows(api, "SELECT name FROM automations WHERE id=?", (aid,))[0]["name"] for aid in ids if aid != kept}
        assert set(armed) == names, (bot, armed, names)
        shown = act(api, attempt, "GET", f"bots/{seats[bot][0]}").json()["routines"]
        assert {row["name"]: row["on"] for row in shown} == {
            rows(api, "SELECT name FROM automations WHERE id=?", (aid,))[0]["name"]: aid != kept for aid in ids
        }, shown
    # Once: a later go-live arms nothing again, so the one the owner turns off afterwards stays off.
    weekly = by_bot[release][0]
    assert api.patch(f"/api/v2/automations/{weekly}", json={"enabled": False}, headers=signed_in()).status_code == 200
    again = act(api, attempt, "POST", "bots/release-owner/go-live", {})
    assert again.status_code == 200 and again.json()["routine_armed"] == [], again.text
    assert not _on(api, [weekly])[weekly]


def test_a_seat_that_went_live_with_its_routines_off_gets_them_on_at_start_once(environment, monkeypatch):
    """Companies that went live on rc.4 have every seat routine off. The next start turns on the ones nobody has
    touched since BotOps made them, once, and leaves off one a person turned off."""
    from backend.onboarding import Onboarding

    api, computer, seats = _company(environment)
    attempt = _claim(api, computer, "botops")
    seeded = _seed(api, attempt, seats)
    with monkeypatch.context() as rc4:
        rc4.setattr(Onboarding, "arm_routines", lambda self, c, who, slug: [])
        for slug, _ in seats.values():
            assert act(api, attempt, "POST", f"bots/{slug}/go-live", {}).status_code == 202
        _report(api, computer, present=list(seats))
    assert all(_state(api, bot)["state"] == "active" for bot in seats)
    assert not any(_on(api, seeded).values())
    kept = next(iter(seeded))
    for enabled in (True, False):
        assert (
            api.patch(f"/api/v2/automations/{kept}", json={"enabled": enabled}, headers=signed_in()).status_code == 200
        )

    onboarding = api.app.state.onboarding
    with api.app.state.store.transaction() as c:
        first = onboarding.arm_live_seats(c)
    assert sorted(bot for bot, _ in first) == sorted(bot for aid, bot in seeded.items() if aid != kept)
    on = _on(api, seeded)
    assert {aid for aid, enabled in on.items() if not enabled} == {kept}, on
    assert len(rows(api, "SELECT 1 FROM events WHERE action='bot.routine_armed'")) == len(seeded) - 1
    # Idempotent: the next start turns nothing on, even one a person turned off since.
    other = next(aid for aid in seeded if aid != kept)
    assert api.patch(f"/api/v2/automations/{other}", json={"enabled": False}, headers=signed_in()).status_code == 200
    with api.app.state.store.transaction() as c:
        assert onboarding.arm_live_seats(c) == []
    assert not _on(api, [other])[other]


def test_each_seat_bot_runs_its_template_setup_by_itself_with_no_person_once_live(environment):
    """Acme bug 12: a seat bot went live without its template's setup ever running (a person's first message starts it,
    and nobody chats with a seat bot), so the Product Owner wrote its first spec with no knowledge files. Going live
    now gives each seat bot its own setup task, first in its queue, from BotOps: it names the files the template's setup
    writes, says to take the defaults and ask nothing now, and raises no onboarding prompt or person ask. Once only."""
    api, computer, seats = _company(environment)
    builder, _ = _seat(seats, "feature-owner")
    with api.app.state.store.transaction() as c:
        waiting = H.task_create(
            c, "human:owner", "Add negate", "negate(x) returns -x.", "bot:" + builder, allow_planned=True
        )
    attempt = _claim(api, computer, "botops")
    for slug, _ in seats.values():
        assert act(api, attempt, "POST", f"bots/{slug}/go-live", {}).status_code == 202
    _report(api, computer, present=list(seats))

    found = rows(
        api,
        "SELECT b.id, b.slug, s.template, (SELECT t.id FROM tasks t WHERE t.owner_id='bot:'||b.id "
        "AND t.title='Set yourself up') AS task FROM bots b JOIN bot_settings s ON s.bot_id=b.id "
        "WHERE json_extract(s.extra,'$.goes_live_on_setup')",
    )
    assert {row["template"] for row in found} >= set(SETUP_FILES)
    for row in found:
        expected = SETUP_FILES.get(row["template"])
        if expected is None:
            continue
        task = rows(api, "SELECT * FROM tasks WHERE id=?", (row["task"],))
        assert task, row
        task = task[0]
        assert task["status"] == "open" and task["owner_id"].endswith(row["id"]), (row["slug"], task)
        assert task["requester_id"].endswith("botops"), task["requester_id"]
        for name in expected:
            assert f"`{name}`" in task["body"], (row["slug"], name)
        assert "ask nothing" in task["body"] and "Not yet told:" in task["body"]
        assert _turn(api, task["id"])["state"] == "bot", row["slug"]
    # Product's stages a feature owner holds are its tasks' assignee's: its manager is told it manages it.
    pm = next(row for row in found if row["slug"] == "product-manager")
    assert "Product (you manage it" in rows(api, "SELECT body FROM tasks WHERE id=?", (pm["task"],))[0]["body"]

    assert not rows(api, "SELECT 1 FROM events WHERE action='bot.needs_onboarding'")
    assert not rows(api, "SELECT 1 FROM messages WHERE body LIKE '%set you up%'")
    with api.app.state.store.read() as c:
        from backend import waiting_on

        asked = waiting_on.needs_by_person(c)
    assert not any(row["task"] in str(asked) for row in found if row["task"]), asked

    first = _claim(api, computer, builder)
    own = next(row["task"] for row in found if row["slug"] == "feature-owner")
    assert first, "the Builder claims work"
    assert (first["task"]["id"], own) != (waiting["id"], own) and first["task"]["id"] == own, (first["task"], own)
    again = act(api, attempt, "POST", "bots/feature-owner/go-live", {})
    assert again.status_code == 200 and "own_setup_task_id" not in again.json(), again.text
