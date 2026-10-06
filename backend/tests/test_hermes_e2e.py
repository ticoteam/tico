"""One end-to-end walk through a Hermes bot's life: pair, work, archive, restore, pair again.

The hub is the real app served over HTTP on a local port; the connector is the real clients/hermes_agent.py,
run against a fake profile directory under tmp with launchd/systemd mocked. Nothing touches the real home
directory, launchd or systemd. The detail of each step is in test_hermes_pairing.py, test_agents.py and
clients/tests/test_hermes_agent.py; this test is the proof they fit together."""

import contextlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_botops_parity import act
from backend.tests.test_member_bots import botops, turn  # noqa: F401
from backend.tests.test_runner import live  # noqa: F401
from clients import hermes_agent as H

REAL_RUN, REAL_SLEEP, REAL_WHICH = subprocess.run, time.sleep, H.shutil.which
CODE = re.compile(r"\bcode ([A-Z0-9]{4}-[A-Z0-9]{4})\b")


class Connector:
    """Runs the connector's commands in this process and keeps everything it prints."""

    def __init__(self, tmp_path, monkeypatch, url):
        if sys.platform not in ("darwin", "linux"):
            pytest.skip("the connector installs a launchd or systemd timer")
        self.url, self.home = url, tmp_path / "home"
        self.config_dir = self.home / ".config" / "tico" / "agents"
        self.profile = self.home / ".hermes" / "profiles" / "scout"
        self.profile.mkdir(parents=True)
        (self.profile / "config.yaml").write_text("model:\n  default: gpt-5.6-luna\n  provider: openai-codex\n")
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("HERMES_HOME", str(self.home / ".hermes"))
        monkeypatch.setenv("TICO_AGENT_CONFIG_DIR", str(self.config_dir))
        monkeypatch.setattr(H, "CONFIG_DIR", self.config_dir)
        self.timer_calls, self.printed, self.tokens = [], "", set()

    def _run_command(self, command, *args, **kwargs):
        if command and command[0] in ("launchctl", "systemctl"):
            self.timer_calls.append(list(command))
            out = "active\n" if command[:3] == ["systemctl", "--user", "is-active"] else ""
            return subprocess.CompletedProcess(command, 0, stdout=out, stderr="")
        if command and Path(str(command[0])).name == "hermes":
            return self._hermes(list(command[1:]))
        return REAL_RUN(command, *args, **kwargs)

    def _hermes(self, args):
        """`hermes -p scout cron create|remove`: the profile's jobs.json is all the connector reads back."""
        done = lambda code=0, err="": subprocess.CompletedProcess(args, code, stdout="ok\n", stderr=err)
        if args[:2] != ["-p", "scout"] or args[2:3] != ["cron"]:
            return done(2, "unexpected hermes call")
        jobs_file = self.profile / "cron" / "jobs.json"
        jobs = json.loads(jobs_file.read_text())["jobs"] if jobs_file.exists() else []
        if args[3] == "create":
            jobs.append({"id": f"job{len(jobs) + 1}", "name": args[args.index("--name") + 1], "schedule_display": args[4],
                         "enabled": True, "last_run_at": None, "last_status": None})
        elif args[3] == "remove":
            jobs = [j for j in jobs if j["id"] != args[4]]
        jobs_file.parent.mkdir(parents=True, exist_ok=True)
        jobs_file.write_text(json.dumps({"jobs": jobs}))
        return done()

    def sync_jobs(self):
        return [j["name"] for j in json.loads((self.profile / "cron" / "jobs.json").read_text())["jobs"]]

    def run(self, *argv, sleep=None):
        """(exit code, stdout, stderr) of one command. Fails the test if any token we ever held was printed."""
        out, err = io.StringIO(), io.StringIO()
        which = lambda name, *a, **k: "/usr/bin/systemctl" if name == "systemctl" else "/usr/bin/hermes" if name == "hermes" else REAL_WHICH(name, *a, **k)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(H.subprocess, "run", self._run_command), mock.patch.object(H.shutil, "which", which), \
                mock.patch.object(H.time, "sleep", (lambda seconds: sleep(out.getvalue())) if sleep else REAL_SLEEP):
            code = H.main(list(argv))
        self.printed += out.getvalue() + err.getvalue()
        self.token()                                               # remember the latest
        for token in self.tokens:
            assert token not in self.printed, "the token was printed"
        return code, out.getvalue(), err.getvalue()

    def pair(self, sleep):
        return self.run("pair", "--profile", "scout", "--url", self.url, sleep=sleep)

    def token(self):
        for line in (self.profile / ".env").read_text().splitlines() if (self.profile / ".env").exists() else []:
            if line.startswith("TICO_AGENT_TOKEN="):
                self.tokens.add(line.split("=", 1)[1])
                return line.split("=", 1)[1]
        return ""

    def credential(self):
        return json.loads((self.config_dir / "scout.json").read_text())

    def status(self):
        return json.loads(self.run("status", "--profile", "scout")[1])

    def mcp(self, method, params=None, token=None):
        """What the profile's agent does over the `mcp_servers.tico` entry: JSON-RPC to the hub with its token."""
        reply = H.request(self.url, token or self.token(), "POST", "/api/v2/mcp",
                          {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
        return reply["result"] if "result" in reply else reply

    def tool(self, name, **arguments):
        result = self.mcp("tools/call", {"name": name, "arguments": arguments})
        assert not result["isError"], result
        return result.get("structuredContent") or json.loads(result["content"][0]["text"])


@pytest.fixture
def connector(api, live, tmp_path, monkeypatch):
    monkeypatch.setattr(api.app.state.store.settings, "runner_url", live)        # the address the hub tells a profile to use
    return Connector(tmp_path, monkeypatch, live)


def revision(api):
    return get(api, "bots/scout/access")["revision"]


def bot(api):
    return next(b for b in get(api, "bots?include_archived=1") if b["slug"] == "scout")


@pytest.mark.slow
def test_a_hermes_bot_pairs_works_is_archived_restored_and_pairs_again(api, botops, connector):
    # An owner makes a Hermes bot and turns it on; a Hermes bot has no computer to be placed on.
    made = api.post("/api/v2/bots/register", json={"slug": "scout", "display_name": "Scout", "model": "hermes",
                                                   "description": "A Hermes profile."}, headers=headers())
    assert made.status_code == 200 and made.json()["harness"] == "hermes", made.text
    post(api, "bots/scout/go-live", {"setup": False})

    # Pair: the connector prints a code and waits; BotOps approves it as the owner who read it out.
    def approve_as_botops(printed):
        code = CODE.search(printed).group(1)
        ana = turn(api, botops, person="ana-test", text=f"Connect my Hermes profile scout, code {code}")
        done = act(api, ana, "POST", "agents/pairings/approve", {"code": code, "bot": "scout"})
        assert done.status_code == 200 and done.json()["profile"] == "scout", done.text
        REAL_SLEEP(0.02)

    started = time.monotonic()
    code, out, err = connector.pair(approve_as_botops)
    assert code == 0, err
    assert "Tell BotOps" in out and "Approved for bot 'scout'" in out and "Tico sees it: 0 message(s)" in out
    token = connector.token()
    assert token.startswith("tico-agent-")
    # The profile is wired: config entry, .env and credential file (the last two private), and a timer.
    assert "mcp_servers" in (connector.profile / "config.yaml").read_text()
    assert (connector.profile / ".env").read_text().splitlines() == [f"TICO_AGENT_TOKEN={token}", f"TICO_URL={connector.url}"]
    for private in (connector.profile / ".env", connector.config_dir / "scout.json"):
        assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert connector.credential()["token"] == token and connector.credential()["bot"] == "scout"
    assert [c for c in connector.timer_calls if c[0] == "launchctl" and "bootstrap" in c or c[:3] == ["systemctl", "--user", "enable"]]
    # ... and the heartbeat it sent is what the hub shows.
    listed = bot(api)
    assert listed["online"] is True and listed["agent"]["credential"] is True and listed["agent"]["profile"] == "scout"
    assert listed["agent"]["model"] == "gpt-5.6-luna" and listed["agent"]["last_seen"]
    assert connector.status()["last_reply"]["waiting"] == {"messages": 0, "tasks": 0}
    # Pairing also scheduled the agent's hourly sync job (a fake `hermes` records it), so doctor finds it.
    assert connector.sync_jobs() == ["tico-sync"]
    code, out, err = connector.run("doctor", "--profile", "scout")
    assert code == 0, out
    assert "sync job tico-sync (every 1h) is scheduled" in out

    # The agent's MCP door lists the inbox tools.
    assert "hub_message_list" in {t["name"] for t in connector.mcp("tools/list")["tools"]}

    # The owner writes to the bot: it waits (no job), the heartbeat counts it, and the agent reads it over MCP.
    sent = post(api, "chat/scout", {"text": "What did you find today?"})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE bot='scout'").fetchone()[0] == 0
    assert connector.run("heartbeat", "--profile", "scout")[0] == 0
    assert connector.status()["last_reply"]["waiting"]["messages"] == 1
    (waiting,) = connector.tool("hub_message_list")["messages"]
    assert waiting["body"] == "What did you find today?"
    reply = connector.tool("hub_message_send", to="ana", text="Three leads, all in Austin.", conversation_id=sent["conversation_id"])
    assert reply["from_actor"] == "bot:scout"
    assert connector.tool("hub_message_mark_read", message_id=sent["id"]) == {"read": True}
    assert [m["body"] for m in get(api, f"conversations/{sent['conversation_id']}/messages")] == [
        "What did you find today?", "Three leads, all in Austin."]
    # The next heartbeat, and `status` the profile's cron job reads, say nothing is waiting.
    assert connector.run("heartbeat", "--profile", "scout")[0] == 0
    assert connector.status()["last_reply"]["waiting"] == {"messages": 0, "tasks": 0}

    # Archived without revoking: the credential is kept, the hub answers 409 bot_archived, the connector says
    # so once, backs off (no call to the hub while it waits) and comes back by itself after a restore.
    archived = post(api, "bots/scout/archive", {"expected_revision": revision(api), "revoke_agent": False})
    assert archived["agent"]["credential_revoked"] is False
    code, out, err = connector.run("heartbeat", "--profile", "scout")
    assert code == 1 and err == "Bot scout is archived in Tico: restore it (ask BotOps) or run uninstall\n"
    assert connector.status()["backoff"]["reason"] == "bot_archived"
    with mock.patch.object(H, "request", side_effect=AssertionError("a backed-off heartbeat must not call the hub")):
        assert connector.run("heartbeat", "--profile", "scout")[0] == 0
    assert post(api, "bots/scout/restore", {})["status"] == "active"
    held = connector.credential()
    held["backoff"]["until"] = time.time() - 1                      # an hour has gone by
    H.save_config("scout", held)
    code, out, err = connector.run("heartbeat", "--profile", "scout")
    assert code == 0 and "Bot scout answers again" in out and "backoff" not in connector.credential()

    # Archived with the credential revoked (the default): 401, the one line that says to pair again, the same back-off.
    archived = post(api, "bots/scout/archive", {"expected_revision": revision(api)})
    assert archived["status"] == "archived" and archived["agent"]["credential_revoked"] is True
    assert api.post("/api/v2/agents/heartbeat", json={}, headers=headers(token)).status_code == 401
    code, out, err = connector.run("heartbeat", "--profile", "scout")
    assert code == 1 and err == "Bot scout: credential revoked: run pair again\n"
    assert connector.status()["backoff"]["message"] == err.strip()
    assert connector.credential()["backoff"]["until"] > time.time() + 3000

    # Restored, the old credential is still revoked; pairing again (approved from Settings -> Bots -> Pair, the same
    # route) makes a new one, clears the back-off and the loop works again.
    restored = post(api, "bots/scout/restore", {})
    assert restored["status"] == "active" and restored["restored"] is True
    assert connector.run("heartbeat", "--profile", "scout")[0] == 0            # still backed off, so silent
    def approve_as_owner(printed):
        done = api.post("/api/v2/agents/pairings/approve", json={"code": CODE.search(printed).group(1), "bot": "scout"},
                        headers=headers())
        assert done.status_code == 200, done.text
        REAL_SLEEP(0.02)
    code, out, err = connector.pair(approve_as_owner)
    assert code == 0, err
    fresh = connector.token()
    assert fresh.startswith("tico-agent-") and fresh != token and "backoff" not in connector.credential()
    assert api.get("/api/v2/me", headers=headers(token)).status_code == 401
    assert "hub_message_list" in {t["name"] for t in connector.mcp("tools/list")["tools"]}
    assert connector.run("heartbeat", "--profile", "scout")[0] == 0
    post(api, "chat/scout", {"text": "Welcome back."})
    assert [m["body"] for m in connector.tool("hub_message_list")["messages"]] == ["Welcome back."]
    assert connector.run("doctor", "--profile", "scout")[0] == 0
    assert time.monotonic() - started < 10
