"""Helpers that other tests share, moved out of test_tasks_board.py so they import no test file."""

import uuid
import pytest
from fastapi.testclient import TestClient
from backend import hubdb as H
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import encode
from backend import bot_settings as BS


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "TITLE_LINT", "warn")
    app = create_app(
        Settings(
            db_path=tmp_path / "hub.db",
            test_identities={
                "ana-test": Identity("human:ana", "owner", "ana@acme.example"),
                "ben-test": Identity("human:ben", "human", "ben@acme.example"),
                "priya-test": Identity("human:priya", "human", "priya@acme.example"),
                # BotOps acting for Ben (backend/botops_act.py): his rights, not Ben signed in.
                "ben-via-botops": Identity("human:ben", "human", "ben@acme.example", via="botops", confirmed=True),
            },
        )
    )
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            bots = {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in ("ops", "cpo", "cmo")}
            H.sync_registry(
                c,
                bots,
                {
                    "people": [
                        {"id": "ana", "email": "ana@acme.example", "team": "leadership"},
                        {"id": "ben", "email": "ben@acme.example", "team": "product"},
                        {"id": "priya", "email": "priya@acme.example", "team": "sales"},
                    ]
                },
            )
            c.execute(
                "INSERT INTO registry_metadata VALUES('people',?)",
                (
                    encode(
                        {
                            "people": [
                                {"id": "ana", "email": "ana@acme.example", "team": "leadership", "primary_for": ["*"]},
                                {"id": "ben", "email": "ben@acme.example", "team": "product"},
                                {"id": "priya", "email": "priya@acme.example", "team": "sales"},
                            ]
                        }
                    ),
                ),
            )
            for slug, config in bots.items():
                c.execute(
                    'INSERT INTO bot_config(bot,team,owner_id) VALUES(?,?,?)',
                    (slug, 'product' if slug == 'cpo' else 'marketing' if slug == 'cmo' else None, 'ana'),
                )
                BS.write(c, slug, config)
            c.execute(
                "INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({"completed": "2026-01-01T00:00:00Z"}),)
            )
        yield client


def headers(token="ana-test"):
    return {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid.uuid4())}


def post(api, path, body, token="ana-test", expected=200):
    r = api.post("/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    data = r.json()
    return data["task"] if isinstance(data, dict) and "task" in data and set(data) <= {"task", "readback"} else data


def relate(api, tid, other, kind="related", token="ana-test", expected=200, remove=False):
    """POST /tasks/{tid}/relations: `tid kind other`, or take it off with `remove`."""
    return post(
        api,
        "tasks/" + tid + "/relations",
        {"task": other, "kind": kind, "remove": remove},
        token=token,
        expected=expected,
    )


def get(api, path, token="ana-test", expected=200):
    r = api.get("/api/v2/" + path, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def bot_token(api, slug="ops"):
    """A leased bot identity: enroll a runner, assign the bot, send it a message, claim the turn."""
    code = post(api, "enrollments", {"operator": "ana"})["code"]
    r = post(api, "runners/enroll", {"code": code, "label": "Mac", "platform": "test"})
    post(api, "bots/" + slug + "/assignment", {"runner_id": r["runner_id"], "expected_generation": 0})
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {slug: True}}, token=r["token"])
    post(api, "chat/" + slug, {"text": "Please look at your queue."})
    return post(api, "jobs/claim", {"bot": slug}, token=r["token"])["attempt"]["token"]


def pipeline(api, token='ana-test'):
    # Not Marketing: a fresh file's idea type is named Marketing (backend/marketing_tasks.py).
    return post(
        api,
        'task-types',
        {
            'name': 'Campaign',
            'steps': [
                {'name': 'Draft', 'status': 'open'},
                {'name': 'Legal review', 'status': 'review'},
                {'name': 'Copy review', 'status': 'review'},
                {'name': 'Complete', 'status': 'done'},
                {'name': 'Archive', 'status': 'closed'},
            ],
        },
        token=token,
    )['type']
