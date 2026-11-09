"""Who is acting: the actor helpers the hub modules share. Imports nothing from the hub."""

KEEPER = "keeper"


def bot_actor(slug):
    """The actor string for a bot. Pass its id: the actor is `bot:<id>`, which is the bot's slug only for a bot whose
    id is its slug."""
    return f"bot:{str(slug).strip()}"


def human_actor(pid):
    return f"human:{str(pid).strip()}"


def actor_kind(actor):
    """`bot`, `human`, `keeper`, or None for anything else."""
    a = str(actor or "").strip()
    if a == KEEPER:
        return KEEPER
    if a.startswith("bot:"):
        return "bot"
    if a.startswith("human:"):
        return "human"
    return None


def actor_id(actor):
    """The slug or person id inside an actor string; `keeper` for the keeper."""
    a = str(actor or "").strip()
    return a.split(":", 1)[1] if ":" in a else a


def is_bot(actor):
    return actor_kind(actor) == "bot"


def is_human(actor):
    return actor_kind(actor) == "human"
