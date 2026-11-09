"""Helpers that other tests share, moved out of test_visuals.py so they import no test file."""

import time
from backend.tests.helpers.api import headers, post


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


VISUALS = "http://visuals.localhost:9001"


def bot_reply(api, attempt, to="human:ben", **body):
    conversation = attempt["conversation_id"] if "conversation_id" in attempt else attempt["message"]["conversation_id"]
    return api.post(
        "/api/v2/messages",
        json={"to": to, "text": "Here is the chart", "conversation_id": conversation, **body},
        headers=headers(attempt["token"]),
    )


def task_with_file(api, *, private, name="page.html", text="<p>hi</p>"):
    task_with_file.n = getattr(task_with_file, "n", 0) + 1
    task = post(
        api,
        "tasks",
        {"owner": "ops", "title": f"Chart {task_with_file.n}", "body": "x", "private": private},
        "ben-test",
    )
    tid = task["id"]
    made = api.post(f"/api/v2/tasks/{tid}/files", json={"name": name, "text": text}, headers=headers("ben-test"))
    assert made.status_code == 200, made.text
    return tid, made.json()["file_id"], made.json()["version"]


# ---------------------------------------------------------------------------------------------- access (18, 19)
def visual_on_task(api, private):
    tid, fid, number = task_with_file(api, private=private, name="chart.html", text="<p>chart</p>")
    r = api.post(
        f"/api/v2/tasks/{tid}/comments",
        json={"text": "chart", "attachments": [f"{fid}@{number}"]},
        headers=headers("ben-test"),
    )
    assert r.status_code == 200, r.text
    return tid, fid, r.json()["comment"]


def wav():
    import io
    import wave

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000), w.writeframes(b"\x00\x01" * 800)
    return out.getvalue()


def media_settled(api):
    """Wait until the media worker (backend/file_metadata.py) has nothing pending, deferred retries included: its
    save rewrites a version's poster and preview, so one landing after a test sets them would clear them."""
    from backend.file_metadata import PENDING

    for _ in range(200):
        with api.app.state.store.read() as c:
            if not c.execute(PENDING, (float("inf"),)).fetchone():
                return
        time.sleep(0.05)
    raise AssertionError("the media worker never settled")
