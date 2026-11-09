"""Helpers that other tests share, moved out of test_usage_limits.py so they import no test file."""

from backend.tests.helpers.api import assign, claim, get, headers, post, ready, runner
from backend.tests.helpers.usage import complete


def put(api, path, body, token="ana-test", expected=200):
    r = api.put("/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def cost(output_tokens):
    """A usage report priced by gpt-6-sol at $10 per million output tokens."""
    return {
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": output_tokens,
        "model": "gpt-6-sol",
        "runtime": "codex",
    }


def start(api, r, text="Please look at this."):
    post(api, "chat/ops", {"text": text})
    attempt = claim(api, r)
    return attempt


def finish(api, r, attempt, output_tokens):
    complete(api, r, attempt, cost(output_tokens))


def fleet(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    return r


def paused(api):
    status = get(api, "status")["bots"]
    return next((s for s in status if s["bot"] == "ops"), {})


def groups(api, ops_team):
    """Engineering with Product nested under it; ops in `ops_team`."""
    import json

    with api.app.state.store.transaction() as c:
        people = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        people["org_groups"] = {"eng": {"name": "Engineering"}, "product": {"name": "Product", "parent": "eng"}}
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(people),))
        c.execute("UPDATE bot_config SET team=? WHERE bot='ops'", (ops_team,))
