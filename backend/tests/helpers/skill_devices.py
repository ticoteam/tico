"""Helpers that other tests share, moved out of test_skill_devices.py so they import no test file."""

import json
from backend.skill_marketplace import bundle_sha
from backend.store import H
from backend.tests.helpers.api import post


def bearer(token):
    return {"Authorization": "Bearer " + token}


def ask(api, label="Cara's laptop", expected=201):
    r = api.post("/api/v2/skills/devices/codes", json={"label": label})
    assert r.status_code == expected, r.text
    return r.json()


def poll(api, code):
    return api.post("/api/v2/skills/devices/token", json={"device_code": code["device_code"]})


def connect(api, reporting=False, person="cara-test"):
    code = ask(api)
    post(api, "skills/devices/codes/" + code["user_code"] + "/approve", {"reporting_enabled": reporting}, token=person)
    got = poll(api, code)
    assert got.status_code == 200, got.text
    return got.json()


def add_skill(api, name, *bodies):
    """A skill with one approved version per body (the last is latest); returns (skill id, [version ids])."""
    now, skill_id, versions = H.now(), "sk_" + name, []
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO skills(id,name,description,created_by_type,created_by_id,created_at,updated_at) "
            "VALUES(?,?,?,'Human','ana',?,?)",
            (skill_id, name, name + " skill", now, now),
        )
        for number, body in enumerate(bodies, 1):
            files = [{"path": "SKILL.md", "content": body}, {"path": "scripts/run.sh", "content": "echo " + name}]
            vid = f"skv_{name}_{number}"
            c.execute(
                "INSERT INTO skill_versions(id,skill_id,number,sha256,files,description,proposal_id,approved_by_id,"
                "approved_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    vid,
                    skill_id,
                    number,
                    bundle_sha(files),
                    json.dumps(files),
                    f"{name} v{number}",
                    "skp_x",
                    "ana",
                    now,
                    now,
                ),
            )
            versions.append(vid)
        c.execute("UPDATE skills SET latest_version_id=? WHERE id=?", (versions[-1], skill_id))
    return skill_id, versions


def install_for(api, skill_id, person, pinned=None):
    now = H.now()
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO skill_installs(id,skill_id,target_type,target_id,version_policy,pinned_version_id,"
            "installed_by_type,installed_by_id,created_at,updated_at) VALUES(?,?,'Human',?,?,?,'Human','ana',?,?)",
            ("ski_" + skill_id + person, skill_id, person, "pinned" if pinned else "latest", pinned, now, now),
        )


def rows(api, sql, *args):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(sql, args)]
