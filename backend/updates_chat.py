"""The Updates chat (docs/assistant.md, docs/strategy-bot.md; spec: Strategy Bot v3, section 10.8): a person's own chat
bot beside the Updates page: their Assistant, the owner's included.

- It is the same conversation as their menu tab: the page reads and posts through GET /api/v2/assistant and POST
  /api/v2/assistant/messages, which say `place: "updates"` on the message (`refs.updates`). No new room, session, spend
  type or cap: each question is one turn of that bot on the company's usual model path, and a reply is a chat message,
  never a Needs you item, so it does not count toward the Strategy Bot's 5 a day (backend/strategy_bot.py).
- What its turn is handed (`for_turn`, backend/execution.py; rendered by runner-rs/src/prompt.rs): only a turn the
  person started from the Updates chat, in their own chat, gets `updates`: the recent daily and weekly updates that
  person may read, read now by the same rules as their Updates page (`updates.visible_authors`, the meetings flag), less
  any update that links or names by id a task, chat or bot the turn may not open. The filter is on what the bot is
  handed, not a rule in its prompt: an update it is not handed it cannot quote or link.
- Each update comes with a default `rank`: affects a goal (its author has a live goal), then waits on you (its author
  waits on a task of yours, or it names you), then newest. The bot follows what the person taught it, and for the
  owner the company layer, over that order (backend/learned.py).
- Each update's `link` (`#/updates?kind=&day=&update=`) scrolls the page to that card and highlights it, switching Daily
  or Weekly and the day as needed (ui/app/updates.js updGoTo)."""
import re

from . import rooms
from . import updates as U
from .store import H, Problem

DAILY_DAYS = 4            # today and the days before it, so Monday still has Friday's to offer as the last day's top
WEEKLY_DAYS = 8           # the latest week in review
ITEMS_MAX = 40            # every turn from Updates carries them all
BODY_MAX = 700            # characters of one update's text
LINKS = re.compile(r"(?:#/|/)(tasks?|bot|chat|conversations?)/([A-Za-z0-9_.:-]+)", re.I)
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
SHORT_TASK = re.compile(r"^[0-9a-f]{8,}$", re.I)
BARE_SHORT_TASK = re.compile(r"(?<![0-9a-f])([0-9a-f]{8,})(?![0-9a-f])", re.I)
TASK_NUMBER = re.compile(r"(?:\b(?:task|dev)\s*#?\s*|#)(\d{1,18})\b", re.I)


def _mentions(text, identifier):
    """Match a bot slug or display name as a distinct identifier, not a substring of another word."""
    return bool(identifier and re.search(r"(?<![\w])" + re.escape(str(identifier)) + r"(?![\w])", text, re.I))


def from_updates(conversation, message):
    """Whether this turn's message is the person's own, sent from the Updates chat into their own chat."""
    conversation, message = conversation or {}, message or {}
    refs = message.get("refs") or {}
    owner = str(conversation.get("owner_id") or "")
    return (conversation.get("scope") == rooms.PERSONAL and H.is_human(owner) and not conversation.get("closed_at")
            and message.get("sender_id") == owner and refs.get("assistant") is True and refs.get("updates") is True)


def _room_bot(settings, conversation, bot):
    key = (conversation or {}).get("room_key")
    return bot == settings.assistant_bot and key == rooms.ASSISTANT_ROOM


def opens_all(c, auth, who, text, access=None):
    """Whether `who` may open every task, chat and bot that `text` links (`#/task/<id>`, `#/bot/<slug>`, a chat or
    conversation link) or names by id. One that no longer exists leaves nothing to leak and does not count."""
    text = str(text or "")
    access = auth.bot_accesses(c, who) if access is None else access
    tasks, numbers, convs = set(), set(), set()
    for kind, ref in LINKS.findall(text or ""):
        kind = kind.lower()
        if kind == "bot":
            ref = (H.resolve_bot(c, ref) or {}).get("id", ref)
            if ref in access and not access[ref]["read"]:
                return False
        elif kind in ("task", "tasks"):
            (numbers if ref.isdigit() else tasks).add(ref)
        else:
            convs.add(ref)
    # Short IDs and #123 / "task 123" / "Dev 123" references appear in update prose too, not
    # just in links. Resolve only references that match a task so ordinary numbers stay harmless.
    tasks.update(BARE_SHORT_TASK.findall(text))
    numbers.update(TASK_NUMBER.findall(text))
    for ref in UUID.findall(text or ""):
        ref = ref.lower()
        if c.execute("SELECT 1 FROM tasks WHERE id=?", (ref,)).fetchone():
            tasks.add(ref)
            continue
        row = c.execute("SELECT conversation_id FROM messages WHERE id=?", (ref,)).fetchone()
        convs.add(row[0] if row else ref)
    hidden_bots = {slug for slug, level in access.items() if not level["read"]}
    if hidden_bots:
        marks = ",".join("?" * len(hidden_bots))
        for row in c.execute(f"SELECT slug, display_name FROM bots WHERE id IN ({marks})", sorted(hidden_bots)):
            if _mentions(text, row["slug"]) or _mentions(text, row["display_name"]):
                return False
    visible = auth.task_sql(c, who)
    for ref in tasks:
        if not SHORT_TASK.match(ref) and not UUID.fullmatch(ref):
            continue
        rows = c.execute("SELECT id FROM tasks WHERE id=? OR id LIKE ?",
                         (ref, ref + "%" if SHORT_TASK.match(ref) else ref)).fetchall()
        for row in rows:
            if not c.execute(f"SELECT 1 FROM tasks WHERE id=? AND ({visible})", (row["id"],)).fetchone():
                return False
    for number in numbers:
        rows = c.execute("SELECT id FROM tasks WHERE number=?", (int(number),)).fetchall()
        for row in rows:
            if not c.execute(f"SELECT 1 FROM tasks WHERE id=? AND ({visible})", (row["id"],)).fetchone():
                return False
    for ref in convs:
        if not c.execute("SELECT 1 FROM conversations WHERE id=?", (ref,)).fetchone():
            continue
        try:
            auth.conversation(c, who, ref)
        except Problem:
            return False
    return True


def _clip(text):
    text = "\n".join(line.rstrip() for line in str(text or "").strip().splitlines() if line.strip())
    return text if len(text) <= BODY_MAX else text[:BODY_MAX].rsplit(" ", 1)[0] + "…"


def recent(c, auth, who, at=None, *, bot=None, attempt_id=""):
    """The recent daily and weekly updates `who` may read, newest first, each with its default rank and its link.
    Only what their Updates page would show them (bots they may Read, every person's, the meetings flag), less any
    update that links or names a task, chat or bot they may not open."""
    readable = U.visible_authors(c, auth, who)
    if not readable:
        return []
    access = auth.bot_accesses(c, who)
    if bot:
        # A chat turn gets no more update sources than both its person and its acting bot can read.
        # `who` has the person's rights plus task_actor; bot access is checked separately as the bot.
        from .auth import Identity
        acting = Identity("bot:" + bot, "bot", attempt_id=attempt_id)
        bot_access = auth.bot_accesses(c, acting)
        readable = {author for author in readable if U.is_person(author)
                    or bot_access.get(author, {}).get("read", False)}
        access = {slug: {"read": bool(level.get("read") and bot_access.get(slug, {}).get("read"))}
                  for slug, level in access.items()}
    if not readable:
        return []
    today = U.today(at)
    since = {"daily": H.shift(today + "T12:00:00.000000Z", days=-(DAILY_DAYS - 1))[:10],
             "weekly": H.shift(today + "T12:00:00.000000Z", days=-(WEEKLY_DAYS - 1))[:10]}
    clause, values = U._only(sorted(readable))
    rows = c.execute(
        f"SELECT u.id, u.bot, u.kind, u.day, u.body, u.created_at, u.updated_at, r.read_at FROM updates u "
        "LEFT JOIN update_reads r ON r.update_id=u.id AND r.actor=? "
        f"WHERE {clause} AND {U._kept(U.meeting_reader(who))} AND ((u.kind='daily' AND u.day>=?) "
        "OR (u.kind='weekly' AND u.day>=?)) AND u.day<=? ORDER BY u.created_at DESC LIMIT ?",
        (who.actor, *values, since["daily"], since["weekly"], today, ITEMS_MAX * 3)).fetchall()
    rows = [r for r in rows if opens_all(c, auth, who, r["body"], access)][:ITEMS_MAX]
    if not rows:
        return []
    authors = sorted({r["bot"] for r in rows})
    marks = ",".join("?" * len(authors))
    goals = {}
    for g in c.execute(f"SELECT owner_id, title FROM goals WHERE owner_id IN ({marks}) AND "
                       "(status IS NULL OR status NOT IN ('done','dropped')) ORDER BY rank",
                       [U.author_actor(a) for a in authors]):
        goals.setdefault(g["owner_id"], []).append(g["title"])
    waiting = {r[0] for r in c.execute(
        f"SELECT DISTINCT requester_id FROM tasks WHERE owner_id=? AND requester_id IN ({marks}) "
        "AND status IN ('open','doing','waiting')", [who.actor, *[U.author_actor(a) for a in authors]])}
    person = H.human(c, H.actor_id(who.actor)) or {}
    first = str(person.get("name") or "").split()[:1]
    named = re.compile(r"\b" + re.escape(first[0]) + r"\b", re.I) if first and len(first[0]) > 1 else None
    names = {r["id"]: r["display_name"] or r["slug"] for r in c.execute(
        f"SELECT id, slug, display_name FROM bots WHERE id IN ({marks})", authors)}
    people = {}
    for a in authors:
        if U.is_person(a):
            row = H.human(c, H.actor_id(a)) or {}
            people[a] = row.get("name") or H.actor_id(a)
    items = []
    for r in rows:
        actor = U.author_actor(r["bot"])
        own_goals = goals.get(actor, [])[:3]
        waits = actor in waiting or bool(named and named.search(r["body"] or ""))
        items.append({"id": r["id"], "author": people.get(r["bot"]) or names.get(r["bot"], r["bot"]),
                      "kind": r["kind"], "day": r["day"], "posted": r["updated_at"] or r["created_at"],
                      "read": bool(r["read_at"]), "goals": own_goals, "waits_on_you": waits,
                      "text": _clip(r["body"]),
                      "link": f"#/updates?kind={r['kind']}&day={r['day']}&update={r['id']}"})
    order = sorted(items, key=lambda i: (bool(i["goals"]), i["waits_on_you"], i["posted"]), reverse=True)
    for n, item in enumerate(order, 1):
        item["rank"] = n
    return order


def for_turn(c, auth, settings, conversation, bot, message, attempt_id="", at=None):
    """What a turn is handed about Updates: None unless the person sent this message from the Updates chat into their
    own chat with their Assistant. Read as that
    person, with exactly the rights their turn has (backend/auth.py chat_person), so a private task, chat or bot they
    may not open never reaches the bot."""
    if not from_updates(conversation, message) or not _room_bot(settings, conversation, bot):
        return None
    who = auth.chat_person(c, conversation["owner_id"], bot, attempt_id)
    if who is None:
        return None
    # What the turn may open, which is never more than the person may: a private task of theirs the bot is no party
    # to stays out too (a claim that named it would be refused, task_privacy's response check).
    return {"today": U.today(at), "updates": recent(c, auth, who, at, bot=bot, attempt_id=attempt_id)}
