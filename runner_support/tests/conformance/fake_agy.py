"""A stand-in for the Antigravity `agy` CLI, for the runner conformance suite (runner_support/tests/test_conformance.py).

`--version` answers as agy 1.5.15, and `models` lists its models (the readiness sign-in check). `acp` speaks ACP
(JSON-RPC 2.0, one message per line) the way runner-rs/src/hosts/antigravity.rs drives it: `initialize`,
`authenticate`, `session/resume` of a conversation this home began or `session/new`, the model and thinking level as
config options (a session starts on Hydrogen at low), and `session/prompt`, which streams two message chunks and
answers with its stopReason and that prompt's usage. Every turn is appended to $FAKE_LOG as one JSON line (argv, pid,
the conversation and the one it resumed, the model and level, the prompt, the granted variables and the token
HUB_TOKEN held). A prompt containing HANG streams one chunk and answers nothing more, once per $FAKE_LOG; a
notification (`session/cancel`) is not answered. The runner keeps the process between a thread's turns; it exits
when stdin closes."""

import json
import os
import sys
import uuid

GRANTED = ("ACME_API_KEY", "HUB_TOKEN", "HUB_API_URL", "HUB_BOT", "TICO_CRED_SOCKET")


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def record(entry):
    with open(os.environ["FAKE_LOG"], "a") as log:
        log.write(json.dumps(entry) + "\n")


def lease_token():
    value = os.environ.get("HUB_TOKEN") or ""
    if not value.startswith("tico-file:"):
        return value
    with open(value[len("tico-file:") :]) as token:
        return token.read()


def chunk(session, text):
    send(
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": session,
                "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}},
            },
        }
    )


def main():
    args = sys.argv[1:]
    if args == ["--version"]:
        print("1.5.15")
        return
    if args == ["models"]:  # the sign-in check (harnesses/antigravity.toml)
        print("gemini-4-neon\ngemini-4-hydrogen")
        return
    home = args[args.index("--gemini-dir") + 1]
    known = os.path.join(home, ".fake-agy-conversations")
    state = {"model": "gemini-4-hydrogen", "level": "low", "session": None, "resumed": None}

    def options():
        models = [{"value": "gemini-4-neon", "name": "Neon"}, {"value": "gemini-4-hydrogen", "name": "Hydrogen"}]
        levels = [{"value": level, "name": level} for level in ("low", "medium", "high", "xhigh")]
        return [
            {"id": "model", "category": "model", "type": "select", "currentValue": state["model"], "options": models},
            {
                "id": "level",
                "category": "thought_level",
                "type": "select",
                "currentValue": state["level"],
                "options": levels,
            },
        ]

    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        method, rid, p = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method is None or rid is None:
            continue
        if method == "initialize":
            result = {
                "protocolVersion": 1,
                "agentCapabilities": {"loadSession": True, "sessionCapabilities": {"resume": {}}},
            }
            send({"jsonrpc": "2.0", "id": rid, "result": result})
        elif method == "session/resume":
            begun = open(known).read().split() if os.path.exists(known) else []
            if p.get("sessionId") not in begun:
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": "Conversation not found"}})
                continue
            state["session"] = state["resumed"] = p["sessionId"]
            send({"jsonrpc": "2.0", "id": rid, "result": {"configOptions": options()}})
        elif method == "session/new":
            state["session"] = str(uuid.uuid4())
            os.makedirs(home, exist_ok=True)
            with open(known, "a") as out:
                out.write(state["session"] + "\n")
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": state["session"], "configOptions": options()}})
        elif method == "session/set_config_option":
            state["model" if p["configId"] == "model" else "level"] = p["value"]
            send({"jsonrpc": "2.0", "id": rid, "result": {"configOptions": options()}})
        elif method == "session/prompt":
            text = p["prompt"][0]["text"]
            session = state["session"]
            record(
                {
                    "harness": "antigravity",
                    "argv": args,
                    "pid": os.getpid(),
                    "conversation": session,
                    "resumed": state["resumed"],
                    "model": state["model"],
                    "level": state["level"],
                    "prompt": text,
                    "token": lease_token(),
                    "env": {k: os.environ.get(k) for k in GRANTED},
                }
            )
            marker = os.environ["FAKE_LOG"] + ".hung"
            if "HANG" in text and not os.path.exists(marker):
                open(marker, "w").close()
                chunk(session, "Working on it")
                continue
            if "PUBLISH" in text:  # a deliverable the runner publishes as a Tico file
                os.makedirs("reports", exist_ok=True)
                with open(os.path.join("reports", "acme.md"), "w") as report:
                    report.write("# Acme report\n")
            reply = "Acme reply."
            if os.environ.get("ACME_API_KEY"):
                reply += " The key is " + os.environ["ACME_API_KEY"]
            for piece in (reply[:5], reply[5:]):
                chunk(session, piece)
            usage = {"inputTokens": 10, "outputTokens": 2, "cachedReadTokens": 3}
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn", "usage": usage}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {}})


if __name__ == "__main__":
    main()
