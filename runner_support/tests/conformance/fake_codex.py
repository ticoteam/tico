"""A stand-in for the `codex` CLI, for the runner conformance suite (runner/tests/test_conformance.py):
`--version`, `login status`, `app-server generate-json-schema` (the protocol files that say goals, compact and
review are there), and `app-server` speaking the part of the JSON-RPC protocol runner-rs/src/hosts/codex.rs uses
(scripts/journey-fake-codex.py, plus token usage, native goals, steering, `account/read` and `account/rateLimits/read`).
Every thread, goal, steer, turn and rate-limit request is appended to $FAKE_LOG, with the turn's homes, cwd and granted
variables; a prompt containing REMEMBER leaves a file in that cwd's memory/. FAKE_CODEX_LOGIN=apikey signs it in with
an API key, whose plan usage the server refuses to read. FAKE_CODEX_LOGIN=file makes the sign-in $CODEX_HOME/auth.json:
`login status` says logged in only when it is there, and `login --device-auth` shows a link and a code, then waits for
the test to put `approve` (writes auth.json) or `deny` (fails) in $CODEX_HOME.

Scripted prompts: HANG streams one delta and then waits, once per $FAKE_LOG, until its parent goes away; LIMIT
fails the turn with a usage limit (after some token usage when $FAKE_LIMIT_SPENDS names codex); SLOW streams one delta and answers after $FAKE_SLOW seconds (2), or at once
after a steer, whose text the answer then carries."""
import json
import os
import queue
import sys
import threading
import time
from memory_tidy_fixture import custody, reply as packet_reply

GRANTED = ("ACME_API_KEY", "HUB_TOKEN", "HUB_API_URL", "HUB_BOT", "TICO_CRED_SOCKET", "HOME", "CODEX_HOME")


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def record(entry):
    with open(os.environ["FAKE_LOG"], "a") as log:
        log.write(json.dumps(entry) + "\n")


def schema(out):
    os.makedirs(os.path.join(out, "v2"), exist_ok=True)
    for name in ("ThreadGoalSetParams", "ThreadCompactStartParams", "ReviewStartParams", "TurnSteerParams"):
        with open(os.path.join(out, "v2", name + ".json"), "w") as f:
            f.write("{}")


def finish(thread, turn, number, reply):
    for chunk in (reply[:5], reply[5:]):
        send({"method": "item/agentMessage/delta", "params": {"threadId": thread, "turnId": turn, "delta": chunk}})
    send({"method": "item/completed", "params": {"threadId": thread, "turnId": turn,
          "item": {"type": "agentMessage", "id": "m%d" % number, "text": reply, "phase": "final_answer"}}})
    send({"method": "thread/tokenUsage/updated", "params": {"threadId": thread, "turnId": turn, "tokenUsage": {
        "total": {"inputTokens": 7 * number, "cachedInputTokens": 3 * number, "outputTokens": 2 * number, "totalTokens": 9 * number},
        "last": {"inputTokens": 7, "cachedInputTokens": 3, "outputTokens": 2, "totalTokens": 9}}}})
    record({"harness": "codex", "method": "turn/completed", "thread": thread, "turn": turn, "at": time.time()})
    send({"method": "turn/completed", "params": {"threadId": thread, "turn": {"id": turn, "status": "completed"}}})


def main():
    if "--version" in sys.argv:
        print("codex-cli 99.0.0")
        return
    home = os.environ.get("CODEX_HOME", "")
    by_file = os.environ.get("FAKE_CODEX_LOGIN") == "file"
    if sys.argv[1:3] == ["login", "status"]:
        if by_file and not os.path.exists(os.path.join(home, "auth.json")):
            print("Not logged in")
            sys.exit(1)
        print("Logged in using an API key - sk-***" if os.environ.get("FAKE_CODEX_LOGIN") == "apikey" else "Logged in using ChatGPT")
        return
    if by_file and sys.argv[1:3] == ["login", "--device-auth"]:
        print("Open this link\n  https://auth.openai.com/codex/device \nEnter this one-time code\n   ABCD-1234", flush=True)
        deadline = time.time() + 120
        while time.time() < deadline:
            if os.path.exists(os.path.join(home, "deny")):
                print("Login was not completed")
                sys.exit(1)
            if os.path.exists(os.path.join(home, "approve")):
                with open(os.path.join(home, "auth.json"), "w") as f:
                    f.write("{}")
                print("Successfully logged in")
                return
            time.sleep(0.05)
        sys.exit(1)
    if sys.argv[1:3] == ["app-server", "generate-json-schema"]:
        schema(sys.argv[sys.argv.index("--out") + 1])
        return
    if sys.argv[1:2] != ["app-server"]:
        sys.exit(2)
    lines = queue.Queue()

    def read():
        for line in sys.stdin:
            lines.put(line)
        lines.put(None)
    threading.Thread(target=read, daemon=True).start()
    threads, turns, parent, cwds = 0, 0, os.getppid(), {}
    goals = {}
    slow = None          # (thread, turn, number, due) of the turn answering later
    while True:
        try:
            line = lines.get(timeout=0.05)
        except queue.Empty:
            line = ""
        if line is None:
            return
        if slow and time.monotonic() >= slow[3]:
            finish(slow[0], slow[1], slow[2], "Acme reply.")
            slow = None
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, params, rid = msg.get("method"), msg.get("params") or {}, msg.get("id")
        if rid is None:
            continue
        thread = params.get("threadId")
        if method in ("thread/start", "thread/resume"):
            threads += 1
            thread = thread or "acme-thread-%d-%d" % (os.getpid(), threads)
            cwds[thread] = params.get("cwd") or os.getcwd()
            record({"harness": "codex", "method": method, "thread": thread})
            send({"id": rid, "result": {"thread": {"id": thread}}})
        elif method == "thread/goal/set":
            goal = {**goals.get(thread, {}), **{k: params[k] for k in ("objective", "status") if k in params}}
            goals[thread] = goal
            record({"harness": "codex", "method": method, "thread": thread, "goal": goal})
            send({"id": rid, "result": {"goal": goal}})
            send({"method": "thread/goal/updated", "params": {"threadId": thread, "goal": goal}})
        elif method == "thread/goal/clear":
            goals.pop(thread, None)
            record({"harness": "codex", "method": method, "thread": thread})
            send({"id": rid, "result": {}})
            send({"method": "thread/goal/cleared", "params": {"threadId": thread}})
        elif method == "thread/goal/get":
            send({"id": rid, "result": {"goal": goals.get(thread)}})
        elif method == "account/read":
            # Not logged: the runner reads it in the background on every computer with codex signed in.
            if params.get("refreshToken") is not False:
                send({"id": rid, "error": {"code": -32600, "message": "refreshToken must be false"}})
                continue
            account = ({"type": "apiKey"} if os.environ.get("FAKE_CODEX_LOGIN") == "apikey"
                       else {"type": "chatgpt", "email": "dev@acme.example", "planType": "pro"})
            send({"id": rid, "result": {"account": account, "requiresOpenaiAuth": True}})
        elif method == "account/rateLimits/read":
            # As a ChatGPT login answers it: the 5-hour window first, the weekly one second, resets in epoch seconds.
            record({"harness": "codex", "method": method})
            if os.environ.get("FAKE_CODEX_LOGIN") == "apikey":
                send({"id": rid, "error": {"code": -32600, "message": "chatgpt authentication required to read rate limits"}})
                continue
            now = int(time.time())
            send({"id": rid, "result": {"rateLimits": {"limitId": "codex", "planType": "pro",
                  "primary": {"usedPercent": 40, "windowDurationMins": 300, "resetsAt": now + 3 * 3600},
                  "secondary": {"usedPercent": 12, "windowDurationMins": 10080, "resetsAt": now + 3 * 86400}}}})
        elif method == "turn/steer":
            text = " ".join(part.get("text", "") for part in params.get("input", []))
            record({"harness": "codex", "method": method, "thread": thread, "steer": text})
            if not slow or params.get("expectedTurnId") != slow[1]:
                send({"id": rid, "error": {"code": -32600, "message": "no active turn to steer"}})
                continue
            send({"id": rid, "result": {"turnId": slow[1]}})
            finish(slow[0], slow[1], slow[2], "Acme reply. Steered: " + text.strip().splitlines()[-1])
            slow = None
        elif method == "turn/start":
            turns += 1
            turn = "acme-turn-%d-%d" % (os.getpid(), turns)
            text = " ".join(part.get("text", "") for part in params.get("input", []))
            record({"harness": "codex", "method": method, "thread": thread, "turn": turn, "prompt": text,
                    "cwd": cwds.get(thread), "env": {k: os.environ.get(k) for k in GRANTED}, "at": time.time(), **custody(cwds.get(thread))})
            if "REMEMBER" in text and cwds.get(thread):
                # As a bot does: a lesson left uncommitted in memory/.
                os.makedirs(os.path.join(cwds[thread], "memory"), exist_ok=True)
                with open(os.path.join(cwds[thread], "memory", "lesson-%d.md" % turns), "w") as note:
                    note.write("Keep the product tests fast.\n")
            send({"id": rid, "result": {"turn": {"id": turn}}})
            send({"method": "turn/started", "params": {"threadId": thread, "turn": {"id": turn}}})
            marker = os.environ["FAKE_LOG"] + ".hung"
            if "HANG" in text and not os.path.exists(marker):
                open(marker, "w").close()
                send({"method": "item/agentMessage/delta", "params": {"threadId": thread, "turnId": turn, "delta": "Working on it"}})
                while os.getppid() == parent:
                    time.sleep(0.1)
                return
            if "LIMIT" in text:
                if "codex" in os.environ.get("FAKE_LIMIT_SPENDS", ""):
                    send({"method": "thread/tokenUsage/updated", "params": {"threadId": thread, "turnId": turn, "tokenUsage": {
                        "total": {"inputTokens": 5, "cachedInputTokens": 0, "outputTokens": 1, "totalTokens": 6},
                        "last": {"inputTokens": 5, "cachedInputTokens": 0, "outputTokens": 1, "totalTokens": 6}}}})
                send({"method": "turn/completed", "params": {"threadId": thread, "turn": {
                    "id": turn, "status": "failed", "error": {"message": "You've hit your usage limit. Try again later."}}}})
                continue
            if "SLOW" in text:
                send({"method": "item/agentMessage/delta", "params": {"threadId": thread, "turnId": turn, "delta": "Working on it"}})
                slow = (thread, turn, turns, time.monotonic() + float(os.environ.get("FAKE_SLOW") or 2))
                continue
            if "PUBLISH" in text:                # a deliverable the runner publishes as a Tico file
                os.makedirs(os.path.join(cwds.get(thread) or os.getcwd(), "reports"), exist_ok=True)
                with open(os.path.join(cwds.get(thread) or os.getcwd(), "reports", "acme.md"), "w") as report:
                    report.write("# Acme report\n")
            reply = "Acme reply."
            if os.environ.get("FAKE_MEMORY_TIDY"):
                reply = packet_reply(text)
            if os.environ.get("ACME_API_KEY"):
                reply += " The key is " + os.environ["ACME_API_KEY"]
            finish(thread, turn, turns, reply)
        else:
            send({"id": rid, "result": {}})


if __name__ == "__main__":
    main()
