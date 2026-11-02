"""The task allocator: rules, not a bot, hand out the tickets waiting at a step that assigns them.

A step's flow may say `assign: {"pool": [bot slugs], "slots": n}` (backend/step_flow.py). A ticket at that step that
no bot of the pool owns yet goes, on each scheduler pass, to the pool bot with the fewest tickets in hand on that type
(ties: the fewest open tickets of any kind, then the slug), when it has a free slot. A ticket in hand is one the bot
owns past the assigning step and before the type's merged step: work, checks waiting, fixes. Only active bots with
free slots, completed onboarding and a profile that accepts the ticket take it; otherwise the ticket waits for
the next pass, oldest first.

The allocator only changes the owner; the owner moves the ticket on, and the step's own wake tells it."""
from . import hubdb as H
from . import step_flow as F

FINISHED = ("done", "closed", "declined")
DEFAULT_SLOTS = 4
MAX_SLOTS = 50


def clean(c, actor, raw):
    """A step's `assign` setting as stored, or refused."""
    if not isinstance(raw, dict) or set(raw) - {"pool", "slots"}:
        H.refuse(c, actor, "kind", "A step's assign takes pool (bot slugs) and slots")
    pool = []
    for value in raw.get("pool") or []:
        slug = H.actor_id(str(value or "").strip())
        if not slug or slug in pool:
            continue
        if not H.bot(c, slug):
            H.refuse(c, actor, "not-found", f"{slug} is not a bot, so it cannot be in a step's assign pool")
        pool.append(slug)
    if not pool:
        H.refuse(c, actor, "kind", "A step's assign needs at least one bot in its pool")
    slots = raw.get("slots", DEFAULT_SLOTS)
    if isinstance(slots, bool) or not isinstance(slots, int) or not 1 <= slots <= MAX_SLOTS:
        H.refuse(c, actor, "kind", f"A step's assign slots is a whole number from 1 to {MAX_SLOTS}")
    return {"pool": pool, "slots": slots}


def _available(c, slug):
    found = c.execute("SELECT coalesce(state,'') FROM bots WHERE slug=?", (slug,)).fetchone()
    return bool(found) and found[0] == "active" and not H._onboarding(c, slug)


def held_between(c, type_id, assign_step):
    """(after, before): a ticket is in its owner's hand at a step whose position is strictly between the assigning
    step and the type's merged step."""
    merged = F.merge_step(c, type_id)
    return assign_step["position"], merged["position"] if merged else 10 ** 6


def in_hand(c, slug, type_id, assign_step):
    """The bot's tickets on the type past the assigning step and before its merged step."""
    after, before = held_between(c, type_id, assign_step)
    return c.execute(
        "SELECT count(*) FROM tasks t JOIN task_steps s ON s.id=t.step_id WHERE t.owner=? AND t.type_id=? "
        f"AND t.status NOT IN ({','.join('?' * len(FINISHED))}) AND s.position>? AND s.position<?",
        ("bot:" + slug, type_id, *FINISHED, after, before)).fetchone()[0]


def _open(c, slug):
    return c.execute(f"SELECT count(*) FROM tasks WHERE owner=? AND status NOT IN ({','.join('?' * len(FINISHED))})",
                     ("bot:" + slug, *FINISHED)).fetchone()[0]


def allocate(c, at=None):
    """One pass: give each waiting ticket at an assigning step to the least busy pool bot with a free slot. Returns
    [(ticket id, bot slug)]."""
    if not F._ready(c):
        return []
    given = []
    steps = [dict(r) for r in c.execute("SELECT * FROM task_steps WHERE flow LIKE '%\"assign\"%'")]
    for step in steps:
        rule = F.of(step).get("assign")
        if not rule:
            continue
        pool = [s for s in rule["pool"] if _available(c, s)]
        owners = {"bot:" + s for s in rule["pool"]}
        waiting = c.execute(
            f"SELECT id, owner FROM tasks WHERE step_id=? AND status NOT IN ({','.join('?' * len(FINISHED))}) "
            "ORDER BY created_at, id", (step["id"], *FINISHED)).fetchall()
        for ticket_id, owner in waiting:
            if owner in owners:
                continue
            ticket = H.task(c, ticket_id)
            free = [(in_hand(c, s, step["type_id"], step), _open(c, s), s) for s in pool
                    if H.bot_reads_task(c, "bot:" + s, ticket)
                    and not H.task_taking_refusal(c, H.KEEPER, "bot:" + s, ticket["title"],
                                                 H.TR.parent_of(c, ticket_id))]
            free = [f for f in free if f[0] < rule["slots"]]
            if not free:
                continue
            slug = min(free)[2]
            H.task_update(c, H.KEEPER, ticket_id, owner="bot:" + slug,
                          note=f"Tico gave this to {slug}: the least busy of the {step['name']} pool "
                               f"({min(free)[0]} of {rule['slots']} slots in use).")
            H._task_event(c, ticket_id, H.KEEPER, "allocated", owner, "bot:" + slug, step["name"])
            given.append((ticket_id, slug))
    return given
