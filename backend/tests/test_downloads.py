"""The desktop app's downloads (backend/downloads.py): the Electron app's installers from the running GitHub release,
each system's link served without a sign-in."""

from types import SimpleNamespace

import httpx
import pytest

from backend.downloads import Downloads

from backend.tests.helpers.api import headers  # noqa: F401

BASE = "https://github.com/ticoteam/tico/releases/download/v0.3.7/"
ELECTRON = ["Tico-0.3.6-arm64.dmg", "Tico-0.3.6-setup.exe", "Tico-0.3.6-x86_64.AppImage", "Tico-0.3.6-amd64.deb"]
# Files beside the installers that share a prefix but are not ones: the updater's zip, blockmaps, feeds.
ELECTRON_OTHER = [
    "Tico-0.3.6-arm64.zip",
    "Tico-0.3.6-arm64.dmg.blockmap",
    "Tico-0.3.6-setup.exe.blockmap",
    "latest-mac.yml",
]
# What a release of the retired Tauri app carried: its updater manifest and its own installers.
TAURI = ["latest.json", "Tico_universal.dmg", "Tico.app.tar.gz", "Tico_x64-setup.exe", "Tico.AppImage", "Tico.deb"]
TAURI_MANIFEST = {
    "version": "0.3.7",
    "platforms": {"darwin-aarch64": {"url": BASE + "Tico.app.tar.gz", "signature": "sig"}},
    "installers": {"mac": {"file": "Tico_universal.dmg", "signed": True, "notarized": True}},
}
# A server with no storage bucket (the Tauri app's builds were once read from one).
SETTINGS = SimpleNamespace(blob_bucket="", runner_url="", public_url="")


def release_of(names, version="0.3.7"):
    base = f"https://github.com/ticoteam/tico/releases/download/v{version}/"
    return {
        "tag_name": "v" + version,
        "assets": [{"name": n, "browser_download_url": base + n, "size": 12_582_912} for n in names],
    }


def github(release, requests=None):
    def get(request):
        if requests is not None:
            requests.append(request)
        if request.url.host == "api.github.com":
            return httpx.Response(200, json=release)
        # The release's own files: only the Tauri app's manifest was ever read.
        assert str(request.url) == BASE + "latest.json"
        return httpx.Response(200, json=TAURI_MANIFEST)

    return httpx.MockTransport(get)


def github_downloads(release):
    downloads = Downloads(SETTINGS, github_transport=github(release))
    downloads.version = "0.3.7"
    return downloads


def serve(api, release, requests=None):
    """Point the server's own Downloads at a fake GitHub release."""
    built = api.app.state.downloads
    built.version, built._github, built._github_transport = "0.3.7", (0.0, None), github(release, requests)
    return built


def test_only_each_systems_installer_link_needs_no_sign_in(api):
    serve(api, release_of(ELECTRON + ELECTRON_OTHER))
    for os_name, name in [
        ("mac", "Tico-0.3.6-arm64.dmg"),
        ("windows", "Tico-0.3.6-setup.exe"),
        ("linux", "Tico-0.3.6-x86_64.AppImage"),
    ]:
        response = api.get("/download/" + os_name, follow_redirects=False)  # no Authorization header at all
        assert response.status_code == 302 and response.headers["location"] == BASE + name
    # The Tauri updater's manifest and bundle routes are gone, and nothing else under /download/ skips the sign-in.
    for path in ["/download/latest.json", "/download/file/0.3.6/Tico-0.3.6-arm64.dmg", "/download/linux_deb"]:
        assert api.get(path, follow_redirects=False).status_code == 401, path
    assert api.get("/download/latest.json", headers=headers()).status_code == 404


def test_the_running_github_release_offers_the_electron_installers_without_credentials(api):
    requests = []
    serve(api, release_of(ELECTRON + ELECTRON_OTHER), requests)
    # The Electron app may be carried forward from an earlier release, so its version is its own; its signing is
    # recorded nowhere on the release, so it is never claimed.
    assert api.get("/api/download/mac", headers=headers()).json() == {
        "available": True,
        "version": "0.3.6",
        "url": BASE + "Tico-0.3.6-arm64.dmg",
        "file": "Tico-0.3.6-arm64.dmg",
        "app_kind": "generic",
        "size_mb": 12,
        "notarized": False,
        "signed": False,
    }
    described = api.get("/api/download/linux", headers=headers()).json()
    assert described["file"] == "Tico-0.3.6-x86_64.AppImage" and described["app_kind"] == "generic"
    assert api.get("/download/windows", follow_redirects=False).headers["location"] == BASE + "Tico-0.3.6-setup.exe"
    assert len(requests) == 1, "cached"
    assert all("authorization" not in r.headers and "cookie" not in r.headers for r in requests)
    assert requests[0].url.path.endswith("/tags/v0.3.7")


def test_a_release_with_only_the_tauri_app_offers_nothing(api):
    requests = []
    serve(api, release_of(TAURI), requests)
    assert api.get("/api/download/mac", headers=headers()).json() == {"available": False}
    assert api.get("/download/windows", follow_redirects=False).status_code == 404
    assert [r.url.host for r in requests] == ["api.github.com"], "the Tauri manifest is never read"


def test_a_release_with_both_apps_offers_only_the_electron_installers():
    downloads = github_downloads(release_of(TAURI + ["Tico-0.3.6-arm64.dmg"]))
    assert downloads.manifest() == {
        "version": "0.3.7",
        "app_kind": "generic",
        "installers": {
            "mac": {
                "file": "Tico-0.3.6-arm64.dmg",
                "bytes": 12_582_912,
                "url": BASE + "Tico-0.3.6-arm64.dmg",
                "version": "0.3.6",
                "signed": False,
                "notarized": False,
            }
        },
    }
    assert downloads.installer("windows") is None


def test_github_release_with_a_universal_electron_mac_build_offers_it():
    installers = github_downloads(release_of(["Tico-0.6.145-universal.dmg"])).manifest()["installers"]
    assert installers["mac"]["file"] == "Tico-0.6.145-universal.dmg" and installers["mac"]["signed"] is False


def test_github_release_prefers_the_apple_chip_build_and_reads_prerelease_names():
    downloads = github_downloads(release_of(["Tico-0.3.7-rc.2-universal.dmg", "Tico-0.3.7-rc.2-arm64.dmg"]))
    assert downloads.manifest()["installers"]["mac"]["file"] == "Tico-0.3.7-rc.2-arm64.dmg"


def test_github_release_with_no_installers_is_unavailable():
    downloads = github_downloads(release_of(ELECTRON_OTHER))
    assert downloads.manifest() is None and downloads.installer("mac") is None


def test_assets_off_this_release_and_draft_releases_are_not_offered():
    release = release_of(["Tico-0.3.6-arm64.dmg"])
    release["assets"][0]["browser_download_url"] = "https://elsewhere.example.com/Tico-0.3.6-arm64.dmg"
    assert github_downloads(release).manifest() is None
    assert github_downloads({**release_of(ELECTRON), "draft": True}).manifest() is None
    assert github_downloads(release_of(ELECTRON, version="0.3.8")).manifest() is None


@pytest.mark.parametrize("failure", ["offline", "missing"])
def test_github_failure_is_unavailable_and_cached(api, failure):
    requests = []

    def get(request):
        requests.append(request)
        if failure == "offline":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(404, json={})

    built = serve(api, {})
    built._github_transport = httpx.MockTransport(get)
    assert api.get("/api/download/mac", headers=headers()).json() == {"available": False}
    assert api.get("/download/mac", follow_redirects=False).status_code == 404
    assert len(requests) == 1


@pytest.mark.parametrize("warm", [False, True])
def test_github_fetch_is_single_flight_and_does_not_hold_lock(warm, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    downloads = Downloads(SETTINGS)
    stale = {"version": "0.3.6"} if warm else None
    found = {"version": "0.3.7", "installers": {}, "app_kind": "generic"}
    # Expiry is independent of machine uptime, including a freshly booted CI worker.
    monkeypatch.setattr("backend.downloads.time", SimpleNamespace(monotonic=lambda: 1000.0))
    downloads._github = (399.0, stale)
    entered, finish = threading.Event(), threading.Event()
    calls = []

    def fetch():
        calls.append(True)
        entered.set()
        assert finish.wait(10)
        return found

    downloads._fetch_github_manifest = fetch
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(downloads.manifest)
        assert entered.wait(10)
        try:
            waits = []
            monkeypatch.setattr(downloads._github_ready, "wait", lambda timeout: waits.append(timeout) or False)
            assert pool.submit(downloads.manifest).result(timeout=10) is stale
            assert waits == ([] if warm else [0.1]), "only cold callers take the bounded wait"
            assert len(calls) == 1
        finally:
            finish.set()
        assert first.result(timeout=10) == found
    assert downloads.manifest() == found and len(calls) == 1
