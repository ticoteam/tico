"""Decisions as a Tico tool: `POST /api/v2/decisions`, the door behind `hub_decision_ask` and `hub decision ask`
(the route, the `judge.call` audit events and this module keep their old name, `judge`).

The server asks its providers in this order, each failure falling through to the next (fail open), with the same keys
the Slack gateway routes with:
1. the Tico AI Gateway as `tico-decide-1.0`, with the gateway key (`TICO_GATEWAY_KEY` or `TICO_GATEWAY_SECRET_ARN`, at
   `TICO_GATEWAY_URL`, Tico HQ's decisions endpoint by default), else the key and URL saved in Settings > AI providers
   (backend/gateway_connection.py), read at each call;
2. a customer's own TypeSafe key (`TYPESAFE_API_KEY` or `TICO_TYPESAFE_SECRET_ARN`), never set by default;
3. the team's own AI provider, when the server has that provider's API key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
   `GEMINI_API_KEY`, `XAI_API_KEY`, `OPENROUTER_API_KEY`);
4. off: with none of them it is unconfigured, and callers carry on without it. A bot or a person sends a JSON state and typed
questions with their own credential and gets calibrated answers back; no bot ever carries the
key, and nothing here decides anything with the answers. The contract and the validation are
`clients/judge.py`, shared with the direct transport, so a call that this route would refuse is
refused the same way before it is sent.

What is kept: one `judge.call` event per call with the label, the question count, the value and
confidence per answer, the provider and model that answered, the token usage and the latency. Never the state: mail
bodies and transcripts pass through here, and the audit is not the place for them. The label
(`questions/README.md`, a set's `id@version`) is how a month of calls is read back per decision.

A budget per actor and day, so a loop cannot spend the key: the cap is on calls, counted from
the events, and a call over it is a 429 that says when to try again. And a spend cap for the team per UTC month
(`TICO_DECISIONS_MONTHLY_USD`, $50 by default), summed from each call's `cost_usd` in the audit: at the cap every
server-side caller (this route, listening, learnings, live meetings, the Slack gateway) carries on as if Decide were
off. Each of them gets its model from `server_engine`, so Jev's one short try and the fallback are the same for all. The caps per role are settings
(`TICO_DECISIONS_DAILY_CALLS_OWNER`, `_HUMAN`, `_BOT`); the Slack gateway's routing counts against the
sender's, and the market ask (`market.ask` events) has its own count under the same caps.

`GET /api/v2/decisions/readout` reads the audit back per question set: calls, cost, Jev or fallback, answer spread
and the month's spend against the cap (backend/decide_readout.py).
"""

import json
import logging
import math
import os
import threading
from typing import Any

from fastapi import Request
from pydantic import Field

from clients import gateway as G
from clients import judge as J
from clients import openrouter
from . import gateway_connection, providers
from .config import Settings
from .models import Contract
from .store import H, Problem

LOG = logging.getLogger("tico.judge")
ROLES = ("owner", "human", "bot")
# The defaults; a server's own caps are `daily_cap(settings, role)`.
DAILY_CALLS = {role: getattr(Settings, "decisions_daily_calls_" + role) for role in ROLES}
# The Gateway and TypeSafe each get one short try from the server; when one is slow, down or refuses the key, the next
# provider answers, so a caller never waits through several long attempts only to receive a 503.
JEV_TIMEOUT = 8
# The most one chain of providers takes, all of them together: each is asked with what is left (`J.within`), so the
# server answers before its slowest caller stops waiting (30 s: `hub decision ask`, the MCP tools, `J.from_env`).
CHAIN_SECONDS = 25
# Tico Decide's price in USD per 1M input and output tokens (Tico AI Gateway lineup 1.0): what the team's monthly cap
# counts a Gateway call at when its answer carries no cost.
GATEWAY_USD_PER_MTOK = (0.06, 0.0)


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


def _count(value):
    """Whether `value` is a usable count or cost from a provider: a finite number, not negative."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def tokens(usage):
    """(uncached input, cached input, output) tokens from a provider's usage, whichever names it uses. A count that is
    not a finite, non-negative number is not read."""
    usage = usage if isinstance(usage, dict) else {}

    def first(*keys, within=usage):
        for key in keys:
            value = within.get(key)
            if _count(value):
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
# A call's output is at most what every transport asks for (clients/judge.py); a call in flight is held at all of it.
MAX_OUTPUT_TOKENS = J.MAX_OUTPUT_TOKENS


def priced(settings, model):
    """Whether `model` has a known price: a Tico model at `GATEWAY_USD_PER_MTOK`, Jev at the settings' rates, any other
    at `providers.PRICES`."""
    if model in J.GATEWAY_MODELS:
        return True
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
        if _count(value):
            return round(float(value), 6)
    fresh, cached, out = tokens(usage)
    if model in J.GATEWAY_MODELS:
        rate_in, rate_out = GATEWAY_USD_PER_MTOK
    elif priced(settings, model) and str(model or "").lower().startswith("jev"):
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
    """The most one call can cost: all of its input and MAX_OUTPUT_TOKENS of output at each of `models`, the providers
    a chain asks in turn. Each can be paid: an answer that comes back unusable still costs, and the next is asked."""
    size = len(json.dumps(state, default=str)) + len(json.dumps(questions, default=str))
    usage = {"input_tokens": size // 3 + 1000, "output_tokens": MAX_OUTPUT_TOKENS}
    return sum(cost_usd(settings, model, usage) for model in models or [""])


def metered(store, models, state, questions, label, call):
    """One paid decision call under the team's monthly cap, for every caller (server and Slack gateway alike).

    Under one lock per process: read the month's spend from the audit, add the reservations of calls in flight here
    and this call's own most, refuse `spend_cap` if that is over the cap, else reserve it. After the call its real cost
    is written as `judge.spend` rows before the reservation is released, so the next check sees one or the other: one
    for the answer, and one for each answer before it that came back unusable (`paid`), even when no step answered.
    Another process (the Slack gateway's container) sees this one's calls once they are written, and admits its own
    only if they fit too; so the cap can be passed by at most one process's calls in flight, each at most its
    reservation. That bound is for the two processes that ask today, the server and the Slack gateway: each further
    process with its own meter can add its own calls in flight on top. A call stays within its reservation because
    every transport caps the answer at MAX_OUTPUT_TOKENS; a provider's own reported cost can still be higher."""
    settings = store.settings
    cap = float(settings.decisions_monthly_usd or 0)
    reserved = reservation(settings, models, state, questions) if cap > 0 else 0.0
    with _METER:
        if cap > 0:
            with store.read() as c:
                spent = spent_this_month(c)
            if spent + _RESERVED[0] + reserved > cap:
                _skip("every caller", f"the month's decision spend ${spent:.2f} leaves no room under the ${cap:.2f} cap")
                raise J.JudgeError("spend_cap", f"Decide has cost ${spent:.2f} this month; the team's cap is "
                                   f"${cap:.2f} until the 1st (UTC)", 429)
        _RESERVED[0] += reserved
    try:
        try:
            result = call()
        except J.JudgeError as exc:
            if getattr(exc, "paid", None):
                exc.cost_usd = _spend(store, label, exc.paid, state, questions)
            raise
        result = dict(result)
        paid = result.pop("paid", None) or []
        return {**result, "cost_usd": _spend(store, label, paid + [result], state, questions)}
    finally:
        with _METER:
            _RESERVED[0] = max(0.0, _RESERVED[0] - reserved)


def _spend(store, label, attempts, state, questions):
    """One `judge.spend` row per paid attempt (the answer is the last; any before it were unusable), in one
    transaction. Returns their total USD. An attempt whose usage gives neither a cost nor any tokens is counted at its
    most (`reservation`), not as nothing, and says `usage: unknown`."""
    settings, total = store.settings, 0.0
    with store.transaction() as c:
        for attempt in attempts:
            model, usage = attempt.get("model"), attempt.get("usage")
            known = _own_cost(attempt) or any(tokens(usage))
            cost = cost_usd(settings, model, usage) if known else reservation(settings, [model], state, questions)
            total += cost
            H.event(c, H.KEEPER, "judge.spend", label or "", {
                "label": label, "model": model, "cost_usd": cost,
                **({} if "answers" in attempt else {"unusable": True}),
                **({} if known else {"usage": "unknown"}),
                **({} if priced(settings, model) or _own_cost(attempt) else {"price": "ceiling"})})
    return round(total, 6)


def _own_cost(result):
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    return any(_count(usage.get(k)) for k in ("cost_usd", "cost"))


def gateway(settings, store=None, vault=None):
    """The Tico AI Gateway as the server asks it: one try of at most `J.GATEWAY_TIMEOUT` seconds. The environment's key
    wins; without one, and with a `store`, the key saved in Settings > AI providers, read at each call so saving or
    clearing it counts at once (`saved` marks that step; `live` leaves it out while no key is saved). None in rehearsal
    or with neither."""
    if settings.rehearsal:
        return None
    if settings.gateway_key:
        return J.gateway(settings.gateway_key, settings.gateway_url) if settings.gateway_url else None
    if store is None:
        return None

    def engine(state, questions, label=None):
        from .credentials import Vault
        keys = vault or Vault(store)
        with store.read() as c:
            secret = gateway_connection.key(c, keys, settings) if keys.configured else ""
            base = gateway_connection.url(c, settings)
        if not secret:
            raise J.JudgeError("unconfigured", "no Tico AI Gateway key is saved")
        return J.gateway(secret, G.decisions_url(base))(state, questions, label)
    engine.model, engine.provider, engine.saved = J.GATEWAY_MODEL, J.GATEWAY_PROVIDER, True
    return engine


def live(primary, c, settings):
    """`primary`'s steps that can ask now: the saved-key Gateway step only while a key is saved in Settings."""
    steps = list(getattr(primary, "steps", None) or [primary]) if primary else []
    saved = gateway_connection.source(c, settings) == "settings"
    return [step for step in steps if saved or not getattr(step, "saved", False)]


def jev(settings):
    """A customer's own TypeSafe key as the server asks it: one try of at most JEV_TIMEOUT seconds. None without one."""
    if not settings.typesafe_api_key or settings.rehearsal:
        return None
    return J.direct(settings.typesafe_api_key, timeout=JEV_TIMEOUT, retries=0)


def ordered(steps):
    """One engine that asks each of `steps` in turn and returns the first answer, or None when there are none.

    The request is checked against the contract once, first: a request the contract refuses is `invalid` and no
    provider is asked. After that every failure moves on to the next step (fail open), a provider's own 422 too: the
    request was valid, so that refusal is the provider's. All of them answer within CHAIN_SECONDS together (`J.within`).
    An answer after a failure says `fallback` with the first failure's code, and `paid` lists the unusable answers
    before it (`metered` counts them); when every step fails, the last error is raised, carrying all of `paid`. The
    engine keeps its `steps`, so `server_engine` can put the company's own model after them."""
    steps = [step for step in steps if step is not None]
    if len(steps) < 2:
        return steps[0] if steps else None

    def engine(state, questions, label=None):
        J.validate(state, questions, label)
        failed, paid = None, []
        with J.within(CHAIN_SECONDS):
            for i, step in enumerate(steps):
                try:
                    result = step(state, questions, label)
                except J.JudgeError as exc:
                    paid += getattr(exc, "paid", None) or []
                    if i == len(steps) - 1:
                        exc.paid = paid
                        raise
                    failed = failed or J.error_code(exc)
                    continue
                result = {**result, "fallback": failed} if failed else result
                return {**result, "paid": paid} if paid else result
    engine.steps = steps
    engine.model, engine.provider = step_models(steps)[0], getattr(steps[0], "provider", "")
    return engine


def step_models(steps, default=J.MODEL):
    """The model each of `steps` asks as (`default` for one that does not say: a test's fake)."""
    return [getattr(step, "model", "") or default for step in steps]


def primary_engine(settings, store=None, vault=None):
    """The server's own providers before the company's model: the Gateway (the environment's key, else with a `store`
    the one saved in Settings), then a customer's own TypeSafe key."""
    return ordered([gateway(settings, store, vault), jev(settings)])


def server_engine(store, c, primary=None):
    """The decision model every server-side caller uses, or None for "carry on as if Decide were off": rehearsal, no
    model at all, or the month's spend at the cap. `primary` is the Gateway and the own TypeSafe key
    (`primary_engine(settings)`, or the test's fake); when they are slow, down or refused, the company's own model answers
    and the result says `fallback`. Each call goes through `metered`, so a batch that reuses one engine (listening,
    learnings) stops at the cap call by call; each result carries `cost_usd`."""
    settings = store.settings
    if settings.rehearsal or over_spend_cap(c, settings):
        return None
    backup = fallback_engine(providers.load(c, settings))
    steps = live(primary, c, settings)
    chain = ordered(steps + [backup])
    if chain is None:
        return None
    asks_as = step_models(steps) + ([getattr(backup, "model", "")] if backup is not None else [])

    def engine(state, questions, label=None):
        return metered(store, asks_as, state, questions, label, lambda: chain(state, questions, label))
    engine.model, engine.provider = asks_as[0], getattr(chain, "provider", "")
    return engine


# What a caller is told when the call fails: a fixed sentence per code, never the provider's text, which can carry more
# than the caller's own request.
FAILED = {
    "auth": "The decision model refused the server's key",
    "http": "The decision model answered with an error",
    "invalid": "The decision model refused the request; check the state and questions against the contract",
    "not_found": "The decision model was not found",
    "shape": "The decision model's answer could not be read",
    "spend_cap": "Decide has reached the team's monthly spend cap until the 1st (UTC). Carry on without it.",
    "unavailable": "The decision model did not answer; try again shortly",
    "unconfigured": "No decision model is configured on this server",
    "unreadable": "The decision call failed",
}


def audit_detail(result):
    """What a `judge.call` row keeps of one answer: provider, model, tokens, latency, cost and any fallback. Never the
    state."""
    detail = {"ms": result.get("ms"), "model": result.get("model"), "usage": result.get("usage") or {},
              "cost_usd": result.get("cost_usd")}
    if result.get("provider"):
        detail["provider"] = result["provider"]
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
    app.state.judge = primary_engine(settings, store, getattr(app.state, "vault", None))

    @app.get("/api/v2/judge")
    def config(request: Request):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            used = used_today(c, who.actor)
            steps = [] if settings.rehearsal else live(app.state.judge, c, settings)
            first = steps[0] if steps else None if settings.rehearsal else fallback_engine(providers.load(c, settings))
            spent = spent_this_month(c)
        engine = first
        return {"configured": engine is not None, "model": step_models([first])[0] if first else "",
                "provider": getattr(first, "provider", "") if first else "",
                "daily_calls": daily_cap(settings, who.role), "used_today": used,
                "monthly_usd": float(settings.decisions_monthly_usd or 0), "spent_this_month_usd": round(spent, 4),
                "max_questions": J.MAX_QUESTIONS, "max_state_chars": J.MAX_STATE_CHARS}

    @app.get("/api/v2/judge/readout")
    def readout(request: Request, days: int = 7):
        """The Decide readout over the last `days` (backend/decide_readout.py): the same readers as the config above,
        since it names no caller, no state and no free-text label."""
        from .decide_readout import MAX_DAYS, readout as build
        who = request.state.identity
        auth.domain(who)
        if not 1 <= days <= MAX_DAYS:
            raise Problem("invalid", f"days is 1 to {MAX_DAYS}", 422)
        with store.read() as c:
            return build(c, settings, days)

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
            raise Problem("judge_unconfigured", "No decision model is configured on this server: set a Tico AI Gateway "
                          "key, your own TypeSafe key, or the API key of one of the team's AI providers", 503)
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
                H.event(c, who.actor, "judge.call", label, {
                    **detail, "error": J.error_code(exc),
                    **({"cost_usd": exc.cost_usd} if getattr(exc, "cost_usd", None) else {})})
            code = J.error_code(exc)
            if code == "spend_cap":
                raise Problem("spend_cap", FAILED[code], 429)
            status = 503 if exc.retryable else (422 if code == "invalid" else 502)
            raise Problem("judge_" + code, FAILED[code], status)
        with store.transaction() as c:
            H.event(c, who.actor, "judge.call", label, {
                **detail, **audit_detail(result), "answers": J.summary(result["answers"])})
        return {"model": result["model"], "answers": result["answers"], "usage": result.get("usage") or {},
                "ms": result["ms"], "label": body.label}
