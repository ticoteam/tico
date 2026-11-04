"""The automation executor: a flow, the trigger's payload and injected services in; a run record out.

Pure. Everything with an effect (the judge, actions, turns, finding items, the clock and the spend so far) is a
method of `services`, so a test hands in fakes and the hub hands in the real thing (backend/automations.py).

A run is a list of threads. A thread is where one item is in the flow: the outputs it has gathered and a cursor,
the forks it took (`trail`) and the next step in that list. Paths never rejoin and a `paths` step ends its list, so
that cursor is the whole position. A run starts with one thread; a `find` replaces it with one per item.

A thread stops when its list ends, a filter rule ends it (`next` end, or `to` a destination), a fork has no
path or a step fails. A turn over the spend
cap is recorded as `spend_cap` and the thread goes on without it; a decision with Decide off is `skipped` likewise. It waits for two things: the answer of an `llm` turn that has steps after it, or of a `bot_turn` a later step reads
(`{{brief.text}}`, or a `post_update` with no text of its own; `answer` fills it, a bot turn's under its step id) and,
with `batch` times, the next batch time once the leading filters ran (`advance` picks it up when the time comes).

Every effect (an action, a turn) gets `ctx["key"]`, its place in its step's record: `<n>.<m>` for a filter's m-th action
in a step shaped as before (`_legacy`), and `<n>.r<r>.<i>` or `<n>.r<r>.d<i>` (rule r's i-th action, or that rule's
destination's) in any other. With the run's id it is the effect's idempotency key, the same when a resumed run repeats
the step. `checkpoint(state)` follows every step, so the hub saves the run before the next step starts.

A choose step (an `llm` step with `choose`) reads its answer when it comes in (`answer`, `read_choice`), and its reply
and the chosen destination's chain run as the thread's next step (`_resume`): the reply is keyed `<record>.0`, the chain
`<record>.1` on, so a resumed run repeats none of them.

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
    finish (`ok` false) fails the thread. A choose step's turn is read by `_answered` instead. Returns whether a waiting
    thread had that ref."""
    for thread in state["threads"]:
        wait = thread.get("wait") or {}
        if thread["state"] == WAITING and wait.get("kind") == "turn" and wait.get("ref") == ref:
            if wait.get("choose"):
                _answered(state, thread, wait, text, cost, ok)
                return True
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
    """Run every ready thread (and every batch wait whose time has come) until it ends or waits, or until the run
    guard puts its automation on hold (`services.on_hold`, asked before each step): then the rest stays ready."""
    now = services.now()
    for thread in state["threads"]:
        wait = thread.get("wait") or {}
        if thread["state"] == WAITING and wait.get("kind") == "batch" and wait["until"] <= now:
            thread.update(state=READY, wait=None)
    threads, n = state["threads"], 0
    while n < len(threads):   # a find puts its items' threads in the place of its own
        if threads[n]["state"] == READY:
            if services.on_hold():
                break
            _step(flow, state, threads[n], services)
            services.checkpoint(state)
        else:
            n += 1
    state["status"] = _status(state)
    return state


def _status(state):
    states = {t["state"] for t in state["threads"]}
    if READY in states:   # stopped by a hold with steps still to run
        return "running"
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
    """Run the thread's next step, or end it at the end of its list. A thread whose choose step was answered runs that
    step's effects first (`_resume`); the step is the one before its cursor, and nothing after it runs."""
    steps = steps_at(flow, thread["trail"])
    if thread.get("choice") is not None:
        step, run = steps[thread["next"] - 1], _resume
    else:
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
        run = RUN[step["type"]]
    try:
        run(flow, state, thread, step, services)
    except Exception as exc:  # noqa: BLE001 - a service's failure is the step's, recorded on the run
        _record(state, thread, step, "failed", {"error": (str(exc) or type(exc).__name__)[:300]})
        thread["state"] = FAILED


def _chain(services, chain, values, step, key):
    """Run one chain of actions in order. Each entry is (action, source, effect, producer): the action's effect key is
    `<key>.<effect>`, and `producer` is the identity a task it opens is deduplicated under (`_filter` gives both). A
    refused action ends the chain (what follows it may depend on it); the list keeps it as the last entry."""
    done = []
    for action, source, effect, producer in chain:
        result = _act(services, action["do"], action.get("args") or {}, values, step, f"{key}.{effect}", producer)
        done.append({**result, **source})
        if not result.get("ok"):
            break
    return done


def _legacy(flow, step):
    """A filter step shaped as before destinations and `next` existed: no destination in the flow, and no rule of the
    step with `to` or a `next` other than the default. Its effects keep their keys (`<key>.<m>`, m counting across the
    step), so a run in flight resumes on the same keys."""
    return not flow.get("destinations") and all(
        rule.get("to") is None and rule.get("next") in (None, "step") for rule in step.get("rules") or [])


def _filter(flow, state, thread, step, services):
    """The rules in order. A rule that holds runs its actions and, with `to`, its destination's chain. What it does
    after that is its `next` (F.rule_next): `end` ends the thread, `rule` moves on to the rules after it, `step` goes on
    to the next step. A refused action fails the thread and the step's record says which.

    Identities come from where an action is in the flow, never from what ran first, so a retry whose rules match
    differently cannot hand one rule's task to another. In a step shaped as before (`_legacy`) the step's own id is the
    identity of its first task, as it always was, and a later task of the run is `<step>@r<r>.<i>`, its place in the
    rule: the step's record index (`<key>`) moves when a step is inserted before it, so it is not used. In any other step
    rule r's i-th action is `r<r>.<i>`, and the i-th action of the destination that rule reaches is `r<r>.d<i>`: the
    effect key is `<key>.<slot>` and the producer `<step>@<slot>`."""
    values = values_of(state, thread)
    destinations = {d["id"]: d for d in flow.get("destinations") or []}
    legacy = _legacy(flow, step)
    key, actions, fired, failed, ended = len(state["steps"]), [], [], False, False
    tasked = False   # legacy: a create_task of this run has taken the step's own id
    for n, rule in enumerate(step.get("rules") or []):
        if not F.all_hold(rule.get("when") or [], values, rule.get("match", "all")):
            continue
        fired.append(n)
        chain = [(action, {"from": "rule", "rule": n}, f"r{n}.{i}")
                 for i, action in enumerate(rule.get("then") or [])]
        if rule.get("to") is not None:
            if rule["to"] not in destinations:   # check() refuses a flow that names none; a run never reaches this
                raise ValueError(f"no destination named {rule['to']}")
            dest = destinations[rule["to"]]
            chain += [(action, {"from": "destination", "destination": dest["id"]}, f"r{n}.d{i}")
                      for i, action in enumerate(dest.get("then") or [])]
        marked = []
        for action, source, slot in chain:
            if legacy:
                m = len(actions) + len(marked)
                again = action["do"] == "create_task" and tasked
                tasked = tasked or action["do"] == "create_task"
                marked.append((action, source, str(m), f"{step['id']}@{slot}" if again else step["id"]))
            else:
                marked.append((action, source, slot, f"{step['id']}@{slot}"))
        actions += _chain(services, marked, values, step, key)
        if any(not action.get("ok") for action in actions):
            failed = True
            break
        after = F.rule_next(rule)
        if after != "rule":
            ended = after == "end"
            break
    _record(state, thread, step, "failed" if failed else "ok",
            {"rule": fired[0] if fired else None, "rules": fired, "actions": actions})
    if failed:
        thread["state"] = FAILED
    elif ended:
        thread["state"] = DONE


# A notice's text reaches a bot as words to act on: what arrived (mail, a webhook) goes in as quoted data, as in a
# turn's prompt. A note on a task (not to a bot) is for the task's people and stays plain.
NOTICE_TEXT = {"wake_bot": ("text", "prompt"), "note": ("text", "note")}


def _act(services, do, args, values, step, key, producer=None):
    """One action. `producer` is what the action is deduplicated under (`_chain`); the step's own id by default."""
    to_bot = do == "wake_bot" or (do == "note" and args.get("bot") and not values.get("task"))
    quoted = NOTICE_TEXT[do] if to_bot else ()
    rendered = F.render_args({k: v for k, v in args.items() if k not in quoted}, values)
    rendered.update({k: F.render_prompt(args[k], values) for k in quoted if isinstance(args.get(k), str)})
    result = services.act(do, rendered, {"values": values, "step": step["id"], "key": key,
                                         "producer": producer or step["id"]})
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


def _start(flow, state, thread, step, services, who, prompt, wait, tail=""):
    """Starts the step's turn. The service checks the cap (`ctx["cap"]` against what was spent, `ctx["cost"]` of it this
    run's) after its effect key: a resumed run whose turn already started gets that turn back, never its own hold's
    refusal. `tail` is added after the prompt is rendered, so it is never templated. Returns the service's answer, or
    None when the cap stops it."""
    values = values_of(state, thread)
    cap = (flow.get("spend_cap") or {}).get("per_30m")
    # A bot_turn step's model, effort and harness are the model its task runs on (backend/task_run_models.py).
    run_model = ({k: step[k] for k in ("model", "effort", "harness") if step.get(k)}
                 if step.get("type") == "bot_turn" else {})
    return services.start_turn(who, F.render_prompt(prompt, values, cap=F.MAX_PROMPT) + tail, {
        "values": values, "step": step["id"], "key": str(len(state["steps"])), "cost": state["cost"],
        "cap": None if cap is None else float(cap), "label": _turn_label(thread), "run_model": run_model or None}, wait)


def _turn_outputs(started):
    return {"ref": started["ref"], **({"turn": started["turn"]} if started.get("turn") else {})}


def _turn(flow, state, thread, step, services, who, prompt, wait):
    started = _start(flow, state, thread, step, services, who, prompt, wait)
    if started is None:
        # The cap stops spending, not the run: later filters, decisions and actions still go.
        _record(state, thread, step, "spend_cap")
        return
    ref = started["ref"]
    if started.get("held"):
        # Screening held the webhook's text for a person (backend/screening.py): no turn, and the run goes on.
        _record(state, thread, step, "held", {"ref": ref})
        return
    outputs = _turn_outputs(started)
    if not wait:
        _record(state, thread, step, "ok", outputs)
        return
    record = _record(state, thread, step, "waiting", outputs)
    thread.update(state=WAITING, wait={"kind": "turn", "ref": ref, "out": step.get("out") or step["id"],
                                       "record": record})


def _llm(flow, state, thread, step, services):
    if step.get("choose") is not None:
        return _choose_turn(flow, state, thread, step, services)
    # Nothing after it reads the answer: the turn runs on its own and the run does not wait.
    rest = thread["next"] < len(steps_at(flow, thread["trail"]))
    _turn(flow, state, thread, step, services, step["as"], step["prompt"], rest)


REPLY = {"draft": "draft_reply", "reply": "send_reply"}   # what a choose step's body does (`_resume`)


def _choose_turn(flow, state, thread, step, services):
    """An llm step with `choose`: its turn's answer picks the destination (`_answered`). With no turn to wait for (the
    spend cap stops it, or a webhook's screening holds it) the default destination's chain runs at once, with no reply.
    The turn's effect key is the record's index, which the reply and the chain extend (`_resume`)."""
    choose, rec = step["choose"], len(state["steps"])
    started = _start(flow, state, thread, step, services, step["as"], step["prompt"], True, _choice_tail(flow, choose))
    if started is None or started.get("held"):
        # No turn ran: the record says why, and the default destination runs with no reply.
        _record(state, thread, step, "ok", {"skipped": "spend_cap" if started is None else "held",
                                            **({} if started is None else {"ref": started["ref"]})})
        thread["choice"] = {"record": rec, "to": choose["default"], "body": None}
        _resume(flow, state, thread, step, services)
        return
    record = _record(state, thread, step, "waiting", _turn_outputs(started))
    thread.update(state=WAITING, wait={"kind": "turn", "ref": started["ref"], "out": step.get("out") or step["id"],
                                       "record": record, "choose": choose})


def _choice_tail(flow, choose):
    """What a choose step's turn is asked after its prompt: one JSON answer, the destinations it may pick by id, and what
    its body does in the step's mode."""
    names = {d["id"]: " ".join(str(d.get("name") or d["id"]).split()) for d in flow.get("destinations") or []}
    what = ("saved as a draft for a person to review" if choose["mode"] == "draft"
            else "sent as the reply (a draft while the inbox's send is off)")
    listed = "\n".join(f"- {dest}: {names.get(dest, dest)}" for dest in choose["to"])
    return (f'\n\nAnswer with one JSON object and nothing else: {{"to": "<id>", "body": "<text>"}}.\n'
            f'"to" is one of these destination ids:\n{listed}\n'
            f'"body" is the reply to the message, {what}. Use "" for no reply.')


def read_choice(text, choose):
    """A choose step's answer: one JSON object with exactly `to` (one of choose.to) and `body`, text of at most
    F.MAX_TEXT characters (a blank body means no reply). Returns ({"to", "body"}, None), or (None, why) for any other."""
    try:
        answer = json.loads(text) if isinstance(text, str) else None
    except (ValueError, RecursionError):
        answer = None
    if not isinstance(answer, dict) or set(answer) != {"to", "body"}:
        return None, "the answer is not one JSON object with to and body"
    if not isinstance(answer["to"], str) or answer["to"] not in choose["to"]:
        return None, "to is not one of the destinations the step may choose"
    body = answer["body"]
    if not isinstance(body, str) or len(body) > F.MAX_TEXT:
        return None, f"body is not text of at most {F.MAX_TEXT} characters"
    return {"to": answer["to"], "body": body if body.strip() else None}, None


def _answered(state, thread, wait, text, cost, ok):
    """A choose step's turn finished. Its answer picks the destination and the body (`read_choice`); an answer that does
    not fit, or a turn that did not finish, picks the default destination with no body, and the record says why. The
    thread is ready for `_resume`, which runs the reply and the chain."""
    choose, record = wait["choose"], state["steps"][wait["record"]]
    choice, error = read_choice(text, choose) if ok else (None, str(text or "the turn did not finish")[:300])
    if choice is None:
        choice = {"to": choose["default"], "body": None}
    outputs = {**record["outputs"], "to": choice["to"], "actions": []}
    if ok:
        outputs["text"] = text
        thread["outputs"][wait["out"]] = {k: v for k, v in outputs.items() if k != "actions"}
    if error:
        outputs["error"] = error
    record.update(status="ok", outputs=outputs, cost=round(record["cost"] + cost, 6))
    state["cost"] = round(state["cost"] + cost, 6)
    thread.update(state=READY, wait=None, choice={"record": wait["record"], **choice})


def _resume(flow, state, thread, step, services):
    """A choose step's effects, once its answer was read (`_answered`): the reply when the body is not blank, then the
    chosen destination's chain. The reply is keyed `<record>.0` and the chain's effects follow it (`<record>.1` on), so a
    run resumed after a crash repeats none of them (`_once`). A refused reply ends the step without its chain."""
    choice = thread.pop("choice")
    rec = choice["record"]
    record, values, actions = state["steps"][rec], values_of(state, thread), []
    if choice["body"] is not None:
        do = REPLY[step["choose"]["mode"]]
        # The body is the model's reply as written: it is not rendered, so `{{...}}` in it goes out as text.
        ctx = {"values": values, "step": step["id"], "key": f"{rec}.0", "producer": f"{step['id']}@{rec}.0"}
        actions.append({"do": do, "from": "reply", **services.act(do, {"body": choice["body"]}, ctx)})
    if all(action.get("ok") for action in actions):
        dest = {d["id"]: d for d in flow.get("destinations") or []}[choice["to"]]
        chain = [(action, {"from": "destination", "destination": dest["id"]}, str(1 + i), f"{step['id']}@{rec}.{1 + i}")
                 for i, action in enumerate(dest.get("then") or [])]
        actions += _chain(services, chain, values, step, str(rec))
    failed = not all(action.get("ok") for action in actions)
    record["outputs"] = {**record["outputs"], "to": choice["to"], "actions": actions}
    if failed:
        record["status"] = "failed"
    thread["state"] = FAILED if failed else DONE


def _bot_turn(flow, state, thread, step, services):
    # A bot's turn is its own work and the run goes on, unless a later step reads what it wrote (a briefing to post).
    bot = step["bot"]
    later = steps_at(flow, thread["trail"])[thread["next"]:]
    destinations = {d["id"]: d for d in flow.get("destinations") or []}
    _turn(flow, state, thread, step, services, bot if bot.startswith("bot:") else "bot:" + bot, step["prompt"],
          _reads(later, step["id"], destinations))


def _reads(steps, name, destinations=None):
    """Whether one of these steps (or a step on a path under them) reads step `name`'s answer: names `name.<field>`,
    or posts an update with no text of its own, which posts what the turn before wrote. A filter rule's `to` runs its
    destination's actions too, so they count as the rule's own; `destinations` is the flow's, by id."""
    used = re.compile(r"(?<![A-Za-z0-9_.-])" + re.escape(name) + r"\.")
    destinations = destinations or {}
    for step in steps:
        if step.get("type") == "paths":
            nested = [p.get("steps") or [] for p in step.get("paths") or []]
            if any(_reads(s, name, destinations)
                   for s in nested + [(step.get("otherwise") or {}).get("steps") or []]):
                return True
            step = {k: v for k, v in step.items() if k not in ("paths", "otherwise")} | {
                "when": [p.get("when") for p in step.get("paths") or []]}
        rules = step.get("rules") or []
        reached = [action for rule in rules if rule.get("to") in destinations
                   for action in destinations[rule["to"]].get("then") or []]
        for action in [step] + [a for rule in rules for a in rule.get("then") or []] + reached:
            args = action.get("args") or {}
            if action.get("do") == "post_update" and not (args.get("text") or args.get("body")):
                return True
        if used.search(json.dumps([step, *reached], default=str)):
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
