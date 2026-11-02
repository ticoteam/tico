"""Public releases: immutable folders of rendered files behind a `current` symlink that swaps atomically.

Rendered files are the only way public content leaves the install. A router in another container reads them, so folders
are 0755, files 0644, the link is relative, and nothing in them is a secret. A release is complete on its own: its
fonts and logo are inside it, so serving it never reaches the company's server, and the router needs nothing outside
this folder. On a cell, settings.company_page_dir sits inside the one folder the router mounts read-only.

Ordering is the point of rebuild(). Files are rendered with no lock held. The swap then happens inside the database's
write transaction, after reading the view once more and finding it unchanged, so:
- the write lock serializes every swap, in this process or another, and rebuilds can't interleave;
- a rebuild that read before someone closed the page can't overwrite the closed release: it waits for that writer to
  commit, sees the view changed, and starts again.
"""
import json
import logging
import os
import re
import secrets
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from . import render

LOG = logging.getLogger(__name__)

DEFAULT_DIR = "company-page-public"
HOSTED_DOMAIN = "tico.page"
STREAM_PAGE = 50
# Static files can't answer ?before=, so a public release holds its whole history, up to these limits.
MAX_PUBLIC_UPDATES = 200
MAX_PUBLIC_UPDATE_BYTES = 4 * 1024 * 1024
MAX_LOGO_BYTES = 2 * 1024 * 1024
LOGO_TYPES = {"image/png": ("png", b"\x89PNG\r\n\x1a\n"), "image/jpeg": ("jpg", b"\xff\xd8\xff"),
              "image/webp": ("webp", b"RIFF")}
STEADY_ATTEMPTS = 5
KEEP_RELEASE_ROWS = 200
ABANDONED_SECONDS = 3600

_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


# ------------------------------------------------------------------------------------------------- where files live
def _root(settings):
    named = str(getattr(settings, "company_page_dir", "") or "").strip()
    if named:
        return Path(named)
    db_path = getattr(settings, "db_path", None)
    return Path(db_path).parent / DEFAULT_DIR if db_path else None


def current_dir(settings):
    """The folder now being served, or None when there has never been a release. An empty folder means nothing is
    public, and every path under it is a 404."""
    root = _root(settings)
    if root is None:
        return None
    current = root / "current"
    return current if current.is_symlink() and current.is_dir() else None


def _settings_of_connection(c):
    """The server's settings, which the store attaches to every connection it hands out. A connection from anywhere else
    falls back to what the server reads from the environment, which counts only if it describes this database: another
    install's folder is never written. The folder served and the folder written must be the same one."""
    server = getattr(c, "settings", None)
    if server is not None:
        return server
    path = next((r[2] for r in c.execute("PRAGMA database_list") if r[1] == "main"), "")
    from ..config import DEFAULT_COMPANY_NAME, Settings
    try:
        settings = Settings.from_env()
        if path and Path(settings.db_path).resolve() == Path(path).resolve():
            return settings
    except Exception:  # noqa: BLE001, S110 - a bad or partial environment means the fallback, never a failed take-back
        pass
    environ = os.environ
    return Settings(db_path=Path(path), company_page_dir=environ.get("TICO_COMPANY_PAGE_DIR", ""),
                    reader_origin=environ.get("TICO_READER_ORIGIN", ""),
                    company_name=environ.get("TICO_TEAM_NAME", "") or environ.get("TICO_COMPANY_NAME", "")
                    or DEFAULT_COMPANY_NAME)


# ------------------------------------------------------------------------------------------------- the view model
def _string(value):
    return value if isinstance(value, str) else ""


def _clean_stat(stat, finance_lines=()):
    """Field by field: whatever else a Stat grows, no other field reaches a reader."""
    series = []
    for point in stat.get("series") or []:
        value = point.get("value")
        finite = isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) != float("inf")
        series.append({"period": _string(point.get("period")), "value": value if finite else None,
                       "display": _string(point.get("display"))})
    chart = stat.get("chart")
    out = {"id": _string(stat.get("id")), "label": _string(stat.get("label")),
           "definition": _string(stat.get("definition")), "chart": chart if chart in ("number", "line", "bars") else "number",
           "period": _string(stat.get("period")), "as_of": _string(stat.get("as_of")),
           "display": _string(stat.get("display")), "stale": stat.get("stale") is True, "series": series}
    if out["id"] in finance_lines:
        out["group"] = "pnl"
    return out


def _clean_update(update):
    author = update.get("author") or {}
    kind = author.get("kind")
    sections = []
    for section in update.get("sections") or []:
        items = [{"body": _string(i.get("body")), "ask_id": i.get("ask_id") if isinstance(i.get("ask_id"), str) else None}
                 for i in section.get("items") or []]
        sections.append({"key": _string(section.get("key")), "items": items})
    version = update.get("version")
    note = update.get("correction_note")
    return {"anchor": render.anchor_of(update.get("anchor")), "title": _string(update.get("title")),
            "author": {"name": _string(author.get("name")), "kind": kind if kind in ("bot", "person", "company") else "person"},
            "period": _string(update.get("period")), "published_at": _string(update.get("published_at")),
            "version": version if isinstance(version, int) and not isinstance(version, bool) else 1,
            "correction_note": note if isinstance(note, str) else None, "sections": sections}


def _sources_of_stats(c, stats):
    ids = [s["id"] for s in stats if s.get("id")]
    if not ids:
        return {}
    rows = c.execute("SELECT id, source_kind FROM page_stats WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)
    return {row["id"]: row["source_kind"] for row in rows}


def _logo_url(c, file_id):
    """A logo's path inside the release, named by its bytes, so it can be compared without reading them."""
    if not file_id:
        return ""
    row = c.execute("SELECT digest, size, content_type FROM blobs WHERE id=?", (file_id,)).fetchone()
    if not row or row["size"] > MAX_LOGO_BYTES or row["content_type"] not in LOGO_TYPES:
        return ""
    return f"assets/logo-{row['digest'][:32]}.{LOGO_TYPES[row['content_type']][0]}"


def _public_url(settings, slug):
    origin = str(getattr(settings, "reader_origin", "") or "").strip()
    if origin:
        return origin.rstrip("/")
    return f"https://{slug}.{HOSTED_DOMAIN}" if isinstance(slug, str) and _SLUG.match(slug) else ""


def _updated_at(stats, updates):
    """When the content last changed, from the content, so an unchanged page stays byte-for-byte unchanged."""
    moments = [u["published_at"] for u in updates] + [s["as_of"] for s in stats]
    return max((m for m in moments if re.match(r"\d{4}-\d{2}-\d{2}", m)), default="")


def view_model(c, audience, stats, updates, next_before=None, badge="private", settings=None):
    """The view model in the package docstring, built field by field from what the projection and the stream returned.
    This is where a value becomes something a reader can see, so a signed-in reader's page is built here too."""
    from . import settings as page_settings
    settings = settings or _settings_of_connection(c)
    page = page_settings.get(c) or {}
    # The mission is the audience's to read or not, by its disclosure rule. An audience with no id (the signed-out door)
    # gets none: there is no rule to read.
    mission = page_settings.mission_for(c, audience["id"]) if audience.get("id") else ""
    sources = _sources_of_stats(c, [s for s in stats if isinstance(s, dict) and isinstance(s.get("id"), str)])
    clean_stats = [_clean_stat(s, {i for i, k in sources.items() if k == "finance_line"}) for s in stats]
    clean_updates = [_clean_update(u) for u in updates]
    company = {"name": _string(getattr(settings, "company_name", "")), "logo_url": _logo_url(c, page.get("logo_file_id")),
               "mission": mission, "badge": badge, "accent": render.accent_of(page.get("accent")),
               "updated_at": _updated_at(clean_stats, clean_updates)}
    url = _public_url(settings, page.get("slug"))
    if url:
        company["url"] = url
    return {"company": company, "audience": {"kind": _string(audience["kind"]), "name": _string(audience["name"])},
            "stats": clean_stats, "updates": clean_updates, "next_before": next_before}


def _whole_stream(c, audience_id, company_name=""):
    """Every published update for the audience, walking the stream's pages, up to what one static page may hold."""
    from . import editions
    found, before, size = [], None, 0
    while len(found) < MAX_PUBLIC_UPDATES:
        batch = list(editions.stream(c, [audience_id], before=before, limit=STREAM_PAGE, company_name=company_name))
        for update in batch:
            size += len(json.dumps(update, default=str))
            if size > MAX_PUBLIC_UPDATE_BYTES or len(found) >= MAX_PUBLIC_UPDATES:
                return found
            found.append(update)
        cursor = batch[-1].get("published_at") if batch else None
        if len(batch) < STREAM_PAGE or not cursor or cursor == before:
            return found
        before = cursor
    return found


def public_view(c, at, settings=None):
    """The public view model, or {} when nothing may be public.

    Nothing is public unless transparency is Open and the Public audience exists and is enabled. A stats board that
    isn't approved means no stats; approved updates still appear, because a person approved each of them. With neither
    stats nor updates there's nothing anyone approved to show, so the page is {} too. Held stats never appear.
    """
    from . import settings as page_settings
    from . import stats as stats_module
    if (page_settings.get(c) or {}).get("transparency") != "open":
        return {}
    row = c.execute("SELECT id, name, kind, enabled FROM audiences WHERE kind='public'").fetchone()
    if not row or row["enabled"] != 1:
        return {}
    stats = []
    if stats_module.board_approved(c, row["id"]) is True:
        stats = list((stats_module.project(c, row["id"], at) or {}).get("stats") or [])
    company = settings or _settings_of_connection(c)
    updates = _whole_stream(c, row["id"], company.company_name)     # the company's own updates carry its name, as on the board
    if not stats and not updates:
        return {}
    sources = _sources_of_stats(c, [s for s in stats if isinstance(s.get("id"), str)])
    # Open company: the company is showing its money. Public: it publishes, and keeps the books to itself.
    badge = "open" if any(k in ("finance_line", "finance_metric") for k in sources.values()) else "public"
    return view_model(c, {"id": row["id"], "kind": "public", "name": row["name"]}, stats, updates, badge=badge,
                      settings=settings)


# ------------------------------------------------------------------------------------------------- files
def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _mkdir(path):
    """Every folder this makes is 0755 whatever the server's umask, because the router runs as someone else and reads
    them through its own mount. A folder that already exists and isn't this user's stays as its owner set it."""
    missing, walk = [], path
    while not walk.exists():
        missing.append(walk)
        walk = walk.parent
    path.mkdir(parents=True, exist_ok=True)
    for made in {path, *missing}:
        try:
            os.chmod(made, 0o755)
        except PermissionError:
            LOG.debug("company page folder %s keeps its owner's mode", made)


def _write(path, data):
    with open(path, "wb") as out:
        os.fchmod(out.fileno(), 0o644)
        out.write(data)
        out.flush()
        os.fsync(out.fileno())


def _new_id():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(4)


def _logo_bytes(c_read, settings, view):
    """The logo's bytes, checked against its stated type, or None. A logo that can't be read never stops a release."""
    url = view["company"]["logo_url"]
    if not url:
        return None
    from ..blobs import Blobs
    try:
        digest_part = url.split("logo-", 1)[1].split(".", 1)[0]
        row = c_read.execute("SELECT digest, content_type FROM blobs WHERE substr(digest,1,32)=? AND size<=? "
                             "AND content_type IN (%s)" % ",".join("?" * len(LOGO_TYPES)),
                             (digest_part, MAX_LOGO_BYTES, *LOGO_TYPES)).fetchone()
        data = Blobs(settings).get(row["digest"])
        return data if data.startswith(LOGO_TYPES[row["content_type"]][1]) and len(data) <= MAX_LOGO_BYTES else None
    except Exception:  # noqa: BLE001 - logged; the release goes out without the logo
        LOG.warning("company page logo could not be read", exc_info=True)
        return None


def _prepare(root, view, logo=None):
    """Writes a whole release to a folder nothing serves, durably. Returns (id, folder)."""
    release_id = _new_id()
    temporary = root / "tmp"
    for folder in (root, temporary, root / "releases"):
        _mkdir(folder)
    folder = temporary / release_id
    _mkdir(folder)
    if not view:
        _fsync_dir(folder)
        return release_id, folder
    assets, fonts = folder / "assets", folder / "assets" / "fonts"
    _mkdir(assets)
    _mkdir(fonts)
    for source in sorted(render.FONT_DIR.iterdir()):
        _write(fonts / source.name, source.read_bytes())
    if logo is not None:
        _write(folder / view["company"]["logo_url"], logo)
    _write(folder / "index.html", render.page(view).encode("utf-8"))
    _write(folder / "data.json", json.dumps(view, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8"))
    _write(folder / "feed.xml", render.feed(view).encode("utf-8"))
    for directory in (fonts, assets, folder):
        _fsync_dir(directory)
    return release_id, folder


def _swap(root, release_id, folder):
    """Moves the finished folder under releases/ and points `current` at it in one rename."""
    target = root / "releases" / release_id
    os.rename(folder, target)
    _fsync_dir(root / "releases")
    link = root / f".current-{release_id}"
    os.symlink(os.path.join("releases", release_id), link)
    os.replace(link, root / "current")
    _fsync_dir(root)


def _sweep(root, keep):
    """Old releases go at the swap; taken-back content must not sit on disk. Runs with the write lock held, so it can't
    race another swap. A leftover is retried at the next swap, and `current` no longer reaches it."""
    def remove(path):
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError:
            LOG.warning("company page could not remove %s", path, exc_info=True)

    for entry in (root / "releases").iterdir():
        if entry.name != keep:
            remove(entry)
    horizon = time.time() - ABANDONED_SECONDS
    for entry in [*(root / "tmp").iterdir(), *root.glob(".current-*")]:
        try:
            if entry.lstat().st_mtime < horizon:
                remove(entry)
        except OSError:
            pass


def _discard(folder):
    shutil.rmtree(folder, ignore_errors=True)


def _commit(c, root, release_id, folder, reason):
    """The row first: it takes the write lock if this connection doesn't hold it yet, and only the holder may swap."""
    c.execute("INSERT INTO page_releases (id, path, reason, created_at) VALUES (?,?,?,?)",
              (release_id, f"releases/{release_id}", str(reason or "")[:500], datetime.now(timezone.utc).isoformat()))
    _swap(root, release_id, folder)
    _sweep(root, release_id)
    c.execute("DELETE FROM page_releases WHERE id NOT IN (SELECT id FROM page_releases ORDER BY created_at DESC, id DESC LIMIT ?)",
              (KEEP_RELEASE_ROWS,))


def _result(release_id, view, failed=False):
    return {"id": release_id, "empty": not view, "failed": failed}


def _safe_view(view, logo):
    """A logo that can't be read is left out rather than shown broken."""
    if view and view["company"]["logo_url"] and logo is None:
        return {**view, "company": {**view["company"], "logo_url": ""}}
    return view


def _rebuild_on_connection(c, reason, at):
    """For a caller already in its write transaction, so a change that takes something away lands in the same step. This
    is the one path that renders inside a write transaction, the caller's; rendering takes milliseconds. If the caller
    then rolls back the files stay as they are until the next rebuild, which is why publishing goes through the store
    after commit and this path is for taking things away."""
    settings = _settings_of_connection(c)
    root = _root(settings)
    if root is None:
        raise RuntimeError("company page folder is not known")
    began = not c.in_transaction
    if began:
        c.execute("BEGIN IMMEDIATE")
    try:
        failed = False
        try:
            view = public_view(c, at, settings)
            logo = _logo_bytes(c, settings, view) if view else None
            view = _safe_view(view, logo)
            release_id, folder = _prepare(root, view, logo)
        except Exception:
            LOG.exception("company page release failed; closing the page")
            view, failed = {}, True
            release_id, folder = _prepare(root, {})
        _commit(c, root, release_id, folder, f"{reason} (failed, closed)" if failed else reason)
        if began:
            c.execute("COMMIT")
        return _result(release_id, view, failed)
    except BaseException:
        if began and c.in_transaction:
            c.execute("ROLLBACK")
        raise


def _fail_closed(store, root, reason):
    """Unknown state means no access. Rather than leave old files up, the page goes empty even if the database can't be
    written just now; only the record of it is lost then."""
    LOG.exception("company page release failed; closing the page")
    release_id, folder = _prepare(root, {})
    try:
        with store.transaction() as c:
            _commit(c, root, release_id, folder, f"{reason} (failed, closed)")
    except Exception:
        LOG.exception("company page could not record its closing release")
        if not folder.exists():     # the swap itself failed, so old files are still up: the caller has to know
            raise
        _swap(root, release_id, folder)
    return _result(release_id, {}, True)


def _rebuild_on_store(store, reason, at):
    settings = store.settings
    root = _root(settings)
    if root is None:
        raise RuntimeError("company page folder is not known")
    folder = None
    try:
        for _ in range(STEADY_ATTEMPTS):
            with store.read_transaction() as c:
                view = public_view(c, at, settings)
                logo = _logo_bytes(c, settings, view) if view else None
            shown = _safe_view(view, logo)
            release_id, folder = _prepare(root, shown, logo)
            with store.transaction() as c:
                if public_view(c, at, settings) == view:
                    _commit(c, root, release_id, folder, reason)
                    return _result(release_id, shown)
            _discard(folder)
            folder = None
        # The page's data kept changing under the render. Being current beats being outside the lock for once.
        with store.transaction() as c:
            view = public_view(c, at, settings)
            logo = _logo_bytes(c, settings, view) if view else None
            shown = _safe_view(view, logo)
            release_id, folder = _prepare(root, shown, logo)
            _commit(c, root, release_id, folder, reason)
            return _result(release_id, shown)
    except Exception:  # noqa: BLE001 - logged in _fail_closed, which takes the page down rather than leave old files up
        if folder is not None:
            _discard(folder)
        return _fail_closed(store, root, reason)


def rebuild(store_or_c, reason, at=None):
    """Renders what may be public now and swaps it in. When nothing may be public the swap is to an empty release, so every
    path 404s. Old release folders are deleted at the swap. If anything fails while reading or rendering, the page closes
    and the failure is logged; a swap that can't happen raises.

    With a store: read, render, swap, with no write transaction held while rendering. This is how publishing and daily
    stats go. With a connection in a write transaction: the same step as the caller's change, for taking things away.

    Returns {"id", "empty", "failed"}.
    """
    at = at or datetime.now(timezone.utc)
    if hasattr(store_or_c, "transaction"):
        return _rebuild_on_store(store_or_c, reason, at)
    return _rebuild_on_connection(store_or_c, reason, at)


def _served(settings):
    current = current_dir(settings)
    if current is None:
        return {}
    try:
        return json.loads((current / "data.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def refresh(store, at):
    """The daily job: rebuild only if what's public would change, so a quiet day writes nothing. A failure here leaves the
    page as it is, since nothing is being taken away, and tomorrow tries again. A rebuild that does run closes on failure
    as usual. Returns rebuild's result, or None when nothing changed."""
    try:
        with store.read_transaction() as c:
            view = public_view(c, at, store.settings)
    except Exception:
        LOG.exception("company page daily refresh could not read")
        return None
    served = _served(store.settings)
    if view == served or (view and served and view == {**served, "company": {**served["company"], "logo_url": view["company"]["logo_url"]}}):
        return None
    return rebuild(store, "daily stats", at=at)
