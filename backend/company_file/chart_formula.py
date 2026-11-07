# pyright: strict
# The cross-language rules. The site's JS port must match this file byte for byte, so each rule is stated the way a JS
# port can do it too (chart_formula.json's "rules" says the same in words):
#   length:  a count of Unicode code points (Python len; JS [...s].length), and a cut slices code points, never code units
#   space:   the ASCII whitespace [ \t\n\r\f\v]; each run is one space and none is kept at either end (draft.clean)
#   names:   compared by lower(), never casefold() (JS toLowerCase), and only to tell names apart (draft.fold)
#   ids:     slugs of a-z and 0-9 only: every other run of characters is one "-", none at either end, "x" if none is left
#   fill:    placeholders such as {label} are filled in one pass, so a name that contains braces is only text
"""The org chart formula: the chart a company's picks make, the same way every time.

`build(inputs, removed)` returns `{"employees": [...], "uncovered": [...]}`. Each employee is a company file `employees`
row: id, template, title, department, reports_to, team, reason and start (true). `uncovered` names what no seat owns. A
pure function: no database, no network, no model. The data (teams, titles, templates, reasons, channels, phrases, limits)
is chart_formula.json; this file holds the rules.

inputs (`inputs_from_draft` makes them from a setup draft):
  segments     the picked segments' names, in order
  channels     channel keys; none picked means the default list
  initiatives  the strategy's bets: {"id", "statement", "name" (optional), "segment" (optional)}
  industry     up to three {"title", "template"} Owners for the business type; the template must be an industry card
removed: the ids of employees the visitor took off the chart, as a list (a string is refused). Their direct reports move
up to the seat above them, and `uncovered` is worked out without them.

What the chart keeps: a title is unique (the second of a repeat gets " 2", the third " 3"), and an id is a slug of the
title. The fixed seats claim their titles and ids first (every seat but the initiative and industry Owners), so a clash
gives the initiative or industry seat the " 2". Channel seats follow the channel list's order, not the order they were
picked in. An initiative tagged to a picked segment is owned by that segment's Product Owner and has no seat of its own;
a product owner's reason names those initiatives and their bet ids, or counts them when the names would not fit a reason.
An initiative Owner's reason names its bet (its id and statement). Its `first_job` is not set: the setup draft allows one
first job per company, so the bet is named in the reason instead. reports_to is null for a seat with no manager in the
chart (the Corporate, Support, initiative and industry Owners). A company file cannot say "no manager", so the app gives
that seat its default manager (draft.manager_of): the department's lead, else the owner.
"""

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Required, TypedDict, cast

from . import draft as D


class Phrases(TypedDict):
    one: str
    many: str
    count: str
    join: str
    last: str
    ref: str
    statement: str


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
    rules: str
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
    id: str
    label: str
    statement: str
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
STATEMENT_LENGTH = cast(int, D.LIMITS["statement"])
ROLES = cast(list[dict[str, object]], D.ROLES)
ROLE_DEPARTMENT = {str(role["slug"]): str(role["department"]) for role in ROLES}
# An industry Owner takes no built-in card (core: coo, botops, goal-manager, librarian) and no card the catalog marks
# required; the company's own setup supplies those.
INDUSTRY_TEMPLATES = frozenset(str(role["slug"]) for role in ROLES if not role.get("core") and not role.get("required"))
slugify = cast(Callable[[str], str], D.slugify)
# The marks a cut label does not keep at its end: "Photos are the input." is "Photos are the input".
TRAILING = ".,;:!?-–—'\"‘’“”"
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def cut(text: str, limit: int = LABEL_LENGTH) -> str:
    """`text` on one line, at most `limit` characters, cut at a word boundary, with no trailing punctuation or space."""
    words = D.clean(text)
    if len(words) > limit:
        head = words[: limit + 1]
        words = head[: head.rfind(" ")] if " " in head else words[:limit]
    # The marks and the spaces come off the end together, until the end is a word.
    while words and (words[-1] in TRAILING or words[-1] == " "):
        words = words[:-1]
    return words


def _fill(template: str, values: Mapping[str, str]) -> str:
    """`template` with each {name} that `values` has replaced, in one pass: a value that holds braces is only text."""
    return PLACEHOLDER.sub(lambda match: values.get(match.group(1), match.group(0)), template)


def _has_slug(text: str) -> bool:
    """Whether `text` has a slug: an ASCII letter or digit once lower()ed. A name with none gets a fallback id."""
    return re.search(r"[a-z0-9]", text.lower()) is not None


def _items(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _part(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _field(item: object, key: str) -> object:
    return _part(item).get(key)


def _text(item: object, key: str) -> str:
    value = _field(item, key)
    return D.clean(value) if isinstance(value, str) else ""


def _segments(value: object) -> list[Segment]:
    """The picked segments, in order: {"name", "label"}. A segment with no name is dropped."""
    out: list[Segment] = []
    for name in _items(value):
        if not isinstance(name, str):
            continue
        label = cut(name)
        if label:
            out.append({"name": D.clean(name), "label": label})
    return out


def _channels(value: object) -> list[str]:
    """The picked channel keys in the channel list's order. Unknown keys and repeats are dropped; none picked is the
    default list."""
    picked = {key for key in _items(value) if isinstance(key, str)}
    return [key for key in CHANNELS if key in picked] or list(DEFAULT_CHANNELS)


def _initiatives(value: object) -> list[Initiative]:
    """The bets as initiatives: the bet's id, its label (the name, else the statement, cut to the label length), its
    statement (cut to the statement length) and its segment. A bet with neither a name nor a statement is dropped."""
    out: list[Initiative] = []
    for item in _items(value):
        label = cut(_text(item, "name") or _text(item, "statement"))
        if label:
            out.append(
                {
                    "id": _text(item, "id"),
                    "label": label,
                    "statement": cut(_text(item, "statement"), STATEMENT_LENGTH),
                    "segment": _text(item, "segment"),
                    "segment_index": None,
                }
            )
    return out


def _industry(value: object) -> list[Industry]:
    """Up to INDUSTRY_MAX business-type Owners. An entry with no title, or a template that is not an industry card, is
    dropped."""
    out: list[Industry] = []
    for item in _items(value):
        template = _field(item, "template")
        title = _text(item, "title")
        if title and isinstance(template, str) and template in INDUSTRY_TEMPLATES:
            out.append({"title": title, "template": template})
    return out[:INDUSTRY_MAX]


def _refs(tagged: list[Initiative]) -> str:
    """The bet ids a reason names after its initiatives: " (b1, b2)", or "" when none of them has an id."""
    phrases = FORMULA["phrases"]
    ids = [item["id"] for item in tagged if item["id"]]
    return _fill(phrases["ref"], {"ids": phrases["join"].join(ids)}) if ids else ""


def _initiative_phrase(tagged: list[Initiative]) -> str:
    """What a product owner's reason says about its initiatives: " and the “A” and “B” initiatives (b1, b2)", or "" for
    none. A label that repeats is named once; the singular or plural follows the number of initiatives."""
    if not tagged:
        return ""
    phrases = FORMULA["phrases"]
    quoted = ["“%s”" % label for label in dict.fromkeys(item["label"] for item in tagged)]
    names = quoted[0] if len(quoted) == 1 else phrases["join"].join(quoted[:-1]) + phrases["last"] + quoted[-1]
    pattern = phrases["one"] if len(tagged) == 1 else phrases["many"]
    return _fill(pattern, {"names": names}) + _refs(tagged)


def _initiative_count(tagged: list[Initiative]) -> str:
    """The count that stands in for the names when they would not fit a reason: " and 5 initiatives (b1, ...)"."""
    return _fill(FORMULA["phrases"]["count"], {"count": str(len(tagged))}) + _refs(tagged)


def _topic(title: str) -> str:
    """An industry Owner's topic for its reason: "Scheduling Owner" is "scheduling", "Insurance Billing Owner" is
    "insurance billing". A word with a capital after its first letter, such as "HR", keeps its capitals, and so does a
    single letter."""
    topic = title[: -len(" Owner")] if title.endswith(" Owner") else title
    words: list[str] = []
    for word in topic.split(" "):
        rest = word[1:]
        lowercase = rest.lower() == rest and rest.upper() != rest  # no capital after the first letter, a letter besides
        words.append(word.lower() if lowercase else word)
    return " ".join(words)


class _Names:
    """The titles and ids of one chart, each one once. A title whose lower() form repeats gets " 2", " 3" ...; an id
    that repeats gets "-2", "-3"."""

    def __init__(self) -> None:
        self.titles: set[str] = set()
        self.ids: set[str] = set()

    def title(self, base: str) -> str:
        n = 1
        while True:
            suffix = "" if n == 1 else " %d" % n
            head = base if len(base) + len(suffix) <= TITLE_LENGTH else cut(base, TITLE_LENGTH - len(suffix))
            title = head + suffix
            if D.fold(title) not in self.titles:
                self.titles.add(D.fold(title))
                return title
            n += 1

    def id(self, slug: str) -> str:
        stem = slug[:60].rstrip("-") or "x"
        seat_id, n = stem, 1
        while seat_id in self.ids:
            n += 1
            seat_id = "%s-%d" % (stem, n)
        self.ids.add(seat_id)
        return seat_id


@dataclass
class _Slot:
    """A seat before its title and id are claimed. An initiative or industry Owner is `late`: it claims after the fixed
    seats."""

    team: str
    boss: str | None
    base_title: str
    template: str
    department: str
    reason: str
    tags: list[Tag]
    key: str = ""  # a team's singleton key: the seats under it report to this seat
    id_base: str = ""  # the slug of the id when the title's own slug is empty (a name with no ASCII letter or digit)
    late: bool = False
    title: str = ""
    id: str = ""


def _slots(
    segments: list[Segment], channels: list[str], initiatives: list[Initiative], industry: list[Industry]
) -> list[_Slot]:
    """Every seat the picks make, in output order, before a title or id is claimed."""
    phrases = FORMULA["phrases"]
    slots: list[_Slot] = []
    for team in FORMULA["teams"]:
        for spec in team["seats"]:
            per = spec.get("per")
            boss = spec.get("reports_to")
            if per is None:
                key = spec.get("key", "")
                slots.append(
                    _Slot(
                        team["team"],
                        boss,
                        spec.get("title", ""),
                        spec.get("template", ""),
                        spec.get("department", ""),
                        spec.get("reason", ""),
                        [("seat", key)],
                        key=key,
                    )
                )
            elif per == "segment":
                covers = spec.get("covers", "")
                for i, segment in enumerate(segments):
                    # A product owner owns its segment's initiatives; a marketing owner owns only the segment.
                    tagged = (
                        [j for j, item in enumerate(initiatives) if item["segment_index"] == i]
                        if covers == "product"
                        else []
                    )
                    owned = [initiatives[j] for j in tagged]
                    reason = _fill(
                        spec.get("reason", ""), {"label": segment["label"], "initiatives": _initiative_phrase(owned)}
                    )
                    if len(reason) > REASON_LENGTH:
                        # Long names would not fit a reason; the count says the same in fewer characters.
                        reason = _fill(
                            spec.get("reason", ""), {"label": segment["label"], "initiatives": _initiative_count(owned)}
                        )
                    title_spec = spec.get("title", "")
                    tags: list[Tag] = [(covers, i)]
                    tags.extend(("initiative", j) for j in tagged)
                    slots.append(
                        _Slot(
                            team["team"],
                            boss,
                            _fill(title_spec, {"label": segment["label"]}),
                            spec.get("template", ""),
                            spec.get("department", ""),
                            reason,
                            tags,
                            id_base=""
                            if _has_slug(segment["label"])
                            else "segment-%d-%s" % (i + 1, slugify(_fill(title_spec, {"label": ""}))),
                        )
                    )
            elif per == "channel":
                for key in channels:
                    channel = CHANNELS[key]
                    slots.append(
                        _Slot(
                            team["team"],
                            boss,
                            channel["title"],
                            channel["template"],
                            channel["department"],
                            channel["reason"],
                            [("channel", key)],
                        )
                    )
            elif per == "initiative":
                for j, item in enumerate(initiatives):
                    if item["segment_index"] is None:
                        ref = _fill(phrases["ref"], {"ids": item["id"]}) if item["id"] else ""
                        statement = (
                            _fill(phrases["statement"], {"statement": item["statement"]}) if item["statement"] else ""
                        )
                        reason = _fill(
                            spec.get("reason", ""), {"label": item["label"], "ref": ref, "statement": statement}
                        )
                        if len(reason) > REASON_LENGTH:
                            reason = cut(reason, REASON_LENGTH)
                        bet = slugify(item["id"]) if item["id"] else str(j + 1)
                        slots.append(
                            _Slot(
                                team["team"],
                                boss,
                                _fill(spec.get("title", ""), {"label": item["label"]}),
                                spec.get("template", ""),
                                spec.get("department", ""),
                                reason,
                                [("initiative", j)],
                                id_base="" if _has_slug(item["label"]) else "initiative-%s-owner" % bet,
                                late=True,
                            )
                        )
            elif per == "industry":
                for item in industry:
                    slots.append(
                        _Slot(
                            team["team"],
                            boss,
                            _fill(spec.get("title", ""), {"title": item["title"]}),
                            item["template"],
                            ROLE_DEPARTMENT[item["template"]],
                            _fill(spec.get("reason", ""), {"topic": _topic(item["title"])}),
                            [],
                            late=True,
                        )
                    )
    return slots


def _seats(
    segments: list[Segment], channels: list[str], initiatives: list[Initiative], industry: list[Industry]
) -> list[Seat]:
    """Every seat the picks make, in output order (see `Seat`). The fixed seats claim their titles and ids first, then the
    initiative and industry seats; the output keeps the order of the formula."""
    slots = _slots(segments, channels, initiatives, industry)
    names = _Names()
    for late in (False, True):
        for slot in slots:
            if slot.late == late:
                slot.title = names.title(slot.base_title)
                slot.id = names.id(slot.id_base or slugify(slot.title))
    by_key = {slot.key: slot.id for slot in slots if slot.key}
    return [
        {
            "id": slot.id,
            "template": slot.template,
            "title": slot.title,
            "department": slot.department,
            "reports_to": by_key.get(slot.boss) if slot.boss else None,
            "team": slot.team,
            "reason": slot.reason,
            "tags": slot.tags,
        }
        for slot in slots
    ]


def build(inputs: object, removed: Iterable[str] = ()) -> Result:
    """The chart for `inputs` (see the module docstring), without the seats named in `removed`.

    Each seat's tags say what it covers: ("product", i) and ("marketing", i) for picked segment i, ("channel", key) for a
    channel, ("initiative", j) for initiative j (a product owner's tags include its tagged initiatives), and ("seat", key)
    for a team's singleton seat. `uncovered` lists the things with no tag left among the kept seats."""
    if isinstance(removed, str):
        raise TypeError("removed is a list of seat ids, not one string")
    source = _part(inputs)
    segments = _segments(source.get("segments"))
    channels = _channels(source.get("channels"))
    initiatives = _initiatives(source.get("initiatives"))
    industry = _industry(source.get("industry"))
    for item in initiatives:
        # The picked segment a bet is tagged to: the first with that name, ignoring case and spacing; else none.
        item["segment_index"] = next(
            (
                i
                for i, segment in enumerate(segments)
                if item["segment"] and D.fold(segment["name"]) == D.fold(item["segment"])
            ),
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
