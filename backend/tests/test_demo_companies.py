"""Demo companies (backend/demo_companies.py) load from their CSV files through the import path: every row of the
locations, items and contacts files lands (H1, the data side; the P&L numbers wait for the reports branch). Equipment
and due dates are named as not loaded until this build has those record types."""
import csv
import sqlite3

import pytest

from backend import demo
from backend.demo_companies import COMPANIES, DATA
from backend.finance.core import kinds as K


def rows(company, name):
    with open(DATA / company / (name + ".csv"), encoding="utf-8", newline="") as f:
        return sum(1 for _ in csv.DictReader(f))


def live(c, table):
    return int(c.execute(f"SELECT count(*) FROM {table} WHERE archived_at IS NULL").fetchone()[0])


@pytest.mark.parametrize("company", sorted(COMPANIES))
def test_h1_a_demo_company_loads_every_row_of_its_locations_items_and_contacts(tmp_path, company, capsys):
    settings = demo.build(tmp_path / company, company=company)
    out = capsys.readouterr().out
    with sqlite3.connect(settings.db_path) as c:
        assert live(c, "locations") == rows(company, "locations")
        assert live(c, "items") == rows(company, "items")
        assert c.execute("SELECT count(*) FROM fin_contacts WHERE active=1").fetchone()[0] == rows(company, "contacts")
    for name, kind in (("equipment", "equipment_create"), ("due_dates", "due_date_create")):
        if (DATA / company / (name + ".csv")).exists() and kind not in K.LIVE:
            assert f"{name}.csv not loaded" in out
