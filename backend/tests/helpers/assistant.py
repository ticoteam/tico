"""Helpers that other tests share, moved out of test_assistant.py so they import no test file."""

from backend.tests.helpers.api import assign, get, headers, post, ready, runner


def room(api, token="ana-test"):
    return get(api, "assistant", token)


def say(api, text, token="ana-test", expected=200):
    return post(api, "assistant/messages", {"text": text}, token=token, expected=expected)


def assistant_turn(api, person, text="Please plan my week around the launch", busy_bots=None):
    """The assistant bot's runner turn for a message this person sent in their Assistant room."""
    r = runner(api)
    assign(api, r, "assistant")
    ready(api, r, ["assistant"])
    say(api, text, person)
    body = {} if busy_bots is None else {"busy_bots": busy_bots}
    return r, post(api, "jobs/claim", body, token=r["token"])["attempt"]


def asked(api, path, body, token, ref, expected=200):
    """A write of the assistant's turn that cites the person's message asking for it."""
    r = api.post("/api/v2/" + path, json=body, headers={**headers(token), "X-Tico-On-Behalf-Of": ref})
    assert r.status_code == expected, r.text
    return r.json()


def turn_message(api, attempt):
    with api.app.state.store.read() as c:
        return c.execute(
            "SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (attempt["id"],)
        ).fetchone()[0]
