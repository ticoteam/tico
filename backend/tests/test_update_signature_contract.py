"""A company desktop app updates through a hub redirect to a presigned storage URL, and follows it only when its own
check accepts the URL (app/src/hub.rs has_s3_signature). The server signs that URL with botocore, which signs SigV2
unless told otherwise; a server that did so stranded every company app on its old version while every test passed.
Here the server's real URL meets the parameters the desktop's check reads, taken from the Rust source, and matches
the URL pinned in app/src/testdata that the desktop's own test accepts."""

import os
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

from backend.config import Settings
from backend.downloads import Downloads

ROOT = Path(__file__).resolve().parents[2]
PINNED = ROOT / "app" / "src" / "testdata" / "presigned-artifact-url.txt"


def desktop_check():
    """The query parameters has_s3_signature reads and the algorithm it accepts, from its source."""
    source = (ROOT / "app" / "src" / "hub.rs").read_text()
    body = source[source.index("fn has_s3_signature"):]
    body = body[:body.index("\n}\n")]
    algorithm = re.search(r'"X-Amz-Algorithm" => algorithm = value == "([^"]+)"', body)
    return set(re.findall(r'"(X-Amz-[A-Za-z]+)" =>', body)), algorithm and algorithm.group(1)


def shape(url):
    """What a signer decides: scheme, parameter names, algorithm, signed headers and credential scope."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    return (parts.scheme, sorted(query), query.get("X-Amz-Algorithm"), query.get("X-Amz-SignedHeaders"),
            query.get("X-Amz-Credential", "").split("/")[2:])


@pytest.fixture
def server_url(monkeypatch, tmp_path):
    """The redirect URL the server makes for a company artifact: real botocore, signing offline."""
    import boto3
    from botocore.stub import Stubber
    for name in list(os.environ):
        if name.startswith(("AWS_", "LITESTREAM_", "TICO_BLOB_", "TICO_BACKUP_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing-secret")
    real = boto3.client

    def make(service, **options):
        s3 = real(service, **options)
        stub = Stubber(s3)
        stub.add_response("head_object", {})
        stub.activate()
        return s3
    monkeypatch.setattr(boto3, "client", make)
    downloads = Downloads(Settings(db_path=tmp_path / "hub.db", blob_bucket="acme-files", blob_region="us-west-2"))
    return downloads.file_url("0.3.49", "Acme Tico.app.tar.gz")


def test_the_server_signs_company_artifacts_the_way_the_desktop_app_checks(server_url):
    params, algorithm = desktop_check()
    assert algorithm and len(params) >= 6, "has_s3_signature changed shape; update desktop_check"
    query = dict(parse_qsl(urlsplit(server_url or "").query))
    assert params <= set(query) and query["X-Amz-Algorithm"] == algorithm, server_url
    assert shape(server_url) == shape(PINNED.read_text().strip()), server_url
