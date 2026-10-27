"""A disposable hub on a fake clock, with a fake GitHub, a Dev tickets board and one runner for every bot.

The simulation drives the real server in-process: real API calls with each bot's attempt token, real webhooks for pull
requests, and the real scheduler pass on every tick. Only the bots' replies (bots.py) and time are made up."""
import datetime as dt
import hashlib
import hmac
import json
import os
import random
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("TICO_REGISTRY_DIR", str(ROOT / "backend/tests/fixtures/registry"))
for name, value in {"TICO_UPDATE_CHECK": "off", "TICO_RUNNER_CONTAINER_PROBE": "off", "TICO_SQLITE_SYNC": "off"}.items():
    os.environ[name] = value

import time_machine  # noqa: E402

START = dt.datetime(2026, 10, 20, 9, tzinfo=dt.timezone.utc)
SECRET = "factory-sim"
REPO = "acme/app"



class Refusal(Exception):
    def __init__(self, status, body):
        super().__init__(f"{status}: {body[:300]}")
        self.status, self.body = status, body


class World:
    """One run of one scenario. Use as a context manager: it starts the clock and the server, and stops both."""

    def __init__(self, board, bots, tick_minutes=5, rng=None, ci_fail=0.0):
        self.board = board
        self.bot_slugs = list(bots)
        self.roles = {}
        for slug, (role, _family) in (bots.items() if isinstance(bots, dict) else []):
            self.roles.setdefault(role, []).append(slug)
        self.fallbacks = []      # (when, who, ticket, what): a move or hand-off the board should have made itself
        self.stuck_told = []     # (when, ticket id): the lead told the owner of a stuck ticket
        self.rng = rng or random.Random(0)
        self.ci_fail = ci_fail   # chance a CI run on a new head fails (a flaky or broken change)
        self.ci = {}             # pr number -> (head, when its checks finish, passed)
        self.ci_skip = 0.0       # chance a new PR's only CI check is skipped or neutral on every head (a path filter)
        self.tick = dt.timedelta(minutes=tick_minutes)
        self.tmp = Path(tempfile.mkdtemp(prefix="factory-sim-"))
        self.prs = {}        # pr number -> {"task": id, "head": sha, "merged": bool}
        self.next_pr = 100

    # ------------------------------------------------------------------ lifecycle
    def __enter__(self):
        self.travel = time_machine.travel(START, tick=False)
        self.clock = self.travel.start()
        # Ids break ties in the server's ordering (`ORDER BY created, id`): drawn from the run's own generator, the
        # same seed runs the same way.
        ids = random.Random(self.rng.random())
        self._uuid4, uuid.uuid4 = uuid.uuid4, lambda: uuid.UUID(int=ids.getrandbits(128), version=4)
        from fastapi.testclient import TestClient
        from backend.app import create_app
        from backend.auth import Identity
        from backend.config import Settings
        from backend.scheduler import Scheduler
        from backend.store import H, encode
        from backend import rules as R
        self.H = H
        registry = self.tmp / "registry"
        registry.mkdir()
        (registry / "hub-access.yaml").write_text(json.dumps({"owner": "ana@acme.example"}))
        settings = Settings(db_path=self.tmp / "hub.db", registry_dir=registry, github_webhook_secret=SECRET,
                            release_commit="", release_repo=REPO,
                            test_identities={"ana-test": Identity("human:ana", "owner", "ana@acme.example"),
                                             # The Release Manager ships between its turns, outside the sim's queue.
                                             "release-test": Identity("bot:release-manager", "bot", agent="hermes")})
        self.app = create_app(settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.store = self.app.state.store
        with self.store.transaction() as c:
            bots = {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in self.bot_slugs}
            H.sync_registry(c, bots, {"people": [{"id": "ana", "email": "ana@acme.example"}]})
            c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode({"people": [
                {"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]}]}),))
            for slug, config in bots.items():
                c.execute("INSERT INTO bot_config(bot,config_json,team,operator) VALUES(?,?,?,?)",
                          (slug, encode(config), "engineering", "ana"))
            if "release-manager" in bots:   # signed in as an agent, as an external Release Manager is
                c.execute("INSERT INTO agents(bot,harness,token_hash,created,created_by) "
                          "VALUES('release-manager','hermes','x-release',?,'human:ana')", (H.now(),))
            c.execute("INSERT INTO registry_metadata VALUES('onboarding',?)",
                      (encode({"completed": "2026-01-01T00:00:00Z"}),))
            self.dev = H.type_create(c, H.KEEPER, self.board.name,
                                     [{"name": s.name, "status": s.status} for s in self.board.steps], bots="work")
            R.seed_dev(c)
            if "engineering-lead" in bots:   # a live install's Dev type is its Head of Engineering's (fixed by role)
                c.execute("UPDATE task_types SET manager='engineering-lead' WHERE id=?", (self.dev["id"],))
        self.scheduler = Scheduler(self.store, self.app.state.execution)
        code = self.post("enrollments", {"operator": "ana"})["code"]
        self.runner = self.post("runners/enroll", {"code": code, "label": "Sim", "platform": "test"})
        for slug in self.bot_slugs:
            self.post("bots/" + slug + "/assignment", {"runner_id": self.runner["runner_id"], "expected_generation": 0})
        self.heartbeat(self.bot_slugs)
        self.configure()
        return self

    def configure(self):
        """Set the board up as a team would, through the API only: each step's flow and check pool, and the lead's
        hourly sweep."""
        from .board import flows
        ids = {st["name"]: st["id"] for st in self.dev["steps"]}
        steps = [{**row, "id": ids[row["name"]]} for row in flows(self.board, self.roles)]
        self.dev = self.post("task-types/" + self.dev["id"], {"steps": steps})["type"]
        for lead in self.roles.get("lead", []) if self.board.lead_sweep else []:
            self.post(f"bots/{lead}/routines", {
                "title": "Hourly board sweep", "key": "board-sweep",
                "cron": f"0 */{max(1, int(self.board.lead_sweep_hours))} * * *", "timezone": "UTC",
                "text": "Look over the board for stuck tickets and get each one moving."})

    def __exit__(self, *exc):
        self.client.__exit__(*exc)
        self.travel.stop()
        uuid.uuid4 = self._uuid4

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
        if getattr(self, "github_reads", False):
            # With the GitHub App connected the app's loop also retries refused review moves and reads gate holds
            # afresh (backend/app.py); both read GitHub, so without the App there is nothing for them to do.
            from backend import move_retry, task_blocked
            move_retry.tick(self.app.state.github_app, force=True)
            task_blocked.fresh(self.app.state.github_app, force=True)

    def retry_moves(self):
        """The server's retry of refused review moves (backend/move_retry.py), which the app runs beside its GitHub
        sync: a move the review gate refused is made once what refused it changes (a receipt posted, CI finished)."""
        from backend import move_retry
        return move_retry.tick(self.app.state.github_app, store=self.store, force=True)

    def heartbeat(self, ready):
        self.post("runners/heartbeat", {"version": "test", "platform": "test",
                                        "readiness": {slug: slug in ready for slug in self.bot_slugs}},
                  token=self.runner["token"])

    # ------------------------------------------------------------------ API
    def call(self, method, path, body=None, token="ana-test", ok=True):
        headers = {"Authorization": "Bearer " + token}
        if method == "post":
            headers["Idempotency-Key"] = str(uuid.uuid4())
        response = getattr(self.client, method)("/api/v2/" + path, headers=headers,
                                                **({"json": body} if body is not None else {}))
        if response.status_code >= 400:
            if ok:
                raise Refusal(response.status_code, response.text)
            return None
        return response.json()

    def post(self, path, body, token="ana-test"):
        return self.call("post", path, body, token)

    def get(self, path, token="ana-test"):
        return self.call("get", path, token=token)

    def task(self, task_id):
        return self.get("tasks/" + task_id)["task"]

    def update(self, task_id, token="ana-test", **fields):
        """Change a task; a refused change raises Refusal with the server's reason."""
        result = self.post("tasks/" + task_id, {"version": self.task(task_id)["version"], **fields}, token=token)
        return result.get("task", result)

    def step_of(self, task):
        steps = {s["id"]: s["name"] for s in self.dev["steps"]}
        if not steps.get(task.get("step_id")):
            with self.store.read() as c:
                steps.update({r["id"]: r["name"] for r in c.execute(
                    "SELECT id,name FROM task_steps WHERE type_id=?", (self.dev["id"],))})
        return steps.get(task.get("step_id"))

    def sql(self, query, args=()):
        with self.store.read() as c:
            return [dict(r) for r in c.execute(query, args)]

    # ------------------------------------------------------------------ fake GitHub
    def hook(self, event, payload):
        body = json.dumps(payload).encode()
        sign = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        response = self.client.post("/api/v2/github/webhook", content=body, headers={
            "X-GitHub-Event": event, "X-Hub-Signature-256": sign,
            "Content-Type": "application/json"})
        assert response.status_code == 200, response.text

    def pr_url(self, number):
        return f"https://github.com/{REPO}/pull/{number}"

    def pr_payload(self, number, action, merged=False):
        pr = self.prs[number]
        return {"action": action, "repository": {"full_name": REPO}, "number": number, "pull_request": {
            "number": number, "html_url": self.pr_url(number), "state": "closed" if action == "closed" else "open",
            "merged": merged, "draft": False, "mergeable": True, "mergeable_state": "clean", "title": "Change", "head": {"sha": pr["head"], "ref": f"b{number}"},
            "base": {"ref": "main", "sha": "c" * 40}, "user": {"login": "sim-bot"}, "merge_commit_sha":
            ("d" * 40) if merged else None}}

    def open_pr(self, task_id, token="ana-test"):
        number = self.next_pr
        self.next_pr += 1
        self.prs[number] = {"task": task_id, "head": uuid.uuid4().hex + uuid.uuid4().hex[:8], "merged": False}
        self.prs[number]["heads"] = [(self.now(), self.prs[number]["head"])]
        if self.ci_skip and self.rng.random() < self.ci_skip:
            self.prs[number]["skip_ci"] = True
        self.post(f"tasks/{task_id}/links", {"url": self.pr_url(number)}, token=token)
        self.hook("pull_request", self.pr_payload(number, "opened"))
        self.start_ci(number)
        return number

    def push(self, number):
        self.prs[number].setdefault("old_heads", []).append(self.prs[number]["head"])
        self.prs[number]["head"] = uuid.uuid4().hex + uuid.uuid4().hex[:8]
        self.prs[number].setdefault("heads", []).append((self.now(), self.prs[number]["head"]))
        self.hook("pull_request", self.pr_payload(number, "synchronize"))
        self.start_ci(number)
        return self.prs[number]["head"]

    # CI on a new head takes 3 to 15 minutes, usually about 6.
    def start_ci(self, number):
        minutes = self.rng.triangular(3, 15, 6)
        self.ci[number] = (self.prs[number]["head"], self.now() + dt.timedelta(minutes=minutes),
                           self.rng.random() >= self.ci_fail)

    def finish_ci(self):
        for number, (head, due, passed) in list(self.ci.items()):
            if due <= self.now():
                del self.ci[number]
                if self.prs[number]["head"] == head and not self.prs[number]["merged"]:
                    # A skipped-only PR's suite ends skipped or neutral: GitHub lets it merge, but no test ran.
                    skip = ("skipped", "neutral")[int(head, 16) % 2] if self.prs[number].get("skip_ci") else None
                    self.hook("check_suite", {"action": "completed", "repository": {"full_name": REPO}, "check_suite": {
                        "id": number * 1000 + len(head), "head_sha": head, "status": "completed",
                        "conclusion": skip or ("success" if passed else "failure"), "app": {"slug": "github-actions"},
                        "pull_requests": [{"number": number, "html_url": self.pr_url(number)}]}})
                    self.prs[number]["ci"] = (head, passed)

    def head_at(self, number, when):
        """The PR's head at `when` (a datetime)."""
        heads = self.prs[number].get("heads") or [(when, self.prs[number]["head"])]
        return next((head for at, head in reversed(heads) if at <= when), heads[0][1])

    def ci_state(self, number):
        """'passing', 'failing' or 'pending' for the PR's current head."""
        head, passed = self.prs[number].get("ci") or (None, None)
        if head != self.prs[number]["head"]:
            return "pending"
        return "passing" if passed else "failing"

    def merge(self, number):
        if self.prs[number]["merged"]:
            return
        self.prs[number]["merged"] = True
        self.hook("pull_request", self.pr_payload(number, "closed", merged=True))

    # ------------------------------------------------------------------ fake GitHub reads (opt in)
    def connect_github(self):
        """Answer the GitHub App's reads from this fake GitHub, as a hub with the App connected does: a review move
        then reads each PR's head and checks afresh, and the scheduler pass retries refused moves and reads gate holds.
        Only the HTTP boundary is faked (the App's row, its token mint and its calls), as backend/tests do. While
        `github_fault` is set ("timeout", or an HTTP status such as 502 or 429), every read and token mint fails that
        way. Without this call the hub has no App and the gate reads the webhook cache, as every other scenario does."""
        import re
        from types import SimpleNamespace
        from backend import task_blocked
        from backend.store import Problem
        service = self.app.state.github_app
        self.github_reads, self.github_fault = True, None
        self.github_failed = []          # (when, fault) per read or mint that failed
        task_blocked._fresh["seen"].clear()
        task_blocked._fresh["tried"].clear()
        token = {"until": None}

        def down():
            if not self.github_fault:
                return
            self.github_failed.append((self.now(), self.github_fault))
            if self.github_fault == "timeout":   # what GitHubApp._call raises when httpx times out
                raise Problem("github_unreachable", "GitHub could not be reached; try again shortly", 502,
                              retryable=True, extra={"timed_out": True})
            return int(self.github_fault)

        def mint(repos, permissions, **kw):
            # An installation token lasts an hour, so a long outage reaches the mint as well as the reads.
            if not token["until"] or token["until"] <= self.now():
                if status := down():
                    raise Problem("github_token", f"GitHub would not issue an installation token (HTTP {status})",
                                  502, extra={"github_status": status})
                token["until"] = self.now() + dt.timedelta(hours=1)
            return "sim-token", token["until"].strftime("%Y-%m-%dT%H:%M:%SZ")

        def answer(status, payload):
            return SimpleNamespace(status_code=status, json=lambda: payload, headers={}, text=json.dumps(payload))

        def call(method, path, **kw):
            if status := down():
                return answer(status, {"message": "Server Error" if status >= 500 else "Too Many Requests"})
            if found := re.fullmatch(rf"/repos/{REPO}/pulls/(\d+)", path):
                pr = self.prs.get(int(found[1]))
                return answer(200, {"head": {"sha": pr["head"]}, "merged": pr["merged"]}) if pr else answer(404, {})
            if found := re.fullmatch(rf"/repos/{REPO}/commits/(\w+)/(check-runs|statuses)", path):
                if found[2] == "statuses":
                    return answer(200, [])
                runs = []
                for number, pr in self.prs.items():
                    head, passed = pr.get("ci") or (None, None)
                    if pr["head"] == found[1] or head == found[1]:
                        done = head == found[1]
                        runs.append({"id": number * 1000, "name": "tests", "status": "completed" if done else "in_progress",
                                     "conclusion": ("success" if passed else "failure") if done else None})
                return answer(200, {"check_runs": runs})
            return answer(404, {"message": "Not Found"})

        service.row = lambda c=None: {"org": REPO.split("/")[0], "installation_id": 1, "app_id": 1}
        service.mint = mint
        service._call = call

    # ------------------------------------------------------------------ board reads (no turn, no side effects)
    def tickets(self):
        """The Dev tickets: tasks of the Dev type, with their step names."""
        rows = self.sql("SELECT t.id,t.title,t.owner,t.status,t.step_id,s.name step,t.blocked_since,t.updated,"
                        "t.labels_json,t.created FROM tasks t LEFT JOIN task_steps s ON s.id=t.step_id WHERE t.type_id=?",
                        (self.dev["id"],))
        for r in rows:
            r["labels"] = json.loads(r.pop("labels_json") or "[]")
        return rows

    def title(self, task_id):
        found = self.sql("SELECT title FROM tasks WHERE id=?", (task_id,))
        return found[0]["title"] if found else "?"

    def slots(self, slug):
        """Tickets the builder is actively working: at one of the board's work steps."""
        work = [s.name for s in self.board.steps if s.work]
        return sum(1 for t in self.tickets() if t["owner"] == "bot:" + slug and t["step"] in work
                   and t["status"] not in ("done", "closed", "declined"))

    def cleared(self, task_id):
        """Whether Tico let the ticket pass its current step without a check: a skip tag or a skipped round."""
        entered = self.step_entered_at(task_id) or ""
        return bool(self.sql("SELECT 1 FROM task_events WHERE task_id=? AND field IN ('step_skipped','round_skipped') "
                             "AND ts>=? LIMIT 1", (task_id, entered)))

    def fallback(self, who, task_id, what):
        self.fallbacks.append((self.now(), who, self.title(task_id), what))

    def check_step(self, child):
        """The board step a check subtask was filed for: where its ticket was when the check was made."""
        parent = self.parent_of(child["id"])
        found = self.sql("SELECT s.name FROM task_events e JOIN task_steps s ON s.id=e.new WHERE e.task_id=? "
                         "AND e.field='step' AND e.ts<=? ORDER BY e.ts DESC, e.rowid DESC LIMIT 1",
                         (parent, child["created"]))
        return found[0]["name"] if found else None

    def judged(self, task_id, step=None):
        """The ticket's newest check with a verdict (for `step` when given)."""
        found = [c for c in self.checks(task_id) if c.get("verdict") and (step is None or self.check_step(c) == step)]
        return found[-1] if found else None

    def checks(self, task_id):
        return self.sql("SELECT t.* FROM tasks t JOIN task_relations r ON r.from_task=t.id AND r.kind='parent' "
                        "WHERE r.to_task=? ORDER BY t.created, t.rowid", (task_id,))

    def latest_check(self, task_id, kind):
        found = [c for c in self.checks(task_id) if c["title"].lower().startswith(kind.lower() + ":")]
        return found[-1] if found else None

    def open_checks(self, slug):
        return self.sql("SELECT count(*) n FROM tasks WHERE owner=? AND status NOT IN ('done','closed','declined')",
                        ("bot:" + slug,))[0]["n"]

    def parent_of(self, task_id):
        found = self.sql("SELECT to_task FROM task_relations WHERE from_task=? AND kind='parent'", (task_id,))
        return found[0]["to_task"] if found else None

    def pr_of(self, task_id):
        return next((n for n, pr in self.prs.items() if pr["task"] == task_id), None)

    def head_for(self, child):
        pr = self.pr_of(self.parent_of(child["id"]) or child["id"])
        return self.prs[pr]["head"] if pr else None

    def step_entered_at(self, task_id, claimed_by=None):
        """When the task last changed step before this moment: the claim that just happened starts an On Deck task
        (as `keeper`), and that is no reason to call the notice stale. Nor is a hold clearing: the ticket goes back
        to its step because of the news the notice brings."""
        hold = next((st["id"] for st in self.dev["steps"] if st["name"] == self.board.hold_step), "")
        found = self.sql("SELECT max(ts) ts FROM task_events WHERE task_id=? AND field='step' AND ts<? "
                         "AND coalesce(old,'')<>?", (task_id, self.H.now(), hold))
        return found[0]["ts"] if found else None

    def step_spans(self, task_id, until=None):
        """[(step name, hours there)] in order, the last one up to `until` (default now)."""
        rows = self.sql("SELECT e.ts, s.name FROM task_events e LEFT JOIN task_steps s ON s.id=e.new "
                        "WHERE e.task_id=? AND e.field='step' ORDER BY e.ts, e.rowid", (task_id,))
        out = []
        for row, nxt in zip(rows, rows[1:] + [{"ts": until or self.H.now()}]):
            span = self.H.parse_ts(nxt["ts"]) - self.H.parse_ts(row["ts"])
            out.append((row["name"], span.total_seconds() / 3600))
        return out

    def check_waits(self, task_id, until=None):
        """[(step name, hours)] per stay at a step, each ended by the first thing that settles it: leaving the step, a
        verdict move, a skipped round or a merge wake. Read for check steps: what follows a settled check (the owner's
        merge) is the owner's time, not the check's."""
        rows = self.sql("SELECT e.ts, e.field, s.name FROM task_events e LEFT JOIN task_steps s ON s.id=e.new "
                        "WHERE e.task_id=? AND e.field IN ('step','verdict_move','round_skipped','merge_wake') "
                        "ORDER BY e.ts, e.rowid", (task_id,))
        out, stay = [], None
        for row in rows + [{"ts": until or self.H.now(), "field": "end", "name": None}]:
            if stay:
                out.append((stay[0], (self.H.parse_ts(row["ts"]) - self.H.parse_ts(stay[1])).total_seconds() / 3600))
                stay = None
            if row["field"] == "step":
                stay = (row["name"], row["ts"])
        return out

    def idle_hours(self, task_id, until=None):
        """Hours since the task last changed, as of `until` (default now)."""
        row = self.sql("SELECT updated FROM tasks WHERE id=?", (task_id,))[0]
        return ((until or self.now()) - self.H.parse_ts(row["updated"])).total_seconds() / 3600

    def queued(self, slug=None):
        """Queued jobs per bot, and how many are held."""
        rows = self.sql("SELECT bot, count(*) n, sum(held) held FROM jobs WHERE state='queued' "
                        + ("AND bot=? " if slug else "") + "GROUP BY bot", (slug,) if slug else ())
        return {r["bot"]: (r["n"], r["held"] or 0) for r in rows}
