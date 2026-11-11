"""A disposable hub on a fake clock, with a fake GitHub and one runner for every bot.

The redesign scenarios and the UI's real-hub tests (ui/tests/support/real_hub_server.py) drive the real server
in-process: real API calls with test tokens, real webhooks for pull requests, and the real scheduler pass on demand. Only
time and GitHub are made up. This was the factory simulation's world (evals/factory, deleted at the Pipelines v3
cut-over) without its own pipeline, its bots' replies or its reads of them: every scenario builds the org it needs."""

import datetime as dt
import hashlib
import hmac
import json
import os
import random
import re
import tempfile
import traceback
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("TICO_REGISTRY_DIR", str(ROOT / "backend/tests/fixtures/registry"))
for name, value in {
    "TICO_UPDATE_CHECK": "off",
    "TICO_RUNNER_CONTAINER_PROBE": "off",
    "TICO_SQLITE_SYNC": "off",
}.items():
    os.environ[name] = value

import time_machine

START = dt.datetime(2026, 10, 20, 9, tzinfo=dt.timezone.utc)
# A world's environment while it runs: no support tickets, no usage count, and an HQ address nothing answers on.
OFFLINE = {"TICO_SUPPORT": "off", "TICO_TELEMETRY": "off", "TICO_HQ_URL": "http://127.0.0.1:9"}
SECRET = "redesign-world"
REPO = "acme/app"


class Refusal(Exception):
    def __init__(self, status, body):
        super().__init__(f"{status}: {body[:300]}")
        self.status, self.body = status, body


class World:
    """One run of one scenario. Use as a context manager: it starts the clock and the server, and stops both."""

    def __init__(self, bots, tick_minutes=5, rng=None, capacity=4):
        self.bot_slugs = list(bots)
        self.rng = rng or random.Random(0)
        self.ci = {}  # pr number -> (head, when its checks finish, passed)
        self.tick = dt.timedelta(minutes=tick_minutes)
        self.tmp = Path(tempfile.mkdtemp(prefix="redesign-world-"))
        self.prs = {}  # pr number -> {"task": id, "head": sha, "merged": bool}
        self.next_pr = 100
        self.capacity = capacity  # the computer's slots, as its heartbeat says
        self.server_errors = []  # (when, route, status, body) for each call the server answered with a 5xx
        self.patches = []  # (module, name, original) for each patch(), put back on __exit__
        self.github_reads = False

    # ------------------------------------------------------------------ lifecycle
    def __enter__(self):
        # Nothing leaves for Tico HQ. Put back on __exit__, so a test process that builds a world keeps its own settings.
        self._environ = {name: os.environ.get(name) for name in OFFLINE}
        os.environ.update(OFFLINE)
        self.travel = time_machine.travel(START, tick=False)
        self.clock = self.travel.start()
        # Ids break ties in the server's ordering (`ORDER BY created_at, id`): drawn from the run's own generator, the
        # same seed runs the same way.
        ids = random.Random(self.rng.random())
        self._uuid4, uuid.uuid4 = uuid.uuid4, lambda: uuid.UUID(int=ids.getrandbits(128), version=4)
        from fastapi.testclient import TestClient
        from backend.app import create_app
        from backend.auth import Identity
        from backend import bot_settings, maintenance
        from backend.config import Settings
        from backend.scheduler import Scheduler
        from backend.store import H, encode

        self.H = H

        # The fake clock advances only between completed passes: Maintenance runs inline, not on its own thread.
        def inline_maintenance(start):
            def run(store, auth, run_id, only, wait):
                return start(store, auth, run_id, only, wait=True)

            return run

        self.patch(maintenance, "_start", inline_maintenance)
        registry = self.tmp / "registry"
        registry.mkdir()
        (registry / "hub-access.yaml").write_text(json.dumps({"owner": "ana@acme.example"}))
        settings = Settings(
            db_path=self.tmp / "hub.db",
            registry_dir=registry,
            github_webhook_secret=SECRET,
            release_commit="",
            release_repo=REPO,
            test_identities={
                "ana-test": Identity("human:ana", "owner", "ana@acme.example"),
                # The Release Owner ships between its turns, signed in as an agent.
                "release-test": Identity("bot:release-manager", "bot", agent="hermes"),
            },
        )
        self.settings = settings
        self.app = create_app(settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.store = self.app.state.store
        with self.store.transaction() as c:
            bots = {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in self.bot_slugs}
            H.sync_registry(c, bots, {"people": [{"id": "ana", "email": "ana@acme.example"}]})
            c.execute(
                "INSERT INTO registry_metadata VALUES('people',?)",
                (encode({"people": [{"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]}]}),),
            )
            for slug, config in bots.items():
                c.execute("INSERT INTO bot_config(bot,team,owner_id) VALUES(?,?,?)", (slug, "engineering", "ana"))
                bot_settings.write(c, slug, config)
            if "release-manager" in bots:  # signed in as an agent, as an external Release Owner is
                c.execute(
                    "INSERT INTO agents(bot,harness,token_hash,created_at,created_by) "
                    "VALUES('release-manager','hermes','x-release',?,'human:ana')",
                    (H.now(),),
                )
            c.execute(
                "INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({"completed": "2026-01-01T00:00:00Z"}),)
            )
        self.scheduler = Scheduler(self.store, self.app.state.execution)
        code = self.post("enrollments", {"operator": "ana"})["code"]
        self.runner = self.post("runners/enroll", {"code": code, "label": "Sim", "platform": "test"})
        for slug in self.bot_slugs:
            self.post("bots/" + slug + "/assignment", {"runner_id": self.runner["runner_id"], "expected_generation": 0})
        self.heartbeat(self.bot_slugs)
        self.configure()
        return self

    def configure(self):
        """What a subclass sets up once the server is running and the bots are on the runner."""

    def __exit__(self, *exc):
        for module, name, original in reversed(self.patches):
            setattr(module, name, original)
        self.client.__exit__(*exc)
        self.travel.stop()
        uuid.uuid4 = self._uuid4
        for name, value in self._environ.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def patch(self, module, name, wrap):
        """Replace module.name with wrap(original) for this run only: __exit__ puts the original back."""
        original = getattr(module, name)
        setattr(module, name, wrap(original))
        self.patches.append((module, name, original))

    # ------------------------------------------------------------------ time
    def now(self):
        return dt.datetime.now(dt.timezone.utc)

    def advance(self, delta=None, ready=None, beat=None):
        """Move the clock on. The runner reports in every 50 seconds on the way, as a real one does: a longer gap reads
        as a computer that slept, and its claims wait for it to settle (execution.AWAKE_GAP)."""
        left = (delta or self.tick).total_seconds()
        while left > 0:
            step = min(50, left)
            self.clock.shift(step)
            left -= step
            self.finish_ci()
            self.heartbeat(self.bot_slugs if ready is None else ready)
            if beat:
                beat()

    def scheduler_pass(self):
        """The server's background pass: the scheduler tick, and the GitHub wakes the app's loop sends."""
        from backend.github import flush_wakes

        self.scheduler.tick()
        with self.store.transaction() as c:
            flush_wakes(c)

    def heartbeat(self, ready):
        self.post(
            "runners/heartbeat",
            {
                "version": "test",
                "platform": "test",
                "capacity": self.capacity,
                "readiness": {slug: slug in ready for slug in self.bot_slugs},
            },
            token=self.runner["token"],
        )

    # ------------------------------------------------------------------ API
    def call(self, method, path, body=None, token="ana-test", ok=True):
        """One API call: a refusal raises (or answers None when `ok` is off), and a server error is recorded and answers
        None."""
        status, reply = self.request(method, path, body, token)
        if status >= 500:
            return None
        if status >= 400:
            if ok:
                raise Refusal(status, str(reply))
            return None
        return reply

    def request(self, method, path, body=None, token="ana-test"):
        """One API call that answers with its status and body rather than raising. A server error (a 500, or the app
        raising) is recorded with its route and body, and the run goes on."""
        headers = {"Authorization": "Bearer " + token}
        if method != "get":  # the server wants a key on every write, PUT included
            headers["Idempotency-Key"] = str(uuid.uuid4())
        route = f"{method.upper()} /api/v2/{path}"
        try:
            response = getattr(self.client, method)(
                "/api/v2/" + path, headers=headers, **({"json": body} if body is not None else {})
            )
        except Exception:  # noqa: BLE001 - TestClient re-raises what the app raised; a live server would answer 500
            status, reply = 500, "raised:\n" + traceback.format_exc()[-3000:]
        else:
            status = response.status_code
            try:
                reply = response.json()
            except ValueError:
                reply = response.text
        if status >= 500:
            self.server_errors.append((self.now(), route, status, str(reply)[:4000]))
        return status, reply

    def post(self, path, body, token="ana-test"):
        return self.call("post", path, body, token)

    def get(self, path, token="ana-test"):
        return self.call("get", path, token=token)

    def sql(self, query, args=()):
        with self.store.read() as c:
            return [dict(r) for r in c.execute(query, args)]

    # ------------------------------------------------------------------ fake GitHub
    def hook(self, event, payload):
        body = json.dumps(payload).encode()
        sign = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        response = self.client.post(
            "/api/v2/github/webhook",
            content=body,
            headers={"X-GitHub-Event": event, "X-Hub-Signature-256": sign, "Content-Type": "application/json"},
        )
        if response.status_code >= 500:
            self.server_errors.append(
                (self.now(), "POST /api/v2/github/webhook", response.status_code, response.text[:4000])
            )
            return
        assert response.status_code == 200, response.text

    def pr_url(self, number):
        return f"https://github.com/{REPO}/pull/{number}"

    def pr_payload(self, number, action, merged=False):
        pr = self.prs[number]
        return {
            "action": action,
            "repository": {"full_name": REPO},
            "number": number,
            "pull_request": {
                "number": number,
                "html_url": self.pr_url(number),
                "state": "closed" if action == "closed" else "open",
                "merged": merged,
                "draft": False,
                "mergeable": True,
                "mergeable_state": "clean",
                "title": "Change",
                "head": {"sha": pr["head"], "ref": f"b{number}"},
                "base": {"ref": "main", "sha": "c" * 40},
                "user": {"login": "sim-bot"},
                "merge_commit_sha": ("d" * 40) if merged else None,
            },
        }

    def open_pr(self, task_id, token="ana-test"):
        number = self.next_pr
        self.next_pr += 1
        self.prs[number] = {"task": task_id, "head": uuid.uuid4().hex + uuid.uuid4().hex[:8], "merged": False}
        self.post(f"tasks/{task_id}/links", {"url": self.pr_url(number)}, token=token)
        self.hook("pull_request", self.pr_payload(number, "opened"))
        self.start_ci(number)
        return number

    def push(self, number):
        """A new commit on the pull request: a new head, GitHub's `synchronize`, and CI on it. Returns the head."""
        pr = self.prs[number]
        pr.setdefault("old_heads", []).append(pr["head"])
        pr["head"] = uuid.uuid4().hex + uuid.uuid4().hex[:8]
        pr.setdefault("heads", []).append((self.now(), pr["head"]))
        self.hook("pull_request", self.pr_payload(number, "synchronize"))
        self.start_ci(number)
        return pr["head"]

    # CI on a new head takes 3 to 15 minutes, usually about 6. It always passes.
    def start_ci(self, number):
        minutes = self.rng.triangular(3, 15, 6)
        self.ci[number] = (self.prs[number]["head"], self.now() + dt.timedelta(minutes=minutes), True)

    def finish_ci(self):
        for number, (head, due, passed) in list(self.ci.items()):
            if due <= self.now():
                del self.ci[number]
                if self.prs[number]["head"] == head and not self.prs[number]["merged"]:
                    self.hook(
                        "check_suite",
                        {
                            "action": "completed",
                            "repository": {"full_name": REPO},
                            "check_suite": {
                                "id": number * 1000 + len(head),
                                "head_sha": head,
                                "status": "completed",
                                "conclusion": "success" if passed else "failure",
                                "app": {"slug": "github-actions"},
                                "pull_requests": [{"number": number, "html_url": self.pr_url(number)}],
                            },
                        },
                    )
                    self.prs[number]["ci"] = (head, passed)

    def merge(self, number):
        if self.prs[number]["merged"]:
            return
        self.prs[number]["merged"] = True
        self.prs[number]["merged_at"] = self.H.now()
        self.hook("pull_request", self.pr_payload(number, "closed", merged=True))

    def connect_github(self):
        """Answer the GitHub App's reads from this fake GitHub, as a hub with the App connected does. Only the HTTP
        boundary is faked (the App's row, its token mint and its calls), as backend/tests do."""
        service = self.app.state.github_app
        self.github_reads = True
        token = {"until": None}

        def mint(repos, permissions, **kw):
            if not token["until"] or token["until"] <= self.now():
                token["until"] = self.now() + dt.timedelta(hours=1)
            return "sim-token", token["until"].strftime("%Y-%m-%dT%H:%M:%SZ")

        def answer(status, payload):
            return SimpleNamespace(status_code=status, json=lambda: payload, headers={}, text=json.dumps(payload))

        def call(method, path, **kw):
            if found := re.fullmatch(rf"/repos/{REPO}/pulls/(\d+)", path):
                pr = self.prs.get(int(found[1]))
                if not pr:
                    return answer(404, {})
                if method == "PATCH":
                    pr["body"] = (kw.get("json") or {}).get("body", "")
                    return answer(200, {"number": int(found[1])})
                return answer(
                    200,
                    {
                        "number": int(found[1]),
                        "title": f"Change {found[1]}",
                        "html_url": f"https://github.example/{REPO}/pull/{found[1]}",
                        "body": pr.get("body", ""),
                        "head": {"sha": pr["head"]},
                        "merged": pr["merged"],
                    },
                )
            if found := re.fullmatch(rf"/repos/{REPO}/commits/(\w+)/(check-runs|statuses)", path):
                if found[2] == "statuses":
                    return answer(200, [])
                runs = []
                for number, pr in self.prs.items():
                    head, passed = pr.get("ci") or (None, None)
                    if pr["head"] == found[1] or head == found[1]:
                        done = head == found[1]
                        runs.append(
                            {
                                "id": number * 1000,
                                "name": "tests",
                                "status": "completed" if done else "in_progress",
                                "conclusion": ("success" if passed else "failure") if done else None,
                            }
                        )
                return answer(200, {"check_runs": runs})
            if path == f"/repos/{REPO}/actions/runs":
                return answer(200, {"workflow_runs": []})
            return answer(404, {"message": "Not Found"})

        service.row = lambda c=None: {"org": REPO.split("/")[0], "installation_id": 1, "app_id": 1}
        service.mint = mint
        service._call = call
