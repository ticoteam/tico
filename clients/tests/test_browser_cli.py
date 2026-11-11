"""`tico browser` and `tico login` as a bot runs them: each line is one request to the browser routes (path and body),
the answer prints as a line a bot reads (a page, a path, a code), and a refusal as one plain sentence with what to do
next. The routes themselves are in backend/tests/test_browser_commands.py.
"""

import base64
import json
import os
from pathlib import Path

import pytest

from clients import hubcli, hubtools, remotecli
from clients.tico import APIError

SITE = "app.example.com"
PAGE = {"ok": True, "where": "central", "url": "https://app.example.com/home", "title": "Home"}


class Recorder:
    """Stands in for the API client: answers each POST with a canned body (or raises a canned refusal) and keeps the
    request it was given, with the client's own settings."""

    def __init__(self):
        self.answer, self.calls, self.settings = PAGE, [], None

    def post(self, path, body=None, key=None):
        self.calls.append((path, body))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.chdir(tmp_path)
    fake = Recorder()

    def client(*args, **settings):
        fake.settings = settings
        return fake

    monkeypatch.setattr(remotecli, "Client", client)
    return fake


def run(argv, capsys):
    code = remotecli.main(hubcli.parser().parse_args(argv))
    return code, capsys.readouterr().out.rstrip("\n")


def make_file(path):
    path.write_bytes(b"")
    return path


def verb(which, **args):
    return ("browser/commands", {"site": SITE, "verb": which, "args": args})


@pytest.mark.parametrize(
    "argv, answer, call, printed",
    [
        (["browser", "open", SITE], PAGE, verb("open"), "Home — https://app.example.com/home"),
        (
            ["browser", "open", SITE, "https://app.example.com/billing"],
            PAGE,
            verb("open", url="https://app.example.com/billing"),
            "Home — https://app.example.com/home",
        ),
        (
            ["browser", "read", SITE],
            {**PAGE, "text": 'Home — https://app.example.com/home\n[e1] button "Sign in"'},
            verb("read"),
            'Home — https://app.example.com/home\n[e1] button "Sign in"',
        ),
        (["browser", "click", SITE, "e1"], PAGE, verb("click", ref="e1"), "Home — https://app.example.com/home"),
        (["browser", "type", SITE, "e2", "invoices"], PAGE, verb("type", ref="e2", text="invoices"), PAGE["title"] + " — " + PAGE["url"]),
        (["browser", "type", SITE, "e2", "q", "--submit"], PAGE, verb("type", ref="e2", text="q", submit=True), "Home — https://app.example.com/home"),
        (["browser", "press", SITE, "Enter"], PAGE, verb("press", key="Enter"), "Home — https://app.example.com/home"),
        (["browser", "scroll", SITE], PAGE, verb("scroll", dy=600), "Home — https://app.example.com/home"),
        (["browser", "scroll", SITE, "--dy", "-300"], PAGE, verb("scroll", dy=-300), "Home — https://app.example.com/home"),
        (["browser", "back", SITE], PAGE, verb("back"), "Home — https://app.example.com/home"),
        (["browser", "close", SITE], {"ok": True}, verb("close"), "Closed."),
        (
            ["browser", "needs-you", SITE, "Type the code texted to the office phone"],
            {"requirement_id": "rq_1"},
            ("browser/needs-you", {"site": SITE, "line": "Type the code texted to the office phone"}),
            "Asked a person; their Done wakes you. End this turn now.",
        ),
        (["login", "fill", SITE, "username"], {"ok": True}, verb("fill", field="username"), "Filled the username."),
        (
            ["login", "fill", SITE, "password", "--ref", "e4"],
            {"ok": True},
            verb("fill", field="password", ref="e4"),
            "Filled the password.",
        ),
        (["login", "fill", SITE, "code"], {"ok": True}, verb("fill", field="code"), "Filled the 2FA code."),
        (
            ["login", "copy", SITE, "password"],
            {"ok": True},
            verb("copy", field="password"),
            "Copied the password to this computer's clipboard; it clears in 30 s.",
        ),
        (["login", "code", SITE], {"ok": True, "text": "492039", "expires_in": 21}, verb("code"), "492039 (21 s left)"),
    ],
)  # fmt: skip
def test_each_verb_is_one_request_and_prints_a_line_a_bot_reads(api, capsys, argv, answer, call, printed):
    api.answer = answer
    assert run(argv, capsys) == (0, printed)
    assert api.calls == [call]
    # A browser verb waits past the route's 30 s for the computer, and a refused or timed-out one is never sent twice.
    assert api.settings == {"timeout": 40, "retries": 0}


def test_json_prints_the_answer_as_it_came(api, capsys):
    api.answer = {**PAGE, "text": "Home — https://app.example.com/home"}
    code, out = run(["browser", "read", SITE, "--json"], capsys)
    assert code == 0 and json.loads(out) == api.answer


def test_a_screenshot_is_a_jpeg_file_and_only_its_path_is_printed(api, capsys, tmp_path):
    jpeg = b"\xff\xd8\xff\xe0 a picture"
    api.answer = {**PAGE, "image": base64.b64encode(jpeg).decode()}
    code, out = run(["browser", "screenshot", "https://app.example.com/a?b=c"], capsys)
    assert code == 0 and "\n" not in out and os.path.isabs(out)
    assert os.path.dirname(out) == str((tmp_path / "screenshots").resolve())
    assert os.path.basename(out).startswith("https-app.example.com-a-b-c-") and out.endswith(".jpg")
    assert Path(out).read_bytes() == jpeg

    code, out = run(["browser", "screenshot", SITE, "--out", "shots/login.jpg"], capsys)
    assert (code, out) == (0, str((tmp_path / "shots" / "login.jpg").resolve()))
    assert api.calls[-1] == verb("screenshot")


def test_a_download_lands_in_the_folder_under_its_own_name_and_never_overwrites(api, capsys, tmp_path):
    api.answer = {**PAGE, "file": {"name": "../../invoice.pdf", "data": base64.b64encode(b"%PDF-1").decode()}}
    first = run(["browser", "download", SITE], capsys)
    second = run(["browser", "download", SITE, "--out", "downloads"], capsys)
    folder = (tmp_path / "downloads").resolve()
    assert first == (0, str(folder / "invoice.pdf")) and second == (0, str(folder / "invoice-1.pdf"))
    assert (folder / "invoice-1.pdf").read_bytes() == b"%PDF-1"


def test_an_upload_sends_the_file_from_the_checkout(api, capsys, tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "march.csv").write_text("a,b\n1,2\n")
    assert run(["browser", "upload", SITE, "e7", "reports/march.csv"], capsys) == (0, "Uploaded march.csv.")
    data = base64.b64encode(b"a,b\n1,2\n").decode()
    assert api.calls == [verb("upload", ref="e7", name="march.csv", data=data)]


@pytest.mark.parametrize(
    "make, path, says",
    [
        (
            lambda root: os.truncate(make_file(root / "big.bin"), 10 * 1024 * 1024 + 1),
            "big.bin",
            "big.bin is over 10 MB",
        ),
        (lambda root: (root / ".env").write_text("KEY=1"), ".env", "upload a file from your checkout"),
        (lambda root: None, "/etc/hosts", "outside the bot's checkout"),
        (lambda root: None, "missing.pdf", "not a regular file"),
    ],
)
def test_an_upload_that_is_too_big_or_not_the_bots_to_send_is_refused_before_any_request(
    api, capsys, tmp_path, make, path, says
):
    make(tmp_path)
    code, out = run(["browser", "upload", SITE, "e7", path], capsys)
    assert code == 2 and says in out and "\n" not in out
    assert api.calls == []


@pytest.mark.parametrize(
    "code", ["busy", "person_in_control", "browser_host_offline", "stale_ref", "not_on_site", "something_new"]
)
def test_a_refusal_keeps_the_servers_detail_verbatim(api, capsys, code):
    detail = "The server supplies this refusal; follow its next action."
    api.answer = APIError(code, detail, 409, extra={"by": {"name": "Builder"}})
    assert run(["browser", "click", SITE, "e1"], capsys) == (2, detail)


def test_a_timeout_keeps_the_servers_detail_and_exits_as_retryable(api, capsys):
    detail = "The browser did not answer; read the page and try again."
    api.answer = APIError("browser_timeout", detail, 504, retryable=True)
    assert run(["login", "fill", SITE, "password"], capsys) == (1, detail)


def test_json_keeps_the_refusal_as_json(api, capsys):
    api.answer = APIError("busy", "held", 409)
    code, out = run(["browser", "read", SITE, "--json"], capsys)
    assert code == 2 and json.loads(out)["error"] == "busy"


def test_only_a_bots_own_runs_are_offered_the_browser_and_login_tools():
    names = [t["name"] for t in hubtools.TOOLS if t["name"].startswith(("tico_browser_", "tico_login_"))]
    assert len(names) == 15
    for name in names:
        assert set(hubtools.offered_to(hubtools.BY_NAME[name])) == {"bot", "botops", "agent"}, name
    # The tools that touch this computer's files are served by the local MCP server only, never by the Tico server.
    assert {n for n in names if hubtools.BY_NAME[n]["local"]} == {
        "tico_browser_screenshot",
        "tico_browser_download",
        "tico_browser_upload",
    }
