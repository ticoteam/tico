"""Automation conditions (backend/automation_flow.py `holds`): every op on plain values and on judge answers, `min`."""
import pytest

from backend.automation_flow import all_hold, holds

CHOICE = {"type": "choice", "choice": "reply", "confidence": 0.8, "probabilities": {"archive": 0.2, "reply": 0.8}}
NOUL = {"type": "noul", "noul": 0.9}
SCORE = {"type": "score", "score": 4, "confidence": 0.7}
VALUES = {"message": {"from": "Ana@Acme.example", "subject": "Invoice 42 overdue", "labels": ["billing", "vip"],
                      "size": 12, "empty": None},
          "d1": {"bucket": CHOICE, "is_ask": NOUL, "urgency": SCORE}}


def cond(field, op, value=None, **extra):
    return {"field": field, "op": op, "value": value, **extra}


@pytest.mark.parametrize("c, want", [
    (cond("message.from", "is", "ana@acme.example"), True),            # text compares without case
    (cond("message.from", "is", "ben@acme.example"), False),
    (cond("message.from", "is_not", "ben@acme.example"), True),
    (cond("message.labels", "is", "vip"), True),                        # a list is any of its values
    (cond("message.labels", "is_not", "vip"), False),
    (cond("message.size", "is", 12), True),
    (cond("message.size", ">=", 12), True),
    (cond("message.size", ">=", 13), False),
    (cond("message.size", "<=", "12"), True),
    (cond("message.subject", ">=", 1), False),                          # not a number: never compares
    (cond("message.subject", "contains", "INVOICE"), True),
    (cond("message.subject", "contains", "refund"), False),
    (cond("message.labels", "contains", "billing"), True),
    (cond("message.subject", "matches", r"invoice \d+"), True),
    (cond("message.subject", "matches", r"^overdue"), False),
    (cond("message.nope", "is", "x"), False),                           # missing: only is_not holds
    (cond("message.nope", "is_not", "x"), True),
    (cond("message.nope", "contains", "x"), False),
    (cond("message.nope", "matches", "."), False),
    (cond("message.nope", ">=", 0), False),
    (cond("message.empty", "is", None), True),
])
def test_plain_values(c, want):
    assert holds(c, VALUES) is want


@pytest.mark.parametrize("c, want", [
    (cond("d1.bucket", "is", "reply"), True),
    (cond("d1.bucket", "is", "archive"), False),
    (cond("d1.bucket", "is", "reply", min=0.7), True),
    (cond("d1.bucket", "is", "reply", min=0.85), False),                # the answer, not sure enough
    (cond("d1.bucket", "is_not", "reply", min=0.85), True),
    (cond("d1.bucket", "contains", "rep"), True),
    (cond("d1.is_ask", "is", "yes"), True),
    (cond("d1.is_ask", "is", True, min=0.9), True),
    (cond("d1.is_ask", "is", "yes", min=0.95), False),
    (cond("d1.is_ask", "is", "no"), False),
    (cond("d1.is_ask", ">=", 0.85), True),
    (cond("d1.urgency", "is", 4), True),
    (cond("d1.urgency", ">=", 3), True),
    (cond("d1.urgency", "<=", 3), False),
])
def test_judge_answers(c, want):
    assert holds(c, VALUES) is want


def test_a_choice_without_probabilities_uses_its_confidence():
    answer = {"choice": "reply", "confidence": 0.6}
    assert holds(cond("a", "is", "reply", min=0.5), {"a": answer})
    assert not holds(cond("a", "is", "reply", min=0.7), {"a": answer})


def test_no_conditions_hold_and_any_or_all():
    yes, no = cond("message.size", "is", 12), cond("message.size", "is", 1)
    assert all_hold([], VALUES)
    assert all_hold([yes, yes], VALUES) and not all_hold([yes, no], VALUES)
    assert all_hold([no, yes], VALUES, "any") and not all_hold([no, no], VALUES, "any")


def test_matches_reads_only_the_start_of_a_long_field():
    long = {"message": {"body": "x" * 2000 + "invoice 42"}}
    assert not holds(cond("message.body", "matches", r"invoice \d+"), long)
    assert holds(cond("message.body", "matches", r"invoice \d+"), {"message": {"body": "x" * 1980 + "invoice 42"}})
