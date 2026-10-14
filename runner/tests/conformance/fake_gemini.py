"""A stand-in for the Gemini CLI, for the runner conformance suite (runner/tests/test_conformance.py).

`--version` answers as Gemini CLI 0.40.0. `-p ""` reads the prompt from stdin and speaks stream-json the way
runner/hosts/gemini.py reads it: `init`, assistant text deltas, then a `result` with stats for the model asked
for. Sessions live in $GEMINI_CLI_HOME, so `--resume` of one this home never saw fails before any event, as the CLI
does. Every call is appended to $FAKE_LOG as one JSON line (argv, the prompt, the granted variables). A prompt
containing HANG streams one delta and then waits, once per $FAKE_LOG, until its parent goes away."""
import json
import os
import sys
import time

GRANTED = ("ACME_API_KEY", "HUB_TOKEN", "HUB_API_URL", "HUB_BOT", "TICO_CRED_SOCKET", "GEMINI_API_KEY",
           "GEMINI_CLI_HOME", "GEMINI_CLI_SYSTEM_SETTINGS_PATH")


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def record(entry):
    with open(os.environ["FAKE_LOG"], "a") as log:
        log.write(json.dumps(entry) + "\n")


def flag(name):
    args = sys.argv[1:]
    return args[args.index(name) + 1] if name in args else None


def main():
    args = sys.argv[1:]
    if args == ["--version"]:
        print("0.40.0")
        return
    parent = os.getppid()
    model = flag("--model")
    session = flag("--session-id") or flag("--resume")
    text = sys.stdin.read()
    record({"harness": "gemini", "argv": args, "prompt": text, "env": {k: os.environ.get(k) for k in GRANTED}})
    sessions = os.path.join(os.environ["GEMINI_CLI_HOME"], "fake-sessions")
    known = open(sessions).read().split() if os.path.exists(sessions) else []
    if flag("--resume") and session not in known:
        sys.stderr.write(f'Error resuming session: Invalid session identifier "{session}".\n')
        sys.exit(1)
    with open(sessions, "a") as out:
        out.write(session + "\n")
    send({"type": "init", "session_id": session, "model": model})
    marker = os.environ["FAKE_LOG"] + ".hung"
    if "HANG" in text and not os.path.exists(marker):
        open(marker, "w").close()
        send({"type": "message", "role": "assistant", "content": "Working on it", "delta": True})
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
    for chunk in (reply[:5], reply[5:]):
        send({"type": "message", "role": "assistant", "content": chunk, "delta": True})
    send({"type": "result", "status": "success", "stats": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
          "cached": 3, "models": {model: {"total_tokens": 12}}}})


if __name__ == "__main__":
    main()
