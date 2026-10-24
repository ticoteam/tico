#!/usr/bin/env python3
"""A stand-in for the `codex` CLI, for scripts/journey-test.sh only: `--version`, `login status`, and `app-server` speaking the small
part of the JSON-RPC protocol the runner's Codex host uses. Every turn is answered with "journey-reply: <prompt>".

A thread is kept under CODEX_HOME, as the real CLI keeps its sessions, so resuming one this computer never started (or
lost) fails as it does there. A prompt holding `journey-ask <nonce> [env=NAME] [worktree=TASK]` is answered with facts
instead, on one line the journey reads: "journey-reply: <nonce> thread=<id> resumed=<yes|no> NAME=<sha256 prefix|missing>
worktree=<ok|failed>". The worktree is added the way a bot's turn adds one, with `hub task worktree add` in the turn's
environment, from a local repository this fake makes once."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid

ASK = re.compile(r"journey-ask (\S+)((?: [a-z]+=\S+)*)")


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


SHARED = "/home/runner/journey-threads"


def threads_dir():
    # A bot's turn runs as its own user, which cannot write in the runner's ~/.codex; the journey makes this one on
    # the runner's volume, so threads survive a new runner container as sessions do.
    if os.path.isdir(SHARED):
        return SHARED
    home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    return os.path.join(home, "journey-threads")


def thread_env(params):
    policy = (params.get("config") or {}).get("shell_environment_policy") or {}
    return {k: str(v) for k, v in (policy.get("set") or {}).items()}


def origin(env):
    """A one-commit repository the turn's user owns, so cloning it needs no credentials and no safe.directory."""
    path = os.path.join(threads_dir(), "origin-%d" % os.getuid())
    if not os.path.isdir(os.path.join(path, ".git")):
        git = {**env, "GIT_AUTHOR_NAME": "Acme", "GIT_AUTHOR_EMAIL": "bot@acme.example",
               "GIT_COMMITTER_NAME": "Acme", "GIT_COMMITTER_EMAIL": "bot@acme.example"}
        subprocess.run(["git", "init", "-q", "-b", "main", path], env=git, check=True, capture_output=True)
        subprocess.run(["git", "-C", path, "commit", "-q", "--allow-empty", "-m", "init"], env=git, check=True,
                       capture_output=True)
    return "file://" + path


def add_worktree(task, env):
    hub = shutil.which("hub", path=env.get("PATH")) or "/opt/tico/scripts/hub"
    try:
        done = subprocess.run([sys.executable, hub, "task", "worktree", "add", origin(env), "--task", task], env=env,
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return "failed", str(exc)
    if done.returncode == 0:
        return "ok", ""
    return "failed", (done.stderr or done.stdout).strip().replace("\n", " ")[-300:]


def answer(text, thread, resumed, env):
    asks = ASK.findall(text)
    if not asks:
        return "journey-reply: " + text[:200]
    nonce, args = asks[-1]
    facts, notes = [nonce, "thread=" + thread, "resumed=" + ("yes" if resumed else "no")], []
    for key, value in (arg.split("=", 1) for arg in args.split()):
        if key == "env":
            facts.append(value + "=" + (hashlib.sha256(env[value].encode()).hexdigest()[:12] if value in env else "missing"))
        elif key == "worktree":
            outcome, why = add_worktree(value, env)
            facts.append("worktree=" + outcome)
            if why:
                notes.append(why)
    return "journey-reply: " + " ".join(facts) + (" | " + " | ".join(notes) if notes else "")


def main():
    if "--version" in sys.argv:
        print("codex-cli 99.0.0")
        return
    if sys.argv[1:3] == ["login", "status"]:
        print("Logged in using an API key")
        return
    turns, envs, resumed = 0, {}, set()
    os.makedirs(threads_dir(), exist_ok=True)
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, params, rid = msg.get("method"), msg.get("params") or {}, msg.get("id")
        if rid is None:
            continue
        if method in ("thread/start", "thread/fork"):
            thread = "journey-" + uuid.uuid4().hex[:12]
            open(os.path.join(threads_dir(), thread), "w").close()
            envs[thread] = thread_env(params)
            send({"id": rid, "result": {"thread": {"id": thread}}})
        elif method == "thread/resume":
            thread = str(params.get("threadId") or "")
            if not re.fullmatch(r"journey-[0-9a-f]{12}", thread) or not os.path.exists(os.path.join(threads_dir(), thread)):
                send({"id": rid, "error": {"code": -32600, "message": "no rollout found for thread id " + thread}})
                continue
            envs[thread] = thread_env(params)
            resumed.add(thread)
            send({"id": rid, "result": {"thread": {"id": thread}}})
        elif method == "turn/start":
            turns += 1
            thread, turn = params["threadId"], "journey-turn-%d" % turns
            text = " ".join(part.get("text", "") for part in params.get("input", []))
            send({"id": rid, "result": {"turn": {"id": turn}}})
            send({"method": "turn/started", "params": {"threadId": thread, "turn": {"id": turn}}})
            reply = answer(text, thread, thread in resumed, {**os.environ, **envs.get(thread, {})})
            send({"method": "item/agentMessage/delta", "params": {"threadId": thread, "turnId": turn, "delta": reply}})
            send({"method": "item/completed", "params": {"threadId": thread, "turnId": turn,
                  "item": {"type": "agentMessage", "id": "m%d" % turns, "text": reply, "phase": "final_answer"}}})
            send({"method": "turn/completed", "params": {"threadId": thread, "turn": {"id": turn, "status": "completed"}}})
        else:
            send({"id": rid, "result": {}})


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        # The runner keeps only the tail of a harness's stderr in the run; the journey prints this file on a failure.
        import traceback
        with open("/tmp/journey-fake-codex.log", "a") as log:
            log.write("%s %s\n%s" % (os.getuid(), sys.argv[1:], traceback.format_exc()))
        raise
