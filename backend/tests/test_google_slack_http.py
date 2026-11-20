"""The default HTTP calls of the Google and Slack adapters (the functions other tests replace), on a fake network: each
keeps its answer shapes on backend/outbound_http.py, and none follows a redirect with its token."""

import json

import httpx
import pytest

from backend import gcal_live, gmail_live, google_connect, google_meet, google_workspace, outbound_http, slack_gateway


@pytest.fixture
def net(monkeypatch):
    """Answers each request with the next queued httpx.Response (or raises the queued exception); keeps the requests."""
    state = {"answers": [], "seen": []}

    def handle(request):
        state["seen"].append(request)
        answer = state["answers"].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(outbound_http, "TRANSPORT", httpx.MockTransport(handle))
    return state


DOWN = httpx.ConnectError("down")


class _CutShort(httpx.SyncByteStream):
    def __iter__(self):
        yield b'{"ok": '
        raise httpx.ReadError("connection dropped")


MOVED = httpx.Response(302, headers={"Location": "https://elsewhere.example/"})


def test_gmail_calls_answer_status_and_json_and_zero_without_an_answer(net):
    net["answers"] += [httpx.Response(200, json={"id": "m1"}), httpx.Response(403, json={"error": "private"}), DOWN]
    assert gmail_live.gmail_call("https://gmail.example/m1", token="t") == (200, {"id": "m1"})
    assert gmail_live.gmail_call("https://gmail.example/m1", token="t", data={"a": 1}, method="PUT") == (403, {})
    assert gmail_live.gmail_call("https://gmail.example/m1", token="t") == (0, {})
    put = net["seen"][1]
    assert put.method == "PUT" and json.loads(put.content) == {"a": 1} and put.headers["Authorization"] == "Bearer t"
    net["answers"] += [httpx.Response(204), MOVED, DOWN]
    assert [gmail_live.gmail_delete_call("https://gmail.example/l1", token="t") for _ in range(3)] == [204, 302, 0]
    net["answers"] += [httpx.Response(200, json={"id": "s1"}), httpx.Response(200, content=b"not json"), MOVED, DOWN]
    sent = [gmail_live.send_call("https://gmail.example/send", token="t", body=b"{}", content_type="x") for _ in "1234"]
    assert sent == [(200, {"id": "s1"}), (200, {}), (302, {}), (0, {})]
    assert len(net["seen"]) == 10  # a send is never sent twice


def test_a_gmail_answer_too_large_is_refused(net, monkeypatch):
    net["answers"].append(httpx.Response(200, content=b"x" * 50))
    with pytest.raises(gmail_live.Problem, match="too large"):
        gmail_live.gmail_call("https://gmail.example/m1", token="t", max_bytes=10)


def test_calendar_answers_only_google_s_reason_word_for_a_refusal(net):
    refusal = {"error": {"errors": [{"reason": "rateLimitExceeded", "message": "PRIVATE"}]}}
    net["answers"] += [httpx.Response(403, json=refusal), httpx.Response(200, json={"items": []}), DOWN]
    assert gcal_live.gcal_call("https://cal.example/e", token="t", method="POST", data={}) == (
        403,
        {"reason": "rateLimitExceeded"},
    )
    assert gcal_live.gcal_call("https://cal.example/e", token="t") == (200, {"items": []})
    assert gcal_live.gcal_call("https://cal.example/e", token="t") == (0, {})


def test_workspace_and_meet_calls_keep_a_refusal_s_body_and_status(net):
    net["answers"] += [httpx.Response(400, json={"error": "invalid_grant"}), DOWN]
    assert google_workspace.google_call("https://oauth.example/token", data={"a": "b"}) == (
        400,
        {"error": "invalid_grant"},
    )
    assert net["seen"][0].method == "POST" and net["seen"][0].content == b"a=b"
    assert google_workspace.google_call("https://oauth.example/x", token="t") == (0, {})
    net["answers"] += [httpx.Response(200, json={"name": "spaces/1"}), DOWN]
    assert google_meet.meet_call("PATCH", "https://meet.example/s", token="t") == (200, {"name": "spaces/1"})
    assert json.loads(net["seen"][2].content) == {}
    assert google_meet.meet_call("GET", "https://meet.example/s", token="t") == (0, {})


def test_google_s_token_endpoint_refusal_outage_and_no_answer(net):
    net["answers"] += [httpx.Response(200, json={"access_token": "ya29.x"})]
    assert google_connect.google_token(("id", "secret"), {"grant_type": "refresh_token"})["access_token"] == "ya29.x"
    assert b"client_secret=secret" in net["seen"][0].content
    for answer, kind in (
        (httpx.Response(400, json={"error": "invalid_grant"}), "signin"),
        (httpx.Response(503), "network"),
        (DOWN, "network"),
        (httpx.Response(200, content=b"<html>"), "network"),
    ):
        net["answers"].append(answer)
        with pytest.raises(google_connect.Problem) as refused:
            google_connect.google_token(("id", "secret"), {})
        assert refused.value.extra["kind"] == kind and "secret" not in refused.value.detail
    net["answers"] += [httpx.Response(200, json={}), httpx.Response(400), DOWN]
    assert [google_connect.revoke_at_google("1//g") for _ in range(3)] == [True, False, False]


def test_slack_calls_raise_slack_s_own_errors_and_downloads_follow_no_redirect(net):
    api = slack_gateway.SlackAPI("xoxb-t", "")
    net["answers"] += [
        httpx.Response(200, json={"ok": True}, headers={"x-oauth-scopes": "chat:write, files:read"}),
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(503),
        DOWN,
    ]
    assert api.auth_test() == {"ok": True} and api.scopes == ("chat:write", "files:read")
    with pytest.raises(slack_gateway.SlackError) as limited:
        api.users_info("U1")
    assert limited.value.retry_after == 7
    net["answers"] += [
        httpx.Response(429, headers={"Retry-After": "7"}, stream=_CutShort()),
        httpx.Response(302, json={"ok": True, "ts": "1.0"}),
    ]
    for _ in range(4):  # a 5xx, no answer, a cut-off 429 and a redirect: a post's fate is unknown, never rescheduled
        with pytest.raises(slack_gateway.SlackUnreachable):
            api.users_info("U1")
    net["answers"] += [httpx.Response(200, content=b"pdf", headers={"Content-Type": "application/pdf"}), MOVED]
    assert api.download("https://files.slack.example/f", 10) == (200, "application/pdf", b"pdf")
    assert api.download("https://files.slack.example/f", 10) == (302, "", b"")
    net["answers"] += [httpx.Response(200, content=b"x" * 11), DOWN]
    with pytest.raises(slack_gateway.SlackError, match="file_too_large"):
        api.download("https://files.slack.example/f", 10)
    with pytest.raises(slack_gateway.SlackUnreachable):
        api.download("https://files.slack.example/f", 10)
