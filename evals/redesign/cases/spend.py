"""Spend and the run guard (PLAN §7, scenarios 11-15): the 30-minute window, the daily limit, a per-automation cap, pool
nesting and the turn budget. Every spend is a real completed attempt with its token use, so the hub meters it."""
import datetime as dt

from ..fakes import ReplayJudge, attach_judge
from ..harness import (LEAD_ID, OPENAI_ID, OPENAI_REF, W2, W3, Check, Scenario, Unmet, automation, auto_id,
                       body, got, hub_notices, make_group, put_budget, put_limit, put_pool, replied, rows, run_now,
                       setup, work)


# ---------------------------------------------------------------------------------------------- helpers
def held(f, key, needs=()):
    """The bot's turn was not claimed: the hub held it (turn() returns None when no job is handed out)."""
    setup(f, *needs)
    ran = got(f, key)
    return ran is None, "held, no job claimed" if ran is None else "ran"


def ran(f, *keys, needs=()):
    """Every named turn was claimed and run."""
    setup(f, *needs)
    results = {key: got(f, key) for key in keys}
    return all(results.values()), f"{sum(bool(v) for v in results.values())} of {len(keys)} ran"


def drain(org, slug, limit=4):
    """Runs the bot's queued turns until none is held or ready: a run's LLM and bot turn steps each take one."""
    count = 0
    while count < limit and org.turn(slug):
        count += 1
    return count


def new_run_steps(f):
    """The step statuses of the one run started between the `runs1` and `runs2` listings."""
    before = {run.get("id") for run in rows(body(f, "runs1"))}
    fresh = [run for run in rows(body(f, "runs2")) if run.get("id") not in before]
    if len(fresh) != 1:
        raise Unmet(W3, f"{len(fresh)} new run(s) after the second run, want 1")
    return {step.get("id"): step.get("status") for step in fresh[0].get("steps", [])}


# ---------------------------------------------------------------------------------------------- 11 over 30-min limit
def s11_steps(org, f):
    org.step(f, "limit", put_limit, org, OPENAI_ID, {"per_30m_usd": 2.0})
    org.step(f, "first", work, org, OPENAI_ID, "Spend one")
    org.step(f, "second", work, org, OPENAI_ID, "Over the limit")
    org.step(f, "window", org.pass_time, dt.timedelta(minutes=30))
    org.step(f, "resumed", org.turn, OPENAI_ID)   # no person acts: the queued task starts when the window ends


# ---------------------------------------------------------------------------------------------- 12 daily after 30-min
def s12_steps(org, f):
    org.step(f, "limit", put_limit, org, OPENAI_ID, {"per_30m_usd": 1000.0, "daily_usd": 1.0})
    org.step(f, "first", work, org, OPENAI_ID, "Spend one")
    org.step(f, "window", org.pass_time, dt.timedelta(minutes=30))
    org.step(f, "after_window", work, org, OPENAI_ID, "After a window")
    org.step(f, "window2", org.pass_time, dt.timedelta(minutes=30))
    org.step(f, "after_two", work, org, OPENAI_ID, "After two windows")


# ---------------------------------------------------------------------------------------------- 13 per-automation cap
RANK = {"needs_you": {"type": "noul", "noul": 0.2}, "urgency": {"type": "score", "score": 0.4}}   # task-rank@1


def s13_steps(org, f):
    # Each run's Decide asks the judge once: without one attached the step would be skipped (CONTRACT §2, no model).
    org.step(f, "judge", attach_judge, org, ReplayJudge([RANK, RANK]))
    org.step(f, "auto", automation, org, "Write · builder-openai", OPENAI_REF,
             trigger={"type": "schedule", "cron": "0 9 * * *", "tz": "UTC"},
             steps=[{"id": "gate", "type": "filter", "enabled": True, "rules": []},
                    {"id": "rank", "type": "decide", "enabled": True, "set": "task-rank@1", "level": "low",
                     "out": "d1"},
                    {"id": "draft", "type": "llm", "enabled": True, "as": OPENAI_REF, "prompt": "Draft a reply",
                     "out": "txt"},
                    {"id": "act", "type": "bot_turn", "enabled": True, "bot": OPENAI_ID, "prompt": "Do the work"}],
             spend_cap={"per_30m": 0.01})
    org.step(f, "run1", run_now, org, f, "auto")
    org.step(f, "drain1", drain, org, OPENAI_ID)       # the first run's LLM and bot turns spend past the cap
    org.step(f, "runs1", lambda: org.api("get", f"/api/v2/automations/{auto_id(f, 'auto')}/runs", wave=W3))
    org.step(f, "run2", run_now, org, f, "auto")
    org.step(f, "runs2", lambda: org.api("get", f"/api/v2/automations/{auto_id(f, 'auto')}/runs", wave=W3))
    org.step(f, "after", org.turn, OPENAI_ID)          # the second run starts no turn


def gate_and_rank_ran(f):
    steps = new_run_steps(f)
    ok = all(steps.get(key) not in (None, "spend_cap", "skipped") for key in ("gate", "rank"))
    return ok, f"gate {steps.get('gate')}, rank {steps.get('rank')}"


def llm_and_bot_stopped(f):
    steps = new_run_steps(f)
    ok = steps.get("draft") == "spend_cap" and steps.get("act") == "spend_cap"
    return ok, f"draft {steps.get('draft')}, act {steps.get('act')}"


# ---------------------------------------------------------------------------------------------- 14 pool nesting
def s14_steps(org, f):
    org.step(f, "parent", make_group, org, "Engineering", bots=[LEAD_ID, OPENAI_ID])
    org.step(f, "child", lambda: make_group(org, "Builders", bots=[OPENAI_ID], parent=got(f, "parent")))
    org.step(f, "parent_pool", lambda: put_pool(org, got(f, "parent"), {"daily_usd": 50.0}))
    org.step(f, "over", lambda: put_pool(org, got(f, "child"), {"daily_usd": 80.0}))
    org.step(f, "within", lambda: put_pool(org, got(f, "child"), {"daily_usd": 20.0}))


# ---------------------------------------------------------------------------------------------- 15 run guard
def s15_steps(org, f):
    org.step(f, "budget_now", lambda: org.api("get", f"/api/v2/bots/{OPENAI_ID}/budget", wave=W2))
    org.step(f, "budget", lambda: put_budget(org, OPENAI_ID, {   # the default 300 is checked in 45
        "turns_per_hour": 5, "expected_revision": body(f, "budget_now")["revision"]}))
    for n in range(1, 7):
        org.step(f, f"t{n}", work, org, OPENAI_ID, f"Runaway {n}")
    org.step(f, "hour", org.pass_time, dt.timedelta(hours=1))
    org.step(f, "later", work, org, OPENAI_ID, "An hour later")
    org.step(f, "release", org.api, "post", f"/api/v2/bots/{OPENAI_ID}/release", {}, wave=W2)
    org.step(f, "resumed", work, org, OPENAI_ID, "After the release")
    for slug in GUARD_HEARS:
        org.step(f, "notices:" + slug, hub_notices, org, slug)


GUARD_HEARS = {"botops": ("botops", "claude"), "health-monitor": ("health", "claude")}


def guard_noticed(f):
    """One hub notice each to BotOps and Health naming the held bot (CONTRACT §1 run guard: refs.held)."""
    setup(f, "budget")
    counts = {slug: sum(1 for m in got(f, "notices:" + slug) if (m.get("refs") or {}).get("held") == OPENAI_ID)
              for slug in GUARD_HEARS}
    return all(n == 1 for n in counts.values()), f"notices {counts}"


def pool_refused(f):
    setup(f, "parent_pool")
    reply = got(f, "over")
    return reply.status in (400, 422), f"{reply.status} {reply.excerpt()}"


SCENARIOS = [
    Scenario(11, "spend-over-30m", "Bot over its 30-min $ limit: no new turns; resumes next window with no person", s11_steps, [
        Check("first turn runs under the limit", W2, lambda f: ran(f, "first", needs=("limit",))),
        Check("over the 30-min limit the next turn is held, no job claimed", W2,
              lambda f: held(f, "second", needs=("limit",))),
        Check("the window ends and the queued task starts with no person acting", W2,
              lambda f: ran(f, "resumed", needs=("limit",))),
    ]),
    Scenario(12, "spend-daily-after-30m", "Daily limit still applies after 30-min windows reset", s12_steps, [
        Check("first turn runs under both limits", W2, lambda f: ran(f, "first", needs=("limit",))),
        Check("after the 30-min window resets, the daily cap still holds the bot", W2,
              lambda f: held(f, "after_window", needs=("limit",))),
        Check("two windows later the daily cap still holds the bot", W2,
              lambda f: held(f, "after_two", needs=("limit",))),
    ]),
    Scenario(13, "spend-automation-cap", "Per-automation cap reached: LLM and Bot turn stop; Filter and Decide still run", s13_steps, [
        Check("Filter and Decide still run on the capped run", W3, gate_and_rank_ran),
        Check("LLM and Bot turn steps stop with result spend_cap, logged on the run", W3, llm_and_bot_stopped),
        Check("the stopped steps start no turn", W3, lambda f: held(f, "after", needs=("run2",))),
    ]),
    Scenario(14, "spend-pool-nesting", "Group pool: a child's limit may not exceed its parent's", s14_steps, [
        Check("child pool above its parent refused (validation)", W2, pool_refused),
        Check("child pool within its parent accepted", W2,
              lambda f: replied(f, "within", 200, 201, needs=("parent_pool",))),
    ]),
    Scenario(15, "spend-run-guard", "Run guard trips on runaway turns: held until a person releases it", s15_steps, [
        Check("turns one to five run under a budget of five an hour", W2,
              lambda f: ran(f, "t1", "t2", "t3", "t4", "t5", needs=("budget",))),
        Check("the sixth turn is held: no job claimed", W2, lambda f: held(f, "t6", needs=("budget",))),
        Check("still held an hour later with no person acting", W2, lambda f: held(f, "later", needs=("budget",))),
        Check("a person's release starts the bot again", W2, lambda f: ran(f, "resumed", needs=("release",))),
        Check("one notice each to BotOps and Health", W2, guard_noticed),
    ], bots=GUARD_HEARS),
]
