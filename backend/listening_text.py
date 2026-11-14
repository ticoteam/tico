"""What a listening's send says, as text: the numbered lines of its items (backend/listening.py `payload_of`), and the
same send cut down to some of its items (`narrow`), which the executor hands a step that already delivered the rest in
an earlier run (backend/automation_engine.py). Pure: no database, no clock."""
# pyright: strict

import re
from typing import Any, Iterable

Entry = dict[str, Any]


def _short_url(url: object) -> str:
    return re.sub(r"^https?://(www\.)?", "", str(url or "")).rstrip("/")


def lines(entries: list[Entry]) -> str:
    width = max((len(e["tag"]) for e in entries), default=0)
    out: list[str] = []
    for n, entry in enumerate(entries, 1):
        head = f"{n}. {entry['tag'].ljust(width)}  " if width else f"{n}. "
        copies = entry["copies"]
        about = [entry["site"], f"{copies} {'copy' if copies == 1 else 'copies'}" if copies else ""]
        about.append(_short_url(entry["url"]))
        out.append(head + entry["title"])
        out.append("   " + " · ".join(part for part in about if part))
    return "\n".join(out)


def _kept(entries: object, keep: set[object] | None) -> list[Entry]:
    """The entries (a list from the payload) that are objects and whose id is in `keep` (any, with None), in order."""
    if not isinstance(entries, list):
        return []
    out: list[Entry] = []
    for entry in entries:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(entry, dict) and (keep is None or entry.get("id") in keep):  # pyright: ignore[reportUnknownMemberType]
            out.append(entry)  # pyright: ignore[reportUnknownArgumentType]
    return out


def narrow(sent: Entry, ids: Iterable[object]) -> Entry:
    """The send `sent` (a payload's `listening`) with only the items whose id is in `ids`, in their order: its items,
    groups, count and text say only those. The head of the text (name and time) stays."""
    keep = set(ids)
    items = _kept(sent.get("items"), keep)
    groups: list[Entry] = []
    for group in _kept(sent.get("groups"), None):
        members = _kept(group.get("items"), keep)
        if members:
            groups.append({**group, "count": len(members), "text": lines(members), "items": members})
    first = str(sent.get("text") or "").split("\n\n", 1)[0]
    head = first.rsplit(" · ", 1)[0] if " · " in first else first
    waiting = int(sent.get("waiting") or 0)
    text = f"{head} · {len(items)} of {len(items) + waiting}\n\n{lines(items)}" if head else lines(items)
    return {**sent, "count": len(items), "items": items, "groups": groups, "text": text}


def narrow_group(group: Entry, ids: Iterable[object]) -> Entry:
    """A Paths by tag thread's group with only the items whose id is in `ids`."""
    members = _kept(group.get("items"), set(ids))
    return {**group, "count": len(members), "text": lines(members), "items": members}
