# pyright: strict
"""The owner's plate and the asks a bot puts to her. Head of Engineering asks the owner, each sweep, what to do with a
ticket that sits with no move; it reports to her, one level below. A builder asks her directly too, two levels below,
which the rules must route or refuse (#2879, merged). Three things must hold: the owner's plate stays under ten items,
no ask comes from a bot more than one level away, and no question is asked twice, nor one whose default is reversible
(a bot goes ahead on that step itself, so there is nothing to ask). Duplicates and reversible-default asks are #2880,
still open: on main they are filed, so their checks fail until it lands."""

from evals.factory.scenarios import Scenario, standard, tickets

PLATE_LIMIT = 10
GAP_ASKS = "#2880"
GAP_LEVEL = "#2879"


def level(run, slug):
    """How many reporting lines a bot is below the owner: 1 for a bot that reports to her, 2 for one under such a bot."""
    hops, who = 0, slug
    while who != "ana" and hops < 10:
        who = run.w.manager_of.get(who, "ana")
        hops += 1
    return hops


def plate_under(run):
    peak = run.plate_peak
    return peak < PLATE_LIMIT, f"the owner's plate peaked at {peak} items (under {PLATE_LIMIT} required)"


def no_ask_from_far(run):
    """No ask from a bot two or more levels below the owner is filed (the rule routes it through its manager)."""
    asks = [a for a in run.w.asks if a["status"] < 300]
    far = [a for a in asks if level(run, a["bot"]) > 1]
    tried = [a for a in run.w.asks if level(run, a["bot"]) > 1]
    return not far, f"{len(far)} filed, {len(tried) - len(far)} refused, from bots two levels down"


def no_duplicate_asks(run):
    """No question is put to the owner twice while the first still waits on her."""
    filed = [a for a in run.w.asks if a["status"] < 300]
    seen: dict[str, int] = {}
    for a in filed:
        seen[a["text"]] = seen.get(a["text"], 0) + 1
    dupes = sum(n - 1 for n in seen.values() if n > 1)
    return dupes == 0, f"{dupes} duplicate asks of {len(filed)} filed"


def no_reversible_asks(run):
    """No ask whose default is reversible is put to the owner: the bot goes ahead on that step itself."""
    reversible = [a for a in run.w.asks if a["status"] < 300 and a["reversible"]]
    return not reversible, f"{len(reversible)} asks with a reversible default filed"


SCENARIOS = [
    Scenario(
        "owner_plate",
        "Head of Engineering asks the owner about each ticket that sits with no move, every sweep; builders ask her too.",
        setup=lambda run, w, team: tickets(run, w, 6),
        styles={
            "builder": {"leave_doing": 0.4, "asks": 0.5},
            "lead": {"asks": 1.0, "reversible": 0.5},
        },
        hours=12,
        checks=standard(
            ("the owner's plate stays under ten items", GAP_ASKS, plate_under),
            ("no ask from a bot more than one level away", GAP_LEVEL, no_ask_from_far),
            ("no question asked twice", GAP_ASKS, no_duplicate_asks),
            ("no ask whose default is reversible", GAP_ASKS, no_reversible_asks),
        ),
    ),
]
