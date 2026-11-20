"""backend/outbound_http.py: the one client for outside services. The network is an httpx.MockTransport; stamina runs in
testing mode (conftest.py), so a retry is counted, never waited out."""

import time

import httpx
import pytest

from backend import outbound_http as H


def _replying(*answers, seen=None):
    """A transport that answers each request with the next (status, headers) or raises the next exception."""
    queue = list(answers)

    def handle(request):
        if seen is not None:
            seen.append(request)
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        status, headers = answer
        return httpx.Response(status, headers=headers, content=b'{"ok": true}')

    return httpx.MockTransport(handle)


def test_a_redirect_comes_back_as_the_answer_and_the_token_stays_home():
    seen = []
    reply = H.send(
        "GET",
        "https://api.example/x",
        headers={"Authorization": "Bearer t"},
        transport=_replying((302, {"Location": "https://elsewhere.example/"}), seen=seen),
    )
    assert reply.status == 302
    assert [str(r.url) for r in seen] == ["https://api.example/x"]
    with pytest.raises(H.Failed) as refused:
        reply.check()
    assert refused.value.status == 302


def test_a_429_is_tried_again_for_a_read_and_a_write_until_the_tries_run_out():
    for method in ("GET", "POST"):
        seen = []
        transport = _replying((429, {"Retry-After": "3"}), (429, {}), (200, {}), seen=seen)
        assert H.send(method, "https://api.example/x", retries=3, transport=transport).json() == {"ok": True}
        assert len(seen) == 3
    seen = []
    last = H.send("GET", "https://api.example/x", retries=1, transport=_replying((429, {}), (429, {}), seen=seen))
    assert (last.status, len(seen)) == (429, 2)


def test_a_write_that_may_have_landed_is_never_sent_twice():
    seen = []
    transport = _replying((503, {}), (200, {}), seen=seen)
    assert H.send("POST", "https://api.example/x", retries=3, retry_on={429, 503}, transport=transport).status == 503
    assert len(seen) == 1
    seen = []
    dropped = _replying(httpx.ReadTimeout("slow"), (200, {}), seen=seen)
    with pytest.raises(H.Failed):
        H.send("POST", "https://api.example/x", retries=3, retry_errors=True, transport=dropped)
    assert len(seen) == 1


def test_a_read_with_no_answer_is_tried_again_only_when_asked():
    seen = []
    with pytest.raises(H.Failed) as failed:
        H.send("GET", "https://api.example/x", retries=2, transport=_replying(httpx.ConnectError("down"), seen=seen))
    assert failed.value.reply is None and len(seen) == 1
    seen = []
    transport = _replying(httpx.ConnectError("down"), (500, {}), (200, {}), seen=seen)
    reply = H.send("GET", "https://api.example/x", retries=2, retry_on={500}, retry_errors=True, transport=transport)
    assert (reply.status, len(seen)) == (200, 3)


def test_a_wait_past_the_deadline_is_not_taken_and_a_spent_deadline_sends_nothing():
    seen = []
    transport = _replying((429, {"Retry-After": "25"}), (200, {}), seen=seen)
    reply = H.send("GET", "https://api.example/x", retries=3, deadline=time.monotonic() + 10, transport=transport)
    assert (reply.status, len(seen)) == (429, 1)
    seen = []
    with pytest.raises(H.Failed):
        H.send("GET", "https://api.example/x", deadline=time.monotonic() - 1, transport=_replying(seen=seen))
    assert seen == []


def test_the_wait_is_retry_after_capped_else_doubling():
    def reply(retry_after):
        return H.Reply(429, httpx.Headers({"Retry-After": retry_after} if retry_after else {}), b"")

    assert H.wait_for(reply("3"), 0) == 3.0
    assert H.wait_for(reply("600"), 0) == H.MAX_WAIT
    assert H.wait_for(reply("-5"), 0) == 0.0
    assert H.wait_for(reply("Wed, 21 Oct 2015 07:28:00 GMT"), 0) == 0.0  # a date already past
    assert [H.wait_for(reply(None), n) for n in range(3)] == [1.0, 2.0, 4.0]
    assert H.wait_for(reply("soon"), 2) == 4.0
    assert H.wait_for(None, 10, max_wait=5) == 5


def test_the_test_transport_seam_and_the_request_shape(monkeypatch):
    seen = []
    monkeypatch.setattr(H, "TRANSPORT", _replying((200, {}), seen=seen))
    H.send("post", "https://api.example/x", params={"a": "b c"}, data={"k": "v"}, timeout=2)
    (request,) = seen
    assert request.method == "POST" and request.url.params["a"] == "b c" and request.content == b"k=v"
    assert request.extensions["timeout"]["read"] == 2
