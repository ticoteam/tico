"""Outcome stats: counts of how proposals and tool calls turned out, for a bot the owner names.

A bot that studies how the team's work holds up (accepted, cancelled, failed) needs the totals, never the rows: an
Assistant proposal belongs to the person it was made for and carries their words, and a tool call's event names who
made it. The owner grants a bot these totals in `registry_metadata` (`outcome-stats-readers`), outside any editable
bot definition, so no bot grants them to itself. A granted bot reads `assistant_action_weekly` and `mcp_call_weekly` in
`hub sql` (backend/sql.py) and `GET /api/v2/live/stats` without `via`; it still reads no `assistant_actions` row and
writes nothing. Revoking the grant takes all of that away on its next query.
"""

from fastapi import Request

from clients import hubtools

from . import models as M
from .repositories import metadata, save_metadata
from .store import H, Problem

GRANTS = "outcome-stats-readers"
TOOL_NAMES = frozenset(hubtools.BY_NAME)


class ReaderSet(M.Contract):
    read: bool


def may_read(c, who):
    """The owner, and a bot the owner granted the totals. A person reads their own proposals in the Assistant."""
    if who.via or who.task_actor:
        return False
    if who.role == "owner":
        return True
    return who.role == "bot" and metadata(c, GRANTS).get(H.actor_id(who.actor)) is True


def require_owner(who):
    """The owner, as themselves: never a bot, BotOps or the Assistant acting for them."""
    if who.role != "owner" or who.via_token or who.via or who.task_actor:
        raise Problem("forbidden", "Only the owner chooses which bots read outcome stats, signed in to Tico", 403)


def readers(c):
    return sorted(slug for slug, on in metadata(c, GRANTS).items() if on is True)


def install(app, store, auth):
    @app.get("/api/v2/outcome-stats/readers")
    def outcome_readers(request: Request):
        """The bots that may read outcome stats (counts by week, status and tool; never a row). The owner."""
        who = request.state.identity
        auth.domain(who)
        require_owner(who)
        with store.read() as c:
            return {"readers": readers(c)}

    @app.put("/api/v2/outcome-stats/readers/{slug}")
    def outcome_reader_set(request: Request, slug: str, body: ReaderSet):
        """Grant (`read: true`) or revoke (`read: false`) one bot's read of outcome stats. The owner's own click."""
        who = request.state.identity
        auth.domain(who)
        require_owner(who)
        with store.transaction() as c:
            if not H.bot(c, slug):
                raise Problem("not_found", "No bot " + slug, 404)
            grants = metadata(c, GRANTS)
            if (grants.get(slug) is True) != body.read:
                if body.read:
                    grants[slug] = True
                else:
                    grants.pop(slug, None)
                save_metadata(c, GRANTS, grants)
                H.event(c, who.actor, "outcome_stats.granted" if body.read else "outcome_stats.revoked",
                        "bot:" + slug, {})
            return {"readers": readers(c)}
