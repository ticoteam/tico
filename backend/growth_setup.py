# pyright: strict
"""Growth's first run: the four steps from an empty Growth to a team's first numbers, which of them are done, the one
to do next and whether the caller may do it, and the dates the Growth activation rate is read from.

The steps, in order, one rule for the web, the CLI and the JSON:
1. `settings`: revenue per customer per month and margin are set (the unit has a default).
2. `channel`: at least one active channel (Unknown is not a channel).
3. `source`: an active channel names a live source.
4. `month`: an active channel has a month with spend and new customers.
Steps 3 and 4 follow the first channel made; any channel that meets them completes them. Once all four have been done,
setup stays done (`growth_setup.done_at`), even if a channel is retired later.

Every Growth read carries `setup` (`block`): while setup is not done, each step and whether it is done and the next
step with its CLI command, its MCP tool, whether this caller may do it and, if not, who may (the owner and admins by
name). Once done it is only `{"done": true}`. Every Growth read also carries `you_may` (`you_may`): per action, what
this caller may do.

Counting (`growth_setup`, one row): the first Growth read by anyone is the first open; each step's first done date; and
the first time anyone reads the numbers (the Growth page, economics, numbers or a channel) after all four are done is
"numbers seen". Each date is written once (`coalesce`), so reading again never moves it.
"""

from datetime import datetime, timezone
from typing import Any

from . import access as _access_mod
from . import auth as _auth_mod
from . import brand as _brand_mod
from . import hubdb as _hubdb_mod
from . import store as _store_mod

H: Any = _hubdb_mod
Access: Any = _access_mod
Auth: Any = _auth_mod
Brand: Any = _brand_mod
Store: Any = _store_mod

STEPS = ("settings", "channel", "source", "month")
# What doing a step is, as the actions every read names in `you_may`.
ACTION = {"settings": "settings", "channel": "channels", "source": "sources", "month": "months"}
DATES = ("first_opened_at", "settings_at", "channel_at", "source_at", "month_at", "done_at", "numbers_seen_at")


def you_may(auth: Any, who: Any) -> dict[str, bool]:
    """Per action, what this caller may do: the owner and admins (as people) change settings, channels and sources; they
    and any bot record months (backend/growth.py)."""
    person = bool(Brand.admin(auth, who))
    return {"settings": person, "channels": person, "sources": person, "months": person or who.role == "bot"}


def who_may(c: Any) -> list[dict[str, str]]:
    """The owner, then the admins, still on the roster: `{ref, name}` each."""
    emails = [str(e).lower() for e in Access.owner_and_admin_emails(c)]
    people: dict[str, dict[str, Any]] = {}
    for row in H._rows(c.execute("SELECT id, name, email FROM humans WHERE email IS NOT NULL")):
        email = str(row["email"] or "").lower()
        if email in emails and email not in people and not Auth.standing(c, row["id"]):
            people[email] = row
    return [
        {"ref": "human:" + people[e]["id"], "name": people[e]["name"] or people[e]["id"]} for e in emails if e in people
    ]


def recorded(c: Any) -> dict[str, Any]:
    return H._one(c, "SELECT * FROM growth_setup WHERE id=1") or {}


def state(c: Any) -> dict[str, Any]:
    """Which steps are done, and the channel steps 3 and 4 are about (`channel`: slug and name, or None)."""
    conf: dict[str, Any] = H._one(c, "SELECT revenue_month, margin_pct FROM growth_settings WHERE id=1") or {}
    live = H._rows(
        c.execute(
            "SELECT ch.slug, ch.name, (s.id IS NOT NULL) AS sourced, EXISTS (SELECT 1 FROM growth_cohorts g "
            "WHERE g.channel_id=ch.id AND g.spend IS NOT NULL AND g.new_customers IS NOT NULL) AS counted "
            "FROM growth_channels ch LEFT JOIN gtm_sources s ON s.id=ch.source_id AND s.retired_at IS NULL "
            "WHERE ch.retired_at IS NULL ORDER BY ch.created_at, ch.rowid"
        )
    )
    done = {
        "settings": conf.get("revenue_month") is not None and conf.get("margin_pct") is not None,
        "channel": bool(live),
        "source": any(ch["sourced"] for ch in live),
        "month": any(ch["counted"] for ch in live),
    }
    if recorded(c).get("done_at"):
        done = dict.fromkeys(STEPS, True)
    # Step 3 is about the first channel made; step 4 about the first that names a source, else the first.
    first = live[0] if live else None
    if done["source"]:
        first = next((ch for ch in live if ch["sourced"]), first)
    return {
        "done": all(done.values()),
        "steps": [{"step": s, "done": done[s]} for s in STEPS],
        "channel": {"slug": first["slug"], "name": first["name"]} if first else None,
    }


def last_full_month(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    index = now.year * 12 + now.month - 2
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _next(c: Any, st: dict[str, Any]) -> dict[str, Any] | None:
    """The first step not done: what it is, in a line, with its CLI command and MCP tool."""
    step = next((s["step"] for s in st["steps"] if not s["done"]), None)
    channel = st["channel"] or {"slug": "<slug>", "name": "the channel"}
    slug, name = channel["slug"], channel["name"]
    if step == "settings":
        return {
            "step": step,
            "says": "Set what a customer is worth: revenue per customer per month and your margin.",
            "command": "tico growth settings --set revenue_month=<per customer per month> --set margin_pct=<margin %>",
            "tool": "tico_growth_settings",
        }
    if step == "channel":
        return {
            "step": step,
            "says": "Add your first channel: where customers come from (search ads, SEO, partners).",
            "command": 'tico growth channel add <slug> --name "<name>"',
            "tool": "tico_growth_channel_add",
        }
    if step == "source":
        ids = [r[0] for r in c.execute("SELECT id FROM gtm_sources WHERE retired_at IS NULL ORDER BY created_at")]
        update = f"tico growth channel update {slug} --source {ids[0] if len(ids) == 1 else '<source id>'}"
        if ids:
            return {
                "step": step,
                "says": f"Say where {name}'s numbers come from.",
                "command": update,
                "tool": "tico_growth_channel_update",
            }
        return {
            "step": step,
            "says": f"Add the source {name}'s numbers come from, then name it on the channel.",
            "command": 'tico growth source add --truth-for "ad spend" --system "<system>" '
            "--reach-kind <mcp|credential|url|person>",
            "then": update,
            "tool": "tico_growth_source_add",
        }
    if step == "month":
        month = last_full_month()
        return {
            "step": step,
            "says": f"Add {name}'s first month: spend and new customers.",
            "command": f"tico growth cohort set --channel {slug} --month {month} --spend <spend> --new <new customers>",
            "tool": "tico_growth_cohort_set",
        }
    return None


def block(c: Any, auth: Any, who: Any, st: dict[str, Any] | None = None) -> dict[str, Any]:
    """The setup block every Growth read carries: only `{"done": true}` once setup is done."""
    st = st or state(c)
    if st["done"]:
        return {"done": True}
    nxt = _next(c, st)
    if nxt is None:  # G: a setup not done has a step not done
        return {"done": True}
    may = you_may(auth, who)[ACTION[nxt["step"]]]
    nxt = {**nxt, "number": STEPS.index(nxt["step"]) + 1, "you_may": may}
    if not may:
        nxt["who_may"] = who_may(c)
        nxt["bots_may"] = nxt["step"] == "month"
    return {"done": False, "steps": st["steps"], "next": nxt}


def _due(row: dict[str, Any], st: dict[str, Any], seen: bool) -> bool:
    """Whether this read has a date to write: the first open, a step's first done, setup done, or numbers seen."""
    if not row.get("first_opened_at") or (st["done"] and not row.get("done_at")):
        return True
    if any(s["done"] and not row.get(s["step"] + "_at") for s in st["steps"]):
        return True
    return seen and st["done"] and not row.get("numbers_seen_at")


def record(c: Any, actor: str, *, seen: bool = False, at: str | None = None) -> None:
    """Write the dates this moment adds, each only if not written before (on a write connection)."""
    st, at = state(c), at or H.now()
    if not _due(recorded(c), st, seen):
        return
    c.execute("INSERT OR IGNORE INTO growth_setup (id) VALUES (1)")
    fields = {"first_opened_at": at, "first_opened_by": actor}
    fields.update({s["step"] + "_at": at for s in st["steps"] if s["done"]})
    if st["done"]:
        fields["done_at"] = at
        if seen:
            fields.update(numbers_seen_at=at, numbers_seen_by=actor)
    # SQLite reads the old row for every expression, so each `_by` is set only with its `_at`.
    sets = ", ".join(
        f"{k}=CASE WHEN {k[:-3]}_at IS NULL THEN ? ELSE {k} END" if k.endswith("_by") else f"{k}=coalesce({k}, ?)"
        for k in fields
    )
    c.execute(f"UPDATE growth_setup SET {sets} WHERE id=1", list(fields.values()))


def attach(store: Any, auth: Any, who: Any, out: dict[str, Any], *, seen: bool = False) -> dict[str, Any]:
    """A Growth read's answer with `you_may` and `setup`, after writing what this read adds."""
    with store.read() as c:
        st = state(c)
        due = _due(recorded(c), st, seen)
        setup = block(c, auth, who, st)
    if due:

        def work(w: Any) -> None:
            record(w, who.actor, seen=seen)

        try:
            store.write(who, work)
        except Store.Problem:
            pass  # a caller who may write nothing (support reading the app) still reads; the next read records it
    return {**out, "you_may": you_may(auth, who), "setup": setup}


def show(c: Any, auth: Any, who: Any) -> dict[str, Any]:
    """GET /api/v2/growth/setup: every step with its first done date, the next step (None once done), what the caller
    may do, and the first-open and numbers-seen dates."""
    st, row = state(c), recorded(c)
    out: dict[str, Any] = block(c, auth, who, st) if not st["done"] else {"done": True, "next": None}
    out["steps"] = [{**s, "at": row.get(s["step"] + "_at")} for s in st["steps"]]
    out["channel"] = st["channel"]
    out["you_may"] = you_may(auth, who)
    for k in ("first_opened_at", "first_opened_by", "done_at", "numbers_seen_at", "numbers_seen_by"):
        out[k] = row.get(k)
    return out
