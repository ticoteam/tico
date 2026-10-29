"""The automation executor: a flow, the trigger's payload and injected services in; a run record out.

Pure. Everything with an effect (the judge, actions, turns, finding items, the clock and the spend so far) is a
method of `services`, so a test hands in fakes and the hub hands in the real thing (backend/automations.py).

A run is a list of threads. A thread is where one item is in the flow: the outputs it has gathered and a cursor,
the forks it took (`trail`) and the next step in that list. Paths never rejoin and a `paths` step ends its list, so
that cursor is the whole position. A run starts with one thread; a `find` replaces it with one per item.

A thread stops when its list ends, a filter rule says stop, a fork has no path or a step fails. A turn over the spend
cap is recorded as `spend_cap` and the thread goes on without it; a decision with Decide off is `skipped` likewise. It waits for two things: the answer of an `llm` turn that has steps after it, or of a `bot_turn` a later step reads
(`{{brief.text}}`, or a `post_update` with no text of its own; `answer` fills it, a bot turn's under its step id) and,
with `batch` times, the next batch time once the leading filters ran (`advance` picks it up when the time comes).

Every effect (an action, a turn) gets `ctx["key"]`, the index its step record will have (`<n>.<m>` for a filter
rule's m-th action): with the run's id it is the effect's idempotency key, the same when a resumed run repeats the
step. `checkpoint(state)` follows every step, so the hub saves the run before the next step starts.

services:
  judge(state, questions, label, level) -> {"answers": {...}, "cost_usd": n}, or None while Decide is off
  question_set(ref) -> questions, and state_fields(ref) -> the set's `state` fields
  act(do, args, ctx) -> {"ok": bool, "detail": str, ...}
  start_turn(who, prompt, ctx, wait) -> {"ref": str, "turn": {bot, task, session}}, or None when the spend cap stops it
    (checked after the effect key, so a turn already started is returned, never refused); ctx["label"] is the find
    item's part of the turn's title (`_turn_label`), None off a find
  find(source, query, limit) -> [item, ...]
  next_batch() -> the next batch time (ISO), and now() -> now (ISO)
  checkpoint(state) -> None
"""
import json
import re

from backend import automation_flow as F

READY, WAITING, DONE, FAILED = "ready", "waiting", "done", "failed"


def start(payload, *, manual=False):
    """A new run's state: one thread at the first step."""
    return {"status": "running", "cost": 0.0, "steps": [], "payload": payload or {}, "manual": bool(manual),
            "empty": False, "threads": [_thread(None, None, {}, [], 0)]}


def _thread(item, value, outputs, trail, at, batched=False):
    return {"item": item, "value": value, "outputs": dict(outputs), "trail": list(trail), "next": at,
            "state": READY, "wait": None, "batched": batched}


def execute(flow, payload, services, *, manual=False):
    return advance(flow, start(payload, manual=manual), services)


def steps_at(flow, trail):
    """The step list a cursor's trail leads to."""
    steps = flow.get("steps") or []
    for index, branch in trail:
        fork = steps[index]
        steps = ((fork.get("otherwise") or {}).get("steps") if branch == "otherwise"
                 else fork["paths"][branch].get("steps")) or []
    return steps


def values_of(state, thread):
    """What a condition, a prompt and an action's args read: the payload (also as `trigger`), the thread's outputs,
    and the item a `find` gave it."""
    payload = state["payload"]
    out = {**payload, "trigger": payload, **thread["outputs"]}
    if thread["item"] is not None:
        out["item"] = thread["value"]
    return out


def answer(state, ref, text, cost=0.0, ok=True):
    """An `llm` turn finished: its thread gets the text as the step's output and is ready again; a turn that did not
    finish (`ok` false) fails the thread. Returns whether a waiting thread had that ref."""
    for thread in state["threads"]:
        wait = thread.get("wait") or {}
        if thread["state"] == WAITING and wait.get("kind") == "turn" and wait.get("ref") == ref:
            record = state["steps"][wait["record"]]
            outputs = ({**record["outputs"], "text": text} if ok
                       else {**record["outputs"], "error": str(text or "the turn did not finish")[:300]})
            record.update(status="ok" if ok else "failed", outputs=outputs, cost=round(record["cost"] + cost, 6))
            state["cost"] = round(state["cost"] + cost, 6)
            if ok:
                thread["outputs"][wait["out"]] = outputs
            thread.update(state=READY if ok else FAILED, wait=None)
            if not ok:
                state["status"] = _status(state)
            return True
    return False


def charge(state, ref, cost):
    """What a turn the run started (`ref`) cost, once it ends, on its step and the run. Returns whether `ref` is one."""
    for record in state["steps"]:
        if (record.get("outputs") or {}).get("ref") == ref and record["type"] in ("llm", "bot_turn"):
            record["cost"] = round(record["cost"] + cost, 6)
            state["cost"] = round(state["cost"] + cost, 6)
            return True
    return False


def advance(flow, state, services):
    """Run every ready thread (and every batch wait whose time has come) until it ends or waits."""
    now = services.now()
    for thread in state["threads"]:
        wait = thread.get("wait") or {}
        if thread["state"] == WAITING and wait.get("kind") == "batch" and wait["until"] <= now:
            thread.update(state=READY, wait=None)
    threads, n = state["threads"], 0
    while n < len(threads):   # a find puts its items' threads in the place of its own
        if threads[n]["state"] == READY:
            _step(flow, state, threads[n], services)
            services.checkpoint(state)
        else:
            n += 1
    state["status"] = _status(state)
    return state


def _status(state):
    states = {t["state"] for t in state["threads"]}
    if WAITING in states:
        return "waiting"
    if FAILED in states:
        return "failed"
    if state["empty"] and not state["threads"]:
        return "empty"
    if state["steps"] and all(r["status"] == "skipped" for r in state["steps"]):
        return "skipped"
    return "ok"


def _record(state, thread, step, status, outputs=None, cost=0.0):
    record = {"id": step["id"], "type": step["type"], "status": status, "outputs": outputs or {},
              "cost": round(cost, 6)}
    if thread["item"] is not None:
        record["item"] = thread["item"]
    state["steps"].append(record)
    state["cost"] = round(state["cost"] + cost, 6)
    return len(state["steps"]) - 1


def _step(flow, state, thread, services):
    """Run the thread's next step, or end it at the end of its list."""
    steps = steps_at(flow, thread["trail"])
    if thread["next"] >= len(steps):
        thread["state"] = DONE
        return
    step = steps[thread["next"]]
    if (flow.get("batch") and not state["manual"] and not thread["batched"] and not thread["trail"]
            and step["type"] != "filter"):
        # Filter on receipt, the rest at the next batch time.
        thread.update(state=WAITING, wait={"kind": "batch", "until": services.next_batch()}, batched=True)
        return
    thread["next"] += 1
    if step.get("enabled", True) is False:
        _record(state, thread, step, "skipped")
        return
    try:
        RUN[step["type"]](flow, state, thread, step, services)
    except Exception as exc:  # noqa: BLE001 - a service's failure is the step's, recorded on the run
        _record(state, thread, step, "failed", {"error": (str(exc) or type(exc).__name__)[:300]})
        thread["state"] = FAILED


def _filter(flow, state, thread, step, services):
    values = values_of(state, thread)
    for n, rule in enumerate(step.get("rules") or []):
        if not F.all_hold(rule.get("when") or [], values, rule.get("match", "all")):
            continue
        done, key = [], len(state["steps"])
        for m, action in enumerate(rule.get("then") or []):
            done.append(_act(services, action["do"], action.get("args") or {}, values, step, f"{key}.{m}"))
            if not done[-1].get("ok"):
                break   # a refused action stops the rule: what follows it may depend on it
        failed = [d for d in done if not d.get("ok")]
        _record(state, thread, step, "failed" if failed else "ok", {"rule": n, "actions": done})
        if failed:
            thread["state"] = FAILED
        elif rule.get("stop"):
            thread["state"] = DONE
        return
    _record(state, thread, step, "ok", {"rule": None, "actions": []})


# A notice's text reaches a bot as words to act on: what arrived (mail, a webhook) goes in as quoted data, as in a
# turn's prompt. A note on a task (not to a bot) is for the task's people and stays plain.
NOTICE_TEXT = {"wake_bot": ("text", "prompt"), "note": ("text", "note")}


def _act(services, do, args, values, step, key):
    to_bot = do == "wake_bot" or (do == "note" and args.get("bot") and not values.get("task"))
    quoted = NOTICE_TEXT[do] if to_bot else ()
    rendered = F.render_args({k: v for k, v in args.items() if k not in quoted}, values)
    rendered.update({k: F.render_prompt(args[k], values) for k in quoted if isinstance(args.get(k), str)})
    result = services.act(do, rendered, {"values": values, "step": step["id"], "key": key})
    return {"do": do, **result}


def _decide(flow, state, thread, step, services):
    questions = step.get("questions") or services.question_set(step["set"])
    values = values_of(state, thread)
    level = step.get("level", "low")
    judged = F.judge_state(values, services.state_fields(step["set"]) if step.get("set") else None)
    result = services.judge(judged, questions, f"automation.{step['id']}", level)
    if result is None:
        # Decide is off (no decision model, or the month's cap): carry on as callers elsewhere do, with no answers,
        # so a path on them falls to Otherwise.
        thread["outputs"][step["out"]] = {}
        _record(state, thread, step, "skipped", {"off": True, "level": level})
        return
    answers = result.get("answers") or {}
    thread["outputs"][step["out"]] = answers
    _record(state, thread, step, "ok", {**answers, "level": level}, float(result.get("cost_usd") or 0))


def _turn_label(thread):
    """What a find item's turn adds to its automation's name in its title: the item's own title, else its place in
    the find ("item 2"). None when the thread is not a find item's."""
    if thread["item"] is None:
        return None
    value = thread["value"] if isinstance(thread["value"], dict) else {}
    own = " ".join(str(value.get("title") or value.get("subject") or "").split())
    return own or f"item {thread['item'] + 1}"


def _turn(flow, state, thread, step, services, who, prompt, wait):
    # The service checks the cap (`ctx["cap"]` against what was spent, `ctx["cost"]` of it this run's) after its
    # effect key: a resumed run whose turn already started gets that turn back, never its own hold's refusal.
    values = values_of(state, thread)
    cap = (flow.get("spend_cap") or {}).get("per_30m")
    # A bot_turn step's model, effort and harness are the model its task runs on (backend/task_run_models.py).
    run_model = ({k: step[k] for k in ("model", "effort", "harness") if step.get(k)}
                 if step.get("type") == "bot_turn" else {})
    started = services.start_turn(who, F.render_prompt(prompt, values, cap=F.MAX_PROMPT), {
        "values": values, "step": step["id"], "key": str(len(state["steps"])), "cost": state["cost"],
        "cap": None if cap is None else float(cap), "label": _turn_label(thread), "run_model": run_model or None}, wait)
    if started is None:
        # The cap stops spending, not the run: later filters, decisions and actions still go.
        _record(state, thread, step, "spend_cap")
        return
    ref = started["ref"]
    if started.get("held"):
        # Screening held the webhook's text for a person (backend/screening.py): no turn, and the run goes on.
        _record(state, thread, step, "held", {"ref": ref})
        return
    outputs = {"ref": ref, **({"turn": started["turn"]} if started.get("turn") else {})}
    if not wait:
        _record(state, thread, step, "ok", outputs)
        return
    record = _record(state, thread, step, "waiting", outputs)
    thread.update(state=WAITING, wait={"kind": "turn", "ref": ref, "out": step.get("out") or step["id"],
                                       "record": record})


def _llm(flow, state, thread, step, services):
    # Nothing after it reads the answer: the turn runs on its own and the run does not wait.
    rest = thread["next"] < len(steps_at(flow, thread["trail"]))
    _turn(flow, state, thread, step, services, step["as"], step["prompt"], rest)


def _bot_turn(flow, state, thread, step, services):
    # A bot's turn is its own work and the run goes on, unless a later step reads what it wrote (a briefing to post).
    bot = step["bot"]
    later = steps_at(flow, thread["trail"])[thread["next"]:]
    _turn(flow, state, thread, step, services, bot if bot.startswith("bot:") else "bot:" + bot, step["prompt"],
          _reads(later, step["id"]))


def _reads(steps, name):
    """Whether one of these steps (or a step on a path under them) reads step `name`'s answer: names `name.<field>`,
    or posts an update with no text of its own, which posts what the turn before wrote."""
    used = re.compile(r"(?<![A-Za-z0-9_.-])" + re.escape(name) + r"\.")
    for step in steps:
        args = step.get("args") or {}
        if step.get("do") == "post_update" and not (args.get("text") or args.get("body")):
            return True
        if step.get("type") == "paths":
            nested = [p.get("steps") or [] for p in step.get("paths") or []]
            if any(_reads(s, name) for s in nested + [(step.get("otherwise") or {}).get("steps") or []]):
                return True
            step = {k: v for k, v in step.items() if k not in ("paths", "otherwise")} | {
                "when": [p.get("when") for p in step.get("paths") or []]}
        if used.search(json.dumps(step, default=str)):
            return True
    return False


def _find(flow, state, thread, step, services):
    values = values_of(state, thread)
    idle = {"idle_hours": step["idle_hours"]} if step.get("idle_hours") else {}
    items = list(services.find(step["source"], F.render(step.get("query") or "", values), step.get("limit", 20),
                               **idle))
    _record(state, thread, step, "ok", {"count": len(items), "items": [_brief(item) for item in items]})
    state["empty"] = not items
    place = next(n for n, t in enumerate(state["threads"]) if t is thread)
    state["threads"][place:place + 1] = [
        _thread(n, item, thread["outputs"], thread["trail"], thread["next"], thread["batched"])
        for n, item in enumerate(items)]


ITEM_TEXT = 500   # what the run log keeps of each found item's text; the item's own steps read all of it


def _brief(item):
    """A found item as its step's outputs list it: long text cut, so the run row stays small."""
    if not isinstance(item, dict):
        return item
    return {k: (v[:ITEM_TEXT] if isinstance(v, str) else v) for k, v in item.items()}


def _paths(flow, state, thread, step, services):
    values = values_of(state, thread)
    index = thread["next"] - 1
    for n, path in enumerate(step.get("paths") or []):
        if F.all_hold(path.get("when") or [], values, path.get("match", "all")):
            _record(state, thread, step, "ok", {"path": path["name"]})
            thread["trail"].append([index, n])
            thread["next"] = 0
            return
    if step.get("otherwise") is not None:
        _record(state, thread, step, "ok", {"path": "otherwise"})
        thread["trail"].append([index, "otherwise"])
        thread["next"] = 0
        return
    _record(state, thread, step, "ok", {"path": None})
    thread["state"] = DONE


def _action(flow, state, thread, step, services):
    values = values_of(state, thread)
    result = _act(services, step["do"], step.get("args") or {}, values, step, str(len(state["steps"])))
    thread["outputs"][step["id"]] = result
    _record(state, thread, step, "ok" if result.get("ok") else "failed", result)
    if not result.get("ok"):
        thread["state"] = FAILED


RUN = {"filter": _filter, "decide": _decide, "llm": _llm, "bot_turn": _bot_turn, "find": _find, "paths": _paths,
       "action": _action}


def view(state):
    """The run record's public part (CONTRACT §2): status, cost and the step records."""
    return {"status": state["status"], "cost": state["cost"], "steps": state["steps"]}
