"""Helpers that other tests share, moved out of test_org_rules_surfaces.py so they import no test file."""

import json
from backend.tests.helpers.api import headers
from backend.tests.helpers.automation_cli import Client
from clients import cli_spec, hubcli


def cli(api, token, *argv):
    args = hubcli.parser().parse_args(["rules", *argv])
    return cli_spec.of(args).call(Client(api, token), args)


def mcp(api, name, arguments=None, token="ana-test"):
    r = api.post(
        "/api/v2/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
        headers=headers(token),
    )
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    payload = result.get("structuredContent")
    return result["isError"], payload if payload is not None else json.loads(result["content"][0]["text"])
