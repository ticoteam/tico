"""The desktop app's downloads (backend/downloads.py): the manifest and the installers come from
`releases/app/` in the bucket or the running GitHub release, served without a sign-in."""
import io
import json
from types import SimpleNamespace

import httpx
import pytest

from backend.downloads import Downloads, MANIFEST_MAX_BYTES

from backend.tests.test_api import api, headers  # noqa: F401

MANIFEST = {"version": "2.1.0", "pub_date": "2026-09-19T00:00:00Z",
            "platforms": {"darwin-aarch64": {"url": "https://runner.test/download/file/2.1.0/Tico.app.tar.gz", "signature": "sig"}},
            "installers": {"mac": {"file": "Tico_2.1.0_universal.dmg", "bytes": 12_582_912, "signed": True, "notarized": True},
                           "windows": {"file": "Tico_2.1.0_x64-setup.exe", "bytes": 9_000_000}}}


class FakeS3:
    def __init__(self):
        self.keys = {"releases/app/latest.json": json.dumps(MANIFEST).encode(),
                     "releases/app/2.1.0/Tico_2.1.0_universal.dmg": b"dmg"}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.keys[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self.keys:
            raise KeyError(Key)

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://s3.test/{Params['Key']}?signed"


def test_installers_and_the_manifest_are_served_without_a_sign_in(api):
    # The routes hold the Downloads instance they were built with: point it at a fake bucket.
    built = api.app.state.downloads
    built.bucket, built.base, built._s3, built._manifest = "b", "https://runner.test", FakeS3(), (0.0, None)

    r = api.get("/download/latest.json")                        # no Authorization header at all
    assert r.status_code == 200 and r.json()["version"] == "2.1.0"
    assert r.json()["platforms"]["darwin-aarch64"]["signature"] == "sig"
    r = api.get("/download/mac", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://runner.test/download/file/2.1.0/Tico_2.1.0_universal.dmg"
    r = api.get("/download/file/2.1.0/Tico_2.1.0_universal.dmg", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("https://s3.test/releases/app/2.1.0/")
    assert api.get("/download/file/2.1.0/missing.dmg", follow_redirects=False).status_code == 404
    assert api.get("/download/linux", follow_redirects=False).status_code == 404   # not in this manifest
    described = api.get("/api/download/mac", headers=headers()).json()
    assert described == {"available": True, "version": "2.1.0", "file": "Tico_2.1.0_universal.dmg", "size_mb": 12,
                         "url": "https://runner.test/download/file/2.1.0/Tico_2.1.0_universal.dmg", "notarized": True, "signed": True, "app_kind": "company"}
    assert api.get("/api/download/windows", headers=headers()).json()["notarized"] is False


def github_fixture(version="0.3.7"):
    base = f"https://github.com/ticoteam/tico/releases/download/v{version}/"
    names = ["latest.json", "Tico_universal.dmg", "Tico.app.tar.gz", "Tico_x64-setup.exe", "Tico.AppImage", "Tico.deb"]
    value = {"version": version, "pub_date": "2026-10-02T00:00:00Z",
             "platforms": {"darwin-aarch64": {"url": base + "Tico.app.tar.gz", "signature": "sig"}},
             "installers": {"mac": {"notarized": True, "signed": True}}}
    release = {"tag_name": "v" + version, "assets": [
        {"name": name, "browser_download_url": base + name, "size": 12_582_912} for name in names]}
    return base, release, value


def test_no_bucket_uses_running_github_release_and_caches_without_credentials(api):
    base, release, value = github_fixture()
    requests = []

    def get(request):
        requests.append(request)
        assert "authorization" not in request.headers and "cookie" not in request.headers
        if request.url.host == "api.github.com":
            assert request.url.path.endswith("/tags/v0.3.7")
            return httpx.Response(200, json=release)
        assert str(request.url) == base + "latest.json"
        return httpx.Response(200, json=value)

    built = api.app.state.downloads
    built.bucket, built.version = "", "0.3.7"
    built._github_transport = httpx.MockTransport(get)
    assert api.get("/download/latest.json").status_code == 404
    assert not requests
    for os_name, name in [("mac", "Tico_universal.dmg"), ("windows", "Tico_x64-setup.exe"), ("linux", "Tico.AppImage")]:
        response = api.get("/download/" + os_name, follow_redirects=False)
        assert response.status_code == 302 and response.headers["location"] == base + name
        described = api.get("/api/download/" + os_name, headers=headers()).json()
        assert described["available"] is True and described["url"] == base + name
        assert described["app_kind"] == "generic"
    assert len(requests) == 2


@pytest.mark.parametrize("bucket_version,github_expected", [("0.3.6", False)])
def test_bucket_version_selection(bucket_version, github_expected):
    base, release, value = github_fixture()
    requests = []

    def get(request):
        requests.append(request)
        return httpx.Response(200, json=release if request.url.host == "api.github.com" else value)

    s3 = FakeS3()
    s3.keys["releases/app/latest.json"] = json.dumps({**MANIFEST, "version": bucket_version}).encode()
    downloads = Downloads(SimpleNamespace(blob_bucket="b", runner_url="https://runner.example.com", public_url=""),
                          s3, httpx.MockTransport(get))
    downloads.version = "0.3.7"
    assert downloads.manifest()["version"] == ("0.3.7" if github_expected else bucket_version)
    assert bool(requests) == github_expected


def test_github_failure_is_unavailable_and_cached(api, failure="offline"):
    requests = []

    def get(request):
        requests.append(request)
        if failure == "offline":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(404 if failure == "missing" else 200, json=[])

    built = api.app.state.downloads
    built.bucket, built.version = "", "0.3.7"
    built._github_transport = httpx.MockTransport(get)
    assert api.get("/api/download/mac", headers=headers()).json() == {"available": False}
    assert api.get("/download/latest.json").status_code == 404
    assert api.get("/download/mac", follow_redirects=False).status_code == 404
    assert len(requests) == 1


def test_updater_keeps_environment_build_even_when_older_than_server(api):
    built = api.app.state.downloads
    built.bucket, built._s3, built.version = "b", FakeS3(), "3.0.0"
    built._github_transport = httpx.MockTransport(lambda request: pytest.fail("Updater must not fetch GitHub"))
    assert api.get("/download/latest.json").json()["version"] == MANIFEST["version"]


@pytest.mark.parametrize("change", [
    {"app_kind": "generic"},
    {"platforms": {"windows-x86_64": {"url": "https://runner.test/download/file/2.1.0/Tico.exe", "signature": ""}}},
])
def test_updater_rejects_generic_and_invalid_bucket_manifests_without_github(api, change):
    built = api.app.state.downloads
    s3 = FakeS3()
    s3.keys["releases/app/latest.json"] = json.dumps({**MANIFEST, **change}).encode()
    built.bucket, built.base, built._s3 = "b", "https://runner.test", s3
    built._github_transport = httpx.MockTransport(lambda request: pytest.fail("Updater must not fetch GitHub"))
    assert api.get("/download/latest.json").status_code == 404


def test_company_download_urls_stay_on_this_hub_and_manifest_read_is_bounded():
    s3 = FakeS3()
    value = {**MANIFEST, "installers": {"mac": {**MANIFEST["installers"]["mac"], "url": "https://elsewhere.example.com/file"}}}
    s3.keys["releases/app/latest.json"] = json.dumps(value).encode()
    downloads = Downloads(SimpleNamespace(blob_bucket="b", runner_url="https://runner.test", public_url=""), s3)
    assert downloads.installer("mac")["url"].startswith("https://runner.test/download/file/")
    s3.keys["releases/app/latest.json"] = b" " * MANIFEST_MAX_BYTES + json.dumps(value).encode()
    downloads._manifest = (0.0, None)
    assert downloads.bucket_manifest() is None


@pytest.mark.parametrize('failure', ['read'])
def test_bucket_manifest_reads_are_bounded_closed_and_failures_cached(failure):
    from backend.downloads import MANIFEST_MAX_BYTES

    calls = []
    class Body(io.BytesIO):
        def read(self, size=-1):
            assert size == MANIFEST_MAX_BYTES + 1
            if failure == 'read':
                raise RuntimeError('private credential material')
            return super().read(size)
    body = Body(b'x' * (MANIFEST_MAX_BYTES + 2) if failure == 'oversize' else b'{invalid')
    class S3:
        def get_object(self, **options):
            calls.append(options)
            return {'Body': body}
    downloads = Downloads(SimpleNamespace(blob_bucket='acme-files', runner_url='', public_url=''), S3())
    assert downloads.bucket_manifest() is None
    assert body.closed
    assert downloads.bucket_manifest() is None and len(calls) == 1


@pytest.mark.parametrize('warm', [False])
def test_bucket_manifest_fetch_is_single_flight_without_holding_lock(warm, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    entered, release = threading.Event(), threading.Event()
    calls, bodies = [], []
    class S3:
        def get_object(self, **options):
            calls.append(options)
            entered.set()
            assert release.wait(10)
            body = io.BytesIO(json.dumps(MANIFEST).encode())
            bodies.append(body)
            return {'Body': body}
    downloads = Downloads(SimpleNamespace(blob_bucket='acme-files', runner_url='https://runner.test', public_url=''), S3())
    stale = {'version': '0.3.6'} if warm else None
    monkeypatch.setattr('backend.downloads.time', SimpleNamespace(monotonic=lambda: 1000.0))
    downloads._manifest = (939.0, stale)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(downloads.bucket_manifest)
        try:
            assert entered.wait(10)
            waits = []
            monkeypatch.setattr(downloads._manifest_ready, 'wait', lambda timeout: waits.append(timeout) or False)
            assert pool.submit(downloads.bucket_manifest).result(timeout=10) == stale
            assert waits == ([] if warm else [0.1]), 'only cold callers take the bounded wait'
            assert len(calls) == 1
        finally:
            release.set()
        assert first.result(timeout=10) == {**MANIFEST, 'app_kind': 'company'}
    assert downloads.bucket_manifest() == {**MANIFEST, 'app_kind': 'company'} and len(calls) == 1
    assert bodies[0].closed


@pytest.mark.parametrize("warm", [False])
def test_github_fetch_is_single_flight_and_does_not_hold_lock(warm, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    downloads = Downloads(SimpleNamespace(blob_bucket="", runner_url="", public_url=""))
    stale = {"version": "0.3.6"} if warm else None
    # Expiry is independent of machine uptime, including a freshly booted CI worker.
    monkeypatch.setattr("backend.downloads.time", SimpleNamespace(monotonic=lambda: 1000.0))
    downloads._github = (399.0, stale)
    entered, finish = threading.Event(), threading.Event()
    calls = []
    def fetch():
        calls.append(True)
        entered.set()
        assert finish.wait(10)
        return MANIFEST
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
        assert first.result(timeout=10) == MANIFEST
    assert downloads.manifest() == MANIFEST and len(calls) == 1


def desktop_follows(url):
    """The signed-storage redirect the desktop updater follows (app/src/hub.rs has_s3_signature)."""
    import re
    from urllib.parse import parse_qsl, urlsplit
    query = dict(parse_qsl(urlsplit(url).query))
    return (urlsplit(url).scheme == "https" and query.get("X-Amz-Algorithm") == "AWS4-HMAC-SHA256"
            and bool(query.get("X-Amz-Credential")) and bool(re.fullmatch(r"[0-9a-fA-F]{64}", query.get("X-Amz-Signature", "")))
            and bool(re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", query.get("X-Amz-Date", "")))
            and int(query.get("X-Amz-Expires", "0")) > 0 and "host" in query.get("X-Amz-SignedHeaders", "").split(";"))


@pytest.fixture
def stubbed_s3(monkeypatch):
    """Real botocore clients, signing offline; each answers HeadObject, plus whatever a test queues."""
    import os
    import boto3
    from botocore.stub import Stubber
    from backend import blob_s3
    # Building a real client on a loaded box can outlast the 5 s production budget, and file_url
    # turns that timeout into None; these tests check signing, not that budget.
    monkeypatch.setattr(blob_s3, "CLIENT_BUILD_TIMEOUT", 60)
    for name in list(os.environ):
        if name.startswith(("AWS_", "LITESTREAM_", "TICO_BLOB_", "TICO_BACKUP_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing-secret")
    real, made = boto3.client, []
    def make(service, **options):
        s3 = real(service, **options)
        stub = Stubber(s3)
        stub.add_response("head_object", {})
        stub.activate()
        made.append((s3, stub))
        return s3
    monkeypatch.setattr(boto3, "client", make)
    return made


def credential_region(url):
    from urllib.parse import parse_qsl, urlsplit
    return dict(parse_qsl(urlsplit(url).query))["X-Amz-Credential"].split("/")[2]


def test_company_artifact_redirect_is_sigv4_for_the_configured_region(stubbed_s3, tmp_path):
    from backend.config import Settings
    downloads = Downloads(Settings(db_path=tmp_path / "hub.db", blob_bucket="acme-files", blob_region="us-west-2"))
    url = downloads.file_url("0.3.49", "Acme Tico.app.tar.gz")
    assert desktop_follows(url), url
    assert credential_region(url) == "us-west-2"
    assert len(stubbed_s3) == 1, "a configured region needs no lookup and no second client"


@pytest.mark.parametrize("answer", ["refused", "allowed"])
def test_company_artifact_redirect_signs_for_the_buckets_region_when_none_is_configured(answer, stubbed_s3, tmp_path):
    from backend.config import Settings
    downloads = Downloads(Settings(db_path=tmp_path / "hub.db", blob_bucket="acme-files"))
    reader = downloads._s3_source.client_for(downloads._s3_source.kind, reads=True)
    stub = stubbed_s3[0][1]
    # S3 names a bucket's region on HeadBucket even when it refuses the caller ListBucket.
    if answer == "refused":
        stub.add_client_error("head_bucket", "403", http_status_code=403,
                              response_meta={"HTTPHeaders": {"x-amz-bucket-region": "us-west-2"}})
    else:
        stub.add_response("head_bucket", {"BucketRegion": "us-west-2"})
    stub.add_response("head_object", {})
    first = downloads.file_url("0.3.49", "Acme Tico.app.tar.gz")
    assert reader.meta.region_name == "us-east-1"
    assert desktop_follows(first) and credential_region(first) == "us-west-2", first
    second = downloads.file_url("0.3.49", "Acme Tico.app.tar.gz")
    assert desktop_follows(second) and credential_region(second) == "us-west-2", second
    stub.assert_no_pending_responses()
    assert len(stubbed_s3) == 2, "the region is looked up once and its signer is reused"


def test_unknown_bucket_region_still_signs_sigv4_and_is_not_looked_up_on_every_download(stubbed_s3, tmp_path, monkeypatch):
    from backend import blob_s3
    from backend.config import Settings
    downloads = Downloads(Settings(db_path=tmp_path / "hub.db", blob_bucket="acme-files"))
    downloads._s3_source.client_for(downloads._s3_source.kind, reads=True)
    stub = stubbed_s3[0][1]
    lookups, lookup = [], blob_s3.bucket_region
    monkeypatch.setattr(blob_s3, "bucket_region", lambda s3, bucket: lookups.append(bucket) or lookup(s3, bucket))
    stub.add_client_error("head_bucket", "500", http_status_code=500)
    stub.add_response("head_object", {})
    for _ in range(2):
        url = downloads.file_url("0.3.49", "Acme Tico.app.tar.gz")
        assert desktop_follows(url) and credential_region(url) == "us-east-1"
    assert len(lookups) == 1


def test_server_wiring_through_file_storage_signs_for_the_buckets_region(stubbed_s3, tmp_path):
    """The server builds Downloads on the attachment store (install_downloads), not its own S3 source."""
    from backend.blobs import Blobs
    from backend.config import Settings
    settings = Settings(db_path=tmp_path / "hub.db", blob_bucket="acme-files")
    blobs = Blobs(settings)
    downloads = Downloads(settings, s3_source=blobs)
    blobs._sources.client_for(blobs._sources.kind, reads=True)
    stubbed_s3[0][1].add_client_error("head_bucket", "403", http_status_code=403,
                                      response_meta={"HTTPHeaders": {"x-amz-bucket-region": "us-west-2"}})
    url = downloads.file_url("0.3.49", "Acme Tico.app.tar.gz")
    assert desktop_follows(url) and credential_region(url) == "us-west-2", url
