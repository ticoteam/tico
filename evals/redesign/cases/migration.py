"""Migration (PLAN §7, scenarios 39-44; CONTRACT §4; mapping PLAN §8). Each scenario installs the fixture as the
release before left it (migration_fixture.py), then starts this release on it the way an upgrading install does: the
cutover runs once (backend/redesign_migration.py `cutover`). Checks read what it left through the real routes
(automations, runs, usage limits, turn budgets, bots, webhooks) and through `report`, the before/after inventory the
operator's `python -m backend.redesign_migration report` prints.

An automation names what it came from in `source` (an object, or a list for an inbox or a "What wakes" group)."""
import datetime as dt
import json
from decimal import ROUND_CEILING, Decimal
from zoneinfo import ZoneInfo

from ..harness import CLAUDE_ID, DANA, OPENAI_ID, RELEASE_ID, W5, Check, Scenario, Unmet, body, find, got, hook_post, \
    replied, rows, setup
from .migration_fixture import (BOT_BUDGET, BUILTIN_JOBS, CRON, DESTINATIONS, FIXTURE_BOTS, GUARD, HEALTH,
                                HOOK_ID, HOURLY, INBOXES, LIBRARIAN, OLD_NAME, ROUTINES, STRATEGY, SYSTEM_JOBS,
                                WAKE_RULES, install_fixture, installed, report, upgrade)

UTC, LONDON = dt.timezone.utc, ZoneInfo("Europe/London")
WAKES = "What wakes"


# ---------------------------------------------------------------------------------------------- reading
def text(value):
    return json.dumps(value, sort_keys=True, default=str)


def fx(f):
    return f["fixture"]


def half_up(hourly):
    """PLAN decision 16: $X an hour is $X/2 per 30 minutes, rounded up to $0.10."""
    tenths = (Decimal(str(hourly)) / 2 / Decimal("0.1")).to_integral_value(rounding=ROUND_CEILING)
    return float(tenths * Decimal("0.1"))


def when(value):
    """An ISO time as an aware datetime; a naive one is read as UTC."""
    at = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def step_of(auto, kind):
    return find(auto.get("steps"), lambda d: d.get("type") == kind)


def is_bot(value, slug):
    return value in (slug, "bot:" + slug)


def automations(f, key="autos"):
    return [a for a in rows(body(f, key)) if isinstance(a, dict)]


def sources(auto):
    found = auto.get("source")
    return {s.get("id") for s in (found if isinstance(found, list) else [found]) if isinstance(s, dict)}


def linked_to(f, *idents, key="autos"):
    return [a for a in automations(f, key) if sources(a) & set(idents)]


def converted_id(f, ident, key="autos"):
    hits = linked_to(f, ident, key=key)
    if len(hits) != 1:
        raise Unmet(W5, f"{len(hits)} automation(s) link to {ident} after the cutover, want 1")
    return hits[0]["id"]


def items(f, key):
    """The report's inventory by object id."""
    data = got(f, key)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise Unmet(W5, f"{key}: no inventory: {text(data)[:120]}")
    return {i["id"]: i for i in data["items"]}


def started(run):
    return when(run.get("started_at"))


def fixture_ids(f):
    """Every fixture object as the inventory names it, by label."""
    ids = fx(f)
    return {**{"routine " + n: i for n, i in ids["routines"].items()},
            **{"wake rule " + n: i for n, i in ids["rules"].items()},
            "task rule": ids["task_rule"],
            **{"system job " + job: "job:" + job for job in (*BUILTIN_JOBS, *SYSTEM_JOBS)},
            **{"inbox " + p: "inbox:" + i["mailbox"] for p, i in ids["inboxes"].items()},
            **{"destination " + key: "destination:" + key for key in DESTINATIONS},
            **{"hourly limit " + bot: "limit:" + bot for bot in HOURLY},
            "team turn budget": "budget:team", f"{BOT_BUDGET[0]} turn budget": "budget:" + BOT_BUDGET[0],
            "Health Monitor": "bot:" + HEALTH, OLD_NAME: "bot:" + STRATEGY, "goal": "goal:" + str(ids["goal"])}


# ---------------------------------------------------------------------------------------------- steps
def get(org, path):
    return org.api("get", path, wave=W5)


def guarded(f, need, fn, *args):
    """Run `fn` only once the `need` step ran: a later step never acts on a cutover that did not happen."""
    got(f, need)
    return fn(*args)


def cutover(org, f):
    """The fixture, its report as it is, this release's first start on it, and the report after."""
    install_fixture(org, f)
    org.step(f, "before", report, org)
    org.step(f, "cutover", upgrade, org)
    f["cutover_at"] = org.now()
    org.step(f, "after", guarded, f, "cutover", report, org)
    org.step(f, "autos", guarded, f, "cutover", get, org, "/api/v2/automations")


def jump(org, at):
    """Move the fake clock to `at` in one jump, report the runner in, and run the scheduler pass. World.advance
    heartbeats every 50 seconds (1,800 calls for 25 hours), which would take several seconds a scenario."""
    org.clock.shift((at - org.now()).total_seconds())
    org.heartbeat(org.bot_slugs)
    org.scheduler_pass()
    return at


def runs_for(org, f, ident, key="autos"):
    return get(org, f"/api/v2/automations/{converted_id(f, ident, key)}/runs")


def deliver(org, f):
    """A delivery to the old incoming address, with the secret the trigger was created with."""
    got(f, "cutover")
    installed(f, "trigger")
    hook = fx(f)["hook"]
    return hook_post(org, hook["id"], hook["secret"], {"service": "api", "status": "failed"})


# ---------------------------------------------------------------------------------------------- 39 the cutover
def s39_steps(org, f):
    cutover(org, f)
    for slug in (STRATEGY, *(bot for bot, _ in INBOXES.values())):
        org.step(f, "bot:" + slug, guarded, f, "cutover", get, org, f"/api/v2/bots/{slug}")


def listed_before(f):
    """Before the upgrade, the report lists every fixture object: what would move, or unchanged."""
    installed(f, *("routine:" + n for n in ROUTINES), *("inbox:" + p for p in INBOXES), "task_rule", "goal")
    found = items(f, "before")
    bad = {label: (found.get(i) or {}).get("now") for label, i in fixture_ids(f).items()
           if not str((found.get(i) or {}).get("now")).startswith(("pending: would move", "unchanged"))}
    return not bad, f"{len(found)} item(s); not pending or unchanged: {bad}"


def accounted(f):
    """After it, every fixture object moved, runs as before (a read-only built-in), or is unchanged; nothing is
    pending, kept or failed."""
    setup_cut(f)
    found = items(f, "after")
    bad = {label: (found.get(i) or {}).get("now") for label, i in fixture_ids(f).items()
           if (found.get(i) or {}).get("now") not in ("moved", "runs as before", "unchanged")}
    left = sorted({i["now"] for i in found.values()} - {"moved", "runs as before", "unchanged", "removed"})
    return not bad and not left, f"fixture objects not accounted for: {bad}; other outcomes in the report: {left}"


def setup_cut(f):
    result = got(f, "cutover")
    if not isinstance(result, dict):
        raise Unmet(W5, f"the cutover did not run: {result}")
    return result


def scheduled(bot, routine):
    def want(auto):
        trig, turn = auto.get("trigger") or {}, step_of(auto, "bot_turn") or {}
        ok = (auto.get("owner_id") == "bot:" + bot and trig.get("type") == "schedule"
              and trig.get("cron") == routine["cron"] and trig.get("tz") == routine["timezone"]
              and is_bot(turn.get("bot"), bot) and turn.get("prompt") == routine["text"])
        return ok, f"owner {auto.get('owner_id')}, trigger {text(trig)}, bot turn {text(turn)[:100]}"
    return want


def triggered(bot, routine, trigger):
    def want(auto):
        trig, turn = auto.get("trigger") or {}, step_of(auto, "bot_turn") or {}
        ok = (auto.get("owner_id") == "bot:" + bot and all(trig.get(k) == v for k, v in trigger.items())
              and is_bot(turn.get("bot"), bot) and turn.get("prompt") == routine["text"])
        return ok, f"owner {auto.get('owner_id')}, trigger {text(trig)}, bot turn {text(turn)[:100]}"
    return want


def mapped(f, label, ident, want):
    """The one automation linked to `ident` is one `want` accepts; `want` answers (ok, detail)."""
    setup(f, "autos")
    hits = linked_to(f, ident)
    if len(hits) != 1:
        return False, f"{len(hits)} automation(s) link to {label} ({ident}), want 1"
    return want(hits[0])


def routine_row(name, trigger=None):
    bot, routine = ROUTINES[name]

    def check(f):
        installed(f, "routine:" + name, *(["trigger"] if name == "deploy_triage" else []))
        want = scheduled(bot, routine) if trigger is None else triggered(bot, routine, trigger)
        return mapped(f, name, fx(f)["routines"][name], want)
    return check


def builtin_row(label, ident_of, extra=None, needs=()):
    def check(f):
        installed(f, *needs)

        def want(auto):
            ok = auto.get("builtin") is True and auto.get("can_edit") is False and (extra is None or extra(auto))
            return ok, f"builtin {auto.get('builtin')}, can_edit {auto.get('can_edit')}, {text(auto)[:120]}"
        return mapped(f, label, ident_of(f), want)
    return check


def still_wanted_kept(auto):
    """Still wanted? was set daily at 10:00: the built-in keeps that control."""
    controls = auto.get("controls") or {}
    return controls.get("preset") == "daily" and controls.get("at") == "10:00"


def stale_row(f):
    installed(f, "job:stale_review")

    def want(auto):
        trig = auto.get("trigger") or {}
        fields = str(trig.get("cron") or "").split()
        hourly = trig.get("type") == "schedule" and len(fields) == 5 and fields[0].isdigit() and fields[1:] == ["*"] * 4
        ok = auto.get("builtin") is not True and auto.get("enabled") is False and hourly
        return ok, f"builtin {auto.get('builtin')}, enabled {auto.get('enabled')}, trigger {text(trig)}"
    return mapped(f, "stale ticket review", "job:stale_review", want)


def inbox_rows(f):
    installed(f, *("inbox:" + person for person in INBOXES))
    results = []
    for person, (_bot, mailbox) in INBOXES.items():
        def want(auto, person=person, mailbox=mailbox):
            trig, llm = auto.get("trigger") or {}, step_of(auto, "llm") or {}
            ok = (auto.get("owner_id") == "person:" + person and trig.get("type") == "message"
                  and trig.get("inbox") == mailbox and llm.get("as") == "assistant:" + person)
            return ok, f"owner {auto.get('owner_id')}, trigger {text(trig)}, llm as {llm.get('as')}"
        results.append(mapped(f, mailbox, mailbox, want))
    return all(ok for ok, _ in results), " | ".join(detail for _, detail in results)


def inbox_bots_retired(f):
    installed(f, *("inbox:" + person for person in INBOXES))
    states = {bot: body(f, "bot:" + bot).get("state") for bot, _ in INBOXES.values()}
    return all(s == "paused" for s in states.values()), f"Inbox Manager states {states}"


def briefing_row(f):
    """The Librarian's briefing routine becomes the hub's Market briefing built-in, on the routine's time and words."""
    _bot, routine = ROUTINES["briefing"]
    installed(f, "routine:briefing")

    def want(auto):
        ok, detail = scheduled(LIBRARIAN, routine)(auto)
        ok = ok and auto.get("builtin") is True and auto.get("name") == "Market briefing"
        return ok, f"{auto.get('name')} builtin {auto.get('builtin')}; {detail}"
    return mapped(f, "the briefing routine", fx(f)["routines"]["briefing"], want)


def destinations_kept(f):
    installed(f, *("destination:" + key for key in DESTINATIONS))
    setup(f, "destinations_after")
    have = {d.get("key"): d.get("receiver") for d in rows(body(f, "destinations_after"))}
    want = {key: "bot:" + d["receiver"] for key, d in DESTINATIONS.items()}
    return all(have.get(k) == v for k, v in want.items()), f"receivers {have}, want {want}"


def strategy_renamed(f):
    found = body(f, "bot:" + STRATEGY)
    return found.get("display_name") == "Strategy Bot", f"{STRATEGY} is named {found.get('display_name')!r}"


def wake_rows(f):
    """Each bot's custom wake rules are one What wakes automation it owns, each rule a Filter rule."""
    installed(f, *("rule:" + name for name in WAKE_RULES))
    setup(f, "autos")
    names, bad = {}, []
    for name, (bot, _event, _outcome) in WAKE_RULES.items():
        hits = linked_to(f, fx(f)["rules"][name])
        hit = hits[0] if len(hits) == 1 else None
        if hit and str(hit.get("name") or "").startswith(WAKES) and hit.get("owner_id") == "bot:" + bot \
                and hit.get("builtin") is not True and step_of(hit, "filter"):
            names.setdefault(bot, set()).add(hit.get("name"))
        else:
            bad.append(name)
    one_each = len(names) == 2 and all(len(v) == 1 for v in names.values())
    return not bad and one_each, f"unmapped {bad}, automations {({b: sorted(v) for b, v in names.items()})}"


def told_once(f):
    """The owner gets one task (something moved with a change: the 0.25 limit's rounding) naming what changed."""
    result = setup_cut(f)
    setup(f, "told")
    task = body(f, "told")
    task = task.get("task", task)
    words = f"{task.get('title')} {task.get('body')}"
    ok = not result["kept"] and not result["failed"] and task.get("owner_id") in ("human:dana", "person:dana", "dana") \
        and "$0.25 an hour" in words
    return ok, f"told {result['told']}: {task.get('title')!r} to {task.get('owner_id')}; kept {result['kept']}, " \
        f"failed {result['failed']}"


# ---------------------------------------------------------------------------------------------- 40 wake rules
def s40_steps(org, f):
    cutover(org, f)


def filter_rules(auto):
    found = []

    def walk(value):
        if isinstance(value, dict):
            if value.get("type") == "filter":
                found.extend(r for r in value.get("rules") or [] if isinstance(r, dict))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(auto.get("steps"))
    return found


def wakes_of(f, bot):
    return [a for a in automations(f) if str(a.get("name") or "").startswith(WAKES) and a.get("owner_id") == "bot:" + bot
            and a.get("builtin") is not True]


def wake_automation(bot):
    want = [(event, outcome) for _n, (b, event, outcome) in WAKE_RULES.items() if b == bot]

    def check(f):
        installed(f, *("rule:" + n for n, r in WAKE_RULES.items() if r[0] == bot))
        setup(f, "autos")
        mine = wakes_of(f, bot)
        if len(mine) != 1:
            return False, f"{len(mine)} {WAKES} automation(s) owned by bot:{bot}, want 1"
        rules = filter_rules(mine[0])
        hits = [w for w in want if any(w[0] in text(r) and w[1] in text(r) for r in rules)]
        return len(rules) == len(want) and hits == want, \
            f"{mine[0].get('name')}: {len(rules)} Filter rule(s), matched {hits} of {want}"
    return check


def only_custom(f):
    installed(f, *("rule:" + n for n in WAKE_RULES))
    setup(f, "autos")
    owners = sorted({a.get("owner_id") for a in automations(f) if str(a.get("name") or "").startswith(WAKES)
                     and a.get("builtin") is not True})
    want = sorted("bot:" + b for b in fx(f)["rule_bots"])   # the fixture's, and the Librarian's profile rows
    return owners == want and set(owners) >= {"bot:" + b for b, _e, _o in WAKE_RULES.values()}, \
        f"{WAKES} automations for {owners}, want {want}"


def builtin_wakes(f):
    setup(f, "autos")
    hits = [a for a in automations(f) if a.get("name") == "What wakes bots"]
    ok = len(hits) == 1 and hits[0].get("builtin") is True and hits[0].get("can_edit") is False
    return ok, f"{len(hits)} 'What wakes bots'" + (f": builtin {hits[0].get('builtin')}, can_edit "
                                                   f"{hits[0].get('can_edit')}" if hits else "")


def rules_moved(f):
    """Each custom rule's row moved to its bot's What wakes automation (the report names it)."""
    installed(f, *("rule:" + n for n in WAKE_RULES))
    found = items(f, "after")
    bad = {}
    for name, (bot, _e, _o) in WAKE_RULES.items():
        item = found.get(fx(f)["rules"][name]) or {}
        if item.get("now") != "moved" or [a["id"] for a in wakes_of(f, bot)] != [item.get("automation")]:
            bad[name] = f"{item.get('now')} -> {item.get('automation')}"
    return not bad, f"not moved to their bot's {WAKES}: {bad}"


# ---------------------------------------------------------------------------------------------- 41 runs across it
def old_fired(org, f, at):
    """The release before fired the hourly sweep's 09:30 occurrence and set its next one, as its scheduler did."""
    from backend.scheduler import stamp
    installed(f, "routine:pr_sweep")
    with org.store.transaction() as c:
        c.execute("UPDATE schedules SET last_fired_at=?, next_due_at=? WHERE id=?",
                  (stamp(at), stamp(at + dt.timedelta(hours=1)), fx(f)["routines"]["pr_sweep"]))
    return at


def s41_steps(org, f):
    install_fixture(org, f)                                                  # 09:00 UTC
    jump(org, org.now() + dt.timedelta(minutes=31))
    org.step(f, "old_fired", old_fired, org, f, org.now().replace(minute=30, second=0, microsecond=0))
    org.step(f, "cutover", upgrade, org)                                     # 09:31
    f["cutover_at"] = org.now()
    org.step(f, "after", guarded, f, "cutover", report, org)
    org.step(f, "autos", guarded, f, "cutover", get, org, "/api/v2/automations")
    org.step(f, "destinations_after", guarded, f, "cutover", get, org, "/api/v2/listening/destinations")
    org.step(f, "hook", deliver, org, f)
    org.step(f, "hook_runs", guarded, f, "hook", runs_for, org, f, fx(f)["routines"]["deploy_triage"])
    for n in range(4):   # to 10:31, a scheduler pass every 15 minutes: the 10:30 sweep is due once
        org.step(f, f"tick{n}", guarded, f, "cutover", org.pass_time, dt.timedelta(minutes=15))
    org.step(f, "sweep_runs", guarded, f, "cutover", runs_for, org, f, fx(f)["routines"]["pr_sweep"])
    org.step(f, "schedules", guarded, f, "cutover", schedule_rows, org, f)


def schedule_rows(org, f):
    with org.store.read() as c:
        return {r["id"]: dict(r) for r in c.execute("SELECT id, deleted_at, next_due_at FROM schedules WHERE id IN (%s)" % (
            ",".join("?" * len(ROUTINES))), tuple(fx(f)["routines"].values()))}


def sources_linked(f):
    installed(f, *("routine:" + n for n in ROUTINES), "task_rule", *("inbox:" + p for p in INBOXES))
    setup(f, "autos")
    want = {**{"routine " + n: sid for n, sid in fx(f)["routines"].items()}, "task rule": fx(f)["task_rule"],
            **{"inbox " + p: i["mailbox"] for p, i in fx(f)["inboxes"].items()}}
    off = {label: len(linked_to(f, ident)) for label, ident in want.items() if len(linked_to(f, ident)) != 1}
    return not off, f"automations linked (want 1 each): {off}"


def routines_stopped(f):
    """Each old routine row is retired in the write that moved it: soft-deleted, with no next fire."""
    installed(f, *("routine:" + n for n in ROUTINES))
    found = got(f, "schedules")
    live = [n for n, sid in fx(f)["routines"].items()
            if not (found.get(sid) or {}).get("deleted_at") or (found.get(sid) or {}).get("next_due_at")]
    return not live, f"old routine rows still due or live: {live}"


def hook_kept(f):
    ok, detail = replied(f, "hook", 200, 202, needs=())
    if not ok:
        return ok, f"old address answered {detail}"
    count = len(rows(body(f, "hook_runs")))
    return count == 1, f"old address answered {detail}; the Webhook automation has {count} run(s), want 1"


def fired_once(f):
    """The release before fired 09:30; the automation takes the routine's next_due, so across the cutover only the
    10:30 occurrence fires, once, and 09:30 never fires again."""
    installed(f, "routine:pr_sweep")
    setup(f, "sweep_runs")
    got(f, "old_fired")
    cut = f["cutover_at"]
    found = rows(body(f, "sweep_runs"))
    times = [started(r).strftime("%H:%M") for r in found]
    on_time = all(started(r) >= cut.replace(hour=10, minute=30, second=0, microsecond=0) for r in found)
    return len(found) == 1 and on_time, f"sweep runs after the cutover at {times}, want one at 10:30"


# ---------------------------------------------------------------------------------------------- 42 times
STANDUP_DAYS = (21, 22, 23, 24, 25, 26)   # Europe/London leaves summer time at 02:00 on Sunday Oct 25, 2026


def nine_london(day):
    return dt.datetime(2026, 10, day, 9, tzinfo=LONDON)


def close_standup(org):
    """The standup is finished each morning, so its open task closes before the next occurrence. While a converted
    routine's last task is open, an occurrence starts no run (CONTRACT section 4); the scenario checks the clock, not
    that rule, so it closes the task the way the team would."""
    with org.store.read() as c:
        open_ids = [r["id"] for r in c.execute(
            "SELECT id FROM tasks WHERE owner_id IN (?, ?) AND status NOT IN ('closed','done')",
            (CLAUDE_ID, "bot:" + CLAUDE_ID))]
    closed = 0
    for tid in open_ids:
        task = org.api("get", f"/api/v2/tasks/{tid}", wave=W5)
        version = (task.body.get("task") or task.body).get("version") if isinstance(task.body, dict) else None
        closed += org.api("post", f"/api/v2/tasks/{tid}", {"version": version, "status": "closed"}, wave=W5).status < 300
    return closed


def s42_steps(org, f):
    cutover(org, f)   # Tue Oct 20, 09:00 UTC (10:00 London)
    org.step(f, "read", lambda: get(org, f"/api/v2/automations/{converted_id(f, fx(f)['routines']['standup'])}"))
    org.step(f, "runs20", runs_for, org, f, fx(f)["routines"]["standup"])
    for day in STANDUP_DAYS:   # one day at a time, to 09:00 London exactly, so the scheduler fires each in turn
        org.step(f, f"to{day}", guarded, f, "cutover", jump, org, nine_london(day))
        org.step(f, f"runs{day}", runs_for, org, f, fx(f)["routines"]["standup"])
        org.step(f, f"closed{day}", close_standup, org)


def standup_trigger(f):
    installed(f, "routine:standup")
    trig = body(f, "read").get("trigger") or {}
    routine = ROUTINES["standup"][1]
    ok = trig.get("type") == "schedule" and trig.get("cron") == routine["cron"] and trig.get("tz") == routine["timezone"]
    return ok, f"trigger {text(trig)}"


def next_run_kept(f):
    installed(f, "routine:standup")
    setup(f, "read")
    want = nine_london(21).astimezone(UTC)
    old, new = fx(f)["due"].get("standup"), body(f, "read").get("next_run")
    if not old or not new:
        return False, f"routine next_due {old}, automation next_run {new}"
    return when(old) == when(new) == want, f"routine next_due {old}, automation next_run {new}, want {want.isoformat()}"


def fired_at_nine(day, utc_hour):
    def check(f):
        installed(f, "routine:standup")
        setup(f, f"runs{day - 1}", f"runs{day}")
        seen = {r.get("id") for r in rows(body(f, f"runs{day - 1}"))}
        new = [r for r in rows(body(f, f"runs{day}")) if r.get("id") not in seen]
        times = [started(r) for r in new]
        hits = [t for t in times if t.astimezone(LONDON).date().day == day and t.astimezone(LONDON).hour == 9
                and t.astimezone(LONDON).minute < 10 and t.astimezone(UTC).hour == utc_hour]
        return len(new) == 1 and len(hits) == 1, \
            f"started {[t.isoformat() for t in times]}, want one at 09:00 London on Oct {day} ({utc_hour}:00 UTC)"
    return check


# ---------------------------------------------------------------------------------------------- 43 limits and budgets
def s43_steps(org, f):
    cutover(org, f)
    org.step(f, "limits", guarded, f, "cutover", get, org, "/api/v2/usage/limits")
    org.step(f, "team_budget", guarded, f, "cutover", get, org, "/api/v2/settings/turn-budgets")
    org.step(f, "bot_budget", guarded, f, "cutover", get, org, f"/api/v2/bots/{BOT_BUDGET[0]}/budget")
    org.step(f, "told", told_task, org, f)


def told_task(org, f):
    told = setup_cut(f).get("told") or ""
    if not told or told.startswith("message:"):
        raise Unmet(W5, f"the owner was told by {told or 'nothing'}, want a task (the 0.25 limit moved with a change)")
    return org.api("get", f"/api/v2/tasks/{told}", who=DANA, wave=W5)


def limit_now(bot):
    want = half_up(HOURLY[bot])

    def check(f):
        installed(f, "limit:" + bot)
        setup(f, "limits")
        row = ((body(f, "limits") or {}).get("bots") or {}).get(bot) or {}
        per_30m = row.get("own_per_30m_usd", row.get("per_30m_usd"))
        hours = {k: row.get(k) for k in ("hourly_usd", "own_hourly_usd") if row.get(k) is not None}
        ok = isinstance(per_30m, (int, float)) and abs(per_30m - want) < 1e-6 and not hours
        return ok, f"per_30m_usd {per_30m} (want {want:.2f}), hour fields left {hours}"
    return check


def budgets(f):
    installed(f, "budget")
    team, mine = body(f, "team_budget"), body(f, "bot_budget")
    bot, own = BOT_BUDGET
    raised = all(team.get(k) == v for k, v in GUARD.items()) and team.get("defaults") == GUARD
    kept = (mine.get("own") or {}).get("turns_per_hour") == own["turns_per_hour"]
    return raised and kept, f"team {({k: team.get(k) for k in GUARD})}, defaults {team.get('defaults')}, want " \
        f"{GUARD}; {bot} own {mine.get('own')}"


# ---------------------------------------------------------------------------------------------- 44 restart
def s44_steps(org, f):
    cutover(org, f)
    org.step(f, "restart", guarded, f, "cutover", restart, org)
    org.step(f, "autos_restart", guarded, f, "restart", get, org, "/api/v2/automations")
    org.step(f, "again", guarded, f, "restart", upgrade, org)   # even with its record lost, nothing moves twice
    org.step(f, "autos_again", guarded, f, "again", get, org, "/api/v2/automations")
    org.step(f, "told_count", guarded, f, "again", told_count, org, f)


def restart(org):
    """The server starts again on the moved install (Store.initialize runs the cutover at its end)."""
    from backend import redesign_migration as M
    org.store.initialize(seed_market=False)
    with org.store.read() as c:
        return M.state(c)


def told_count(org, f):
    mid = setup_cut(f)["migration"]
    with org.store.read() as c:
        tasks = c.execute("SELECT count(*) FROM tasks WHERE title LIKE '%Automations%'").fetchone()[0]
        notes = c.execute("SELECT count(*) FROM messages WHERE refs LIKE ?", (f"%{mid}%",)).fetchone()[0]
    return tasks + notes


def same_ids(f, key):
    before = sorted(a["id"] for a in automations(f))
    after = sorted(a["id"] for a in automations(f, key))
    return before == after, f"{len(before)} automation(s) before, {len(after)} after; added " \
        f"{sorted(set(after) - set(before))[:3]}, gone {sorted(set(before) - set(after))[:3]}"


def restart_same(f):
    first = setup_cut(f)
    record = got(f, "restart")
    ok, detail = same_ids(f, "autos_restart")
    return ok and record.get("migration") == first["migration"] and record.get("state") == "done", \
        f"record {record.get('state')} {record.get('migration')}; {detail}"


def again_nothing(f):
    result = got(f, "again")
    moved = (result or {}).get("converted")
    ok, detail = same_ids(f, "autos_again")
    return ok and moved == 0 and not result.get("kept") and not result.get("failed"), \
        f"second cutover moved {moved}, kept {result.get('kept')}, failed {result.get('failed')}; {detail}"


def told_one(f):
    count = got(f, "told_count")
    return count == 1, f"{count} task(s) or notice(s) about the move, want 1"


# ---------------------------------------------------------------------------------------------- the scenarios
def routine_label(name, becomes):
    bot, routine = ROUTINES[name]
    return f"{routine['title']} ({bot}) becomes {becomes}"


def _all(f, checks):
    results = [check(f) for check in checks]
    return all(ok for ok, _ in results), " | ".join(detail for _, detail in results)


SCENARIOS = [
    Scenario(39, "migration-cutover", "The upgrade's cutover maps every fixture object (PLAN §8)", s39_steps, [
        Check("before the upgrade the report lists every fixture object: would move, or unchanged", W5, listed_before),
        Check("after it every fixture object moved, runs as before, or is unchanged; none pending, kept or failed", W5,
              accounted),
        *[Check(routine_label(name, "a Schedule automation: same owner, cron, time zone; text as its Bot turn"), W5,
                routine_row(name)) for name in CRON if name != "briefing"],
        Check(routine_label("release_notes", "an Event automation on release_published"), W5,
              routine_row("release_notes", {"type": "event", "name": "release_published"})),
        Check(routine_label("deploy_triage", f"a Webhook automation on the same trigger ({HOOK_ID})"), W5,
              routine_row("deploy_triage", {"type": "webhook", "id": HOOK_ID})),
        Check("custom wake rules become one What wakes automation per bot, each rule a Filter step", W5, wake_rows),
        Check("the task rule becomes a read-only built-in", W5,
              builtin_row("task rule", lambda f: fx(f)["task_rule"], needs=("task_rule",))),
        Check("system jobs (Start assigned tasks, Recover unstarted) become read-only built-ins", W5,
              lambda f: _all(f, [builtin_row(job, lambda f, job=job: "job:" + job) for job in BUILTIN_JOBS])),
        Check("Still wanted? becomes a built-in that keeps its control (daily at 10:00)", W5,
              builtin_row("still wanted", lambda f: "job:still_wanted", still_wanted_kept, needs=("job:still_wanted",))),
        Check("Stale ticket review becomes a regular automation, Off, hourly", W5, stale_row),
        Check("Dana's and Priya's inboxes become message automations on their owner, LLM as the owner's Assistant", W5,
              inbox_rows),
        Check("their Inbox Manager bots retire (paused)", W5, inbox_bots_retired),
        Check("Health Monitor becomes a read-only built-in", W5,
              builtin_row("Health Monitor", lambda f: HEALTH)),
        Check("the Librarian's market briefing becomes the Market briefing built-in, on its own time and words", W5,
              briefing_row),
        Check(f"{OLD_NAME} becomes Strategy Bot in the same row (slug goal-manager)", W5, strategy_renamed),
    ], bots=FIXTURE_BOTS, first_tick=False),
    Scenario(40, "migration-wake-rules", "Custom wake rules become one What wakes automation per bot", s40_steps, [
        Check("Builder (Claude): one What wakes automation, its two rules as Filter rules (wake, note)", W5,
              wake_automation(CLAUDE_ID)),
        Check("Builder (OpenAI): one What wakes automation, its rule as a Filter rule (batch)", W5,
              wake_automation(OPENAI_ID)),
        Check("only bots with custom rules get a What wakes automation", W5, only_custom),
        Check("the built-in wake rules show as one read-only built-in, What wakes bots", W5, builtin_wakes),
        Check("each custom rule's row moved to its bot's What wakes automation", W5, rules_moved),
    ], bots=FIXTURE_BOTS, first_tick=False),
    Scenario(41, "migration-runs", "Across the cutover: links to sources, old rows stopped, no double fire", s41_steps, [
        Check("each converted routine, the task rule and each inbox links to its source", W5, sources_linked),
        Check("the old routine rows are retired: soft-deleted, nothing due", W5, routines_stopped),
        Check("the webhook keeps its URL and secret: the old address runs the Webhook automation", W5, hook_kept),
        Check("the hourly sweep fires exactly once across the cutover (10:30)", W5, fired_once),
        Check("listening destinations keep their receivers (market: the Librarian)", W5, destinations_kept),
    ], bots=FIXTURE_BOTS, first_tick=False),
    Scenario(42, "migration-times", "A converted routine fires at the same local time, across the DST change",
             s42_steps, [
        Check("the converted standup keeps 0 9 * * * in Europe/London", W5, standup_trigger),
        Check("its next_run is the routine's next fire: 09:00 London on Oct 21 (08:00 UTC)", W5, next_run_kept),
        Check("Oct 24, summer time: fires at 09:00 London (08:00 UTC)", W5, fired_at_nine(24, 8)),
        Check("Oct 25, the change: fires at 09:00 London (09:00 UTC)", W5, fired_at_nine(25, 9)),
        Check("Oct 26, winter time: fires at 09:00 London (09:00 UTC)", W5, fired_at_nine(26, 9)),
    ], bots=FIXTURE_BOTS, first_tick=False),
    Scenario(43, "migration-limits", "Hourly $X becomes 30-min $X/2, rounded up to $0.10; budgets rise to the guard",
             s43_steps, [
        Check("hourly 3.00 is now 1.50 per 30 minutes, hour cleared", W5, limit_now(OPENAI_ID)),
        Check("hourly 5.00 is now 2.50 per 30 minutes, hour cleared", W5, limit_now(CLAUDE_ID)),
        Check("hourly 0.25 is now 0.20 per 30 minutes, hour cleared", W5, limit_now(RELEASE_ID)),
        Check("the owner is told once, in a task naming the 0.25 limit's rounding; nothing kept or failed", W5,
              told_once),
        Check("team turn budgets rise to the guard (300/h, 2,500/day, also the defaults); a bot's 600/h is kept", W5,
              budgets),
    ], bots=FIXTURE_BOTS, first_tick=False),
    Scenario(44, "migration-restart", "A restart, or a second cutover, moves nothing again", s44_steps, [
        Check("a restart keeps the cutover's record and the same automations", W5, restart_same),
        Check("a second cutover (record lost) converts nothing and makes no duplicates", W5, again_nothing),
        Check("the owner was told once in all", W5, told_one),
    ], bots=FIXTURE_BOTS, first_tick=False),
]
