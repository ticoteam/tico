"""Per-segment market briefings (backend/market_briefing.py and the 20261107005705_segment_briefings migration): the table
rebuild keeps the whole market's rows, a segment's briefing is its own row and page, and the whole market reads as before.
Examples use Acme and Harborly."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from backend import automations
from backend import market_briefing as MB
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_automation_actions import act
from backend.tests.test_market import _librarian, _seed
from backend.tests.test_market_spend import defined

WEEK = "2026-W44"
OLD_TABLE = (
    "CREATE TABLE market_briefings (id TEXT PRIMARY KEY, week TEXT NOT NULL UNIQUE, title TEXT NOT NULL, "
    "body TEXT NOT NULL, items TEXT NOT NULL DEFAULT '[]', bets TEXT NOT NULL DEFAULT '[]', created_by TEXT, "
    "created_at TEXT, updated_at TEXT)"
)


def _migration(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[1] / "migrations" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _shape(c):
    columns = [(r[1], r[2], r[3], r[4], r[5]) for r in c.execute("PRAGMA table_info(market_briefings)")]
    unique = sorted(
        tuple(info[2] for info in c.execute(f"PRAGMA index_info({r[1]})"))
        for r in c.execute("PRAGMA index_list(market_briefings)")
        if r[2]
    )
    return columns, unique


def post_briefing(
    api, segment="", *, title="Week 44", body="Page.", items=(), answers=None, token="ana-test", expected=200
):
    payload = {"week": WEEK, "title": title, "body": body, "items": list(items), "segment_id": segment}
    if answers is not None:
        payload["answers"] = answers
    return post(api, "market/briefings", payload, token=token, expected=expected)


def ours_segment(api):
    first, second, _ = defined(api)
    post(api, "market/segments/target", {"id": first, "target": "ours"})
    return first, second


@pytest.fixture
def seeded(api, tmp_path):
    _seed(api, tmp_path)
    return api


@pytest.fixture
def events(monkeypatch):
    """The `market.briefing` events the automations hear, with their payloads."""
    seen, heard = [], automations.on_event

    def listen(c, type_, subject, data, actor):
        if type_ == "market.briefing":
            seen.append((type_, subject, data() if callable(data) else data))
        return heard(c, type_, subject, data, actor)

    monkeypatch.setattr(automations, "on_event", listen)
    return seen


def test_the_rebuild_keeps_every_whole_market_row_and_its_index_and_takes_the_segment_key():
    up = _migration("20261107005705_segment_briefings").up
    with sqlite3.connect(":memory:") as c:
        c.row_factory = sqlite3.Row
        c.execute(OLD_TABLE)
        c.execute("CREATE INDEX market_briefings_created ON market_briefings(created_at)")
        c.executemany(
            "INSERT INTO market_briefings (id, week, title, body, items, bets, created_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ("b1", "2026-W43", "Week 43", "Old page.", '[{"text": "x"}]', '["bet_1"]', "bot:librarian", "t1", "t2"),
                ("b2", "2026-W44", "Week 44", "Page.", "[]", "[]", "ana", "t3", "t3"),
            ],
        )
        up(c)
        rows = [dict(r) for r in c.execute("SELECT * FROM market_briefings ORDER BY week")]
        assert [(r["id"], r["week"], r["segment_id"]) for r in rows] == [("b1", "2026-W43", ""), ("b2", "2026-W44", "")]
        assert (rows[0]["items"], rows[0]["bets"], rows[0]["created_by"]) == (
            '[{"text": "x"}]',
            '["bet_1"]',
            "bot:librarian",
        )
        assert "market_briefings_created" in [r[1] for r in c.execute("PRAGMA index_list(market_briefings)")]
        c.execute(
            "INSERT INTO market_briefings (id, week, segment_id, title, body) VALUES ('s1','2026-W44','segment/acme','Acme','P')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                "INSERT INTO market_briefings (id, week, segment_id, title, body) VALUES ('b3','2026-W44','','Dup','P')"
            )


def test_a_rebuilt_table_and_a_fresh_one_have_the_same_columns_and_unique_keys():
    up = _migration("20261107005705_segment_briefings").up
    with sqlite3.connect(":memory:") as fresh, sqlite3.connect(":memory:") as rebuilt:
        MB.ensure(fresh)
        rebuilt.execute(OLD_TABLE)
        up(rebuilt)
        assert _shape(rebuilt) == _shape(fresh)
        assert ("week", "segment_id") in _shape(fresh)[1]


def test_a_segment_briefing_is_its_own_row_and_page_and_the_whole_market_reads_as_before(seeded):
    first, _ = ours_segment(seeded)
    whole = post_briefing(seeded, body="Whole page.")["briefing"]
    mine = post_briefing(seeded, first, title="Screening week", body="Screening page.")["briefing"]
    assert (whole["segment_id"], mine["segment_id"]) == ("", first) and whole["id"] != mine["id"]

    assert get(seeded, "market/briefings/latest")["briefing"]["title"] == "Week 44"
    assert get(seeded, f"market/briefings/latest?segment={first}")["briefing"]["title"] == "Screening week"
    assert [b["segment_id"] for b in get(seeded, "market/briefings")["briefings"]] == [""]
    assert [b["segment_id"] for b in get(seeded, f"market/briefings?segment={first}")["briefings"]] == [first]
    assert get(seeded, f"market/briefings/{WEEK}?segment={first}")["briefing"]["body"] == "Screening page."
    with seeded.app.state.store.read() as c:
        pages = c.execute("SELECT id, body FROM market_pages WHERE id LIKE 'market/briefings/%' ORDER BY id").fetchall()
    assert [(p["id"], p["body"]) for p in pages] == [
        ("market/briefings/2026-W44", "Whole page."),
        ("market/briefings/2026-W44-segment-screening", "Screening page."),
    ]

    again = post_briefing(seeded, first, title="Screening, final", body="Final.")["briefing"]
    assert (again["id"], again["created_at"]) == (mine["id"], mine["created_at"])
    assert get(seeded, "market/briefings/latest")["briefing"]["title"] == "Week 44"


def test_a_segment_that_is_not_ours_is_a_404_on_post_and_on_read(seeded, events):
    _, second = ours_segment(seeded)
    post_briefing(seeded, second, expected=404)
    post_briefing(seeded, "segment/nope", expected=404)
    assert get(seeded, f"market/briefings?segment={second}", expected=404)["error"]["code"] == "not_found"
    assert get(seeded, "market/briefings/latest?segment=segment/nope", expected=404)
    assert get(seeded, f"market/briefings/{WEEK}?segment=segment/nope", expected=404)
    assert get(seeded, "market/briefings")["briefings"] == [] and events == []


def test_the_event_names_a_segment_and_the_whole_markets_payload_is_unchanged(seeded, events):
    first, _ = ours_segment(seeded)
    post_briefing(seeded, body="Whole page.")
    post_briefing(seeded, first, body="Screening page.")
    assert [data for _, _, data in events] == [
        {"week": WEEK, "bets": []},
        {"week": WEEK, "bets": [], "segment_id": first},
    ]


def test_an_automation_posts_a_segments_briefing_and_refuses_one_that_is_not_ours(seeded):
    _librarian(seeded)
    first, second = ours_segment(seeded)
    done = act(
        seeded,
        "post_update",
        {"to": ["market"], "text": "- Screening: one rival cut prices.", "segment_id": first},
        owner="bot:librarian",
    )
    assert done["ok"] and done["briefing"] == MB.this_week()  # the automation posts this real week
    assert get(seeded, f"market/briefings/latest?segment={first}")["briefing"]["segment_id"] == first
    refused = act(
        seeded,
        "post_update",
        {"to": ["market"], "text": "- Payments: nothing.", "segment_id": second},
        owner="bot:librarian",
    )
    assert refused["ok"] is False
    assert get(seeded, "market/briefings")["briefings"] == []  # the whole market has no briefing


def test_the_cli_and_the_tools_send_the_segment_and_the_latest_read_takes_it():
    from clients import hubcli, hubtools

    args = hubcli.parser().parse_args(
        [
            "market",
            "briefing",
            "post",
            "--week",
            WEEK,
            "--title",
            "Screening",
            "--body-file",
            "b.md",
            "--segment",
            "segment/screening",
        ]
    )
    assert (args.fn, args.segment_id) == ("market briefing post", "segment/screening")
    assert hubcli.parser().parse_args(["market", "briefing", "latest"]).segment_id is None

    class Api:
        def __init__(self):
            self.calls = []

        def post(self, path, body=None, key=None):
            self.calls.append(("POST", path, body))
            return {}

        def get(self, path, **query):
            self.calls.append(("GET", path, query))
            return {}

    fake = Api()
    hubtools.market_briefing_post(
        fake, {"week": WEEK, "title": "Screening", "body": "Page.", "segment_id": "segment/screening"}
    )
    hubtools.market_briefing_latest(fake, {"segment_id": "segment/screening"})
    hubtools.market_briefing_latest(fake, {})
    assert fake.calls == [
        (
            "POST",
            "market/briefings",
            {"week": WEEK, "title": "Screening", "body": "Page.", "items": [], "segment_id": "segment/screening"},
        ),
        ("GET", "market/briefings/latest", {"segment": "segment/screening"}),
        ("GET", "market/briefings/latest", {}),
    ]
