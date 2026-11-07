"""The model side of the judge matrix. The server's judge (backend/pipeline_judges.py `judge_once`) judges each labeled case
as it does in production; this file supplies only the model call, through the same Gateway clients the server uses, and
adds the case's work and evidence text to the state (the server's state carries a description, not the file's contents).

A model name picks its Gateway route, the one the server uses for it:

    jev         Decide Low (clients/judge.py `gateway`, the Gateway's Low model)
    sonnet      Decide Medium (the Gateway's Medium language model)
    haiku-5.5   the Gateway's Low chat tier, written as backend/pipeline_judges.py writes it (`gateway_writer`)

All three need TICO_GATEWAY_KEY; the URL is the server's own (backend/config.py `_gateway_url`). Nothing calls Anthropic
directly. Costs are the Gateway's `cost_usd` when it gives one, else tokens at PRICES. PRICES are estimates: Haiku's and
Jev's are the Gateway lineup's (docs/ai-gateway.md), and Medium's is an assumption."""

import hashlib
import io
import json
import os
import sqlite3
import time
from contextlib import closing

from backend import pipeline_judges as P
from clients import gateway as G
from clients import judge as J

MODELS = tuple(P.MODELS)
PRICES = {"jev": (0.06, 0.0), "haiku-5.5": (0.15, 0.75), "sonnet": (3.0, 15.0)}
QUESTION_SET = P.QUESTION_SET
QUESTIONS = P._questions()  # the server's question set, not a copy


def cost_usd(name, usage):
    """What the call cost: the Gateway's own figure when it gives one, else tokens at PRICES."""
    usage = usage or {}
    if isinstance(usage.get("cost_usd"), (int, float)):
        return float(usage["cost_usd"])
    low, high = PRICES.get(name, (0.0, 0.0))
    tokens_in = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
    tokens_out = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
    return (tokens_in * low + tokens_out * high) / 1_000_000


def _requirement(case):
    req = case["requirement"]
    return {"id": f"req-{case['id']}", "name": req["name"], "rubric": req["rubric"], "pipeline_id": None}


def _attachment(case):
    att = case.get("attachment") or {}
    text = att.get("text") or ""
    return {
        "id": f"att-{case['id']}",
        "source": f"{att.get('kind', 'none')}: {att.get('name', '')}".rstrip(": "),
        "digest": hashlib.sha256(text.encode()).hexdigest()[:16] if text else "",
        "url": "",
        "file_version": 1,
        "work_version": int(att.get("work_version") or 0),
        "task_work_version": int(case["work"]["work_version"]),
    }


def _with_evidence(state, case):
    """The server's state plus what a description of the evidence and the work would add: the work's fields, and the
    attachment's text. Nothing is added that a person could not see."""
    att = case.get("attachment") or {}
    work = {k: v for k, v in case["work"].items() if k != "work_version"}
    return {
        **state,
        "work": {**state["work"], **work},
        "attachment": {**state["attachment"], "text": att.get("text") or "(nothing attached)"},
    }


def state_for(case):
    """The state the judge is sent for a case (what `judge_once` builds, plus the evidence)."""
    return _with_evidence(P._state(_requirement(case), _attachment(case)), case)


def _decide(name, key, url):
    """Decide at the level the server uses for this model; its answer carries the Gateway's usage."""
    level = P.DECIDE_LEVELS[name]
    ask = J.gateway(key, url, model=J.decide_model(level), timeout=J.decide_timeout(level))

    def engine(model, state, questions, label):
        result = ask(state, questions, label)
        return {
            "p": result["answers"]["met"]["noul"],
            "reason": "",
            "provider": result.get("provider") or J.GATEWAY_PROVIDER,
            "model": result.get("model") or "",
            "cost_usd": cost_usd(name, result.get("usage")),
            "latency_ms": result.get("ms") or 0,
        }

    return engine


def _haiku(key, url):
    """Haiku on the Gateway's Low chat tier. The writer keeps no usage on a good answer, so the response is read here
    first and its usage kept; the body is then handed back unchanged."""
    from backend import gateway_tiers

    model = gateway_tiers.default_model("low")
    usage: dict = {}

    def opener(request, timeout):
        with J.gateway_open(request, timeout) as response:
            body = response.read()
        payload = json.loads(body or b"{}")
        usage.clear()
        usage.update(J.clean_usage(payload.get("usage")) if isinstance(payload, dict) else {})
        return io.BytesIO(body)

    write = J.gateway_writer(lambda: key, G.chat_base(url), model, timeout=P.GATEWAY_TIMEOUT, opener=opener)

    def engine(_model, state, questions, label):
        usage.clear()
        started = time.monotonic()
        parsed = write(P.JUDGE_SYSTEM, json.dumps(state, sort_keys=True, default=str))
        return {
            "p": parsed.get("p"),
            "reason": " ".join(str(parsed.get("reason") or "").split()),
            "provider": J.GATEWAY_PROVIDER,
            "model": write.model,
            "cost_usd": cost_usd("haiku-5.5", usage),
            "latency_ms": round((time.monotonic() - started) * 1000),
        }

    return engine


def build_engine(name, env=None):
    """The model call for `name` (the server's ENGINE shape), or None when the environment has no Gateway key."""
    if name not in MODELS:
        raise ValueError(f"unknown model {name!r}; choose from {', '.join(MODELS)}")
    env = os.environ if env is None else env
    key = (env.get("TICO_GATEWAY_KEY") or "").strip()
    if not key:
        return None
    from backend.config import _gateway_url

    url = _gateway_url()
    return _haiku(key, url) if name == "haiku-5.5" else _decide(name, key, url)


def judge_once(engine, case, model=""):
    """(probability the requirement is met, cost in USD, milliseconds) for one case, through the server's `judge_once`
    with `engine` as the model call. Raises J.JudgeError when the model call fails: the caller counts that as unsure."""
    failed: list[str] = []

    def ask(name, state, questions, label):
        try:
            return engine(name, _with_evidence(state, case), questions, label)
        except J.JudgeError as exc:
            failed.append(J.error_code(exc))
            raise

    with closing(sqlite3.connect(":memory:")) as c:  # judge_once reads the connection only to pick a model, and it is given
        result = P.judge_once(c, _requirement(case), _attachment(case), model, engine=ask)
    if failed:
        raise J.JudgeError(failed[0], "the model call failed")
    return float(result["p"]), float(result["cost_usd"]), int(result["latency_ms"])
