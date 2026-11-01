"""Engine (PLAN §7, scenarios 22-31): Find, bot turns, switched-off steps, paths in order and their limits, the run
log, versions, built-ins, next_run across DST, and webhook triggers. Every automation is a real POST to
/api/v2/automations. Input is fed the way CONTRACT §2 settles it: POST /{id}/run with {"input": ...} (the product's
"Test with sample"), mail through the mail sync's route, webhooks through the existing incoming trigger. Every check
reads what the product answered: the run log (GET /{id}/runs), the automation read (counts, next_run), or the reply
itself. A check that needs a run waits on its automation (wave 3), so an empty listing never passes."""
import datetime as dt
import json
from zoneinfo import ZoneInfo

from ..fakes import ReplayJudge, attach_judge, hook, stripe_sig
from ..harness import (DANA, DANA_REF, CLAUDE_ID, CLAUDE_REF, LEAD, LEAD_REF, OPENAI_REF, W3, Check, Reply,
                       Scenario, Unmet, auto_id, automation, body, builtin_rows, edit_auto, got, hook_post,
                       mail_message, publish_mail, replied, rows, run_now, setup, today_counts, turn_of)

INBOX = "dana@acme.example"
SCHEDULE = {"type": "schedule", "cron": "0 9 * * *", "tz": "UTC"}
TO_DANA = {"type": "task_received", "to": DANA_REF}
BUCKET = {"bucket": {"type": "choice", "instructions": "Where does it go?",
                     "criteria": {"reply": "answer it", "archive": "noise"}}}
NY, UTC = ZoneInfo("America/New_York"), dt.timezone.utc
MINUTE = dt.timedelta(minutes=1)


# ---------------------------------------------------------------------------------------------- shared steps
def note(sid, text, enabled=True):
    return {"id": sid, "type": "action", "enabled": enabled, "do": "note", "args": {"text": text}}


def cond(field, op, value, **extra):
    return {"field": field, "op": op, "value": value, **extra}


def task_input(title):
    """A task_received trigger's output: the task builder-claude sent Dana (CONTRACT §2 Feeding input)."""
    return {"task": {"title": title, "body": "Scenario task.", "owner_id": DANA_REF, "requester_id": CLAUDE_REF}}


def message_input(msg_id, sender, subject):
    """A message trigger's output: one received mail, in the shape the mail sync publishes."""
    return {"message": mail_message(msg_id, sender, subject)}


def mail(org, msg_id, sender, subject):
    """One received mail as the sync stores it, with the epoch the sync sets (mail_message leaves it 0)."""
    return {**mail_message(msg_id, sender, subject), "epoch": int(org.now().timestamp())}


def runs_of(org, facts, key="auto"):
    return org.api("get", f"/api/v2/automations/{auto_id(facts, key)}/runs", wave=W3)


def versions_of(org, facts, key="auto"):
    return org.api("get", f"/api/v2/automations/{auto_id(facts, key)}/versions", wave=W3)


def current(org, facts, key="auto"):
    return org.api("get", f"/api/v2/automations/{auto_id(facts, key)}", wave=W3)


def restore(org, facts, n, key="auto"):
    return org.api("post", f"/api/v2/automations/{auto_id(facts, key)}/versions/{n}/restore", {}, wave=W3)


def listing(org):
    return org.api("get", "/api/v2/automations", wave=W3)


def later(org, delta, tail=dt.timedelta(minutes=10)):
    """Moves the fake clock on by `delta`: one jump for all but the last ten minutes, then org.pass_time for those, so
    the runner reports in and the scheduler runs when the time comes. pass_time alone steps 50 seconds with a heartbeat
    each (about 0.2 s an hour here), so 31 days that way would take two minutes."""
    jump = (delta - tail).total_seconds()
    if jump > 0:
        org.clock.shift(jump)
    org.pass_time(min(delta, tail))
    return org.now()


def until(org, when):
    return later(org, when - org.now())


def incoming(org, path, payload, headers=None):
    """An outside delivery with its own headers (a signature), no bearer token: the secret is in the path
    (backend/triggers.py). hook_post covers the unsigned ones; this is the signed one, through the same route check."""
    if not org._registered("post", path):
        raise Unmet(W3, f"no route POST {path}")
    response = hook(org.client, path, json.dumps(payload).encode(), headers)
    try:
        data = response.json()
    except ValueError:
        data = response.text
    reply = Reply(response.status_code, data, W3)
    org.log.append(f"POST {path} (signed) -> {reply.status} {reply.excerpt()}")
    return reply


# ---------------------------------------------------------------------------------------------- reading runs
def unwrap(data):
    return data.get("automation", data) if isinstance(data, dict) else {}


def fresh(f, before, after):
    """The runs the `after` listing has that the `before` listing did not."""
    seen = {r.get("id") for r in rows(body(f, before))}
    return [r for r in rows(body(f, after)) if r.get("id") not in seen]


def one_new(f, before, after):
    """The one run that appeared between two listings; Unmet-free once both listings were read, else a reason."""
    setup(f, "auto", before, after)
    runs = fresh(f, before, after)
    return (runs[0], "") if len(runs) == 1 else (None, f"{len(runs)} new run(s), want 1")


def step_row(run, sid):
    return next((s for s in run.get("steps") or [] if s.get("id") == sid), None)


def ran_ok(run, sid):
    row = step_row(run, sid)
    return bool(row) and row.get("status") == "ok"


def started(run):
    """The run's start as an aware time (a naive one is read as UTC), or None when it has none."""
    try:
        when = dt.datetime.fromisoformat(str(run.get("started_at") or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def branch(f, before, after, ran_ids, held_ids):
    """Exactly one run in the window; every id in `ran_ids` ran (ok) and none in `held_ids` did."""
    run, why = one_new(f, before, after)
    if run is None:
        return False, why
    ran = [sid for sid in ran_ids if ran_ok(run, sid)]
    extra = [sid for sid in held_ids if ran_ok(run, sid)]
    return ran == list(ran_ids) and not extra, f"ran {ran}, also ran {extra}"


# ---------------------------------------------------------------------------------------------- 22 find
def s22_steps(org, f):
    org.step(f, "auto", automation, org, "Invoices · dana", DANA_REF, trigger=SCHEDULE, steps=[
        {"id": "look", "type": "find", "enabled": True, "source": "mail", "query": "Invoice", "since": "last_run",
         "limit": 10},
        note("each", "seen")])
    org.step(f, "runs0", runs_of, org, f)
    org.pass_time(MINUTE)
    org.step(f, "mail1", publish_mail, org, INBOX,
             [mail(org, f"m22-{n}", "billing@vendor.example", f"Invoice {n}") for n in (1, 2, 3)]
             + [mail(org, "m22-lunch", "omar@acme.example", "Lunch on Friday")])
    org.pass_time(MINUTE)
    org.step(f, "run1", run_now, org, f, "auto")
    org.step(f, "runs1", runs_of, org, f)
    org.pass_time(MINUTE)
    org.step(f, "run2", run_now, org, f, "auto")
    org.step(f, "runs2", runs_of, org, f)
    org.pass_time(MINUTE)
    org.step(f, "mail2", publish_mail, org, INBOX, [mail(org, "m22-4", "billing@vendor.example", "Invoice 4")])
    org.pass_time(MINUTE)
    org.step(f, "run3", run_now, org, f, "auto")
    org.step(f, "runs3", runs_of, org, f)


def items_of(run):
    return ((step_row(run, "look") or {}).get("outputs") or {}).get("items") or []


def found_three(f):
    setup(f, "mail1", "run1")
    run, why = one_new(f, "runs0", "runs1")
    if run is None:
        return False, why
    items = items_of(run)
    return len(items) == 3, f"{len(items)} item(s) found, want the 3 invoices (not the lunch mail)"


def once_per_item(f):
    setup(f, "mail1", "run1")
    run, why = one_new(f, "runs0", "runs1")
    if run is None:
        return False, why
    each = [s for s in run.get("steps") or [] if s.get("id") == "each" and s.get("status") == "ok"]
    return len(each) == 3, f"{len(each)} ok row(s) of the per-item step, want 3"


def empty_second(f):
    setup(f, "run2")
    run, why = one_new(f, "runs1", "runs2")
    if run is None:
        return False, why
    return run.get("status") == "empty", f"status {run.get('status')}, want empty"


def since_last(f):
    setup(f, "mail2", "run3")
    run, why = one_new(f, "runs2", "runs3")
    if run is None:
        return False, why
    items = items_of(run)
    return len(items) == 1, f"{len(items)} item(s) found, want only Invoice 4"


# ---------------------------------------------------------------------------------------------- 23 bot turn
PROMPT = "Check the build"


def s23_steps(org, f):
    org.step(f, "auto", automation, org, "Build check · builder-claude", CLAUDE_REF, trigger=SCHEDULE, steps=[
        {"id": "act", "type": "bot_turn", "enabled": True, "bot": CLAUDE_ID, "prompt": PROMPT}])
    org.step(f, "runs0", runs_of, org, f)
    org.step(f, "run", run_now, org, f, "auto")
    org.step(f, "runs", runs_of, org, f)
    org.step(f, "task", lambda: org.api("get", f"/api/v2/tasks/{turn_task(f)}", wave=W3))
    # During the turn the bot calls the hub with the turn's own token, as its tools do.
    org.step(f, "turn1", org.turn, CLAUDE_ID,
             act=lambda token, attempt: f.__setitem__("me", org.api("get", "/api/v2/me", who=token, wave=W3)))
    org.step(f, "turn2", org.turn, CLAUDE_ID)


def the_turn(f):
    """The turn the run's bot_turn step started (CONTRACT §2 Turn origin), or a reason."""
    setup(f, "run")
    run, why = one_new(f, "runs0", "runs")
    if run is None:
        return None, why
    turn = turn_of(step_row(run, "act") or {})
    return (turn, "") if turn else (None, f"no turn on the bot_turn step: {step_row(run, 'act')}")


def turn_task(f):
    turn, why = the_turn(f)
    if not turn or not turn.get("task"):
        raise Unmet(W3, why or f"the turn names no task: {turn}")
    return turn["task"]


def turn_shape(f):
    turn, why = the_turn(f)
    if turn is None:
        return False, why
    missing = [k for k in ("bot", "task", "session") if k not in turn]
    return not missing and bool(turn.get("task")), f"turn {turn}, missing {missing}"


def turn_bot(f):
    turn, why = the_turn(f)
    if turn is None:
        return False, why
    return turn.get("bot") in (CLAUDE_ID, CLAUDE_REF), f"turn bot {turn.get('bot')!r}, want builder-claude"


def turn_prompt(f):
    task = body(f, "task")
    return PROMPT in json.dumps(task, default=str), f"prompt {PROMPT!r} in the turn's task: {PROMPT in str(task)}"


def turn_claimed(f):
    setup(f, "run")
    turn = got(f, "turn1")
    return bool(turn), "one turn claimed on builder-claude" if turn else "no turn claimed for the run"


def turn_tools(f):
    """A turn's tools are its bot's (the hub's tools plus its bot.yaml's): they act as the token the turn was handed. The
    turn's hub calls are builder-claude's own, from this attempt, not the automation owner's."""
    setup(f, "run")
    if not got(f, "turn1"):
        return False, "no turn claimed"
    me = f.get("me")
    body = me.body if me is not None and isinstance(me.body, dict) else {}
    ok = body.get("actor") == "bot:" + CLAUDE_ID and body.get("attempt_id") == got(f, "turn1")
    return ok, f"the turn's calls act as {body.get('actor')} (attempt {body.get('attempt_id')})"


def second_held(f):
    setup(f, "run")
    if not got(f, "turn1"):
        return False, "no first turn to compare with"
    return got(f, "turn2") is None, "the second claim got nothing" if got(f, "turn2") is None else "a second turn ran"


# ---------------------------------------------------------------------------------------------- 24 switched off
def s24_steps(org, f):
    org.step(f, "judge", attach_judge, org, ReplayJudge())
    org.step(f, "auto", automation, org, "Triage · dana", DANA_REF, trigger=TO_DANA, steps=[
        {"id": "gate", "type": "filter", "enabled": True, "rules": []},
        {"id": "rank", "type": "decide", "enabled": False, "questions": BUCKET, "level": "low", "out": "d1"},
        {"id": "draft", "type": "llm", "enabled": False, "as": CLAUDE_REF, "prompt": "Draft a reply", "out": "txt"},
        note("note", "ran")])
    org.step(f, "runs0", runs_of, org, f)
    org.step(f, "run", run_now, org, f, "auto", sample=task_input("Quarterly report"))
    org.step(f, "runs", runs_of, org, f)
    org.step(f, "claim", org.turn, CLAUDE_ID)


def the_run(f):
    setup(f, "run")
    return one_new(f, "runs0", "runs")


def skipped(f, sid):
    run, why = the_run(f)
    if run is None:
        return None, why
    row = step_row(run, sid)
    return row, f"{sid} logged {(row or {}).get('status')}, want skipped"


def off_logged(f):
    row, text = skipped(f, "rank")
    ok = bool(row) and row.get("status") == "skipped" and not row.get("outputs")
    return ok, text + (f", outputs {row.get('outputs')}" if row else "")


def llm_off(f):
    row, text = skipped(f, "draft")
    if not row:
        return False, text
    claimed = got(f, "claim")
    ok = row.get("status") == "skipped" and turn_of(row) is None and claimed is None
    return ok, f"{text}; turn {turn_of(row)}; claimed {claimed}"


def judge_untouched(f):
    run, why = the_run(f)
    if run is None:
        return False, why
    calls = got(f, "judge").calls
    return calls == [], f"{len(calls)} judge call(s), want 0"


def enabled_ran(f):
    run, why = the_run(f)
    if run is None:
        return False, why
    states = {sid: (step_row(run, sid) or {}).get("status") for sid in ("gate", "note")}
    return all(v == "ok" for v in states.values()), f"{states}"


# ---------------------------------------------------------------------------------------------- 25 paths
def s25_steps(org, f):
    org.step(f, "auto", automation, org, "Urgent first · dana", DANA_REF, trigger=TO_DANA, steps=[
        {"id": "fork", "type": "paths", "enabled": True,
         "paths": [{"name": "urgent", "when": [cond("task.title", "contains", "urgent")], "steps": [note("u", "urgent")]},
                   {"name": "prod", "when": [cond("task.title", "contains", "prod")], "steps": [note("p", "prod")]}],
         "otherwise": {"steps": [note("o", "otherwise")]}}])
    org.step(f, "runs0", runs_of, org, f)
    org.step(f, "both", run_now, org, f, "auto", sample=task_input("urgent: prod down"))
    org.step(f, "runs1", runs_of, org, f)
    org.step(f, "none", run_now, org, f, "auto", sample=task_input("lunch?"))
    org.step(f, "runs2", runs_of, org, f)


def path_taken(f, sent, before, after, want):
    setup(f, sent)
    run, why = one_new(f, before, after)
    if run is None:
        return False, why
    out = (step_row(run, "fork") or {}).get("outputs")
    return out == {"path": want}, f"paths output {out}, want {{'path': {want!r}}}"


def ran_in(f, sent, before, after, ran_ids, held_ids):
    setup(f, sent)
    return branch(f, before, after, ran_ids, held_ids)


# ---------------------------------------------------------------------------------------------- 26 limits
def wide(n):
    """One paths step with n branches (CONTRACT: at most 10 per fork)."""
    return [{"id": "fork", "type": "paths", "enabled": True,
             "paths": [{"name": f"p{i}", "when": [cond("task.title", "contains", str(i))], "steps": [note(f"n{i}", "x")]}
                       for i in range(n)],
             "otherwise": {"steps": []}}]


def nested(depth):
    """`depth` paths forks, each inside the branch of the one before (CONTRACT: at most 3 levels)."""
    steps = [note("leaf", "deep")]
    for level in range(depth):
        steps = [{"id": f"fork{level}", "type": "paths", "enabled": True,
                  "paths": [{"name": "in", "when": [cond("task.title", "contains", "x")], "steps": steps}],
                  "otherwise": {"steps": []}}]
    return steps


def s26_steps(org, f):
    org.step(f, "ten", automation, org, "Ten paths", DANA_REF, steps=wide(10))
    org.step(f, "eleven", automation, org, "Eleven paths", DANA_REF, steps=wide(11))
    org.step(f, "three", automation, org, "Three forks", DANA_REF, steps=nested(3))
    org.step(f, "deep", automation, org, "Four forks", DANA_REF, steps=nested(4))
    org.step(f, "listing", listing, org)


def not_listed(f):
    setup(f, "listing", "ten", "three")   # the accepted flows must be listed, or the check has nothing to show
    names = {r.get("name") for r in rows(body(f, "listing"))}
    gone = {"Eleven paths", "Four forks"} & names
    return not gone and {"Ten paths", "Three forks"} <= names, f"listed: {sorted(n for n in names if n)}"


# ---------------------------------------------------------------------------------------------- 27 run log
def s27_steps(org, f):
    org.step(f, "auto", automation, org, "Invoice log · dana", DANA_REF,
             trigger={"type": "message", "inbox": INBOX, "filters": [cond("message.subject", "contains", "Invoice")]},
             steps=[{"id": "gate", "type": "filter", "enabled": True, "rules": []}, note("kept", "kept")])
    org.step(f, "read0", current, org, f)
    org.step(f, "runs0", runs_of, org, f)
    org.step(f, "hit", run_now, org, f, "auto",
             sample=message_input("m27-1", "billing@vendor.example", "Invoice 27"))
    org.step(f, "runs1", runs_of, org, f)
    org.step(f, "miss", run_now, org, f, "auto",
             sample=message_input("m27-2", "news@list.example", "Weekly newsletter"))
    org.step(f, "runs2", runs_of, org, f)
    org.step(f, "read1", current, org, f)
    org.step(f, "day29", later, org, dt.timedelta(days=29))
    org.step(f, "runs29", runs_of, org, f)
    org.step(f, "day31", later, org, dt.timedelta(days=2))
    org.step(f, "runs31", runs_of, org, f)


def step_rows_carry_cost(f):
    setup(f, "hit")
    run, why = one_new(f, "runs0", "runs1")
    if run is None:
        return False, why
    steps = run.get("steps") or []
    ok = len(steps) == 2 and all(s.get("status") and "cost" in s for s in steps) and "cost" in run
    return ok, f"run cost {run.get('cost')!r}; steps {[(s.get('id'), s.get('status'), s.get('cost')) for s in steps]}"


def dropped_no_row(f):
    setup(f, "miss", "runs1", "runs2")
    new = fresh(f, "runs1", "runs2")
    return not new, f"{len(new)} run row(s) for the dropped message, want 0"


def dropped_counted(f):
    setup(f, "auto", "read0", "miss", "read1")
    before, after = today_counts(unwrap(body(f, "read0"))), today_counts(unwrap(body(f, "read1")))
    if before is None or after is None:
        return False, f"counts.today before {before}, after {after}"
    runs = (after.get("runs") or 0) - (before.get("runs") or 0)
    dropped = (after.get("dropped") or 0) - (before.get("dropped") or 0)
    return runs == 1 and dropped == 1, f"today {before} -> {after}: runs +{runs}, dropped +{dropped}, want +1 each"


def hit_run_at(f, key):
    """The matching message's run as the listing at `key` shows it, or None when the row is gone."""
    setup(f, "hit", key)
    run, why = one_new(f, "runs0", "runs1")
    if run is None:
        raise Unmet(W3, why)
    return next((r for r in rows(body(f, key)) if r.get("id") == run.get("id")), None), run


def kept_29(f):
    got(f, "day29")
    after, _ = hit_run_at(f, "runs29")
    left = (after or {}).get("steps") or []
    return bool(left), f"{len(left)} step row(s) on day 29" if after else "the run row was pruned by day 29"


def pruned_31(f):
    got(f, "day31")
    after, before = hit_run_at(f, "runs31")
    if after is None:
        return False, "the run row itself was pruned"
    left = after.get("steps") or []
    return bool(before.get("steps")) and not left, f"{len(left)} step row(s) left after 31 days; row stays"


def counts_kept(f):
    """The day's counts outlive the step detail: the runs listing's `daily` (CONTRACT §2 Runs) still has the day the
    two messages came, with its run and its drop, after the 31-day prune."""
    got(f, "day31")
    _, run = hit_run_at(f, "runs31")
    day = str(run.get("started_at") or "")[:10]
    daily = (unwrap(body(f, "runs31")) or {}).get("daily") or []
    row = next((d for d in daily if d.get("day") == day), None)
    ok = bool(row) and row.get("runs") == 1 and row.get("dropped") == 1
    return ok, f"daily for {day}: {row}, want runs 1 and dropped 1"


# ---------------------------------------------------------------------------------------------- 28 versions
def s28_steps(org, f):
    org.step(f, "auto", automation, org, "Versions · dana", DANA_REF, steps=[note("one", "one")])
    org.step(f, "edit", edit_auto, org, f, "auto", DANA, steps=[note("two", "two")])
    org.step(f, "versions", versions_of, org, f)
    org.step(f, "restore", restore, org, f, 1)
    org.step(f, "after", current, org, f)
    org.step(f, "versions2", versions_of, org, f)


def versions_recorded(f):
    setup(f, "auto", "edit", "versions")
    vs = [v for v in rows(body(f, "versions")) if v.get("n") in (1, 2)]
    ns = sorted(v["n"] for v in vs)
    ok = ns == [1, 2] and all(v.get("actor") and v.get("at") for v in vs)
    return ok, f"versions {ns}, each with actor and time"


def restored(f):
    setup(f, "restore", "after", "versions2")
    steps = unwrap(body(f, "after")).get("steps") or []
    text = (steps[0].get("args") or {}).get("text") if steps else None
    count = len(rows(body(f, "versions2")))
    return text == "one" and count == 3, f"flow says {text!r}, {count} version(s), want 'one' and 3"


# ---------------------------------------------------------------------------------------------- 29 built-in
def pick_builtin(f):
    """A built-in the hub made (CONTRACT §2 Built-ins). One owned by the lead's reports or the lead comes first, so the
    lead's 403 is the built-in rule and not plain ownership."""
    found = builtin_rows(body(f, "listing"))
    if not found:
        raise Unmet(W3, "the listing has no built-in automation (builtin true)")
    order = {CLAUDE_REF: 0, OPENAI_REF: 0, LEAD_REF: 1}
    return min(found, key=lambda row: order.get(row.get("owner_id"), 2))


def on_builtin(org, f, method, who, payload=None):
    path = f"/api/v2/automations/{pick_builtin(f).get('id')}"
    return org.api(method, path, payload, who=who, wave=W3)


def s29_steps(org, f):
    org.step(f, "listing", listing, org)
    org.step(f, "post", org.api, "post", "/api/v2/automations", {
        "name": "Fake built-in · dana", "owner_id": DANA_REF, "enabled": True, "builtin": True, "trigger": SCHEDULE,
        "steps": [{"id": "gate", "type": "filter", "enabled": True, "rules": []}]}, wave=W3)
    org.step(f, "lead_pause", on_builtin, org, f, "patch", LEAD, {"enabled": False})
    org.step(f, "dana_pause", on_builtin, org, f, "patch", DANA, {"enabled": False})
    org.step(f, "delete", on_builtin, org, f, "delete", DANA)
    org.step(f, "listing2", listing, org)


def builtins_listed(f):
    found = builtin_rows(body(f, "listing"))
    return bool(found), f"{len(found)} built-in(s): {[r.get('name') for r in found][:4]}"


def post_refused(f):
    ok, text = replied(f, "post", 400, 403, 409, 422, needs=("listing",))
    names = {r.get("name") for r in rows(body(f, "listing2"))}
    return ok and "Fake built-in · dana" not in names, f"{text}; listed after: {'Fake built-in · dana' in names}"


def lead_refused(f):
    ok, text = replied(f, "lead_pause", 403, needs=("listing",))
    return ok, f"{text} (built-in owned by {pick_builtin(f).get('owner_id')})"


def dana_pause_warns(f):
    setup(f, "dana_pause")
    reply = got(f, "dana_pause")
    warned = "warn" in json.dumps(reply.body, default=str).lower()   # CONTRACT names no field for the warning
    return reply.status == 200 and warned, f"{reply.status}, warning {warned}"


def delete_refused(f):
    ok, text = replied(f, "delete", 409, needs=("listing",))
    still = any(r.get("id") == pick_builtin(f).get("id") for r in rows(body(f, "listing2")))
    return ok and still, f"{text}; still listed {still}"


# ---------------------------------------------------------------------------------------------- 30 next_run, DST
# Nov 1 2026 is the change in New York (02:00 EDT back to 01:00 EST): 09:00 local is 13:00 UTC before it, 14:00 after.
DAYS = [dt.date(2026, 10, 31), dt.date(2026, 11, 1), dt.date(2026, 11, 2)]


def local(day, hour, minute=0):
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY)


def s30_steps(org, f):
    org.step(f, "auto", automation, org, "Daily digest · dana", DANA_REF,
             trigger={"type": "schedule", "cron": "0 9 * * *", "tz": "America/New_York"}, steps=[note("d", "digest")])
    org.step(f, "read0", current, org, f)
    org.step(f, "to_base", until, org, local(dt.date(2026, 10, 30), 9, 1))   # catch-up and Oct 30's run land here
    org.step(f, "runs_base", runs_of, org, f)
    for n, day in enumerate(DAYS):
        org.step(f, f"morning{n}", until, org, local(day, 8))
        org.step(f, f"read{n + 1}", current, org, f)
        org.step(f, f"fired{n}", until, org, local(day, 9, 1))
        org.step(f, f"runs{n}", runs_of, org, f)


def next_run_is(f, key, want_utc):
    """`next_run` on the automation read is a UTC ISO time naming `want_utc`."""
    setup(f, "auto", key)
    value = unwrap(body(f, key)).get("next_run")
    text = str(value or "")
    try:
        when = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False, f"next_run {value!r} is not an ISO time"
    in_utc = when.utcoffset() == dt.timedelta(0)
    return in_utc and when == want_utc, f"next_run {value!r}, want {want_utc.strftime('%Y-%m-%dT%H:%MZ')} (UTC)"


def fired_each_day(f):
    """Exactly one run a day, started at 09:00 New York, on both sides of the change."""
    keys = ["runs_base"] + [f"runs{n}" for n in range(len(DAYS))]
    setup(f, "auto", *keys)
    seen = []
    for n, day in enumerate(DAYS):
        new = fresh(f, keys[n], keys[n + 1])
        starts = [started(r) for r in new]
        hits = [t for t in starts if t and t.astimezone(NY).date() == day and t.astimezone(NY).hour == 9]
        seen.append(f"{day}: {[t.astimezone(UTC).strftime('%H:%MZ') if t else None for t in starts]}")
        if len(new) != 1 or len(hits) != 1:
            return False, "; ".join(seen) + " (want one run at 09:00 New York)"
    return True, "; ".join(seen)


# ---------------------------------------------------------------------------------------------- 31 webhooks
TRIGGER = "failed-payments"
PAYLOAD = {"order": 42}
STRIPE_EVENT = {"id": "evt_31", "type": "invoice.payment_failed", "data": {"order": 42}}
RATE = 60   # deliveries an hour (backend/triggers.py HOURLY)


def secret_of(f):
    """The address secret from the create reply's url, the one time it is shown."""
    url = str((body(f, "trigger").get("trigger") or {}).get("url") or "")
    head, _, secret = url.rpartition("/")
    if not head.endswith(f"/hooks/{TRIGGER}") or not secret:
        raise Unmet(W3, f"the trigger create reply carries no address with its secret: {url!r}")
    return secret


def signing_secret(f):
    secret = (body(f, "sign").get("trigger") or {}).get("signing_secret")
    if not secret:
        raise Unmet(W3, "the signing reply shows no signing secret")
    return secret


def burst(org, f, n):
    return [hook_post(org, TRIGGER, secret_of(f), PAYLOAD).status for _ in range(n)]


def signed(org, f):
    raw = json.dumps(STRIPE_EVENT).encode()
    sig = stripe_sig(signing_secret(f), raw, int(org.now().timestamp()))
    return incoming(org, f"/api/v2/hooks/{TRIGGER}/{secret_of(f)}", STRIPE_EVENT, {"Stripe-Signature": sig})


def s31_steps(org, f):
    org.step(f, "trigger", org.api, "post", "/api/v2/triggers", {"name": "Failed payments", "id": TRIGGER}, wave=W3)
    org.step(f, "show", org.api, "get", f"/api/v2/triggers/{TRIGGER}", wave=W3)
    org.step(f, "auto", automation, org, "Failed payments · dana", DANA_REF,
             trigger={"type": "webhook", "id": TRIGGER}, steps=[note("h", "hooked")])
    org.step(f, "runs0", runs_of, org, f)
    org.step(f, "bad", hook_post, org, TRIGGER, "wrong-secret", PAYLOAD)
    org.step(f, "runs_bad", runs_of, org, f)
    org.step(f, "good", lambda: hook_post(org, TRIGGER, secret_of(f), PAYLOAD))
    org.step(f, "runs_good", runs_of, org, f)
    org.step(f, "burst", burst, org, f, RATE - 1)   # with the good one, the hour's 60
    org.step(f, "over", lambda: hook_post(org, TRIGGER, secret_of(f), PAYLOAD))
    org.step(f, "read", current, org, f)
    org.step(f, "sign", org.api, "post", f"/api/v2/triggers/{TRIGGER}/signing", {"preset": "stripe"}, wave=W3)
    org.step(f, "show2", org.api, "get", f"/api/v2/triggers/{TRIGGER}", wave=W3)
    org.step(f, "runs_s0", runs_of, org, f)
    org.step(f, "unsigned", lambda: hook_post(org, TRIGGER, secret_of(f), PAYLOAD))
    org.step(f, "runs_s1", runs_of, org, f)
    org.step(f, "hour", later, org, dt.timedelta(hours=1))   # the rate window clears
    org.step(f, "signed", signed, org, f)
    org.step(f, "runs_s2", runs_of, org, f)


def secret_once(f):
    secret = secret_of(f)
    shown = str((body(f, "show").get("trigger") or {}).get("url") or "")
    return secret not in shown, f"create shows the secret; a read shows {shown!r}"


def bad_refused(f):
    ok, text = replied(f, "bad", 401, 403, 404, needs=("auto",))
    if not ok:
        return ok, text
    fired = fresh(f, "runs0", "runs_bad")
    return not fired, f"{text}; {len(fired)} run(s) started"


def good_runs(f):
    ok, text = replied(f, "good", 200, 201, 202, needs=("auto",))
    if not ok:
        return ok, text
    fired = fresh(f, "runs0", "runs_good")
    return len(fired) == 1, f"{text}; {len(fired)} run(s), want 1"


def rate_holds(f):
    taken = [got(f, "good").status] + got(f, "burst")
    over = got(f, "over")
    ok = len(taken) == RATE and all(code == 202 for code in taken) and over.status == 429
    return ok, f"{sum(code == 202 for code in taken)} of {len(taken)} taken; 61st {over.status} {over.excerpt()}"


def rate_runs(f):
    setup(f, "auto", "read")
    got(f, "over")   # the 61st was sent before the read
    counts = today_counts(unwrap(body(f, "read")))
    return (counts or {}).get("runs") == RATE, f"counts.today {counts}, want runs {RATE}"


def stripe_applied(f):
    setup(f, "sign", "show2")
    made = body(f, "sign").get("trigger") or {}
    shown = body(f, "show2").get("trigger") or {}
    ok = made.get("preset") == "stripe" and bool(made.get("signing_secret")) and shown.get("preset") == "stripe" \
        and "signing_secret" not in shown
    return ok, f"sign reply preset {made.get('preset')}, secret shown {bool(made.get('signing_secret'))}; " \
               f"read preset {shown.get('preset')}, secret on read {'signing_secret' in shown}"


def unsigned_refused(f):
    ok, text = replied(f, "unsigned", 401, needs=("auto", "sign"))
    if not ok:
        return ok, text
    fired = fresh(f, "runs_s0", "runs_s1")
    return not fired, f"{text}; {len(fired)} run(s) started"


def signed_runs(f):
    ok, text = replied(f, "signed", 200, 201, 202, needs=("auto", "sign"))
    if not ok:
        return ok, text
    fired = fresh(f, "runs_s1", "runs_s2")
    return len(fired) == 1, f"{text}; {len(fired)} run(s), want 1"


SCENARIOS = [
    Scenario(22, "engine-find", "Schedule + Find over mail: items since the last run, once per item, empty run logged",
             s22_steps, [
                 Check("Find pulls the three invoices published since the last run, not the lunch mail", W3,
                       found_three),
                 Check("the per-item step runs once for each item", W3, once_per_item),
                 Check("with nothing new, the second run is logged empty", W3, empty_second),
                 Check("a mail after the empty run is the only item the next run finds", W3, since_last),
             ]),
    Scenario(23, "engine-bot-turn", "Bot turn step: one turn on the chosen bot with its prompt", s23_steps, [
        Check("the bot_turn step's output carries the turn {bot, task, session}", W3, turn_shape),
        Check("the turn is on builder-claude, the chosen bot", W3, turn_bot),
        Check("the turn's task carries the step's prompt", W3, turn_prompt),
        Check("the run's turn is claimed on builder-claude", W3, turn_claimed),
        Check("a second claim for the same run gets nothing", W3, second_held),
        Check("the turn gets builder-claude's tools: its calls act as builder-claude", W3, turn_tools),
    ]),
    Scenario(24, "engine-step-off", "Steps switched off: skipped and logged; their model is never called", s24_steps, [
        Check("the switched-off Decide is logged skipped, with no outputs", W3, off_logged),
        Check("the switched-off LLM step is logged skipped and starts no turn", W3, llm_off),
        Check("the judge is never called", W3, judge_untouched),
        Check("the enabled Filter and note still run", W3, enabled_ran),
    ]),
    Scenario(25, "engine-paths-order", "Paths run in order: the first match wins; otherwise when none match", s25_steps, [
        Check("two paths match: the paths output is {path: urgent}", W3,
              lambda f: path_taken(f, "both", "runs0", "runs1", "urgent")),
        Check("only the first matching path's steps run", W3,
              lambda f: ran_in(f, "both", "runs0", "runs1", ["u"], ["p", "o"])),
        Check("no path matches: the paths output is {path: otherwise}", W3,
              lambda f: path_taken(f, "none", "runs1", "runs2", "otherwise")),
        Check("no path matches: only the otherwise steps run", W3,
              lambda f: ran_in(f, "none", "runs1", "runs2", ["o"], ["u", "p"])),
    ]),
    Scenario(26, "engine-path-limits", "11th path or 4th nesting level refused on save", s26_steps, [
        Check("ten paths (the limit) are saved", W3, lambda f: replied(f, "ten", 200, 201)),
        Check("an 11th path is refused on save (422)", W3, lambda f: replied(f, "eleven", 422, needs=("ten",))),
        Check("three nesting levels (the limit) are saved", W3, lambda f: replied(f, "three", 200, 201)),
        Check("a fourth nesting level is refused on save (422)", W3, lambda f: replied(f, "deep", 422, needs=("three",))),
        Check("the refused flows are not listed; the accepted ones are", W3, not_listed),
    ]),
    Scenario(27, "engine-run-log", "Run log: step results and costs; dropped triggers counted only; pruned after 30 days",
             s27_steps, [
                 Check("a matching message makes one run; it and each step carry a result and a cost", W3,
                       step_rows_carry_cost),
                 Check("a message the trigger's filters drop writes no run row", W3, dropped_no_row),
                 Check("today's counts go up: runs +1 and dropped +1", W3, dropped_counted),
                 Check("on day 29 the run still has its step detail", W3, kept_29),
                 Check("after 31 days the step detail is pruned and the run row stays", W3, pruned_31),
                 Check("the day's counts are kept after the prune", W3, counts_kept),
             ]),
    Scenario(28, "engine-versions", "Each save makes a version; restore brings back the old flow", s28_steps, [
        Check("two saves make versions 1 and 2, with actor and time", W3, versions_recorded),
        Check("restore brings the first flow back as a new version", W3, restored),
    ]),
    Scenario(29, "engine-builtin", "Built-ins: made by the hub, paused by the top only with a warning, never deleted",
             s29_steps, [
                 Check("the hub lists its built-in automations with builtin true", W3, builtins_listed),
                 Check("POST with builtin true is refused and nothing is listed", W3, post_refused),
                 Check("the Engineering Lead cannot pause a built-in (403)", W3, lead_refused),
                 Check("Dana pauses it and the reply carries a warning", W3, dana_pause_warns),
                 Check("DELETE of a built-in answers 409 and it stays listed", W3, delete_refused),
             ]),
    Scenario(30, "engine-dst", "next_run in UTC; a New York schedule at 09:00 local across the DST change", s30_steps, [
        Check("GET carries next_run as UTC ISO: 13:00Z today (09:00 New York, EDT)", W3,
              lambda f: next_run_is(f, "read0", dt.datetime(2026, 10, 20, 13, tzinfo=UTC))),
        Check("before the change, next_run is Oct 31 13:00Z (09:00 EDT)", W3,
              lambda f: next_run_is(f, "read1", dt.datetime(2026, 10, 31, 13, tzinfo=UTC))),
        Check("on the change day, next_run is Nov 1 14:00Z (09:00 EST)", W3,
              lambda f: next_run_is(f, "read2", dt.datetime(2026, 11, 1, 14, tzinfo=UTC))),
        Check("after the change, next_run is Nov 2 14:00Z (09:00 EST)", W3,
              lambda f: next_run_is(f, "read3", dt.datetime(2026, 11, 2, 14, tzinfo=UTC))),
        Check("it fires once each day at 09:00 New York, Oct 31 to Nov 2", W3, fired_each_day),
    ]),
    Scenario(31, "engine-webhook", "Webhook trigger: secret shown once, a bad secret refused, Stripe signing, 60 an hour",
             s31_steps, [
                 Check("the trigger's secret is shown once: in the create reply, not on a read", W3, secret_once),
                 Check("a bad secret is refused and starts no run", W3, bad_refused),
                 Check("the good secret starts one run", W3, good_runs),
                 Check("60 deliveries an hour are taken; the 61st is refused with 429", W3, rate_holds),
                 Check("only the 60 taken deliveries ran: today's runs are 60", W3, rate_runs),
                 Check("the stripe preset is applied; its signing secret is shown once", W3, stripe_applied),
                 Check("with stripe on, an unsigned delivery is refused (401) and starts no run", W3, unsigned_refused),
                 Check("a delivery signed the Stripe way starts one run", W3, signed_runs),
             ]),
]
