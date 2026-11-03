#!/usr/bin/env python3
"""The hub as an MCP server over stdio: `python -m clients.hubmcp`.

A runtime (Codex, Claude, ...) spawns this inside a bot turn. It reads newline-delimited
JSON-RPC on stdin, answers on stdout, and turns every `tools/call` into the same HTTPS
request the `hub` CLI would make, with the turn's own credential (`HUB_API_URL`,
`HUB_TOKEN`; a warm harness's `tico-file:` token is read fresh on every call by
`clients.tico.Client`, so a rotated lease keeps working). Nothing here opens a database
or decides a rule; the schema and the tool bodies are `clients/hubtools.py`.
"""
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):                          # run as a script: python clients/hubmcp.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clients import runenv
from clients import hubtools  # noqa: E402
from clients.tico import APIError, Client  # noqa: E402


def main(stdin=None, stdout=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    url, token = runenv.get("HUB_API_URL"), runenv.get("HUB_TOKEN", "")
    if not url:
        print(json.dumps({"error": "HUB_API_URL is not set: the hub MCP server runs inside a bot turn"}),
              file=sys.stderr)
        return 1
    # An ask may wait up to 300 s on the server side of one poll; leave room for that.
    protocol = hubtools.Protocol(Client(url, token, timeout=30), api_error=APIError, local=True)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = protocol.handle(message)
        if reply is not None:
            stdout.write(json.dumps(reply, default=str) + "\n")
            stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
