"""Moving tasks to another pipeline over the API (docs/pipelines-v3.md "Move to another pipeline"): one task through
`POST /api/v2/tasks/{id}/move` with `to_pipeline`, many through `POST /api/v2/tasks/move`, and a stage removed from a
pipeline once its tasks have somewhere to go."""

from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import get, headers, post


def as_bot(api, slug="ops"):
    """A token for a bot of the company, as its own command would carry: an agent's (backend/agents.py)."""
    token = slug + "-bot-test"
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT OR IGNORE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
            (slug, "hermes", "x-" + slug, H.now(), "human:ana"),
        )
    api.app.state.auth.settings.test_identities[token] = Identity("bot:" + slug, "bot", agent="hermes")
    return token


def boards(api):
    """Dev (prefix D): Ideas, left by hand, then Build. Backlog (prefix B): On ice, then Next up."""
    done = {"owner": "assignee", "requirements": [{"kind": "owner_done"}]}
    post(
        api,
        "pipelines/dev",
        {
            "name": "Dev",
            "prefix": "D",
            "stages": [{"id": "ideas", "name": "Ideas", "by_hand": True}, {"id": "build", "name": "Build", **done}],
        },
    )
    post(
        api,
        "pipelines/backlog",
        {
            "name": "Backlog",
            "prefix": "B",
            "stages": [
                {"id": "on-ice", "name": "On ice", "by_hand": True},
                {"id": "next-up", "name": "Next up", **done},
            ],
        },
    )


def idea(api, title, owner="ben", token="ana-test", private=False):
    body = {"title": title, "body": "From the old board.", "owner": owner, "pipeline": "dev", "private": private}
    return post(api, "tasks", body, token=token)


def running(api, task_id, bot="ops"):
    """A bot's turn on the task, running now."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO attempts (id, bot_id, state, lease_until, created_at, started_at, task_id) "
            "VALUES (?,?,'running','2999-01-01T00:00:00.000000Z',?,?,?)",
            (H.new_id(), bot, H.now(), H.now(), task_id),
        )


def test_one_task_moves_to_another_pipeline_keeps_its_label_and_is_found_by_it(api):
    boards(api)
    made = idea(api, "Old Trello idea")
    moved = post(api, f"tasks/{made['id']}/move", {"to_pipeline": "Backlog", "to": "On ice", "why": "Parked"})
    assert (moved["pipeline"]["id"], moved["stage"]["id"], moved["number"]) == ("backlog", "on-ice", made["number"])
    for label in (f"D{made['number']}", f"d-{made['number']}"):
        assert get(api, "tasks/" + label)["task"]["id"] == made["id"], label
    r = api.get(f"/api/v2/tasks/B{made['number']}", headers=headers())
    assert r.status_code == 404, "the label stays the one it was made with"
    # Naming its own pipeline is the move inside it that it always was: forward, here.
    on = post(api, f"tasks/{made['id']}/move", {"to_pipeline": "backlog", "to": "Next up"})
    assert on["stage"]["id"] == "next-up" and on["prefix"] == "D"


def test_a_reader_moves_a_task_and_a_bot_says_why(api):
    boards(api)
    made = idea(api, "Ben's idea")
    # Cara is on no side of it, and reads it: that is enough to re-file it.
    moved = post(api, f"tasks/{made['id']}/move", {"to_pipeline": "backlog"}, token="cara-test")
    assert moved["stage"]["id"] == "on-ice", "the first stage when none is named"
    bot = as_bot(api)
    version = moved["version"]
    r = api.post(
        f"/api/v2/tasks/{made['id']}/move", json={"to_pipeline": "dev", "version": version}, headers=headers(bot)
    )
    assert r.status_code == 409 and r.json()["error"] == "task_why", r.text
    r = api.post("/api/v2/tasks/move", json={"ids": [made["id"]], "to_pipeline": "dev"}, headers=headers(bot))
    assert r.status_code == 422 and r.json()["error"] == "task_why", r.text
    back = post(
        api, f"tasks/{made['id']}/move", {"to_pipeline": "dev", "why": "Picked up again", "version": version}, token=bot
    )
    assert back["pipeline"]["id"] == "dev"


def test_many_move_in_one_write_and_what_cannot_move_is_listed_with_why(api):
    boards(api)
    mine = [idea(api, f"Idea {n}") for n in range(3)]
    hidden = idea(api, "Ana's own idea", owner="ana", private=True)
    busy = idea(api, "Being worked on")
    running(api, busy["id"])
    with api.app.state.store.read() as c:
        seq = c.execute("SELECT MAX(seq) FROM changes").fetchone()[0]
    ids = [t["id"] for t in (*mine, hidden, busy)]
    out = post(api, "tasks/move", {"ids": ids, "to_pipeline": "Backlog", "to": "On ice"}, token="ben-test")
    assert out["moved"] == [t["id"] for t in mine] and out["batch_id"]
    # One the mover may not read is counted, never named.
    assert [(s.get("id"), s["code"]) for s in out["skipped"]] == [
        (None, "not_found"),
        (busy["id"], "task_turn_running"),
    ]
    for t in mine:
        assert get(api, "tasks/" + t["id"])["task"]["stage"]["id"] == "on-ice"
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT kind FROM changes WHERE topic='tasks' AND seq>?", (seq,)).fetchall()
        events = c.execute(
            "SELECT json_extract(data,'$.batch') FROM history_events WHERE subject_id=? AND kind='move' "
            "ORDER BY rowid DESC LIMIT 1",
            (mine[0]["id"],),
        ).fetchone()
    assert [r[0] for r in rows if r[0] is not None][-1:] == ["bulk"] and len(rows) == 1, (
        "one live change, not one a task"
    )
    assert events[0] == out["batch_id"]


def test_every_open_task_of_a_stage_moves_by_naming_the_stage(api):
    boards(api)
    made = [idea(api, f"Idea {n}") for n in range(2)]
    out = post(api, "tasks/move", {"from": {"pipeline": "Dev", "stage": "Ideas"}, "to_pipeline": "backlog"})
    assert sorted(out["moved"]) == sorted(t["id"] for t in made) and out["skipped"] == []
    r = api.post(
        "/api/v2/tasks/move",
        json={"from": {"pipeline": "dev", "stage": "ideas"}, "ids": [made[0]["id"]], "to_pipeline": "backlog"},
        headers=headers(),
    )
    assert r.status_code == 422, "ids or from, not both"


def test_a_stage_that_holds_tasks_is_removed_once_they_have_somewhere_to_go(api):
    boards(api)
    made = [idea(api, f"Idea {n}") for n in range(2)]
    record = get(api, "pipelines/dev")["pipeline"]
    kept = {**record, "stages": [s for s in record["stages"] if s["id"] != "ideas"]}
    r = api.post("/api/v2/pipelines/dev", json=kept, headers=headers())
    body = r.json()
    assert r.status_code == 409 and body["error"]["code"] == "stage_has_tasks", r.text
    assert body["error"]["stages"] == [{"id": "ideas", "name": "Ideas", "tasks": 2}]
    assert body["error"]["move_to"] == {"pipeline": "dev", "stage": "build"}
    r = api.post(
        "/api/v2/pipelines/dev",
        json={**kept, "move_tasks_to": {"pipeline": "dev", "stage": "ideas"}},
        headers=headers(),
    )
    assert r.status_code == 409, "not to a stage that goes"
    saved = post(api, "pipelines/dev", {**kept, "move_tasks_to": {"pipeline": "Backlog", "stage": "On ice"}})
    assert [s["id"] for s in saved["pipeline"]["stages"]] == ["build"]
    for t in made:
        task = get(api, "tasks/" + t["id"])["task"]
        assert (task["pipeline"]["id"], task["stage"]["id"]) == ("backlog", "on-ice")


def test_a_pipeline_that_holds_tasks_retires_once_they_move_to_another(api):
    boards(api)
    made = [
        idea(api, "Idea"),
        post(api, "tasks", {"title": "Build it", "body": "x", "owner": "ben", "pipeline": "dev"}),
    ]
    version = get(api, "pipelines/dev")["pipeline"]["settings_version"]
    r = api.post("/api/v2/pipelines/dev/retire", json={"settings_version": version}, headers=headers())
    assert r.status_code == 409, "it holds open tasks"
    r = api.post(
        "/api/v2/pipelines/dev/retire",
        json={"settings_version": version, "move_tasks_to": {"pipeline": "dev", "stage": "build"}},
        headers=headers(),
    )
    assert r.status_code == 409, "not into itself"
    out = post(
        api,
        "pipelines/dev/retire",
        {"settings_version": version, "move_tasks_to": {"pipeline": "Backlog", "stage": "On ice"}},
    )
    assert out["pipeline"]["retired_at"]
    for t in made:
        assert get(api, "tasks/" + t["id"])["task"]["stage"]["id"] == "on-ice"
