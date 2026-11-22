"""A running company is updated in place: this code starts on the database the last release made, and the company is
the one it was.

The database is a hosted company a day old, made by v0.6.0's own code (scripts/make_company_upgrade_fixture.py):
HQ's starting chart applied at the first start, setup complete, one shared cloud computer, 21 bots (the 11 seat bots
taken live by Agent Ops), two reporting lines a person set by hand, tasks at different stages of Product and Engineering,
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
from backend.tests.helpers.fresh_install import OWNER_EMAIL, settings_for, signed_in
from backend.tests.helpers.template_renames import CHARTS, employees_file
from backend.tests.helpers.upgrade import FIXTURES
from clients.environments import seed_registry

RELEASES = ["v0.6.0"]
HAND_SET = {"botops": "general-manager", "design-owner": "growth-manager"}
RENAMED = {"botops": "agent-ops"}  # a slug the update changes (backend/migrations/20261121173732_agent_ops_name.sql)
BUILD_STAGES = ("product-build", "product-fixes-1", "product-fixes-2")  # stage_rules.OWNER_ON_TEAM_ON, Product


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
    (backend/bookkeeper.py grant_view) and leaves their Needs you."""
    from backend.bookkeeper import VIEW_TITLE

    after = copy.deepcopy(before)
    for task in after["tasks"].values():
        if task["title"] == VIEW_TITLE and task["status"] == "open":
            task.update(status="closed", stage=task["stage"].rsplit("-", 1)[0] + "-closed", asks=[])
    return migrated(after)


def pitch_removed(reading):
    """The reading as migration stage_approvals_documents leaves it (a copy): a stage's OK only a person gives becomes a
    page approving a document, Product's Pitch is retired with its requirements, and each open task there moves on to
    Spec."""
    after = copy.deepcopy(reading)
    for pipeline in after.get("pipelines", {}).values():
        pipeline["stages"] = [stage for stage in pipeline["stages"] if stage[0] != "Pitch"]
        pipeline["requirements"] = [
            {**r, "kind": "page"} if r["kind"] == "ok" and r["who_oks"] == "people_only" else r
            for r in pipeline["requirements"]
            if r["stage"] != "Pitch"
        ]
    for task in after.get("tasks", {}).values():
        if task["status"] == "open" and task["stage"] == "product-ideas":
            task["stage"] = "product-spec"
    return after


def agent_ops_renamed(reading):
    """The reading as migration agent_ops_name leaves it (a copy): the bot whose id is `botops` goes by its new slug and
    name, wherever the reading names it."""

    def renamed(value):
        if isinstance(value, str):
            return RENAMED.get(value, value)
        if isinstance(value, dict):
            return {renamed(key): renamed(item) for key, item in value.items()}
        if isinstance(value, list):
            return [renamed(item) for item in value]
        if isinstance(value, tuple):
            return tuple(renamed(item) for item in value)
        return value

    after = renamed(reading)
    for key in ("automations", "schedules", "seats", "placed", "cloud"):  # the readings seen() sorts
        if key in after:
            after[key] = sorted(after[key])
    if "agent-ops" in after.get("bots", {}):
        after["bots"]["agent-ops"]["name"] = "Agent Ops"
    return after


def migrated(reading):
    """The reading with every pending migration's one intended change (a copy)."""
    return agent_ops_renamed(pitch_removed(reading))


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


def updated(before):
    """The company an update of the last release's means to make: `before` with each one-time change this code makes to
    it on purpose, named here with the code that makes it. Anything else the update changes still fails the tests."""
    after = copy.deepcopy(before)
    engineering = after["pipelines"].get("engineering")
    # pipeline_settings._add_qa: Engineering gets a QA stage after Fixes 2, owned by Fixes 2's owner, with Checks pass
    # and the page a person OKs, moved from Fixes 2 as it was; Fixes 2 gets Owner says done first.
    names = [stage[0] for stage in engineering["stages"]] if engineering else []
    page = next(
        (
            r
            for r in (engineering or {}).get("requirements", [])
            if r["stage"] == "Fixes 2" and r["kind"] == "page" and r["who_oks"] == "people_only"
        ),
        None,
    )
    if "Fixes 2" in names and "QA" not in names and page:
        fixes = names.index("Fixes 2")
        engineering["stages"].insert(fixes + 1, ("QA", "work", engineering["stages"][fixes][2], 0))
        rule = {"source": None, "condition": None, "who_oks": "none", "can_push_past": 1}
        requirements = [r for r in engineering["requirements"] if r is not page]
        last = max(i for i, r in enumerate(requirements) if r["stage"] == "Fixes 2")
        first = next(i for i, r in enumerate(requirements) if r["stage"] == "Fixes 2")
        qa = [
            {**rule, "stage": "QA", "kind": "item", "source": "github", "condition": "checks_pass"},
            {**page, "stage": "QA"},
        ]
        requirements[last + 1 : last + 1] = qa
        if not any(r["stage"] == "Fixes 2" and r["kind"] == "owner_done" for r in requirements):
            requirements.insert(first, {**rule, "stage": "Fixes 2", "kind": "owner_done"})
        engineering["requirements"] = requirements
    # stage_rules.sweep_owners: the update makes Owner on the team on Product's Build and Fixes stages, and an open task
    # there owned by a bot of another team (the Builder, on Engineering) loses its owner; Product's manager picks one.
    for task in after["tasks"].values():
        team = after["bots"].get(task["owner"], {}).get("team")
        if task["status"] == "open" and task["stage"] in BUILD_STAGES and team not in (None, "product"):
            task["owner"] = ""
    # onboarding.ensure_tools_owner: a company from before the Tools Owner was built in gets it once, under the General
    # Manager, on Agent Ops' computer and the shared cloud machine like the other built-ins, with its weekly review.
    if "tools-owner" not in after["bots"]:
        after["bots"]["tools-owner"] = {
            "name": "Tools Owner",
            "state": "active",
            "manager": "general-manager",
            "team": "built-in",
            "setup": None,
        }
        after["automations"] = sorted([*after["automations"], ("Review the tools", "tools-owner", "schedule", 1)])
        agent_ops = next(runner for bot, runner in after["placed"] if bot == "agent-ops")
        after["placed"] = sorted([*after["placed"], ("tools-owner", agent_ops)])
        after["cloud"] = sorted([*after["cloud"], ("tools-owner", "cloud", "shared", None, None)])
    # onboarding.ensure_builtin_routines: the Tools Owner's daily learn-from-use reaches one from before it, once, on.
    learn = ("Learn from how tools are used", "tools-owner", "schedule", 1)
    if learn not in after["automations"]:
        after["automations"] = sorted([*after["automations"], learn])
    return after


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
        self.expected = updated(intended(self.before))  # what the update may change on purpose
        # What the person had waiting on the last release, as its own page listed it.
        self.needs_you = set(json.loads((FIXTURES / f"company-{release}.json").read_text())["needs_you"])
        from backend.bookkeeper import VIEW_TITLE

        # Closed for the owner by the update (intended), and the pitches whose OK migration stage_approvals_documents
        # dropped when it moved them on to Spec.
        pitched = {t["title"] for t in self.before["tasks"].values() if t["stage"] == "product-ideas"}
        self.needs_you_after = self.needs_you - {VIEW_TITLE} - pitched
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
    assert len(before["pipelines"]) == 11
    assert {slug: before["bots"][slug]["manager"] for slug in HAND_SET} == HAND_SET
    stages = {t["title"]: t["stage"] for t in before["tasks"].values()}
    assert stages["Export invoices as CSV"] == "product-spec" and stages["Rate limit the public API"] == "product-build"
    assert [t for t in before["tasks"].values() if t["snoozed_until"]]
    assert [t for t in before["tasks"].values() if any(a["kind"] == "ok" and a["person_id"] for a in t["asks"])]
    assert "Ship the pricing page" in company.needs_you  # the sign-off waits on the person
    assert {"Friday numbers", "New signup follow-up"} <= {a[0] for a in before["automations"]}
    assert [m for m in before["cloud"] if m[0] == "_shared" and m[3] and m[4]]


def test_a_company_updated_in_place_is_the_company_it_was(company):
    """The update: this code starts on the last release's company, and then starts again (a deploy is often followed by
    a restart). Nothing a person set or a bot was doing changes; the only work it may add is named here, and the only
    changes it makes on purpose are named in `updated`."""
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
    assert {slug: after[RENAMED.get(slug, slug)]["manager"] for slug in HAND_SET} == HAND_SET
    # One of each built-in: an update that cannot find the one a company has makes a second under a `-2` slug.
    names = [bot["name"] for bot in after.values() if bot["state"] != "archived"]
    assert sorted(names) == sorted(set(names)), sorted(n for n in names if names.count(n) > 1)


def test_no_bot_is_set_up_or_interviewed_again(company):
    """A bot's "Set yourself up" and Agent Ops' "Set up ..." tasks are filed once, when the bot is made."""
    before = sorted(t["title"] for t in company.before["tasks"].values() if t["title"].startswith("Set "))
    company.start()
    company.start()
    after = company.after()
    assert sorted(t["title"] for t in after["tasks"].values() if t["title"].startswith("Set ")) == before
    assert {bot["setup"] for bot in after["bots"].values()} == {bot["setup"] for bot in company.before["bots"].values()}
    assert after["refused"] == company.before["refused"], "The update recorded refusals nobody made"


def schema(path):
    return rows(path, "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")


EMAIL_CUTOVER_STATES = {
    "waiting_approval": "waiting_approval",
    "pending": "queued",
    "dispatching": "unknown",
    "sent": "done",
    "blocked": "blocked",
    "canceled": "canceled",
    "failed": "failed",
    "unknown": "unknown",
    "declined": "blocked",
}
EMAIL_CREATED = "2026-11-21T12:00:00.000000Z"
EMAIL_ATTEMPTED = "2026-11-22T00:01:00.000000Z"
EMAIL_DUE = "2026-11-22T18:00:00.000000Z"


def seed_old_email(c, prior):
    sid = "old-email-" + prior
    c.execute(
        "INSERT INTO email_sends(id,mailbox,actor,bot,via,content_sha256,idempotency_key,recipients,attachments,"
        "subject,body_sha256,raw,draft_id,thread_id,reply_to_id,approval_id,classification,decision,state,"
        "created_at,send_after,provider_attempted_at,claimed_at,lease_id,provider_message_id,finished_at,operation_key) "
        "VALUES (?,'ops@acme.example','bot:ops','ops','finance',?,?,'[\"a@vendor.example\"]','[]','Update',"
        "'body','frozen-mime','','thread','original','old-ok','{}','{}',?,?,?,?,?,?,?,?,?)",
        (
            sid,
            "hash-" + sid,
            "key-" + sid,
            prior,
            EMAIL_CREATED,
            EMAIL_DUE,
            EMAIL_ATTEMPTED if prior == "dispatching" else EMAIL_CREATED if prior in ("sent", "unknown") else None,
            EMAIL_CREATED if prior == "dispatching" else None,
            "old-lease" if prior == "dispatching" else "",
            "gmail-id" if prior == "sent" else None,
            EMAIL_CREATED if prior not in ("waiting_approval", "pending", "dispatching") else None,
            "op-" + sid,
        ),
    )
    return sid


def assert_email_cutover(c, cutover_at):
    assert all(index["origin"] != "c" for index in c.execute("PRAGMA index_list(email_sends)"))
    for prior, state in EMAIL_CUTOVER_STATES.items():
        sid = "old-email-" + prior
        old = c.execute("SELECT state, raw FROM email_sends WHERE id=?", (sid,)).fetchone()
        assert tuple(old) == (prior, "")
        row = c.execute("SELECT * FROM outbound_actions WHERE id=?", (sid,)).fetchone()
        assert (row["channel"], row["state"], row["occurrence_key"]) == ("email", state, f"email:send:{sid}")
        assert (row["destination"], row["actor"], row["approval_id"]) == ("ops@acme.example", "bot:ops", "old-ok")
        assert row["send_after"] == row["next_attempt_at"] == EMAIL_DUE
        meta = json.loads(row["metadata"])
        assert (meta["recipients"], meta["subject"], meta["content_sha256"]) == (
            ["a@vendor.example"],
            "Update",
            "hash-" + sid,
        )
        if prior in ("waiting_approval", "pending"):
            assert json.loads(row["payload"]) == {"raw": "frozen-mime"}
        else:
            assert row["payload"] == ""
        if prior == "dispatching":
            assert row["provider_attempted_at"] is None and row["claim_token"] is None
            assert meta["finished_at"] == cutover_at and meta["cutover_unconfirmed"] == 1
            assert meta["cutover_attempted_at"] == EMAIL_ATTEMPTED
            projected = c.execute("SELECT claimed_at,provider_attempted_at FROM outbound_email WHERE id=?", (sid,))
            assert tuple(projected.fetchone()) == (EMAIL_CREATED, EMAIL_ATTEMPTED)
        if prior in ("sent", "unknown"):
            assert row["provider_attempted_at"] == EMAIL_CREATED
        if prior == "sent":
            assert json.loads(row["receipt"]) == {"message_id": "gmail-id"}


def test_the_pending_migrations_apply_in_any_order(company, tmp_path, monkeypatch):
    """Migrations merge in any order and an install may be anywhere between two releases, so the files this company
    has not run must give the same database whichever runs first (CLAUDE.md, Conventions)."""
    applied = {r["version"] for r in rows(company.db, "SELECT version FROM schema_migrations")}
    pending = [m for m in migrations.files() if m.version not in applied]
    # Compare data as well as schema when this cutover is among the pending files.
    slack_cutover = any(m.version.endswith("_slack_outbound") for m in pending)
    email_cutover = any(m.version.endswith("_email_outbound") for m in pending)
    slack_states = {
        "ready": "queued",
        "retry": "queued",
        "rate_limited": "queued",
        "sending": "unknown",
        "uncertain": "unknown",
        "sent": "done",
        "canceled": "canceled",
        "failed": "failed",
    }
    retry_at = "2026-11-21T15:00:00.000000Z"
    cutover_at = "2026-11-22T15:00:00.000000Z"
    monkeypatch.setattr(H, "now", lambda: cutover_at)
    results = []
    for name, order in (("oldest first", pending), ("newest first", pending[::-1])):
        path = tmp_path / (name.replace(" ", "-") + ".db")
        shutil.copy(company.db, path)
        with closing(sqlite3.connect(path, isolation_level=None)) as c:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            if email_cutover:
                for state in EMAIL_CUTOVER_STATES:
                    seed_old_email(c, state)
            if slack_cutover:
                mids = [r[0] for r in c.execute("SELECT id FROM messages ORDER BY id LIMIT ?", (len(slack_states),))]
                for mid, state in zip(mids, slack_states, strict=True):
                    c.execute(
                        "INSERT INTO slack_posts(message_id,channel,thread_ts,bot,text,state,created_at,updated_at,"
                        "next_attempt_at,slack_ts,error) VALUES(?,'C0ACME','','botops','Release update',?,"
                        "'2026-11-21','2026-11-21',?,?,?)",
                        (
                            mid,
                            state,
                            retry_at if state in ("retry", "rate_limited") else None,
                            "1700000001.000100" if state == "sent" else None,
                            "Kept in Tico by their Slack settings" if state == "canceled" else None,
                        ),
                    )
            for migration in order:
                try:
                    migrations._run(c, migration)
                except Exception as exc:
                    pytest.fail(f"Migration {migration.path.name} fails on a running company when run {name}: {exc!r}")
            assert c.execute("PRAGMA foreign_key_check").fetchall() == [], f"{name}: rows refer to missing rows"
            if email_cutover:
                assert_email_cutover(c, cutover_at)
            if slack_cutover:
                assert not c.execute("PRAGMA foreign_key_list(slack_posts)").fetchall()
                for mid, prior in zip(mids, slack_states, strict=True):
                    old = c.execute("SELECT state,text FROM slack_posts WHERE message_id=?", (mid,)).fetchone()
                    assert tuple(old) == (prior, "Release update")
                    found = c.execute("SELECT * FROM outbound_actions WHERE id=?", (mid,)).fetchone()
                    assert found["state"] == slack_states[prior] and found["occurrence_key"] == f"slack:post:{mid}"
                    assert not found["provider_attempted_at"]
                    if prior in ("ready", "retry", "rate_limited", "sending"):
                        assert found["send_after"] == H.shift(cutover_at, minutes=2)
                    else:
                        assert found["send_after"] == "2026-11-21"
                    if prior in ("rate_limited", "retry"):
                        assert found["next_attempt_at"] == retry_at
                    if prior == "canceled":
                        assert found["last_error"] == "Kept in Tico by their Slack settings"
                    if prior == "sent":
                        assert json.loads(found["receipt"]) == {"ts": "1700000001.000100"}
                        assert json.loads(found["metadata"])["_tico"]["sent_text"] == "Release update"
                    if prior in ("canceled", "sent", "failed"):
                        assert not found["payload"]
        results.append((schema(path), seen(path)))
    assert results[0][0] == results[1][0], "The schema depends on the order the pending migrations ran in"
    assert not changes(results[0][1], results[1][1])
    assert not changes(migrated(company.before), results[0][1]), "The pending migrations changed the company"


def test_email_cutover_keeps_operation_receipts_bound_to_the_original_request(company, tmp_path):
    from backend import email_sending
    from backend.store import Problem

    path = tmp_path / "pre-email.db"
    shutil.copy(company.db, path)
    pending = {migration.version: migration for migration in migrations.files()}
    with closing(sqlite3.connect(path, isolation_level=None)) as c:
        c.row_factory = sqlite3.Row
        migrations._run(c, pending["20261118234339_email_send_tasks"])
        migrations._run(c, pending["20261121073005_email_operation_requests"])
        sid = seed_old_email(c, "sent")
        actor, operation, mailbox = "bot:ops", "op-" + sid, "ops@acme.example"
        c.execute("UPDATE email_sends SET task_id='case', approval_task_id='ok-task' WHERE id=?", (sid,))
        c.execute("INSERT INTO email_send_operations VALUES(?,?,?,?)", (actor, operation, sid, "request-hash"))
        cutover = next(m for version, m in pending.items() if version.endswith("_email_outbound"))
        migrations._run(c, cutover)
        found = email_sending.receipt(c, actor, operation, mailbox, "request-hash")
        assert (found["id"], found["state"], found["provider_message_id"]) == (sid, "sent", "gmail-id")
        assert (found["task_id"], found["approval_task_id"]) == ("case", "ok-task")
        with pytest.raises(Problem, match="different email content"):
            email_sending.receipt(c, actor, operation, mailbox, "changed-request")


REMOVED = ("growth-manager", "legal-owner", "feature-owner")  # live seat bots' templates in the fixture


@pytest.mark.parametrize("state", ["ready", "sent", "canceled"])
def test_slack_cutover_preserves_history_and_fences_pre_cutover_trash(company, tmp_path, state):
    path = tmp_path / "pre-slack.db"
    shutil.copy(company.db, path)
    pending = {migration.version: migration for migration in migrations.files()}
    before = "20261122001930_outbound_actions"
    cutover = "20261122144410_slack_outbound"
    with closing(sqlite3.connect(path, isolation_level=None)) as c:
        c.row_factory = sqlite3.Row
        migrations._run(c, pending[before])
        mid = c.execute("SELECT id FROM messages LIMIT 1").fetchone()[0]
        key = f"slack:post:{mid}"
        c.execute(
            "INSERT INTO outbound_actions (id,channel,audience,operation,destination,occurrence_key,"
            "send_after,next_attempt_at,created_at,updated_at) "
            "VALUES ('old-jira','jira','external','comment','ACME-1',?,'now','now','now','now')",
            (key,),
        )
        c.execute(
            "INSERT INTO slack_posts (message_id,channel,thread_ts,bot,text,state,created_at,updated_at,slack_ts) "
            "VALUES (?,'C0ACME','','botops','Actually sent','sent','now','now','1700000001.000100')",
            (mid,),
        )
        trash = {
            "message_id": "trashed-post",
            "channel": "C0ACME",
            "thread_ts": "",
            "bot": "botops",
            "text": "Old draft",
            "state": state,
            "slack_ts": "1700000002.000100" if state == "sent" else None,
            "attempts": None,
            "created": "now",
            "updated": "now",
        }
        c.execute(
            "INSERT INTO task_trash (task_id,deleted_at,deleted_by,rows) VALUES ('trashed-task','now','keeper',?)",
            (json.dumps({"rows": {"slack_posts": [trash]}}),),
        )
        migrations._run(c, pending[cutover])
        assert c.execute("SELECT id FROM outbound_actions WHERE occurrence_key=?", (key,)).fetchone()[0] == "old-jira"
        sent = c.execute("SELECT * FROM outbound_actions WHERE id=?", (mid,)).fetchone()
        assert sent["occurrence_key"] != key and json.loads(sent["metadata"])["_tico"]["sent_text"] == "Actually sent"
        found = c.execute(
            "SELECT state,attempts,payload,receipt FROM outbound_actions WHERE id='trashed-post'"
        ).fetchone()
        assert tuple(found)[:3] == ("done" if state == "sent" else "canceled", 0, "")
        assert json.loads(found["receipt"]).get("ts") == trash["slack_ts"]
        legacy = c.execute("SELECT state,error,text FROM slack_posts WHERE message_id='trashed-post'").fetchone()
        assert tuple(legacy) == ("canceled", "outbound", "")


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
