"""The Product and Engineering presets (docs/pipelines-v3.md "Default pipelines", contract §5, §10 items 1 and 2): who
fills each role (backend/pipeline_seats.py), when they are made and retired (pipeline_settings.ensure_presets), the
handoff In development files (backend/stage_effects.py), C10's one wake when Spec has room (never_stuck), and
company_import.new_company passing over untouched presets.

Synthetic rows only (backend/tests/helpers/engine.py): the fictional company, where Ana owns it, and a few bots.
"""

import json

import pytest

from backend import bot_settings as BS
from backend import company_import
from backend import never_stuck as N
from backend import pipeline_seats as S
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend import task_requirements as R
from backend.store import H
from backend.tests.helpers import engine as X

ANA, BEN = X.ANA, "human:ben"
ROLE_BOTS = {
    "pm": "product-manager",
    "em": "engineering-manager",
    "builder": "builder",
    "rev-b": "pr-reviewer",
    "rev-a": "pr-reviewer",
    "rm": "release-owner",
}


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def _answers(c, software="", completed=True):
    """The setup record (backend/onboarding.py): the software answer, and whether setup finished."""
    record = {"answers": {"software_product": software}, "completed": H.now() if completed else None}
    c.execute(
        "INSERT INTO registry_metadata VALUES('onboarding',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps(record),),
    )


def _role_bot(c, slug, template):
    actor = X.bot(c, slug)
    BS.update(c, slug, {"template": template})
    return actor


def _seat(c, title, template, filled_by="", at=None):
    at = at or H.now()
    c.execute(
        "INSERT INTO seats (id, title, template, filled_by, created_at, created_by, updated_at, updated_by, closed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (H.new_id(), title, template, filled_by, at, ANA, at, ANA, at if filled_by else None),
    )


def _team(c):
    """The six role bots, each seated as setup seats them. The reviewer seats are titled so `rev-a` is Reviewer 1."""
    for slug, template in ROLE_BOTS.items():
        _role_bot(c, slug, template)
    _seat(c, "Product Manager", "product-manager", "pm")
    _seat(c, "Engineering Manager", "engineering-manager", "em")
    _seat(c, "Builder", "builder", "builder")
    _seat(c, "Reviewer 2", "pr-reviewer", "rev-b")
    _seat(c, "Reviewer 1", "pr-reviewer", "rev-a")


def _owners(record):
    return {s["id"]: s["owner"] for s in record["stages"]}


def _kinds(record):
    return {s["id"]: [r["kind"] for r in s["requirements"]] for s in record["stages"]}


# ----------------------------------------------------------------------------- seats
def test_roles_are_found_by_seat_template_then_by_bot_template_never_by_name(c):
    assert S.resolve(c) == dict.fromkeys(S.ROLES)
    _team(c)
    assert S.resolve(c) == {
        S.PRODUCT_MANAGER: "bot:pm",
        S.ENGINEERING_MANAGER: "bot:em",
        S.BUILDER: "bot:builder",
        S.REVIEWER_1: "bot:rev-a",  # its seat's title sorts first
        S.REVIEWER_2: "bot:rev-b",
        S.RELEASE_MANAGER: "bot:rm",  # release_schedule_routes.release_manager, the release hour's bot
    }
    # An archived bot fills no role; another bot from the template does, even with no seat.
    c.execute("UPDATE bots SET state='archived' WHERE id='pm'")
    _role_bot(c, "zz-product", "product-manager")
    assert S.resolve(c)[S.PRODUCT_MANAGER] == "bot:zz-product"
    # A bot named like a role but from another template is not that role.
    _role_bot(c, "product-manager-ish", "coo")
    assert S.resolve(c)[S.PRODUCT_MANAGER] == "bot:zz-product"


def test_the_ok_person_is_the_first_person_above_the_manager_else_the_owner(c):
    _team(c)
    assert S.ok_person(c, "bot:pm") == ANA  # nobody above it: the company's owner
    assert S.ok_person(c, None) == ANA
    c.execute("UPDATE bot_config SET reports_to=? WHERE bot='pm'", (BEN,))
    assert S.ok_person(c, "bot:pm") == BEN
    X.left(c, BEN)
    assert S.ok_person(c, "bot:pm") == ANA


# ----------------------------------------------------------------------------- the presets
def test_a_set_up_software_company_gets_product_and_engineering_with_their_roles(c):
    _team(c)
    _answers(c, software="")  # a blank answer is yes (D7)
    assert PS.ensure_presets(c) == ["engineering", "product"]
    assert PS.ensure_presets(c) == []
    product, engineering = PS.read(c, "product"), PS.read(c, "engineering")
    assert (product["name"], product["prefix"], product["manager_id"], product["factory_id"]) == (
        "Product",
        "P",
        "bot:pm",
        "product",
    )
    assert (engineering["name"], engineering["prefix"], engineering["manager_id"]) == ("Engineering", "E", "bot:em")
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
    assert limits == {"product-spec": 2, "engineering-build": 3}
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
    assert (spec["line"], spec["person_id"], spec["who_oks"]) == ("Spec page", ANA, "people_with_exceptions")
    assert spec["settings"]["rubric"] == "Covers goal, scope, what done looks like"
    validate = stages["product-validate"]["requirements"][0]
    assert (validate["person_id"], validate["settings"]["with_release"]) == (None, True)  # Jev only; G15
    fixes_2 = stages["engineering-fixes-2"]["requirements"][1]
    assert fixes_2["person_id"] == ANA
    assert all(
        r["settings"]["at_head"]
        for s in ("engineering-review-1", "engineering-review-2")
        for r in stages[s]["requirements"]
    )
    never = [r for s in stages.values() for r in s["requirements"] if r["kind"] in ("pr_merged", "in_release")]
    assert len(never) == 2 and not any(r["can_push_past"] for r in never)


def test_presets_wait_for_setup_and_a_company_that_builds_no_software_gets_neither(c):
    _team(c)
    _answers(c, software="", completed=False)
    assert PS.ensure_presets(c) == []  # setup not finished: only General, as every start makes it
    _answers(c, software="no")
    assert PS.ensure_presets(c) == []
    assert [r[0] for r in c.execute("SELECT id FROM pipelines")] == ["general"]
    assert not c.execute("SELECT 1 FROM factories WHERE id='product'").fetchone()
    _answers(c, software="yes")
    assert PS.ensure_presets(c) == ["engineering", "product"]


def test_an_rc1_company_with_general_only_gets_the_pair_and_nothing_else_changes(c):
    X.bot(c, "task-manager")
    _answers(c, software="", completed=False)
    PS.ensure_presets(c)
    _team(c)
    before = PS.read(c, "general")
    events = c.execute("SELECT COUNT(*) FROM events WHERE action='pipeline.save'").fetchone()[0]
    _answers(c, software="yes")
    assert PS.ensure_presets(c) == ["engineering", "product"]
    assert PS.read(c, "general") == before
    after = c.execute("SELECT COUNT(*) FROM events WHERE action='pipeline.save'").fetchone()[0]
    assert after == events + 2


def test_answering_no_retires_the_untouched_pair_and_yes_brings_it_back(c):
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    _answers(c, software="no")
    assert PS.ensure_presets(c) == []
    assert PS.read(c, "product")["retired_at"] and PS.read(c, "engineering")["retired_at"]
    assert company_import.new_company(c)
    _answers(c, software="yes")
    assert PS.ensure_presets(c) == []
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
    assert PS.ensure_presets(c) == ["engineering", "product"]  # setup finished before any role was hired
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
                    {"kind": "pr_open"},
                    {"kind": "checks_pass"},
                    {"kind": "pr_merged"},
                    {"kind": "in_release", "settings": {"release_repo": "acme/app", "prereleases": True}},
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


def test_a_handoff_the_product_manager_cannot_file_is_filed_by_the_engine(c):
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
    for n in (1, 2):
        E.move(c, row["id"], do, ANA, "Go")
        filed = _filed(c, row["id"])
        assert len(filed) == n
        child = E.task(c, filed[-1][0])
        X.say_done(c, child)
        assert E.advance(c, child["id"])["status"] == "done"
        assert E.task(c, row["id"])["stage_id"] == check  # Linked work done moved it on
        if n == 1:
            E.move(c, row["id"], do, ANA, "Not validated")


# ----------------------------------------------------------------------------- C10
def test_the_product_manager_is_woken_once_when_spec_has_room(c):
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    ideas = [X.new_task(c, ANA, f"Idea {n}", "product") for n in range(4)]
    X.settled(c)
    N.check(c)
    first = N.room_key("product-spec", None)
    rows = c.execute("SELECT wake_key, recipient_id FROM messages WHERE wake_key LIKE 'room:%'").fetchall()
    assert [tuple(r) for r in rows] == [(first, "bot:pm")]
    N.check(c)
    assert c.execute("SELECT COUNT(*) FROM messages WHERE wake_key LIKE 'room:%'").fetchone()[0] == 1
    for idea in ideas[:2]:
        E.move(c, idea["id"], "product-spec", ANA)
    N.check(c)  # Spec is full: nothing
    assert c.execute("SELECT COUNT(*) FROM messages WHERE wake_key LIKE 'room:%'").fetchone()[0] == 1
    E.move(c, ideas[0]["id"], "product-ideas", ANA, "Not ready")
    left = c.execute("SELECT id FROM visits WHERE task_id=? AND stage_id='product-spec'", (ideas[0]["id"],)).fetchone()[
        0
    ]
    N.check(c)
    N.check(c)
    keys = [r[0] for r in c.execute("SELECT wake_key FROM messages WHERE wake_key LIKE 'room:%' ORDER BY rowid")]
    assert keys == [first, N.room_key("product-spec", left)]
    assert N.rules_hold(c) == []


def test_the_software_factory_guide_and_routing_playbook_name_the_seats_templates():
    """The guide and Engineering's route-a-request playbook send work to the seats the presets place (TEMPLATES),
    not to the 0.5 chart's Head of Product, Head of Engineering or a release-notes Release Manager."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    guide = (root / "docs/software-factory.md").read_text()
    playbook = (root / "templates/catalog/engineering-lead/playbooks/route-a-request.md").read_text()
    for text in (guide, playbook):
        assert "Head of Product" not in text and "Head of Engineering" not in text
        assert "`product-lead`" not in text and "`engineering-lead`" not in text
    for role in (S.PRODUCT_MANAGER, S.ENGINEERING_MANAGER, S.RELEASE_MANAGER):
        assert f"`{S.TEMPLATES[role]}`" in guide
    assert f"`{S.TEMPLATES[S.RELEASE_MANAGER]}` (Release Manager)" in playbook
    assert f"`{S.TEMPLATES[S.PRODUCT_MANAGER]}` (Product Manager)" in playbook
