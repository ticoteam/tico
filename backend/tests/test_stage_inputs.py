"""What each stage of the Product and Engineering presets is handed (backend/stage_inputs.py, docs/pipelines-v3.md
"What a stage is handed"): the wake that wakes a stage's bot, and the task's own read, list the Spec page, the PR, each
earlier Review page and what was said at done; a page an earlier stage never attached is named as missing.

Acme rc.3 (case 1): in Fixes 1 the Builder could not see Review 1's page, said done with nothing changed, and the
engine took it. Synthetic rows only (backend/tests/helpers/engine.py).
"""

import json

import pytest

from backend import bot_settings as BS
from backend import item_metadata as IM
from backend import pipeline_settings as PS
from backend import stage_inputs as SI
from backend import task_engine as E
from backend import task_wakes as TW
from backend.store import H
from backend.tests.helpers import engine as X
from clients import hubtools

REPO = "acme/app"
ANA = X.ANA
# The role bots, each found by its seat as setup seats them (test_product_engineering_presets.py has the full set).
SEATS = [
    ("pm", "segment-product-owner", "Product Owner"),
    ("builder", "builder", "Builder"),
    ("rev-a", "pr-reviewer", "Reviewer 1"),
    ("rev-b", "pr-reviewer", "Reviewer 2"),
    ("rm", "release-owner", None),
]


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def _link_pr(c, task_id, number, head):
    c.execute(
        "INSERT INTO task_links (id, task_id, kind, url, repo, number, source, item_type, item_key, created_at) "
        "VALUES (?,?,?,?,?,?,'github','pr',?,?)",
        (
            H.new_id(),
            task_id,
            "pr",
            f"https://github.com/{REPO}/pull/{number}",
            REPO,
            number,
            f"{REPO}#{number}",
            H.now(),
        ),
    )
    _push(c, number, head)


def _push(c, number, head, state="open", merged_sha=None):
    key = f"{REPO}#{number}"
    old = (IM.get(c, "github", "pr", key) or {}).get("fields") or {}
    IM.save(
        c,
        "github",
        "pr",
        key,
        {
            **old,
            "state": state,
            "head_sha": head,
            "merged_sha": merged_sha,
            "checks_state": "pending",
            "queue_state": None,
        },
        1,
    )


def _attach(c, task_id, file_id, by, version=1):
    """The page that counts for the open stage's page requirement, as task_pages.attach leaves it."""
    row = E.task(c, task_id)
    rid = c.execute(
        "SELECT id FROM requirements WHERE stage_id=? AND kind='page' AND withdrawn_at IS NULL", (row["stage_id"],)
    ).fetchone()[0]
    c.execute(
        "UPDATE requirement_pages SET replaced_at=? WHERE visit_id=? AND requirement_id=? AND replaced_at IS NULL",
        (H.now(), row["visit"]["id"], rid),
    )
    c.execute(
        "INSERT INTO requirement_pages (id, visit_id, requirement_id, file_id, version, item_versions, attached_by_id, "
        "created_at) VALUES (?,?,?,?,?,'{}',?,?)",
        (H.new_id(), row["visit"]["id"], rid, file_id, version, by, H.now()),
    )


def _done(c, task_id, notes):
    row = E.task(c, task_id)
    rid = c.execute(
        "SELECT id FROM requirements WHERE stage_id=? AND kind='owner_done' AND withdrawn_at IS NULL",
        (row["stage_id"],),
    ).fetchone()[0]
    X.answer(c, row, rid, "done", row["owner_id"], notes=notes)


def _move(c, task_id, stage):
    """A person moves the task on, past whatever its stage still lacks: the test sets up each stage's output itself."""
    return E.move(c, task_id, stage, ANA, "moved on for the test")


def _wake(c, task_id, to):
    """The newest wake to `to` for the task's open visit: what its bot reads."""
    row = E.task(c, task_id)
    found = c.execute(
        "SELECT body FROM messages WHERE wake_key LIKE ? AND recipient_id=? ORDER BY created_at DESC, rowid DESC",
        (f"{task_id}:{row['visit']['id']}:%", to),
    ).fetchone()
    assert found, (row["stage"]["name"], X.wakes(c, task_id))
    return found[0]


def _handed(c, task_id):
    return [e["line"] for e in SI.of(c, E.task(c, task_id))]


def _product_task(c):
    """A Product task in Spec, with the Spec page that counted there (file f-spec, version 3)."""
    at = H.now()
    for slug, template, title in SEATS:
        X.bot(c, slug)
        BS.update(c, slug, {"template": template})
        if title:
            c.execute(
                "INSERT INTO seats (id, title, template, filled_by, created_at, created_by, updated_at, updated_by, "
                "closed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (H.new_id(), title, template, slug, at, ANA, at, ANA, at),
            )
    c.execute(
        "INSERT INTO registry_metadata VALUES('onboarding',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps({"answers": {"software_product": "yes"}, "completed": at}),),
    )
    PS.ensure_presets(c)
    row = H.task_create(c, ANA, "Saved carts", "Let a shopper keep a cart.", ANA, pipeline="product", lint=False)
    E.move(c, row["id"], "product-spec", ANA)
    _attach(c, row["id"], "f-spec", "bot:pm", version=3)
    return E.task(c, row["id"])


def _filed_engineering(c):
    """The Product task in In development, and the Engineering task it filed with the OK'd Spec page."""
    product = _product_task(c)
    E.move(c, product["id"], "product-in-development", ANA, "The spec is OK'd")
    eng = c.execute("SELECT to_task_id FROM links WHERE from_task_id=?", (product["id"],)).fetchone()[0]
    return product["id"], eng


SPEC = "Spec page from Spec (Product): page:f-spec@3, read it with `tico file read f-spec`"
PR_OPEN = f"PR {REPO}#12 https://github.com/{REPO}/pull/12: open, newest commit "
REVIEW_1 = "Review page from Review 1: page:f-rev1@1, read it with `tico file read f-rev1`"
REVIEW_2 = "Review page from Review 2: page:f-rev2@1, read it with `tico file read f-rev2`"
FIXES_1 = "Fixes 1 was said done: bbb: fixed the empty cart; declined the rename (out of scope)"
BUILT = "Review page from Fixes 2: page:f-built@2, read it with `tico file read f-built`"


def test_each_engineering_and_product_stage_is_handed_what_came_before(c):
    product, eng = _filed_engineering(c)
    # Build: the Spec, as the Product task OK'd it.
    _move(c, eng, "engineering-build")
    assert _handed(c, eng) == [SPEC]
    assert "Spec page from Spec (Product): page:f-spec@3" in _wake(c, eng, "bot:builder")
    _link_pr(c, eng, 12, "aaa")

    # Review 1: the PR (the number its playbook's `gh pr view <n> -R <repo>` needs) and the Spec.
    _move(c, eng, "engineering-review-1")
    wake = _wake(c, eng, "bot:rev-a")
    assert "From earlier stages (read each before you act):" in wake
    assert f"- {SPEC} (" in wake and f"- {PR_OPEN}aaa" in wake
    assert SI.IF_MISSING in wake
    _attach(c, eng, "f-rev1", "bot:rev-a")

    # Fixes 1: Review 1's page, said by whom; the Acme case.
    _move(c, eng, "engineering-fixes-1")
    wake = _wake(c, eng, "bot:builder")
    assert f"- {REVIEW_1} (" in wake, wake
    assert _handed(c, eng) == [SPEC, PR_OPEN + "aaa", REVIEW_1]
    _push(c, 12, "bbb")
    _done(c, eng, "bbb: fixed the empty cart;\n declined the rename (out of scope)")

    # Review 2: Review 1's page and what the Builder said it fixed, at the new commit.
    _move(c, eng, "engineering-review-2")
    assert _handed(c, eng) == [SPEC, PR_OPEN + "bbb", REVIEW_1, FIXES_1]
    assert FIXES_1 in _wake(c, eng, "bot:rev-b")
    _attach(c, eng, "f-rev2", "bot:rev-b")

    # Fixes 2: both reviews, in the order they were written, and the Fixes 1 note between them.
    _move(c, eng, "engineering-fixes-2")
    assert _handed(c, eng) == [SPEC, PR_OPEN + "bbb", REVIEW_1, FIXES_1, REVIEW_2]
    wake = _wake(c, eng, "bot:builder")
    assert REVIEW_1 in wake and REVIEW_2 in wake
    _attach(c, eng, "f-built", "bot:builder", version=1)
    _attach(c, eng, "f-built", "bot:builder", version=2)  # sent back once: only the newest counts

    # Merge: the PR and the page the person OK'd. The Release Owner is woken only when GitHub takes the PR out of the
    # queue; that wake is the same body.
    _move(c, eng, "engineering-merge")
    assert E.task(c, eng)["owner_id"] == "bot:rm"
    assert _handed(c, eng)[-1] == BUILT
    wake = TW.body(c, E.task(c, eng), to="bot:rm")
    assert f"- {PR_OPEN}bbb" in wake and BUILT in wake

    # Release: the merge commit.
    _push(c, 12, "bbb", state="merged", merged_sha="ccc111")
    _move(c, eng, "engineering-release")
    merged = f"PR {REPO}#12 https://github.com/{REPO}/pull/12: merged as ccc111"
    assert merged in TW.body(c, E.task(c, eng), to="bot:rm")

    # Validate (Product): its own Spec page and the filed work's PR, with the release it went out in.
    _move(c, product, "product-validate")
    filed_pr = f"PR {REPO}#12 https://github.com/{REPO}/pull/12: merged as ccc111 (work this task filed)"
    assert _handed(c, product) == [
        "Spec page from Spec: page:f-spec@3, read it with `tico file read f-spec`",
        filed_pr + "; in no release yet",
    ]
    assert filed_pr + "; in no release yet" in _wake(c, product, "bot:pm")
    at = "2026-11-05T00:00:00Z"
    c.execute(
        "INSERT INTO github_releases (id, repo, tag, commit_sha, published_at, metadata_version, read_at, created_at) "
        "VALUES ('r1',?,'v1.2.0','x',?,1,?,?)",
        (REPO, at, at, at),
    )
    c.execute(
        "INSERT INTO github_release_commits (release_id, commit_sha, included, checked_at) VALUES ('r1','ccc111',1,?)",
        (at,),
    )
    assert H.task_view(c, product, ANA)["inputs"] == []  # a list of many tasks (Needs you) leaves it out
    view = H.task_view(c, product, ANA, inputs=True)
    assert view["inputs"][-1]["line"] == filed_pr + "; in release v1.2.0"
    assert view["inputs"][-1]["releases"] == ["v1.2.0"]


def test_a_page_an_earlier_stage_never_attached_is_named_missing_and_the_bot_is_told_to_send_it_back(c):
    _product, eng = _filed_engineering(c)
    _move(c, eng, "engineering-build")
    _link_pr(c, eng, 12, "aaa")
    _move(c, eng, "engineering-review-1")
    _move(c, eng, "engineering-fixes-1")  # pushed past Review 1 with no Review page
    missing = "Review page from Review 1: none was attached"
    assert _handed(c, eng)[-1] == missing
    wake = _wake(c, eng, "bot:builder")
    assert f"- {missing}" in wake and SI.IF_MISSING in wake
    assert 'tico task move <id> "<stage>"' in SI.IF_MISSING
    # Once a later visit to Review 1 attaches one, nothing is missing.
    E.move(c, eng, "engineering-review-1", ANA, "No review page")
    _attach(c, eng, "f-rev1", "bot:rev-a")
    _move(c, eng, "engineering-fixes-1")
    assert _handed(c, eng)[-1] == REVIEW_1


def test_the_open_visits_own_page_and_done_are_not_earlier_inputs(c):
    _product, eng = _filed_engineering(c)
    _move(c, eng, "engineering-build")
    _move(c, eng, "engineering-review-1")
    _attach(c, eng, "f-rev1", "bot:rev-a")
    assert _handed(c, eng) == [SPEC]  # its own page is under "To leave", not handed from before
    _move(c, eng, "engineering-fixes-1")
    _done(c, eng, "nothing to fix")
    assert _handed(c, eng)[-1] == REVIEW_1


def test_a_private_product_task_hands_on_none_of_its_pages(c):
    product, eng = _filed_engineering(c)
    _move(c, eng, "engineering-build")
    assert SPEC in _handed(c, eng)
    assert SI.of(c, E.task(c, eng), readable=lambda filer: False) == []
    c.execute("UPDATE tasks SET private=1 WHERE id=?", (product,))
    _move(c, eng, "engineering-review-1")
    assert "Spec page from Spec (Product)" not in _wake(c, eng, "bot:rev-a")


def test_a_filed_task_the_reader_may_not_name_is_left_out(c):
    product, eng = _filed_engineering(c)
    _link_pr(c, eng, 12, "aaa")
    assert [e["line"] for e in SI.of(c, E.task(c, product)) if e["kind"] == "pr"] == [
        PR_OPEN + "aaa (work this task filed)"
    ]
    assert not [e for e in SI.of(c, E.task(c, product), shown=lambda task_id: False) if e["kind"] == "pr"]


def test_tico_task_show_prints_what_earlier_stages_handed_on():
    task = {
        "id": "t1",
        "title": "Saved carts",
        "version": 4,
        "stage": {"name": "Fixes 1"},
        "to_leave": [],
        "inputs": [
            {"kind": "pr", "line": PR_OPEN + "aaa"},
            {"kind": "page", "line": REVIEW_1, "by": "bot:rev-a"},
            {"removed": True, "label": "A page you can't open"},
        ],
    }
    text = hubtools.task_text(task, {"bot:rev-a": "Reviewer 1"})
    lines = text.splitlines()
    at = lines.index("From earlier stages:")
    assert lines[at + 1 : at + 4] == [
        f"  {PR_OPEN}aaa",
        f"  {REVIEW_1} (Reviewer 1)",
        "  A page you can't open",
    ]
    assert "From earlier stages" not in hubtools.task_text({**task, "inputs": []})
