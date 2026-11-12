"""A running company is updated in place: this code starts on the database the last release made, and the company is
the one it was.

The database is a hosted company a day old, made by v0.6.0-rc.7's own code (scripts/make_company_upgrade_fixture.py):
HQ's starting chart applied at the first start, setup complete, one shared cloud computer, 21 bots (the 11 seat bots
taken live by BotOps), two reporting lines a person set by hand, tasks at different stages of Product and Engineering,
one snoozed, one waiting on a person's sign-off, a routine and an automation.

Each test starts the real app (create_app and its lifespan, start jobs included) on a copy of that file, the way a
deploy does, and compares what a person would notice. Only HQ's machines API is a double. Refresh the file from a
release tag when one ships, and keep the older ones: an install may skip releases.
"""

import copy
import gzip
import json
import shutil
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from backend import cloud_access, migrations
from backend.app import create_app
from backend.company_file import starter
from backend.execution import AWAKE_SETTLE
from backend.observability import Observability
from backend.store import H
from backend.tests.helpers.cloud_access import signals
from backend.tests.helpers.cloud_machines import FakeHQ
from backend.tests.helpers.sample_company import OWNER_EMAIL, settings_for, signed_in
from backend.tests.helpers.template_renames import CHARTS, employees_file
from backend.tests.helpers.upgrade import FIXTURES
from clients.environments import seed_registry

RELEASES = ["v0.6.0-rc.7"]
HAND_SET = {"botops": "general-manager", "design-owner": "growth-manager"}


def rows(path, sql, args=()):
    with closing(sqlite3.connect(path)) as c:
        c.row_factory = sqlite3.Row
        return [dict(row) for row in c.execute(sql, args)]


def seen(path):
    """The company as a person knows it, read straight from the file so the same reading holds before and after."""
    bots = rows(
        path,
        "SELECT b.id, b.slug, b.display_name, b.state, bc.reports_to, bc.team, bc.onboarding_state, "
        "json_extract(s.extra,'$.template') AS template FROM bots b JOIN bot_config bc ON bc.bot=b.id "
        "LEFT JOIN bot_settings s ON s.bot_id=b.id ORDER BY b.slug",
    )
    slug = {b["id"]: b["slug"] for b in bots}

    def who(actor):
        """An actor by the name a person reads: a bot's slug, or the person."""
        actor = actor or ""
        return slug.get(actor.removeprefix("bot:"), actor)

    roster = json.loads(rows(path, "SELECT value FROM registry_metadata WHERE key='people'")[0]["value"])
    return {
        "bots": {
            b["slug"]: {
                "name": b["display_name"],
                "state": b["state"],
                "manager": who(b["reports_to"]),
                "team": b["team"],
                "setup": b["onboarding_state"],
            }
            for b in bots
        },
        "groups": {g["id"]: (g["name"], g["parent"], who(g["reports_to"])) for g in roster["org_groups"].values()},
        "people": sorted(p["id"] for p in roster["people"]),
        "pipelines": {
            p["id"]: {
                "name": p["name"],
                "manager": who(p["manager_id"]),
                "stages": [
                    (s["name"], s["kind"], who(s["owner"]), s["by_hand"])
                    for s in rows(
                        path,
                        "SELECT * FROM stages WHERE pipeline_id=? AND retired_at IS NULL ORDER BY position",
                        (p["id"],),
                    )
                ],
                "requirements": rows(
                    path,
                    "SELECT s.name AS stage, r.kind, r.source, r.condition, r.who_oks, r.can_push_past FROM "
                    "requirements r JOIN stages s ON s.id=r.stage_id WHERE s.pipeline_id=? AND r.visit_id IS NULL "
                    "AND r.withdrawn_at IS NULL ORDER BY s.position, r.position",
                    (p["id"],),
                ),
            }
            for p in rows(path, "SELECT * FROM pipelines WHERE retired_at IS NULL ORDER BY id")
        },
        "tasks": {
            t["id"]: {
                "title": t["title"],
                "status": t["status"],
                "pipeline": t["pipeline_id"],
                "stage": t["stage_id"],
                "owner": who(t["owner_id"]),
                "snoozed_until": [
                    r["until_at"]
                    for r in rows(path, "SELECT * FROM snoozes WHERE task_id=? AND ended_at IS NULL", (t["id"],))
                ],
                "asks": rows(
                    path,
                    "SELECT r.kind, r.line, r.person_id FROM requirements r JOIN visits v ON v.id=r.visit_id "
                    "WHERE v.task_id=? AND v.left_at IS NULL AND r.withdrawn_at IS NULL ORDER BY r.position",
                    (t["id"],),
                ),
            }
            for t in rows(path, "SELECT * FROM tasks ORDER BY id")
        },
        "automations": sorted(
            (a["name"], who(a["owner_id"]), a["trigger_type"], a["enabled"])
            for a in rows(path, "SELECT * FROM automations WHERE deleted_at IS NULL")
        ),
        "schedules": sorted(
            (who(s["bot"]), s["title"], s["cron"])
            for s in rows(path, "SELECT * FROM schedules WHERE deleted_at IS NULL")
        ),
        "seats": sorted(
            (s["title"], who(s["filled_by"]), who(s["reports_to"]), bool(s["closed_at"]))
            for s in rows(path, "SELECT * FROM seats")
        ),
        "computers": sorted(
            (r["label"], bool(r["revoked_at"])) for r in rows(path, "SELECT label, revoked_at FROM runners")
        ),
        "placed": sorted((who(a["bot"]), a["runner_id"]) for a in rows(path, "SELECT bot, runner_id FROM assignments")),
        "cloud": sorted(
            (who(m["bot"]), m["mode"], m["placement"], m["machine_id"], m["runner_id"])
            for m in rows(path, "SELECT * FROM cloud_machines")
        ),
        "refused": rows(path, "SELECT count(*) AS n FROM refusals")[0]["n"],
        "setup_done": bool(
            json.loads(rows(path, "SELECT value FROM registry_metadata WHERE key='onboarding'")[0]["value"])[
                "completed"
            ]
        ),
    }


def intended(before):
    """The company as this release means to leave it: the one change an update makes on purpose. The Finance Owner sees
    the books by default, so the owner's open "Let the Finance Owner see the books" to-do is closed for them
    (backend/bookkeeper.py grant_view) and leaves their Needs you. Product's Ideas is no longer a stage tasks leave by
    hand: an idea moves on to Spec by itself (migration ideas_say_it_once, docs/tasks.md "Product's Ideas"). Only a
    stage still in the preset's shape is changed."""
    from backend.bookkeeper import VIEW_TITLE

    after = copy.deepcopy(before)
    for task in after["tasks"].values():
        if task["title"] == VIEW_TITLE and task["status"] == "open":
            task.update(status="closed", stage=task["stage"].rsplit("-", 1)[0] + "-closed", asks=[])
    product = after.get("pipelines", {}).get("product")
    if product:
        product["stages"] = [
            (name, kind, owner, 0 if name == "Ideas" else by_hand) for name, kind, owner, by_hand in product["stages"]
        ]
    return after


def changes(before, after, path=""):
    """Every difference between two readings, one plain line each."""
    if isinstance(before, dict) and isinstance(after, dict):
        found = []
        for key in sorted(set(before) | set(after), key=str):
            where = f"{path}/{key}" if path else str(key)
            if key not in after:
                found.append(f"{where}: gone (was {before[key]!r})")
            elif key not in before:
                found.append(f"{where}: new ({after[key]!r})")
            else:
                found += changes(before[key], after[key], where)
        return found
    if isinstance(before, list) and isinstance(after, list) and before != after:
        gone = [item for item in before if item not in after]
        new = [item for item in after if item not in before]
        return [f"{path}: gone {gone!r}, new {new!r}"] if gone or new else [f"{path}: order changed"]
    return [] if before == after else [f"{path}: {before!r} became {after!r}"]


class Company:
    """The last release's company on disk, and this code started on it."""

    def __init__(self, tmp_path, release, monkeypatch):
        self.db = tmp_path / "hub.db"
        with gzip.open(FIXTURES / f"company-{release}.sqlite.gz") as source, open(self.db, "wb") as target:
            shutil.copyfileobj(source, target)
        # A hosted server keeps its registry files and the company file HQ gave it across an update.
        seed_registry(
            tmp_path, "Owner", OWNER_EMAIL, {"company_name": "Acme", "app_name": "Tico", "assistant_name": "Assistant"}
        )
        self.settings = settings_for(tmp_path, hosted="own", enabled_providers=("anthropic", "openai"))
        self.settings.company_file_data = starter.pack(employees_file(CHARTS["0.6 hotmic"]), starter.MAX_PACKED)
        self.settings.company_file_configured = True
        self.before = seen(self.db)
        self.expected = intended(self.before)  # what the update may change on purpose
        # What the person had waiting on the last release, as its own page listed it.
        self.needs_you = set(json.loads((FIXTURES / f"company-{release}.json").read_text())["needs_you"])
        from backend.bookkeeper import VIEW_TITLE

        self.needs_you_after = self.needs_you - {VIEW_TITLE}  # closed for the owner by the update (intended)
        shared = next(m for m in self.before["cloud"] if m[0] == "_shared")
        self.hq = FakeHQ()
        self.hq.machines[shared[3]] = {
            "id": shared[3],
            "key": "_shared",
            "size": "default",
            "state": "running",
            "started_at": "2026-11-11T00:00:00Z",
            "stopped_at": None,
            "cost_month_usd": 0.0,
            "starts_month": 1,
            "last_error": "",
            "pending": None,
        }
        self.failed = []  # a start job that fails is captured, not raised: the server starts and nobody is told
        monkeypatch.setattr(
            Observability, "capture", lambda _self, source, exc=None, *a, **kw: self.failed.append((source, repr(exc)))
        )

    def start(self):
        """One start of this code on the company, and what the person's Needs you holds once it is up."""
        with TestClient(create_app(self.settings)) as api:
            cloud_access.apply(api.app.state.store, cloud_access.parse(signals("open")))
            api.app.state.cloud_machines.client = self.hq
            api.app.state.cloud_machines.tick()
            needs = api.get("/api/v2/needs-you/summary", headers=signed_in())
            assert needs.status_code == 200, needs.text
            tasks = api.get("/api/v2/tasks", headers=signed_in())
            assert tasks.status_code == 200, "The task list does not load after the update: " + tasks.text[:300]
            setup = api.get("/api/v2/setup", headers=signed_in()).json()
        assert not self.failed, f"Start jobs failed on the updated company (captured, never shown): {self.failed}"
        assert setup["company_import_failed"] is None, setup["company_import_failed"]
        tasks = self.after()["tasks"]
        titles = {
            tasks[item["task_id"]]["title"] if item.get("task_id") else f"{item['kind']}: {item['line']}"
            for group in needs.json()["by_bot"]
            for item in group["items"]
        }
        return needs.json()["total"], titles

    def after(self):
        return seen(self.db)


@pytest.fixture(params=RELEASES)
def company(request, tmp_path, monkeypatch):
    return Company(tmp_path, request.param, monkeypatch)


def test_the_fixture_is_the_company_the_journey_needs(company):
    """The file is the company described above; a refresh that loses part of it would make the tests below say less."""
    before = company.before
    assert len(before["bots"]) == 21 and {b["state"] for b in before["bots"].values()} == {"active"}
    assert len(before["pipelines"]) == 12
    assert {slug: before["bots"][slug]["manager"] for slug in HAND_SET} == HAND_SET
    stages = {t["title"]: t["stage"] for t in before["tasks"].values()}
    assert (
        stages["Export invoices as CSV"] == "product-spec"
        and stages["Rate limit the public API"] == "engineering-build"
    )
    assert [t for t in before["tasks"].values() if t["snoozed_until"]]
    assert [t for t in before["tasks"].values() if any(a["kind"] == "ok" and a["person_id"] for a in t["asks"])]
    assert "Ship the pricing page" in company.needs_you  # the sign-off waits on the person
    assert {"Friday numbers", "New signup follow-up"} <= {a[0] for a in before["automations"]}
    assert [m for m in before["cloud"] if m[0] == "_shared" and m[3] and m[4]]


def test_a_company_updated_in_place_is_the_company_it_was(company):
    """The update: this code starts on the last release's company, and then starts again (a deploy is often followed by
    a restart). Nothing a person set or a bot was doing changes; the only work it may add is named here."""
    total, titles = company.start()
    first = company.after()
    found = changes(company.expected, first)
    assert not found, "The update changed the company:\n  " + "\n  ".join(found)
    assert titles == company.needs_you_after and total == len(titles), (
        f"Needs you held {sorted(company.needs_you)} before the update and holds {sorted(titles)} after it"
    )
    # No machine is made, stopped or deleted for an update: the shared computer the company had is the one it has.
    assert [a for a in company.hq.actions() if a != "heartbeat"] == [], company.hq.actions()

    again_total, again_titles = company.start()
    found = changes(first, company.after())
    assert not found, "A second start after the update changed the company:\n  " + "\n  ".join(found)
    assert (again_total, again_titles) == (total, titles)


def test_every_bot_has_one_manager_and_the_lines_a_person_set_stay(company):
    company.start()
    after = company.after()["bots"]
    assert [slug for slug, bot in after.items() if not bot["manager"]] == []
    assert {slug: after[slug]["manager"] for slug in HAND_SET} == HAND_SET
    # One of each built-in: an update that cannot find the one a company has makes a second under a `-2` slug.
    names = [bot["name"] for bot in after.values() if bot["state"] != "archived"]
    assert sorted(names) == sorted(set(names)), sorted(n for n in names if names.count(n) > 1)


def test_no_bot_is_set_up_or_interviewed_again(company):
    """A bot's "Set yourself up" and BotOps' "Set up ..." tasks are filed once, when the bot is made."""
    before = sorted(t["title"] for t in company.before["tasks"].values() if t["title"].startswith("Set "))
    company.start()
    company.start()
    after = company.after()
    assert sorted(t["title"] for t in after["tasks"].values() if t["title"].startswith("Set ")) == before
    assert {bot["setup"] for bot in after["bots"].values()} == {bot["setup"] for bot in company.before["bots"].values()}
    assert after["refused"] == company.before["refused"], "The update recorded refusals nobody made"


def schema(path):
    return rows(path, "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")


def test_the_pending_migrations_apply_in_any_order(company, tmp_path):
    """Migrations merge in any order and an install may be anywhere between two releases, so the files this company
    has not run must give the same database whichever runs first (CLAUDE.md, Conventions)."""
    applied = {r["version"] for r in rows(company.db, "SELECT version FROM schema_migrations")}
    pending = [m for m in migrations.files() if m.version not in applied]
    results = []
    for name, order in (("oldest first", pending), ("newest first", pending[::-1])):
        path = tmp_path / (name.replace(" ", "-") + ".db")
        shutil.copy(company.db, path)
        with closing(sqlite3.connect(path, isolation_level=None)) as c:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            for migration in order:
                try:
                    migrations._run(c, migration)
                except Exception as exc:
                    pytest.fail(f"Migration {migration.path.name} fails on a running company when run {name}: {exc!r}")
            assert c.execute("PRAGMA foreign_key_check").fetchall() == [], f"{name}: rows refer to missing rows"
        results.append((schema(path), seen(path)))
    assert results[0][0] == results[1][0], "The schema depends on the order the pending migrations ran in"
    assert not changes(results[0][1], results[1][1])
    assert not changes(company.before, results[0][1]), "The pending migrations changed the company"


REMOVED = ("growth-manager", "legal-owner", "segment-product-owner")  # live seat bots' templates in the fixture


def test_a_bot_whose_template_the_new_release_no_longer_ships_keeps_working(company, tmp_path):
    """A release renames and removes templates. A running company's bots made from one that is gone are still its bots:
    the server starts, each is still there with its manager and its work, its page opens and it can be handed a task."""
    catalog = tmp_path / "catalog"
    shutil.copytree(company.settings.catalog_dir, catalog, ignore=shutil.ignore_patterns(*REMOVED))
    company.settings.catalog_dir = catalog
    company.start()
    company.start()
    found = changes(company.expected, company.after())
    assert not found, "Starting without three templates changed the company:\n  " + "\n  ".join(found)
    with TestClient(create_app(company.settings)) as api:
        for slug in ("growth-manager", "legal-owner", "creators-product-owner"):
            page = api.get(f"/api/v2/bots/{slug}", headers=signed_in())
            assert page.status_code == 200, f"{slug}'s page does not open once its template is gone: {page.text[:300]}"
            task = api.post(
                "/api/v2/tasks",
                json={"title": "Say hello", "body": "After the update.", "owner": slug},
                headers=signed_in(),
            )
            assert task.status_code == 200, (
                f"{slug} cannot be handed a task once its template is gone: {task.text[:300]}"
            )
        for path in ("employees", "v2/pipelines", "v2/groups", "v2/onboarding", "v2/setup"):
            read = api.get("/api/" + path, headers=signed_in())
            assert read.status_code == 200, f"/api/{path} fails once a live bot's template is gone: {read.text[:300]}"
    assert not company.failed, company.failed


COMPUTER_TOKEN = "the-shared-computer-test-token-0123456789"
# One turn as a runner sends it (contracts/runner/fixtures, the kept release's copy), in order.
TURN = ["heartbeat.ok", "claim.attempt", "start_place.reserved", "started.ok", "events.ok", "renew.ok", "complete.ok"]


def test_the_computer_still_on_the_last_release_keeps_working_until_it_updates(company):
    """The server is updated first; the company's computer runs the last release's runner until it updates itself. Its
    requests, as that release recorded them, are still taken: its heartbeat, a claim that hands it the work its bots
    had waiting, and the turn through to done. A refusal here stops every bot of the company at once."""
    from backend.auth import digest
    from scripts import check_wire_contract as W

    kept = W.RELEASES / "0.6.0"
    wire = W.load(kept)["wire"]
    with closing(sqlite3.connect(company.db)) as c:
        # The computer's own token never leaves it: the test gives the enrolled computer one it knows.
        c.execute("UPDATE runners SET token_hash=? WHERE revoked_at IS NULL", (digest(COMPUTER_TOKEN),))
        c.commit()
        placed = [row[0] for row in c.execute("SELECT bot FROM assignments")]
    with TestClient(create_app(company.settings)) as api:
        cloud_access.apply(api.app.state.store, cloud_access.parse(signals("open")))
        api.app.state.cloud_machines.client = company.hq
        aid = ""
        for step in TURN:
            name = step.split(".")[0]
            body = json.loads((kept / W.FIXTURES / (step + ".json")).read_text())["request"]
            if name == "heartbeat":
                body["version"] = body["release"] = RELEASES[-1].removeprefix("v")
                body["readiness"] = {
                    "schema_version": 1,
                    "bots": {
                        bot: {
                            "ready": True,
                            "runtime": "claude",
                            "repository_present": True,
                            "configuration_valid": True,
                            "problems": [],
                        }
                        for bot in placed
                    },
                }
            answer = api.post(
                wire["endpoints"][name]["path"].replace("{aid}", aid),
                json=body,
                headers={"Authorization": "Bearer " + COMPUTER_TOKEN, "Idempotency-Key": f"{name}:{aid}"},
            )
            assert answer.status_code == 200, (
                f"After the update the computer on {RELEASES[-1]} is refused at {name}: {answer.text[:400]}"
            )
            if name == "heartbeat":
                # Work is handed out once a computer that was out of contact has reported in for two minutes
                # (execution.AWAKE_SETTLE): those two minutes have passed.
                with api.app.state.store.transaction() as c:
                    c.execute("UPDATE runner_states SET awake_since=?", (H.shift(H.now(), seconds=-AWAKE_SETTLE - 1),))
            if name == "claim":
                attempt = answer.json()["attempt"]
                assert attempt, f"The computer on {RELEASES[-1]} is handed no work after the update: {answer.json()}"
                aid = attempt["id"]
    assert not company.failed, company.failed
