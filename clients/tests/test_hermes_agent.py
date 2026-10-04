"""The Hermes and OpenClaw connector (clients/hermes_agent.py) against a fake hub: pair, update, doctor, old-label
cleanup, the archived/revoked back-off and the scheduled tico-sync job. launchctl, systemctl, `hermes cron` and
`openclaw cron` are mocked; nothing touches the real home directory, launchd, systemd, Hermes or OpenClaw."""
import argparse
import contextlib
import io
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from clients import hermes_agent as H

REPO = Path(__file__).resolve().parents[2]
REAL_RUN = subprocess.run
REAL_SLEEP = __import__("time").sleep
TOKEN = "tico-agent-SECRET-0123456789abcdef"
PAIR_SECRET = "pairing-secret-0123456789abcdefghijkl"
CODE = "K7QM-4F2P"


class Hub:
    """A hub that answers only what the connector asks, and remembers every request."""

    def __init__(self):
        self.requests = []
        self.pair_states = [{"state": "approved", "bot": "scout", "token": TOKEN, "url": None}]
        self.expires_in = 600
        self.heartbeat = (200, {"server_time": "2026-09-30T10:00:00Z", "bot": "scout",
                                "waiting": {"messages": 2, "tasks": 1}})
        self.script = ""
        self.skill = (REPO / "skills" / "tico-sync" / "SKILL.md").read_text()
        self.mcp = (200, {"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": '{"messages": []}'}], "isError": False}})
        self.me = {"role": "bot", "actor": "bot:scout", "agent": "hermes"}
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body, text=False):
                raw = body.encode() if text else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/plain" if text else "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def record(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode()) if length else None
                hub.requests.append({"method": self.command, "path": self.path, "body": body,
                                     "headers": {k.lower(): v for k, v in self.headers.items()}})
                return body

            def do_POST(self):
                self.record()
                if self.path == "/api/v2/agents/pairings":
                    return self.reply(201, {"pairing_id": "p-1", "code": CODE, "secret": PAIR_SECRET,
                                            "expires_in": hub.expires_in, "poll_every": 1})
                if self.path == "/api/v2/agents/heartbeat":
                    status, body = hub.heartbeat
                    return self.reply(status, body)
                if self.path == "/api/v2/mcp":
                    status, body = hub.mcp
                    return self.reply(status, body)
                self.reply(404, {"error": {"code": "not_found", "detail": "no"}})

            def do_GET(self):
                self.record()
                if self.path == "/api/v2/agents/pairings/p-1":
                    if self.headers.get("X-Pairing-Secret") != PAIR_SECRET:
                        return self.reply(404, {"error": {"code": "not_found", "detail": "no"}})
                    state = hub.pair_states.pop(0) if len(hub.pair_states) > 1 else hub.pair_states[0]
                    state = dict(state)
                    if state.get("url") is None:
                        state.pop("url", None)
                        if state["state"] == "approved":
                            state["url"] = hub.url
                    return self.reply(200, state)
                if self.path == "/api/v2/me":
                    return self.reply(200, hub.me)
                if self.path == "/api/v2/agents/setup-script":
                    return self.reply(200, hub.script, text=True)
                if self.path == "/api/v2/agents/sync-skill":
                    return self.reply(200, hub.skill, text=True)
                self.reply(404, {"error": {"code": "not_found", "detail": "no"}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def count(self, path):
        return sum(1 for r in self.requests if r["path"] == path)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Fake:
    """Records launchctl/systemctl calls instead of running them, and plays `hermes cron` and `openclaw cron`:
    Hermes keeps its jobs in <profile>/cron/jobs.json, OpenClaw's live in its Gateway (here, `oc_jobs`)."""

    def __init__(self, loaded=True):
        self.calls = []
        self.loaded = loaded
        self.oc_jobs = {}
        self.broken = {}             # {"openclaw": "gateway closed"}: every call of that tool fails
        self.counter = 0

    def run(self, command, *args, **kwargs):
        if len(command) > 1 and str(command[1]).endswith("hermes_agent.py"):
            return REAL_RUN(command, *args, **kwargs)        # the freshly downloaded connector really runs
        self.calls.append(list(command))
        name = Path(str(command[0])).name
        if name in ("hermes", "openclaw"):
            return getattr(self, name)(list(command[1:]))
        code = 0
        out = ""
        if command[:2] == ["launchctl", "print"] and not self.loaded:
            code = 113
        if command[:3] == ["systemctl", "--user", "is-active"]:
            out, code = ("active\n", 0) if self.loaded else ("inactive\n", 3)
        return subprocess.CompletedProcess(command, code, stdout=out, stderr="")

    def ran(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]

    def done(self, command, out="", code=0, err=""):
        return subprocess.CompletedProcess(command, code, stdout=out, stderr=err)

    def hermes(self, args):
        if self.broken.get("hermes"):
            return self.done(args, code=1, err=self.broken["hermes"])
        home = Path(os.environ["HERMES_HOME"])
        directory = home
        if args[:1] == ["-p"]:
            directory, args = home / "profiles" / args[1], args[2:]
        if args == ["--version"]:
            return self.done(args, "Hermes Agent v0.19.0 (2026.7.20)\n")
        jobs_file = directory / "cron" / "jobs.json"
        try:
            data = json.loads(jobs_file.read_text())
        except (OSError, ValueError):
            data = {}
        jobs = data.setdefault("jobs", [])
        if args[:2] == ["cron", "create"]:
            ap = argparse.ArgumentParser()
            ap.add_argument("schedule")
            ap.add_argument("prompt", nargs="?")
            ap.add_argument("--name")
            ap.add_argument("--skill", action="append", dest="skills")
            ap.add_argument("--script")
            ap.add_argument("--deliver")
            got = ap.parse_args(args[2:])
            self.counter += 1
            jobs.append({"id": "job%d" % self.counter, "name": got.name, "prompt": got.prompt, "skills": got.skills,
                         "script": got.script, "deliver": got.deliver, "schedule_display": got.schedule,
                         "enabled": True, "last_run_at": None, "last_status": None, "no_agent": False})
        elif args[:2] == ["cron", "remove"]:
            data["jobs"] = [j for j in jobs if j["id"] != args[2]]
        else:
            return self.done(args, code=2, err="unexpected hermes call")
        jobs_file.parent.mkdir(parents=True, exist_ok=True)
        jobs_file.write_text(json.dumps(data))
        return self.done(args, "ok\n")

    def openclaw(self, args):
        if self.broken.get("openclaw"):
            return self.done(args, code=1, err=self.broken["openclaw"])
        profile = "default"
        if args[:1] == ["--profile"]:
            profile, args = args[1], args[2:]
        jobs = self.oc_jobs.setdefault(profile, [])
        banner = "OpenClaw 2026.3.13 (61d171a)\n| Doctor warnings: something harmless {not json}\n"
        if args == ["--version"]:
            return self.done(args, "OpenClaw 2026.3.13 (61d171a)\n")
        if args[:2] == ["cron", "add"]:
            ap = argparse.ArgumentParser()
            ap.add_argument("--name")
            ap.add_argument("--every")
            ap.add_argument("--cron")
            ap.add_argument("--session")
            ap.add_argument("--message")
            ap.add_argument("--no-deliver", action="store_true")
            ap.add_argument("--timeout-seconds")
            ap.add_argument("--json", action="store_true")
            got = ap.parse_args(args[2:])
            self.counter += 1
            schedule = ({"kind": "every", "everyMs": int(got.every[:-1]) * (3600000 if got.every[-1] == "h" else 60000)}
                        if got.every else {"kind": "cron", "expr": got.cron})
            job = {"id": "oc-%d" % self.counter, "name": got.name, "enabled": True, "schedule": schedule,
                   "sessionTarget": got.session, "payload": {"kind": "agentTurn", "message": got.message},
                   "delivery": {"mode": "none" if got.no_deliver else "announce"}, "state": {}}
            jobs.append(job)
            return self.done(args, banner + json.dumps(job, indent=2))
        if args[:2] == ["cron", "list"]:
            return self.done(args, banner + json.dumps({"jobs": jobs, "total": len(jobs)}, indent=2))
        if args[:2] == ["cron", "rm"]:
            self.oc_jobs[profile] = [j for j in jobs if j["id"] != args[2]]
            return self.done(args, json.dumps({"ok": True}))
        return self.done(args, code=2, err="unexpected openclaw call")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.home = self.tmp / "home"
        self.config_dir = self.home / ".config" / "tico" / "agents"
        self.profile = self.home / ".hermes" / "profiles" / "scout"
        self.profile.mkdir(parents=True)
        (self.profile / "config.yaml").write_text("model:\n  default: some-model\n  provider: some\n")
        env = mock.patch.dict(os.environ, {"HOME": str(self.home), "HERMES_HOME": str(self.home / ".hermes"),
                                           "TICO_AGENT_CONFIG_DIR": str(self.config_dir),
                                           "PATH": "/usr/bin:/bin"})   # no real `hermes` for the child to run
        env.start()
        self.addCleanup(env.stop)
        patch = mock.patch.object(H, "CONFIG_DIR", self.config_dir)
        patch.start()
        self.addCleanup(patch.stop)
        self.hub = Hub()
        self.addCleanup(self.hub.close)
        self.fake = Fake()
        self.platform("darwin")

    def platform(self, name):
        for target in (mock.patch.object(H.sys, "platform", name),
                       mock.patch.object(H.subprocess, "run", self.fake.run),
                       mock.patch.object(H.shutil, "which",
                                         lambda n: "/usr/bin/" + n if n in ("systemctl", "hermes", "openclaw") else None)):
            target.start()
            self.addCleanup(target.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = H.main(list(argv))
        self.assertNotIn(TOKEN, out.getvalue() + err.getvalue(), "the token was printed")
        return code, out.getvalue(), err.getvalue()

    def install(self, *extra):
        code, out, err = self.run_cli("install", "--profile", "scout", "--url", self.hub.url, "--bot", "scout",
                                      "--token", TOKEN, *extra)
        self.assertEqual(code, 0, err)
        return out

    def credential(self):
        return json.loads((self.config_dir / "scout.json").read_text())


class Pair(Base):
    def test_missing_yaml_reports_incomplete_tools_and_preserves_other_servers(self):
        config = self.profile / "config.yaml"
        config.write_text("mcp_servers:\n  other:\n    url: https://example.com/mcp\n")
        with mock.patch.dict("sys.modules", {"yaml": None}):
            out = self.install("--no-timer", "--sync", "off")
        self.assertIn("Paired, finish connecting tools:", out)
        self.assertIn("-m pip install PyYAML", out)
        self.assertIn("reinstall --profile scout", out)
        self.assertNotIn("Registered Hermes", out)
        self.assertEqual(config.read_text(), "mcp_servers:\n  other:\n    url: https://example.com/mcp\n")
        self.assertTrue((self.config_dir / "scout.json").exists())

    def test_happy_path_installs_everything_and_never_prints_the_token(self):
        self.hub.pair_states = [{"state": "pending"}, {"state": "pending"},
                                {"state": "approved", "bot": "scout", "token": TOKEN}]
        with mock.patch.object(H.time, "sleep", lambda s: None):
            code, out, err = self.run_cli("pair", "--profile", "scout", "--url", self.hub.url)
        self.assertEqual(code, 0, err)
        self.assertIn("Tell BotOps", out)
        self.assertIn("connect my Hermes profile scout, code " + CODE, out)
        self.assertNotIn(PAIR_SECRET, out + err)
        # The pairing call is unauthenticated and carries the connector's own User-Agent.
        first = self.hub.requests[0]
        self.assertEqual(first["path"], "/api/v2/agents/pairings")
        self.assertNotIn("authorization", first["headers"])
        self.assertEqual(first["body"]["profile"], "scout")
        self.assertEqual(first["body"]["harness"], "hermes")
        self.assertTrue(first["headers"]["user-agent"].startswith("tico-hermes-agent/"))
        polls = [r for r in self.hub.requests if r["path"] == "/api/v2/agents/pairings/p-1"]
        self.assertEqual(len(polls), 3)
        self.assertEqual(polls[0]["headers"]["x-pairing-secret"], PAIR_SECRET)
        # What install does: .env, MCP entry, credential file, timer.
        self.assertIn("TICO_AGENT_TOKEN=" + TOKEN, (self.profile / ".env").read_text())
        self.assertEqual(stat.S_IMODE((self.profile / ".env").stat().st_mode), 0o600)
        self.assertIn("mcp_servers", (self.profile / "config.yaml").read_text())
        self.assertEqual(stat.S_IMODE((self.config_dir / "scout.json").stat().st_mode), 0o600)
        self.assertEqual(self.credential()["bot"], "scout")
        self.assertEqual(self.hub.count("/api/v2/agents/heartbeat"), 1)
        self.assertTrue((self.home / "Library/LaunchAgents/team.tico-agent.scout.plist").exists())
        self.assertTrue(self.fake.ran("launchctl", "bootstrap"))

    def test_an_expired_code_stops_cleanly_and_changes_nothing(self):
        self.hub.pair_states = [{"state": "pending"}, {"state": "expired"}]
        with mock.patch.object(H.time, "sleep", lambda s: None):
            code, out, err = self.run_cli("pair", "--profile", "scout", "--url", self.hub.url)
        self.assertEqual(code, 1)
        self.assertIn("expired", err)
        self.assertIn("run pair again", err)
        self.assertFalse((self.config_dir / "scout.json").exists())
        self.assertFalse((self.profile / ".env").exists())
        self.assertEqual(self.fake.calls, [])

    def test_waiting_past_the_deadline_is_an_expiry_too(self):
        self.hub.pair_states = [{"state": "pending"}]
        self.hub.expires_in = 1
        with mock.patch.object(H.time, "sleep", lambda s: REAL_SLEEP(0.6)):
            code, out, err = self.run_cli("pair", "--profile", "scout", "--url", self.hub.url)
        self.assertEqual(code, 1)
        self.assertIn("expired", err)

    def test_declined_is_reported_and_changes_nothing(self):
        self.hub.pair_states = [{"state": "declined"}]
        code, out, err = self.run_cli("pair", "--profile", "scout", "--url", self.hub.url)
        self.assertEqual(code, 1)
        self.assertIn("declined", err)
        self.assertFalse((self.config_dir / "scout.json").exists())

    def test_a_missing_profile_fails_before_asking_the_hub_for_a_code(self):
        code, out, err = self.run_cli("pair", "--profile", "nobody", "--url", self.hub.url)
        self.assertEqual(code, 1)
        self.assertEqual(self.hub.requests, [])


class Update(Base):
    def test_update_replaces_the_installed_copy_atomically_and_installs_again(self):
        self.install("--no-timer")
        target = self.config_dir / "hermes_agent.py"
        target.write_text("# old connector\n")
        os.chmod(target, 0o644)
        self.hub.script = Path(H.__file__).read_text().replace('VERSION = "%s"' % H.VERSION, 'VERSION = "9.9.9"')
        beats = self.hub.count("/api/v2/agents/heartbeat")
        code, out, err = self.run_cli("update", "--profile", "scout")
        self.assertEqual(code, 0, err)
        self.assertIn("9.9.9", out)
        self.assertIn('VERSION = "9.9.9"', target.read_text())
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertFalse(list(self.config_dir.glob("*.tmp")))
        fetch = [r for r in self.hub.requests if r["path"] == "/api/v2/agents/setup-script"][0]
        self.assertEqual(fetch["headers"]["authorization"], "Bearer " + TOKEN)
        self.assertTrue(fetch["headers"]["user-agent"].startswith("tico-hermes-agent/"))
        self.assertNotIn("python-urllib", fetch["headers"]["user-agent"].lower())
        # The new file ran the install steps again with the saved values.
        self.assertEqual(self.hub.count("/api/v2/agents/heartbeat"), beats + 1)
        self.assertEqual(self.credential()["token"], TOKEN)

    def test_a_sign_in_page_is_not_installed(self):
        self.install("--no-timer")
        target = self.config_dir / "hermes_agent.py"
        target.write_text("# current connector\n")
        self.hub.script = "<html><body>Sign in</body></html>"
        code, out, err = self.run_cli("update", "--profile", "scout")
        self.assertEqual(code, 1)
        self.assertEqual(target.read_text(), "# current connector\n")


class Doctor(Base):
    def test_manual_heartbeat_survives_reinstall_and_old_config_and_can_enable_timer(self):
        self.install("--no-timer", "--sync", "off")
        self.fake.loaded = False
        code, out, err = self.run_cli("doctor", "--profile", "scout")
        self.assertEqual(code, 0, out + err)
        self.assertIn("manual heartbeat mode", out)
        self.assertIn("heartbeat --profile scout", out)
        self.assertIn("reinstall --profile scout --timer", out)
        self.assertFalse(self.fake.ran("launchctl", "print"))
        self.assertEqual(self.run_cli("reinstall", "--profile", "scout")[0], 0)
        self.assertEqual(self.credential()["heartbeat_mode"], "manual")
        saved = self.credential()
        saved.pop("heartbeat_mode")
        H.save_config("scout", saved)
        self.assertEqual(self.run_cli("doctor", "--profile", "scout")[0], 0)
        self.assertEqual(self.run_cli("reinstall", "--profile", "scout", "--timer")[0], 0)
        self.assertEqual(self.credential()["heartbeat_mode"], "timer")
        self.assertEqual(self.run_cli("doctor", "--profile", "scout")[0], 1)

    def seed_old_names(self):
        (self.profile / "SOUL.md").write_text("You are Scout.\nAlways use\nhub_say to reply.\n")
        (self.profile / "skills" / "triage").mkdir(parents=True)
        (self.profile / "skills" / "triage" / "SKILL.md").write_text("Call `hub_docs_search` then hub_message_send.\n")
        (self.profile / "cron").mkdir(exist_ok=True)
        jobs = self.profile / "cron" / "jobs.json"        # the tico-sync job is in it already: keep it, on one line
        data = json.loads(jobs.read_text()) if jobs.exists() else {}
        data["prompt"] = "check hub_inbox and hub_fleet-check"
        jobs.write_text(json.dumps(data) + "\n")
        (self.profile / "memories").mkdir()
        (self.profile / "memories" / "MEMORY.md").write_text("- a\n- mark with hub_ack, status via hub_status_set\n")

    def test_a_healthy_profile_reports_ok_and_old_tool_names_with_file_line_and_new_name(self):
        self.install()
        self.seed_old_names()
        before = {p: p.read_text() for p in self.profile.rglob("*") if p.is_file()}
        code, out, err = self.run_cli("doctor", "--profile", "scout")
        self.assertEqual(code, 0, out + err)
        for text in ("credential file", "mcp_servers.tico is in config.yaml", "TICO_AGENT_TOKEN is present",
                     "heartbeat timer", "is loaded", "last heartbeat reply", "GET /api/v2/me works"):
            self.assertIn(text, out)
        self.assertNotIn("PROBLEM", out)
        self.assertIn("SOUL.md:3: hub_say -> hub_message_send", out)
        self.assertIn("SKILL.md:1: hub_docs_search -> hub_doc_search", out)
        self.assertIn("jobs.json:1: hub_inbox -> hub_message_list", out)
        self.assertIn("jobs.json:1: hub_fleet-check -> hub_health_check", out)
        self.assertIn("MEMORY.md:2: hub_ack -> hub_message_mark_read", out)
        self.assertIn("MEMORY.md:2: hub_status_set -> hub_bot_status_set", out)
        self.assertNotIn("hub_message_send ->", out)        # current names are not flagged
        self.assertTrue(self.fake.ran("launchctl", "print"))
        after = {p: p.read_text() for p in before}
        self.assertEqual(before, after, "doctor must not edit anything")

    def test_problems_are_named_in_plain_words(self):
        self.install()
        os.chmod(self.config_dir / "scout.json", 0o644)
        (self.profile / ".env").write_text("OTHER=1\n")
        (self.profile / "config.yaml").write_text("model:\n  default: x\n")
        self.fake.loaded = False
        self.hub.me = {"role": "human", "actor": "human:x"}
        code, out, err = self.run_cli("doctor", "--profile", "scout")
        self.assertEqual(code, 1)
        self.assertIn("mode 644", out)
        self.assertIn("no mcp_servers.tico entry", out)
        self.assertIn("TICO_AGENT_TOKEN is missing", out)
        self.assertIn("not loaded", out)
        self.assertIn("GET /api/v2/me answers", out)

    def test_no_credential_file_is_one_clear_finding(self):
        code, out, err = self.run_cli("doctor", "--profile", "scout")
        self.assertEqual(code, 1)
        self.assertIn("no usable credential file", out)
        self.assertIn("run pair", out)


class OldLabels(Base):
    def old_plist(self, label, program="hermes_agent.py", profile="scout"):
        agents = self.home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        path = agents / (label + ".plist")
        plistlib.dump({"Label": label, "ProgramArguments": ["/usr/bin/python3", "/x/" + program, "heartbeat",
                                                            "--profile", profile]}, path.open("wb"))
        return path

    def test_install_removes_the_old_launchd_job_for_this_profile_only(self):
        old = self.old_plist("com.acme.tico-agent.scout")
        other_profile = self.old_plist("com.acme.tico-agent.other", profile="other")
        not_ours = self.old_plist("com.example.tico-agent.scout", program="backup.py")
        out = self.install()
        self.assertFalse(old.exists())
        self.assertTrue(other_profile.exists())
        self.assertTrue(not_ours.exists())
        self.assertTrue((self.home / "Library/LaunchAgents/team.tico-agent.scout.plist").exists())
        self.assertIn(["launchctl", "bootout", "gui/%d" % os.getuid(), str(old)], self.fake.calls)
        self.assertIn("removed the older launchd job com.acme.tico-agent.scout", out)

    def test_pair_and_update_clean_up_too(self):
        old = self.old_plist("com.acme.tico-agent.scout")
        self.hub.pair_states = [{"state": "approved", "bot": "scout", "token": TOKEN}]
        code, out, err = self.run_cli("pair", "--profile", "scout", "--url", self.hub.url)
        self.assertEqual(code, 0, err)
        self.assertFalse(old.exists())
        again = self.old_plist("com.acme.tico-agent.scout")
        self.assertEqual(self.run_cli("reinstall", "--profile", "scout")[0], 0)
        self.assertFalse(again.exists())

    def test_install_removes_old_systemd_units_for_this_profile(self):
        self.platform("linux")
        units = self.home / ".config" / "systemd" / "user"
        units.mkdir(parents=True)
        (units / "com.acme.tico-agent.scout.service").write_text("[Service]\nExecStart=/usr/bin/python3 /x/hermes_agent.py heartbeat --profile scout\n")
        (units / "com.acme.tico-agent.scout.timer").write_text("[Timer]\nOnBootSec=30\n")
        (units / "tico-agent-other.service").write_text("[Service]\nExecStart=/usr/bin/python3 /x/hermes_agent.py heartbeat --profile other\n")
        (units / "tico-agent-other.timer").write_text("[Timer]\n")
        self.install()
        self.assertFalse((units / "com.acme.tico-agent.scout.service").exists())
        self.assertFalse((units / "com.acme.tico-agent.scout.timer").exists())
        self.assertTrue((units / "tico-agent-other.timer").exists())
        self.assertTrue((units / "tico-agent-scout.timer").exists())
        self.assertIn(["systemctl", "--user", "disable", "--now", "com.acme.tico-agent.scout.timer"], self.fake.calls)
        self.assertNotIn(["systemctl", "--user", "disable", "--now", "tico-agent-other.timer"], self.fake.calls)


class Backoff(Base):
    ARCHIVED = (409, {"error": {"code": "bot_archived", "detail": "Bot scout is archived"}})

    def beat(self):
        return self.run_cli("heartbeat", "--profile", "scout")

    def test_archived_backs_off_to_once_an_hour_and_recovers(self):
        self.install("--no-timer")
        self.hub.heartbeat = self.ARCHIVED
        before = self.hub.count("/api/v2/agents/heartbeat")
        code, out, err = self.beat()
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(), "Bot scout is archived in Tico: restore it (ask BotOps) or run uninstall")
        self.assertEqual(self.hub.count("/api/v2/agents/heartbeat"), before + 1)
        # The next minute is skipped without calling the hub.
        now = H.time.time()
        with mock.patch.object(H.time, "time", lambda: now + 60):
            self.assertEqual(self.beat()[0], 0)
        self.assertEqual(self.hub.count("/api/v2/agents/heartbeat"), before + 1)
        self.assertIn("backoff", self.credential())
        # After an hour it tries once more, still archived, and waits another hour.
        with mock.patch.object(H.time, "time", lambda: now + 3601):
            code, out, err = self.beat()
        self.assertEqual(code, 1)
        self.assertEqual(self.hub.count("/api/v2/agents/heartbeat"), before + 2)
        # Restored: the next attempt after the wait succeeds and normal service resumes.
        self.hub.heartbeat = (200, {"server_time": "t", "bot": "scout", "waiting": {"messages": 0, "tasks": 0}})
        with mock.patch.object(H.time, "time", lambda: now + 3601 + 3601):
            code, out, err = self.beat()
        self.assertEqual(code, 0, err)
        self.assertIn("answers again", out)
        self.assertNotIn("backoff", self.credential())
        self.assertEqual(self.beat()[0], 0)
        self.assertEqual(self.hub.count("/api/v2/agents/heartbeat"), before + 4)

    def test_older_servers_are_matched_by_the_word_archived(self):
        self.install("--no-timer")
        self.hub.heartbeat = (409, {"error": {"code": "conflict", "detail": "This bot is Archived"}})
        code, out, err = self.beat()
        self.assertEqual(code, 1)
        self.assertIn("Bot scout is archived in Tico", err)
        self.assertIn("backoff", self.credential())

    def test_other_409s_do_not_back_off(self):
        self.install("--no-timer")
        self.hub.heartbeat = (409, {"error": {"code": "bot_paused", "detail": "Bot is paused"}})
        code, out, err = self.beat()
        self.assertEqual(code, 1)
        self.assertIn("paused", err)
        self.assertNotIn("backoff", self.credential())

    def test_revoked_says_to_pair_again_and_backs_off(self):
        self.install("--no-timer")
        self.hub.heartbeat = (401, {"error": {"code": "unauthorized", "detail": "Invalid credential"}})
        code, out, err = self.beat()
        self.assertEqual(code, 1)
        self.assertIn("credential revoked: run pair again", err)
        self.assertIn("backoff", self.credential())
        before = self.hub.count("/api/v2/agents/heartbeat")
        self.assertEqual(self.beat()[0], 0)
        self.assertEqual(self.hub.count("/api/v2/agents/heartbeat"), before)


class Sync(Base):
    """The scheduled tico-sync job on a Hermes profile: the skill, the pre-check script and one `hermes cron` job."""

    def jobs(self):
        try:
            return json.loads((self.profile / "cron" / "jobs.json").read_text())["jobs"]
        except OSError:
            return []

    def cron_calls(self):
        return [c for c in self.fake.calls if Path(c[0]).name == "hermes" and "cron" in c]

    def test_install_makes_the_skill_the_pre_check_script_and_one_hourly_job(self):
        out = self.install()
        skill = self.profile / "skills" / "tico-sync" / "SKILL.md"
        text = skill.read_text()
        self.assertIn("name: tico-sync", text)
        self.assertNotIn("{{", text)
        self.assertIn(str(self.config_dir / "hermes_agent.py"), text)
        self.assertIn("call --profile scout <tool name>", text)
        self.assertNotIn(TOKEN, text)
        script = self.profile / "scripts" / "tico-sync-check.py"
        self.assertTrue(script.is_file())
        self.assertNotIn(TOKEN, script.read_text())
        (job,) = self.jobs()
        self.assertEqual((job["name"], job["schedule_display"], job["skills"], job["script"], job["deliver"]),
                         ("tico-sync", "every 1h", ["tico-sync"], "tico-sync-check.py", "local"))
        self.assertIn("[SILENT]", job["prompt"])
        create = self.cron_calls()[0]
        self.assertEqual(create[1:4], ["-p", "scout", "cron"])
        self.assertIn("every 1h", create)
        self.assertIn("Hermes cron job tico-sync, every 1h, skill attached, pre-check tico-sync-check.py", out)
        self.assertEqual(self.credential()["sync"], "1h")

    def test_the_interval_takes_minutes_hours_daily_a_cron_expression_or_off(self):
        for asked, schedule in (("15m", "every 15m"), ("90m", "every 90m"), ("2h", "every 2h"), ("every 3h", "every 3h"),
                                ("1d", "every 24h"), ("daily", "0 9 * * *"), ("*/20 8-18 * * 1-5", "*/20 8-18 * * 1-5")):
            self.install("--sync", asked, "--no-timer")
            (job,) = self.jobs()
            self.assertEqual(job["schedule_display"], schedule, asked)
        self.install("--sync", "off", "--no-timer")
        self.assertEqual(self.jobs(), [])
        self.assertTrue((self.profile / "skills" / "tico-sync" / "SKILL.md").exists(), "the skill stays: a person can run it")
        self.assertEqual(self.credential()["sync"], "off")

    def test_a_bad_interval_is_refused_before_anything_changes(self):
        for asked in ("soon", "1m", "0 9 * *", "every"):
            before = len(self.hub.requests)
            code, out, err = self.run_cli("install", "--profile", "scout", "--url", self.hub.url, "--bot", "scout",
                                          "--token", TOKEN, "--sync", asked)
            self.assertEqual(code, 1, asked)
            self.assertIn("sync", err)
            self.assertEqual(len(self.hub.requests), before)
        self.assertFalse((self.profile / "skills" / "tico-sync").exists())

    def test_pair_takes_the_same_option(self):
        self.hub.pair_states = [{"state": "approved", "bot": "scout", "token": TOKEN}]
        code, out, err = self.run_cli("pair", "--profile", "scout", "--url", self.hub.url, "--sync", "15m")
        self.assertEqual(code, 0, err)
        self.assertEqual([j["schedule_display"] for j in self.jobs()], ["every 15m"])

    def test_reinstall_replaces_the_job_and_leaves_an_unchanged_one_alone(self):
        self.install()
        self.fake.calls.clear()
        self.assertEqual(self.run_cli("reinstall", "--profile", "scout")[0], 0)
        self.assertEqual(self.cron_calls(), [], "same interval: the job is not touched")
        code, out, err = self.run_cli("reinstall", "--profile", "scout", "--sync", "30m")
        self.assertEqual(code, 0, err)
        self.assertEqual([j["schedule_display"] for j in self.jobs()], ["every 30m"])
        self.assertEqual([c[4] for c in self.cron_calls()], ["remove", "create"])
        self.assertEqual(self.credential()["sync"], "30m")
        self.assertEqual(self.run_cli("reinstall", "--profile", "scout", "--sync", "off")[0], 0)
        self.assertEqual(self.jobs(), [])

    def test_other_jobs_are_never_touched(self):
        (self.profile / "cron").mkdir()
        (self.profile / "cron" / "jobs.json").write_text(json.dumps({"jobs": [
            {"id": "mine", "name": "Morning brief", "schedule_display": "0 7 * * *"}]}))
        self.install()
        self.install("--sync", "2h")
        self.run_cli("uninstall", "--profile", "scout")
        self.assertEqual([j["id"] for j in self.jobs()], ["mine"])

    def test_a_failing_hermes_does_not_undo_the_pairing(self):
        self.fake.broken["hermes"] = "no scheduler"
        code, out, err = self.run_cli("install", "--profile", "scout", "--url", self.hub.url, "--bot", "scout",
                                      "--token", TOKEN)
        self.assertEqual(code, 0, err)
        self.assertIn("WARNING: the sync job is not in place", out)
        self.assertIn("no scheduler", out)
        self.assertEqual(self.credential()["bot"], "scout")
        code, out, err = self.run_cli("doctor", "--profile", "scout")
        self.assertEqual(code, 1)
        self.assertIn("the sync job was not created", out)

    def test_doctor_says_when_the_job_last_ran(self):
        self.install()
        code, out, err = self.run_cli("doctor", "--profile", "scout")
        self.assertIn("sync job tico-sync (every 1h) is scheduled; has not run yet", out)
        self.assertIn("gateway is not running", out)
        data = json.loads((self.profile / "cron" / "jobs.json").read_text())
        data["jobs"][0].update(last_run_at="2026-09-30T09:00:00-07:00", last_status="ok")
        (self.profile / "cron" / "jobs.json").write_text(json.dumps(data))
        code, out, err = self.run_cli("doctor", "--profile", "scout")
        self.assertIn("last ran 2026-09-30T09:00:00-07:00 (ok)", out)
        (self.profile / "cron" / "jobs.json").write_text('{"jobs": []}')
        code, out, err = self.run_cli("doctor", "--profile", "scout")
        self.assertEqual(code, 1)
        self.assertIn("no scheduled job named tico-sync", out)

    def test_doctor_warns_when_the_gateway_is_down_and_says_the_fix(self):
        self.install()
        code, out, err = self.run_cli("doctor", "--profile", "scout")
        self.assertIn("WARN", out)
        self.assertIn("gateway is not running", out)
        self.assertIn("`hermes -p scout gateway install`", out)
        self.assertNotIn("bypass Tico's rules", out)

    def test_doctor_is_quiet_about_a_running_gateway_and_names_chat_channels(self):
        self.install()
        (self.profile / "gateway.pid").write_text(str(os.getpid()))
        code, out, err = self.run_cli("doctor", "--profile", "scout")
        self.assertIn("the profile's gateway is running", out)
        self.assertNotIn("gateway is not running", out)
        env = self.profile / ".env"
        env.write_text(env.read_text() + "SLACK_BOT_TOKEN=xoxb-1\nTELEGRAM_BOT_TOKEN=123:abc\n")
        code, out, err = self.run_cli("doctor", "--profile", "scout")
        self.assertIn("set up for Slack, Telegram", out)
        self.assertIn("bypass Tico's rules", out)
        self.assertNotIn("xoxb-1", out)

    def test_doctor_does_not_ask_for_a_gateway_when_sync_is_off(self):
        self.install("--sync", "off")
        code, out, err = self.run_cli("doctor", "--profile", "scout")
        self.assertNotIn("gateway is not running", out)

    def test_uninstall_removes_the_job_and_the_skill(self):
        self.install()
        code, out, err = self.run_cli("uninstall", "--profile", "scout")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.jobs(), [])
        self.assertFalse((self.profile / "skills" / "tico-sync").exists())
        self.assertFalse((self.profile / "scripts" / "tico-sync-check.py").exists())
        self.assertFalse((self.config_dir / "scout.json").exists())

    def test_the_hub_serves_the_skill_and_an_old_hub_falls_back_to_a_checkout(self):
        self.hub.skill = self.hub.skill.replace("# Tico sync", "# Tico sync (from the hub)")
        self.install()
        self.assertIn("(from the hub)", (self.profile / "skills" / "tico-sync" / "SKILL.md").read_text())
        self.hub.skill = "<html>Sign in</html>"
        self.assertEqual(self.run_cli("reinstall", "--profile", "scout")[0], 0)
        self.assertNotIn("Sign in", (self.profile / "skills" / "tico-sync" / "SKILL.md").read_text())

    def run_check_script(self):
        script = self.profile / "scripts" / "tico-sync-check.py"
        done = REAL_RUN([sys.executable, str(script)], capture_output=True, text=True, timeout=60,
                        env={"PATH": "/usr/bin:/bin", "HOME": str(self.home)})
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip().splitlines()

    def test_the_pre_check_script_keeps_the_agent_asleep_when_nothing_waits(self):
        self.install()
        self.assertEqual(self.run_check_script(), ["Tico: 2 message(s) and 1 task(s) waiting for scout."])
        self.hub.heartbeat = (200, {"server_time": "t", "bot": "scout", "waiting": {"messages": 0, "tasks": 0}})
        self.assertEqual(self.run_cli("heartbeat", "--profile", "scout")[0], 0)
        self.assertEqual(self.run_check_script(), ["Tico: nothing waiting for scout.", '{"wakeAgent": false}'])

    def test_a_stale_reply_is_refreshed_so_a_stopped_timer_cannot_hide_work(self):
        self.install("--no-timer")
        config = self.credential()
        config["last_ok"] = H.time.time() - 3600
        config["last_reply"] = {"waiting": {"messages": 0, "tasks": 0}}
        (self.config_dir / "scout.json").write_text(json.dumps(config))
        beats = self.hub.count("/api/v2/agents/heartbeat")
        code, out, err = self.run_cli("check", "--profile", "scout", "--gate")
        self.assertEqual(self.hub.count("/api/v2/agents/heartbeat"), beats + 1)
        self.assertIn("2 message(s) and 1 task(s) waiting", out)
        self.assertNotIn("wakeAgent", out)

    def test_an_unreachable_hub_is_said_in_one_line_and_does_not_wake_the_agent(self):
        self.install("--no-timer")
        config = self.credential()
        config["last_ok"] = 0
        (self.config_dir / "scout.json").write_text(json.dumps(config))
        self.hub.heartbeat = (500, {"error": {"code": "boom", "detail": "down"}})
        code, out, err = self.run_cli("check", "--profile", "scout", "--gate")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip().splitlines()[0][:18], "Tico check failed:")
        self.assertEqual(out.strip().splitlines()[-1], '{"wakeAgent": false}')

    def test_a_weekly_update_is_due_after_seven_days_and_wakes_the_agent(self):
        self.install("--no-timer")
        config = self.credential()
        config["last_reply"] = {"waiting": {"messages": 0, "tasks": 0}}
        config["last_ok"] = H.time.time()
        config["updated_at"] = H.time.time() - 8 * 86400
        (self.config_dir / "scout.json").write_text(json.dumps(config))
        code, out, err = self.run_cli("check", "--profile", "scout", "--gate")
        self.assertIn("Connector update due (last updated 8 days ago)", out)
        self.assertNotIn("wakeAgent", out)
        self.hub.script = Path(H.__file__).read_text()
        self.assertEqual(self.run_cli("update", "--profile", "scout")[0], 0)
        code, out, err = self.run_cli("check", "--profile", "scout")
        self.assertNotIn("update due", out)

    def test_a_reinstall_keeps_the_weekly_clock(self):
        self.install("--no-timer")
        config = self.credential()
        config["updated_at"] = 1000.0
        (self.config_dir / "scout.json").write_text(json.dumps(config))
        self.assertEqual(self.run_cli("reinstall", "--profile", "scout")[0], 0)
        self.assertEqual(self.credential()["updated_at"], 1000.0)


class OpenClaw(Base):
    """The same connector for an OpenClaw profile: no MCP client, so the credential goes in tico.env and the
    skill calls Tico through `call`; the job is `openclaw cron`."""

    def setUp(self):
        super().setUp()
        self.state = self.home / ".openclaw-claw"
        self.state.mkdir()
        (self.state / "openclaw.json").write_text(json.dumps({
            "gateway": {"mode": "remote", "port": 18789},
            "agents": {"defaults": {"model": {"primary": "anthropic/claude-opus-4-6"}}}}))
        self.hub.me = {"role": "bot", "actor": "bot:claw", "agent": "openclaw"}

    def pair(self, *extra, profile=("--profile", "claw")):
        self.hub.pair_states = [{"state": "approved", "bot": "claw", "token": TOKEN}]
        return self.run_cli("pair", "--harness", "openclaw", *profile, "--url", self.hub.url, *extra)

    def jobs(self, profile="claw"):
        return self.fake.oc_jobs.get(profile, [])

    def test_manual_heartbeat_is_healthy_without_timer_and_reinstall_keeps_mode(self):
        self.assertEqual(self.pair("--no-timer", "--sync", "off")[0], 0)
        self.fake.loaded = False
        code, out, err = self.run_cli("doctor", "--harness", "openclaw", "--profile", "claw")
        self.assertEqual(code, 0, out + err)
        self.assertIn("manual heartbeat mode", out)
        self.assertIn("--harness openclaw --profile claw", out)
        self.assertEqual(self.run_cli("reinstall", "--harness", "openclaw", "--profile", "claw")[0], 0)
        self.assertEqual(H.load_config("openclaw-claw")["heartbeat_mode"], "manual")

    def test_pair_wires_openclaw_without_mcp_and_makes_the_hourly_job(self):
        code, out, err = self.pair()
        self.assertEqual(code, 0, err)
        self.assertIn("connect my OpenClaw profile claw, code " + CODE, out)
        first = self.hub.requests[0]
        self.assertEqual((first["path"], first["body"]["harness"], first["body"]["profile"]),
                         ("/api/v2/agents/pairings", "openclaw", "claw"))
        # The credential: our 600 file, and tico.env beside OpenClaw's config; no config.yaml, no MCP entry.
        env = self.state / "tico.env"
        self.assertIn("TICO_AGENT_TOKEN=" + TOKEN, env.read_text())
        self.assertEqual(stat.S_IMODE(env.stat().st_mode), 0o600)
        self.assertFalse((self.state / "config.yaml").exists())
        self.assertIn("no MCP client", out)
        saved = json.loads((self.config_dir / "openclaw-claw.json").read_text())
        self.assertEqual((saved["bot"], saved["harness"], saved["sync"]), ("claw", "openclaw", "1h"))
        self.assertEqual(stat.S_IMODE((self.config_dir / "openclaw-claw.json").stat().st_mode), 0o600)
        # The skill, rendered for this profile, in OpenClaw's managed skills folder.
        text = (self.state / "skills" / "tico-sync" / "SKILL.md").read_text()
        self.assertIn("name: tico-sync", text)
        self.assertIn("call --harness openclaw --profile claw <tool name>", text)
        self.assertNotIn(TOKEN, text)
        # The job: one, every hour, an isolated agent turn, nothing announced.
        (job,) = self.jobs()
        self.assertEqual((job["name"], job["schedule"], job["sessionTarget"], job["delivery"]["mode"]),
                         ("tico-sync", {"kind": "every", "everyMs": 3600000}, "isolated", "none"))
        self.assertIn(str(self.state / "skills" / "tico-sync" / "SKILL.md"), job["payload"]["message"])
        add = self.fake.ran("/usr/bin/openclaw", "--profile", "claw", "cron", "add")[0]
        self.assertIn("--no-deliver", add)
        # The heartbeat timer runs the openclaw form, under its own label.
        plist = plistlib.loads((self.home / "Library/LaunchAgents/team.tico-agent.openclaw-claw.plist").read_bytes())
        self.assertEqual(plist["ProgramArguments"][2:], ["heartbeat", "--harness", "openclaw", "--profile", "claw"])
        beat = [r for r in self.hub.requests if r["path"] == "/api/v2/agents/heartbeat"][0]["body"]
        self.assertEqual((beat["version"], beat["model"], beat["provider"], beat["profile"]),
                         ("2026.3.13", "anthropic/claude-opus-4-6", "anthropic", "claw"))
        self.assertIn("gateway remote", beat["detail"])

    def test_the_default_profile_is_the_plain_state_directory_and_a_hermes_profile_of_the_same_name_is_separate(self):
        (self.home / ".openclaw").mkdir()
        self.hub.pair_states = [{"state": "approved", "bot": "claw", "token": TOKEN}]
        code, out, err = self.run_cli("pair", "--harness", "openclaw", "--url", self.hub.url)
        self.assertEqual(code, 0, err)
        self.assertTrue((self.home / ".openclaw" / "tico.env").exists())
        self.assertTrue((self.config_dir / "openclaw-default.json").exists())
        self.assertEqual(len(self.jobs("default")), 1)
        self.assertTrue(self.fake.ran("/usr/bin/openclaw", "cron", "add"), "no --profile for the default one")
        # A Hermes profile called `claw` and an OpenClaw profile called `claw` keep separate files.
        self.hub.me = {"role": "bot", "actor": "bot:claw", "agent": "hermes"}
        (self.home / ".hermes" / "profiles" / "claw").mkdir()
        self.assertEqual(self.run_cli("install", "--profile", "claw", "--url", self.hub.url, "--bot", "claw",
                                      "--token", TOKEN)[0], 0)
        self.assertTrue((self.config_dir / "claw.json").exists())

    def test_the_profile_directory_must_exist(self):
        code, out, err = self.run_cli("pair", "--harness", "openclaw", "--profile", "nobody", "--url", self.hub.url)
        self.assertEqual(code, 1)
        self.assertIn("No OpenClaw state directory", err)
        self.assertEqual(self.hub.requests, [])

    def test_reinstall_changes_the_interval_and_off_removes_the_job(self):
        self.assertEqual(self.pair("--sync", "daily")[0], 0)
        self.assertEqual(self.jobs()[0]["schedule"], {"kind": "cron", "expr": "0 9 * * *"})
        r = self.run_cli("reinstall", "--harness", "openclaw", "--profile", "claw", "--sync", "15m")
        self.assertEqual(r[0], 0, r[2])
        (job,) = self.jobs()
        self.assertEqual(job["schedule"], {"kind": "every", "everyMs": 900000})
        self.assertEqual(self.run_cli("reinstall", "--harness", "openclaw", "--profile", "claw", "--sync", "off")[0], 0)
        self.assertEqual(self.jobs(), [])

    def test_a_gateway_that_is_down_is_a_warning_not_a_failed_pairing(self):
        self.fake.broken["openclaw"] = "gateway closed (1006)"
        code, out, err = self.pair()
        self.assertEqual(code, 0, err)
        self.assertIn("WARNING: the sync job is not in place", out)
        self.assertIn("gateway closed", out)
        self.assertTrue((self.config_dir / "openclaw-claw.json").exists())
        self.fake.broken.clear()
        self.assertEqual(self.run_cli("reinstall", "--harness", "openclaw", "--profile", "claw")[0], 0)
        self.assertEqual(len(self.jobs()), 1, "reinstall retries with the saved interval")

    def test_doctor_reads_the_job_from_the_gateway(self):
        self.pair()
        code, out, err = self.run_cli("doctor", "--harness", "openclaw", "--profile", "claw")
        self.assertEqual(code, 0, out + err)
        self.assertIn("OpenClaw has no MCP client", out)
        self.assertIn("TICO_AGENT_TOKEN is present in tico.env", out)
        self.assertIn("sync job tico-sync (every 60m) is scheduled; has not run yet", out)
        self.jobs()[0]["state"] = {"lastRunAtMs": 1790800256000, "lastStatus": "ok"}
        code, out, err = self.run_cli("doctor", "--harness", "openclaw", "--profile", "claw")
        self.assertIn("last ran 2026-", out)
        self.assertIn("(ok)", out)

    def test_uninstall_removes_the_job_the_skill_and_tico_env(self):
        self.pair()
        self.assertEqual(self.run_cli("uninstall", "--harness", "openclaw", "--profile", "claw")[0], 0)
        self.assertEqual(self.jobs(), [])
        self.assertFalse((self.state / "skills" / "tico-sync").exists())
        self.assertFalse((self.state / "tico.env").exists())
        self.assertFalse((self.config_dir / "openclaw-claw.json").exists())
        self.assertTrue((self.state / "openclaw.json").exists(), "OpenClaw's own config is left alone")

    def test_call_sends_one_tool_to_the_mcp_endpoint_with_the_bearer_token(self):
        self.pair()
        self.hub.mcp = (200, {"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": '{"messages": [{"id": "m1"}]}'}], "isError": False}})
        code, out, err = self.run_cli("call", "--harness", "openclaw", "--profile", "claw", "hub_message_list", "{}")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"messages": [{"id": "m1"}]})
        sent = [r for r in self.hub.requests if r["path"] == "/api/v2/mcp"][0]
        self.assertEqual(sent["headers"]["authorization"], "Bearer " + TOKEN)
        self.assertEqual(sent["body"]["method"], "tools/call")
        self.assertEqual(sent["body"]["params"], {"name": "hub_message_list", "arguments": {}})
        self.assertTrue(sent["headers"]["user-agent"].startswith("tico-hermes-agent/"))
        self.run_cli("call", "--harness", "openclaw", "--profile", "claw", "hub_message_send",
                     '{"to": "human:ana", "text": "On it."}')
        self.assertEqual([r for r in self.hub.requests if r["path"] == "/api/v2/mcp"][1]["body"]["params"]["arguments"],
                         {"to": "human:ana", "text": "On it."})

    def test_call_reports_a_refused_tool_and_bad_arguments(self):
        self.pair()
        self.hub.mcp = (200, {"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": '{"error": "forbidden"}'}], "isError": True}})
        code, out, err = self.run_cli("call", "--harness", "openclaw", "--profile", "claw", "hub_task_update", "{}")
        self.assertEqual(code, 1)
        self.assertIn("forbidden", err)
        code, out, err = self.run_cli("call", "--harness", "openclaw", "--profile", "claw", "hub_whoami", "[1]")
        self.assertEqual(code, 1)
        self.assertIn("JSON object", err)
        self.hub.mcp = (200, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "Unknown tool: hub_nope"}})
        code, out, err = self.run_cli("call", "--harness", "openclaw", "--profile", "claw", "hub_nope")
        self.assertEqual(code, 1)
        self.assertIn("Unknown tool", err)

    def test_a_hermes_command_still_needs_a_profile(self):
        code, out, err = self.run_cli("status")
        self.assertEqual(code, 1)
        self.assertIn("--profile is required", err)


class SkillFile(unittest.TestCase):
    """skills/tico-sync/SKILL.md is what both agents load: lint it so a bad edit cannot ship."""
    PATH = REPO / "skills" / "tico-sync" / "SKILL.md"

    def front_matter(self):
        text = self.PATH.read_text()
        self.assertTrue(text.startswith("---\n"), "front matter must open the file")
        head, _, body = text[4:].partition("\n---\n")
        fields = {}
        for line in head.splitlines():
            # OpenClaw's parser reads single-line `key: value` pairs only.
            self.assertRegex(line, r"^[a-z][a-z-]*: \S.*$", "front matter is single-line key: value pairs")
            key, _, value = line.partition(": ")
            fields[key] = value
        return fields, body

    def test_the_file_exists_with_valid_front_matter(self):
        fields, body = self.front_matter()
        self.assertEqual(set(fields), {"name", "description"})
        self.assertEqual(fields["name"], "tico-sync")
        self.assertEqual(fields["name"], self.PATH.parent.name)
        self.assertTrue(20 <= len(fields["description"]) <= 1024)
        self.assertTrue(body.strip())

    def test_it_asks_for_the_steps_and_the_rules_the_owner_wants(self):
        _, body = self.front_matter()
        for needed in ("hub_whoami", "hub_note_list", "hub_message_list", "hub_message_send", "hub_message_mark_read",
                       "hub_task_update", "status: done", "status: waiting", "idle", "{{connector}} update",
                       "Never paste a secret", "{{connector}} call {{profile}}"):
            self.assertIn(needed, body)

    def test_every_tool_it_names_exists_and_every_placeholder_is_one_the_connector_fills(self):
        from clients import hubtools
        text = self.PATH.read_text()
        names = set(re.findall(r"\bhub_[a-z_]+\b", text))
        self.assertLessEqual(names, set(hubtools.BY_NAME), sorted(names - set(hubtools.BY_NAME)))
        self.assertEqual(set(re.findall(r"\{\{(\w+)\}\}", text)), {"connector", "profile"})
        rendered = H.render_skill(text, "openclaw", "claw", "/usr/bin/python3")
        self.assertNotIn("{{", rendered)


class Interval(unittest.TestCase):
    def test_parse(self):
        for asked, expected in (("15m", ("every", "15m")), ("1h", ("every", "1h")), ("Daily", ("cron", "0 9 * * *")),
                                ("every 2h", ("every", "2h")), ("60m", ("every", "1h")), ("off", ("off", "")),
                                ("0 */6 * * *", ("cron", "0 */6 * * *")), (None, ("every", "1h"))):
            self.assertEqual(H.parse_sync(asked), expected, asked)
        for bad in ("", "soon", "4m", "0 9 * *", "every", "1w"):
            with self.assertRaises(H.Failure, msg=bad):
                H.parse_sync(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)


def test_openclaw_error_keeps_gateway_cause_and_target_without_credentials():
    stderr = ('Error: gateway closed (1006): token="test-secret"\n'
              'Gateway target: ws://sam:private@127.0.0.1:18789/?token=test-secret\n'
              'Source: local loopback\nConfig: /tmp/profile.json\nBind: loopback\n')
    with mock.patch.object(H.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", stderr)):
        try:
            H.run_tool(["openclaw", "cron", "add"], "schedule")
        except H.Failure as exc:
            detail = str(exc)
        else:
            raise AssertionError("failed command must report an error")
    assert "gateway closed (1006)" in detail and "127.0.0.1:18789" in detail and "Source: local loopback" in detail
    assert "start this profile's Gateway" in detail and "reinstall" in detail
    assert "test-secret" not in detail and "private" not in detail


def test_openclaw_state_override_and_home_apply_to_named_profiles_and_every_cron_call(tmp_path, monkeypatch):
    root = tmp_path / "home"
    monkeypatch.setenv("OPENCLAW_HOME", str(root))
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
    assert H.openclaw_dir("scout") == root / ".openclaw-scout"
    assert H.openclaw_dir("default") == root / ".openclaw"
    directory = tmp_path / "explicit"
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(directory))
    assert H.openclaw_dir("scout") == directory
    assert H.openclaw_dir("default") == directory
    seen = []
    monkeypatch.setattr(H.shutil, "which", lambda name: name)
    def run(command, **kwargs):
        seen.append((command, kwargs["env"]["OPENCLAW_STATE_DIR"]))
        jobs = '{"jobs": [{"id": "j1", "name": "tico-sync", "enabled": true}]}'
        return subprocess.CompletedProcess(command, 0, stdout=jobs, stderr="")
    monkeypatch.setattr(H.subprocess, "run", run)
    H.sync_jobs("openclaw", "scout", directory)
    H.create_job("openclaw", "scout", directory, ("every", "1h"), "sync")
    H.remove_jobs("openclaw", "scout", directory)
    assert len(seen) == 4 and all(state == str(directory) for _, state in seen)
