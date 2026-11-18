"""News from GitHub about a task's PR starts its owner's run at once: no notice wait (Product #546, #248 criterion 15).

Production (Nov 13-17 2026) never held one of these: on a pipeline task they are the owner's turn. A plain task's
review comment used to be marked for the 5-minute notice wait (refs.wake_batch); nothing marks GitHub news now, and the
bot's settings no longer promise that wait. Synthetic rows only (backend/tests/helpers/engine.py)."""

import json

import pytest

from backend import github as G
from backend.tests.helpers import stage_inputs as T

URL = f"https://github.com/{T.REPO}/pull/12"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def _news(c, task_id):
    """The refs of the notices GitHub's review comments wrote in the task's conversation."""
    rows = c.execute(
        "SELECT m.refs FROM messages m JOIN tasks t ON t.conversation_id=m.conversation_id "
        "WHERE t.id=? AND m.body LIKE '%review comment%' ORDER BY m.rowid",
        (task_id,),
    ).fetchall()
    return [json.loads(r[0] or "{}") for r in rows]


def _comment(c, n):
    G.pr_signal(
        c,
        "pull_request_review_comment",
        {
            "action": "created",
            "repository": {"full_name": T.REPO},
            "pull_request": {"html_url": URL},
            "comment": {"id": n, "user": {"login": "sam"}},
        },
    )


def _pipeline_task_in_build(c):
    _product, eng = T._filed_engineering(c)
    T._move(c, eng, "engineering-build")
    T._link_pr(c, eng, 12, "aaa")
    c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")  # its Build turn was worked
    return eng


def test_a_persons_review_comment_on_a_pipeline_task_is_never_marked_for_the_notice_wait(c):
    # Before #546 this notice carried refs.wake_batch "review": the hold would have kept a pipeline turn 5 minutes.
    eng = _pipeline_task_in_build(c)
    _comment(c, 1)
    assert G.flush_wakes(c) == [eng]
    news = _news(c, eng)
    assert news and all("wake_batch" not in refs for refs in news)


def test_a_burst_an_earlier_build_marked_goes_out_unmarked(c):
    eng = _pipeline_task_in_build(c)
    _comment(c, 1)
    key = "github-task-wake:" + eng
    c.execute(
        "UPDATE registry_metadata SET value=json_set(value,'$.batch','review') WHERE key=?", (key,)
    )  # saved by an earlier build, flushed after the upgrade
    assert G.flush_wakes(c) == [eng]
    assert all("wake_batch" not in refs for refs in _news(c, eng))


def _burst(c, task_id):
    from backend.repositories import metadata

    return metadata(c, "github-task-wake:" + task_id)


def test_an_awaited_apps_approval_wakes_the_owner_and_its_review_comment_is_a_note(c):
    eng = _pipeline_task_in_build(c)
    app = {"login": "acme-ci[bot]"}
    c.execute(  # the owner armed a wait for the review's result (github.fire_once)
        "UPDATE task_links SET detail=json_set(coalesce(detail,'{}'),'$.wake_once',1) WHERE task_id=?", (eng,)
    )
    G.pr_signal(
        c,
        "pull_request_review",
        {
            "action": "submitted",
            "repository": {"full_name": T.REPO},
            "pull_request": {"html_url": URL},
            "review": {"id": 7, "state": "approved", "user": app, "html_url": URL + "#r7"},
        },
    )
    burst = _burst(c, eng)
    assert any("approved" in item and "acme-ci[bot]" in item for item in burst.get("items", []))
    assert not burst.get("notes")
    G.flush_wakes(c)
    G.pr_signal(
        c,
        "pull_request_review_comment",
        {
            "action": "created",
            "repository": {"full_name": T.REPO},
            "pull_request": {"html_url": URL},
            "comment": {"id": 8, "user": app},
        },
    )
    burst = _burst(c, eng)
    assert burst.get("notes") and not burst.get("items")
