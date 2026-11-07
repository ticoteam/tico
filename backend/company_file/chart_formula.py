# pyright: strict
"""The org chart formula: the chart a company's picks make, the same way every time.

`build(inputs)` returns `{"employees": [...], "uncovered": [...]}`. Each employee is a company file `employees` row: id,
template, title, department, reports_to, team, reason and start (true). `uncovered` names what no seat owns. A pure
function: no database, no network, no model. The data (teams, titles, templates, reasons, channels, defaults, limits) is
chart_formula.json; this file holds the rules.

inputs (`inputs_from_draft` makes them from a setup draft):
  segments     the picked segments' names, in order
  channels     channel keys; none picked means the default list
  initiatives  the strategy's bets: {"id", "statement", "name" (optional), "segment" (optional)}
  industry     up to three {"title", "template"} Owners for the business type; the template must be a card
removed: the ids of employees the visitor took off the chart. Their direct reports move up to the seat above them, and
`uncovered` is worked out without them.

What the chart keeps: a title is unique (the second of a repeat gets " 2", the third " 3"), and an id is a slug of the
title. Channel seats follow the channel list's order, not the order they were picked in. An initiative tagged to a
picked segment is owned by that segment's Product Owner and has no seat of its own; a product owner's reason names those
initiatives, or counts them when the names would not fit a reason. reports_to is null at the top of the chart: that seat
reports to the company's owner (draft.manager_of).
"""

import json
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Required, TypedDict, cast

from . import draft as D


class Phrases(TypedDict):
    one: str
    many: str
    count: str
    join: str
    last: str


class Channel(TypedDict):
    key: str
    title: str
    template: str
    department: str
    reason: str


class Spec(TypedDict, total=False):
    """One seat in a team: a singleton (no `per`), or made once per segment, channel, initiative or industry Owner."""

    key: Required[str]
    reports_to: Required[str | None]
    per: str
    covers: str
    title: str
    template: str
    department: str
    reason: str


class Team(TypedDict):
    team: str
    seats: list[Spec]


class Coverage(TypedDict):
    seat: str
    name: str


class Formula(TypedDict):
    about: str
    version: int
    label_length: int
    title_length: int
    industry_max: int
    channels_default: list[str]
    phrases: Phrases
    channels: list[Channel]
    teams: list[Team]
    coverage: list[Coverage]


class Segment(TypedDict):
    name: str
    label: str


class Initiative(TypedDict):
    label: str
    segment: str
    segment_index: int | None


class Industry(TypedDict):
    title: str
    template: str


# What a seat covers: ("product", i) for picked segment i, ("initiative", j) for initiative j, ("seat", key) and so on.
Tag = tuple[str, int | str]


class Seat(TypedDict):
    id: str
    template: str
    title: str
    department: str
    reports_to: str | None
    team: str
    reason: str
    tags: list[Tag]


class Employee(TypedDict):
    id: str
    template: str
    title: str
    department: str
    reports_to: str | None
    team: str
    reason: str
    start: bool


class Result(TypedDict):
    employees: list[Employee]
    uncovered: list[str]


class Inputs(TypedDict):
    segments: list[str]
    channels: list[str]
    initiatives: list[dict[str, str]]
    industry: list[dict[str, str]]


FORMULA = cast(Formula, json.loads((Path(__file__).resolve().parent / "chart_formula.json").read_text()))
LABEL_LENGTH = FORMULA["label_length"]
TITLE_LENGTH = FORMULA["title_length"]
INDUSTRY_MAX = FORMULA["industry_max"]
DEFAULT_CHANNELS = tuple(FORMULA["channels_default"])
CHANNELS = {channel["key"]: channel for channel in FORMULA["channels"]}
# draft.py's data is loaded from JSON, so its values come in untyped; they are cast here, once.
REASON_LENGTH = cast(int, D.LIMITS["reason"])
TEMPLATES = cast(set[str], D.TEMPLATES)
ROLE_DEPARTMENT = {role["slug"]: role["department"] for role in cast(list[dict[str, str]], D.ROLES)}
slugify = cast(Callable[[str], str], D.slugify)
# The marks a cut label does not keep at its end: "Photos are the input." is "Photos are the input".
TRAILING = ".,;:!?-–—'\"‘’“”"


def clean(text: object) -> str:
    """`text` with its words joined by single spaces: no line breaks, tabs or runs of blanks."""
    return " ".join(str(text).split())


def cut(text: str, limit: int = LABEL_LENGTH) -> str:
    """`text` on one line, at most `limit` characters, cut at a word boundary, with no trailing punctuation."""
    words = clean(text)
    if len(words) > limit:
        head = words[: limit + 1]
        words = head[: head.rfind(" ")] if " " in head else words[:limit]
    while words and words[-1] in TRAILING:
        words = words[:-1]
    return words


def _items(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _part(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _field(item: object, key: str) -> object:
    return _part(item).get(key)


def _text(item: object, key: str) -> str:
    value = _field(item, key)
    return clean(value) if isinstance(value, str) else ""


def _segments(value: object) -> list[Segment]:
    """The picked segments, in order: {"name", "label"}. A segment with no name is dropped."""
    out: list[Segment] = []
    for name in _items(value):
        label = cut(name) if isinstance(name, str) else ""
        if label:
            out.append({"name": clean(name), "label": label})
    return out


def _channels(value: object) -> list[str]:
    """The picked channel keys in the channel list's order. Unknown keys and repeats are dropped; none picked is the
    default list."""
    picked = {key for key in _items(value) if isinstance(key, str)}
    return [key for key in CHANNELS if key in picked] or list(DEFAULT_CHANNELS)


def _initiatives(value: object) -> list[Initiative]:
    """The bets as initiatives: {"label", "segment"}. A label is the bet's name, else its statement, cut to the label
    length. A bet with neither is dropped."""
    out: list[Initiative] = []
    for item in _items(value):
        label = cut(_text(item, "name") or _text(item, "statement"))
        if label:
            out.append({"label": label, "segment": _text(item, "segment"), "segment_index": None})
    return out


def _industry(value: object) -> list[Industry]:
    """Up to INDUSTRY_MAX business-type Owners. An entry with no title, or a template Tico has no card for, is dropped."""
    out: list[Industry] = []
    for item in _items(value):
        template = _field(item, "template")
        title = _text(item, "title")
        if title and isinstance(template, str) and template in TEMPLATES:
            out.append({"title": title, "template": template})
    return out[:INDUSTRY_MAX]


def _initiative_phrase(labels: list[str]) -> str:
    """What a product owner's reason says about its initiatives: " and the 'A' and 'B' initiatives", or "" for none."""
    if not labels:
        return ""
    phrases = FORMULA["phrases"]
    quoted = ["'%s'" % label for label in labels]
    if len(quoted) == 1:
        names = quoted[0]
        pattern = phrases["one"]
    else:
        names = phrases["join"].join(quoted[:-1]) + phrases["last"] + quoted[-1]
        pattern = phrases["many"]
    return pattern.replace("{names}", names)


def _topic(title: str) -> str:
    """An industry Owner's topic for its reason: "Scheduling Owner" is "scheduling", "Insurance Billing Owner" is
    "insurance billing". A word with a capital after its first letter, such as "HR", keeps its capitals."""
    topic = title[: -len(" Owner")] if title.endswith(" Owner") else title
    return " ".join(word.lower() if word[1:].islower() else word for word in topic.split())


class _Names:
    """The titles and ids of one chart, each one once. A repeated title gets " 2", " 3" ...; a repeated id gets "-2"."""

    def __init__(self) -> None:
        self.titles: set[str] = set()
        self.ids: set[str] = set()

    def title(self, base: str) -> str:
        n = 1
        while True:
            suffix = "" if n == 1 else " %d" % n
            head = base if len(base) + len(suffix) <= TITLE_LENGTH else cut(base, TITLE_LENGTH - len(suffix))
            title = head + suffix
            if title.casefold() not in self.titles:
                self.titles.add(title.casefold())
                return title
            n += 1

    def id(self, title: str) -> str:
        base = slugify(title)[:60].rstrip("-") or "x"
        seat_id, n = base, 1
        while seat_id in self.ids:
            n += 1
            seat_id = "%s-%d" % (base, n)
        self.ids.add(seat_id)
        return seat_id


def _seats(
    segments: list[Segment], channels: list[str], initiatives: list[Initiative], industry: list[Industry]
) -> list[Seat]:
    """Every seat the picks make, in output order (see `Seat`); its tags say what it covers (see `build`)."""
    names = _Names()
    seats: list[Seat] = []
    by_key: dict[str, str] = {}  # a team's singleton seat key (chart_formula.json) -> its id, for the seats under it

    def add(
        team: str, spec: Spec, base_title: str, template: str, department: str, reason: str, tags: list[Tag]
    ) -> Seat:
        title = names.title(base_title)
        boss = spec.get("reports_to")
        seat: Seat = {
            "id": names.id(title),
            "template": template,
            "title": title,
            "department": department,
            "reports_to": by_key.get(boss) if boss else None,
            "team": team,
            "reason": reason,
            "tags": tags,
        }
        seats.append(seat)
        return seat

    for team in FORMULA["teams"]:
        for spec in team["seats"]:
            per = spec.get("per")
            if per is None:
                seat = add(
                    team["team"],
                    spec,
                    spec.get("title", ""),
                    spec.get("template", ""),
                    spec.get("department", ""),
                    spec.get("reason", ""),
                    [("seat", spec.get("key", ""))],
                )
                by_key[spec.get("key", "")] = seat["id"]
            elif per == "segment":
                covers = spec.get("covers", "")
                for i, segment in enumerate(segments):
                    # A product owner owns its segment's initiatives; a marketing owner owns only the segment.
                    tagged = (
                        [j for j, item in enumerate(initiatives) if item["segment_index"] == i]
                        if covers == "product"
                        else []
                    )
                    phrase = _initiative_phrase([initiatives[j]["label"] for j in tagged])
                    reason = spec.get("reason", "").replace("{label}", segment["label"])
                    if len(reason.replace("{initiatives}", phrase)) > REASON_LENGTH:
                        # Long names would not fit a reason; the count says the same in fewer characters.
                        phrase = FORMULA["phrases"]["count"].replace("{count}", str(len(tagged)))
                    tags: list[Tag] = [(covers, i)]
                    tags.extend(("initiative", j) for j in tagged)
                    add(
                        team["team"],
                        spec,
                        spec.get("title", "").replace("{label}", segment["label"]),
                        spec.get("template", ""),
                        spec.get("department", ""),
                        reason.replace("{initiatives}", phrase),
                        tags,
                    )
            elif per == "channel":
                for key in channels:
                    channel = CHANNELS[key]
                    add(
                        team["team"],
                        spec,
                        channel["title"],
                        channel["template"],
                        channel["department"],
                        channel["reason"],
                        [("channel", key)],
                    )
            elif per == "initiative":
                for j, item in enumerate(initiatives):
                    if item["segment_index"] is None:
                        add(
                            team["team"],
                            spec,
                            spec.get("title", "").replace("{label}", item["label"]),
                            spec.get("template", ""),
                            spec.get("department", ""),
                            spec.get("reason", "").replace("{label}", item["label"]),
                            [("initiative", j)],
                        )
            elif per == "industry":
                for item in industry:
                    add(
                        team["team"],
                        spec,
                        spec.get("title", "").replace("{title}", item["title"]),
                        item["template"],
                        ROLE_DEPARTMENT[item["template"]],
                        spec.get("reason", "").replace("{topic}", _topic(item["title"])),
                        [],
                    )
    return seats


def build(inputs: object, removed: Iterable[str] = ()) -> Result:
    """The chart for `inputs` (see the module docstring), without the seats named in `removed`.

    Each seat's tags say what it covers: ("product", i) and ("marketing", i) for picked segment i, ("channel", key) for a
    channel, ("initiative", j) for initiative j (a product owner's tags include its tagged initiatives), and ("seat", key)
    for a team's singleton seat. `uncovered` lists the things with no tag left among the kept seats."""
    source = _part(inputs)
    segments = _segments(source.get("segments"))
    channels = _channels(source.get("channels"))
    initiatives = _initiatives(source.get("initiatives"))
    industry = _industry(source.get("industry"))
    for item in initiatives:
        # The picked segment an initiative is tagged to: the first with that name, ignoring case; else none.
        item["segment_index"] = next(
            (i for i, segment in enumerate(segments) if segment["name"].casefold() == item["segment"].casefold()),
            None,
        )
    seats = _seats(segments, channels, initiatives, industry)
    gone = set(removed)
    by_id = {seat["id"]: seat for seat in seats}
    kept = [seat for seat in seats if seat["id"] not in gone]
    for seat in kept:
        boss = seat["reports_to"]
        while boss is not None and boss in gone:  # a removed manager: the seat moves up to that manager's line
            boss = by_id[boss]["reports_to"]
        seat["reports_to"] = boss
    covered = {tag for seat in kept for tag in seat["tags"]}
    things: list[tuple[Tag, str]] = []
    for i, segment in enumerate(segments):
        things.append((("product", i), "%s product" % segment["label"]))
        things.append((("marketing", i), "%s marketing" % segment["label"]))
    for key in channels:
        things.append((("channel", key), CHANNELS[key]["title"].removesuffix(" Owner")))
    for j, item in enumerate(initiatives):
        things.append((("initiative", j), item["label"]))
    for item in FORMULA["coverage"]:
        things.append((("seat", item["seat"]), item["name"]))
    employees: list[Employee] = [
        {
            "id": seat["id"],
            "template": seat["template"],
            "title": seat["title"],
            "department": seat["department"],
            "reports_to": seat["reports_to"],
            "team": seat["team"],
            "reason": seat["reason"],
            "start": True,
        }
        for seat in kept
    ]
    return {"employees": employees, "uncovered": [text for tag, text in things if tag not in covered]}


def inputs_from_draft(draft: object, industry: Iterable[Mapping[str, str]] = ()) -> Inputs:
    """The chart's inputs for a setup draft: its picked segments (`ours`) in order, its channels (the default list when
    none is picked) and its bets as initiatives. `industry` is the business type's Owners, which a draft does not hold."""
    strategy = _part(_part(draft).get("strategy"))
    segments = _items(_part(strategy.get("market")).get("segments"))
    return {
        "segments": [_text(segment, "name") for segment in segments if _field(segment, "ours") is True],
        "channels": [key for key in _items(strategy.get("channels")) if isinstance(key, str)] or list(DEFAULT_CHANNELS),
        "initiatives": [
            {
                "id": _text(bet, "id"),
                "statement": _text(bet, "statement"),
                "name": _text(bet, "name"),
                "segment": _text(bet, "segment"),
            }
            for bet in _items(strategy.get("bets"))
        ],
        "industry": [dict(item) for item in industry],
    }
