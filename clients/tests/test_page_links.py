"""Pages stand alone (docs/pages.md): publish with `--page` or `--task --as`, link and unlink, the
list filters, `task attach --doc` (a document is a page), and the ambiguous refusal. HTTP is faked the way the other
CLI tests fake it: the request each command makes is the assertion."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from clients import bot_files as BF, hubcli, hubtools, remotecli
from clients.commands import content
from clients.tico import APIError


def fake_client(monkeypatch):
    """A Tico client that records each call as (method, path, body, query); a page publish records (method, path, raw)
    with the query in the path, as the publish sends it."""
    sent = []

    class Client:
        def __init__(self, *args, **kwargs):
            self.__dict__["_tico_is_botops"] = False  # the caller uses its own rights: no `me` lookup

        def features(self):
            return {}

        def get(self, path, **query):
            sent.append(("GET", path, None, {k: v for k, v in query.items() if v is not None}))
            return {"pages": [], "results": []}

        def post(self, path, body=None, key=None):
            sent.append(("POST", path, body, None))
            return {"ok": True}

        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            sent.append((method, path, body, query))
            return {"ok": True}

        def request(self, method, path, body=None, key=None, raw=None):
            sent.append((method, path, raw, None))
            return {"file_id": "file-1", "version": 1}

    monkeypatch.setattr(remotecli, "Client", Client)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    return sent


def checkout(monkeypatch, tmp_path):
    (tmp_path / "spec.html").write_text("<h1>Spec</h1>")
    monkeypatch.setattr(BF, "checkout_root", lambda cwd=None: tmp_path)


def test_publish_with_page_writes_a_version_of_that_page(monkeypatch, tmp_path):
    sent = fake_client(monkeypatch)
    checkout(monkeypatch, tmp_path)
    remotecli.run(hubcli.parser().parse_args(["page", "publish", "spec.html", "--page", "file-1"]))
    method, target, _raw, _ = sent[-1]
    url = urlsplit(target)
    assert (method, url.path) == ("POST", "/api/v2/pages")
    query = parse_qs(url.query)
    assert query["page"] == ["file-1"] and "task" not in query and "as" not in query


def test_publish_as_a_task_role_names_the_task_and_the_role(monkeypatch, tmp_path):
    sent = fake_client(monkeypatch)
    checkout(monkeypatch, tmp_path)
    remotecli.run(hubcli.parser().parse_args(["page", "publish", "spec.html", "--task", "T1", "--as", "Spec"]))
    query = parse_qs(urlsplit(sent[-1][1]).query)
    assert query["task"] == ["T1"] and query["as"] == ["Spec"] and "page" not in query


def test_the_mcp_publish_sends_page_and_as_in_the_body():
    sent = []

    class Api:
        def __init__(self):
            self._tico_is_botops = False

        def post(self, path, body, key=None):
            sent.append((path, body))
            return {"ok": True}

    args = {"name": "spec.html", "text": "<h1>Spec</h1>", "task": "T1", "as": "Spec", "page": "file-1"}
    content.pages_publish(Api(), args)
    assert sent == [
        ("pages", {"name": "spec.html", "text": "<h1>Spec</h1>", "task": "T1", "as": "Spec", "page": "file-1"})
    ]


def test_an_ambiguous_task_role_names_the_pages_and_says_to_pass_page(monkeypatch, tmp_path):
    def ambiguous():
        return APIError("ambiguous", "two pages", 409, extra={"pages": ["file-a", "file-b"]})

    class Api:
        def __init__(self, *args, **kwargs):
            self._tico_is_botops = False

        def features(self):
            return {}

        def post(self, path, body, key=None):
            raise ambiguous()

        def request(self, method, path, body=None, key=None, raw=None):
            raise ambiguous()

    with pytest.raises(APIError) as caught:
        content.pages_publish(Api(), {"name": "spec.html", "text": "<h1>S</h1>", "task": "T1", "as": "Spec"})
    assert caught.value.code == "ambiguous" and caught.value.status == 409
    assert "file-a, file-b" in caught.value.detail and "--page" in caught.value.detail

    checkout(monkeypatch, tmp_path)
    monkeypatch.setattr(remotecli, "Client", Api)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    with pytest.raises(APIError, match="Pass --page"):
        remotecli.run(hubcli.parser().parse_args(["page", "publish", "spec.html", "--task", "T1", "--as", "Spec"]))


@pytest.mark.parametrize(
    ("flags", "target"),
    [
        (["--task", "T1"], "task:T1"),
        (["--bot", "ana"], "bot:ana"),
        (["--person", "steven"], "human:steven"),
        (["--goal", "G1"], "goal:G1"),
        (["--page", "file-2"], "page:file-2"),
    ],
)
def test_page_link_posts_the_target_and_the_role(monkeypatch, flags, target):
    sent = fake_client(monkeypatch)
    remotecli.run(hubcli.parser().parse_args(["page", "link", "file-1", *flags, "--as", "Spec"]))
    assert sent[-1] == ("POST", "pages/file-1/links", {"target": target, "role": "Spec"}, None)


def test_page_link_defaults_the_role_to_related(monkeypatch):
    sent = fake_client(monkeypatch)
    remotecli.run(hubcli.parser().parse_args(["page", "link", "file-1", "--bot", "ana"]))
    assert sent[-1] == ("POST", "pages/file-1/links", {"target": "bot:ana", "role": "related"}, None)


def test_page_link_takes_exactly_one_target(monkeypatch):
    sent = fake_client(monkeypatch)
    with pytest.raises(SystemExit):
        hubcli.parser().parse_args(["page", "link", "file-1", "--task", "T1", "--bot", "ana"])
    with pytest.raises(SystemExit):
        hubcli.parser().parse_args(["page", "link", "file-1"])
    assert sent == []


def test_page_unlink_deletes_the_link_by_target_and_role(monkeypatch):
    sent = fake_client(monkeypatch)
    remotecli.run(hubcli.parser().parse_args(["page", "unlink", "file-1", "--task", "T1", "--as", "Spec"]))
    assert sent[-1] == ("DELETE", "pages/file-1/links", None, {"target": "task:T1", "role": "Spec"})
    remotecli.run(hubcli.parser().parse_args(["page", "unlink", "file-1", "--task", "T1"]))
    assert sent[-1] == ("DELETE", "pages/file-1/links", None, {"target": "task:T1", "role": "related"})


def test_page_list_sends_the_link_filters(monkeypatch):
    sent = fake_client(monkeypatch)
    argv = ["page", "list", "--linked", "task:T1", "--role", "Spec", "--writer", "human:steven"]
    remotecli.run(hubcli.parser().parse_args(argv))
    assert sent[-1] == ("GET", "pages", None, {"linked": "task:T1", "role": "Spec", "writer": "human:steven"})


def test_task_attach_doc_is_refused_with_a_note_and_otherwise_a_page(monkeypatch, tmp_path):
    from clients.commands._shared import DOCUMENT_NOTE

    sent = fake_client(monkeypatch)
    checkout(monkeypatch, tmp_path)
    with pytest.raises(APIError, match="takes no --note") as refused:
        remotecli.run(hubcli.parser().parse_args(["task", "attach", "T1", "spec.html", "--doc", "Spec", "--note", "x"]))
    assert refused.value.detail == DOCUMENT_NOTE
    assert sent == []
    remotecli.run(hubcli.parser().parse_args(["task", "attach", "T1", "spec.html", "--doc", "Spec"]))
    method, target, _raw, _ = sent[-1]
    query = parse_qs(urlsplit(target).query)
    assert (method, urlsplit(target).path) == ("POST", "/api/v2/pages")
    assert query["task"] == ["T1"] and query["as"] == ["Spec"]


def test_page_show_keeps_its_json_output_with_links_and_writers(monkeypatch, capsys):
    result = {
        "page": {
            "title": "Plan",
            "links": [{"target": "task:T1", "role": "Spec", "name": "D-18463 Edit Property"}],
            "writers": [{"actor": "human:steven", "name": "Steven", "versions": 2}],
        }
    }
    monkeypatch.setattr(remotecli, "run", lambda *_: result)
    for argv in (["page", "show", "file-1"], ["page", "show", "file-1", "--json"]):
        assert remotecli.main(hubcli.parse(hubcli.parser(), argv)) == 0
        assert json.loads(capsys.readouterr().out) == result


def test_the_mcp_tools_take_the_link_arguments():
    publish = hubtools.BY_NAME["tico_page_publish"]["inputSchema"]["properties"]
    listing = hubtools.BY_NAME["tico_page_list"]["inputSchema"]["properties"]
    assert {"page", "as"} <= set(publish)
    assert {"linked", "role", "writer"} <= set(listing)
    assert hubtools.BY_NAME["tico_page_link"]["writes"] and hubtools.BY_NAME["tico_page_unlink"]["writes"]


def test_the_mcp_link_defaults_the_role_to_related():
    sent = []

    class Api:
        def __init__(self):
            self._tico_is_botops = False

        def post(self, path, body, key=None):
            sent.append((path, body))
            return {"ok": True}

    hubtools.BY_NAME["tico_page_link"]["fn"](Api(), {"id": "file-1", "target": "task:T1"})
    assert sent == [("pages/file-1/links", {"target": "task:T1", "role": "related"})]
