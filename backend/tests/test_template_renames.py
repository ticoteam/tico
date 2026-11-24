"""A company file names whatever template slugs its 0.6 release had, and every later release still reads it.

- `draft.RENAMED` maps each slug a 0.6 release renamed to the template that has the role now; `check` and `from_yaml`
  read the old slug as the new one (bots, removed roles, YAML employees).
- fixtures/shipped_template_slugs.json records every slug a release from v0.6.0 on shipped. Each must still resolve,
  and every current template must be in the record, so renaming a template fails here until its old slug is in RENAMED
  and the record is rewritten (`python -m backend.tests.test_template_renames --write`, which refuses while a slug is
  lost).
- A slug from before 0.6 is not read: the file is refused, and its reason names the role by its title and the slug.
"""

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

from backend import company_file as C
from backend.company_file import draft as D
from backend.company_file import starter
from backend.tests.helpers.template_renames import employees_file

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
    rows = [bot(template, at) for at, template in enumerate(old)]
    removed = [{"template": old[0], "title": "Fleet Fixer"}]
    saved = starter.load(company(rows, removed=removed))
    assert [b["template"] for b in saved["bots"]] == [D.RENAMED[t] for t in old]
    assert D.check(company(rows, removed=removed))["removed"] == [
        {"template": D.RENAMED[old[0]], "title": "Fleet Fixer"}
    ]
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
    assert D.current("botops") == "agent-ops" and D.current("agent-ops") == "agent-ops" and D.current(None) is None


# ---------------------------------------------------------------------------------------------- every shipped slug
def lost(shipped=SHIPPED):
    """Slugs a release shipped that this release can no longer read."""
    return sorted(s for s in shipped if D.current(s) not in CURRENT)


def test_every_slug_a_release_shipped_still_reads():
    assert lost() == [], (
        "These template slugs shipped in a release and are gone from the catalog: add each to RENAMED in "
        "backend/company_file/draft.py with the template that has the role now"
    )


def test_every_template_a_file_can_name_has_its_card():
    """A live seat is made from its card folder, so each template the reader takes is one."""
    folders = {path.name for path in CATALOG.iterdir() if (path / "card.yaml").is_file()}
    assert sorted((D.TEMPLATES | D.HELPER_TEMPLATES) - folders) == []


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


# Slugs only a release before 0.6 had: a Pipeline Manager and Team Manager seat, a role renamed in the rc releases, and
# a template retired before 0.6.
@pytest.mark.parametrize(
    "slug", ["pipeline-manager", "growth-team-manager", "pr-reviewer", "bookkeeper", "bookkeeping"]
)
def test_a_slug_from_before_0_6_is_refused_naming_the_slug(slug):
    with pytest.raises(D.DraftError) as refused:
        starter.load(company([bot("assistant", 0), bot(slug, 1, title="Old Seat")]))
    assert refused.value.plain == "Old Seat names a role template this version of Tico doesn't have: " + slug
    text = employees_file([{"id": "r1", "template": slug, "title": "Old Seat", "department": "operations"}])
    with pytest.raises(D.DraftError) as refused:
        starter.load(text)
    assert refused.value.plain == "Old Seat names a role template this version of Tico doesn't have: " + slug


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
