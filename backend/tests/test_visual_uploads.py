"""Visual replies 1/4 (Dev 245, QA Fixes): a bot's `--visual` / MCP `visuals` file goes up as bytes, not base64 JSON
(a third bigger), so a 21 MB picture and a 2.5 MB page attach on both paths: a chat reply (raw body, 27 MB request
limit) and a task comment (multipart; JSON there stops at 10 MB)."""
import pytest

from backend.tests.test_api import api, headers, post  # noqa: F401  (fixture)
from backend.tests.test_files import turn
from clients import bot_files as BF, hubtools
from clients.tico import APIError, MultipartBody

BIG_PICTURE = b"\x89PNG\r\n\x1a\n" + b"\x00" * 21_000_000                    # under the 25 MB picture limit
BIG_PAGE = "<!doctype html><p>" + "Signups by week. " * 150_000 + "</p>"     # about 2.5 MB of text


class Hub:
    """clients.tico.Client's surface, sent to the test app, so the server's real request limits apply."""
    def __init__(self, api, token):
        self.api, self.token = api, token

    def request(self, method, path, body=None, key=None, raw=None, **_):
        sent = headers(self.token, key)
        if isinstance(raw, MultipartBody):
            sent["Content-Type"], raw = raw.content_type, b"".join(raw)
        elif raw is not None:
            sent["Content-Type"] = "application/octet-stream"
        made = self.api.request(method, path, headers=sent, **({"content": raw} if raw is not None else {"json": body}))
        if made.status_code >= 400:
            raise APIError("http", made.text, status=made.status_code)
        return made.json()

    def post(self, path, body=None, key=None):
        return self.request("POST", "/api/v2/" + path, body or {}, key)

    def post_multipart(self, path, files, fields=None, key=None):
        return self.request("POST", "/api/v2/" + path, key=key, raw=MultipartBody(files, fields or {}))


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    (tmp_path / "chart.png").write_bytes(BIG_PICTURE)
    (tmp_path / "page.html").write_text(BIG_PAGE)
    monkeypatch.setattr(BF, "checkout_root", lambda: tmp_path)
    return tmp_path


def attached(api, message):
    """(title, kind, size) per attachment, and each file reads back whole for a reader of that conversation."""
    found = message["refs"]["attachments"]
    for item in found:
        made = api.get(item["url"], headers=headers("ben-test"))
        assert made.status_code == 200 and len(made.content) == item["size"], made.text[:300]
    return [(a["title"], a["kind"], a["size"]) for a in found]


EXPECTED = [("Signups", "image", len(BIG_PICTURE)), ("Funnel", "page", len(BIG_PAGE.encode()))]


def test_a_bots_chat_reply_attaches_a_big_picture_and_a_big_page(api, checkout):
    _, attempt = turn(api)
    sent = hubtools._message_send(Hub(api, attempt["token"]), {
        "to": "human:ben", "text": "Two visuals", "conversation_id": attempt["message"]["conversation_id"],
        "visuals": [{"path": "chart.png", "title": "Signups"}, {"path": "page.html", "title": "Funnel"}]})
    assert attached(api, sent) == EXPECTED


def test_a_bots_task_comment_attaches_a_big_picture_and_a_big_page(api, checkout):
    _, attempt = turn(api)
    task = post(api, "tasks", {"owner_id": "ops", "title": "Signups chart", "body": "x"}, "ben-test")
    made = hubtools.task_comment(Hub(api, attempt["token"]), {
        "id": task["id"], "text": "Two visuals",
        "visuals": [{"path": "chart.png", "title": "Signups"}, {"path": "page.html", "title": "Funnel"}]})
    assert attached(api, made["comment"]) == EXPECTED
