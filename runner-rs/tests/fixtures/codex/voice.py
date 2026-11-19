#!/usr/bin/env python3
"""The stdio app server used by the call runner test."""

import json
import socket
import sys
from pathlib import Path

root = Path(__file__).parent
pending = None


def send(message):
    print(json.dumps(message), flush=True)


def notify(method, **params):
    send({"method": method, "params": params})


def tokens(thread, turn, count):
    last = {"inputTokens": 100, "cachedInputTokens": 25, "outputTokens": 4}
    total = {key: count * value for key, value in last.items()}
    notify("thread/tokenUsage/updated", threadId=thread, turnId=turn, tokenUsage={"last": last, "total": total})


for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method, p = msg["method"], msg["params"]
    with (root / "calls.jsonl").open("a") as log:
        log.write(json.dumps([method, p]) + "\n")

    def reply(**result):
        send({"id": msg["id"], "result": result})

    if method == "thread/start":
        reply(thread={"id": "call-thread"})
    elif method == "thread/resume":
        assert p["threadId"] == "call-thread"
        assert (root / "turn-count").is_file()
        reply(thread={"id": p["threadId"]})
    elif method == "turn/start":
        counter = root / "turn-count"
        n = int(counter.read_text()) + 1 if counter.exists() else 1
        counter.write_text(str(n))
        thread, turn = p["threadId"], f"codex-turn-{n}"
        prompt = p["input"][0]["text"]
        event = {"threadId": thread, "turnId": turn}
        reply(turn={"id": turn})
        notify("turn/started", threadId=thread, turn={"id": turn})
        if prompt.endswith(("Stall", "End the call")):
            pending = (thread, turn, n, prompt.endswith("Stall"))
            delta = "Stalled" if pending[-1] else "Ending"
            notify("item/agentMessage/delta", **event, itemId=turn, delta=delta)
            continue
        text = "READY" if "Reply exactly READY" in prompt else "Agreed. Tomorrow."
        if prompt.endswith("First question"):
            notify("item/agentMessage/delta", **event, itemId=turn, delta="Agreed. ")
            with socket.create_connection(("127.0.0.1", int((root / "stream-gate").read_text()))) as gate:
                assert gate.recv(1) == b"y"
            notify("item/agentMessage/delta", **event, itemId=turn, delta="Tomorrow.")
        else:
            notify("item/agentMessage/delta", **event, itemId=turn, delta=text)
        notify(
            "item/completed", **event, item={"type": "agentMessage", "id": turn, "phase": "final_answer", "text": text}
        )
        tokens(thread, turn, n)
        notify("turn/completed", threadId=thread, turn={"id": turn, "status": "completed"})
    elif method == "turn/interrupt":
        assert pending is not None
        thread, turn, n, crash = pending
        assert p == {"threadId": thread, "turnId": turn}
        reply()
        tokens(thread, turn, n)
        if crash:
            sys.exit(1)
        notify("turn/completed", threadId=thread, turn={"id": turn, "status": "interrupted"})
        pending = None
    else:
        reply()
