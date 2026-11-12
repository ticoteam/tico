# pyright: strict
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
import sqlite3
from typing import Any

from . import bot_settings, execution, harnesses, model_login, pipeline_seats, providers, store

# The helpers this module calls are untyped; each is used through Any.
BS: Any = bot_settings
EX: Any = execution
HN: Any = harnesses
ML: Any = model_login
P: Any = providers
H: Any = store.H

CLAUDE, CHATGPT, BOTH = "claude", "chatgpt", "both"
# The plans a runtime's subscription stands for (model_subscriptions.KINDS `runtime`).
PLAN_OF_RUNTIME = {"claude": CLAUDE, "codex": CHATGPT}

HEAVY, REVIEWER_2, LIGHT = "heavy", "reviewer-2", "light"
# Routine built-ins: little judgment per turn, many turns.
LIGHT_TEMPLATES = frozenset(("librarian", "rules-owner", "updates-owner", "automations-owner", "finance-owner"))

# role -> {plans connected: (model, effort)}. Heavy is every role not named: the Builder, Reviewer 1, the managers, the
# spec and plan writers, the Assistant. Reviewer 2 always runs another model than Reviewer 1 (D12), and with both plans
# another provider. With both, the light roles run on the ChatGPT plan so the Claude plan's limit goes to heavy work.
SETS: dict[str, dict[str, tuple[str, str]]] = {
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


def pick(role: str, plans: str) -> tuple[str, str]:
    """(model, effort) for `role` with `plans` connected. A model the catalog does not offer yet gives the set's first
    offered pick on the same tool, so a bot never moves onto a model it cannot be set to, nor onto the other plan."""
    model, effort = SETS[role][plans]
    if not _offered(model):
        found: dict[str, Any] = P.MODEL_BY_ID.get(model) or {}
        runtime = found.get("runtime")
        model, effort = next(
            (
                SETS[r][plans]
                for r in (HEAVY, REVIEWER_2, LIGHT)
                if _offered(SETS[r][plans][0]) and P.MODEL_BY_ID[SETS[r][plans][0]]["runtime"] == runtime
            ),
            SETS[HEAVY][plans],
        )
    row: dict[str, Any] = P.MODEL_BY_ID[model]
    efforts = [str(e) for e in row.get("efforts") or ()]
    return model, effort if effort in efforts else str(row.get("default_effort") or "")


def set_by_person(c: sqlite3.Connection, bot: str) -> bool:
    return (
        c.execute(
            "SELECT 1 FROM settings_changes WHERE bot=? AND field='model' AND undone_at IS NULL LIMIT 1", (bot,)
        ).fetchone()
        is not None
    )


def _reviewer_2(c: sqlite3.Connection) -> str:
    found = pipeline_seats.resolve(c).get(pipeline_seats.REVIEWER_2) or ""
    return found.removeprefix("bot:")


def follow(
    c: sqlite3.Connection, settings: Any, actor: str, bots: set[str] | None = None
) -> list[tuple[str, str, str]]:
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
    moved: list[tuple[str, str, str]] = []
    for bot, template in ((str(r[0]), str(r[1])) for r in rows):
        if bots is not None and bot not in bots:
            continue
        row = c.execute(BS.ROW_SQL + " WHERE bc.bot=?", (bot,)).fetchone()
        if not row:
            continue
        config: dict[str, Any] = json.loads(row["config"] or "{}")
        if config.get("shared_from") or set_by_person(c, bot):
            continue
        role = REVIEWER_2 if bot == second else LIGHT if template in LIGHT_TEMPLATES else HEAVY
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
    if moved:
        ML.hold_removals(c, settings)
    return moved
