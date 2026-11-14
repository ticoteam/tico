"""The model side of the judge matrix. The server's judge (backend/pipeline_judges.py `judge_once`) judges each labeled case
as it does in production: the state is built by the server's own `_state`, and the evidence text by the server's own loader
(`text_of_bytes`, the one the server's read uses), from the case's text as the bytes Tico would hold. This file supplies only
the model call, through the same Gateway clients the server uses.

The only judge model is Jev, Decide Low (clients/judge.py `gateway`, the Gateway's Low model), the route the server uses.
It needs TICO_GATEWAY_KEY; the URL is the server's own (backend/config.py `_gateway_url`). Nothing calls Anthropic
directly. Costs are the Gateway's `cost_usd` when it gives one, else tokens at PRICES. PRICES is an estimate from the
Gateway lineup (docs/ai-gateway.md)."""

import hashlib
import json
import os
import sqlite3
from contextlib import closing

from backend import pipeline_judges as P
from clients import judge as J

MODELS = tuple(P.MODELS)
PRICES = {"jev": (0.06, 0.0)}
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


def _page_job(case):
    """A page case (attachment kind "page"): the requirement and attachment the server's Jev step builds for a page
    (backend/page_verdicts.py `job_of`), from the case's page text as the Markdown bytes Tico holds. The work's `goal` is
    the pipeline's goal, its `context` what the page is judged against (task_pages.judged_against: the task, the
    linked PRs' heads for a review at the newest commit, the release for a Validate page), and its repo, PR and author
    the one PR the task links."""
    from backend import page_verdicts as V

    w, att = case["work"], case["attachment"]
    data = (att.get("text") or "").encode()
    text, cut = P.text_of_bytes("text/markdown", data, len(data))
    task = {
        "title": w.get("title") or "",
        "body": w.get("spec") or "",
        "version": int(w["work_version"]),
        "pipeline": {"goal": w.get("goal") or ""},
        "pipeline_id": None,
    }
    page = {"file_id": f"file-{case['id']}", "version": 1, "digest": hashlib.sha256(data).hexdigest()}
    judged = {"rubric": case["requirement"]["rubric"], "context": w.get("context") or {}}
    link = None
    if w.get("repo") and w.get("pr"):  # the one PR the task links, as its task_links row holds it
        link = {
            "repo": w["repo"],
            "number": w["pr"],
            "detail": json.dumps({"author_login": w["author"]}) if w.get("author") else None,
        }
    return V.job_of(_requirement(case), task, page, text, cut, judged, link)


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


def _job(case):
    """(requirement, attachment) as the server asks Jev for this case: a page case through the page step's own builder,
    any other through the attachment read."""
    if (case.get("attachment") or {}).get("kind") == "page":
        return _page_job(case)
    return _requirement(case), _attachment(case)


def state_for(case):
    """The state the judge is sent for a case: the server's `_state` over the case's attachment."""
    return P._state(*_job(case))


def _decide(name, key, url):
    """Ask Jev on the Gateway, as the server does; its answer carries the Gateway's usage."""
    ask = J.gateway(key, url, model=J.GATEWAY_MODEL, timeout=J.GATEWAY_TIMEOUT)

    def engine(model, state, questions, label):
        # Read as the server's live engine reads it (pipeline_judges._read): every asked question, so a check asked in
        # parts gives its parts, and an answer missing one is a failed call (JudgeError "shape").
        result = ask(state, questions, label)
        return {
            **P._read(result, list(questions), model, cost_usd(name, result.get("usage"))),
            "provider": result.get("provider") or J.GATEWAY_PROVIDER,
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
    return _decide(name, key, url)


def judge_once(engine, case):
    """(probability the requirement is met, cost in USD, milliseconds, the server's verdict) for one case, through the
    server's `judge_once` with `engine` as the model call. The verdict is the one the server gives (each part on its
    check's bar, unsure when Jev could not see the whole page), never p banded again. Raises J.JudgeError when the model
    call fails: the caller counts that as unsure."""
    failed: list[str] = []

    def ask(name, state, questions, label):
        try:
            return engine(name, state, questions, label)
        except J.JudgeError as exc:
            failed.append(J.error_code(exc))
            raise

    with closing(sqlite3.connect(":memory:")) as c:  # judge_once takes a connection; the judge reads nothing from it
        result = P.judge_once(c, *_job(case), engine=ask)
    if failed or result["verdict"] == "failed":
        raise J.JudgeError(failed[0] if failed else "unreadable", "the model call failed")
    return float(result["p"]), float(result["cost_usd"]), int(result["latency_ms"]), result["verdict"]
