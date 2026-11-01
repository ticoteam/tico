"""Make task from a thread message (POST /api/v2/threads/messages/{message_id}/task).

The server builds the task from the message it reads, never from text the client sends, so the task can't claim words
the viewer may not read, and it keeps the message's privacy: a message outside a company-wide thread, or one tied to a
private task, makes a private task. The body ends with where it came from (author, thread, a link back). The write
itself is `POST /api/v2/tasks` (its rule check, privacy refusals, audit and events), handed in as `create_task`.
"""

from urllib.parse import quote

from fastapi import Request
from pydantic import BaseModel, Field

from . import hubdb as H
from . import models as M
from . import threads
from .names import lookup
from .store import Problem

BODY_MAX = 60_000


class MessageTask(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    owner: str = Field(min_length=1, max_length=200)


def source(c, v, message_id):
    """The message and its root thread, when the viewer reads the message; the same 404 otherwise."""
    message = H.message(c, message_id)
    if not message or not threads.message_readable(c, v, message):
        raise Problem("not_found", "Message not found", 404)
    row = threads.thread_row(c, message["conversation_id"])
    if not row:
        raise Problem("not_found", "Message not found", 404)
    root = threads.thread_row(c, row.get("root_id") or row["id"]) or row
    return message, row, root


def private(message, root):
    """Company-wide threads hold company-visible words; anything narrower stays private, as does a message that belongs
    to a private task (`restricted`)."""
    return threads.audience_of(root) != "company" or bool(message.get("restricted"))


def task_body(c, message, row, base_url=""):
    author = message.get("from_actor") or ""
    name = lookup(c, {author}).get(author) or author.split(":", 1)[-1]
    href = f"{base_url}/#/thread/{quote(row['id'], safe='')}?m={quote(message['id'], safe='')}"
    text = str(message.get("body") or "")[:BODY_MAX]
    return f"{text}\n\nFrom {name} in [{row.get('subject') or 'a thread'}]({href})", href


def install(app, store, auth, create_task, public_url=""):
    @app.post("/api/v2/threads/messages/{message_id}/task")
    def message_task(request: Request, message_id: str, body: MessageTask):
        """A task from a message the viewer reads: its text and a link back to the message, private unless the thread is company-wide."""
        with store.read() as c:
            v = threads.viewer(auth, request.state.identity)
            message, row, root = source(c, v, message_id)
            text, href = task_body(c, message, row, public_url)
            keep_private = private(message, root)
        links = [href] if public_url else []
        create = M.TaskCreate(title=body.title.strip(), body=text, owner=body.owner,
                              private=True if keep_private else None, links=links)
        return create_task(request, create)
