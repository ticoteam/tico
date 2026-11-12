"""The load sender: several processes, each an asyncio loop, driving one Tico server over HTTP as real clients do.

Open-loop: every request has a planned time, drawn from the seed before the level starts, and goes out at that time
whether or not earlier ones have answered, so a slow server shows as slow answers, not as less load. A call that must
follow another (the steps of one turn) is planned at the later of its timetable slot and the previous step's answer, as a
runner does: those steps' sender times leave out any queueing behind a slow earlier step. Each request records how late
it went out against its plan (the sender's lateness), and the loop records its own oversleep every 100 ms; when the
sender is the limit, either grows and the run is not valid (report.py). The loop's oversleep is what catches a lagging
sender at high levels, where a chained step planned "now" has no lateness to show.

People: each holds the live stream (GET /api/v2/events) open and, at Poisson times, opens a page (Home, Needs you, task
lists, search, a task, a thread, a run view) or writes (a comment, a thread message, a move, a chat to a bot). Writes
carry a marker with their send time; a stream that reads one records the delivery delay.
Runners: each computer sends its heartbeat; each turn slot claims a run, takes its place, starts, posts events of a
size drawn from a fixed spread (some large) at a set rate, polls for inputs, renews its lease and completes. No model
is called. When a claim finds nothing, a person asks one of the computer's bots for something, so work keeps coming;
routines the server fires are claimed the same way."""

import asyncio
import contextlib
import os
import pickle
import random
import re
import resource
import time
import uuid

import httpx

from bench.load_gate.company import CAPABILITIES

MARK = re.compile(r"gate-mark-(\d{13})")

# What a person does, by weight (the route template the server records, the call). Navigation and writes, in the
# proportion the report writes down; production's 24 hours to 2026-11-12 were runner-heavy, and this mix keeps people
# at about a quarter of the requests at level 60.
PERSON_MIX = (
    ("home", 10),
    ("needs", 12),
    ("needs_summary", 6),
    ("task_list", 10),
    ("search", 4),
    ("task", 12),
    ("task_comments", 6),
    ("timeline", 10),
    ("run_view", 6),
    ("comment", 4),
    ("thread_post", 6),
    ("move", 1),
    ("chat", 2),
)
PERSON_RATE = 0.4  # actions a second per person, on average
TURN_SECONDS = (20, 120)  # a fake turn's length, uniform
EVENT_EVERY = 2.0  # seconds between a turn's event posts
INPUTS_EVERY = 1.0  # seconds between its input polls
RENEW_EVERY = 20.0
HEARTBEAT_EVERY = 30.0
OUTPUT_SIZES = (80, 80, 80, 400, 400, 2000, 16000)  # characters per event: mostly small, some large
CLAIM_RETRY = 1.0
ASK_EVERY = 3.0  # a slot whose claims find nothing asks for work at most this often


class Log:
    """What one worker saw: one tuple per request, the delivery delays and its own CPU."""

    def __init__(self):
        self.requests = []  # (planned epoch s, route template, ms, status, bytes, lateness ms)
        self.deliveries = []  # (epoch s, delay ms)
        self.lag = []  # (epoch s, ms) the loop's own oversleep, every 100 ms
        self.cpu = []  # (epoch s, process CPU s), every 100 ms, for the CPU share inside the steady window
        self.turns = 0
        self.spans = []  # (start, end) of each fake turn, for the turns actually running at once
        self.claims_empty = 0


class Worker:
    def __init__(self, target, plan, log, starve_ms=0):
        self.target, self.plan, self.log = target.rstrip("/"), plan, log
        self.starve_ms = starve_ms
        self.stop_at = plan["end"]
        self.rnd = random.Random(plan["seed"])
        self.moves = {}

    # ------------------------------------------------------------------ one request

    async def call(self, client, method, route, path, tok, body=None, planned=None):
        planned = planned if planned is not None else time.time()
        delay = planned - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        sent = time.time()
        late = max(0.0, (sent - planned) * 1000)
        headers = {"Authorization": "Bearer " + tok}
        if method != "GET":
            headers["Idempotency-Key"] = uuid.uuid4().hex
        status, size, data = 0, 0, None
        try:
            r = await client.request(method, self.target + "/api/v2/" + path, json=body, headers=headers)
            status, size = r.status_code, len(r.content)
            if status == 200:
                with contextlib.suppress(ValueError):
                    data = r.json()
        except httpx.HTTPError:
            status = 599
        self.log.requests.append((planned, route, (time.time() - sent) * 1000, status, size, late))
        return data

    # ------------------------------------------------------------------ people

    async def stream(self, client, who):
        """The person's browser holding the live stream; each marker read is a delivery."""
        seen = set()
        while time.time() < self.stop_at:
            try:
                async with client.stream(
                    "GET", self.target + "/api/v2/events", headers={"Authorization": "Bearer " + who["token"]}
                ) as r:
                    async for line in r.aiter_lines():
                        if time.time() >= self.stop_at:
                            return
                        for mark in MARK.findall(line):
                            if mark not in seen:
                                seen.add(mark)
                                now = time.time()
                                self.log.deliveries.append((now, now * 1000 - int(mark)))
            except (httpx.HTTPError, asyncio.TimeoutError):
                await asyncio.sleep(1)

    async def person(self, client, who, first):
        plan = self.plan
        rnd = random.Random(f"{plan['seed']}-{who['id']}-{plan['level']}")
        kinds = [k for k, w in PERSON_MIX for _ in range(w)]
        at = first
        pending = []
        while True:
            at += rnd.expovariate(PERSON_RATE)
            if at >= self.stop_at:
                break
            # Every choice is drawn here, in plan order, so a seed sends the same requests whatever the timing.
            pick = {
                "kind": rnd.choice(kinds),
                "task": rnd.choice(plan["tasks"]) if plan["tasks"] else None,
                "thread": rnd.choice(plan["threads"]) if plan["threads"] else None,
                "q": rnd.choice(("Acme", "Topic", "kickoff", "routine", "history")),
                "bot": rnd.choice(plan["all_bots"]),
            }
            pending.append(asyncio.ensure_future(self.action(client, who, pick, at)))
            # Plan one action ahead: sleep until shortly before it is due, so no backlog of waiting plans builds up.
            ahead = at - time.time() - 0.05
            if ahead > 0:
                await asyncio.sleep(ahead)
        await asyncio.gather(*pending, return_exceptions=True)

    async def action(self, client, who, pick, at):
        tok = who["token"]
        kind, task, thread = pick["kind"], pick["task"], pick["thread"]
        mark = lambda: f"gate-mark-{int(time.time() * 1000):013d}"
        if kind == "home":
            await self.call(client, "GET", "GET /api/v2/places/{place}", "places/person:me", tok, planned=at)
        elif kind == "needs":
            await self.call(client, "GET", "GET /api/v2/needs-you", "needs-you", tok, planned=at)
        elif kind == "needs_summary":
            await self.call(client, "GET", "GET /api/v2/needs-you/summary", "needs-you/summary", tok, planned=at)
        elif kind == "task_list":
            await self.call(client, "GET", "GET /api/v2/tasks", "tasks?limit=50", tok, planned=at)
        elif kind == "search":
            q = pick["q"]
            await self.call(client, "GET", "GET /api/v2/threads/search", f"threads/search?q={q}", tok, planned=at)
        elif kind == "task" and task:
            await self.call(client, "GET", "GET /api/v2/tasks/{tid}", f"tasks/{task}", tok, planned=at)
        elif kind == "task_comments" and task:
            await self.call(
                client, "GET", "GET /api/v2/tasks/{tid}/comments", f"tasks/{task}/comments", tok, planned=at
            )
        elif kind == "timeline" and thread:
            await self.call(
                client, "GET", "GET /api/v2/threads/{thread_id}/timeline", f"threads/{thread}/timeline", tok, planned=at
            )
        elif kind == "run_view" and task:
            await self.call(
                client, "GET", "GET /api/v2/tasks/{tid}/run-view", f"tasks/{task}/run-view", tok, planned=at
            )
        elif kind == "comment" and task:
            await asyncio.sleep(max(0.0, at - time.time()))
            body = {"text": "Acme comment " + mark()}
            await self.call(
                client, "POST", "POST /api/v2/tasks/{tid}/comments", f"tasks/{task}/comments", tok, body, at
            )
        elif kind == "thread_post" and thread:
            await asyncio.sleep(max(0.0, at - time.time()))
            body = {"text": "Acme note " + mark()}
            await self.call(
                client, "POST", "POST /api/v2/threads/{thread_id}/messages", f"threads/{thread}/messages", tok, body, at
            )
        elif kind == "move" and task:
            # Each task goes back and forth between done and open, so moves keep working through a long run.
            to = "reopen" if self.moves.get(task) == "done" else "done"
            self.moves[task] = to if to == "done" else "open"
            await self.call(client, "POST", "POST /api/v2/tasks/{tid}/move", f"tasks/{task}/move", tok, {"to": to}, at)
        elif kind == "chat":
            bot = pick["bot"]
            await self.call(
                client, "POST", "POST /api/v2/chat/{bot}", f"chat/{bot}", tok, {"text": "Acme question"}, at
            )

    # ------------------------------------------------------------------ runners

    async def heartbeat(self, client, computer, first):
        at = first
        while at < self.stop_at:
            await self.call(
                client,
                "POST",
                "POST /api/v2/runners/heartbeat",
                "runners/heartbeat",
                computer["token"],
                {
                    "version": "gate",
                    "platform": "test",
                    "readiness": computer["readiness"],
                    "capabilities": CAPABILITIES,
                },
                at,
            )
            at += HEARTBEAT_EVERY

    async def slot(self, client, computer, asker, first):
        """One turn at a time, back to back, for the whole level."""
        rnd = random.Random(f"{self.plan['seed']}-{computer['runner_id']}-{first}")
        tok = computer["token"]
        at, asked = first, 0.0
        while at < self.stop_at:
            answer = await self.call(
                client, "POST", "POST /api/v2/jobs/claim", "jobs/claim", tok, {"bot": None, "start_places": True}, at
            )
            attempt = (answer or {}).get("attempt")
            if not attempt:
                self.log.claims_empty += 1
                if time.time() - asked >= ASK_EVERY:  # nothing queued: a person asks one of its bots, now and then
                    asked = time.time()
                    bot = rnd.choice(computer["bots"])
                    await self.call(
                        client, "POST", "POST /api/v2/chat/{bot}", f"chat/{bot}", asker["token"], {"text": "Acme work"}
                    )
                at = max(time.time(), at) + CLAIM_RETRY
                continue
            aid = attempt["id"]
            await self.call(
                client, "POST", "POST /api/v2/attempts/{aid}/start-place", f"attempts/{aid}/start-place", tok, {}
            )
            await self.call(
                client,
                "POST",
                "POST /api/v2/attempts/{aid}/started",
                f"attempts/{aid}/started",
                tok,
                {"thread_id": "g-" + aid},
            )
            begin = time.time()
            length = rnd.uniform(*TURN_SECONDS)
            end = min(begin + length, self.stop_at)
            seq, renewed, next_event, next_input = 0, begin, begin + EVENT_EVERY, begin + INPUTS_EVERY
            while True:
                slot = min(next_event, next_input)
                if slot >= end:
                    break
                # A step follows the one before it: planned at its slot, or at the previous answer if that came later,
                # so a slow server is not counted as the sender's lateness.
                at = max(slot, time.time())
                if slot == next_input:
                    await self.call(
                        client, "POST", "POST /api/v2/attempts/{aid}/inputs", f"attempts/{aid}/inputs", tok, {}, at
                    )
                    next_input += INPUTS_EVERY
                else:
                    seq += 1
                    text = "Acme output " + "z" * rnd.choice(OUTPUT_SIZES)
                    event = {"seq": seq, "kind": "delta", "payload": {"text": text, "delta_kind": "text"}}
                    await self.call(
                        client,
                        "POST",
                        "POST /api/v2/attempts/{aid}/events",
                        f"attempts/{aid}/events",
                        tok,
                        {"events": [event]},
                        at,
                    )
                    next_event += EVENT_EVERY
                if time.time() - renewed >= RENEW_EVERY:
                    renewed = time.time()
                    await self.call(
                        client, "POST", "POST /api/v2/attempts/{aid}/renew", f"attempts/{aid}/renew", tok, {}
                    )
            await self.call(
                client,
                "POST",
                "POST /api/v2/attempts/{aid}/complete",
                f"attempts/{aid}/complete",
                tok,
                {"outcome": "completed", "text": "Done.", "last_seq": seq},
            )
            self.log.turns += 1
            self.log.spans.append((begin, time.time()))
            at = time.time()

    # ------------------------------------------------------------------ the loop's own lag

    async def ticker(self):
        """The loop's oversleep every 100 ms, and, for the starvation test only, a blocking pause that makes this
        sender the limit on purpose."""
        while time.time() < self.stop_at:
            before = time.monotonic()
            await asyncio.sleep(0.1)
            now = time.time()
            self.log.lag.append((now, (time.monotonic() - before - 0.1) * 1000))
            self.log.cpu.append((now, time.process_time()))
            if self.starve_ms:
                time.sleep(self.starve_ms / 1000)  # noqa: ASYNC251  blocking on purpose: the loop falls behind

    async def run(self):
        plan = self.plan
        limits = httpx.Limits(max_connections=None, max_keepalive_connections=200)
        timeout = httpx.Timeout(60.0, read=None)
        async with (
            httpx.AsyncClient(limits=limits, timeout=timeout) as streams,
            httpx.AsyncClient(limits=limits, timeout=httpx.Timeout(60.0)) as client,
        ):
            jobs = [asyncio.ensure_future(self.ticker())]
            for n, who in enumerate(plan["people"]):
                first = plan["start"] + plan["ramp"] * n / max(1, len(plan["people"]))
                jobs.append(asyncio.ensure_future(self.stream(streams, who)))
                jobs.append(asyncio.ensure_future(self.person(client, who, first)))
            for n, computer in enumerate(plan["heartbeats"]):
                jobs.append(asyncio.ensure_future(self.heartbeat(client, computer, plan["start"] + n)))
            askers = plan["all_people"]
            for n, (computer, offset) in enumerate(plan["slots"]):
                first = plan["start"] + plan["ramp"] * offset
                jobs.append(asyncio.ensure_future(self.slot(client, computer, askers[n % len(askers)], first)))
            await asyncio.sleep(max(0.0, plan["end"] - time.time()))
            # Let what is in flight answer, then stop the streams and anything still waiting.
            await asyncio.sleep(plan.get("grace", 5))
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)


def work(target, plan, out_path, starve_ms=0):
    """One sender process: run its share of the level and pickle what it saw to `out_path`."""
    log = Log()
    cpu0, wall0 = time.process_time(), time.time()
    asyncio.run(Worker(target, plan, log, starve_ms).run())
    usage = resource.getrusage(resource.RUSAGE_SELF)
    out = {
        "requests": log.requests,
        "deliveries": log.deliveries,
        "lag": log.lag,
        "cpu": log.cpu,
        "turns": log.turns,
        "spans": log.spans,
        "claims_empty": log.claims_empty,
        "cpu_s": time.process_time() - cpu0,
        "wall_s": time.time() - wall0,
        "max_rss_kb": usage.ru_maxrss,
        "pid": os.getpid(),
    }
    with open(out_path, "wb") as f:
        pickle.dump(out, f)


def plans(manifest, level, workers, start, ramp, hold, seed, grace=5):
    """Split one level over `workers` processes: people, computers' heartbeats and turn slots dealt round-robin."""
    computers = manifest["computers"]
    tasks = [t["id"] for t in manifest["tasks"]]
    out = []
    for w in range(workers):
        out.append(
            {
                "seed": f"{seed}-{w}",
                "level": level,
                "start": start,
                "ramp": ramp,
                "end": start + ramp + hold,
                "grace": grace,
                "people": manifest["people"][w::workers],
                "all_people": manifest["people"],
                "heartbeats": computers[w::workers],
                "slots": [(computers[k % len(computers)], k / max(1, level)) for k in range(w, level, workers)],
                "tasks": tasks,
                "threads": manifest["threads"],
                "all_bots": manifest["bots"],
            }
        )
    return out


def send(target, manifest, level, workers, start, ramp, hold, seed, folder, starve_ms=0, grace=5):
    """Run one level from `workers` processes and return their logs."""
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    procs, paths = [], []
    for n, plan in enumerate(plans(manifest, level, workers, start, ramp, hold, seed, grace)):
        path = os.path.join(folder, f"level-{level}-worker-{n}.pickle")
        paths.append(path)
        p = context.Process(target=work, args=(target, plan, path, starve_ms), daemon=True)
        p.start()
        procs.append(p)
    for p in procs:
        p.join()
    logs = []
    for path in paths:
        with open(path, "rb") as f:
            logs.append(pickle.load(f))
        os.unlink(path)
    return logs


def plan_json(manifest):
    """The fixed proportions, for the report."""
    total = sum(w for _, w in PERSON_MIX)
    return {
        "person_actions_per_second": PERSON_RATE,
        "person_mix": {k: round(w / total, 3) for k, w in PERSON_MIX},
        "turn_seconds": list(TURN_SECONDS),
        "event_every_s": EVENT_EVERY,
        "inputs_every_s": INPUTS_EVERY,
        "renew_every_s": RENEW_EVERY,
        "heartbeat_every_s": HEARTBEAT_EVERY,
        "output_sizes": list(OUTPUT_SIZES),
        "computers": len(manifest["computers"]),
        "streams": len(manifest["people"]),
        "approvals": "not sent: the synthetic company has no asks to answer yet",
        # What differs from a plain open-loop reading, so nobody reads the runner routes' sender p95 as one.
        "turn_steps": "chained: a turn's step goes out at its slot or after the previous step's answer, whichever is "
        "later, as a runner does; people's actions and heartbeats are open-loop",
        "people_auth": "fixed test tokens (a dictionary lookup), not the session sign-in browsers use; runners use "
        "their real enrollment tokens",
    }
