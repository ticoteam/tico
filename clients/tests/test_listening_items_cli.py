"""`tico listening items` and the tico_listening_items tool (clients/commands/listening.py): one page of what a
listening gathered, or with `--all` every page to the end, the total first."""

import json

import pytest

from clients import hubtools
from clients.commands import listening
from clients.tests.helpers import cli_fixtures
from clients.tests.helpers.cli_fixtures import run_line


class Pages:
    """A fake API with 1,200 dropped items, 500 to a page, as GET listening/items answers them."""

    def __init__(self):
        self.asked = []

    def get(self, path, **query):
        self.asked.append((path, query))
        start = int(query.get("cursor") or 0)
        end = min(start + 500, 1200)
        return {
            "items": [{"id": f"i{n}"} for n in range(start, end)],
            "total": 1200,
            "counts": {"dropped": 1200, "kept": 30},
            "next_cursor": str(end) if end < 1200 else None,
        }


def test_all_follows_the_cursor_to_the_end_and_answers_the_total_first():
    api = Pages()
    out = listening.listen_items(api, {"automation": "a1", "status": "dropped", "since": "2026-11-01", "all": True})
    assert list(out) == ["total", "counts", "items", "next_cursor"]
    assert (out["total"], len(out["items"]), out["next_cursor"]) == (1200, 1200, None)
    assert len({i["id"] for i in out["items"]}) == 1200
    assert [q.get("cursor") for _, q in api.asked] == [None, "500", "1000"]
    assert all(q["since"] == "2026-11-01" and q["status"] == "dropped" for _, q in api.asked)


def test_all_stops_when_a_page_answers_the_cursor_it_was_sent():
    class Stuck(Pages):
        def get(self, path, **query):
            if len(self.asked) >= 5:  # a loop that never stops fails here, not as a hang
                raise AssertionError("still asking after 5 pages")
            page = super().get(path, **query)
            return {**page, "next_cursor": query.get("cursor") or "500"}

    api = Stuck()
    with pytest.raises(ValueError, match="did not move"):
        listening.listen_items(api, {"automation": "a1", "all": True})
    assert len(api.asked) == 2


def test_without_all_it_answers_one_page_and_its_cursor():
    api = Pages()
    out = listening.listen_items(api, {"automation": "a1", "cursor": "500"})
    assert (len(out["items"]), out["next_cursor"]) == (500, "1000")
    assert api.asked == [("listening/items", {"automation": "a1", "cursor": "500"})]


def test_the_command_line_sends_its_filters_and_prints_the_total_first(monkeypatch):
    page = {"items": [{"id": "i1"}], "total": 1, "counts": {"dropped": 1}, "next_cursor": None}
    monkeypatch.setitem(cli_fixtures.ANSWERS, "GET listening/items", page)
    line = ["listening", "items", "a1", "--status", "dropped", "--source", "s9", "--since", "2026-11-01", "--all"]
    ran = run_line(line, monkeypatch)
    assert ran["exit"] == 0, ran
    assert ran["http"] == [
        ["GET", "listening/items?automation=a1&status=dropped&source=s9&since=2026-11-01&limit=500", None]
    ]
    assert list(json.loads(ran["stdout"])) == ["total", "counts", "items", "next_cursor"]


def test_the_tool_is_offered_to_agents():
    tool = hubtools.BY_NAME["tico_listening_items"]
    assert tool["inputSchema"]["required"] == ["automation"]
    assert {"status", "source", "since", "until", "cursor", "all"} <= set(tool["inputSchema"]["properties"])
