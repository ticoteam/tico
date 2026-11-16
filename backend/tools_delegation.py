"""Tools changes a built-in makes for the person who asked it: the Tools Owner and the Assistant act with that person's
rights, the way BotOps does (backend/botops_act.py, app.py `delegated_identity`), so a request for access gets done
instead of bouncing back to the person.

The Tools page's own owner (backend/page_owners.py, the built-in Tools Owner unless someone moved the page) borrows the
asking person's rights on the routes below only: a person's own message to it in their chat, or a task they asked it
for, lends them as the same checks lend them to BotOps. Its writes are recorded as the person's, via `tools-owner`, with
the Tools Owner named on the delegation (`by`). The Assistant already writes as its person (via `assistant`); these are
the Tools changes its turn may make on the person's cited words (`assistant.admits_on_turn`), where the rest of the
credential routes stay on its NEVER list. Neither ever reads, stores or repeats a value: a body that carries one is
refused (`botops_act.secret_in_request`), and a value reaches the vault only through the person's own key card.
"""

import re

from .botops_act import API, _routes
from .store import H

# The `via` the Tools Owner's writes for a person carry (every event and history row the request writes says so).
VIA = "tools-owner"
# Every `via` that means "a built-in acting with a person's rights": BotOps, and the Tools Owner on Tools changes.
DELEGATE_VIAS = ("botops", VIA)

_S = r"[^/]+"
# The Tools changes a person may ask the Tools Owner or their Assistant for, each checked with the person's own rights by
# its route: give or take back a key, set a level, share a website account, add or change a tool and its settings, and
# a bot's bot.yaml `tools:` entries. A key's value, its reveal and deleting a tool or a key stay the person's own tap.
ROUTES = _routes(
    ("GET", r"credentials"),  # the person's key list, to find the key they named (names and grants, never a value)
    ("POST", rf"credentials/{_S}/grants"),
    ("POST", rf"credentials/{_S}/grants/{_S}/revoke"),
    ("POST", r"api-tools"),
    ("PATCH", rf"api-tools/{_S}"),
    ("PUT", rf"api-tools/{_S}/access/{_S}"),
    ("POST", rf"api-tools/{_S}/settings"),
    ("PATCH", rf"api-tools/{_S}/settings/{_S}"),
    ("DELETE", rf"api-tools/{_S}/settings/{_S}"),
    ("POST", r"mcp-tools"),
    ("PUT", rf"website-tools/{_S}/accounts/{_S}/access/{_S}"),
    ("POST", rf"bots/{_S}/tools"),
    ("POST", rf"bots/{_S}/tools/{_S}/(update|delete)"),
    ("DELETE", rf"bots/{_S}/tools/{_S}"),
)
# Of those, the ones the Assistant's NEVER list would otherwise hold (a bot's tool entries never were on it).
_GRANT_SUBJECT = re.compile(API + rf"(api-tools/{_S}/access|website-tools/{_S}/accounts/{_S}/access)/({_S})")


def route(method, path):
    """Whether this call is a Tools change a built-in makes for the person who asked it."""
    from .botops_act import normalize

    path, method = normalize(path), str(method or "").upper()
    return bool(path) and any(m == method and p.fullmatch(path) for m, p in ROUTES)


def subject_of(path, body):
    """The person or bot a grant or level change is for (`bot:<id or slug>`, `human:<id>`), or "" for any other change."""
    from .botops_act import normalize

    path = normalize(path) or ""
    found = _GRANT_SUBJECT.fullmatch(path)
    if found:
        return found.group(found.lastindex or 0)
    if re.fullmatch(API + rf"credentials/{_S}/grants", path) and isinstance(body, dict):
        return str(body.get("subject") or "")
    return ""


def for_bots_only(method, path, body):
    """Whether the change gives or sets access only for a bot (or takes a grant back, or changes no one's access): a
    person's own access and every computer's stay their own tap in Tools, as they do for BotOps."""
    from .botops_act import normalize

    path = normalize(path) or ""
    subject = subject_of(path, body)
    if _GRANT_SUBJECT.fullmatch(path):
        return subject.startswith("bot:") or not subject.startswith(("human:", "person:", "computer"))
    if re.fullmatch(API + rf"credentials/{_S}/grants", path):
        return subject.startswith("bot:")
    return True


def to_itself(c, agent, path, body):
    """Whether a grant or level change is for `agent` itself: the Tools Owner connects keys for others and holds none."""
    subject = subject_of(path, body)
    if not subject.startswith("bot:"):
        return False
    return "bot:" + ((H.resolve_bot(c, subject) or {}).get("id") or H.actor_id(subject)) == agent


def tools_owner(c):
    """The Tools page's own owner (`bot:<id>`), never the General Manager standing in, or None."""
    from . import page_owners

    return page_owners.owner(c, "tools", fallback=False)


def is_tools_owner(c, actor):
    return bool(actor) and str(actor).startswith("bot:") and tools_owner(c) == actor


def via_of(agent):
    """The `via` a write carries when `agent` (BotOps or the Tools Owner) makes it with a person's rights."""
    return "botops" if agent == "bot:" + H.FLEET_MAINTAINER else VIA


def name_of(c, agent):
    """How a person reads the acting built-in: BotOps, or the Tools page owner's name."""
    if agent == "bot:" + H.FLEET_MAINTAINER:
        return "BotOps"
    bot = H.bot(c, H.actor_id(agent)) or {}
    return bot.get("display_name") or bot.get("slug") or "the Tools Owner"
