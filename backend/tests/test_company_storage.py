"""The company's own bucket (backend/company_storage.py): who may set it, that its secret never comes back, that new
bytes go to it and are read through Tico, that built-in bytes still open, and that a broken bucket fails loudly.
A fake S3 only: nothing here reaches a network or a real account."""
import hashlib
import io

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from backend import company_storage
from backend.tests.test_api import api, get, headers  # noqa: F401
from backend.tests.test_storage_delivery import attach, task

SECRET = "fake-secret-0123456789abcdefghijklmnop"


class FakeS3:
    """Buckets as dictionaries. `deny` holds operations answered AccessDenied; `offline` refuses every call."""

    def __init__(self):
        self.objects, self.extra, self.deny, self.offline, self.made = {}, {}, set(), False, []

    def _gate(self, operation):
        if self.offline:
            raise EndpointConnectionError(endpoint_url="https://s3.example")
        if operation in self.deny:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "secret material " + SECRET}}, operation)

    def head_object(self, Bucket, Key):
        self._gate("HeadObject")
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Bucket, Key])}

    def upload_fileobj(self, stream, Bucket, Key, ExtraArgs=None):
        self._gate("PutObject")
        self.objects[Bucket, Key], self.extra[Bucket, Key] = stream.read(), dict(ExtraArgs or {})

    def put_object(self, Bucket, Key, Body, **extra):
        self._gate("PutObject")
        self.objects[Bucket, Key], self.extra[Bucket, Key] = Body, extra

    def get_object(self, Bucket, Key, Range=None):
        self._gate("GetObject")
        data = self.objects[Bucket, Key]
        if Range:
            start, _, end = Range.removeprefix("bytes=").partition("-")
            data = data[int(start):int(end) + 1 if end else None]
        return {"Body": io.BytesIO(data)}

    def delete_object(self, Bucket, Key):
        self._gate("DeleteObject")
        self.objects.pop((Bucket, Key), None)


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(company_storage, "make_client", lambda target, secret: fake.made.append(secret) or fake)
    return fake


CHOICE = {"mode": "s3", "bucket": "acme-files", "region": "us-east-1", "prefix": "tico/",
          "access_key_id": "AKIAEXAMPLEKEY1234", "secret_access_key": SECRET}


def put(api, body, token="ana-test"):
    return api.put("/api/v2/team/storage", json=body, headers=headers(token))


def test_only_an_admin_sets_storage_and_the_secret_never_comes_back(api, s3):
    for token in ("cara-test",):
        assert api.get("/api/v2/team/storage", headers=headers(token)).status_code == 403
        assert put(api, CHOICE, token).status_code == 403
        assert api.post("/api/v2/team/storage/test", json=CHOICE, headers=headers(token)).status_code == 403
    assert get(api, "team/storage")["mode"] == "builtin"
    saved = put(api, CHOICE, "ben-test")                      # an admin, not only the owner
    assert saved.status_code == 200, saved.text
    view = saved.json()
    assert (view["mode"], view["bucket"], view["prefix"], view["secret_set"]) == ("s3", "acme-files", "tico", True)
    assert view["access_key_id"] == "…1234"
    for response in (saved, api.get("/api/v2/team/storage", headers=headers())):
        assert SECRET not in response.text and "AKIAEXAMPLEKEY1234" not in response.text
    with api.app.state.store.read() as c:
        row = c.execute("SELECT secret_sealed FROM storage_targets WHERE active=1").fetchone()
        assert row["secret_sealed"] and SECRET not in row["secret_sealed"]
        assert SECRET not in str([tuple(r) for r in c.execute("SELECT * FROM events WHERE action='storage.settings'")])
    # Saving again without the secret keeps the sealed one for the same key ID; the probe object is gone.
    again = put(api, {k: v for k, v in CHOICE.items() if k != "secret_access_key"})
    assert again.status_code == 200 and s3.made[-1] == SECRET
    assert not [key for key in s3.objects if "tico-connection-test" in key[1]]
    # A bucket that fails its test is not saved.
    s3.deny.add("PutObject")
    refused = put(api, {**CHOICE, "bucket": "acme-other"})
    assert refused.status_code == 422 and "Access denied while writing in acme-other" in refused.text
    assert SECRET not in refused.text and get(api, "team/storage")["bucket"] == "acme-files"
    s3.deny.clear()
    assert put(api, {"mode": "builtin"}).json()["mode"] == "builtin"


def test_new_bytes_go_to_the_bucket_and_every_file_still_opens_through_tico(api, s3):
    tid = task(api)
    before = attach(api, tid, "before.txt", b"built-in bytes")
    assert put(api, CHOICE).status_code == 200
    data = b"report bytes for the company bucket"
    digest = hashlib.sha256(data).hexdigest()
    fid = attach(api, tid, "report.txt", data)
    key = ("acme-files", "tico/blobs/" + digest[:2] + "/" + digest)
    assert s3.objects[key] == data
    assert s3.extra[key]["ServerSideEncryption"] == "AES256" and "ACL" not in s3.extra[key]
    storage = api.app.state.blobs
    assert not (storage.directory / storage.key(digest)).exists()
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM blob_targets WHERE digest=?", (digest,)).fetchone()[0] == 1
    opened = api.get("/api/v2/files/" + fid, headers=headers())
    assert opened.status_code == 200 and opened.content == data
    assert "acme-files" not in opened.text and "acme-files" not in str(dict(opened.headers))
    ranged = api.get("/api/v2/files/" + fid, headers={**headers(), "Range": "bytes=0-5"})
    assert ranged.status_code == 206 and ranged.content == data[:6]
    assert api.get("/api/v2/files/" + before, headers=headers()).content == b"built-in bytes"
    # Back to built-in: new bytes stay on this server, and the bucket's files still open from where they are.
    assert put(api, {"mode": "builtin"}).status_code == 200
    after = attach(api, tid, "after.txt", b"local again")
    assert api.get("/api/v2/files/" + after, headers=headers()).content == b"local again"
    assert api.get("/api/v2/files/" + fid, headers=headers()).content == data


def test_an_unreachable_bucket_fails_the_write_and_health_names_it(api, s3):
    tid = task(api)
    before = attach(api, tid, "before.txt", b"built-in bytes")
    assert put(api, CHOICE).status_code == 200
    s3.offline = True
    response = api.post(f"/api/v2/tasks/{tid}/files", files={"file": ("lost.txt", b"never scattered")},
                        headers=headers())
    assert response.status_code == 503, response.text
    assert "Could not reach the storage service while writing in acme-files" in response.json()["error"]["detail"]
    storage = api.app.state.blobs
    digest = hashlib.sha256(b"never scattered").hexdigest()
    assert not (storage.directory / storage.key(digest)).exists()      # no silent fallback to the built-in store
    api.app.state.company_storage.flush()
    checks = {check["id"]: check for check in get(api, "health")["checks"]}
    assert "acme-files" in checks["company_storage"]["summary"] and checks["company_storage"]["status"] == "warn"
    assert api.get("/api/v2/files/" + before, headers=headers()).content == b"built-in bytes"
    s3.offline = False
    tested = api.post("/api/v2/team/storage/test", json={}, headers=headers()).json()
    assert tested == {"ok": True, "error": ""}
    assert "company_storage" not in {check["id"] for check in get(api, "health")["checks"]}


def test_connection_test_reports_a_plain_error(api, s3):
    ok = api.post("/api/v2/team/storage/test", json=CHOICE, headers=headers())
    assert ok.json() == {"ok": True, "error": ""} and not s3.objects
    s3.deny.add("DeleteObject")
    denied = api.post("/api/v2/team/storage/test", json=CHOICE, headers=headers()).json()
    assert denied == {"ok": False, "error": "Access denied while deleting in acme-files: allow s3:DeleteObject"}
    bad = api.post("/api/v2/team/storage/test", json={**CHOICE, "endpoint": "http://minio.acme.example"}, headers=headers())
    assert bad.status_code == 422 and "https" in bad.text
    role = api.post("/api/v2/team/storage/test", json={**CHOICE, "credentials": "role"}, headers=headers())
    assert role.status_code == 422 and "access key" in role.text       # the operator has not offered the server's role
