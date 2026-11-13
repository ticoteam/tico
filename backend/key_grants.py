# pyright: strict
"""The Tools owner proposes, the person grants (spec tools-chat-key-drop, Scope 12–13).

Keys dropped in the Tools chat reach the owner bot as names only (backend/secret_drop.py, ui/app/tools-key-drop.ts).
The owner proposes which tool and which bots each key is for; the server turns that proposal into a card of rows it
builds itself (Key → Tool → Level → Bots), posted as the owner in the person's chat with it. Only a credential
administrator's Grant on the card grants. The card can never offer a grant to the owner bot, to every bot or to every
computer, whatever the proposal says, and the owner holds none of the keys. Grant attaches each key to its tool as that
tool's key (kind, username and level) and grants each bot that level; then the tool's own server check runs and its
result goes on the card's row. No value is read or returned here except by the check, on the server.

  propose(c, who, thread_id, rows)   the card message (who: the Tools page's owner bot)
  grant(store, vault, who, message_id, keep)  the card after the grants and checks (who: a credential admin)

The card lives in its message's refs (`card: key_grant`); only `propose` mints one (`MINTING`, checked by
hubdb.check_message_refs), so a message from any client that carries `key_grant` is refused.
"""

import contextvars
import json
from typing import Any, cast

from . import secret_drop
from .store import H as _H
from .store import Problem

# The store and the modules below are not strictly typed: they are used through Any here.
H: Any = _H
Row = dict[str, Any]


def _threads() -> Any:
    """Imported when used: threads, api_tools and credentials import much of the backend (and hubdb imports this)."""
    from . import threads

    return threads


def _api_tools() -> Any:
    from . import api_tools

    return api_tools


def _credentials() -> Any:
    from . import credentials

    return credentials


CARD = "key_grant"
MINTING: contextvars.ContextVar[bool] = contextvars.ContextVar("key_grant_minting", default=False)
LEVELS = {"read": "Read", "read_write": "Read and write"}
MAX_ROWS = 20
MAX_BOTS = 10
# Words that would mean everyone: the card offers named bots only.
EVERYONE = {"*", "all", "every", "everyone", "all-bots", "all_bots", "computers", "every-computer", "team", "company"}


def _owner(c: Any) -> str | None:
    from . import page_owners

    return page_owners.owner(c, secret_drop.TOOLS_PAGE)


def _members(c: Any, thread: Row) -> list[str]:
    return [str(m) for m in _threads()._dm_members(c, thread)]


def _thread(c: Any, thread_id: str, owner: str) -> Row:
    thread: Row | None = _threads().thread_row(c, thread_id)
    if not thread or not secret_drop.guarded(c, thread) or owner not in _members(c, thread):
        raise Problem("not_found", "No chat with the Tools owner here", 404)
    return thread


def _person(c: Any, thread: Row, owner: str) -> str | None:
    people = [m for m in _members(c, thread) if m != owner]
    return people[0] if len(people) == 1 else None


def _credential(c: Any, ref: object, person: str) -> Any:
    """A key the person saved from this chat, by its name or variable."""
    name = str(ref or "").strip()
    row = c.execute(
        "SELECT * FROM credentials WHERE source='tools-chat' AND created_by=? AND (upper(name)=upper(?) OR env=?) "
        "AND kind!='login' ORDER BY updated_at DESC LIMIT 1",
        (person, name, name),
    ).fetchone()
    if not row:
        raise Problem("proposal", f"No key named {name or '(none)'} was saved in this chat", 422)
    return row


def _tool(c: Any, ref: object) -> Any:
    ref = str(ref or "").strip().removeprefix("api:")
    row = c.execute("SELECT * FROM tools WHERE id=? OR lower(name)=lower(?) LIMIT 1", (ref, ref)).fetchone()
    if not row:
        raise Problem("proposal", f"No tool {ref or '(none)'} in Tools. Add it in Tools > Add first", 422)
    return row


def _list(value: Any) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _bots(c: Any, refs: Any, owner: str) -> list[Row]:
    given = _list(refs)
    if not given or len(given) > MAX_BOTS:
        raise Problem("proposal", f"Name 1 to {MAX_BOTS} bots for each key", 422)
    out: list[Row] = []
    for ref in given:
        word = str(ref or "").strip()
        if word.lower() in EVERYONE:
            raise Problem("proposal", "A key goes to named bots only, never to every bot or every computer", 422)
        bot: Row | None = H.resolve_bot(c, word if word.startswith("bot:") else "bot:" + word) or H.resolve_bot(c, word)
        if not bot or bot.get("state") in ("planned", "archived"):
            raise Problem("proposal", f"No active bot {word}", 422)
        actor = "bot:" + str(bot["id"])
        if actor == owner:
            raise Problem("proposal", "The Tools owner holds none of the keys it connects", 422)
        if actor not in [b["id"] for b in out]:
            out.append({"id": actor, "name": bot.get("display_name") or bot.get("slug") or bot["id"]})
    return out


def _rows(c: Any, rows: Any, person: str, owner: str) -> list[Row]:
    asked = _list(rows)
    if not asked or len(asked) > MAX_ROWS:
        raise Problem("proposal", f"Propose 1 to {MAX_ROWS} keys", 422)
    built: list[Row] = []
    for given in asked:
        row: Row = cast("Row", given) if isinstance(given, dict) else {}
        level = str(row.get("level") or "read")
        if level not in LEVELS:
            raise Problem("proposal", "A level is read or read_write", 422)
        cred, tool = _credential(c, row.get("key"), person), _tool(c, row.get("tool"))
        built.append(
            {
                "key": cred["name"],
                "credential_id": cred["id"],
                "username": cred["username"] or "",
                "tool_id": tool["id"],
                "tool_name": tool["name"],
                "level": level,
                "bots": _bots(c, row.get("bots"), owner),
            }
        )
        built[-1]["replaces"] = _replaced(c, built[-1], owner)
    return built


def _holders(c: Any, credential_id: str) -> list[Row]:
    """Who holds a key now ({subject, level}): each live grant's subject and level, the vault's effective rule."""
    effective_grant = _credentials().effective_grant
    out: list[Row] = []
    for g in c.execute(
        "SELECT DISTINCT subject FROM credential_grants WHERE credential_id=? AND revoked_at IS NULL", (credential_id,)
    ).fetchall():
        grant: Any = effective_grant(c, credential_id, g["subject"])
        if grant is not None:
            out.append({"subject": str(g["subject"]), "level": str(grant["access_level"] or "read_write")})
    return out


def _who(c: Any, subject: str) -> str:
    if subject.startswith("bot:"):
        bot: Row | None = H.resolve_bot(c, subject)
        return str((bot or {}).get("display_name") or (bot or {}).get("slug") or subject[4:])
    if subject.startswith("human:"):
        row = c.execute("SELECT name FROM humans WHERE id=?", (subject[6:],)).fetchone()
        return str(row["name"] if row and row["name"] else subject[6:])
    return "Every computer" if subject == "computers" else subject


def _replaced(c: Any, row: Row, owner: str) -> Row | None:
    """The tool's key at the row's level that this key replaces, and who keeps their access on the new key (every bot
    and person who holds it) or loses it (the owner bot, which holds none of these keys, and every-computer grants)."""
    old = c.execute(
        "SELECT id, name FROM credentials WHERE tool_id=? AND access_level=? AND id<>? LIMIT 1",
        (row["tool_id"], row["level"], row["credential_id"]),
    ).fetchone()
    if not old:
        return None
    holders = _holders(c, old["id"])
    keeps = [h for h in holders if h["subject"].startswith(("bot:", "human:")) and h["subject"] != owner]
    loses = [h for h in holders if h not in keeps]
    return {
        "key": old["name"],
        "keeps": [_who(c, h["subject"]) for h in keeps],
        "loses": [_who(c, h["subject"]) for h in loses],
    }


def _card(message: Row) -> Row | None:
    refs: Any = message.get("refs") or {}
    if isinstance(refs, str):
        refs = json.loads(refs or "{}")
    if not isinstance(refs, dict):
        return None
    shown = cast("Row", refs)
    return cast("Row | None", shown.get(CARD)) if shown.get("card") == CARD else None


def propose(c: Any, who: Any, thread_id: str, rows: list[Row], note: str = "") -> Row:
    """The owner's proposal, as a card the server builds: the rows checked and named, posted as the owner."""
    owner = _owner(c)
    if not owner or who.actor != owner:
        raise Problem("forbidden", "Only the Tools page's owner proposes key grants", 403)
    thread = _thread(c, thread_id, owner)
    person = _person(c, thread, owner)
    if not person:
        raise Problem("not_found", "No chat with the Tools owner here", 404)
    built = _rows(c, rows, person, owner)
    card: Row = {"id": H.new_id(), "state": "proposed", "person": person, "rows": built, "results": []}
    text = (
        (str(note).strip()[:500] + "\n" if str(note or "").strip() else "")
        + "Proposal: "
        + "; ".join(
            f"{r['key']} → {r['tool_name']} · {LEVELS[r['level']]} · {', '.join(b['name'] for b in r['bots'])}"
            for r in built
        )
    )
    secret_drop.refuse(c, thread, person, text)  # the card's words never carry a key either
    token = MINTING.set(True)
    try:
        # Written as the owner by the server itself: the rows are the server's, checked above.
        return _threads().post(c, owner, thread["id"], text, kind="notice", refs={"card": CARD, CARD: card})
    finally:
        MINTING.reset(token)


def _attach(c: Any, who: Any, vault: Any, row: Row) -> None:
    """The key becomes its tool's key at the row's level (a password kind counts as the tool's key from now on). A key
    the tool held at that level moves off it, and each bot and person who held it keeps their level on the new key:
    replacing a key never takes a tool away from anyone silently (the card names who keeps it and who loses it)."""
    api_tools = _api_tools()
    owner = _owner(c)
    old = c.execute(
        "SELECT id FROM credentials WHERE tool_id=? AND access_level=? AND id<>?",
        (row["tool_id"], row["level"], row["credential_id"]),
    ).fetchall()
    moving: list[Row] = []
    for key in old:
        for held in _holders(c, key["id"]):
            if held["subject"].startswith(("bot:", "human:")) and held["subject"] != owner:
                moving.append(held)
            api_tools._revoke(c, who, key["id"], held["subject"])
        c.execute("UPDATE credentials SET tool_id=NULL WHERE id=?", (key["id"],))
    c.execute(
        "UPDATE credentials SET tool_id=?, access_level=?, kind=CASE WHEN kind IN ('api_key','token') THEN kind ELSE "
        "'api_key' END WHERE id=?",
        (row["tool_id"], row["level"], row["credential_id"]),
    )
    tool = c.execute("SELECT auth_scheme FROM tools WHERE id=?", (row["tool_id"],)).fetchone()
    if row["username"] and tool and tool["auth_scheme"] == "basic_user":
        # A login's check signs in as its username with the key as the password (AC-18): a key-only Basic scheme
        # would drop the username and fail.
        c.execute("UPDATE tools SET auth_scheme='basic_password' WHERE id=?", (row["tool_id"],))
        H.event(c, who.actor, "tool.updated", row["tool_id"], {"auth_scheme": "basic_password", "via": "tools-chat"})
    for held in moving:
        if held["subject"] not in [b["id"] for b in row["bots"]]:
            kept = "read" if "read" in (row["level"], held["level"]) else "read_write"  # never above the key's level
            api_tools.set_level(c, who, vault, row["tool_id"], held["subject"], kept)
    for bot in row["bots"]:
        api_tools.set_level(c, who, vault, row["tool_id"], bot["id"], row["level"])
    H.event(c, who.actor, "tool.key_added", row["tool_id"], {"credential": row["credential_id"], "via": "tools-chat"})


def _save(c: Any, message_id: str, card: Row) -> None:
    raw = c.execute("SELECT refs FROM messages WHERE id=?", (message_id,)).fetchone()["refs"]
    refs = json.loads(raw or "{}")
    refs[CARD] = card
    c.execute("UPDATE messages SET refs=? WHERE id=?", (json.dumps(refs), message_id))


def grant(store: Any, vault: Any, who: Any, message_id: str, keep: list[int] | None = None) -> Row:
    """The person's Grant: exactly the rows kept (`keep`: their positions; None keeps all), then each tool's check.
    The card after it, with a result per row: Connected to <tool>, or Didn't work: <reason>."""
    api_tools, credentials = _api_tools(), _credentials()
    with store.transaction() as c:
        credentials.require_admin(c, who, vault.admins)
        message: Row | None = H.message(c, message_id)
        card = _card(message or {})
        owner = _owner(c)
        if not owner or not message or card is None or message.get("sender_id") != owner:
            raise Problem("not_found", "No key proposal here", 404)
        thread = _thread(c, message["conversation_id"], owner)
        if who.actor not in _members(c, thread):
            raise Problem("not_found", "No key proposal here", 404)
        if card["state"] != "proposed":
            return card
        positions = list(range(len(card["rows"]))) if keep is None else [int(i) for i in keep]
        if not positions or any(i < 0 or i >= len(card["rows"]) for i in positions):
            raise Problem("proposal", "Keep at least one of the card's rows", 422)
        # Checked again at Grant: the rows are the server's, and a key or bot may have changed since.
        again = [card["rows"][i] for i in positions]
        rows = _rows(
            c,
            [
                {"key": r["key"], "tool": r["tool_id"], "level": r["level"], "bots": [b["id"] for b in r["bots"]]}
                for r in again
            ],
            card["person"],
            owner,
        )
        for row in rows:
            _attach(c, who, vault, row)
        card = card | {"state": "granted", "rows": rows, "granted_by": who.actor, "results": []}
        _save(c, message_id, card)
    results: list[Row] = []
    for tool_id in dict.fromkeys(r["tool_id"] for r in rows):
        try:
            checks: dict[str, Row] = {r["credential_id"]: r for r in api_tools.check_tool(store, vault, tool_id)}
        except Problem as exc:
            checks = {r["credential_id"]: {"ok": False, "detail": exc.detail} for r in rows if r["tool_id"] == tool_id}
        for row in rows:
            if row["tool_id"] == tool_id:
                got = checks.get(row["credential_id"]) or {"ok": False, "detail": "Not checked"}
                results.append(
                    {
                        "key": row["key"],
                        "tool_id": tool_id,
                        "ok": bool(got["ok"]),
                        "detail": f"Connected to {row['tool_name']}" if got["ok"] else f"Didn't work: {got['detail']}",
                    }
                )
    with store.transaction() as c:
        card = card | {"results": results}
        _save(c, message_id, card)
    return card
