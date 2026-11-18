"""A bot's Growth proposal: a channel, a source or both that the bot will own, asked as an OK of the owner (or an admin
it names) from its preview (backend/task_asks.py, subject `growth:<id>`). When the person OKs it, Tico adds what it
names in that transaction, as that person, owned by the bot; the OK is used and the bot gets a notice. Nothing is added
before the OK, and a send back adds nothing.

  propose(c, who, body, catalog)   the bot's ask: {task_id, requirement_id, status, asked_of}
  may_ok(c, person, grant)         task_asks.MAY_OK: the owner or an admin, as a person
  ok_given(c, grant, ctx)          task_asks.ON_OK: checks it all again, adds it, uses the OK, tells the bot

Content: `{kind: "growth", owner, channel, source, for_channel}`. `channel` and `source` are the bodies of POST
/api/v2/growth/channels and /api/v2/growth/sources with owner_ref the bot; with both, the channel names the new source.
`for_channel` (a source alone) is a channel the bot owns, which names the new source. A Finance ad spend source reads
stored ad keys, so a person connects it (POST /api/v2/growth/sources); a bot never proposes one.
"""
# pyright: strict

from typing import Any

from . import growth_finance as _gf_mod
from . import hubdb as _hubdb_mod
from . import models as _models_mod
from . import task_asks
from .store import Problem

H: Any = _hubdb_mod
M: Any = _models_mod
GF: Any = _gf_mod
Row = dict[str, Any]

SUBJECT = "growth:"
REACH_DEFAULT = {"file": "person", "bot_cost": "person"}


def _g() -> Any:
    from . import growth

    return growth


def _line(content: Row) -> str:
    if content.get("channel") and content.get("source"):
        return "Add a Growth channel and its source?"
    if content.get("channel"):
        return "Add a Growth channel?"
    return "Add a Growth source?"


def _parse(content: Row) -> tuple[Any, Any, str | None]:
    channel, source = content.get("channel"), content.get("source")
    return (
        M.GrowthChannelCreate(**channel) if channel else None,
        M.GrowthSourceCreate(**source) if source else None,
        content.get("for_channel") or None,
    )


def check(c: Any, bot: str, channel: Any, source: Any, for_channel: str | None, catalog: Any = None) -> None:
    """Refuses, when it is asked and again when it is OK'd, what Tico could not add as it stands now."""
    G = _g()
    if not channel and not source:
        raise Problem("validation", "Propose a channel, a source or both", 422)
    if channel and for_channel:
        raise Problem("validation", "for_channel goes with a source alone", 422)
    if for_channel and not source:
        raise Problem("validation", "for_channel names the channel a proposed source is for", 422)
    me = G._owner_ref(c, bot)
    for body in (channel, source):
        if body and body.owner_ref not in (None, "") and G._owner_ref(c, body.owner_ref) != me:
            raise Problem("forbidden", "A bot proposes a channel or source it owns itself", 403)
    if channel:
        if G.channel(c, channel.slug):
            raise Problem("conflict", f"There is already a channel {channel.slug}", 409)
        if channel.source_id and source:
            raise Problem("validation", "Name a source_id or propose a new source, not both", 422)
        G._source_ref(c, channel.source_id)
        G._kpi_ref(c, channel.guardrail_kpi_id, "guardrail_kpi_id")
        G._kpi_ref(c, channel.input_kpi_id, "input_kpi_id")
    if for_channel:
        row = G.channel(c, for_channel)
        if not row or row.get("retired_at"):
            raise Problem("not_found", f"No channel {for_channel}", 404)
        if row.get("owner_ref") != me:
            raise Problem("forbidden", "A bot proposes a source for a channel it owns", 403)
    if source:
        if source.sync_kind == GF.KIND:
            raise Problem("forbidden", "A person connects Finance ad spend: it reads the stored ad keys", 403)
        if not (source.reach_kind or REACH_DEFAULT.get(source.sync_kind or "")):
            raise Problem("validation", "reach_kind is mcp, credential, url or person", 422)
        if source.sync_kind:
            G._sync_fields(c, {"sync_map": source.sync_map}, H.new_id(), source.sync_kind, catalog)


def _content(c: Any, bot: str, body: Any) -> Row:
    me = _g()._owner_ref(c, bot)

    def owned(model: Any) -> Row | None:
        return {**model.model_dump(mode="json", exclude_none=True), "owner_ref": me} if model else None

    return {
        "kind": "growth",
        "owner": me,
        "channel": owned(body.channel),
        "source": owned(body.source),
        "for_channel": body.for_channel,
    }


def waiting(c: Any) -> list[tuple[Row, Row]]:
    """The Growth proposals a person has yet to OK, oldest first: (grant, content)."""
    out: list[tuple[Row, Row]] = []
    for (rid,) in c.execute(
        "SELECT requirement_id FROM ok_grants WHERE subject LIKE ? AND used_at IS NULL ORDER BY created_at",
        (SUBJECT + "%",),
    ).fetchall():
        found = task_asks.grant(c, rid)
        if found and task_asks.standing(c, rid) == task_asks.WAITING:
            out.append((found, found.get("content") or {}))
    return out


def _slug(content: Row) -> str | None:
    return (content.get("channel") or {}).get("slug") or content.get("for_channel")


def _approver(c: Any, ask: str | None) -> str:
    if ask:
        person = H.resolve_actor(c, ask)
        if not person or not task_asks.owner_or_admin(c, person):
            raise Problem("validation", "A Growth proposal is asked of the owner or an admin", 422)
        return person
    from .automations import top_person

    pid = top_person(c)
    if not pid:
        raise Problem("no_approver", "Nobody can approve this: the company has no owner on the team", 409)
    return "human:" + pid


def propose(c: Any, who: Any, body: Any, catalog: Any = None) -> Row:
    """A bot's channel or source as an OK of the owner (or the admin `body.ask` names). The same proposal sent again is
    the same ask; another one for a channel already waiting is refused, naming the task where it waits."""
    if who.role != "bot":
        raise Problem("forbidden", "A bot proposes; the owner or an admin adds a channel or source directly", 403)
    channel, source = body.channel, body.source
    check(c, who.actor, channel, source, body.for_channel, catalog)
    content = _content(c, who.actor, body)
    slug = _slug(content)
    person = _approver(c, body.ask)
    for found, other in waiting(c):
        task_id = task_asks.task_of(c, found["requirement_id"])
        if found["asked_by_id"] == who.actor and other == content:
            return {"task_id": task_id, "requirement_id": found["requirement_id"], "status": "waiting"}
        if slug and _slug(other) == slug:
            number = (H.task(c, task_id) or {}).get("number") if task_id else None
            raise Problem("conflict", f"A proposal for {slug} already waits for an OK on task #{number}", 409)
    line = _line(content)
    asked = task_asks.ask_ok(
        c,
        who.actor,
        person,
        line,
        shows=["growth:proposal"],
        subject=SUBJECT + H.new_id(),
        content=content,
        task_id=body.task_id,
        title=line.rstrip("?"),
    )
    return {**asked, "status": "waiting", "asked_of": H.actor_name(c, person)}


def may_ok(c: Any, person: str, grant: Row) -> str | None:
    """Whether `person` may OK a Growth proposal now (task_asks.MAY_OK): the owner or an admin, as a person."""
    if not task_asks.owner_or_admin(c, person):
        return "Only the owner or an admin OKs a Growth channel"
    return None


def ok_given(c: Any, grant: Row, ctx: Any) -> None:
    """The person OK'd it from its preview (task_asks.ON_OK), in that transaction: what it names is added as them,
    owned by the bot, after every check runs again; the OK is used and the bot hears what was added."""
    who = getattr(ctx, "who", None)
    if who is None or who.actor != grant["ok_by_id"]:
        raise Problem("approval", "A Growth proposal is OK'd from its preview in Tico", 409)
    G = _g()
    content: Row = grant["content"] or {}
    bot, rid = str(grant["asked_by_id"]), str(grant["requirement_id"])
    channel, source, for_channel = _parse(content)
    catalog = getattr(getattr(getattr(ctx, "app", None), "state", None), "integrations", None)
    check(c, bot, channel, source, for_channel, catalog)
    person = who.actor
    said: list[str] = []
    sid = None
    if source:
        sid = G.source_create(c, person, source, catalog, who)["id"]
        said.append(f"source {source.system} ({sid})")
        H.event(c, person, "growth.source.proposed", sid, {"proposed_by": bot, "approval": rid})
    if channel:
        if sid:
            channel.source_id = sid
        made = G.channel_create(c, person, channel)
        said.insert(0, f"channel {channel.slug}")
        H.event(c, person, "growth.channel.proposed", made["id"], {"proposed_by": bot, "approval": rid})
    elif for_channel and sid:
        G.channel_update(c, person, for_channel, {"source_id": sid})
        said.append(f"named on channel {for_channel}")
    words = f"Added {' and '.join(said)}, owned by you. OK'd by {H.actor_name(c, person)}."
    task_asks.use(c, rid, grant["subject"], content, bot)
    task_asks.settle(c, rid, words)
    task_id = task_asks.task_of(c, rid)
    H.say(c, H.TICO_ACTOR, bot, words, kind="notice", refs={"approval": rid, **({"task": task_id} if task_id else {})})
