"""The company demo as its owner sees it: what the seed leaves behind, read through the same API the app uses. These
check behaviour (who owns what, what waits for whom, what every bot has done), not the wording of the seed."""

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest
import time_machine
from fastapi.testclient import TestClient

from backend import demo, demo_seed, hubdb, judge, never_stuck
from backend import demo_content as D
from backend import usage_limits as U
from backend.demo_sections import budgets as B
from backend.app import create_app

from backend.tests.helpers.demo_company import NOW  # noqa: F401

ROSTER = {slug for slug, _template, _parent in D.BOTS}
DAY = {"from": "2026-10-31", "to": "2026-11-06"}
PER_INSTALL = {
    "credential_keys",
    "granola_connections",
    "slack_credentials",
}  # this machine's keys and the secrets they seal
# Bytes this machine's keys produce: a sealed secret (AES-GCM, a fresh nonce), and an idempotency fingerprint keyed
# with the cipher key. Each install differs there by design, so they are compared as a mark, not as bytes.
SEALED = re.compile(r"sealed:v1:[A-Za-z0-9+/=]+")
KEYED = {("idempotency", "request_hash")}


def _mark(table, column, value):
    if column in ("ciphertext", "nonce") or (table, column) in KEYED:
        return "<per-install>"
    return SEALED.sub("sealed:v1:<per-install>", value) if isinstance(value, str) else value


def stored(path):
    """Every table's rows, as the build wrote them, less the per-install keys and the bytes they make."""
    with sqlite3.connect(path) as db:
        tables = [
            name
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        ]
        out = {}
        for name in tables:
            if name in PER_INSTALL:
                continue
            cursor = db.execute(f'SELECT * FROM "{name}"')
            columns = [column[0] for column in cursor.description]
            out[name] = [
                tuple(_mark(name, column, value) for column, value in zip(columns, row)) for row in cursor.fetchall()
            ]
        return out


@pytest.fixture(scope="module", autouse=True)
def demo_clock():
    """Builds and API reads see the same day, including clocks outside hubdb.now."""
    with time_machine.travel(NOW, tick=False):
        yield


def real_clock_at(patch, moment):
    """The machine's clock as the usage-limit overlay reads it (`datetime.now`), set to `moment`."""
    clock = type("RealClock", (datetime,), {"now": classmethod(lambda cls, tz=None: moment)})
    patch.setattr(U, "datetime", clock)


@pytest.fixture(scope="module")
def dialed():
    """The outbound connections the builds below tried (and were refused), in order."""
    return []


@pytest.fixture(scope="module")
def built(tmp_path_factory, dialed):
    """Two builds of the same date, back to back, read before any app has opened either database. The machine's clock
    crosses 07:00 UTC (a usage-limit window edge) between them: the first build runs just after it, the second just
    before. Like `tico demo` (demo.main), they build with the network blocked. The build asks no model: without this, a
    person's message check dials the Gateway the demo saved a key for, and the answer depends on timing."""
    with pytest.MonkeyPatch.context() as patch, demo.network_blocked() as attempts:
        tried = len(attempts)
        patch.setattr(judge, "server_engine", lambda *args, **kwargs: None)
        real_clock_at(patch, datetime(2026, 11, 6, 7, 0, 1, tzinfo=timezone.utc))
        first = demo.build(tmp_path_factory.mktemp("company"), now=NOW)
        rows = stored(first.db_path)
        real_clock_at(patch, datetime(2026, 11, 6, 6, 59, 59, tzinfo=timezone.utc))
        second = demo.build(tmp_path_factory.mktemp("company-again"), now=NOW)
        dialed.extend(attempts[tried:])
        return first, rows, stored(second.db_path)


@pytest.fixture(scope="module")
def ana(built):
    settings, _rows, _again = built
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as api:
        api.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
        yield api


def test_the_company_goal_has_department_goals_and_one_is_off_track(ana):
    goals = ana.get("/api/v2/goals?all=true").json()["goals"]
    [company] = [goal for goal in goals if goal["owner_id"] == "company"]
    assert [goal["id"] for goal in goals if goal["parent_id"] == company["id"]], "departments hang off the company goal"
    red = [goal for goal in goals if goal["status"] == "red"]
    assert len(red) == 1 and red[0]["parent_id"] == company["id"], (
        "exactly one goal is off track, under the company goal"
    )


def test_every_bot_has_a_daily_update_and_the_friday_review_covers_the_team(ana):
    for slug in sorted(ROSTER):
        flags = ana.get(f"/api/v2/bots/{slug}/updates").json()
        assert flags["daily"] and flags["weekly"], slug
    friday = ana.get("/api/v2/updates?kind=weekly&limit=100").json()["updates"]
    reviewed = {update["bot"] for update in friday if update["day"] == "2026-11-06"}
    assert ROSTER <= reviewed, sorted(ROSTER - reviewed)


def test_the_pricing_decision_waits_for_ana(ana):
    waiting = [
        item
        for item in ana.get("/api/v2/needs-you").json()["items"]
        if item["task"]["title"] == "Raise Home Pro from $449 to $479 for December?"
    ]
    assert [item["kind"] for item in waiting] == ["task"]


def test_tasks_are_open_done_or_closed_and_show_asks_snoozes_and_sub_tasks(ana):
    """As Tasks reads them: what is open, then the latest finished (the weeks before hold hundreds more done). Most
    are in General, a few in Product and Engineering (demo_sections/software.py), the Bookkeeper's own in Finance (a private
    pipeline the owner reads), and the open ones show what they wait on: a person asked on the task, a snooze (a snoozed
    task is off the list until asked for)."""
    tasks = [
        *ana.get("/api/v2/tasks?status=open&snoozed=all&limit=200").json()["tasks"],
        *ana.get("/api/v2/tasks?status=done,closed&sort=finished&limit=200").json()["tasks"],
    ]
    assert {task["status"] for task in tasks} == {"open", "done", "closed"}
    assert {task["pipeline_id"] for task in tasks} == {"general", "product", "engineering", "finance"}
    by_title = {task["title"]: task for task in tasks}
    stages = {title: by_title[title]["stage_id"] for title in ("Share a show with a link", "Faster scene upload")}
    assert stages == {"Share a show with a link": "product-spec", "Faster scene upload": "engineering-on-deck"}
    beta = by_title["Ship the calibration screen to every owner"]
    assert (beta["turn"]["state"], beta["turn"]["who"]) == ("person", "human:eli")
    assert by_title["Fix the Linux-only test that flakes"]["snoozed"]
    assert by_title["Email the whole mailing list about the holiday sale"]["status"] == "closed"
    rollout = ana.get(f"/api/v2/tasks/{by_title['Roll firmware 2.4 out in three rings']['id']}").json()["task"]
    assert {"Watch ring 2 crash reports for 48 hours", "Write the firmware 2.4 release notes"} <= {
        filed["title"] for filed in rollout["filed"]
    }


def test_every_rule_of_the_task_engine_holds(built):
    """Every task has its pipeline, its stage and one open visit, and every open one waits on someone (the never-stuck
    check's rules, read at the build's moment)."""
    settings, _rows, _again = built
    with closing(hubdb.connect(settings.db_path)) as c:
        assert never_stuck.rules_hold(c) == []


def test_a_meeting_waiting_for_review_is_not_in_the_live_list(ana):
    live = ana.get("/api/meetings").json()
    pending = ana.get("/api/meetings?review=pending").json()
    assert live and pending
    assert not {meeting["id"] for meeting in live} & {meeting["id"] for meeting in pending}
    assert all(meeting["review_state"] == "pending" for meeting in pending)


def test_docs_market_and_links_are_there(ana):
    paths = [doc["path"] for doc in ana.get("/api/v2/docs").json()["docs"]]
    assert any(path.startswith("market/") for path in paths) and any(path.startswith("help/") for path in paths)
    entities = {entity["id"]: entity for entity in ana.get("/api/v2/market/entities").json()["entities"]}
    assert entities["company/self"]["name"] == D.COMPANY
    rivals = [
        edge
        for edge in ana.get("/api/v2/market/edges").json()["edges"]
        if edge["rel"] == "competes_with" and edge["dst"] == "company/self"
    ]
    assert len(rivals) >= 3, "competitors point at the company"


def test_usage_covers_every_bot_and_every_routine_has_a_run(ana):
    usage = {row["bot"] for row in ana.get("/api/v2/usage", params=DAY).json()["rows"]}
    assert ROSTER <= usage, sorted(ROSTER - usage)
    routine_owners = {automation["owner_id"] for automation in ana.get("/api/v2/automations").json()["automations"]}
    assert {f"bot:{slug}" for slug in {row[0] for row in D.ROUTINES}} <= routine_owners
    assert ana.get("/api/v2/automations/runs").json()["runs"], "the routines have run"


def test_status_focus_turns_and_history_are_there_for_every_bot(ana):
    status = {row["bot"]: row for row in ana.get("/api/v2/status").json()["bots"]}
    assert all(status[slug]["focus"] for slug in ROSTER)
    for slug in sorted(ROSTER):
        assert ana.get(f"/api/v2/bots/{slug}/turns").json(), slug
        assert ana.get(f"/api/v2/bots/{slug}/history").status_code == 200, slug


def test_chats_and_slack_are_connected(ana):
    assert ana.get("/api/v2/slack/app").json()["state"] == "connected"
    channels = ana.get("/api/v2/slack/channels").json()["channels"]
    assert any(channel["name"] == D.SLACK["channel"] for channel in channels)
    conversations = ana.get("/api/v2/conversations").json()["conversations"]
    assert any(any(p.startswith("bot:") for p in conv["participants"]) for conv in conversations)


LEADS = {
    "operations-manager": "human:ben",
    "success-manager": "human:ben",
    "growth-manager": "human:cara",
    "growth-manager": "human:cara",
    "product-manager": "human:eli",
    "finance-owner": "human:ana",
}
PEOPLE = ("human:ana", "human:ben", "human:cara", "human:eli")


def test_each_department_lead_reports_to_the_person_who_runs_it(ana):
    bots = {bot["slug"]: bot for bot in ana.get("/api/v2/bots").json()}
    assert {slug: bots[slug]["reports_to"] for slug in LEADS} == LEADS


def test_each_person_has_a_draft_update_for_every_day_and_the_friday_review(ana):
    daily = {"2026-10-31", "2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"}
    fridays = {"2026-10-30", "2026-11-06"}
    for person in PEOPLE:
        rows = ana.get("/api/v2/updates", params={"bot": person, "limit": 100, "include_archive": True}).json()[
            "updates"
        ]
        assert {row["day"] for row in rows if row["kind"] == "daily"} == daily, person
        assert {row["day"] for row in rows if row["kind"] == "weekly"} == fridays, person
        assert {row["drafted_by"] for row in rows} == {"assistant"}, person


def test_ana_sidebar_leaves_unread_only_the_decisions_she_still_owes(ana):
    # Task news is not a chat (hubdb.task_news): the decisions she owes wait in Needs you, not as unread task threads.
    listing = ana.get("/api/v2/threads?limit=200").json()
    rows = listing["threads"] + listing["dms"] + listing["attention"]
    unread = {row["title"] for row in rows if row.get("kind") == "task" and (row.get("me") or {}).get("unread")}
    assert unread <= set(D.KEEP_UNREAD), sorted(unread - set(D.KEEP_UNREAD))
    owed = {item["task"]["title"] for item in ana.get("/api/v2/needs-you").json()["items"]}
    assert {"Raise Home Pro from $449 to $479 for December?", "Sign the FCC filing for Home Pro 2"} <= owed


def test_home_has_a_bot_page_settings_has_a_team_icon_and_granola_syncs(ana):
    pages = ana.get("/api/v2/pages").json()["pages"]
    assert {page["bot_id"] for page in pages} == {D.PAGE["bot"], "assistant"}
    assert ana.get("/api/v2/team/icon").status_code == 200
    assert ana.post("/api/v2/meetings/granola/sync").status_code == 200


def test_budgets_set_a_monthly_company_limit_and_leave_one_group_and_one_bot_near_a_limit(built):
    """Strategy › Budget at the build's time: the whole team on a monthly limit, a few groups and bots with limits, the
    support pool and one bot at about 86% of theirs, and nothing paused."""
    settings, _rows, _again = built
    with closing(hubdb.connect(settings.db_path)) as c:
        company = U.pool_state(c, U.COMPANY, at=NOW)
        pools = {g: U.pool_state(c, "group:" + g, at=NOW) for g in (*B.CONTENT["groups"], B.CONTENT["near_group"])}
        bots = {bot: U.state(c, bot, at=NOW) for bot in ROSTER}
    assert company["monthly_usd"] and company["percent"] < 80 and not company["blocked"]
    assert all(pools[g]["monthly_usd"] and pools[g]["percent"] < 80 for g in B.CONTENT["groups"])
    assert 80 <= pools[B.CONTENT["near_group"]]["percent"] < 100
    capped = {bot for bot, row in bots.items() if "bot" in row["source"].values()}
    assert set(B.CONTENT["bots"]) <= capped
    near = [bot for bot in capped if 80 <= bots[bot]["percent"] < 100]
    assert len(near) == 1 and near[0] in B.CONTENT["near_bots"]
    assert not [bot for bot, row in bots.items() if row["blocked"]]


def test_run_and_task_budgets_are_set_for_the_team_two_bots_and_ana(ana):
    team = ana.get("/api/v2/settings/turn-budgets").json()
    assert (team["turns_per_hour"], team["turns_per_day"]) == (60, 500)
    for bot, body in B.CONTENT["runs_bots"].items():
        own = ana.get(f"/api/v2/bots/{bot}/budget").json()["own"]
        assert {k: own[k] for k in body} == body
    tasks = ana.get("/api/v2/ask-ingest/settings").json()
    assert {k: tasks["team"][k] for k in B.CONTENT["tasks_team"]} == B.CONTENT["tasks_team"]
    assert (
        tasks["settings"]["own"] and {k: tasks["settings"][k] for k in B.CONTENT["tasks_ana"]} == B.CONTENT["tasks_ana"]
    )


def test_the_team_has_closed_tasks_every_week_for_two_months(built):
    """Overview's Done per week: every one of the eight weeks before the build has work done, more as the team grew."""
    settings, _rows, _again = built
    monday = (NOW - timedelta(days=NOW.weekday())).date()
    with closing(sqlite3.connect(settings.db_path)) as db:
        days = db.execute(
            "SELECT substr(done_at,1,10) FROM tasks WHERE status='done' AND done_at>=?",
            ((monday - timedelta(weeks=7)).isoformat(),),
        ).fetchall()
    weeks = [0] * 8
    for (day,) in days:
        done = datetime.strptime(day, "%Y-%m-%d").date()
        weeks[7 - (monday - (done - timedelta(days=done.weekday()))).days // 7] += 1
    assert all(n >= 20 for n in weeks[:-1]) and weeks[0] < weeks[-2], weeks


def test_the_demo_clock_also_reads_the_spend_windows(built):
    """The spend windows (30 minutes, hour, day...) are read at the build's own moment, not the real clock: a build that
    straddles a window boundary would otherwise store different spend from its twin (the same-date test's failure)."""
    saved = U.clock
    moment = NOW - timedelta(hours=2)
    clock = demo_seed.Clock(moment)
    with demo_seed.fixed(clock):
        assert U.clock() == moment
    assert U.clock is saved and U.clock() == NOW


def test_the_same_date_builds_the_same_company(built):
    """Every table, with the machine's clock either side of 07:00 UTC: the usage limits the builds save match too."""
    first, rows, again = built
    left, right = again, rows
    if left != right:
        # Say which tables and which event row differ: a CI log cuts a pytest diff short. `first` is the build.
        mismatch = next(
            ((n, a, b) for n, (a, b) in enumerate(zip(left["events"], right["events"])) if a != b),
            None,
        )
        differing = sorted(name for name in set(left) | set(right) if left.get(name) != right.get(name))
        raise AssertionError(
            f"builds differ in {differing}; events {len(left['events'])} vs {len(right['events'])}; "
            f"first differing event row {str(mismatch)[:600]}"
        )
    with closing(sqlite3.connect(first.db_path)) as db:
        columns = [column[1] for column in db.execute("PRAGMA table_info(idempotency)")]
    operation, response = columns.index("operation"), columns.index("response")
    hours = [
        json.loads(row[response]).get("limit", {}).get("spent", {}).get("hour")
        for row in rows["idempotency"]
        if row[operation].startswith("/api/v2/usage/")
    ]
    assert any(hours), "the windows hold spend, so the hour is really compared"


def test_every_department_has_its_lead_and_answers_to_a_person(ana):
    """The org chart reads groups and reporting lines: each department names its lead, and every bot in a group
    reports, a step at a time, up to a person (so the chart hangs each department under its human head)."""
    bots = ana.get("/api/v2/bots").json()
    by_slug = {bot["slug"]: bot for bot in bots}
    names = {bot.get("id"): bot["slug"] for bot in bots} | {slug: slug for slug in by_slug}
    groups = {group["id"]: group for group in ana.get("/api/v2/groups").json()}
    for gid, members, lead in D.GROUP_FIXES:
        ref = groups[gid]["lead"]
        kind, _, key = ref.partition(":")
        assert (kind + ":" + names.get(key, key)) == lead, f"{gid} is led by {lead}"
        assert set(members) <= set(groups[gid]["bot_slugs"]), f"{gid} holds {members}"
    for group in groups.values():
        if group["id"] == "built-in":  # Tico's own upkeep, not a department: it has no lead and is not checked here
            continue
        for slug in group["bot_slugs"]:
            seen, at = set(), slug  # a bot's reports_to is a bot's slug or "human:<id>"
            while at in by_slug and at not in seen:
                seen.add(at)
                at = by_slug[at]["reports_to"] or ""
            assert at in PEOPLE, f"{slug} answers to a person ({at or 'nobody'})"


def test_each_department_lead_has_a_goal_that_rolls_up_to_the_company_goal(ana):
    goals = {goal["id"]: goal for goal in ana.get("/api/v2/goals?all=true").json()["goals"]}
    [company] = [goal for goal in goals.values() if goal["owner_id"] == "company"]
    for _gid, _members, lead in D.GROUP_FIXES:
        if not lead.startswith("bot:"):
            continue
        mine = [goal for goal in goals.values() if goal["owner_id"] == lead and goal["status"] != "dropped"]
        assert mine, f"{lead} has a goal"
        for goal in mine:
            at, steps = goal, 0
            while at["parent_id"] and steps < 10:
                at, steps = goals[at["parent_id"]], steps + 1
            assert at["id"] == company["id"], f"{goal['title']} rolls up to the company goal"
