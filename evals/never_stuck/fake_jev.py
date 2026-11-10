"""A fake Jev for the fault rig: the model behind `pipeline_judges.judge_once`, seeded, never a real call.

It is installed as `pipeline_judges.ENGINE`, the hook judge_once asks when one is set, so everything around the model
call is the real code: the Jev step (`page_verdicts.run`, track B), judge_once's bands, the verdict store and its tries.

What it answers for a page is set by whoever wrote the page (`say(digest, verdict)`, keyed by the page's bytes, which
judge_once is shown as the attachment's `digest`), else read from a word in the page's text (`jev:met`,
`jev:not_met`, `jev:unsure`), else met. With faults on it is sometimes unsure of a page it would pass, and sometimes
the call fails (a JudgeError, as the live engine raises when the model does not answer)."""

import random
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

MET, NOT_MET, UNSURE, FAIL = "met", "not_met", "unsure", "fail"
P = {MET: 0.97, NOT_MET: 0.03, UNSURE: 0.5}
REASONS = {
    MET: "The page does what the requirement asks.",
    NOT_MET: "The page does not show what the requirement asks.",
    UNSURE: "The page is not clear enough to tell.",
}


class FakeJev:
    def __init__(self, rng: random.Random | None = None, *, unsure_rate: float = 0.0, fail_rate: float = 0.0) -> None:
        self.rng = rng or random.Random(0)
        self.unsure_rate, self.fail_rate = unsure_rate, fail_rate
        self.said: dict[str, str] = {}  # digest -> met, not_met, unsure or fail
        self.calls: list[tuple[str, str]] = []  # (digest, what it answered)
        self.counts: Counter[str] = Counter()
        self.down = False  # every call fails

    def say(self, digest: str, verdict: str) -> None:
        """What Jev answers for the page with these bytes: met, not_met, unsure, or fail (every call fails)."""
        assert verdict in (MET, NOT_MET, UNSURE, FAIL), verdict
        self.said[digest] = verdict

    def _wanted(self, attachment: dict[str, Any]) -> str:
        digest = str(attachment.get("digest") or "")
        if digest in self.said:
            return self.said[digest]
        text = str(attachment.get("text") or "")
        for word in (NOT_MET, UNSURE, FAIL, MET):  # not_met before met: "jev:not_met" holds "met" too
            if f"jev:{word}" in text:
                return word
        return MET

    def engine(self, model: str, state: dict[str, Any], questions: dict[str, Any], label: str) -> dict[str, Any]:
        from clients import judge as J

        attachment = state.get("attachment") or {}
        want = self._wanted(attachment)
        if want == MET and self.unsure_rate and self.rng.random() < self.unsure_rate:
            want = UNSURE
        if self.down or want == FAIL or (self.fail_rate and self.rng.random() < self.fail_rate):
            self.calls.append((str(attachment.get("digest") or ""), FAIL))
            self.counts[FAIL] += 1
            raise J.JudgeError("unavailable", "Jev did not answer", retryable=True)
        self.calls.append((str(attachment.get("digest") or ""), want))
        self.counts[want] += 1
        return {"p": P[want], "reason": REASONS[want], "model": model, "cost_usd": 0.0, "latency_ms": 1}

    @contextmanager
    def installed(self) -> Iterator["FakeJev"]:
        """Jev is this fake while the block runs; whatever was there is put back."""
        from backend import pipeline_judges

        saved = pipeline_judges.ENGINE
        pipeline_judges.ENGINE = self.engine
        try:
            yield self
        finally:
            pipeline_judges.ENGINE = saved
