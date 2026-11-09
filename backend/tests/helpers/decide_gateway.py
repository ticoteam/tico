"""Helpers that other tests share, moved out of test_decide_gateway.py so they import no test file."""

GATEWAY_URL = "https://hq.example/v1/gateway/decisions"


def answer_all(questions):
    """A right-shaped answer to every question asked."""
    out = {}
    for qid, q in questions.items():
        if q["type"] == "choice":
            names = list(q["criteria"])
            out[qid] = {
                "choice": names[-1],
                "confidence": 0.8,
                "probabilities": {n: (0.8 if n == names[-1] else 0.2 / (len(names) - 1)) for n in names},
            }
        elif q["type"] == "score":
            out[qid] = {"score": 0.0, "confidence": 0.9, "probabilities": {"0": 0.9, "1": 0.1}}
        else:
            out[qid] = {"noul": 0.1}
    return out
