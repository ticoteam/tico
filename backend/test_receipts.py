"""Pure receipt recognition shared by review handoffs and release evidence collection.

Receipts remain ordinary ticket text. A result and its exact head must be on the
same line; a base SHA or a promise to run tests cannot stand in for a result.
"""
import re

SHA = re.compile(r"\b[0-9a-f]{40}\b", re.I)
HEAD = re.compile(r"\bhead\s*[:=]?\s*`?([0-9a-f]{40})\b", re.I)
TEST = re.compile(r"\b(tests?|pytest|cargo|browser|CI|receipt)\b", re.I)
PASS = re.compile(r"\b(pass(?:ed)?|green|success(?:ful)?)\b", re.I)
FAIL = re.compile(r"\b(fail(?:s|ing|ed|ures?)?|error(?:s|ed)?|timed?\s*out|timeouts?|cancelled|canceled)\b", re.I)
ZERO = re.compile(r"\b(?:0|no)\s+(?:fail(?:s|ing|ed|ures?)?|error(?:s|ed)?)\b|"
                  r"\b(?:fail(?:s|ing|ed|ures?)?|error(?:s|ed)?)\s*[:=]\s*(?:0|none)\b", re.I)
PLAN = re.compile(r"\b(not run|pending|will|planned|0\s*pass(?:ed)?|(?:no|not|without)\s+(?:pass(?:ed)?|green|success))\b", re.I)
REMOVED = ("deleted_at", "hidden_at", "superseded_at")
# These describe a repair or history, not the outcome of the named run. Remove
# only the phrase: a separate "2 failed" in the same receipt must still block.
_FAILURE = r"(?:fail(?:s|ing|ed|ures?)?|errors?)"
_FIX = r"(?:fix(?:ed|ing)|repair(?:ed|ing)|resolv(?:ed|ing)|correct(?:ed|ing))"
# A failure placed on an earlier commit is not this head's result; an earlier
# run at the same head is, so "run" is deliberately absent.
_ELSEWHERE = (r"(?:(?:seen|found|reported|observed)\s+)?(?:at|on|from|in)\s+(?:the\s+)?"
              r"(?:prior|previous|earlier|older|old|last)\s+(?:head|commit|push)\b")
# A historical subject can still describe a failure that is happening now.
CURRENT_FAILURE = re.compile(
    rf"\b{_FAILURE}\s+(?:tests?\s+)?(?:{_ELSEWHERE}\s+)?"
    rf"(?:(?:(?:is|was|were|are|has|have|had|been|now|already)\s+){{0,5}}"
    rf"{_FIX}\s*(?:[;,]\s*)?(?:but\s+)?(?:(?:they|it|these|those|some|the\s+(?:test\s+)?{_FAILURE})\s+)?)?"
    r"(?:(?:is|are|has|have|been|now|still|keeps)\s+){0,4}"
    r"(?:recurring|recurs|recurred|reoccurred|returned|persisting|persists|persistent|present|happening|"
    r"(?:is|are|was|were|came|comes|coming)\s+back)\b", re.I)
HISTORY = re.compile(
    rf"\b(?:earlier|previous|historical|old)\s+(?:test\s+)?{_FAILURE}\b|"
    rf"\b{_FIX}\s+(?:(?:the|a|an|earlier|previous|test|fixture|[0-9]+)\s+){{0,5}}{_FAILURE}\b|"
    rf"\b{_FAILURE}\s+(?:tests?\s+)?"
    rf"(?:(?:is|was|were|are|has|have|had|been|now|already)\s+){{0,5}}{_FIX}\b|"
    rf"\b{_FAILURE}\s+{_ELSEWHERE}", re.I)


CONTINUATION = re.compile(
    rf"^\s*[0-9]+\s+(?:(?:earlier|previous|historical|old|test)\s+){{0,3}}"
    rf"(?:{_FAILURE}|pass(?:ed)?|skip(?:ped)?)\b|\b[0-9]+\s+(?:tests?\s+)?{_FAILURE}\b|"
    rf"\b{_FAILURE}\s*[:=]?\s*[0-9]+\b", re.I)


def _recognise(line, test_context=False):
    """Recognise one head's result clause, retaining failure precedence."""
    match = HEAD.search(line)
    shas = list(SHA.finditer(line))
    if not test_context and not TEST.search(line):
        return None
    head = match[1].lower() if match else shas[0][0].lower() if len(shas) == 1 else None
    outcome = ZERO.sub("", line)
    def history(match):
        # "2 earlier failed" is still a counted outcome. Repair phrases such
        # as "2 failures now fixed" describe a repair even when they have counts.
        counted = re.search(r"\b[1-9][0-9]*\s+$", outcome[:match.start()])
        historical = re.match(r"(?:earlier|previous|historical|old)\b", match[0], re.I)
        return match[0] if counted and historical else ""
    result = HISTORY.sub(history, outcome)
    status = ("failed" if CURRENT_FAILURE.search(outcome) or FAIL.search(result) else "unrecognised" if PLAN.search(result)
              else "passed" if PASS.search(result) else "unrecognised")
    return {"head": head, "status": status, "text": line}


def recognise_many(line):
    """Bind separate result clauses to their own heads, including within one line.

    A semicolon alone cannot clear a failure: an unheaded continuation stays
    with its run. Only another explicit head starts a separate result.
    """
    found = []
    sentences = []
    for sentence in re.split(r"[.!?]\s+", line):
        # An unheaded counted failure belongs to the previous run, including
        # labels such as "Re-run:" or "Latest run:" after a period.
        # Other sentences (plans, repairs, commentary) retain their own scope.
        if sentences and not SHA.search(sentence) and CONTINUATION.search(sentence):
            sentences[-1] += "; " + sentence
        else:
            sentences.append(sentence)
    for sentence in sentences:
        parts = re.split(r"[;,]\s*|\s+(?:and|but|after|while)\s+", sentence, flags=re.I)
        clauses = []
        for part in parts:
            if clauses and (not SHA.search(part) or not SHA.search(clauses[-1])
                            or re.search(r"\bbase\b", clauses[-1], re.I) and not HEAD.search(clauses[-1])):
                clauses[-1] += "; " + part
            else:
                clauses.append(part)
        for clause in clauses:
            # A counted result sentence is part of a receipt line even when only
            # an earlier sentence names the tests ("Re-run at head H: 2 failed").
            context = TEST.search(sentence) or TEST.search(line) and CONTINUATION.search(sentence)
            if receipt := _recognise(clause, test_context=bool(context)):
                found.append({**receipt, "text": line})
    return found


def recognise(line):
    """First head's result with failure precedence; collection uses every head."""
    found = recognise_many(line)
    if not found:
        return None
    return next((r for r in found if r["head"] == found[0]["head"] and r["status"] == "failed"), found[0])


def sources(task):
    """The task's own evidence and scoped evidence from review children.

    Collectors supply scoped messages. Keeper notices are accepted only when they
    describe ready-for-review evidence, never arbitrary keeper chatter.
    `receipt_authors` is supplied by the review gate's permission-aware collector;
    callers without one retain the owner-only rule.
    """
    out = []
    for item in [task, *task.get("children", [])]:
        if item is not task and not re.match(r"^Review [12](?:\b|:)", item.get("title", ""), re.I):
            continue
        if item.get("note"):
            out.append({"task": item["id"], "message": None, "actor": item.get("owner"),
                        "created": item.get("updated") or "", "source": "note", "text": item["note"]})
        for message in item.get("messages", []):
            if any(message.get(k) for k in REMOVED):
                continue
            actor = message.get("from_actor")
            text = message.get("body", "")
            keeper = actor == "keeper" and re.search(r"ready[ -]for[ -]review|Review [12]", text, re.I)
            allowed = {item.get("owner"), task.get("owner"), *item.get("receipt_authors", [])}
            if actor not in allowed and not keeper:
                continue
            out.append({"task": item["id"], "message": message.get("id"), "actor": actor,
                        "created": message.get("created") or "", "source": "message", "text": text})
    return sorted(out, key=lambda s: (s["created"], s["message"] or ""))


def receipts(task):
    return [{**source, **receipt} for source in sources(task) for line in source["text"].splitlines()
            for receipt in recognise_many(line)]


def at_head(task, head):
    """A known failure cannot be erased by a later passing rerun at the same head."""
    found = receipts(task)
    current = [r for r in found if r["head"] == head]
    if any(r["status"] == "failed" for r in current):
        return "failed", found
    if current:
        return current[-1]["status"], found
    return ("stale" if any(r["head"] for r in found) else "unrecognised" if found else "missing"), found
