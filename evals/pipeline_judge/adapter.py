"""The stand-in for Track B's seam (`backend/pipeline_judges.judge_once`): one requirement judged by one model, through the
same Decide client the server uses (clients/judge.py). Delete this file when Track B lands and call judge_once from run.py.

A model name picks the transport, first match wins for what the environment has:

    jev          the Tico AI Gateway at Decide Low (TICO_GATEWAY_KEY, TICO_GATEWAY_URL for a dev Gateway)
    haiku-5.5    Anthropic directly (ANTHROPIC_API_KEY), claude-haiku-5-5
    sonnet       Anthropic directly (ANTHROPIC_API_KEY), claude-sonnet-5-5

Prices are USD per million input and output tokens. Haiku's and Jev's are the Gateway lineup's (docs/ai-gateway.md); Sonnet's is
an assumption: edit PRICES before trusting its cost column."""

import os
import time

from clients import judge as J

MODELS = ("jev", "haiku-5.5", "sonnet")
ANTHROPIC = {"haiku-5.5": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"}
PRICES = {"jev": (0.06, 0.0), "haiku-5.5": (0.15, 0.75), "sonnet": (3.0, 15.0)}

QUESTION_SET = "pipeline-requirement"
QUESTIONS = {
    "met": {
        "type": "noul",
        "instructions": {
            "question": "The `attachment` meets the `requirement`, for the work as it is now.",
            "focus": (
                "Read `requirement.rubric` literally. `work.work_version` is the work as it is now; an attachment made for "
                "an older `work_version` is out of date and does not meet it. No attachment means not met. Judge only what "
                "the attachment says; do not assume anything it leaves out."
            ),
            "not_for": "Say about 0.5 when the attachment is cut off, unreadable, or leaves the decisive point open.",
        },
    }
}


def build_engine(name, env=None):
    """The engine for model `name`, or None when the environment lacks its key."""
    env = os.environ if env is None else env
    if name == "jev":
        key = (env.get("TICO_GATEWAY_KEY") or "").strip()
        if not key:
            return None
        from backend.config import _gateway_url

        return J.gateway(key, _gateway_url(), model=J.decide_model("low"), timeout=J.decide_timeout("low"))
    if name in ANTHROPIC:
        key = (env.get("ANTHROPIC_API_KEY") or "").strip()
        return J.llm("anthropic", key, ANTHROPIC[name], timeout=60) if key else None
    raise ValueError(f"unknown model {name!r}; choose from {', '.join(MODELS)}")


def cost_usd(name, usage):
    """What the call cost: the provider's own figure when it gives one, else tokens at PRICES."""
    usage = usage or {}
    if isinstance(usage.get("cost_usd"), (int, float)):
        return float(usage["cost_usd"])
    low, high = PRICES.get(name, (0.0, 0.0))
    tokens_in = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
    tokens_out = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
    return (tokens_in * low + tokens_out * high) / 1_000_000


def state_for(case):
    return {"requirement": case["requirement"], "work": case["work"], "attachment": case["attachment"]}


def judge_once(engine, case, model=""):
    """(probability the requirement is met, cost in USD, milliseconds). Raises J.JudgeError when the model fails."""
    started = time.monotonic()
    result = engine(state_for(case), QUESTIONS, f"{QUESTION_SET}@eval")
    answer = result["answers"]["met"]
    ms = result.get("ms") or round((time.monotonic() - started) * 1000)
    return float(answer["noul"]), cost_usd(model, result.get("usage")), ms
