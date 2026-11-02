"""The Company page's switch and its look. Closed is the default: an install with no row here publishes nothing.

Taking anything away happens in the caller's transaction, rebuild included, so a page is never closed in the database and
still served from disk."""
import re
import sqlite3

from .. import company as C
from .. import hubdb as H
from ..store import Problem

TRANSPARENCY = ("closed", "private", "open")
FIELDS = ("slug", "accent", "logo_file_id")
# The name a logo upload is stored under: its file name starts with this, which marks it as the page's own.
LOGO_NAME = "company-page-logo"
# One DNS label, lower case, under the hosted address. `xn--` is out because a punycode name can look like another
# company's in the address bar.
SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
RESERVED_SLUGS = frozenset({"www", "api", "app", "admin", "mail", "static", "assets", "hooks", "status", "help"})
ACCENT = re.compile(r"#[0-9a-fA-F]{6}")      # it reaches the page's stylesheet, so nothing but a colour gets through
FILE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def require_person(c, actor):
    """Every write on this surface: a person on the roster. A bot never changes what leaves the install, whatever its
    other rights; BotOps arrives here as the person it acts for, so it follows them."""
    if not H.is_human(actor):
        raise Problem("forbidden", "Only a person changes the Company page", 403)
    H._writer(c, actor)
    return actor


def rebuild(c, reason):
    from . import release
    release.rebuild(c, reason)


def rebuild_if_live(c, reason):
    """A closed page has nothing served, so there is nothing to take back."""
    if get(c)["transparency"] != "closed":
        rebuild(c, reason)


def published_vision(c):
    """The team's vision as everyone inside reads it, with no fallback to the strategy's summary: that summary is
    written for bots and was never meant to leave. Falls back to the older profile row a team may still have."""
    for sql in ("SELECT vision FROM strategy_versions WHERE status='published' ORDER BY n DESC LIMIT 1",
                "SELECT vision FROM strategy_versions WHERE status='draft'"):
        try:
            row = c.execute(sql).fetchone()
        except sqlite3.OperationalError:
            row = None
        if row and row[0]:
            return " ".join(str(row[0]).split())
    try:
        return " ".join(str(C.profile(c)["vision"]).split())
    except sqlite3.OperationalError:
        return ""


def mission_for(c, audience_id):
    """The mission as this audience may read it: empty unless its disclosure says so, since Quiet means nothing."""
    from . import disclosure
    return published_vision(c) if disclosure.rule_for(c, audience_id, "mission")["mode"] != "hidden" else ""


def get(c):
    row = H._one(c, "SELECT * FROM company_page_settings WHERE id='company'") or {}
    transparency = row.get("transparency")
    return {"transparency": transparency if transparency in TRANSPARENCY else "closed", "slug": row.get("slug"),
            "accent": row.get("accent"), "logo_file_id": row.get("logo_file_id"), "updated_at": row.get("updated_at"),
            "updated_by": row.get("updated_by"), "mission": published_vision(c)}


def _save(c, actor, **changed):
    ts = H.now()
    names = list(changed)
    c.execute(f"INSERT INTO company_page_settings (id, {', '.join(names)}, updated_at, updated_by) "
              f"VALUES ('company', {', '.join('?' * len(names))}, ?, ?) ON CONFLICT(id) DO UPDATE SET "
              f"{', '.join(f'{n}=excluded.{n}' for n in names)}, updated_at=excluded.updated_at, "
              "updated_by=excluded.updated_by", (*changed.values(), ts, actor))


def set_transparency(c, actor, value):
    """Closed, Private or Open. Every change rebuilds in this step: Open to anything else must stop serving at once."""
    require_person(c, actor)
    if value not in TRANSPARENCY:
        raise Problem("transparency", f"Transparency is {', '.join(TRANSPARENCY)}", 422)
    before = get(c)["transparency"]
    if value == before:
        return get(c)
    _save(c, actor, transparency=value)
    if value == "open":
        # Open means Public is on, in the same step: a page with no public audience would have nothing to serve.
        c.execute("UPDATE audiences SET enabled=1, updated_at=? WHERE kind='public' AND enabled=0", (H.now(),))
    H.event(c, actor, "company_page.transparency", "company", {"from": before, "to": value})
    rebuild(c, f"transparency {before} to {value}")
    return get(c)


def clean_slug(value):
    if value in (None, ""):
        return None
    slug = str(value).strip().lower()
    if not SLUG.fullmatch(slug) or slug.startswith("xn--"):
        raise Problem("slug", "The address is one label of letters, numbers and hyphens, 63 characters at most", 422)
    if slug in RESERVED_SLUGS:
        raise Problem("slug", f"{slug} is reserved", 422)
    return slug


def address(slug, app):
    """The page's full address, or None when it has none: the install's own reader origin, else the slug under the page
    domain. `app` is the install's Settings."""
    origin = (app.reader_origin or "").rstrip("/")
    if origin:
        return origin
    return f"https://{slug}.{app.page_domain}" if slug and app.page_domain else None


def _clean(c, name, value):
    if name == "slug":
        return clean_slug(value)
    if value in (None, ""):
        return None
    if name == "accent":
        if not ACCENT.fullmatch(str(value)):
            raise Problem(name, "The accent is a colour like #2255aa", 422)
        return str(value)
    # A logo goes out on a public page, so it is only ever a picture the Company page itself uploaded. Any other file in
    # the library, however much a picture it is, would become public on this one field.
    found = FILE_ID.fullmatch(str(value)) and c.execute(
        "SELECT content_type, name FROM blobs WHERE id=?", (str(value),)).fetchone()
    if not found or not str(found[0]).startswith("image/") or not str(found[1]).startswith(LOGO_NAME):
        raise Problem(name, "The logo is an image uploaded to the Company page", 422)
    return str(value)


def update(c, actor, **fields):
    """Address, accent and logo; `transparency` here is the same as set_transparency. What a live page shows changes in
    this step."""
    require_person(c, actor)
    unknown = sorted(set(fields) - {*FIELDS, "transparency"})
    if unknown:
        raise Problem("fields", f"Unknown: {', '.join(unknown)}", 422)
    wanted = {name: _clean(c, name, value) for name, value in fields.items() if name in FIELDS}
    before = get(c)
    changed = {name: value for name, value in wanted.items() if value != before[name]}
    if changed:
        _save(c, actor, **changed)
        H.event(c, actor, "company_page.settings", "company", {"fields": sorted(changed)})
    if "transparency" in fields and fields["transparency"] != before["transparency"]:
        return set_transparency(c, actor, fields["transparency"])
    if changed:
        rebuild_if_live(c, "settings changed")
    return get(c)
