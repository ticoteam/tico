"""The default pipelines beyond Product and Engineering (backend/pipeline_presets_more.py, docs/pipelines-v3.md
"Default pipelines"): a new company gets Growth Experiments, Growth Monitoring, Lead and Opportunity in the Growth factory
and Customer, Success Experiments and Success Monitoring in the Success factory, once; nothing it retired comes back; no
stage is left without an owner; an experiment goes live only once a person OKs its Go-live page. Legal and Finance (private, in the Corporate
factory) are made once their owner bot is: the sample company has both, the Bookkeeper and the Legal Owner."""

import pytest

from backend import page_verdicts as V
from backend import pipeline_presets_more as M
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend import task_pages as P
from backend import task_requirements as R
from backend.answers import answer
from backend.store import H
from backend.tests.helpers.sample_company import (  # noqa: F401
    booted,
    environment,
    rows,
    signed_in,
)

OWNER = "human:owner"
NEW = (
    "growth-experiments",
    "opportunity",
    "lead",
    "customer",
    "success-experiments",
    "growth-monitoring",
    "success-monitoring",
)
FACTORIES = [
    ("product", ["product"]),
    ("growth", ["growth-experiments", "growth-monitoring", "lead", "opportunity"]),
    ("success", ["customer", "success-experiments", "success-monitoring"]),
    ("corporate", ["finance", "legal"]),
]


def _new_company(environment):
    api = environment()
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    return api


def _restart(environment):
    return environment(seed_roster=False)


def _layout(api):
    """[(factory, sorted pipelines)] in factory order, and the live pipelines."""
    factories = rows(api, "SELECT id FROM factories WHERE retired_at IS NULL ORDER BY position")
    out = [
        (
            f["id"],
            sorted(
                r["id"]
                for r in rows(api, "SELECT id FROM pipelines WHERE factory_id=? AND retired_at IS NULL", (f["id"],))
            ),
        )
        for f in factories
    ]
    live = {r["id"] for r in rows(api, "SELECT id FROM pipelines WHERE retired_at IS NULL")}
    return out, live


def _snapshot(api):
    return (
        rows(api, "SELECT id,name,prefix,manager_id,factory_id,settings_version,retired_at FROM pipelines ORDER BY id"),
        rows(api, "SELECT id,pipeline_id,name,owner,position,retired_at FROM stages ORDER BY id"),
        rows(api, "SELECT id,name,position,retired_at FROM factories ORDER BY id"),
        rows(api, "SELECT id,stage_id,kind,person_id FROM requirements WHERE visit_id IS NULL ORDER BY id"),
    )


def test_a_new_company_gets_every_default_pipeline_in_its_factory(environment):
    api = _new_company(environment)
    layout, live = _layout(api)
    assert [(f, sorted(p)) for f, p in FACTORIES] == layout
    assert live == {"general", "product", "finance", "legal", *NEW}
    stages = [r["name"] for r in rows(api, "SELECT name FROM stages WHERE pipeline_id='lead' ORDER BY position")]
    assert stages == ["Targets", "Outreach", "Replies", "Qualified", "Done", "Closed"]
    stages = [
        r["name"] for r in rows(api, "SELECT name FROM stages WHERE pipeline_id='growth-experiments' ORDER BY position")
    ]
    assert stages == ["Ideas", "Building", "Running", "Done", "Closed"]
    stages = [
        r["name"] for r in rows(api, "SELECT name FROM stages WHERE pipeline_id='success-monitoring' ORDER BY position")
    ]
    assert stages == ["Watching", "Slipping", "Done", "Closed"]
    prefixes = {r["id"]: r["prefix"] for r in rows(api, "SELECT id,prefix FROM pipelines")}
    assert [prefixes[p] for p in NEW] == ["GX", "O", "L", "C", "SX", "GM", "SM"]


def test_every_work_stage_has_an_owner_and_every_pipeline_a_manager(environment):
    """The ladder: a role no bot fills goes to the pipeline's manager, a manager nobody fills to the General Manager."""
    api = _new_company(environment)
    general_manager = "bot:" + rows(api, "SELECT id FROM bots WHERE slug='general-manager'")[0]["id"]
    for pid in NEW:
        manager = rows(api, "SELECT manager_id FROM pipelines WHERE id=?", (pid,))[0]["manager_id"]
        seat = rows(api, "SELECT id FROM bots WHERE slug=? AND state<>'archived'", (M.manager_role(pid),))
        assert manager == ("bot:" + seat[0]["id"] if seat else general_manager), pid
        for stage in rows(api, "SELECT id,owner FROM stages WHERE pipeline_id=? AND kind='work'", (pid,)):
            assert stage["owner"], stage["id"]
            if "experiments" in pid or "monitoring" in pid:
                assert stage["owner"] == "assignee"
    # Opportunity's Proposal has a person on its OK.
    (proposal,) = rows(api, "SELECT person_id FROM requirements WHERE stage_id='opportunity-proposal' AND kind='page'")
    assert proposal["person_id"]


def test_a_second_start_changes_nothing(environment):
    api = _new_company(environment)
    before = _snapshot(api)
    assert _snapshot(_restart(environment)) == before


def test_a_retired_or_renamed_preset_stays_as_the_company_left_it(environment):
    api = _new_company(environment)
    with api.app.state.store.transaction() as c:
        PS.retire(c, OWNER, "customer")
        record = PS.read(c, "lead")
        record["name"] = "Prospects"
        PS.save(c, OWNER, record)
    again = _restart(environment)
    found = {r["id"]: r for r in rows(again, "SELECT id,name,retired_at FROM pipelines")}
    assert found["customer"]["retired_at"]
    assert found["lead"]["name"] == "Prospects"
    assert not rows(
        again, "SELECT id FROM pipelines WHERE preset IN ('customer','lead') AND id NOT IN ('customer','lead')"
    )


def test_a_company_set_up_before_these_presets_gets_them_at_its_next_start(environment):
    """The one 0.6 company set up with General, Product and Engineering only."""
    api = _new_company(environment)
    with api.app.state.store.transaction() as c:
        ids = ",".join(f"'{p}'" for p in NEW)
        c.execute(f"DELETE FROM requirements WHERE stage_id IN (SELECT id FROM stages WHERE pipeline_id IN ({ids}))")
        c.execute(f"DELETE FROM stages WHERE pipeline_id IN ({ids})")
        c.execute(f"DELETE FROM pipelines WHERE id IN ({ids})")
        c.execute("DELETE FROM factories WHERE id IN ('growth','success')")
    assert _layout(api)[0] == [(f, sorted(p)) for f, p in FACTORIES if f in ("product", "corporate")]
    # Corporate stayed, so Growth and Success come after it here; what each factory holds is the same.
    assert sorted(_layout(_restart(environment))[0]) == sorted((f, sorted(p)) for f, p in FACTORIES)


# ----------------------------------------------------------------------------- an experiment, end to end
def _page(c, title):
    fid = "file-" + H.new_id()[:20]
    now = H.now()
    c.execute(
        "INSERT INTO bot_files (id, bot_id, scope, identity, title, kind, mime, locator, first_activity_at, "
        "last_activity_at, current_version) VALUES (?,?,?,?,?,'file','text/html','tico_blob',?,?,1)",
        (fid, "general-manager", "bot", fid, title, now, now),
    )
    c.execute(
        "INSERT INTO blobs (id, owner, digest, size, name, content_type, created_at) VALUES (?,?,?,?,?,?,?)",
        ("blob-" + fid, OWNER, "d", 1, "results.html", "text/html", now),
    )
    c.execute(
        "INSERT INTO bot_file_versions (file_id, version, blob_id, digest, size, name, mime, actor, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (fid, 1, "blob-" + fid, "d", 1, "results.html", "text/html", OWNER, now),
    )
    return fid


def _verdict(c, page):
    owed = V.owe(c, page["file_id"], page["version"], page["rubric_hash"])
    V.record(c, owed["id"], {"verdict": "met", "p": 0.9, "reason": ""})


def test_an_experiment_goes_live_only_once_a_person_oks_its_go_live_page(environment):
    """A person's idea rests in Ideas; Ideas -> Building by its bot owner, a Go-live page Jev passes and a person OKs, Running, a Results page, Done."""
    api = _new_company(environment)
    assignee = "bot:" + rows(api, "SELECT id FROM bots WHERE slug='general-manager'")[0]["id"]
    (go_live,) = rows(
        api, "SELECT id, person_id FROM requirements WHERE stage_id='growth-experiments-building' AND kind='page'"
    )
    person = go_live["person_id"]
    assert person and person.startswith("human:")
    with api.app.state.store.transaction() as c:
        row = H.task_create(
            c,
            OWNER,
            "Try a referral banner",
            "",
            assignee,
            pipeline="growth-experiments",
            lint=False,
            allow_planned=True,
        )
        assert E.task(c, row["id"])["stage_id"] == "growth-experiments-ideas"
        row = E.move(c, row["id"], "growth-experiments-building", assignee, seen_version=row["version"])
        assert row["owner_id"] == assignee
        page = P.attach(
            c, row["id"], "Go-live page", "page:" + _page(c, "Go live"), assignee, seen_version=row["version"]
        )
        _verdict(c, page)
        row = E.task(c, row["id"])
        assert row["stage_id"] == "growth-experiments-building"  # Jev passed it: it waits on the person
        with pytest.raises(H.Refused, match="task_needs_person"):
            E.move(c, row["id"], E.NEXT, assignee, "Looks fine", seen_version=row["version"])
        answer(c, row["id"], go_live["id"], person, "ok", own_click=True)
        row = E.task(c, row["id"])
        assert (row["stage_id"], row["owner_id"]) == ("growth-experiments-running", assignee)
        page = P.attach(
            c, row["id"], "Results page", "page:" + _page(c, "Results"), assignee, seen_version=row["version"]
        )
        _verdict(c, page)
        assert E.advance(c, row["id"])["stage_id"] == "growth-experiments-done"


def test_a_qualified_lead_files_one_opportunity_on_an_owned_stage(environment):
    api = _new_company(environment)
    with api.app.state.store.transaction() as c:
        row = H.task_create(c, OWNER, "Acme Co", "", OWNER, pipeline="lead", lint=False)
        E.move(c, row["id"], "lead-qualified", OWNER, "They want a demo")
        filed = c.execute(
            "SELECT to_task_id FROM links WHERE from_task_id=? AND kind='handoff'", (row["id"],)
        ).fetchall()
        assert len(filed) == 1
        deal = E.task(c, filed[0][0])
    assert (deal["pipeline_id"], deal["stage_id"]) == ("opportunity", "opportunity-talking")
    assert deal["owner_id"]
