"""The starting charts Tico HQ hands new companies (fixtures/hq_starting_charts.json) and the company file a chart's
rows make: shared by test_template_renames.py and test_company_import.py."""

import json
from pathlib import Path

import yaml

CHARTS = json.loads((Path(__file__).parents[1] / "fixtures" / "hq_starting_charts.json").read_text())["charts"]


def employees_file(rows):
    """A company file (YAML) with these employees rows, as the setup page and HQ write one."""
    doc = {
        "version": "0.3",
        "created": "2026-11-07T00:00:00.000Z",
        "company": {"name": "Acme", "about": "", "signals": []},
        "people": [{"id": "chris", "name": "Chris", "role": "Owner", "owner": True}],
        "employees": rows,
        "goals": [],
        "rules": {"mode": "defaults", "never_without_a_person": [], "review": "one"},
        "tools": {"mode": "defaults", "connect": []},
    }
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
