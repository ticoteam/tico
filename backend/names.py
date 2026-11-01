"""Display names in the stable v2 answers.

The store keeps raw actor ids (`human:ana`, `bot:ops`). A frontend should not have to look
every one up, so an answer on a stable route also carries the names, added beside the ids and never
instead of them:

- an object with an `owner`, `requester`, `from_actor`, `to_actor` or `actor` id gets `owner_name` and so on;
- a read (GET) answer that is an object gets `actors`: {id: name} for every id it names, one lookup for the whole page;
- for a person, a notice the hub wrote for bots to read ("New task from bot:x: ...") is shown with names
  (the original is kept in `body_raw`). Bots and other callers get the text as written.

Nothing here reads more than the `humans` and `bots` tables, and a name is only a label.
"""

from . import fast_json
import re

ACTOR_KEYS = ("owner", "requester", "from_actor", "to_actor", "actor", "author", "operator", "reports_to",
              "status_by", "proposed_by", "decided_by", "source_actor")
ID = re.compile(r"\b(human|bot):([A-Za-z0-9._-]*[A-Za-z0-9_])")
# Tico support in a session a person opened (backend/support_access.py): no roster row, always this name.
SUPPORT_ACTOR, SUPPORT_NAME = "human:tico-support", "Tico support"


def _title(slug):
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), slug.replace("-", " ").replace("_", " "))


def _is_id(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def _collect(node, found, notices):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ACTOR_KEYS and _is_id(value):
                found.add(value)
            elif key in ("participants", "owners", "participant_ids") and isinstance(value, list):
                found.update(v for v in value if _is_id(v))
            else:
                _collect(value, found, notices)
        if node.get("kind") == "notice" and isinstance(node.get("body"), str) and "sender_id" in node:
            notices.append(node)
            found.update(m.group(0) for m in ID.finditer(node["body"]))
    elif isinstance(node, list):
        for value in node:
            _collect(value, found, notices)


def lookup(conn, ids):
    """{actor id: display name} for the human and bot ids among `ids`; an unknown id is left out."""
    humans = sorted({i.split(":", 1)[1] for i in ids if i.startswith("human:")})
    bots = sorted({i.split(":", 1)[1] for i in ids if i.startswith("bot:")})
    out = {SUPPORT_ACTOR: SUPPORT_NAME} if SUPPORT_ACTOR in ids else {}
    for start in range(0, len(humans), 500):
        chunk = humans[start:start + 500]
        for row in conn.execute("SELECT id,name FROM humans WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk):
            out["human:" + row[0]] = (row[1] or "").strip() or _title(row[0])
    for start in range(0, len(bots), 500):
        chunk = bots[start:start + 500]
        for row in conn.execute("SELECT id,slug,display_name FROM bots WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk):
            out["bot:" + row[0]] = (row[2] or "").strip() or _title(row[1])
    return out


def _apply(node, names):
    if isinstance(node, dict):
        for key in list(node):
            value = node[key]
            if key in ACTOR_KEYS and _is_id(value):
                if value in names:
                    node.setdefault(key + "_name", names[value])
            elif key in ("participants", "owners", "participant_ids") and isinstance(value, list):
                if all(isinstance(v, str) for v in value) and any(v in names for v in value):
                    node.setdefault({"participants": "participant_names", "participant_ids": "participant_names",
                                     "owners": "owner_names"}[key], [names.get(v, v) for v in value])
            else:
                _apply(value, names)
    elif isinstance(node, list):
        for value in node:
            _apply(value, names)


def annotate(conn, payload, render_notices=False, actors=True):
    """Add names to a JSON-ready answer, in place, and return it."""
    if not isinstance(payload, (dict, list)):
        return payload
    found, notices = set(), []
    _collect(payload, found, notices)
    if not found:
        return payload
    names = lookup(conn, found)
    _apply(payload, names)
    if render_notices:
        for message in notices:
            text = ID.sub(lambda m: names.get(m.group(0), m.group(0)), message["body"])
            if text != message["body"]:
                message.setdefault("body_raw", message["body"])
                message["body"] = text
    if actors and isinstance(payload, dict) and names and "actors" not in payload:
        payload["actors"] = dict(sorted(names.items()))
    return payload


def annotate_json(conn, raw, render_notices=False, actors=True):
    """The same for a JSON body; returns the new bytes, or None when the body is not an object or list."""
    try:
        payload = fast_json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, (dict, list)):
        return None
    annotate(conn, payload, render_notices, actors)
    return fast_json.dumps(payload)
