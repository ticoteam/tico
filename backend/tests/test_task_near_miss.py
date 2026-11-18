"""A mistyped task id still gets the same "Did you mean" answer after the near-miss search reads only the ids SQL says
could be within two edits (backend/auth.py near_task_sql) and compares them in a band (_edit_distance). The resolver
as it was (helpers/task_near_miss.old_resolve_task) is the oracle."""

import itertools
import random
import sqlite3

from backend.auth import _edit_distance, near_task_sql
from backend.tests.helpers.task_near_miss import edited, old_edit_distance, old_resolve_task, outcome, random_ids, seed

VISIBLE = ["human:ana", "human:ben"]
CARA = ["human:cara"]  # Cara's private tasks: Eve, a plain member, may not read them


def answers(api, token, typed):
    """(new, old) answers to `typed` for this caller."""
    auth, store = api.app.state.auth, api.app.state.store
    who = auth.authenticate({"authorization": "Bearer " + token}, "/api/v2/tasks/x", "GET")
    with store.read() as c:
        new = outcome(auth.resolve_task, c, who, typed)
    with store.read() as c:
        old = outcome(old_resolve_task, auth, c, who, typed)
    return new, old


def named(answer, tid):
    return answer == ("id", tid) or (answer[0] == "not_found" and "Did you mean" in answer[2] and tid in answer[2])


def test_banded_distance_is_the_full_one():
    rng = random.Random(7)
    for _ in range(4000):
        alphabet = rng.choice(["ab", "ab-", "0123456789abcdef-"])
        a = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 14)))
        if a and rng.random() < 0.7:
            positions = rng.sample(range(len(a)), min(len(a), rng.randrange(4)))
            b = edited(a, [(rng.choice(["sub", "ins", "del"]), p) for p in positions], rng)
        else:
            b = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 14)))
        for limit in range(4):
            assert _edit_distance(a, b, limit) == old_edit_distance(a, b, limit), (a, b, limit)


def test_prefilter_keeps_every_id_two_edits_away():
    """Every one- and two-edit change of a stored id of 34 to 38 characters, at every position, still passes the SQL
    filter, so the Python comparison sees every task it would have suggested."""
    c = sqlite3.connect(":memory:")
    rng = random.Random(11)
    kinds = ["sub", "ins", "del"]
    for length in (34, 35, 36, 37, 38):
        stored = "".join(rng.choice("0123456789abcdef-") for _ in range(length))
        changes = [[(k, p)] for k in kinds for p in range(length + (k == "ins"))]
        changes += [
            [(k1, p1), (k2, p2)]
            for p1, p2 in itertools.combinations(range(length + 1), 2)
            for k1, k2 in itertools.product(kinds, repeat=2)
            if (k1 == "ins" or p1 < length) and (k2 == "ins" or p2 < length)
        ]
        changes += [[("ins", p), ("ins", p)] for p in range(length + 1)]
        changes += [[("sub", p), ("ins", p)] for p in range(length)]
        for change in changes:
            typed = edited(stored, change, rng)
            close, params = near_task_sql(typed)
            kept = c.execute("SELECT count(*) FROM (SELECT ? AS id) WHERE " + close, (stored, *params)).fetchone()[0]
            assert kept == 1, (stored, typed, change)
    # SQLite's substr and length stop at a NUL; instr does not.
    for stored in ["a" * 12 + "b" * 12 + "c" * 11 + "\0", "\0" + "d" * 35, "e" * 18 + "\0" + "f" * 17]:
        for change in ([("sub", 0), ("sub", 12)], [("sub", 0), ("sub", 30)], [("sub", 13), ("sub", 30)]):
            close, params = near_task_sql(edited(stored, change, rng))
            assert c.execute("SELECT count(*) FROM (SELECT ? AS id) WHERE " + close, (stored, *params)).fetchone()[0]


def test_near_misses_in_every_piece_are_still_suggested(api):
    rng = random.Random(3)
    ids = random_ids(200, rng)
    with api.app.state.store.transaction() as c:
        seed(c, ids, owners=VISIBLE)
    target = ids[17]
    ones = [("sub", p) for p in (0, 1, 11, 12, 13, 23, 24, 25, 34, 35)]
    ones += [("del", p) for p in (0, 1, 11, 12, 13, 23, 24, 25, 34, 35)]
    ones += [("ins", p) for p in (0, 1, 11, 12, 13, 23, 24, 25, 34, 35, 36)]
    pairs = [(0, 1), (11, 12), (23, 24), (0, 35), (12, 23), (10, 13), (22, 25), (34, 35), (5, 30), (2, 14)]
    twos = [[(k1, p1), (k2, p2)] for p1, p2 in pairs for k1, k2 in itertools.product(["sub", "ins", "del"], repeat=2)]
    for change in [[one] for one in ones] + twos:
        typed = edited(target, change, rng)
        assert old_edit_distance(typed, target, 2) is not None
        for token in ("ana-test", "eve-test"):
            new, old = answers(api, token, typed)
            assert new == old, (typed, change)
            assert named(new, target), (typed, change, new)
        # The way a bot types it: upper case, with stray spaces.
        new, old = answers(api, "eve-test", " " + typed.upper() + " ")
        assert new == old and named(new, target)


def test_three_edits_away_is_not_suggested(api):
    rng = random.Random(5)
    ids = random_ids(200, rng)
    with api.app.state.store.transaction() as c:
        seed(c, ids, owners=VISIBLE)
    target = ids[40]
    for positions in [(0, 12, 24), (11, 23, 35), (13, 14, 15), (0, 1, 2), (33, 34, 35), (5, 18, 30)]:
        typed = edited(target, [("sub", p) for p in positions], rng)
        assert old_edit_distance(typed, target, 3) == 3
        new, old = answers(api, "ana-test", typed)
        assert new == old == ("not_found", 404, "Task not found"), (typed, new)


def test_a_task_the_caller_cannot_read_is_never_named(api):
    rng = random.Random(9)
    typed = random_ids(1, rng)[0]
    hidden = edited(typed, [("sub", 20)], rng)  # one edit away, Cara's private task
    shown = edited(typed, [("sub", 3), ("sub", 30)], rng)  # two edits away, a company task
    lone = random_ids(1, rng)[0]  # Cara's, with nothing visible near it
    with api.app.state.store.transaction() as c:
        seed(c, random_ids(100, rng) + [shown], owners=VISIBLE)
        seed(c, [hidden, lone], owners=CARA, private=1)
    new, old = answers(api, "eve-test", typed)
    assert new == old
    assert named(new, shown) and hidden not in new[2]
    new, old = answers(api, "eve-test", edited(lone, [("del", 12)], rng))
    assert new == old == ("not_found", 404, "Task not found")


def test_suggestions_match_the_old_resolver_on_random_typos(api):
    """Clusters of ids a few edits apart (ties at the closest distance, more than three of them, upper-case and
    private ones), inserted in shuffled order, and typos of 0 to 3 edits of them: every answer, its suggestions and
    their order is the one the old resolver gave, for the owner and for a plain member."""
    rng = random.Random(13)
    centers = random_ids(40, rng)
    rows = [(tid, VISIBLE, 0) for tid in random_ids(150, rng)]
    for n, center in enumerate(centers):
        for _ in range(rng.randrange(1, 6)):
            variant = edited(center, [(rng.choice(["sub", "ins", "del"]), rng.randrange(36))], rng)
            if rng.random() < 0.1:
                variant = variant.upper()
            rows.append((variant, CARA if rng.random() < 0.3 else VISIBLE, 1 if rng.random() < 0.3 else 0))
        if n % 3:
            rows.append((center, VISIBLE, 0))
    unique = {}
    for tid, owners, private in rows:
        unique.setdefault(tid, (owners, private))
    order = list(unique.items())
    rng.shuffle(order)
    with api.app.state.store.transaction() as c:
        for tid, (owners, private) in order:
            seed(c, [tid], owners=owners, private=private)
    stored = [tid for tid, _ in order]
    typos = []
    for _ in range(150):
        base = rng.choice(centers + stored)
        typo = edited(
            base,
            [(rng.choice(["sub", "ins", "del"]), p) for p in rng.sample(range(len(base)), rng.randrange(4))],
            rng,
        )
        typos.append(typo.upper() if rng.random() < 0.15 else typo)
    typos += centers
    suggested = 0
    for typo in typos:
        for token in ("ana-test", "eve-test"):
            new, old = answers(api, token, typo)
            assert new == old, (token, typo)
            suggested += "Did you mean" in new[2] if new[0] == "not_found" else 0
    assert suggested > 50  # the set exercises suggestions, not only refusals


def test_a_tie_names_the_first_three_in_table_order(api):
    """Five tasks one edit from what was typed, inserted out of id order: the same three, in the same order."""
    rng = random.Random(17)
    typed = random_ids(1, rng)[0]
    tied = [edited(typed, [("sub", p)], rng) for p in (30, 2, 20, 9, 14)]
    stray = "a" * 12 + "b" * 12 + "c" * 11 + "\0"  # a NUL ends SQLite's length and substr, not instr
    with api.app.state.store.transaction() as c:
        seed(c, random_ids(50, rng) + tied + [stray], owners=VISIBLE)
    for token in ("ana-test", "eve-test"):
        new, old = answers(api, token, typed)
        assert new == old
        assert [tid for tid in tied if tid in new[2]] == tied[:3]
        assert new[2].index(tied[0]) < new[2].index(tied[1]) < new[2].index(tied[2])
        new, old = answers(api, token, edited(stray, [("sub", 0), ("sub", 12)], rng))
        assert new == old and named(new, stray)
