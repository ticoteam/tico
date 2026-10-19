"""A subtask a bot runs on another model: `hub task child --check-model <runtime>[:<model>] --role review|check`.

The subtask stays the bot's own. Its turns run on the named runtime and model, in a session of their own, and
read only the subtask's room. Only the runtime, model, harness and effort change: the bot's repository, tools,
isolation and credentials are its own, so a check never runs with more than the bot has. When the bot finishes
it, the parent's owner is woken on the parent with the verdict (hubdb._subtask_wake)."""
from . import hubdb as H
from . import providers
from .harnesses import runtime_of
from .readiness import SIGNED_OUT, can_run

ROLES = ("review", "check")


class CheckModelError(ValueError):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code


def parse(spec):
    """(runtime, model) from `<runtime>[:<model>]`; a bare model id names its own runtime."""
    text = str(spec or "").strip()
    if not text:
        raise CheckModelError("check_model", "Name the check model as <runtime> or <runtime>:<model>")
    runtime, _, model = text.partition(":")
    runtime, model = runtime.strip(), model.strip()
    if not model and runtime in providers.MODEL_BY_ID and runtime not in providers.PROVIDER_BY_RUNTIME:
        return "", runtime
    return runtime, model


AUTO = "auto"


def authors_of(c, settings, task_id):
    """The model makers who wrote a task's work: every maker on its runs so far (usage_records.ran_on, so a fallback
    counts as well as the bot's own model), and the provider its owner is set to run on. A run still going has no usage
    yet and adds nothing. Empty when none is known (a person's task with no runs)."""
    from .shared_bots import follow
    from .usage_records import ran_on
    task = H.task(c, task_id) if task_id else None
    if not task:
        return set()
    turns = [row["id"] for row in c.execute("SELECT id FROM turns WHERE task_id=?", (task_id,))]
    makers = {s["family"] for made in ran_on(c, turns).values() for s in made["segments"] if s["family"]}
    if H.is_bot(task["owner"]):
        row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (H.actor_id(task["owner"]),)).fetchone()
        config = follow(c, H.actor_id(task["owner"]), H._json(row["config_json"], {}) or {}) if row else {}
        makers.add(providers.author_provider(providers.bot_choice(c, settings, config)[1]))
    return makers - {""}


def _auto(c, settings, bot, authors):
    """The company's check model for work by `authors` (providers.check_order): the first from a provider none of
    them is, that this bot's computer does not refuse."""
    record = providers.load(c, settings)
    refused = []
    for row in providers.check_order(record):
        if row["provider"] in authors:
            continue
        model = providers.MODEL_BY_ID[row["model"]]
        harnesses = tuple(model.get("harnesses") or (row["runtime"],))
        harness = next((h for h in harnesses if runtime_of(h) == row["runtime"]), harnesses[0])
        can = can_run(c, bot, row["runtime"], harness=harness, model=row["model"])
        if can.get("can_run") is False:
            refused.append(can.get("problem") or row["runtime"])
            continue
        return row["runtime"], row["model"]
    names = sorted(providers.PROVIDER_BY_ID[p]["label"] for p in authors if p in providers.PROVIDER_BY_ID)
    raise CheckModelError("runner_not_ready" if refused else "check_model",
                          f"No check model from a provider other than {' or '.join(names) or 'the author'}"
                          + (": " + "; ".join(refused[:3]) if refused else
                             ": enable another AI provider in Settings > AI providers, or name one with --check-model"))


def choose(c, settings, bot, spec, role, authors=()):
    """The check model a subtask of `bot` may name, or CheckModelError. The company must have the provider
    on (Settings > AI providers), the model must be current, and the bot's computer must not report that
    runtime signed out. `auto` takes the company's check model for work by `authors`, never one of their providers."""
    if role not in ROLES:
        raise CheckModelError("role", "Choose --role review or --role check")
    record = providers.load(c, settings)
    runtime, model = _auto(c, settings, bot, set(authors)) if str(spec or "").strip() == AUTO else parse(spec)
    try:
        runtime, model = providers.complete_choice(record["enabled"], runtime, model)
    except providers.ProviderError as exc:
        raise CheckModelError("check_model", str(exc).replace("default model", "check model")) from None
    if not runtime:
        raise CheckModelError("check_model", "Enable an AI provider in Settings before naming a check model")
    row = providers.MODEL_BY_ID.get(model) or {}
    harnesses = tuple(row.get("harnesses") or (runtime,))
    harness = next((h for h in harnesses if runtime_of(h) == runtime), harnesses[0])
    can = can_run(c, bot, runtime, harness=harness, model=model)
    if can.get("can_run") is False:
        raise CheckModelError("runner_not_ready", can.get("problem") or f"{runtime} cannot run on this bot's computer")
    return {"runtime": runtime, "model": model, "harness": harness,
            "effort": str(row.get("default_effort") or ""), "role": role}


def record(c, actor, task_id, check):
    c.execute("INSERT INTO task_check_models(task_id,runtime,model,harness,effort,role,created_by,created) "
              "VALUES(?,?,?,?,?,?,?,?)", (task_id, check["runtime"], check["model"], check["harness"],
                                          check["effort"], check["role"], actor, H.now()))
    H._task_event(c, task_id, actor, "check_model", None, f"{check['runtime']}:{check['model']}", check["role"])


def of(c, task_id, bot):
    """The check model `bot`'s turns on the task run on, or None: only the bot that filed the check, while it owns it.
    While its first model would run on the default login that move_on found out of quota, and that login still is, the
    next check model it moved to. A bot moved to another computer, or given a subscription for that tool, tries its
    first model again."""
    first = H.check_model(c, task_id, bot)
    from .repositories import metadata
    moved = metadata(c, "check-moved:" + task_id) if first else None
    if (moved and _default_login(c, bot, first, moved["runner_id"])
            and _out_of_quota(c, moved["runner_id"], first["runtime"])):
        return {k: moved[k] for k in ("runtime", "model", "harness", "effort", "role")}
    return first


def _default_login(c, bot, check, runner_id):
    """Whether `bot`'s turns on `check` run on `runner_id`'s default login: the bot is assigned to that computer and no
    subscription of its covers the check's harness."""
    from .subscriptions import covers, effective
    row = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()
    if not row or row[0] != runner_id:
        return False
    return not (effective(c, bot)[0] and covers({"runtime": check["runtime"], "harness": check["harness"]}))


def _out_of_quota(c, runner_id, runtime, settings=None):
    """Whether Health's default_login_limited marks `runner_id`'s default `runtime` login out of quota: a turn there
    completing, or the provider's reset passing, ends it (health_monitor.out_of_quota_logins)."""
    from .health_monitor import out_of_quota_logins
    return any(x["runner_id"] == runner_id and x["runtime"] == runtime for x in out_of_quota_logins(c, settings))


def move_on(c, settings, bot, task_id, check, runner_id):
    """When a usage limit stops a check on `runner_id`'s default login that Health already marks out of quota, the
    next check model it runs on from its next turn, or None (no next model: the check keeps its hold). The next is the
    first in the company's check order that is not the limited model, not from a provider whose work it checks, not on
    another login out of quota there, and that this bot's computer can run. It goes back to its first model once
    that model's login is not out of quota any more (of)."""
    from .health_monitor import out_of_quota_logins
    from .repositories import save_metadata
    from .task_relations import parent_of
    first = H.check_model(c, task_id, bot)
    out = {x["runtime"] for x in out_of_quota_logins(c, settings) if x["runner_id"] == runner_id}
    if not first or check["runtime"] not in out or not _default_login(c, bot, check, runner_id):
        return None
    authors = authors_of(c, settings, parent_of(c, task_id))
    for row in providers.check_order(providers.load(c, settings)):
        if row["model"] == check["model"] or row["runtime"] in out or row["provider"] in authors:
            continue
        try:
            nxt = choose(c, settings, bot, f"{row['runtime']}:{row['model']}", first["role"])
        except CheckModelError:
            continue
        save_metadata(c, "check-moved:" + task_id, {**nxt, "runner_id": runner_id})
        H._task_event(c, task_id, H.KEEPER, "check_model", f"{check['runtime']}:{check['model']}",
                      f"{nxt['runtime']}:{nxt['model']}", f"the computer's default {check['runtime']} login is out "
                      f"of quota; back to {first['runtime']}:{first['model']} once it works again")
        return nxt
    return None


def handed(c, attempt_id, task_id, check):
    """Remember that the claim handed this attempt out on the check's model, for its completion."""
    from .repositories import save_metadata
    save_metadata(c, "check-attempt:" + attempt_id, {"task_id": task_id, **dict(check)})


def handed_out(c, attempt_id):
    """(task id, check) the claim handed this attempt out on, or (None, None); forgotten once read."""
    from .repositories import metadata
    found = metadata(c, "check-attempt:" + attempt_id)
    if not found:
        return None, None
    c.execute("DELETE FROM registry_metadata WHERE key=?", ("check-attempt:" + attempt_id,))
    return found.pop("task_id"), found


def hold(c, task_id, runtime, until):
    """A usage limit on the check's model holds that check alone until `until`; the bot's own work goes on."""
    from .repositories import save_metadata
    save_metadata(c, "check-limited:" + task_id, {"runtime": runtime, "until": until})


def held(c, task_id):
    """Whether a usage limit on the check's model still holds the check."""
    from .repositories import metadata
    return bool(task_id) and str(metadata(c, "check-limited:" + task_id).get("until") or "") > H.now()


def dropped(c, task_id):
    """Whether a check a usage limit has held has left the board (done, closed or declined): its queued turn is let go
    (Execution.hold_unrunnable) instead of retrying after every reset and running once on a finished task."""
    from .repositories import metadata
    if not task_id or not metadata(c, "check-limited:" + task_id):
        return False
    row = c.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
    return bool(row) and row[0] not in H.ACTIVE_STATUSES


def apply(config, check):
    """The bot's config for a turn on its check subtask: the check's runtime and model, no fallback to another
    provider (a check that ran elsewhere would not be the check asked for), and a session of the subtask's own."""
    if not check:
        return config
    out = {k: v for k, v in config.items() if k not in ("fallback_models",)}
    out.update(runtime=check["runtime"], model=check["model"], harness=check["harness"],
               fallback={"off": True}, session="task", subtask_session="own")
    if check["effort"]:
        out["reasoning_effort"] = check["effort"]
    else:
        out.pop("reasoning_effort", None)
    return out


def runs_here(readiness, check):
    """False when this computer reports the check's runtime signed out. A runtime it has not reported yet is
    left to the turn, which installs it or fails with the reason, rather than waiting for ever."""
    state = (readiness.get("runtimes") or {}).get(check["runtime"]) if check else None
    if not isinstance(state, dict):
        return True
    return str(state.get("authenticated") or "unknown") not in SIGNED_OUT


def review_default(c, settings, owner, parent_id):
    """(check, why not) for a subtask whose owner bot is set to review on the company's check model
    (`review_model: check`) and whose parent is someone else's work. The check is None when that does not apply, or
    when the author is unknown or no other provider can run it; then `why not` says so for the task's history, and
    the bot works on its own model, as before, rather than the review being refused."""
    from .shared_bots import follow
    parent = H.task(c, parent_id) if parent_id else None
    if not parent or not H.is_bot(owner) or parent["owner"] == owner:
        return None, None
    row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (H.actor_id(owner),)).fetchone()
    config = follow(c, H.actor_id(owner), H._json(row["config_json"], {}) or {}) if row else {}
    if config.get("review_model") != "check":
        return None, None
    authors = authors_of(c, settings, parent_id)
    if not authors:
        return None, "review_model: check, but the parent's author is unknown, so this runs on the bot's own model"
    try:
        return choose(c, settings, H.actor_id(owner), AUTO, "review", authors), None
    except CheckModelError as exc:
        return None, f"review_model: check, but {exc}; this runs on the bot's own model"
