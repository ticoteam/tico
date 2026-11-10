"""A person's OK of a Finance action in tests, as the OK route gives it: their own click on its preview
(backend/task_asks.py). A test then hands the outcome to Finance itself (`FA.approval_decided`), the way the scheduler's
sweep does, so `give` keeps the OK's own step (task_asks.ON_OK) from running it first."""

from unittest import mock

from backend import hubdb as H
from backend import task_asks
from backend.tests.helpers.api import headers, post


def give(c, person, requirement_id):
    """`person` opens the OK's preview in their own session and clicks OK. Refused (H.Refused) as the route would be."""
    found = task_asks.grant(c, str(requirement_id))
    assert found, requirement_id
    shown = task_asks.show(c, person, str(requirement_id), found["content"], own_click=True)
    with mock.patch.dict(task_asks.ON_OK):
        task_asks.ON_OK.pop("finance", None)
        task_asks.give_ok(c, person, str(requirement_id), shown, own_click=True)


def send_back(c, person, requirement_id):
    """`person` sends the OK back."""
    task_id = task_asks.task_of(c, str(requirement_id))
    H.task_answer(c, person, task_id, str(requirement_id), "no", "Not this one")


def withdraw(c, requirement_id, why="Withdrawn."):
    """The OK is taken back: nobody needs it any more."""
    task_asks.withdraw(c, str(requirement_id), why)


def the_ok(api, requirement_id):
    """The OK's grant: who asked, its subject (`finance:<action id>`), its content and who gave it."""
    with api.app.state.store.read() as c:
        return task_asks.grant(c, str(requirement_id))


def click(api, requirement_id, token="ana-test", **kw):
    """The person opens the OK's preview (GET /api/v2/oks/{id}) and clicks OK on it, through the routes."""
    shown = api.get(f"/api/v2/oks/{requirement_id}", headers=headers(token))
    assert shown.status_code == 200, shown.text
    with api.app.state.store.read() as c:
        task = task_asks.task_of(c, str(requirement_id))
    return post(
        api, f"tasks/{task}/asks/{requirement_id}/ok", {"shown_hash": shown.json()["shown_hash"]}, token=token, **kw
    )
