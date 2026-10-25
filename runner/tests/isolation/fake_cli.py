#!/usr/bin/env python3
"""A stand-in for every model CLI, for test_isolation_docker.py: mounted as each harness's executable.

/fake/spec.json, written by the test from runner/harnesses/*.toml, says per executable which wire format it speaks
(the manifest's `host`), what `--version` prints and how its sign-in check is answered. A turn speaks just enough
of that format for the runner to finish it, and its final answer is `probe <json>`: what bot code could do from
where the runner started it (its user and capabilities, whether it reaches the runner's registration, state and
legacy secrets, and a commit and pull in its working tree as itself).
"""
import base64
import json
import time
import os
import subprocess
import sys
import uuid
from pathlib import Path

HOME = Path("/home/runner")
NAME = Path(sys.argv[0]).name
SPEC = json.loads(Path("/fake/spec.json").read_text())[NAME]


def readable(path):
    try:
        if os.path.isdir(path):
            os.listdir(path)
        else:
            Path(path).read_bytes()
        return True
    except OSError:
        return False


def writable(directory):
    try:
        mark = Path(directory) / f".sibling-{NAME}-{uuid.uuid4().hex[:8]}"
        mark.write_text("from another bot's turn\n")
        mark.unlink()
        return True
    except OSError:
        return False


def siblings(cwd):
    """Other bots' checkouts and task worktrees: whether this turn can list or write any of them."""
    mine = next((part for part in Path(cwd).parts if part.startswith("bot-")), None)
    workspace = HOME / "workspace"
    others = [p for p in workspace.glob("bot-*") if p.name != mine]
    others += [p for lane in (workspace / "sessions").glob("bot-*") if lane.name != mine for p in lane.iterdir()]
    return {"sibling_enter": any(readable(p) for p in others), "sibling_write": any(writable(p) for p in others),
            "sibling_rename": any(renamable(p) for p in others), "siblings_seen": len(others)}


def renamable(path):
    """Whether this turn can move a sibling's checkout aside (to put its own in its place); moved back if so."""
    aside = path.with_name(path.name + ".probe")
    try:
        os.rename(path, aside)
    except OSError:
        return False
    os.rename(aside, path)
    return True


def sibling_credentials():
    folder = HOME / "workspace/credential-sibling"
    if not folder.exists():
        return {}
    # A missing/stale fixture is a test failure, never a vacuous denial.
    pid = int((folder / "pid").read_text())
    present = Path(f"/proc/{pid}").exists()
    try:
        environment = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        environment = []
    return {"credential_sibling_present": present, "sibling_granted_secret": readable(folder / "granted-secret"),
            "sibling_tico_token": b"HUB_TOKEN=synthetic-sibling-tico" in environment,
            "sibling_github_token": b"GH_TOKEN=synthetic-sibling-github" in environment}


def git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)
    return "ok" if done.returncode == 0 else "failed: " + (done.stderr.strip().splitlines() or ["?"])[-1][:160]


def probe(cwd):
    cwd = cwd or os.getcwd()
    caps = next(line.split(":", 1)[1].strip() for line in Path("/proc/self/status").read_text().splitlines()
                if line.startswith("CapEff:"))
    # Where it ran, relative to the workspace and without slashes: the runner takes local file links out of a reply.
    where = os.path.relpath(cwd, HOME / "workspace").replace(os.sep, " > ")
    result = {"harness": NAME, "uid": os.geteuid(), "caps": caps, "where": where,
              "registration": readable(HOME / "runner.json"),
              "state": any(readable(p) for p in HOME.glob("state-*")),
              "secrets": readable(HOME / "workspace/secrets/_shared.env"), **siblings(cwd), **sibling_credentials()}
    if SPEC["host"] in {"codex", "claude"} and os.geteuid() != 10003:
        variable, basename = ("CODEX_HOME", "auth.json") if SPEC["host"] == "codex" else ("CLAUDE_CONFIG_DIR", ".credentials.json")
        private = Path(os.environ[variable])
        result["private_signin"] = (private.is_relative_to(Path(os.environ["HOME"])) and
            not (private / basename).exists() and not os.environ.get("CLAUDE_CODE_OAUTH_REFRESH_TOKEN") and
            (SPEC["host"] != "claude" or bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))))
    # Bot code's own git in its checkout: a commit, then a pull, as whatever user the runner started it as.
    note = Path(cwd) / f"turn-{NAME}-{uuid.uuid4().hex[:8]}.txt"
    try:
        note.write_text("from the turn\n")
        result["commit"] = git(cwd, "add", note.name)
        if result["commit"] == "ok":
            result["commit"] = git(cwd, "commit", "-q", "-m", f"{NAME}: a turn's commit")
    except OSError as error:
        result["commit"] = f"failed: {error.strerror}"
    result["pull"] = git(cwd, "pull", "-q", "--rebase", "origin", "main")
    return "probe " + json.dumps(result, sort_keys=True)


def say(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def lines():
    for line in sys.stdin:
        try:
            yield json.loads(line)
        except ValueError:
            continue


def codex():
    if "generate-json-schema" in sys.argv:
        return  # no native goals
    cwds = {}
    private = os.geteuid() not in {10002, 10003, 0}
    authenticated, refreshed, pending = not private, False, None
    for msg in lines():
        method, params, rid = msg.get("method"), msg.get("params") or {}, msg.get("id")
        if rid is None:
            continue
        if method == "account/read":
            if params.get("refreshToken"):
                path = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
                auth = json.loads(path.read_text())
                body = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 7200, "rotation": uuid.uuid4().hex}).encode()).decode().rstrip("=")
                auth["tokens"]["access_token"] = "synthetic." + body + ".fixture"
                auth["tokens"]["refresh_token"] = "synthetic-rotated-refresh-never-in-turn"
                path.write_text(json.dumps(auth))
            say({"id": rid, "result": {"account": {"type": "chatgpt", "email": "acme@example.test", "planType": "plus"}}})
            continue
        if method == "account/login/start":
            assert private and params["type"] == "chatgptAuthTokens"
            assert set(params) == {"type", "accessToken", "chatgptAccountId"}
            authenticated = bool(params["accessToken"]) and params["chatgptAccountId"] == "acme"
            say({"id": rid, "result": {"type": "chatgptAuthTokens"}})
            continue
        if method is None and rid == "synthetic-refresh":
            assert msg.get("result", {}).get("chatgptAccountId") == "acme", msg.get("error")
            assert set(msg["result"]) == {"accessToken", "chatgptAccountId"}
            refreshed = True
            msg = pending
            method, params, rid = msg["method"], msg["params"], msg["id"]
        if method in ("thread/start", "thread/resume"):
            assert authenticated
            thread = params.get("threadId") or str(uuid.uuid4())
            cwds[thread] = params.get("cwd")
            say({"id": rid, "result": {"thread": {"id": thread}}})
        elif method == "turn/start":
            if private and not refreshed:
                pending = msg
                say({"id": "synthetic-refresh", "method": "account/chatgptAuthTokens/refresh",
                     "params": {"reason": "unauthorized", "previousAccountId": "acme"}})
                continue
            thread, turn = params["threadId"], str(uuid.uuid4())
            text = probe(params.get("cwd") or cwds.get(thread))
            say({"id": rid, "result": {"turn": {"id": turn}}})
            say({"method": "turn/started", "params": {"threadId": thread, "turn": {"id": turn}}})
            say({"method": "item/completed", "params": {"threadId": thread, "turnId": turn, "item": {
                "type": "agentMessage", "id": "m-" + turn, "text": text, "phase": "final_answer"}}})
            say({"method": "turn/completed", "params": {"threadId": thread, "turn": {"id": turn, "status": "completed"}}})
        else:
            say({"id": rid, "result": {}})


def claude():
    if "--help" in sys.argv:
        print("Usage: claude [options]")
        return
    session = sys.argv[sys.argv.index("--session-id") + 1] if "--session-id" in sys.argv else str(uuid.uuid4())
    for msg in lines():
        if msg.get("type") != "user":
            continue
        text = probe(None)
        say({"type": "system", "subtype": "init", "session_id": session, "model": "fake", "tools": []})
        say({"type": "assistant", "message": {"id": "msg_1", "role": "assistant", "content": [{"type": "text", "text": text}]},
             "parent_tool_use_id": None})
        say({"type": "result", "subtype": "success", "is_error": False, "result": text, "stop_reason": "end_turn",
             "session_id": session, "num_turns": 1, "usage": {"input_tokens": 1, "output_tokens": 1}})
        break
    for _ in lines():
        pass


def gemini():
    sys.stdin.read()
    text = probe(None)
    say({"type": "init", "session_id": str(uuid.uuid4()), "model": "fake"})
    say({"type": "message", "role": "assistant", "content": text, "delta": True})
    say({"type": "result", "status": "success", "stats": {"total_tokens": 2, "input_tokens": 1, "output_tokens": 1}})


def antigravity():
    # A resumed conversation (`--conversation <id>`, after a restart) keeps its id, as the real CLI does.
    resumed = sys.argv[sys.argv.index("--conversation") + 1] if "--conversation" in sys.argv else None
    conversation = resumed or str(uuid.uuid4())
    for msg in lines():
        if msg.get("event") != "user":
            continue
        text = probe(None)
        say({"event": "init", "conversation_id": conversation})
        say({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": text}})
        say({"event": "result", "result": {"status": "SUCCESS", "response": text, "conversation_id": conversation,
                                           "usage": {"input_tokens": 1, "output_tokens": 1}}})


def musecode():
    session = str(uuid.uuid4())
    text = probe(None)
    base = {"schema_version": 1, "stream": {"kind": "session", "id": session}, "payload_schema_version": 1}
    say({**base, "sequence": 1, "payload_type": "run.output.delta", "payload": {"kind": "run_output_delta", "text": text}})
    say({**base, "sequence": 2, "payload_type": "run.terminal.completed",
         "payload": {"kind": "run_terminal", "terminal": "completed", "text": text, "reason": None}})


def cursor():
    if "create-chat" in sys.argv:
        print(uuid.uuid4())
        return
    sys.stdin.read()
    text = probe(None)
    session = str(uuid.uuid4())
    say({"type": "system", "subtype": "init", "session_id": session, "model": "Auto"})
    say({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}, "session_id": session})
    say({"type": "result", "subtype": "success", "is_error": False, "result": text, "session_id": session})


def pi():
    sys.stdin.read()
    text = probe(None)
    message = {"role": "assistant", "content": [{"type": "text", "text": text}], "stopReason": "stop",
               "usage": {"input": 1, "output": 1, "totalTokens": 2}}
    say({"type": "session", "version": 3, "id": str(uuid.uuid4()), "cwd": os.getcwd()})
    say({"type": "agent_start"})
    say({"type": "message_end", "message": message})
    say({"type": "turn_end", "message": message, "toolResults": []})
    say({"type": "agent_end", "messages": []})


def grok():
    session, cwd = "sess-" + uuid.uuid4().hex[:8], None
    for msg in lines():
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if rid is None or method is None:
            continue
        if method in ("session/new", "session/load"):
            cwd = params.get("cwd") or cwd   # ACP names the working folder per session, not per process
            session = params.get("sessionId") or session   # a loaded session keeps its id
            say({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": session} if method == "session/new" else None})
        elif method == "session/prompt":
            text = probe(cwd)
            say({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": params.get("sessionId") or session, "update": {
                "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}}})
            say({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
        else:
            say({"jsonrpc": "2.0", "id": rid, "result": {}})


TURNS = {"codex": codex, "claude": claude, "gemini": gemini, "antigravity": antigravity, "musecode": musecode,
         "cursor": cursor, "pi": pi, "grok": grok}


def main():
    args = sys.argv[1:]
    if args == SPEC["version_args"]:
        print(SPEC["version"])
        return
    status = SPEC.get("status") or {}
    if status.get("args") and args == status["args"]:
        if status["check"] == "json":
            print(json.dumps({status["field"]: True, status.get("method_field") or "authMethod": "fake"}))
        else:
            print(status["ready"])
        return
    if args[:1] == ["models"]:
        return
    TURNS[SPEC["host"]]()


main()
