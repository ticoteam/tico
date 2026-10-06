"""Eval contracts without a live Tico or model call."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("docs_eval", Path(__file__).resolve().parents[2] / "docs-eval/run.py")
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)


def test_exact_values_and_negation_belong_to_the_claim():
    assert E.exact_fact("Costs $29 per month; refunds within 14 days", "$29")
    assert not E.exact_fact("Costs $299 or $29.99; refunds within 114 days", "$29")
    assert not E.exact_fact("within 114 days", "14 days")
    fact = {"subject": "monthly", "predicate": "refund(?:able|s)?", "polarity": "negative"}
    assert E.fact_matches("Monthly plans are not refundable", fact)
    assert E.fact_matches("Monthly plans are non-refundable", fact)
    assert E.fact_matches("Refunds are not available on monthly plans", fact)
    assert not E.fact_matches("Monthly plans have no cancellation penalties and are refundable", fact)
    assert not E.fact_matches("Monthly plans are refundable, but annual plans are not", fact)
    assert not E.fact_matches("Monthly plans are refundable, not annual plans", fact)
    assert not E.fact_matches("Monthly plans are not refundable. Monthly plans are refundable.", fact)
    assert E.score({"manual": ["hermes-agents"], "contains": ["/reload-mcp"]},
                   "Run `/reload-mcp`. [Tico manual · Hermes](https://github.com/ticoteam/tico/blob/main/docs/hermes-agents.md)", {})["cited"]


def setup_eval(monkeypatch, *, fail_import=False):
    monkeypatch.setenv("TICO_URL", "http://example.com")
    monkeypatch.setenv("TICO_TOKEN", "fake-test-token")
    monkeypatch.setattr(E, "load_questions", lambda: [{"id": "price", "question": "Price?", "cite": ["price.md"], "contains": ["$29"]}])
    calls = []
    class FakeTico:
        def __init__(self, *args):
            pass
        def call(self, method, path, body=None):
            calls.append((method, path, body))
            if path == "/api/v2/librarian":
                return {"available": True}
            return {"doc": {"version": 1}}
    monkeypatch.setattr(E, "Tico", FakeTico)
    def load(tico, ids):
        ids[E.PREFIX + "price.md"] = "created-id"
        if fail_import:
            raise RuntimeError("second import failed")
    monkeypatch.setattr(E, "load_fixture", load)
    monkeypatch.setattr(E, "ask", lambda *args: ("Costs $299. [Internal doc · Pricing](doc:created-id)", 1))
    return calls


def test_wrong_cited_fact_fails_the_gate_and_archives_this_runs_docs(monkeypatch):
    calls = setup_eval(monkeypatch)
    assert E.main(["--fail-under", "1"]) == 1
    assert any(method == "PATCH" and body["archived"] for method, path, body in calls)


def test_unknown_selection_never_connects(monkeypatch):
    calls = setup_eval(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        E.main(["--only", "typo"])
    assert exc.value.code == 2 and not calls


def test_partial_import_cleanup_and_keep_report(monkeypatch, capsys):
    calls = setup_eval(monkeypatch, fail_import=True)
    with pytest.raises(RuntimeError, match="second import"):
        E.main([])
    assert ("GET", "/api/v2/docs/created-id", None) in calls
    assert any(method == "PATCH" for method, _, _ in calls)
    calls.clear()
    with pytest.raises(RuntimeError):
        E.main(["--keep"])
    assert not any(method == "PATCH" for method, _, _ in calls)
    assert "Retained fixture IDs: created-id" in capsys.readouterr().err


def test_value_claims_reject_negation_contradiction_wrong_subject_and_double_negatives():
    fact = {"subject": "studio", "predicate": "cost|price|month", "value": "$29"}
    assert E.fact_matches("Studio costs $29 per month.", fact)
    assert E.fact_matches("Studio costs $29. Team costs $79.", fact)
    assert E.fact_matches("Studio costs $29 and Team costs $79.", fact)
    for wrong in ["Studio does not cost $29. It costs $99.", "Studio costs $29. Studio costs $99.",
                  "Team costs $29. Studio costs $99.", "Studio costs $299.", "Studio costs $29.99.",
                  "Studio costs $29. Its price is $99.", "Studio costs $29. Actually, it costs $99.",
                  "Studio costs $29–$99.", "Studio costs $29 to $99.", "Studio costs at least $29.",
                  "Studio costs $29. Their price is $99.",
                  "Studio costs $29. The price is $99.", "Studio costs $29. In reality, it costs $99.",
                  "Studio costs roughly $29.", "Studio costs about $29.", "Studio costs approximately $29.",
                  "Studio costs around $29.", "Studio costs ~$29.", "Studio costs between $29 and $99.",
                  "Studio costs from $29 to $99.", "Studio costs $19–$29."]:
        assert not E.fact_matches(wrong, fact), wrong
    assert E.fact_matches("Studio costs $29. Its price is $29.", fact)
    assert E.fact_matches("Studio costs $29. The price is $29.", fact)
    assert E.fact_matches("Studio costs $29. In reality, it costs $29.", fact)
    assert not E.fact_matches("Team costs $29. Its price is $29.", fact)
    for wrong in ["Its price is $99.", "Actually, it costs $99.", "The price is $99.",
                  "In reality, it costs $99."]:
        answer = "Studio costs $29. " + wrong + " [Internal doc · Pricing](doc:d1)"
        scored = E.score({"cite": ["sales/pricing.md"], "facts": [fact]}, answer,
                         {E.PREFIX + "sales/pricing.md": "d1"})
        assert scored["cited"] and not scored["facts"], wrong
    assert E.fact_matches("Studio costs $29 from the pricing docs, updated 2026-09-12.", fact)
    assert E.fact_matches("Studio costs $29. Team costs $99. Its price is $99.", fact)
    assert E.fact_matches("Studio costs $29. The Team plan is $99.", fact)
    assert E.fact_matches("Studio costs $29 per month, with no variation.", fact)
    assert E.fact_matches("Studio costs $29 per month, with no price variation.", fact)
    assert E.fact_matches("Studio costs **$29** per month.", fact)
    monthly = {"subject": "monthly", "predicate": "refund(?:able|s)?", "polarity": "negative"}
    assert not E.fact_matches("Monthly plans are not non-refundable.", monthly)
    assert not E.fact_matches("Monthly plans aren't not refundable.", monthly)
    cited = "Studio does not cost $29. It costs $99. [Internal doc · Pricing](doc:d1)"
    assert not E.score({"cite": ["sales/pricing.md"], "facts": [fact]}, cited,
                       {E.PREFIX + "sales/pricing.md": "d1"})["facts"]
    assert not E.score({"unknown": True}, "Not in the docs: no policy.", {})["unknown_ok"]
    assert E.score({"unknown": True}, "Not in the docs. No policy.", {})["unknown_ok"]
    ids = {q["id"] for q in E.load_questions()}
    assert ids >= {"change-instructions", "add-human-signin", "copy-credential-grants", "reopen-task"}


@pytest.mark.parametrize("answer", ["Studio costs $29. In practice, it costs $99.", "Studio costs $29, give or take."])
def test_a_later_sentence_contradicting_the_fact_fails_it(answer):
    fact = {"subject": "studio", "predicate": "cost|price|month", "value": "$29"}
    assert not E.fact_matches(answer, fact)
    assert E.fact_matches("Studio costs $29. Team costs $99. Its price is $99.", fact)
