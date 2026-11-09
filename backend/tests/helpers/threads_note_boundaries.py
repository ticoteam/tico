"""Helpers that other tests share, moved out of test_threads_note_boundaries.py so they import no test file."""

import json
from backend import threads
from backend.tests.helpers.api import assign, headers, post, ready, runner


def bot_runner(api, bot="cpo"):
    r = runner(api)
    assign(api, r, bot)
    ready(api, r, [bot])
    return r


def claim(api, r, bot):
    return post(api, "jobs/claim", {"next_run": True, "bot": bot}, token=r["token"])["attempt"]


def new_thread(api, audience="members", members=("bot:cpo", "bot:ops"), title="Board prep", token="ana-test"):
    payload = {"title": title, "audience": audience}
    if audience == "members":
        payload["members"] = list(members)
    answer = api.post("/api/v2/threads", json=payload, headers=headers(token))
    assert answer.status_code == 200, answer.text
    return answer.json()["id"]


def say(api, thread_id, text, token="ana-test", parent=None):
    payload = {"text": text, **({"parent_message_id": parent} if parent else {})}
    answer = api.post(f"/api/v2/threads/{thread_id}/messages", json=payload, headers=headers(token))
    assert answer.status_code == 200, answer.text
    return answer.json()


def bot_says(api, thread_id, text, bot="bot:ops", **kwargs):
    """A bot's mention of another bot, held as a note (bot_mentions_wait_as_notes), so it starts no run."""
    with api.app.state.store.transaction() as c:
        return threads.post(c, bot, thread_id, text, **kwargs)


def finish(api, r, attempt, text="Done"):
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": f"t-{aid}"}, token=r["token"])
    return post(
        api, f"attempts/{aid}/complete", {"outcome": "completed", "text": text, "last_seq": 0}, token=r["token"]
    )


def drain(api, r, bot="cpo", most=12):
    """Every run the runner would get, in order: each one's root and the words of the notes it carried."""
    runs = []
    while True:
        attempt = claim(api, r, bot)
        if not attempt:
            return runs
        runs.append(
            {
                "root": attempt["conversation"].get("root_id"),
                "class": attempt["conversation"].get("audience_class"),
                "notes": [n["text"] for n in attempt.get("notes") or []],
                "prompt": json.dumps(attempt),
            }
        )
        finish(api, r, attempt)
        assert len(runs) < most, "a wake loop"
