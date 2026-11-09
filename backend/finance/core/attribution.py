"""Attribution, the pure part: which teammates and teams a posted P&L line counts for, and with what share.

A line's own tags say who it counts for (`team`, `teammate`). An attribution replaces those two for a share of the
line: `splits` name each part's teammate and/or team with `share_bps` (basis points, 1..10000, at most MAX_PARTS parts,
total at most 10000). What the splits leave over is unattributed (team and teammate empty). Each part's amount is
allocated by largest remainder, so the parts always sum exactly to the amount (cents), and a partial amount (a
settlement's share of a bill line) is spread by the same weights.

Precedence (decided in finance/store/attribution.py): a person's or bot's override > a matching rule > the line's tags.
No splits means the line counts as its tags, exactly as before attribution existed: `parts` then returns one part,
the line's tags and amount, with count 1.

Every rollup row a line writes (accrual in store/ledger.py, cash in documents.py and posting.py) goes through
`parts` or `spread` here, so the reports and budgets that read the rollups follow the same shares.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

WHOLE = 10000  # a line's shares add up to this many basis points
MAX_PARTS = 10


@dataclass(frozen=True)
class Split:
    """One part of a line: a teammate (`human:<id>` or `bot:<id>`) and/or a team, and its share in basis points."""

    teammate: str
    team: str
    share_bps: int


@dataclass(frozen=True)
class Part:
    """What one rollup row takes from a line: its tags, its amount (cents) and its line count (1 on the largest part,
    so a count of lines still counts each line once)."""

    tags: dict[str, str]
    amount: int
    count: int


def allocate(amount: int, weights: Sequence[int]) -> list[int]:
    """Split `amount` by `weights` (non-negative integers with a positive sum) by largest remainder: the floors first,
    then one cent each to the parts with the largest remainders (the earlier part on a tie). The results sum to exactly
    `amount`. A negative amount splits as its magnitude and is negated, so a reversal's parts mirror the original's."""
    total = sum(weights)
    if total <= 0:
        raise ValueError("weights must add up to a positive number")
    sign = -1 if amount < 0 else 1
    magnitude = abs(amount)
    out = [magnitude * w // total for w in weights]
    rest = magnitude - sum(out)
    by_remainder = sorted(range(len(weights)), key=lambda i: (-(magnitude * weights[i] % total), i))
    for i in by_remainder[:rest]:
        out[i] += 1
    return [sign * x for x in out]


def _tags_for(tags: Mapping[str, str], teammate: str, team: str, team_of: Mapping[str, str]) -> dict[str, str]:
    """The line's tags with its team and teammate replaced by a part's. A teammate named without a team counts in the
    team it is in (as posting.with_team does for a tagged line)."""
    named_team = team or (team_of.get(teammate, "") if teammate else "")
    return {**dict(tags), "team": named_team, "teammate": teammate}


def weights(
    tags: Mapping[str, str], splits: Sequence[Split], team_of: Mapping[str, str]
) -> list[tuple[dict[str, str], int]]:
    """Each part's tags and weight in basis points, the unattributed rest last when there is one. No splits: the line
    as its tags, weight WHOLE."""
    if not splits:
        return [(dict(tags), WHOLE)]
    out = [(_tags_for(tags, s.teammate, s.team, team_of), s.share_bps) for s in splits]
    rest = WHOLE - sum(s.share_bps for s in splits)
    if rest > 0:
        out.append((_tags_for(tags, "", "", team_of), rest))
    return out


def spread(
    tags: Mapping[str, str], amount: int, splits: Sequence[Split], team_of: Mapping[str, str]
) -> list[tuple[dict[str, str], int]]:
    """A (possibly partial) amount of a line split by its weights: (tags, amount) for each part, zero amounts kept, so
    the amounts sum to `amount`. With no splits the amount stays on the line's tags."""
    parts = weights(tags, splits, team_of)
    shares = allocate(amount, [w for _, w in parts])
    return [(t, a) for (t, _), a in zip(parts, shares, strict=True)]


def parts(tags: Mapping[str, str], amount: int, splits: Sequence[Split], team_of: Mapping[str, str]) -> list[Part]:
    """The rollup rows a whole line writes. With no splits, one part: the tags, the amount and count 1, exactly the
    unattributed rollup. With splits, each part with an amount or a count; the count sits on the largest part."""
    if not splits:
        return [Part(dict(tags), amount, 1)]
    shares = spread(tags, amount, splits, team_of)
    weighted = weights(tags, splits, team_of)
    largest = max(range(len(weighted)), key=lambda i: (weighted[i][1], -i))
    out: list[Part] = []
    for i, ((part_tags, part_amount), _) in enumerate(zip(shares, weighted, strict=True)):
        count = 1 if i == largest else 0
        if part_amount or count:
            out.append(Part(part_tags, part_amount, count))
    return out


def shares_of(splits: Sequence[Split], amount: int) -> list[int]:
    """The amount each named split takes (the unattributed rest left out), largest remainder over the whole line, for a
    report of what an attribution comes to on a line."""
    if not splits:
        return []
    rest = WHOLE - sum(s.share_bps for s in splits)
    shares = allocate(amount, [s.share_bps for s in splits] + ([rest] if rest > 0 else []))
    return shares[: len(splits)]
