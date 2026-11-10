"""Which fixed department a teammate is in: a subgroup inherits its department; a group that only shares a name grants
nothing."""

import json

from backend import task_rules as R


def _groups(c, groups, cara_team):
    roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
    roster["org_groups"] = groups
    next(p for p in roster["people"] if p["id"] == "cara")["team"] = cara_team
    c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(roster),))


def test_a_subgroup_inherits_its_department(company):
    ids = {d["id"] for d in R.departments(company.settings)}
    assert {"engineering", "marketing"} <= ids
    with company.transaction() as c:
        _groups(
            c,
            {"engineering": {"name": "Engineering"}, "web": {"name": "Web", "parent": "engineering"}},
            "web",
        )
        assert R.department_of(c, company.settings, "human:cara") == "engineering"
        assert R.department_of(c, company.settings, "human:ben") is None
        assert R.department_of(c, company.settings, "keeper") is None


def test_a_group_named_like_a_department_grants_nothing(company):
    with company.transaction() as c:
        _groups(c, {"eng-team": {"name": "Engineering"}}, "eng-team")
        assert R.department_of(c, company.settings, "human:cara") is None
