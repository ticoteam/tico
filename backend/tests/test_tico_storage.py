"""Tico file hosting 1/2 (Dev 315): a company's files in Tico's bucket, handed over by Tico HQ, served from its
ticofiles.com address. Spec tests 1, 4 and 7, and the app halves of 5 and 6 (Product roadmap "Tico file hosting
through Gateway"). Fake S3 and a fake HQ only: no network."""
import base64
import hashlib
import hmac
import io
import json
import struct
import threading
import time
import urllib.parse
import zipfile
import zlib
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from botocore.exceptions import ClientError

from backend import tico_storage
from backend.tests.test_api import api, headers, post  # noqa: F401
from backend.tests.test_storage_delivery import S3, attach, task

SECRET = "tico-hosting-secret-access-key-01"
TOKEN = "tico-hosting-session-token-01"
KEY, KEY2 = "AKIATICOHOSTING0001", "AKIATICOHOSTING0002"
SIGNING = "0123456789abcdef0123456789abcdef0123456789abcdef"
ADDRESS = "acme-files.ticofiles.com"
OFF = {"state": "off", "writes": False, "reason": None, "access": "none", "address": None, "bucket": None,
       "region": None, "prefix": None, "credentials": None, "signing": None, "keep_until": None}


def png():
    """A 1x1 picture."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


class Bucket(S3):
    """Tico's bucket: keeps each object's type and disposition as S3 does, and answers them on HEAD. A file's bytes
    leave it only through the edge (`Edge`): a direct read is refused, as reads there would go unmetered."""
    def __init__(self):
        super().__init__()
        self.meta, self.metadata = {}, {}

    def get_object(self, **kw):
        from backend.company_storage import CHECK
        if self.objects.get(kw['Key']) != CHECK:      # the write check reads back its few bytes itself
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
        return {**found, **({'ContentType': kind} if kind else {}), **({'ContentDisposition': how} if how else {}),
                'Metadata': dict(self.metadata.get(kw['Key'], {}))}


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
        if (link.netloc != ADDRESS or query['s'] != base64.urlsafe_b64encode(mac).rstrip(b'=').decode()
                or int(query['e']) < time.time()):
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
    hq, writes, reason, access = {"on": ("on", True, None, "read_write"),
                                  "credits_out": ("on", False, "out_of_credit", "read_only"),
                                  "stopped": ("stopped", False, "stopped", "read_only")}[state]
    return {"state": hq, "writes": writes, "reason": reason, "access": access, "address": ADDRESS,
            "bucket": "tico-files-us", "region": "us-east-1", "prefix": "c/acme/",
            "credentials": {"access_key_id": key, "secret_access_key": SECRET, "session_token": token,
                            "expires": stamp(expires)},
            "signing": {"key_id": "k1", "secret": SIGNING}, "keep_until": stamp(30 * 24 * 60) if hq == "stopped" else None,
            **extra}


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


def download(api, fid, who='ana-test', **options):
    return api.get(f'/api/v2/files/{fid}', headers=headers(who), **options)


def test_turning_it_on_stores_new_files_in_tico_storage_and_existing_files_still_open(api, tico):
    """Spec 1: new files in Tico's storage, under the company's own folder; existing files still open. Keys come
    from HQ, sealed, never shown."""
    made, hq, sync = tico
    tid = task(api)
    before = attach(api, tid, 'before.txt', b'on the server disk before')
    assert sync(report=True) == {"state": "off", "label": "Tico file hosting"}, 'not bought: nothing changes'
    assert not api.app.state.blobs.bucket
    assert hq.asked == [("GET", "https://gateway.example/files", "Bearer gk_test_0123456789abcdef", None),
                        ("POST", "https://gateway.example/files/total", "Bearer gk_test_0123456789abcdef",
                         {"total_bytes": len(b'on the server disk before')})], 'HQ gets the total for its estimate'

    view = sync(on())
    assert view['state'] == 'on' and view['address'] == ADDRESS and view['location'] == 'tico-files-us/c/acme'
    after = attach(api, tid, 'after.txt', b'in tico storage')
    bucket = made[KEY]
    assert any(k.startswith('c/acme/blobs/') and v == b'in tico storage' for k, v in bucket.objects.items())
    assert download(api, before).content == b'on the server disk before'
    assert download(api, after).content == b'in tico storage'
    assert b'on the server disk before' not in bucket.objects.values(), 'existing files move only when asked'
    api.app.state.blobs.copy_local(api.app.state.store, threading.Event(), interval=0)
    assert b'on the server disk before' not in bucket.objects.values(), 'not even by the background disk copy'
    settings = api.app.state.store.settings
    assert settings.visual_origin == '', "the files address is not the visual address (Dev 314's is)"

    # The owner's storage page says so; the keys are sealed and never returned or logged.
    page = api.get('/api/v2/system/storage', headers=headers())
    assert page.status_code == 200 and page.json()['tico']['state'] == 'on'
    with api.app.state.store.read() as c:
        sealed = c.execute('SELECT secret FROM tico_storage').fetchone()['secret']
        assert sealed.startswith('sealed:v1:') and SECRET not in sealed and TOKEN not in sealed and SIGNING not in sealed
        logged = json.dumps([dict(r) for r in c.execute('SELECT * FROM events')])
    for secret in (SECRET, TOKEN, SIGNING):
        assert secret not in page.text + logged

    from backend import health
    with api.app.state.store.read() as c:
        assert health.storage_view(c, settings)['bucket'] == 'tico-files-us', 'Health names where files go now'

    # A restart reads it back from the vault.
    from backend.blobs import Blobs
    from backend import company_storage
    again = Blobs(settings)
    company_storage.startup(api.app.state.store, api.app.state.vault, again)
    assert again.bucket == 'tico-files-us' and again.s3_key('0' * 64).startswith('c/acme/blobs/')


def test_new_keys_for_the_same_place_are_swapped_in_and_asked_for_before_they_end(api, tico):
    """HQ's keys last an hour: each answer's keys are used from then on, with no new write check and nothing
    interrupted, and HQ is asked again every 5 minutes, or sooner when the keys end first."""
    made, hq, sync = tico
    tid = task(api)
    sync(on())
    blobs, store = api.app.state.blobs, api.app.state.store
    generation, probes = blobs._generation, len(made[KEY].calls)
    assert 240 < tico_storage._due[id(store)] - time.monotonic() <= tico_storage.REFRESH

    sync(on(key=KEY2, token=TOKEN + "-2", expires=12))
    assert blobs._generation == generation, 'a move or copy under way carries on'
    attach(api, tid, 'later.txt', b'signed with the new key')
    assert hq.signed[-1] == TOKEN + "-2" and b'signed with the new key' in made[KEY].objects.values()
    assert len(made[KEY].calls) == probes + 1, 'no new write check, only the upload'
    assert tico_storage._due[id(store)] - time.monotonic() <= 120, '10 minutes before the keys end'

    # The scheduler asks only when due, one answer at a time.
    with tico_storage._lock:
        tico_storage._live[id(store)] = (store, api.app.state.vault, blobs)
        tico_storage._due[id(store)] = time.monotonic() + 100
    assert tico_storage.sync_later(store, due_only=True) is None
    hq.answer = OFF
    with tico_storage._lock:
        tico_storage._due[id(store)] = time.monotonic() - 1
    import backend.tico_storage as T
    asked = len(hq.asked)
    original = T.ask
    T.ask = lambda key, base, opener=None: original(key, base, hq)
    try:
        thread = tico_storage.sync_later(store, due_only=True)
        assert thread is not None
        thread.join(60)
        assert not thread.is_alive()
    finally:
        T.ask = original
    assert len(hq.asked) == asked + 1
    # HQ keeps the files no longer: nothing is read from its bucket, and no more answers are due before the hour.
    assert not blobs.bucket and blobs.place_at('tico-files-us/c/acme') is None and blobs.edge is None
    assert tico_storage._due[id(store)] is None


def test_hqs_answer_is_checked_before_anything_changes(api, tico):
    made, hq, sync = tico
    for bad, field in ((on(prefix=''), 'prefix'), (on(address='evil.example'), 'address'),
                       ({**on(), "state": "maybe"}, 'state'), (on(bucket='Not A Bucket'), 'bucket'),
                       (on(expires=-1), 'credentials'), (on(writes=False), 'writes'), (on(access='none'), 'access'),
                       (on(signing={"key_id": "k1", "secret": "short"}), 'signing'),
                       # Neither may be left out: Tico's bucket would then be read directly, past the meter.
                       (on(signing=None), 'signing'), (on(address=None), 'address')):
        view = sync(bad)
        assert not api.app.state.blobs.bucket, field
        assert field in view.get('error', field), field
    assert api.app.state.store.settings.visual_origin == ''
    # Keys that can't write are refused by the same check as the owner's own bucket, and nothing switches.
    made[KEY] = NoWrite()
    made[KEY].close = lambda: None
    sync(on())
    assert not api.app.state.blobs.bucket
    with api.app.state.store.read() as c:
        assert tico_storage.row(c) is None
    # HQ without file hosting yet (404) changes nothing either.
    hq.status = 404
    assert sync(on())['state'] == 'off' and not api.app.state.blobs.bucket


def test_a_private_tasks_file_in_tico_storage_is_refused_to_a_person_without_access(api, tico):
    """Spec 7: the file and its version are refused, and no link to the company's address is handed out; a reader
    with access is sent to a five-minute signed link on it, checked again on every request."""
    made, hq, sync = tico
    # The media worker (backend/file_metadata.py) reads each new picture once, through the company's address, on its
    # own poll: it is stopped here and its work run below, so that read lands before any count, not inside one.
    from backend.file_metadata import PENDING
    worker = api.app.state.file_metadata
    worker.stop.set()
    worker.wake.set()
    picture, tid = png(), task(api)
    early = attach(api, tid, 'early.png', picture + b'early')
    sync(on())
    task_id = post(api, "tasks", {"owner": "ops", "title": "Private chart", "body": "x", "private": True}, "ben-test")["id"]
    made_file = api.post(f"/api/v2/tasks/{task_id}/files", files={'file': ('chart.png', picture)},
                         headers=headers("ben-test"))
    assert made_file.status_code == 200, made_file.text
    fid = made_file.json()["file"]["id"]
    digest = hashlib.sha256(picture).hexdigest()
    assert f'c/acme/blobs/{digest[:2]}/{digest}' in made[KEY].objects
    with api.app.state.store.read() as c:
        waiting = [dict(r) for r in c.execute(PENDING, (float("inf"),))]
    delivered = hq.edge.delivered.get(ADDRESS, 0)
    for blob in waiting:
        worker.process(blob)
    assert hq.edge.delivered.get(ADDRESS, 0) == delivered + len(picture), "its preview read it once, metered"

    for who in ("cara-test",):
        for path in (f"/api/v2/files/{fid}", f"/api/v2/files/{fid}?v=1"):
            refused = api.get(path, headers=headers(who), follow_redirects=False)
            assert refused.status_code in (403, 404) and 'location' not in refused.headers, path
            assert ADDRESS not in refused.text and 'tico-files-us' not in refused.text

    from backend import visuals as V
    forged = V.mint(V.key_for(api.app, api.app.state.store.settings), "human:cara", fid, 1)
    refused = api.get("/visuals/" + forged, follow_redirects=False)
    assert refused.status_code == 404 and 'location' not in refused.headers, 'nor its visual link'

    sent = api.get(f"/api/v2/files/{fid}", headers=headers("ben-test"), follow_redirects=False)
    assert sent.status_code == 302, sent.text
    link = urllib.parse.urlsplit(sent.headers['location'])
    query = dict(urllib.parse.parse_qsl(link.query))
    assert (link.scheme, link.netloc, link.path) == ("https", ADDRESS, f"/blobs/{digest[:2]}/{digest}")
    assert 0 < int(query['e']) - time.time() <= tico_storage.LINK_SECONDS and query['k'] == 'k1'
    mac = hmac.new(SIGNING.encode(), f"{ADDRESS}\n{link.path}\n{query['e']}".encode(), hashlib.sha256).digest()
    assert query['s'] == base64.urlsafe_b64encode(mac).rstrip(b'=').decode()
    assert sent.headers['cache-control'] == 'private, no-store'
    assert 'tico-files-us' not in sent.headers['location'] and 'amazonaws' not in sent.headers['location']
    delivered = hq.edge.delivered.get(ADDRESS, 0)
    part = api.get(f"/api/v2/files/{fid}", headers={**headers("ben-test"), 'Range': 'bytes=0-7'},
                   follow_redirects=False)
    assert part.status_code == 206 and part.content == picture[:8], "a Range read streams from here"
    assert part.headers['content-range'] == f"bytes 0-7/{len(picture)}"
    assert hq.edge.delivered[ADDRESS] == delivered + 8, "read through the company's address"

    # Everything else streams from here as before: a file not in Tico's bucket, one it holds under another type,
    # and anything that is not a picture, video or audio.
    assert download(api, early, follow_redirects=False).status_code == 200
    text = attach(api, tid, 'notes.txt', b'plain words')
    delivered = hq.edge.delivered.get(ADDRESS, 0)
    streamed = download(api, text, follow_redirects=False)
    assert streamed.status_code == 200 and streamed.content == b'plain words'
    part = api.get(f'/api/v2/files/{text}', headers={**headers('ana-test'), 'Range': 'bytes=6-'})
    assert part.status_code == 206 and part.content == b'words'
    assert hq.edge.delivered[ADDRESS] == delivered + len(b'plain words') + len(b'words'), \
        "streamed from here, read through the company's address, where it is counted"
    key = f'c/acme/blobs/{digest[:2]}/{digest}'
    made[KEY].meta[key] = ('text/html', 'inline')
    api.app.state.blobs._edge_seen.clear()
    assert api.get(f"/api/v2/files/{fid}", headers=headers("ben-test"), follow_redirects=False).status_code == 200


def test_move_existing_files_copies_every_file_and_version_and_matches_the_estimate(api, tico):
    """Spec 4: every file opens, every version is kept, GB moved matches the estimate; nothing is deleted, and the
    owner is not charged delivery for Tico's own check of each copy."""
    made, hq, sync = tico
    tid = task(api)
    first = attach(api, tid, 'notes.txt', b'version one')
    second = attach(api, tid, 'notes.txt', b'version two, longer')
    other = attach(api, tid, 'other.txt', b'another file')
    sync(on())
    attach(api, tid, 'since.txt', b'uploaded to tico storage already')
    disk = api.app.state.blobs.directory
    before = sorted(p.name for p in disk.rglob('*') if p.is_file())

    shown = api.get('/api/v2/system/storage/move', headers=headers())
    assert shown.status_code == 200, shown.text
    estimate = shown.json()['estimate']
    size = len(b'version one') + len(b'version two, longer') + len(b'another file')
    assert (estimate['files'], estimate['bytes'], estimate['to']) == (3, size, 'tico-files-us/c/acme')
    assert api.post('/api/v2/system/storage/move', json={'expect_bytes': size}, headers=headers('ben-test')).status_code == 403
    changed = api.post('/api/v2/system/storage/move', json={'expect_bytes': size - 1}, headers=headers())
    assert changed.status_code == 409 and 'estimate' in changed.text
    # Another start that gets in after the running check is refused the same way, not with a server error.
    listed = tico_storage.pending
    def raced(c, *args):
        c.execute("INSERT INTO storage_moves(id,target,state,started_at,started_by,estimate_files,estimate_bytes) "
                  "VALUES('other','x','running','2026-10-23T00:00:00Z','owner',0,0)")
        return listed(c, *args)
    tico_storage.pending = raced
    try:
        raced_start = api.post('/api/v2/system/storage/move', json={'expect_bytes': size}, headers=headers())
    finally:
        tico_storage.pending = listed
    assert raced_start.status_code == 409 and 'move_running' in raced_start.text, raced_start.text

    delivered = dict(hq.edge.delivered)
    tico_storage.start_move(api.app.state.store, api.app.state.blobs, Owner(), size, wait=True)
    assert hq.edge.delivered == delivered, "each copy is checked by S3's own SHA-256, not read back through the edge"
    move = api.get('/api/v2/system/storage/move', headers=headers()).json()
    assert move['move']['state'] == 'done' and move['move']['matches'], move
    assert (move['move']['moved_files'], move['move']['moved_bytes']) == (3, size)
    assert move['estimate']['files'] == 0, 'nothing left to move'
    stored = set(made[KEY].objects.values())
    assert {b'version one', b'version two, longer', b'another file'} <= stored
    assert sorted(p.name for p in disk.rglob('*') if p.is_file()) == before, 'the source is never deleted'
    # Reads now come from Tico storage: with this disk's copies gone, every version still opens.
    for path in disk.rglob('*'):
        if path.is_file() and not path.name.startswith('.'):
            path.unlink()
    for fid, data in ((first, b'version one'), (second, b'version two, longer'), (other, b'another file')):
        assert download(api, fid).content == data

    # After a restart the server no longer knows what it uploaded there itself: the move finds it held and does not
    # copy it again.
    api.app.state.blobs._landed.clear()
    estimate = api.get('/api/v2/system/storage/move', headers=headers()).json()['estimate']
    assert estimate['files'] == 1
    puts = len(made[KEY].calls)
    tico_storage.start_move(api.app.state.store, api.app.state.blobs, Owner(), estimate['bytes'], wait=True)
    move = api.get('/api/v2/system/storage/move', headers=headers()).json()
    assert move['move']['state'] == 'done' and move['move']['matches'] and move['estimate']['files'] == 0, move
    assert len(made[KEY].calls) == puts


def test_a_copy_that_does_not_verify_is_not_recorded(api, tico):
    made, hq, sync = tico
    tid = task(api)
    attach(api, tid, 'a.txt', b'good bytes')
    sync(on())
    made[KEY].corrupt = True
    shown = api.get('/api/v2/system/storage/move', headers=headers()).json()['estimate']
    tico_storage.start_move(api.app.state.store, api.app.state.blobs, Owner(), shown['bytes'], wait=True)
    move = api.get('/api/v2/system/storage/move', headers=headers()).json()
    assert move['move']['state'] == 'failed' and not move['move']['matches'] and move['move']['failed'] == 1
    assert move['estimate']['files'] == 1, 'still to move'
    with api.app.state.store.read() as c:
        assert not c.execute('SELECT 1 FROM blob_locations').fetchone()


def test_a_move_records_a_copy_only_in_the_place_it_was_written_and_read_back(api, tico):
    """Where new files go may change during a Move (credits run out, the owner stops): the copy under way is not
    written anywhere else, nothing is recorded or counted for the old place, and a later Move still finds the file
    missing there. New keys for the same place change nothing: the move carries on."""
    made, hq, sync = tico
    tid = task(api)
    files = [b'first file!', b'second file']          # copied in digest order; the same size, so either is first
    for n, data in enumerate(files):
        attach(api, tid, f'{n}.txt', data)
    sync(on())
    store, blobs = api.app.state.store, api.app.state.blobs
    # Each file's read waits until the test lets it go: reading[n] says file n is being read, go[n] lets it on.
    reading, go = [threading.Event(), threading.Event()], [threading.Event(), threading.Event()]
    original = blobs._open_elsewhere

    def held(*args):
        n = sum(r.is_set() for r in reading)
        reading[n].set()
        assert go[n].wait(60)
        return original(*args)
    blobs._open_elsewhere = held
    try:
        size = api.get('/api/v2/system/storage/move', headers=headers()).json()['estimate']['bytes']
        # While the first file is read: new keys for the same place, then credits run out.
        tico_storage.start_move(store, blobs, Owner(), size)
        assert reading[0].wait(60)
        sync(on(key=KEY2, token=TOKEN + "-2"))
        go[0].set()
        assert reading[1].wait(60), 'new keys for the same place: the move goes on to the next file'
        # Another place that can take files at once (a company bucket whose keys were just checked) takes over.
        blobs.configure({"bucket": "acme-own", "prefix": "", "region": "us-west-2",
                         "keys": ("AKIAEXAMPLEGOOD0001", "example-secret")}, checked=True)
        go[1].set()
        thread = next(t for t in threading.enumerate() if t.name == 'tico-storage-move')
        thread.join(60)
    finally:
        blobs._open_elsewhere = original
    move = api.get('/api/v2/system/storage/move', headers=headers()).json()['move']
    assert move['state'] == 'stopped' and (move['moved_files'], move['moved_bytes']) == (1, 11), move
    first, second = sorted(files, key=lambda data: hashlib.sha256(data).hexdigest())
    assert first in made[KEY].objects.values()
    assert second not in made[KEY].objects.values() and second not in made['AKIAEXAMPLEGOOD0001'].objects.values()
    with store.read() as c:
        recorded = [tuple(r) for r in c.execute('SELECT bucket, digest FROM blob_locations')]
    assert recorded == [('tico-files-us/c/acme', hashlib.sha256(first).hexdigest())]

    # Back to Tico storage: the second file is still to move. A switch before its copy starts stops it the same way.
    from backend import company_storage
    company_storage.apply(store, api.app.state.vault, blobs, copy=False, checked=True)
    estimate = api.get('/api/v2/system/storage/move', headers=headers()).json()['estimate']
    assert estimate['files'] == 1, 'the second file is still to move'
    move_now = tico_storage._move

    def switched(*args):
        sync(on(state='stopped'))
        return move_now(*args)
    tico_storage._move = switched
    try:
        tico_storage.start_move(store, blobs, Owner(), estimate['bytes'], wait=True)
    finally:
        tico_storage._move = move_now
    move = api.get('/api/v2/system/storage/move', headers=headers()).json()['move']
    assert move['state'] == 'stopped' and move['moved_files'] == 0, move
    assert second not in made[KEY].objects.values() and second not in made['server'].objects.values()
    with store.read() as c:
        assert c.execute('SELECT COUNT(*) FROM blob_locations').fetchone()[0] == 1


def test_stopping_offers_the_export_and_new_files_go_back_to_own_storage(api, tico):
    """Spec 5 (app half): export offered; new files to the company's own storage; Tico's files still open."""
    made, hq, sync = tico
    tid = task(api)
    sync(on())
    in_tico = attach(api, tid, 'kept.txt', b'stored in tico')
    sync(on(state='stopped'))
    assert not api.app.state.blobs.bucket, "new files go to the server's own storage again"
    attach(api, tid, 'back.txt', b'back on the disk')
    assert b'back on the disk' not in made[KEY].objects.values()
    assert download(api, in_tico).content == b'stored in tico'

    export = api.get('/api/v2/system/storage/export', headers=headers('ben-test'))
    assert export.status_code == 403, export.text
    delivered = hq.edge.delivered.get(ADDRESS, 0)
    export = api.get('/api/v2/system/storage/export', headers=headers())
    assert export.status_code == 200 and export.headers['content-type'] == 'application/zip'
    assert hq.edge.delivered[ADDRESS] == delivered + len(b'stored in tico'), 'the export reads through the address'
    archive = zipfile.ZipFile(io.BytesIO(export.content))
    contents = {archive.read(name) for name in archive.namelist()}
    assert {b'stored in tico', b'back on the disk'} <= contents and 'manifest.json' in archive.namelist()

    # Move files back: the same move, into the company's own storage, so nothing is lost to HQ's 30-day delete.
    estimate = api.get('/api/v2/system/storage/move', headers=headers()).json()['estimate']
    assert estimate['to'] == "this server's disk" and estimate['files'] == 1
    delivered = hq.edge.delivered[ADDRESS]
    started = api.post('/api/v2/system/storage/move', json={'expect_bytes': estimate['bytes']}, headers=headers())
    assert started.status_code == 200, started.text
    for thread in threading.enumerate():
        if thread.name == 'tico-storage-move':
            thread.join(60)
    move = api.get('/api/v2/system/storage/move', headers=headers()).json()['move']
    assert move['state'] == 'done' and move['matches'], move
    assert hq.edge.delivered[ADDRESS] == delivered + len(b'stored in tico'), 'so does Move files back'
    made[KEY].offline = True
    assert download(api, in_tico).content == b'stored in tico'


def test_the_owners_export_leaves_out_files_private_to_someone_else(api, tico):
    made, hq, sync = tico
    sync(on())
    secret_task = post(api, "tasks", {"owner": "cara", "title": "Cara's", "body": "x", "private": True}, "cara-test")["id"]
    r = api.post(f"/api/v2/tasks/{secret_task}/files", json={"name": "mine.txt", "text": "cara only"},
                 headers=headers("cara-test"))
    assert r.status_code == 200, r.text
    export = api.get('/api/v2/system/storage/export', headers=headers())
    assert export.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(export.content))
    assert b'cara only' not in {archive.read(n) for n in archive.namelist()}
    assert int(export.headers['x-tico-left-out']) >= 1


def test_credits_out_existing_files_open_and_uploads_follow_the_rule(api, tico):
    """Spec 6 (app half): files stay readable; new uploads go to the company's own storage, or are refused with a
    plain reason when it can't take them."""
    made, hq, sync = tico
    tid = task(api)
    sync(on())
    in_tico = attach(api, tid, 'kept.txt', b'stored in tico')
    sync(on(state='credits_out'))
    assert download(api, in_tico).content == b'stored in tico'
    own = attach(api, tid, 'own.txt', b'on the server disk now')
    assert b'on the server disk now' not in made[KEY].objects.values()
    assert download(api, own).content == b'on the server disk now'

    # With the company's own bucket refusing writes, the upload is refused with a plain reason.
    from backend import company_storage
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO company_storage(id,bucket,prefix,region,access_key_id,secret,active,created_at,created_by) "
                  "VALUES('own','acme-files','','us-west-2','AKIAEXAMPLENOWR0001',?,1,'2026-10-23T00:00:00Z','human:ben')",
                  (api.app.state.vault.cipher.seal(c, 'company_storage:own', 'example-secret'),))
    company_storage.apply(api.app.state.store, api.app.state.vault, api.app.state.blobs, copy=False, checked=True)
    refused = api.post(f'/api/v2/tasks/{tid}/files', files={'file': ('late.txt', b'no room')}, headers=headers('ana-test'))
    assert refused.status_code == 503, refused.text
    assert refused.json()['error']['code'] == 'storage_paused'
    assert 'Gateway credits have run out' in refused.text and 'AccessDenied' in refused.text
    assert SECRET not in refused.text
    assert download(api, in_tico).content == b'stored in tico'

    # Credits added: HQ says on again, and new files go to Tico storage.
    sync(on())
    attach(api, tid, 'again.txt', b'tico again')
    assert b'tico again' in made[KEY].objects.values()

