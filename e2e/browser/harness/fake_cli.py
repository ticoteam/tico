#!/usr/bin/env python3
"""A stand-in for every harness CLI, for scripts/browser-harness.sh only. The runner starts it under the CLI's own name
(claude, codex, muse, gemini, grok, cursor-agent, pi, agy); it answers the version and sign-in probes the runner makes,
and for a turn it does three things: writes a dump of what the run gave it (argv, environment, the MCP servers its config
names, the files under its home), runs e2e/browser/harness/probe.mjs with that dump, which drives the run's Chrome through
the servers exactly as written, and answers the turn with one line. The probe's checks go to <HARNESS_DIR>/results.

A prompt holding `harness-run <nonce> <phase> [KEY=value ...]` is a turn; the phase is "request" (open a login card),
"status" (Chrome answers) or "probe" (the full check). Only the values in ALLOWED reach the probe. Nothing here prints a
password, a 2FA secret or a code: the probe never has them."""
import glob
import json
import os
import re
import select
import subprocess
import sys
import time
import uuid

NAME = os.path.basename(sys.argv[0])
HARNESS = {"claude": "claude-code", "codex": "codex", "muse": "muse-code", "gemini": "gemini-cli",
           "grok": "grok", "cursor-agent": "cursor-agent", "pi": "pi", "agy": "antigravity"}.get(NAME, NAME)
VERSIONS = {"claude": "2.1.0 (Claude Code)", "codex": "codex-cli 0.163.0", "muse": "muse 1.4.8",
            "gemini": "0.30.0", "grok": "0.9.0", "cursor-agent": "2026.10.01-abcdef1", "pi": "0.70.0", "agy": "1.2.0"}
# The copies in the runner's PATH are not next to the probe, so it lives at a fixed place there.
PROBE = os.environ.get("HARNESS_PROBE", "/opt/harness/probe.mjs")
ROOT = os.environ.get("HARNESS_DIR", "/home/runner/harness")
RUN = re.compile(r"harness-run (\S+) ([a-z-]+)((?: [A-Za-z_]+=\S+)*)")
ALLOWED = {"PRACTICE", "HOST", "LABEL", "OTHER"}


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def read_stdin_until(predicate, seconds):
    """Lines from stdin until one satisfies predicate; a CLI that keeps stdin open is not waited on past `seconds`."""
    seen, end = [], time.time() + seconds
    while time.time() < end:
        ready, _, _ = select.select([sys.stdin], [], [], 1)
        if not ready:
            continue
        line = sys.stdin.readline()
        if not line:
            break
        seen.append(line)
        if predicate(line):
            break
    return "".join(seen)


def files_under_home(limit=40):
    """Config and state files under HOME (names, sizes, and the text of small JSON or TOML files)."""
    found, home = {}, os.path.expanduser("~")
    for dirpath, dirnames, filenames in os.walk(home):
        dirnames[:] = [d for d in dirnames if d not in ("node_modules", ".cache") and dirpath.count(os.sep) < home.count(os.sep) + 4]
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            text = None
            if size <= 16384 and name.endswith((".json", ".toml", ".yaml", ".yml", ".env")):
                try:
                    text = open(path, encoding="utf-8", errors="replace").read()
                except OSError:
                    text = None
            found[os.path.relpath(path, home)] = {"size": size, "text": text}
            if len(found) >= limit:
                return found
    return found


def claude_servers(argv):
    for i, arg in enumerate(argv[:-1]):
        if arg == "--mcp-config":
            try:
                return json.loads(argv[i + 1]).get("mcpServers", {})
            except ValueError:
                return {"unparsed": {"raw": argv[i + 1][:200]}}
    return {}


def muse_servers(argv):
    servers = {}
    for i, arg in enumerate(argv[:-1]):
        if arg == "-c" and argv[i + 1].startswith("mcp_servers."):
            name, _, value = argv[i + 1][len("mcp_servers."):].partition("=")
            try:
                servers[name] = json.loads(value)
            except ValueError:
                servers[name] = {"unparsed": value[:200]}
    return servers


def read_prompt(argv):
    """Argument text, the text of any argument that names a file, then stdin when the CLI reads its prompt there."""
    parts = list(argv)
    for arg in argv:
        if os.path.isfile(arg):
            try:
                parts.append(open(arg, encoding="utf-8", errors="replace").read())
            except OSError:
                pass
    return " ".join(parts)


def secret_needles():
    """The values no command line may carry: the run's browser secret (the last segment of its endpoint) and the hub
    token. Read from this process's environment, which is the turn's own."""
    needles = []
    url = os.environ.get("TICO_BROWSER_URL", "")
    if url:
        needles.append(("the browser secret", url))
        needles.append(("the browser secret", url.rstrip("/").rsplit("/", 1)[-1]))
    token = os.environ.get("HUB_TOKEN", "")
    if token:
        needles.append(("HUB_TOKEN", token))
    return [(label, value) for label, value in needles if len(value) >= 8]


def command_line_leaks(needles):
    """The labels of the secrets found in any process's command line, read from /proc as any local user can read it."""
    found = set()
    for path in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(path, "rb") as handle:
                line = handle.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        for label, value in needles:
            if value in line:
                found.add(label)
    return found


def record_command_line_check(results_path, leaks):
    """One more check in the probe's result: no process's command line carried a secret while the run went on."""
    try:
        with open(results_path) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {"phase": "", "ok": False, "checks": [], "data": {}}
    checks = data.setdefault("checks", [])
    checks.append({"name": "no process command line carries the browser secret or HUB_TOKEN", "ok": not leaks,
                   "detail": ("on a command line: " + ", ".join(sorted(leaks))) if leaks else ""})
    data["ok"] = all(item.get("ok") for item in checks)
    with open(results_path, "w") as handle:
        json.dump(data, handle)


def run_probe(text, servers, argv):
    # A stream-json prompt carries its newlines as \n escapes, which would run a value on into the next words.
    # A codex turn can carry a stale harness-run line ahead of the new one: the last line is the message being answered.
    matches = list(RUN.finditer(text.replace("\\n", "\n")))
    if not matches:
        return None
    found = matches[-1]
    nonce, phase, args = found.group(1), found.group(2), found.group(3)
    values = dict(part.split("=", 1) for part in args.split())
    os.makedirs(os.path.join(ROOT, "dumps"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    dump_path = os.path.join(ROOT, "dumps", "%s-%s.json" % (HARNESS, nonce))
    results_path = os.path.join(ROOT, "results", "%s.json" % nonce)
    needles = secret_needles()
    # This process is the run's own CLI, so its command line is the argv the real CLI would have been given.
    leaks = command_line_leaks(needles)
    dump = {"harness": HARNESS, "executable": NAME, "phase": phase, "nonce": nonce, "argv": argv,
            "cwd": os.getcwd(), "env": dict(os.environ), "servers": servers, "files": files_under_home(),
            "prompt": text[:4000]}
    with open(dump_path, "w") as out:
        json.dump(dump, out)
    env = {**os.environ, **{key: value for key, value in values.items() if key in ALLOWED}}
    ran = ""
    try:
        # The probe's own children (the MCP servers it starts from the config) are on the process list while it runs.
        proc = subprocess.Popen(["node", PROBE, dump_path, phase, results_path], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.time() + 900
        while proc.poll() is None and time.time() < end:
            leaks |= command_line_leaks(needles)
            time.sleep(0.2)
        if proc.poll() is None:
            proc.kill()
            proc.wait()
            ran = "timed out"
        else:
            ran = "" if proc.returncode == 0 else "exit %d" % proc.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        ran = type(exc).__name__
    leaks |= command_line_leaks(needles)
    if not os.path.exists(results_path):
        # The probe did not finish: say so in the results, with no output from it (its output is never echoed).
        with open(results_path, "w") as out:
            json.dump({"phase": phase, "ok": False, "checks": [{"name": "probe ran", "ok": False,
                       "detail": ran or "no result"}], "data": {}}, out)
    record_command_line_check(results_path, leaks)
    return nonce, phase


def reply_text(nonce, phase):
    ok = False
    try:
        with open(os.path.join(ROOT, "results", "%s.json" % nonce)) as handle:
            ok = bool(json.load(handle).get("ok"))
    except (OSError, ValueError):
        pass
    return "harness-reply: %s phase=%s ok=%s" % (nonce, phase, "yes" if ok else "no")


def claude_turn(argv):
    sid = str(uuid.uuid4())
    servers = claude_servers(argv)
    text = read_prompt(argv)
    if "harness-run" not in text:
        text += read_stdin_until(lambda line: "harness-run" in line, 120)
    done = run_probe(text, servers, argv)
    reply = reply_text(*done) if done else "no harness-run in the prompt"
    send({"type": "system", "subtype": "init", "session_id": sid, "model": "claude", "tools": [], "mcp_servers": []})
    send({"type": "assistant", "session_id": sid, "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}})
    send({"type": "result", "subtype": "success", "is_error": False, "result": reply, "session_id": sid,
          "num_turns": 1, "usage": {"input_tokens": 1, "output_tokens": 1}})


def codex_app_server():
    threads, configs, turns = {}, {}, 0
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            with open(os.path.join(ROOT, "codex-protocol.log"), "a") as trace:
                trace.write("unparsed: %s\n" % line[:160])
            continue
        method, params, rid = msg.get("method"), msg.get("params") or {}, msg.get("id")
        # Every message the host sends, by name, so a turn that never starts shows where the protocol stopped.
        with open(os.path.join(ROOT, "codex-protocol.log"), "a") as trace:
            trace.write("%s id=%s pid=%d\n" % (method, rid, os.getpid()))
        if rid is None:
            continue
        if method in ("thread/start", "thread/resume", "thread/fork"):
            thread = str(params.get("threadId") or "harness-" + uuid.uuid4().hex[:12])
            configs[thread] = ((params.get("config") or {}).get("mcp_servers") or {})
            threads[thread] = True
            send({"id": rid, "result": {"thread": {"id": thread}}})
        elif method in ("turn/start", "turn/steer"):
            # A steer adds input to the turn the host believes is running; the stand-in answers it as a turn of its own,
            # so the text is never dropped (the journey shim ignores steers, and a probe sent as one never ran).
            turns += 1
            thread = str(params.get("threadId"))
            turn = str(params.get("expectedTurnId") or "harness-turn-%d" % turns)
            items = params.get("input", [])
            text = " ".join(part.get("text", "") for part in items if isinstance(part, dict))
            with open(os.path.join(ROOT, "codex-protocol.log"), "a") as trace:
                at = text.find("harness-run")
                shown = text[at:at + 220] if at >= 0 else "NO harness-run in %d chars: %s" % (len(text), text[-160:])
                trace.write("  %s text: %s\n" % (method, shown.replace("\n", " ")))
            if method == "turn/start":
                send({"id": rid, "result": {"turn": {"id": turn}}})
            else:
                send({"id": rid, "result": {"turnId": turn}})
            send({"method": "turn/started", "params": {"threadId": thread, "turn": {"id": turn}}})
            done = run_probe(text, configs.get(thread, {}), sys.argv)
            reply = reply_text(*done) if done else "no harness-run in the prompt"
            send({"method": "item/agentMessage/delta", "params": {"threadId": thread, "turnId": turn, "delta": reply}})
            send({"method": "item/completed", "params": {"threadId": thread, "turnId": turn,
                  "item": {"type": "agentMessage", "id": "m%d" % turns, "text": reply, "phase": "final_answer"}}})
            send({"method": "turn/completed", "params": {"threadId": thread, "turn": {"id": turn, "status": "completed"}}})
        else:
            send({"id": rid, "result": {}})


def grok_acp():
    """`grok agent ... stdio` speaks ACP over stdin/stdout: initialize, session/new, session/prompt (its reply comes as
    session/update chunks, then the prompt's stopReason)."""
    sessions, servers = {}, {}
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, params, rid = msg.get("method"), msg.get("params") or {}, msg.get("id")
        if rid is None:
            continue
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1, "agentCapabilities": {}, "authMethods": []}})
        elif method in ("session/new", "session/load"):
            sid = str(params.get("sessionId") or "grok-" + uuid.uuid4().hex[:12])
            servers[sid] = {item.get("name", "server%d" % i): item for i, item in enumerate(params.get("mcpServers") or [])}
            sessions[sid] = True
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": sid}})
        elif method == "session/prompt":
            sid = str(params.get("sessionId"))
            text = " ".join(part.get("text", "") for part in params.get("prompt", []) if isinstance(part, dict))
            done = run_probe(text, servers.get(sid, {}), sys.argv)
            reply = reply_text(*done) if done else "no harness-run in the prompt"
            send({"jsonrpc": "2.0", "method": "session/update",
                  "params": {"sessionId": sid, "update": {"sessionUpdate": "agent_message_chunk",
                                                          "content": {"type": "text", "text": reply}}}})
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {}})


def main():
    args = sys.argv[1:]
    if "--version" in args or args[:1] == ["version"]:
        print(VERSIONS.get(NAME, "1.0.0"))
        return
    if NAME == "claude" and args[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}))
        return
    if NAME == "codex" and args[:2] == ["login", "status"]:
        print("Logged in using ChatGPT")
        return
    if NAME == "cursor-agent" and args[:1] == ["status"]:
        print("Logged in as harness@acme.example")
        return
    if NAME == "cursor-agent" and args[:1] == ["create-chat"]:
        # cursor-agent prints the new chat's id on stdout; the runner keeps it as the thread id.
        print(str(uuid.uuid4()))
        return
    if NAME == "grok" and "stdio" in args and "agent" in args[:1]:
        grok_acp()
        return
    if NAME == "codex" and args[:1] == ["app-server"]:
        codex_app_server()
        return
    if NAME == "claude":
        claude_turn(args)
        return
    servers = muse_servers(args) if NAME == "muse" else {}
    text = read_prompt(args)
    if "harness-run" not in text:
        text += read_stdin_until(lambda line: "harness-run" in line, 30)
    done = run_probe(text, servers, args)
    print(reply_text(*done) if done else "no harness-run in the prompt")


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        import traceback
        os.makedirs(ROOT, exist_ok=True)
        with open(os.path.join(ROOT, "fake-cli.log"), "a") as log:
            log.write("%s %s\n%s" % (NAME, sys.argv[1:], traceback.format_exc()))
        raise
