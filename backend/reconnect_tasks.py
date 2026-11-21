# pyright: strict
"""Reconnect tasks: a tool whose key the vendor rejects becomes ONE task for the Tools page's owner, "Reconnect
<vendor>", with a key card in its thread for the person who can put a new key on the tool.

When it opens:
- A tool's status turns Key rejected (backend/api_tools.py `status`: a check's real 401, or REJECTED_AFTER 401s in a
  row on proxied calls, `call_health`). A 403, or a 401 that says a scope is missing, is Works (limited) there and
  opens nothing. `checked` runs after each check writes its rows; `called` after each proxied call.
- A run's failed tool call (its turn diagnostics, backend/run_diagnostics.py) answered 401 for a key the run's bot holds,
  named by its variable in the command (`failed_calls`). Only for a key no check reaches (no check path, or an id): a
  checked tool's own check decides, every CHECK_EVERY, and a bot that misuses a working key opens nothing.

Who gets what:
- The task goes to the Tools page's own owner (backend/page_owners.py, the Tools Owner), else Agent Ops; with neither,
  nothing is filed. It is public (`private` False), so the stage bots that read it are never stranded.
- The key card (backend/credential_cards.py, a request whose `credential_id` names the key it replaces) goes to the
  person who connected the tool (`tools.created_by`) when they may change it, else a credential administrator who may
  (the company owner first). The card stands on the task as its reconnect wait (backend/reconnects.py, key
  `tool:<id>`), so the task is paused on that person and Needs you shows it to them. The value they type replaces the
  key in place (`api_tools.update_key`): every grant stays as it was.

Renew tasks: from api_tools.EXPIRING_DAYS before a key's end date (typed, or read from the vendor on its check), the
tool reads "Expires in N days" and the same task opens as "Renew <vendor> key", with the same key card, naming the date
and the bots that hold the key. It is decided after each check and proxied call, and every hour for every tool with an
end date that close (`sweep`), so a key no check reaches is renewed too. It closes itself when the key's value is
replaced, its date moves past the window (typed, read, or noticed by the sweep) or the key is removed; a key that lapses
first keeps the same task.

One open task a tool: a repeat finds it (event `tool.reconnect_task`, its target the tool) and adds nothing, notifies
nobody. When the tool reads Working (or Works (limited)) again or its key's value is replaced, its card says fixed
and the task closes unless it is in a side stage. When the tool is deleted the task closes as not needed.
"""

import json
import logging
import re
from typing import Any

from pydantic import SecretStr

from . import hubdb, task_engine
from . import reconnects as _reconnects
from .store import Problem as _Problem

# Untyped neighbours, typed as Any at the door (as backend/tool_access.py does).
H: Any = hubdb
RC: Any = _reconnects
Problem: Any = _Problem
Row = Any

log = logging.getLogger(__name__)

EVENT = "tool.reconnect_task"
FIXED = ("Working", "Works (limited)")
# What a failed command printed when the vendor refused its key: an HTTP 401, or the words vendors answer a bad key
# with. A 403, and a 401 that names a missing scope (api_tools.SCOPE_SAYS), are a known key without a permission.
AUTH_ERROR = re.compile(
    r"HTTP/[\d.]+\s+401\b|\b401\s+unauthori[sz]ed\b|returned error:\s*401\b|"
    r"[\"']?(?:status|status_?code|statusCode)[\"']?\s*[:=]\s*[\"']?401\b|"
    r"\b(?:invalid|incorrect|revoked)[ _-]?(?:api[ _-]?key|token|access[ _-]?token|credentials)\b",
    re.IGNORECASE,
)
VARIABLE = re.compile(r"\$\{?([A-Z_][A-Z0-9_]*)\}?")


def _api_tools() -> Any:
    from . import api_tools

    return api_tools


def _module(name: str) -> Any:
    """A neighbour imported when used, by a literal import (the PR test selection reads imports), typed as Any."""
    if name == "tool_services":
        from . import tool_services as found
    elif name == "auth":
        from . import auth as found
    elif name == "events":
        from . import events as found
    elif name == "credentials":
        from . import credentials as found
    else:
        from . import credential_cards as found
    return found


def key_of(tool_id: str) -> str:
    """The tool's reconnect wait key (backend/reconnects.py)."""
    return f"tool:{tool_id}"


def _tool(c: Any, tool_id: str) -> Row:
    return c.execute("SELECT * FROM tools WHERE id=? AND kind='api'", (tool_id,)).fetchone()


def _opened(c: Any, tool_id: str) -> tuple[Row, dict[str, Any]]:
    """The tool's open task and the detail it was opened with, or (None, {})."""
    found = c.execute(
        "SELECT detail FROM events WHERE action=? AND target=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (EVENT, tool_id),
    ).fetchone()
    detail: dict[str, Any] = json.loads(found[0] or "{}") if found else {}
    task: Row = H.task(c, detail.get("task")) if detail.get("task") else None
    return (task, detail) if task and task["status"] in H.ACTIVE_STATUSES else (None, {})


def open_task(c: Any, tool_id: str) -> Row:
    """The tool's open Reconnect task, or None."""
    return _opened(c, tool_id)[0]


def renewing(c: Any, tool_id: str) -> bool:
    """Whether the tool's open task is a Renew task (opened before an end date, not on a rejected key)."""
    return bool(_opened(c, tool_id)[1].get("renew"))


def _ends_soon(c: Any, tool_id: str) -> bool:
    """Whether a stored key of the tool ends within api_tools.EXPIRING_DAYS (or has ended)."""
    api_tools = _api_tools()
    soon = (api_tools._today() + api_tools.timedelta(days=api_tools.EXPIRING_DAYS)).isoformat()
    return bool(
        c.execute(
            "SELECT 1 FROM credentials WHERE tool_id=? AND ciphertext IS NOT NULL AND expires_on IS NOT NULL "
            "AND expires_on<=? LIMIT 1",
            (tool_id, soon),
        ).fetchone()
    )


def _vendor(tool: Row) -> str:
    TS = _module("tool_services")
    return str(
        TS.vendor_name(TS.vendor_of(tool["service"], tool["name"], host=tool["base_url"] or tool["mcp_url"] or ""))
    )


def title_of(tool: Row, renew: bool = False) -> str:
    """The task's title, Reconnect (or Renew ... key, before an end date) and the vendor: the tool's own name when it
    names its vendor (Raygun · production), else the vendor's with it."""
    vendor = _vendor(tool)
    name = str(tool["name"] or "")
    what = "Renew" if renew else "Reconnect"
    tail = " key" if renew else ""
    if not vendor or vendor.lower() in name.lower():
        return f"{what} {name or vendor}{tail}"[:150]
    return f"{what} {vendor} · {name}{tail}"[:150]


# ---------------------------------------------------------------------------------------------------- who
def _owner(c: Any) -> str | None:
    """The bot the task goes to: the Tools page's own owner, else Agent Ops, as an actor; None with neither."""
    from . import page_owners

    found = page_owners.owner(c, "tools", fallback=False)
    for actor in (found, H.bot_actor(H.FLEET_MAINTAINER)):
        bot: Row = H.bot(c, H.actor_id(actor)) if actor else None
        if bot and bot.get("state") not in (None, "archived"):
            return str(H.bot_actor(bot["id"]))
    return None


def _identity(c: Any, actor: str) -> Any:
    """A person as the server would rebuild them for deferred work (Auth.identity_for_actor), or None."""
    A = _module("auth")
    human: Row = H.human(c, actor) if str(actor).startswith("human:") else None
    if not human or A.standing(c, human["id"]):
        return None
    email = str(human.get("email") or "").lower()
    owner = str(getattr(c.settings, "owner_email", "") or "").lower()
    return A.Identity("human:" + human["id"], "owner" if email and email == owner else "human", email=email)


def may_replace(c: Any, who: Any, tool: Row) -> bool:
    """Whether `who` may put a new value on the tool's key: the one Tools rule (backend/tool_access.py)."""
    from . import tool_access as TA

    admins = getattr(c.settings, "credential_admins", ()) or ()
    return bool(who) and TA.may_change_access(c, who, TA.tool(tool), action=TA.EDIT, admins=admins)


def person_for(c: Any, tool: Row) -> str | None:
    """Who the key card asks: whoever connected the tool, when they may change it; else a credential administrator who
    may, the company owner first. None when nobody can."""
    admins = {str(e).lower() for e in (getattr(c.settings, "credential_admins", ()) or ())}
    connector = str(tool["created_by"] or "")
    people = [connector if connector.startswith("human:") else ""]
    others = [
        _identity(c, "human:" + r["id"])
        for r in c.execute("SELECT id, email FROM humans ORDER BY name, id")
        if str(r["email"] or "").lower() in admins
    ]
    people += [p.actor for p in sorted((p for p in others if p), key=lambda p: p.role != "owner")]
    for actor in dict.fromkeys(a for a in people if a):
        if may_replace(c, _identity(c, actor), tool):
            return actor
    return None


# ---------------------------------------------------------------------------------------------------- opening
def _rejected_key(c: Any, tool_id: str) -> Row:
    """The key to replace: the one whose newest check or proxied call was refused, else the tool's Read and write key,
    else any stored one."""
    for sql in (
        "SELECT credential_id FROM tool_checks WHERE tool_id=? AND ok=0 ORDER BY checked_at DESC LIMIT 1",
        "SELECT credential_id FROM tool_calls WHERE tool_id=? AND status_code=401 AND result='sent' "
        "ORDER BY created_at DESC LIMIT 1",
    ):
        found = c.execute(sql, (tool_id,)).fetchone()
        key = (
            c.execute(
                "SELECT * FROM credentials WHERE id=? AND tool_id=? AND ciphertext IS NOT NULL", (found[0], tool_id)
            ).fetchone()
            if found and found[0]
            else None
        )
        if key:
            return key
    return c.execute(
        "SELECT * FROM credentials WHERE tool_id=? AND ciphertext IS NOT NULL "
        "ORDER BY access_level='read_write' DESC, created_at LIMIT 1",
        (tool_id,),
    ).fetchone()


def _ending_key(c: Any, tool_id: str) -> Row:
    """The key to renew: the tool's stored key whose end date comes first."""
    return c.execute(
        "SELECT * FROM credentials WHERE tool_id=? AND ciphertext IS NOT NULL AND expires_on IS NOT NULL "
        "ORDER BY expires_on, created_at LIMIT 1",
        (tool_id,),
    ).fetchone()


def _holders(c: Any, key: Row) -> list[str]:
    """The bots granted the key, by name, at most eight."""
    names: list[str] = []
    for row in c.execute(
        "SELECT DISTINCT subject FROM credential_grants WHERE credential_id=? AND revoked_at IS NULL "
        "AND subject LIKE 'bot:%' ORDER BY subject LIMIT 8",
        (key["id"],),
    ):
        names.append(str(H.actor_name(c, row[0]) or row[0]))
    return names


def _renew_body(c: Any, tool: Row, key: Row) -> str:
    vendor = _vendor(tool) or str(tool["name"])
    said = f" ({vendor} says)" if key["expires_source"] == "vendor" else ""
    holders = _holders(c, key)
    who = (
        f"{', '.join(holders)} use it: put a new value on it before then and their runs keep working."
        if holders
        else "Put a new value on it before then, or remove it if nobody needs it."
    )
    return f"{tool['name']}'s key {key['env'] or key['name']} ends on {key['expires_on']}{said}. {who}"


def _card(c: Any, task: Row, owner: str, person: str, tool: Row, key: Row) -> str | None:
    """The key card in the task's thread, standing on the task as its reconnect wait; the request id."""
    KINDS: tuple[str, ...] = _module("credential_cards").KINDS
    rid = str(H.new_id())
    label = f"a new {tool['name']} key"
    row = {
        "id": rid,
        "requester": person,
        "conversation_id": task["conversation_id"],
        "bot": H.actor_id(owner),
        "asker": H.actor_id(owner),
        "env": key["env"] or "",
        "label": label,
        "format": "",
        "help_url": "",
        "kind": key["kind"] if key["kind"] in KINDS else "api_key",
        "status": "pending",
        "created_at": H.now(),
        "updated_at": H.now(),
        "credential_id": key["id"],  # the key its value replaces (credential_cards.save_card)
        "message_id": None,
    }
    c.execute(
        "INSERT INTO credential_requests VALUES(:id,:requester,:conversation_id,:bot,:asker,:env,:label,:format,"
        ":help_url,:kind,:status,:created_at,:updated_at,:credential_id,:message_id)",
        row,
    )
    bot: Row = H.bot(c, H.actor_id(owner)) or {}
    name = str(bot.get("display_name") or H.actor_id(owner))
    sent: Row = H._write_message(
        c,
        owner,
        person,
        f"{name} needs {label}",
        H.conversation(c, task["conversation_id"]),
        "say",
        {"credential_request": rid, "task": task["id"]},
        None,
        None,
        delivered_at=H.now(),
    )
    message_id = sent["id"] if sent else None
    c.execute("UPDATE credential_requests SET message_id=? WHERE id=?", (message_id, rid))
    waits: Row = RC.open(
        c,
        task["id"],
        H.actor_id(owner),
        key_of(tool["id"]),
        "secret",
        label,
        person,
        {"kind": "secret", "request": rid, "tool": tool["id"]},
        message_id=message_id,
        owner=owner,
    )
    if not waits:
        c.execute("UPDATE credential_requests SET status='canceled',updated_at=? WHERE id=?", (H.now(), rid))
        return None
    _module("events").needs_moved(c, [person], "key")  # the card stands: it is in their Needs you now
    return rid


def start(c: Any, tool_id: str, why: str, renew: bool = False) -> Row:
    """The tool's Reconnect task (with `renew`, its Renew task, before a key's end date), opened now with its key card:
    None when one is already open (a repeat adds nothing), or when nobody can take it."""
    tool = _tool(c, tool_id)
    if tool is None or open_task(c, tool_id):
        return None
    owner = _owner(c)
    if not owner:
        log.warning("No Tools Owner or Agent Ops to reconnect tool %s", tool_id)
        return None
    person = person_for(c, tool)
    key = (_ending_key(c, tool_id) if renew else None) or _rejected_key(c, tool_id)
    who = H.actor_name(c, person) if person else ""
    body = (
        (_renew_body(c, tool, key) + "\n" if renew and key else f"{tool['name']} rejects its key: {why}.\n")
        + (
            f"The key card in this thread asks {who} for a new one: never ask for a key in words."
            if person and key
            else "Nobody here can change its key: ask a credential administrator to replace it in Tools."
        )
        + (
            f"\nThis task closes itself when the key's value is replaced or its end date is more than "
            f"{_api_tools().EXPIRING_DAYS} days away.\n"
            if renew
            else f"\nThis task closes itself when {tool['name']} reads Working again or its key is replaced.\n"
        )
        + f"#/tools/api/{tool_id}"
    )
    try:
        task: Row = H.task_create(
            c, H.TICO_ACTOR, title_of(tool, renew), body, owner, lint=False, deduplicate=False, private=False
        )
    except H.Refused:
        log.warning("Could not file the reconnect task for tool %s", tool_id)
        return None
    rid = _card(c, task, owner, person, tool, key) if person and key else None
    H.event(
        c,
        H.TICO_ACTOR,
        EVENT,
        tool_id,
        {"task": task["id"], "request": rid, "person": person, "why": why, "renew": renew},
    )
    return task


# ---------------------------------------------------------------------------------------------------- closing
def close(c: Any, tool_id: str, why: str, done: bool = True) -> Row:
    """Settle the tool's open Reconnect task's card without a wake. Finish or close the task unless it waits in a side
    stage for an explicit move after a fix. Return the task if it moved."""
    task = open_task(c, tool_id)
    if not task:
        return None
    RC.settle(c, task["id"], key_of(tool_id), "fixed" if done else "retired")
    if done and (task_engine.stage(c, task["stage_id"]) or {}).get("side"):
        return None
    try:
        if done:
            H.task_done(c, H.TICO_ACTOR, task["id"], why)
        else:
            H.task_close(c, H.TICO_ACTOR, task["id"], why)
    except H.Refused:
        H.task_close(c, H.TICO_ACTOR, task["id"], why)
    H.event(c, H.TICO_ACTOR, "tool.reconnect_task_closed", tool_id, {"task": task["id"], "why": why})
    return task


def sync(c: Any, tool_id: str) -> Row:
    """After a check or a proxied call: open the task on Key rejected, close it on Working or Works (limited)."""
    tool = _tool(c, tool_id)
    if tool is None:
        return None
    text = _api_tools().state_of(c, tool)["text"]
    if text == "Key rejected":
        return start(c, tool_id, "its check or its calls got 401")
    if ending(text):
        return start(c, tool_id, "its key's end date is near", renew=True)
    if renewing(c, tool_id) and not _ends_soon(c, tool_id):
        # Its date moved away or its key went, whatever the tool reads now (Not checked has no check to say Working).
        return close(c, tool_id, f"No key of {tool['name']} ends within {_api_tools().EXPIRING_DAYS} days now.")
    if text in FIXED:
        return close(c, tool_id, f"{tool['name']} reads {text} again.")
    return None


def ending(text: str) -> bool:
    """Whether a tool's status says its key's end date is near or past (api_tools.status)."""
    return text.startswith("Expires ") or text == "Key expired"


def sweep(store: Any) -> int:
    """Every hour (backend/scheduler.py): each API tool with a stored key whose end date is EXPIRING_DAYS away or less
    and no open task is synced, so a key no check reaches gets its Renew task too. The tools synced."""
    api_tools = _api_tools()
    soon = (api_tools._today() + api_tools.timedelta(days=api_tools.EXPIRING_DAYS)).isoformat()
    done = 0
    with store.transaction() as c:
        ids = [
            str(r[0])
            for r in c.execute(
                "SELECT DISTINCT k.tool_id FROM credentials k JOIN tools t ON t.id=k.tool_id WHERE t.kind='api' "
                "AND k.ciphertext IS NOT NULL AND k.expires_on IS NOT NULL AND k.expires_on<=? ORDER BY k.tool_id",
                (soon,),
            )
        ]
        # Tools with a Renew task open whose date has moved away since: it ends here too.
        renewals = [
            str(r[0])
            for r in c.execute(
                "SELECT DISTINCT target FROM events WHERE action=? AND json_extract(detail,'$.renew') ORDER BY target",
                (EVENT,),
            )
        ]
        for tool_id in ids:
            if not open_task(c, tool_id):
                _safely(sync, c, tool_id)
                done += 1
        for tool_id in renewals:
            if tool_id not in ids and renewing(c, tool_id):
                _safely(sync, c, tool_id)
                done += 1
    return done


def redated(c: Any, tool_id: str) -> Row:
    """A key's end date was typed, cleared or the key removed: an open Renew task ends when no end date is near."""
    return _safely(sync, c, tool_id) if renewing(c, tool_id) else None


def replaced(c: Any, tool_id: str) -> Row:
    """A key of the tool has a new value (the card, Tools or the API): its open task is done."""
    tool = _tool(c, tool_id)
    return close(c, tool_id, f"{tool['name'] if tool else 'Its'} key was replaced.") if tool else None


def gone(c: Any, tool_id: str) -> Row:
    """The tool was deleted: its open task closes as not needed."""
    return close(c, tool_id, "The tool was deleted.", done=False)


def _safely(fn: Any, *args: Any) -> Any:
    """A hook never fails the write it follows: a check, a proxied call or a run's diagnostics."""
    try:
        return fn(*args)
    except Exception:  # the write it follows stands either way
        log.exception("reconnect task hook %s failed", getattr(fn, "__name__", fn))
        return None


def checked(store: Any, tool_id: str) -> Row:
    """After a check wrote its rows (api_tools.check_tool)."""

    def run() -> Row:
        with store.transaction() as c:
            return sync(c, tool_id)

    return _safely(run)


def called(c: Any, tool_id: str, status_code: int | None) -> Row:
    """After a proxied call was recorded (backend/tool_proxy.py): a 401 may turn the tool Key rejected; a 2xx may make
    it Working again. Anything else changes nothing, and a 401 on a tool with its task open reads nothing more."""
    if status_code == 401 and not open_task(c, tool_id):
        return _safely(sync, c, tool_id)
    if status_code is not None and 200 <= status_code < 300 and open_task(c, tool_id):
        return _safely(sync, c, tool_id)
    return None


# ---------------------------------------------------------------------------------------------------- diagnostics
def _refused(call: Any) -> bool:
    text = str(call.get("output_tail") or "")
    said = text.lower()
    return bool(AUTH_ERROR.search(text)) and not any(p in said for p in _api_tools().SCOPE_SAYS)


def failed_calls(c: Any, bot: str, calls: Any) -> list[str]:
    """A run's failed tool calls (`calls`, models.FailedTool dumps): a 401 for a key the bot holds, named in the command
    by its variable, opens its tool's task when no check reaches that key. The tool ids opened."""
    granted: Any = _module("credentials").granted
    refused = [call for call in calls or () if _refused(call)]
    if not refused or not bot:
        return []
    held: set[str] = {str(k) for k in granted(c, "bot:" + bot)}
    opened: list[str] = []
    for call in refused:
        names = set(VARIABLE.findall(str(call.get("command") or "")))
        if not names:
            continue
        marks = ",".join("?" * len(names))
        for key in c.execute(
            f"SELECT * FROM credentials WHERE env IN ({marks}) AND tool_id IS NOT NULL AND ciphertext IS NOT NULL",
            tuple(sorted(names)),
        ).fetchall():
            tool = _tool(c, key["tool_id"])
            if key["id"] not in held or tool is None or tool["id"] in opened:
                continue
            if _api_tools()._checked_today(tool, key):
                continue  # its own check decides
            if start(c, tool["id"], f"a run's call with {key['env']} got 401"):
                opened.append(tool["id"])
    return opened


def diagnosed(c: Any, row: Any, calls: Any) -> list[str]:
    """After a run's diagnostics were kept (run_diagnostics.keep): `failed_calls` for the run's bot."""
    return _safely(failed_calls, c, str(row["bot_id"] or ""), calls) or []


# ---------------------------------------------------------------------------------------------------- the card
def card_tool(c: Any, row: Row) -> Row:
    """The tool whose key a pending Reconnect card replaces, or None for any other credential card."""
    if not row["credential_id"] or row["status"] != "pending":
        return None
    key = c.execute("SELECT tool_id FROM credentials WHERE id=?", (row["credential_id"],)).fetchone()
    return _tool(c, key[0]) if key and key[0] else None


def save(c: Any, vault: Any, who: Any, row: Row, value: str) -> None:
    """The person's new value replaces the key the card names, as Tools' own key change does (its permission check,
    its event); the hook in `api_tools.update_key` then ends the task."""
    tool = card_tool(c, row)
    if tool is None:
        raise Problem("not_found", "That key is no longer in Tools", 404)
    api_tools = _api_tools()
    api_tools.update_key(c, who, vault, tool["id"], row["credential_id"], api_tools.KeyUpdate(value=SecretStr(value)))
    if c.execute(
        "UPDATE credential_requests SET status='saved',updated_at=? WHERE id=? AND status='pending'",
        (H.now(), row["id"]),
    ).rowcount:
        _module("events").needs_moved(c, [row["requester"]], "key")  # it leaves their Needs you
