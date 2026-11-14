# pyright: strict
"""The models bots run on follow the company's Claude and ChatGPT plans.

A company that connects only a ChatGPT plan must not keep its bots on Claude models: with no Claude sign-in, every turn
runs on the paid fallback. So each bot a person never moved by hand runs on the set for the plans the company has now:
Claude only, ChatGPT only, or both (SETS below, the one table), and so do its tasks (`task_model`, TASK_SETS): a heavy
role's own turns run on a cheap model and its tasks on a strong one. It follows when a plan is added, connected or
reconnected (model_subscriptions.save, beside `spread`), when one is removed or the provider refuses it
(`needs_reconnect`), and on every start (onboarding.complete_company), which also brings a company that had a plan before
this table existed onto it. With no plan, nothing moves: a bot keeps its model and runs on the Gateway path it has.

- A bot a person set stays: one with a model change in its settings history that is not undone (`settings_changes`,
  field `model`, written by Settings, `POST /api/v2/bots/{bot}/model`, a manager bot or the Assistant). Undoing that
  change gives the bot back to the plans. Its task model follows the same rule on its own field (`task_model`, written
  by Settings or `POST /api/v2/bots/{bot}/task-model`), and a bot whose own model a person set gets no task model from
  the plans either: its tasks keep running on the model the person chose.
- Only bots made from a template follow (the role says which row applies); a shared copy follows its original, and an
  archived bot is left.
- Decision models (pipeline judges, `decision ask`, Decide) are not bots: they stay on the Gateway (Jev) and never
  follow a plan.
- Each move writes `bot.model_changed` (its own model) or `bot.task_model_changed` (its tasks') with `why: plans`;
  nothing asks a person.
"""

import json
import sqlite3
from typing import Any

from . import bot_settings, execution, harnesses, model_login, pipeline_seats, providers, store, task_models

# The helpers this module calls are untyped; each is used through Any.
BS: Any = bot_settings
EX: Any = execution
HN: Any = harnesses
ML: Any = model_login
P: Any = providers
TM: Any = task_models
H: Any = store.H

CLAUDE, CHATGPT, BOTH = "claude", "chatgpt", "both"
# The plans a runtime's subscription stands for (model_subscriptions.KINDS `runtime`).
PLAN_OF_RUNTIME = {"claude": CLAUDE, "codex": CHATGPT}

HEAVY, REVIEWER_2, LIGHT, ASSISTANT = "heavy", "reviewer-2", "light", "assistant"
# Routine built-ins: little judgment per turn, many turns.
LIGHT_TEMPLATES = frozenset(("librarian", "rules-owner", "updates-owner", "automations-owner", "finance-owner"))
ASSISTANT_TEMPLATE = "assistant"

# role -> {plans connected: (model, effort)} for the bot's own turns. Heavy is every role not named: every Feature Owner
# (it pitches, specs, builds and validates in one task), Reviewer 1, every other Owner and manager, a Builder a company
# still has, and the spec and plan writers. Their own turns (routing, stage
# moves, comments) run on the cheap model and their tasks on the strong one (TASK_SETS). The Assistant chats with
# people rather than working tasks, and Haiku scores half of Opus on its evals (evals/assistant/BASELINE.md), so it keeps
# the strong model for its own turns and has no task model. Reviewer 2 always runs another model than Reviewer 1 (D12),
# and with both plans another provider. With both, the light roles run on the ChatGPT plan so the Claude plan's limit
# goes to heavy work.
SETS: dict[str, dict[str, tuple[str, str]]] = {
    HEAVY: {
        CLAUDE: ("claude-haiku-5-5", "xhigh"),
        CHATGPT: ("gpt-6.1-terra", "medium"),
        BOTH: ("claude-haiku-5-5", "xhigh"),
    },
    ASSISTANT: {
        CLAUDE: ("claude-opus-5-5", "medium"),
        CHATGPT: ("gpt-6.1-sol", "high"),
        BOTH: ("claude-opus-5-5", "medium"),
    },
    REVIEWER_2: {
        CLAUDE: ("claude-haiku-5-5", "xhigh"),
        CHATGPT: ("gpt-6.1-terra", "medium"),
        BOTH: ("gpt-6.1-sol", "high"),
    },
    LIGHT: {
        CLAUDE: ("claude-haiku-5-5", "xhigh"),
        CHATGPT: ("gpt-6.1-terra", "medium"),
        BOTH: ("gpt-6.1-terra", "medium"),
    },
}

# role -> {plans connected: (model, effort)} for the bot's tasks (`task_model`, backend/task_models.py). A role not named
# has none: its tasks run on its own model, and a task model the plans gave it before is taken back.
TASK_SETS: dict[str, dict[str, tuple[str, str]]] = {
    HEAVY: {
        CLAUDE: ("claude-opus-5-5", "medium"),
        CHATGPT: ("gpt-6.1-sol", "high"),
        BOTH: ("claude-opus-5-5", "medium"),
    },
}


def connected(c: sqlite3.Connection) -> str:
    """CLAUDE, CHATGPT, BOTH or '' : the plans the company has that work (a subscription, not an API key, that does not
    need reconnecting; one at its usage limit still counts)."""
    from . import model_subscriptions

    MS: Any = model_subscriptions
    KINDS: dict[str, dict[str, str]] = MS.KINDS
    kinds = [kind for kind, spec in KINDS.items() if spec.get("billing") == "subscription"]
    have = {
        PLAN_OF_RUNTIME.get(KINDS[str(r[0])]["runtime"], "")
        for r in c.execute(
            f"SELECT kind FROM model_subscriptions WHERE kind IN ({','.join('?' * len(kinds))}) "
            "AND status<>'needs_reconnect'",
            kinds,
        )
    } - {""}
    return BOTH if len(have) > 1 else next(iter(have), "")


def _offered(model: str) -> bool:
    row: dict[str, Any] = P.MODEL_BY_ID.get(model) or {}
    return bool(row) and not row.get("deprecated") and not row.get("held")


def _pick(model: str, effort: str, plans: str) -> tuple[str, str]:
    """(model, effort) with `plans` connected. A model the catalog does not offer yet gives the first offered pick in
    the tables on the same tool, so a bot never moves onto a model it cannot be set to, nor onto the other plan."""
    if not _offered(model):
        found: dict[str, Any] = P.MODEL_BY_ID.get(model) or {}
        runtime = found.get("runtime")
        rows = [*SETS.values(), *TASK_SETS.values()]
        model, effort = next(
            (
                row[plans]
                for row in rows
                if _offered(row[plans][0]) and P.MODEL_BY_ID[row[plans][0]]["runtime"] == runtime
            ),
            SETS[ASSISTANT][plans],
        )
    row: dict[str, Any] = P.MODEL_BY_ID[model]
    efforts = [str(e) for e in row.get("efforts") or ()]
    return model, effort if effort in efforts else str(row.get("default_effort") or "")


def pick(role: str, plans: str) -> tuple[str, str]:
    """(model, effort) for the own turns of a bot in `role` with `plans` connected."""
    return _pick(*SETS[role][plans], plans)


def task_pick(role: str, plans: str) -> tuple[str, str] | None:
    """(model, effort) for the tasks of a bot in `role` with `plans` connected, or None when they run on its own
    model."""
    found = TASK_SETS.get(role, {}).get(plans)
    return _pick(*found, plans) if found else None


def role_of(template: str, reviewer_2: bool) -> str:
    if reviewer_2:
        return REVIEWER_2
    if template in LIGHT_TEMPLATES:
        return LIGHT
    return ASSISTANT if template == ASSISTANT_TEMPLATE else HEAVY


def set_by_person(c: sqlite3.Connection, bot: str, field: str = "model") -> bool:
    """Whether a person set `field` (`model` or `task_model`) on `bot` and has not undone it."""
    return (
        c.execute(
            "SELECT 1 FROM settings_changes WHERE bot=? AND field=? AND undone_at IS NULL LIMIT 1", (bot, field)
        ).fetchone()
        is not None
    )


def _reviewer_2(c: sqlite3.Connection) -> str:
    found = pipeline_seats.resolve(c).get(pipeline_seats.REVIEWER_2) or ""
    return found.removeprefix("bot:")


def follow(
    c: sqlite3.Connection, settings: Any, actor: str, bots: set[str] | None = None
) -> list[tuple[str, str, str]]:
    """Move each bot that follows the plans (module doc) onto its pick for the plans connected now, its own model and
    its task model; `bots` limits it to those ids. Returns [(bot, model, effort)] for each bot whose own model moved."""
    plans = connected(c)
    if not plans:
        return []
    rows = c.execute(
        "SELECT b.id, s.template FROM bots b JOIN bot_settings s ON s.bot_id=b.id "
        "WHERE COALESCE(s.template,'')<>'' AND COALESCE(b.state,'active')<>'archived' ORDER BY b.id"
    ).fetchall()
    second = _reviewer_2(c)
    moved: list[tuple[str, str, str]] = []
    tasks_moved = False
    for bot, template in ((str(r[0]), str(r[1])) for r in rows):
        if bots is not None and bot not in bots:
            continue
        row = c.execute(BS.ROW_SQL + " WHERE bc.bot=?", (bot,)).fetchone()
        if not row:
            continue
        config: dict[str, Any] = json.loads(row["config"] or "{}")
        if config.get("shared_from"):
            continue
        role = role_of(template, bot == second)
        if set_by_person(c, bot):
            continue  # its tasks keep running on the model the person chose (module doc)
        if not set_by_person(c, bot, TM.KEY):
            tasks_moved = _follow_tasks(c, actor, bot, config, task_pick(role, plans), plans) or tasks_moved
        model, effort = pick(role, plans)
        runtime = str(P.MODEL_BY_ID[model]["runtime"])
        if config.get("model") == model and config.get("reasoning_effort") == effort:
            continue
        old_harness = str(HN.resolve_harness(config, config.get("runtime")))
        harness = old_harness if HN.runtime_of(old_harness) == runtime else str(HN.harnesses_for_runtime(runtime)[0])
        was = EX.model_choice(c, settings, bot)
        before = config.get("model") or ""
        config.update({"model": model, "runtime": runtime, "harness": harness, "reasoning_effort": effort})
        BS.write(c, bot, config)
        c.execute("UPDATE bot_config SET revision=revision+1 WHERE bot=?", (bot,))
        c.execute("UPDATE bots SET effort=? WHERE id=?", (effort, bot))
        EX.lift_after_model_change(c, settings, actor, bot, was)
        reset = EX.reset_bot_sessions(c, bot)
        H.event(
            c,
            actor,
            "bot.model_changed",
            bot,
            {
                "from": before,
                "to": model,
                "runtime": runtime,
                "harness": harness,
                "effort": effort,
                "sessions_reset": reset,
                "why": "plans",
                "plans": plans,
            },
        )
        moved.append((bot, model, effort))
    if moved or tasks_moved:
        ML.hold_removals(c, settings)
    return moved


def _follow_tasks(
    c: sqlite3.Connection,
    actor: str,
    bot: str,
    config: dict[str, Any],
    target: tuple[str, str] | None,
    plans: str,
) -> bool:
    """Put `bot`'s task model on `target` (None: its own model), keeping the task context length it has. Writes
    `config` in place and returns whether it changed. Like a person's change (settings_admin.set_task_model), it
    leaves the bot's sessions as they are."""
    before: dict[str, Any] | None = TM.stored(config)
    compact_at = (before or {}).get("compact_at")
    if target is None:
        if not (before and before.get("model")):
            return False
        after: dict[str, Any] | None = (
            {"harness": "", "model": "", "reasoning_effort": "", "runtime": "", "compact_at": compact_at}
            if compact_at
            else None
        )
    else:
        choice: dict[str, Any] = TM.choice_of(target[0], target[1], current=before)
        after = {**choice, "compact_at": compact_at}
    if after == before:
        return False
    if after is None:
        config.pop(TM.KEY, None)
    else:
        config[TM.KEY] = after
    BS.write(c, bot, config)
    c.execute("UPDATE bot_config SET revision=revision+1 WHERE bot=?", (bot,))
    H.event(c, actor, "bot.task_model_changed", bot, {"before": before, "after": after, "why": "plans", "plans": plans})
    return True
