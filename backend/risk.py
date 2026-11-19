# pyright: strict
"""Risks and matters (Risk plan, slice 1).

- A risk is a tag of kind `risk` (`tags.kind`), keyed `risk:{slug}`. Any task can carry it, in whatever pipeline it
  lives; tagging is the task's labels (POST /api/v2/tasks/{tid} with labels, `tico task label {task} --add risk:{slug}`).
  Making one adds the Risk page's owner bot to the tag's bot_writers, so it may tag any task it can read. A tag's kind
  never changes after it is made.
- A matter is a task tagged with a risk. Its facts are one `matters` row: kind, party, amount claimed (whole minor units,
  like Finance), currency, claim, side, forum, place, product. Whoever may update the task sets them, and so does a bot
  the risk lets tag tasks (bot_writers) on a task it reads.
- `risk_links` ties a risk to org rules (role `rule`) and Directory documents (role `source`, or `covers` for a policy).
- Who sees what: every teammate lists every risk (backend/task_privacy.py `_listed_alone`); the counts, sums, dates and
  matters come only from the tasks the viewer reads (auth.task_sql), so they differ by viewer. Every rule is read by every
  teammate (backend/org_rules.py); a Directory record (party, place, product, document) shows only to a reader the
  Directory lets see it, read as Contacts reads it: as the caller (who.actor), and as the record it was merged into.
- Every write: one permission check and an audit_log row (`risk:<tag id>`, `matter:<task id>`, `risk_link:<id>`).
- Obligations (slice 3) are rule due dates (backend/rule_due.py) with a title, a basis and, for a matter's own date, its
  task. A risk's obligations are the dates in force on the rules linked to it and the dates of the tasks that carry it;
  a task's date shows only to that task's readers. An unconfirmed date, or a binding one (law, order, agency, contract)
  due within 14 days, makes the risk live; the next date is the soonest one still due.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, cast

from fastapi import Request
from pydantic import Field

from . import audit_log as _audit_log
from . import org_rules as _org_rules
from . import page_owners
from . import rule_due as _rule_due
from . import task_privacy as _privacy
from . import teammate_access as _teammate_access
from .api_contract import api_v2
from .entities import store as ES
from .models import ID, Contract
from .store import H as _H, Problem

# The untyped modules this one calls, as Any (their rows are dicts and sqlite3 rows).
H = cast(Any, _H)
D = cast(Any, _rule_due)
OR = cast(Any, _org_rules)
audit_log = cast(Any, _audit_log)
privacy = cast(Any, _privacy)
teammate_access = cast(Any, _teammate_access)
Row = dict[str, Any]

KIND = "risk"
PREFIX = "risk:"
SLUG_MAX = 40 - len(PREFIX)  # a tag key is at most 40 characters (hubdb._labels)
MATTER_KINDS = ("dispute", "request", "notice", "incident")
SIDES = ("theirs", "ours")
# What a link may point at, and the roles each takes; the first role is the default.
LINK_ROLES = {"org_rule": ("rule",), "entity": ("source", "covers")}
# The Directory types each matter fact names.
ENTITY_TYPES = {"party": ("person", "organization"), "place": ("place",), "product": ("product",)}
COLUMNS = {"party": "party_entity_id", "place": "place_id", "product": "product_id"}
NEW_DAYS = 7  # a matter that arrived this recently makes its risk live
LEVELS = ("live", "open", "quiet")
LIMIT_DEFAULT = 50
LIMIT_MAX = 200
LABEL_MAX = 200
MARKDOWN_MAX = 20_000
CLAIM_MAX = 2_000
FORUM_MAX = 200
GROUPS = ("unconfirmed", "next", "later", "past")  # the Obligations tab, in its order
DAYS_DEFAULT = 90
DAYS_MAX = 366


class RiskCreate(Contract):
    label: str = Field(min_length=1, max_length=LABEL_MAX)
    key: str | None = Field(default=None, max_length=40, description="The slug; made from the label when left out")
    markdown: str | None = Field(default=None, max_length=MARKDOWN_MAX)
    owner: str | None = Field(default=None, description="Who owns the risk; default the Risk page's owner bot")


class RiskUpdate(Contract):
    version: int
    label: str | None = Field(default=None, min_length=1, max_length=LABEL_MAX)
    markdown: str | None = Field(default=None, max_length=MARKDOWN_MAX)
    owner: str | None = None


class RiskLink(Contract):
    linkable_type: Literal["org_rule", "entity"]
    linkable_id: ID
    role: Literal["rule", "source", "covers"] | None = Field(
        default=None, description="rule for an org_rule; source (default) or covers for a document"
    )


class MatterWrite(Contract):
    """Set the facts given; a field sent as null clears it. `clear` removes the matter's facts. A bot sends the version
    it read when the task has facts already."""

    version: int | None = None
    clear: bool = False
    kind: Literal["dispute", "request", "notice", "incident"] | None = None
    party_entity_id: ID | None = None
    amount: int | None = Field(default=None, ge=0, le=10**15, description="Amount claimed in minor units (cents)")
    currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    claim: str | None = Field(default=None, max_length=CLAIM_MAX)
    side: Literal["theirs", "ours"] | None = None
    forum: str | None = Field(default=None, max_length=FORUM_MAX)
    place_id: ID | None = None
    product_id: ID | None = None


def _via(who: Any) -> str:
    return who.via or H.VIA.get() or "api"


def _mover(c: Any, who: Any) -> bool:
    return who.role == "owner" or who.role == "human" and H.can_move(c, who.actor)


def slug(text: object) -> str:
    """`Customer theft claims` -> `customer-theft-claims`: lower-case letters, digits and single hyphens."""
    text = str(text or "").strip().lower()
    if text.startswith(PREFIX):
        text = text[len(PREFIX) :]
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:SLUG_MAX].strip("-")


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def _visible_tasks(auth: Any, c: Any, who: Any, columns: str = "id") -> str:
    """The tasks `who` reads, as a derived table: the audience clause names the tasks table's columns unqualified, so it
    stays inside its own SELECT before anything is joined to it."""
    return f"(SELECT {columns} FROM tasks WHERE ({auth.task_sql(c, who)}))"


def risk_rows(c: Any, who: Any, ids: list[str] | None = None) -> list[Row]:
    """The risks `who` may list (every teammate lists every risk), by label."""
    sql, args = "SELECT * FROM tags WHERE kind=?", [KIND]
    if ids is not None:
        if not ids:
            return []
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        args += list(ids)
    rows = [H._tag_view(r) for r in c.execute(sql + " ORDER BY label, key", args)]
    return privacy.tags_readable(c, who, rows)


def require_risk(c: Any, who: Any, ident: str) -> Row:
    row = H.tag(c, ident)
    if not row or row.get("kind") != KIND:
        # A plain tag `claims` does not hide the risk `risk:claims`.
        row = H.tag(c, PREFIX + slug(ident))
    if not row or row.get("kind") != KIND or not privacy.tag_readable(c, who, row):
        raise Problem("not_found", "Risk not found", 404)
    return row


# The open and closed matters of each risk among the tasks a viewer reads, and whether any open one arrived in the last
# NEW_DAYS. Driven from the risk tags' task_tags rows; each task is looked up by its key under the viewer's rule. The
# next date comes from the risk's obligations (`dates`), not from the tasks.
STANDING_SQL = (
    "SELECT tt.tag_id AS risk_id, "
    "coalesce(sum(t.status='open'),0) AS open, coalesce(sum(t.status<>'open'),0) AS closed, "
    "coalesce(max(t.status='open' AND coalesce(m.created_at, t.created_at) >= ?),0) AS new "
    "FROM tags g JOIN task_tags tt ON tt.tag_id=g.id JOIN {visible} t ON t.id=tt.task_id "
    "LEFT JOIN matters m ON m.task_id=t.id WHERE g.kind='risk'{only} GROUP BY tt.tag_id"
)
# What the other side claims on open matters, per risk and currency.
CLAIMED_SQL = (
    "SELECT tt.tag_id AS risk_id, m.currency AS currency, sum(m.amount) AS amount "
    "FROM tags g JOIN task_tags tt ON tt.tag_id=g.id JOIN {visible} t ON t.id=tt.task_id "
    "JOIN matters m ON m.task_id=t.id WHERE g.kind='risk'{only} AND t.status='open' AND m.side='theirs' "
    "AND m.amount IS NOT NULL GROUP BY tt.tag_id, m.currency ORDER BY m.currency"
)


def standing(auth: Any, c: Any, who: Any, ids: list[str] | None = None) -> dict[str, Row]:
    """{risk id: {open, closed, level, claimed, next_date, unconfirmed}} over the tasks and dates `who` reads."""
    visible = _visible_tasks(auth, c, who, "id, status, created_at")
    only, args = "", cast(list[str], [])
    if ids is not None:
        only = f" AND g.id IN ({','.join('?' * len(ids))})" if ids else " AND 0"
        args = list(ids)
    out: dict[str, Row] = {}
    for r in c.execute(STANDING_SQL.format(visible=visible, only=only), [_since(NEW_DAYS), *args]):
        level = "live" if r["new"] else "open" if r["open"] else "quiet"
        out[r["risk_id"]] = {
            "open": r["open"],
            "closed": r["closed"],
            "level": level,
            "claimed": [],
            "next_date": None,
            "unconfirmed": 0,
        }
    for r in c.execute(CLAIMED_SQL.format(visible=visible, only=only), args):
        if r["risk_id"] in out:
            out[r["risk_id"]]["claimed"].append({"currency": r["currency"] or "", "amount": r["amount"]})
    for risk_id, found in dates(auth, c, who, ids).items():
        entry = out.setdefault(risk_id, {**EMPTY, "claimed": []})
        entry["next_date"], entry["unconfirmed"] = found["next_date"], found["unconfirmed"]
        if found["unconfirmed"] or found["soon"]:
            entry["level"] = "live"
    return out


EMPTY: Row = {"open": 0, "closed": 0, "level": "quiet", "claimed": [], "next_date": None, "unconfirmed": 0}


# ----------------------------------------------------------------------------- obligations

# Every date in force on an active rule linked to a risk, and every date of a task that carries a risk, with the risk.
# The second part starts from the dates that have a task (rule_due_dates_task, a partial index) and is held to that
# order with CROSS JOIN: left to itself, SQLite started from the risk tags and walked every active rule.
DATES_SQL = (
    "SELECT rl.risk_id AS on_risk, d.* FROM risk_links rl JOIN rule_due_dates d ON d.rule_id=rl.linkable_id "
    "JOIN org_rules r ON r.id=d.rule_id WHERE rl.linkable_type='org_rule' AND rl.role='rule' AND r.status='active' "
    "AND d.retired_at IS NULL AND d.ended_on IS NULL{linked} "
    "UNION "
    "SELECT tt.tag_id AS on_risk, d.* FROM rule_due_dates d CROSS JOIN task_tags tt ON tt.task_id=d.task_id "
    "CROSS JOIN tags g ON g.id=tt.tag_id JOIN org_rules r ON r.id=d.rule_id WHERE d.task_id IS NOT NULL AND g.kind='risk' "
    "AND r.status='active' AND d.retired_at IS NULL AND d.ended_on IS NULL{tagged}"
)


def date_rows(auth: Any, c: Any, who: Any, ids: list[str] | None = None) -> tuple[list[Row], dict[str, list[str]]]:
    """The dates of these risks (every risk with None) that `who` may be shown, and {date id: [risk ids]}."""
    linked = tagged = ""
    args: list[str] = []
    if ids is not None:
        if not ids:
            return [], {}
        marks = ",".join("?" * len(ids))
        linked, tagged = f" AND rl.risk_id IN ({marks})", f" AND tt.tag_id IN ({marks})"
        args = [*ids, *ids]
    rows: dict[str, Row] = {}
    on: dict[str, list[str]] = {}
    for r in c.execute(DATES_SQL.format(linked=linked, tagged=tagged), args):
        row = dict(r)
        risk_id = row.pop("on_risk")
        rows.setdefault(row["id"], row)
        if risk_id not in on.setdefault(row["id"], []):
            on[row["id"]].append(risk_id)
    return D.readable(c, auth, who, list(rows.values())), on


def dates(auth: Any, c: Any, who: Any, ids: list[str] | None = None) -> dict[str, Row]:
    """{risk id: {unconfirmed, soon, next_date}}: how many of its occurrences are unconfirmed, whether a binding one is
    due within 14 days, and the soonest day still due."""
    rows, on = date_rows(auth, c, who, ids)
    out: dict[str, Row] = {}
    for item in D.obligations(c, rows, DAYS_MAX, only_next=True):
        for risk_id in on[item["row"]["id"]]:
            entry = out.setdefault(risk_id, {"unconfirmed": 0, "soon": False, "next_date": None})
            if item["state"] == "unconfirmed":
                entry["unconfirmed"] += 1
            elif item["state"] == "due":
                if item["group"] == "next" and item["row"]["basis"] in D.BINDING:
                    entry["soon"] = True
                if entry["next_date"] is None or item["due_on"] < entry["next_date"]:
                    entry["next_date"] = item["due_on"]
    return out


def _order(item: Row) -> tuple[int, str]:
    """Unconfirmed and past: newest first; next and later: soonest first."""
    group = GROUPS.index(item["group"])
    if item["group"] in ("next", "later"):
        return group, item["due_on"]
    mark: Row = item["mark"] or {}
    recent = max(item["due_on"], str(mark.get("satisfied_at") or "")[:10])
    return group, "".join(chr(0x10FFFF - ord(ch)) for ch in recent)  # descending


def obligations(
    auth: Any,
    c: Any,
    who: Any,
    *,
    risk_id: str | None = None,
    state: str | None = None,
    group: str | None = None,
    days: Any = DAYS_DEFAULT,
    limit: Any = LIMIT_MAX,
    offset: Any = 0,
) -> Row:
    """The Obligations tab: {obligations: [...], next_offset}. Unconfirmed first (newest first), then due within 14 days
    and later (soonest first), then what was settled or passed in the last 30 days (newest first)."""
    limit, offset = _page(limit, offset)
    try:
        days = int(days)
    except (TypeError, ValueError):
        raise Problem("validation", "days is a whole number", 422) from None
    if not 1 <= days <= DAYS_MAX:
        raise Problem("validation", f"days is from 1 to {DAYS_MAX}", 422)
    if state is not None and state not in (*D.STATES, "watched"):
        raise Problem("validation", "state is one of " + ", ".join((*D.STATES, "watched")), 422)
    if group is not None and group not in GROUPS:
        raise Problem("validation", "group is one of " + ", ".join(GROUPS), 422)
    rows, on = date_rows(auth, c, who, [risk_id] if risk_id else None)
    found = [
        item
        for item in D.obligations(c, rows, days)
        if (state is None or (item["state"] or "watched") == state) and (group is None or item["group"] == group)
    ]
    found.sort(key=lambda item: (*_order(item), item["row"]["id"]))
    page = found[offset : offset + limit]
    risks = {r["id"]: r for r in risk_rows(c, who, sorted({i for item in page for i in on[item["row"]["id"]]}))}
    task_ids = sorted({item["row"]["task_id"] for item in page if item["row"].get("task_id")})
    tasks: dict[str, Row] = {}
    if task_ids:
        for r in c.execute(
            f"SELECT id, number, prefix, title, status FROM tasks WHERE id IN ({','.join('?' * len(task_ids))})",
            task_ids,
        ):
            label = f"{r['prefix']}{r['number']}" if r["prefix"] and r["number"] is not None else None
            tasks[r["id"]] = {"id": r["id"], "label": label, "title": r["title"], "status": r["status"]}
    ctx: Row = OR._ctx(c) if page else {}  # pyright: ignore[reportPrivateUsage]
    rules: dict[str, Row] = {}
    memo: dict[Any, Any] = {}
    out: list[Row] = []
    for item in page:
        row = item["row"]
        if row["rule_id"] not in rules:
            rule = OR._get(c, row["rule_id"])  # pyright: ignore[reportPrivateUsage]
            rules[row["rule_id"]] = {
                "view": {
                    "id": rule["id"],
                    "kind": rule["kind"],
                    "text": rule["text"],
                    "source": ctx["sources"].get(rule["source_id"]) if rule["source_id"] else None,
                },
                "manage": OR.may_manage(c, auth, who, rule["who"], ctx),
            }
        rule = rules[row["rule_id"]]
        mark = item["mark"]
        # As backend/org_rules.py satisfy_due: the date's owner, or a person who may edit the rule.
        satisfy = who.actor == row["owner"] or (who.role != "bot" and bool(rule["manage"]))
        fields: Row = D.mark_fields(mark) if mark else {}
        out.append(
            {
                "id": row["id"],
                "due_on": item["due_on"],
                "title": row.get("title") or "",
                "basis": row.get("basis") or "",
                "state": item["state"],
                "group": item["group"],
                "repeats": row["repeats"],
                "first_due_on": row["first_due_on"],
                "timezone": row["timezone"],
                "owner": row["owner"],
                "routine_id": row["routine_id"],
                "rule": rule["view"],
                "task": tasks.get(row.get("task_id") or ""),
                "risks": [{k: risks[i][k] for k in ("id", "key", "label")} for i in on[row["id"]] if i in risks],
                "mark": None
                if not mark
                else {
                    "satisfied_by": mark["satisfied_by"],
                    "via": mark["via"],
                    "satisfied_at": mark["satisfied_at"],
                    "task_id": mark["task_id"],
                    **fields,
                    "proof": D.proof_view(c, auth, who, fields["proof"], memo),
                },
                "can_edit": bool(rule["manage"]) and who.role != "bot",
                "can_satisfy": satisfy,
                "can_waive": satisfy and who.role != "bot",
            }
        )
    more = offset + limit < len(found)
    return {"obligations": out, "next_offset": offset + limit if more else None}


def risk_view(row: Row, counts: Row | None = None, *, brief: bool = False) -> Row:
    value: Row = {
        k: row.get(k) for k in ("id", "key", "label", "owner", "version", "bot_writers", "created_at", "updated_at")
    }
    value["kind"] = KIND
    if not brief:
        value["markdown"] = row.get("markdown") or ""
    shown = counts or EMPTY
    value.update({**shown, "claimed": list(cast(list[Row], shown["claimed"]))})
    return value


def list_risks(auth: Any, c: Any, who: Any, task: str | None = None) -> Row:
    ids = None
    if task:
        tid = auth.resolve_task(c, who, task)
        auth.task(c, who, tid, tags=False)
        ids = [
            r[0]
            for r in c.execute(
                "SELECT tt.tag_id FROM task_tags tt JOIN tags g ON g.id=tt.tag_id WHERE tt.task_id=? AND g.kind='risk'",
                (tid,),
            )
        ]
    rows = risk_rows(c, who, ids)
    counts = standing(auth, c, who, [r["id"] for r in rows])
    risks = [risk_view(r, counts.get(r["id"]), brief=True) for r in rows]
    risks.sort(key=lambda r: (LEVELS.index(r["level"]), str(r["label"] or "").lower(), r["key"]))
    return {"risks": risks}


# ----------------------------------------------------------------------------- matters


def _current(c: Any, ids: Any) -> dict[str, str]:
    """{stored id: the id it answers as now}: a merged record answers as the one it was merged into (ES._follow), whose
    sources say who may see it; the merged row itself has none left, and a record with none shows to everyone."""
    return {ident: ES._follow(c, [ident])[0] for ident in dict.fromkeys(ids)}  # pyright: ignore[reportPrivateUsage]


def _entity(c: Any, actor: str, ident: str, field: str) -> str:
    """The live Directory record a matter fact names, followed through a merge, when `actor` may see it."""
    row = c.execute("SELECT id, type, deleted_at FROM entities WHERE id=?", (_current(c, [ident])[ident],)).fetchone()
    if not row or row["deleted_at"] or not ES.visible_ids(c, actor, [row["id"]]):
        raise Problem("not_found", f"No such {field} in the Directory: {ident}", 404)
    if row["type"] not in ENTITY_TYPES[field]:
        raise Problem(
            "validation", f"The {field} is a {' or '.join(ENTITY_TYPES[field])}, and {ident} is a {row['type']}", 422
        )
    return row["id"]


def matter_view(c: Any, actor: str | None, row: Any) -> Row | None:
    """A matters row as JSON. A Directory record shows only to a reader the Directory lets see it: anyone else gets
    null for it."""
    if not row:
        return None
    row = dict(row)
    now = _current(c, [row[COLUMNS[f]] for f in COLUMNS if row.get(COLUMNS[f])])
    seen: set[str] = ES.visible_ids(c, actor or "", set(now.values())) if now else set()
    names = ES.entity_names(c, seen) if seen else {}
    value: Row = {
        "task_id": row["task_id"],
        "kind": row["kind"],
        "amount": row["amount"],
        "currency": row["currency"],
        "claim": row["claim"],
        "side": row["side"],
        "forum": row["forum"],
        "version": row["version"],
        "created_by_id": row["created_by_id"],
        "created_at": row["created_at"],
        "updated_by_id": row["updated_by_id"],
        "updated_at": row["updated_at"],
    }
    for field, column in COLUMNS.items():
        ident = now.get(row.get(column) or "")
        value[column] = ident if ident in seen else None
        value[field] = {"id": ident, "name": names.get(ident or "", "")} if ident in seen else None
    return value


def matter_of(c: Any, who: Any, task_id: str) -> Row | None:
    """The matter of a task its reader is shown (the full task view), or None."""
    row = c.execute("SELECT * FROM matters WHERE task_id=?", (task_id,)).fetchone()
    return matter_view(c, who.actor if who is not None else None, row) if row else None


def _task_risks(c: Any, task_id: str) -> list[Row]:
    return [
        H._tag_view(r)
        for r in c.execute(
            "SELECT g.* FROM task_tags tt JOIN tags g ON g.id=tt.tag_id WHERE tt.task_id=? AND g.kind='risk' "
            "ORDER BY tt.rowid",
            (task_id,),
        )
    ]


def may_set_matter(c: Any, who: Any, row: Row, risks: list[Row]) -> bool:
    """Whoever may update the task (task_update's identity rule), or a bot a risk on it lets tag tasks (bot_writers)
    on a task it reads."""
    if H.may_change_readers(c, who.actor, row, _mover(c, who)):
        return True
    return (
        H.is_bot(who.actor)
        and H.task_private_readable(c, who.actor, row)
        and any(who.actor in r["bot_writers"] for r in risks)
    )


def set_matter(auth: Any, c: Any, who: Any, tid: str, body: "MatterWrite") -> Row:
    auth.domain(who)
    task_id = auth.resolve_task(c, who, tid)
    row = auth.task(c, who, task_id)
    risks = _task_risks(c, task_id)
    found = c.execute("SELECT * FROM matters WHERE task_id=?", (task_id,)).fetchone()
    # Facts left after the last risk came off can still be cleared, by whoever may change the task.
    if not risks and not (body.clear and found):
        raise Problem("validation", f"Tag the task with a risk first: tico task label {task_id} --add risk:<slug>", 422)
    if not may_set_matter(c, who, row, risks):
        raise Problem(
            "forbidden",
            "A matter's facts are set by whoever may change the task, or by a bot the risk lets tag tasks",
            403,
        )
    before = dict(found) if found else None
    # A version names facts that were read: gone since (cleared), they are not made again from a stale read.
    if body.version is not None and (not found or body.version != found["version"]):
        raise Problem("version_conflict", "The matter changed; read the task and retry", 409)
    if found and body.version is None and H.is_bot(who.actor):
        raise Problem("version_conflict", "Send the matter's version from the task you read", 409)
    if body.clear:
        given = body.model_fields_set - {"clear", "version"}
        if given:
            raise Problem("validation", "Clear takes no facts: send clear alone", 422)
        if found:
            c.execute("DELETE FROM matters WHERE task_id=?", (task_id,))
            audit_log.write(c, who.actor, "matter:" + task_id, before, None, _via(who))
        return {"task_id": task_id, "matter": None}
    given = {k: getattr(body, k) for k in body.model_fields_set - {"clear", "version"}}
    if not given:
        raise Problem("validation", "Give a fact to set, or clear", 422)
    for field, column in COLUMNS.items():
        if given.get(column):
            given[column] = _entity(c, who.actor, given[column], field)
    if given.get("currency"):
        given["currency"] = given["currency"].upper()
    for name, default in (("claim", ""), ("forum", "")):
        if name in given and given[name] is None:
            given[name] = default
    if "side" in given and given["side"] is None:
        given["side"] = "theirs"
    if "kind" in given and given["kind"] is None:
        raise Problem("validation", "A matter keeps a kind: one of " + ", ".join(MATTER_KINDS), 422)
    merged = {**(before or {}), **given}
    if not merged.get("kind"):
        raise Problem("validation", "Say what kind of matter it is: one of " + ", ".join(MATTER_KINDS), 422)
    if merged.get("amount") is not None and not merged.get("currency"):
        raise Problem("validation", "Say the amount's currency (for example USD)", 422)
    ts = H.now()
    if found:
        sets = ", ".join(f"{k}=?" for k in given)
        c.execute(
            f"UPDATE matters SET {sets}, version=version+1, updated_by_id=?, updated_at=? WHERE task_id=?",
            [*given.values(), who.actor, ts, task_id],
        )
    else:
        fields = {
            "task_id": task_id,
            "kind": merged["kind"],
            "party_entity_id": merged.get("party_entity_id"),
            "amount": merged.get("amount"),
            "currency": merged.get("currency"),
            "claim": merged.get("claim") or "",
            "side": merged.get("side") or "theirs",
            "forum": merged.get("forum") or "",
            "place_id": merged.get("place_id"),
            "product_id": merged.get("product_id"),
            "created_by_id": who.actor,
            "created_at": ts,
            "updated_by_id": who.actor,
            "updated_at": ts,
        }
        c.execute(
            f"INSERT INTO matters({','.join(fields)}) VALUES({','.join('?' * len(fields))})", list(fields.values())
        )
    after = c.execute("SELECT * FROM matters WHERE task_id=?", (task_id,)).fetchone()
    audit_log.write(c, who.actor, "matter:" + task_id, before, dict(after), _via(who))
    return {"task_id": task_id, "matter": matter_view(c, who.actor, after)}


# The tasks a viewer reads that carry a risk, newest first, with their facts and stage.
MATTERS_SQL = (
    "SELECT t.id, t.number, t.prefix, t.title, t.status, t.due_at, t.created_at, t.owner_id, t.pipeline_id, "
    "t.stage_id, t.requester_id, t.conversation_id, s.name AS stage, {matter} FROM {visible} t "
    "{join} matters m ON m.task_id=t.id LEFT JOIN stages s ON s.id=t.stage_id "
    "WHERE t.id IN (SELECT tt.task_id FROM task_tags tt JOIN tags g ON g.id=tt.tag_id WHERE g.kind='risk'{risk})"
    "{where} ORDER BY t.status<>'open', coalesce(m.created_at, t.created_at) DESC, t.id LIMIT ? OFFSET ?"
)
TASK_FIELDS = (
    "id",
    "number",
    "prefix",
    "title",
    "status",
    "due_at",
    "created_at",
    "owner_id",
    "pipeline_id",
    "stage_id",
)
# Read to mask a task's owner (teammate_access.Known.task), not shown.
MASK_FIELDS = ("requester_id", "conversation_id")
MATTER_FIELDS = (
    "task_id",
    "kind",
    "party_entity_id",
    "amount",
    "currency",
    "claim",
    "side",
    "forum",
    "place_id",
    "product_id",
    "version",
    "created_by_id",
    "created_at",
    "updated_by_id",
    "updated_at",
)


def _page(limit: int, offset: int) -> tuple[int, int]:
    """The routes parse both as whole numbers (FastAPI refuses anything else); out of range is brought in."""
    return max(1, min(int(limit), LIMIT_MAX)), max(0, int(offset))


def matters(
    auth: Any,
    c: Any,
    who: Any,
    *,
    risk_id: str | None = None,
    kind: str | None = None,
    open_only: bool | None = None,
    limit: Any = LIMIT_DEFAULT,
    offset: Any = 0,
) -> Row:
    """One page of matters `who` reads: {matters: [{task, matter, risks}], next_offset}."""
    limit, offset = _page(limit, offset)
    if kind is not None and kind not in MATTER_KINDS:
        raise Problem("validation", "kind is one of " + ", ".join(MATTER_KINDS), 422)
    where: list[str] = []
    args: list[Any] = []
    if kind:
        where.append("m.kind=?")
        args.append(kind)
    if open_only is not None:
        where.append("t.status='open'" if open_only else "t.status<>'open'")
    sql = MATTERS_SQL.format(
        matter=", ".join(f"m.{k} AS m_{k}" for k in MATTER_FIELDS),
        visible=_visible_tasks(auth, c, who, ", ".join((*TASK_FIELDS, *MASK_FIELDS))),
        join="JOIN" if kind else "LEFT JOIN",
        risk=" AND g.id=?" if risk_id else "",
        where="".join(" AND " + w for w in where),
    )
    rows = c.execute(sql, [*([risk_id] if risk_id else []), *args, limit + 1, offset]).fetchall()
    page = rows[:limit]
    ids = [r["id"] for r in page]
    tags: dict[str, list[Row]] = {}
    if ids:
        for r in c.execute(
            f"SELECT tt.task_id AS on_task, g.* FROM task_tags tt JOIN tags g ON g.id=tt.tag_id "
            f"WHERE g.kind='risk' AND tt.task_id IN ({','.join('?' * len(ids))}) ORDER BY tt.rowid",
            ids,
        ):
            tags.setdefault(r["on_task"], []).append(H._tag_view(r))
    shown = {g["id"] for g in privacy.tags_readable(c, who, [g for found in tags.values() for g in found])}
    known = teammate_access.Known(c, who, auth)  # an owner the reader may not see reads "", as on the task itself
    out: list[Row] = []
    for r in page:
        task = {k: r[k] for k in TASK_FIELDS}
        task["owner_id"] = known.task({k: r[k] for k in (*TASK_FIELDS, *MASK_FIELDS)})["owner_id"]
        task["stage"] = r["stage"]
        task["label"] = f"{r['prefix']}{r['number']}" if r["prefix"] and r["number"] is not None else None
        out.append(
            {
                "task": task,
                "matter": matter_view(c, who.actor, {k: r["m_" + k] for k in MATTER_FIELDS})
                if r["m_task_id"]
                else None,
                "risks": [{k: g[k] for k in ("id", "key", "label")} for g in tags.get(r["id"], []) if g["id"] in shown],
            }
        )
    return {"matters": out, "next_offset": offset + limit if len(rows) > limit else None}


def redact_audit(c: Any, auth: Any, who: Any, found: list[Row]) -> None:
    """The audit rows of matters (backend/audit_log.py `rows`), with the facts shown only to a reader of the task now:
    anyone else, the owner included, reads only which task it was. Changed in place."""
    tasks = {
        str(item.get("object"))[len("matter:") :] for item in found if str(item.get("object")).startswith("matter:")
    }
    if not tasks:
        return
    marks = ",".join("?" * len(tasks))
    readable = {
        r[0]
        for r in c.execute(
            f"SELECT id FROM {_visible_tasks(auth, c, who)} WHERE id IN ({marks})",
            list(tasks),
        )
    }
    for item in found:
        task_id = str(item.get("object"))[len("matter:") :]
        if str(item.get("object")).startswith("matter:") and task_id not in readable:
            for key in ("before", "after"):
                if item.get(key):
                    item[key] = {"task_id": task_id}


# ----------------------------------------------------------------------------- links


def links(c: Any, who: Any, risk_id: str) -> dict[str, list[Row]]:
    """{rules, sources, coverage}: the rules linked to a risk (every teammate reads every rule), and the documents a
    reader may see, as sources and as the policies that cover it."""
    rows = c.execute("SELECT * FROM risk_links WHERE risk_id=? ORDER BY created_at, id", (risk_id,)).fetchall()
    rule_ids = [r["linkable_id"] for r in rows if r["linkable_type"] == "org_rule"]
    rules: dict[str, Row] = {}
    if rule_ids:
        for r in c.execute(
            f"SELECT id, kind, text, status, if_broken FROM org_rules WHERE id IN ({','.join('?' * len(rule_ids))})",
            rule_ids,
        ):
            rules[r["id"]] = dict(r)
    now = _current(c, [r["linkable_id"] for r in rows if r["linkable_type"] == "entity"])
    seen: set[str] = ES.visible_ids(c, who.actor, set(now.values())) if now else set()
    facts = ES.entity_facts(c, seen) if seen else {}
    names = ES.entity_names(c, seen) if seen else {}
    out: dict[str, list[Row]] = {"rules": [], "sources": [], "coverage": []}
    for r in rows:
        if r["linkable_type"] == "org_rule" and r["linkable_id"] in rules:
            out["rules"].append({"link_id": r["id"], **rules[r["linkable_id"]]})
        elif r["linkable_type"] == "entity" and now[r["linkable_id"]] in seen:
            ident = now[r["linkable_id"]]
            entry = {
                "link_id": r["id"],
                "id": ident,
                "name": names.get(ident, ""),
                "type": (facts.get(ident) or {}).get("type"),
            }
            out["coverage" if r["role"] == "covers" else "sources"].append(entry)
    return out


def _link_target(c: Any, who: Any, body: RiskLink) -> tuple[str, str]:
    roles = LINK_ROLES[body.linkable_type]
    role = body.role or roles[0]
    if role not in roles:
        raise Problem("validation", f"A link to an {body.linkable_type} takes the role {' or '.join(roles)}", 422)
    if body.linkable_type == "org_rule":
        if not c.execute("SELECT 1 FROM org_rules WHERE id=?", (body.linkable_id,)).fetchone():
            raise Problem("not_found", f"There is no rule {body.linkable_id}", 404)
        return body.linkable_id, role
    current = _current(c, [body.linkable_id])[body.linkable_id]
    row = c.execute("SELECT id, type, deleted_at FROM entities WHERE id=?", (current,)).fetchone()
    if not row or row["deleted_at"] or not ES.visible_ids(c, who.actor, [row["id"]]):
        raise Problem("not_found", f"No such document in the Directory: {body.linkable_id}", 404)
    if row["type"] != "document":
        raise Problem("validation", f"A risk links documents, and {body.linkable_id} is a {row['type']}", 422)
    return row["id"], role


def _editable(c: Any, who: Any, row: Row) -> None:
    if not H.tag_can_edit(c, who.actor, row, _mover(c, who)):
        raise Problem("forbidden", "A risk is changed by its owner or a task mover", 403)


def link(c: Any, who: Any, row: Row, body: RiskLink) -> Row:
    _editable(c, who, row)
    target, role = _link_target(c, who, body)
    if c.execute(
        "SELECT 1 FROM risk_links WHERE risk_id=? AND linkable_type=? AND linkable_id=? AND role=?",
        (row["id"], body.linkable_type, target, role),
    ).fetchone():
        raise Problem("duplicate", "That link is there already", 409)
    ident, ts = H.new_id(), H.now()
    after = {
        "id": ident,
        "risk_id": row["id"],
        "linkable_type": body.linkable_type,
        "linkable_id": target,
        "role": role,
        "created_by_id": who.actor,
        "created_at": ts,
    }
    c.execute(f"INSERT INTO risk_links({','.join(after)}) VALUES({','.join('?' * len(after))})", list(after.values()))
    audit_log.write(c, who.actor, "risk_link:" + ident, None, after, _via(who))
    return {"link": after}


def unlink(c: Any, who: Any, row: Row, body: RiskLink) -> Row:
    _editable(c, who, row)
    roles = LINK_ROLES[body.linkable_type]
    role = body.role or roles[0]
    # A document linked before a merge still unlinks by the id it answers as now.
    found = next(
        (
            r
            for r in c.execute(
                "SELECT * FROM risk_links WHERE risk_id=? AND linkable_type=? AND role=? ORDER BY created_at, id",
                (row["id"], body.linkable_type, role),
            )
            if body.linkable_id in (r["linkable_id"], _current(c, [r["linkable_id"]])[r["linkable_id"]])
        ),
        None,
    )
    if not found:
        raise Problem("not_found", "There is no such link", 404)
    c.execute("DELETE FROM risk_links WHERE id=?", (found["id"],))
    audit_log.write(c, who.actor, "risk_link:" + found["id"], dict(found), None, _via(who))
    return {"link": None, "removed": found["id"]}


# ----------------------------------------------------------------------------- risks


def create(auth: Any, c: Any, who: Any, body: RiskCreate) -> Row:
    auth.domain(who)
    made = slug(body.key or body.label)
    if not made:
        raise Problem("validation", "Give the risk a name with letters or digits", 422)
    key = PREFIX + made
    existing = H.tag_by_key(c, key)
    if existing:
        what = "a risk" if existing.get("kind") == KIND else "a plain tag"
        raise Problem("duplicate", f"{key} exists already as {what}", 409)
    page_bot = page_owners.owner(c, "risk", fallback=False)
    owner = auth.target(c, who, body.owner) if body.owner else page_bot or who.actor
    # Any person may add a risk, as anyone may add one on the page; a bot adds one it owns, or as the Risk page's owner.
    if not (who.role in ("owner", "human") or who.actor in (owner, page_bot)):
        raise Problem("forbidden", "A bot adds a risk it owns, or asks the Risk page's owner bot", 403)
    row = H.tag_create(c, who.actor, key, label=body.label, markdown=body.markdown, owner=owner, mover=True, kind=KIND)
    if row is None:
        raise Problem("duplicate", f"{key} exists already", 409)
    if page_bot:
        # The page owner was chosen by a person (Settings > page owners), so it is granted here, not by the caller.
        c.execute("UPDATE tags SET bot_writers=? WHERE id=?", (H._dump([page_bot]), row["id"]))
        row = H.tag(c, row["id"]) or row
    audit_log.write(c, who.actor, "risk:" + row["id"], None, row, _via(who))
    return {"risk": risk_view(row)}


def update(auth: Any, c: Any, who: Any, row: Row, body: RiskUpdate) -> Row:
    auth.domain(who)
    _editable(c, who, row)
    if row["version"] != body.version:
        raise Problem("version_conflict", "The risk changed; read it and retry", 409)
    fields = body.model_dump(exclude={"version"}, exclude_none=True)
    if not fields:
        raise Problem("validation", "Give a label, markdown or owner to change", 422)
    if fields.get("owner"):
        fields["owner"] = auth.target(c, who, fields["owner"])
    after = H.tag_update(c, who.actor, row["id"], body.version, **fields, mover=_mover(c, who))
    audit_log.write(c, who.actor, "risk:" + row["id"], row, after, _via(who))
    return {"risk": risk_view(after, standing(auth, c, who, [row["id"]]).get(row["id"]))}


def detail(auth: Any, c: Any, who: Any, row: Row, limit: Any = LIMIT_DEFAULT, offset: Any = 0) -> Row:
    counts = standing(auth, c, who, [row["id"]]).get(row["id"])
    page = matters(auth, c, who, risk_id=row["id"], limit=limit, offset=offset)
    return {
        "risk": risk_view(row, counts),
        "editable": H.tag_can_edit(c, who.actor, row, _mover(c, who)),
        **page,
        **links(c, who, row["id"]),
        "obligations": obligations(auth, c, who, risk_id=row["id"])["obligations"],
    }


def install(app: Any, store: Any, auth: Any, mutate: Any) -> None:
    def reader(request: Request) -> Any:
        who = request.state.identity
        auth.domain(who)
        return who

    @app.get(
        "/api/v2/risks",
        **api_v2("Risk", "listRisks", "Every risk with its level, counts, claimed and next date", "RiskList"),
    )
    def list_route(request: Request, task: str | None = None) -> Any:  # pyright: ignore[reportUnusedFunction]
        """Every risk, live first. Counts, claimed totals and the next date come from the tasks the caller reads.
        `task` lists only the risks that task carries."""
        who = reader(request)
        with store.read() as c:
            return list_risks(auth, c, who, task)

    @app.post("/api/v2/risks", **api_v2("Risk", "createRisk", "Add a risk: a tag of kind risk", "RiskResult"))
    def create_route(request: Request, body: RiskCreate) -> Any:  # pyright: ignore[reportUnusedFunction]
        return mutate(request, body, lambda c: create(auth, c, request.state.identity, body))  # pyright: ignore[reportUnknownLambdaType]

    @app.get("/api/v2/matters", **api_v2("Risk", "listMatters", "Matters the caller reads, newest first", "MatterList"))
    def matters_route(  # pyright: ignore[reportUnusedFunction]
        request: Request,
        kind: str | None = None,
        open: bool | None = None,
        risk: str | None = None,
        limit: int = LIMIT_DEFAULT,
        offset: int = 0,
    ) -> Any:
        """Tasks carrying a risk that the caller reads, with their facts. `kind=dispute&open=true` is the Disputes tab."""
        who = reader(request)
        with store.read() as c:
            risk_id = require_risk(c, who, risk)["id"] if risk else None
            return matters(auth, c, who, risk_id=risk_id, kind=kind, open_only=open, limit=limit, offset=offset)

    @app.get(
        "/api/v2/risk/obligations",
        **api_v2("Risk", "listRiskObligations", "Every risk's dates, unconfirmed first", "RiskObligationList"),
    )
    def obligations_route(  # pyright: ignore[reportUnusedFunction]
        request: Request,
        risk: str | None = None,
        state: str | None = None,
        group: str | None = None,
        days: int = DAYS_DEFAULT,
        limit: int = LIMIT_MAX,
        offset: int = 0,
    ) -> Any:
        """The dates of every risk (or of `risk`) the caller may see: unconfirmed ones back a year, due ones up to `days`
        ahead, and those settled or passed in the last 30 days. `state` (due, met, waived, missed, passed, unconfirmed,
        watched) or `group` (unconfirmed, next, later, past) narrows it."""
        who = reader(request)
        with store.read() as c:
            risk_id = require_risk(c, who, risk)["id"] if risk else None
            return obligations(
                auth, c, who, risk_id=risk_id, state=state, group=group, days=days, limit=limit, offset=offset
            )

    @app.post(
        "/api/v2/tasks/{tid}/matter",
        **api_v2("Risk", "setTaskMatter", "Set or clear a task's matter facts", "MatterResult"),
    )
    def matter_route(request: Request, tid: str, body: MatterWrite) -> Any:  # pyright: ignore[reportUnusedFunction]
        return mutate(request, body, lambda c: set_matter(auth, c, request.state.identity, tid, body))  # pyright: ignore[reportUnknownLambdaType]

    @app.get(
        "/api/v2/risks/{risk_id}", **api_v2("Risk", "getRisk", "A risk, its matters, rules and sources", "RiskDetail")
    )
    def get_route(request: Request, risk_id: str, limit: int = LIMIT_DEFAULT, offset: int = 0) -> Any:  # pyright: ignore[reportUnusedFunction]
        who = reader(request)
        with store.read() as c:
            return detail(auth, c, who, require_risk(c, who, risk_id), limit, offset)

    @app.post(
        "/api/v2/risks/{risk_id}", **api_v2("Risk", "updateRisk", "Edit a risk using its current version", "RiskResult")
    )
    def update_route(request: Request, risk_id: str, body: RiskUpdate) -> Any:  # pyright: ignore[reportUnusedFunction]
        def work(c: Any) -> Any:
            who = request.state.identity
            return update(auth, c, who, require_risk(c, who, risk_id), body)

        return mutate(request, body, work)

    @app.post(
        "/api/v2/risks/{risk_id}/links",
        **api_v2("Risk", "linkRisk", "Link a rule or a document to a risk", "RiskLinkResult"),
    )
    def link_route(request: Request, risk_id: str, body: RiskLink) -> Any:  # pyright: ignore[reportUnusedFunction]
        def work(c: Any) -> Any:
            who = request.state.identity
            auth.domain(who)
            return link(c, who, require_risk(c, who, risk_id), body)

        return mutate(request, body, work)

    @app.post(
        "/api/v2/risks/{risk_id}/unlink",
        **api_v2("Risk", "unlinkRisk", "Remove a rule or document link from a risk", "RiskLinkResult"),
    )
    def unlink_route(request: Request, risk_id: str, body: RiskLink) -> Any:  # pyright: ignore[reportUnusedFunction]
        def work(c: Any) -> Any:
            who = request.state.identity
            auth.domain(who)
            return unlink(c, who, require_risk(c, who, risk_id), body)

        return mutate(request, body, work)
