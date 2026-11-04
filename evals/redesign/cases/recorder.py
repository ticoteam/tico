"""Recorder and replay (TESTING L4, PLAN decision 37): the saved inputs, the recorder that answers each one once, and the
judge replay that reads the answers back. No Org is needed: each check runs the recorder or the replay on a stub engine
in a tempfile folder, so nothing touches the network, a credential or a real recording. See record.py."""
import json
import re
import tempfile
from pathlib import Path
from unittest import mock

from clients import judge
from .. import fakes
from ..fakes import MissingRecording, ReplayJudge, recording_key
from ..harness import BUILT, Check, Scenario
from ..record import INPUTS, load_inputs, record, save

WAVE = "recorder · replay judge"
BUILT.add(WAVE)   # this change builds the recorder and the replay, so a failing check prints its reason
ACME = "acme.example"
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+")
COMMAND = "python -m evals.redesign.record"
# One small call for the replay checks.
CALL_STATE = {"subject": "Q4 hiring budget", "snippet": "Can you sign off by Thursday?"}
CALL_QUESTIONS = {"is_ask": {"type": "noul", "instructions": "Is someone asking for something?"}}
RECORDED = {"is_ask": {"noul": 0.92}}
SCRIPTED = {"is_ask": {"noul": 0.4}}


# ---------------------------------------------------------------------------------------------- stub engine
def stub_answers(questions):
    """A well-formed answer to every question: the first option, a level, a coin toss. Only the shape matters here."""
    out = {}
    for qid, q in questions.items():
        if q["type"] == "choice":
            out[qid] = {"choice": next(iter(q["criteria"])), "confidence": 0.9}
        elif q["type"] == "score":
            out[qid] = {"score": 1.0, "confidence": 0.8}
        else:
            out[qid] = {"noul": 0.3}
    return out


class Stub:
    """A stand-in engine that counts its calls. With `bad`, its first answer is out of shape: a noul of 7 is outside
    [0,1] for every question type, so the recorder must refuse it."""

    def __init__(self, bad=False):
        self.bad, self.calls = bad, 0

    def __call__(self, state, questions, label=None, level=None):
        self.calls += 1
        answers = stub_answers(questions)
        if self.bad:
            answers[next(iter(questions))] = {"noul": 7.0}
        return {"model": "stub", "usage": {"input_tokens": 1, "output_tokens": 1}, "ms": 1, "answers": answers}


def key_of(data):
    qset = judge.load_set(data["questions"], root=judge.QUESTIONS_DIR)
    return recording_key(data["state"], qset["questions"], data["level"])


def saved_files(out):
    return {path.name: json.loads(path.read_text()) for path in Path(out).glob("*.json")}


# ---------------------------------------------------------------------------------------------- the inputs
def inputs_valid(facts):
    loaded = load_inputs(INPUTS)
    if not loaded:
        return False, f"no inputs in {INPUTS}"
    bad = [f"{stem}: {problems[0]}" for stem, _, problems in loaded if problems]
    if bad:
        return False, f"{len(bad)} invalid, first {bad[0]}"
    return True, f"{len(loaded)} inputs, each with its set's state fields"


def ids_and_keys_unique(facts):
    good = [data for _, data, problems in load_inputs(INPUTS) if not problems]
    ids = [data["id"] for data in good]
    keys = [key_of(data) for data in good]
    if len(set(ids)) != len(ids):
        return False, "two inputs share an id"
    if len(set(keys)) != len(keys):
        return False, "two inputs share a recording key (same state, questions and level)"
    return True, f"{len(keys)} recording keys, all distinct"


def emails_at_acme(facts):
    """No address outside acme.example appears anywhere in an input, notes included: the inputs are fiction."""
    found, outside = 0, []
    for path in sorted(INPUTS.glob("*.json")):
        for address in EMAIL.findall(path.read_text()):
            found += 1
            if address.rsplit("@", 1)[1].rstrip(".") != ACME:
                outside.append(f"{path.name}: {address}")
    if outside:
        return False, f"{len(outside)} outside acme.example, first {outside[0]}"
    return True, f"{found} addresses, all at acme.example"


# ---------------------------------------------------------------------------------------------- the recorder
def one_file_per_input(facts):
    stub = Stub()
    with tempfile.TemporaryDirectory() as out:
        rows = record(stub, INPUTS, out)
        saved = saved_files(out)
    if not rows:
        return False, "no inputs were recorded"
    failed = [row for row in rows if row["status"] != "recorded"]
    if failed:
        return False, f"{failed[0]['id']}: {failed[0]['detail']}"
    if set(saved) != {row["key"] + ".json" for row in rows}:
        return False, f"{len(saved)} files, names differ from the {len(rows)} keys"
    for name, meta in saved.items():
        qset = judge.load_set(meta["questions_id"], root=judge.QUESTIONS_DIR)
        if meta["answers"] != judge.check_answers(stub_answers(qset["questions"]), qset["questions"]):
            return False, f"{name} does not hold the stub's answers"
    return True, f"{len(saved)} files, each named by its key and holding the stub's answers"


def second_run_skips(facts):
    stub = Stub()
    with tempfile.TemporaryDirectory() as out:
        record(stub, INPUTS, out)
        first = stub.calls
        rows = record(stub, INPUTS, out)
    skipped = [row for row in rows if row["status"] == "skipped"]
    if not rows or len(skipped) != len(rows) or stub.calls != first:
        return False, f"{len(skipped)} of {len(rows)} skipped; the engine ran {stub.calls - first} more time(s)"
    return True, f"{len(skipped)} skipped, the engine not called again"


def force_answers_again(facts):
    stub = Stub()
    with tempfile.TemporaryDirectory() as out:
        record(stub, INPUTS, out)
        first = stub.calls
        rows = record(stub, INPUTS, out, force=True)
    recorded = [row for row in rows if row["status"] == "recorded"]
    if not rows or len(recorded) != len(rows) or stub.calls - first != len(rows):
        return False, f"{len(recorded)} of {len(rows)} answered again on force"
    return True, f"force: {len(recorded)} inputs answered again"


def wrong_shape_not_saved(facts):
    stub = Stub(bad=True)
    with tempfile.TemporaryDirectory() as out:
        rows = record(stub, INPUTS, out)
        saved = saved_files(out)
    failed = [row for row in rows if row["status"] == "failed"]
    if not rows or len(failed) != len(rows) or saved:
        return False, f"{len(failed)} of {len(rows)} failed, {len(saved)} saved"
    return True, f"{len(failed)} wrong-shape answers reported as failures, none saved"


# ---------------------------------------------------------------------------------------------- the replay
def replay_returns_recording(facts):
    recorded = {"model": "stub-model", "usage": {"input_tokens": 120}, "ms": 240, "answers": RECORDED}
    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(fakes, "RECORDINGS", Path(tmp)):
        key = recording_key(CALL_STATE, CALL_QUESTIONS, "low")
        save(tmp, key, {"key": key, **recorded})
        got = ReplayJudge()(CALL_STATE, CALL_QUESTIONS, label="check@1", level="low")
    if got != recorded:
        return False, f"got {sorted(got)}, not the recorded answers"
    return True, "the recorded model, usage, ms and answers, as the engine returns them"


def replay_missing_names_command(facts):
    key = recording_key(CALL_STATE, CALL_QUESTIONS, "low")
    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(fakes, "RECORDINGS", Path(tmp)):
        try:
            ReplayJudge()(CALL_STATE, CALL_QUESTIONS, label="check@1", level="low")
        except MissingRecording as exc:
            text = str(exc)
        else:
            return False, "no MissingRecording was raised"
    ok = COMMAND in text and f"{key}.json" in text and "live credentials" in text
    return ok, f"names {key}.json and `{COMMAND}`, and says it needs live credentials" if ok else text


def replay_order(facts):
    """A passed script answers first, in order, so a routing check gets its exact confidence; a recording answers once
    the script is spent; with neither, MissingRecording."""
    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(fakes, "RECORDINGS", Path(tmp)):
        key = recording_key(CALL_STATE, CALL_QUESTIONS, "low")
        save(tmp, key, {"key": key, "model": "stub-model", "usage": {}, "ms": 1, "answers": RECORDED})
        judge_ = ReplayJudge(script=[SCRIPTED])
        first = judge_(CALL_STATE, CALL_QUESTIONS, label="check@1", level="low")["answers"]
        second = judge_(CALL_STATE, CALL_QUESTIONS, label="check@1", level="low")["answers"]
        try:
            judge_(dict(CALL_STATE, subject="another mail"), CALL_QUESTIONS, label="check@1", level="low")
        except MissingRecording:
            missing = True
        else:
            missing = False
    if first != SCRIPTED:
        return False, "the passed script did not answer first"
    if second != RECORDED:
        return False, "the recording did not answer once the script was spent"
    if not missing:
        return False, "a call with neither a script nor a recording was not MissingRecording"
    return True, "script first, then the recording, then MissingRecording"


def no_steps(org, facts):
    """Nothing to set up: each check runs the recorder or the replay itself, on a stub engine in a tempfile folder."""


SCENARIOS = [
    Scenario(90, "recorder-replay", "L4 recorder and replay judge: inputs, one recording per input, refusals, order",
             no_steps, [
                 Check("inputs: every input parses and carries its set's state fields", WAVE, inputs_valid),
                 Check("inputs: ids and recording keys are unique", WAVE, ids_and_keys_unique),
                 Check("inputs: every email address is at acme.example", WAVE, emails_at_acme),
                 Check("recorder: one file per input, named by key, holding the engine's answers", WAVE,
                       one_file_per_input),
                 Check("recorder: a second run skips every input and calls no engine", WAVE, second_run_skips),
                 Check("recorder: force answers every input again", WAVE, force_answers_again),
                 Check("recorder: a wrong-shape answer fails and is not saved", WAVE, wrong_shape_not_saved),
                 Check("replay: a recorded call returns its recorded answers", WAVE, replay_returns_recording),
                 Check("replay: with no recording, MissingRecording names the re-record command", WAVE,
                       replay_missing_names_command),
                 Check("replay: a passed script answers first, then the recording, then MissingRecording", WAVE,
                       replay_order),
             ]),
]
