"""A company's own S3 bucket (backend/company_storage.py): owner only, secret sealed, files keep opening."""

import json
import threading

import boto3
import pytest
from botocore.exceptions import ClientError

from backend import company_storage
from backend.blobs import Blobs
from backend.tests.helpers.api import headers, post  # noqa: F401
from backend.tests.helpers.backup_bundle import ObjectStore
from backend.tests.helpers.storage_delivery import S3, attach, task

SECRET = "example-secret-access-key-0001"


class Denied(S3):
    def put_object(self, **kw):
        raise ClientError({'Error': {'Code': 'AccessDenied', 'Message': SECRET}}, 'PutObject')


class NoList(S3):
    """Keys without s3:ListBucket: S3 answers a missing file with AccessDenied, not NoSuchKey."""

    reads = 0

    def get_object(self, **kw):
        self.reads += 1
        if not self.offline and kw['Key'] not in self.objects:
            raise ClientError({'Error': {'Code': 'AccessDenied'}}, 'GetObject')
        return super().get_object(**kw)


class NoAbort(S3):
    """Writes, but cannot cancel an upload: the server's write probe would fail after the save."""

    def abort_multipart_upload(self, **kw):
        raise ClientError({'Error': {'Code': 'AccessDenied', 'Message': SECRET}}, 'AbortMultipartUpload')


@pytest.fixture
def buckets(monkeypatch):
    """One fake S3 per access key ID; each records the bucket it was asked for."""
    made = {
        'AKIAEXAMPLEGOOD0001': NoList(),
        'AKIAEXAMPLEGOOD0002': NoList(),
        'AKIAEXAMPLEDENY0001': Denied(),
        'AKIAEXAMPLENOAB0001': NoAbort(),
        'server': S3(),
    }
    for s3 in made.values():
        s3.close = lambda: None

    def client(service, **options):
        if 'aws_access_key_id' not in options:
            return made['server']
        assert options['aws_secret_access_key'] == SECRET
        return made[options['aws_access_key_id']]

    monkeypatch.setattr(boto3, 'client', client)
    return made


def body(bucket='acme-files', key='AKIAEXAMPLEGOOD0001', secret=SECRET, prefix='tico'):
    return {
        'bucket': bucket,
        'region': 'us-west-2',
        'prefix': prefix,
        'access_key_id': key,
        'secret_access_key': secret,
    }


def download(api, fid):
    response = api.get(f'/api/v2/files/{fid}', headers=headers())
    assert response.status_code == 200, response.text
    return response.content


def test_owner_sets_bucket_secret_stays_sealed_and_every_file_keeps_opening(api, buckets):
    blobs = api.app.state.blobs
    blobs._loop = None  # copies run below, in this thread
    tid = task(api)
    before = attach(api, tid, 'before.txt', b'stored on the server disk')
    assert api.put('/api/v2/system/storage', json=body(), headers=headers('ben-test')).status_code == 403
    denied = api.put('/api/v2/system/storage', json=body(key='AKIAEXAMPLEDENY0001'), headers=headers())
    assert denied.status_code == 422 and "AccessDenied on acme-files/tico (s3:PutObject)" in denied.text
    assert SECRET not in denied.text
    no_abort = api.put('/api/v2/system/storage', json=body(key='AKIAEXAMPLENOAB0001'), headers=headers())
    assert no_abort.status_code == 422 and 's3:AbortMultipartUpload' in no_abort.text
    assert api.get('/api/v2/system/storage', headers=headers()).json()['active'] is None

    saved = api.put('/api/v2/system/storage', json=body(), headers=headers())
    assert saved.status_code == 200, saved.text
    assert (
        saved.json()['active']['location'] == 'acme-files/tico' and saved.json()['active']['access_key_id'] == '…0001'
    )
    first = buckets['AKIAEXAMPLEGOOD0001']
    assert api.app.state.downloads.s3 is buckets['server'], 'desktop app builds keep the server credentials'
    with api.app.state.store.read() as c:
        row = c.execute('SELECT secret FROM company_storage WHERE active=1').fetchone()
        assert row['secret'].startswith('sealed:v1:') and SECRET not in row['secret']
        assert SECRET not in json.dumps([dict(r) for r in c.execute('SELECT * FROM events')])
        assert SECRET not in json.dumps([dict(r) for r in c.execute('SELECT * FROM idempotency')])

    # New files go to the bucket at once; the one on disk still opens, then is copied there.
    after = attach(api, tid, 'after.txt', b'stored in the company bucket')
    assert any(k.startswith('tico/blobs/') and v == b'stored in the company bucket' for k, v in first.objects.items())
    assert download(api, before) == b'stored on the server disk'
    blobs.copy_local(api.app.state.store, threading.Event(), interval=0)
    assert b'stored on the server disk' in first.objects.values()

    # The scheduled backup reads files from the company bucket with the company's keys.
    from backend.operations import backup

    class Metrics:
        def put_metric_data(self, **kw):
            pass

    vault = ObjectStore()
    report = backup(api.app.state.store, vault, Metrics(), 'backup-bucket')
    assert report['objects'] == 2 and b'stored in the company bucket' in vault.objects.values()

    # A restart reads the saved place back from the vault.
    again = Blobs(api.app.state.store.settings)
    company_storage.startup(api.app.state.store, api.app.state.vault, again)
    assert again.bucket == 'acme-files' and again.s3_key('0' * 64).startswith('tico/blobs/')

    # Another bucket: the first is still read from; turning it off goes back to the disk and reads both.
    assert (
        api.put(
            '/api/v2/system/storage', json=body('acme-files-2', 'AKIAEXAMPLEGOOD0002', prefix=''), headers=headers()
        ).status_code
        == 200
    )
    second = attach(api, tid, 'second.txt', b'in the second bucket')
    assert b'in the second bucket' in buckets['AKIAEXAMPLEGOOD0002'].objects.values()
    assert download(api, after) == b'stored in the company bucket'
    off = api.delete('/api/v2/system/storage', headers=headers())
    assert off.status_code == 200 and off.json()['active'] is None
    assert [p['location'] for p in off.json()['earlier']] == ['acme-files-2', 'acme-files/tico']
    assert not blobs.bucket
    for fid, data in (
        (before, b'stored on the server disk'),
        (after, b'stored in the company bucket'),
        (second, b'in the second bucket'),
    ):
        assert download(api, fid) == data
    assert download(api, second) == b'in the second bucket', 'a miss answered AccessDenied is not an outage'

    # A bucket that does not answer is skipped for a while, so its timeout is not added to every read.
    newer = buckets['AKIAEXAMPLEGOOD0002']
    newer.offline = True
    assert download(api, after) == b'stored in the company bucket'
    newer.offline, tries = False, newer.reads
    assert download(api, after) == b'stored in the company bucket'
    assert newer.reads == tries


def test_keeping_the_secret_needs_the_same_key_id(api, buckets):
    api.app.state.blobs._loop = None
    assert api.put('/api/v2/system/storage', json=body(), headers=headers()).status_code == 200
    kept = api.put('/api/v2/system/storage', json=body(prefix='other', secret=''), headers=headers())
    assert kept.status_code == 200 and kept.json()['active']['location'] == 'acme-files/other'
    other = api.put('/api/v2/system/storage', json=body(key='AKIAEXAMPLEGOOD0002', secret=''), headers=headers())
    assert other.status_code == 422 and 'secret access key' in other.text
