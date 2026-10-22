"""The model a bot's tasks run on: `task_model` in its config, beside its own model, harness and effort.

Empty means the same as the bot. A turn that carries a task in its own session (clients/sessions.py, a `task:` key)
runs on it; chat with the bot, and a bot on `session: bot`, stays on the bot's model. A check subtask's model
(backend/check_models.py) wins over it. A task model that cannot run here (retired, its provider off, its computer
not signed in) never holds or fails the turn: it runs on the bot's own model and effort, and the run and the task say
so. Each turn's choice is kept on its attempt (`model_json`) for its run history (usage_records.ran_on)."""
import json

from clients import sessions

from . import hubdb as H
from . import providers
from .harnesses import runtime_of
from .readiness import can_run

KEY = "task_model"


def of(config):
    """The task model a bot's config sets, {harness, model, reasoning_effort, runtime}, or None for the same as the bot."""
    chosen = (config or {}).get(KEY)
    if not isinstance(chosen, dict) or not str(chosen.get("model") or "").strip():
        return None
    model = str(chosen["model"]).strip()
    row = providers.MODEL_BY_ID.get(model) or {}
    harness = str(chosen.get("harness") or "").strip()
    return {"harness": harness, "model": model, "reasoning_effort": str(chosen.get("reasoning_effort") or ""),
            "runtime": str(chosen.get("runtime") or "") or runtime_of(harness) or str(row.get("runtime") or "")}


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


def why_not(c, settings, bot, choice, runner_id, job_id=None):
    """Why the task model cannot run this turn on `bot`'s computer `runner_id`, or "" when it can. Its sign-in is
    `can_run`'s to judge (the bot's own profile, the Gateway preset); a login a usage limit holds, or that another job
    is probing after one, is a reason too, so the task runs on the bot's model rather than wait on it."""
    from . import login_holds
    from .subscriptions import effective
    row = providers.MODEL_BY_ID.get(choice["model"])
    if not row:
        return f"{choice['model']} is not a model Tico knows any more"
    if row.get("deprecated"):
        return f"{row.get('label') or choice['model']} is retired"
    try:
        providers.complete_choice(providers.load(c, settings)["enabled"], choice["runtime"], choice["model"])
    except providers.ProviderError as exc:
        return str(exc).replace("default model", "task model")
    can = can_run(c, bot, choice["runtime"], runner=runner_id, harness=choice["harness"], model=choice["model"])
    if can.get("can_run") is False:
        return can.get("problem") or f"{choice['runtime']} cannot run on this bot's computer"
    login = login_holds.canonical(c, runner_id, effective(c, bot)[0] or "", choice["runtime"])
    held = login_holds.holding(c, runner_id, login, choice["runtime"], job_id)
    if held:
        until = str(login_holds.get(c, runner_id, login, choice["runtime"]).get("until") or "")
        return (f"{choice['runtime']} is at its usage limit until {until[:10]} {until[11:16]} UTC" if held == "held"
                and until else f"{choice['runtime']} is being retried after its usage limit")
    return ""


def apply(config, choice):
    """The bot's config for a turn on the task model: its runtime, harness, model and effort. The bot's fallback,
    session and everything else stay as they are."""
    out = dict(config)
    out.update(runtime=choice["runtime"], model=choice["model"], harness=choice["harness"])
    if choice["reasoning_effort"]:
        out["reasoning_effort"] = choice["reasoning_effort"]
    else:
        out.pop("reasoning_effort", None)
    return out


def pick(c, settings, bot, followed, task_id, session_task_id, conversation_id, runner_id, job_id=None, check=None):
    """(config, record) for a claimed turn. `record` is None for a turn the task model does not cover; otherwise it
    says what the turn was handed and why: `task` (the task model), `fallback` (the task model could not run; the bot's
    own model ran), or `check` (a check subtask kept its check model over the task model)."""
    choice = of(followed)
    if not choice or not task_keyed(followed, bot, task_id, session_task_id, conversation_id):
        return followed, None
    asked = {k: choice[k] for k in ("runtime", "harness", "model", "reasoning_effort")}
    if check:
        return followed, {"source": "check", "asked": asked,
                          "note": f"Kept the check model ({check['runtime']}:{check['model']}) over the bot's task "
                                  f"model {label(choice)}"}
    why = why_not(c, settings, bot, choice, runner_id, job_id)
    if why:
        own = providers.bot_choice(c, settings, followed)[1] or "the bot's own model"
        effort = followed.get("reasoning_effort") or ""
        return followed, {"source": "fallback", "asked": asked, "why": why,
                          "ran": f"{own}{f' ({effort})' if effort else ''}",
                          "note": f"Ran on the bot's model {own}{f' ({effort})' if effort else ''}: task model "
                                  f"{label(choice)} could not run ({why})"}
    return apply(followed, choice), {"source": "task", "asked": asked,
                                     "note": f"Ran on the bot's task model {label(choice)}"}


def handed(c, attempt_id, task_id, record):
    """Keep what the claim chose on the attempt. A fallback is also a task event, once per reason, so the task says
    the fallback happened and why without a line for every turn."""
    if not record:
        return
    c.execute("UPDATE attempts SET model_json=? WHERE id=?", (json.dumps(record), attempt_id))
    if record["source"] != "fallback" or not task_id:
        return
    last = c.execute("SELECT note FROM task_events WHERE task_id=? AND field='task_model' ORDER BY rowid DESC LIMIT 1",
                     (task_id,)).fetchone()
    if not last or last[0] != record["note"]:
        H._task_event(c, task_id, H.KEEPER, "task_model", label(record["asked"]), record["ran"], record["note"])


def handed_out(c, attempt_id):
    """The task model the claim handed this attempt, {runtime, model, ...}, or None when it ran on another."""
    row = c.execute("SELECT model_json FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    record = H._json(row[0]) if row and row[0] else None
    return record["asked"] if isinstance(record, dict) and record.get("source") == "task" else None
