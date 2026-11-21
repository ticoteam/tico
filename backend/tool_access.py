# pyright: strict
"""Who may change access in Tools: one rule, `may_change_access(c, who, resource, subject, action)`, behind every Tools
write (API and MCP tools, their keys, levels and settings; vault credentials and their grants; website accounts; the key
cards; a bot's bot.yaml `tools:` notes). `require` raises the refusal in the words a person reads.

The rule, in order:
- Nobody but a person or a bot: never Tico support, a computer or another system's key.
- A delegation is the person's rights. BotOps, the Tools Owner and the Assistant acting for someone (`via` botops,
  tools-owner, assistant: app.py `delegated_identity`, backend/tools_delegation.py) arrive here as that person, and the
  person's rights decide. The built-in that acted never receives access through it (the Tools Owner connects keys for
  others and holds none), and through the Tools Owner a person's own access stays their own tap in Tools.
- A tool the reader may not see (Only listed, backend/api_tools.py `visible`) is answered as if it did not exist, a
  credential administrator included, unless it lists them.
- Changing a tool, its keys, settings or sign-in, a level, a group's or every bot's or every computer's access: a
  credential administrator. Who can see a tool: the owner, or an administrator who can see it.
- Giving a bot a key: an administrator; the member who stored that key for a bot they manage; or a person who holds
  the key passing it on to a bot they own (the grant hangs on theirs, `Decision.parent`).
- Taking a grant back: an administrator, or whoever stored the key or made the grant, from a bot they manage.
- Storing a value for a bot (its key card, `tico credential set --for-bot`): an administrator, or a member who manages
  the bot while the team rule "Members store credentials" is on, for a new key or one they stored.
- A bot's own key (`_itself`): a bot acting as itself (no delegation) stores a value it already holds for itself alone,
  as a new key or over one it stored itself, and gives it to itself; never over anyone else's key, never to anyone
  else. The Tools page's owner holds no key, so it has none of its own.
- A website account: an administrator, or its holder (a person, or a person who manages the bot that holds it).
- A bot's bot.yaml `tools:` entries, which are notes (identity, scope, note) and never access: the bot itself,
  BotOps and the Tools page's owner on their own, an administrator, or anyone who manages the bot.

The Tools page's owner reads every tool (`api_tools.tools_reader`) but, being a bot, is never a credential
administrator: it changes access only with a person's delegation.
"""

import importlib
from dataclasses import dataclass, field
from typing import Any

from . import store as store_module

# Untyped neighbours, typed as Any at the door (as backend/tools_delegation.py does).
H: Any = store_module.H
Problem: Any = store_module.Problem
Row = Any  # a sqlite row or a dict read like one
Who = Any  # backend/auth.py Identity

TOOL, KEY, ACCOUNT, BOT = "tool", "key", "account", "bot"
CREATE, EDIT, DELETE, GRANT, REVOKE, AUDIENCE, STORE, NOTE = (
    "create",
    "edit",
    "delete",
    "grant",
    "revoke",
    "audience",
    "store",
    "note",
)
# The `via` of a built-in acting with a person's rights (backend/tools_delegation.py DELEGATE_VIAS, and the Assistant).
DELEGATED = ("botops", "tools-owner", "assistant")


@dataclass(frozen=True)
class Resource:
    """What is changed: a tool (`row` its tools row, when the caller has it), a vault credential or website account
    (`id`), or a bot's `tools:` entries (`id` the bot). `grant`: the grant row a revoke takes back."""

    kind: str
    id: str = ""
    row: Row = None
    grant: Row = None
    name: str = ""


@dataclass
class Decision:
    ok: bool
    why: str = ""
    hidden: bool = False
    parent: str | None = None
    extra: dict[str, str] = field(default_factory=lambda: {})


def _credentials() -> Any:
    return importlib.import_module(".credentials", __package__)


def tool(row: Row = None, tool_id: str = "") -> Resource:
    return Resource(TOOL, str(tool_id or (row["id"] if row is not None else "")), row)


def key(credential_id: str | None = "", grant: Row = None) -> Resource:
    return Resource(KEY, str(credential_id or ""), grant=grant)


def account(row: Row) -> Resource:
    return Resource(ACCOUNT, str(row["id"]), row)


def bot(bot_id: str) -> Resource:
    return Resource(BOT, str(bot_id))


def admin(c: Any, who: Who, admins: Any) -> bool:
    """A credential administrator (TICO_CREDENTIAL_ADMINS), never Tico support."""
    return not getattr(who, "support", None) and bool(_credentials().administrator(c, who, admins))


def _ask(c: Any, admins: Any, what: str) -> str:
    return str(_credentials().ask_admin_detail(c, admins, what))


def _subject(c: Any, subject: str) -> str:
    subject = str(subject or "")
    if subject.startswith("bot:"):
        found: Any = H.resolve_bot(c, subject) or {}
        return "bot:" + str(found.get("id", H.actor_id(subject)))
    return subject


def manages(c: Any, who: Who, subject: str, auth: Any) -> bool:
    """`subject` is a bot this person owns or manages (`Auth.bot_manager`)."""
    return (
        str(subject or "").startswith("bot:")
        and auth is not None
        and getattr(who, "role", "") in ("owner", "human")
        and bool(auth.bot_manager(c, who, H.actor_id(subject)))
    )


def _creator(c: Any, who: Who, credential_id: str) -> bool:
    return bool(credential_id) and bool(_credentials().creator(c, who, credential_id))


def _hidden(c: Any, who: Who, resource: Resource, admins: Any, auth: Any) -> bool:
    api_tools: Any = importlib.import_module(".api_tools", __package__)

    if resource.kind == TOOL and resource.row is not None:
        return not bool(api_tools.visible(c, who, resource.row, admins, auth))
    if resource.kind in (KEY, ACCOUNT) and resource.id:
        return bool(api_tools.key_hidden(c, who, resource.id, admins, auth))
    return False


def _login(c: Any, resource: Resource) -> bool:
    """A website login: an account, or a vault credential of kind `login` (backend/website_tools.py)."""
    if resource.kind == ACCOUNT:
        return True
    if resource.kind != KEY or not resource.id:
        return False
    row = c.execute("SELECT kind FROM credentials WHERE id=?", (resource.id,)).fetchone()
    return row is not None and row["kind"] == "login"


def acting_agent(who: Who) -> str:
    """The agent whose run makes this write, whoever's rights it carries: `by` on a delegation, else the run's own bot
    (`task_actor`, set on every lent identity), else the caller when it is a bot; "" for a person's own write. Every
    check on what an agent may do (itself, every agent, the catalog) reads this, never `via` or the principal."""
    lent: Any = H.DELEGATION.get() or {}
    if lent.get("by"):
        return str(lent["by"])
    if getattr(who, "task_actor", ""):
        return str(who.task_actor)
    return str(getattr(who, "actor", "")) if getattr(who, "role", "") == "bot" else ""


def _tools_owner(c: Any) -> str:
    """The Tools page's owner bot (backend/page_owners.py), "" when it has none."""
    page_owners: Any = importlib.import_module(".page_owners", __package__)
    return str(page_owners.owner(c, "tools", fallback=False) or "")


def may_change_access(
    c: Any, who: Who, resource: Resource, subject: str = "", action: str = EDIT, *, admins: Any = (), auth: Any = None
) -> bool:
    """Whether `who` may make this change. `decide` says why not."""
    return decide(c, who, resource, subject, action, admins=admins, auth=auth).ok


def decide(
    c: Any, who: Who, resource: Resource, subject: str = "", action: str = EDIT, *, admins: Any = (), auth: Any = None
) -> Decision:
    role = str(getattr(who, "role", ""))
    if getattr(who, "support", None) or role not in ("owner", "human", "bot"):
        return Decision(False, _ask(c, admins, "authorize this change"))
    if resource.kind == BOT:
        return _note(c, who, resource.id, admins, auth)
    if _hidden(c, who, resource, admins, auth):
        return Decision(False, "", hidden=True)
    subject = _subject(c, subject)
    via = str(getattr(who, "via", "") or "")
    acting = acting_agent(who)
    # An agent never gives itself access with someone's rights, and the Tools Owner never at all.
    if subject and acting and subject == acting and (via or acting == _tools_owner(c)):
        if acting == _tools_owner(c):
            return Decision(
                False, "The Tools Owner holds none of the keys it connects: give the level to the bot that needs it"
            )
        return Decision(False, "An agent never gives itself access: a person adds it under Who can use it")
    # A website login is shared only by a person's own tap: no agent shares one, with anyone's rights or its own, on
    # any route (every grant of a login reaches here: the vault's `grant` as a key, All bots as an account).
    if action == GRANT and (acting or via) and _login(c, resource):
        return Decision(False, "Sharing a website login is a person's own tap in Tools")
    is_admin = admin(c, who, admins)
    if action == AUDIENCE:
        name = resource.name or (resource.row["name"] if resource.row is not None else "this tool")
        return Decision(
            role == "owner" or is_admin,
            f"Only the owner, or a credential administrator who can see it, changes who can see {name}",
        )
    if action == REVOKE:
        return _revoke(c, who, resource, is_admin, admins, auth)
    if action == STORE:
        return _store(c, who, resource, subject, is_admin, admins, auth)
    if action in (CREATE, EDIT, DELETE) and resource.kind == ACCOUNT:
        holder = str(resource.row["holder"] or "") if resource.row is not None else ""
        own = bool(holder) and role in ("owner", "human") and (holder == who.actor or manages(c, who, holder, auth))
        return Decision(is_admin or own, "Ask a credential administrator or the account's holder to change this login.")
    if action == GRANT and subject and via in ("tools-owner", "assistant") and not getattr(who, "confirmed", False):
        # One named bot at a time: a person, a team or every bot (`bots`, which takes in the built-in that acted) is
        # the person's own tap.
        if not subject.startswith("bot:"):
            named = "every agent" if subject == "bots" else subject
            return Decision(
                False,
                f"Giving {named} access is the person's own tap in Tools: open it and add them under Who can use it",
                extra={"fix": "Add them under Who can use it", "link": "#/tools/keys?show=credentials"},
            )
    if action == GRANT and resource.kind in (KEY, ACCOUNT) and subject.startswith(("bot:", "human:")):
        return _grant(c, who, resource, subject, is_admin, admins, auth)
    return Decision(is_admin, _ask(c, admins, "authorize this change"))


def _itself(c: Any, who: Who, subject: str) -> bool:
    """`who` is a bot acting as itself (no person's rights lent to it) and `subject` is that bot: its own key. Never
    the Tools page's owner, which holds none of the keys it connects."""
    page_owners: Any = importlib.import_module(".page_owners", __package__)

    return (
        getattr(who, "role", "") == "bot"
        and not getattr(who, "via", "")
        and bool(subject)
        and subject == str(getattr(who, "actor", ""))
        and subject != page_owners.owner(c, "tools", fallback=False)
    )


def _grant(c: Any, who: Who, resource: Resource, subject: str, is_admin: bool, admins: Any, auth: Any) -> Decision:
    if is_admin:
        return Decision(True)
    cid = resource.id
    if _creator(c, who, cid):
        # The person who added a credential shares it with their own bots, as a direct grant, and a bot gives the key
        # it stored to itself; anyone else's bot or a person stays an administrator's call.
        if manages(c, who, subject, auth) or _itself(c, who, subject):
            return Decision(True)
        named: Any = H.bot(c, H.actor_id(subject)) or {}
        slug = str(named.get("slug") or H.actor_id(subject))
        return Decision(
            False,
            "You can share a credential you added only with bots you own or manage. "
            + _ask(c, admins, f"give it to {slug}"),
        )
    owner_ids: Any = importlib.import_module(".bot_access", __package__).owner_ids
    held: Any = (
        _credentials().effective_grant(c, cid, who.actor) if getattr(who, "role", "") in ("human", "owner") else None
    )
    row: Any = (
        c.execute("SELECT owner_id,bot_owners FROM bot_config WHERE bot=?", (H.actor_id(subject),)).fetchone()
        if subject.startswith("bot:")
        else None
    )
    me = str(H.actor_id(who.actor))
    # A bot's owners (its creator and co-owners, and its operator) attach credentials they hold themselves.
    if held and row and (row["owner_id"] == me or me in owner_ids(row["bot_owners"])):
        return Decision(True, parent=str(held["id"]))
    return Decision(
        False,
        _ask(c, admins, "give a bot a credential")
        + (", or give a bot you manage a credential you added or were granted" if not held else ""),
    )


def _revoke(c: Any, who: Who, resource: Resource, is_admin: bool, admins: Any, auth: Any) -> Decision:
    grant: Any = resource.grant or {}
    mine = bool(grant) and (_creator(c, who, resource.id) or grant["granted_by"] == who.actor)
    return Decision(
        is_admin or (mine and manages(c, who, str(grant["subject"]), auth)),
        _ask(c, admins, "take this away") + ", or take a credential you added away from a bot you manage",
    )


def _store(c: Any, who: Who, resource: Resource, subject: str, is_admin: bool, admins: Any, auth: Any) -> Decision:
    team_rules: Any = importlib.import_module(".team_rules", __package__)

    if is_admin:
        return Decision(True)
    if _itself(c, who, subject):
        # A bot keeps a value it already holds as its own key: a new one, or over one it stored itself.
        return Decision(
            not resource.id or _creator(c, who, resource.id),
            "That key is not one you stored: store yours as a new key, or ask a credential admin to change it",
        )
    member = (
        getattr(who, "role", "") in ("human", "owner")
        and team_rules.load(c)["members_store_credentials"]
        and manages(c, who, subject, auth)
        and (not resource.id or _creator(c, who, resource.id))
    )
    names: list[str] = list(_credentials().admin_names(c, admins))
    return Decision(
        bool(member),
        "Only a credential admin, or someone who manages this bot, can store credentials for it"
        + (". Ask " + ", ".join(names[:3]) if names else ""),
    )


def _note(c: Any, who: Who, bot_id: str, admins: Any, auth: Any) -> Decision:
    """A bot's `tools:` entries name a service and a variable, never a value, and grant nothing: everyone with a reason
    to keeps them. A built-in acting for someone has that someone's rights here, so a member never changes a bot that
    is not theirs through one."""
    page_owners: Any = importlib.import_module(".page_owners", __package__)

    if who.role == "bot" and who.actor == "bot:" + bot_id:
        return Decision(True)  # its own entries, also when BotOps or the Tools Owner carries its request
    own = who.role == "bot" and not who.via
    if own and who.actor in ("bot:" + H.FLEET_MAINTAINER, page_owners.owner(c, "tools")):
        return Decision(True)
    if admin(c, who, admins):
        return Decision(True)
    if who.role == "owner" or manages(c, who, "bot:" + bot_id, auth):
        return Decision(True)
    if auth is not None and auth.botops_manages(c, who, bot_id):
        return Decision(True)
    found: Any = H.bot(c, bot_id) or {}
    name = str(found.get("display_name") or bot_id)
    return Decision(
        False,
        f"Only {name} itself, BotOps, the Tools Owner, a credential admin, or someone who manages {name} changes its "
        "tools. Ask the Tools Owner in its chat (or BotOps): it makes the change for you",
        extra={"fix": "Ask the Tools Owner to add it: it acts with your rights", "link": "#/tools"},
    )


ADDRESS_FIELDS = ("base_url", "auth_scheme", "check_path")


def catalog_only(who: Who, service: Any = "", fields: Any = None, mcp_url: str = "") -> None:
    """The one guard on an address an agent brings (`acting_agent`: BotOps, the Assistant, the Tools Owner or any
    agent, in its own run or with a person's rights): only the catalog's (backend/tool_services.py), a known service
    with its own address, scheme and check path, or a vendor's listed MCP server (`mcp_url`). Called before anything
    reaches the address (a probe, a check) or stores it (a tool, a bot.yaml entry for BotOps). Any other address is a
    person's to add."""
    if not acting_agent(who):
        return
    services: Any = importlib.import_module(".tool_services", __package__)
    if mcp_url:
        ok = mcp_url.strip().rstrip("/") in {u.rstrip("/") for u in services.MCP_URLS.values()}
    else:
        known: Any = services.defaults(service)
        given: Any = fields or {}
        ok = bool(known) and all(not given.get(k) or given[k] == known[k] for k in ADDRESS_FIELDS)
    if not ok:
        raise Problem("catalog_only", "An agent adds tools from the catalog only: a person adds any other address", 403)


def require(
    c: Any,
    who: Who,
    resource: Resource,
    subject: str = "",
    action: str = EDIT,
    *,
    admins: Any = (),
    auth: Any = None,
    refused: str = "",
) -> Decision:
    """`may_change_access`, raising the refusal: a 404 for a tool or key `who` may not see, else a 403 in plain words
    (`refused` when the caller words it). Returns the decision (its `parent` for a holder's grant)."""
    found = decide(c, who, resource, subject, action, admins=admins, auth=auth)
    if found.ok:
        return found
    if found.hidden:
        if resource.kind == TOOL:
            raise Problem("not_found", "No such tool", 404)
        raise Problem("not_found", "Credential not found", 404)
    raise Problem("forbidden", refused or found.why, 403, **({"extra": found.extra} if found.extra else {}))
