"""Shared set-up for the Finance tests: one initialized hub database built once per worker and copied per test, the
fictional Acme org chart, and helpers to post and act. No tests here."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from backend import hubdb as H
from backend.finance import actions as FA
from backend.store import Store
from backend.tests.test_api import api_settings

AT = "2026-10-15T12:00:00.000000Z"
OWNER = "human:ana"
# Engineering (led by ben) has Web under it (led by cara); Sales is led by dan. Ana owns the install.
PEOPLE = [{"id": "ana", "email": "ana@acme.example"},
          {"id": "ben", "email": "ben@acme.example", "team": "eng"},
          {"id": "cara", "email": "cara@acme.example", "team": "web", "reports_to": "ben"},
          {"id": "dan", "email": "dan@acme.example", "team": "sales"},
          {"id": "eve", "email": "eve@acme.example", "team": "web", "reports_to": "cara"},
          {"id": "fay", "email": "fay@acme.example", "team": "web", "reports_to": "cara"},
          {"id": "gus", "email": "gus@acme.example", "team": "sales", "reports_to": "dan"}]
GROUPS = {"eng": {"name": "Engineering", "lead": "person:ben"},
          "web": {"name": "Web", "parent": "eng", "lead": "person:cara"},
          "sales": {"name": "Sales", "lead": "person:dan"}}
BOTS = {"coder": ("web", "human:cara"), "seller": ("sales", "human:dan")}


def _seed(c: sqlite3.Connection) -> None:
    H.sync_registry(c, {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in BOTS},
                    {"people": PEOPLE})
    c.execute("INSERT OR REPLACE INTO registry_metadata(key, value_json) VALUES('people', ?)",
              (json.dumps({"people": PEOPLE, "org_groups": GROUPS, "default_user": "ana"}),))
    for slug, (team, boss) in BOTS.items():
        c.execute("INSERT OR REPLACE INTO bot_config(bot, config_json, team, operator, reports_to) VALUES(?,?,?,?,?)",
                  (slug, json.dumps({"team": team}), team, "human:ana", boss))


@pytest.fixture(scope="session")
def finance_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("finance-template")
    store = Store(api_settings(base))
    store.initialize(seed_market=False)
    with store.transaction() as c:
        _seed(c)
    return base / "hub.db"


@pytest.fixture
def store(tmp_path: Path, finance_template: Path) -> Store:
    """A fresh hub with the Acme org and nothing in Finance yet."""
    s = Store(api_settings(tmp_path))
    with closing(sqlite3.connect(finance_template)) as source, closing(sqlite3.connect(s.settings.db_path)) as target:
        source.backup(target)
    return s


@pytest.fixture
def books(store: Store) -> Store:
    """The hub with Finance set up for the owner."""
    with store.transaction() as c:
        FA.setup(c, OWNER, AT)
    return store


def account(c: sqlite3.Connection, code: str) -> str:
    return str(c.execute("SELECT id FROM fin_accounts WHERE code=?", (code,)).fetchone()[0])


def budget(c: sqlite3.Connection, name: str) -> str:
    return str(c.execute("SELECT id FROM fin_budgets WHERE name=?", (name,)).fetchone()[0])


_keys = iter(range(10**9))


def act(c: sqlite3.Connection, who: str, kind: str, payload: dict[str, object], key: str | None = None,
        **kw: object) -> FA.ActionResult:
    return FA.act(c, who, kind, payload, key or f"k{next(_keys)}", at=kw.pop("at", AT), **kw)  # type: ignore[arg-type]


def spend(c: sqlite3.Connection, code: str, amount: int, *, day: str = "2026-10-10", team: str = "",
          teammate: str = "", vendor: str = "", source_id: str = "") -> dict[str, object]:
    """A system-posted expense paid from the bank."""
    tags = {k: v for k, v in (("team", team), ("teammate", teammate)) if v}
    line: dict[str, object] = {"account_id": account(c, code), "amount": amount, "tags": tags}
    if vendor:
        line["contact_id"] = vendor
    return FA.post_entry(c, {"date": day, "source": "usage", "source_id": source_id or f"s{next(_keys)}",
                             "lines": [line, {"account_id": account(c, "1000"), "amount": -amount}]}, at=AT)
