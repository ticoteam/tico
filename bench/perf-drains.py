"""Due times keep their beat while the drains are slow (perf item I08, backend/drains.py).

    python3 -m pytest bench/perf-drains.py -n 0 -s -q
    BENCH_SECONDS=60 BENCH_DELAYS=2 python3 -m pytest bench/perf-drains.py -n 0 -s -q   # a shorter run

A synthetic store only: 50 queued automation runs, 50 Workspace Gmail actions and 20 due emails across two Workspace
inboxes. Each automation step, Gmail action and email send is stubbed to answer after DELAY seconds (2 s, then 10 s).
The server's loop is played as `backend/app.py` runs it, for BENCH_SECONDS (300) at the 10-second interval:

- before: the loop as it was, with the queue drained inside the scheduler's tick, then the Workspace key check and the
  email drain, one after another;
- after: the same loop with the drains on their workers (backend/drains.py), each pass with its 5-second budget.

A deferred run comes due every 10 s (at 3.7 s past each 10 s mark); it starts at the first beat at or after its due
time, so its delay is that beat's start minus its due time. Printed per case: beats and passes of the due-time and
recovery steps, the deferred delay (p50, worst), each drain pass's time, the longest write-lock wait on the scheduler's
health row (`wait_ms`), and the guard: each run, Gmail action and email done once, none twice. A pass still running
when the window ends counts its open delay as "at least".
"""

import base64
import json
import os
import statistics
import threading
import time
from collections import Counter
from email.message import EmailMessage

import pytest

from backend import automations as M
from backend import drains, email_sending as S, gmail_live, google_workspace as GW, heartbeat
from backend import mailbox_access as MA
from backend.scheduler import Scheduler
from backend.tests.helpers.automation_mail_actions import owned_by_ana
from backend.tests.helpers.automation_runs import make, queued
from backend.tests.helpers.email_sending import BOX, autonomous

SECONDS = float(os.environ.get("BENCH_SECONDS", "300"))
DELAYS = [float(d) for d in os.environ.get("BENCH_DELAYS", "2,10").split(",")]
INTERVAL = 10.0
RUNS, ACTIONS, EMAILS = 50, 50, 20
BOX2 = "ops@acme.example"
WAKE = {"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "assistant", "text": "Done"}}


def mime(sender, n):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, "partner@vendor.example", f"Update {n}"
    msg.set_content(f"Update {n}.")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def seed(api):
    """The fixture: queued runs, queued Gmail actions and due emails, all synthetic."""
    store = api.app.state.store
    with store.transaction() as c:
        for box in (BOX, BOX2):
            row = MA.ensure_mailbox(c, box, person_id="ana", created_by="human:ana")
            MA.grant(c, "human:ana", row["id"], "bot", "ops", "read_write")
    autonomous(api)
    aid = make(api, [WAKE])
    runs = [queued(api, aid) for _ in range(RUNS)]
    with store.transaction() as c:
        owned_by_ana(c, "auto_mail")
        from backend import automation_actions as A

        actions = [
            A._queue_mail(
                c,
                {"id": "auto_mail", "flow": {"owner": "person:ana"}},
                "run_bench",
                "archive",
                {"mailbox": BOX, "id": f"m{n}"},
                {},
                step=f"s{n}",
            )["mail_action"]
            for n in range(ACTIONS)
        ]
    emails = []
    for n in range(EMAILS):
        box = (BOX, BOX2)[n % 2]
        with store.transaction() as c:
            emails.append(
                S.enqueue(
                    c,
                    api.app.state.auth,
                    "human:ana",
                    S.Enqueue(mailbox=box, raw=mime(box, n)),
                    {"legal_or_money": False, "status": "classified"},
                )["id"]
            )
    return runs, actions, emails


def stub(monkeypatch, delay, over):
    """Every step, Gmail action and send answers after `delay` seconds, until the window is `over`; counts each."""
    done = Counter()
    real_go = M.Hub.go

    def wait():
        over.wait(delay)

    def go(self, claimed):
        wait()
        done["run:" + claimed.id] += 1
        return real_go(self, claimed)

    def organize(store, row):
        wait()
        done["mail:" + row["id"]] += 1
        return {"ok": True, "detail": "archived"}

    def send_call(url, *, token, body, content_type):
        wait()
        done["email:" + json.loads(body)["raw"][-40:]] += 1
        return 200, {"id": "gmail-" + str(len(done))}

    monkeypatch.setattr(M.Hub, "go", go)
    monkeypatch.setattr(M, "_workspace_mail", organize)
    monkeypatch.setattr(GW, "authority", lambda c, mailbox: "workspace")
    monkeypatch.setattr(GW, "mint", lambda c, mailbox, service, row=None, scopes=None: {"access_token": "bench"})
    monkeypatch.setattr(GW, "tick", lambda store: None)  # the key check's own Google call is not what is measured
    monkeypatch.setattr(gmail_live, "send_call", send_call)
    return done


def play(api, mode, seconds):
    """The schedule loop for `seconds`; returns beat starts, rest-of-pass times, drain passes and the longest wait."""
    store, auth = api.app.state.store, api.app.state.auth
    scheduler = Scheduler(store, api.app.state.execution, auth=auth)
    passes, waits = [], [0]
    workers = None
    if mode == "after":
        real = drains.Worker.pass_once

        def counted(self):
            last = real(self)
            passes.append((self.name, last["ms"], last["done"]))
            return last

        drains.Worker.pass_once = counted
        workers = drains.Drains(store, auth).start()
        scheduler.drains = workers
    beats, rests, t0 = [], [], time.monotonic()
    rest = heartbeat.Rest()
    try:
        while time.monotonic() - t0 < seconds:
            beats.append(time.monotonic() - t0)
            ctx = heartbeat.Context(scheduler)
            heartbeat.beat(ctx)
            if rest.due(ctx):
                began = time.monotonic()
                scheduler.tick(ctx.at, ctx)
                if mode == "before":
                    S.drain(store, auth)
                    passes.append(("tick+email", round((time.monotonic() - began) * 1000), None))
                rests.append(time.monotonic() - began)
            with store.read() as c:
                row = c.execute("SELECT detail FROM service_health WHERE service='scheduler'").fetchone()
            waits[0] = max(waits[0], json.loads(row[0] or "{}").get("wait_ms", 0) if row else 0)
            time.sleep(max(0.0, min(INTERVAL, seconds - (time.monotonic() - t0))))
    finally:
        if workers:
            workers.stop(30)
            drains.Worker.pass_once = real
    return beats, rests, passes, waits[0], time.monotonic() - t0


def delays(beats, end, stuck):
    """Each deferred run's delay; one due after the last beat counts only while a pass was still running (stuck)."""
    out = []
    due = 3.7
    while due < end:
        after = [b for b in beats if b >= due]
        if after:
            out.append((after[0] - due, False))
        elif stuck:
            out.append((end - due, True))
        due += INTERVAL
    return out


@pytest.mark.parametrize("delay", DELAYS)
@pytest.mark.parametrize("mode", ["before", "after"])
def test_due_times_keep_their_beat(api, monkeypatch, mode, delay):
    over = threading.Event()
    runs, actions, emails = seed(api)
    done = stub(monkeypatch, delay, over)
    result = {}

    def loop():
        result["out"] = play(api, mode, SECONDS)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    thread.join(SECONDS + 5)
    stuck = thread.is_alive()
    over.set()  # stubs answer at once from here, so a pass still running ends
    thread.join(600)
    beats, rests, passes, wait_ms, end = result["out"]
    if stuck:
        end = SECONDS
        beats = [b for b in beats if b < end]
    d = delays(beats, end, stuck)
    late = [x for x, _ in d]
    by = {}
    for name, ms, _n in passes:
        by.setdefault(name, []).append(ms)
    twice = [k for k, n in done.items() if n > 1]
    print(
        f"\n[{mode} stub={delay:g}s window={SECONDS:g}s] beats={len([b for b in beats if b < SECONDS])} "
        f"(intended ~{int(SECONDS // INTERVAL)}) rest-of-pass p50={statistics.median(rests) if rests else 0:.1f}s "
        f"worst={max(rests, default=0):.1f}s"
    )
    print(
        f"  deferred delay p50={statistics.median(late):.1f}s worst={max(late):.1f}s"
        f"{' (at least: a pass was still running)' if any(o for _, o in d) else ''}"
    )
    for name, ms in by.items():
        print(f"  {name}: passes={len(ms)} p50={statistics.median(ms) / 1000:.1f}s worst={max(ms) / 1000:.1f}s")
    print(f"  longest write-lock wait on the loop: {wait_ms} ms")
    print(
        f"  done: runs={sum(1 for k in done if k.startswith('run:'))}/{RUNS} "
        f"gmail={sum(1 for k in done if k.startswith('mail:'))}/{ACTIONS} "
        f"emails={sum(1 for k in done if k.startswith('email:'))}/{EMAILS} twice={len(twice)}"
    )
    assert not twice, "a run, Gmail action or email was done twice"
    if mode == "after":
        assert max(late) < 12, "a deferred run started more than 12 s after its due time"
