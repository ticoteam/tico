#!/usr/bin/env python3
"""Build the running hosted company that backend/tests/test_company_update_in_place.py updates in place.

Run it from a checkout of the release a company is updated FROM, so that release's own code makes every row, with the
Python environment of a current checkout (one release back has the same pins):

    git worktree add /tmp/tico-v0.6.0-rc.7 v0.6.0-rc.7
    cd /tmp/tico-v0.6.0-rc.7 && PYTHONPATH=. /path/to/.venv/bin/python /path/to/scripts/make_company_upgrade_fixture.py \
        /path/to/backend/tests/fixtures/company-v0.6.0-rc.7.sqlite.gz

It writes the database and, beside it, `company-<release>.json`: what the person's Needs you held on that release.

It makes the company the way a hosted one is made and then used for a day: the starting chart HQ hands a new company
(fixtures/hq_starting_charts.json, the 0.6 line) applied at the first start, setup completed, one shared cloud
computer enrolled, BotOps taking every seat bot live and closing its "Set up" task, a restart, then a person's own
changes (two reporting lines set by hand) and work in flight: tasks at different stages of Product and Engineering,
one snoozed, one waiting on a person's sign-off, a routine and an automation.

Everything outside Tico is the release's own test double (HQ's machines API). It uses the release's test helpers, so
it works on 0.6 releases; an older line needs its own script.
"""

import gzip
import json
import logging
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

OWNER = "owner"


def build(out):
    from fastapi.testclient import TestClient

    from backend import cloud_access
    from backend.app import create_app
    from backend.company_file import starter
    from backend.tests.helpers.botops_parity import act
    from backend.tests.helpers.cloud_access import signals
    from backend.tests.helpers.cloud_machines import FakeHQ
    from backend.tests.helpers.sample_company import OWNER_EMAIL, rows, settings_for, signed_in
    from backend.tests.helpers.template_renames import CHARTS, employees_file
    from clients.environments import seed_registry

    logging.disable(logging.CRITICAL)
    work = Path(tempfile.mkdtemp())
    seed_registry(
        work, "Owner", OWNER_EMAIL, {"company_name": "Acme", "app_name": "Tico", "assistant_name": "Assistant"}
    )
    settings = settings_for(work, hosted="own", enabled_providers=("anthropic", "openai"))
    app = create_app(settings)
    app.state.store.initialize()
    app.state.store.seed()
    settings.company_file_data = starter.pack(employees_file(CHARTS["0.6 hotmic"]), starter.MAX_PACKED)
    settings.company_file_configured = True

    def call(api, method, path, body=None, token=None, expected=(200,)):
        headers = (
            signed_in() if token is None else {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid.uuid4())}
        )
        r = api.request(method, "/api/v2/" + path, json=body, headers=headers)
        assert r.status_code in expected, (method, path, r.status_code, r.text[:600])
        return r.json()

    def report(api, computer, present):
        readiness = {
            row["bot"]: {
                "ready": row["bot"] in present,
                "runtime": "claude",
                "repository_present": row["bot"] in present,
                "configuration_valid": True,
                "problems": [],
            }
            for row in rows(api, "SELECT bot FROM assignments WHERE runner_id=?", (computer["runner_id"],))
        }
        body = {
            "version": "test",
            "platform": "linux",
            "capacity": 4,
            "readiness": {"schema_version": 1, "bots": readiness},
        }
        call(api, "POST", "runners/heartbeat", body, token=computer["token"])

    hq = FakeHQ()

    def cloud(api):
        """Tico Cloud, open, with HQ's machines API as the release's double; one tick so HQ's sizes are known."""
        cloud_access.apply(api.app.state.store, cloud_access.parse(signals("open")))
        api.app.state.cloud_machines.client = hq
        api.app.state.cloud_machines.tick()

    # ---- day one: the first start applies HQ's chart, the person finishes setup, the shared cloud computer enrolls
    with TestClient(create_app(settings)) as api:
        assert call(api, "GET", "setup")["company_import_failed"] is None
        call(api, "POST", "onboarding/complete", {})
        cloud(api)
        api.app.state.cloud_machines.tick()
        created = [c for c in hq.calls if c[0] == "create"]
        assert created, "the company's bots are placed on a cloud computer and it is created"
        computer = call(
            api, "POST", "runners/enroll", {"code": created[-1][3], "label": "boat-vm", "platform": "linux"}
        )
        with api.app.state.store.transaction() as c:
            api.app.state.onboarding.complete_company(c)
        seats = {
            row["id"]: (row["slug"], row["task"])
            for row in rows(
                api,
                "SELECT b.id, b.slug, json_extract(s.extra,'$.setup_task_id') AS task FROM bots b "
                "JOIN bot_settings s ON s.bot_id=b.id WHERE json_extract(s.extra,'$.goes_live_on_setup')",
            )
        }
        assert len(seats) >= 10, seats
        # BotOps builds each seat bot's repository in its own workspace, and the build records that computer
        # (backend/built_on.py): the Builder, whose machine would be its own, is first placed there.
        from backend import built_on

        with api.app.state.store.transaction() as c:
            for bot in seats:
                built_on.remember(c, bot, computer["runner_id"])
        every = {row["bot"] for row in rows(api, "SELECT bot FROM assignments")}
        report(api, computer, every - set(seats))
        # BotOps' own turn: every seat bot goes live, then its "Set up" task is done.
        attempt = call(api, "POST", "jobs/claim", {"bot": "botops", "start_places": True}, token=computer["token"])[
            "attempt"
        ]
        assert attempt, "BotOps claims its setup work"
        for slug, _ in seats.values():
            live = act(api, attempt, "POST", f"bots/{slug}/go-live", {})
            assert live.status_code in (200, 202), (slug, live.text)
        report(api, computer, {row["bot"] for row in rows(api, "SELECT bot FROM assignments")})
        for slug, task in seats.values():
            version = call(api, "GET", f"tasks/{task}")["task"]["version"]
            done = act(api, attempt, "POST", f"tasks/{task}/done", {"note": "Built and live.", "version": version})
            assert done.status_code == 200, (slug, done.text)

    # ---- a restart (a deploy, a reboot): the start jobs run once more, as on any company a day old
    with TestClient(create_app(settings)) as api:
        cloud(api)

    # ---- the person's own changes, and work in flight
    with TestClient(create_app(settings)) as api:
        cloud(api)
        ids = {row["slug"]: row["id"] for row in rows(api, "SELECT id,slug FROM bots")}

        def line(slug, manager):
            revision = rows(api, "SELECT revision FROM bot_config WHERE bot=?", (ids[slug],))[0]["revision"]
            call(api, "POST", f"bots/{slug}/definition", {"reports_to": manager, "expected_revision": revision})

        line("botops", "general-manager")  # set by hand on the live company
        line("design-owner", "growth-manager")  # a line no default would pick

        def task(title, owner, pipeline):
            body = {"title": title, "body": "As typed by the person.", "owner": owner, "pipeline": pipeline}
            return call(api, "POST", "tasks", body)["task"]

        def move(found, to):
            version = call(api, "GET", f"tasks/{found['id']}")["task"]["version"]
            return call(
                api, "POST", f"tasks/{found['id']}/move", {"to": to, "why": "Moved by hand.", "version": version}
            )

        task("Dark mode for the dashboard", "creators-product-owner", "product")
        spec = task("Export invoices as CSV", "creators-product-owner", "product")
        move(spec, "next")
        task("Fix the login redirect", "builder", "engineering")
        review = task("Rate limit the public API", "builder", "engineering")
        move(review, "next")
        snoozed = task("Look at the onboarding funnel", "growth-manager", "general")
        call(api, "POST", f"tasks/{snoozed['id']}/snooze", {"until": "2027-01-15", "why": "After the holidays."})
        signoff = task("Ship the pricing page", "builder", "general")
        call(
            api,
            "POST",
            f"tasks/{signoff['id']}/asks",
            {"to": "human:" + OWNER, "line": "OK to publish the pricing page?", "kind": "ok"},
        )
        wake = {"id": "tell", "type": "action", "enabled": True, "do": "wake_bot"}
        call(
            api,
            "POST",
            "automations",
            {
                "name": "Friday numbers",
                "owner": "person:" + OWNER,
                "trigger": {"type": "schedule", "cron": "0 9 * * 5", "tz": "UTC"},
                "steps": [{**wake, "args": {"bot": "strategy-owner", "text": "Send this week's numbers."}}],
            },
        )
        call(
            api,
            "POST",
            "automations",
            {
                "name": "New signup follow-up",
                "owner": "person:" + OWNER,
                "trigger": {"type": "event", "name": "signup.created"},
                "steps": [{**wake, "args": {"bot": "success-manager", "text": "A new signup: say hello."}}],
            },
        )

        # What the person has waiting, as this release's own page lists it: the update may add nothing to it.
        tasks = {row["id"]: row["title"] for row in rows(api, "SELECT id, title FROM tasks")}
        needs = sorted(
            tasks[item["task_id"]] if item.get("task_id") else f"{item['kind']}: {item['line']}"
            for group in call(api, "GET", "needs-you/summary")["by_bot"]
            for item in group["items"]
        )

    db = settings.db_path
    c = sqlite3.connect(db)
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    # Search indexes, traces and request logs are rebuilt or unread after an update; without them the file is small.
    for table in ("flight_requests", "flight_sql", "flight_events", "flight_slow", "flight_db", "flight_process"):
        if c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            c.execute(f"DELETE FROM {table}")
    c.commit()
    c.execute("VACUUM")
    c.close()
    with open(db, "rb") as source, gzip.GzipFile(out, "wb", mtime=0) as target:
        shutil.copyfileobj(source, target)
    facts = Path(str(out).removesuffix(".sqlite.gz") + ".json")
    facts.write_text(json.dumps({"needs_you": needs}, indent=2) + "\n")
    print(f"{out}: {Path(out).stat().st_size // 1024} KB; {facts.name}: Needs you {needs}")


if __name__ == "__main__":
    build(sys.argv[1])
