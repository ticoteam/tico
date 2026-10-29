"""The model one task's runs use: `hub task create --model`, `hub task model`, `POST /api/v2/tasks/{id}/model`, and a
routine's bot_turn step, which stamps its choice on the task its run opens. It is kept per task in `task_run_models`,
apart from the check model (backend/check_models.py), which holds when it cannot run: this falls back, to the bot's
task model and then the bot's own model (backend/task_models.py `pick`). Whoever may set the bot's model may set this,
and a bot never does, not even on its own task."""
from . import ceo_acts
from . import hubdb as H
from . import providers
from . import shared_bots
from .harnesses import runtime_of


def of(c, task_id):
    """The task's own choice {harness, runtime, model, reasoning_effort, set_by, set_at}, or None when its runs use the
    bot's task model."""
    if not task_id or not H._has_table(c, "task_run_models"):
        return None
    return H._one(c, "SELECT harness,runtime,model,effort AS reasoning_effort,set_by,set_at FROM task_run_models "
                     "WHERE task_id=?", (task_id,))


def stamped(model, effort="", harness=""):
    """A routine step's choice as the save kept it, not checked again: a model retired since is not run on, and the turn
    falls back saying why (task_models.why_not). Its effort is the one named, else the model's default, as
    task_models.choice_of gives it; its harness is the one named, else the model's first."""
    row = providers.MODEL_BY_ID.get(model) or {}
    harness = str(harness or "").strip().lower() or str(next(iter(row.get("harnesses") or ()), "") or "")
    effort = str(effort or "").strip().lower() or str(row.get("default_effort") or "")
    return {"harness": harness, "runtime": runtime_of(harness) or str(row.get("runtime") or ""), "model": model,
            "reasoning_effort": effort}


def _label(choice):
    if not choice:
        return ""
    effort = choice.get("reasoning_effort") or ""
    return f"{choice.get('model') or ''}{f' ({effort})' if effort else ''}"


def set_choice(c, actor, task_id, choice, note=""):
    """Keep `choice` on the task (checked by task_models.choice_of, or stamped), or clear it with None. The task's
    history says what it was and what it is now. Returns what the task has now."""
    before = of(c, task_id)
    if choice is None:
        c.execute("DELETE FROM task_run_models WHERE task_id=?", (task_id,))
    else:
        c.execute("INSERT INTO task_run_models(task_id,runtime,model,harness,effort,set_by,set_at) "
                  "VALUES(?,?,?,?,?,?,?) "
                  "ON CONFLICT(task_id) DO UPDATE SET runtime=excluded.runtime,model=excluded.model,"
                  "harness=excluded.harness,effort=excluded.effort,set_by=excluded.set_by,set_at=excluded.set_at",
                  (task_id, choice["runtime"], choice["model"], choice["harness"], choice["reasoning_effort"], actor,
                   H.now()))
    H._task_event(c, task_id, actor, "run_model", _label(before), _label(choice), note)
    return of(c, task_id)


def moved(c, actor, task_id):
    """A task handed to another owner loses its own model: whoever chose it chose it for the bot that had the task, and
    nobody has chosen one for the new owner's bot. The task's history says so."""
    if of(c, task_id):
        set_choice(c, actor, task_id, None, note="Cleared when the task changed owner")


def may_set(c, auth, who, bot):
    """Whether `who` may set the model `bot`'s tasks run on: whoever may manage the bot whose model that is
    (Auth.bot_manager, the rule settings_admin applies to the bot's own model; a branch's is its original's), never a
    bot, whatever it manages, and never the CEO Bot, acting for a person or itself: a model stays the person's at every
    level (the "cost" gate settings_admin runs first)."""
    return (who.role != "bot" and not ceo_acts.is_ceo(who.actor)
            and bool(auth.bot_manager(c, who, shared_bots.model_bot(c, bot))))
