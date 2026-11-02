"""A stand-in for Cursor's `cursor-agent`, for the runner conformance suite (runner_support/tests/test_conformance.py).

`--version` answers as 2026.09.28; `status` says it is logged in; `create-chat` prints a new chat id. `-p
--output-format stream-json` reads the prompt from stdin and writes the lines runner-rs/src/hosts/cursor.rs reads
(runner-rs/tests/fixtures/cursor/turn.jsonl): `system init`, an `assistant` message, and a `result` with its usage.
Every turn is appended to $FAKE_LOG as one JSON line (argv, the prompt, the granted variables). A prompt
containing HANG writes one assistant message and then waits, once per $FAKE_LOG, until its parent goes away."""
import json
import os
import sys
import time
import uuid

GRANTED = ("ACME_API_KEY", "HUB_TOKEN", "HUB_API_URL", "HUB_BOT", "TICO_CRED_SOCKET", "CURSOR_API_KEY")


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def flag(args, name):
    return args[args.index(name) + 1] if name in args else None


def main():
    args = sys.argv[1:]
    if args == ["--version"]:
        print("2026.09.28-a1b2c3d")
        return
    if args == ["status"]:
        print("Logged in as ops@acme.invalid")
        return
    if args == ["create-chat"]:
        print(uuid.uuid4())
        return
    parent = os.getppid()
    text = sys.stdin.read()
    chat = flag(args, "--resume")
    with open(os.environ["FAKE_LOG"], "a") as log:
        log.write(json.dumps({"harness": "cursor", "argv": args, "prompt": text, "cwd": os.getcwd(),
                              "env": {k: os.environ.get(k) for k in GRANTED}}) + "\n")
    send({"type": "system", "subtype": "init", "apiKeySource": "login", "cwd": os.getcwd(), "session_id": chat,
          "model": "Auto", "permissionMode": "default"})
    marker = os.environ["FAKE_LOG"] + ".hung"
    if "HANG" in text and not os.path.exists(marker):
        open(marker, "w").close()
        send({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Working on it"}]}})
        while os.getppid() == parent:
            time.sleep(0.1)
        return
    if "PUBLISH" in text:                    # a deliverable the runner publishes as a Tico file
        os.makedirs("reports", exist_ok=True)
        with open(os.path.join("reports", "acme.md"), "w") as report:
            report.write("# Acme report\n")
    reply = "Acme reply."
    if os.environ.get("ACME_API_KEY"):
        reply += " The key is " + os.environ["ACME_API_KEY"]
    send({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}, "session_id": chat})
    send({"type": "result", "subtype": "success", "is_error": False, "result": reply, "session_id": chat,
          "usage": {"inputTokens": 7, "outputTokens": 2, "cacheReadTokens": 3, "cacheWriteTokens": 0}})


if __name__ == "__main__":
    main()
