"""Helpers that other tests share, moved out of test_company_file.py so they import no test file."""

import datetime as dt
from backend import company_file as C


TODAY = dt.date(2026, 10, 17)


def started(**fields):
    """A draft as the page sends it after the first turn: the Everything model on the chart (no signals yet)."""
    turn = C.Turn(C.blank(), TODAY)
    return {**turn.finish(), **fields}


# ------------------------------------------------------------------------------- seat guard (Org chart models 2/7)
def tag(i):
    """A title suffix without digits: a title like "Account Manager 7" slugs to an id the key scrubber can cut."""
    return "".join("ABCDEFGHIJKLMNOPQRSTUVWXYZ"[d] for d in (i // 26, i % 26))


def chart(bots, people=1):
    """The starter chart grown (or cut) to `bots` bots and `people` people, through the chat's own actions."""
    draft = started()
    draft["bots"] = draft["bots"][:bots]
    roles = [r for r in C.ROLES if not r["core"]]  # built-ins stay one each
    turn = C.Turn(draft, TODAY)
    for i in range(bots - len(turn.draft["bots"])):
        role = roles[i % len(roles)]
        result = turn.apply(
            "bot_add",
            {
                "template": role["slug"],
                "title": "%s %s" % (role["name"], tag(i)),
                "reason": "The owner asked for one more.",
            },
        )
        assert result["ok"], result
    for i in range(people - len(turn.draft["people"])):
        assert turn.apply("person_set", {"name": "Person %s" % tag(i), "role": "Account Manager"})["ok"]
    draft = turn.finish()
    assert (len(draft["bots"]), len(draft["people"])) == (bots, people)
    return draft
