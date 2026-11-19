"""The Gateway's transport (clients/gateway.py `_exchange`, and the calls built on it): what each answer becomes for the
caller. urllib is the only thing stubbed, at `OpenerDirector.open`, the one call every Gateway request goes through: a
2xx answer is a `Reply`, an error answer is an HTTPError, a Gateway that does not answer is a URLError. Each test checks
the request the call built too (URL, method, key, body). Nothing is sent anywhere."""

import http.client
import io
import json
import urllib.error
import urllib.request

import pytest

from clients import gateway as G

BASE = "https://gateway.example.test/v1/gateway"
KEY = "tgw-test-key"
JOB = "gv_" + "a" * 24
EIGHT_MIB = 8 * 1024 * 1024


class Reply:
    """A 2xx answer, as urllib's response gives it to a `with` block."""

    def __init__(self, body=b"", status=200):
        self.status = status
        self.body = io.BytesIO(body)

    def read(self, size=-1):
        return self.body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Unreadable(io.BytesIO):
    """An error body that fails while it is read, as a connection dropped part way does."""

    def read(self, size=-1):
        raise ConnectionResetError("connection reset while reading the body")


def error(status, body=b"", *, unreadable=False):
    """An error answer: urllib raises an HTTPError for it, with `body` as its readable body."""
    return urllib.error.HTTPError(BASE, status, "error", {}, Unreadable(body) if unreadable else io.BytesIO(body))


def coded(code):
    """The Gateway's nested error body, `{"error": {"code": ...}}`."""
    return json.dumps({"error": {"code": code}}).encode()


def header(request, name):
    """A request header by its name. urllib keeps the name capitalised as `Content-type`, `Idempotency-key`."""
    return request.get_header(name.capitalize())


class Network:
    """urllib's side of each request: the next answer is returned (a Reply) or raised (an HTTPError, a URLError). A
    request with no answer left fails the test. `requests` keeps (request, timeout) for every request sent."""

    def __init__(self):
        self.answers = []
        self.requests = []

    def send(self, request, timeout):
        self.requests.append((request, timeout))
        if not self.answers:
            raise AssertionError(f"unexpected request {request.get_method()} {request.full_url}")
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


@pytest.fixture
def network(monkeypatch):
    net = Network()
    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        lambda _opener, request, data=None, timeout=None: net.send(request, timeout),
    )
    return net


# --- letters(): a failed call raises LettersError with the Gateway's code, or a fixed one ----------------------------

LETTER_FAILURES = [
    pytest.param(lambda: error(409, coded("quote_expired")), 409, "quote_expired", id="letter-code"),
    pytest.param(lambda: error(401, b'{"error": "unknown_key"}'), 401, "unknown_key", id="flat-error-string"),
    pytest.param(lambda: error(400, b'{"error": {"type": "invalid"}}'), 400, "invalid", id="error-type"),
    pytest.param(lambda: error(500, coded("kaboom")), 500, "gateway_error", id="code-not-a-letter-code"),
    pytest.param(lambda: error(502, b"<html>bad gateway</html>"), 502, "gateway_error", id="error-not-json"),
    pytest.param(lambda: error(404), 404, "gateway_error", id="error-without-body"),
    pytest.param(lambda: error(503, unreadable=True), 503, "gateway_error", id="error-body-unreadable"),
    pytest.param(lambda: urllib.error.URLError("connection refused"), 502, "gateway_unavailable", id="no-answer"),
    pytest.param(lambda: Reply(b"<html>"), 502, "gateway_unavailable", id="2xx-not-json"),
    pytest.param(lambda: Reply(b" " * (128 * 1024 + 1)), 502, "response_too_large", id="2xx-over-128-kib"),
]


@pytest.mark.parametrize(("answer", "status", "code"), LETTER_FAILURES)
def test_a_letters_call_that_fails_raises_its_status_and_a_fixed_code(network, answer, status, code):
    network.answers.append(answer())
    with pytest.raises(G.LettersError) as failed:
        G.letters(KEY, BASE, "/quotes")
    assert (failed.value.status, failed.value.code) == (status, code)


def test_a_letters_call_gets_its_json_with_the_key_and_a_60_second_timeout(network):
    network.answers.append(Reply(b'{"quotes": []}'))
    assert G.letters(KEY, BASE, "/quotes?limit=1") == {"quotes": []}
    ((request, timeout),) = network.requests
    assert request.get_method() == "GET" and request.full_url == BASE + "/letters/quotes?limit=1"
    assert header(request, "Authorization") == "Bearer " + KEY and header(request, "Accept") == "application/json"
    assert request.data is None and timeout == 60


def test_a_letters_post_sends_the_body_and_the_idempotency_key(network):
    network.answers.append(Reply(b'{"ok": true}'))
    assert G.letters(KEY, BASE, "/quotes", body={"amount": 5}, idempotency_key="idem-1") == {"ok": True}
    ((request, _timeout),) = network.requests
    assert request.get_method() == "POST" and json.loads(request.data) == {"amount": 5}
    assert header(request, "Content-Type") == "application/json" and header(request, "Idempotency-Key") == "idem-1"


def test_a_letters_pdf_is_the_bytes_asked_for_as_a_pdf(network):
    network.answers.append(Reply(b"%PDF-1.7 the letter"))
    assert G.letters(KEY, BASE, "/1/pdf", pdf=True) == b"%PDF-1.7 the letter"
    ((request, _timeout),) = network.requests
    assert header(request, "Accept") == "application/pdf"


def test_a_pdf_over_8_mib_is_too_large(network):
    network.answers.append(Reply(b"%" * (EIGHT_MIB + 1)))
    with pytest.raises(G.LettersError) as failed:
        G.letters(KEY, BASE, "/1/pdf", pdf=True)
    assert (failed.value.status, failed.value.code) == (502, "response_too_large")


# --- check(): the key's state, from the Gateway's answer or from how it failed ---------------------------------------

CHECK_ANSWERS = [
    pytest.param(lambda: Reply(b'{"used": 0.2}'), "connected", True, id="connected"),
    pytest.param(lambda: Reply(b'{"used": 0.85}'), "warning", True, id="warning-by-share"),
    pytest.param(lambda: Reply(b'{"warning": true}'), "warning", True, id="warning-by-flag"),
    pytest.param(lambda: Reply(b'{"cap_reached": true}'), "cap_reached", True, id="cap-by-flag"),
    pytest.param(lambda: Reply(b'{"spent_usd": 50, "cap_usd": 50}'), "cap_reached", True, id="cap-by-amounts"),
    pytest.param(lambda: Reply(b"<html>"), "unreachable", True, id="2xx-not-json"),
    pytest.param(lambda: error(401, coded("revoked_key")), "revoked", False, id="revoked"),
    pytest.param(lambda: error(401, coded("unknown_key")), "unknown", False, id="unknown-key"),
    pytest.param(lambda: error(403), "unknown", False, id="403-without-code"),
    pytest.param(lambda: error(401, unreadable=True), "unknown", False, id="401-body-unreadable"),
    pytest.param(lambda: error(429, coded("cap_reached")), "cap_reached", True, id="429-cap"),
    pytest.param(lambda: error(429, coded("rate_limited")), "busy", True, id="429-rate"),
    pytest.param(lambda: error(429), "busy", True, id="429-without-code"),
    pytest.param(lambda: error(404), "not_ready", True, id="404"),
    pytest.param(lambda: error(302), "not_ready", True, id="redirect-not-followed"),
    pytest.param(lambda: error(500, coded("boom")), "unreachable", True, id="500"),
    pytest.param(lambda: error(500, unreadable=True), "unreachable", True, id="500-body-unreadable"),
    pytest.param(lambda: urllib.error.URLError("connection refused"), "unreachable", True, id="no-answer"),
]


@pytest.mark.parametrize(("answer", "state", "working"), CHECK_ANSWERS)
def test_check_says_what_the_gateway_answered(network, answer, state, working):
    network.answers.append(answer())
    result = G.check(KEY, BASE)
    assert (result["state"], result["working"]) == (state, working)


def test_check_reads_the_key_with_the_key_and_keeps_the_amounts(network):
    network.answers.append(Reply(b'{"used": 0.2, "spent_usd": 10.123, "cap_usd": 50}'))
    assert G.check(KEY, BASE) == {
        "state": "connected",
        "working": True,
        "message": "Connected.",
        "used": 0.2,
        "spent_usd": 10.12,
        "cap_usd": 50.0,
    }
    ((request, _timeout),) = network.requests
    assert request.get_method() == "GET" and request.full_url == BASE + "/key"
    assert header(request, "Authorization") == "Bearer " + KEY


@pytest.mark.parametrize(
    ("key", "url", "state"),
    [("", BASE, "none"), (KEY, "http://gateway.example.test/v1/gateway", "bad_url")],
    ids=["no-key", "plain-http-to-a-remote-host"],
)
def test_check_sends_nothing_without_a_key_or_to_an_address_the_key_may_not_reach(network, key, url, state):
    assert G.check(key, url)["state"] == state
    assert network.requests == []


STATE_OF_ERROR = [
    (401, "revoked_key", "revoked"),
    (404, "unknown_key", "unknown"),
    (403, "", "unknown"),
    (429, "cap_reached", "cap_reached"),
    (429, "rate_limited", "busy"),
    (429, "", "busy"),
    (404, "", "not_ready"),
    (302, "", "not_ready"),
    (500, "", "unreachable"),
    (0, "unreachable", "unreachable"),
]


@pytest.mark.parametrize(("status", "code", "state"), STATE_OF_ERROR)
def test_an_error_answer_to_the_key_check_has_a_state(status, code, state):
    assert G.state_of_error(status, code) == state


# --- enroll() and rotate(): a POST whose answer is its status, payload and code ---------------------------------------

POST_ANSWERS = [
    pytest.param(
        lambda: Reply(b'{"key": "tgw-new", "key_id": "k1"}'),
        (200, {"key": "tgw-new", "key_id": "k1"}, ""),
        id="json",
    ),
    pytest.param(lambda: Reply(b"[1]"), (200, {}, ""), id="json-not-an-object"),
    pytest.param(lambda: Reply(b"<html>"), (0, {}, "unreachable"), id="2xx-not-json"),
    pytest.param(lambda: error(403, coded("install_revoked")), (403, {}, "install_revoked"), id="coded-error"),
    pytest.param(lambda: error(500), (500, {}, "500"), id="error-without-body"),
    pytest.param(lambda: error(409, unreadable=True), (409, {}, "409"), id="error-body-unreadable"),
    pytest.param(lambda: urllib.error.URLError("connection refused"), (0, {}, "unreachable"), id="no-answer"),
]


@pytest.mark.parametrize(("answer", "expected"), POST_ANSWERS)
def test_enroll_gives_the_gateways_status_payload_and_code(network, answer, expected):
    network.answers.append(answer())
    assert G.enroll(BASE, "gi_install", "gis_secret", "tgi_invite") == expected


def test_enroll_posts_the_install_and_its_invite_and_rotate_sends_the_key(network):
    network.answers.extend([Reply(b"{}"), Reply(b"{}")])
    G.enroll(BASE, "gi_install", "gis_secret", "tgi_invite", version="0.6.1")
    G.rotate(BASE, KEY)
    enroll, rotate = [request for request, _timeout in network.requests]
    assert enroll.get_method() == "POST" and enroll.full_url == BASE + "/enroll"
    assert json.loads(enroll.data) == {
        "install": "gi_install",
        "secret": "gis_secret",
        "invite": "tgi_invite",
        "version": "0.6.1",
    }
    assert rotate.get_method() == "POST" and rotate.full_url == BASE + "/rotate"
    assert header(rotate, "Authorization") == "Bearer " + KEY


# --- video_status() and video_content(): a job's fields, and its MP4 or nothing --------------------------------------

VIDEO_STATUS = [
    pytest.param(
        lambda: Reply(
            json.dumps({"id": JOB, "status": "done", "seconds": 5, "provider_id": "p-1", "cost_usd": True}).encode()
        ),
        (200, {"id": JOB, "status": "done", "seconds": 5, "cost_usd": None}, ""),
        id="job-keeps-only-the-public-fields",
    ),
    pytest.param(lambda: Reply(b"[1]"), (200, {}, ""), id="json-not-an-object"),
    pytest.param(lambda: Reply(b""), (200, {}, ""), id="empty-body"),
    pytest.param(lambda: Reply(b"<html>"), (0, {}, "unreadable"), id="2xx-not-json"),
    pytest.param(lambda: error(404, coded("not_found")), (404, {}, "not_found"), id="not-found"),
    pytest.param(lambda: error(500, unreadable=True), (500, {}, "500"), id="error-body-unreadable"),
    pytest.param(lambda: urllib.error.URLError("connection refused"), (0, {}, "unreachable"), id="no-answer"),
]


@pytest.mark.parametrize(("answer", "expected"), VIDEO_STATUS)
def test_video_status_gives_the_job_or_the_failure(network, answer, expected):
    network.answers.append(answer())
    assert G.video_status(BASE, KEY, JOB) == expected


VIDEO_CONTENT = [
    pytest.param(lambda: Reply(b"MP4 bytes"), (200, b"MP4 bytes", ""), id="video"),
    pytest.param(lambda: error(409, coded("failed")), (409, b"", "failed"), id="failed"),
    pytest.param(lambda: error(409, b'{"error": "not_ready"}'), (409, b"", "not_ready"), id="not-ready"),
    pytest.param(lambda: error(410, coded("expired")), (410, b"", "expired"), id="expired"),
    pytest.param(lambda: error(409, unreadable=True), (409, b"", "409"), id="error-body-unreadable"),
    pytest.param(lambda: urllib.error.URLError("connection refused"), (0, b"", "unreachable"), id="no-answer"),
]


@pytest.mark.parametrize(("answer", "expected"), VIDEO_CONTENT)
def test_video_content_is_the_mp4_or_no_bytes_with_the_code(network, answer, expected):
    network.answers.append(answer())
    assert G.video_content(BASE, KEY, JOB) == expected


def test_a_video_over_the_limit_is_too_large_and_one_at_it_is_kept(network):
    network.answers.extend([Reply(b"0123456789"), Reply(b"0123456789")])
    assert G.video_content(BASE, KEY, JOB, limit=9) == (200, b"", "too_large")
    assert G.video_content(BASE, KEY, JOB, limit=10) == (200, b"0123456789", "")


def test_a_video_id_that_is_not_a_job_id_is_not_found_without_a_request(network):
    assert G.video_status(BASE, KEY, "../etc") == (404, {}, "not_found")
    assert G.video_content(BASE, KEY, "../etc") == (404, b"", "not_found")
    assert network.requests == []


# --- signals_*(): the JSON calls, their empty answers and their size limit ---------------------------------------------

SIGNALS = [
    pytest.param(
        lambda: G.signals_watches(BASE, KEY),
        lambda: Reply(b'{"watches": [{"id": "w1"}]}'),
        (200, {"watches": [{"id": "w1"}]}, ""),
        id="watches",
    ),
    pytest.param(lambda: G.signals_watches(BASE, KEY), lambda: Reply(b""), (200, {}, ""), id="empty-body"),
    pytest.param(lambda: G.signals_watches(BASE, KEY), lambda: Reply(b"[1]"), (200, {}, ""), id="json-not-an-object"),
    pytest.param(
        lambda: G.signals_watches(BASE, KEY), lambda: Reply(b"<html>"), (0, {}, "unreachable"), id="2xx-not-json"
    ),
    pytest.param(
        lambda: G.signals_watches(BASE, KEY),
        lambda: Reply(b" " * (4 * 1024 * 1024 + 1)),
        (0, {}, "too_large"),
        id="over-4-mib",
    ),
    pytest.param(
        lambda: G.signals_watches(BASE, KEY), lambda: error(500, coded("boom")), (500, {}, "boom"), id="coded"
    ),
    pytest.param(
        lambda: G.signals_watches(BASE, KEY),
        lambda: error(500, unreadable=True),
        (500, {}, "500"),
        id="error-body-unreadable",
    ),
    pytest.param(lambda: G.signals_end(BASE, KEY, "w1"), lambda: Reply(b"", 204), (204, {}, ""), id="ended"),
    pytest.param(
        lambda: G.signals_end(BASE, KEY, "w1"),
        lambda: error(404, coded("not_found")),
        (404, {}, "not_found"),
        id="not-held",
    ),
    pytest.param(
        lambda: G.signals_watch(BASE, KEY, "page", "https://a.example/p"),
        lambda: Reply(b'{"id": "w2"}'),
        (200, {"id": "w2"}, ""),
        id="watch-created",
    ),
    pytest.param(
        lambda: G.signals_changes(BASE, KEY),
        lambda: urllib.error.URLError("connection refused"),
        (0, {}, "unreachable"),
        id="no-answer",
    ),
]


@pytest.mark.parametrize(("call", "answer", "expected"), SIGNALS)
def test_a_signals_call_gives_its_payload_status_and_code(network, call, answer, expected):
    network.answers.append(answer())
    assert call() == expected


def test_signals_calls_build_their_urls_methods_and_bodies(network):
    network.answers.extend([Reply(b"{}") for _ in range(5)])
    G.signals_changes(BASE, KEY, since="c1", limit=5)
    G.signals_changes(BASE, KEY)
    G.signals_watch(BASE, KEY, "release", "github:ticoteam/tico")
    G.signals_end(BASE, KEY, "a/b")
    G.signals_watches(BASE, KEY)
    changes, default_changes, watch, end, watches = [request for request, _timeout in network.requests]
    assert changes.full_url == BASE + "/signals/changes?since=c1&limit=5"
    assert default_changes.full_url == BASE + "/signals/changes?limit=200"
    assert watch.get_method() == "POST" and watch.full_url == BASE + "/signals/watches"
    assert json.loads(watch.data) == {"kind": "release", "target": "github:ticoteam/tico"}
    assert end.get_method() == "DELETE" and end.full_url == BASE + "/signals/watches/a%2Fb"
    assert watches.get_method() == "GET" and watches.full_url == BASE + "/signals/watches"
    assert all(header(request, "Authorization") == "Bearer " + KEY for request in (changes, watch, end, watches))


# --- relay(): the answer is passed on as it is, its error kept, and nothing goes to an unsafe address -------------

RELAY = [
    pytest.param(lambda: Reply(b"speech"), (200, b"speech"), id="answer"),
    pytest.param(lambda: error(404, b"gone"), (404, b"gone"), id="error-keeps-its-status-and-body"),
    pytest.param(lambda: urllib.error.URLError("connection refused"), (0, b""), id="no-answer"),
]


@pytest.mark.parametrize(("answer", "expected"), RELAY)
def test_relay_passes_the_gateways_answer_on_as_it_is(network, answer, expected):
    network.answers.append(answer())
    assert G.relay("POST", BASE + "/transcribe", b"{}") == expected


def test_relay_keeps_at_most_its_limit_and_sends_nothing_to_an_unsafe_address(network, monkeypatch):
    monkeypatch.setattr(G, "RELAY_LIMIT", 4)
    network.answers.append(Reply(b"abcdefgh"))
    assert G.relay("GET", BASE + "/audio") == (200, b"abcd")
    assert G.relay("GET", "http://gateway.example.test/audio") == (0, b"")
    assert len(network.requests) == 1


# --- relay_speech(): raw PCM and its sample rate, an error kept, nothing to an unsafe address ------------------------


class Speech(Reply):
    """A text-to-speech answer: raw PCM, with the rate in its `X-Audio-Sample-Rate` header when the Gateway names one."""

    def __init__(self, body, rate=None):
        super().__init__(body)
        self.headers = {"X-Audio-Sample-Rate": rate} if rate is not None else {}


SPEECH = [
    pytest.param(lambda: Speech(b"\x01\x00", "16000"), (200, b"\x01\x00", 16000), id="named-rate"),
    pytest.param(lambda: Speech(b"\x01\x00"), (200, b"\x01\x00", G.SPEECH_RATE), id="no-rate-is-24k"),
    pytest.param(lambda: Speech(b"\x01\x00", "4"), (200, b"\x01\x00", G.SPEECH_RATE), id="rate-out-of-range"),
    pytest.param(lambda: error(404, b"unknown"), (404, b"unknown", G.SPEECH_RATE), id="error-keeps-its-status"),
    pytest.param(lambda: error(503, unreadable=True), (503, b"", G.SPEECH_RATE), id="error-body-unreadable"),
    pytest.param(lambda: urllib.error.URLError("connection refused"), (0, b"", G.SPEECH_RATE), id="no-answer"),
]


@pytest.mark.parametrize(("answer", "expected"), SPEECH)
def test_relay_speech_gives_the_pcm_and_its_rate(network, answer, expected):
    network.answers.append(answer())
    assert G.relay_speech(BASE + "/audio/speech", b"{}", {"Authorization": "Bearer " + KEY}) == expected
    [(request, _timeout)] = network.requests
    assert request.get_method() == "POST" and header(request, "Accept") == "audio/pcm"
    assert header(request, "Authorization") == "Bearer " + KEY


def test_relay_speech_sends_nothing_to_an_unsafe_address(network):
    assert G.relay_speech("http://gateway.example.test/audio/speech", b"{}") == (0, b"", G.SPEECH_RATE)
    assert network.requests == []


# --- a body cut short: a connection that closes part way makes read() raise IncompleteRead ---------------------------


class Truncated(Reply):
    """A 2xx answer whose body stops short of its length, as a connection that closes part way leaves it."""

    def read(self, size=-1):
        raise http.client.IncompleteRead(b"partial")


def test_a_body_cut_short_is_unreachable_not_an_exception(network):
    network.answers.append(Truncated())
    assert G.video_content(BASE, KEY, JOB) == (0, b"", "unreachable")
