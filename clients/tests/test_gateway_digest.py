"""`hub gateway digest` (clients/hubcli.py, clients/remotecli.py): the items are read from a JSON file and sent as the
tool's `items`, with the focus and style, to the server's digest route. The server is faked: nothing is sent anywhere."""

import json

import pytest

from clients import hubcli, remotecli


@pytest.fixture
def sent(monkeypatch, tmp_path):
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def post(self, path, body, key=None):
            calls.append((path, body))
            return {"summary": "S", "groups": [], "dropped": []}

    monkeypatch.setattr(remotecli, "Client", FakeClient)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    return calls


def run(argv):
    return remotecli.run(hubcli.parser().parse_args(argv))


def test_digest_reads_the_items_file_and_asks_the_route_with_the_focus_and_style(sent, tmp_path):
    items = [{"title": "Pro plan price rise", "url": "https://northwind.example/pricing"}]
    path = tmp_path / "items.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    assert run(["gateway", "digest", "--items-file", str(path)]) == {"summary": "S", "groups": [], "dropped": []}
    run(["gateway", "digest", "--items-file", str(path), "--focus", "pricing", "--style", "full"])
    assert sent == [
        ("gateway/digest", {"items": items, "style": "brief"}),
        ("gateway/digest", {"items": items, "focus": "pricing", "style": "full"}),
    ]


def test_digest_needs_an_items_file():
    with pytest.raises(SystemExit):
        hubcli.parser().parse_args(["gateway", "digest"])
