"""A bot's browser needs a person on a website: one card on the task, the computer's screen and a login card behind it.

`hub browser needs-you <host> --reason ...` (`POST /api/v2/browser/needs-you`) is what a bot with the Chrome tool does
when a site wants something only a person has: a login it was never given, a captcha, a code sent to a phone. Its task
waits on a reconnect card (backend/reconnects.py, key `site:<bot>:<host>`, service `site`) in the task's thread and in
the fixer's Needs you. The card carries the bot's computer (its screen, where the person can take over the browser) and
a login card for the host (`request`, a credential request of kind `login`): saving a login there, or giving the bot
one for that site, ends the wait; so does the person pressing Done (`POST /api/v2/reconnects/{id}/done`). Either way the
bot is woken once, as for every reconnect.
"""
import json

from fastapi import Request
from pydantic import Field

from . import reconnects
from .auth import validate_identity
from .credential_cards import insert_request
from .credentials import LOGIN, clean_sites
from .models import Contract
from .store import H, Problem


class BrowserNeedsYou(Contract):
    host: str = Field(min_length=1, max_length=300)
    reason: str = Field(default="", max_length=300)
    task: str | None = Field(default=None, max_length=100)


def _task(c, ref):
    """The task `ref` names: its id, or the first 8 or more characters of one."""
    ref = str(ref or "").strip()
    if not ref:
        return None
    row = H.task(c, ref)
    if row or len(ref) < 8:
        return row
    found = c.execute("SELECT id FROM tasks WHERE id LIKE ? LIMIT 2", (ref.replace("%", "") + "%",)).fetchall()
    return H.task(c, found[0][0]) if len(found) == 1 else None


def install(app, store, auth):
    @app.post("/api/v2/browser/needs-you")
    def needs_you(request: Request, body: BrowserNeedsYou):
        who = request.state.identity
        if who.role != "bot" or not who.attempt_id:
            raise Problem("forbidden", "Only a bot in a turn asks for a person on a website", 403)
        with store.transaction() as c:
            validate_identity(c, who)
            bot = H.actor_id(who.actor)
            hosts = clean_sites([body.host])
            if not hosts:
                raise Problem("host", "Name the website's host, like acme.example", 422)
            host, reason = hosts[0], " ".join(body.reason.split())
            task = _task(c, body.task)
            if not task:
                raise Problem("no_task", "A person is asked on a task: run this in a turn on a task (HUB_TASK_ID), "
                              "or name the task", 409)
            if task["owner"] != who.actor or task["status"] not in H.ACTIVE_STATUSES:
                raise Problem("no_task", "That task is not a live task of yours", 409)
            person = reconnects.person_for(c, store.settings, bot, task)
            if not person:
                raise Problem("no_person", "Nobody can help on this task: neither this bot's operator nor the company owner "
                              "can read it", 409)
            place = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()
            computer = place["runner_id"] if place else ""
            key = reconnects.site_key(bot, host)
            # The login card behind the card's Enter login: a credential request for this host, saved like any other.
            rid, now = H.new_id(), H.now()
            insert_request(c, {"id": rid, "requester": person, "conversation_id": task["conversation_id"], "bot": bot,
                               "asker": bot, "env": "", "label": "", "format": "", "help_url": "", "kind": LOGIN,
                               "status": "pending", "created": now, "updated": now, "credential_id": None, "message_id": None,
                               "sites_json": json.dumps([host]), "username": ""})
            fix = {"kind": "site", "host": host, "computer": computer, "reason": reason, "request": rid}
            record = reconnects.open(c, task["id"], bot, key, "site", host, person, fix, owner=task["owner"])
            if not record:
                c.execute("DELETE FROM credential_requests WHERE id=?", (rid,))
                raise Problem("no_card", "This task cannot wait on a person now", 409)
            if json.loads(record.get("fix_json") or "{}").get("request") != rid:
                # Asked again while its card stands: that card is the answer, and this login card is not needed.
                c.execute("DELETE FROM credential_requests WHERE id=?", (rid,))
            else:
                c.execute("UPDATE credential_requests SET message_id=? WHERE id=?", (record.get("message_id"), rid))
                H.event(c, who.actor, "browser.needs_you", task["id"], {"host": host, "person": person})
            return {"id": record.get("message_id"), "key": key, "status": "open", "host": host, "computer": computer,
                    "task": task["id"], "person": person,
                    "detail": "The task waits on a card for the person. You are woken when they are done or save a login "
                              "for this site: then try again. Do not ask for the password in words."}

    @app.post("/api/v2/reconnects/{card_id}/done")
    def reconnect_done(request: Request, card_id: str):
        """A person closes a website card: they did what the bot needed (logged in on the screen, passed a check)."""
        who = request.state.identity
        if who.role not in ("owner", "human"):
            raise Problem("forbidden", "A person presses Done", 403)
        with store.transaction() as c:
            validate_identity(c, who)
            reconnects.ensure(c)
            row = c.execute("SELECT * FROM task_reconnects WHERE message_id=? ORDER BY fixed_at IS NULL DESC LIMIT 1",
                            (card_id,)).fetchone()
            task = H.task(c, row["task_id"]) if row else None
            if not row or not task or not H.task_private_readable(c, who.actor, task):
                raise Problem("not_found", "No such card", 404)
            wait = dict(row)
            if wait["service"] != "site":
                raise Problem("state", "This card closes by itself once the account works", 409)
            if who.actor != wait["person"] and not auth.bot_manager(c, who, wait["bot"]):
                raise Problem("forbidden", "Only the person it asks, or someone who manages this bot, closes it", 403)
            if wait["fixed_at"]:
                return {"id": card_id, "status": wait["ended"] or "fixed", "woken": []}
            woken = reconnects.done(c, wait)
            H.event(c, who.actor, "reconnect.done", wait["task_id"], {"key": wait["key"], "bot": wait["bot"]})
            return {"id": card_id, "status": "fixed", "woken": woken}
