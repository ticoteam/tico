"""The desktop app's downloads and updates.

CI builds the app for macOS, Windows and Linux (`.github/workflows/app.yml`) and attaches the
bundles and manifest to each GitHub release. An optional deploy job also publishes under
`releases/app/` in the storage bucket. Three routes serve them, none needing a sign-in (the app's
updater has no browser session, and an installer is nothing to protect):

- `GET /download/latest.json` — only the environment bucket manifest, in the shape Tauri's updater reads
  (`version`, `pub_date`, `platforms[<target>].url/.signature`), plus `installers[<os>]`
  for people: the file to hand a visitor on each OS.
- `GET /download/{os}` — `mac`, `windows` or `linux`: redirects to the current installer.
- `GET /download/file/{version}/{name}` — one bundle, as a short-lived S3 link.

`GET /api/download/{os}` is the signed-in question the site asks before it offers a download.
Without a bucket manifest, downloads come from the running version's public GitHub release.
A company bucket build always wins, including when it predates the server.
The updater feed never falls back to a generic GitHub build, even if the bucket build is older.
GitHub lookups (including failures) are cached for ten minutes and never carry credentials.

The no-sign-in promise holds on the runner hostname (`runner.<host>`), which the tunnel routes
for `/api/v2` and `/download` (your tunnel's public hostname rules) and which the updater
and the installer links use (`settings.runner_url`). On the main hostname Cloudflare Access
answers first, so a `/download/...` link there works only for a signed-in browser.
"""
import json
import re
import time
import threading
from urllib.parse import quote, unquote, urlsplit

import httpx

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from .store import Problem
from . import blob_s3, releases

PREFIX = "releases/app/"
MANIFEST_MAX_BYTES = 1024 * 1024
OS_NAMES = ("mac", "windows", "linux")
FILE_RE = re.compile(r"^[\w][\w. +()-]{0,200}$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?(?:\+[A-Za-z0-9.-]+)?$")


class Downloads:
    def __init__(self, settings, s3=None, github_transport=None, s3_source=None):
        self.settings = settings
        self.bucket = settings.blob_bucket
        prefix = getattr(settings, "blob_prefix", "")
        self.prefix = (prefix + "/" if prefix else "") + PREFIX
        self.base = settings.runner_url or settings.public_url
        self._s3 = s3
        self._s3_source = s3_source or blob_s3.Sources(settings)
        self._manifest = (0.0, None)
        self.version = releases.version()
        self._github = (0.0, None)
        self._github_transport = github_transport
        self._lock = threading.Lock()
        self._manifest_ready = threading.Event()
        self._manifest_ready.set()
        self._github_ready = threading.Event()
        self._github_ready.set()

    @property
    def s3(self):
        return self._s3 if self._s3 is not None else self._s3_source.s3

    def read_s3(self, operation, **options):
        if self._s3 is not None:
            return getattr(self._s3, operation)(**options), self._s3
        return self._s3_source.read_s3(operation, **options)

    def manifest(self):
        """Prefer the company's build; otherwise offer the running release's generic app."""
        bucket = self.bucket_manifest()
        if bucket:
            return bucket
        return self.github_manifest()

    def bucket_manifest(self):
        """Read the bucket at most once a minute, including absent manifests."""
        if not self.bucket:
            return None
        with self._lock:
            fetched, cached = self._manifest
            if fetched and time.monotonic() - fetched < 60:
                return cached
            fetching = not self._manifest_ready.is_set()
            if not fetching:
                self._manifest_ready.clear()
        if fetching:
            if cached is not None:
                return cached
            self._manifest_ready.wait(0.1)
            with self._lock:
                return self._manifest[1]
        value = None
        try:
            response, _ = self.read_s3("get_object", Bucket=self.bucket, Key=self.prefix + "latest.json")
            stream = response["Body"]
            try:
                body = stream.read(MANIFEST_MAX_BYTES + 1)
                if len(body) > MANIFEST_MAX_BYTES:
                    raise ValueError("app manifest too large")
            finally:
                stream.close()
            value = json.loads(body)
            value = self.company_manifest(value)
        except Exception:
            value = None
        finally:
            with self._lock:
                self._manifest = (time.monotonic(), value)
                self._manifest_ready.set()
        return value

    def company_manifest(self, value):
        # Older environment manifests have no app_kind. Keep those installations working,
        # but an explicitly generic manifest must never update a company's app.
        if (not isinstance(value, dict) or value.get("app_kind", "company") != "company"
                or not VERSION_RE.fullmatch(str(value.get("version") or ""))
                or not releases.parse(value["version"])):
            return None
        platforms, installers = value.get("platforms"), value.get("installers")
        if not isinstance(platforms, dict) or not platforms or not isinstance(installers, dict):
            return None
        safe_platforms = {}
        for target, entry in platforms.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("signature"), str) or not entry["signature"].strip():
                return None
            address = urlsplit(str(entry.get("url") or ""))
            # Publish routes, including retained older-platform versions, are the only
            # permitted updater source. Reconstruct with this hub's configured origin.
            root = urlsplit(self.base).path.rstrip("/") + "/download/file/"
            if not address.path.startswith(root):
                return None
            parts = address.path[len(root):].split("/")
            if len(parts) != 2:
                return None
            version, name = map(unquote, parts)
            if not VERSION_RE.fullmatch(version) or not FILE_RE.fullmatch(name):
                return None
            safe_platforms[target] = {"signature": entry["signature"],
                                      "url": f"{self.base}/download/file/{quote(version, safe='')}/{quote(name)}"}
        safe_installers = {}
        for os_name, entry in installers.items():
            if not isinstance(entry, dict) or not FILE_RE.fullmatch(str(entry.get("file") or "")):
                return None
            # Company human downloads also stay on the hub's authorized bucket path.
            safe_installers[os_name] = {k: v for k, v in entry.items() if k != "url"}
        return {**value, "platforms": safe_platforms, "installers": safe_installers, "app_kind": "company"}

    def github_manifest(self):
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
        value = None
        if VERSION_RE.fullmatch(self.version):
            tag = "v" + self.version
            asset_base = f"https://github.com/ticoteam/tico/releases/download/{quote(tag, safe='')}/"
            try:
                # A dedicated public client never inherits hub sessions, tokens, proxy credentials or netrc.
                with httpx.Client(timeout=5, trust_env=False, transport=self._github_transport,
                                  follow_redirects=True) as http:
                    response = http.get("https://api.github.com/repos/ticoteam/tico/releases/tags/" + quote(tag, safe=""),
                                        headers={"Accept": "application/vnd.github+json", "User-Agent": "tico-desktop-downloads"})
                    response.raise_for_status()
                    release = response.json()
                    if release.get("tag_name") != tag or release.get("draft"):
                        raise ValueError("wrong release")
                    assets = {a["name"]: a for a in release.get("assets", [])
                              if isinstance(a, dict) and FILE_RE.fullmatch(str(a.get("name") or ""))
                              and a.get("browser_download_url") == asset_base + quote(a["name"])}
                    if "latest.json" not in assets:
                        raise ValueError("no app manifest")
                    response = http.get(assets["latest.json"]["browser_download_url"])
                    response.raise_for_status()
                    value = response.json()
                    # A release whose desktop shell did not change carries the previous app forward: the app's
                    # version may be older than the server's, but its files are assets of this release.
                    if not isinstance(value, dict) or not VERSION_RE.fullmatch(str(value.get("version") or "")):
                        raise ValueError("wrong app version")
                    platforms = value.get("platforms")
                    if not isinstance(platforms, dict) or not platforms:
                        raise ValueError("no updater platforms")
                    asset_urls = {a["browser_download_url"] for a in assets.values()}
                    for entry in platforms.values():
                        if not isinstance(entry, dict) or not entry.get("signature") or entry.get("url") not in asset_urls:
                            raise ValueError("invalid updater asset")
                    installers = {}
                    for name, asset in assets.items():
                        os_name = ("mac" if name.endswith(".dmg") else "windows" if name.endswith("-setup.exe")
                                   else "linux" if name.endswith(".AppImage") else "linux_deb" if name.endswith(".deb") else None)
                        if os_name:
                            metadata = (value.get("installers") or {}).get(os_name) or {}
                            installers[os_name] = {"file": name, "bytes": asset.get("size"),
                                                   "url": asset["browser_download_url"],
                                                   "signed": bool(metadata.get("signed", False)),
                                                   "notarized": bool(metadata.get("notarized", False))}
                    value = {**value, "installers": installers, "app_kind": "generic"}
            except Exception:
                value = None
        return value

    def file_url(self, version, name):
        if not self.bucket or not VERSION_RE.fullmatch(version) or not FILE_RE.fullmatch(name):
            return None
        key = f"{self.prefix}{version}/{name}"
        try:
            _, s3 = self.read_s3("head_object", Bucket=self.bucket, Key=key)
            return s3.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=600)
        except Exception:
            return None

    def installer(self, os_name):
        manifest = self.manifest()
        if not manifest or os_name not in OS_NAMES:
            return None
        entry = (manifest.get("installers") or {}).get(os_name)
        if not isinstance(entry, dict) or not FILE_RE.fullmatch(str(entry.get("file") or "")):
            return None
        return {"version": manifest["version"], "file": entry["file"], "bytes": entry.get("bytes"),
                "app_kind": manifest.get("app_kind", "company"),
                "notarized": bool(entry.get("notarized", False)), "signed": bool(entry.get("signed", False)),
                "url": entry.get("url") or f"{self.base}/download/file/{quote(manifest['version'], safe='')}/{quote(entry['file'])}"}


def install_downloads(app, store):
    downloads = Downloads(store.settings, s3_source=app.state.blobs)
    app.state.downloads = downloads

    @app.get("/download/latest.json")
    def latest(request: Request):
        manifest = downloads.bucket_manifest()
        if not manifest:
            return JSONResponse({"error": {"code": "not_found", "detail": "No app build is published"}}, status_code=404)
        return JSONResponse(manifest, headers={"Cache-Control": "no-cache"})

    @app.get("/download/file/{version}/{name}")
    def file(request: Request, version: str, name: str):
        url = downloads.file_url(version, name)
        if not url:
            raise Problem("not_found", "No such app build", 404)
        return RedirectResponse(url, status_code=302)

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
        return {"available": True, "version": entry["version"], "url": entry["url"], "file": entry["file"],
                "app_kind": entry["app_kind"],
                "size_mb": round(size / 1048576) if isinstance(size, (int, float)) and size else None,
                "notarized": entry["notarized"], "signed": entry["signed"]}
