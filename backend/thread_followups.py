"""Earlier messages of a migrated bot (spec 4): one notice to each of its managers about its Home's closed history.

A bot's Home can still hold closed shared-room history after a migration. The same scheduler pass (backend/scheduler.py
run_thread_followups) tells each of the bot's managers once, in their own DM with the bot because the Home's readers must
not see it, with the one action that opens the history to Read. It is recorded per bot and manager, so whoever comes to
manage the bot later hears once too, and once the history is open nobody more is told.

Asks that go unanswered get no nudge from Tico: an ask a bot is waiting on is listed on the bot's task roundup
(backend/task_roundup.py) and an unanswered ask to a person goes back to its bot (backend/still_needed.py).
"""
import logging

from . import hubdb as H
from . import sources as SRC

LOG = logging.getLogger("tico.thread_followups")


def _bot_managers(c, auth, slug):
    """Who hears about a bot's closed history: its operator and owners and the people above it on the chart, else the
    company owner. Admins can manage every bot but aren't told about each one."""
    from . import bot_access as A
    from . import people as P
    from . import views
    roster, entries = views.roster(c), views.entries(c)
    archived = {row["id"] for row in H.bots(c) if row.get("state") == "archived"}
    row = c.execute("SELECT owner_id, bot_owners FROM bot_config WHERE bot=?", (slug,)).fetchone()
    direct = {row["owner_id"], *A.owner_ids(row["bot_owners"])} if row else set()
    found = [p["id"] for p in roster.get("people") or [] if not p.get("hidden") and (
        p["id"] in direct or P.manages(p["id"], "bot", slug, roster, entries, archived, True))]
    if found:
        return found
    owner = auth.owner_id(c) if auth is not None else ""
    return [owner] if owner else []


@SRC.scoped("earlier-messages")
def earlier_sweep(c, auth, at):
    """One notice to each manager of a bot whose Home still holds closed shared-room history. Returns those written."""
    from . import threads
    done = []
    for home_id, slug in c.execute(
            "SELECT DISTINCT conversation_id, substr(legacy_audience,8) FROM messages "
            "WHERE legacy_audience IS NOT NULL AND legacy_audience LIKE 'shared:%'").fetchall():
        home = c.execute("SELECT id FROM conversations WHERE id=? AND audience='home' AND owner_id=? "
                         "AND closed_at IS NULL AND archived_at IS NULL", (home_id, "bot:" + slug)).fetchone()
        bot = H.bot(c, slug)
        if not home or not bot or bot.get("state") == "archived":
            continue
        for pid in _bot_managers(c, auth, slug):
            actor = "human:" + pid
            if c.execute("SELECT 1 FROM thread_earlier_notices WHERE bot=? AND actor=?", (slug, actor)).fetchone():
                continue
            c.execute("SAVEPOINT thread_earlier")
            try:
                dm = threads.dm(c, actor, ["bot:" + slug])
                posted = threads.post(c, H.KEEPER, dm["id"],
                                      f"{bot.get('display_name') or slug}'s earlier messages are closed to its Home's readers.",
                                      kind="notice", refs={"earlier_messages": {"thread_id": home_id}}, to_actor=actor)
                c.execute("INSERT INTO thread_earlier_notices(bot, actor, message_id, created_at) VALUES(?,?,?,?)",
                          (slug, actor, posted["id"], at))
                c.execute("RELEASE thread_earlier")
                done.append({"bot": slug, "to": actor})
            except Exception:  # noqa: BLE001 - one manager's notice failing leaves the next run to try again
                c.execute("ROLLBACK TO thread_earlier")
                c.execute("RELEASE thread_earlier")
                LOG.warning("earlier-messages notice failed", exc_info=True)
    return done
