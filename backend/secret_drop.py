# pyright: strict
"""The Tools chat's key detector, server side (spec tools-chat-key-drop). The browser runs the same rules
(ui/app/secret-drop.ts) on a paste before it reaches the text box; this one is the backstop on every message a person
puts into the Tools owner's conversation, from any client, before it is stored or reaches a model.

The rules are `secret_rules.json`, beside this file, the one file both sides read (the page fetches it from
GET /api/v2/secret-rules). `backend/tests/fixtures/secret_drop_cases.json` is run by both test suites, so the two
detectors agree on every case (tier and value). Keep this file and secret-drop.ts step for step alike.

  RULES                      the rule file, read once
  normalize(text)            NFKC, zero-width characters removed: what both sides scan
  scan(text)                 [Item]: each secret found, in text order, certain before suspect where they overlap
  guarded(c, thread)         whether a thread is a person's conversation with the Tools page's owner
  refuse(c, thread, author, text, via, unmarked)  raises 422 looks_like_key when the backstop refuses the text

An Item is a dict: tier ('certain' | 'suspect'), rule, kind, service, name, type (a credential kind), username,
start, end (offsets in the normalized text) and value. Nothing here logs or returns a value outside an Item.
"""

import hashlib
import json
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import sqlite3

RULES_PATH = Path(__file__).with_name("secret_rules.json")
TOOLS_PAGE = "tools"
# The web chat sends a suspect the person un-marked as its digest; any other client's suspect is refused.
WEB_VIAS = ("", "web")
COPY_WEB = "This looks like a key. Save it instead?"
COPY_ELSEWHERE = (
    "This looked like a key, so Tico didn't keep it. It is still in {where}: delete it there, then drop it in Tools."
)
WHERE = {"slack": "Slack", "email": "your mail", "mail": "your mail", "sms": "your messages", "phone": "your messages"}

Item = dict[str, Any]


@lru_cache(maxsize=1)
def rules() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(RULES_PATH.read_text(encoding="utf-8")))


def _flags(letters: str) -> int:
    return (re.I if "i" in letters else 0) | (re.M if "m" in letters else 0)


@lru_cache(maxsize=256)
def _re(pattern: str, flags: str = "") -> "re.Pattern[str]":
    return re.compile(pattern, _flags(flags))


def normalize(text: str) -> str:
    return _re(str(rules()["zero_width"])).sub("", unicodedata.normalize("NFKC", text or ""))


def _placeholder(value: str) -> bool:
    return bool(_re(str(rules()["placeholder"]), "i").match(value))


def _negative_full(value: str, shapes: str = "negative") -> bool:
    """Whether `value` is wholly one of the ordinary shapes in the rule list `shapes`."""
    return any(_re(p).fullmatch(value) for p in cast("list[str]", rules()[shapes]))


def secretish(value: str, shapes: str | None = "negative") -> bool:
    """A value that reads as a key, not a word: 6+ characters with a digit or a symbol, or mixed case past the first
    letter, and no placeholder or ordinary shape (an address, an email, an id, a path; `shapes`: see _shapes)."""
    if len(value) < 6 or _placeholder(value) or (shapes is not None and _negative_full(value, shapes)):
        return False
    digit, symbol = _has(value, "[0-9]"), _has(value, "[^A-Za-z0-9]")
    mixed = _has(value[1:], "[A-Z]") and _has(value, "[a-z]")
    return digit or symbol or (mixed and len(value) >= 10)


def _has(text: str, cls: str) -> bool:
    """Character classes are ASCII on both sides: a letter outside ASCII counts as a symbol in Python and JavaScript."""
    return bool(_re(cls).search(text))


def _shapes(rule: dict[str, Any], name: str) -> str | None:
    """Which ordinary shapes still cancel a value after the context word `name`: every one with no strong word; after a
    strong one only a web address, an email or a path from /, ~/, ./ or ../ (a hex string, a UUID or a relative path
    there is a key); after a password word none, since a password can take any shape. pass and pw are plain English
    too, so after a space they are not strong (spec Scope 4b: hex is negative only with no context word)."""
    word = _word(name) if name else None
    if not word or not word["strong"] or (rule.get("spaced") and word.get("spaced") is False):
        return "negative"
    return None if word["type"] == "password" else "negative_keyed"


def _word(name: str) -> dict[str, Any] | None:
    lowered = name.lower()
    for word in cast("list[dict[str, Any]]", rules()["words"]):
        if _re(str(word["re"])).search(lowered):
            return word
    return None


def _service_near(text: str, start: int) -> str:
    """The service named last before `start` on the same line (at most 80 characters back), or ''."""
    window, best, at = text[max(0, start - 80) : start].lower(), "", -1
    window = window[window.rfind("\n") + 1 :]
    for svc in cast("list[dict[str, str]]", rules()["services"]):
        for m in _re("(?<![a-z0-9])(?:" + svc["re"] + ")(?![a-z0-9])").finditer(window):
            if m.start() > at:
                best, at = svc["service"], m.start()
    return best


def _service_in(name: str) -> str:
    """The service a key's own name names (POSTHOG_API_KEY), or ''."""
    words = _re("[^a-z0-9]+").sub(" ", name.lower())
    return _service_near(" " + words + " ", len(words) + 2)


def _service_word(word: str) -> str:
    for svc in cast("list[dict[str, str]]", rules()["services"]):
        if _re("^(?:" + svc["re"] + ")$").match(word.lower()):
            return svc["service"]
    return ""


def _env_name(text: str) -> str:
    return _re("_+").sub("_", _re("[^A-Z0-9_]").sub("_", text.upper())).strip("_")


def _prefixed(service: str, base: str) -> str:
    return (_env_name(service) + "_" + base) if service and not base.startswith(_env_name(service) + "_") else base


def _certain(text: str, rule: dict[str, Any]) -> list[Item]:
    found: list[Item] = []
    for m in _re(str(rule["re"]), str(rule.get("flags", ""))).finditer(text):
        group = next((g for g in cast("list[int]", rule["group"]) if m.start(g) >= 0), -1)
        if group < 0 or m.end(group) <= m.start(group):
            continue
        value = m.group(group)
        check, kind, base, kind_of = (
            str(rule.get("check", "")),
            str(rule["kind"]),
            str(rule.get("base", "")),
            rule["type"],
        )
        named = m.group(int(rule["name_group"])) if rule.get("name_group") else ""
        shapes = _shapes(rule, m.group(int(rule["word_group"])) if rule.get("word_group") else named)
        if check == "not_placeholder" and _placeholder(value):
            continue
        if check == "secretish" and not secretish(value, shapes):
            continue
        if check in ("env", "context"):
            word = _word(named)
            if shapes != "negative" and (_placeholder(value) or (shapes and _negative_full(value, shapes))):
                continue
            if shapes == "negative" and not secretish(value):
                continue
            if word:
                kind, kind_of, base = str(word["kind"]), word["type"], str(word["base"]) or base
        elif check == "secretish" and named:
            word = _word(named)
            if word:
                kind, kind_of, base = str(word["kind"]), word["type"], str(word["base"]) or base
        service = str(rule.get("service", ""))
        if rule.get("service_group"):
            service = _service_word(m.group(int(rule["service_group"])))
        username = m.group(int(rule["user"])) if rule.get("user") else ""
        if not service and named:
            service = _service_in(named)
        if not service and (not named or _word_only(named)):
            service = _service_near(text, m.start())
        if rule["id"] == "url_login":
            scheme = text[m.start() : text.index("://", m.start())]
            if _re(str(rules()["db_schemes"]), "i").match(scheme):
                service = "Database"
        if rule.get("name"):
            name = str(rule["name"])
        elif check == "env" or (check == "context" and named and not _word_only(named)):
            name = _env_name(named)
        else:
            name = _prefixed(service, base)
        if kind == "Login":
            kind = (service + " login") if service else "Login"
        found.append(
            {
                "tier": "certain",
                "rule": rule["id"],
                "kind": kind,
                "service": service,
                "name": name,
                "type": kind_of,
                "username": username,
                "start": m.start(group),
                "end": m.end(group),
                "value": value,
                "structured": check in ("env", "context") and bool(named),
            }
        )
    return found


def _word_only(named: str) -> bool:
    """A key name that is only a context word (password, token, api key): the name comes from the service instead."""
    word = _word(named)
    return bool(word and str(word["base"]))


def _entropy(token: str) -> float:
    counts: dict[str, int] = {}
    for ch in token:
        counts[ch] = counts.get(ch, 0) + 1
    return -sum((n / len(token)) * math.log2(n / len(token)) for n in counts.values())


def _plain(part: str, plain_hex: int) -> bool:
    return (
        bool(_re("[A-Za-z]+").fullmatch(part))
        or bool(_re("[0-9]+").fullmatch(part))
        or len(part) <= 3
        or (len(part) <= plain_hex and bool(_re("[0-9a-fA-F]+").fullmatch(part)))
    )


def _suspect(token: str) -> bool:
    cfg = cast("dict[str, float]", rules()["suspect"])
    parts = [p for p in _re("[-_./:]").split(token) if p]
    if all(_plain(p, int(cfg["plain_hex"])) for p in parts) or _placeholder(token):
        return False
    digit, symbol = _has(token, "[0-9]"), _has(token, "[^A-Za-z0-9]")
    lower, upper = _has(token, "[a-z]"), _has(token, "[A-Z]")
    if len(token) >= cfg["entropy_length"] and _entropy(token) >= cfg["entropy_bits"] and (digit or symbol):
        return True
    return len(token) >= cfg["mixed_length"] and digit and (lower + upper + digit + symbol) >= 3


def _suspects(text: str, taken: list[Item]) -> list[Item]:
    masked = list(text)
    for item in taken:
        masked[item["start"] : item["end"]] = " " * (item["end"] - item["start"])
    flat = "".join(masked)
    for pattern in cast("list[str]", rules()["negative"]):
        flat = _re(pattern).sub(lambda m: " " * len(m.group(0)), flat)
    found: list[Item] = []
    for m in _re(str(rules()["token"])).finditer(flat):
        token = m.group(0).rstrip(".,:;!?)")
        token = token.lstrip("(")
        lead = m.group(0).find(token)
        if not token or not _suspect(token):
            continue
        start = m.start() + lead
        service = _service_near(text, start)
        found.append(
            {
                "tier": "suspect",
                "rule": "suspect",
                "kind": "Looks like a key",
                "service": service,
                "name": _prefixed(service, "API_KEY") if service else "SECRET",
                "type": "api_key",
                "username": "",
                "start": start,
                "end": start + len(token),
                "value": text[start : start + len(token)],
                "structured": False,
            }
        )
    return found


def _overlaps(item: Item, taken: list[Item]) -> Item | None:
    return next((t for t in taken if item["start"] < t["end"] and t["start"] < item["end"]), None)


def scan(text: str) -> list[Item]:
    """Each secret in `text` (normalized first), in text order. Rules run in the file's order and a later rule's match
    that overlaps an earlier one is dropped; a structure rule (a .env or key: value line) on the same value names it."""
    t = normalize(text)
    taken: list[Item] = []
    for rule in cast("list[dict[str, Any]]", rules()["certain"]):
        for item in _certain(t, rule):
            other = _overlaps(item, taken)
            if other is None:
                taken.append(item)
            elif item["structured"] and other["start"] == item["start"] and other["end"] == item["end"]:
                other["name"] = item["name"]
    taken.extend(_suspects(t, taken))
    taken.sort(key=lambda i: i["start"])
    seen: dict[str, int] = {}
    for item in taken:
        item.pop("structured", None)
        n = seen[item["name"]] = seen.get(item["name"], 0) + 1
        if n > 1:
            item["name"] = item["name"] + "_" + str(n)
    return taken


def digest(value: str) -> str:
    """How the web chat names a suspect the person un-marked, so the server lets that one through and no other."""
    return hashlib.sha256(normalize(value).encode("utf-8")).hexdigest()


def guarded(c: "sqlite3.Connection", thread: dict[str, Any] | None) -> bool:
    """A person's conversation with the Tools page's owner: a DM root whose one other member is that bot, or its
    Home (page-owner chat: one chat per bot). Sub-threads under it are guarded with it."""
    from . import page_owners, threads

    if not thread:
        return False
    bot = page_owners.owner(c, TOOLS_PAGE)
    if not bot:
        return False
    root = cast("dict[str, Any]", threads.root_of(c, thread))  # pyright: ignore[reportUnknownMemberType]
    audience = threads.audience_of(root)  # pyright: ignore[reportUnknownMemberType]
    if audience == "home":
        return str(threads.home_owner(root) or "") == bot  # pyright: ignore[reportUnknownMemberType]
    if audience in ("personal", "participants"):
        members = cast("list[str]", threads._dm_members(c, root))  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]
        return bot in members and all(m == bot or not m.startswith("bot:") for m in members)
    return False


def refuse(
    c: "sqlite3.Connection",
    thread: dict[str, Any] | None,
    author: str,
    text: str,
    via: str | None = None,
    unmarked: list[str] | None = None,
    *,
    audit: bool = True,
) -> None:
    """The backstop (spec Scope 10): a person's message into the Tools owner's conversation that holds a key is refused
    before anything is stored, checked or put in a prompt. Certain matches are refused from every client; a suspect is
    let through only from the web chat when the person un-marked it there (its digest in `unmarked`), with an audit row
    holding no value (`audit`: the write's own call; a read-only precheck before the org-rule check writes nothing).
    The refusal never repeats the text."""
    if not str(author).startswith("human:") or not text or not guarded(c, thread):
        return
    refuse_found(c, scan(text), author, (thread or {}).get("id", ""), via, unmarked, audit=audit)


def refuse_found(
    c: "sqlite3.Connection",
    found: list[Item],
    author: str,
    target: str,
    via: str | None,
    unmarked: list[str] | None,
    *,
    audit: bool = True,
) -> None:
    from .store import H, Problem

    if not found:
        return
    web = (via or "") in WEB_VIAS
    allowed: set[str] = set(unmarked or ()) if web else set()
    if any(i["tier"] == "certain" or digest(i["value"]) not in allowed for i in found):
        where = WHERE.get(str(via or "").lower())
        raise Problem("looks_like_key", COPY_ELSEWHERE.format(where=where) if where else COPY_WEB, 422)
    if audit:
        H.event(c, author, "secret_drop.suspect_let_through", target, {"count": len(found)})  # pyright: ignore[reportUnknownMemberType]


def refuse_start(
    c: "sqlite3.Connection", author: str, members: list[str], text: str, unmarked: list[str] | None = None
) -> None:
    """The backstop for a DM's first message, before the DM exists: a DM with the Tools page's owner alone."""
    from . import page_owners
    from .store import H

    if not str(author).startswith("human:") or not text:
        return
    bot = page_owners.owner(c, TOOLS_PAGE)
    others = {str(H.resolve_actor(c, m) or m) for m in members} - {author}  # pyright: ignore[reportUnknownMemberType]
    if bot and others == {bot}:
        refuse_found(c, scan(text), author, "", None, unmarked, audit=False)
