"""External agent sync: a person's bots on another platform, as bots under them on the team chart.

Some agents live on their maker's platform with no API, export or webhook for Tico to call: Grok
Bot (xAI's cloud agents, any number per account) and Dots (OpenAI's always-on agent, one per
account). What they can do is call Tico's MCP tools as their person, on a routine. So the person
links them with their own sign-in and the agent calls `hub_external_sync` (docs/external-agent-sync.md).
Tico does the rest here:

- A bot it has not seen becomes a bot with that provider's harness, placed under the person who
  synced it (`reports_to: human:<them>`). They may move it anywhere on the chart afterwards; a
  later sync never moves it back. It joins their group too (a bot reporting to a human does not
  inherit the human's group on its own), so it sits under them on the chart.
- Its name, description, section (where it sits in the maker's own list, e.g. Grok's "Pinned")
  and full instructions are kept in `config_json[<provider key>]`, so the bot can be rebuilt
  somewhere else if the account goes away.
- Its transcript is copied into the syncing person's own room with the bot, read and quiet
  (no job, no unread badge). A message's id is derived from the bot and the message, so sending
  the same messages again adds nothing.
- What the person wrote to it in Tico since the last sync comes back in the reply (`inbox`), for
  the routine to hand to the bot; its answer arrives with the next transcript.

Nothing is dispatched to these bots: the harnesses are external, so presence is "last synced"
rather than a heartbeat.
"""

import base64
import binascii
import hashlib
import ipaddress
import json
import mimetypes
import re
import socket
import uuid
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import Field

from . import access as Access
from . import models as M
from . import people as P
from . import rooms
from .blobs import register
from .store import H, Problem, encode

# A routine runs on a schedule of days, not minutes: a bot synced within the last day and a bit
# is reporting in.
PRESENCE_GAP = 26 * 60 * 60
NAMESPACE = uuid.UUID("5b0c7f1e-8d0a-4c55-9d0b-6f3a1c2e9a41")
INBOX_LIMIT = 50
IMAGE_FETCHES = 50      # image links one sync may download; the rest stay links (an inline image costs no download)


IMAGE_BYTES = 10_000_000
IMAGE_TIMEOUT = 20
IMAGE_REDIRECTS = 3


class SyncedImage(M.Contract):
    """An image the bot showed: a link Tico fetches, or the bytes themselves (small ones)."""
    url: str = Field(default="", max_length=4000)
    name: str = Field(default="", max_length=200)
    content_base64: str = Field(default="", max_length=7_000_000)


class SyncedMessage(M.Contract):
    role: str = Field(pattern=r"^(user|bot)$")
    text: str = Field(min_length=1, max_length=40_000)
    at: str = Field(default="", max_length=50)
    id: str = Field(default="", max_length=200)
    images: list[SyncedImage] = Field(default_factory=list, max_length=10)


class SyncedBot(M.Contract):
    id: str = Field(default="", max_length=100, pattern=r"^[A-Za-z0-9_.:-]*$")
    grok_id: str = Field(default="", max_length=100, pattern=r"^[A-Za-z0-9_.:-]*$")   # older Grok routines
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    instructions: str = Field(default="", max_length=40_000)
    section: str = Field(default="", max_length=100)
    messages: list[SyncedMessage] = Field(default_factory=list, max_length=500)


class ExternalSync(M.Contract):
    provider: str = Field(default="grokbot", pattern=r"^(grokbot|dots)$")
    bots: list[SyncedBot] = Field(min_length=1, max_length=50)
    source: str = Field(default="", max_length=200)      # which bot ran the sync, for the page
    # The caller hands `inbox` on to its bots. Only then is it filled, and its messages marked delivered: a routine
    # from before the inbox existed ignores it, and what the person wrote would be marked delivered and never arrive.
    inbox: bool = False


TICO_WORD = re.compile(r"\bTico\b", re.I)


def local_name(name):
    """What a Grok Bot is called here. In Grok a Bot says "Tico" to mark it as one of ours; here
    every bot is Tico's, so the word says where it runs instead: "Tico Designer" in Grok is "Grok
    Designer" in Tico."""
    renamed = " ".join(TICO_WORD.sub("Grok", name).split())
    return re.sub(r"\b(Grok)(\s+Grok\b)+", r"\1", renamed, flags=re.I) or name


@dataclass(frozen=True)
class Provider:
    id: str             # the harness and the `provider` a sync names
    label: str          # what the pages call it
    key: str            # where its record sits in config_json, and its message ids' prefix
    maker: str          # whose model it runs on, for the presence line
    single: bool        # one bot per person: Dots is a single always-on agent
    local_name: object = None


PROVIDERS = {
    "grokbot": Provider("grokbot", "Grok Bot", "grok", "xai", False, local_name),
    "dots": Provider("dots", "Dots", "dots", "openai", True),
}
HARNESSES = tuple(PROVIDERS)
SINGLE_ID = "default"


def provider_for(harness):
    return PROVIDERS.get(harness)


def model_for(provider):
    return provider.id + "-own"


def external_id(provider, item):
    if provider.single:
        return item.id or SINGLE_ID
    return item.id or item.grok_id


def check(provider, body):
    if provider.single and len(body.bots) > 1:
        raise Problem("bots", f"{provider.label} is one agent: send one bot", 422)
    if not provider.single and not all(external_id(provider, item) for item in body.bots):
        raise Problem("bots", f"Each {provider.label} needs its id", 422)


def slug_for(c, name, fallback):
    base = "-".join("".join(ch if ch.isalnum() else " " for ch in name.lower()).split())[:60] or fallback
    slug, n = base, 2
    while H.bot(c, slug):
        slug, n = f"{base}-{n}", n + 1
    return slug


def find(c, provider, person, ext_id):
    for row in c.execute("SELECT bot,config_json FROM bot_config WHERE operator=? AND "
                         "json_extract(config_json,'$.harness')=?", (person, provider.id)):
        record = (H._json(row["config_json"], {}) or {}).get(provider.key) or {}
        if provider.single or record.get("id") == ext_id:
            return row["bot"]
    return None


# ----------------------------------------------------------------------------- images
# A synced message may carry images. Each one is fetched once, stored like a chat attachment and shown inline; one
# that cannot be fetched stays a link. Tico fetches only public https addresses, so a sync can
# never make it read something on its own network.
def public_host(host):
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return False
    addresses = {ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos}
    return bool(addresses) and all(a.is_global for a in addresses)


def fetch_image(url, transport=None):
    """(bytes, content type) for an image at a public https address, or None."""
    for _ in range(IMAGE_REDIRECTS + 1):
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or (transport is None and not public_host(parts.hostname)):
            return None
        try:
            with httpx.Client(timeout=IMAGE_TIMEOUT, follow_redirects=False, transport=transport) as client:
                with client.stream("GET", url) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if response.status_code != 200 or not kind.startswith("image/"):
                        return None
                    data = b""
                    for chunk in response.iter_bytes():
                        data += chunk
                        if len(data) > IMAGE_BYTES:
                            return None
                    return (data, kind) if data else None
        except httpx.HTTPError:
            return None
    return None


def image_bytes(image, transport=None):
    """(bytes, content type, name) for one SyncedImage, or None when it cannot be had."""
    name = image.name or (urlsplit(image.url).path.rsplit("/", 1)[-1] if image.url else "") or "image"
    if image.content_base64:
        try:
            data = base64.b64decode(image.content_base64, validate=True)
        except (ValueError, binascii.Error):
            return None
        kind = mimetypes.guess_type(name)[0]
        return (data, kind or "image/png", name) if 0 < len(data) <= IMAGE_BYTES else None
    fetched = fetch_image(image.url, transport) if image.url else None
    return (fetched[0], fetched[1], name) if fetched else None


def allowed(who):
    """A person syncs their own agent's bots, with their own sign-in. Adding a bot goes through the
    usual rules (`settings_admin.create_bot`): a member within their bot limit, as for any bot."""
    if who.role not in ("human", "owner"):
        raise Problem("forbidden", "A person syncs their own external agent's bots, with their own sign-in", 403)


def precheck(c, settings_admin, who, body):
    """Everything that would refuse the sync, before any image is fetched for it: who is asking, the body's shape, and
    whether the bots it would add fit the person's bot limit (`create_bot` checks each again inside the sync)."""
    allowed(who)
    provider = PROVIDERS[body.provider]
    check(provider, body)
    person = H.actor_id(who.actor)
    new = sum(1 for item in body.bots if not find(c, provider, person, external_id(provider, item)))
    if not new:
        return
    settings_admin._creator(c, who)       # may they add bots at all, and is there room for one
    if who.role == "owner" or settings_admin.auth.bot_admin(who):
        return
    limit = Access.load_access(c, settings_admin.settings)["member_bot_limit"]
    have = settings_admin.counted_bots(c, who.actor)
    if have + new > limit:
        raise Problem("bot_limit", f"This sync would add {new} bots to your {have}; a member may have {limit}. "
                      "Archive some, or ask an admin to raise the limit", 409)


def prefetch_images(store, blobs, body, transport=None, person=""):
    """Fetch and store the images of messages Tico does not have yet, outside any transaction, at most IMAGE_FETCHES
    downloads a sync. {message id: [(digest, size, name, content type) or (None, url)]}."""
    provider = PROVIDERS[body.provider]
    wanted = {}
    for item in body.bots:
        for msg in item.messages:
            if msg.images:
                wanted[message_id(provider, external_id(provider, item), msg, person)] = msg.images
    if not wanted:
        return {}
    with store.read() as c:
        marks = ",".join("?" * len(wanted))
        have = {r[0] for r in c.execute(f"SELECT id FROM messages WHERE id IN ({marks})", tuple(wanted))}
    out, fetches = {}, IMAGE_FETCHES
    for mid, images in wanted.items():
        if mid in have:
            continue
        rows = []
        for image in images:
            if not image.content_base64:
                if fetches <= 0:
                    if image.url:
                        rows.append((None, image.url))
                    continue
                fetches -= 1
            got = image_bytes(image, transport)
            if got:
                data, kind, name = got
                rows.append((blobs.put(data, kind), len(data), name, kind))
            elif image.url:
                rows.append((None, image.url))
        out[mid] = rows
    return out


def message_id(provider, ext_id, msg, person=""):
    key = msg.id or hashlib.sha256(json.dumps(
        [msg.role, msg.at, msg.text, [image.model_dump() for image in msg.images]],
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    # Grok's ids predate other providers and stay as they were, so a resync adds nothing.
    parts = [person, ext_id, key] if provider.id == "grokbot" else [provider.id, person, ext_id, key]
    return provider.key + "-" + str(uuid.uuid5(NAMESPACE, json.dumps(parts)))


def moment(value, fallback):
    at = H.parse_ts(value) if value else None
    return at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z" if at else fallback


def legacy_grok_message(c, room, ext_id, msg, mid, images):
    """True when a message from before per-person ids is already here, unchanged."""
    legacy_key = msg.id or f"{msg.role}|{msg.at}|{msg.text[:500]}"
    legacy_id = "grok-" + str(uuid.uuid5(NAMESPACE, ext_id + "|" + legacy_key))
    legacy = c.execute("SELECT id,body,refs_json FROM messages WHERE id=? AND conversation_id=?", (legacy_id, room["id"])).fetchone()
    if not legacy:
        return False
    fetched = (images or {}).get(mid) or []
    text = msg.text + "".join(f"\n\n[Image in Grok]({row[1]})" for row in fetched if row[0] is None)
    refs = H._json(legacy["refs_json"], {})
    previous = [c.execute("SELECT digest,name FROM blobs WHERE id=?", (item["id"],)).fetchone()
                for item in refs.get("attachments") or []]
    have = [(row["digest"], row["name"]) for row in previous if row]
    wanted = [(row[0], row[2]) for row in fetched if row[0] is not None]
    return len(fetched) == len(msg.images) and legacy["body"] == text and have == wanted


def import_messages(c, provider, person, slug, ext_id, messages, images=None, who=None):
    """Copy a transcript into the person's room with the bot. Returns (added, already there)."""
    if not messages:
        return 0, 0
    me, bot = "human:" + person, "bot:" + slug
    room = rooms.personal_room(c, me, slug, subject=provider.label)
    now = H.now()
    added = 0
    place = provider.label.split()[0]
    for msg in messages:
        mid = message_id(provider, ext_id, msg, person)
        if provider.id == "grokbot" and legacy_grok_message(c, room, ext_id, msg, mid, images):
            continue
        sender, target = (me, bot) if msg.role == "user" else (bot, me)
        created = moment(msg.at, now)
        refs = {"quiet": True, provider.key: {"bot": ext_id, "id": msg.id or None}}
        text, attached = msg.text, []
        for row in (images or {}).get(mid) or []:
            if row[0] is None:          # could not be fetched: the link, so it is not lost
                text += f"\n\n[Image in {place}]({row[1]})"
            elif who is not None:
                attached.append(register(c, who, *row))
        if attached:
            refs["attachments"] = attached
        cur = c.execute(
            "INSERT OR IGNORE INTO messages (id, conversation_id, from_actor, to_actor, kind, body, "
            "refs_json, created, delivered_at, read_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (mid, room["id"], sender, target, "say", text, encode(refs), created, now, now))
        added += cur.rowcount
        if cur.rowcount:
            for item in attached:
                c.execute("INSERT INTO message_assets VALUES(?,?)", (mid, item["id"]))
    if added:
        last = c.execute("SELECT max(created) FROM messages WHERE conversation_id=?", (room["id"],)).fetchone()[0]
        c.execute("UPDATE conversations SET last_message_at=? WHERE id=?", (last, room["id"]))
    return added, len(messages) - added


def synced_through(c, provider, person, slug):
    row = c.execute("SELECT max(m.created) FROM messages m JOIN conversations v ON v.id=m.conversation_id "
                    "WHERE v.scope='personal' AND v.owner_actor=? AND v.room_key=? AND m.id LIKE ?",
                    ("human:" + person, slug, provider.key + "-%")).fetchone()
    return row[0] if row else None


def take_inbox(c, provider, person, slug):
    """What the person wrote to the bot in Tico and the bot has not been given yet, oldest first.
    Only their own room: another person's message to it is theirs to relay, not this sync's."""
    room = c.execute("SELECT id FROM conversations WHERE scope='personal' AND owner_actor=? AND room_key=?",
                     ("human:" + person, slug)).fetchone()
    if not room:
        return []
    rows = c.execute("SELECT id,body,created FROM messages WHERE conversation_id=? AND from_actor=? AND to_actor=? "
                     "AND delivered_at IS NULL AND deleted_at IS NULL AND kind='say' AND id NOT LIKE ? "
                     "ORDER BY created LIMIT ?",
                     (room["id"], "human:" + person, "bot:" + slug, provider.key + "-%", INBOX_LIMIT)).fetchall()
    if rows:
        now = H.now()
        c.executemany("UPDATE messages SET delivered_at=? WHERE id=? AND delivered_at IS NULL",
                      [(now, row["id"]) for row in rows])
    return [{"id": row["id"], "text": row["body"], "at": row["created"]} for row in rows]


def sync(c, auth, settings_admin, who, body, images=None):
    allowed(who)
    provider = PROVIDERS[body.provider]
    check(provider, body)
    person = H.actor_id(who.actor)
    from . import views
    roster = views.roster(c)
    group = (P.person(person, roster) or {}).get("team") or ""
    group = group if group in (roster.get("org_groups") or {}) else ""
    now = H.now()
    out = []
    for item in body.bots:
        ext_id = external_id(provider, item)
        slug = find(c, provider, person, ext_id)
        created = not slug
        name = provider.local_name(item.name) if provider.local_name else item.name
        if created:
            slug = slug_for(c, name, provider.id)
            settings_admin.create_bot(c, who, M.BotDefinitionCreate(
                slug=slug, display_name=name, description=item.description,
                reports_to="human:" + person, status="active", thread_mode="personal",
                model=model_for(provider), effort="as-configured", harness=provider.id, operator=person,
                owners=[person]))
        row = c.execute("SELECT config_json,description FROM bot_config WHERE bot=?", (slug,)).fetchone()
        config = H._json(row["config_json"], {}) or {}
        before = dict(config.get(provider.key) or {})
        record = {**before, "id": ext_id, "name": item.name, "last_sync": now,
                  "source": body.source or before.get("source") or ""}
        if "section" in item.model_fields_set:
            record["section"] = item.section.strip()
        if "instructions" in item.model_fields_set:
            record["instructions"] = item.instructions
            record["instructions_hash"] = hashlib.sha256(item.instructions.encode()).hexdigest()[:16]
            if record["instructions_hash"] != before.get("instructions_hash"):
                record["instructions_updated"] = now
        config[provider.key] = record
        config["display_name"] = name
        if group and "team" not in config:      # never set, so not moved since: in its human's group
            config["team"] = group
            c.execute("UPDATE bot_config SET team=? WHERE bot=?", (group, slug))
        c.execute("UPDATE bot_config SET config_json=?,description=? WHERE bot=?",
                  (encode(config), item.description if "description" in item.model_fields_set else row["description"], slug))
        c.execute("UPDATE bots SET display_name=? WHERE slug=?", (name, slug))
        added, known = import_messages(c, provider, person, slug, ext_id, item.messages, images, who)
        H.event(c, who.actor, provider.id + ".synced", slug, {"id": ext_id, "created": created, "messages": added})
        out.append({"id": ext_id, "grok_id": ext_id, "bot": slug, "created": created,
                    "messages_added": added, "messages_known": known,
                    "instructions_changed": record.get("instructions_updated") == now,
                    "synced_through": synced_through(c, provider, person, slug),
                    "inbox": take_inbox(c, provider, person, slug) if body.inbox else []})
    return {"provider": provider.id, "person": person, "bots": out, "synced": now}


def presence(c, bot, harness, config, now=None):
    """`agents.presence` for a synced bot: reporting in means synced lately."""
    provider = PROVIDERS[harness]
    record = config.get(provider.key) or {}
    last = record.get("last_sync")
    now = now or H.now()
    online = bool(last and last > H.shift(now, seconds=-PRESENCE_GAP))
    agent = {"harness": provider.id, "credential": True, "last_seen": last, "revoked_at": None,
             "version": "", "platform": provider.label.lower(), "model": "", "provider": provider.maker,
             "profile": record.get("name") or "", "detail": record.get("source") or "",
             "synced": True, "label": provider.label, "section": record.get("section") or "",
             "instructions_updated": record.get("instructions_updated")}
    return {"online": online, "awake": online, "ready": online, "machine": None, "agent": agent}
