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

import json
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


# The dearest list price in `providers.PRICES` (USD per 1M input, output tokens): what a call to a model with no known
# price is counted at, so no paid call counts $0 toward the cap.
CEILING = (max(p[0] for p in providers.PRICES.values()), max(p[2] for p in providers.PRICES.values()))
# A call's output is at most the transports' max_tokens; the reservation for a call in flight assumes all of it.
MAX_OUTPUT_TOKENS = 4096


def priced(settings, model):
    """Whether `model` has a known price: Jev at the settings' rates, any other at `providers.PRICES`."""
    if str(model or "").lower().startswith("jev"):
        return bool(settings.decisions_jev_usd_per_mtok_input or settings.decisions_jev_usd_per_mtok_output)
    return providers.estimate_cost(model, 1, 0, 1) is not None


def cost_usd(settings, model, usage):
    """What one call cost in USD: the provider's own figure when it sends one (OpenRouter's `usage.cost`), else its
    tokens at list price (Jev at the settings' rates, the company's models at `providers.PRICES`), else, for a model
    with no known price, its tokens at `CEILING`. Never None: an unknown price is counted high, not as nothing."""
    usage = usage if isinstance(usage, dict) else {}
    for key in ("cost_usd", "cost"):
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return round(float(value), 6)
    fresh, cached, out = tokens(usage)
    if priced(settings, model) and str(model or "").lower().startswith("jev"):
        rate_in, rate_out = settings.decisions_jev_usd_per_mtok_input, settings.decisions_jev_usd_per_mtok_output
    elif priced(settings, model):
        return round(providers.estimate_cost(model, fresh, cached, out), 6)
    else:
        rate_in, rate_out = CEILING
    return round(((fresh + cached) * rate_in + out * rate_out) / 1_000_000, 6)


def spent_this_month(c, at=None):
    """USD the team's decision calls have cost since the 1st of this UTC month: the sum of the `judge.spend` rows
    `metered` writes after every paid call, whoever made it. (`judge.call` rows repeat the cost for reading.)"""
    month = (at or H.now())[:7]
    total = c.execute("SELECT coalesce(sum(json_extract(detail_json,'$.cost_usd')),0) FROM events "
                      "WHERE ts>=? AND action='judge.spend'", (month + "-01T00:00:00",)).fetchone()[0]
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


# Calls in flight in this process, as USD reserved for them: a request or batch thread that checks the cap counts
# what the others have started and not yet written down.
_METER, _RESERVED = threading.Lock(), [0.0]


def reservation(settings, models, state, questions):
    """The most one call can cost: all of its input and MAX_OUTPUT_TOKENS of output, at the dearest of `models`."""
    size = len(json.dumps(state, default=str)) + len(json.dumps(questions, default=str))
    usage = {"input_tokens": size // 3 + 1000, "output_tokens": MAX_OUTPUT_TOKENS}
    return max(cost_usd(settings, model, usage) for model in models or [""])


def metered(store, models, state, questions, label, call):
    """One paid decision call under the team's monthly cap, for every caller (server and Slack gateway alike).

    Under one lock per process: read the month's spend from the audit, add the reservations of calls in flight here,
    refuse `spend_cap` at or over the cap, else reserve this call's most. After the call its real cost is written as a
    `judge.spend` row before the reservation is released, so the next check sees one or the other. Another process
    (the Slack gateway's container) sees this one's calls once they are written; that is the most the cap can be
    passed by: the other process's calls in flight, each at most its reservation."""
    settings = store.settings
    cap = float(settings.decisions_monthly_usd or 0)
    reserved = reservation(settings, models, state, questions) if cap > 0 else 0.0
    with _METER:
        if cap > 0:
            with store.read() as c:
                spent = spent_this_month(c)
            if spent + _RESERVED[0] >= cap:
                _skip("every caller", f"the month's decision spend ${spent:.2f} has reached the ${cap:.2f} cap")
                raise J.JudgeError("spend_cap", f"Decide has cost ${spent:.2f} this month; the team's cap is "
                                   f"${cap:.2f} until the 1st (UTC)", 429)
        _RESERVED[0] += reserved
    try:
        result = call()
        cost = cost_usd(settings, result.get("model"), result.get("usage"))
        with store.transaction() as c:
            H.event(c, H.KEEPER, "judge.spend", label or "", {
                "label": label, "model": result.get("model"), "cost_usd": cost,
                **({} if priced(settings, result.get("model")) or _own_cost(result) else {"price": "ceiling"})})
        return {**result, "cost_usd": cost}
    finally:
        with _METER:
            _RESERVED[0] = max(0.0, _RESERVED[0] - reserved)


def _own_cost(result):
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    return any(isinstance(usage.get(k), (int, float)) and not isinstance(usage.get(k), bool) for k in ("cost_usd", "cost"))


def jev(settings):
    """TypeSafe as the server asks it: one try of at most JEV_TIMEOUT seconds. None without a key."""
    if not settings.typesafe_api_key or settings.rehearsal:
        return None
    return J.direct(settings.typesafe_api_key, timeout=JEV_TIMEOUT, retries=0)


def server_engine(store, c, primary=None):
    """The decision model every server-side caller uses, or None for "carry on as if Decide were off": rehearsal, no
    model at all, or the month's spend at the cap. `primary` is Jev (`jev(settings)`, or the test's fake); when Jev
    is slow or down the company's own model answers and the result says `fallback`. Each call goes through `metered`,
    so a batch that reuses one engine (listening, learnings) stops at the cap call by call; each result carries
    `cost_usd`."""
    settings = store.settings
    if settings.rehearsal or over_spend_cap(c, settings):
        return None
    backup = fallback_engine(providers.load(c, settings))
    first = primary or backup
    if first is None:
        return None
    models = [J.MODEL if first is primary else getattr(backup, "model", "")]
    if first is primary and backup is not None:
        models.append(getattr(backup, "model", ""))

    def answer(state, questions, label):
        try:
            return first(state, questions, label)
        except J.JudgeError as exc:
            if first is not primary or backup is None or not exc.retryable:
                raise
            return {**backup(state, questions, label), "fallback": exc.code}

    def engine(state, questions, label=None):
        return metered(store, models, state, questions, label, lambda: answer(state, questions, label))
    engine.model = models[0]
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
            engine = None if capped else server_engine(store, c, app.state.judge)
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
                H.event(c, who.actor, "judge.call", label, {**detail, "error": J.error_code(exc)})
            if exc.code == "spend_cap":
                raise Problem("spend_cap", exc.detail + ". Carry on without it.", 429)
            status = 503 if exc.retryable else (422 if exc.code == "invalid" else 502)
            raise Problem("judge_" + exc.code, exc.detail, status)
        with store.transaction() as c:
            H.event(c, who.actor, "judge.call", label, {
                **detail, **audit_detail(result), "answers": J.summary(result["answers"])})
        return {"model": result["model"], "answers": result["answers"], "usage": result.get("usage") or {},
                "ms": result["ms"], "label": body.label}
