"""The model side of the judge matrix. The server's judge (backend/pipeline_judges.py `judge_once`) judges each labeled case
as it does in production: the state is built by the server's own `_state`, and the evidence text by the server's own loader
(`text_of_bytes`, the one the server's read uses), from the case's text as the bytes Tico would hold. This file supplies only
the model call, through the same Gateway clients the server uses.

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


# The source a production attachment row carries (requirement_attachments.source) for each case kind.
SOURCES = {"check": "check_run", "receipt": "receipt", "image": "blob"}


def _work(case):
    """The work's fields as the server's waiting read gives them (task_title, task_spec, task_repo, task_pr, task_author and
    task_work_version), from the case's work. Its repo, PR and author come as production's do: from a PR link row, through
    the server's own reader (`pr_work`), with the author as the GitHub login on the link's detail. A case's work has no PR
    or author it does not name: those stay out. An author with no PR has no source in production, so it is refused."""
    w = case["work"]
    if w.get("author") and w.get("repo") is None and w.get("pr") is None:
        raise ValueError(f"{case['id']}: an author is known only from a PR link; give the work a repo and PR")
    link = None
    if w.get("repo") is not None or w.get("pr") is not None:
        detail = json.dumps({"author_login": w["author"]}) if w.get("author") else None
        link = {"repo": w.get("repo"), "number": w.get("pr"), "detail": detail}
    return {
        "task_work_version": int(w["work_version"]),
        "task_title": w.get("title") or "",
        "task_spec": w.get("spec") or "",
        **P.pr_work(link),
    }


def _attachment(case):
    """The attachment as the server reads it from a Tico row. The case's text stands for the bytes Tico would hold, under the
    MIME type its kind carries, and the server's loader gives the text the judge is sent: none for an image (Tico reads no
    text from one, so the judge sees only its metadata and answers unsure), the words of a text otherwise, cut at the cap.
    A case with no attachment has no row, so it has no id: the judge is asked about no evidence. Either way the work's
    fields (_work) travel with it, as the server's read puts them on the row."""
    att = case.get("attachment")
    if att is None:
        return _work(case)
    kind = att.get("kind")
    data = (att.get("text") or "").encode()
    text, cut = P.text_of_bytes("image/png" if kind == "image" else "text/plain", data, len(data))
    return {
        "id": f"att-{case['id']}",
        "source": SOURCES.get(kind, "file"),
        "digest": hashlib.sha256(data).hexdigest()[:16],
        "url": "",
        "file_version": 1,
        "work_version": int(att.get("work_version") or 0),
        **_work(case),
        "text": text,
        "truncated": cut,
    }


def state_for(case):
    """The state the judge is sent for a case: the server's `_state` over the case's attachment."""
    return P._state(_requirement(case), _attachment(case))


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
            return engine(name, state, questions, label)
        except J.JudgeError as exc:
            failed.append(J.error_code(exc))
            raise

    with closing(
        sqlite3.connect(":memory:")
    ) as c:  # judge_once reads the connection only to pick a model, and it is given
        result = P.judge_once(c, _requirement(case), _attachment(case), model, engine=ask)
    if failed:
        raise J.JudgeError(failed[0], "the model call failed")
    return float(result["p"]), float(result["cost_usd"]), int(result["latency_ms"])
