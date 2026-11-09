"""Helpers that other tests share, moved out of test_login_holds.py so they import no test file."""

from backend.store import H
from backend.tests.helpers.api import assign, get, headers, post, runner


CLAUDE = "You've hit your session limit · resets 2:40am (UTC)"


def limit(api, machine, attempt, errors=(CLAUDE,), **extra):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "limit-" + attempt["id"]}, machine["token"])
    events = [
        {"seq": n, "kind": "error", "payload": {"error": text, "limit": True}} for n, text in enumerate(errors, 1)
    ]
    post(api, f"attempts/{attempt['id']}/events", {"events": events}, machine["token"])
    return post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "failed", "last_seq": len(events), "limited": True, "text": "", **extra},
        machine["token"],
    )


def done(api, machine, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "ok-" + attempt["id"]}, machine["token"])
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "Done."},
        machine["token"],
    )


def at(monkeypatch, when, api=None):
    """The clock reads `when`; the test computer has been reporting in all along, not waking from a silence."""
    monkeypatch.setattr(H, "now", lambda: when)
    if api is not None:
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE runner_states SET last_seen_at=?,awake_since=NULL", (when,))


def signed_in(api, machine, bots, runtimes):
    """A heartbeat from a computer whose bots are ready and on which `runtimes` are signed in."""
    report = {
        "schema_version": 1,
        "runtimes": {name: {"installed": True, "authenticated": "ready"} for name in runtimes},
        "bots": {
            bot: {"ready": True, "runtime": "fake", "repository_present": True, "configuration_valid": True}
            for bot in bots
        },
    }
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": report}, token=machine["token"])


def providers_on(api):
    view = get(api, "providers")
    api.put(
        "/api/v2/providers",
        json={
            "enabled": ["openai", "anthropic"],
            "model": view["default"]["model"] or "",
            "expected_revision": view["revision"],
        },
        headers={**headers(), "Idempotency-Key": f"providers-{view['revision']}"},
    ).raise_for_status()


def two_bots(api, monkeypatch, runtimes=("codex",)):
    providers_on(api)
    at(monkeypatch, "2026-10-19T01:46:47.000000Z")
    machine = runner(api)
    for bot in ("ops", "finance"):
        assign(api, machine, bot)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runner_states SET capacity=16 WHERE runner_id=?", (machine["runner_id"],))
    signed_in(api, machine, ["ops", "finance"], runtimes)
    return machine


def events(api, machine, attempt, rows):
    post(
        api,
        f"attempts/{attempt['id']}/events",
        {
            "events": [
                {
                    "seq": n,
                    "kind": kind,
                    "payload": {"error": text, "limit": True} if kind == "error" else {"text": text},
                }
                for n, (kind, text) in enumerate(rows, 1)
            ]
        },
        machine["token"],
    )
    return len(rows)


QUOTA = "Quota exceeded. Check your plan and billing details."


def health_detail(api, bot):
    return next(i for i in api.get("/api/status", headers=headers()).json()["health_issues"] if i.get("bot") == bot)[
        "detail"
    ]
