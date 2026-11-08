"""A stand-in for the `claude` CLI, for the runner conformance suite (runner/tests/test_conformance.py).

`--version`, `--help` and `auth status --json` answer as Claude Code 2.1.296 does; `-p` speaks stream-json the way
runner-rs/src/hosts/claude.rs reads it: each user message is replayed, then a text delta, the assistant message and a
`result`. Every call is appended to $FAKE_LOG as one JSON line (argv, the prompt, cwd, the granted variables and
homes, when it started and finished); a prompt containing REMEMBER leaves a file in memory/. Each turn reports its
usage windows as Claude Code 2.1.301 does (`rate_limit_event` with `unifiedWindows`, 53% of five hours, 17% of the week). A prompt containing HANG
streams one delta and then waits, once per $FAKE_LOG, until its parent goes away; SLOW streams one delta and answers
after $FAKE_SLOW seconds (2), so a steer written meanwhile is read (replayed) and answered after it, in the same
process, as Claude Code does. RECALL answers with the code word only when the prompt itself holds it ("code word is X"),
as a model with no other memory of the conversation would. With $FAKE_CLAUDE_LIMIT set, every turn ends at the session
limit as Claude Code's does (runner/tests/fixtures/claude/session-limit.jsonl): a rejected `rate_limit_event`, the
synthetic `rate_limit` message and an error `result`, having used no tokens (or some, when $FAKE_LIMIT_SPENDS names
claude)."""
import json
import os
import re
import sys
import time
from memory_tidy_fixture import custody, reply as packet_reply

GRANTED = ("ACME_API_KEY", "HUB_TOKEN", "HUB_API_URL", "HUB_BOT", "TICO_CRED_SOCKET", "HOME", "CODEX_HOME")


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def record(entry):
    with open(os.environ["FAKE_LOG"], "a") as log:
        log.write(json.dumps(entry) + "\n")


def remember(cwd, text):
    """A prompt containing REMEMBER leaves a lesson uncommitted in memory/, as a bot does."""
    if "REMEMBER" in text:
        os.makedirs(os.path.join(cwd, "memory"), exist_ok=True)
        with open(os.path.join(cwd, "memory", "lesson-%d.md" % os.getpid()), "w") as note:
            note.write("Keep the product tests fast.\n")


def flag(name):
    args = sys.argv[1:]
    return args[args.index(name) + 1] if name in args else None


def main():
    args = sys.argv[1:]
    if args == ["--version"]:
        print("2.1.296 (Claude Code)")
        return
    if args == ["--help"]:
        print("Usage: claude [options]\n  --autocompact <tokens>  compact the conversation at this many tokens")
        return
    if args[:2] == ["auth", "status"]:
        # As Claude Code reports it, with an id and a token-shaped field the runner must not pass on.
        print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": "ops@acme.example", "orgId": "acme-org-0001",
                          "orgName": "Acme Ltd", "subscriptionType": "max", "oauthToken": "sk-ant-oat01-acmefakeacmefake"}))
        return
    session = flag("--session-id") or flag("--resume")
    parent = os.getppid()
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        text = " ".join(part.get("text", "") for part in message["message"]["content"])
        record({"harness": "claude", "argv": args, "prompt": text, "cwd": os.getcwd(),
                "env": {k: os.environ.get(k) for k in GRANTED}, "at": time.time(), **custody(os.getcwd())})
        remember(os.getcwd(), text)
        send({**message, "isReplay": True})
        send({"type": "system", "subtype": "init", "session_id": session, "model": "claude-sonnet-5", "tools": []})
        send({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "msg_1"}}, "parent_tool_use_id": None})
        if os.environ.get("FAKE_CLAUDE_LIMIT"):
            limit = "You've hit your session limit · resets 12:40am (UTC)"
            send({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour",
                                                                  "resetsAt": int(time.time()) + 3600}})
            send({"type": "assistant", "message": {"id": "msg_2", "model": "<synthetic>", "role": "assistant",
                  "content": [{"type": "text", "text": limit}]}, "parent_tool_use_id": None, "error": "rate_limit"})
            send({"type": "result", "subtype": "success", "is_error": True, "result": limit, "session_id": session,
                  "usage": ({"input_tokens": 7, "output_tokens": 2} if "claude" in os.environ.get("FAKE_LIMIT_SPENDS", "")
                            else {"input_tokens": 0, "output_tokens": 0})})
            continue
        marker = os.environ["FAKE_LOG"] + ".hung"
        if "HANG" in text and not os.path.exists(marker):
            open(marker, "w").close()
            send({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                  "delta": {"type": "text_delta", "text": "Working on it"}}, "parent_tool_use_id": None})
            while os.getppid() == parent:
                time.sleep(0.1)
            return
        if "SLOW" in text:
            send({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                  "delta": {"type": "text_delta", "text": "Working on it"}}, "parent_tool_use_id": None})
            time.sleep(float(os.environ.get("FAKE_SLOW") or 2))
        if "PUBLISH" in text:                    # a deliverable the runner publishes as a Tico file
            os.makedirs("reports", exist_ok=True)
            with open(os.path.join("reports", "acme.md"), "w") as report:
                report.write("# Acme report\n")
        reply = "Acme reply."
        if os.environ.get("FAKE_MEMORY_TIDY"):
            reply = packet_reply(text)
        if "RECALL" in text:
            word = re.search(r"code word is (\w+)", text)
            reply = f"The code word is {word.group(1)}." if word else "I do not know the code word."
        if os.environ.get("ACME_API_KEY"):
            reply += " The key is " + os.environ["ACME_API_KEY"]
        for chunk in (reply[:5], reply[5:]):
            send({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                  "delta": {"type": "text_delta", "text": chunk}}, "parent_tool_use_id": None})
        send({"type": "assistant", "message": {"id": "msg_1", "role": "assistant", "content": [{"type": "text", "text": reply}]},
              "parent_tool_use_id": None})
        now = int(time.time())
        send({"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "resetsAt": now + 3 * 3600, "rateLimitType": "five_hour", "overageStatus": "rejected",
            "isUsingOverage": False, "unifiedWindows": {"five_hour": {"utilization": 0.53, "resetsAt": now + 3 * 3600},
                                                        "seven_day": {"utilization": 0.17, "resetsAt": now + 3 * 86400}}}})
        record({"harness": "claude", "finished": text, "at": time.time()})
        send({"type": "result", "subtype": "success", "is_error": False, "result": reply, "stop_reason": "end_turn",
              "session_id": session, "total_cost_usd": 0.01,
              "usage": {"input_tokens": 7, "cache_read_input_tokens": 3, "output_tokens": 2}})


if __name__ == "__main__":
    main()
