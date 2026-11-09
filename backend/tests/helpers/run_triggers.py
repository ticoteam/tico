"""Helpers that other tests share, moved out of test_run_triggers.py so they import no test file."""

from backend.tests.helpers.api import assign, post, ready, runner


def machine(api, bot="ops"):
    r = runner(api)
    assign(api, r, bot)
    ready(api, r, [bot])
    return r


def start(api, r, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "fixture-thread"}, r["token"])


USAGE = {"input_tokens": 200, "cached_tokens": 1000, "output_tokens": 30, "model": "fake"}


def finish(api, r, attempt, text="", tokens_in=1200, usage=USAGE):
    return post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": text, "tokens_in": tokens_in, "tokens_out": 30, "usage": usage},
        r["token"],
    )
