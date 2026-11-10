"""Helpers that other tests share, moved out of test_mcp.py so they import no test file."""

import json
from backend.tests.helpers.api import headers


def rpc(api, method, params=None, token="ana-test", rid=1):
    r = api.post(
        "/api/v2/mcp",
        json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}},
        headers=headers(token),
    )
    assert r.status_code == 200, r.text
    return r.json()


def call(api, name, arguments=None, token="ana-test"):
    reply = rpc(api, "tools/call", {"name": name, "arguments": arguments or {}}, token=token)
    result = reply["result"]
    payload = result.get("structuredContent")
    return result["isError"], payload if payload is not None else json.loads(result["content"][0]["text"])
