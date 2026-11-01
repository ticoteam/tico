"""A question to a person is delivered once their Needs you holds it and read once they open it there, on its task or
as the item an assistant reads them (POST /api/v2/needs-you/seen, /api/v2/tasks/{id}/seen, backend/batch.py)."""
from backend import hubdb as H, task_holds
from backend.tests.test_api import api, get, post, setup_attempt  # noqa: F401


def asked(api, title="Pick the venue", question="Hall A or Hall B?"):
    with api.app.state.store.transaction() as c:
        tid = H.task_create(c, "human:ben", title, "x", "bot:ops")["id"]
        ask = H.say(c, "bot:ops", "human:ana", question, kind="ask",
                    conversation_id=H.task(c, tid)["conversation_id"], refs={"task": tid})
    return tid, ask["id"]


def marks(api, mid):
    with api.app.state.store.read() as c:
        row = H.message(c, mid)
        return row["delivered_at"], row["read_at"]


def withdrawn_label(c, ask_id):
    return (H._json(H.message(c, ask_id)["refs_json"], {}) or {}).get("withdrawn")


def test_a_question_on_needs_you_is_delivered_and_opening_its_task_reads_it(api):
    """Criterion 1."""
    tid, mid = asked(api)
    item = next(it for it in get(api, "needs-you")["items"] if it["id"] == tid)
    assert item["ask"]["id"] == mid and not item["ask"]["delivered_at"]
    assert marks(api, mid) == (None, None)
    assert post(api, "needs-you/seen", {"asks": [mid]})["delivered"] == [mid]
    delivered, read = marks(api, mid)
    assert delivered and read is None
    assert post(api, "needs-you/seen", {"asks": [mid]})["delivered"] == []      # once
    assert post(api, f"tasks/{tid}/seen", {})["read"] == [mid]
    assert marks(api, mid)[0] == delivered and marks(api, mid)[1]
    assert post(api, f"tasks/{tid}/seen", {})["read"] == []


def test_only_the_person_asked_marks_their_open_questions(api):
    tid, mid = asked(api)
    # Someone else: not their question, nothing changes.
    assert post(api, "needs-you/seen", {"asks": [mid]}, token="ben-test")["delivered"] == []
    assert post(api, f"tasks/{tid}/seen", {}, token="ben-test")["read"] == []
    assert marks(api, mid) == (None, None)
    # A bot's own token is not a person seeing their list.
    _, _, ops = setup_attempt(api, "ops")
    post(api, "needs-you/seen", {"asks": [mid]}, token=ops["token"], expected=403)
    # An answered question stays as it was.
    with api.app.state.store.transaction() as c:
        H.answer(c, "human:ana", mid, "Hall A")
    assert post(api, "needs-you/seen", {"asks": [mid]})["delivered"] == []
    assert marks(api, mid) == (None, None)


def test_the_item_an_assistant_reads_the_person_is_read(api):
    tid, mid = asked(api)
    started = post(api, "batch", {"bot": "ops"})
    assert started["item"]["key"] == "task:" + tid
    delivered, read = marks(api, mid)
    assert delivered and read


def test_a_question_read_on_its_task_is_withdrawn_after_the_person_saw_it(api):
    """Criterion 3 end to end: the read mark the task page sets is the one the withdrawal label reads."""
    tid, mid = asked(api)
    unread_tid, unread = asked(api, "Pick the caterer", "Tacos or pizza?")
    assert post(api, f"tasks/{tid}/seen", {})["read"] == [mid]
    with api.app.state.store.transaction() as c:
        ana = task_holds.name(c, "human:ana")
        H.task_close(c, "human:ben", tid, "Board cleanup")
        H.task_close(c, "human:ben", unread_tid, "Board cleanup")
        assert withdrawn_label(c, mid) == f"Withdrawn after {ana} saw it: its task was closed"
        assert withdrawn_label(c, unread) == f"Withdrawn before {ana} saw it: its task was closed"
