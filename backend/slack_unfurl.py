"""Slack link previews ("unfurls") for links to a task.

A link to a task pasted in Slack arrives as a `link_shared` event on the gateway's Socket Mode
connection when its host is one the app registers for unfurling (`unfurl_domains` in the
manifest). The gateway answers with `chat.unfurl`: the task's number and title, its column or
status, its owner and due date, the start of its description, and an open button.

The links read here, on any host in `hosts()`:
  /task/<id>, /ticket/<number>             either one may carry a readable slug after it
  /open/task/<id>, /open/ticket/<number>   (`/task/<id>/fix-the-login`), which is never read
  /#/task/<id>                             the web app's own task page

The person who pasted the link must be one the gateway admits and must be able to read the task,
and the task must not be private: nothing of a private task goes to Slack. Everyone in the
conversation then sees the preview, as they see the link.
"""

import ipaddress
import re
import urllib.parse

DESCRIPTION_CHARS = 280
TITLE_CHARS = 150
MAX_LINKS = 5                   # previews answered per message; Slack sends a handful at most
TASK_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
PATH_RE = re.compile(r"^/(?:open/)?(task|ticket)/([^/]+)(?:/[^/]*)?/?$")
FRAGMENT_RE = re.compile(r"^/tasks?/(" + TASK_ID + r")(?:[/?].*)?$")
# Errors that mean Slack refused the blocks themselves (an open URL in a scheme its buttons do not take): the
# preview is sent again with the open link as text, which Slack links whatever the scheme.
BLOCK_ERRORS = ("invalid_blocks", "invalid_blocks_format", "invalid_unfurls", "invalid_arguments")


def hosts(settings):
    """The hosts whose links get a preview: the configured ones and the server's own, never an
    address or a loopback name, which Slack cannot register."""
    names = {str(h or "").strip().lower().rstrip(".") for h in getattr(settings, "slack_unfurl_hosts", ()) or ()}
    names.add((urllib.parse.urlsplit(settings.public_url).hostname or "").lower())
    out = set()
    for name in names:
        if not name or name == "localhost" or "." not in name:
            continue
        try:
            ipaddress.ip_address(name)
            continue
        except ValueError:
            out.add(name)
    return out


def parse(url, allowed):
    """("id", <task id>) or ("number", <n>) for a task link on an allowed host, else None."""
    try:
        parts = urllib.parse.urlsplit(str(url or "").strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if parts.scheme not in ("https", "http") or host not in allowed:
        return None
    match = PATH_RE.match(urllib.parse.unquote(parts.path))
    if match:
        kind, ident = match.groups()
        if kind == "ticket":
            return ("number", int(ident)) if re.fullmatch(r"[1-9]\d{0,8}", ident) else None
        return ("id", ident.lower()) if re.fullmatch(TASK_ID, ident, re.I) else None
    if parts.path in ("", "/") and parts.fragment:
        match = FRAGMENT_RE.match(urllib.parse.unquote(parts.fragment))
        if match:
            return ("id", match.group(1))
    return None


def links(event, allowed):
    """[(url, ref)] for the task links in a `link_shared` event, first MAX_LINKS, each url once."""
    out, seen = [], set()
    for link in (event or {}).get("links") or []:
        url = str((link or {}).get("url") or "")
        ref = parse(url, allowed)
        if ref and url not in seen:
            seen.add(url)
            out.append((url, ref))
    return out[:MAX_LINKS]


def find(c, ref):
    """The task row a ref names, or None. Permission is the caller's to check."""
    from . import hubdb as H
    if ref[0] == "number":
        row = c.execute("SELECT id FROM tasks WHERE number=?", (ref[1],)).fetchone()
        return H.task(c, row[0]) if row else None
    return H.task(c, ref[1])


def escape(text):
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def excerpt(body, limit=DESCRIPTION_CHARS):
    """The start of a description as one short paragraph: markdown marks and runs of space folded."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", str(body or ""))                 # images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)                         # links keep their words
    text = re.sub(r"(?m)^\s{0,3}(?:#{1,6}\s+|[-*+]\s+|>\s?|\d+[.)]\s+)", "", text)
    text = re.sub(r"[*_`~]{1,3}", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"
    return text


def state(c, row):
    """What column or status a task is in, in words."""
    if row.get("step_id"):
        step = c.execute("SELECT name FROM task_steps WHERE id=?", (row["step_id"],)).fetchone()
        if step and step[0]:
            return step[0]
    return str(row.get("status") or "open").replace("_", " ").capitalize()


def open_url(settings, task_id):
    template = str(getattr(settings, "slack_open_url", "") or "").strip() or settings.public_url + "/#/task/{id}"
    return template.replace("{id}", urllib.parse.quote(task_id, safe=""))


def preview(c, settings, url, row, button=True):
    """One `chat.unfurl` entry: a title linked to the pasted URL, the description's start, one
    line of facts, and the open button (or, with `button` off, the same link as text)."""
    from .names import lookup
    names = lookup(c, {a for a in (row.get("owner"), row.get("requester")) if a})
    title = (f"#{row['number']} " if row.get("number") else "") + str(row.get("title") or "Untitled task")
    if len(title) > TITLE_CHARS:
        title = title[:TITLE_CHARS - 1].rstrip() + "…"
    text = f"*<{escape(url)}|{escape(title)}>*"
    about = excerpt(row.get("body"))
    if about:
        text += "\n" + escape(about)
    facts = [state(c, row)]
    owner = row.get("owner")
    if owner:
        facts.append("Owner: " + names.get(owner, owner.split(":", 1)[-1]))
    if row.get("due"):
        facts.append("Due " + str(row["due"])[:10])
    label = str(getattr(settings, "slack_open_label", "") or "").strip() or "Open in Tico"
    target = open_url(settings, row["id"])
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}},
              {"type": "context", "elements": [{"type": "mrkdwn", "text": escape(" · ".join(facts))}]}]
    if button:
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": label[:75]}, "url": target,
             "action_id": "open_task", "style": "primary"}]})
    else:
        blocks[1]["elements"].append({"type": "mrkdwn", "text": f"<{escape(target)}|{escape(label)}>"})
    return {"blocks": blocks}
