# pyright: strict
"""Directory rules (track 5): which rules an outbound action meets, found by walking the Directory from its targets and
from self.

A rule with a `toward_scope` is structured. It binds the actor when its scope reaches an entity the action touches and
its `applies_when` holds. The walk starts at the targets, at the entities the addresses resolve to, and at self (an
obligation on us, such as a contract's duty on our side), follows links whose type inherits rules up to WALK_DEPTH, and
says when it was cut off. A rule without a scope is free text: it stays on the judged path (backend/org_rule_check.py),
which this module does not read.

What a structured rule does to one action, by its effect:
- block, escalate or warn: it applies to an entity the action touches, and the actor is one it binds. Its if_broken
  decides which.
- ok: it applies, and the actor is not one it binds.
- skip: its scope reaches nothing the action touches, or its applies_when does not hold.
- block: it cannot be checked (a scope term or condition this module does not read, or no self). Fail closed.

`check` turns the rules into the verdict an outbound action gets. An error while checking holds the action, with the
error's class name as the reason: the check never lets an action through because it failed.

A path names only what the reader may see. A step through an entity the reader cannot see is written as a restricted
entity, so the path shows the links without the name.
"""

import sqlite3
from collections.abc import Sequence
from typing import Any, cast

from backend import org_rules as R
from backend.entities import store as S

WALK_DEPTH = S.WALK_DEPTH
HOLDING = ("block", "escalate")
EFFECT_OF = {"block": "block", "escalate": "escalate", "warn": "warn"}
HIDDEN = "a restricted entity"
SCOPE_HEADS = ("entity", "type", "kind", "self", "link")


class ScopeError(ValueError):
    """A scope term or an applies_when condition this module does not read."""


def parse_scope(terms: Sequence[object]) -> list[tuple[str, ...]]:
    """toward_scope terms: `entity:<id>`, `type:<entity type>`, `kind:<organization kind>`, `self:<link key>` (an
    entity with that link to self, e.g. self:customer_of), `link:<link key>:<entity id>` (an entity with that link to
    the entity, e.g. link:located_in:place/california). Anything else is a ScopeError."""
    out: list[tuple[str, ...]] = []
    for raw in terms:
        text = str(raw).strip()
        head, _, rest = text.partition(":")
        if head in ("entity", "type", "kind", "self") and rest and ":" not in rest:
            out.append((head, rest))
        elif head == "link" and rest.count(":") == 1 and all(rest.split(":")):
            key, target = rest.split(":")
            out.append((head, key, target))
        else:
            heads = ", ".join(f"{h}:" for h in SCOPE_HEADS)
            raise ScopeError(f"{text!r} is not a scope term ({heads} with an id or key)")
    return out


def parse_condition(text: object) -> tuple[str, str]:
    """applies_when: `self <link key> <entity id>`, true when self has that link to the entity."""
    parts = str(text or "").split()
    if len(parts) != 3 or parts[0] != "self":
        raise ScopeError(f"{text!r} is not a condition (self <link key> <entity id>)")
    return parts[1], parts[2]


# org_rules is not strict yet, so its functions reach here with unknown types and private names. These four wrappers
# are the only crossing into it, each typed at its boundary.
def _active(c: sqlite3.Connection) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], R._active(c))  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType]


def _ctx(c: sqlite3.Connection) -> dict[str, Any]:
    return cast(dict[str, Any], R._ctx(c))  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType]


def _hits(scope: dict[str, Any], key: dict[str, Any]) -> bool:
    return bool(R._hits(scope, key))  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType, reportUnknownArgumentType]


def _who_key(c: sqlite3.Connection, actor: str, ctx: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], R.who_key(c, actor, ctx))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


def _structured(c: sqlite3.Connection) -> list[dict[str, Any]]:
    return [row for row in _active(c) if row["toward_scope"]]


def _binds(c: sqlite3.Connection, who: str, row: dict[str, Any]) -> bool:
    """Whether the rule's Who names the actor (the same reading `matching` gives a write's author)."""
    ctx = _ctx(c)
    return _hits(row["who"], _who_key(c, who, ctx))


def _matches(
    c: sqlite3.Connection, scope: Sequence[tuple[str, ...]], reached: dict[str, Any], me: str | None
) -> set[str]:
    """The entities the scope names that the walk reached."""
    found: set[str] = set()
    facts: dict[str, dict[str, Any]] | None = None
    for term in scope:
        head = term[0]
        if head == "entity":
            found.add(term[1])
        elif head in ("type", "kind"):
            facts = S.entity_facts(c, list(reached)) if facts is None else facts
            found |= {eid for eid, fact in facts.items() if fact[head] == term[1]}
        elif head == "self":
            if me is None:
                raise ScopeError("the company (self) is not set up")
            found |= set(S.linked_to(c, me, term[1]))
        else:
            found |= set(S.linked_to(c, term[2], term[1]))
    return found & set(reached)


def _nearest(ids: set[str], reached: dict[str, Any]) -> str | None:
    """The reached entity with the shortest path (ties by id), or None."""
    if not ids:
        return None
    return min(ids, key=lambda eid: (len(reached[eid]), eid))


def _judge(
    c: sqlite3.Connection, who: str, row: dict[str, Any], reached: dict[str, Any], me: str | None
) -> tuple[str, str | None, str]:
    """(effect, the reached entity the rule applies through or None, the reason when the effect is block)."""
    try:
        hit = _nearest(_matches(c, parse_scope(row["toward_scope"]), reached, me), reached)
        if hit is None:
            return "skip", None, ""
        if row["applies_when"]:
            if me is None:
                raise ScopeError("the company (self) is not set up")
            key, target = parse_condition(row["applies_when"])
            if target not in S.linked_from(c, me, key):
                return "skip", None, ""
    except ScopeError as exc:
        return "block", None, f"cannot be checked: {exc}"
    if not _binds(c, who, row):
        return "ok", hit, ""
    return EFFECT_OF[str(row["if_broken"])], hit, ""


def _path(hit: str | None, steps: Sequence[dict[str, Any]], names: dict[str, str], visible: set[str]) -> list[str]:
    """The path a rule applies through, as one line: `Jane Ortiz → works_at → Acme Lights`."""
    if hit is None:
        return []

    def label(entity_id: str) -> str:
        return names.get(entity_id, "") if entity_id in visible else HIDDEN

    start = steps[0]["from"] if steps else hit
    tokens = [label(start)]
    for step in steps:
        tokens += [str(step["key"]), label(str(step["to"]))]
    return [" → ".join(tokens)]


def rules_for(
    c: sqlite3.Connection, who: str, ref: str | Sequence[str] | None, action: str, *, contacts: Sequence[str] = ()
) -> dict[str, Any]:
    """The structured rules an action meets, for the reader `who`: its targets (`ref`, entity ids) and the entities its
    addresses (`contacts`) resolve to, walked with self. Returns {"action", "depth", "truncated", "rules"}, each rule
    with its effect, the path it applies through and a reason when it is a block the check could not make. Every value
    is JSON-serializable."""
    targets = [ref] if isinstance(ref, str) else [str(x) for x in (ref or [])]
    me = S.self_id(c)
    starts = list(dict.fromkeys([*targets, *S.contact_entities(c, contacts)]))
    if me is not None and me not in starts:
        starts.append(me)
    reached, truncated = S.walk(c, starts, WALK_DEPTH)
    judged = [(row, *_judge(c, who, row, reached, me)) for row in _structured(c)]
    ids = {str(step[k]) for _, _, hit, _ in judged if hit for step in reached[hit] for k in ("from", "to")}
    ids |= {hit for _, _, hit, _ in judged if hit}
    names = S.entity_names(c, ids)
    visible = S.visible_ids(c, who, ids)
    sources = {str(r["id"]): str(r["name"]) for r in S.rule_sources(c)}
    rules: list[dict[str, Any]] = []
    for row, effect, hit, reason in judged:
        rules.append(
            {
                "id": row["id"],
                "text": row["text"],
                "cite": row["cite"],
                "source": sources.get(str(row["source_id"] or ""), ""),
                "if_broken": row["if_broken"],
                "effect": effect,
                "path": _path(hit, reached[hit] if hit else [], names, visible),
                "reason": reason,
            }
        )
    return {"action": action, "depth": WALK_DEPTH, "truncated": truncated, "rules": rules}


def check(
    c: sqlite3.Connection, who: str, ref: str | Sequence[str] | None, action: str, *, contacts: Sequence[str] = ()
) -> dict[str, Any]:
    """The verdict an outbound action gets: "held" when a block or escalation applies, or the check failed (fail closed,
    with the error's class as `error`); "warn" when only warnings apply; "clear" otherwise. `held` and `warned` list the
    rules, as rules_for gives them."""
    try:
        result = rules_for(c, who, ref, action, contacts=contacts)
    except Exception as exc:  # noqa: BLE001  (fail closed: an error holds the action, it never lets it through)
        name = type(exc).__name__
        reason = (
            f"The Directory rules could not be checked ({name}), so this {action} is held until a person checks it."
        )
        return {
            "outcome": "held",
            "error": name,
            "truncated": False,
            "held": [{"id": "", "text": reason, "effect": "block", "path": [], "reason": name}],
            "warned": [],
        }
    held = [rule for rule in result["rules"] if rule["effect"] in HOLDING]
    warned = [rule for rule in result["rules"] if rule["effect"] == "warn"]
    outcome = "held" if held else "warn" if warned else "clear"
    return {
        "outcome": outcome,
        "error": None,
        "truncated": result["truncated"],
        "held": held,
        "warned": warned,
    }
