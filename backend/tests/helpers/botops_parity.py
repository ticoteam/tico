"""Helpers that other tests share, moved out of test_botops_parity.py so they import no test file."""

from uuid import uuid4
from backend.tests.helpers.api import claim, headers
from backend.store import H


def act(api, attempt, method, path, body=None, ref="turn"):
    """One `tico api` call: the request BotOps makes, asking to be answered as the person it works for."""
    kwargs = {"headers": {**headers(attempt["token"]), **({"X-Tico-On-Behalf-Of": ref} if ref else {})}}
    if body is not None:
        kwargs["json"] = body
    return api.request(method.upper(), "/api/v2/" + path, **kwargs)


def bot_id(api, slug):
    """The id a bot is stored under (tables, actors and readiness reports use it; the slug is only its label)."""
    with api.app.state.store.read() as c:
        return H.resolve_bot(c, slug)["id"]


def mcp_tool(env="OPS_ADMIN_MCP_TOKEN", authorization="Bearer ${OPS_ADMIN_MCP_TOKEN}", **extra):
    """A remote MCP server registration whose header names its credential by placeholder (docs/connect-tools.md)."""
    return {
        "service": "ops-admin",
        "can": ["read"],
        "env": env,
        **extra,
        "mcp": {
            "url": "https://mcp.example.com/usage",
            "transport": "http",
            "headers": {"Authorization": authorization},
        },
    }


def vault(api):
    from backend.tests.helpers.credentials import setup

    setup(api)


def everything(api):
    """Every table's text, to look for a value that must not be anywhere."""
    with api.app.state.store.read() as c:
        names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '%fts%'")]
        return "".join(
            str([tuple(r) for r in c.execute(f"SELECT * FROM {n}")])
            for n in names
            if n not in ("credentials", "credential_keys")
        )


# ------------------------------------------------------------------ following through on a person's request later
def _wake(api, machine):
    """A run the keeper starts with no task (a daily-update request): nobody's request is attached."""
    with api.app.state.store.transaction() as c:
        H.say(
            c,
            H.KEEPER,
            "bot:botops",
            "Time for your daily update.",
            kind="notice",
            refs={"wake": "update", "event": str(uuid4())},
        )
    return claim(api, machine, "botops")
