# pyright: strict
"""Access requests: a bot asks for a level on something that already exists, a Tools tool or a GitHub repository, and
the request reaches a person only when a person must decide.

A bot asks for itself (`tico access request <resource> --level read|read_write --why ...`); BotOps, the Tools page's
owner and any person or their agent ask for a bot (`--for <bot>`). The answer, in order:
- The bot already has that level (a grant, Everyone can read, a repository it reaches): answered at once, nothing kept.
- An open request for the same bot, resource and level: the repeat counts on it and notifies nobody again. A task that
  asks again waits on the same request.
- Read on a resource whose Read is enforceable is granted at once (`auto_approved`), with an audit event and a notice
  card to the resource's connector, who can take it back in one tap: a Tools tool that every bot could read by default
  (`default_read.enforceable`, Everyone can read on, not Only listed), and a repository the team ticked that is not a
  bot's own. Writes, a tool whose switch is off, Only listed tools, a tool whose Read cannot be held to reads (database
  URLs, SSH keys, files on computers) and bot repositories always ask.
- Otherwise one card per person who can unblock it (`tool_access.may_change_access` for a tool; the Owner and the
  admins for a repository, the Owner alone for a built-in bot), the resource's connector first, each in their chat
  with the Tools page's owner (BotOps when there is none). The first to Grant or Decline resolves it; every other copy
  of the card shows it resolved and asks nothing.

A request closes itself the moment the access exists, however it was given: its card, the Tools page, the Tools Owner,
the API, Everyone can read turning on, a repository change (`settled`, called from those writes, and the hourly
`sweep`). The task the request was made on waits on it (backend/reconnects.py, key `access:...`) and is woken once
when it closes; a decline wakes it with that. `kind` tags each request (`tool_read`, `tool_write`, `repository_read`,
`repository_write`) for the counts the Tools Owner's weekly review reads (`stats`).

The sweep also closes old free-text access tasks ("Give Sentry Triage read access to gotidy") once it can tie one to
one bot, one level and resources that each exist, and that access exists now (`reconcile_tasks`); anything it cannot
tie with certainty stays as it is.

Cards live in their message's refs (`card: access_request`), minted only here (key_grants.MINTING): a client's message
that carries one is refused (hubdb.check_message_refs).
"""

import contextvars
import json
import re
import statistics

from fastapi import Request
from pydantic import Field

from . import key_grants
from typing import Any

from .models import ID, Contract
from .store import H as _H
from .store import Problem as _Problem

# The store and the modules used below are not strictly typed: they are used through Any here (as key_grants.py does).
H: Any = _H
Problem: Any = _Problem
Row = dict[str, Any]


def _mod(name: str) -> Any:
    """A neighbour module, imported when used (most import much of the backend) and typed as Any at the door."""
    # Every name is a literal import: a computed import would make this module, and every module that imports it, depend
    # on the whole backend in the PR test selection (scripts/pr_checks.py DependencyMap).
    if name == "api_tools":
        from . import api_tools as found
    elif name == "auth":
        from . import auth as found
    elif name == "credentials":
        from . import credentials as found
    elif name == "default_read":
        from . import default_read as found
    elif name == "page_owners":
        from . import page_owners as found
    elif name == "reconnects":
        from . import reconnects as found
    elif name == "repositories":
        from . import repositories as found
    elif name == "rooms":
        from . import rooms as found
    elif name == "threads":
        from . import threads as found
    elif name == "tool_access":
        from . import tool_access as found
    else:
        raise ValueError(name)
    return found


CARD = "access_request"
TOOL, REPOSITORY = "tool", "repository"
LEVELS = key_grants.LEVELS  # read: Read, read_write: Read and write
RANK = {"read": 1, "read_write": 2}
MAX_APPROVERS = 10
MAX_REASON = 300
WAIT = "access"  # the reconnect service a task waits on
# Set while a write closes requests by a known path (the card's own Grant): `resolved_via` says so.
VIA: contextvars.ContextVar[str] = contextvars.ContextVar("access_request_via", default="")
# Set while a level change is half made (api_tools.set_level grants, then sets the grant's level): nothing is settled
# until it is whole.
DEFERRED: contextvars.ContextVar[bool] = contextvars.ContextVar("access_request_deferred", default=False)


class AccessRequestBody(Contract):
    resource: str = Field(min_length=1, max_length=300)
    level: str = Field(default="read", pattern=r"^(read|read_write|write)$")
    for_bot: ID | None = None
    why: str = Field(default="", max_length=MAX_REASON)
    task: ID | None = None


def _present(c: Any) -> Any:
    return H._has_table(c, "access_requests")


def _kind(resource_type: Any, level: Any) -> Any:
    return f"{resource_type}_{'read' if level == 'read' else 'write'}"


def _key(row: Any) -> Any:
    """The task wait's key: one per bot, resource and level."""
    return f"access:{H.actor_id(row['subject'])}:{row['resource_type']}:{row['resource_id']}:{row['level']}"


def _bot_name(c: Any, subject: Any) -> Any:
    bot: Row = H.bot(c, H.actor_id(subject)) or {}
    return bot.get("display_name") or bot.get("slug") or H.actor_id(subject)


def _label(row: Any) -> Any:
    return f"{row['resource_name']} · {LEVELS[row['level']]}"


def _org(c: Any) -> Any:
    row = c.execute("SELECT org FROM github_app WHERE id='app'").fetchone()
    return (row["org"] if row else "") or getattr(getattr(c, "settings", None), "github_owner", "") or ""


# ---------------------------------------------------------------------------------------------------- resources
def _tools(c: Any, ref: Any, by_id: Any, requestable: Any) -> Any:
    """The tools `ref` names among those `requestable` keeps (every Everyone tool, and an Only listed one only where
    the asker may see it), so a hidden tool never hides another of the same name and an answer never tells a hidden
    tool from no tool. `by_id` (`tool:<id>`): the tool with that id, else those with that name. A bare name: every
    tool with that name or id, so two called Sentry, or a tool whose id is `sentry` and another named Sentry, are
    choices rather than a guess."""
    wanted = str(ref or "").strip().removeprefix("api:")
    found = [
        row
        for row in c.execute("SELECT * FROM tools WHERE id=? OR lower(name)=lower(?) ORDER BY id", (wanted, wanted))
        if requestable(row)
    ]
    exact = [row for row in found if row["id"] == wanted] if by_id else []
    return exact or found


def _repository(c: Any, ref: Any) -> Any:
    """The team's repository by its full name, or by its short name when exactly one has it."""
    ref = str(ref or "").strip().removeprefix("https://github.com/").removesuffix(".git").strip("/")
    if not ref:
        return None
    if "/" in ref:
        return c.execute("SELECT * FROM repositories WHERE full_name=?", (ref,)).fetchone()
    rows = c.execute(
        "SELECT * FROM repositories WHERE lower(substr(full_name, instr(full_name, '/') + 1))=lower(?)", (ref,)
    ).fetchall()
    return rows[0] if len(rows) == 1 else None


def resource(c: Any, who: Any, ref: Any, admins: Any = (), auth: Any = None) -> Any:
    """(resource_type, id, name, row) for `tool:<id or name>`, `repo:<owner/name>` or a bare name (a tool first, then a
    repository). An Only listed tool the asker may not see is answered as if it did not exist."""
    api_tools = _mod("api_tools")

    text = str(ref or "").strip()
    prefix, colon, rest = text.partition(":")
    kind = prefix.lower() if colon and prefix.lower() in ("tool", "api", "repo", "repository", "github") else ""
    name = rest.strip() if kind else text
    if kind in ("", "tool", "api"):

        def requestable(row: Any) -> bool:
            # Asking is for access one lacks, so an Everyone tool counts though its Tools list shows it only to holders.
            if kind == "api" and api_tools.kind_of(row) != "api":
                return False  # `api:<name>` names an API tool
            return row["audience"] is None or bool(api_tools.visible(c, who, row, admins, auth))

        named = _tools(c, name, kind != "", requestable)
        if len(named) > 1:
            choices = [{"resource": "tool:" + str(row["id"]), "name": str(row["name"])} for row in named]
            raise Problem(
                "ambiguous",
                f"Several tools are named {name}: ask again for one of "
                + ", ".join(choice["resource"] for choice in choices),
                409,
                extra={"choices": choices},
            )
        if named:
            row = named[0]
            if api_tools.kind_of(row) != "api":
                raise Problem("resource", f"{row['name']} is a website tool: ask for its account in Tools", 422)
            return TOOL, str(row["id"]), str(row["name"]), row
    if kind in ("", "repo", "repository", "github"):
        row = _repository(c, name)
        if row is not None:
            return REPOSITORY, str(row["full_name"]), str(row["full_name"]), row
    raise Problem(
        "not_found", f"No tool or repository named {name or '(none)'}: `tico tool index` lists the tools", 404
    )


def _level_of(c: Any, row: Any) -> Any:
    """The level the subject holds on the request's resource now: read, read_write or None."""
    subject = row["subject"]
    if row["resource_type"] == TOOL:
        api_tools = _mod("api_tools")

        if not c.execute("SELECT 1 FROM tools WHERE id=?", (row["resource_id"],)).fetchone():
            return None
        if not api_tools._templated(c, row["resource_id"]):
            return api_tools.subject_level(c, row["resource_id"], subject)
        # A tool with a template works only with every variable: its level is the lowest one held across them.
        envs = {
            str(k["env"])
            for k in c.execute(
                "SELECT env FROM credentials WHERE tool_id=? AND ciphertext IS NOT NULL", (row["resource_id"],)
            )
        }
        by_env: dict[str, str] = {}
        for key, level in api_tools._held(c, row["resource_id"], api_tools.subject_of(c, subject)):
            env = str(key["env"])
            if RANK[level] > RANK.get(by_env.get(env, ""), 0):
                by_env[env] = level
        if not envs or envs - set(by_env):
            return None
        return min(by_env.values(), key=lambda lv: RANK[lv])
    R = _mod("repositories")

    try:
        effective = R.access(c, H.actor_id(subject), _org(c))["effective"]
    except Problem:
        return None
    for r in effective:
        if r["full_name"].lower() == row["resource_id"].lower():
            return "read_write" if r["access"] == "write" else "read"
    return None


def has(c: Any, row: Any) -> Any:
    held = _level_of(c, row)
    return held is not None and RANK[held] >= RANK[row["level"]]


# ---------------------------------------------------------------------------------------------------- who decides
def _people(c: Any, auth: Any) -> Any:
    out: list[Any] = []
    for r in c.execute("SELECT id FROM humans ORDER BY name, id").fetchall():
        try:
            out.append(auth.identity_for_actor(c, "human:" + r["id"]))
        except Problem:
            continue
    return out


def _connector(c: Any, row: Any) -> Any:
    """Whoever connected the resource: the person who added the tool, or the repository."""
    if row["resource_type"] == TOOL:
        found = c.execute("SELECT created_by FROM tools WHERE id=?", (row["resource_id"],)).fetchone()
    else:
        found = c.execute("SELECT added_by FROM repositories WHERE full_name=?", (row["resource_id"],)).fetchone()
    actor = str((found[0] if found else "") or "")
    if actor and not actor.startswith(("human:", "bot:")):
        actor = "human:" + actor
    return actor if actor.startswith("human:") else ""


def may_unblock(c: Any, who: Any, row: Any, vault: Any, auth: Any) -> Any:
    """Whether `who` can give this access: the one Tools rule for a tool; the Owner or an admin for a repository (the
    Owner alone for a built-in bot), as Settings > General › Repositories decides."""
    if getattr(who, "support", None) or getattr(who, "role", "") not in ("owner", "human"):
        return False
    if row["resource_type"] == TOOL:
        TA = _mod("tool_access")

        tool = c.execute("SELECT * FROM tools WHERE id=?", (row["resource_id"],)).fetchone()
        return tool is not None and TA.may_change_access(
            c, who, TA.tool(tool), row["subject"], TA.GRANT, admins=vault.admins, auth=auth
        )
    if auth.system_bot(H.actor_id(row["subject"])):
        return who.role == "owner"
    return bool(auth.bot_admin(who))


def approvers(c: Any, row: Any, vault: Any, auth: Any) -> Any:
    """Everyone who can unblock the request, the connector first, then the Owner, then the rest by name."""
    people = [p for p in _people(c, auth) if may_unblock(c, p, row, vault, auth)]
    first = _connector(c, row)
    people.sort(key=lambda p: (p.actor != first, p.role != "owner"))
    return people[:MAX_APPROVERS]


# ---------------------------------------------------------------------------------------------------- cards
def _poster(c: Any) -> Any:
    """The bot whose chat carries the cards: the Tools page's own owner, else BotOps."""
    page_owners = _mod("page_owners")

    owner = page_owners.owner(c, "tools", fallback=False)
    if owner and H.bot(c, H.actor_id(owner)):
        return H.actor_id(owner)
    return H.FLEET_MAINTAINER if H.bot(c, H.FLEET_MAINTAINER) else None


def card_of(c: Any, row: Any) -> Any:
    return {
        "id": row["id"],
        "state": row["state"],
        "subject": row["subject"],
        "subject_name": _bot_name(c, row["subject"]),
        "resource_type": row["resource_type"],
        "resource_id": row["resource_id"],
        "resource_name": row["resource_name"],
        "level": row["level"],
        "why": row["reason"],
        "auto": bool(row["auto_approved"]),
        "by": H.actor_name(c, row["resolved_by"]) if row["resolved_by"] and row["resolved_by"] != H.TICO_ACTOR else "",
    }


def _post_card(c: Any, auth: Any, person: Any, row: Any) -> Any:
    """One card in `person`'s chat with the poster bot; its message id, or None when there is nowhere to put it."""
    rooms = _mod("rooms")
    threads = _mod("threads")

    poster = _poster(c)
    if not poster:
        return None
    room = rooms.chat_room(c, auth, person, poster)
    text = f"{_bot_name(c, row['subject'])} → {_label(row)}" + (f"\n{row['reason']}" if row["reason"] else "")
    token = key_grants.MINTING.set(True)
    try:
        sent = threads.post(
            c, "bot:" + poster, room["id"], text, kind="notice", refs={"card": CARD, CARD: card_of(c, row)}
        )
    finally:
        key_grants.MINTING.reset(token)
    shown: Row = sent or {}
    return shown.get("id")


def _refresh_cards(c: Any, row: Any) -> Any:
    """Every copy of the request's card shows where it stands now: resolved copies ask nothing."""
    card = json.dumps(card_of(c, row))
    for message_id in json.loads(row["cards"] or "{}").values():
        c.execute(
            "UPDATE messages SET refs=json_set(coalesce(refs,'{}'),'$.access_request',json(?)) WHERE id=?",
            (card, message_id),
        )


def _row(c: Any, rid: Any) -> Any:
    found = c.execute("SELECT * FROM access_requests WHERE id=?", (rid,)).fetchone()
    return dict(found) if found else None


# ---------------------------------------------------------------------------------------------------- auto-approve
def _auto_tool(c: Any, row: Any) -> Any:
    """The keys a bot may be given at once for Read on this tool, or None: a tool every bot could read by default, its
    switch on, not Only listed, and no other key of the bot's under any of its variables (one value per variable). A
    tool with a multi-secret template reads with one key per variable, so each of them is given."""
    credentials = _mod("credentials")
    default_read = _mod("default_read")

    tool = c.execute("SELECT * FROM tools WHERE id=?", (row["resource_id"],)).fetchone()
    if row["level"] != "read" or tool is None or tool["audience"] is not None or not tool["everyone_reads"]:
        return None
    keys: list[Any] = default_read.enforceable_keys(c).get(str(tool["id"])) or []
    if not keys:
        return None
    mine = {str(k["id"]) for k in keys}
    for key in keys:
        if not key["env"]:
            continue
        for other in c.execute(
            "SELECT id FROM credentials WHERE env=? AND ciphertext IS NOT NULL", (key["env"],)
        ).fetchall():
            if str(other["id"]) not in mine and credentials.effective_grant(c, other["id"], row["subject"]):
                return None
    return keys


def _give_tool_read(c: Any, row: Any, keys: Any) -> Any:
    """The grants at Read on the tool's enforceable keys, made by Tico itself (`granted_by` Tico), with their events."""
    reconnects = _mod("reconnects")

    for key in keys:
        gid = H.new_id()
        c.execute(
            "INSERT INTO credential_grants(id,credential_id,subject,granted_by,created_at,access_level) "
            "VALUES(?,?,?,?,?,'read')",
            (gid, key["id"], row["subject"], H.TICO_ACTOR, H.now()),
        )
        H.event(
            c,
            H.TICO_ACTOR,
            "credential.granted",
            key["id"],
            {"grant": gid, "subject": row["subject"], "level": "read", "via": "access-request", "request": row["id"]},
        )
        if key["env"]:
            reconnects.secret_stored(c, H.actor_id(row["subject"]), key["env"])


def _auto_repository(c: Any, auth: Any, row: Any) -> Any:
    """Read on a repository the team ticked that is no bot's own, for a bot whose access is its own repository or
    chosen ones (All ticked repos already reaches every ticked one)."""
    R = _mod("repositories")

    repo = c.execute("SELECT * FROM repositories WHERE full_name=?", (row["resource_id"],)).fetchone()
    bot = H.actor_id(row["subject"])
    if row["level"] != "read" or repo is None or auth.system_bot(bot):
        return False
    if not repo["enabled"] or repo["bot_repo"] or repo["reachable"] == 0:
        return False
    if repo["full_name"].split("/")[-1].lower().startswith("bot-"):
        return False
    try:
        return R.access(c, bot, _org(c))["mode"] in ("own", "chosen")
    except Problem:
        return False


def _give_repository(c: Any, row: Any, actor: Any, person: Any = False) -> Any:
    """The bot reaches the repository at the request's level: ticked for the team if a person grants it, then chosen for
    the bot (its other chosen repositories kept). A bot on All ticked repos already reaches every ticked one at its
    level; past that level it is a choice in Settings."""
    R = _mod("repositories")

    bot, name, org = H.actor_id(row["subject"]), row["resource_id"], _org(c)
    want = "write" if row["level"] == "read_write" else "read"
    repo = c.execute("SELECT * FROM repositories WHERE full_name=?", (name,)).fetchone()
    if repo is None:
        raise Problem("not_found", f"No repository {name} in the team list; refresh it in Settings", 404)
    if person and not repo["enabled"]:
        c.execute("UPDATE repositories SET enabled=1,updated_at=? WHERE full_name=?", (H.now(), name))
        H.event(c, actor, "repositories.changed", name, {"before": {"enabled": 0}, "after": {"enabled": 1}})
    current = R.access(c, bot, org)
    if current["mode"] == "all" and has(c, row):
        return
    # As a product repository its bot makes (github_app): from All ticked repos it moves to Chosen repos, each
    # repository it reaches now kept at its level, and this one at the asked level or the one it has, if higher.
    keep, _ = R.chosen_keeping(c, current, name, org)
    held = next(
        (
            r["access"]
            for r in current["chosen" if current["mode"] == "chosen" else "effective"]
            if r["full_name"].lower() == name.lower()
        ),
        None,
    )
    grant = R.Grant(full_name=name, access="write" if "write" in (held, want) else "read")
    body = R.RepoAccessUpdate(mode="chosen", chosen=[*keep, grant])
    R.set_access(c, bot, body, org, actor, team_list=True, own_click=person, default=False)


# ---------------------------------------------------------------------------------------------------- the request
def _subject(c: Any, who: Any, for_bot: Any, auth: Any) -> Any:
    page_owners = _mod("page_owners")

    asker_bot = who.actor if who.role == "bot" and not getattr(who, "via", "") else ""
    ref = str(for_bot or "").strip()
    if not ref and not asker_bot:
        raise Problem("subject", "Name the bot that needs it (`--for <bot>`)", 422)
    bot = (
        (H.resolve_bot(c, ref if ref.startswith("bot:") else "bot:" + ref) or H.resolve_bot(c, ref))
        if ref
        else H.bot(c, H.actor_id(asker_bot))
    )
    if not bot or bot.get("state") in ("planned", "archived"):
        raise Problem("not_found", f"No active bot {ref or asker_bot}", 404)
    subject = "bot:" + str(bot["id"])
    tools_owner = page_owners.owner(c, "tools", fallback=False)
    if asker_bot and subject != asker_bot and asker_bot not in ("bot:" + H.FLEET_MAINTAINER, tools_owner):
        raise Problem(
            "forbidden",
            "A bot asks for its own access; BotOps and the Tools Owner ask for any bot's. Ask the Tools Owner",
            403,
        )
    if who.role in ("owner", "human") and not auth.bot_access(c, who, str(bot["id"]))["see"]:
        raise Problem("not_found", f"No active bot {ref}", 404)
    return subject, tools_owner


def _turn_task(c: Any, who: Any) -> Any:
    """The live task the asking bot's turn is on, or None."""
    if not getattr(who, "attempt_id", None):
        return None
    row = c.execute(
        "SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (who.attempt_id,)
    ).fetchone()
    msg = H.message(c, row["message_id"]) if row and row["message_id"] else None
    if not msg:
        return None
    conversation: Row = H.conversation(c, msg["conversation_id"]) or {}
    refs: Row = msg.get("refs") or {}
    task_id = conversation.get("task_id") or H._task_ref(refs.get("task"))
    task = H.task(c, task_id) if task_id else None
    owner = getattr(who, "task_actor", "") or who.actor
    return task if task and task["owner_id"] == owner and task["status"] in H.ACTIVE_STATUSES else None


def _task(c: Any, who: Any, ref: Any) -> Any:
    if not ref:
        return _turn_task(c, who)
    task = H.task(c, str(ref))
    if not task or not H.task_private_readable(c, who.actor, task):
        raise Problem("not_found", "No such task", 404)
    if task["status"] not in H.ACTIVE_STATUSES or not H.is_bot(task["owner_id"]):
        raise Problem("task", "Name a live task a bot owns: it is woken when the access is there", 422)
    return task


def _wait(c: Any, row: Any, task: Any, card_ids: Any) -> Any:
    """The task waits on the request (a Paused cause), carried by the card of the first person who can read it."""
    reconnects = _mod("reconnects")

    if not task:
        return
    for person in json.loads(row["approvers"] or "[]"):
        if H.task_private_readable(c, person, task):
            reconnects.open(
                c,
                task["id"],
                H.actor_id(row["subject"]),
                _key(row),
                WAIT,
                _label(row),
                person,
                {"kind": "access", "request": row["id"]},
                message_id=card_ids.get(person),
                owner=task["owner_id"],
            )
            return


def ask(c: Any, who: Any, body: Any, vault: Any, auth: Any) -> Any:
    """`POST /api/v2/access-requests`: the answer, as `view` gives it, with `detail` in a bot's words."""
    level = "read_write" if body.level in ("write", "read_write") else "read"
    subject, tools_owner = _subject(c, who, body.for_bot, auth)
    kind, rid_, name, _found = resource(c, who, body.resource, vault.admins, auth)
    if kind == TOOL and subject == tools_owner:
        raise Problem(
            "forbidden", "The Tools Owner holds none of the keys it connects: name the bot that needs it", 403
        )
    task = _task(c, who, body.task)
    asked = {
        "subject": subject,
        "resource_type": kind,
        "resource_id": rid_,
        "resource_name": name,
        "level": level,
        "auto_approved": 0,
    }
    if has(c, asked):
        return {"state": "granted", "already": True, "detail": f"{_bot_name(c, subject)} already has {name}."}
    open_ = c.execute(
        "SELECT * FROM access_requests WHERE state='open' AND subject=? AND resource_type=? AND resource_id=? AND level=?",
        (subject, kind, rid_, level),
    ).fetchone()
    if open_:
        row = dict(open_)
        c.execute("UPDATE access_requests SET repeats=repeats+1 WHERE id=?", (row["id"],))
        _wait(c, row, task, json.loads(row["cards"] or "{}"))
        return view(c, _row(c, row["id"])) | {
            "repeat": True,
            "detail": "Already asked: the card is still open. You are woken when it is granted.",
        }
    row = asked | {
        "id": H.new_id(),
        "kind": _kind(kind, level),
        "reason": " ".join(str(body.why or "").split())[:MAX_REASON],
        "requested_by": who.actor,
        "task_id": task["id"] if task else None,
        "state": "open",
        "approvers": "[]",
        "cards": "{}",
        "repeats": 0,
        "resolved_by": None,
        "resolved_via": None,
        "created_at": H.now(),
        "resolved_at": None,
    }
    c.execute(
        "INSERT INTO access_requests(id,subject,resource_type,resource_id,resource_name,level,kind,reason,requested_by,"
        "task_id,state,auto_approved,approvers,cards,repeats,created_at) VALUES(:id,:subject,:resource_type,"
        ":resource_id,:resource_name,:level,:kind,:reason,:requested_by,:task_id,'open',0,'[]','{}',0,:created_at)",
        row,
    )
    H.event(
        c,
        who.actor,
        "access.requested",
        row["id"],
        {"subject": subject, "kind": row["kind"], "resource": rid_, "level": level},
    )
    if _approve_at_once(c, auth, row, vault):
        return view(c, _row(c, row["id"])) | {"detail": f"Granted: {_bot_name(c, subject)} has {_label(row)} now."}
    people = approvers(c, row, vault, auth)
    cards = {}
    for person in people:
        message_id = _post_card(c, auth, person, row)
        if message_id:
            cards[person.actor] = message_id
    c.execute(
        "UPDATE access_requests SET approvers=?,cards=? WHERE id=?",
        (json.dumps([p.actor for p in people]), json.dumps(cards), row["id"]),
    )
    row = _row(c, row["id"])
    _wait(c, row, task, cards)
    names = ", ".join(H.actor_name(c, p.actor) for p in people[:3])
    return view(c, row) | {
        "detail": (f"Asked {names}" if names else "Nobody can grant it yet: a credential admin adds it in Tools")
        + ". You are woken when it is granted: work on something else or end your turn."
    }


def _approve_at_once(c: Any, auth: Any, row: Any, vault: Any) -> Any:
    """Read on an enforceable resource: granted now by Tico itself, with a notice card to its connector (else the
    first person who could have granted it), who can take it back."""
    if row["resource_type"] == TOOL:
        keys = _auto_tool(c, row)
        if not keys:
            return False
        _give_tool_read(c, row, keys)
    else:
        if not _auto_repository(c, auth, row):
            return False
        c.execute("SAVEPOINT access_request_auto")
        try:
            _give_repository(c, row, H.TICO_ACTOR)
        except Problem:
            c.execute("ROLLBACK TO access_request_auto")
            c.execute("RELEASE access_request_auto")
            return False
        c.execute("RELEASE access_request_auto")
    if not has(c, row):
        return False
    c.execute("UPDATE access_requests SET auto_approved=1 WHERE id=?", (row["id"],))
    _close(c, _row(c, row["id"]), "granted", H.TICO_ACTOR, "auto")
    try:
        person = auth.identity_for_actor(c, _connector(c, row)) if _connector(c, row) else None
    except Problem:
        person = None
    person = person or next(iter(approvers(c, _row(c, row["id"]), vault, auth)), None)
    if person:
        connector = person.actor
        message_id = _post_card(c, auth, person, _row(c, row["id"]))
        if message_id:
            c.execute(
                "UPDATE access_requests SET approvers=?,cards=? WHERE id=?",
                (json.dumps([connector]), json.dumps({connector: message_id}), row["id"]),
            )
    return True


def view(c: Any, row: Any) -> Any:
    return card_of(c, row) | {
        "kind": row["kind"],
        "repeats": row["repeats"],
        "task_id": row["task_id"],
        "created_at": row["created_at"],
        "resolved_at": row["resolved_at"],
        "resolved_via": row["resolved_via"],
        "asked": [H.actor_name(c, a) for a in json.loads(row["approvers"] or "[]")],
    }


# ---------------------------------------------------------------------------------------------------- closing
def _close(c: Any, row: Any, state: Any, actor: Any, via: Any) -> Any:
    """Resolve an open request: every copy of its card says so; the waiting task is woken (granted, declined)."""
    reconnects = _mod("reconnects")

    at = H.now()
    if (
        c.execute(
            "UPDATE access_requests SET state=?,resolved_by=?,resolved_via=?,resolved_at=? WHERE id=? AND state='open'",
            (state, actor, via, at, row["id"]),
        ).rowcount
        != 1
    ):
        return False
    row = _row(c, row["id"])
    _refresh_cards(c, row)
    H.event(c, actor or H.TICO_ACTOR, "access." + state, row["id"], {"kind": row["kind"], "via": via})
    if state == "declined":
        reconnects.dropped(c, row["id"])
    elif state == "granted" and via != "auto":  # an approval at once is the asking turn's own answer
        _wake(c, row)
    return True


def _wake(c: Any, row: Any) -> Any:
    reconnects = _mod("reconnects")

    if not row["task_id"]:
        return
    waits = c.execute("SELECT 1 FROM task_reconnects WHERE key=? LIMIT 1", (_key(row),)).fetchone()
    if waits:
        reconnects.fixed(c, _key(row), H.actor_id(row["subject"]), why="access")
        return
    task = H.task(c, row["task_id"])
    if task and task["status"] in H.ACTIVE_STATUSES and H.is_bot(task["owner_id"]):
        H._wake(
            c,
            task,
            task["owner_id"],
            f"Granted: {_bot_name(c, row['subject'])} has {_label(row)} now. Continue.",
            refs={CARD: row["id"]},
            source=row["id"],
        )


def _via(who: Any = None, actor: Any = "") -> Any:
    """How a write that closed requests was made, for `resolved_via`."""
    if VIA.get():
        return VIA.get()
    actor = str(getattr(who, "actor", "") or actor or "")
    if not actor or actor == H.TICO_ACTOR:
        return "auto" if actor else "sweep"
    if getattr(who, "via", ""):
        return str(who.via)
    if getattr(who, "via_token", None):
        return "api"
    return "bot" if actor.startswith("bot:") else "tools"


def settled(
    c: Any,
    who: Any = None,
    *,
    subject: Any = None,
    resource_type: Any = None,
    resource_id: Any = None,
    via: Any = "",
    actor: Any = "",
) -> Any:
    """Close every open request (narrowed to a subject or resource) whose access now exists. The writes that can give
    access call this; the hourly sweep calls it with nothing narrowed. Returns the closed ids."""
    if DEFERRED.get() or not _present(c):
        return []
    where: list[str] = ["state='open'"]
    args: list[Any] = []
    for column, value in (("subject", subject), ("resource_type", resource_type), ("resource_id", resource_id)):
        if value:
            where.append(f"{column}=?")
            args.append(value)
    closed: list[str] = []
    by = str(getattr(who, "actor", "") or actor or "") or None
    for found in c.execute(f"SELECT * FROM access_requests WHERE {' AND '.join(where)}", args).fetchall():
        row = dict(found)
        if (
            row["resource_type"] == TOOL
            and not c.execute("SELECT 1 FROM tools WHERE id=?", (row["resource_id"],)).fetchone()
        ):
            if _close(c, row, "withdrawn", by, "tool-deleted"):
                closed.append(row["id"])
            continue
        if has(c, row) and _close(c, row, "granted", by, via or _via(who, actor)):
            closed.append(row["id"])
    return closed


# ---------------------------------------------------------------------------------------------------- the card's taps
def _open_row(c: Any, who: Any, rid: Any, vault: Any, auth: Any) -> Any:
    row = _row(c, rid)
    if not row:
        raise Problem("not_found", "No such request", 404)
    if not may_unblock(c, who, row, vault, auth):
        names = ", ".join(H.actor_name(c, a) for a in json.loads(row["approvers"] or "[]")[:3])
        raise Problem("forbidden", "You can't grant this" + (f": {names} can" if names else ""), 403)
    return row


def grant(c: Any, who: Any, rid: Any, vault: Any, auth: Any) -> Any:
    """The card's Grant: the access given as this person's own tap, and every copy of the card resolved."""
    api_tools = _mod("api_tools")

    row = _open_row(c, who, rid, vault, auth)
    if row["state"] != "open":
        return view(c, row)
    token = VIA.set("card")
    try:
        if not has(c, row):
            if row["resource_type"] == TOOL:
                api_tools.set_level(c, who, vault, row["resource_id"], row["subject"], row["level"])
            else:
                _give_repository(c, row, who.actor, person=True)
        settled(c, who, subject=row["subject"], resource_type=row["resource_type"], resource_id=row["resource_id"])
    finally:
        VIA.reset(token)
    return view(c, _row(c, rid))


def decline(c: Any, who: Any, rid: Any, vault: Any, auth: Any) -> Any:
    row = _open_row(c, who, rid, vault, auth)
    if row["state"] == "open":
        _close(c, row, "declined", who.actor, "card")
    return view(c, _row(c, rid))


def take_back(c: Any, who: Any, rid: Any, vault: Any, auth: Any) -> Any:
    """Take back what a request gave (its connector's notice card after an auto-approval): the bot is back to the
    level it had before."""
    api_tools = _mod("api_tools")
    R = _mod("repositories")

    row = _open_row(c, who, rid, vault, auth)
    if row["state"] != "granted":
        return view(c, row)
    if row["resource_type"] == TOOL:
        api_tools.set_level(c, who, vault, row["resource_id"], row["subject"], "none")
    else:
        bot, org = H.actor_id(row["subject"]), _org(c)
        current = R.access(c, bot, org)
        if current["mode"] == "chosen":
            kept = [r for r in current["chosen"] if r["full_name"].lower() != row["resource_id"].lower()]
            body = R.RepoAccessUpdate(
                mode="chosen", chosen=[R.Grant(full_name=r["full_name"], access=r["access"]) for r in kept]
            )
            R.set_access(c, bot, body, org, who.actor, team_list=True, owner=who.role == "owner")
    c.execute(
        "UPDATE access_requests SET state='revoked',resolved_by=?,resolved_via='card',resolved_at=? WHERE id=?",
        (who.actor, H.now(), rid),
    )
    row = _row(c, rid)
    _refresh_cards(c, row)
    H.event(c, who.actor, "access.revoked", rid, {"kind": row["kind"]})
    return view(c, row)


# ---------------------------------------------------------------------------------------------------- counts
def stats(c: Any, days: int = 7) -> Any:
    """The week's numbers for the Tools Owner's review: how many requests, how many reached people, the share granted
    at once, and the median minutes from asking to a person's grant."""
    since = H.shift(H.now(), days=-int(days))
    totals = c.execute(
        "SELECT count(*) AS n, coalesce(sum(auto_approved),0) AS auto, "
        "coalesce(sum(state='granted'),0) AS granted, coalesce(sum(state='declined'),0) AS declined, "
        "coalesce(sum(state='open'),0) AS open FROM access_requests WHERE created_at>=?",
        (since,),
    ).fetchone()
    kinds = {
        r["kind"]: r["n"]
        for r in c.execute(
            "SELECT kind, count(*) AS n FROM access_requests WHERE created_at>=? GROUP BY kind ORDER BY kind", (since,)
        )
    }
    minutes = [
        r[0]
        for r in c.execute(
            "SELECT (julianday(resolved_at)-julianday(created_at))*1440 FROM access_requests "
            "WHERE created_at>=? AND state='granted' AND auto_approved=0 AND resolved_at IS NOT NULL LIMIT 5000",
            (since,),
        )
        if r[0] is not None
    ]
    n = totals["n"]
    return {
        "days": int(days),
        "requested": n,
        "to_people": n - totals["auto"],
        "auto_approved": totals["auto"],
        "auto_share": round(totals["auto"] / n, 2) if n else None,
        "granted": totals["granted"],
        "declined": totals["declined"],
        "open": totals["open"],
        "median_minutes_to_grant": round(statistics.median(minutes)) if minutes else None,
        "by_kind": kinds,
    }


# ---------------------------------------------------------------------------------------------------- old tasks
_LEVEL_WORDS = r"(?P<level>read-only|read only|read|write)"
TITLES = (
    # Grant: Acme/app and Acme/web no-token GitHub App read for QA Owner (...)
    re.compile(
        r"^Grant: (?P<items>[\w.-]+/[\w.-]+(?:(?:, | and )[\w.-]+/[\w.-]+)*) no-token GitHub App (?P<level>read)"
        r" for (?P<bot>[^()]+?)(?: \(.*\))?$"
    ),
    # Give Sentry Triage read access to gotidy, web and docs
    re.compile(rf"^Give (?P<bot>.+?) (?:GitHub )?{_LEVEL_WORDS} access to (?P<items>.+?)\??$"),
    # Grant general-manager the google-search-console tool, read only
    re.compile(r"^Grant (?P<bot>[\w-]+) the (?P<items>.+?) tool(?:, (?P<level>read only|read and write))?$"),
    # Add Raygun (error monitoring) to General Manager's tools, read only / Add Raygun access to qa-e2e
    re.compile(r"^Add (?P<items>.+?) to (?P<bot>.+?)'s tools(?:, (?P<level>read only|read and write))?$"),
    re.compile(r"^Add (?P<items>.+?) access to (?P<bot>[\w-]+)$"),
)


def parse_title(title: Any) -> Any:
    """(bot, [resource names], level, kind) a free-text access task's title names, or None. `kind` is `repository` when
    the title says GitHub or names an owner/name, else unknown (`""`: a tool or a repository, whichever exists)."""
    text = str(title or "").strip()
    if not text.startswith("Grant: "):
        text = re.sub(r"\s*\([^)]*\)", "", text)  # an aside in brackets says nothing about what is asked
    for pattern in TITLES:
        found = pattern.match(text.strip())
        if not found:
            continue
        items = [i.strip() for i in re.split(r", and |, | and ", found["items"]) if i.strip()]
        word = (found.groupdict().get("level") or "").lower()
        level = "read_write" if word in ("write", "read and write") else "read" if word else ""
        github = "GitHub" in text or any("/" in i for i in items)
        if not level and github:
            return None  # "GitHub access" names no level: never guessed for a repository
        return found["bot"].strip(), items, level or "read", REPOSITORY if github else ""
    return None


def _named_bot(c: Any, name: Any) -> Any:
    name = re.sub(r"^the ", "", str(name or "").strip(), flags=re.I)
    rows = c.execute(
        "SELECT id FROM bots WHERE state<>'archived' AND (lower(slug)=lower(?) OR lower(display_name)=lower(?))",
        (name, name),
    ).fetchall()
    return "bot:" + rows[0]["id"] if len(rows) == 1 else None


def _everyone(row: Any) -> bool:
    return row["audience"] is None


def tie(c: Any, title: Any) -> Any:
    """[(subject, resource_type, id, level)] a title names with certainty, or None: one bot, a level, and each resource
    one that exists."""
    parsed = parse_title(title)
    if not parsed:
        return None
    bot, items, level, kind = parsed
    subject = _named_bot(c, bot)
    if not subject:
        return None
    out: list[tuple[str, str, str, str]] = []
    api_tools = _mod("api_tools")
    for item in items:
        # Only Everyone tools, with no asker to see the rest; several by the name, or a website tool, is not certain.
        tools: list[Row] = _tools(c, item, False, _everyone) if kind != REPOSITORY else []
        repo = _repository(c, item) if not tools else None
        if len(tools) == 1 and api_tools.kind_of(tools[0]) == "api":
            out.append((subject, TOOL, str(tools[0]["id"]), level))
        elif repo is not None:
            out.append((subject, REPOSITORY, str(repo["full_name"]), level))
        else:
            return None
    return out or None


def reconcile_tasks(c: Any, close: bool = True, limit: int = 500) -> Any:
    """Close each open free-text access task whose access now exists, when its title ties to one bot, one level and
    resources that each exist; leave every other. Returns [{task_id, title, close}]."""
    found: list[Row] = []
    for task in c.execute(
        "SELECT id, title FROM tasks WHERE status='open' AND (title LIKE 'Grant%' OR title LIKE 'Give %' "
        "OR title LIKE 'Add %') ORDER BY created_at LIMIT ?",
        (limit,),
    ).fetchall():
        tied = tie(c, task["title"])
        if not tied:
            continue
        rows = [
            {"subject": s, "resource_type": k, "resource_id": r, "level": lv, "resource_name": r}
            for s, k, r, lv in tied
        ]
        done = all(has(c, r) for r in rows)
        found.append({"task_id": task["id"], "title": task["title"], "close": done})
        if done and close:
            note = "Closed: the access it asks for exists now (" + "; ".join(_label(r) for r in rows) + ")."
            if H.attempt(c, H.task_close, H.TICO_ACTOR, task["id"], note) is not None:
                H.event(
                    c, H.TICO_ACTOR, "access.task_closed", task["id"], {"resources": [r["resource_id"] for r in rows]}
                )
    return found


def sweep(store: Any) -> Any:
    """The hourly backstop: open requests whose access exists by a path no write announced, and old access tasks."""
    with store.transaction() as c:
        if not _present(c):
            return "No access requests"
        closed = settled(c, via="sweep")
        tasks = [t for t in reconcile_tasks(c) if t["close"]]
    return f"Closed {len(closed)} requests and {len(tasks)} access tasks"


# ---------------------------------------------------------------------------------------------------- routes
def install(app: Any, store: Any, auth: Any, mutate: Any) -> Any:
    validate_identity = _mod("auth").validate_identity

    def vault() -> Any:
        return app.state.vault

    @app.post("/api/v2/access-requests")
    def access_request_create(request: Request, body: AccessRequestBody):
        """A bot asks for a level on a tool or a repository (a person or BotOps for a bot, `for_bot`): granted at once
        when it can be, else one card to each person who can grant it. Repeats count on the open request."""
        who = request.state.identity
        return mutate(request, body, lambda c: create(c, who, body))  # pyright: ignore[reportUnknownLambdaType]

    def create(c: Any, who: Any, body: Any) -> Any:
        validate_identity(c, who)
        if getattr(who, "support", None) or who.role not in ("owner", "human", "bot"):
            raise Problem("forbidden", "A bot, or a person for a bot, asks for access", 403)
        return ask(c, who, body, vault(), auth)

    def tap(request: Any, rid: Any, fn: Any) -> Any:
        who = request.state.identity
        with store.transaction() as c:
            validate_identity(c, who)
            return fn(c, who, rid, vault(), auth)

    @app.post("/api/v2/access-requests/{rid}/grant")
    def access_request_grant(request: Request, rid: str):
        """Grant on the card: the access given as this person, every other copy of the card resolved."""
        return tap(request, rid, grant)

    @app.post("/api/v2/access-requests/{rid}/decline")
    def access_request_decline(request: Request, rid: str):
        """Decline on the card: the request closes, and a task waiting on it is told."""
        return tap(request, rid, decline)

    @app.post("/api/v2/access-requests/{rid}/take-back")
    def access_request_take_back(request: Request, rid: str):
        """Take back on a granted request's card (the connector's notice after an auto-approval)."""
        return tap(request, rid, take_back)

    @app.get("/api/v2/access-requests/stats")
    def access_request_stats(request: Request, days: int = 7):
        """The last `days` of access requests in numbers: asked, reached people, approved at once, time to grant."""
        who = request.state.identity
        if getattr(who, "support", None) or who.role not in ("owner", "human", "bot"):
            raise Problem("forbidden", "A teammate reads these numbers", 403)
        with store.read() as c:
            return stats(c, max(1, min(int(days), 90)))
