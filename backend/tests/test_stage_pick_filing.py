"""Filing into a stage that picks its owner from several, over the API (spec "A stage picks its owner from several",
AC-3): a bot files a task into Build without an owner and it is picked; naming an owner always wins; a pipeline whose
first stage does not pick still needs one, and the dry run says the same. Synthetic rows only."""

from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers, post
from backend.tests.helpers.task_moves_routes import as_bot

BUILDERS = ["bot:builder", "bot:builder2"]


def _engineering(api):
    with api.app.state.store.transaction() as c:
        for slug in ("builder", "builder2", "manager", "filer"):
            X.bot(c, slug)
        X.pipeline(
            c,
            "eng",
            "Engineering",
            (
                {
                    "name": "Build",
                    "owner": "pick",
                    "pick": {"bots": BUILDERS, "how": "turns", "prefer": "anyone", "owns_task": True},
                    "requirements": [{"kind": "owner_done"}],
                },
            ),
            manager="bot:manager",
        )
    return as_bot(api, "filer")


def _new(api, token, title="Add negate", **fields):
    return api.post("/api/v2/tasks", json={"title": title, "body": "What to do.", **fields}, headers=headers(token))


def test_a_bot_files_into_build_without_an_owner_and_it_is_picked_and_naming_one_wins(api):
    token = _engineering(api)
    made = _new(api, token, pipeline="eng")
    assert made.status_code == 200, made.text
    task = made.json()["task"]
    assert task["owner_id"] == "bot:builder" and task["assignee_id"] == "bot:builder"
    assert task["requester_id"] == "bot:filer"
    second = _new(api, token, "Add abs", pipeline="eng").json()["task"]
    assert second["owner_id"] == "bot:builder2"  # took turns
    named = _new(api, token, "Add sign", pipeline="eng", owner="builder2").json()["task"]
    assert named["owner_id"] == "bot:builder2" and named["assignee_id"] == "bot:builder2"
    with api.app.state.store.read() as c:
        lines = [
            r[0]
            for r in c.execute(
                "SELECT note FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='pick'",
                (task["id"],),
            )
        ]
        assert lines == ["Build given to builder · took turns"]
        assert not c.execute(
            "SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='pick'", (named["id"],)
        ).fetchone()


def test_a_pipeline_whose_first_stage_does_not_pick_still_needs_an_owner(api):
    token = _engineering(api)
    refused = _new(api, token)  # General: its first stage is whoever the task is for
    error = refused.json()["error"]
    assert refused.status_code == 422 and error["code"] == "owner" and "first stage" in error["detail"]
    checked = post(api, "tasks/dry-run", {"title": "Add negate", "body": "x"}, token=token)
    assert checked["ok"] is False and "first stage" in checked["problems"][0]
    assert post(api, "tasks/dry-run", {"title": "Add negate", "body": "x", "pipeline": "eng"}, token=token)["ok"]


def test_a_task_a_bot_files_with_no_owner_in_its_own_run_starts_the_picked_bot_at_once(api):
    """The hold a bot's task for itself gets (filed_in: start once the filing run ends) is not carried to the bot the
    stage picks: the filer only stands in as requester."""
    import json

    from backend.store import H

    _engineering(api)
    run = H.RUN.set("attempt-of-filer")
    try:
        with api.app.state.store.transaction() as c:
            picked = H.task_create(
                c, "bot:filer", "Add negate", "What to do.", "bot:filer", pipeline="eng", unassigned=True
            )
            own = H.task_create(c, "bot:filer", "Tidy notes", "What to do.", "bot:filer")
    finally:
        H.RUN.reset(run)
    with api.app.state.store.read() as c:

        def wakes(task_id):
            rows = c.execute("SELECT recipient_id, refs FROM messages WHERE wake_key LIKE ?", (task_id + ":%",))
            return [(r[0], json.loads(r[1] or "{}").get("filed_in")) for r in rows]

        assert wakes(picked["id"]) == [("bot:builder", None)]
        assert wakes(own["id"]) == [("bot:filer", "attempt-of-filer")]  # its own task still waits for its run


def test_the_dry_run_with_no_owner_runs_the_other_checks_and_names_an_unknown_pipeline(api):
    token = _engineering(api)
    checked = post(api, "tasks/dry-run", {"title": "Add negate", "body": "x", "pipeline": "eng"}, token=token)
    assert checked["ok"] and checked["owner"] is None
    parent = post(api, "tasks/dry-run", {"title": "x", "body": "x", "pipeline": "eng", "parent": "nope"}, token=token)
    assert parent["ok"] is False  # an unknown parent is still caught with no owner named
    gone = post(api, "tasks/dry-run", {"title": "x", "body": "x", "pipeline": "nope"}, token=token)
    assert gone["problems"][0] == "No pipeline nope"
