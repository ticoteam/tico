"""Private pipelines (backend/private_pipelines.py, docs/pipelines-v3.md "Private pipelines") and the Legal and Finance
presets that use them.

Every task in a private pipeline is private, whatever was asked, and its reader list gets the pipeline's manager and
the company's owner. Nobody else reads it: not an Admin, not a bot that is not on it. The pipeline itself stays visible.

The fictional company (backend/tests/helpers/company.py): Ana owns it, Ben is an Admin, Cara a member. Every bot reports
to an unrelated person here (`unrelated_task_managers`), so no org-chart line lets Ana, Ben or Cara read a bot's task.
"""

import pytest

from backend import never_stuck, pipeline_settings as PS, private_pipelines as PP
from backend import bot_settings as BS
from backend import task_engine as E
from backend import waiting_on as W
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import get, unrelated_task_managers
from backend.tests.helpers.company import bot as bot_token
from backend.tests.helpers import engine as X

ANA, BEN, CARA = "human:ana", "human:ben", "human:cara"
LEGAL_BOT, BOOKKEEPER = "bot:legal-owner", "bot:finance-owner"


def _bot(c, slug, template):
    """An active bot on the test computer, made from `template`."""
    X.bot(c, slug)
    BS.write(c, slug, {"name": slug, "runtime": "fake", "status": "active", "template": template})
    return H.bot_actor(slug)


def _legal(c):
    """The company with its Legal Owner and its Legal pipeline (made by ensure_private)."""
    _bot(c, "legal-owner", "legal-owner")
    PS.ensure_private(c)
    unrelated_task_managers(c)
    return PS.read(c, "legal")


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        X.pipeline(conn)  # General
        _legal(conn)
        yield conn


def _file(c, by=CARA, title="Review the reseller contract", **more):
    return H.task_create(c, by, title, "The contract is attached.", by, pipeline="legal", lint=False, **more)


# ----------------------------------------------------------------------------- the presets
def test_legal_and_finance_wait_for_their_owner_bots(company):
    """No owner bot, no pipeline (never the General Manager standing in), and no empty Corporate factory. Each appears
    once its bot does, managed and staffed by it, with no limit and no person's OK."""
    with company.transaction() as c:
        X.pipeline(c)
        _bot(c, "general-manager", "general-manager")
        PS.ensure_presets(c)
        assert not c.execute("SELECT 1 FROM pipelines WHERE id IN ('legal','finance')").fetchone()
        assert not c.execute("SELECT 1 FROM factories WHERE id='corporate'").fetchone()
        assert PS.preset(c, "legal")["manager_id"] is None  # not the General Manager

        _bot(c, "legal-owner", "legal-owner")
        assert PS.ensure_private(c) == ["legal"]
        _bot(c, "finance-owner", "finance-owner")
        assert PS.ensure_private(c) == ["finance"]
        assert PS.ensure_private(c) == []
        for pid, owner, prefix, stages in (
            ("legal", LEGAL_BOT, "LG", ["Intake", "Review", "Rules check"]),
            ("finance", BOOKKEEPER, "F", ["Intake", "Doing", "Review"]),
        ):
            record = PS.read(c, pid)
            assert record["private"] and record["manager_id"] == owner and record["prefix"] == prefix
            assert record["factory_id"] == "corporate"
            assert [s["name"] for s in record["stages"]] == stages
            for stage in record["stages"]:
                assert stage["owner"] == owner and stage["task_limit"] is None
                assert all(r["kind"] == "owner_done" and not r["person_id"] for r in stage["requirements"])
        assert PS.read(c, "legal")["goal"] == (
            "Contracts and compliance questions answered before they hold up the business"
        )
        assert PS.read(c, "finance")["goal"] == "Money in, money out, and the books that show both"


def test_a_private_preset_never_gets_the_general_manager_when_its_bot_goes(company):
    with company.transaction() as c:
        X.pipeline(c)
        _bot(c, "general-manager", "general-manager")
        _legal(c)
        c.execute("UPDATE bots SET state='archived' WHERE id='legal-owner'")
        PS.ensure_presets(c)  # _fill on an untouched preset
        assert PS.read(c, "legal")["manager_id"] == LEGAL_BOT
        assert {s["owner"] for s in PS.read(c, "legal")["stages"]} == {LEGAL_BOT}


def test_a_new_bot_of_the_template_makes_its_pipeline(api):
    """The hook: making a bot (settings_admin.create_bot) makes Legal once its Legal Owner exists."""
    from backend.tests.helpers.api import post

    post(
        api,
        "bots",
        {"slug": "legal-owner", "display_name": "Legal Owner", "template": "legal-owner", "status": "planned"},
    )
    with api.app.state.store.read() as c:
        found = c.execute("SELECT private, manager_id FROM pipelines WHERE id='legal'").fetchone()
    assert found and found[0] == 1 and found[1].startswith("bot:")


# ----------------------------------------------------------------------------- tasks in it
def test_a_task_filed_into_it_is_private_with_its_readers(c):
    made = _file(c, private=False)  # a person asked for a company task
    row = H.task(c, made["id"])
    assert H.task_private(c, row)
    assert H.task_readers(row) == [LEGAL_BOT, ANA]
    assert row["owner_id"] == LEGAL_BOT and row["stage_id"] == "legal-intake"
    for who in (CARA, LEGAL_BOT, ANA):
        assert H.task_private_readable(c, who, row), who
    for who in (BEN, "bot:assistant"):
        assert not H.task_private_readable(c, who, row), who


def test_from_private_and_a_bot_filing_are_forced_too(c):
    """`from_private` (a company-visible task under private work) does not make a Legal task public."""
    made = _file(c, title="Is our NDA mutual", from_private=True)
    assert H.task_private(c, H.task(c, made["id"]))
    made = H.task_create(c, "bot:ops", "Check the DPA", "Attached.", "bot:ops", pipeline="legal", lint=False)
    row = H.task(c, made["id"])
    assert H.task_private(c, row) and H.task_readers(row) == [LEGAL_BOT, ANA]


def test_it_cannot_be_made_public(c):
    made = _file(c)
    with pytest.raises(H.Refused, match="stays private"):
        H.task_update(c, CARA, made["id"], private=False)
    assert H.task_private(c, H.task(c, made["id"]))


def test_a_child_of_a_private_pipeline_task_is_private(c):
    parent = _file(c)
    child = H.task_create(
        c, LEGAL_BOT, "Rule check: the reseller contract", "Facts.", "bot:ops", parent_id=parent["id"], lint=False
    )
    row = H.task(c, child["id"])
    assert row["pipeline_id"] == "general" and H.task_private(c, row)
    assert not H.task_private_readable(c, BEN, row)


def test_a_company_task_filed_into_it_from_a_public_parent_is_private(c):
    parent = H.task_create(c, CARA, "Launch in Canada", "Plan.", CARA, lint=False, private=False)
    assert not H.task_private(c, H.task(c, parent["id"]))
    child = H.task_create(
        c, CARA, "Canadian terms", "Check.", CARA, parent_id=parent["id"], pipeline="legal", lint=False
    )
    row = H.task(c, child["id"])
    assert H.task_private(c, row) and H.task_readers(row) == [LEGAL_BOT, ANA]


def test_a_stage_effect_files_a_private_task(c):
    """A public pipeline whose first stage files into Legal (stage_effects._file): the filed task is private."""
    ops = PS.save(
        c,
        E.SYSTEM,
        {
            "name": "Sales ops",
            "prefix": "SO",
            "manager_id": "bot:ops",
            "stages": [
                {
                    "name": "Contract",
                    "owner": "bot:ops",
                    "on_enter": {"file_into": "legal"},
                    "requirements": [{"kind": "linked_done"}],
                }
            ],
        },
    )
    deal = H.task_create(c, CARA, "Acme deal", "Their paper.", CARA, pipeline=ops["id"], lint=False)
    filed = c.execute(
        "SELECT t.id FROM links l JOIN tasks t ON t.id=l.to_task_id WHERE l.from_task_id=? AND l.kind='handoff'",
        (deal["id"],),
    ).fetchone()
    assert filed
    row = H.task(c, filed[0])
    assert row["pipeline_id"] == "legal" and H.task_private(c, row)
    assert set(H.task_readers(row)) >= {LEGAL_BOT, ANA}
    assert not H.task_private_readable(c, BEN, row)


def test_a_restore_into_it_is_private(c):
    """A company task deleted from a pipeline that has since been made private (it held no task then) comes back from
    the trash private, with its readers (task_engine.restore)."""
    from backend import task_delete

    board = PS.save(c, ANA, _record(private=False))
    made = H.task_create(c, CARA, "Board minutes", "Draft.", CARA, pipeline=board["id"], lint=False, private=False)
    assert not H.task_private(c, H.task(c, made["id"]))
    task_delete.delete_tasks(c, [made["id"]], apply=True, actor=ANA, keep_work=True)
    board = PS.save(c, ANA, {**PS.read(c, board["id"]), "private": True})
    assert board["private"]
    assert task_delete.restore_tasks(c, [made["id"]], actor=ANA)["restored"] == [made["id"]]
    row = H.task(c, made["id"])
    assert row["pipeline_id"] == board["id"] and H.task_private(c, row)
    assert H.task_readers(row) == ["bot:ops", ANA]
    assert not H.task_private_readable(c, BEN, row)


# ----------------------------------------------------------------------------- reads
def test_non_readers_see_nothing_of_it_but_the_pipeline(api):
    with api.app.state.store.transaction() as c:
        _legal(c)
        made = _file(c, title="Zebra indemnity clause")
    coo = bot_token(api, "assistant")
    for token in ("ben-test", coo):  # an Admin and a bot not on it
        assert get(api, "tasks?pipeline=legal", token=token)["tasks"] == []
        assert get(api, "tasks?q=Zebra", token=token)["tasks"] == []
        get(api, f"tasks/{made['id']}", token=token, expected=404)
        strip = {p["id"]: p for p in get(api, "factories", token=token)["pipelines"]}
        assert strip["legal"]["open"] == 0  # counts are the viewer's own
        assert "Zebra" not in str(get(api, "factories", token=token))
    for token in ("ana-test", "cara-test"):  # the company's owner and the requester
        assert [t["id"] for t in get(api, "tasks?pipeline=legal", token=token)["tasks"]] == [made["id"]]
        assert {p["id"]: p for p in get(api, "factories", token=token)["pipelines"]}["legal"]["open"] == 1
        get(api, f"tasks/{made['id']}", token=token)
    # The bot that is its manager reads it through the same rules a bot's reads use.
    auth = api.app.state.auth
    with api.app.state.store.read() as c:
        row = H.task(c, made["id"])
        auth.task_row(c, Identity(LEGAL_BOT, "bot"), row)
        with pytest.raises(Exception):
            auth.task_row(c, Identity("bot:assistant", "bot"), row)


def test_an_automation_hears_of_it_only_when_its_owner_reads_it(c, monkeypatch):
    made = _file(c)
    public = H.task_create(c, CARA, "Order chairs", "Six.", CARA, lint=False, private=False)
    assert H._automation_hears(c, "person:ana", made["id"])
    assert not H._automation_hears(c, "person:ben", made["id"])
    assert not H._automation_hears(c, "bot:assistant", made["id"])
    assert not H._automation_hears(c, "group:everyone", made["id"])
    assert H._automation_hears(c, "person:ben", public["id"])
    # hook() hands the check to the automations.
    from backend import automations

    heard = []
    monkeypatch.setattr(automations, "on_event", lambda *a, access=None: heard.append(access))
    H.hook(c, "task.commented", made["id"], lambda: {}, CARA)
    assert heard and not heard[0]({"owner_id": "person:ben"}) and heard[0]({"owner_id": "person:ana"})


# ----------------------------------------------------------------------------- never stuck
def test_the_ladder_stays_with_people_who_read_it(c):
    """The manager bot takes it (it reads it); a person after that is one who reads it: never Ben, the Admin."""
    made = H.task_create(c, "bot:ops", "Trademark search", "For the new name.", "bot:ops", pipeline="legal", lint=False)
    row = E.task(c, made["id"])
    assert never_stuck._manager(c, row, {}) == LEGAL_BOT
    # Bots here report to an unrelated person, who reads their tasks as a bot party's manager: still a reader.
    first = W.person_for(c, row)
    assert first != BEN and H.task_private_readable(c, first, row)
    # With nobody above the bots, the company's owner, on the reader list.
    c.execute("UPDATE bot_config SET reports_to=NULL")
    assert W.person_for(c, E.task(c, made["id"])) == ANA


# ----------------------------------------------------------------------------- settings
def _record(**fields):
    return {
        "name": "Board",
        "prefix": "BD",
        "manager_id": "bot:ops",
        "private": True,
        "stages": [{"name": "Draft", "owner": "bot:ops", "requirements": [{"kind": "owner_done"}]}],
        **fields,
    }


def test_settings_refuse_a_private_change_on_a_pipeline_with_tasks(c):
    _file(c)
    record = PS.read(c, "legal")
    with pytest.raises(H.Refused, match="holds tasks"):
        PS.save(c, ANA, {**record, "private": False})
    general = PS.read(c, "general")
    X.new_task(c, CARA, "Buy stamps")
    with pytest.raises(H.Refused, match="holds tasks"):
        PS.save(c, ANA, {**general, "private": True, "manager_id": "bot:ops"})


def test_settings_refuse_the_general_manager_or_no_manager(c):
    _bot(c, "general-manager", "general-manager")
    with pytest.raises(H.Refused, match="General Manager"):
        PS.save(c, ANA, _record(manager_id="bot:general-manager"))
    with pytest.raises(H.Refused, match="needs a manager"):
        PS.save(c, ANA, _record(manager_id=None))
    made = PS.save(c, ANA, _record())  # a brand-new private pipeline
    assert made["private"]
    # An empty one may still change; a bot never turns it off.
    with pytest.raises(H.Refused, match="Only a person"):
        PS.save(c, "bot:ops", {**made, "private": False}, seen_version=made["settings_version"])
    assert not PS.save(c, ANA, {**made, "private": False})["private"]


def test_a_manager_keeps_its_edits_on_a_private_pipeline_with_tasks(c):
    """Saving the Legal record unchanged in privacy (a stage's purpose) is not a flip."""
    _file(c)
    record = PS.read(c, "legal")
    record["stages"][0]["purpose"] = "Say what is asked."
    saved = PS.save(c, LEGAL_BOT, record, seen_version=record["settings_version"])
    assert saved["private"] and saved["stages"][0]["purpose"] == "Say what is asked."


# ----------------------------------------------------------------------------- the Bookkeeper's own tasks
def test_the_bookkeepers_own_tasks_land_in_finance_and_a_persons_do_not(c):
    from backend import bookkeeper as B

    _bot(c, "finance-owner", "finance-owner")
    c.execute("UPDATE bot_config SET reports_to='human:privacy-lead' WHERE bot='finance-owner'")
    assert B.filing(c, B.ACTOR) == (None, None)  # no Finance yet: General
    PS.ensure_private(c)
    assert B.filing(c, B.ACTOR) == ("finance", "finance-review")
    assert B.filing(c, ANA) == (None, None)
    task_id, state = B.deliver(c, "3 rows to sort", 3)
    row = H.task(c, task_id)
    assert state == "opened" and row["pipeline_id"] == "finance" and row["owner_id"] == B.ACTOR
    assert row["stage_id"] == "finance-review"  # filed taken in and done: it waits on its review only
    assert H.task_private(c, row) and H.task_readers(row) == [B.ACTOR, ANA]
    assert B._own_private_work(c, task_id)  # its private Finance actions still apply
    from backend import task_calls

    task_calls.task_done(c, B.ACTOR, task_id, "Sorted.", seen_version=H.task(c, task_id)["version"])
    assert H.task(c, task_id)["status"] == "done"  # one "done" finishes it, as in General
    ask = B.ask_for_view(c, ANA)
    assert H.task(c, ask)["pipeline_id"] == "general"
