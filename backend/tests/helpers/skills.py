"""Helpers that other tests share, moved out of test_skills.py so they import no test file."""

from backend.store import H
from backend.tests.helpers.api import headers


SKILL = "---\nname: triage\ndescription: Sorts the inbox by urgency\n---\n\nSort the inbox by urgency.\n"


NEWER = (
    "---\nname: triage\ndescription: Sorts the inbox by urgency\n---\n\nSort the inbox by urgency, then by sender.\n"
)


def bundle(text=SKILL, *extra):
    return [{"path": "SKILL.md", "content": text}, *extra]


def proposal(api, files=None, token="ana-test", name="triage", expected=201, **body):
    r = api.post(
        "/api/v2/skills/proposals",
        json={"name": name, "files": files or bundle(SKILL.replace("name: triage", "name: " + name)), **body},
        headers=headers(token),
    )
    assert r.status_code == expected, r.text
    return r.json()["proposal"] if expected == 201 else r.json()


def decide(api, pid, verb, body=None, token="ben-test", expected=200):
    r = api.post(f"/api/v2/skills/proposals/{pid}/{verb}", json=body or {}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def screen(api, pid, *, sha=None, base="same", verdict="safe", risk="low", status="screened"):
    """What the screening job writes (track 3): one row per screen, and the proposal's screen status."""
    with api.app.state.store.transaction() as c:
        row = c.execute("SELECT sha256, base_version_id FROM skill_proposals WHERE id=?", (pid,)).fetchone()
        screened = status == "screened"
        c.execute(
            "INSERT INTO skill_screenings (id, proposal_id, sha256, base_version_id, question_set, status, reason, verdict, risk, "
            "summary, findings, patterns, judge_call_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                H.new_id(),
                pid,
                sha or row["sha256"],
                row["base_version_id"] if base == "same" else base,
                "skill-screen@1",
                status,
                None if screened else "error",
                verdict if screened else None,
                risk if screened else None,
                "Reads the files.",
                "[]",
                "[]",
                None,
                H.now(),
            ),
        )
        c.execute(
            "UPDATE skill_proposals SET screen_status=? WHERE id=?", ("screened" if screened else "not_screened", pid)
        )


def approve(api, pid, token="ben-test", **body):
    return api.post(f"/api/v2/skills/proposals/{pid}/approve", json=body, headers=headers(token))


def publish(api, files=None, name="triage", **body):
    """A proposal, screened clean and approved by Ben: the skill's next version."""
    pid = proposal(api, files, name=name, **body)["id"]
    screen(api, pid)
    return approve(api, pid).json()
