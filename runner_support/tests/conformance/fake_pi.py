"""A stand-in for the Pi coding agent (`pi`), for the runner conformance suite (runner_support/tests/test_conformance.py).

`--version` answers as pi 0.70.0. `--mode rpc` stays up and reads JSON commands from stdin, as pi does: each
`prompt` gets its reply and then the JSON lines runner-rs/src/hosts/pi.rs reads (runner-rs/tests/fixtures/pi/turn.jsonl):
two text deltas, the assistant message's end with its usage, `turn_end`, `agent_end` and `agent_settled`. Every prompt
is appended to $FAKE_LOG as one JSON line (argv, the prompt, the granted variables, the pid), and every steer as
{"harness": "pi", "steer": text}. A prompt containing HANG streams one delta and then waits, once per $FAKE_LOG, until
an `abort` or its parent goes away; SLOW streams one delta and answers after $FAKE_SLOW seconds (2), with what was
steered in meanwhile delivered first, as pi does before its next model call."""
import json
import os
import select
import signal
import sys
import time

GRANTED = ("ACME_API_KEY", "HUB_TOKEN", "HUB_API_URL", "HUB_BOT", "TICO_CRED_SOCKET", "OPENROUTER_API_KEY")


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def record(entry):
    with open(os.environ["FAKE_LOG"], "a") as log:
        log.write(json.dumps(entry) + "\n")


def delta(text):
    send({"type": "message_update", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
          "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": text}})


class Pi:
    def __init__(self, args):
        self.args, self.parent, self.queue, self.aborted, self.buffer = args, os.getppid(), [], False, b""

    def reply(self, command, data=None, error=None):
        message = {"type": "response", "id": command.get("id"), "command": command["type"], "success": error is None}
        if error is not None:
            message["error"] = error
        elif data is not None:
            message["data"] = data
        send(message)

    def queued(self):
        send({"type": "queue_update", "steering": list(self.queue), "followUp": []})

    def read(self, wait=None):
        """The next command, or None after `wait` seconds without one; exits when stdin closes."""
        while b"\n" not in self.buffer:
            if wait is not None and not select.select([0], [], [], wait)[0]:
                return None
            chunk = os.read(0, 65536)
            if not chunk:
                sys.exit(0)
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line)

    def handle(self, command):
        kind = command["type"]
        if kind == "prompt":
            self.prompt(command)
        elif kind == "steer":
            record({"harness": "pi", "steer": command["message"]})
            self.queue.append(command["message"])
            self.reply(command, {"disposition": "queued"})
            self.queued()
        elif kind == "clear_queue":
            self.reply(command, {"steering": list(self.queue), "followUp": []})
            self.queue.clear()
            self.queued()
        elif kind == "abort":
            self.aborted = True
            self.reply(command)
        elif kind == "compact":
            send({"type": "compaction_start", "reason": "manual"})
            result = {"summary": "Earlier work.", "tokensBefore": 180000, "estimatedTokensAfter": 12000}
            send({"type": "compaction_end", "reason": "manual", "aborted": False, "willRetry": False, "result": result})
            self.reply(command, result)
        else:
            self.reply(command)

    def until(self, done, seconds=None):
        """Takes the commands that come until `done()` (or `seconds` pass)."""
        end = None if seconds is None else time.time() + seconds
        while not done() and (end is None or time.time() < end):
            command = self.read(0.1)
            if command:
                self.handle(command)

    def prompt(self, command):
        text = command["message"]
        record({"harness": "pi", "argv": self.args, "prompt": text, "cwd": os.getcwd(), "pid": os.getpid(),
                "env": {k: os.environ.get(k) for k in GRANTED}})
        self.reply(command, {"disposition": "started"})
        send({"type": "agent_start"})
        self.aborted = False
        marker = os.environ["FAKE_LOG"] + ".hung"
        if "HANG" in text and not os.path.exists(marker):
            open(marker, "w").close()
            delta("Working on it")
            self.until(lambda: self.aborted or os.getppid() != self.parent)
            if os.getppid() != self.parent:
                sys.exit(0)
            send({"type": "agent_end", "messages": []})
            send({"type": "agent_settled", "aborted": True})
            return
        if "SLOW" in text:
            delta("Working on it")
            self.until(lambda: self.aborted, float(os.environ.get("FAKE_SLOW") or 2))
            for steered in self.queue:
                send({"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": steered}]}})
            self.queue.clear()
            self.queued()
        if "PUBLISH" in text:                    # a deliverable the runner publishes as a Tico file
            os.makedirs("reports", exist_ok=True)
            with open(os.path.join("reports", "acme.md"), "w") as report:
                report.write("# Acme report\n")
        reply = "Acme reply."
        if os.environ.get("ACME_API_KEY"):
            reply += " The key is " + os.environ["ACME_API_KEY"]
        for chunk in (reply[:5], reply[5:]):
            delta(chunk)
        message = {"role": "assistant", "content": [{"type": "text", "text": reply}],
                   "usage": {"input": 7, "output": 2, "cacheRead": 3, "cacheWrite": 0, "totalTokens": 12}}
        send({"type": "message_end", "message": message})
        send({"type": "turn_end", "message": message, "toolResults": []})
        send({"type": "agent_end", "messages": []})
        send({"type": "agent_settled", "aborted": False})


def main():
    args = sys.argv[1:]
    if args == ["--version"]:
        print("0.70.0")
        return
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    pi = Pi(args)
    while True:
        pi.handle(pi.read())


if __name__ == "__main__":
    main()
