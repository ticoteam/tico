"""Skill suggestions (backend/skill_suggestions.py) and the counts of skills outside the directory: remove only what
coverage calls unused (never unknown), promote at 2 bots or 10 uses in 30 days, one suggestion per subject until it is
acted on, dismissed or 30 days old, admins only, nothing removed until an admin says so, and the counts kept 90 days.

Ana owns the company, Ben is an admin, Cara is a member (the base fixture). Bots run Claude Code here unless a test
says otherwise: only a harness that reports every skill use lets coverage call a bot's install unused.
"""

from datetime import timedelta

import pytest

from backend import bot_settings as BS
from backend import skill_suggestions as SS
from backend import skill_usage as U
from backend.maintenance_steps import suggest as step
from backend.store import H
from backend.tests.helpers.api import get, headers, post  # noqa: F401  (fixture)
from backend.tests.helpers.botops_parity import bot_id
from backend.tests.helpers.skill_usage import NOW, skill_id, ts, use
from backend.tests.helpers.skills import proposal, publish


def unlisted(api, name, bot, uses, days=1, at=NOW):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO unlisted_skill_usage_dailies (used_on, name, bot_id, uses_count) VALUES (?,?,?,?) "
            "ON CONFLICT(used_on, name, bot_id) DO UPDATE SET uses_count=uses_count+excluded.uses_count",
            ((at - timedelta(days=days)).date().isoformat(), name, bot, uses),
        )


def installed(api, target_type, target_id, *, age=40, staged=True, reporting=False, harness="claude"):
    r = api.post(
        "/api/v2/skills/triage/installs",
        json={"target_type": target_type, "target_id": target_id, "version_policy": "latest"},
        headers=headers("ana-test"),
    )
    assert r.status_code == 200, r.text
    iid = r.json()["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE skill_installs SET created_at=? WHERE id=?", (ts(NOW - timedelta(days=age)), iid))
        if target_type == "Bot":
            BS.update(c, target_id, {"harness": harness})
        if staged:
            c.execute(
                "INSERT INTO skill_receipts (target_type, target_id, skill_id, sha256, source, staged_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    target_type,
                    target_id,
                    skill_id(api),
                    "a" * 64,
                    "runner" if target_type == "Bot" else "helper",
                    H.now(),
                ),
            )
        if reporting:
            c.execute(
                "INSERT INTO skill_devices (id, person_id, label, token_hash, reporting_enabled, created_at, "
                "updated_at) VALUES (?, ?, 'laptop', ?, 1, ?, ?)",
                ("skd_" + target_id, target_id, "h" + target_id, H.now(), H.now()),
            )
    return iid


def rows(api):
    with api.app.state.store.read() as c:
        return {(r["kind"], r["subject"]): dict(r) for r in c.execute("SELECT * FROM skill_suggestions")}


def refresh(api, at=NOW):
    return SS.run(api.app.state.store, at)


# ------------------------------------------------------------------ what is suggested
def test_remove_is_suggested_only_for_installs_coverage_calls_unused_never_unknown(api):
    publish(api)
    ops, finance, cpo = bot_id(api, "ops"), bot_id(api, "finance"), bot_id(api, "cpo")
    unused_bot = installed(api, "Bot", ops)
    installed(api, "Bot", finance, staged=False)  # no receipt: unknown
    installed(api, "Bot", cpo, age=10)  # under 30 days: unknown
    installed(api, "Bot", bot_id(api, "assistant"), harness="codex")  # its harness does not report every use: unknown
    installed(api, "Human", "cara")  # staged, but her helper does not report: unknown
    unused_person = installed(api, "Human", "ben", reporting=True)
    used = installed(api, "Human", "ana", reporting=True)
    use(api, ("Human", "ana"), days=2)
    assert refresh(api) == {"made": 2, "closed": 0}
    assert {k for k in rows(api)} == {("remove", unused_bot), ("remove", unused_person)}
    assert used not in {s for _, s in rows(api)}


def test_remove_follows_coverage_not_a_copy_of_its_rule(api, monkeypatch):
    """When coverage() calls a bot "unknown" (say a harness that does not report), no remove is suggested for it."""
    publish(api)
    ops, finance = bot_id(api, "ops"), bot_id(api, "finance")
    installed(api, "Bot", ops)
    kept = installed(api, "Bot", finance)
    real = U.coverage

    def stub(c, auth, who, skill, at):
        return [{**r, "coverage": "unknown"} if r["target"]["id"] == ops else r for r in real(c, auth, who, skill, at)]

    monkeypatch.setattr(U, "coverage", stub)
    refresh(api)
    assert set(rows(api)) == {("remove", kept)}


def test_promote_needs_two_bots_or_ten_uses_in_thirty_days_and_a_name_outside_the_directory(api):
    publish(api)
    ops, finance = bot_id(api, "ops"), bot_id(api, "finance")
    unlisted(api, "two-bots", ops, 1)
    unlisted(api, "two-bots", finance, 1)
    unlisted(api, "ten-uses", ops, 7)
    unlisted(api, "ten-uses", ops, 3, days=5)
    unlisted(api, "ten-uses", finance, 2, days=3)
    unlisted(api, "nine-uses", ops, 9)
    unlisted(api, "long-ago", ops, 50, days=40)
    unlisted(api, "triage", ops, 20)  # in the directory now
    unlisted(api, "proposed", ops, 20)
    proposal(api, [{"path": "SKILL.md", "content": "---\nname: proposed\n---\n\nx\n"}], name="proposed")
    refresh(api)
    found = {s: r for (k, s), r in rows(api).items() if k == "promote"}
    assert set(found) == {"two-bots", "ten-uses"}
    assert (found["ten-uses"]["target_id"], found["ten-uses"]["uses_count"], found["ten-uses"]["bots_count"]) == (
        ops,
        12,
        2,
    ), "it asks the bot that used it most"


# ------------------------------------------------------------------ once per subject
def test_one_suggestion_per_subject_until_acted_on_dismissed_or_thirty_days_old(api):
    publish(api)
    iid = installed(api, "Bot", bot_id(api, "ops"))
    assert refresh(api)["made"] == 1
    assert refresh(api, NOW + timedelta(hours=1)) == {"made": 0, "closed": 0}, "running again adds nothing"
    (row,) = rows(api).values()
    post(api, f"skills/suggestions/{row['id']}/dismiss", {}, "ben-test")
    assert refresh(api, NOW + timedelta(days=20))["made"] == 0, "dismissed: not again within 30 days"
    assert rows(api)[("remove", iid)]["status"] == "dismissed"
    assert refresh(api, NOW + timedelta(days=31))["made"] == 1, "30 days after it closed, it comes back if it holds"
    again = rows(api)[("remove", iid)]
    assert again["status"] == "open" and again["id"] == row["id"] and again["closed_by_id"] is None
    # Open 30 days without an answer: expired, and not made again in the same pass.
    assert refresh(api, NOW + timedelta(days=62)) == {"made": 0, "closed": 1}
    assert rows(api)[("remove", iid)]["status"] == "expired"


def test_a_suggestion_that_no_longer_holds_closes_and_the_page_never_shows_it(api):
    publish(api)
    ops = bot_id(api, "ops")
    iid = installed(api, "Bot", ops)
    unlisted(api, "home-notes", ops, 12)
    refresh(api)
    assert len(get(api, "skills/suggestions", "ben-test")["suggestions"]) == 2
    api.delete(f"/api/v2/skills/installs/{iid}", headers=headers("ana-test"))
    proposal(api, [{"path": "SKILL.md", "content": "---\nname: home-notes\n---\n\nx\n"}], name="home-notes")
    assert get(api, "skills/suggestions", "ben-test")["suggestions"] == [], "the page filters before the next run"
    assert refresh(api, NOW + timedelta(hours=1)) == {"made": 0, "closed": 2}
    assert {r["status"] for r in rows(api).values()} == {"acted"}


# ------------------------------------------------------------------ admins only; nothing on its own
def test_suggestions_and_the_counts_outside_the_directory_are_for_admins_only(api):
    publish(api)
    ops, finance = bot_id(api, "ops"), bot_id(api, "finance")
    iid = installed(api, "Bot", ops)
    unlisted(api, "home-notes", ops, 8)
    unlisted(api, "home-notes", finance, 4)
    unlisted(api, "triage", ops, 3)
    refresh(api)
    assert api.get("/api/v2/skills/suggestions").status_code in (401, 403)
    assert api.get("/api/v2/skills/suggestions", headers=headers("cara-test")).status_code == 403
    assert get(api, "skills/usage", "cara-test")["popular_elsewhere"] is None
    (other,) = get(api, "skills/usage", "ben-test")["popular_elsewhere"]
    assert (other["name"], other["uses"], other["bots_count"]) == ("home-notes", 12, 2)
    assert [b["id"] for b in other["bots"]] == [ops, finance], "the most using first; directory skills never listed"

    by_kind = {s["kind"]: s for s in get(api, "skills/suggestions", "ben-test")["suggestions"]}
    remove = by_kind["remove"]
    assert remove["install_id"] == iid and remove["target"]["id"] == ops
    for verb in ("remove", "asked", "dismiss"):
        post(api, f"skills/suggestions/{remove['id']}/{verb}", {}, "cara-test", expected=403)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM skill_installs WHERE id=?", (iid,)).fetchone(), "refreshing removes nothing"
    post(api, f"skills/suggestions/{remove['id']}/asked", {}, "ben-test", expected=404)  # Ask is for promote only
    assert post(api, f"skills/suggestions/{remove['id']}/remove", {}, "ben-test")["removed"] is True
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM skill_installs WHERE id=?", (iid,)).fetchone() is None
    assert rows(api)[("remove", iid)]["status"] == "acted" and rows(api)[("remove", iid)]["closed_by_id"] == "ben"
    post(api, f"skills/suggestions/{remove['id']}/remove", {}, "ben-test", expected=409)
    # Asking a bot to submit closes the suggestion and submits nothing: the bot runs the submit, an admin approves it.
    post(api, f"skills/suggestions/{by_kind['promote']['id']}/asked", {}, "ben-test")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM skill_proposals WHERE name='home-notes'").fetchone()[0] == 0


# ------------------------------------------------------------------ retention and the run
def test_the_rollup_keeps_counts_outside_the_directory_90_days(api):
    ops = bot_id(api, "ops")
    unlisted(api, "home-notes", ops, 2, days=95)
    unlisted(api, "home-notes", ops, 3, days=89)
    found = U.run(api.app.state.store, NOW)
    assert found["unlisted_pruned"] == 1
    with api.app.state.store.read() as c:
        assert [r[0] for r in c.execute("SELECT uses_count FROM unlisted_skill_usage_dailies")] == [3]


def test_the_suggest_step_makes_them_without_the_gateway_and_not_when_it_is_off(api):
    publish(api)
    installed(api, "Bot", bot_id(api, "ops"))
    store = api.app.state.store
    ctx = {"store": store, "run_id": H.new_id(), "settings": {"key": "suggest", "enabled": 0}, "now": H.now()}
    with store.read() as c:
        assert step.run(c, ctx)["reason"] == "Suggest is off"
    assert rows(api) == {}
    with store.read() as c:
        result = step.run(c, {**ctx, "settings": {"key": "suggest", "enabled": 1}})
    assert result["reason"] in ("Needs Tico Gateway", "Privacy consent is off")
    assert result["details"]["skills"] == {"made": 1, "closed": 0} and result["found"] == 1
