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
ZERO = re.compile(r"\b(?:0|no)\s+(?:fail(?:s|ing|ed|ures?)?|error(?:s|ed)?)\b", re.I)
PLAN = re.compile(r"\b(not run|pending|will|planned|0\s*pass(?:ed)?|(?:no|not|without)\s+(?:pass(?:ed)?|green|success))\b", re.I)
REMOVED = ("deleted_at", "hidden_at", "superseded_at")
# These describe a repair or history, not the outcome of the named run. Remove
# only the phrase: a separate "2 failed" in the same receipt must still block.
_FAILURE = r"(?:fail(?:s|ing|ed|ures?)?|errors?)"
_FIX = r"(?:fix(?:ed|ing)|repair(?:ed|ing)|resolv(?:ed|ing)|correct(?:ed|ing))"
_PAST = r"(?:earlier|previous|prior|historical|old)"
# A non-zero count, in digits or words ("Two failures remain").
_COUNT = r"(?:[1-9][0-9]*|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|several|multiple)"
# Where an earlier failure was seen ("seen at the prior head"). Only these words
# may sit between the failure and its repair, so "2 failed tests need fixing"
# and "2 failures were not fixed" stay failures.
_SEEN_TERM = (rf"(?:seen|found|reported|observed|at|on|in|from|the|a|an|last|{_PAST}|head|run|"
              r"commit|build|CI|tests?)")
_SEEN_WORD = rf"{_SEEN_TERM}\s+"
_SEEN = rf"(?:{_SEEN_WORD}){{0,8}}"
_AUX_WORD = r"(?:is|was|were|are|has|have|had|been|now|already)\s+"
_AUX = rf"(?:{_AUX_WORD}){{0,5}}"
# After a where-seen gap the repair must be the clause's own verb ("have been
# fixed.", "at prior head, now fixed."), so "2 failed on the commit fixing the
# parser" or "2 failed in the repaired fixture" stays a failure.
_SEEN_REPAIR = (rf"(?:{_SEEN_WORD}){{0,7}}{_SEEN_TERM}(?:\s*[;,]\s*|\s+)(?:{_AUX_WORD}){{1,5}}"
                r"(?:fixed|repaired|resolved|corrected)"
                r"(?=\s*(?:[.;,:!?)]|$|\b(?:but|and)\b))")
# A historical subject can still describe a failure that is happening now.
CURRENT_FAILURE = re.compile(
    rf"\b{_FAILURE}\s+(?:tests?\s+)?"
    rf"(?:{_SEEN}{_AUX}{_FIX}\s*[;,]?\s*(?:but\s+)?(?:(?:they|it|this|that)\s+)?)?"
    r"(?:(?:is|are|has|have|been|now|still|keeps)\s+){0,4}"
    r"(?:recurring|recurs|recurred|reoccurred|returned|persisting|persists|persistent|present|happening|"
    r"remain(?:s|ing)?|(?:is|are)\s+(?:still\s+)?back)\b", re.I)
HISTORY = re.compile(
    rf"\b(?:{_PAST}\s+)?(?:test\s+)?{_FAILURE}\s+(?:tests?\s+)?(?:{_SEEN_REPAIR}|{_AUX}{_FIX}\b)|"
    rf"\b{_PAST}\s+(?:test\s+)?{_FAILURE}\b|"
    rf"\b{_FIX}\s+(?:(?:the|a|an|earlier|previous|prior|test|fixture|[0-9]+)\s+){{0,5}}{_FAILURE}\b", re.I)
# A counted failure in a clause that names its own head is a failure at that
# head even without a test word ("Re-run at head <sha>: 2 failed"). It never
# makes a pass: "Lint at head <sha>: passed after 2 errors fixed" is no receipt.
COUNTED_FAILURE = re.compile(
    rf"\b{_COUNT}\s+(?:{_PAST}\s+)?(?:tests?\s+)?{_FAILURE}\b|"
    rf"\b{_FAILURE}(?:\s+tests?)?\s*[:=]\s*[1-9][0-9]*\b", re.I)


def _tested(text):
    """A test word inside a repair phrase ("2 failing tests were fixed") says
    nothing about this run, so it never makes a lint or deploy pass a receipt."""
    return TEST.search(HISTORY.sub(lambda m: "" if re.search(rf"\b{_FIX}\b", m[0], re.I) else m[0], text))


CONTINUATION = re.compile(
    rf"^\s*(?:[0-9]+|{_COUNT})\s+(?:(?:{_PAST}|test)\s+){{0,3}}"
    rf"(?:{_FAILURE}|pass(?:ed)?|skip(?:ped)?)\b|\b{_COUNT}\s+(?:tests?\s+)?{_FAILURE}\b|"
    rf"\b{_FAILURE}(?:\s+tests?)?\s*[:=]\s*[1-9][0-9]*\b", re.I)


def _recognise(line, test_context=False, failure_only=False):
    """Recognise one head's result clause, retaining failure precedence.

    `failure_only` context (a headed failure count, no test word) yields only
    a failure; anything else in that clause is not a test receipt.
    """
    match = HEAD.search(line)
    shas = list(SHA.finditer(line))
    tested = test_context or _tested(line)
    if not tested and not failure_only:
        return None
    head = match[1].lower() if match else shas[0][0].lower() if len(shas) == 1 else None
    outcome = ZERO.sub("", line)
    def history(match):
        # "2 earlier failed" is still a counted outcome. Repair phrases such
        # as "2 failures now fixed" describe a repair even when they have counts.
        counted = re.search(rf"\b{_COUNT}\s+$", outcome[:match.start()], re.I)
        historical = re.match(rf"{_PAST}\b", match[0], re.I) and not re.search(rf"\b{_FIX}\b", match[0], re.I)
        return match[0] if counted and historical else ""
    result = HISTORY.sub(history, outcome)
    status = ("failed" if CURRENT_FAILURE.search(outcome) or FAIL.search(result) else "unrecognised" if PLAN.search(result)
              else "passed" if PASS.search(result) else "unrecognised")
    if not tested and status != "failed":
        return None
    return {"head": head, "status": status, "text": line}


def recognise_many(line):
    """Bind separate result clauses to their own heads, including within one line.

    A semicolon alone cannot clear a failure: an unheaded continuation stays
    with its run. Only another explicit head starts a separate result.
    """
    found = []
    sentences = []
    for sentence in re.split(r"[.!?]\s+", line):
        # An unheaded counted failure ("Re-run: 2 failed", "Re-run failed: 2")
        # or a recurring failure belongs to the previous run.
        # Other sentences (plans, repairs, commentary) retain their own scope.
        if sentences and not SHA.search(sentence) and (CONTINUATION.search(sentence) or CURRENT_FAILURE.search(sentence)):
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
            counted = bool(SHA.search(clause) and COUNTED_FAILURE.search(clause))
            if receipt := _recognise(clause, test_context=bool(_tested(sentence)), failure_only=counted):
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
    # The review gate marks each child it collected as a review of the board's review steps and names those steps;
    # a caller that did not classify them gets the Dev board's generic review titles.
    names = [re.escape(n) for n in task.get("review_steps") or ()] or [r"Review \d+"]
    keeper_words = re.compile(r"ready[ -]for[ -]review|" + "|".join(names), re.I)
    for item in [task, *task.get("children", [])]:
        review = item.get("review_check")
        if item is not task and not (review if review is not None
                                     else re.match(r"^Review \d+(?:\b|:)", item.get("title", ""), re.I)):
            continue
        if item.get("note"):
            out.append({"task": item["id"], "message": None, "actor": item.get("owner"),
                        "created_at": item.get("updated_at") or "", "source": "note", "text": item["note"]})
        for message in item.get("messages", []):
            if any(message.get(k) for k in REMOVED):
                continue
            actor = message.get("from_actor")
            text = message.get("body", "")
            keeper = actor == "keeper" and keeper_words.search(text)
            allowed = {item.get("owner"), task.get("owner"), *item.get("receipt_authors", [])}
            if actor not in allowed and not keeper:
                continue
            out.append({"task": item["id"], "message": message.get("id"), "actor": actor,
                        "created_at": message.get("created_at") or "", "source": "message", "text": text})
    return sorted(out, key=lambda s: (s["created_at"], s["message"] or ""))


def _lines(text):
    """An unheaded failure line ("failing tests: 2") continues the headed line above it."""
    lines = []
    for line in text.splitlines():
        if (lines and SHA.search(lines[-1]) and not SHA.search(line)
                and (CONTINUATION.search(line) or CURRENT_FAILURE.search(line))):
            lines[-1] = lines[-1].rstrip() + "\n" + line
        else:
            lines.append(line)
    return lines


def receipts(task):
    return [{**source, **receipt} for source in sources(task) for line in _lines(source["text"])
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
