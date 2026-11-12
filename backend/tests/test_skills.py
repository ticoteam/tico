"""The company skills directory (backend/skills.py, clients/skills.py): the bundle rules and the sha, the packing of a folder on
the computer, who may propose, approve and install what, and the approval checks that keep an unscreened or moved-on version
from going out.

Ana owns the company, Ben is an admin, Cara is a member (the base fixture). `setup_attempt` gives a bot ("ops") its own token.
"""

import hashlib
import json

import pytest

from backend.skills import validate_bundle
from backend.store import H, Problem
from backend.tests.helpers.api import get, headers, restrict, setup_attempt  # noqa: F401  (fixture)
from backend.tests.helpers.botops_parity import bot_id
from clients import skills as bundles
from backend.tests.helpers.skills import NEWER, SKILL, approve, bundle, decide, proposal, publish, screen


def skill_row(api, name="triage"):
    with api.app.state.store.read() as c:
        return c.execute("SELECT * FROM skills WHERE name=?", (name,)).fetchone()


def versions(api, name="triage"):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT number, sha256 FROM skill_versions WHERE skill_id=? ORDER BY number",
                (skill_row(api, name)["id"],),
            ).fetchall()
        ]


# ------------------------------------------------------------------ the bundle rules
@pytest.mark.parametrize(
    "name, files, reason",
    [
        ("Triage", bundle(), "named with lowercase"),
        (
            "triage",
            [{"path": "SKILL.md", "content": SKILL}, {"path": "/abs.md", "content": "x"}],
            "not a relative path",
        ),
        ("triage", [{"path": "SKILL.md", "content": SKILL}, {"path": "a/../b.md", "content": "x"}], "segment"),
        ("triage", [{"path": "SKILL.md", "content": SKILL}, {"path": "a\\b.md", "content": "x"}], "relative path"),
        ("triage", [{"path": "SKILL.md", "content": SKILL}, {"path": "a//b.md", "content": "x"}], "segment"),
        ("triage", [{"path": "SKILL.md", "content": SKILL}, {"path": "a" * 201, "content": "x"}], "200 characters"),
        ("triage", [{"path": "SKILL.md", "content": SKILL}, {"path": "b.bin", "content": "a\0b"}], "binary"),
        ("triage", [{"path": "SKILL.md", "content": SKILL}] * 2, "twice"),
        ("triage", [{"path": "a.md", "content": "x"}], "no SKILL.md"),
        ("triage", bundle("---\nname: other\n---\nBody\n"), "names the skill other"),
        ("triage", bundle("---\ndescription: " + "d" * 1025 + "\n---\nBody\n"), "over 1024"),
        ("triage", [{"path": f"f{i}.md", "content": "x"} for i in range(101)], "at most 100"),
        ("triage", bundle("x" * 60_000, {"path": "big.md", "content": "y" * 40_000}), "characters of text in all"),
    ],
)
def test_a_bundle_breaking_one_rule_is_refused_with_the_reason(name, files, reason):
    problem = bundles.bundle_problem(name, files)
    assert problem and reason in problem
    with pytest.raises(Problem) as refused:
        validate_bundle(name, files)
    assert refused.value.status == 422 and reason in refused.value.detail


def test_a_proposal_keeps_each_file_byte_for_byte_so_its_sha_matches_the_submitters(api):
    files = bundle(SKILL + "\n\n", {"path": "notes.md", "content": "  indented\n"})
    p = proposal(api, files=files)
    assert p["sha256"] == bundles.bundle_sha(files)
    stored = api.get(f"/api/v2/skills/proposals/{p['id']}", headers=headers("ana-test")).json()["files"]
    assert stored == bundles.normalize(files)


def test_a_folder_is_packed_as_it_is_and_a_symlink_a_binary_file_or_an_oversize_one_is_refused(tmp_path):
    folder = tmp_path / "triage"
    (folder / "scripts").mkdir(parents=True)
    (folder / "SKILL.md").write_text(SKILL)
    (folder / "scripts" / "run.py").write_text("print('ok')\n")
    (folder / ".DS_Store").write_bytes(b"\0\0")  # Finder's noise: never part of a skill
    packed = bundles.pack(folder, "triage")
    assert [f["path"] for f in packed] == ["SKILL.md", "scripts/run.py"]

    (folder / "link.md").symlink_to(folder / "SKILL.md")
    with pytest.raises(bundles.SkillError) as link:
        bundles.pack(folder, "triage")
    assert link.value.code == "symlink"
    (folder / "link.md").unlink()

    (folder / "image.png").write_bytes(b"\x89PNG\0\x01")
    with pytest.raises(bundles.SkillError) as binary:
        bundles.pack(folder, "triage")
    assert binary.value.code == "binary"
    (folder / "image.png").unlink()

    (folder / "big.md").write_text("y" * 90_001)
    with pytest.raises(bundles.SkillError) as oversize:
        bundles.pack(folder, "triage")
    assert oversize.value.code == "bundle" and "characters" in oversize.value.detail


# ------------------------------------------------------------------ rights
def test_a_bot_and_a_member_propose_but_only_an_admin_approves_declines_and_installs_for_a_person(api):
    _, _, attempt = setup_attempt(api, "ops")
    bot = headers(attempt["token"])
    made = api.post("/api/v2/skills/proposals", json={"name": "triage", "files": bundle()}, headers=bot)
    assert made.status_code == 201 and made.json()["proposal"]["proposed_by"] == {
        "type": "Bot",
        "id": bot_id(api, "ops"),
    }
    pid = made.json()["proposal"]["id"]
    assert proposal(api, name="billing", token="cara-test")["proposed_by"]["type"] == "Human"

    screen(api, pid)
    assert approve(api, pid, token="cara-test").status_code == 403  # a member
    assert approve(api, pid, token=attempt["token"]).status_code == 403  # a bot
    assert api.post(f"/api/v2/skills/proposals/{pid}/decline", json={"reason": "no"}, headers=bot).status_code == 403
    assert approve(api, pid).status_code == 200  # an admin

    cara = proposal(api, name="billing", token="cara-test")["id"]
    assert decide(api, cara, "decline", {"reason": "Not yet"})["proposal"]["status"] == "declined"
    assert (
        api.post(
            "/api/v2/skills/triage/installs",
            json={"target_type": "Human", "target_id": "ben", "version_policy": "latest"},
            headers=headers("cara-test"),
        ).status_code
        == 403
    )  # someone else's install
    assert (
        api.post(
            "/api/v2/skills/triage/installs",
            json={"target_type": "Human", "target_id": "cara", "version_policy": "latest"},
            headers=headers("cara-test"),
        ).status_code
        == 200
    )  # her own


def test_an_install_for_a_bot_needs_write_on_it(api):
    publish(api)
    with api.app.state.store.transaction() as c:
        restrict(c, bot_id(api, "ops"), see={"everyone": True}, read={"everyone": True}, write={"people": ["ana"]})
    body = {"target_type": "Bot", "target_id": "ops", "version_policy": "latest"}
    assert api.post("/api/v2/skills/triage/installs", json=body, headers=headers("cara-test")).status_code == 403
    assert api.post("/api/v2/skills/triage/installs", json=body, headers=headers("ana-test")).status_code == 200


def test_a_member_sees_only_their_own_proposals_and_admins_see_all(api):
    proposal(api, token="ana-test")
    proposal(api, name="billing", token="cara-test")
    assert [p["proposed_by"]["id"] for p in get(api, "skills/proposals", "cara-test")["proposals"]] == ["cara"]
    assert len(get(api, "skills/proposals", "ben-test")["proposals"]) == 2


# ------------------------------------------------------------------ the approval checks
def test_approval_waits_for_the_exact_screened_version_and_its_base(api):
    pid = proposal(api)["id"]
    assert approve(api, pid).json()["error"]["code"] == "not_screened"  # nothing screened yet

    screen(api, pid, sha="0" * 64)  # a screen of some other bundle
    assert approve(api, pid).json()["error"]["code"] == "not_screened"

    screen(api, pid)
    first = approve(api, pid).json()
    assert first["version"]["number"] == 1 and first["proposal"]["status"] == "approved"
    assert [v["number"] for v in versions(api)] == [1]
    assert get(api, "skills")["skills"][0]["latest"]["number"] == 1

    # Ana proposes an update based on v1; Ben's own update lands first, so v1 is no longer the latest.
    update = proposal(api, bundle(NEWER), token="ana-test", base_version=1)
    screen(api, update["id"])
    newer = proposal(api, bundle(NEWER + "More.\n"), token="ben-test")
    screen(api, newer["id"])
    assert approve(api, newer["id"]).json()["version"]["number"] == 2
    stale = get(api, f"skills/proposals/{update['id']}")
    assert stale["stale"] is True and "diff_latest" in stale and stale["diff"]
    assert approve(api, update["id"]).json()["error"]["code"] == "stale"

    # Rescreening takes the clean screen away until the new one is in.
    decide(api, update["id"], "rescreen", token="ana-test")
    assert approve(api, update["id"]).json()["error"]["code"] == "not_screened"


def test_a_high_risk_or_unsafe_screen_needs_an_acknowledgement_and_an_unchanged_version_is_refused(api):
    pid = proposal(api)["id"]
    screen(api, pid, risk="high")
    assert approve(api, pid).status_code == 422
    assert approve(api, pid, acknowledged=True).status_code == 200
    again = api.post(
        "/api/v2/skills/proposals", json={"name": "triage", "files": bundle()}, headers=headers("ana-test")
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "unchanged"


def test_a_proposer_replaces_their_own_pending_proposal_and_nobody_elses(api):
    first = proposal(api, bundle(NEWER), token="ana-test")
    other = proposal(api, bundle(SKILL + "Other.\n"), token="cara-test")
    second = proposal(api, bundle(NEWER + "Again.\n"), token="ana-test")
    statuses = {p["id"]: p["status"] for p in get(api, "skills/proposals?status=pending", "ana-test")["proposals"]}
    assert first["id"] not in statuses and second["id"] in statuses and other["id"] in statuses
    assert get(api, f"skills/proposals/{first['id']}", "ana-test")["proposal"]["status"] == "withdrawn"


def test_a_proposal_is_withdrawn_only_by_its_proposer(api):
    pid = proposal(api, token="cara-test")["id"]
    assert api.post(f"/api/v2/skills/proposals/{pid}/withdraw", json={}, headers=headers("ana-test")).status_code == 403
    assert decide(api, pid, "withdraw", token="cara-test")["proposal"]["status"] == "withdrawn"


def test_events_name_the_skill_and_never_hold_its_text(api):
    publish(api)
    pid = proposal(api, bundle(NEWER.replace("name: triage", "name: billing")), name="billing")["id"]
    decide(api, pid, "decline", {"reason": "Not yet"})
    with api.app.state.store.read() as c:
        rows = [dict(r) for r in c.execute("SELECT action, detail FROM events WHERE action LIKE 'skill.%'").fetchall()]
    assert {"skill.proposed", "skill.approved", "skill.declined"} <= {r["action"] for r in rows}
    assert all("Sort the inbox" not in (r["detail"] or "") for r in rows)


# ------------------------------------------------------------------ installs
def test_installs_are_explicit_pinned_or_latest_and_show_what_is_staged(api):
    publish(api)
    publish(api, bundle(NEWER), name="triage")
    assert get(api, "skills")["skills"][0]["latest"]["number"] == 2
    install = {"target_type": "Bot", "target_id": "ops", "version_policy": "pinned", "pinned_version": 1}
    made = api.post("/api/v2/skills/triage/installs", json=install, headers=headers("ana-test")).json()
    assert made["version_policy"] == "pinned" and made["desired"]["number"] == 1 and made["staged"] is None

    with api.app.state.store.transaction() as c:  # the runner reports what it staged
        c.execute(
            "INSERT INTO skill_receipts (target_type, target_id, skill_id, sha256, source, staged_at) "
            "SELECT 'Bot', ?, id, ?, 'runner', ? FROM skills WHERE name='triage'",
            (bot_id(api, "ops"), versions(api)[0]["sha256"], H.now()),
        )
    listed = get(api, "skills/installs?target_type=Bot&target_id=ops")
    assert listed[0]["staged"]["sha256"] == versions(api)[0]["sha256"] and listed[0]["desired"]["number"] == 1

    missing = api.post(
        "/api/v2/skills/triage/installs", json={**install, "pinned_version": 9}, headers=headers("ana-test")
    )
    assert missing.status_code == 404

    bulk = {
        "targets": [{"target_type": "Human", "target_id": "cara"}, {"target_type": "Bot", "target_id": "nobody"}],
        "version_policy": "latest",
    }
    assert api.post("/api/v2/skills/triage/installs/bulk", json=bulk, headers=headers("ana-test")).status_code == 404
    assert len(get(api, "skills/triage", "ana-test")["installs"]) == 1  # all or nothing

    bulk["targets"] = bulk["targets"][:1]
    assert (
        len(api.post("/api/v2/skills/triage/installs/bulk", json=bulk, headers=headers("ana-test")).json()["installs"])
        == 1
    )


def test_an_install_is_taken_off_by_someone_with_the_rights_to_add_it(api):
    publish(api)
    with api.app.state.store.transaction() as c:
        restrict(c, bot_id(api, "ops"), see={"everyone": True}, read={"everyone": True}, write={"people": ["ana"]})
    made = api.post(
        "/api/v2/skills/triage/installs",
        json={"target_type": "Bot", "target_id": "ops", "version_policy": "latest"},
        headers=headers("ana-test"),
    ).json()
    assert api.delete(f"/api/v2/skills/installs/{made['id']}", headers=headers("cara-test")).status_code == 403
    assert api.delete(f"/api/v2/skills/installs/{made['id']}", headers=headers("ana-test")).status_code == 200
    assert get(api, "skills/triage", "ana-test")["installs"] == []


def test_a_skill_page_shows_only_the_installs_the_caller_may_see(api):
    publish(api)
    with api.app.state.store.transaction() as c:
        restrict(c, bot_id(api, "ops"), see={"people": ["ana"]}, read={"people": ["ana"]}, write={"people": ["ana"]})
    api.post(
        "/api/v2/skills/triage/installs",
        json={"target_type": "Bot", "target_id": "ops", "version_policy": "latest"},
        headers=headers("ana-test"),
    )
    api.post(
        "/api/v2/skills/triage/installs",
        json={"target_type": "Human", "target_id": "cara", "version_policy": "latest"},
        headers=headers("ana-test"),
    )
    assert [i["target"]["id"] for i in get(api, "skills/triage", "cara-test")["installs"]] == ["cara"]
    assert len(get(api, "skills/triage", "ben-test")["installs"]) == 2
