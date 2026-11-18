"""Action items and a short summary drafted from a call's transcript once the call has ended.

The server drafts once, after the call, on the install's meeting model through the Gateway (`model`): one model call
answers both. An item stands only when its quote is in the transcript word for word; its owner only when they spoke.

Nothing is stored here. The caller posts the items and the summary into the call's thread as one message.
"""

import json
import re

from . import gateway_connection, providers

CHAT_TIMEOUT = 60
MAX_ITEMS = 5  # one long stretch of speech cannot flood the list
MAX_TITLE = 300
MAX_SUMMARY = 5  # bullets
MAX_BULLET = 300

# A call sends all its lines as NEW, with no EARLIER lines and no EXISTING items.
DRAFT_PROMPT = (
    "You find action items in a meeting transcript while the meeting is happening. An action item is something a "
    "person committed to, asked for, or the group agreed should happen: a task, a follow-up to send, a document to "
    "change. Read the NEW lines (EARLIER lines are context only). Return JSON only: "
    "{\"summary\":[\"...\"],\"items\":[{\"title\":\"...\",\"quote\":\"...\",\"owner\":\"...\"}]}. summary: 3 to 5 "
    "short sentences on what was reported and decided, most important first ([] when nothing of substance was said). "
    "title: a short imperative sentence naming the deliverable "
    "and, when said, who it is for or by when (\"Send Dana the revised pricing by Friday\"). quote: the exact words, "
    "copied character for character from one NEW line, that the item comes from (at most 25 words). owner: the "
    "speaker, exactly as a line's speaker is written, who took the item on; \"\" when nobody did. Leave out small "
    "talk, vague ideas, questions nobody took on, things finished during the meeting, and anything already in "
    "EXISTING. Return {\"summary\":[],\"items\":[]} when there are none."
)


def model(c, settings):
    """The model the items are drafted on: the install's meeting model when it is a Tico model, else the default one."""
    meeting = providers.load(c, settings).get("meeting_model") or ""
    return meeting if gateway_connection.is_tico(meeting) else providers.MEETING_MODEL


class ItemsUnavailable(Exception):
    """The Gateway did not draft the items: no answer, or an answer that is not the items JSON."""


def _object(text):
    """The JSON object in a reply, also when the model wraps it in prose or a code fence."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        found = json.loads(text[start : end + 1])
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
    """The notes for `rows` (the call's lines in order, each {"t", "speaker", "text"}): {"summary": [bullet, ...],
    "items": [{"title", "quote", "owner"}, ...]}, at most MAX_SUMMARY bullets and MAX_ITEMS items, each quote found in
    the transcript and each owner one of its speakers ("" for none). Raises ItemsUnavailable when the Gateway does not
    answer with the items JSON."""
    from clients import gateway as G

    body = {
        "model": model,
        "stream": False,
        "temperature": 0.2,
        "max_tokens": 1600,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": DRAFT_PROMPT},
            {"role": "user", "content": json.dumps({"EXISTING": [], "EARLIER": [], "NEW": rows}, ensure_ascii=False)},
        ],
    }
    status, answer = G.relay(
        "POST",
        base + "/chat/completions",
        json.dumps(body).encode("utf-8"),
        {"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        CHAT_TIMEOUT,
    )
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
    speakers = {str(row.get("speaker") or "") for row in rows} - {""}
    items = []
    for item in found["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("title"), str):
            continue
        title = " ".join(item["title"].split())[:MAX_TITLE]
        quote = _quoted(item.get("quote"), lines)
        owner = item.get("owner") if item.get("owner") in speakers else ""
        if title and quote:
            items.append({"title": title, "quote": quote, "owner": owner})
        if len(items) == MAX_ITEMS:
            break
    return {"summary": _summary(found.get("summary")), "items": items}


def _summary(found):
    """The summary's bullets: strings only, each on one line, at most MAX_SUMMARY. A missing or odd summary is none."""
    if not isinstance(found, list):
        return []
    bullets = [" ".join(b.split())[:MAX_BULLET] for b in found if isinstance(b, str)]
    return [b for b in bullets if b][:MAX_SUMMARY]
