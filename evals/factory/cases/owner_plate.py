# pyright: strict
"""The owner's plate and the asks a bot puts to her. Head of Engineering asks the owner, each sweep, what to do with a
ticket that sits with no move; it reports to her, one level below. A builder asks her directly too, two levels below,
which #2879 lets through only about a task she handed it (reach.handed). Three things must hold: the owner's plate stays
under ten items, no ask comes from a bot two levels down about a task it was not handed, and no question is asked
twice, nor one whose default is reversible (a bot goes ahead on that step itself, so there is nothing to ask).
Duplicate and reversible-default asks are #2880 (merged): their checks guard it. The checks are regression guards now,
so none of them names a gap."""

from evals.factory.scenarios import Scenario, standard, tickets

PLATE_LIMIT = 10


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
    """Today's rule (#2879, reach.handed): an ask from a bot two or more levels below the owner is filed only about a task
    the person handed that bot (requester is the person, owner the bot). Any other such ask fails.
    TODO Phase 2: builder questions go to the manager (then no far ask is filed, handed or not)."""
    filed = [a for a in run.w.asks if a["status"] < 300 and level(run, a["bot"]) > 1]
    not_handed = [a for a in filed if not a["handed"]]
    return not not_handed, (
        f"{len(not_handed)} filed about tasks the person did not hand the bot, {len(filed) - len(not_handed)} about "
        "tasks it was handed"
    )


def no_duplicate_asks(run):
    """No question is put to the owner again while the first still waits on her. A repeat on the same topic is folded
    into the open question (`folded`), one card, so it does not count. A repeat filed anyway while the first still waits
    (`waiting`, read by the bot before it files) is the duplicate; one filed after the first was answered is a new question."""
    filed = [a for a in run.w.asks if a["status"] < 300 and not a["folded"]]
    dupes = [a for a in filed if a["waiting"]]
    folded = sum(1 for a in run.w.asks if a["status"] < 300 and a["folded"] and a["waiting"])
    return (
        not dupes,
        f"{len(dupes)} duplicate asks of {len(filed)} filed; {folded} repeats folded into a waiting question",
    )


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
            ("the owner's plate stays under ten items", None, plate_under),
            ("no ask from a bot two levels down about a task it was not handed", None, no_ask_from_far),
            ("no question asked twice", None, no_duplicate_asks),
            ("no ask whose default is reversible", None, no_reversible_asks),
        ),
    ),
]
