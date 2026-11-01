"""`hub doc ask "question"`: ask the Librarian and wait for its answer (docs/librarian.md).

A bot sends the Librarian an `ask` message, the ordinary ask and answer path, and waits for the answer
the way `hub question ask` does. A person's script (or the Assistant, acting as that person) uses the route the
Docs page uses, `POST /api/v2/docs/ask`, which puts the question in their own private docs conversation,
then waits for the reply there. Either way the result is the same:

    {"answer": "...", "citations": [{"type": "internal", "title": "...", "url_or_id": "<doc id>"},
                                    {"type": "linked", "title": "...", "url_or_id": "https://..."},
                                    {"type": "manual", "title": "...", "url_or_id": "https://.../v0.2.32/docs/people.md"}],
     "covered": true}

`covered` is false when the Librarian said the docs do not cover the question (its answer then starts
"Not in the docs"). A wait that runs out returns `timeout`, `message_id` and `conversation_id`;
`hub doc ask-status <conversation_id> <message_id>` collects the same answer without sending again.
The server MCP uses short waits so proxies can return these ids before their request timeout.
"""

import re
import time

LIBRARIAN = "librarian"
WAIT_MAX = 300
# The Librarian's citations: [Internal doc · Title](doc:<id>) and [Linked · host or title](https://...).
# A manual page: [Tico manual · Title](https://...), the link its search result carried.
CITATION = re.compile(r"\[(Internal doc|External|Linked|Tico manual) · ([^\]]+)\]\((doc:[^)\s]+|https?://[^)\s]+)\)")


def parse_citations(text):
    found, seen = [], set()
    for kind, title, target in CITATION.findall(str(text or "")):
        internal = kind == "Internal doc"
        if internal != target.startswith("doc:"):
            continue                    # an internal citation is a doc id, a linked one is a web address
        ref = target[len("doc:"):] if target.startswith("doc:") else target
        if (internal, ref) not in seen:
            seen.add((internal, ref))
            found.append({"type": "internal" if internal else "manual" if kind == "Tico manual" else "external" if kind == "External" else "linked",
                          "title": title.strip(), "url_or_id": ref})
    return found


def covered(text):
    return not str(text or "").lstrip().lower().startswith("not in the docs")


def result(text):
    return {"answer": text, "citations": parse_citations(text), "covered": covered(text)}


def final_reply(snapshot, message_id):
    replies = [message for message in snapshot.get("messages") or []
               if message.get("in_reply_to") == message_id and message.get("sender_id") == "bot:" + LIBRARIAN]
    execution = snapshot.get("execution") or {}
    if execution.get("state") in ("queued", "leased", "running", "input", "uncertain"):
        return None
    return max(enumerate(replies), key=lambda pair: (pair[1].get("created_at", ""), pair[0]))[1] if replies else None


def status(api, conversation_id, message_id, wait_s=0, key=None, sleep=time.sleep, clock=time.monotonic):
    wait = max(0, min(int(wait_s or 0), getattr(api, "docs_wait_max", WAIT_MAX)))
    deadline = clock() + wait
    ids = {"conversation_id": conversation_id, "message_id": message_id}
    while True:
        snapshot = api.get(f"conversations/{conversation_id}/snapshot")
        if answer := final_reply(snapshot, message_id):
            if api.get("me").get("role") == "bot":
                api.post(f"messages/{answer['id']}/ack", {}, key=key + ":ack" if key else None)
            return {**result(answer["body"]), **ids}
        if clock() >= deadline:
            return {"timeout": True, **ids}
        sleep(min(2, max(0, deadline - clock())))


def ask(api, question, wait_s=120, key=None, sleep=time.sleep, clock=time.monotonic):
    wait = max(0, min(int(wait_s or 0), getattr(api, "docs_wait_max", WAIT_MAX)))
    ask_key = key + ":ask" if key else None
    if api.get("me").get("role") == "bot":
        sent = api.post("messages", {"to": LIBRARIAN, "text": question, "kind": "ask", "wait_s": wait}, key=ask_key)
        message_id = sent["id"]
    else:
        sent = api.post("docs/ask", {"question": question}, key=ask_key)
        message_id = sent["message_id"]
    return status(api, sent["conversation_id"], message_id, wait, key, sleep, clock)
