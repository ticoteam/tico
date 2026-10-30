"""All-pairs plan for the coverage matrix (TESTING.md L2b). Plain Python, no server, no dependencies.

A row takes one value from each dimension. A pair of values is required when some valid row holds both; SKIP lists
the rules that make a row invalid (CONTRACT refuses it, or it cannot be built). The plan is a greedy cover: each step
adds the best of a random sample of valid rows, or one row built to cover a chosen uncovered pair, whichever covers
the most uncovered pairs. The seed fixes every draw, so the same seed gives the same plan."""
import functools
import itertools
import random
from collections import Counter

DIMENSIONS = {
    "trigger": ["email", "slack", "task", "schedule", "webhook", "listening", "event"],
    "first": ["none", "filter", "decide", "filter+decide", "find"],
    "shape": ["straight", "two-paths", "four-paths", "nested"],
    "worker": ["none", "llm-assistant", "llm-bot", "bot-turn"],
    "action": ["archive", "leave-for-you", "draft-reply", "send-reply", "create-task", "label", "forward",
               "wake-bot", "note", "post-update", "call-webhook", "assign"],
    "condition": ["is", "is-min", "is-not", "is-not-min", "gte", "lte", "contains", "matches"],
    "outcome": ["ok", "step-fails", "spend-cap", "switched-off", "no-match"],
}
NAMES = list(DIMENSIONS)
CHAT = ("email", "slack")
MAIL = ("archive", "leave-for-you", "draft-reply", "send-reply", "label", "forward")
MIN = ("is-min", "is-not-min")
SCORE = ("gte", "lte")


def decides(c):
    return c["first"] in ("decide", "filter+decide")


def gated(c):
    """A condition needs something to hold it: a Filter step, or a path shape."""
    return c["first"] in ("filter", "filter+decide") or c["shape"] != "straight"


# Each rule names why a row is invalid. A row that breaks any rule is never generated, so no pair is required that
# only such rows hold.
SKIP = [
    ("find pulls from a list, so only a schedule can run it",
     lambda c: c["first"] == "find" and c["trigger"] != "schedule"),
    ("mail and chat actions need an email or Slack trigger",
     lambda c: c["action"] in MAIL and c["trigger"] not in CHAT),
    ("min needs a Decide step to take a confidence from",
     lambda c: c["condition"] in MIN and not decides(c)),
    ("score comparisons need a Decide step",
     lambda c: c["condition"] in SCORE and not decides(c)),
    ("a condition needs a Filter or a path shape to hold it",
     lambda c: not gated(c)),
    ("a schedule has no input that can fail a condition",
     lambda c: c["trigger"] == "schedule" and c["outcome"] == "no-match"),
    ("step-fails needs a Decide step (the judge is the step that fails)",
     lambda c: c["outcome"] == "step-fails" and not decides(c)),
    ("spend-cap stops only LLM and Bot turn steps",
     lambda c: c["outcome"] == "spend-cap" and c["worker"] == "none"),
]


def valid(c):
    return not any(rule(c) for _, rule in SKIP)


def pairs_of(row):
    """Every pair of (dimension, value) in a row, in dimension order."""
    return [((a, row[a]), (b, row[b])) for a, b in itertools.combinations(NAMES, 2)]


def _completions(fixed, rest):
    for values in itertools.product(*(DIMENSIONS[n] for n in rest)):
        yield {**fixed, **dict(zip(rest, values))}


@functools.lru_cache(maxsize=None)
def feasible_pairs():
    """(pairs some valid row holds, pairs no valid row holds). Only the first set is required of the plan."""
    feasible, none_valid = set(), set()
    for a, b in itertools.combinations(NAMES, 2):
        rest = [n for n in NAMES if n not in (a, b)]
        for va in DIMENSIONS[a]:
            for vb in DIMENSIONS[b]:
                hit = any(valid(c) for c in _completions({a: va, b: vb}, rest))
                (feasible if hit else none_valid).add(((a, va), (b, vb)))
    return frozenset(feasible), frozenset(none_valid)


def _targeted(pair, rng):
    """A valid row that holds `pair`: random completions first, then the first valid one (the pair is feasible)."""
    (a, va), (b, vb) = pair
    rest = [n for n in NAMES if n not in (a, b)]
    for _ in range(400):
        row = {a: va, b: vb, **{n: rng.choice(DIMENSIONS[n]) for n in rest}}
        if valid(row):
            return row
    return next(c for c in _completions({a: va, b: vb}, rest) if valid(c))


def plan(seed, tries=100):
    """The rows of the all-pairs plan for `seed`. Every feasible pair appears in at least one row."""
    rng = random.Random(seed)
    uncovered = set(feasible_pairs()[0])
    rows = []
    while uncovered:
        target = sorted(uncovered)[rng.randrange(len(uncovered))]
        sample = [_targeted(target, rng)]
        for _ in range(tries):
            row = {n: rng.choice(DIMENSIONS[n]) for n in NAMES}
            if valid(row):
                sample.append(row)
        best = max(sample, key=lambda row: len(uncovered.intersection(pairs_of(row))))
        rows.append(best)
        uncovered -= set(pairs_of(best))
    return rows


def coverage(rows):
    """How many rows hold each value, and the (dimension, value) pairs no row holds."""
    seen = {n: {v: 0 for v in DIMENSIONS[n]} for n in NAMES}
    for row in rows:
        for n in NAMES:
            seen[n][row[n]] += 1
    unseen = [(n, v) for n in NAMES for v, count in seen[n].items() if count == 0]
    return seen, unseen


def lower_bound(need):
    """Each row holds one value pair per dimension pair, so the most required pairs any two dimensions have is a floor
    on the number of rows."""
    per = Counter((a, b) for (a, _), (b, _) in need)
    return max(per.values(), default=0)
