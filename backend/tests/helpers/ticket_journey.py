"""A ticket's whole journey on the default company (test_ticket_journey.py): the company as setup makes it, its seat
bots live on one computer, and hands for a person and for each bot.

Nothing here stands in for Tico. A person acts through the API with their sign-in. A bot acts only inside a turn the
computer claimed for it, with that turn's token, through the real `tico` CLI against a listening server (`Turn.cli`) or
the bot API (`Turn.call`). The server's own periodic steps (the Jev step, the 5-minute PR re-read, the merge queue's
calls, the never-stuck check) are run by `Company.beat`. Only what is outside Tico is a double: GitHub
(helpers/fake_github.py) and the model behind Jev (evals/never_stuck/fake_jev.py)."""

import contextlib
import io
import os
import re
import sys
import uuid

from backend import idea_checks, never_stuck, page_verdicts
from backend import source_actions as SA
from backend.item_sources import github as GS
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.sample_company import machine, rows, signed_in
from clients import hubcli

REPO = "acme/app"
SEATS = (
    "product-manager",
    "product-owner",
    "engineering-manager",
    "builder",
    "reviewer-1",
    "reviewer-2",
    "qa-owner",
    "release-owner",
)


def _headers(token):
    return {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid.uuid4())}


def default_company(environment):
    """The default company as setup makes it (onboarding with no chart picked), with its computer. Returns the API
    client and the computer's enrolment."""
    api = environment()
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    return api, machine(api)


class Turn:
    """One run of a bot: what the computer claimed for it, and the hands the run has."""

    def __init__(self, company, slug, attempt):
        self.company, self.slug, self.attempt = company, slug, attempt
        message = attempt.get("message") or {}
        self.wake = message.get("body") or ""
        self.refs = message.get("refs") or {}
        self.task_id = self.refs.get("task") or (attempt.get("task") or {}).get("id")
        # The bot's checkout on the computer: where its run starts, and where the pages it publishes are written.
        self.checkout = company.workspace / slug
        self.checkout.mkdir(parents=True, exist_ok=True)

    def cli(self, *argv, ok=True):
        """`tico <argv>` as the bot in this run: (exit code, stdout, stderr). `ok` asserts it exits 0."""
        env = {
            "HUB_API_URL": self.company.live,
            "HUB_TOKEN": self.attempt["token"],
            "HUB_BOT": self.slug,
            "HUB_WORKSPACE": str(self.company.workspace),
        }
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        out, err, stdin, cwd = io.StringIO(), io.StringIO(), sys.stdin, os.getcwd()
        sys.stdin = io.StringIO("")
        os.chdir(self.checkout)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = hubcli.main([str(a) for a in argv])
                except SystemExit as stop:
                    code = stop.code if isinstance(stop.code, int) else 1
        finally:
            sys.stdin = stdin
            os.chdir(cwd)
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        if ok:
            assert code == 0, (
                f"{self.slug}: tico {' '.join(map(str, argv))} was refused: {err.getvalue() or out.getvalue()}"
            )
        return code, out.getvalue(), err.getvalue()

    def call(self, method, path, body=None):
        """One bot API call with the run's token."""
        kwargs = {"headers": _headers(self.attempt["token"])}
        if body is not None:
            kwargs["json"] = body
        return self.company.api.request(method.upper(), "/api/v2/" + path, **kwargs)

    def page(self, name, title, text, task_id=None):
        """The bot writes a page in its checkout and publishes it (`tico page publish`), again for a new version.
        Returns (file id, version)."""
        import json

        self.company.pages += 1
        (self.checkout / name).write_text(
            f"<html><body><h1>{title}</h1><p>{text}</p><p>Edition {self.company.pages}.</p></body></html>"
        )
        _, out, _ = self.cli("page", "publish", name, "--title", title, "--task", task_id or self.task_id)
        made = json.loads(out)["file"]
        return made["id"], made["version"]

    def attach(self, requirement, file_id, task_id=None):
        """`tico task attach <task> page:<file> --req <requirement>` at the version the bot was last shown."""
        task_id = task_id or self.task_id
        return self.cli(
            "task", "attach", task_id, f"page:{file_id}", "--req", requirement, "--version", self.version(task_id)
        )

    def show(self, task_id=None):
        """What `tico task show` prints for the bot."""
        return self.cli("task", "show", task_id or self.task_id)[1]

    def version(self, task_id=None):
        """The task's version as `tico task show` prints it: what the bot sends with its next command."""
        found = re.search(r"version (\d+)", self.show(task_id))
        assert found, f"{self.slug}: tico task show printed no version"
        return int(found.group(1))

    def end(self, text="Done for now."):
        """The run ends: the computer reports it completed."""
        sent = self.company.api.post(
            f"/api/v2/attempts/{self.attempt['id']}/complete",
            json={"outcome": "completed", "text": text, "last_seq": 0},
            headers=_headers(self.company.computer["token"]),
        )
        assert sent.status_code == 200, f"{self.slug}: the end of the run was refused: {sent.text}"
        self.company.running.pop(self.attempt["id"], None)


class Company:
    def __init__(self, api, computer, live, workspace, github, jev, time_machine=None, webhooks=True):
        self.api, self.computer, self.live, self.workspace = api, computer, live, workspace
        self.github, self.jev = github, jev
        # `webhooks` off: GitHub's webhooks never arrive (an outage on their side), so only the 5-minute re-read tells
        # Tico what a PR did. That needs `time_machine`, to let the five minutes pass.
        self.time_machine, self.webhooks = time_machine, webhooks
        self.ids = {}
        self.pages = 0
        self.running = {}  # the runs under way, by attempt id

    # ------------------------------------------------------------------ rows and reads
    def rows(self, sql, args=()):
        return rows(self.api, sql, args)

    def bot(self, slug):
        """A bot's id by its slug."""
        if slug not in self.ids:
            self.ids = {row["slug"]: row["id"] for row in self.rows("SELECT id, slug FROM bots")}
        return self.ids[slug]

    def actor(self, slug):
        return "bot:" + self.bot(slug)

    @property
    def owner(self):
        """The slug of the feature owner the journey's tasks are given to: the default chart's first, found by its role
        (pipeline_seats.PRODUCT_OWNER), whatever the seat is called."""
        from backend import pipeline_seats as S

        with self.api.app.state.store.read() as c:
            actor = S.resolve(c)[S.PRODUCT_OWNER]
        assert actor, "the default company has no feature owner"
        return self.slug_of(actor)

    def slug_of(self, actor):
        self.bot("assistant")
        return next((slug for slug, bid in self.ids.items() if "bot:" + bid == actor), actor)

    # ------------------------------------------------------------------ the person
    def person(self, method, path, body=None, expected=200):
        kwargs = {"headers": signed_in()}
        if body is not None:
            kwargs["json"] = body
        sent = self.api.request(method.upper(), "/api/v2/" + path, **kwargs)
        assert sent.status_code == expected, f"the person's {method} {path} answered {sent.status_code}: {sent.text}"
        return sent.json()

    def task(self, task_id):
        """The person's read of a task."""
        return self.person("GET", f"tasks/{task_id}")["task"]

    def needs_you(self):
        return self.person("GET", "needs-you")["items"]

    def sign_off(self, task_id, note=None):
        """The person OKs the one sign-off the task waits on, from their list: they open it and
        answer ok, as the ask screen does."""
        asks = [i for i in self.needs_you() if i["task_id"] == task_id and i["kind"] == "ask"]
        assert len(asks) == 1, f"the person's list holds {len(asks)} asks for the task, not one. {self.line(task_id)}"
        assert "ok" in asks[0]["ask"]["may"], f"the person may not OK it: {asks[0]['ask']}"
        body = {"value": "ok", **({"note": note} if note else {})}
        return self.person("POST", f"tasks/{task_id}/asks/{asks[0]['requirement_id']}/answer", body)

    # ------------------------------------------------------------------ the computer
    def report(self):
        """The computer's heartbeat: every bot assigned to it is there and ready."""
        readiness = {
            row["bot"]: {
                "ready": True,
                "runtime": "codex",
                "repository_present": True,
                "configuration_valid": True,
                "problems": [],
            }
            for row in self.rows("SELECT bot FROM assignments WHERE runner_id=?", (self.computer["runner_id"],))
        }
        body = {"version": "test", "platform": "test", "capacity": 4}
        body["readiness"] = {"schema_version": 1, "bots": readiness}
        sent = self.api.post("/api/v2/runners/heartbeat", json=body, headers=_headers(self.computer["token"]))
        assert sent.status_code == 200, sent.text
        # A runner also keeps its runs' leases: a run that outlives its lease is told to stop.
        for attempt in self.running:
            kept = self.api.post(f"/api/v2/attempts/{attempt}/renew", json={}, headers=_headers(self.computer["token"]))
            assert kept.status_code == 200, f"a run under way could not keep its lease: {kept.text}"

    def later(self, minutes):
        """Time passes with the computer checking in all the while, as a running one does: a computer that falls
        silent is held back from new runs until it has been back two minutes."""
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc)
        for _ in range(int(minutes * 60 // 45)):
            now += datetime.timedelta(seconds=45)
            self.time_machine.move_to(now, tick=True)
            self.report()

    def go_live(self):
        """BotOps takes every seat bot live in its first run, with `tico bot go-live`, once the computer has their
        repositories: what the "Set up" tasks of a new company ask of it."""
        self.report()
        botops = self.turn("botops")
        assert botops, "BotOps has no run queued on a new company: nobody would take the seat bots live"
        seats = self.rows(
            "SELECT b.slug FROM bots b JOIN bot_settings s ON s.bot_id=b.id "
            "WHERE json_extract(s.extra,'$.goes_live_on_setup')"
        )
        for row in seats:
            botops.cli("bot", "go-live", row["slug"])
        botops.end()
        self.report()
        asleep = self.rows("SELECT slug FROM bots WHERE state NOT IN ('active','archived')")
        assert not asleep, f"bots that are not live after BotOps took them live: {[r['slug'] for r in asleep]}"
        # First-run setup reaches no bot that has not started: a company once began with 11 such refused writes.
        refused = self.rows("SELECT actor, rule, detail FROM refusals")
        assert not refused, (
            f"first-run setup had writes refused: {[(r['actor'], r['rule'], r['detail'][:120]) for r in refused]}"
        )

    def turn(self, slug):
        """The bot's next run, started as the runner starts it, or None when nothing is queued for it. A claim the
        server no longer wants started (the task moved on while the wake sat queued) is passed over, as a runner does."""
        for _ in range(20):
            sent = self.api.post(
                "/api/v2/jobs/claim",
                json={"bot": self.bot(slug), "start_places": True},
                headers=_headers(self.computer["token"]),
            )
            assert sent.status_code == 200, sent.text
            attempt = sent.json()["attempt"]
            if not attempt:
                return None
            started = self.api.post(
                f"/api/v2/attempts/{attempt['id']}/started",
                json={"thread_id": "t-" + attempt["id"]},
                headers=_headers(self.computer["token"]),
            )
            if started.status_code == 200:
                self.running[attempt["id"]] = slug
                return Turn(self, slug, attempt)
            assert started.status_code == 409, f"{slug}: the start of a run was refused: {started.text}"
        raise AssertionError(f"{slug}: twenty claims in a row could not start")

    def turn_on(self, slug, task_id, what=""):
        """The bot's run for `task_id`: runs for its other tasks are ended as they come. Fails when none is queued."""
        seen = []
        for _ in range(12):
            turn = self.turn(slug)
            if turn is None:
                break
            if turn.task_id == task_id:
                return turn
            seen.append(turn.task_id)
            turn.end()
        raise AssertionError(
            f"{slug} was never woken on the task{' ' + what if what else ''}: no run is queued for it "
            f"(other runs it had: {len(seen)}). The task: {self.line(task_id)}"
        )

    def queued(self, slug=None):
        """Queued wakes on tasks: (bot slug, task id), oldest first."""
        found = self.rows(
            "SELECT j.bot_id, m.wake_key FROM jobs j JOIN messages m ON m.id=j.message_id "
            "WHERE j.state='queued' AND m.wake_key IS NOT NULL ORDER BY m.created_at, m.id"
        )
        out = [(self.slug_of("bot:" + row["bot_id"]), row["wake_key"].split(":", 1)[0]) for row in found]
        return [pair for pair in out if slug is None or pair[0] == slug]

    # ------------------------------------------------------------------ the server's own steps
    def beat(self, times=1):
        """The Heartbeat steps a ticket depends on, in the order the server runs them."""
        store = self.api.app.state.store
        for _ in range(times):
            GS.refresh(store, self.github)
            SA.run(store, {"github": self.github})
            page_verdicts.run(store, H.now())
            idea_checks.run(store, H.now())  # Jev's duplicate check on ideas filed into Ideas (the pair's Product)
            with store.transaction() as c:
                never_stuck.check(c)

    def heard(self, number):
        """Tico learns what GitHub did to a PR. With webhooks, at once: what GitHub says of it now is saved as the
        webhook saves it. Without them, six minutes pass and the re-read finds it. Then one beat."""
        if self.webhooks:
            with self.api.app.state.store.transaction() as c:
                GS.save_and_settle(c, REPO, number, GS.read_pr(self.github, REPO, number), GS.version_now())
        else:
            self.later(6)
        self.beat()

    def commit(self):
        """A commit sha no other commit has."""
        return uuid.uuid4().hex + uuid.uuid4().hex[:8]

    # ------------------------------------------------------------------ what must hold at every handoff
    def new_in_list(self, before):
        """What the person's Needs you holds that it did not hold in `before`: (kind, task id) pairs."""
        seen = {item["id"] for item in before}
        return [(item["kind"], item["task_id"]) for item in self.needs_you() if item["id"] not in seen]

    def handoff(self, task_id, stage, slug):
        """The task reached `stage` and it is `slug`'s: the stage's owner, woken once and nobody else, with a run it
        can start, a wake that names the task's stage, and every page from the stages before readable. Returns the
        bot's run."""
        task = self.task(task_id)
        assert task["stage"]["name"] == stage, f"the task did not reach {stage}. {self.line(task_id)}"
        assert task["owner_id"] == self.actor(slug), f"{stage} is not {slug}'s. {self.line(task_id)}"
        turn = task["turn"]
        assert (turn["state"], turn["who"]) == ("bot", self.actor(slug)), (
            f"it is not {slug}'s turn. {self.line(task_id)}"
        )
        woken = [bot for bot, task in self.queued() if task == task_id]
        assert woken == [slug], f"in {stage}, {slug} should be woken once and nobody else; queued wakes: {woken}"
        run = self.turn_on(slug, task_id, f"in {stage}")
        assert f"is in {stage} " in run.wake, f"{slug}'s wake does not say the task is in {stage}: {run.wake[:300]}"
        shown = run.show()
        for file_id in sorted(set(re.findall(r"tico file read (file-[0-9a-f]+)", run.wake + shown))):
            code, out, err = run.cli("file", "read", file_id, ok=False)
            assert code == 0 and '"text"' in out, (
                f"{slug} cannot read {file_id}, a page its wake tells it to read: {err or out}"
            )
        return run

    def clean(self, expected=()):
        """Nothing a person would have to fix by hand: no refused write since the company was made, no open task without an owner, no task made
        twice. `expected`: words of refusals the journey meant to meet (a bot told to send the task back instead)."""
        refused = [
            r
            for r in self.rows("SELECT actor, rule, detail FROM refusals ORDER BY created_at, id")
            if not any(words in r["detail"] for words in expected)
        ]
        assert not refused, (
            f"writes were refused along the way: {[(self.slug_of(r['actor']), r['rule'], r['detail'][:200]) for r in refused]}"
        )
        ownerless = self.rows("SELECT title FROM tasks WHERE status='open' AND coalesce(owner_id,'')=''")
        assert not ownerless, f"open tasks with no owner: {[r['title'] for r in ownerless]}"
        twice = self.rows(
            "SELECT title, pipeline_id, count(*) AS n FROM tasks WHERE pipeline_id IN ('product','engineering') "
            "GROUP BY title, pipeline_id HAVING n > 1"
        )
        assert not twice, f"tasks made twice: {[(r['title'], r['pipeline_id'], r['n']) for r in twice]}"

    def github_connected(self):
        """The company's GitHub App is installed with one repository in use."""
        with self.api.app.state.store.transaction() as c:
            X.github_installed(c, REPO)

    # ------------------------------------------------------------------ what a person would notice
    def line(self, task_id):
        task = self.task(task_id)
        turn = task.get("turn") or {}
        stage = (task.get("stage") or {}).get("name") or task.get("stage_id")
        return (
            f"{task.get('title')!r} is in {stage}, owner {self.slug_of(task.get('owner_id'))}, "
            f"turn {turn.get('state')} ({self.slug_of(turn.get('who'))}): {turn.get('line')}"
        )
