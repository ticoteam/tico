"""A stand-in for Grok Build's `grok agent stdio`, for the runner conformance suite (runner_support/tests/test_conformance.py).

`--version` answers as grok 1.0.25. `agent ... stdio` speaks ACP (JSON-RPC 2.0, one message per line) the way
runner-rs/src/hosts/grok.rs drives it: `initialize`, `session/new` and `session/load` (of a session this home made),
and `session/prompt`, which first asks the client's permission for a tool (as Grok does for a few calls even with
--always-approve), then streams two message chunks and answers with its stopReason. Every session call and turn is
appended to $FAKE_LOG as one JSON line (argv, the prompt, the granted variables, the permission chosen). A prompt
containing HANG streams one chunk and then waits, once per $FAKE_LOG, until its parent goes away."""
import json
import os
import sys
import time
import uuid

GRANTED = ("XAI_API_KEY", "ACME_API_KEY", "HUB_TOKEN", "HUB_API_URL", "HUB_BOT", "TICO_CRED_SOCKET", "GROK_CLAUDE_MCPS_ENABLED",
           "GROK_CURSOR_MCPS_ENABLED", "GROK_MANAGED_MCPS_ENABLED")


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def record(entry):
    with open(os.environ["FAKE_LOG"], "a") as log:
        log.write(json.dumps({"harness": "grok", "argv": sys.argv[1:], **entry}) + "\n")


def chunk(session, text):
    send({"jsonrpc": "2.0", "method": "session/update",
          "params": {"sessionId": session, "update": {"sessionUpdate": "agent_message_chunk",
                                                      "content": {"type": "text", "text": text}}}})


def main():
    args = sys.argv[1:]
    if args == ["--version"]:
        print("grok 1.0.25")
        return
    parent = os.getppid()
    known = os.path.join(os.environ.get("HOME") or ".", ".fake-grok-sessions")
    cwd = {}
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        method, rid, p = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}})
        elif method == "session/new":
            session = str(uuid.uuid4())
            with open(known, "a") as out:
                out.write(session + "\n")
            cwd[session] = p.get("cwd")
            record({"method": method, "session": session, "mcpServers": p.get("mcpServers")})
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": session}})
        elif method == "session/load":
            session = p.get("sessionId")
            record({"method": method, "session": session, "mcpServers": p.get("mcpServers")})
            if os.path.exists(known) and session in open(known).read().split():
                cwd[session] = p.get("cwd")
                send({"jsonrpc": "2.0", "id": rid, "result": None})
            else:
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32002, "message": "Session not found"}})
        elif method == "session/prompt":
            session = p["sessionId"]
            text = "".join(block.get("text", "") for block in p.get("prompt") or [])
            send({"jsonrpc": "2.0", "id": "perm-1", "method": "session/request_permission",
                  "params": {"sessionId": session, "toolCall": {"toolCallId": "call-1", "kind": "execute"},
                             "options": [{"optionId": "once", "kind": "allow_once", "name": "Allow"},
                                         {"optionId": "always", "kind": "allow_always", "name": "Always allow"},
                                         {"optionId": "no", "kind": "reject_once", "name": "Reject"}]}})
            answer = {}
            for reply in sys.stdin:
                answer = json.loads(reply)
                if answer.get("id") == "perm-1" and "method" not in answer:
                    break
            record({"method": method, "session": session, "prompt": text, "cwd": cwd.get(session),
                    "permission": (answer.get("result") or {}).get("outcome"),
                    "env": {k: os.environ.get(k) for k in GRANTED}})
            marker = os.environ["FAKE_LOG"] + ".hung"
            if "HANG" in text and not os.path.exists(marker):
                open(marker, "w").close()
                chunk(session, "Working on it")
                while os.getppid() == parent:
                    time.sleep(0.1)
                return
            if "PUBLISH" in text and cwd.get(session):      # a deliverable the runner publishes as a Tico file
                os.makedirs(os.path.join(cwd[session], "reports"), exist_ok=True)
                with open(os.path.join(cwd[session], "reports", "acme.md"), "w") as report:
                    report.write("# Acme report\n")
            reply = "Acme reply."
            if os.environ.get("ACME_API_KEY"):
                reply += " The key is " + os.environ["ACME_API_KEY"]
            for part in (reply[:5], reply[5:]):
                chunk(session, part)
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
        elif rid is not None and method:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown method"}})


if __name__ == "__main__":
    main()
