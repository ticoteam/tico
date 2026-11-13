#!/usr/bin/env python3
"""Real server + Rust runners + Google Chrome; no pytest, external sites, model calls or command retries."""

import argparse
import base64
import contextlib
import json
import os
import re
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import totp
from practice import Practice


class Failure(Exception):
    pass


ERRORS = (Failure, OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, subprocess.SubprocessError)


def reason_for(exc):
    if isinstance(exc, Failure):
        return str(exc)
    frame = traceback.extract_tb(exc.__traceback__)[-1]
    return f"{type(exc).__name__} at {Path(frame.filename).name}:{frame.lineno}"


def require(condition, reason):
    if not condition:
        raise Failure(reason)


def until(check, reason, seconds=25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.1)
    raise Failure(reason)


def python():
    candidates = (ROOT / ".venv/bin/python", ROOT.parent / "judge-always-a-reason/.venv/bin/python")
    return str(next((p for p in candidates if p.exists()), Path(sys.executable)))


def tree(pid):
    """Descendants by parentage only: never find a process by its command, name or path."""
    result = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, check=True)
    parents = [tuple(map(int, line.split())) for line in result.stdout.splitlines()]
    found = {pid}
    while True:
        more = {child for child, parent in parents if parent in found}
        if more <= found:
            return found - {pid}
        found |= more


def exited(pid):
    result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
    return not result.stdout.strip() or result.stdout.lstrip().startswith("Z")


class Live:
    def __init__(self, directory, runner, ocr):
        self.dir = directory.resolve()
        self.runner = runner
        self.ocr = ocr
        self.output = []
        self.canaries = set()
        self.processes = []
        self.logs = []
        self.runs = {}
        self.tokens = {}
        self.tasks = {}
        self.practice = Practice()
        self.host = None
        self.own = None
        self.server = None
        self.server_port = self.port()
        self.url = f"http://127.0.0.1:{self.server_port}"
        self.db = directory / "hub/hub.db"
        self.screenshots = []

    @staticmethod
    def port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def spawn(self, command, name, env=None):
        log = self.dir / f"{name}-{len(self.logs)}.log"
        self.logs.append(log)
        with log.open("wb") as stream:
            proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream, stderr=stream, start_new_session=True)
        self.processes.append(proc)
        return proc

    def stop(self, proc):
        if proc.poll() is not None:
            return
        children = tree(proc.pid)
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(25)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)
        # A crashed runner cannot close Chrome; these are exclusively its recorded descendants.
        for pid in children:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)

    def api(self, path, body=None, method=None, token="ana-test", capture=False, expect=None):
        headers = {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid.uuid4())}
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + "/api/v2/" + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        result = json.loads(raw)
        if capture:
            self.output.append(("API response", raw.decode()))
        if expect is not None:
            require(result.get("error", {}).get("code") == expect, f"API {path}: expected {expect}, HTTP {status}")
        else:
            code = result.get("error", {}).get("code", "unknown") if isinstance(result, dict) else "unknown"
            require(status == 200, f"API {path}: HTTP {status}, code {code}")
        return result

    def sql(self, statement, args=()):
        with sqlite3.connect(self.db, timeout=20) as conn:
            return conn.execute(statement, args).fetchall()

    def start_server(self):
        self.server = self.spawn(
            [python(), str(HERE / "server.py"), str(self.dir / "hub"), str(self.server_port)], "server"
        )

        def ready():
            require(self.server.poll() is None, "server exited during startup")
            try:
                self.api("bots")
                return True
            except (OSError, Failure):
                return False

        until(ready, "server did not listen", 60)

    def enroll(self, name):
        code = self.api("enrollments", {"operator": "ana"})["code"]
        enrolled = self.api("runners/enroll", {"code": code, "label": name, "platform": "macos"})
        folder = self.dir / name
        folder.mkdir()
        config = folder / "runner.json"
        config.write_text(
            json.dumps(enrolled | {"url": self.url, "projects_dir": str(folder / "projects"), "capacity": 1})
        )
        config.chmod(0o600)
        return enrolled | {"config": config, "label": name}

    def beat(self, machine, capabilities=("browser_v1",), bots=()):
        self.api(
            "runners/heartbeat",
            {
                "version": "live",
                "platform": "macos",
                "capabilities": list(capabilities),
                "readiness": dict.fromkeys(bots, True),
            },
            token=machine["token"],
        )

    def start_runner(self, machine):
        before = self.sql("SELECT last_seen_at FROM runner_states WHERE runner_id=?", (machine["runner_id"],))
        last_seen = (before[0][0] or "") if before else ""
        env = dict(os.environ, TICO_SIDE_JOBS="0", TICO_SCREEN="0")
        self.runs[machine["runner_id"]] = self.spawn(
            [str(self.runner), "--config", str(machine["config"]), "run"], machine["label"], env
        )

        def accepted():
            require(self.runs[machine["runner_id"]].poll() is None, "runner exited before its heartbeat")
            rows = self.sql(
                "SELECT last_seen_at,capabilities,readiness FROM runner_states WHERE runner_id=?",
                (machine["runner_id"],),
            )
            return bool(
                rows
                and rows[0][0]
                and rows[0][0] > last_seen
                and "browser_v1" in json.loads(rows[0][1])
                and json.loads(rows[0][2]).get("browser", {}).get("chrome")
            )

        until(accepted, "runner's browser heartbeat was not accepted", 75)

    def select_host(self, machine):
        self.api(f"runners/{machine['runner_id']}/browser-host", {"on": True})

    def setup(self):
        self.start_server()
        self.host = self.enroll("Live host")
        self.own = self.enroll("Live own")
        for bot in ("ops", "finance"):
            self.api(f"bots/{bot}/assignment", {"runner_id": self.own["runner_id"], "expected_generation": 0})
            task = self.api(
                "tasks", {"owner": bot, "title": "Practice website access", "body": "Use the local practice site."}
            )
            self.tasks[bot] = task.get("task", task)
        self.beat(self.own, bots=("ops", "finance"))
        for bot in ("ops", "finance"):
            attempt = self.api("jobs/claim", {"bot": bot, "start_places": True}, token=self.own["token"])["attempt"]
            require(attempt is not None, "could not claim practice attempt")
            self.tokens[bot] = attempt
            self.api(f"attempts/{attempt['id']}/started", {"thread_id": "website-live-" + bot}, token=self.own["token"])
        self.start_runner(self.host)
        self.start_runner(self.own)
        self.select_host(self.host)

    def account(self, name, twofa=False, shared=False, short=False):
        self.phase = name
        alphabet = "abcdefghkmnprstwxyz"
        username = (
            "".join(secrets.choice("jkqv") for _ in range(4))
            if short
            else "user" + "".join(secrets.choice(alphabet) for _ in range(10))
        )
        password = "pass" + "".join(secrets.choice(alphabet) for _ in range(10))
        key = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=") if twofa else ""
        self.canaries.update((username, password))
        if key:
            self.canaries.add(key)
        self.practice.accounts[username] = (password, key)
        tool = self.api(
            "website-tools",
            {"name": name, "sites": [self.practice.url().rstrip("/"), self.practice.url(host="127.0.0.1").rstrip("/")]},
        )["id"]
        body = {"username": username, "password": password, "holder": None if shared else "bot:ops"}
        if key:
            body["totp"] = key
        account = self.api(f"website-tools/{tool}/accounts", body)["id"]
        if shared:
            for bot in ("ops", "finance"):
                self.api(f"website-tools/{tool}/accounts/{account}/access/bot:{bot}", {"on": True}, method="PUT")
        return {"site": tool, "id": account, "key": key, "username": username}

    def cli(self, words, bot="ops", expect=None, asynchronous=False):
        env = dict(
            os.environ,
            HUB_API_URL=self.url,
            HUB_TOKEN=self.tokens[bot]["token"],
            HUB_BOT=bot,
            PYTHONPATH=str(ROOT),
        )
        command = [python(), "-m", "clients.hubcli", *words, "--json"]
        proc = subprocess.Popen(
            command, cwd=self.dir, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.processes.append(proc)
        if asynchronous:
            return proc
        return self.finish(proc, expect)

    def finish(self, proc, expect=None):
        out, err = proc.communicate(timeout=48)
        self.output.extend((("CLI stdout", out), ("CLI stderr", err)))
        try:
            result = json.loads(out or err)
        except ValueError:
            raise Failure(f"CLI returned non-JSON (exit {proc.returncode})") from None
        error = result.get("error", "")
        code = error.get("code", "") if isinstance(error, dict) else error
        label = " ".join(proc.args[3:5]) + " (" + self.phase + ")"
        if expect:
            require(code == expect, f"CLI {label}: expected {expect}, got {code or 'success'}")
        else:
            require(proc.returncode == 0, f"CLI {label} failed: {code or 'unknown'}")
        return result

    def browser(self, account, verb, *args, **kwargs):
        return self.cli(["browser", verb, account["site"], *args], **kwargs)

    def fill(self, account, field, ref=None, **kwargs):
        return self.cli(["login", "fill", account["site"], field, *(["--ref", ref] if ref else [])], **kwargs)

    def read(self, account):
        return self.browser(account, "read").get("text", "")

    @staticmethod
    def ref(text, label):
        found = re.search(r'\[(e\d+)\][^\n]*"' + re.escape(label) + '"', text)
        require(found is not None, f"page has no {label} control")
        return found[1]

    def open(self, account, path, **kwargs):
        return self.browser(account, "open", self.practice.url(path), **kwargs)

    def sign_in(self, account, path="/login"):
        self.open(account, path)
        text = self.read(account)
        self.fill(account, "username", self.ref(text, "Username"))
        self.fill(account, "password", self.ref(text, "Password"))
        self.browser(account, "press", "Enter")
        require("Signed in" in self.read(account), "sign-in did not reach home")

    def routing(self):
        a = self.account("Routing")
        require(self.api(f"website-tools/{a['site']}")["host"]["online"], "accepted heartbeat did not make host online")
        self.phase = "Routing online host"
        require(self.open(a, "/login")["where"] == "central", "online host was not chosen")
        self.browser(a, "close")
        host_proc = self.runs[self.host["runner_id"]]
        self.stop(host_proc)
        until(
            lambda: not self.api(f"website-tools/{a['site']}")["host"]["online"],
            "stopped browser host stayed online",
            60,
        )
        self.phase = "Routing offline host"
        require(self.open(a, "/login")["where"] == "own", "offline host did not fall back")
        self.browser(a, "close")
        self.api(f"website-tools/{a['site']}", {"central_only": True}, method="PATCH")
        self.open(a, "/login", expect="browser_host_offline")
        self.api(f"website-tools/{a['site']}", {"central_only": False}, method="PATCH")
        self.start_runner(self.host)
        host_proc = self.runs[self.host["runner_id"]]
        os.kill(host_proc.pid, signal.SIGSTOP)
        try:
            # Freeze this real runner while simulating its missing capability, so its next heartbeat cannot undo it.
            self.beat(self.host, capabilities=())
            self.phase = "Routing missing Chrome"
            require(self.api(f"website-tools/{a['site']}")["host"]["online"], "missing-Chrome host was not reachable")
            require(self.open(a, "/login")["where"] == "own", "missing Chrome did not fall back")
            self.browser(a, "close")
        finally:
            os.kill(host_proc.pid, signal.SIGCONT)
            self.beat(self.host)
        self.api(f"runners/{self.host['runner_id']}/browser-host", {"on": False})
        self.phase = "Routing revoked host"
        require(self.open(a, "/login")["where"] == "own", "revoked host did not fall back")
        self.browser(a, "close")
        self.select_host(self.host)

    def ended_attempt(self):
        a = self.account("Attempt boundary")
        self.api(f"runners/{self.host['runner_id']}/browser-host", {"on": False})
        attempt = self.tokens["ops"]["id"]
        before, state = self.sql("SELECT runner_id,state FROM attempts WHERE id=?", (attempt,))[0]
        count = self.practice.logs.count("GET /login 200")
        try:
            self.sql("UPDATE attempts SET runner_id=? WHERE id=?", (self.host["runner_id"], attempt))
            # A copied turn token on the former computer must not dispatch to it.
            self.open(a, "/login", expect="stale_lease")
            require(
                self.practice.logs.count("GET /login 200") == count, "wrong-computer attempt reached the practice site"
            )
            self.sql("UPDATE attempts SET runner_id=? WHERE id=?", (before, attempt))
            self.practice.slow_entered.clear()
            self.practice.slow_release.clear()
            pending = self.open(a, "/slow", asynchronous=True)
            require(self.practice.slow_entered.wait(10), "own-computer command did not start")
            self.sql("UPDATE attempts SET state='failed' WHERE id=?", (attempt,))
            self.finish(pending, expect="stale_lease")
        finally:
            self.practice.slow_release.set()
            self.sql("UPDATE attempts SET runner_id=?,state=? WHERE id=?", (before, state, attempt))
            self.select_host(self.host)

    def restarts(self):
        a = self.account("Restarts")
        self.open(a, "/login")
        self.api(f"runners/{self.host['runner_id']}/browser-host", {"on": False})
        require(self.open(a, "/login")["where"] == "own", "host change did not move session")
        self.browser(a, "close")
        self.select_host(self.host)
        self.practice.slow_entered.clear()
        self.practice.slow_release.clear()
        pending = self.open(a, "/slow", asynchronous=True)
        require(self.practice.slow_entered.wait(10), "slow navigation did not start")
        self.stop(self.runs[self.host["runner_id"]])
        self.practice.slow_release.set()
        self.finish(pending, expect="browser_timeout")
        self.start_runner(self.host)
        self.open(a, "/login")
        self.practice.slow_entered.clear()
        self.practice.slow_release.clear()
        pending = self.open(a, "/slow", asynchronous=True)
        require(self.practice.slow_entered.wait(10), "second slow navigation did not start")
        self.server.kill()
        self.server.wait(5)
        self.practice.slow_release.set()
        self.finish(pending, expect="unavailable")
        self.start_server()
        until(
            lambda: self.api(f"website-tools/{a['site']}")["host"]["online"],
            "browser transport did not reconnect after server restart",
            60,
        )
        refused = self.browser(a, "read", expect="browser_host_offline")
        require(
            "open the website again" in refused.get("detail", ""), "server restart refusal gives no recovery action"
        )
        self.sign_in(a)
        _, pid = self.chrome(a)
        self.practice.slow_entered.clear()
        self.practice.slow_release.clear()
        pending = self.open(a, "/slow", asynchronous=True)
        require(self.practice.slow_entered.wait(10), "expiring navigation did not start")
        try:
            self.finish(pending, expect="browser_timeout")
            until(lambda: exited(pid), "expired command did not finish closing Chrome", 18)
        finally:
            self.practice.slow_release.set()
        self.open(a, "/home")
        require("Signed in" in self.read(a), "timeout close did not preserve the sign-in")
        self.browser(a, "close")

    def forms(self):
        for variant in ("plain", "spa", "show", "trap"):
            a = self.account("Form " + variant, short=variant == "plain")
            self.open(a, "/login?variant=" + variant)
            text = self.read(a)
            if variant == "spa":
                old = self.ref(text, "Password")
                self.browser(a, "click", self.ref(text, "Replace inputs"))
                self.fill(a, "password", old, expect="stale_ref")
                text = self.read(a)
            self.fill(a, "username", self.ref(text, "Username"))
            if variant == "plain":
                self.read(a)
                self.picture(a)
            if variant == "trap":
                self.fill(a, "password", self.ref(text, "Password"), expect="focus_changed")
            else:
                self.fill(a, "password", self.ref(text, "Password"))
                if variant == "show":
                    self.browser(a, "click", self.ref(text, "Show password"))
                    self.browser(a, "click", self.ref(self.read(a), "Replace filled inputs"))
                    self.read(a)
                    self.picture(a)
                self.browser(a, "click", self.ref(self.read(a), "Sign in"))
                require("Signed in" in self.read(a), variant + " did not sign in")
            self.browser(a, "close")
        a = self.account("Two step")
        self.open(a, "/two-step")
        self.fill(a, "username", self.ref(self.read(a), "Username"))
        self.browser(a, "press", "Enter")
        self.fill(a, "password", self.ref(self.read(a), "Password"))
        self.browser(a, "press", "Enter")
        require("Signed in" in self.read(a), "two-step did not sign in")
        self.browser(a, "close")
        for cross in (False, True):
            a = self.account("Iframe " + str(cross))
            before = len(self.practice.sessions)
            self.open(a, "/iframe" + ("?cross=1" if cross else ""))
            if cross:
                # Chrome blocks autofocus across origins; keyboard navigation enters the first frame field.
                self.browser(a, "press", "Tab")
            self.fill(a, "username")
            self.browser(a, "press", "Tab")
            self.fill(a, "password")
            self.picture(a)
            self.browser(a, "press", "Enter")
            require(
                "Signed in" in self.read(a) if not cross else len(self.practice.sessions) == before + 1,
                "iframe sign-in failed",
            )
            self.picture(a)
            self.browser(a, "close")

    def challenges(self):
        a = self.account("Authenticator", twofa=True, shared=True)
        self.api(f"runners/{self.host['runner_id']}/browser-host", {"on": True}, token="ben-test", expect="forbidden")
        self.open(a, "/login")
        text = self.read(a)
        self.fill(a, "username", self.ref(text, "Username"))
        self.fill(a, "password", self.ref(text, "Password"))
        self.browser(a, "press", "Enter")
        for n in (-1, 0, 1):
            self.canaries.add(totp.code(a["key"], time.time() + n * 30))
        self.fill(a, "code", self.ref(self.read(a), "Code"))
        self.picture(a)
        self.browser(a, "press", "Enter")
        require("Signed in" in self.read(a), "TOTP sign-in failed")
        for own in (False, True):
            if own:
                self.api(f"runners/{self.host['runner_id']}/browser-host", {"on": False})
            try:
                for path in ("/texted", "/captcha"):
                    self.open(a, path)
                    asked = self.cli(["browser", "needs-you", a["site"], "Complete the practice check."])
                    base = f"website-tools/{a['site']}/accounts/{a['id']}/browser"
                    # Ben manages this bot and task but is neither an admin nor a credential admin.
                    result = self.api(base, {}, token="ben-test")
                    require(result["where"] == ("own" if own else "central"), "person's view moved the challenge")
                    self.frame(base, "ben-test")
                    require("Person check" in self.read(a), "person's view navigated away from the challenge")
                    self.api(base + "/control", {"take": True}, token="ben-test")
                    self.api(base + "/input", {"kind": "key", "key": "Enter"}, token="ben-test")
                    self.api(base + "/control", {"take": False}, token="ben-test")
                    self.api(
                        f"tasks/{self.tasks['ops']['id']}/asks/{asked['requirement_id']}/answer",
                        {"value": "Done"},
                        token="ben-test",
                    )
                    self.api(base + "/control", {"take": True}, token="ben-test", expect="not_found")
                    require("Signed in" in self.read(a), "bot did not continue after Done")
            finally:
                if own:
                    self.select_host(self.host)
        self.browser(a, "close")

    def frame(self, base, token):
        req = urllib.request.Request(
            self.url + "/api/v2/" + base + "/frame", headers={"Authorization": "Bearer " + token}
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read()
        require(raw.startswith(b"\xff\xd8"), "person's frame is not a JPEG")
        path = self.dir / f"picture-{len(self.screenshots)}.jpg"
        path.write_bytes(raw)
        self.screenshots.append(path)

    def redirects(self):
        a = self.account("Redirects")
        self.sign_in(a, "/redirect")
        self.open(a, "/redirect?unlisted=1")
        key = (f"unlisted.localhost:{self.practice.port}", "password")
        before = self.practice.inputs.get(key, 0)
        self.fill(a, "password", self.ref(self.read(a), "Password"), expect="not_on_site")
        require(self.practice.inputs.get(key, 0) == before, "unlisted host received input")
        self.open(a, "/redirect?port=1")
        key = (f"practice.localhost:{self.practice.other_port}", "password")
        self.fill(a, "password", self.ref(self.read(a), "Password"), expect="not_on_site")
        require(self.practice.inputs.get(key, 0) == 0, "unlisted port received input")
        self.picture(a)
        self.browser(a, "close")

    def persistence(self):
        a = self.account("Persistence")
        self.sign_in(a)
        self.browser(a, "close")
        self.open(a, "/home")
        require("Signed in" in self.read(a), "close lost the session")
        self.stop(self.runs[self.host["runner_id"]])
        self.start_runner(self.host)
        self.phase = "Persistence after runner restart"
        self.open(a, "/home")
        require("Signed in" in self.read(a), "runner restart lost the profile")
        self.practice.sessions.clear()
        self.open(a, "/home")
        require("Practice sign in" in self.read(a), "expired session was not signed out")
        self.sign_in(a)
        self.browser(a, "close")

        a = self.account("Session cookie only")
        self.sign_in(a, "/login?variant=session")
        self.stop(self.runs[self.host["runner_id"]])
        self.start_runner(self.host)
        self.open(a, "/home")
        require("Practice sign in" in self.read(a), "session-only cookie survived a clean browser restart")
        self.sign_in(a, "/login?variant=session")
        self.browser(a, "close")

    def concurrency(self):
        a = self.account("Shared", shared=True)
        b = self.account("Parallel")
        self.open(a, "/login")
        self.browser(a, "read", bot="finance", expect="busy")
        self.browser(a, "close")
        self.open(a, "/login", bot="finance")
        self.browser(a, "close", bot="finance")
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(self.open, account, "/login") for account in (a, b)]
            for future in futures:
                future.result()
        base = f"website-tools/{a['site']}/accounts/{a['id']}/browser"
        self.api(base + "/control", {"take": True})
        self.browser(a, "read", expect="person_in_control")
        self.api(base + "/control", {"take": False})
        self.read(a)
        self.browser(a, "close")
        self.browser(b, "close")
        self.queued_control()
        self.evicted_views()

    def evicted_views(self):
        accounts = [self.account(f"Eviction {n}") for n in range(10)]
        for account in accounts[:8]:
            self.open(account, "/login")
        first_pid = self.chrome(accounts[0])[1]
        second_pid = self.chrome(accounts[1])[1]
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(self.open, account, "/login") for account in accounts[8:]]
            for future in futures:
                future.result()
        until(lambda: exited(first_pid) and exited(second_pid), "two starts did not evict the two oldest browsers")
        folder = self.host["config"].parent / f"state-{self.host['runner_id']}" / "browsers"
        pids = []
        for lock in folder.glob("*/profile/SingletonLock"):
            try:
                pid = int(os.readlink(lock).rsplit("-", 1)[-1])
            except (FileNotFoundError, ValueError):
                continue
            if not exited(pid):
                pids.append(pid)
        require(len(pids) == 8, "simultaneous starts exceeded the eight-browser cap")
        # The person's request itself launches an evicted browser, so keep=true must navigate a fresh tab.
        account = accounts[1]
        base = f"website-tools/{account['site']}/accounts/{account['id']}/browser"
        self.api(base, {})
        self.frame(base, "ana-test")
        require("Practice sign in" in self.read(account), "person's wake did not land on the site")
        account = accounts[0]
        self.browser(account, "read", expect="session_lost")
        base = f"website-tools/{account['site']}/accounts/{account['id']}/browser"
        self.api(base, {})
        require("Practice sign in" in self.read(account), "person's wake did not recover a lost session")
        for account in accounts:
            self.browser(account, "close")

    def queued_control(self):
        account = self.account("Queued control")
        self.open(account, "/click-test")
        ref = self.ref(self.read(account), "Record click")
        runner = self.runs[self.host["runner_id"]]
        before = self.practice.clicks
        os.kill(runner.pid, signal.SIGSTOP)
        try:
            # Supersede the stopped runner's outstanding poll before the bot queues its click.
            self.api("runner-browser-commands?wait=0", token=self.host["token"])
            pending = self.browser(account, "click", ref, asynchronous=True)
            until(
                lambda: self.api("live-browser-queued?account=" + account["id"])["click"] == 1,
                "bot click did not queue",
            )
            base = f"website-tools/{account['site']}/accounts/{account['id']}/browser"
            self.api(base + "/control", {"take": True})
            self.finish(pending, expect="person_in_control")
        finally:
            os.kill(runner.pid, signal.SIGCONT)
        self.api(base + "/control", {"take": False})
        self.read(account)
        require(self.practice.clicks == before, "a click queued before person control ran afterward")
        self.browser(account, "close")

    def chrome(self, account):
        profile = (
            self.host["config"].parent / f"state-{self.host['runner_id']}" / "browsers" / account["id"] / "profile"
        )
        pid = int(os.readlink(profile / "SingletonLock").rsplit("-", 1)[-1])
        require(pid in tree(self.runs[self.host["runner_id"]].pid), "Chrome is not this suite's child")
        return profile, pid

    def files_and_crash(self):
        a = self.account("Files and crash")
        self.sign_in(a)
        self.browser(a, "click", self.ref(self.read(a), "Download report"))
        result = self.browser(a, "download", "--out", str(self.dir / "download"))
        path = Path(result["path"])
        require(path.read_bytes() == b"practice report\n42\n", "download content changed")
        self.browser(a, "upload", self.ref(self.read(a), "Attachment"), str(path))
        self.browser(a, "click", self.ref(self.read(a), "Upload report"))
        require("Upload received" in self.read(a), "upload round trip changed bytes")
        profile, pid = self.chrome(a)
        os.kill(pid, signal.SIGKILL)
        until(
            lambda: exited(pid),
            "Chrome did not exit",
        )
        self.open(a, "/home")
        text = self.read(a)
        require("Signed in" in text or "Practice sign in" in text, "Chrome crash did not reopen the practice page")
        relaunched = int(os.readlink(profile / "SingletonLock").rsplit("-", 1)[-1])
        require(
            relaunched != pid and relaunched in tree(self.runs[self.host["runner_id"]].pid), "Chrome did not relaunch"
        )
        self.browser(a, "close")

    def picture(self, account):
        path = self.dir / f"picture-{len(self.screenshots)}.jpg"
        self.browser(account, "screenshot", "--out", str(path))
        self.screenshots.append(path)

    def leaks(self):
        # People-only account metadata deliberately contains usernames; capture the bot surface and Activity.
        for (tool,) in self.sql("SELECT id FROM tools WHERE kind='website'"):
            self.api(f"website-tools/{tool}", token=self.tokens["ops"]["token"], capture=True)
            self.api(f"website-tools/{tool}/activity", capture=True)
        for table in ("events", "tool_calls", "messages", "idempotency"):
            self.output.append((table, repr(self.sql(f"SELECT * FROM {table}"))))

    def scan(self):
        for path in self.logs:
            self.output.append((path.name, path.read_text(errors="replace")))
        self.output.extend(("practice request log", line) for line in self.practice.logs)
        for path in self.screenshots:
            result = subprocess.run([str(self.ocr), str(path)], capture_output=True, text=True, check=True)
            self.output.extend((("screenshot pixels", result.stdout), ("OCR stderr", result.stderr)))
        for source, text in self.output:
            if source == "screenshot pixels":
                text = re.sub(r"[^a-z0-9]", "", text.casefold())
                values = [re.sub(r"[^a-z0-9]", "", value.casefold()) for value in self.canaries]
            else:
                values = self.canaries
            require(not any(value in text for value in values), f"a secret appeared in {source}")

    def close(self):
        self.practice.slow_release.set()
        for proc in reversed(self.processes):
            self.stop(proc)
        self.practice.close()


SCENARIOS = [
    (10, "host online/offline/missing Chrome/revoked/central-only", "routing"),
    (11, "attempt delivery boundary", "ended_attempt"),
    (12, "host change and runner/server interruption", "restarts"),
    (13, "plain/two-step/SPA/iframes/show-password/focus trap", "forms"),
    (14, "TOTP and person Done for text/captcha", "challenges"),
    (15, "listed SSO and unlisted redirect", "redirects"),
    (16, "profile persistence and expired sign-in", "persistence"),
    (17, "account holds, parallel accounts and person control", "concurrency"),
    (18, "download/upload and Chrome crash recovery", "files_and_crash"),
]


def interrupted(_signal, _frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--row", type=int, action="append", choices=range(10, 19), help="run only this matrix row (repeatable)"
    )
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, interrupted)
    require(args.repeat > 0, "--repeat must be positive")
    require(Path("/Applications/Google Chrome.app").exists(), "Google Chrome is required")
    # Build once for the whole invocation; Cargo's cache makes the next invocation incremental.
    built = subprocess.run(
        [
            str(ROOT / "scripts/heavy.sh"),
            "cargo",
            "build",
            "--manifest-path",
            str(ROOT / "runner-rs/Cargo.toml"),
            "--bin",
            "tico-runner",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    require(built.returncode == 0, "runner build failed")
    runner = Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "runner-rs/target")) / "debug/tico-runner"
    failed = False
    with tempfile.TemporaryDirectory(prefix="tico-website-live-", dir="/tmp") as work:
        base = Path(work)
        ocr = base / "ocr"
        compiled = subprocess.run(
            ["swiftc", str(HERE / "ocr.swift"), "-o", str(ocr)], capture_output=True, text=True, check=False
        )
        require(compiled.returncode == 0, "Vision OCR build failed")
        for repeat in range(1, args.repeat + 1):
            directory = base / str(repeat)
            directory.mkdir(mode=0o700)
            live = Live(directory, runner, ocr)
            captured = False
            try:
                live.setup()
                for row, label, method in SCENARIOS:
                    if args.row and row not in args.row:
                        continue
                    try:
                        getattr(live, method)()
                        print(f"PASS run {repeat} row {row}: {label}", flush=True)
                    except ERRORS as exc:
                        # Never include page content, subprocess output or arbitrary exception text in a report.
                        reason = reason_for(exc)
                        print(f"FAIL run {repeat} row {row}: {reason}", flush=True)
                        failed = True
                        break
                try:
                    live.leaks()
                    captured = True
                except ERRORS as exc:
                    reason = reason_for(exc)
                    print(f"FAIL run {repeat} row 19: {reason}", flush=True)
                    failed = True
            except ERRORS as exc:
                reason = reason_for(exc)
                print(f"FAIL run {repeat} setup: {reason}", flush=True)
                failed = True
            finally:
                live.close()
                try:
                    live.scan()
                    if captured:
                        print(
                            f"PASS run {repeat} row 19: CLI/log/event/Activity/storage/screenshot canaries after cleanup",
                            flush=True,
                        )
                except ERRORS as exc:
                    print(f"FAIL run {repeat} row 19: {reason_for(exc)}", flush=True)
                    failed = True
            if failed:
                break
    print(
        "website-tools: FAIL" if failed else f"website-tools: PASS ({args.repeat} consecutive runs; no retries)",
        flush=True,
    )
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
