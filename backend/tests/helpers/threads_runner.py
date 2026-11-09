"""Helpers that other tests share, moved out of test_threads_runner.py so they import no test file."""

from backend import threads
from backend.auth import Identity


ANA = Identity("human:ana", "owner", "ana@acme.example")


def write(api, fn):
    store = api.app.state.store
    with store.transaction() as c:
        return fn(c, threads.viewer(api.app.state.auth, ANA))


def make_thread(api, **kw):
    return write(api, lambda c, v: threads.create(c, v, **kw))["id"]


def say(api, thread_id, text, parent=None):
    return write(api, lambda c, v: threads.post(c, v, thread_id, text, parent_message_id=parent))
