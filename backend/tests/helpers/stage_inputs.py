"""A software company's Product task and the Engineering task it filed, with PRs and pages as the engine leaves them:
the shared set-up of backend/tests/test_stage_inputs.py and test_pr_conflicts.py. Synthetic rows only."""

import json

from backend import bot_settings as BS
from backend import item_metadata as IM
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.product_engineering_presets import legacy_pair

REPO = "acme/app"
ANA = X.ANA
# The role bots, each found by its seat as setup seats them (test_product_engineering_presets.py has the full set).
SEATS = [
    ("pm", "segment-product-owner", "Product Owner"),
    ("builder", "builder", "Builder"),
    ("rev-a", "reviewer", "Reviewer 1"),
    ("rev-b", "reviewer", "Reviewer 2"),
    ("rm", "release-owner", None),
]


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


def _move(c, task_id, stage):
    """A person moves the task on, past whatever its stage still lacks: the test sets up each stage's output itself."""
    return E.move(c, task_id, stage, ANA, "moved on for the test")


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
    legacy_pair(c)  # Product's Spec and In development, which files into Engineering
    PS.ensure_presets(c)
    X.github_installed(c)  # Build needs a repository: with no App it would wait on Connect GitHub
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
