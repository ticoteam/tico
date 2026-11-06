"""Face photos for the org tree.

Prefers a Google Workspace Directory thumbnail when the mail service-account key is present
and domain-wide delegation includes `admin.directory.user.readonly`. Otherwise the roster
`photo` URL (the Slack profile image) is used by the photo endpoint as a redirect.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

DIRECTORY_SCOPE = "https://www.googleapis.com/auth/admin.directory.user.readonly"
PHOTO_TTL = 7 * 24 * 3600
# A person with no Workspace photo is remembered for a day. Without it every page load asked
# Google again for each of them (a token and a Directory call, up to seconds each).
MISS_TTL = 24 * 3600


def cache_dir(settings):
    root = Path(settings.blob_dir or Path(settings.db_path).parent / "blobs")
    return root / "people-photos"


def _key_path():
    raw = (os.environ.get("GOOGLE_SA_KEY") or "").strip()
    return Path(raw).expanduser() if raw else None


def google_photo(email, subject=None):
    """JPEG/PNG bytes from Workspace, or None when the Directory API is not granted."""
    email = str(email or "").strip().lower()
    path = _key_path()
    if not email or "@" not in email or path is None or not path.is_file():
        return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        return None
    subject = subject or email
    try:
        creds = service_account.Credentials.from_service_account_file(str(path), scopes=[DIRECTORY_SCOPE]).with_subject(
            subject
        )
        service = build("admin", "directory_v1", credentials=creds, cache_discovery=False)
        photo = service.users().photos().get(userKey=email).execute()
        data = photo.get("photoData") or ""
        if not data:
            return None
        import base64

        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
        mime = str(photo.get("mimeType") or "image/jpeg")
        return raw, mime
    except Exception:
        return None


def cached(settings, email):
    directory = cache_dir(settings)
    digest = hashlib.sha256(str(email or "").strip().lower().encode()).hexdigest()
    for suffix, mime in ((".jpg", "image/jpeg"), (".png", "image/png"), (".webp", "image/webp")):
        path = directory / (digest + suffix)
        try:
            if path.exists() and time.time() - path.stat().st_mtime < PHOTO_TTL:
                return path.read_bytes(), mime
        except Exception:
            continue
    return None


def store(settings, email, data, mime):
    directory = cache_dir(settings)
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(email or "").strip().lower().encode()).hexdigest()
    suffix = ".png" if "png" in (mime or "") else ".webp" if "webp" in (mime or "") else ".jpg"
    path = directory / (digest + suffix)
    path.write_bytes(data)
    return data, mime or "image/jpeg"


def _miss_path(settings, email):
    digest = hashlib.sha256(str(email or "").strip().lower().encode()).hexdigest()
    return cache_dir(settings) / (digest + ".none")


def recent_miss(settings, email):
    path = _miss_path(settings, email)
    try:
        return path.exists() and time.time() - path.stat().st_mtime < MISS_TTL
    except Exception:
        return False


def may_have(settings, email, photo=None):
    """Whether asking for this person's photo can find one: a roster photo, a cached Workspace one, or a Workspace
    key with no recent miss. Without one the page shows initials and makes no request that would only 404."""
    if photo:
        return True
    if settings is not None and cached(settings, email):
        return True
    path = _key_path()
    return bool(path and path.is_file() and email and not (settings is not None and recent_miss(settings, email)))


def load(settings, email, subject=None):
    """Cached Workspace bytes, a fresh Directory fetch, or None (remembered for a day)."""
    hit = cached(settings, email)
    if hit:
        return hit
    if recent_miss(settings, email):
        return None
    fetched = google_photo(email, subject=subject)
    if fetched:
        return store(settings, email, fetched[0], fetched[1])
    try:
        path = _miss_path(settings, email)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except Exception:
        pass
    return None


# A roster photo on Slack's
# image hosts is fetched once, kept a week next to the Workspace ones, and served by the hub, so
# the page makes no redirect and no second connection to Slack per face. Only https on Slack's
# own hosts (and Gravatar, which two roster photos use) is fetched, never an arbitrary URL;
# anything else is still a redirect.
SLACK_PHOTO_HOSTS = ("slack-edge.com", "slack.com", "slack-files.com", "gravatar.com")
REMOTE_MAX_BYTES = 2_000_000


def _slack_url(url):
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in SLACK_PHOTO_HOSTS)


def remote_photo(settings, url, fetch=None):
    """Kept bytes of a Slack roster photo, fetched once a week, or None (the caller redirects)."""
    if not _slack_url(url):
        return None
    key = "url:" + str(url)
    hit = cached(settings, key)
    if hit:
        return hit
    if recent_miss(settings, key):
        return None
    try:
        data, mime = (fetch or _fetch)(url)
    except Exception:
        data, mime = None, None
    if data and str(mime or "").startswith("image/"):
        return store(settings, key, data, mime)
    try:
        path = _miss_path(settings, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except Exception:
        pass
    return None


def _fetch(url):
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "Tico/1.0 (+https://tico.team)"})
    with urllib.request.urlopen(request, timeout=5) as response:
        data = response.read(REMOTE_MAX_BYTES + 1)
        if len(data) > REMOTE_MAX_BYTES:
            return None, None
        return data, response.headers.get_content_type()
