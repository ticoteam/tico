"""Skill usage (backend/skill_usage.py): the daily rollup is idempotent and picks up late rows, raw uses past 90 days go
while their rollup stays, a member never sees another person's uses, a use's task link shows only to someone who may
read the task, and coverage says unknown rather than unused where Tico has no data.

Ana owns the company, Ben is an admin, Cara is a member (the base fixture).
"""

from datetime import timedelta

import pytest

from backend import bot_settings as BS
from backend import skill_usage as U
from backend.store import H
from backend.tests.helpers.api import get, headers, restrict  # noqa: F401  (fixture)
from backend.tests.helpers.botops_parity import bot_id
from backend.tests.helpers.skills import publish
from backend.tests.helpers.skill_usage import NOW, skill_id, ts, use


def dailies(api):
    with api.app.state.store.read() as c:
        return sorted(
            tuple(r)
            for r in c.execute("SELECT used_on, actor_type, actor_id, trigger, uses_count FROM skill_usage_dailies")
        )


def raw_count(api):
    with api.app.state.store.read() as c:
        return c.execute("SELECT COUNT(*) FROM skill_usages").fetchone()[0]


def day(days):
    return (NOW - timedelta(days=days)).date().isoformat()


# ------------------------------------------------------------------ the rollup
def test_the_rollup_is_idempotent_takes_late_rows_and_keeps_the_rollup_of_pruned_uses(api):
    publish(api)
    store = api.app.state.store
    use(api, days=3)
    use(api, days=3, trigger="person")
    use(api, ("Bot", bot_id(api, "ops")), days=3)
    use(api, days=95)  # past the keep, before any rollup ever ran
    use(api, days=89.5)
    first = U.run(store, NOW - timedelta(days=6))  # a run back then: 95 days ago was still kept
    assert first["pruned"] == 0
    assert (day(95), "Human", "cara", "model", 1) in dailies(api)

    found = U.run(store, NOW)
    assert found["pruned"] == 1 and raw_count(api) == 4
    once = dailies(api)
    assert (day(95), "Human", "cara", "model", 1) in once, "a pruned day keeps its counts"
    assert (day(3), "Human", "cara", "model", 1) in once and (day(3), "Human", "cara", "person", 1) in once
    assert U.run(store, NOW + timedelta(minutes=5)) == {"days": 0, "pruned": 0, "unlisted_pruned": 0}
    assert dailies(api) == once, "running it again changes nothing"

    # A use the helper reports late, for a day already rolled up, is counted on the next run without doubling the rest.
    use(api, days=3, created=NOW + timedelta(minutes=30))
    U.run(store, NOW + timedelta(hours=1))
    assert (day(3), "Human", "cara", "model", 2) in dailies(api)
    assert sum(r[4] for r in dailies(api) if r[0] == day(3)) == 4

    # A day that is partly past the keep is never recomputed from what is left of it.
    with store.transaction() as c:
        c.execute("UPDATE skill_usage_dailies SET uses_count=7 WHERE used_on=?", (day(95),))
    use(api, days=95, created=NOW + timedelta(hours=2))
    U.run(store, NOW + timedelta(hours=2))
    assert (day(95), "Human", "cara", "model", 7) in dailies(api)
    assert raw_count(api) == 5, "the late row past the keep is pruned, not counted"


def test_reads_count_the_rollup_and_todays_raw_uses_once_each(api):
    publish(api)
    use(api, days=10)
    use(api, days=10)
    U.run(api.app.state.store, NOW)
    use(api, days=0)  # not rolled up yet: still counted
    (row,) = get(api, "skills/usage?days=30", "ben-test")["skills"]
    assert row["uses"] == 3 and row["people"] == 1 and row["bots"] == 0
    (week,) = get(api, "skills/usage?days=7", "ben-test")["skills"]
    assert week["uses"] == 1
    assert api.get("/api/v2/skills/usage?days=9", headers=headers("ben-test")).status_code == 422


# ------------------------------------------------------------------ privacy
def test_a_member_sees_totals_and_only_her_own_rows_and_an_admin_sees_who(api):
    publish(api)
    ops = bot_id(api, "ops")
    use(api, ("Human", "cara"), days=1)
    use(api, ("Human", "ben"), days=1)
    use(api, ("Human", "ben"), days=2)
    use(api, ("Bot", ops), days=1, trigger="person", requested_by="cara", source="thread")
    use(api, ("Bot", ops), days=2, trigger="person", requested_by="ben", source="thread")

    (mine,) = get(api, "skills/usage", "cara-test")["skills"]
    assert mine["uses"] == 5 and mine["people"] == 2 and mine["bots"] == 1, "totals"
    assert [(a["type"], a["id"]) for a in mine["top"]] == [("Human", "cara")], "only herself among the top"
    (admin,) = get(api, "skills/usage", "ben-test")["skills"]
    assert {a["id"] for a in admin["top"]} == {"cara", "ben", ops}

    detail = get(api, "skills/triage/usage", "cara-test")
    assert detail["totals"]["uses"] == 5 and not detail["admin"]
    assert [a["id"] for a in detail["actors"]] == ["cara"]
    seen = {(u["actor"]["id"], (u["requested_by"] or {}).get("id")) for u in detail["recent"]}
    assert seen == {("cara", None), (ops, "cara")}, "her uses and the one she asked a bot for, never Ben's"
    assert "ben" not in str(detail)
    full = get(api, "skills/triage/usage", "ben-test")
    assert len(full["recent"]) == 5 and {a["id"] for a in full["actors"]} == {"cara", "ben", ops}


def test_a_use_links_its_task_only_for_someone_who_may_read_it(api):
    publish(api)
    with api.app.state.store.transaction() as c:
        restrict(c, bot_id(api, "ops"), see={"everyone": True}, read={"people": ["ana"]}, write={"people": ["ana"]})
        hidden = H.task_create(c, "human:ana", "Payroll run", "x", "bot:ops")["id"]
        open_ = H.task_create(c, "human:cara", "Inbox sweep", "x", "human:cara")["id"]
    use(api, ("Human", "cara"), days=1, task_id=hidden)
    use(api, ("Human", "cara"), days=2, task_id=open_)
    links = {u["used_at"]: u["task_id"] for u in get(api, "skills/triage/usage", "cara-test")["recent"]}
    assert set(links.values()) == {None, open_}
    assert hidden in {u["task_id"] for u in get(api, "skills/triage/usage", "ana-test")["recent"]}


# ------------------------------------------------------------------ coverage
def test_coverage_is_unknown_without_data_and_unused_only_with_a_receipt_reporting_and_thirty_days(api):
    publish(api)
    store = api.app.state.store
    ops, finance = bot_id(api, "ops"), bot_id(api, "finance")
    for target_type, target_id in (("Bot", ops), ("Bot", finance), ("Human", "cara"), ("Human", "ben")):
        r = api.post(
            "/api/v2/skills/triage/installs",
            json={"target_type": target_type, "target_id": target_id, "version_policy": "latest"},
            headers=headers("ana-test"),
        )
        assert r.status_code == 200, r.text
    with store.transaction() as c:
        sid = skill_id(api)
        BS.update(c, ops, {"harness": "claude"})  # a harness that reports every skill use
        c.execute("UPDATE skill_installs SET created_at=?", (ts(NOW - timedelta(days=40)),))
        for target_type, target_id in (("Bot", ops), ("Human", "cara"), ("Human", "ben")):
            c.execute(
                "INSERT INTO skill_receipts (target_type, target_id, skill_id, sha256, source, staged_at) VALUES (?,?,?,?,?,?)",
                (target_type, target_id, sid, "a" * 64, "runner" if target_type == "Bot" else "helper", H.now()),
            )
        c.execute(
            "INSERT INTO skill_devices (id, person_id, label, token_hash, reporting_enabled, created_at, updated_at) "
            "VALUES ('skd_1', 'ben', 'laptop', 'h1', 1, ?, ?)",
            (H.now(), H.now()),
        )
        c.execute(
            "INSERT INTO skill_devices (id, person_id, label, token_hash, reporting_enabled, created_at, updated_at) "
            "VALUES ('skd_2', 'cara', 'laptop', 'h2', 0, ?, ?)",
            (H.now(), H.now()),
        )
    use(api, ("Human", "ben"), days=40)  # outside the 30 days
    states = {i["target"]["id"]: i["coverage"] for i in get(api, "skills/triage/usage", "ana-test")["installs"]}
    assert states == {ops: "unused", finance: "unknown", "cara": "unknown", "ben": "unused"}

    use(api, ("Bot", ops), days=1)
    with store.transaction() as c:
        c.execute("UPDATE skill_installs SET created_at=? WHERE target_id='ben'", (H.now(),))
    states = {
        i["target"]["id"]: (i["coverage"], i["uses_30d"])
        for i in get(api, "skills/triage/usage", "ana-test")["installs"]
    }
    assert states[ops] == ("used", 1) and states["ben"] == ("unknown", 0), (
        "an install under 30 days old says nothing yet"
    )
    # A member sees coverage only for the installs she may see: her own and bots she sees.
    assert "ben" not in {i["target"]["id"] for i in get(api, "skills/triage/usage", "cara-test")["installs"]}


def test_a_codex_bot_is_never_unused_since_codex_reports_only_the_skills_its_input_names(api):
    publish(api)
    store = api.app.state.store
    claude, codex = bot_id(api, "ops"), bot_id(api, "finance")
    for target in (claude, codex):
        r = api.post(
            "/api/v2/skills/triage/installs",
            json={"target_type": "Bot", "target_id": target, "version_policy": "latest"},
            headers=headers("ana-test"),
        )
        assert r.status_code == 200, r.text
    with store.transaction() as c:
        sid = skill_id(api)
        BS.update(c, claude, {"harness": "claude"})
        BS.update(c, codex, {"harness": "codex"})
        c.execute("UPDATE skill_installs SET created_at=?", (ts(NOW - timedelta(days=40)),))
        for target in (claude, codex):
            c.execute(
                "INSERT INTO skill_receipts (target_type, target_id, skill_id, sha256, source, staged_at) VALUES ('Bot',?,?,?,?,?)",
                (target, sid, "a" * 64, "runner", H.now()),
            )
    states = {i["target"]["id"]: i["coverage"] for i in get(api, "skills/triage/usage", "ana-test")["installs"]}
    assert states == {claude: "unused", codex: "unknown"}


def test_install_targets_offer_only_who_the_caller_may_install_for(api):
    publish(api)
    with api.app.state.store.transaction() as c:
        restrict(c, bot_id(api, "ops"), see={"everyone": True}, read={"everyone": True}, write={"people": ["ana"]})
    cara = get(api, "skills/install-targets", "cara-test")
    assert [p["id"] for p in cara["people"]] == ["cara"] and not cara["admin"]
    assert bot_id(api, "ops") not in {b["id"] for b in cara["bots"]}
    for target in (t for team in cara["teams"] for t in team["targets"]):
        assert (
            target["target_id"] == "cara"
            if target["target_type"] == "Human"
            else target["target_id"] != bot_id(api, "ops")
        )
    ana = get(api, "skills/install-targets", "ana-test")
    assert {"ana", "ben", "cara"} <= {p["id"] for p in ana["people"]} and bot_id(api, "ops") in {
        b["id"] for b in ana["bots"]
    }


def test_installed_lists_my_installs_and_the_bots_i_may_write_only(api):
    publish(api)
    ops, finance = bot_id(api, "ops"), bot_id(api, "finance")
    with api.app.state.store.transaction() as c:
        restrict(c, ops, see={"everyone": True}, read={"everyone": True}, write={"people": ["ana"]})
    for target_type, target_id in (("Bot", ops), ("Bot", finance), ("Human", "cara"), ("Human", "ben")):
        r = api.post(
            "/api/v2/skills/triage/installs",
            json={"target_type": target_type, "target_id": target_id, "version_policy": "latest"},
            headers=headers("ana-test"),
        )
        assert r.status_code == 200, r.text
    mine = {(i["target"]["type"], i["target"]["id"]) for i in get(api, "skills/installed", "cara-test")["installs"]}
    assert ("Human", "cara") in mine and ("Human", "ben") not in mine, "her own, never Ben's"
    assert ("Bot", ops) not in mine, "not a bot she may only read"
    writable = {b["id"] for b in get(api, "skills/install-targets", "cara-test")["bots"]}
    assert {i for t, i in mine if t == "Bot"} == writable & {ops, finance}
    (row,) = [i for i in get(api, "skills/installed", "cara-test")["installs"] if i["target"]["id"] == "cara"]
    assert row["version_policy"] == "latest" and row["latest"] == 1 and row["update_available"] is False
    assert row["desired"]["number"] == 1 and row["staged"] is None


@pytest.mark.parametrize("path", ["skills/usage", "skills/triage/usage", "skills/install-targets", "skills/installed"])
def test_the_reads_answer_people_and_refuse_nobody_signed_in(api, path):
    publish(api)
    assert api.get("/api/v2/" + path).status_code in (401, 403)
    assert api.get("/api/v2/" + path, headers=headers("cara-test")).status_code == 200


def test_an_install_on_the_skill_page_is_removable_only_with_the_rights_to_add_it(api):
    publish(api)
    ops, finance = bot_id(api, "ops"), bot_id(api, "finance")
    with api.app.state.store.transaction() as c:
        restrict(c, ops, see={"everyone": True}, read={"everyone": True}, write={"people": ["ana"]})
    for target_type, target_id, policy in (
        ("Bot", ops, "pinned"),
        ("Bot", finance, "latest"),
        ("Human", "cara", "latest"),
    ):
        body = {"target_type": target_type, "target_id": target_id, "version_policy": policy}
        if policy == "pinned":
            body["pinned_version"] = 1
        assert api.post("/api/v2/skills/triage/installs", json=body, headers=headers("ana-test")).status_code == 200

    def rows(token):
        return {
            i["target"]["id"]: (i["removable"], i["pinned_version"])
            for i in get(api, "skills/triage/usage", token)["installs"]
        }

    assert rows("ana-test") == {ops: (True, 1), finance: (True, None), "cara": (True, None)}
    assert rows("cara-test") == {ops: (False, 1), finance: (True, None), "cara": (True, None)}
