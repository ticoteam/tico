"""What a bot's tasks run on: `task_model` in its config, beside its own model, harness and effort.

The record is {harness, model, reasoning_effort, runtime, compact_at}, and each part is optional on its own. A model
is the task model: its tasks run on that harness, model and effort. An empty model means the bot's own model, so a
record can carry only a task effort, only a task context length (`compact_at`), or both, and the bot keeps its model
(one record, not a second key, because a task's effort and context length are read on the same turn as its model and
are cleared the same way). Effort is checked against the model that runs: the task model's own at save, and on a
fallback or an empty model, the model that actually runs (`_fits`). Chat with the bot, and a bot on `session: bot`,
never changes. A turn that carries a task in its own session (clients/sessions.py, a `task:` key) gets them.
A task's own model (backend/task_run_models.py) comes before the bot's task model, and a check subtask's check model
(backend/check_models.py) before both; the task context length applies to all of them. A task model that cannot
run here (retired, its provider off, not installed or not signed in on its computer) never holds or fails the turn:
the next one in that order runs, and the bot's own model runs last, with the task context length and any task effort
that model takes, and the run and the task say so. Each turn's choice is kept on its attempt (`model`) for its
run history (usage_records.ran_on). A config with no model, effort or context length is the same as the bot."""
import json

from clients import sessions

from . import hubdb as H
from . import providers
from . import task_run_models
from .harnesses import runtime_of
from .readiness import can_run
from .store import Problem, readiness_document

KEY = "task_model"


def stored(config):
    """The task settings a bot's config sets, {harness, model, reasoning_effort, runtime, compact_at}, with an empty
    model where the bot's own model runs its tasks, or None when no part is set. `harness` and `runtime` are empty
    without a model."""
    chosen = (config or {}).get(KEY)
    if not isinstance(chosen, dict):
        return None
    model = str(chosen.get("model") or "").strip()
    effort = str(chosen.get("reasoning_effort") or "")
    compact_at = _context(chosen.get("compact_at"))
    if not (model or effort or compact_at):
        return None
    if not model:
        return {"harness": "", "model": "", "reasoning_effort": effort, "runtime": "", "compact_at": compact_at}
    row = providers.MODEL_BY_ID.get(model) or {}
    harness = str(chosen.get("harness") or "").strip()
    return {"harness": harness, "model": model, "reasoning_effort": effort,
            "runtime": str(chosen.get("runtime") or "") or runtime_of(harness) or str(row.get("runtime") or ""),
            "compact_at": compact_at}


def of(config):
    """The task model a bot's config sets, {harness, model, reasoning_effort, runtime, compact_at}, or None when its
    tasks run on the bot's own model (with or without a task effort or context length)."""
    record = stored(config)
    return record if record and record["model"] else None


def _context(value):
    """A task context length in tokens, or None when unset (the same as the bot's Compact at)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def refuse_retired(choice):
    """A retired model stays in the catalog for bots already on it; nothing new may pick it."""
    if choice.get("deprecated"):
        raise Problem("model", f"{choice['label']} is retired; choose a current model", 422)


def harness_of(choice, requested=None, current=None):
    """The harness a model runs on: the one named (it must run the model), else `current` when it still does, else the
    model's first."""
    supported = tuple(choice.get("harnesses") or (choice.get("runtime"),))
    value = str(requested or "").strip().lower()
    if requested is not None and value not in supported:
        raise Problem("harness", "Choose a harness that can run that model", 422)
    current = str(current or "").strip().lower()
    return value or (current if current in supported else (supported[0] if supported else ""))


def effort_of(choice, requested=None, current=None):
    """The reasoning effort a model runs at: the one named (it must be one the model takes), else `current` when it
    still is one, else the model's default."""
    supported = tuple(choice.get("efforts") or ())
    value = str(requested or "").strip().lower()
    if requested is not None and value not in supported:
        raise Problem("effort", "Choose an effort supported by that model", 422)
    current = str(current or "").strip().lower()
    return value or (current if current in supported else choice.get("default_effort") or "")


def choice_of(model, effort=None, harness=None, current=None):
    """A task's model as a request names it, {harness, model, reasoning_effort, runtime}, checked the way a bot's task
    model is: the model is in the catalog and not retired, the harness runs it, and the effort is one it takes (the
    model's default when none is named). `current` is the choice a change keeps what it does not name, while the model
    stays the same. Raises Problem."""
    row = providers.MODEL_BY_ID.get(model)
    if not row:
        raise Problem("model", "Choose one of the supported models", 422)
    refuse_retired(row)
    keep = current if current and current.get("model") == row["id"] else {}
    harness = harness_of(row, harness, keep.get("harness"))
    return {"harness": harness, "model": row["id"],
            "reasoning_effort": effort_of(row, effort, keep.get("reasoning_effort")),
            "runtime": runtime_of(harness) or row["runtime"]}


def _fits(model, effort):
    """Whether `effort` is one `model` takes: a task effort is kept only when the model that runs it takes it."""
    return bool(effort) and effort in tuple((providers.MODEL_BY_ID.get(model or "") or {}).get("efforts") or ())


def _size(tokens):
    """A context length as Compact at names it: 600k, 1M."""
    return f"{tokens // 1_000_000}M" if tokens >= 1_000_000 and tokens % 1_000_000 == 0 else f"{tokens // 1000}k"


def _context_said(compact_at):
    return f", context {_size(compact_at)}" if compact_at else ""


def label(choice):
    """`model (effort)`, how a run names what it was on."""
    if not choice:
        return ""
    effort = choice.get("reasoning_effort") or choice.get("effort") or ""
    return f"{choice.get('model') or ''}{f' ({effort})' if effort else ''}"


def task_keyed(config, bot, task_id, session_task_id, conversation_id):
    """Whether a turn on `task_id` runs in that task's own session: a `task:` session key, which `session: bot` and a
    turn with no task never have."""
    return bool(task_id) and sessions.session_key(config, bot, session_task_id or task_id,
                                                  conversation_id).startswith("task:")


def readiness(c, bot, choice, runner_id=None):
    """`can_run` for the task model on `bot`'s computer, as a task turn would see it. A runtime the computer has not
    installed is not ready yet: until the computer syncs and installs it (runner-rs/src/relays.rs), tasks run on the
    bot's own model. And a runtime that is not signed in is not signed in, even where
    the Gateway would run the bot's own turns before sign-in (models_preset): a task turn falls back to the bot's own
    model instead of spending Gateway credit under the task model's name."""
    can = can_run(c, bot, choice["runtime"], runner=runner_id, harness=choice["harness"], model=choice["model"])
    computer = can.get("computer") or {}
    where = computer.get("label") or "this bot's computer"
    row = c.execute("SELECT readiness FROM runners WHERE id=?", (computer.get("id") or "",)).fetchone()
    ready = readiness_document(row[0]) if row else {}
    state = (ready.get("runtimes") or {}).get(choice["runtime"])
    if ready.get("schema_version") == 1 and isinstance(state, dict) and state.get("installed") is False:
        return {**can, "can_run": False, "problem": f"{choice['runtime']} is not installed on {where}",
                "fix": f"Install {choice['runtime']} on {where}, or choose a model it already runs",
                "link": "#/settings"}
    if can.get("gateway"):
        return {**{k: v for k, v in can.items() if k != "gateway"}, "can_run": False, "sign_in": True,
                "problem": f"{choice['runtime']} is not signed in on {where}",
                "fix": f"Sign in {choice['runtime']} on {where} in Settings > Computers, or choose a model it already "
                       "runs", "link": "#/settings"}
    return can


def why_not(c, settings, bot, choice, runner_id, job_id=None):
    """Why the task model cannot run this turn on `bot`'s computer `runner_id`, or "" when it can. A no from
    `readiness` is a reason (not installed, not signed in). Its "don't know" is not: Grok's sign-in can't be checked
    without starting it (`check = "none"`) and Antigravity reports no readiness, so a "don't know" would keep them off
    for good. Such a turn runs, and a refused login comes back through `mark_rejected` as a no for the next claim. A
    login a usage limit holds, or that another job is probing after one, is a reason too, so the task runs on the
    bot's model rather than wait on it."""
    from . import login_holds
    row = providers.MODEL_BY_ID.get(choice["model"])
    if not row:
        return f"{choice['model']} is not a model Tico knows any more"
    if row.get("deprecated") and row.get("lineup") != "old":      # the Gateway's old lineup is not offered but still runs
        return f"{row.get('label') or choice['model']} is retired"
    try:
        providers.complete_choice(providers.load(c, settings)["enabled"], choice["runtime"], choice["model"])
    except providers.ProviderError as exc:
        return str(exc).replace("default model", "task model")
    can = readiness(c, bot, choice, runner_id)
    if can.get("can_run") is False:
        return can.get("problem") or f"{choice['runtime']} cannot run on this bot's computer"
    login = login_holds.canonical(c, runner_id, "", choice["runtime"])
    held = login_holds.holding(c, runner_id, login, choice["runtime"], job_id)
    if held:
        until = str(login_holds.get(c, runner_id, login, choice["runtime"]).get("until") or "")
        return (f"{choice['runtime']} is at its usage limit until {until[:10]} {until[11:16]} UTC" if held == "held"
                and until else f"{choice['runtime']} is being retried after its usage limit")
    return ""


# What a bot does that a weaker model could get wrong, from the approvals it has asked for (backend/hubdb.py
# APPROVAL_KINDS), and the plain words a warning names it with.
STAKES = {"send": ("sends messages for you", "may word things badly"),
          "spend": ("spends money for you", "may spend it badly"),
          "publish": ("publishes for you", "may get what it publishes wrong")}


def stakes(c, settings, bot):
    """What is at stake on `bot`, plain words per signal: each send, spend or publish approval it has asked for, and
    each tool grant that lets it write beyond its own repository (backend/bot_tools.py). Empty for a bot with none.
    Customer data and permission changes have no signal of their own beyond these."""
    asked = {row[0] for row in c.execute("SELECT DISTINCT kind FROM approvals WHERE requested_by_id=?",
                                         (H.bot_actor(bot),))}
    found = [{"kind": kind, "does": STAKES[kind][0], "risk": STAKES[kind][1]} for kind in STAKES if kind in asked]
    from . import bot_tools
    from .github_app import repo_of
    row = c.execute("SELECT repo FROM bot_config WHERE bot=?", (bot,)).fetchone()
    stored = (row and row[0]) or "emp-" + (H.bot(c, bot) or {}).get("slug", bot)
    own = (repo_of(stored, settings.github_owner) or stored).lower()

    def own_repo(tool):
        # Its own repository, as the built-in `repo` row or a declared GitHub entry for it: every bot writes there.
        # Without an owner on one side (no GitHub owner set, or a bare name), the repository name alone decides.
        named = str((tool.get("scope") or {}).get("repo") or tool.get("identity") or "").lower()
        if tool["id"] == "repo" or not named:
            return tool["id"] == "repo"
        return named == own or (("/" not in named or "/" not in own) and named.split("/")[-1] == own.split("/")[-1])
    # Only grants it holds now: a requested tool not added yet (`pending: add`) gives it nothing to write with.
    writes = [tool for tool in bot_tools.listing(c, settings, bot)["tools"]
              if tool["id"] != "model" and tool.get("pending") != "add" and not own_repo(tool)
              and "write" in (tool.get("can") or [])]
    if writes:
        names = ", ".join(dict.fromkeys(tool.get("identity") or tool.get("name") or tool["service"] for tool in writes))
        found.append({"kind": "write", "does": f"can change {names}", "risk": "may change things it should not"})
    return found


def below_minimum(own, choice):
    """Whether the task model is a cheaper tier than the bot's own model in the same family: the same provider, at a
    lower output list price (providers.PRICES). A model with no price, or another provider's, is not judged."""
    mine, theirs = providers.MODEL_BY_ID.get(own or ""), providers.MODEL_BY_ID.get((choice or {}).get("model") or "")
    if not mine or not theirs or mine.get("provider") != theirs.get("provider"):
        return False
    a, b = providers.PRICES.get(mine["id"]), providers.PRICES.get(theirs["id"])
    return bool(a and b and b[2] < a[2])


def _named(model):
    """A model as the warning names it: its catalog label, capitalised ("Haiku 5.5")."""
    text = providers.MODEL_BY_ID.get(model, {}).get("label") or model
    return text[:1].upper() + text[1:]


def warning(c, settings, bot, config, choice):
    """The plain-words warning for a task model below the minimum on a high-stakes bot, or "". It never refuses: the
    task model saves and its tasks run on it."""
    if not choice:   # most bots: no lookups at all, so the bot list does not grow with them
        return ""
    own = providers.bot_choice(c, settings, config)[1]
    if not below_minimum(own, choice):
        return ""
    found = stakes(c, settings, bot)
    if not found:
        return ""
    does = " and ".join(row["does"] for row in found[:2]) + (" and more" if len(found) > 2 else "")
    return (f"This bot {does}. {_named(choice['model'])} is a cheaper model than its own {_named(own)}, and "
            f"{found[0]['risk']}. It still saves, and its tasks run on it.")


def _turn(config, effort="", compact_at=None):
    """The bot's config for one turn, with a task effort and context length when set; the rest stays as it is."""
    out = dict(config)
    if effort:
        out["reasoning_effort"] = effort
    if compact_at:
        out["compact_at"] = compact_at
    return out


def apply(config, choice):
    """The bot's config for a turn on the task model: its runtime, harness, model and effort, and the task context
    length when set. The bot's fallback, session and everything else stay as they are."""
    out = dict(config)
    out.update(runtime=choice["runtime"], model=choice["model"], harness=choice["harness"])
    if choice["reasoning_effort"]:
        out["reasoning_effort"] = choice["reasoning_effort"]
    else:
        out.pop("reasoning_effort", None)
    if choice["compact_at"]:
        out["compact_at"] = choice["compact_at"]
    return out


def _effort_on(model, effort):
    """(applied, dropped): the task effort when `model` takes it, else "" and the effort that was dropped, so the bot's
    own effort runs and the run says so."""
    applied = effort if _fits(model, effort) else ""
    return applied, ("" if applied else effort or "")


def pick(c, settings, bot, followed, task_id, session_task_id, conversation_id, runner_id, job_id=None, check=None):
    """(config, record) for a claimed turn. `record` is None for a turn the task settings do not cover; otherwise it
    says what the turn was handed and why: `task` (a task model, or the bot's own model with the task effort or
    context length), `fallback` (a task model could not run; the next one, or the bot's own model, ran), or `check` (a
    check subtask kept its check model over the task's models). `effort` and `compact_at` in the record are what was
    applied. The task's own model is tried first, then the bot's task model, then the bot's own model."""
    tasks = stored(followed)
    mine = task_run_models.of(c, task_id) if task_id else None
    if not (tasks or mine) or not task_keyed(followed, bot, task_id, session_task_id, conversation_id):
        return followed, None
    choice = of(followed)
    compact_at = tasks["compact_at"] if tasks else None
    if mine:
        # Saved with its effort checked against its model; a model that has moved on since does not take it.
        mine = {**mine, "compact_at": compact_at,
                "reasoning_effort": mine["reasoning_effort"] if _fits(mine["model"], mine["reasoning_effort"]) else ""}
    if check:
        out = _turn(followed, compact_at=compact_at)
        over = (f"the task's model {label(mine)}" if mine else f"the bot's task model {label(choice)}") \
            if mine or choice else ""
        if not over:
            return out, None
        return out, {"source": "check", "asked": _asked(mine or choice), "effort": "", "compact_at": compact_at,
                     "note": f"Kept the check model ({check['runtime']}:{check['model']}) over {over}"
                             f"{_context_said(compact_at)}"}
    if not mine and not choice:
        # The bot's own model runs the task, with its task effort when that model takes it. The task model field goes
        # off this turn: there is no other model to switch to, and the runner reads that field as one.
        own = providers.bot_choice(c, settings, followed)[1]
        applied, dropped = _effort_on(own, tasks["reasoning_effort"])
        out = _turn({k: v for k, v in followed.items() if k != KEY}, applied, compact_at)
        note = f"Ran on the bot's model {own}" if own else "Ran on the bot's model"
        note += f" at task effort {applied}" if applied else ""
        note += _context_said(compact_at)
        note += f" (task effort {dropped} is not one {own or 'its model'} takes)" if dropped else ""
        return out, {"source": "task", "asked": {"model": "", "reasoning_effort": tasks["reasoning_effort"]},
                     "effort": applied, "compact_at": compact_at, "note": note}
    failed = []
    for cand in [x for x in (mine, choice) if x]:
        why = why_not(c, settings, bot, cand, runner_id, job_id)
        if not why:
            return _on_task_model(c, settings, bot, followed, cand, cand is mine, compact_at, failed)
        failed.append((cand, why))
    return _on_own_model(c, settings, followed, failed, compact_at)


def _on_task_model(c, settings, bot, followed, choice, own, compact_at, failed):
    """The turn on a task model that can run: the task's own, or the bot's task model after the task's own could not."""
    named = "the task's model" if own else "the bot's task model"
    note = f"Ran on {named} {label(choice)}{_context_said(compact_at)}"
    record = {"source": "task", "asked": _asked(choice), "effort": choice["reasoning_effort"], "compact_at": compact_at,
              "note": note}
    if failed:
        # Its run says the task's own model could not run, and what ran instead; the task says so once (handed).
        record.update(source="fallback", asked=_asked(failed[0][0]), why="; ".join(why for _, why in failed),
                      ran=label(choice), ran_model=_asked(choice),
                      note=f"{note}: " + "; ".join(f"task model {label(cand)} could not run ({why})"
                                                  for cand, why in failed))
        # As for the bot's own model below: a provider thread on the bot's runtime can predate this turn's model, so the
        # runner starts the turn in a side session from the task checkpoint instead of resuming that thread.
        return {**apply(followed, choice), "task_model_fallback": True}, record
    bot_model = providers.bot_choice(c, settings, followed)[1]
    if below_minimum(bot_model, choice) and stakes(c, settings, bot):
        # Below the minimum for what this bot does: it runs all the same, and the run says so (task 7a9b30d4).
        record.update(below_minimum=True, note=f"{record['note']}, below the minimum for this bot: a cheaper model "
                                               f"than its own {bot_model} on a bot that sends, spends, publishes or "
                                               "writes")
    return apply(followed, choice), record


def _on_own_model(c, settings, followed, failed, compact_at):
    """The bot's own model runs the turn after every task model failed to run, with the task effort that model takes."""
    first = failed[0][0]
    own_model = providers.bot_choice(c, settings, followed)[1]
    own = own_model or "the bot's own model"
    bot_effort = followed.get("reasoning_effort") or ""
    applied, dropped = _effort_on(own_model, first["reasoning_effort"])
    at = f" at task effort {applied}" if applied else (f" ({bot_effort})" if bot_effort else "")
    note = (f"Ran on the bot's model {own}{at}{_context_said(compact_at)}: "
            + "; ".join(f"task model {label(cand)} could not run ({why})" for cand, why in failed))
    note += f" (task effort {dropped} is not one {own} takes)" if dropped else ""
    # A provider thread on the bot's runtime can predate task-model turns on another runtime. Each fallback
    # starts fresh from the task checkpoint rather than resuming that incomplete history. This flag is only
    # handed to this turn; it is never stored in the bot's configuration.
    out = _turn(followed, applied, compact_at)
    ran_effort = applied or bot_effort
    return {**out, "task_model_fallback": True}, {"source": "fallback", "asked": _asked(first),
                                                  "why": "; ".join(why for _, why in failed),
                                                  "effort": applied, "compact_at": compact_at,
                                                  "ran": f"{own}{f' ({ran_effort})' if ran_effort else ''}",
                                                  "note": note}


def _asked(choice):
    return {k: choice[k] for k in ("runtime", "harness", "model", "reasoning_effort")}


def handed(c, attempt_id, task_id, record):
    """Keep what the claim chose on the attempt. A fallback, and a run below the minimum for its bot, is also a task
    event, once per reason, so the task says it happened and why without a line for every turn."""
    if not record:
        return
    c.execute("UPDATE attempts SET model=? WHERE id=?", (json.dumps(record), attempt_id))
    if (record["source"] != "fallback" and not record.get("below_minimum")) or not task_id:
        return
    last = c.execute("SELECT note FROM task_events WHERE task_id=? AND field='task_model' ORDER BY rowid DESC LIMIT 1",
                     (task_id,)).fetchone()
    if not last or last[0] != record["note"]:
        H._task_event(c, task_id, H.KEEPER, "task_model", label(record["asked"]),
                      record.get("ran") or label(record["asked"]), record["note"])


def _record(c, attempt_id):
    row = c.execute("SELECT model FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    record = H._json(row[0]) if row and row[0] else None
    return record if isinstance(record, dict) else None


def handed_out(c, attempt_id):
    """The task model the claim handed this attempt, {runtime, model, ...}, or None when it ran on another. A task with
    only an effort or context length runs on the bot's own model, so it is None too. A fallback that ran on the bot's
    task model names that model (`ran_model`)."""
    record = _record(c, attempt_id)
    ran = (record or {}).get("ran_model") or ((record or {}).get("asked") if (record or {}).get("source") == "task"
                                              else None)
    return ran if isinstance(ran, dict) and ran.get("model") else None


def off_bot_model(c, attempt_id):
    """Whether the attempt ran on a task model or a check model rather than the bot's own: then the bot's own model
    being moved says nothing about the turn (execution.ran_another_model)."""
    return (_record(c, attempt_id) or {}).get("source") == "check" or bool(handed_out(c, attempt_id))
