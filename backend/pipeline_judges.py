# pyright: strict
"""How Jev judges a piece of evidence against a requirement: one attachment, one rubric, one model, one probability.

`judge_once` is the seam. It asks a model for p, the probability the attachment meets the rubric, and the bands decide:
p >= 0.75 is met, p <= 0.2 is not met, anything between is unsure. Every verdict carries a reason: Jev answers a
probability and no prose, so the reason is Tico's own words for the band and the rubric (`reason_for`). A call that
did not give an answer (an error, a timeout, an answer that cannot be read, no model, the team's Decide cap) is not a
verdict at all: it is `failed`, with what went wrong as its reason, and the caller retries it (page_verdicts). Nothing
unsure or failed passes work.

The judge model is Jev alone: Decide Low (`tico-decide-low`), by Chris's decision on Nov 9. No pipeline picks another
model. Jev goes through judge.server_engine, so its spend counts against the team's Decide cap.

A page requirement's verdicts call it (backend/page_verdicts.py, through `judge_many`), and the eval
(evals/pipeline_judge). The model is never asked while a write transaction is open.

`judge_many` asks about several (requirement, attachment) pairs at once: one page attached to several requirements is
read once, one call with a `met` question per requirement (the same input asked every question once, as the rule check
does), and up to GROUP pages share one call (backend/decide_groups.py). Each pair gets back only its own answer, read
exactly as `judge_once` reads one.
"""

import hashlib
import json
import logging
import math
import sqlite3
from typing import Any, TYPE_CHECKING, cast

from clients import judge as clients_judge

from . import decide_groups as DG
from . import file_library
from . import hubdb
from . import judge as server_judge

# These modules are untyped: through Any, their calls are Any here, not partially unknown (as in stranded_resume.py).
H: Any = hubdb
J: Any = clients_judge
judge: Any = server_judge
files: Any = file_library
JudgeError = clients_judge.JudgeError

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth
    from .config import Settings
    from .store import Store

LOG = logging.getLogger("tico.pipeline_judges")

# The judge model: the name -> the Gateway's model id. Jev is the only judge.
MODELS: dict[str, str] = {"jev": "tico-decide-low"}
DEFAULT_MODEL = "jev"
QUESTION_SET = "pipeline-requirement"
# The bar: met at 0.75 or more, not met at 0.2 or less. It lives here and nowhere else (the eval imports it), and no
# company or pipeline stores its own. Was 0.8 to Nov 11: at 0.75 the whole eval set had no false met, good pages unsure
# went from 4 of 30 to 1 of 30, and the highest not-met case was 0.43 (Chris, Nov 11).
MET_AT, NOT_MET_AT = 0.75, 0.2
REASON_BYTES = 500
# How much of the evidence's text and the task's spec Jev is sent. Jev reads 32k tokens of state (clients/judge.py
# MAX_STATE_CHARS: 120,000 characters of JSON, about four a token), and the two share it with the rest of the state
# (the rubric, the goal, the title, the commits and releases: a few thousand). 40,000 + 12,000 is under half of it, so
# even dense text (three characters a token) fits with room, and a page a bot writes (5,000 to 15,000 characters) is
# read whole. A longer text is cut and flagged truncated; `_state` cuts further if escaping would pass Jev's limit.
# Was 6,000 each (Nov 7, never measured): a normal 6,500-character spec came back unsure (rc.5, Nov 11).
TEXT_CHARS = 40_000
SPEC_CHARS = 12_000
STATE_CHARS = clients_judge.MAX_STATE_CHARS * 3 // 4  # the most JSON `_fit` lets through: a quarter of Jev's to spare
NO_EVIDENCE = "No evidence is attached."
# What a person sees as the reason when the judge was not shown all of the evidence (Fix L2-2).
CANNOT_SEE = "Tico could not read all of this evidence, so a person decides."
# What the owner is told when the text was too long for Jev: what to do, so it shortens it rather than attaching it again.
TOO_LONG = "This {what} is too long for Jev to read: keep it under {chars:,} characters."
# What a person sees when the judge said met on a spec it was shown only the start of: the rest may hold what is missing.
SPEC_CUT = "The judge saw only the start of this work's spec, so a person decides."
# The engine a test or a harness may set: (model, state, questions, label) -> {p, reason, provider, model, cost_usd,
# latency_ms}. None, with no installed store, asks no one, so every call fails until a caller sets one.
ENGINE: "Callable[[str, dict[str, Any], dict[str, Any], str], dict[str, Any]] | None" = None
# The raw engine a test may set for `judge_many`: (state, questions, label) -> the decision answer ({answers, cost_usd,
# provider, model, ms}), as judge.server_engine gives it. None asks the installed store's Jev.
ASK: "Callable[[dict[str, Any], dict[str, Any], str], dict[str, Any]] | None" = None
# Pages asked about in one call (backend/decide_groups.py). One until the replay through real Jev shows grouped verdicts
# match the ones given alone (no more than 2 in 100 changed); then DG.MOST.
GROUP = 1
# A page attached to several requirements read once, with one question per requirement. Off until the same replay shows
# the merged call gives each requirement the verdict it gets alone: the state and question wording differ from today's.
SAME_PAGE = False
# The store the server installed (install): judge_once asks the production engine on it when no engine is given.
_installed_store: Any = None


def judge_model_of(conn: sqlite3.Connection, pipeline_id: str | None) -> str:
    """The judge model a pipeline is judged by: Jev, for every pipeline."""
    return DEFAULT_MODEL


def verdict_of(p: float) -> str:
    """The band a probability falls in: met at MET_AT (0.75) or more, not_met at NOT_MET_AT (0.2) or less, unsure between."""
    if p >= MET_AT:
        return "met"
    if p <= NOT_MET_AT:
        return "not_met"
    return "unsure"


def reason_for(verdict: str, p: float, rubric: str) -> str:
    """What a verdict says when the model gave no reason (Jev never does): the band, Jev's probability and the check it
    was asked, so a person or the bot that wrote the page can see what was judged. Never empty."""
    check = " ".join(str(rubric or "").split()) or "the requirement"
    if verdict == "met":
        return f"Jev found it meets the check (p {p:.2f}): {check}"
    if verdict == "not_met":
        return f"Jev found it does not meet the check (p {p:.2f}): {check}"
    return f"Jev could not tell whether it meets the check (p {p:.2f}, between {NOT_MET_AT} and {MET_AT}): {check}"


def _probability(value: object) -> float | None:
    """A probability from a model's answer, clamped to 0..1; None when it is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        score = float(value)
    except ValueError:
        return None
    if not math.isfinite(score):
        return None
    return min(1.0, max(0.0, score))


def excerpt(text: str | None) -> tuple[str | None, bool]:
    """The first TEXT_CHARS characters of an attachment's text, and whether that cut any. None (no text) stays None."""
    if text is None:
        return None, False
    return text[:TEXT_CHARS], len(text) > TEXT_CHARS


def _reads_text(mime: str) -> bool:
    """Whether Tico reads text from this type: a text, a table or an HTML page (a Tico page). Other bytes are not loaded."""
    return mime in file_library.TEXT_TYPES or mime in file_library.TABLE_TYPES or mime == "text/html"


def pr_work(link: dict[str, Any] | None) -> dict[str, Any]:
    """The work's repo, PR and author from the PR link it is judged against, as task_repo, task_pr and
    task_author. The author is the GitHub login that opened the PR (`detail.author_login`, kept from GitHub's webhook by
    backend/github.py), or None when Tico does not know it. Never the task's owner: that is whoever holds the task now,
    who may be its reviewer. The server's read and the eval adapter both use this, so the judge is told the same."""
    link = link or {}
    raw: object = link.get("detail")
    detail: object = raw
    if isinstance(raw, (str, bytes)):
        try:
            detail = json.loads(raw)
        except ValueError:
            detail = None
    found = cast("dict[str, Any]", detail).get("author_login") if isinstance(detail, dict) else None
    author = str(found or "").strip()
    return {"task_repo": link.get("repo"), "task_pr": link.get("number"), "task_author": author or None}


def _state(requirement: dict[str, Any], attachment: dict[str, Any]) -> dict[str, Any]:
    """What the judge is told: the requirement and its rubric, the work as it is now (the task's work_revision, which
    the attachment row does not carry; its title, its spec (the description, its first SPEC_CHARS characters with
    `spec_truncated` when cut), and its repo, PR and PR author when Tico knows them), and the evidence: the version it was
    made for, what it is (a description), and its text, the first TEXT_CHARS characters with `truncated` when cut. `text`
    is null when Tico holds no text for it (an image, a video, a PDF or a link); the question set answers that as
    unreadable."""
    text, cut = excerpt(attachment.get("text"))
    spec = str(attachment.get("task_spec") or "")
    work: dict[str, Any] = {
        "work_version": int(attachment.get("task_work_version") or 0),
        "title": str(attachment.get("task_title") or ""),
        "spec": spec[:SPEC_CHARS],
    }
    if len(spec) > SPEC_CHARS:
        work["spec_truncated"] = True
    for key in ("repo", "pr", "author"):
        value = attachment.get("task_" + key)
        if value not in (None, ""):
            work[key] = value
    if attachment.get("context"):
        # What the page is judged against besides the rubric: the commits a review is for, the release it went out in.
        work["context"] = attachment["context"]
    return _fit(
        {
            "requirement": {
                "name": requirement.get("name") or "",
                "rubric": requirement.get("rubric") or "",
                "goal": requirement.get("goal") or "",  # the pipeline's goal (D58)
                # Present (true) only on a requirement that does not need evidence newer than the work's last change.
                **({"older_evidence_ok": True} if attachment.get("older_evidence_ok") else {}),
            },
            "work": work,
            "attachment": {
                "work_version": int(attachment.get("work_version") or 0),
                "source": attachment.get("source") or "",
                "digest": attachment.get("digest") or "",
                "url": attachment.get("url") or "",
                "file_version": attachment.get("file_version") or 0,
                "text": text,
                "truncated": cut or bool(attachment.get("truncated")),
            },
        }
    )


def _fit(state: dict[str, Any]) -> dict[str, Any]:
    """`state`, cut to STATE_CHARS of JSON (three quarters of Jev's limit, clients/judge.py MAX_STATE_CHARS): text that
    escapes long in JSON (quotes, new lines, letters outside ASCII, up to six characters each) can pass it under
    TEXT_CHARS. The evidence's text is cut first, then the spec, each to the longest start that fits, and flagged."""

    def size() -> int:
        return len(json.dumps(state, default=str))

    for part, key, flag in (("attachment", "text", "truncated"), ("work", "spec", "spec_truncated")):
        whole = state[part].get(key)
        if size() <= STATE_CHARS:
            break
        if not whole:
            continue
        low, high = 0, len(whole)  # the longest start of `whole` that fits: low fits (or is 0), high + 1 does not
        while low < high:
            mid = (low + high + 1) // 2
            state[part][key] = whole[:mid]
            low, high = (mid, high) if size() <= STATE_CHARS else (low, mid - 1)
        state[part][key] = whole[:low]
        state[part][flag] = True
    return state


def _questions() -> dict[str, Any]:
    """The question set's questions (questions/pipeline-requirement.json)."""
    return J.load_set(QUESTION_SET)["questions"]


def _default_engine() -> "Callable[[str, dict[str, Any], dict[str, Any], str], dict[str, Any]] | None":
    """The engine a judge call uses when none is given: ENGINE when a test or harness set it, else the production engine on
    the installed store, else None (every call fails)."""
    if ENGINE is not None:
        return ENGINE
    return live_engine(_installed_store) if _installed_store is not None else None


def raw_engine(store: "Store") -> "Callable[[dict[str, Any], dict[str, Any], str], dict[str, Any]]":
    """Jev through judge.server_engine, answering the decision as it comes (`answers` by question id)."""

    db: Any = store

    def ask(state: dict[str, Any], questions: dict[str, Any], label: str) -> dict[str, Any]:
        with db.read() as c:
            asks = judge.server_engine(store, c, judge.primary_engine(store.settings, store))
        if asks is None:
            raise JudgeError("unconfigured", "no decision model is configured on this server")
        return cast("dict[str, Any]", asks(state, questions, label))

    return ask


def _read(result: dict[str, Any], qid: str, model: str, cost: object = None) -> dict[str, Any]:
    """The engine's answer to `qid` as judge_once's engines give it: {p, reason, provider, model, cost_usd,
    latency_ms}. Raises JudgeError("shape") when it has no probability."""
    answers: Any = result.get("answers")
    answer: Any = cast("dict[str, Any]", answers).get(qid) if isinstance(answers, dict) else None
    p = _probability(cast("dict[str, Any]", answer).get("noul") if isinstance(answer, dict) else None)
    if p is None:
        raise JudgeError("shape", "the decision model's answer has no probability")
    return {
        "p": p,
        "reason": "",
        "provider": result.get("provider") or "",
        "model": result.get("model") or MODELS[model],
        "cost_usd": (result.get("cost_usd") if cost is None else cost) or 0.0,
        "latency_ms": result.get("ms") or 0,
    }


def live_engine(store: "Store") -> "Callable[[str, dict[str, Any], dict[str, Any], str], dict[str, Any]]":
    """The engine the server asks: Jev, through judge.server_engine."""

    ask = raw_engine(store)

    def engine(model: str, state: dict[str, Any], questions: dict[str, Any], label: str) -> dict[str, Any]:
        return _read(ask(state, questions, label), "met", model)

    return engine


def _base(requirement: dict[str, Any], attachment: dict[str, Any], chosen: str) -> dict[str, Any]:
    """The result judge_once starts from, before any answer."""
    return {
        "requirement_id": requirement.get("id") or requirement.get("stage_requirement_id") or "",
        "attachment_id": attachment.get("id") or "",
        "model": chosen,
        "provider": "",
        "cost_usd": 0.0,
        "latency_ms": 0,
        "reason": "",
    }


def judge_once(
    c: sqlite3.Connection,
    requirement: dict[str, Any],
    attachment: dict[str, Any],
    *,
    settings: "Settings | None" = None,
    engine: "Callable[[str, dict[str, Any], dict[str, Any], str], dict[str, Any]] | None" = None,
) -> dict[str, Any]:
    """Judge one attachment against one requirement's rubric with one model.

    Returns {"verdict": "met" | "not_met" | "unsure" | "failed", "p": float | None, "model", "cost_usd", "latency_ms",
    "provider", "reason", "requirement_id", "attachment_id"}; the reason is never empty. The model is Jev
    (judge_model_of). `engine` (or the module's ENGINE) makes the call; with neither, the call fails. An attachment with
    no id is no evidence: it is not met with p 0 and no model is asked. A failed call, an unreadable answer, or a call
    over the team's Decide cap (checked when `settings` is given) is `failed` (and `failed: True`), p None, never a pass.
    The caller's `c` is a read; no write transaction is held during the model call."""
    chosen = judge_model_of(c, requirement.get("pipeline_id"))
    base = _base(requirement, attachment, chosen)

    if not attachment.get("id"):
        return _ask(base, requirement, attachment, None)  # no evidence is not met, whatever the model or the cap
    if settings is not None and judge.over_spend_cap(c, settings) is not None:
        return _failed(base, "Decide has reached the team's monthly spend cap")
    return _ask(base, requirement, attachment, engine or _default_engine())


def _failed(base: dict[str, Any], why: str) -> dict[str, Any]:
    """The call itself failed (no model, an error, an unreadable answer, over the Decide cap): a judge failure, never a
    verdict and never a pass, with what went wrong as its reason, so a caller retries it (page_verdicts) and a person who
    sees it is told it was Jev that failed, not the page."""
    return {**base, "verdict": "failed", "p": None, "reason": why or "The judge call failed", "failed": True}


def judge_outside(
    store: Any,
    requirement: dict[str, Any],
    attachment: dict[str, Any],
    *,
    engine: "Callable[[str, dict[str, Any], dict[str, Any], str], dict[str, Any]] | None" = None,
) -> dict[str, Any]:
    """`judge_once` with no snapshot held during the model call: the model and the Decide cap are read in a short read,
    then the model is asked with no connection (the Jev step, page_verdicts.run)."""
    with store.read() as c:
        chosen = judge_model_of(c, requirement.get("pipeline_id"))
        capped = judge.over_spend_cap(c, store.settings) is not None
    base = _base(requirement, attachment, chosen)
    if capped and attachment.get("id"):
        return _failed(base, "Decide has reached the team's monthly spend cap")
    return _ask(base, requirement, attachment, engine or _default_engine())


def _ask(
    base: dict[str, Any],
    requirement: dict[str, Any],
    attachment: dict[str, Any],
    engine: "Callable[[str, dict[str, Any], dict[str, Any], str], dict[str, Any]] | None",
) -> dict[str, Any]:
    """The model call and its answer, with no connection: `base` is judge_once's starting result (model chosen and the
    cap already checked). Safe to run while no database snapshot is held."""
    chosen = base["model"]

    if not attachment.get("id"):
        # No evidence row: nothing to read, so the answer is not met and no model is asked (it cost nothing and needs no
        # person to fix what is missing).
        return {**base, "verdict": "not_met", "p": 0.0, "reason": NO_EVIDENCE}

    ask = engine
    if ask is None:
        return _failed(base, "No judge is configured on this server")
    state = _state(requirement, attachment)
    try:
        result = ask(chosen, state, _questions(), QUESTION_SET)
    except Exception as exc:  # noqa: BLE001 - any error in the call is a judge failure, retried, never a verdict
        return _call_failed(base, exc)
    return _finish(base, requirement, attachment, state, result)


def _call_failed(base: dict[str, Any], exc: BaseException) -> dict[str, Any]:
    """The pair's result when its call ended in `exc`."""
    if isinstance(exc, JudgeError):
        failed: Any = exc
        return _failed(base, judge.FAILED.get(J.error_code(exc), failed.detail))
    LOG.warning("The judge call failed: %s", type(exc).__name__)
    return _failed(base, "The judge call failed")


def _finish(
    base: dict[str, Any],
    requirement: dict[str, Any],
    attachment: dict[str, Any],
    state: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """The verdict from the engine's answer about one pair, `state` being what one call about it alone is shown."""
    chosen = base["model"]
    p = _probability(result.get("p"))
    if p is None:
        return _failed(base, "The judge's answer could not be read")
    verdict = verdict_of(p)
    said = " ".join(str(result.get("reason") or "").split())
    reason = (said or reason_for(verdict, p, str(requirement.get("rubric") or requirement.get("name") or "")))[
        :REASON_BYTES
    ]
    if _cannot_see(attachment, state):
        # Enforced here, not only in the prompt: a model that says met on text it was not all shown is still unsure.
        # The call was made and is paid for, so its cost and latency are kept. A text that was cut tells the owner how
        # long it may be, so it is shortened rather than attached again.
        verdict, p, reason = "unsure", 0.5, _cannot_see_why(attachment, state)
    elif verdict == "met" and state["work"].get("spec_truncated"):
        # The rubric may need the whole spec, and the judge saw only its start: its met is not trusted. A not met stands.
        verdict, p, reason = "unsure", 0.5, SPEC_CUT
    return {
        **base,
        "verdict": verdict,
        "p": p,
        "model": str(result.get("model") or chosen),
        "provider": str(result.get("provider") or ""),
        "cost_usd": float(result.get("cost_usd") or 0.0),
        "latency_ms": int(result.get("latency_ms") or 0),
        "reason": reason,
    }


NAME = "requirement{}"
ONE_OF = (
    "Here `requirement` means `requirements.{name}`: this question is about that one requirement alone, and the other "
    "requirements are not part of it."
)


def _page_item(states: "list[dict[str, Any]]") -> "tuple[dict[str, Any], dict[str, Any], list[str]] | None":
    """One call about one page and several requirements: (state, questions, the question id of each requirement), the
    page and the work shown once and each requirement's `met` asked about its own. None when it does not fit one call;
    then each requirement is its own item."""
    met = _questions()["met"]
    first = states[0]
    held: dict[str, Any] = {}
    questions: dict[str, Any] = {}
    qids: list[str] = []
    for n, one in enumerate(states, start=1):
        name = NAME.format(n)
        held[name] = one["requirement"]
        qid = f"met{n}"
        questions[qid] = {**met, "instructions": ONE_OF.format(name=name) + " " + str(met["instructions"])}
        qids.append(qid)
    state = {"requirements": held, "work": first["work"], "attachment": first["attachment"]}
    return (state, questions, qids) if J.fits(state, questions) else None


def judge_many(store: Any, pairs: "list[tuple[dict[str, Any], dict[str, Any]]]") -> list[dict[str, Any]]:
    """`judge_outside` for each (requirement, attachment) pair, in order, in as few calls as fit: the pairs that show
    Jev the same page and work (one page attached to several requirements) are one item, read once with a question per
    requirement; up to GROUP items share a call (backend/decide_groups.py). Each pair's result is judge_once's, read
    from its own answer only; a pair whose answer is missing is asked again alone, and a failed call fails each pair
    in it as its own call failing would (`failed`, retried by page_verdicts). With ENGINE set (a test or harness's
    judge_once engine) each pair is asked on its own, as before."""
    if ENGINE is not None or (ASK is None and _installed_store is None):
        return [judge_outside(store, requirement, attachment) for requirement, attachment in pairs]
    ask = ASK or raw_engine(_installed_store if _installed_store is not None else store)
    with store.read() as c:
        capped = judge.over_spend_cap(c, store.settings) is not None
        bases = [_base(r, a, judge_model_of(c, r.get("pipeline_id"))) for r, a in pairs]
    out: list[dict[str, Any] | None] = [None] * len(pairs)
    states: dict[int, dict[str, Any]] = {}
    pages: dict[str, list[int]] = {}
    for n, (requirement, attachment) in enumerate(pairs):
        if not attachment.get("id"):
            out[n] = _ask(bases[n], requirement, attachment, None)
        elif capped:
            out[n] = _failed(bases[n], "Decide has reached the team's monthly spend cap")
        else:
            states[n] = _state(requirement, attachment)
            seen = json.dumps([states[n]["work"], states[n]["attachment"]], sort_keys=True, default=str)
            pages.setdefault(seen, []).append(n)
    # Each item: (state, questions) and, for each pair it holds, (its place, its question id).
    items: list[DG.Item] = []
    holds: list[list[tuple[int, str]]] = []
    for members in pages.values():
        merged = _page_item([states[n] for n in members]) if SAME_PAGE and len(members) > 1 else None
        if merged is not None:
            items.append((merged[0], merged[1]))
            holds.append(list(zip(members, merged[2], strict=True)))
            continue
        for n in members:
            items.append((states[n], _questions()))
            holds.append([(n, "met")])
    for run in DG.packs(items, GROUP):
        replies = DG.ask_run(ask, [items[i] for i in run], QUESTION_SET, None, run)
        for i, reply in zip(run, replies, strict=True):
            share = None
            if isinstance(reply, dict):
                share = round(float(reply.get("cost_usd") or 0) / len(holds[i]), 6)
            for n, qid in holds[i]:
                requirement, attachment = pairs[n]
                if not isinstance(reply, dict):
                    out[n] = _call_failed(bases[n], reply)
                    continue
                try:
                    result = _read(reply, qid, bases[n]["model"], share)
                except JudgeError as exc:
                    out[n] = _call_failed(bases[n], exc)
                    continue
                out[n] = _finish(bases[n], requirement, attachment, states[n], result)
    return [r if r is not None else _failed(bases[n], "The judge call failed") for n, r in enumerate(out)]


def _cannot_see_why(attachment: dict[str, Any], state: dict[str, Any]) -> str:
    """Why Jev could not see all of the evidence: too long (with the length Jev was shown, in thousands, as the most it
    may be), or no text Tico can read."""
    evidence = state["attachment"]
    if evidence["text"] is None:
        return CANNOT_SEE
    what = "page" if str(attachment.get("source") or "").startswith("page:") else "evidence"
    return TOO_LONG.format(what=what, chars=max(1000, len(evidence["text"]) // 1000 * 1000))


def _cannot_see(attachment: dict[str, Any], state: dict[str, Any]) -> bool:
    """Whether the judge was not shown all of the evidence: Tico holds no text for an attachment that is there (an image, a
    video, a PDF, a link, or a text it could not read), or the text was cut. Such evidence never passes on the model's word
    (D60). An attachment with no id is no evidence row (the eval's missing-evidence cases): a rule judge decides that, so
    it is not treated as unreadable."""
    if not attachment.get("id"):
        return False
    evidence = state["attachment"]
    return evidence["text"] is None or bool(evidence["truncated"])


def read_prefix(blobs: Any, digest: str) -> bytes:
    """The first READ_MAX + 1 bytes of a blob, by a range read: a large upload is never loaded whole. The extra byte says
    the bytes go on (text_of_bytes sets truncated from it)."""
    return b"".join(blobs.open_range(digest, 0, files.READ_MAX))


def text_of_bytes(mime: str, data: bytes, size: int) -> tuple[str | None, bool]:
    """The text the judge is sent for evidence of this type, from its bytes: the words of the first READ_MAX bytes, and
    whether the bytes were cut (the extra byte in `data`, or a stored `size` past the cap). (None, False) for a type Tico
    reads no text from. The server's read and the eval adapter both use this, so they see the same text."""
    if not _reads_text(mime):
        return None, False
    cut = size > files.READ_MAX or len(data) > files.READ_MAX
    return files.extract(mime, data[: files.READ_MAX], full=True), cut


def _intact(data: bytes, digest: str, size: int) -> bool:
    """Whether the bytes a range read gave are the blob's bytes as far as the judge reads them. A blob within the cap must
    come back whole: its length is the stored size and its sha256 is the stored digest. A larger one must give exactly
    READ_MAX + 1 bytes, the cap and the extra byte that says more follows. Anything else is a damaged or cut read."""
    if size > files.READ_MAX:
        return len(data) == files.READ_MAX + 1
    if len(data) != size:
        return False
    return hashlib.sha256(data).hexdigest() == digest


def text_of(c: sqlite3.Connection, job: dict[str, Any]) -> tuple[str | None, bool]:
    """The text Tico holds for a piece of evidence, read up to file_library.READ_MAX bytes: a Tico page, a company file or
    an uploaded blob of a text type. (None, False) when Tico holds none (an image, a video, a PDF or a link), or the bytes
    cannot be read: the judge then answers unreadable, and the pass goes on. `truncated` is set when the bytes were cut."""
    blobs = getattr(c, "blobs", None)
    mime = str(job.get("mime") or "").split(";", 1)[0].strip().lower()
    digest = job.get("blob_digest")
    if blobs is None or not digest or not _reads_text(mime):
        return None, False
    size = int(job.get("blob_size") or 0)
    try:
        data = read_prefix(blobs, digest)
    except Exception as exc:  # noqa: BLE001 - an unreadable file is judged as unreadable; one bad blob never stops the pass
        LOG.warning("An attachment's bytes could not be read for the judge: %s", type(exc).__name__)
        return None, False
    if not _intact(data, str(digest), size):
        LOG.warning("An attachment's bytes came back short or altered for the judge; it is judged as unreadable")
        return None, False
    return text_of_bytes(mime, data, size)


def install(app: "FastAPI", store: "Store", mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """Judges have no routes: a decision is read through the task routes. The installed store is what
    judge_once asks the production engine on when no engine is given."""
    global _installed_store
    _installed_store = store
    return None
