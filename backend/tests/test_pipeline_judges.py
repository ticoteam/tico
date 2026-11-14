"""How Jev judges evidence: an answer passes only when it is a confident yes or no, a failed or capped call is unsure,
evidence the judge cannot read in full is unsure, and the judge is told the work as it is."""

import hashlib
import json
import sqlite3
import threading
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from clients import judge as clients_judge

from backend import blobs as BL
from backend import hubdb as H
from backend import judge as backend_judge
from backend import pipeline_judges as P
from backend.config import Settings
from backend.store import Store
from clients import judge as J

from backend.tests.helpers.pipeline_foundation import boot, hub

BOOTED = {}


@pytest.fixture(scope="module", autouse=True)
def booted_hub(tmp_path_factory):
    """One boot for the module (Store.initialize is the slow part); each test copies this file."""
    path = tmp_path_factory.mktemp("pipeline-judges") / "booted.sqlite"
    boot(path)
    BOOTED["path"] = path
    yield path


def copy_booted(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(BOOTED["path"])) as source, closing(sqlite3.connect(path)) as target:
        source.backup(target)


FIXTURES = Path(__file__).parent / "fixtures"
BEN = "human:ben"
REQ = {"id": "req-1", "name": "Shows the page live", "rubric": "The link opens the page", "pipeline_id": None}
ATTACHMENT = {
    "id": "att-1",
    "source": "link",
    "digest": "d",
    "url": "https://x.example",
    "file_version": 0,
    "text": "The link opens the page",
}


def _settings(path):
    return Settings(db_path=path, registry_dir=FIXTURES / "registry")


def _engine(p, *, reason="ok"):
    def ask(model, state, questions, label):
        return {"p": p, "reason": reason, "provider": "t", "model": model}

    return ask


def test_the_bands_decide_met_not_met_or_unsure(tmp_path):
    path = tmp_path / "hub.sqlite"
    copy_booted(path)
    c = hub(path)
    settings = _settings(path)

    def verdict(p):
        return P.judge_once(c, REQ, ATTACHMENT, settings=settings, engine=_engine(p))["verdict"]

    assert verdict(0.75) == "met" and verdict(0.8) == "met" and verdict(0.95) == "met"
    assert verdict(0.2) == "not_met" and verdict(0.01) == "not_met"
    assert verdict(0.5) == "unsure" and verdict(0.74) == "unsure" and verdict(0.21) == "unsure"
    assert verdict(float("nan")) == "failed"  # an answer with no probability is a judge failure, not a pass


def test_a_failed_model_call_is_failed_and_no_engine_means_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        P, "_installed_store", None
    )  # no installed app: no production engine, so no engine means the call fails
    path = tmp_path / "hub.sqlite"
    copy_booted(path)
    c = hub(path)
    settings = _settings(path)

    def broken(model, state, questions, label):
        raise J.JudgeError("unavailable", "the model did not answer")

    def crashed(model, state, questions, label):
        raise KeyError("answers")  # not a JudgeError: still a failure of the call, never an unsure

    for engine in (broken, None, crashed):
        result = P.judge_once(c, REQ, ATTACHMENT, settings=settings, engine=engine)
        assert (result["verdict"], result["p"], result["failed"]) == ("failed", None, True)
        assert result["reason"], "a failure says what went wrong"


def test_the_spend_cap_stops_the_call_and_the_call_is_failed(tmp_path, monkeypatch):
    path = tmp_path / "hub.sqlite"
    copy_booted(path)
    c = hub(path)
    called = []
    monkeypatch.setattr(backend_judge, "over_spend_cap", lambda conn, settings, *a, **k: (12.0, 10.0))

    def ask(model, state, questions, label):
        called.append(model)
        return {"p": 1.0, "reason": "ok"}

    result = P.judge_once(c, REQ, ATTACHMENT, settings=_settings(path), engine=ask)

    assert result["verdict"] == "failed" and "spend cap" in result["reason"] and called == []


def test_the_judge_with_no_engine_given_uses_the_installed_production_engine(tmp_path, monkeypatch):
    """B11: judge_once's default engine is the installed store's live engine, not a permanent unsure."""
    path = tmp_path / "hub.sqlite"
    copy_booted(path)
    c = hub(path)
    asked = []

    def recording_engine(store):
        def engine(model, state, questions, label):
            asked.append(model)
            return {"p": 0.95, "reason": "ok", "provider": "t", "model": model, "cost_usd": 0.0, "latency_ms": 1}

        return engine

    monkeypatch.setattr(P, "_installed_store", object())
    monkeypatch.setattr(P, "live_engine", recording_engine)
    P.judge_once(c, REQ, ATTACHMENT, settings=_settings(path))
    assert asked == ["jev"]


def test_a_large_blob_is_read_only_to_one_byte_past_the_cap(monkeypatch):
    """Fix L2-3: the judge's read is a range read of READ_MAX + 1 bytes, never the whole upload, and the cut is flagged."""
    from backend import file_library as files

    asked = []

    class RangeStore:
        """A blob store that honours the range it is asked for, and records it."""

        def open_range(self, digest, start=0, end=None):
            asked.append((start, end))
            body = b"w" * (files.READ_MAX * 2)
            yield body[start : None if end is None else end + 1]

    text, cut = P.text_of(
        SimpleNamespace(blobs=RangeStore()),
        {"mime": "text/plain", "blob_digest": "a" * 64, "blob_size": files.READ_MAX * 2},
    )
    assert asked == [(0, files.READ_MAX)]
    assert len(text) == files.READ_MAX and cut is True


def _blob_job(digest: str, size: int) -> dict:
    return {"mime": "text/plain", "blob_digest": digest, "blob_size": size}


def test_a_short_read_of_a_small_blob_is_unreadable_and_the_judge_says_unsure():
    """Codex 2: a 1,000 byte blob that comes back as 18 bytes is not the evidence. The judge is not shown it as complete, so
    even a met from the model is unsure."""
    body = b"x" * 1000
    digest = hashlib.sha256(body).hexdigest()

    class ShortStore:
        def open_range(self, digest, start=0, end=None):
            yield body[:18]

    text, cut = P.text_of(SimpleNamespace(blobs=ShortStore()), _blob_job(digest, len(body)))
    assert (text, cut) == (None, False)

    def met(model, state, questions, label):
        return {"p": 0.95, "reason": "looks right", "provider": "stub", "model": model}

    result = P.judge_once(
        None,
        {"id": "r1", "name": "Spec", "rubric": "The spec is attached", "pipeline_id": None},
        {"id": "a1", "digest": digest, "text": text, "truncated": cut},
        engine=met,
    )
    assert result["verdict"] == "unsure"


def test_a_small_blob_whose_bytes_do_not_match_its_digest_is_unreadable():
    body = b"Shipped the page."

    class AlteredStore:
        def open_range(self, digest, start=0, end=None):
            yield body.replace(b"S", b"T")

    text, _ = P.text_of(
        SimpleNamespace(blobs=AlteredStore()),
        _blob_job(hashlib.sha256(body).hexdigest(), len(body)),
    )
    assert text is None


def test_a_large_blob_that_comes_back_short_is_unreadable_not_truncated_text():
    from backend import file_library as files

    class ShortLargeStore:
        def open_range(self, digest, start=0, end=None):
            yield b"w" * 100

    text, cut = P.text_of(SimpleNamespace(blobs=ShortLargeStore()), _blob_job("a" * 64, files.READ_MAX * 2))
    assert (text, cut) == (None, False)


def test_a_good_small_blob_is_judged_on_its_text():
    body = b"The page says the measures held: 42 percent."
    digest = hashlib.sha256(body).hexdigest()

    class GoodStore:
        def open_range(self, digest, start=0, end=None):
            yield body

    text, cut = P.text_of(SimpleNamespace(blobs=GoodStore()), _blob_job(digest, len(body)))
    assert text == body.decode() and cut is False

    def met(model, state, questions, label):
        return {"p": 0.95, "reason": "held", "provider": "stub", "model": model}

    result = P.judge_once(
        None,
        {"id": "r1", "name": "Validated", "rubric": "Measures held", "pipeline_id": None},
        {"id": "a1", "digest": digest, "text": text, "truncated": cut},
        engine=met,
    )
    assert result["verdict"] == "met"


def test_evidence_the_judge_cannot_read_in_full_is_unsure_whatever_the_model_says(tmp_path):
    """Fix L2-2: no text (an image, or a text Tico could not read) or a cut text is unsure, even at p 0.95; readable text is
    still judged by the bands. Through the engine seam."""
    path = tmp_path / "hub.sqlite"
    copy_booted(path)
    c = hub(path)
    settings = _settings(path)
    image = {**ATTACHMENT, "id": "att-shot", "source": "blob", "text": None}
    cut = {**ATTACHMENT, "id": "att-cut", "truncated": True}
    for evidence in (image, cut):
        for p in (0.95, 0.05):
            result = P.judge_once(c, REQ, evidence, settings=settings, engine=_engine(p))
            assert result["verdict"] == "unsure" and result["p"] == 0.5, (evidence["id"], p)
    assert P.judge_once(c, REQ, ATTACHMENT, settings=settings, engine=_engine(0.95))["verdict"] == "met"


def test_a_page_a_bot_writes_is_read_whole_and_one_too_long_says_how_long_it_may_be():
    """rc.5: a 6,147-character spec was cut at 6,000 and came back "Tico could not read all of this evidence", which the
    bot could not act on. A page of a few pages is read whole and judged on the model's word; a longer one is unsure and
    the owner is told the most Jev reads, so it shortens it rather than attaching it again."""
    page = {**ATTACHMENT, "source": "page:f1@2"}
    for chars in (6_500, 12_000, P.TEXT_CHARS):
        text = ("Scope: the importer reads the CSV. " * 2_000)[:chars]
        state = P._state(REQ, {**page, "text": text})
        assert state["attachment"]["text"] == text and state["attachment"]["truncated"] is False, chars
        assert P.judge_once(None, REQ, {**page, "text": text}, engine=_engine(0.95))["verdict"] == "met", chars
    too_long = {**page, "text": "x" * (P.TEXT_CHARS + 1)}
    result = P.judge_once(None, REQ, too_long, engine=_engine(0.95))
    assert (result["verdict"], result["p"]) == ("unsure", 0.5)
    assert result["reason"] == "This page is too long for Jev to read: keep it under 40,000 characters."
    other = P.judge_once(None, REQ, {**ATTACHMENT, "text": "x" * (P.TEXT_CHARS + 1)}, engine=_engine(0.05))
    assert other["reason"] == "This evidence is too long for Jev to read: keep it under 40,000 characters."
    bytes_cut = P.judge_once(None, REQ, {**page, "text": "short", "truncated": True}, engine=_engine(0.95))
    assert bytes_cut["verdict"] == "unsure" and "too long for Jev" in bytes_cut["reason"], "cut bytes are too long too"
    image = P.judge_once(None, REQ, {**ATTACHMENT, "text": None}, engine=_engine(0.95))
    assert image["reason"] == P.CANNOT_SEE, "no text is not a length problem"


def test_text_that_escapes_long_in_json_is_cut_to_fit_jevs_limit_and_the_owner_is_told_the_length_it_got():
    """Letters outside ASCII are six characters each in the JSON sent: 40,000 of them would pass Jev's limit. The state
    is cut to STATE_CHARS, the cut is flagged, and the owner is told the length Jev was shown."""
    page = {**ATTACHMENT, "source": "page:f1@2", "text": "é" * P.TEXT_CHARS}
    state = P._state(REQ, page)
    assert len(json.dumps(state, default=str)) <= P.STATE_CHARS < clients_judge.MAX_STATE_CHARS
    assert state["attachment"]["truncated"] is True and 0 < len(state["attachment"]["text"]) < P.TEXT_CHARS
    clients_judge.validate(state, P._questions(), P.QUESTION_SET)  # Jev takes it
    result = P.judge_once(None, REQ, page, engine=_engine(0.95))
    shown = len(state["attachment"]["text"]) // 1000 * 1000
    assert result["reason"] == f"This page is too long for Jev to read: keep it under {shown:,} characters."
    both = P._state(REQ, {**page, "task_spec": "ü" * P.SPEC_CHARS})
    assert len(json.dumps(both, default=str)) <= P.STATE_CHARS, "with a spec that escapes long as well"
    assert len(both["attachment"]["text"]) < len(state["attachment"]["text"]), "the evidence is cut before the spec"


def _work_fields(**extra):
    """An attachment dict as the server's waiting read gives it, carrying the work's own fields."""
    return {
        **ATTACHMENT,
        "task_work_version": 3,
        "task_title": "Dark mode toggle on Settings",
        "task_spec": "Add a Dark mode toggle to the Settings page.",
        "task_repo": "acme/app",
        "task_pr": 91,
        "task_author": "human:ana",
        **extra,
    }


def test_the_judge_state_carries_the_work_title_spec_repo_pr_and_author():
    """The judge checks the evidence against the work: which task, repo, PR and author it is about."""
    work = P._state(REQ, _work_fields())["work"]
    assert work == {
        "work_version": 3,
        "title": "Dark mode toggle on Settings",
        "spec": "Add a Dark mode toggle to the Settings page.",
        "repo": "acme/app",
        "pr": 91,
        "author": "human:ana",
    }


def test_a_long_spec_is_cut_and_flagged_and_a_work_with_no_pr_leaves_repo_pr_and_author_out():
    assert P.SPEC_CHARS == 12_000
    state = P._state(REQ, _work_fields(task_spec="x" * 15_000, task_repo=None, task_pr=None, task_author=None))
    assert len(state["work"]["spec"]) == P.SPEC_CHARS
    assert state["work"]["spec_truncated"] is True
    assert "repo" not in state["work"] and "pr" not in state["work"] and "author" not in state["work"]
    whole = P._state(REQ, _work_fields(task_spec="x" * P.SPEC_CHARS))["work"]
    assert "spec_truncated" not in whole and len(whole["spec"]) == P.SPEC_CHARS


def test_an_attachment_with_no_id_is_not_met_and_the_model_is_never_called():
    """No evidence row: a rule decides, the model is not asked (it used to answer 0.5 on nothing)."""
    asked = []

    def engine(model, state, questions, label):
        asked.append(model)
        return {"p": 0.9, "reason": "met", "provider": "t", "model": model}

    no_evidence = _work_fields()
    del no_evidence["id"]
    result = P.judge_once(sqlite3.connect(":memory:"), REQ, no_evidence, engine=engine)
    assert result["verdict"] == "not_met"
    assert result["p"] == 0.0 and result["cost_usd"] == 0.0
    assert result["reason"] == "No evidence is attached."
    assert asked == []


def test_a_met_on_a_cut_spec_is_unsure_and_a_not_met_stays_not_met(tmp_path):
    """Codex P2: a rubric may need the whole spec, and the judge saw only its start: its met is not trusted."""
    path = tmp_path / "hub.sqlite"
    copy_booted(path)
    c = hub(path)
    settings = _settings(path)
    long = _work_fields(task_spec="Acceptance: " + "x" * P.SPEC_CHARS)
    met = P.judge_once(c, REQ, long, settings=settings, engine=_engine(0.95))
    assert met["verdict"] == "unsure" and met["p"] == 0.5 and met["reason"] == P.SPEC_CUT
    assert P.judge_once(c, REQ, long, settings=settings, engine=_engine(0.05))["verdict"] == "not_met"
    whole = _work_fields(task_spec="x" * P.SPEC_CHARS)
    assert P.judge_once(c, REQ, whole, settings=settings, engine=_engine(0.95))["verdict"] == "met"


def test_every_verdict_carries_a_reason_even_when_the_model_gives_none(tmp_path):
    """Jev answers a probability, never prose: Tico says what it found in one line naming the requirement, so no
    verdict is a bare word."""
    path = tmp_path / "hub.sqlite"
    copy_booted(path)
    c = hub(path)
    settings = _settings(path)
    said = {}
    for p in (0.95, 0.5, 0.05):
        result = P.judge_once(c, REQ, ATTACHMENT, settings=settings, engine=_engine(p, reason=""))
        said[result["verdict"]] = result["reason"]
    assert set(said) == {"met", "unsure", "not_met"}
    assert said == {
        "met": 'Meets "Shows the page live".',
        "unsure": 'Can\'t tell if it meets "Shows the page live".',
        "not_met": 'Doesn\'t meet "Shows the page live".',
    }
    kept = P.judge_once(c, REQ, ATTACHMENT, settings=settings, engine=_engine(0.5, reason="No scope line"))
    assert kept["reason"] == "No scope line", "a model's own reason is kept"


def test_no_evidence_is_not_met_even_over_the_cap(tmp_path, monkeypatch):
    """Codex round 2, P2: missing evidence is decided before the model and the cap are looked at."""
    path = tmp_path / "hub.sqlite"
    copy_booted(path)
    c = hub(path)
    no_evidence = _work_fields()
    del no_evidence["id"]
    assert P.judge_once(c, REQ, no_evidence)["verdict"] == "not_met"
    monkeypatch.setattr(backend_judge, "over_spend_cap", lambda conn, settings, *a, **k: (12.0, 10.0))
    result = P.judge_once(c, REQ, no_evidence, settings=_settings(path), engine=_engine(0.95))
    assert result["verdict"] == "not_met" and result["p"] == 0.0


def test_a_failed_call_is_marked_failed_and_a_model_that_was_unsure_is_not(tmp_path, monkeypatch):
    """The Jev step retries a failed call (page_verdicts TRIES) and never a model's own unsure: `failed` tells them apart."""
    monkeypatch.setattr(P, "_installed_store", None)
    path = tmp_path / "hub.sqlite"
    copy_booted(path)
    c = hub(path)
    settings = _settings(path)

    def broken(model, state, questions, label):
        raise J.JudgeError("unavailable", "the model did not answer")

    assert P.judge_once(c, REQ, ATTACHMENT, settings=settings, engine=broken)["failed"] is True
    assert P.judge_once(c, REQ, ATTACHMENT, settings=settings, engine=None)["failed"] is True
    assert P.judge_once(c, REQ, ATTACHMENT, settings=settings, engine=_engine(float("nan")))["failed"] is True
    assert "failed" not in P.judge_once(c, REQ, ATTACHMENT, settings=settings, engine=_engine(0.5))
    monkeypatch.setattr(backend_judge, "over_spend_cap", lambda conn, settings, *a, **k: (12.0, 10.0))
    assert P.judge_once(c, REQ, ATTACHMENT, settings=settings, engine=_engine(0.95))["failed"] is True


def test_the_judge_is_shown_what_a_page_is_judged_against():
    """A review page's commits and a Validate page's release go to the judge beside the work (page_verdicts)."""
    context = {"heads": {"acme/app#12": "aaa"}, "release": ["v1.2.0"]}
    assert P._state(REQ, _work_fields(context=context))["work"]["context"] == context
    assert "context" not in P._state(REQ, _work_fields())["work"]


# ----------------------------------------------------------------------------- checks asked in parts (task #351)
def _parts_engine(ps, asked=None):
    """Jev answering each part question (`part_<key>`) with ps[key] (0.95 when not named), as live_engine reads it."""

    def ask(model, state, questions, label):
        if asked is not None:
            asked.append(questions)
        answers = {qid: {"noul": ps.get(qid.removeprefix("part_"), 0.95)} for qid in questions}
        return P._read({"answers": answers, "model": "tico-decide-low"}, list(questions), model)

    return ask


def _spec_req():
    from backend import pipeline_settings as PS

    return {"id": "req-spec", "name": "Spec page", "rubric": PS.SPEC_RUBRIC, "pipeline_id": None}


def test_a_spec_with_no_out_of_scope_list_is_not_met_and_says_so_in_one_line():
    asked = []
    result = P.judge_once(None, _spec_req(), ATTACHMENT, engine=_parts_engine({"out_of_scope": 0.05}, asked))
    assert (result["verdict"], result["reason"]) == ("not_met", "No out-of-scope list found.")
    (questions,) = asked
    assert sorted(questions) == ["part_criteria", "part_goal", "part_in_scope", "part_out_of_scope"], "one call"
    assert "what the work leaves out" in questions["part_out_of_scope"]["instructions"]
    assert all(q["type"] == "noul" for q in questions.values())


def test_the_verdict_comes_from_the_parts_and_names_at_most_three():
    req = _spec_req()
    every = P.judge_once(None, req, ATTACHMENT, engine=_parts_engine({}))
    assert every["verdict"] == "met"
    unsure = P.judge_once(None, req, ATTACHMENT, engine=_parts_engine({"criteria": 0.4}))
    assert (unsure["verdict"], unsure["reason"]) == ("unsure", "Can't tell if the acceptance criteria can be checked.")
    # A no outweighs a can't tell, and each missing part is named, three at most.
    mixed = P.judge_once(None, req, ATTACHMENT, engine=_parts_engine({"criteria": 0.4, "goal": 0.1}))
    assert (mixed["verdict"], mixed["reason"]) == ("not_met", "No goal found.")
    nothing = P.judge_once(
        None,
        req,
        ATTACHMENT,
        engine=_parts_engine(dict.fromkeys(("goal", "in_scope", "out_of_scope", "criteria"), 0.0)),
    )
    assert nothing["reason"] == "No goal found. No in-scope list found. No out-of-scope list found."


def test_each_shipped_check_is_asked_as_its_parts_now_and_as_it_read_before():
    from backend import pipeline_settings as PS

    assert P.check_of(PS.SPEC_RUBRIC) == "spec" and P.check_of(PS.REVIEW_RUBRIC) == "review"
    assert P.check_of(PS.QA_RUBRIC) == "qa" and P.check_of(PS.VALIDATE_RUBRIC) == "validate"
    assert P.check_of(PS.EARLIER["product-spec/page/rubric"][0]) == "spec", "an unedited older line is the same check"
    assert P.check_of("  " + PS.SPEC_RUBRIC.replace(" ", "\n", 3)) == "spec", "spacing does not matter"
    assert P.check_of(REQ["rubric"]) is None, "a company's own rubric is asked whole"
    assert [len(P.CHECKS[c]) for c in ("spec", "review", "qa", "validate")] == [4, 3, 5, 3]
    assert set(P.BARS) == set(P.CHECKS), "every check has its bar, in one place"
    req = {"id": "r", "name": "Checked in production", "rubric": PS.VALIDATE_RUBRIC}
    result = P.judge_once(None, req, ATTACHMENT, engine=_parts_engine({"version": 0.35}))
    assert (result["verdict"], result["reason"]) == ("unsure", "Can't tell if the version production runs is named.")


def test_no_verdict_says_a_probability_a_band_or_the_whole_check():
    """Task #351: "Jev could not tell whether it meets the check (p 0.70, between 0.2 and 0.75): <the whole rubric>"
    named nothing to fix. Whole or in parts, met, not met or unsure, the reason carries none of it."""
    from backend import pipeline_settings as PS

    reasons = []
    for p in (0.95, 0.5, 0.05):
        reasons.append(P.judge_once(None, REQ, ATTACHMENT, engine=_engine(p, reason=""))["reason"])
        for rubric in (PS.SPEC_RUBRIC, PS.REVIEW_RUBRIC, PS.QA_RUBRIC, PS.VALIDATE_RUBRIC):
            req = {"id": "r", "name": "Page", "rubric": rubric}
            for key in [part.key for part in P.CHECKS[P.check_of(rubric)]]:
                reasons.append(P.judge_once(None, req, ATTACHMENT, engine=_parts_engine({key: p}))["reason"])
    assert reasons and all(reasons)
    for reason in reasons:
        assert "p 0." not in reason and "between 0.2" not in reason and "(p " not in reason, reason
        for rubric in (REQ["rubric"], PS.SPEC_RUBRIC, PS.REVIEW_RUBRIC, PS.QA_RUBRIC, PS.VALIDATE_RUBRIC):
            assert rubric not in reason, reason
    assert P.judge_once(None, REQ, ATTACHMENT, engine=_engine(0.5, reason=""))["reason"] == (
        'Can\'t tell if it meets "Shows the page live".'
    ), "a check asked whole names the requirement, never its rubric"


def test_the_grouped_jev_step_asks_the_parts_in_one_call_and_reads_each(monkeypatch):
    """judge_many (the Jev step's path): a Spec page is one call with a question per part, read back by part."""
    calls = []

    def raw(state, questions, label):
        calls.append(sorted(questions))
        answers = {q: {"type": "noul", "noul": 0.1 if q == "part_out_of_scope" else 0.9} for q in questions}
        return {"answers": answers, "model": "tico-decide-low", "provider": "gateway", "cost_usd": 0.001, "ms": 3}

    class Store:
        settings = None

        @contextmanager
        def read(self):
            yield None

    monkeypatch.setattr(P, "ASK", raw)
    monkeypatch.setattr(P.judge, "over_spend_cap", lambda c, s: None)
    (result,) = P.judge_many(Store(), [(_spec_req(), ATTACHMENT)])
    assert calls == [["part_criteria", "part_goal", "part_in_scope", "part_out_of_scope"]]
    assert (result["verdict"], result["reason"]) == ("not_met", "No out-of-scope list found.")


def test_a_shipped_check_keeps_its_parts_when_its_page_is_asked_with_other_requirements(monkeypatch):
    """With one page read once for several requirements (SAME_PAGE), a shipped check is never merged into the
    whole-rubric call: it is still asked, and read, as its parts."""
    calls = []

    def raw(state, questions, label):
        calls.append(sorted(questions))
        answers = {q: {"type": "noul", "noul": 0.1 if q == "part_out_of_scope" else 0.9} for q in questions}
        return {"answers": answers, "model": "tico-decide-low", "provider": "gateway", "cost_usd": 0.001, "ms": 3}

    class Store:
        settings = None

        @contextmanager
        def read(self):
            yield None

    monkeypatch.setattr(P, "ASK", raw)
    monkeypatch.setattr(P, "SAME_PAGE", True)
    monkeypatch.setattr(P.judge, "over_spend_cap", lambda c, s: None)
    other = {"id": "req-own", "name": "Names the risks", "rubric": "Names the risks", "pipeline_id": None}
    spec, own = P.judge_many(Store(), [(_spec_req(), ATTACHMENT), (other, ATTACHMENT)])
    assert ["part_criteria", "part_goal", "part_in_scope", "part_out_of_scope"] in calls, calls
    assert (spec["verdict"], spec["reason"]) == ("not_met", "No out-of-scope list found.")
    assert own["verdict"] == "met"


def test_part_questions_still_build_when_the_question_set_is_reworded(monkeypatch):
    reworded = {"questions": {"met": {"type": "noul", "instructions": "Does the attachment meet the requirement?"}}}
    monkeypatch.setattr(P.J, "load_set", lambda name: reworded)
    questions = P._questions("spec")
    assert sorted(questions) == ["part_criteria", "part_goal", "part_in_scope", "part_out_of_scope"]
    assert "Does the attachment meet the requirement?" in questions["part_goal"]["instructions"]
