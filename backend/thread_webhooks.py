"""Outgoing webhook events for threads: `thread.created`, `message.created` and `message.held` (docs/webhooks.md, the
event list in backend/event_catalog.py).

The threads service (backend/threads.py) calls these inside the write's own transaction, so an endpoint hears of a
write only when it commits: `webhooks.publish` queues rows and never raises. Payloads are thin, as the catalog says:
ids and facts, never a message's words. Who hears each event is decided by `webhooks.view_for` (a thread by its
readers, a held message by owners and admins), so these functions only describe the event.

`message.created` goes to outgoing webhooks alone. Routines and rules are not run for every message, because agents
post a lot of them (docs/threads.md, "Volume").
"""
import logging

from . import webhooks

log = logging.getLogger(__name__)


def thread_facts(thread):
    """The thin payload of a thread: its id, title, audience and where it sits in its tree."""
    return {"id": thread["id"], "title": thread.get("subject") or thread.get("title") or "",
            "audience": thread.get("audience"), "owner_actor": thread.get("owner_actor"),
            "root_id": thread.get("root_id") or thread["id"], "parent_id": thread.get("parent_id"),
            "parent_message_id": thread.get("parent_message_id")}


def message_facts(message, thread):
    """The thin payload of a message: where it was posted and who sent it, never its body."""
    return {"id": message["id"], "thread_id": message.get("conversation_id") or thread["id"],
            "root_id": thread.get("root_id") or thread["id"], "parent_message_id": thread.get("parent_message_id"),
            "seq": message.get("seq"), "from_actor": message.get("from_actor"), "to_actor": message.get("to_actor"),
            "kind": message.get("kind") or "say"}


def _publish(c, type_, subject, data, actor):
    try:
        webhooks.publish(c, type_, subject, data, actor)
    except Exception:  # noqa: BLE001 - a webhook must never fail the write that caused it
        log.warning("webhook event %s was not queued", type_, exc_info=True)


def _owner_reads(c, owner, thread_id, facts):
    """Whether an automation's owner reads this thread. A group stands for no one, so it hears only company-wide ones."""
    kind, _, ident = str(owner).partition(":")
    if kind == "group":
        return facts.get("audience") == "company"
    who = "human:" + ident if kind == "person" else owner
    return webhooks.view_for(c, who, "thread.created", thread_id, facts, {}) is not None


def thread_created(c, thread, actor):
    """A thread was started, by any route (threads.create, dm, home, sub_thread, hubdb.open_conversation): to webhooks
    and to `event` automations. An automation hears only of a thread its owner may read, as an endpoint's owner does."""
    facts = thread_facts(thread)
    _publish(c, "thread.created", thread["id"], facts, actor)
    try:
        from . import automations
        automations.on_event(c, "thread.created", thread["id"], facts, actor,
                             access=lambda row: _owner_reads(c, row["owner"], thread["id"], facts))
    except Exception:  # noqa: BLE001 - an automation problem must never fail the write that caused it
        log.warning("automations did not hear thread.created", exc_info=True)


def message_created(c, message, thread, actor):
    """A message was written into `thread` (threads.post, after the message and its deliveries exist)."""
    _publish(c, "message.created", message["id"], message_facts(message, thread), actor)


def message_held(c, message, thread, hold_id, rule, actor):
    """The org rules held `message` before it reached its thread: `hold_id` is the hold a reviewer releases."""
    data = {**message_facts(message, thread), "rule": rule, "hold": hold_id}
    _publish(c, "message.held", message["id"], data, actor)
