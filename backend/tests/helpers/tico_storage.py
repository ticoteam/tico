"""Helpers that other tests share, moved out of test_tico_storage.py so they import no test file."""

import base64
import hashlib
import hmac
import io
import json
import time
from datetime import datetime, timedelta, timezone
import boto3
import pytest
from botocore.exceptions import ClientError
from backend import tico_storage
from backend.tests.helpers.storage_delivery import S3


SECRET = "tico-hosting-secret-access-key-01"


TOKEN = "tico-hosting-session-token-01"


KEY, KEY2 = "AKIATICOHOSTING0001", "AKIATICOHOSTING0002"


SIGNING = "0123456789abcdef0123456789abcdef0123456789abcdef"


ADDRESS = "acme-files.ticofiles.com"


OFF = {
    "state": "off",
    "writes": False,
    "reason": None,
    "access": "none",
    "address": None,
    "bucket": None,
    "region": None,
    "prefix": None,
    "credentials": None,
    "signing": None,
    "keep_until": None,
}


class Bucket(S3):
    """Tico's bucket: keeps each object's type and disposition as S3 does, and answers them on HEAD. A file's bytes
    leave it only through the edge (`Edge`): a direct read is refused, as reads there would go unmetered."""

    def __init__(self):
        super().__init__()
        self.meta, self.metadata = {}, {}

    def get_object(self, **kw):
        from backend.company_storage import CHECK

        if self.objects.get(kw['Key']) != CHECK:  # the write check reads back its few bytes itself
            raise ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'read it through the edge'}}, 'GetObject')
        return super().get_object(**kw)

    def put_object(self, **kw):
        super().put_object(**kw)
        self.meta[kw['Key']] = (kw.get('ContentType'), kw.get('ContentDisposition'))
        self.metadata[kw['Key']] = dict(kw.get('Metadata') or {})

    def complete_multipart_upload(self, **kw):
        super().complete_multipart_upload(**kw)

    def head_object(self, **kw):
        found = super().head_object(**kw)
        kind, how = self.meta.get(kw['Key'], (None, None))
        return {
            **found,
            **({'ContentType': kind} if kind else {}),
            **({'ContentDisposition': how} if how else {}),
            'Metadata': dict(self.metadata.get(kw['Key'], {})),
        }


class NoWrite(S3):
    def put_object(self, **kw):
        raise ClientError({'Error': {'Code': 'AccessDenied', 'Message': SECRET}}, 'PutObject')


class HQ:
    """Tico HQ's file-hosting endpoints: GET /files answers `self.answer`; POST /files/total is recorded."""

    def __init__(self):
        self.answer, self.status, self.asked = dict(OFF), 200, []

    def __call__(self, request, timeout):
        body = json.loads(request.data) if request.data else None
        self.asked.append((request.get_method(), request.full_url, request.get_header("Authorization"), body))
        if self.status != 200:
            import urllib.error

            raise urllib.error.HTTPError(request.full_url, self.status, "no", {}, io.BytesIO(b'{"error":"not_found"}'))
        out = json.dumps(self.answer if request.get_method() == "GET" else body).encode()

        class Answer(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return Answer(out)


class Edge:
    """Tico's edge for the company's address (DevOps R6): checks the link and serves the company's folder, counting
    each byte it delivers by address, as Dev 316's meter does from the edge's logs."""

    def __init__(self, bucket):
        self.bucket, self.delivered, self.paths = bucket, {}, []

    def __call__(self, request, timeout):
        import urllib.error

        link = urllib.parse.urlsplit(request.full_url)
        query = dict(urllib.parse.parse_qsl(link.query))
        mac = hmac.new(SIGNING.encode(), f"{link.netloc}\n{link.path}\n{query['e']}".encode(), hashlib.sha256).digest()
        if (
            link.netloc != ADDRESS
            or query['s'] != base64.urlsafe_b64encode(mac).rstrip(b'=').decode()
            or int(query['e']) < time.time()
        ):
            raise urllib.error.HTTPError(request.full_url, 403, "refused", {}, io.BytesIO(b''))
        options = {'Key': 'c/acme' + link.path}
        if request.get_header('Range'):
            options['Range'] = request.get_header('Range')
        try:
            body = S3.get_object(self.bucket, Bucket='tico-files-us', **options)['Body'].read()
        except ClientError:
            raise urllib.error.HTTPError(request.full_url, 404, "missing", {}, io.BytesIO(b'')) from None
        self.delivered[link.netloc] = self.delivered.get(link.netloc, 0) + len(body)
        self.paths.append(link.path)
        return io.BytesIO(body)


class Owner:
    actor = 'human:ana'


def stamp(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def on(state="on", key=KEY, token=TOKEN, expires=60, **extra):
    """HQ's answer (tico-services gateway/files.py) for the app's state `state`."""
    hq, writes, reason, access = {
        "on": ("on", True, None, "read_write"),
        "credits_out": ("on", False, "out_of_credit", "read_only"),
        "stopped": ("stopped", False, "stopped", "read_only"),
    }[state]
    return {
        "state": hq,
        "writes": writes,
        "reason": reason,
        "access": access,
        "address": ADDRESS,
        "bucket": "tico-files-us",
        "region": "us-east-1",
        "prefix": "c/acme/",
        "credentials": {
            "access_key_id": key,
            "secret_access_key": SECRET,
            "session_token": token,
            "expires": stamp(expires),
        },
        "signing": {"key_id": "k1", "secret": SIGNING},
        "keep_until": stamp(30 * 24 * 60) if hq == "stopped" else None,
        **extra,
    }


@pytest.fixture
def tico(api, monkeypatch):
    """The Tico bucket (signed by HQ's keys and session token), the company's own bucket, the server's, and HQ."""
    bucket = Bucket()
    made = {KEY: bucket, KEY2: bucket, 'AKIAEXAMPLEGOOD0001': S3(), 'AKIAEXAMPLENOWR0001': NoWrite(), 'server': S3()}
    for s3 in made.values():
        s3.close = lambda: None
    signed = []

    def client(service, **options):
        if 'aws_access_key_id' not in options:
            return made['server']
        if options['aws_access_key_id'] in (KEY, KEY2):
            assert options['aws_secret_access_key'] == SECRET and options['aws_session_token'].startswith(TOKEN)
            signed.append(options['aws_session_token'])
        return made[options['aws_access_key_id']]

    monkeypatch.setattr(boto3, 'client', client)
    monkeypatch.setenv('TICO_GATEWAY_KEY', 'gk_test_0123456789abcdef')
    monkeypatch.setenv('TICO_GATEWAY_URL', 'https://gateway.example')
    api.app.state.blobs._loop = None
    hq = HQ()
    hq.signed = signed
    hq.edge = Edge(bucket)
    monkeypatch.setattr(tico_storage, 'fetch', hq.edge)
    app = api.app

    def sync(answer=None, report=False):
        if answer is not None:
            hq.answer = answer
        return tico_storage.sync(app.state.store, app.state.vault, app.state.blobs, opener=hq, report=report)

    return made, hq, sync
