"""A company file names whatever template slugs its release had, and every later release still reads it.

- `draft.RENAMED` maps each slug a release renamed or folded away to the template that has the role now; `check` and
  `from_yaml` read the old slug as the new one (bots, removed roles, YAML employees).
- fixtures/shipped_template_slugs.json records every slug any release shipped. Each must still resolve, and every
  current template must be in the record, so renaming a template fails here until its old slug is in RENAMED and the
  record is rewritten (`python -m backend.tests.test_template_renames --write`, which refuses while a slug is lost).
- fixtures/hq_starting_charts.json holds the starting charts Tico HQ hands new companies (HQ plans with an older
  release's chart). Each must load in this release: the check that would have caught v0.6.0-rc.6 refusing HQ's plan.
- A refused file's reason, as its owner reads it, names the role by its title.
"""

import datetime as dt
import json
import sys
from pathlib import Path

import pytest
import yaml

from backend import company_file as C
from backend.company_file import draft as D
from backend.company_file import starter
from backend.tests.helpers.template_renames import CHARTS, employees_file

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "templates" / "catalog"
SHIPPED_FILE = Path(__file__).parent / "fixtures" / "shipped_template_slugs.json"
SHIPPED = json.loads(SHIPPED_FILE.read_text())["slugs"]
CURRENT = D.TEMPLATES | D.HELPER_TEMPLATES | D.OPEN_SEATS


def bot(template, at, **more):
    return {
        "id": "role-%d" % at,
        "template": template,
        "title": "Role %s" % template,
        "dept": "operations",
        "lead": False,
        "core": False,
        "reason": "",
        "start": True,
        "routine": "",
        "tools": [],
        "about": "",
        "runtime": None,
        "existing": False,
        **more,
    }


def company(bots, **more):
    draft = C.blank()
    draft["company"].update(name="Acme", about="We sell things.")
    draft["people"] = [{"id": "you", "name": "Pat", "role": "Owner", "dept": None, "owner": True}]
    draft["bots"] = bots
    return {**draft, **more}


# ---------------------------------------------------------------------------------------------- old slugs read as new
def test_a_file_naming_every_old_slug_loads_with_the_new_ones():
    old = sorted(D.RENAMED)
    rows = [bot(template, at) for at, template in enumerate(old)] + [bot("bookkeeping", len(old))]
    removed = [{"template": "pr-reviewer", "title": "Reviewer"}]
    saved = starter.load(company(rows, removed=removed))
    assert [b["template"] for b in saved["bots"]] == [D.RENAMED[t] for t in old]  # the retired one leaves quietly
    assert D.check(company(rows, removed=removed))["removed"] == [{"template": "reviewer", "title": "Reviewer"}]
    assert rows[0]["template"] == old[0]  # the rows sent are not changed

    # The same through a YAML file's employees, and through the packed plan a server starts from.
    text = employees_file(
        [
            {"id": "r%d" % at, "template": t, "title": "Role %d" % at, "department": "operations"}
            for at, t in enumerate(old)
        ]
    )
    assert [b["template"] for b in starter.load(text)["bots"]] == [D.RENAMED[t] for t in old]
    assert [b["template"] for b in starter.unpack(starter.pack(text, starter.MAX_PACKED))["bots"]] == [
        D.RENAMED[t] for t in old
    ]


def test_renamed_maps_old_slugs_only_to_templates_this_release_has():
    assert not set(D.RENAMED) & CURRENT, "a current template can't also be an old name"
    assert {old: new for old, new in D.RENAMED.items() if new not in CURRENT} == {}
    assert D.current("pr-reviewer") == "reviewer" and D.current("reviewer") == "reviewer" and D.current(None) is None


# ---------------------------------------------------------------------------------------------- every shipped slug
def lost(shipped=SHIPPED):
    """Slugs a release shipped that this release can no longer read."""
    return sorted(s for s in shipped if s not in D.RETIRED and D.current(s) not in CURRENT)


def test_every_slug_a_release_shipped_still_reads():
    assert lost() == [], (
        "These template slugs shipped in a release and are gone from the catalog: add each to RENAMED in "
        "backend/company_file/draft.py with the template that has the role now"
    )


def test_every_template_a_file_can_name_has_its_card():
    """A live seat is made from its card folder, so each template the reader takes is one."""
    folders = {path.name for path in CATALOG.iterdir() if (path / "card.yaml").is_file()}
    assert sorted((D.TEMPLATES | D.HELPER_TEMPLATES) - folders) == []


# ---------------------------------------------------------------------------------------------- HQ's starting charts
def tree(saved):
    """A chart's roles as the company gets them: each with its template, department, team, whether it starts live, and
    the template of the role it reports to (None for the person)."""
    templates = {b["id"]: b["template"] for b in saved["bots"]}
    return [
        (b["template"], b["dept"], b.get("team"), bool(b.get("live")), b["lead"], templates.get(D.manager_of(saved, b)))
        for b in saved["bots"]
    ]


@pytest.mark.parametrize("name", sorted(CHARTS))
def test_every_starting_chart_hq_hands_out_loads_in_this_release(name):
    rows = CHARTS[name]
    packed = starter.pack(employees_file(rows), starter.MAX_PACKED)  # what HQ stores for the server
    saved = starter.unpack(packed)  # what the server reads at its first start
    assert {b["template"] for b in saved["bots"]} <= CURRENT
    # Each team's Team Manager seat and its Pipeline Managers are one Manager; every other role keeps its id.
    folded = {r["id"] for r in rows if r["template"] == "pipeline-manager" or r["template"].endswith("-team-manager")}
    kept = [r["id"] for r in rows if r["template"] not in D.RETIRED and r["id"] not in folded]
    managers = [b for b in saved["bots"] if b["template"] in {row[0] for row in D.TEAM_MANAGERS.values()}]
    assert [b["id"] for b in saved["bots"] if b not in managers] == kept
    assert (
        sorted(b["id"] for b in managers)
        == sorted(b["template"] for b in managers)
        == sorted(row[0] for row in D.TEAM_MANAGERS.values())
    )
    assert {b["id"]: b["title"] for b in managers} == {row[0]: row[2] for row in D.TEAM_MANAGERS.values()}


@pytest.mark.parametrize("name", sorted(name for name in CHARTS if name.startswith("0.6 ")))
def test_hqs_chart_in_the_older_shape_gives_the_tree_a_chart_in_todays_shape_gives(name):
    """HQ plans a new company with the chart an older release drew: a Team Manager seat to recruit, a live Pipeline
    Manager per pipeline, and every other seat on the Team Manager's line. Read here, it is the tree this release's
    own chart gives the same company: one live Manager per team with the team under it."""
    golden = json.loads((Path(__file__).parent / "fixtures" / "chart_formula_golden.json").read_text())
    case = next(case for case in golden["cases"] if case["name"] == name.split()[1])
    theirs = starter.load(employees_file(CHARTS[name]))
    ours = starter.load(employees_file(case["expected"]["employees"]))
    # Nov 14: the Product Owner is the Feature Owner (RENAMED), and today's chart has no Builder: the Feature Owner builds.
    # An older chart's Builder seat is still read as the Builder it asked for.
    assert [row for row in tree(theirs) if row[0] != "builder"] == tree(ours)
    assert "feature-owner" in {b["template"] for b in theirs["bots"]}
    live = {b["template"] for b in theirs["bots"] if b.get("live")}
    assert {"product-manager", "engineering-manager", "growth-manager", "success-manager"} <= live
    assert "corporate-manager" not in live


def test_a_pipeline_manager_with_no_team_manager_becomes_its_teams_manager_and_goals_follow():
    rows = [
        {"id": "lead-pipeline-manager", "template": "pipeline-manager", "title": "Lead Pipeline Manager",
         "department": "operations", "team": "Growth", "live": True},
        {"id": "opportunity-pipeline-manager", "template": "pipeline-manager", "title": "Opportunity Pipeline Manager",
         "department": "operations", "team": "Growth", "live": True},
        {"id": "seo-owner", "template": "seo-owner", "title": "SEO Owner", "department": "marketing",
         "reports_to": "opportunity-pipeline-manager"},
        {"id": "ops", "template": "pipeline-manager", "title": "Ops Pipeline Manager", "department": "operations"},
    ]  # fmt: skip
    doc = yaml.safe_load(employees_file(rows))
    doc["goals"] = [
        {"id": "g1", "title": "More deals", "owner": "opportunity-pipeline-manager", "measure": "Deals", "unit": "",
         "direction": "up"}
    ]  # fmt: skip
    saved = starter.load(yaml.safe_dump(doc, sort_keys=False))
    assert [(b["id"], b["template"], b["title"], b["dept"], b.get("live")) for b in saved["bots"]] == [
        ("growth-manager", "growth-manager", "Growth Manager", "marketing", True),
        ("seo-owner", "seo-owner", "SEO Owner", "marketing", None),
        ("ops", "general-manager", "Ops Pipeline Manager", "operations", None),  # no team said: the General Manager
    ]
    assert D.manager_of(saved, saved["bots"][1]) == "growth-manager" and saved["goals"][0]["owner"] == "growth-manager"
    # A draft saved from the setup page folds the same way.
    again = starter.load(saved | {"bots": saved["bots"]})
    assert [b["id"] for b in again["bots"]] == ["growth-manager", "seo-owner", "ops"]


# ---------------------------------------------------------------------------------------------- the owner's reason
def test_an_unknown_template_is_refused_naming_the_role_by_its_title():
    file = company([bot("assistant", 0), bot("no-such-role", 1, title="Reviewer 1")])
    with pytest.raises(D.DraftError) as refused:
        starter.load(file)
    assert str(refused.value) == "bots[1].template is not one of Tico's role templates"
    assert refused.value.plain == "Reviewer 1 names a role template this version of Tico doesn't have: no-such-role"

    text = employees_file(
        [{"id": "r1", "template": "no-such-role", "title": "Reviewer 1", "department": "engineering"}]
    )
    with pytest.raises(D.DraftError) as refused:
        starter.load(text)
    assert refused.value.plain == "Reviewer 1 names a role template this version of Tico doesn't have: no-such-role"


def test_other_refusals_name_the_role_person_or_goal_in_plain_words():
    with pytest.raises(D.DraftError) as refused:
        starter.load(company([bot("assistant", 0, dept="nowhere", title="Office Helper")]))
    assert refused.value.plain == "Office Helper: dept is not a department"
    with pytest.raises(D.DraftError) as refused:
        starter.load(company([bot("assistant", 0, title="")]))
    assert refused.value.plain == "Role 1: title is empty"
    file = company([])
    file["people"][0]["name"] = "x" * 1000
    with pytest.raises(D.DraftError) as refused:
        starter.load(file)
    assert refused.value.plain.startswith("Person 1: name is longer than")
    assert D.DraftError("the draft is too large").plain == "the draft is too large"


def write():
    """Add the current templates to the record (each with today's date), refusing while a recorded slug is lost."""
    gone = lost()
    if gone:
        sys.exit("Add these to RENAMED in backend/company_file/draft.py first: " + ", ".join(gone))
    record = json.loads(SHIPPED_FILE.read_text())
    today = dt.date.today().isoformat()
    record["slugs"] = dict(sorted({**{s: today for s in CURRENT}, **record["slugs"]}.items()))
    SHIPPED_FILE.write_text(json.dumps(record, indent=1) + "\n")


if __name__ == "__main__" and sys.argv[1:] == ["--write"]:
    write()
