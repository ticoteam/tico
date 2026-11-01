"""Demo companies (backend/demo_companies.py) load from their files through the import path and the Finance actions:
every row of the locations, items, equipment, contacts and due dates files lands, the projects, purchase orders and
estimates in documents.json are made, and the company's business type is set (H1, the data side; the P&L numbers wait
for the reports branch)."""
import csv
import json
import sqlite3

import pytest

from backend import demo
from backend.demo_companies import BUSINESS, COMPANIES, DATA


def rows(company, name):
    with open(DATA / company / (name + ".csv"), encoding="utf-8", newline="") as f:
        return sum(1 for _ in csv.DictReader(f))


def documents(company):
    return json.loads((DATA / company / "documents.json").read_text(encoding="utf-8"))


def live(c, table):
    return int(c.execute(f"SELECT count(*) FROM {table} WHERE archived_at IS NULL").fetchone()[0])


def count(c, table):
    return int(c.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


@pytest.mark.parametrize("company", sorted(COMPANIES))
def test_h1_a_demo_company_loads_every_row_of_its_files_and_its_documents(tmp_path, company, capsys):
    settings = demo.build(tmp_path / company, company=company)
    out = capsys.readouterr().out
    doc = documents(company)
    with sqlite3.connect(settings.db_path) as c:
        assert live(c, "locations") == rows(company, "locations")
        assert live(c, "items") == rows(company, "items")
        assert live(c, "equipment") == rows(company, "equipment")
        assert c.execute("SELECT count(*) FROM fin_contacts WHERE active=1").fetchone()[0] == rows(company, "contacts")
        assert count(c, "due_dates") == rows(company, "due_dates")
        assert count(c, "projects") == len(doc.get("projects", []))
        assert count(c, "fin_purchase_orders") == len(doc.get("purchase_orders", []))
        assert count(c, "fin_estimates") == len(doc.get("estimates", []))
        sidebar = c.execute("SELECT value_json FROM registry_metadata WHERE key='company-sidebar'").fetchone()[0]
        assert json.loads(sidebar)["business_type"] == BUSINESS[company]
    assert "not loaded" not in out
