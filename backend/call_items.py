"""Action items drafted from a call's transcript once the call has ended.

This is the Recorder's drafting (recorder/src/assist.rs), ported to the server: the same prompt, the same output shape,
and the same rule that an item stands only when its quote is in the transcript word for word. The Recorder drafts while
a meeting runs on its own computer; a call has no such computer, so the server drafts once, after the call, on the
install's meeting model through the Gateway (backend/recorder_relay.py picks the model).

Nothing is stored here. The caller posts the items into the call's thread as one message.
"""
import json
import re

CHAT_TIMEOUT = 60
MAX_ITEMS = 5              # as the Recorder's MAX_PER_REVIEW: one long stretch of speech cannot flood the list
MAX_TITLE = 300

# recorder/src/assist.rs DRAFT_PROMPT, as the Recorder sends it: the call's lines are all NEW, with no EXISTING items.
DRAFT_PROMPT = (
    "You find action items in a meeting transcript while the meeting is happening. An action item is something a "
    "person committed to, asked for, or the group agreed should happen: a task, a follow-up to send, a document to "
    "change. Read the NEW lines (EARLIER lines are context only). Return JSON only: "
    "{\"items\":[{\"title\":\"...\",\"quote\":\"...\"}]}. title: a short imperative sentence naming the deliverable "
    "and, when said, who it is for or by when (\"Send Dana the revised pricing by Friday\"). quote: the exact words, "
    "copied character for character from one NEW line, that the item comes from (at most 25 words). Leave out small "
    "talk, vague ideas, questions nobody took on, things finished during the meeting, and anything already in "
    "EXISTING. Return {\"items\":[]} when there are none."
)


class ItemsUnavailable(Exception):
    """The Gateway did not draft the items: no answer, or an answer that is not the items JSON."""


def _object(text):
    """The JSON object in a reply, also when the model wraps it in prose or a code fence (as the Recorder reads it)."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        found = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def _quoted(quote, lines):
    """The quote as the transcript has it, or None when the model invented it. The match ignores case, and the
    transcript's own spelling is kept."""
    wanted = str(quote or "").strip().strip("\"“”").strip()
    if not wanted:
        return None
    for text in lines:
        found = re.search(re.escape(wanted), text, re.IGNORECASE)
        if found:
            return found.group(0)
    return None


def draft(base, key, model, rows):
    """Action items for `rows` (the call's lines in order, each {"t", "speaker", "text"}): a list of {"title", "quote"},
    at most MAX_ITEMS, each quote found in the transcript. Raises ItemsUnavailable when the Gateway does not answer with
    the items JSON."""
    from clients import gateway as G

    body = {"model": model, "stream": False, "temperature": 0.2, "max_tokens": 1200,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": DRAFT_PROMPT},
                {"role": "user", "content": json.dumps({"EXISTING": [], "EARLIER": [], "NEW": rows},
                                                       ensure_ascii=False)}]}
    status, answer = G.relay("POST", base + "/chat/completions", json.dumps(body).encode("utf-8"),
                             {"Authorization": "Bearer " + key, "Content-Type": "application/json"}, CHAT_TIMEOUT)
    if status != 200:
        raise ItemsUnavailable(f"The Gateway answered {status}")
    try:
        text = json.loads(answer)["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ItemsUnavailable("The Gateway's answer had no text") from None
    found = _object(text or "")
    if found is None or not isinstance(found.get("items"), list):
        raise ItemsUnavailable("The draft was not the items JSON")
    lines = [str(row.get("text") or "") for row in rows]
    items = []
    for item in found["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("title"), str):
            continue
        title = " ".join(item["title"].split())[:MAX_TITLE]
        quote = _quoted(item.get("quote"), lines)
        if title and quote:
            items.append({"title": title, "quote": quote})
        if len(items) == MAX_ITEMS:
            break
    return items
