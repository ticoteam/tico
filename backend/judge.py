"""Decisions as a Tico tool: `POST /api/v2/decisions`, the door behind `hub_decision_ask` and `hub decision ask`
(the route, the `judge.call` audit events and this module keep their old name, `judge`).

Tico holds the one TypeSafe key (`TYPESAFE_API_KEY` or `TICO_TYPESAFE_SECRET_ARN`, the
same key the Slack gateway routes with). TypeSafe is optional: without it the decision model asks the
team's own AI provider, when the server has that provider's API key (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY`, `OPENROUTER_API_KEY`); with neither it is
unconfigured. A bot or a person sends a JSON state and typed
questions with their own credential and gets calibrated answers back; no bot ever carries the
key, and nothing here decides anything with the answers. The contract and the validation are
`clients/judge.py`, shared with the direct transport, so a call that this route would refuse is
refused the same way before it is sent.

What is kept: one `judge.call` event per call with the label, the question count, the value and
confidence per answer, the model, the token usage and the latency. Never the state: mail
bodies and transcripts pass through here, and the audit is not the place for them. The label
(`questions/README.md`, a set's `id@version`) is how a month of calls is read back per decision.

A budget per actor and day, so a loop cannot spend the key: the cap is on calls, counted from
the events, and a call over it is a 429 that says when to try again. And a spend cap for the team per UTC month
(`TICO_DECISIONS_MONTHLY_USD`, $50 by default), summed from each call's `cost_usd` in the audit: at the cap every
server-side caller (this route, listening, learnings, live meetings, the Slack gateway) carries on as if Decide were
off. Each of them gets its model from `server_engine`, so Jev's one short try and the fallback are the same for all. The caps per role are settings
(`TICO_DECISIONS_DAILY_CALLS_OWNER`, `_HUMAN`, `_BOT`); the Slack gateway's routing counts against the
sender's, and the market ask (`market.ask` events) has its own count under the same caps.
"""

import logging
import os
import threading
from typing import Any

from fastapi import Request
from pydantic import Field

from clients import judge as J
from clients import openrouter
from . import providers
from .config import Settings
from .models import Contract
from .store import H, Problem

LOG = logging.getLogger("tico.judge")
ROLES = ("owner", "human", "bot")
# The defaults; a server's own caps are `daily_cap(settings, role)`.
DAILY_CALLS = {role: getattr(Settings, "decisions_daily_calls_" + role) for role in ROLES}
# TypeSafe gets one short try from the server; when it is slow or down, the company's own model answers, so a
# caller never waits through several long attempts only to receive a 503.
JEV_TIMEOUT = 8


def daily_cap(settings, role):
    """How many server model calls an actor of this role may make today."""
    return int(getattr(settings, "decisions_daily_calls_" + role, DAILY_CALLS.get(role, DAILY_CALLS["bot"])))


_skipped = set()


def _skip(provider, why):
    # Once per process and reason: this runs on every config read and every Slack message.
    if (provider, why) not in _skipped:
        _skipped.add((provider, why))
        LOG.info("The decision model's fallback skips %s: %s", provider, why)


def transport(provider):
    """The API the server calls for a provider: its own, or OpenRouter for the models pi runs through OpenRouter.
    None for a provider with no API the server can call (Cursor)."""
    if provider in J.LLM_KEYS:
        return provider
    runtime = (providers.PROVIDER_BY_ID.get(provider) or {}).get("runtime")
    return "openrouter" if runtime and runtime == providers.CATCH_ALL_RUNTIME else None


def fallback_model(record, env=None):
    """(transport, key, model) on the company's own provider, or None when no enabled provider has a server key.

    The default model's provider goes first; the rest follow in the order the owner enabled them. A provider the
    server cannot call, or whose key it does not hold, is skipped (and logged) for the next one. Models pi runs
    through OpenRouter are asked through OpenRouter with OpenRouter's id, as the runner does."""
    env = os.environ if env is None else env
    default = providers.runtime_provider(record["runtime"])
    for provider in dict.fromkeys(([default] if default else []) + record["enabled"]):
        via = transport(provider)
        if via is None:
            _skip(provider, "the server has no API to call it with")
            continue
        key = (env.get(J.LLM_KEYS[via]) or "").strip()
        model = record["model"] if provider == default else (providers.recommended(provider) or {}).get("id")
        if not key:
            _skip(provider, J.LLM_KEYS[via] + " is not set on the server")
            continue
        if not model:
            _skip(provider, "it has no model")
            continue
        return via, key, openrouter.model_id(model) if via == "openrouter" else model
    return None


def fallback_engine(record, env=None):
    """A decision model on the company's own provider (`fallback_model`), or None."""
    found = fallback_model(record, env)
    return J.llm(*found) if found else None


def tokens(usage):
    """(uncached input, cached input, output) tokens from a provider's usage, whichever names it uses."""
    usage = usage if isinstance(usage, dict) else {}

    def first(*keys, within=usage):
        for key in keys:
            value = within.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return int(value)
        return 0
    details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    cached = first("cache_read_input_tokens", "cachedContentTokenCount") or first("cached_tokens", within=details)
    if "prompt_tokens" in usage:                    # OpenAI-style counts include the cached tokens
        return max(0, first("prompt_tokens") - cached), cached, first("completion_tokens")
    return (first("input_tokens", "promptTokenCount"), cached,
            first("output_tokens", "completion_tokens", "candidatesTokenCount"))


def cost_usd(settings, model, usage):
    """What one call cost in USD: the provider's own figure when it sends one (OpenRouter's `usage.cost`), else its
    tokens at list price (Jev at the settings' rates, the company's models at `providers.PRICES`). None when the price
    is unknown; such a call keeps its tokens in the audit and counts nothing toward the monthly cap."""
    usage = usage if isinstance(usage, dict) else {}
    for key in ("cost_usd", "cost"):
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return round(float(value), 6)
    fresh, cached, out = tokens(usage)
    if str(model or "").lower().startswith("jev"):
        rate_in = settings.decisions_jev_usd_per_mtok_input
        rate_out = settings.decisions_jev_usd_per_mtok_output
        if not (rate_in or rate_out):
            return None
        return round(((fresh + cached) * rate_in + out * rate_out) / 1_000_000, 6)
    found = providers.estimate_cost(model, fresh, cached, out)
    return None if found is None else round(found, 6)


def spent_this_month(c, at=None):
    """USD the team's decision calls have cost since the 1st of this UTC month, from the audit: every `judge.call`,
    plus `judge.spend`, the paid part of a try that failed later and will be retried (the Slack gateway)."""
    month = (at or H.now())[:7]
    total = c.execute("SELECT coalesce(sum(json_extract(detail_json,'$.cost_usd')),0) FROM events "
                      "WHERE ts>=? AND action IN ('judge.call','judge.spend')", (month + "-01T00:00:00",)).fetchone()[0]
    return round(float(total or 0), 6)              # a float sum of cents can land a hair under the cap


def over_spend_cap(c, settings, at=None):
    """(spent, cap) when the month's decision spend has reached the cap, else None. A cap of 0 is no cap."""
    cap = float(settings.decisions_monthly_usd or 0)
    if cap <= 0:
        return None
    spent = spent_this_month(c, at)
    if spent < cap:
        return None
    _skip("every caller", f"the month's decision spend ${spent:.2f} has reached the ${cap:.2f} cap")
    return spent, cap


def jev(settings):
    """TypeSafe as the server asks it: one try of at most JEV_TIMEOUT seconds. None without a key."""
    if not settings.typesafe_api_key or settings.rehearsal:
        return None
    return J.direct(settings.typesafe_api_key, timeout=JEV_TIMEOUT, retries=0)


def server_engine(c, settings, primary=None):
    """The decision model every server-side caller uses, or None for "carry on as if Decide were off": rehearsal, no
    model at all, or the month's spend at the cap. `primary` is Jev (`jev(settings)`, or the test's fake); when Jev
    is slow or down the company's own model answers and the result says `fallback`. Each result carries `cost_usd`.

    A batch (listening, learnings) reuses one engine for many calls, and its rows reach the audit late or only at the
    end, so the engine adds its own calls' cost to the month's spend it read here and refuses `spend_cap` before any
    call that would start at or over the cap. Calls already in flight on other threads still finish."""
    if settings.rehearsal or over_spend_cap(c, settings):
        return None
    backup = fallback_engine(providers.load(c, settings))
    first = primary or backup
    if first is None:
        return None
    cap = float(settings.decisions_monthly_usd or 0)
    spent, lock = [spent_this_month(c) if cap > 0 else 0.0], threading.Lock()

    def engine(state, questions, label=None):
        with lock:
            if cap > 0 and spent[0] >= cap:
                _skip("every caller", f"the month's decision spend ${spent[0]:.2f} has reached the ${cap:.2f} cap")
                raise J.JudgeError("spend_cap", f"Decide has cost ${spent[0]:.2f} this month; the team's cap is "
                                   f"${cap:.2f} until the 1st (UTC)", 429)
        try:
            result = first(state, questions, label)
        except J.JudgeError as exc:
            if first is not primary or backup is None or not exc.retryable:
                raise
            result = {**backup(state, questions, label), "fallback": exc.code}
        cost = cost_usd(settings, result.get("model"), result.get("usage"))
        with lock:
            spent[0] += cost or 0.0
        return {**result, "cost_usd": cost}
    engine.model = J.MODEL if first is primary else getattr(backup, "model", "")
    return engine


def audit_detail(result):
    """What a `judge.call` row keeps of one answer: model, tokens, latency, cost and any fallback. Never the state."""
    detail = {"ms": result.get("ms"), "model": result.get("model"), "usage": result.get("usage") or {},
              "cost_usd": result.get("cost_usd")}
    if result.get("fallback"):
        detail["fallback"] = result["fallback"]
    return detail


class Ask(Contract):
    state: Any
    questions: dict[str, dict] = Field(min_length=1, max_length=J.MAX_QUESTIONS)
    label: str | None = Field(default=None, max_length=J.MAX_LABEL)


def used_today(c, actor, at=None, action="judge.call"):
    day = (at or H.now())[:10]
    return c.execute("SELECT count(*) FROM events WHERE actor=? AND action=? AND ts>=?",
                     (actor, action, day + "T00:00:00")).fetchone()[0]


def install_judge(app, store, auth):
    settings = store.settings
    app.state.judge = jev(settings)

    @app.get("/api/v2/judge")
    def config(request: Request):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            used = used_today(c, who.actor)
            engine = None if settings.rehearsal else app.state.judge or fallback_engine(providers.load(c, settings))
            spent = spent_this_month(c)
        return {"configured": engine is not None, "model": (J.MODEL if app.state.judge else getattr(engine, "model", "")) if engine else "",
                "daily_calls": daily_cap(settings, who.role), "used_today": used,
                "monthly_usd": float(settings.decisions_monthly_usd or 0), "spent_this_month_usd": round(spent, 4),
                "max_questions": J.MAX_QUESTIONS, "max_state_chars": J.MAX_STATE_CHARS}

    @app.post("/api/v2/judge")
    def ask(request: Request, body: Ask):
        who = request.state.identity
        auth.domain(who)
        if settings.rehearsal:
            raise Problem("rehearsal", "Decisions are off in rehearsal mode; nothing is sent to a provider", 503)
        with store.read() as c:
            capped = over_spend_cap(c, settings)
            engine = None if capped else server_engine(c, settings, app.state.judge)
        if capped:
            raise Problem("spend_cap", f"Decide has cost ${capped[0]:.2f} this month; the team's cap is "
                          f"${capped[1]:.2f} until the 1st (UTC). Carry on without it.", 429)
        if engine is None:
            raise Problem("judge_unconfigured", "No decision model is configured on this server: set a TypeSafe key, "
                          "or the API key of one of the team's AI providers", 503)
        try:
            J.validate(body.state, body.questions, body.label)
        except J.JudgeError as exc:
            raise Problem("invalid", exc.detail, 422)
        cap = daily_cap(settings, who.role)
        with store.read() as c:
            used = used_today(c, who.actor)
        if used >= cap:
            raise Problem("budget", f"{who.actor} has made {used} decision calls today; the cap is {cap} "
                          "until midnight UTC", 429)
        label = body.label or ""
        detail = {"label": label, "questions": len(body.questions),
                  "types": sorted({q["type"] for q in body.questions.values()})}
        try:
            result = engine(body.state, body.questions, body.label)
        except J.JudgeError as exc:
            with store.transaction() as c:
                H.event(c, who.actor, "judge.call", label, {**detail, "error": exc.code, "detail": exc.detail[:300]})
            if exc.code == "spend_cap":
                raise Problem("spend_cap", exc.detail + ". Carry on without it.", 429)
            status = 503 if exc.retryable else (422 if exc.code == "invalid" else 502)
            raise Problem("judge_" + exc.code, exc.detail, status)
        with store.transaction() as c:
            H.event(c, who.actor, "judge.call", label, {
                **detail, **audit_detail(result), "answers": J.summary(result["answers"])})
        return {"model": result["model"], "answers": result["answers"], "usage": result.get("usage") or {},
                "ms": result["ms"], "label": body.label}
