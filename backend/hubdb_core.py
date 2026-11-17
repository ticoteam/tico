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


def run_notice(sender, recipient):
    """Whether a message is Tico telling a bot something: its run's input, never a line in a chat a person reads. The
    stamp trigger gives it no seq (migration 20261116234549_run_notices_out_of_chats), so no timeline or unread count
    holds it; the bot reads it as the message its run starts from and in its own reads of the room."""
    return sender == KEEPER and is_bot(recipient)
