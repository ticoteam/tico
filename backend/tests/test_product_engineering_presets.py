"""The Product and Engineering presets (docs/pipelines-v3.md "Default pipelines", contract §5, §10 items 1 and 2): who
fills each role (backend/pipeline_seats.py), when they are made and retired (pipeline_settings.ensure_presets), the
handoff In development files (backend/stage_effects.py), C10 (nobody is told when Spec has room, never_stuck), and
company_import.new_company passing over untouched presets.

Synthetic rows only (backend/tests/helpers/engine.py): the fictional company, where Ana owns it, and a few bots.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import answers as A
from backend import company_import
from backend import never_stuck as N
from backend import page_verdicts as V
from backend import pipeline_presets_more as more
from backend import pipeline_seats as S
from backend import pipeline_settings as PS
from backend import task_calls as TC
from backend import task_engine as E
from backend import task_requirements as R
from backend import task_wakes as TW
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.product_engineering_presets import EPM, PPM, ROLE_BOTS, _answers, _role_bot, _seat, _team


def _made(c):
    """What ensure_presets made, leaving out the Growth and Success pipelines (test_default_pipelines_more.py)."""
    return [p for p in PS.ensure_presets(c) if p not in more.PRESETS]


ANA, BEN = X.ANA, "human:ben"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def _owners(record):
    return {s["id"]: s["owner"] for s in record["stages"]}


def _kinds(record):
    return {s["id"]: [r["condition"] or r["kind"] for r in s["requirements"]] for s in record["stages"]}


# ----------------------------------------------------------------------------- seats
def test_roles_are_found_by_seat_template_then_by_bot_template_never_by_name(c):
    assert S.resolve(c) == dict.fromkeys(S.ROLES)
    _team(c)
    assert S.resolve(c) == {
        S.PRODUCT_MANAGER: "bot:" + PPM,
        S.PRODUCT_OWNER: "bot:pm",
        S.ENGINEERING_MANAGER: "bot:" + EPM,
        S.BUILDER: "bot:builder",
        S.REVIEWER_1: "bot:rev-a",  # its seat's title sorts first
        S.REVIEWER_2: "bot:rev-b",
        S.RELEASE_MANAGER: "bot:rm",  # release_schedule_routes.release_manager, the release hour's bot
    }
    # An archived bot fills no role; another bot from the template does, even with no seat.
    c.execute("UPDATE bots SET state='archived' WHERE id='pm'")
    _role_bot(c, "zz-product", "segment-product-owner")
    assert S.resolve(c)[S.PRODUCT_OWNER] == "bot:zz-product"
    # A bot named like a role but from another template is not that role.
    _role_bot(c, "product-owner-ish", "assistant")
    assert S.resolve(c)[S.PRODUCT_OWNER] == "bot:zz-product"
    # Each team's Manager has its own template: another team's Manager is not this one's.
    _role_bot(c, "growth-manager", "growth-manager")
    c.execute("UPDATE bots SET state='archived' WHERE id=?", (PPM,))
    assert S.resolve(c)[S.PRODUCT_MANAGER] is None
    assert S.resolve(c)[S.ENGINEERING_MANAGER] == "bot:" + EPM


def test_the_ok_person_is_the_first_person_above_the_manager_else_the_owner(c):
    _team(c)
    assert S.ok_person(c, "bot:" + PPM) == ANA  # nobody above it: the company's owner
    assert S.ok_person(c, None) == ANA
    c.execute("UPDATE bot_config SET reports_to=? WHERE bot=?", (BEN, PPM))
    assert S.ok_person(c, "bot:" + PPM) == BEN
    X.left(c, BEN)
    assert S.ok_person(c, "bot:" + PPM) == ANA


# ----------------------------------------------------------------------------- the presets
def test_a_set_up_software_company_gets_product_and_engineering_with_their_roles(c):
    _team(c)
    _answers(c, software="")  # a blank answer is yes (D7)
    assert _made(c) == ["engineering", "product"]
    assert _made(c) == []
    product, engineering = PS.read(c, "product"), PS.read(c, "engineering")
    assert (product["name"], product["prefix"], product["manager_id"], product["factory_id"]) == (
        "Product",
        "P",
        "bot:" + PPM,
        "product",
    )
    assert (engineering["name"], engineering["prefix"], engineering["manager_id"]) == ("Engineering", "E", "bot:" + EPM)
    assert c.execute("SELECT name FROM factories WHERE id='product'").fetchone()[0] == "Product"
    assert _owners(product) == {
        "product-ideas": "bot:pm",
        "product-spec": "bot:pm",
        "product-in-development": "bot:pm",
        "product-validate": "bot:pm",
    }
    assert _owners(engineering) == {
        "engineering-on-deck": "bot:builder",
        "engineering-build": "bot:builder",
        "engineering-review-1": "bot:rev-a",
        "engineering-fixes-1": "bot:builder",
        "engineering-review-2": "bot:rev-b",
        "engineering-fixes-2": "bot:builder",
        "engineering-merge": "bot:rm",
        "engineering-release": "bot:rm",
    }
    assert _kinds(product) == {
        "product-ideas": [],
        "product-spec": ["page"],
        "product-in-development": ["linked_done"],
        "product-validate": ["page"],
    }
    assert _kinds(engineering) == {
        "engineering-on-deck": [],
        "engineering-build": ["pr_open", "checks_pass"],
        "engineering-review-1": ["page"],
        "engineering-fixes-1": ["owner_done", "checks_pass"],
        "engineering-review-2": ["page"],
        "engineering-fixes-2": ["checks_pass", "page"],
        "engineering-merge": ["pr_merged"],
        "engineering-release": ["in_release"],
    }
    stages = {s["id"]: s for s in product["stages"] + engineering["stages"]}
    assert stages["product-ideas"]["by_hand"] and not stages["engineering-on-deck"]["by_hand"]
    limits = {sid: s["task_limit"] for sid, s in stages.items() if s["task_limit"]}
    assert limits == {"product-spec": 20, "engineering-build": 3}
    assert [stages[s]["expected_minutes"] for s in ("product-spec", "product-in-development", "product-validate")] == [
        2880,
        7200,
        1440,
    ]
    assert [
        stages[s]["expected_minutes"]
        for s in ("engineering-build", "engineering-review-1", "engineering-fixes-2", "engineering-merge")
    ] == [1440, 120, 240, 120]
    assert stages["product-in-development"]["on_enter"] == {"file_into": "engineering"}
    assert stages["engineering-merge"]["on_enter"] == {"act": "github.queue"}
    spec = stages["product-spec"]["requirements"][0]
    assert (spec["line"], spec["person_id"], spec["who_oks"]) == ("Spec page", ANA, "people_only")
    assert "acceptance criteria someone can check" in spec["settings"]["rubric"]
    validate = stages["product-validate"]["requirements"][0]
    assert (validate["person_id"], validate["settings"]["with_release"]) == (None, True)  # Jev only; G15
    fixes_2 = stages["engineering-fixes-2"]["requirements"][1]
    assert (fixes_2["person_id"], fixes_2["who_oks"]) == (ANA, "people_only")
    assert "the checks at the newest commit" in fixes_2["settings"]["rubric"]
    assert "what Reviewer 1 and Reviewer 2 each found" in fixes_2["settings"]["rubric"]
    # The two person sign-offs are the only people-only OKs, and the only requirements naming a person.
    people_only = sorted(sid for sid, s in stages.items() for r in s["requirements"] if r["who_oks"] == "people_only")
    assert people_only == ["engineering-fixes-2", "product-spec"]
    named = sorted(sid for sid, s in stages.items() for r in s["requirements"] if r["person_id"])
    assert named == people_only
    assert all(
        r["settings"]["at_head"]
        for s in ("engineering-review-1", "engineering-review-2")
        for r in stages[s]["requirements"]
    )
    never = [r for s in stages.values() for r in s["requirements"] if r["condition"] in ("pr_merged", "in_release")]
    assert len(never) == 2 and not any(r["can_push_past"] for r in never)


def test_presets_wait_for_setup_and_a_company_that_builds_no_software_gets_neither(c):
    _team(c)
    _answers(c, software="", completed=False)
    assert _made(c) == []  # setup not finished: only General, as every start makes it
    _answers(c, software="no")
    assert _made(c) == []
    assert [r[0] for r in c.execute("SELECT id FROM pipelines") if r[0] not in more.PRESETS] == ["general"]
    assert not c.execute("SELECT 1 FROM factories WHERE id='product'").fetchone()
    _answers(c, software="yes")
    assert _made(c) == ["engineering", "product"]


def test_an_rc1_company_with_general_only_gets_the_pair_and_nothing_else_changes(c):
    X.bot(c, "general-manager")
    _answers(c, software="", completed=False)
    PS.ensure_presets(c)
    _team(c)
    before = PS.read(c, "general")
    events = c.execute("SELECT COUNT(*) FROM events WHERE action='pipeline.save'").fetchone()[0]
    _answers(c, software="yes")
    assert _made(c) == ["engineering", "product"]
    assert PS.read(c, "general") == before
    after = c.execute("SELECT COUNT(*) FROM events WHERE action='pipeline.save'").fetchone()[0]
    assert after == events + 2 + len(more.PRESETS) - len(
        more.PRIVATE
    )  # Growth and Success; no Legal Owner or Bookkeeper


def test_answering_no_retires_the_untouched_pair_and_yes_brings_it_back(c):
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    _answers(c, software="no")
    assert _made(c) == []
    assert PS.read(c, "product")["retired_at"] and PS.read(c, "engineering")["retired_at"]
    assert company_import.new_company(c)
    _answers(c, software="yes")
    assert _made(c) == []
    assert not PS.read(c, "product")["retired_at"] and not PS.read(c, "engineering")["retired_at"]
    # A pair someone changed stays when the answer is no: Product as changed, and Engineering, which it files into.
    product = PS.read(c, "product")
    product["goal"] = "Specs our customers asked for"
    PS.save(c, ANA, product)
    _answers(c, software="no")
    PS.ensure_presets(c)
    assert not PS.read(c, "product")["retired_at"] and not PS.read(c, "engineering")["retired_at"]


def test_an_untouched_preset_gets_its_roles_once_their_bots_appear_and_a_changed_one_does_not(c):
    _answers(c, software="yes")
    assert _made(c) == ["engineering", "product"]  # setup finished before any role was hired
    assert set(_owners(PS.read(c, "engineering")).values()) == {None}
    _role_bot(c, "builder", "builder")
    _seat(c, "Builder", "builder", "builder")
    PS.ensure_presets(c)
    engineering = PS.read(c, "engineering")
    assert engineering["settings_version"] == 1
    assert _owners(engineering)["engineering-build"] == "bot:builder"
    assert _owners(engineering)["engineering-merge"] is None
    engineering["goal"] = "Ship it"
    PS.save(c, ANA, engineering)
    _role_bot(c, "rm", "release-owner")
    PS.ensure_presets(c)
    assert _owners(PS.read(c, "engineering"))["engineering-merge"] is None


def test_a_new_company_passes_over_untouched_presets(c):
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    assert company_import.new_company(c)
    product = PS.read(c, "product")
    product["goal"] = "Specs that ship"
    PS.save(c, ANA, product)
    assert not company_import.new_company(c)


def test_a_new_company_is_not_new_once_a_preset_holds_a_task(c):
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    X.new_task(c, ANA, "Saved carts", "product")
    assert not company_import.new_company(c)


# ----------------------------------------------------------------------------- what save takes now
def test_the_new_kinds_and_both_on_enter_actions_save_and_settings_keys_are_checked(c):
    PS.ensure_presets(c)
    record = {
        "name": "Ops",
        "prefix": "OP",
        "stages": [
            {
                "name": "Hand off",
                "on_enter": {"file_into": "general"},
                "requirements": [{"kind": "linked_done"}, {"kind": "page", "settings": {"at_head": True}}],
            },
            {
                "name": "Ship",
                "on_enter": {"act": "github.queue"},
                "requirements": [
                    PS.github("pr_open"),
                    PS.github("checks_pass"),
                    PS.github("pr_merged"),
                    PS.github("in_release", release_repo="acme/app", prereleases=True),
                ],
            },
        ],
    }
    assert PS.check(c, record) == []
    bad = json.loads(json.dumps(record))
    bad["stages"][0]["requirements"][1]["settings"] = {"at_head": "yes", "colour": "red"}
    bad["stages"][1]["requirements"][3]["settings"] = {"release_repo": "not a repo"}
    bad["stages"][1]["requirements"][0]["settings"] = {"rubric": "x"}
    assert PS.check(c, bad) == [
        "at_head on Hand off is on or off.",
        "A page has no setting colour (on Hand off).",
        "PR open has no setting rubric (on Ship).",
        "The release repo on Ship is owner/name, or empty for the server's.",
    ]
    saved = PS.save(c, ANA, record)
    assert saved["stages"][1]["on_enter"] == {"act": "github.queue"}


# ----------------------------------------------------------------------------- the handoff
def _spec_page(c, task, version=3):
    """The Spec page that counted on the task's visit to Spec, as task_pages.attach leaves it (track B)."""
    rid = c.execute("SELECT id FROM requirements WHERE stage_id='product-spec' AND kind='page'").fetchone()[0]
    c.execute(
        "INSERT INTO requirement_pages (id, visit_id, requirement_id, file_id, version, item_versions, attached_by_id, "
        "created_at) VALUES (?,?,?,?,?,?,?,?)",
        (H.new_id(), task["visit"]["id"], rid, "f-spec", version, "{}", "bot:pm", H.now()),
    )


def _filed(c, task_id):
    return [
        (row[0], row[1])
        for row in c.execute(
            "SELECT to_task_id, kind FROM links WHERE from_task_id=? ORDER BY created_at, id", (task_id,)
        ).fetchall()
    ]


def _product_task(c):
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    row = H.task_create(c, ANA, "Saved carts", "Let a shopper keep a cart.", ANA, pipeline="product", lint=False)
    row = E.move(c, row["id"], "product-spec", ANA)
    _spec_page(c, row)
    return row


def test_in_development_files_one_engineering_task_with_the_product_text_and_the_spec_page(c):
    row = _product_task(c)
    moved = E.move(c, row["id"], "product-in-development", ANA, "The spec is OK'd")
    assert moved["stage_id"] == "product-in-development"
    filed = _filed(c, row["id"])
    assert [kind for _, kind in filed] == ["handoff"]
    eng = E.task(c, filed[0][0])
    assert (eng["pipeline_id"], eng["stage_id"], eng["owner_id"]) == (
        "engineering",
        "engineering-on-deck",
        "bot:builder",
    )
    assert eng["title"] == "Saved carts"
    assert eng["body"] == "Let a shopper keep a cart.\n\nSpec: page:f-spec@3"
    assert eng["requester_id"] == "bot:pm"
    assert "handoff_filed" in X.events(c, row["id"])
    standing = R.standings(c, E.task(c, row["id"]))[0]
    assert (standing.state, standing.on) == (R.WAITING, eng["id"])
    # Back to Spec closes it (undo); In development again is a new visit and files a new one.
    E.move(c, row["id"], "product-spec", ANA, "The spec missed returns")
    assert E.task(c, eng["id"])["status"] == "closed"
    E.move(c, row["id"], "product-in-development", ANA, "Returns are in")
    assert len(_filed(c, row["id"])) == 2


def _queued_wakes(c, task_id):
    return c.execute(
        "SELECT j.bot_id, m.body FROM messages m JOIN jobs j ON j.message_id = m.id "
        "WHERE m.wake_key LIKE ? AND j.state='queued' ORDER BY m.created_at, m.id",
        (f"{task_id}:%",),
    ).fetchall()


def _writes(c, slug, repository="acme/app"):
    """`slug` may push to `repository` (Settings > Repositories, Chosen, Write): it can build there."""
    from backend import bot_settings as BS

    BS.write(c, slug, {"repo_access_mode": "chosen"})
    c.execute("INSERT INTO bot_repo_access VALUES(?,?,'write')", (slug, repository))


def _assignee_owned_handoff(c):
    """A Product task moved into In development where Engineering's On deck and Build belong to the task's assignee;
    the Engineering task it filed. The Builder may push to the product's repository; the Product Owner only reads it."""
    row = _product_task(c)
    X.github_installed(c)  # Build needs a repository: with no App it would wait on Connect GitHub
    _writes(c, "builder")
    c.execute("UPDATE stages SET owner='assignee' WHERE id IN ('engineering-on-deck', 'engineering-build')")
    E.move(c, row["id"], "product-in-development", ANA, "The spec is OK'd")
    return E.task(c, _filed(c, row["id"])[0][0])


def test_a_handoff_into_assignee_owned_stages_has_no_owner_and_the_manager_is_woken_to_pick_one(c):
    """The filed task's requester is the Product Owner, never its assignee: where Engineering's stages belong to the
    task's assignee, it has nobody, and the Engineering manager is woken to pick a builder (C25)."""
    eng = _assignee_owned_handoff(c)
    assert (eng["stage_id"], eng["requester_id"], eng["assignee_id"], eng["owner_id"]) == (
        "engineering-on-deck",
        "bot:pm",
        None,
        None,
    )
    built = E.advance(c, eng["id"])  # Build has room: in it goes, as the never-stuck check moves it
    assert (built["stage_id"], built["assignee_id"], built["owner_id"]) == ("engineering-build", None, None)
    assert E._turn_of(c, built) == (f"bot:{EPM}", "This task has no owner. Pick one and reassign it.")
    wakes = _queued_wakes(c, eng["id"])
    assert [bot for bot, _ in wakes] == [EPM], wakes
    assert wakes[0][1].startswith("This task has no owner. Pick one and reassign it."), wakes[0][1]
    picked = E.reassign(c, eng["id"], "bot:builder", f"bot:{EPM}", "Builder takes it", built["version"])
    assert (picked["assignee_id"], picked["owner_id"]) == ("bot:builder", "bot:builder")


def test_the_builder_the_manager_picks_on_deck_owns_the_handoff_in_build(c):
    eng = _assignee_owned_handoff(c)
    E.reassign(c, eng["id"], "bot:builder", f"bot:{EPM}", "Builder takes it", eng["version"])
    built = E.advance(c, eng["id"])
    assert (built["stage_id"], built["assignee_id"], built["owner_id"]) == (
        "engineering-build",
        "bot:builder",
        "bot:builder",
    )
    assert [bot for bot, _ in _queued_wakes(c, eng["id"])] == ["builder"]


def test_a_handoff_into_a_pipeline_with_no_manager_keeps_the_filer_as_assignee(c):
    """Nobody could pick an owner there: an assignee-owned first stage gets the filer, as before."""
    row = _product_task(c)
    c.execute("UPDATE stages SET owner='assignee' WHERE id='engineering-on-deck'")
    c.execute("UPDATE pipelines SET manager_id=NULL WHERE id='engineering'")
    E.move(c, row["id"], "product-in-development", ANA, "The spec is OK'd")
    eng = E.task(c, _filed(c, row["id"])[0][0])
    assert (eng["requester_id"], eng["assignee_id"], eng["owner_id"]) == ("bot:pm", "bot:pm", "bot:pm")


def _owned_by_a_bot_that_lost_write_access(c):
    """An Engineering task the Product Owner owns in On deck, given while it could push; it may only read now."""
    _writes(c, "pm")
    eng = H.task_create(c, ANA, "Saved carts", "Let a shopper keep a cart.", "bot:pm", pipeline="engineering")
    assert eng["owner_id"] == "bot:pm"
    # Read-only now, as the Product Owner in the stress run was: a read grant is not enough to build.
    c.execute("UPDATE bot_repo_access SET access='read' WHERE bot='pm'")
    return eng


def _move_notes(c, task_id):
    """The note on each of the task's moves, oldest first: why it moved, and why its assignee does not own it."""
    rows = c.execute(
        "SELECT note FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='move' ORDER BY rowid",
        (task_id,),
    ).fetchall()
    return [row[0] or "" for row in rows]


def _assignee_owned_engineering(c):
    """Engineering's work stages belong to the task's assignee, the product's repository is In use, and only the
    Builder may push to it."""
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    X.github_installed(c)
    _writes(c, "builder")
    c.execute("UPDATE stages SET owner='assignee' WHERE pipeline_id='engineering'")


def test_a_bot_that_cannot_push_to_the_repository_never_owns_an_assignee_owned_engineering_stage(c):
    """A task given to the Product Owner, read-only on the product's repository, in Engineering (as a handoff before
    the fix, or by hand): it never owns On deck or Build there. The stage has nobody, as with no assignee, the
    Engineering manager is woken to pick a builder, and the task's history says why."""
    _assignee_owned_engineering(c)
    eng = H.task_create(c, "bot:pm", "Saved carts", "Let a shopper keep a cart.", "bot:pm", pipeline="engineering")
    assert (eng["stage_id"], eng["assignee_id"], eng["owner_id"]) == ("engineering-on-deck", None, None)
    built = E.advance(c, eng["id"])
    assert (built["stage_id"], built["assignee_id"], built["owner_id"]) == ("engineering-build", None, None)
    assert E._turn_of(c, built) == (f"bot:{EPM}", "This task has no owner. Pick one and reassign it.")
    assert [bot for bot, _ in _queued_wakes(c, eng["id"])] == [EPM]
    assert _move_notes(c, eng["id"]) == ["pm cannot work On deck: it has no write access to acme/app", ""]


def test_a_task_that_comes_back_to_a_stage_whose_assignee_lost_write_access_goes_to_the_manager(c):
    """A task already in Build with the Product Owner as assignee (filed before the fix): a move into Fixes, a work
    stage of the assignee, leaves it with nobody rather than the bot that cannot push."""
    _assignee_owned_engineering(c)
    eng = _owned_by_a_bot_that_lost_write_access(c)
    fixes = next(s for s in E.stages(c, "engineering") if s["name"] == "Fixes 1")
    moved = E.move(c, eng["id"], fixes["id"], ANA, "Review found the cart is lost on sign-out")
    assert (moved["assignee_id"], moved["owner_id"]) == (None, None)
    assert _move_notes(c, eng["id"])[-1] == (
        "Review found the cart is lost on sign-out. pm cannot work Fixes 1: it has no write access to acme/app"
    )


def test_a_kickback_keeps_the_builder_that_can_push(c):
    """Fixes returning to the same builder is unchanged: the Builder may push, so it keeps the task."""
    _assignee_owned_engineering(c)
    eng = H.task_create(c, ANA, "Saved carts", "Let a shopper keep a cart.", "bot:builder", pipeline="engineering")
    assert eng["owner_id"] == "bot:builder"
    fixes = next(s for s in E.stages(c, "engineering") if s["name"] == "Fixes 1")
    moved = E.move(c, eng["id"], fixes["id"], ANA, "Review found the cart is lost on sign-out")
    assert (moved["assignee_id"], moved["owner_id"]) == ("bot:builder", "bot:builder")


def test_a_bot_that_cannot_push_does_not_snooze_its_engineering_task_out_of_sight(c):
    """The Product Owner left owning Build snoozed the task for two days, and nothing showed it. Its snooze is not
    taken: the manager bot gets the task, to give it to someone who can build. The Builder's own snooze is taken."""
    _assignee_owned_engineering(c)
    eng = _owned_by_a_bot_that_lost_write_access(c)
    row = E.task(c, eng["id"])
    out = E.snooze(c, eng["id"], "bot:pm", "Needs the Builder", "2026-11-14T08:00:00.000000Z", row["version"])
    assert out.get("snooze") is None
    assert out["note"] == (
        "pm cannot work On deck: it has no write access to acme/app. The manager has it now, to give it to someone "
        "who can."
    )
    assert out["visit"]["level"] == "manager"
    assert "snooze_refused" in X.events(c, eng["id"])
    # A long snooze dodges the snooze count and Late: the Engineering manager is woken now, told why, not on a later
    # pass, and Health's Tasks reading counts the task with a manager until someone who can build has it.
    wakes = _queued_wakes(c, eng["id"])
    assert [bot for bot, _ in wakes] == [EPM], wakes
    assert wakes[0][1].startswith(
        "pm cannot work On deck: it has no write access to acme/app, and it tried to snooze the task "
        "(Needs the Builder). Reassign it to a builder."
    ), wakes[0][1]
    from backend import health

    assert health.never_stuck_reading(c)["tasks"]["manager"] == [eng["id"]]
    E.reassign(c, eng["id"], "bot:builder", ANA, "Builder takes it", out["version"])
    row = E.task(c, eng["id"])
    assert E.snooze(c, eng["id"], "bot:builder", "Waiting for #12 to merge", None, row["version"]).get("snooze")


def test_a_refused_snooze_leaves_a_task_a_person_has_with_that_person(c):
    """Once the ladder has handed the task to a person, the owner that cannot build trying to snooze it does not hand
    it back to the manager bot, and the note says a person has it."""
    _assignee_owned_engineering(c)
    eng = _owned_by_a_bot_that_lost_write_access(c)
    c.execute("UPDATE visits SET level='person', person_id=? WHERE task_id=? AND left_at IS NULL", (ANA, eng["id"]))
    row = E.task(c, eng["id"])
    out = E.snooze(c, eng["id"], "bot:pm", "Needs the Builder", "2026-11-14T08:00:00.000000Z", row["version"])
    assert out.get("snooze") is None
    assert out["visit"]["level"] == "person"
    assert out["note"].endswith("A person has it now, to give it to someone who can.")
    assert _queued_wakes(c, eng["id"]) == []


def test_a_handoff_the_product_owner_cannot_file_is_filed_by_the_engine(c):
    row = _product_task(c)
    c.execute("UPDATE bots SET state='paused' WHERE id='pm'")
    E.move(c, row["id"], "product-in-development", ANA, "The spec is OK'd")
    filed = _filed(c, row["id"])
    assert len(filed) == 1
    assert E.task(c, filed[0][0])["requester_id"] == H.KEEPER


def test_a_handoff_nobody_can_file_files_nothing_and_the_move_goes_through(c):
    row = _product_task(c)
    c.execute("UPDATE pipelines SET retired_at=? WHERE id='engineering'", (H.now(),))
    moved = E.move(c, row["id"], "product-in-development", ANA, "The spec is OK'd")
    assert moved["stage_id"] == "product-in-development" and _filed(c, row["id"]) == []
    standing = R.standings(c, moved)[0]
    assert (standing.state, standing.on, standing.line) == (R.NOT_MET, "bot:pm", "No task filed yet")


def test_a_private_product_task_whose_handoff_builder_cannot_read_tells_the_product_manager_why(c):
    """The engine never adds a reader: the private Engineering task is not filed, the stage waits on the Product
    Manager, and their one wake for it says why."""
    row = _product_task(c)
    c.execute("UPDATE tasks SET private=1 WHERE id=?", (row["id"],))
    readers = c.execute("SELECT readers FROM tasks WHERE id=?", (row["id"],)).fetchone()[0]
    moved = E.move(c, row["id"], "product-in-development", ANA, "The spec is OK'd")
    assert moved["stage_id"] == "product-in-development" and _filed(c, row["id"]) == []
    standing = R.standings(c, moved)[0]
    assert (standing.state, standing.on) == (R.NOT_MET, "bot:pm")
    assert c.execute("SELECT readers FROM tasks WHERE id=?", (row["id"],)).fetchone()[0] == readers
    wakes = c.execute(
        "SELECT m.body FROM messages m JOIN jobs j ON j.message_id = m.id WHERE m.wake_key LIKE ? AND j.state='queued'",
        (f"{row['id']}:{moved['visit']['id']}:%",),
    ).fetchall()
    assert len(wakes) == 1, wakes
    assert wakes[0][0].startswith(
        "No Engineering task was filed: builder owns On deck and cannot read this private task.\n"
    ), wakes[0][0]


def test_not_validated_files_a_new_task_each_time_even_with_the_same_title(c):
    """Scenario 26 on a pipeline that files into General, whose filed task its owner can finish."""
    PS.ensure_presets(c)
    X.bot(c, "pm")
    record = {
        "name": "Ops",
        "prefix": "OP",
        "stages": [
            {"name": "Ask", "owner": "bot:pm", "requirements": [{"kind": "owner_done"}]},
            {
                "name": "Do",
                "owner": "bot:pm",
                "on_enter": {"file_into": "general"},
                "requirements": [{"kind": "linked_done"}],
            },
            {"name": "Check", "owner": "bot:pm", "requirements": [{"kind": "owner_done"}]},
        ],
    }
    ops = PS.save(c, ANA, record)
    do, check = ops["stages"][1]["id"], ops["stages"][2]["id"]
    row = X.new_task(c, ANA, "Order chairs", ops["id"])
    X.bot(c, "gm")
    c.execute("UPDATE pipelines SET manager_id='bot:gm' WHERE id='general'")  # a team with a General Manager
    for n in (1, 2):
        E.move(c, row["id"], do, ANA, "Go")
        filed = _filed(c, row["id"])
        assert len(filed) == n
        child = E.task(c, filed[-1][0])
        assert (child["assignee_id"], child["owner_id"]) == ("bot:pm", "bot:pm")  # General keeps the filer
        X.say_done(c, child)
        assert E.advance(c, child["id"])["status"] == "done"
        assert E.task(c, row["id"])["stage_id"] == check  # Linked work done moved it on
        if n == 1:
            E.move(c, row["id"], do, ANA, "Not validated")


# ----------------------------------------------------------------------------- C10
def _room_messages(c):
    return c.execute(
        "SELECT COUNT(*) FROM messages WHERE wake_key LIKE 'room:%' OR body LIKE '%has room.%'"
    ).fetchone()[0]


def test_the_product_manager_is_not_told_when_spec_has_room(c):
    """C10 (spec "Spec has room: move it, don't send a notice"): its own briefs rest in Ideas, and nobody is told when
    Spec has room, whether it had room from the start or a slot frees."""
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    ideas = [X.new_task(c, "bot:pm", f"Idea {n}", "product") for n in range(4)]  # its own briefs: held by hand
    X.settled(c)
    N.check(c)
    N.check(c)
    assert _room_messages(c) == 0
    for idea in ideas[:2]:
        E.move(c, idea["id"], "product-spec", ANA)
    N.check(c)
    E.move(c, ideas[0]["id"], "product-ideas", ANA, "Not ready")
    N.check(c)
    N.check(c)
    assert _room_messages(c) == 0
    assert [E.task(c, idea["id"])["stage_id"] for idea in ideas] == ["product-ideas", "product-spec"] + [
        "product-ideas"
    ] * 2
    assert N.rules_hold(c) == []


def test_an_assignee_owned_ideas_tells_nobody_when_spec_has_room(c):
    """C10 when a customer points Product's Ideas at "the task's assignee" (one Product Owner per area): a request moved
    back into Ideas is held by hand, and its owner is not told when Spec has room, however many slots free."""
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    area = X.bot(c, "app-owner")
    c.execute("UPDATE stages SET owner='assignee' WHERE id='product-ideas'")
    c.execute("UPDATE stages SET task_limit=1 WHERE id='product-spec'")
    bug = X.new_task(c, ANA, "Saved carts lose items", "product", assignee=area)
    X.settled(c)
    N.check(c)  # a person's request: it moves on into Spec by itself
    assert E.task(c, bug["id"])["stage_id"] == "product-spec"
    E.move(c, bug["id"], "product-ideas", ANA, "Route it to its area's owner first")
    row = E.task(c, bug["id"])
    assert (row["stage_id"], row["owner_id"], row["visit"]["entered_how"]) == ("product-ideas", area, "back")
    (held,) = W.of(c, row)
    assert held.state == W.BY_HAND
    X.settled(c)
    N.check(c)
    N.check(c)
    assert _room_messages(c) == 0
    assert E.task(c, bug["id"])["stage_id"] == "product-ideas"  # still held: someone moves it
    other = X.new_task(c, ANA, "Export to CSV", "product", assignee=area)
    X.settled(c)
    N.check(c)
    assert E.task(c, other["id"])["stage_id"] == "product-spec"
    E.move(c, other["id"], E.CLOSED, ANA, "Done elsewhere")
    N.check(c)
    N.check(c)
    assert _room_messages(c) == 0
    assert N.rules_hold(c) == []


def _ideas_moves(c):
    return {
        row[0]: row[1]
        for row in c.execute("SELECT title, stage_id FROM tasks WHERE pipeline_id='product' AND status='open'")
    }


def test_a_request_a_person_files_moves_into_spec_by_itself_and_a_bots_idea_waits_by_hand(c):
    """G5: a person who files into Product already picked it. With Spec's limit at 2, two filed at once both go in; the third
    waits for room, named as room, and goes in first when a slot frees. A bot's idea, and a request moved back into
    Ideas, are held by hand."""
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    c.execute("UPDATE stages SET task_limit=2 WHERE id='product-spec'")  # a full Spec, whatever the preset's limit
    asked = [X.new_task(c, ANA, f"Request {n}", "product") for n in range(3)]
    brief = X.new_task(c, "bot:pm", "Brief", "product")
    X.settled(c)
    assert N.check(c)["moved"] == 2
    assert _ideas_moves(c) == {
        "Request 0": "product-spec",
        "Request 1": "product-spec",
        "Request 2": "product-ideas",
        "Brief": "product-ideas",
    }
    (wait,) = W.of(c, E.task(c, asked[2]["id"]))
    assert (wait.state, wait.line) == (W.ROOM, "No room in Spec")
    (held,) = W.of(c, E.task(c, brief["id"]))
    assert held.state == W.BY_HAND
    assert c.execute("SELECT COUNT(*) FROM messages WHERE wake_key LIKE 'room:%'").fetchone()[0] == 0
    moved = E.task(c, asked[0]["id"])
    assert moved["owner_id"] == "bot:pm" and X.wakes(c, moved["id"], "bot:pm")  # its owner is told at once
    E.move(c, asked[0]["id"], "product-ideas", ANA, "Not this month")
    N.check(c)
    assert _ideas_moves(c) == {
        "Request 0": "product-ideas",  # moved back: held by hand like any idea
        "Request 1": "product-spec",
        "Request 2": "product-spec",  # the slot went to the waiting request, not to a room wake
        "Brief": "product-ideas",
    }
    assert c.execute("SELECT COUNT(*) FROM messages WHERE wake_key LIKE 'room:%'").fetchone()[0] == 0
    assert N.rules_hold(c) == []


# ----------------------------------------------------------------------------- the two person sign-offs (G1)
def _jev_passed(c, task_id, file_id, version):
    found = V.owe(c, file_id, version, "")
    c.execute("UPDATE page_verdicts SET verdict='met', reasons='' WHERE id=?", (found["id"],))
    return E.task(c, task_id)


def _sign_off(c, stage_id):
    """A task waiting on the person's OK of its page in `stage_id`, after Jev passed it. Returns it and the page
    requirement's id."""
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    pipeline = stage_id.split("-")[0]
    row = H.task_create(c, ANA, "Saved carts", "Let a shopper keep a cart.", ANA, pipeline=pipeline, lint=False)
    row = E.move(c, row["id"], stage_id, ANA, "Straight to the sign-off for this test")
    rid = c.execute("SELECT id FROM requirements WHERE stage_id=? AND kind='page'", (stage_id,)).fetchone()[0]
    c.execute(
        "INSERT INTO requirement_pages (id, visit_id, requirement_id, file_id, version, item_versions, attached_by_id, "
        "created_at) VALUES (?,?,?,?,?,?,?,?)",
        (H.new_id(), row["visit"]["id"], rid, "f-page", 1, "{}", row["owner_id"], H.now()),
    )
    return _jev_passed(c, row["id"], "f-page", 1), rid


@pytest.mark.parametrize("stage_id", ["product-spec", "engineering-fixes-2"])
def test_no_bot_gives_or_pushes_past_a_sign_off_and_late_still_ends_on_the_person(c, time_machine, stage_id):
    time_machine.move_to(datetime(2026, 11, 10, 9, tzinfo=timezone.utc), tick=False)
    row, rid = _sign_off(c, stage_id)
    # Fixes 2's checks need GitHub: met here by taking them off, so the person's OK is all the task waits on.
    c.execute("UPDATE requirements SET withdrawn_at=? WHERE stage_id=? AND kind='item'", (H.now(), stage_id))
    row = E.task(c, row["id"])
    page = next(s for s in R.standings(c, row) if s.requirement["id"] == rid)
    assert (page.state, page.on) == (R.WAITING, ANA)
    owner, manager = row["owner_id"], row["pipeline"]["manager_id"]
    for bot in (owner, manager):
        with pytest.raises(H.Refused, match="task_requirement"):
            A.answer(c, row["id"], rid, bot, "ok", seen_version=E.task(c, row["id"])["version"])
        with pytest.raises(H.Refused, match="task_needs_person"):
            E.move(c, row["id"], E.NEXT, bot, "The person is away", seen_version=E.task(c, row["id"])["version"])
    # Late: no bot is woken, since none can give or push past a people-only OK, and the manager still cannot push
    # past. The task stays the person's, whom it reads.
    minutes = row["stage"]["expected_minutes"]
    X.settled(c)
    time_machine.shift(timedelta(minutes=minutes + 1))
    assert N.check(c)["late"] == 1
    late = E.task(c, row["id"])
    assert [who for key, who in X.wakes(c, row["id"]) if key.endswith(":late")] == []
    with pytest.raises(H.Refused, match="task_needs_person"):
        E.move(c, row["id"], E.NEXT, manager, "Late: pushing past", seen_version=late["version"])
    time_machine.shift(timedelta(minutes=3 * minutes))
    N.check(c)
    head = next(w for w in W.of(c, E.task(c, row["id"])) if w.requirement_id == rid)
    assert (head.state, head.who) == (W.PERSON, ANA)
    assert H.task_private_readable(c, ANA, E.task(c, row["id"]))
    assert not c.execute("SELECT 1 FROM answers WHERE answer='pushed_past'").fetchone()
    assert N.rules_hold(c) == []
    # The person's OK is what moves it on.
    A.answer(c, row["id"], rid, ANA, "ok")
    after = E.task(c, row["id"])
    assert (
        after["stage_id"]
        == {"product-spec": "product-in-development", "engineering-fixes-2": "engineering-merge"}[stage_id]
    )


def test_a_people_only_page_that_names_nobody_waits_on_a_person_not_on_jev_alone(c):
    row, rid = _sign_off(c, "product-spec")
    c.execute("UPDATE requirements SET person_id=NULL WHERE id=?", (rid,))
    row = E.task(c, row["id"])
    (page,) = R.standings(c, row)
    assert (page.state, page.on) == (R.WAITING, None)
    (wait,) = W.of(c, row)
    assert (wait.state, wait.who) == (W.PERSON, ANA)
    with pytest.raises(H.Refused, match="task_requirement"):
        A.answer(c, row["id"], rid, "bot:pm", "ok", seen_version=row["version"])
    A.answer(c, row["id"], rid, ANA, "ok")
    assert E.task(c, row["id"])["stage_id"] == "product-in-development"


SPEC_NOTES = "Repo acme/calc, package acme_calc. -0.0 is fine."


def test_the_engineering_task_carries_what_the_person_said_with_their_spec_ok(c):
    """Acme rc.3 bug 11: the Builder asked again which repository to use, which the person had said with their OK."""
    row, rid = _sign_off(c, "product-spec")
    A.answer(c, row["id"], rid, ANA, "ok", SPEC_NOTES)
    ((filed, kind),) = _filed(c, row["id"])
    assert kind == "handoff"
    name = H.actor_name(c, ANA)
    assert E.task(c, filed)["body"] == (
        f"Let a shopper keep a cart.\n\nSpec: page:f-page@1\n\n{name}'s notes on the spec, given with their OK:\n"
        + SPEC_NOTES
    )
    # The Product task's own wakes in In development say it too, marked as the person's.
    line = f"{name}'s notes with their OK of {R.name_of(R.get(c, rid))} in Spec: {SPEC_NOTES}"
    assert line in TW.body(c, E.task(c, row["id"]), to="bot:pm").splitlines()


def test_an_ok_with_no_notes_adds_nothing_to_the_engineering_task(c):
    row, rid = _sign_off(c, "product-spec")
    A.answer(c, row["id"], rid, ANA, "ok")
    ((filed, _),) = _filed(c, row["id"])
    assert E.task(c, filed)["body"] == "Let a shopper keep a cart.\n\nSpec: page:f-page@1"
    assert "notes" not in TW.body(c, E.task(c, row["id"]), to="bot:pm")


def test_what_the_person_says_with_the_pre_release_ok_reaches_whoever_acts_after_it(c):
    """The other direction: the OK of what was built moves the task on to Merge and Release, and every wake there
    carries the person's notes; moved back, it no longer does."""
    row, rid = _sign_off(c, "engineering-fixes-2")
    X.github_installed(c)  # Merge and Release need GitHub: with no App the Release Owner would wait on Connect GitHub
    c.execute("UPDATE requirements SET withdrawn_at=? WHERE stage_id='engineering-fixes-2' AND kind='item'", (H.now(),))
    said = "Say in the release notes that negate(-0.0) is 0.0"
    A.answer(c, row["id"], rid, ANA, "ok", said)
    merge = E.task(c, row["id"])
    assert merge["stage_id"] == "engineering-merge"
    line = f"{H.actor_name(c, ANA)}'s notes with their OK of {R.name_of(R.get(c, rid))} in Fixes 2: {said}"
    assert line in TW.body(c, merge, to=merge["owner_id"]).splitlines()
    told = c.execute(
        "SELECT recipient_id, body FROM messages WHERE wake_key LIKE ? AND body LIKE ?",
        (merge["id"] + ":%", f"%{said}%"),
    ).fetchall()
    assert [who for who, _ in told] == ["bot:rm"]  # the Release Owner, woken as Merge starts
    # On to Release (Merge's PR check taken off: no PR here), it still reads them: no sign-off came in between.
    c.execute("UPDATE requirements SET withdrawn_at=? WHERE stage_id='engineering-merge'", (H.now(),))
    release = E.advance(c, row["id"])
    assert release["stage_id"] == "engineering-release"
    assert line in TW.body(c, release, to=release["owner_id"]).splitlines()
    back = E.move(c, row["id"], "engineering-fixes-2", ANA, "The release notes are wrong")
    again = E.move(c, back["id"], E.NEXT, ANA, "Fixed, no new OK needed", seen_version=back["version"])
    assert again["stage_id"] == "engineering-merge"
    assert line not in TW.body(c, again, to=again["owner_id"])


def _at(stages, sid):
    return next(i for i, s in enumerate(stages) if s["id"] == sid)


def _sign_off_req(stages, sid):
    return next(r for r in stages[_at(stages, sid)]["requirements"] if r["who_oks"] == "people_only")


def _move_req(stages, sid, to):
    req = _sign_off_req(stages, sid)
    stages[_at(stages, sid)]["requirements"].remove(req)
    stages[_at(stages, to)]["requirements"].append(req)


def _swap(stages, a, b):
    i, j = _at(stages, a), _at(stages, b)
    stages[i], stages[j] = stages[j], stages[i]


def _fresh(stages):
    """The same stages written anew: no ids, so every stage and requirement is replaced."""
    for stage in stages:
        stage["id"] = None
        for req in stage["requirements"]:
            req["id"] = None


def _early_queue(stages, before):
    """A new stage that queues the PRs to merge, just before `before`."""
    stages.insert(
        _at(stages, before),
        {
            "name": "Ship early",
            "purpose": "",
            "on_enter": {"act": "github.queue"},
            "requirements": [{"kind": "owner_done"}],
        },
    )


# Every way a save could loosen a sign-off, on each preset: the sign-off's stage, a stage before it, one after it.
LOOSENING = {
    "who_oks": lambda st, sid, before, after: _sign_off_req(st, sid).update(who_oks="people_with_exceptions"),
    "given to a bot": lambda st, sid, before, after: _sign_off_req(st, sid).update(person_id="bot:pm"),
    "removed": lambda st, sid, before, after: st[_at(st, sid)].update(
        requirements=[r for r in st[_at(st, sid)]["requirements"] if r["who_oks"] != "people_only"]
    ),
    "made another kind": lambda st, sid, before, after: _sign_off_req(st, sid).update(kind="owner_done", settings={}),
    "moved to a later stage": lambda st, sid, before, after: _move_req(st, sid, after),
    "its stage deleted": lambda st, sid, before, after: st.pop(_at(st, sid)),
    "a later stage put before it": lambda st, sid, before, after: _swap(st, sid, after),
    "stages replaced wholesale": lambda st, sid, before, after: _fresh(st),
    "an action added before it": lambda st, sid, before, after: st[_at(st, before)].update(
        on_enter={"act": "github.queue"}
    ),
    "a new stage with an action before it": lambda st, sid, before, after: _early_queue(st, sid),
}
SIGN_OFFS = [
    ("product", "product-spec", "product-ideas", "product-in-development", PPM),
    ("engineering", "engineering-fixes-2", "engineering-review-2", "engineering-merge", EPM),
]


@pytest.mark.parametrize(("pipeline", "sid", "before", "after", "seat"), SIGN_OFFS)
def test_no_bot_loosens_a_sign_off_in_settings_but_a_person_may_and_bots_change_the_rest(
    c, pipeline, sid, before, after, seat
):
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    bots = ("bot:" + seat, X.bot(c, "assistant"), X.bot(c, "botops"))
    c.execute("UPDATE pipelines SET owner_id=? WHERE id=?", ("bot:assistant", pipeline))  # one bot as its owner too

    def edit(change, by):
        record = PS.read(c, pipeline)
        change(record["stages"], sid, before, after)
        return PS.save(c, by, record, seen_version=record["settings_version"])

    name = PS.read(c, pipeline)["stages"][_at(PS.read(c, pipeline)["stages"], sid)]["name"]
    for what, change in LOOSENING.items():
        for bot in bots:
            if not PS.may_change(c, bot, E.pipeline(c, pipeline)):
                continue  # BotOps: it may not change the pipeline at all
            with pytest.raises(
                H.Refused, match=f"This change loosens the person's OK on {name}. A person must make it."
            ):
                edit(change, bot)
    assert not PS.may_change(c, "bot:botops", E.pipeline(c, pipeline))
    for bot in bots[:2]:
        with pytest.raises(H.Refused, match="loosens the person's OK"):
            PS.retire(c, bot, pipeline, PS.read(c, pipeline)["settings_version"])
    # What does not loosen it a bot may change: the rubric, its push-past switch, a later stage.
    manager = bots[0]
    edit(lambda st, *_: _sign_off_req(st, sid)["settings"].update(rubric="Enough for a person to OK it."), manager)
    edit(lambda st, *_: _sign_off_req(st, sid).update(can_push_past=False), manager)
    edit(lambda st, *_: st[_at(st, after)].update(expected_minutes=90), manager)
    # A person may make any of them.
    assert edit(LOOSENING["a later stage put before it"], ANA)
    assert edit(LOOSENING["who_oks"], ANA)


def test_no_bot_starts_a_task_past_a_sign_off_or_moves_it_into_another_pipeline(c):
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    task = H.task_create(c, ANA, "Saved carts", "Let a shopper keep a cart.", ANA, pipeline="product", lint=False)
    E.reassign(c, task["id"], "bot:builder", ANA)
    for bot in ("bot:builder", "bot:" + PPM):
        with pytest.raises(H.Refused, match="task_no_such_stage"):
            TC.task_move(c, bot, task["id"], "engineering-merge", "Ship it", E.task(c, task["id"])["version"])
    for pipeline, start in (("product", "product-in-development"), ("engineering", "engineering-merge")):
        with pytest.raises(H.Refused, match="needs a person's OK first"):
            H.task_create(c, "bot:builder", "Skip it", "", "bot:builder", pipeline=pipeline, stage=start, lint=False)
    assert H.task_create(c, ANA, "Hotfix", "", ANA, pipeline="engineering", stage="engineering-merge", lint=False)


def test_an_untouched_preset_made_before_its_sign_offs_were_people_only_gets_them(c):
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    c.execute(
        "UPDATE requirements SET who_oks='people_with_exceptions' "
        "WHERE stage_id IN ('product-spec', 'engineering-fixes-2') AND kind='page'"
    )
    PS.ensure_presets(c)
    found = c.execute(
        "SELECT stage_id, who_oks FROM requirements WHERE stage_id IN ('product-spec', 'engineering-fixes-2') "
        "AND kind='page' ORDER BY stage_id"
    )
    assert [tuple(r) for r in found] == [("engineering-fixes-2", "people_only"), ("product-spec", "people_only")]


@pytest.mark.parametrize(
    ("pipeline", "before", "past"),
    [
        (
            "engineering",
            ("on-deck", "build", "review-1", "fixes-1", "review-2"),
            ("merge", "release", "done"),
        ),
        ("product", ("ideas",), ("in-development", "validate", "done")),
    ],
)
def test_no_bot_jumps_a_task_over_a_sign_off_from_any_earlier_stage(c, pipeline, before, past):
    """A forward move counts every stage it jumps as not met, so a bot's jump over Fixes 2 or Spec meets the
    people-only OK and is refused: no merge is queued, and no Engineering task is filed without the spec OK."""
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    manager = "bot:" + (EPM if pipeline == "engineering" else PPM)
    bots = ("bot:builder", "bot:rm", "bot:pm", manager, X.bot(c, "assistant"), X.bot(c, "botops"))
    for stage in before:
        row = H.task_create(c, ANA, "Saved carts", "Let a shopper keep a cart.", ANA, pipeline=pipeline, lint=False)
        if E.task(c, row["id"])["stage_id"] != f"{pipeline}-{stage}":
            E.move(c, row["id"], f"{pipeline}-{stage}", ANA, "Here for this test")
        for bot in bots:
            # Each bot as the task's owner, so only the sign-off stands in its way.
            E.reassign(c, row["id"], bot, ANA)
            effects = c.execute("SELECT COUNT(*) FROM effects").fetchone()[0]
            tasks = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            for to in past:
                seen = E.task(c, row["id"])["version"]
                with pytest.raises(H.Refused, match="task_needs_person"):
                    TC.task_move(c, bot, row["id"], f"{pipeline}-{to}", "Ship it", seen)
            assert E.task(c, row["id"])["stage_id"] == f"{pipeline}-{stage}"
            assert c.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == effects
            assert c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == tasks
    assert not c.execute("SELECT 1 FROM answers WHERE answer='pushed_past'").fetchone()
