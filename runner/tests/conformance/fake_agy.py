"""A stand-in for the Antigravity `agy` CLI, for the runner conformance suite (runner/tests/test_conformance.py).

`--version` answers as agy 1.3.5. With `--input-format stream-json` it is one persistent process that runs a turn
for each user line on stdin, the way runner/hosts/antigravity.py reads it: `init` with the conversation id, text
deltas, then a `result` whose usage is the process's running total. Every turn is appended to $FAKE_LOG as one JSON
line (argv, pid, the prompt, the granted variables and the token HUB_TOKEN's file held at that moment). A prompt
containing HANG streams one delta and answers nothing more, once per $FAKE_LOG; the process exits when stdin closes."""
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
    with open(value[len("tico-file:"):]) as token:
        return token.read()


def main():
    args = sys.argv[1:]
    if args == ["--version"]:
        print("1.3.5")
        return
    conversation = args[args.index("--conversation") + 1] if "--conversation" in args else str(uuid.uuid4())
    turns = 0
    for line in sys.stdin:
        try:
            text = json.loads(line)["message"]["content"]
        except (ValueError, KeyError, TypeError):
            continue
        turns += 1
        record({"harness": "antigravity", "argv": args, "pid": os.getpid(), "prompt": text, "token": lease_token(),
                "env": {k: os.environ.get(k) for k in GRANTED}})
        send({"event": "init", "conversation_id": conversation})
        marker = os.environ["FAKE_LOG"] + ".hung"
        if "HANG" in text and not os.path.exists(marker):
            open(marker, "w").close()
            send({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "Working on it"}})
            continue
        if "PUBLISH" in text:                    # a deliverable the runner publishes as a Tico file
            os.makedirs("reports", exist_ok=True)
            with open(os.path.join("reports", "acme.md"), "w") as report:
                report.write("# Acme report\n")
        reply = "Acme reply."
        if os.environ.get("ACME_API_KEY"):
            reply += " The key is " + os.environ["ACME_API_KEY"]
        for chunk in (reply[:5], reply[5:]):
            send({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": chunk}})
        send({"event": "result", "result": {"status": "SUCCESS", "response": reply, "conversation_id": conversation,
              "usage": {"input_tokens": 10 * turns, "output_tokens": 2 * turns, "cache_read_tokens": 3 * turns}}})


if __name__ == "__main__":
    main()
