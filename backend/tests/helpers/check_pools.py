"""Helpers that other tests share, moved out of test_check_pools.py so they import no test file."""

from backend import hubdb as H
from backend.tests.helpers.github import get, headers
from backend.tests.helpers.review_gate import HEAD, move, ticket as base_ticket
from backend.tests.usage_rows import segment
from backend import bot_settings as BS


RECEIPT = f"Tests: base {'c' * 40} head {HEAD}: 9 passed, 0 failed"


# The builder (cpo) runs on Anthropic; rev-a and rev-b on OpenAI; rev-c on Anthropic like the builder.
MODELS = {"cpo": "claude-opus-5-5", "rev-a": "gpt-6-sol", "rev-b": "gpt-6-sol", "rev-c": "claude-haiku-5-5"}


def team(api, seen=None):
    """The reviewer bots, each on a computer seen just now (or at `seen`)."""
    with api.app.state.store.transaction() as c:
        bots = {
            slug: {"name": slug, "runtime": "fake", "status": "active"}
            for slug in ("cpo", "cmo", "rev-a", "rev-b", "rev-c")
        }
        H.sync_registry(c, bots, {"people": [{"id": "ana", "email": "ana@acme.example", "team": "leadership"}]})
        for slug, model in MODELS.items():
            config = {
                "name": slug,
                "runtime": "claude" if model.startswith("claude") else "codex",
                "model": model,
                "status": "active",
                **({"profile": "reviewer"} if slug.startswith("rev-") else {}),
            }
            c.execute(
                "INSERT INTO bot_config(bot,team,owner_id) VALUES(?,?,?) ON CONFLICT(bot) DO NOTHING",
                (slug, "engineering", "ana"),
            )
            BS.write(c, slug, config)
        c.execute(
            "INSERT INTO runners(id,label,owner_id,token_hash,created_at) VALUES('r1','box','ana','h',?)", (H.now(),)
        )
        c.execute("UPDATE runner_states SET last_seen_at=? WHERE runner_id='r1'", (seen or H.now(),))
        for slug in ("rev-a", "rev-b", "rev-c"):
            c.execute("INSERT INTO assignments VALUES(?,?,1,?,'human:ana')", (slug, "r1", H.now()))


def set_pool(api, task, step, pool, limit=2):
    """Set a step's pool the way Settings > Tasks does: the whole type saved with its steps."""
    kind = get(api, "task-types/" + task["type_id"])["type"]
    steps = [
        {
            **{k: s[k] for k in ("id", "name", "status")},
            **({"check_pool": pool, "check_limit": limit} if s["name"] == step else {}),
        }
        for s in kind["steps"]
    ]
    r = api.post("/api/v2/task-types/" + task["type_id"], headers=headers(), json={"steps": steps})
    assert r.status_code == 200, r.text
    return r.json()["type"]


def ticket(api, receipt):
    """A synthetic PR built by cpo on Anthropic, with head-specific run evidence for the family gate."""
    task = base_ticket(api, receipt)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO attempts(id,bot_id,created_at,settled_at,task_id) VALUES(?,?,?,?,?)",
            ("builder-" + task["id"], "cpo", H.now(), H.now(), task["id"]),
        )
        segment(c, "builder-" + task["id"], model='claude-opus-5-5', provider='anthropic', runtime='claude')
    return task


def busy(api, slug, title="Review 1: PR #77"):
    """An open check `slug` holds on another ticket."""
    with api.app.state.store.transaction() as c:
        other = H.task_create(c, "bot:cpo", f"Another change for {slug}", "x", "bot:cpo")
        return H.task_create(c, "bot:cpo", title, "Check it.", "bot:" + slug, parent_id=other["id"])


def checks(api, task):
    with api.app.state.store.read() as c:
        kids = [H.task(c, i) for i in H.TR.child_ids(c, task["id"])]
    return [k for k in kids if k["status"] in H.ACTIVE_STATUSES]
