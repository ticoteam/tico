"""A stand-in for Meta's `muse` CLI (Muse Code), for the runner conformance suite (runner/tests/test_conformance.py).

`--version` answers as Muse Code 1.4.7 does; `exec --json` reads `--prompt-file` and writes the JSONL events
runner-rs/src/hosts/musecode.rs reads (the shapes recorded in runner/tests/fixtures/musecode/echo.jsonl): two text
deltas, one model completion's usage and a completed terminal with the final text. Like the real CLI, a turn with
`--session-id` first makes that session's folder under $XDG_DATA_HOME/muse/sessions (~/.local/share without it)
and fails at once when it cannot. Every call is appended to
$FAKE_LOG as one JSON line (argv, the prompt, the granted variables). A prompt containing HANG streams one delta
and then waits, once per $FAKE_LOG, until its parent goes away."""
import json
import os
import sys
import time

GRANTED = ("ACME_API_KEY", "HUB_TOKEN", "HUB_API_URL", "HUB_BOT", "TICO_CRED_SOCKET", "MUSE_NO_AUTO_UPDATE", "XDG_DATA_HOME")


def send(kind, payload):
    sys.stdout.write(json.dumps({"schema_version": 1, "payload_type": kind, "payload_schema_version": 1, "payload": payload}) + "\n")
    sys.stdout.flush()


def flag(args, name):
    return args[args.index(name) + 1] if name in args else None


def main():
    args = sys.argv[1:]
    if args == ["--version"]:
        print("Muse Code 1.4.7 (1.4.7-R6101.1)")
        return
    if args[:1] != ["exec"]:
        sys.exit(2)
    with open(flag(args, "--prompt-file"), encoding="utf-8") as prompt:
        text = prompt.read()
    with open(os.environ["FAKE_LOG"], "a") as log:
        log.write(json.dumps({"harness": "musecode", "argv": args, "prompt": text,
                              "env": {k: os.environ.get(k) for k in GRANTED}}) + "\n")
    session = flag(args, "--session-id")
    if session:
        data = os.environ.get("XDG_DATA_HOME") or os.path.join(os.environ["HOME"], ".local", "share")
        lock = os.path.join(data, "muse", "sessions", time.strftime("%Y/%m/%d", time.gmtime()), session)
        try:
            os.makedirs(lock, exist_ok=True)
        except OSError as e:
            sys.stderr.write(f"--session-id requires session logging: failed to create session lock directory {lock}: {e}\n")
            sys.exit(1)
    parent = os.getppid()
    send("run.lifecycle.started", {"kind": "run_started", "prompt": text})
    marker = os.environ["FAKE_LOG"] + ".hung"
    if "HANG" in text and not os.path.exists(marker):
        open(marker, "w").close()
        send("run.output.delta", {"kind": "run_output_delta", "text": "Working on it"})
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
        send("run.output.delta", {"kind": "run_output_delta", "text": chunk})
    send("run.model.completed", {"usage": {"inputTokens": 7, "cacheReadTokens": 3, "outputTokens": 2}})
    send("run.terminal.completed", {"kind": "run_terminal", "terminal": "completed", "text": reply, "reason": None})


if __name__ == "__main__":
    main()
