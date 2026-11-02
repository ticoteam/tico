"""What a meeting turns into: action items a person pushes to the hub's task board.

A meeting holds three lists: **Doc updates**, **Tasks** and **Feature requests**, an outbox a
person empties. They live in `meeting_items` beside the meeting rather than inside its notes, so
they can be edited and pushed for as long as the meeting exists. A person adds an item, with or
without the quote it came from; a push creates an ordinary hub task, never a card in another tool.

Only a person changes status: `proposed → pushed` happens through Push and nowhere else. Push is
also the approval: a person pressed it.
"""

import difflib
import json
import re
from typing import Literal

from fastapi import Request
from pydantic import Field

from . import media
from . import models as M
from . import people as P
from .store import H, Problem
from .views import human_only, roster

SECTIONS = ("doc", "task", "feature")
PUSHABLE = SECTIONS
# A feature request is a task on the hub's own board, at the bottom of the product team's queue;
# a person on the product or engineering team ranks and reassigns it. Its owner is the person
# who is primary for the product team in registry/people.yaml, else the environment owner.
FEATURE_TEAM = "product"
# Labels that describe a process on the old board, never a request; a push carrying one is refused.
PROCESS_LABELS = {"blocked", "blocked by specs", "needs architecture review", "needs peer review",
                  "spec reviewed", "in review", "ready for qa"}
DUPLICATE_RATIO = 0.8
DOC_BOT = "doc-updater"
# Only these keys mean anything per section, so anything else is a mistake worth naming.
DETAIL_KEYS = {
    "doc": ("document", "change", "why"),
    "task": ("owner", "due", "priority"),
    "feature": ("side", "app", "area", "bug", "current", "expected", "labels"),
}
PRIORITIES = ("p0", "p1", "p2", "p3")
SIDES = {"B": ("back-end",), "F": ("front-end",), "B/F": ("back-end", "front-end")}


class ItemCreate(M.Contract):
    section: Literal["doc", "task", "feature"]
    text: str = Field(min_length=1, max_length=2_000)
    detail: dict = Field(default_factory=dict)
    quote: str = Field(default="", max_length=4_000)
    at_ms: int | None = Field(default=None, ge=0, le=86_400_000)


class ItemEdit(M.Contract):
    text: str | None = Field(default=None, min_length=1, max_length=2_000)
    detail: dict | None = None
    status: Literal["dismissed"] | None = None


class ItemPush(M.Contract):
    force: bool = False


class CommentCreate(M.Contract):
    text: str = Field(min_length=1, max_length=4_000)
    at_ms: int | None = Field(default=None, ge=0, le=86_400_000)


# ----------------------------------------------------------------------------- comments
def comments(c, rid, people=None):
    """The thread on one meeting, oldest first, with the author's roster name."""
    people = people if people is not None else roster(c)
    out = []
    for row in c.execute("SELECT * FROM meeting_comments WHERE meeting_id=? ORDER BY created_at, id", (rid,)):
        person = P.person(H.actor_id(row["author"]), people) if str(row["author"]).startswith("human:") else None
        out.append({**dict(row), "author_name": (person or {}).get("name") or row["author"]})
    return out


def comment(c, record, who, body):
    """Anyone who can open the meeting can add to its thread; the recorder's own note is separate."""
    ts, cid = H.now(), H.new_id()
    c.execute("INSERT INTO meeting_comments(id,meeting_id,author,text,at_ms,created_at) VALUES(?,?,?,?,?,?)",
              (cid, record["id"], who.actor, body.text.strip(), body.at_ms, ts))
    H.event(c, who.actor, "meeting.comment_added", record["id"], {"comment": cid})
    return comments(c, record["id"])[-1]


def detail_of(section, value):
    """The detail a section understands. An unknown key is a mistake, not something to drop."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise Problem("detail", "detail must be an object", 422)
    allowed = DETAIL_KEYS[section]
    extra = sorted(set(value) - set(allowed))
    if extra:
        raise Problem("detail", f"A {section} item has no {', '.join(extra)}; it takes "
                                f"{', '.join(allowed) or 'no detail'}", 422)
    out = {}
    for key, raw in value.items():
        if key == "bug":
            out[key] = bool(raw)
        elif key == "labels":
            if not isinstance(raw, list) or any(not isinstance(v, str) for v in raw):
                raise Problem("detail", "labels must be a list of strings", 422)
            out[key] = [v.strip()[:100] for v in raw[:10] if v.strip()]
        else:
            out[key] = str(raw).strip()[:4_000]
    if out.get("priority") and out["priority"] not in PRIORITIES:
        raise Problem("detail", "priority is one of " + ", ".join(PRIORITIES), 422)
    return {k: v for k, v in out.items() if v not in ("", [])}


# ----------------------------------------------------------------------------- reading
def view(row):
    item = dict(row)
    item["detail"] = json.loads(item.pop("detail") or "{}")
    return item


def one(c, rid, iid):
    row = c.execute("SELECT * FROM meeting_items WHERE id=? AND meeting_id=?", (iid, rid)).fetchone()
    if not row:
        raise Problem("not_found", "Item not found on this meeting", 404)
    return row


def listing(c, who, rid):
    """Every item on this meeting, grouped by section, plus whether this person may push."""
    record = media.authorized(c, who, rid)
    rows = [view(r) for r in c.execute(
        "SELECT * FROM meeting_items WHERE meeting_id=? ORDER BY created_at, id", (rid,))]
    return {"id": rid, "can_push": media.may_write(who, record),
            "sections": {section: [r for r in rows if r["section"] == section] for section in SECTIONS},
            "counts": {"proposed": sum(1 for r in rows if r["status"] == "proposed" and r["section"] in PUSHABLE)}}


# ----------------------------------------------------------------------------- writing
def add(c, record, author, body):
    """One proposed item."""
    section = body.section
    detail = detail_of(section, body.detail)
    ts, iid = H.now(), H.new_id()
    c.execute("INSERT INTO meeting_items(id,meeting_id,section,text,detail,quote,at_ms,status,"
              "created_by,updated_by,pushed_at,result_ref,created_at,updated_at) "
              "VALUES(?,?,?,?,?,?,?,'proposed',?,NULL,NULL,NULL,?,?)",
              (iid, record["id"], section, body.text.strip(), json.dumps(detail, sort_keys=True),
               body.quote.strip(), body.at_ms, author, ts, ts))
    H.event(c, author, "meeting.item_added", record["id"], {"item": iid, "section": section})
    return view(one(c, record["id"], iid))


def edit(c, rid, author, iid, body):
    row = one(c, rid, iid)
    if body.text is None and body.detail is None and body.status is None:
        raise Problem("validation", "Change the text, the detail, or delete the item", 422)
    if body.status == "dismissed" and row["status"] != "proposed":
        raise Problem("status", "Only a proposed item can be deleted", 409)
    sets, args = ["updated_by=?", "updated_at=?"], [author, H.now()]
    if body.text is not None:
        sets.append("text=?"); args.append(body.text.strip())
    if body.detail is not None:
        sets.append("detail=?"); args.append(json.dumps(detail_of(row["section"], body.detail), sort_keys=True))
    if body.status is not None:
        sets.append("status=?"); args.append(body.status)
    c.execute(f"UPDATE meeting_items SET {','.join(sets)} WHERE id=? AND meeting_id=?", (*args, iid, rid))
    if body.status == "dismissed":
        H.event(c, author, "meeting.item_dismissed", rid, {"item": iid, "section": row["section"]})
    return view(one(c, rid, iid))


# ----------------------------------------------------------------------------- what Push makes
def source(settings, record, row, name):
    """The footer every pushed item carries: which meeting it came from, and the words it quotes."""
    started = str(record["metadata"].get("started") or record["created_at"])[:10]
    lines = [f"From meeting: {record['title']}, {started} — {name}"]
    if row["quote"]:
        lines.append("> " + row["quote"].replace("\n", " "))
    lines.append(f"Meeting: {settings.public_url}/#/meetings?meeting={record['id']}")
    return "\n".join(lines)


def person_name(c, who):
    return (P.person(H.actor_id(who.actor), roster(c)) or {}).get("name") or who.email or who.actor


def owner_of(c, detail):
    """`human:<id>` or `bot:<slug>`, checked against the roster and the bots table."""
    owner = str(detail.get("owner") or "").strip()
    if owner.startswith("human:") and c.execute("SELECT 1 FROM humans WHERE id=?", (owner[6:],)).fetchone():
        return owner
    if owner.startswith("bot:") and c.execute("SELECT 1 FROM bots WHERE slug=?", (owner[4:],)).fetchone():
        return owner
    raise Problem("owner", "Choose an owner for this task: human:<id> or bot:<slug>", 422)


def push_task(c, settings, task_create, who, record, row, detail):
    owner = owner_of(c, detail)
    priority = detail.get("priority") or "p2"
    body = "\n\n".join([row["text"], f"Priority: {priority}", source(settings, record, row, person_name(c, who))])
    created = task_create(c, who, M.TaskCreate(title=row["text"][:300], body=body, owner=owner,
                                              due=detail.get("due") or None))
    return created["task"]["id"]


def push_doc(c, settings, task_create, who, record, row, detail):
    body = "\n\n".join(part for part in [
        "Document: " + (detail.get("document") or "(the meeting did not name one)"),
        "Change: " + (detail.get("change") or row["text"]),
        ("Why: " + detail["why"]) if detail.get("why") else "",
        source(settings, record, row, person_name(c, who)),
    ] if part)
    created = task_create(c, who, M.TaskCreate(title=("Doc update: " + row["text"])[:300], body=body,
                                               owner="bot:" + DOC_BOT))
    return created["task"]["id"]


def feature_fields(settings, record, row, detail, name):
    """The task this feature request becomes: a plain title, labels and a body, per the plan."""
    side = detail.get("side") or ""
    area = (detail.get("area") or "").strip()
    sentence = " ".join(row["text"].split())
    title = f"{area + ' - ' if area else ''}{sentence}"[:300]
    labels = list(SIDES.get(side, (side,) if side else ()))
    if detail.get("app"):
        labels.append(str(detail["app"]).lower())
    if area:
        labels.append(area)
    if detail.get("bug"):
        labels.append("bug")
    for extra in detail.get("labels") or []:
        if extra not in labels:
            labels.append(extra)
    for label in labels:
        if str(label).strip().lower() in PROCESS_LABELS:
            raise Problem("label", f'"{label}" is a process label, not a request; leave it off', 403)
    if detail.get("bug"):
        what = (f"**Current behavior**\n{detail.get('current') or sentence}\n\n"
                f"**Expected behavior**\n{detail.get('expected') or ''}").strip()
    else:
        what = sentence
    return {"title": title, "labels": labels, "body": what + "\n\n" + source(settings, record, row, name)}


def _normalise(title):
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", str(title or "").lower()).split())


def feature_duplicate(c, title):
    """A task still on the board whose title means the same thing, or None."""
    wanted = _normalise(title)
    if not wanted:
        return None
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    for row in c.execute(f"SELECT id,title,status FROM tasks WHERE status IN ({marks})", H.ACTIVE_STATUSES):
        if difflib.SequenceMatcher(None, wanted, _normalise(row["title"])).ratio() >= DUPLICATE_RATIO:
            return {"id": row["id"], "name": row["title"], "list": row["status"]}
    return None


def feature_owner(c):
    """The person primary for the product team, else the environment owner."""
    r = roster(c)
    for person in r.get("people") or []:
        if FEATURE_TEAM in (person.get("primary_for") or []):
            return H.human_actor(person["id"])
    return H.human_actor(r.get("default_user") or H.default_human(c))


def push_feature(c, settings, task_create, who, record, row, detail, force):
    """The feature-request task, or a 409 naming the task that already says this."""
    fields = feature_fields(settings, record, row, detail, person_name(c, who))
    if not force:
        found = feature_duplicate(c, fields["title"])
        if found:
            found["url"] = f"{settings.public_url}/#/task/{found['id']}"
            raise Problem("duplicate", "A task on the board already says this: "
                          f"{found['name']} ({found['list']})", 409, extra={"duplicate": found})
    # A feature request is a backlog item (a bug, a feature), not an ask, so rule 7 does not shape it.
    created = task_create(c, who, M.TaskCreate(title=fields["title"], body=fields["body"], owner=feature_owner(c),
                                               labels=fields["labels"]), lint=False)
    return created["task"]["id"]


def pushable(c, who, rid, iid):
    record = media.authorized(c, who, rid)
    media.require_live(record)
    if not media.may_write(who, record):
        raise Problem("forbidden", "The meeting's owner pushes its items", 403)
    row = one(c, rid, iid)
    if row["section"] not in PUSHABLE:
        raise Problem("section", "This section is not pushed", 422)
    if row["status"] == "dismissed":
        raise Problem("status", "This item was deleted; it is not pushed", 409)
    return record, row


def record_push(c, who, rid, iid, ref):
    row = one(c, rid, iid)
    if row["status"] == "pushed":                 # a second click, or a concurrent one: one result
        return view(row)
    ts = H.now()
    c.execute("UPDATE meeting_items SET status='pushed',result_ref=?,pushed_at=?,updated_by=?,updated_at=? "
              "WHERE id=? AND meeting_id=?", (ref, ts, who.actor, ts, iid, rid))
    H.event(c, who.actor, "meeting.item_pushed", rid,
            {"item": iid, "section": row["section"], "result": ref})
    return view(one(c, rid, iid))


# ----------------------------------------------------------------------------- routes
def install_meeting_items(app, store, mutate, task_create):
    settings = store.settings

    @app.get("/api/v2/meetings/{rid}/items")
    @app.get("/api/meetings/{rid}/items")
    def items(request: Request, rid: str):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            return listing(c, who, rid)

    @app.get("/api/v2/meetings/{rid}/comments")
    @app.get("/api/meetings/{rid}/comments")
    def thread(request: Request, rid: str):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            media.authorized(c, who, rid)
            return {"id": rid, "comments": comments(c, rid)}

    @app.post("/api/v2/meetings/{rid}/comments")
    @app.post("/api/meetings/{rid}/comments")
    def say(request: Request, rid: str, body: CommentCreate):
        who = request.state.identity
        human_only(who)
        def work(c):
            record = media.authorized(c, who, rid)
            return {"comment": comment(c, record, who, body)}
        return mutate(request, body, work)

    @app.post("/api/v2/meetings/{rid}/items")
    @app.post("/api/meetings/{rid}/items")
    def create(request: Request, rid: str, body: ItemCreate):
        who = request.state.identity
        human_only(who)
        def work(c):
            record = media.authorized(c, who, rid)
            return {"item": add(c, record, who.actor, body)}
        return mutate(request, body, work)

    @app.post("/api/v2/meetings/{rid}/items/{iid}")
    @app.post("/api/meetings/{rid}/items/{iid}")
    def change(request: Request, rid: str, iid: str, body: ItemEdit):
        who = request.state.identity
        human_only(who)
        def work(c):
            media.authorized(c, who, rid)
            return {"item": edit(c, rid, who.actor, iid, body)}
        return mutate(request, body, work)

    @app.post("/api/v2/meetings/{rid}/items/{iid}/push")
    @app.post("/api/meetings/{rid}/items/{iid}/push")
    def push(request: Request, rid: str, iid: str, body: ItemPush):
        who = request.state.identity
        human_only(who)
        def work(c):
            record, row = pushable(c, who, rid, iid)
            if row["status"] == "pushed":
                return {"item": view(row), "pushed": False}
            detail = json.loads(row["detail"] or "{}")
            if row["section"] == "task":
                result = push_task(c, settings, task_create, who, record, row, detail)
            elif row["section"] == "doc":
                result = push_doc(c, settings, task_create, who, record, row, detail)
            else:
                # a close match on the board is refused; `force` is the person saying they meant it
                result = push_feature(c, settings, task_create, who, record, row, detail, body.force)
            return {"item": record_push(c, who, rid, iid, result), "pushed": True}
        return mutate(request, body, work)

    return {"add": add, "listing": listing}
