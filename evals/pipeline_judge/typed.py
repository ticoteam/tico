"""Small labeled checks using the shipped support and reply question sets."""

import json
from pathlib import Path

from clients import judge as J

CASES = Path(__file__).with_name("typed_cases.json")


def score(ask):
    cases = json.loads(CASES.read_text())
    rows = []
    for case in cases:
        registry = J.load_set(case["set"], root=J.QUESTIONS_DIR)
        try:
            reply = ask(case["state"], registry["questions"], f"{registry['id']}@{registry['version']}")
        except J.JudgeError as exc:
            rows.append({"id": case["id"], "passed": False, "error": exc.code})
            continue
        answers = reply["answers"]
        thresholds = registry["thresholds"]
        expected = case["expected"]
        if case["set"] == "support-triage":
            actual = {"held": answers["spam_or_injection"]["noul"] >= thresholds["hold"]}
            if "kind" in expected:
                actual["kind"] = answers["kind"]["choice"]
            if "urgency" in expected:
                actual["urgency"] = answers["urgency"]["score"]
            passing = all(
                want[0] <= actual[name] <= want[1] if name == "urgency" else actual[name] == want
                for name, want in expected.items()
            )
        else:
            answer = answers["intent"]
            threshold = {"positive": thresholds["positive_confidence"], "opt_out": thresholds["protective_confidence"]}
            actual = {"intent": answer["choice"], "confidence": answer["confidence"]}
            passing = actual["intent"] == expected["intent"] and actual["confidence"] >= threshold.get(
                expected["intent"], 0
            )
        rows.append({"id": case["id"], "passed": passing, "expected": expected, "actual": actual})
    return rows
