# pyright: strict
"""How Jev judges a piece of evidence against a requirement: one attachment, one rubric, one model, one probability.

`judge_once` is the seam. It asks a model for p, the probability the attachment meets the rubric, and the bands decide:
p >= 0.8 is met, p <= 0.2 is not met, anything between is unsure. An unsure answer, a failed call, an unknown model and a
call over the team's Decide cap are all unsure, and nothing unsure passes work.

The judge model is Jev alone: Decide Low (`tico-decide-low`), by Chris's decision on Nov 9. No pipeline picks another
model. Jev goes through judge.server_engine, so its spend counts against the team's Decide cap.

A page requirement's verdicts call it (backend/page_verdicts.py, through `judge_outside`), and the eval
(evals/pipeline_judge). The model is never asked while a write transaction is open.
"""

import hashlib
import json
import logging
import math
import sqlite3
from typing import Any, TYPE_CHECKING, cast

from clients import judge as clients_judge

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
MET_AT, NOT_MET_AT = 0.8, 0.2
REASON_BYTES = 500
TEXT_CHARS = 6000  # the most of an attachment's text the judge is sent; a longer text is cut and flagged truncated
SPEC_CHARS = 6000  # the most of the task's description (its spec) the judge is sent; a longer one is cut and flagged
NO_EVIDENCE = "No evidence is attached."
# What a person sees as the reason when the judge was not shown all of the evidence (Fix L2-2).
CANNOT_SEE = "Tico could not read all of this evidence, so a person decides."
# What a person sees when the judge said met on a spec it was shown only the start of: the rest may hold what is missing.
SPEC_CUT = "The judge saw only the start of this work's spec, so a person decides."
# The engine a test or a harness may set: (model, state, questions, label) -> {p, reason, provider, model, cost_usd,
# latency_ms}. None, with no installed store, asks no one, so every answer is unsure until a caller sets one.
ENGINE: "Callable[[str, dict[str, Any], dict[str, Any], str], dict[str, Any]] | None" = None
# The store the server installed (install): judge_once asks the production engine on it when no engine is given.
_installed_store: Any = None


def judge_model_of(conn: sqlite3.Connection, pipeline_id: str | None) -> str:
    """The judge model a pipeline is judged by: Jev, for every pipeline."""
    return DEFAULT_MODEL


def verdict_of(p: float) -> str:
    """The band a probability falls in: met at 0.8 or more, not_met at 0.2 or less, unsure between."""
    if p >= MET_AT:
        return "met"
    if p <= NOT_MET_AT:
        return "not_met"
    return "unsure"


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
    return {
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


def _questions() -> dict[str, Any]:
    """The question set's questions (questions/pipeline-requirement.json)."""
    return J.load_set(QUESTION_SET)["questions"]


def _default_engine() -> "Callable[[str, dict[str, Any], dict[str, Any], str], dict[str, Any]] | None":
    """The engine a judge call uses when none is given: ENGINE when a test or harness set it, else the production engine on
    the installed store, else None (every answer is unsure)."""
    if ENGINE is not None:
        return ENGINE
    return live_engine(_installed_store) if _installed_store is not None else None


def live_engine(store: "Store") -> "Callable[[str, dict[str, Any], dict[str, Any], str], dict[str, Any]]":
    """The engine the server asks: Jev, through judge.server_engine."""

    db: Any = store

    def engine(model: str, state: dict[str, Any], questions: dict[str, Any], label: str) -> dict[str, Any]:
        with db.read() as c:
            asks = judge.server_engine(store, c, judge.primary_engine(store.settings, store))
        if asks is None:
            raise JudgeError("unconfigured", "no decision model is configured on this server")
        result = asks(state, questions, label)
        p = _probability(result["answers"]["met"]["noul"])
        if p is None:
            raise JudgeError("shape", "the decision model's answer has no probability")
        return {
            "p": p,
            "reason": "",
            "provider": result.get("provider") or "",
            "model": result.get("model") or MODELS[model],
            "cost_usd": result.get("cost_usd") or 0.0,
            "latency_ms": result.get("ms") or 0,
        }

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

    Returns {"verdict": "met" | "not_met" | "unsure", "p": float, "model", "cost_usd", "latency_ms", "provider",
    "reason", "requirement_id", "attachment_id"}, and `failed: True` when the call itself failed. The model is Jev
    (judge_model_of).
    `engine` (or the module's ENGINE) makes the call; with neither, every answer is unsure. An attachment with no id is no
    evidence: it is not met with p 0 and no model is asked. A failed call, an unreadable answer, or a call over the team's
    Decide cap (checked when `settings` is given) is unsure with p 0.5, never a pass.
    The caller's `c` is a read; no write transaction is held during the model call."""
    chosen = judge_model_of(c, requirement.get("pipeline_id"))
    base = _base(requirement, attachment, chosen)

    if not attachment.get("id"):
        return _ask(base, requirement, attachment, None)  # no evidence is not met, whatever the model or the cap
    if settings is not None and judge.over_spend_cap(c, settings) is not None:
        return _failed(base, "Decide has reached the team's monthly spend cap")
    return _ask(base, requirement, attachment, engine or _default_engine())


def _failed(base: dict[str, Any], why: str) -> dict[str, Any]:
    """The call itself failed (no model, an error, an unreadable answer, over the Decide cap): unsure with p 0.5, never a
    pass, and `failed` so a caller that retries (page_verdicts) can tell it from a model that was unsure."""
    return {**base, "verdict": "unsure", "p": 0.5, "reason": why, "failed": True}


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
    except JudgeError as exc:
        return _failed(base, judge.FAILED.get(J.error_code(exc), exc.detail))
    p = _probability(result.get("p"))
    if p is None:
        return _failed(base, "The judge's answer could not be read")
    verdict = verdict_of(p)
    reason = " ".join(str(result.get("reason") or "").split())[:REASON_BYTES]
    if _cannot_see(attachment, state):
        # Enforced here, not only in the prompt: a model that says met on text it was not all shown is still unsure.
        # The call was made and is paid for, so its cost and latency are kept.
        verdict, p, reason = "unsure", 0.5, CANNOT_SEE
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
