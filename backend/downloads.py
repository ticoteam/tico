"""The desktop app's downloads.

CI builds the Electron app for macOS, Windows and Linux (`.github/workflows/desktop.yml`) and attaches its
installers (`Tico-<version>-...`, desktop/electron-builder.yml) to the running version's public GitHub release.
The app updates itself from that release's feeds (desktop/src/main/updater.ts), never from this server. Two routes
hand the installers to people:

- `GET /download/{os}` — `mac`, `windows` or `linux`: redirects to the current installer, with no sign-in (an
  installer is nothing to protect).
- `GET /api/download/{os}` — the signed-in question the site asks before it offers a download.

GitHub lookups (including failures) are cached for ten minutes and never carry credentials.

The no-sign-in promise holds on the runner hostname (`runner.<host>`), which the tunnel routes
for `/api/v2` and `/download` (your tunnel's public hostname rules) and which the installer
links use (`settings.runner_url`). On the main hostname Cloudflare Access
answers first, so a `/download/...` link there works only for a signed-in browser.
"""

import re
import time
import threading
from urllib.parse import quote

import httpx

from fastapi import Request
from fastapi.responses import RedirectResponse

from .store import Problem
from . import releases

OS_NAMES = ("mac", "windows", "linux")
# The routes under /download/ that need no sign-in (backend/app.py): one installer per system.
PUBLIC_PATHS = frozenset("/download/" + os_name for os_name in OS_NAMES)
FILE_RE = re.compile(r"^[\w][\w. +()-]{0,200}$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?(?:\+[A-Za-z0-9.-]+)?$")
_VERSION = r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?)"
# The Electron app's installers on a GitHub release, best first: Apple chip only from v0.6.146, universal before.
# Its signing is decided in CI (desktop.yml) and recorded nowhere on the release, so these are offered as unsigned.
ELECTRON_INSTALLERS = {
    "mac": (re.compile(rf"^Tico-{_VERSION}-arm64\.dmg$"), re.compile(rf"^Tico-{_VERSION}-universal\.dmg$")),
    "windows": (re.compile(rf"^Tico-{_VERSION}-setup\.exe$"),),
    "linux": (re.compile(rf"^Tico-{_VERSION}-x86_64\.AppImage$"),),
    "linux_deb": (re.compile(rf"^Tico-{_VERSION}-amd64\.deb$"),),
}


def electron_installers(assets):
    """The Electron app's installer for each system the release has one for, as unsigned (see ELECTRON_INSTALLERS)."""
    found = {}
    for os_name, patterns in ELECTRON_INSTALLERS.items():
        for pattern in patterns:
            # Normally one per release; should a release carry two versions, the newest.
            matches = [
                (releases.parse(m["version"]), name, m["version"]) for name in assets if (m := pattern.match(name))
            ]
            matches = [m for m in matches if m[0]]
            if matches:
                _, name, version = max(matches)
                asset = assets[name]
                found[os_name] = {
                    "file": name,
                    "bytes": asset.get("size"),
                    "url": asset["browser_download_url"],
                    "version": version,
                    "signed": False,
                    "notarized": False,
                }
                break
    return found


class Downloads:
    def __init__(self, settings, github_transport=None):
        self.settings = settings
        self.version = releases.version()
        self._github = (0.0, None)
        self._github_transport = github_transport
        self._lock = threading.Lock()
        self._github_ready = threading.Event()
        self._github_ready.set()

    def manifest(self):
        """The running release's installers, from GitHub."""
        with self._lock:
            fetched, cached = self._github
            # A miss is retried after a minute: desktop files reach a release a few minutes after it is published.
            if fetched and time.monotonic() - fetched < (600 if cached is not None else 60):
                return cached
            fetching = not self._github_ready.is_set()
            if not fetching:
                self._github_ready.clear()
        if fetching:
            if cached is not None:
                return cached
            # Cold-cache callers wait briefly, never for the network timeout.
            self._github_ready.wait(0.1)
            with self._lock:
                return self._github[1]
        value = None
        try:
            value = self._fetch_github_manifest()
        finally:
            with self._lock:
                self._github = (time.monotonic(), value)
                self._github_ready.set()
        return value

    def _fetch_github_manifest(self):
        if not VERSION_RE.fullmatch(self.version):
            return None
        tag = "v" + self.version
        asset_base = f"https://github.com/ticoteam/tico/releases/download/{quote(tag, safe='')}/"
        try:
            # A dedicated public client never inherits hub sessions, tokens, proxy credentials or netrc.
            with httpx.Client(
                timeout=5, trust_env=False, transport=self._github_transport, follow_redirects=True
            ) as http:
                response = http.get(
                    "https://api.github.com/repos/ticoteam/tico/releases/tags/" + quote(tag, safe=""),
                    headers={"Accept": "application/vnd.github+json", "User-Agent": "tico-desktop-downloads"},
                )
                response.raise_for_status()
                release = response.json()
            if release.get("tag_name") != tag or release.get("draft"):
                return None
            assets = {
                a["name"]: a
                for a in release.get("assets", [])
                if isinstance(a, dict)
                and FILE_RE.fullmatch(str(a.get("name") or ""))
                and a.get("browser_download_url") == asset_base + quote(a["name"])
            }
            installers = electron_installers(assets)
            if not installers:
                return None
            # The web app offers only a `generic` download (web/src/features/notices/queries.ts downloadHref).
            return {"version": self.version, "installers": installers, "app_kind": "generic"}
        except Exception:
            return None

    def installer(self, os_name):
        manifest = self.manifest()
        if not manifest or os_name not in OS_NAMES:
            return None
        entry = (manifest.get("installers") or {}).get(os_name)
        if not isinstance(entry, dict) or not FILE_RE.fullmatch(str(entry.get("file") or "")):
            return None
        return {
            "version": entry["version"],
            "file": entry["file"],
            "bytes": entry.get("bytes"),
            "app_kind": manifest["app_kind"],
            "notarized": bool(entry.get("notarized", False)),
            "signed": bool(entry.get("signed", False)),
            "url": entry["url"],
        }


def install_downloads(app, store):
    downloads = Downloads(store.settings)
    app.state.downloads = downloads

    @app.get("/download/{os_name}")
    def installer(request: Request, os_name: str):
        entry = downloads.installer(os_name)
        if not entry:
            raise Problem("not_found", "No app build is published for that system", 404)
        return RedirectResponse(entry["url"], status_code=302)

    @app.get("/api/download/{os_name}")
    def describe(request: Request, os_name: str):
        entry = downloads.installer(os_name)
        if not entry:
            return {"available": False}
        size = entry.get("bytes")
        return {
            "available": True,
            "version": entry["version"],
            "url": entry["url"],
            "file": entry["file"],
            "app_kind": entry["app_kind"],
            "size_mb": round(size / 1048576) if isinstance(size, (int, float)) and size else None,
            "notarized": entry["notarized"],
            "signed": entry["signed"],
        }
