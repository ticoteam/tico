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


def recognise(line):
    """None for unrelated prose; otherwise explicit status, head and original line."""
    if not TEST.search(line):
        return None
    match = HEAD.search(line)
    shas = SHA.findall(line)
    head = match[1].lower() if match else shas[0].lower() if len(shas) == 1 else None
    result = ZERO.sub("", line)
    status = ("failed" if FAIL.search(result) else "unrecognised" if PLAN.search(result)
              else "passed" if PASS.search(result) else "unrecognised")
    return {"head": head, "status": status, "text": line}


def sources(task):
    """Only the task's own evidence, plus owner-authored evidence from review children.

    Collectors supply scoped messages. Keeper notices are accepted only when they
    describe ready-for-review evidence, never arbitrary keeper chatter.
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
            if actor not in (item.get("owner"), task.get("owner")) and not keeper:
                continue
            out.append({"task": item["id"], "message": message.get("id"), "actor": actor,
                        "created": message.get("created") or "", "source": "message", "text": text})
    return sorted(out, key=lambda s: (s["created"], s["message"] or ""))


def receipts(task):
    return [{**source, **receipt} for source in sources(task) for line in source["text"].splitlines()
            if (receipt := recognise(line)) is not None]


def at_head(task, head):
    """A known failure cannot be erased by a later passing rerun at the same head."""
    found = receipts(task)
    current = [r for r in found if r["head"] == head]
    if any(r["status"] == "failed" for r in current):
        return "failed", found
    if current:
        return current[-1]["status"], found
    return ("stale" if any(r["head"] for r in found) else "unrecognised" if found else "missing"), found
