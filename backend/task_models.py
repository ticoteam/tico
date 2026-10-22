"""The model a bot's tasks run on: `task_model` in its config, beside its own model, harness and effort.

Empty means the same as the bot. A turn that carries a task in its own session (clients/sessions.py, a `task:` key)
runs on it; chat with the bot, and a bot on `session: bot`, stays on the bot's model. A check subtask's model
(backend/check_models.py) wins over it. A task model that cannot run here (retired, its provider off, not installed
or not signed in on its computer) never holds or fails the turn: it runs on the bot's own model and effort, and the
run and the task say so. Each turn's choice is kept on its attempt (`model_json`) for its run history
(usage_records.ran_on)."""
import json

from clients import sessions

from . import hubdb as H
from . import providers
from .harnesses import runtime_of
from .readiness import can_run
from .store import readiness_document

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


def readiness(c, bot, choice, runner_id=None):
    """`can_run` for the task model on `bot`'s computer, as a task turn would see it. A runtime the computer has not
    installed is not "installing": a runner installs only its bots' own models and fallbacks
    (runner-rs/src/relays.rs), so nothing ever will. And a runtime that is not signed in is not signed in, even where
    the Gateway would run the bot's own turns before sign-in (models_preset): a task turn falls back to the bot's own
    model instead of spending Gateway credit under the task model's name."""
    can = can_run(c, bot, choice["runtime"], runner=runner_id, harness=choice["harness"], model=choice["model"])
    computer = can.get("computer") or {}
    where = computer.get("label") or "this bot's computer"
    row = c.execute("SELECT readiness_json FROM runners WHERE id=?", (computer.get("id") or "",)).fetchone()
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
    can = readiness(c, bot, choice, runner_id)
    if can.get("can_run") is False:
        return can.get("problem") or f"{choice['runtime']} cannot run on this bot's computer"
    login = login_holds.canonical(c, runner_id, effective(c, bot)[0] or "", choice["runtime"])
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
    asked = {row[0] for row in c.execute("SELECT DISTINCT kind FROM approvals WHERE requested_by=?",
                                         (H.bot_actor(bot),))}
    found = [{"kind": kind, "does": STAKES[kind][0], "risk": STAKES[kind][1]} for kind in STAKES if kind in asked]
    from . import bot_tools
    from .github_app import repo_of
    row = c.execute("SELECT repo FROM bot_config WHERE bot=?", (bot,)).fetchone()
    stored = (row and row[0]) or "emp-" + bot
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
    record = {"source": "task", "asked": asked, "note": f"Ran on the bot's task model {label(choice)}"}
    own = providers.bot_choice(c, settings, followed)[1]
    if below_minimum(own, choice) and stakes(c, settings, bot):
        # Below the minimum for what this bot does: it runs all the same, and the run says so (task 7a9b30d4).
        record.update(below_minimum=True, note=f"{record['note']}, below the minimum for this bot: a cheaper model "
                                               f"than its own {own} on a bot that sends, spends, publishes or writes")
    return apply(followed, choice), record


def handed(c, attempt_id, task_id, record):
    """Keep what the claim chose on the attempt. A fallback, and a run below the minimum for its bot, is also a task
    event, once per reason, so the task says it happened and why without a line for every turn."""
    if not record:
        return
    c.execute("UPDATE attempts SET model_json=? WHERE id=?", (json.dumps(record), attempt_id))
    if (record["source"] != "fallback" and not record.get("below_minimum")) or not task_id:
        return
    last = c.execute("SELECT note FROM task_events WHERE task_id=? AND field='task_model' ORDER BY rowid DESC LIMIT 1",
                     (task_id,)).fetchone()
    if not last or last[0] != record["note"]:
        H._task_event(c, task_id, H.KEEPER, "task_model", label(record["asked"]),
                      record.get("ran") or label(record["asked"]), record["note"])


def handed_out(c, attempt_id):
    """The task model the claim handed this attempt, {runtime, model, ...}, or None when it ran on another."""
    row = c.execute("SELECT model_json FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    record = H._json(row[0]) if row and row[0] else None
    return record["asked"] if isinstance(record, dict) and record.get("source") == "task" else None
