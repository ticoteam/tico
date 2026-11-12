"""The models bots run on follow the company's Claude and ChatGPT plans.

A company that connects only a ChatGPT plan must not keep its bots on Claude models: with no Claude sign-in, every turn
runs on the paid fallback. So each bot a person never moved by hand runs on the set for the plans the company has now:
Claude only, ChatGPT only, or both (SETS below, the one table). It follows when a plan is added, connected or
reconnected (model_subscriptions.save, beside `spread`), when one is removed or the provider refuses it
(`needs_reconnect`), and on every start (onboarding.complete_company), which also brings a company that had a plan before
this table existed onto it. With no plan, nothing moves: a bot keeps its model and runs on the Gateway path it has.

- A bot a person set stays: one with a model change in its settings history that is not undone (`settings_changes`,
  field `model`, written by Settings, `POST /api/v2/bots/{bot}/model`, a manager bot or the Assistant). Undoing that
  change gives the bot back to the plans.
- Only bots made from a template follow (the role says which row applies); a shared copy follows its original, and an
  archived bot is left.
- Decision models (pipeline judges, `decision ask`, Decide) are not bots: they stay on the Gateway (Jev) and never
  follow a plan.
- Each move writes `bot.model_changed` with `why: plans`; nothing asks a person.
"""

import json

from . import bot_settings as BS, model_login, pipeline_seats, providers
from .execution import lift_after_model_change, model_choice, reset_bot_sessions
from .harnesses import harnesses_for_runtime, resolve_harness, runtime_of
from .store import H

CLAUDE, CHATGPT, BOTH = "claude", "chatgpt", "both"
# The plans a runtime's subscription stands for (model_subscriptions.KINDS `runtime`).
PLAN_OF_RUNTIME = {"claude": CLAUDE, "codex": CHATGPT}

HEAVY, REVIEWER_2, LIGHT = "heavy", "reviewer-2", "light"
# Routine built-ins: little judgment per turn, many turns.
LIGHT_TEMPLATES = frozenset(("librarian", "rules-owner", "updates-owner", "automations-owner", "finance-owner"))

# role -> {plans connected: (model, effort)}. Heavy is every role not named: the Builder, Reviewer 1, the managers, the
# spec and plan writers, the Assistant. Reviewer 2 always runs another model than Reviewer 1 (D12), and with both plans
# another provider. With both, the light roles run on the ChatGPT plan so the Claude plan's limit goes to heavy work.
SETS = {
    HEAVY: {
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


def connected(c):
    """CLAUDE, CHATGPT, BOTH or '' : the plans the company has that work (a subscription, not an API key, that does not
    need reconnecting; one at its usage limit still counts)."""
    from .model_subscriptions import KINDS

    kinds = [kind for kind, spec in KINDS.items() if spec.get("billing") == "subscription"]
    have = {
        PLAN_OF_RUNTIME.get(KINDS[r[0]]["runtime"])
        for r in c.execute(
            f"SELECT kind FROM model_subscriptions WHERE kind IN ({','.join('?' * len(kinds))}) "
            "AND status<>'needs_reconnect'",
            kinds,
        )
    } - {None}
    return BOTH if len(have) > 1 else next(iter(have), "")


def _offered(model):
    row = providers.MODEL_BY_ID.get(model) or {}
    return bool(row) and not row.get("deprecated") and not row.get("held")


def pick(role, plans):
    """(model, effort) for `role` with `plans` connected. A model the catalog does not offer yet gives the set's first
    offered pick on the same tool, so a bot never moves onto a model it cannot be set to, nor onto the other plan."""
    model, effort = SETS[role][plans]
    if not _offered(model):
        runtime = (providers.MODEL_BY_ID.get(model) or {}).get("runtime")
        model, effort = next(
            (
                SETS[r][plans]
                for r in (HEAVY, REVIEWER_2, LIGHT)
                if _offered(SETS[r][plans][0]) and providers.MODEL_BY_ID[SETS[r][plans][0]]["runtime"] == runtime
            ),
            SETS[HEAVY][plans],
        )
    row = providers.MODEL_BY_ID[model]
    return model, effort if effort in tuple(row.get("efforts") or ()) else row.get("default_effort") or ""


def set_by_person(c, bot):
    return (
        c.execute(
            "SELECT 1 FROM settings_changes WHERE bot=? AND field='model' AND undone_at IS NULL LIMIT 1", (bot,)
        ).fetchone()
        is not None
    )


def _reviewer_2(c):
    found = pipeline_seats.resolve(c).get(pipeline_seats.REVIEWER_2) or ""
    return found.removeprefix("bot:")


def follow(c, settings, actor, bots=None):
    """Move each bot that follows the plans (module doc) onto its pick for the plans connected now; `bots` limits it to
    those ids. Returns [(bot, model, effort)] for each bot moved."""
    plans = connected(c)
    if not plans:
        return []
    rows = c.execute(
        "SELECT b.id, s.template FROM bots b JOIN bot_settings s ON s.bot_id=b.id "
        "WHERE COALESCE(s.template,'')<>'' AND COALESCE(b.state,'active')<>'archived' ORDER BY b.id"
    ).fetchall()
    second = _reviewer_2(c)
    moved = []
    for bot, template in ((r[0], r[1]) for r in rows):
        if bots is not None and bot not in bots:
            continue
        row = c.execute(BS.ROW_SQL + " WHERE bc.bot=?", (bot,)).fetchone()
        if not row:
            continue
        config = json.loads(row["config"] or "{}")
        if config.get("shared_from") or set_by_person(c, bot):
            continue
        role = REVIEWER_2 if bot == second else LIGHT if template in LIGHT_TEMPLATES else HEAVY
        model, effort = pick(role, plans)
        runtime = providers.MODEL_BY_ID[model]["runtime"]
        if config.get("model") == model and config.get("reasoning_effort") == effort:
            continue
        old_harness = resolve_harness(config, config.get("runtime"))
        harness = old_harness if runtime_of(old_harness) == runtime else harnesses_for_runtime(runtime)[0]
        was = model_choice(c, settings, bot)
        before = config.get("model") or ""
        config.update({"model": model, "runtime": runtime, "harness": harness, "reasoning_effort": effort})
        BS.write(c, bot, config)
        c.execute("UPDATE bot_config SET revision=revision+1 WHERE bot=?", (bot,))
        c.execute("UPDATE bots SET effort=? WHERE id=?", (effort, bot))
        lift_after_model_change(c, settings, actor, bot, was)
        reset = reset_bot_sessions(c, bot)
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
    if moved:
        model_login.hold_removals(c, settings)
    return moved
