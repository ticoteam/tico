"""A new company's seat bots go live with no person step (backend/setup_live.py; Acme on rc.3, bugs 1 and 2).

Staff create seeds the default org: every live seat gets a bot, `planned`, and a "Set up" task for BotOps. BotOps builds
each repository on the computer and takes the bot live. On Acme the computer had not reported the Builder's repository
yet when BotOps asked, so go-live answered "building" with nothing left to finish it (bug 1), and re-attaching the
template parked the Builder in Onboarding until a person marked it complete (bug 2). Here the whole setup runs with the
doubles the suite has: BotOps' turn token, the computer's heartbeat readiness report and its claim.
"""

import uuid

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
            "problems": [] if row["bot"] in have else ["Missing bot repository or AGENT.md"],
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
    assert {"builder", "product-owner", "customer-pipeline-manager"} <= {slug for slug, _ in seats.values()}
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
    builder, _ = _seat(seats, "builder")
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


def test_botops_cannot_close_a_seat_bots_setup_task_until_the_bot_is_live(environment):
    """Acme case 1, bug 1: BotOps marked the Builder's setup done while it was still planned. Done is refused, naming
    the step, before go-live and while it waits for the repository, and taken once the bot is active."""
    api, computer, seats = _company(environment)
    attempt = _claim(api, computer, "botops")
    builder, task = _seat(seats, "builder")
    version = api.get(f"/api/v2/tasks/{task}", headers=signed_in()).json()["task"]["version"]

    def done(note):
        return act(api, attempt, "POST", f"tasks/{task}/done", {"note": note, "version": version})

    early = done("Built.")
    assert early.status_code == 409, early.text
    assert (
        early.json()["error"]["code"] == "bot_not_live"
        and "tico bot go-live builder" in early.json()["error"]["detail"]
    )
    assert act(api, attempt, "POST", "bots/builder/go-live", {}).status_code == 202
    waiting = done("Built.")
    assert waiting.status_code == 409 and "by itself" in waiting.json()["error"]["detail"], waiting.text
    _report(api, computer, present=[builder])
    assert _state(api, builder)["state"] == "active"
    finished = done("Built and live.")
    assert finished.status_code == 200, finished.text
    assert rows(api, "SELECT status FROM tasks WHERE id=?", (task,))[0]["status"] == "done"


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
    builder, _ = _seat(seats, "builder")
    _report(api, computer, present=[builder])
    assert act(api, _claim(api, computer, "botops"), "POST", "bots/builder/go-live", {}).status_code == 200
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:owner", "Add negate", "negate(x) returns -x.", "bot:" + builder)
        c.execute("UPDATE bot_config SET onboarding_state='needs_setup' WHERE bot=?", (builder,))
        before = c.execute("SELECT COALESCE(MAX(seq),0) FROM changes WHERE topic='runner'").fetchone()[0]
    turn = _turn(api, task["id"])
    assert turn["state"] == "paused" and "still onboarding" in turn["line"], turn
    health = api.get("/api/v2/health", headers=signed_in()).json()
    late = [c for c in health["checks"] if c["id"] == "queue"]
    assert not any("Not started" in str(check) for check in late), late

    marked = api.post("/api/v2/bots/builder/onboarded", json={}, headers=signed_in())
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
    "pipeline-manager": ["knowledge/pipeline.md"],
    "segment-product-owner": [
        "knowledge/segment.md",
        "knowledge/spec-format.md",
        "knowledge/launch-checklist.md",
        "knowledge/problems.md",
        "knowledge/briefs.md",
    ],
    "builder": ["knowledge/standards.md", "knowledge/patterns.md"],
    "pr-reviewer": ["knowledge/standards.md", "knowledge/patterns.md"],
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


def test_each_seat_bot_runs_its_template_setup_by_itself_with_no_person_once_live(environment):
    """Acme bug 12: a seat bot went live without its template's setup ever running (a person's first message starts it,
    and nobody chats with a seat bot), so the Product Owner wrote its first spec with no knowledge files. Going live
    now gives each seat bot its own setup task, first in its queue, from BotOps: it names the files the template's setup
    writes, says to take the defaults and ask nothing now, and raises no onboarding prompt or person ask. Once only."""
    api, computer, seats = _company(environment)
    builder, _ = _seat(seats, "builder")
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
    po = next(row for row in found if row["slug"] == "product-owner")
    assert "Product (" in rows(api, "SELECT body FROM tasks WHERE id=?", (po["task"],))[0]["body"]

    assert not rows(api, "SELECT 1 FROM events WHERE action='bot.needs_onboarding'")
    assert not rows(api, "SELECT 1 FROM messages WHERE body LIKE '%set you up%'")
    with api.app.state.store.read() as c:
        from backend import waiting_on

        asked = waiting_on.needs_by_person(c)
    assert not any(row["task"] in str(asked) for row in found if row["task"]), asked

    first = _claim(api, computer, builder)
    own = next(row["task"] for row in found if row["slug"] == "builder")
    assert first, "the Builder claims work"
    assert (first["task"]["id"], own) != (waiting["id"], own) and first["task"]["id"] == own, (first["task"], own)
    again = act(api, attempt, "POST", "bots/builder/go-live", {})
    assert again.status_code == 200 and "own_setup_task_id" not in again.json(), again.text
