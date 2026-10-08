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
the events, and a call over it is a 429 that says when to try again.
"""

import os
from typing import Any

from fastapi import Request
from pydantic import Field

from clients import judge as J
from . import providers
from .models import Contract
from .store import H, Problem

DAILY_CALLS = {"owner": 20_000, "human": 5_000, "bot": 5_000}
# TypeSafe gets one short try from the server; when it is slow or down, the company's own model answers, so a
# caller never waits through several long attempts only to receive a 503.
JEV_TIMEOUT = 8


def fallback_engine(record, env=None):
    """A decision model on the company's own provider, or None when no enabled provider has a server key.

    The default model's provider goes first; the rest follow in the order the owner enabled them."""
    env = os.environ if env is None else env
    default = providers.runtime_provider(record["runtime"])
    for provider in dict.fromkeys(([default] if default else []) + record["enabled"]):
        key = (env.get(J.LLM_KEYS.get(provider, "")) or "").strip()
        model = record["model"] if provider == default else (providers.recommended(provider) or {}).get("id")
        if key and model:
            return J.llm(provider, key, model)
    return None


class Ask(Contract):
    state: Any
    questions: dict[str, dict] = Field(min_length=1, max_length=J.MAX_QUESTIONS)
    label: str | None = Field(default=None, max_length=J.MAX_LABEL)


def used_today(c, actor, at=None):
    day = (at or H.now())[:10]
    return c.execute("SELECT count(*) FROM events WHERE actor=? AND action='judge.call' AND ts>=?",
                     (actor, day + "T00:00:00")).fetchone()[0]


def install_judge(app, store, auth):
    settings = store.settings
    app.state.judge = J.direct(settings.typesafe_api_key, timeout=JEV_TIMEOUT, retries=0) if settings.typesafe_api_key and not settings.rehearsal else None

    @app.get("/api/v2/judge")
    def config(request: Request):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            used = used_today(c, who.actor)
            engine = None if settings.rehearsal else app.state.judge or fallback_engine(providers.load(c, settings))
        return {"configured": engine is not None, "model": (J.MODEL if app.state.judge else getattr(engine, "model", "")) if engine else "",
                "daily_calls": DAILY_CALLS[who.role], "used_today": used,
                "max_questions": J.MAX_QUESTIONS, "max_state_chars": J.MAX_STATE_CHARS}

    @app.post("/api/v2/judge")
    def ask(request: Request, body: Ask):
        who = request.state.identity
        auth.domain(who)
        if settings.rehearsal:
            raise Problem("rehearsal", "Decisions are off in rehearsal mode; nothing is sent to a provider", 503)
        with store.read() as c:
            engine = app.state.judge or fallback_engine(providers.load(c, settings))
        if engine is None:
            raise Problem("judge_unconfigured", "No decision model is configured on this server: set a TypeSafe key, "
                          "or the API key of one of the team's AI providers", 503)
        try:
            J.validate(body.state, body.questions, body.label)
        except J.JudgeError as exc:
            raise Problem("invalid", exc.detail, 422)
        cap = DAILY_CALLS[who.role]
        with store.read() as c:
            used = used_today(c, who.actor)
        if used >= cap:
            raise Problem("budget", f"{who.actor} has made {used} decision calls today; the cap is {cap} "
                          "until midnight UTC", 429)
        label = body.label or ""
        detail = {"label": label, "questions": len(body.questions),
                  "types": sorted({q["type"] for q in body.questions.values()})}
        try:
            try:
                result = engine(body.state, body.questions, body.label)
            except J.JudgeError as exc:
                with store.read() as c:
                    backup = fallback_engine(providers.load(c, settings)) if engine is app.state.judge and exc.retryable else None
                if backup is None:
                    raise
                detail["fallback"] = exc.code
                result = backup(body.state, body.questions, body.label)
        except J.JudgeError as exc:
            with store.transaction() as c:
                H.event(c, who.actor, "judge.call", label, {**detail, "error": exc.code, "detail": exc.detail[:300]})
            status = 503 if exc.retryable else (422 if exc.code == "invalid" else 502)
            raise Problem("judge_" + exc.code, exc.detail, status)
        with store.transaction() as c:
            H.event(c, who.actor, "judge.call", label, {
                **detail, "ms": result["ms"], "model": result["model"], "usage": result.get("usage") or {},
                "answers": J.summary(result["answers"])})
        return {**result, "label": body.label}
