"""The settings catalogue: one entry per control in Settings that writes, so a person, or BotOps for them, can find a
setting by its words, read it and change it without guessing a route or a body (docs/permissions.md).

Each entry (backend/settings_entries.py) names a stable key (`team.rules.admin_sql`), the control's plain name and
where it sits in Settings, who may change it, its class and the route it writes:

  direct       runs at once, as the person (BotOps or their Assistant acting for them has exactly their rights)
  confirm      BotOps or the Assistant acting for a person puts it to them as a Confirm card; the person's own call
               runs at once
  person       only the person themselves, in Settings: BotOps or the Assistant is told why, with the link to the card

The Assistant acts for its person with the same entries and classes as BotOps (`ACTING_FOR`). A direct entry runs on the
person's own words in their own chat (the turn cites their message, as a task action does) while the owner's rule
"Assistant acts without asking" is on; otherwise it too is a Confirm card in their chat.

`hub settings find|show|set` (clients/hubtools.py) read it here; `set` asks `plan` for the exact request and makes it
with the requester's rights, so the route's own checks still decide. backend/tests/test_settings_catalog.py fails when a
Settings card writes to a route no entry covers.

  find(words)                    entries ranked by how many of the words their name, place, key and words hold
  entry(key)                     the entry, or a 404 naming the nearest keys
  public(e, base)                what a caller sees of an entry, with its card's full link
  plan(c, auth, who, e, value, target)  {"run": {method, path, body}}, the Confirm card, or a refusal naming who may
  matched(method, path, body)    the entry and target a request is exactly the body of, and its class
  stored_class(c, method, path, body)  `matched`, a repository change classed with the stored state it keeps
"""
import json
import re
from dataclasses import dataclass, field as _field

from .store import Problem

CLASSES = ("direct", "confirm", "person")
# Who may change a setting: the check made before the route's own, so a refusal names who can.
WHO = {
    "owner": "the owner",
    "admin": "the owner or an admin",
    "mover": "the owner and people on a team that moves tasks",
    "bot": "the bot's owners, its operator, the people above it, an admin or the owner",
    "self": "each person, for themselves",
    "creator": "the person who made it, an admin or the owner",
    "operator": "the computer's operator, an admin or the owner",
    "anyone": "anyone on the team",
}
TARGETS = ("bot", "computer", "person", "id")
# Who acts for a person through the catalogue, with exactly their rights: BotOps, and the person's own Assistant.
ACTING_FOR = ("botops", "assistant")


@dataclass(frozen=True)
class Setting:
    key: str                    # stable: `area.thing.setting`, lowercase, dots
    name: str                   # the control's label in Settings
    where: str                  # Settings > Section > Card
    who: str                    # a WHO key
    kind: str                   # a CLASSES value
    method: str
    route: str                  # a v2 route, `{bot}`, `{computer}` or `{person}` for the target
    field: str = ""             # where the value goes in the body (dots nest); "" sends the value as the body
    value: str = "bool"         # bool, int, number, text, list, json, choice:a|b|c, or upload (a file, in Settings)
    card: str = "#/settings"    # the card's link in the app
    read: str = ""              # GET route whose answer holds the current value, then `#` and its pointer (dots)
    extra: dict = _field(default_factory=dict)      # fixed keys the route needs beside the value
    words: str = ""             # other words people use for it
    why: str = ""               # person-only: the one-line reason
    confirm_when: tuple = ()    # ((dotted body place, values), ...): a direct entry whose body holds one is `when_held`
    when_held: str = "confirm"

    @property
    def target(self):
        return next((t for t in TARGETS if "{" + t + "}" in self.route), "")


def _entries():
    from .settings_entries import ENTRIES
    return ENTRIES


def _index():
    found = {}
    for e in _entries():
        if e.key in found:
            raise ValueError("duplicate settings key " + e.key)
        found[e.key] = e
    return found


def _tokens(text):
    return set(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def find(words, limit=20):
    """Entries whose name, place, key or words hold the most of `words`, best first; every entry for no words."""
    wanted = _tokens(words)
    rows = []
    for e in _entries():
        name, rest = _tokens(e.name), _tokens(" ".join((e.where, e.key.replace(".", " ").replace("_", " "), e.words)))
        hits = len(wanted & (name | rest))
        if wanted and not hits:
            continue
        rows.append((-(hits * 2 + len(wanted & name)), e.key, e))
    return [e for _, _, e in sorted(rows)][:limit if wanted else None]


def entry(key):
    e = _index().get(str(key or "").strip())
    if not e:
        near = [x.key for x in find(str(key).replace(".", " ").replace("_", " "), 3)]
        raise Problem("not_found", "No setting " + repr(key) + (": did you mean " + ", ".join(near) + "?" if near else
                      "; find one with `hub settings find <words>`"), 404)
    return e


def who_text(e):
    return WHO[e.who][0].upper() + WHO[e.who][1:]


def body_shape(e):
    """The body the route takes, the value's place marked with its type."""
    shape = "<" + e.value + ">"
    if not e.field:
        return shape
    body = dict(e.extra)
    _put(body, e.field, shape)
    return body


def public(e, base=""):
    return {"key": e.key, "name": e.name, "where": e.where, "class": e.kind, "who": e.who, "who_may": who_text(e),
            "value": e.value, "target": e.target, "method": e.method, "route": "/api/v2/" + e.route,
            "body": body_shape(e), "card": (base.rstrip("/") + "/" + e.card) if base else e.card,
            **({"why": e.why} if e.why else {}),
            **({"class_when": {"class": e.when_held, "body": {place: list(values) for place, values in e.confirm_when}}}
               if e.confirm_when else {})}


def _put(body, dotted, value):
    parts = dotted.split(".")
    for part in parts[:-1]:
        body = body.setdefault(part, {})
    body[parts[-1]] = value


def parse(e, value):
    """The value in the type the route takes; a command line gives text."""
    kind = e.value
    text = value.strip() if isinstance(value, str) else value
    try:
        if text is None:
            raise ValueError
        if kind == "bool":
            if isinstance(text, bool):
                return text
            lowered = str(text).lower()
            if lowered in ("true", "on", "yes", "1"):
                return True
            if lowered in ("false", "off", "no", "0"):
                return False
            raise ValueError
        if kind == "int":
            if isinstance(text, bool):
                raise ValueError
            return int(text)
        if kind == "number":
            if isinstance(text, bool):
                raise ValueError
            return float(text)
        if kind == "list":
            if isinstance(text, list):
                return [str(v) for v in text]
            if str(text).startswith("["):
                return [str(v) for v in json.loads(text)]
            return [v.strip() for v in str(text).split(",") if v.strip()]
        if kind == "json":
            parsed = json.loads(text) if isinstance(text, str) else text
            if parsed is None:
                raise ValueError
            return parsed
        if kind.startswith("choice:"):
            choices = kind[len("choice:"):].split("|")
            if str(text) not in choices:
                raise ValueError
            return str(text)
        if text is None or isinstance(text, (dict, list)):
            raise ValueError
        return str(text) if kind == "text" else text
    except (ValueError, TypeError):
        raise Problem("validation", f"{e.name} takes {_kind_words(kind)}, not {value!r}", 422) from None


def _kind_words(kind):
    if kind.startswith("choice:"):
        return "one of " + ", ".join(kind[len("choice:"):].split("|"))
    return {"bool": "on or off", "int": "a whole number", "number": "a number", "list": "a comma-separated list",
            "json": "JSON", "text": "text", "upload": "a file"}.get(kind, kind)


def path_for(e, target, template=None):
    template = template if template is not None else e.route
    if e.target and not (target or {}).get(e.target):
        raise Problem("validation", f"{e.name} is set per {e.target}: name one with --{e.target}", 422)
    path = template
    for name in TARGETS:
        if "{" + name + "}" in path and name == "id" and path.startswith("repositories/"):
            # A repository's id is its full name, and the route takes it as two segments: `{owner}/{repo}`.
            owner, _, repo = str(target[name]).partition("/")
            if not owner or not repo or "/" in repo:
                raise Problem("validation", f"{e.name} names a repository as owner/name", 422)
            from urllib.parse import quote
            path = path.replace("{id}", quote(owner, safe="") + "/" + quote(repo, safe=""))
        elif "{" + name + "}" in path:
            from urllib.parse import quote
            # Routine IDs use `<bot>:<routine-key>`; keep the colon in its path segment so Confirm cards can
            # validate the same route string the Settings API accepts without permitting a slash escape.
            path = path.replace("{" + name + "}", quote(str(target[name]), safe=":" if name == "id" else ""))
    return path


def _held(body, dotted):
    """The values at `dotted` in `body`; a list on the way is read item by item (`chosen.run_workflows`)."""
    found = [body]
    for part in dotted.split("."):
        found = [item for value in found for item in (value if isinstance(value, list) else [value])]
        found = [value.get(part) for value in found if isinstance(value, dict)]
    return found


def kind_for(e, body):
    """The entry's class for this body: a direct entry is `when_held` when the body holds one of its `confirm_when`
    values."""
    if (e.kind == "direct"
            and any(value is not None and value in values
                    for place, values in e.confirm_when for value in _held(body, place))):
        return e.when_held
    return e.kind


def _bot_target(c, e, target):
    """Resolve id-addressed bot settings to their owning bot before checking manager rights."""
    target = target or {}
    if target.get("bot"):
        return target["bot"]
    object_id = target.get("id")
    if not object_id:
        return ""
    if e.route.startswith("routines/{id}"):
        table = "schedules"
    elif e.route.startswith("settings/transitions/{id}/"):
        table = "bot_transitions"
    else:
        return ""
    found = c.execute(f"SELECT bot FROM {table} WHERE id=?", (object_id,)).fetchone()
    return found["bot"] if found else ""


def may(c, auth, who, e, target=None):
    """Whether `who` passes the entry's own check (the route still makes its own)."""
    if who.role == "owner":
        return True
    if e.who == "anyone":
        return who.role in ("human", "bot")
    if e.who in ("creator", "operator"):             # whose it is: the route decides
        return who.role == "human" or auth.bot_admin(who)
    if e.who == "admin":
        return auth.bot_admin(who)
    if e.who == "mover":
        from . import hubdb as H
        return who.role == "human" and H.can_move(c, who.actor)
    if e.who == "bot":
        return auth.bot_manager(c, who, _bot_target(c, e, target))
    if e.who == "self":
        return who.role == "human" and (e.target != "person" or who.actor == "human:" + str((target or {}).get("person")))
    return False


def refusal(e, base=""):
    link = (base.rstrip("/") + "/" + e.card) if base else e.card
    return Problem("forbidden", f"Only {WHO[e.who]} {'changes' if e.who != 'self' else 'change'} {e.name} "
                   f"({e.where}): ask one of them, or open {link}", 403, extra={"link": link, "who_may": who_text(e)})


def plan(c, auth, who, e, value, target=None, base="", propose=None, card_direct=False,
         can_card=None, card_words=None):
    """The exact request that sets `e` to `value` for `target`, after the entry's own checks: `{"run": {...}}` to
    make with the caller's rights; for BotOps or the Assistant acting for a person, a confirm entry's Confirm card
    (`propose`) or a person-only entry's reason and link. `card_direct`: a direct entry is a card too (the Assistant,
    when the owner's rule is off or the turn cites no words of the person's). `can_card(method, path)`: whether that
    card can carry the route. `card_words(c, auth, who, e, body, target)`: for an entry whose class rests on what the
    change does to stored state, the change tried as the person's own click: (needs a card, the card's plain words),
    or the refusal that click would get (then no card is made); None for any other entry."""
    link = (base.rstrip("/") + "/" + e.card) if base else e.card
    acting_for = who.via in ACTING_FOR

    def person_only():
        why = e.why or f"{e.name} is changed only by the person themselves"
        return Problem("person_only", why + ": open " + link, 403, extra={"link": link})
    if e.kind == "person" and (acting_for or who.role not in ("owner", "human")):
        raise person_only()
    if not may(c, auth, who, e, target):
        raise refusal(e, base)
    if e.value == "upload":
        raise Problem("validation", f"{e.name} is a file you upload in Settings: open {link}", 422,
                      extra={"link": link})
    parsed = parse(e, value)
    body = dict(e.extra)
    if e.field:
        _put(body, e.field, parsed)
    elif isinstance(parsed, dict):
        body.update(parsed)
    else:
        raise Problem("validation", f"{e.name} takes a JSON object", 422)
    request = {"method": e.method, "path": path_for(e, target), "body": body}
    kind = kind_for(e, body)
    if kind == "person" and (acting_for or who.role not in ("owner", "human")):
        raise person_only()
    tried = card_words(c, auth, who, e, body, target) if card_words and acting_for else None
    if tried is not None:
        # What the route itself does with the request: a kept Run workflows tick that comes back with
        # `{"mode": "chosen"}` needs the card though the body holds no tick, and re-sending a tick that is on doesn't.
        kind = e.when_held if tried[0] else "direct"
    # The plan is never what a card runs (the card runs the route itself), so BotOps acting for a person always gets
    # the card here, and with no way to make one it gets nothing it could run.
    if acting_for and (kind == "confirm" or (kind == "direct" and card_direct)):
        if propose is None or (can_card and not can_card(e.method, "/api/v2/" + request["path"])):
            raise Problem("unavailable", f"{e.name} needs your yes on a Confirm card, which can't be made here: open "
                          + link, 503, extra={"link": link})
        words = tried and tried[1]
        if words:
            return propose(c, who, e.method, "/api/v2/" + request["path"], body, words[:300], what=words)
        return propose(c, who, e.method, "/api/v2/" + request["path"], body,
                       f"Change {e.name} ({e.where}) to {json.dumps(parsed)}")
    return {"run": request, "setting": public(e, base), "value": parsed}


def _pattern(route):
    """An entry's route as a regex over the path a request names, one group per target."""
    out, at = "", 0
    for m in re.finditer(r"\{(" + "|".join(TARGETS) + r")\}", route):
        # A repository's id is its full name, `owner/name`: the route takes both (PUT /api/v2/repositories/{owner}/{repo}).
        one = "[^/]+/[^/]+" if route.startswith("repositories/") and m.group(1) == "id" else "[^/]+"
        out += re.escape(route[at:m.start()]) + "(?P<" + m.group(1) + ">" + one + ")"
        at = m.end()
    return re.compile("/api/v2/" + out + re.escape(route[at:]))


_PATTERNS = {}


def routed(method, path):
    """The entries whose route this request names, each with the target its path holds."""
    from urllib.parse import unquote
    out = []
    for e in _entries():
        pattern = _PATTERNS.get(e.route) or _PATTERNS.setdefault(e.route, _pattern(e.route))
        m = e.method == method and pattern.fullmatch(str(path or ""))
        if m:
            out.append((e, {k: unquote(v) for k, v in m.groupdict().items()}))
    return out


def _without(body, dotted):
    """The body with the value at `dotted` taken out, and parents left empty dropped; None when it holds none."""
    if not isinstance(body, dict):
        return None
    head, _, rest = dotted.partition(".")
    if head not in body:
        return None
    if not rest:
        return {k: v for k, v in body.items() if k != head}
    inner = _without(body[head], rest)
    if inner is None:
        return None
    return {**{k: v for k, v in body.items() if k != head}, **({head: inner} if inner else {})}


def matched(method, path, body):
    """`(entry, target, class)` when this request is exactly what `plan` builds for one entry: its route, its fixed
    keys and its value at its place, nothing else; else None. A request that sets two settings at once matches none."""
    for e, target in routed(method, path):
        if not isinstance(body, dict):
            continue
        if e.field:
            rest = _without(body, e.field)
            if rest is None or rest != e.extra:
                continue
        elif any(body.get(k) != v for k, v in e.extra.items()):
            continue
        if e.key == "bot.repositories":
            # Classed by what the change does to stored state, which this body alone does not show (`stored_class`):
            # a repository change can keep a Run workflows tick or take access away.
            return e, target, "stored"
        return e, target, kind_for(e, body)
    return None


def stored_class(c, method, path, body, trial=None):
    """`matched`, with a change classed by what it does to stored state, as `plan` classes it: a repository change is a
    card when `trial(e, target, body)` (the change tried as the person's own click, `repositories.card_words`) says
    the route would make one; with no trial, always."""
    found = matched(method, path, body)
    if not found or found[2] != "stored":
        return found
    e, target, _ = found
    return e, target, "direct" if trial and not trial(e, target, body) else e.when_held


def install(app, store, auth, settings):
    from fastapi import Request

    from . import models as M

    @app.get("/api/v2/settings/catalog")
    def settings_find(request: Request, q: str = "", limit: int = 20):
        """Settings that write, found by their words (all of them with none): key, name, place, who may change it, class,
        route and body shape, and the link to the card."""
        auth.domain(request.state.identity)
        rows = find(q[:200], max(1, min(int(limit), 200)))
        return {"settings": [public(e, settings.public_url) for e in rows]}

    @app.get("/api/v2/settings/catalog/{key}")
    def settings_show(request: Request, key: str, bot: str = "", computer: str = "", person: str = "", id: str = ""):
        """One entry, whether this caller may change it, and where its current value is read."""
        who = request.state.identity
        auth.domain(who)
        e = entry(key)
        target = {"bot": bot, "computer": computer, "person": person, "id": id}
        with store.read() as c:
            allowed = may(c, auth, who, e, target) and not (e.kind == "person" and who.via in ACTING_FOR)
        read = None
        if e.read and (not e.target or target.get(e.target)):
            route, _, pointer = e.read.partition("#")
            read = {"path": path_for(e, target, route), "pointer": path_for(e, target, pointer)}
        return {"setting": public(e, settings.public_url), "you_may": allowed, "read": read}

    @app.post("/api/v2/settings/catalog/{key}/plan")
    def settings_plan(request: Request, key: str, body: M.SettingsPlan):
        """The request that sets this entry, checked against the caller first: `run` (make it with your rights), a Confirm
        card for BotOps or the Assistant acting for a person on a confirm entry, or a refusal that names who may change
        it. The Assistant's direct change runs only on the person's cited words while "Assistant acts without asking" is
        on (the same check its write then meets); otherwise it is a card in their chat."""
        from .assistant import valid_operation
        who = request.state.identity
        auth.domain(who)
        e = entry(key)
        target = {"bot": body.bot or "", "computer": body.computer or "", "person": body.person or "", "id": body.id or ""}
        assistant = who.via == "assistant" and not who.confirmed
        asked = request.headers.get("x-tico-on-behalf-of")
        if assistant:
            made = getattr(app.state, "assistant_propose", None)
            propose = made and (lambda c, who, method, path, data, summary, what=None:
                                made(c, who, method, path, data, summary[:300], asked, what=what))
        else:
            made = getattr(app.state, "botops_propose", None)
            propose = made and (lambda c, who, method, path, data, summary, what=None:
                                made(c, who, method, path, data, summary[:300], what=what))
        with store.transaction() as c:
            card_direct = False
            if assistant:
                from . import assistant as assistant_mod, team_rules
                try:
                    cited = bool(asked) and bool(assistant_mod.instruction(c, who, asked))
                except Problem:
                    cited = False
                card_direct = not (cited and team_rules.load(c)["assistant_direct"])

            def words(c, auth, who, e, body, target):
                if e.key == "bot.repositories":
                    from . import repositories
                    return repositories.card_words(c, settings, auth, who, target["bot"], body)
                return None

            return plan(c, auth, who, e, body.value, target, settings.public_url, propose=propose,
                        card_direct=card_direct,
                        can_card=lambda method, path: valid_operation(method, path, settings,
                                                                      "assistant" if assistant else "botops"),
                        card_words=words)
