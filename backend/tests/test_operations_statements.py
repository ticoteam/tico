"""Operations checks each bot's computer, its sign-in and its credentials once per page, not once per harness and move:
the statement counts stay flat as bots and harnesses grow (backend/readiness.py `can_run`, views.runnable_choices)."""

import collections
import re

from backend.store import H, encode
from backend.tests.test_api import api, assign, get, post, ready, runner  # noqa: F401
from backend.tests.test_task_privacy_reads import traced

HARNESSES = ("claude", "codex", "gemini", "pi")       # gemini and pi hold a key per bot, so each check reads the grants


def shape(sql):
    return re.sub(r"\s+", " ", re.sub(r"'[^']*'|\?", "?", sql)).strip()


def seed(api, computers, first, last):
    """Bots first..last-1 on the computers (each with a sign-in for every harness), in the company's roster."""
    store = api.app.state.store
    slugs = [f"bot{i:03d}" for i in range(first, last)]
    with store.transaction() as c:
        H.sync_registry(c, {s: {"name": s, "runtime": "fake", "status": "active"} for s in slugs},
                        {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben", "cara")]})
        for i, s in enumerate(slugs, first):
            harness = HARNESSES[i % len(HARNESSES)]
            c.execute("INSERT INTO bot_config(bot,config,team,operator) VALUES(?,?,?,?)",
                      (s, encode({"name": s, "runtime": harness, "harness": harness}), None, "ana"))
    for k, r in enumerate(computers):
        mine = slugs[k::len(computers)]
        for s in mine:
            assign(api, r, s)
        post(api, "runners/heartbeat", {"version": "test", "platform": "test", "capabilities": ["computer_logins_v1"],
             "readiness": {"schema_version": 1, "runtimes": {rt: {"installed": True, "authenticated": "ready",
                           "account": {"kind": "subscription"}} for rt in HARNESSES},
                           "bots": {s: {"runtime": HARNESSES[int(s[3:]) % len(HARNESSES)], "ready": True} for s in mine}}},
             r["token"])


def counted(api, token="ana-test"):
    page, statements = traced(api, lambda: get(api, "operations", token=token))
    shapes = collections.Counter(shape(s) for s in statements)
    return page, {
        "computer rows": sum(n for s, n in shapes.items() if s.startswith("SELECT id,label,last_seen_at,revoked_at")),
        "placement reads": sum(n for s, n in shapes.items() if "JOIN runners r ON r.id=a.runner_id WHERE a.bot=?" in s),
        "credential reads": sum(n for s, n in shapes.items() if s.startswith("SELECT id,env FROM credentials")),
        "statements": len(statements),
    }


def test_operations_reads_a_bots_placement_and_credentials_once_however_many_bots(api):
    computers = [runner(api, "ana", f"Mac {k}") for k in range(6)]
    seed(api, computers, 0, 24)
    small_page, small = counted(api)
    seed(api, computers, 24, 96)
    large_page, large = counted(api)
    assert len(large_page["computers"]) == 6
    assert sum(len(m["bots"]) for m in large_page["computers"]) == 96
    # A computer is read once, each bot's placement comes from one batch, and the credential table is read once per page.
    assert large["computer rows"] <= len(computers) and large["computer rows"] == small["computer rows"]
    assert large["placement reads"] == 0 and large["credential reads"] <= 1
    # Only the per-bot grant lookups grow with the roster: the rest of the page is flat.
    assert large["statements"] - small["statements"] <= 2 * (96 - 24)
