"""Attachment storage contract; fake S3 only, no network or large allocations."""
import base64
import hashlib
import io
import json
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading

import pytest
from botocore.exceptions import ClientError

from backend.blobs import Blobs, disposition
from backend.config import Settings
from backend.file_delivery import byte_range
from backend.file_metadata import dimensions
from backend.store import Problem
from backend.tests.test_api import api, headers, post  # noqa: F401
from backend.tests.test_files import turn
from backend.tests.test_runner import live  # noqa: F401
from clients.tico import MultipartBody


class S3:
    def __init__(self):
        self.objects, self.calls, self.parts = {}, [], []
        self.offline, self.corrupt = False, False
        self.head_calls = []

    def head_object(self, **kw):
        self.head_calls.append(kw)
        if self.offline:
            raise RuntimeError('offline')
        if kw['Key'] not in self.objects:
            raise ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        data = self.objects[kw['Key']]
        return {'ContentLength': len(data),
                'ChecksumSHA256': base64.b64encode(hashlib.sha256(data).digest()).decode()}

    def put_object(self, **kw):
        self.calls.append(kw)
        if kw['Key'] in self.objects:
            raise ClientError({'Error': {'Code': 'PreconditionFailed'}}, 'PutObject')
        body = kw['Body']
        if hasattr(body, 'read'):
            chunks = []
            while chunk := body.read(64 * 1024):
                chunks.append(chunk)
            body = b''.join(chunks)
        self.objects[kw['Key']] = body + (b'bad' if self.corrupt else b'')

    def get_object(self, **kw):
        if self.offline:
            raise RuntimeError('offline')
        data = self.objects[kw['Key']]
        if 'Range' in kw:
            start, end = byte_range(kw['Range'], len(data))
            data = data[start:end + 1]
        return {'Body': io.BytesIO(data)}

    def create_multipart_upload(self, **kw):
        self.calls.append(kw)
        self.parts = []
        return {'UploadId': 'upload'}

    def upload_part(self, **kw):
        self.parts.append(kw['Body'])
        return {'ETag': str(kw['PartNumber'])}

    def complete_multipart_upload(self, **kw):
        assert kw['IfNoneMatch'] == '*'
        self.objects[kw['Key']] = b''.join(self.parts)

    def abort_multipart_upload(self, **kw):
        self.aborted = True


def test_stream_hash_and_s3_first_multipart(tmp_path):
    s3 = S3()
    storage = Blobs(Settings(db_path=tmp_path / 'hub.db', blob_bucket='private'), s3)
    class Stream(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024
            return super().read(size)
    small = b'streamed bytes'
    digest = storage.put_stream(Stream(small), 'text/plain')
    assert digest == hashlib.sha256(small).hexdigest()
    assert hasattr(s3.calls[-1]['Body'], 'read')
    assert storage.get(digest) == small
    big = b'v' * (8 * 1024 ** 2 + 1)
    digest = storage.put_stream(Stream(big), 'video/mp4')
    assert len(s3.parts) == 2 and storage.get(digest) == big
    assert s3.calls[-1]['ContentType'] == 'video/mp4'
    assert s3.calls[-1]['CacheControl'] == 'private, max-age=31536000, immutable'
    assert not list(tmp_path.rglob('.upload-*'))
    assert not (storage.directory / storage.key(digest)).exists()
    with pytest.raises(Problem) as error:
        storage.put_stream(Stream(small), max_bytes=1)
    assert error.value.status == 413


@pytest.mark.parametrize('mime,inline', [('image/png', True), ('image/svg+xml', False), ('text/html', False)])
def test_safe_disposition(mime, inline):
    assert disposition(mime) == ('inline' if inline else 'attachment')


def task(api):
    return post(api, 'tasks', {'owner': 'ops', 'title': 'Review media', 'body': 'Open the file.'})['id']


def attach(api, tid, name, data):
    response = api.post(f'/api/v2/tasks/{tid}/files', files={'file': (name, data)},
                        headers=headers('ana-test'))
    assert response.status_code == 200, response.text
    return response.json()['file']['id']


@pytest.mark.parametrize('name,original', [
    ('report.json', b'{\n  "owner": "human:ana",\n  "text": "\\u00e9"\n}\n'),
])
def test_attachment_bytes_and_digests_across_versions_and_legacy_ids(api, name, original):
    tid = task(api)
    legacy = attach(api, tid, name, original)
    latest = original + b'\n'
    fid = attach(api, tid, name, latest)
    with api.app.state.store.read() as c:
        row = c.execute('SELECT file_id FROM bot_file_versions WHERE blob_id=?', (legacy,)).fetchone()
        series = row['file_id']
        assert c.execute('SELECT file_id FROM bot_file_versions WHERE blob_id=?', (fid,)).fetchone()[0] == series
    h = headers('ana-test')
    for url, data in [
        (f'/api/v2/files/{series}', latest),
        (f'/api/v2/files/{series}?v=1', original),
        (f'/api/v2/files/{series}?v=2', latest),
        (f'/api/v2/files/{series}/versions/1', original),
        (f'/api/v2/files/{series}/versions/2', latest),
        (f'/api/v2/files/{legacy}', original),
        (f'/api/v2/files/{legacy}?v=1', original),
        (f'/api/v2/files/{fid}', latest),
    ]:
        digest = hashlib.sha256(data).hexdigest()
        full = api.get(url, headers=h)
        assert full.status_code == 200 and full.content == data, url
        assert hashlib.sha256(full.content).hexdigest() == full.headers['x-content-sha256'] == digest
        assert full.headers['etag'] == '"' + digest + '"'
        assert full.headers['content-length'] == str(len(data))
        partial = api.get(url, headers={**h, 'Range': 'bytes=2-9'})
        assert partial.status_code == 206 and partial.content == data[2:10], url
        assert partial.headers['x-content-sha256'] == digest  # digest identifies the whole stored file
        assert partial.headers['content-range'] == f'bytes 2-9/{len(data)}'
        head = api.head(url, headers=h)
        assert head.status_code == 200 and head.content == b'', url
        for key in ('content-length', 'content-type', 'x-content-sha256', 'etag'):
            assert head.headers[key] == full.headers[key], (url, key)


@pytest.mark.parametrize('kind', ['raw', 'stream'])
def test_display_names_leave_byte_responses_untouched(api, tmp_path, monkeypatch, kind):
    from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
    raw = b'{\n  "owner": "human:ana"\n}\n'
    path = {'version': '/api/v2/files/example/versions/1', 'poster': '/api/v2/files/example/poster',
            'thumb': '/api/v2/files/example/thumb', 'meeting': '/api/meetings/example/media',
            'download': '/download/example.json'}.get(kind, '/api/v2/tasks')
    def response():
        if kind == 'file':
            source = tmp_path / 'report.json'
            source.write_bytes(raw)
            return FileResponse(source, media_type='application/json')
        if kind == 'stream':
            return StreamingResponse(iter([raw[:10], raw[10:]]), media_type='application/json',
                                     headers={'Content-Length': str(len(raw))})
        extra = {'Content-Disposition': 'attachment'} if kind == 'disposition' else (
            {'X-Content-SHA256': hashlib.sha256(raw).hexdigest()} if kind == 'digest' else {})
        if kind in ('disposition', 'digest', 'partial', 'head'):
            result = JSONResponse(json.loads(raw), headers=extra, status_code=206 if kind == 'partial' else 200)
            result.body = raw
            result.headers['Content-Length'] = str(len(raw))
            return result
        return Response(raw, media_type='application/json', headers=extra,
                        status_code=206 if kind == 'partial' else 200)
    api.app.add_api_route(path, response, methods=['GET', 'HEAD'])
    api.app.router.routes.insert(0, api.app.router.routes.pop())
    monkeypatch.setattr('backend.names.annotate_json', lambda *a, **kw: pytest.fail('rewrote file bytes'))
    result = api.request('HEAD' if kind == 'head' else 'GET', path, headers=headers('ana-test'))
    assert result.status_code == (206 if kind == 'partial' else 200)
    assert result.content == (b'' if kind == 'head' else raw)
    assert result.headers['content-length'] == str(len(raw))


def test_multipart_range_cache_etag_limits_and_legacy(api):
    tid = task(api)
    bid = attach(api, tid, 'sample.txt', b'0123456789')
    url = '/api/v2/files/' + bid
    h = headers('ana-test')
    full = api.get(url, headers=h)
    assert full.content == b'0123456789' and full.headers['cache-control'] == 'private, no-store'
    assert full.headers['content-type'].startswith('text/plain')
    assert full.headers['content-disposition'].startswith('inline')
    assert full.headers['x-content-type-options'] == 'nosniff'
    assert 'sandbox' in full.headers['content-security-policy']
    for value, expected in [('bytes=2-4', b'234'), ('bytes=-3', b'789'), ('bytes=7-', b'789')]:
        response = api.get(url, headers={**h, 'Range': value})
        assert response.status_code == 206 and response.content == expected
        assert response.headers['accept-ranges'] == 'bytes'
        assert response.headers['content-range'].endswith('/10')
    for value in ('bytes=10-', 'bytes=4-2', 'bytes=-0'):
        response = api.get(url, headers={**h, 'Range': value})
        assert response.status_code == 416 and response.headers['content-range'] == 'bytes */10'
    multi = api.get(url, headers={**h, 'Range': 'bytes=0-1,4-5'})
    assert multi.status_code == 200 and multi.content == full.content
    head = api.head(url, headers=h)
    assert head.status_code == 200 and head.content == b''
    assert head.headers['content-length'] == '10'
    assert api.head(url).status_code == 401
    assert api.get(url, headers={**h, 'If-None-Match': full.headers['etag']}).status_code == 304
    assert 'no-store' in api.get(url + '?v=1', headers=h).headers['cache-control']
    assert api.get(url + '/poster?v=1', headers=h).status_code == 404
    assert api.get(url).status_code == 401
    assert api.app.state.store.settings.upload_max_bytes == 2 * 1024 ** 3
    too_large = api.post(f'/api/v2/tasks/{tid}/files', headers={**h, 'Content-Type': 'multipart/form-data; boundary=x',
        'Content-Length': str(2 * 1024 ** 3 + 11_000_001)})
    assert too_large.status_code == 413 and '2147483648' in too_large.text
    api.app.state.store.settings.upload_max_bytes = 4
    rejected = api.post(f'/api/v2/tasks/{tid}/files', files={'file': ('large.txt', b'12345')}, headers=h)
    assert rejected.status_code == 413 and '4 bytes' in rejected.text
    legacy = post(api, f'tasks/{tid}/files', {'name': 'legacy.txt', 'content_base64': base64.b64encode(b'old bytes').decode()})
    assert api.get(legacy['file']['url'], headers=h).content == b'old bytes'
    dangerous = post(api, f'tasks/{tid}/files', {'name': 'page.html', 'text': '<script>example</script>'})
    assert api.get(dangerous['file']['url'], headers=h).headers['content-disposition'].startswith('attachment')


def test_copy_verified_and_fallback_keeps_local(api):
    bid = attach(api, task(api), 'source.txt', b'retained bytes')
    blobs = api.app.state.blobs
    with api.app.state.store.read() as c:
        digest = c.execute('SELECT digest FROM blobs WHERE id=?', (bid,)).fetchone()[0]
    s3 = S3()
    blobs.bucket, blobs._s3 = 'private', s3
    blobs._probe_s3 = s3
    blobs.copy_local(api.app.state.store, threading.Event(), interval=0)
    assert blobs.copy_status == {'done': 1, 'total': 1, 'running': False, 'error': '', 'failed': 0}
    with api.app.state.store.read() as c:
        assert c.execute('SELECT digest FROM blob_locations').fetchone()[0] == digest
    assert (blobs.directory / blobs.key(digest)).exists()
    s3.offline = True
    assert blobs.get(digest) == b'retained bytes'
    assert b''.join(blobs.open_range(digest, 2, 4)) == b'tai'
    with api.app.state.store.transaction() as c:
        c.execute('DELETE FROM blob_locations')
    s3.offline, s3.corrupt = False, True
    s3.objects.clear()
    blobs.copy_local(api.app.state.store, threading.Event(), interval=0)
    assert blobs.copy_status['error'] and blobs.copy_status['done'] == 0
    assert blobs.get(digest) == b'retained bytes'
    with api.app.state.store.read() as c:
        assert c.execute('SELECT COUNT(*) FROM blob_locations').fetchone()[0] == 0


@pytest.mark.parametrize('failure', ['AccessDenied'])
def test_health_warns_about_s3_write_check_even_with_no_files(api, failure):
    from botocore.exceptions import NoCredentialsError

    blobs = api.app.state.blobs
    settings = api.app.state.store.settings
    blobs.bucket = settings.blob_bucket = 'acme-files'
    blobs._s3 = blobs._probe_s3 = s3 = S3()
    def denied(**kw):
        if failure == 'NoCredentialsError':
            raise NoCredentialsError()
        raise ClientError({'Error': {'Code': failure, 'Message': 'private credential detail'}}, 'CreateMultipartUpload')
    s3.create_multipart_upload = denied
    blobs.copy_local(api.app.state.store, threading.Event(), interval=0)
    response = api.get('/api/v2/health', headers=headers()).json()
    check = next(check for check in response['checks'] if check['id'] == 'blob_storage')
    assert response['storage']['mode'] == 's3'
    assert response['storage']['credentials'] == 'role'
    assert response['storage']['copy'] == {'done': 0, 'total': 0, 'failed': 0}
    assert check['status'] == 'warn'
    assert check['summary'] == f"S3 storage can't write: {failure} on acme-files" + (" (s3:PutObject)" if failure == "AccessDenied" else "")
    assert 'private credential detail' not in json.dumps(response)
    assert not s3.objects
    s3.create_multipart_upload = S3.create_multipart_upload.__get__(s3)
    blobs.copy_local(api.app.state.store, threading.Event(), interval=0)
    healthy = api.get('/api/v2/health', headers=headers()).json()
    assert not any(check['id'] == 'blob_storage' for check in healthy['checks'])


def test_rehearsal_skips_s3_write_probe(api):
    blobs = api.app.state.blobs
    blobs.bucket = 'acme-files'
    blobs.rehearsal = True
    blobs._s3 = blobs._probe_s3 = s3 = S3()
    blobs.copy_local(api.app.state.store, threading.Event(), interval=0)
    assert not s3.calls


def test_header_parsing_without_pillow(monkeypatch, tmp_path):
    from backend.file_metadata import image
    monkeypatch.setitem(sys.modules, 'PIL', None)
    png = b'\x89PNG\r\n\x1a\n' + b'\x00' * 8 + struct.pack('>II', 640, 320)
    source = tmp_path / 'source.png'
    source.write_bytes(png)
    assert image(source, tmp_path / 'thumb.jpg') == (640, 320, None)
    assert dimensions(io.BytesIO(b'GIF89a' + struct.pack('<HH', 20, 10))) == (20, 10)
    webp = b'RIFF' + b'\x00' * 4 + b'WEBPVP8X' + b'\x00' * 8 + (639).to_bytes(3, 'little') + (319).to_bytes(3, 'little')
    assert dimensions(io.BytesIO(webp)) == (640, 320)


def test_multipart_client_replay_and_bounded_reads(tmp_path):
    path = tmp_path / 'media.mp4'
    path.write_bytes(b'v' * (1024 * 1024 + 10))
    body = MultipartBody({'file': path}, {'name': 'media.mp4', 'ask': {'questions': []}})
    first, second = list(body), list(body)
    assert first == second and sum(map(len, first)) == body.size
    assert max(map(len, first)) <= 1024 * 1024


@pytest.mark.slow
@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='optional video tools')
def test_video_metadata_with_tools(api, tmp_path):
    worker = api.app.state.file_metadata
    worker.stop.set()
    worker.wake.set()
    video = tmp_path / 'clip.mp4'
    subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=64x32:d=1',
                    '-c:v', 'mpeg4', str(video)], check=True)
    bid = attach(api, task(api), 'clip.mp4', video.read_bytes())
    with api.app.state.store.read() as c:
        blob = dict(c.execute('SELECT * FROM blobs WHERE id=?', (bid,)).fetchone())
    worker.process(blob)
    meta = api.get('/api/v2/files/' + bid + '/meta', headers=headers('ana-test')).json()
    assert (meta['width'], meta['height'], meta['duration_ms']) == (64, 32, 1000)
    assert meta['poster_blob_id'] and meta['media_state'] == 'ready'


@pytest.mark.slow
def test_client_streams_real_http_upload(api, live, tmp_path):
    from clients.tico import Client
    source = tmp_path / "source.txt"
    source.write_bytes(b"streaming over HTTP")
    client = Client(live, "ana-test")
    assert client.features()["task_files_multipart"]
    made = client.post_multipart(f"tasks/{task(api)}/files", {"file": source}, {"name": source.name})
    assert client.download(made["file"]["id"]) == b"streaming over HTTP"


def test_worker_fills_immutable_published_version(api):
    Image = pytest.importorskip("PIL.Image")
    worker = api.app.state.file_metadata
    worker.stop.set()
    worker.wake.set()
    _, attempt = turn(api)
    data = io.BytesIO()
    Image.new("RGB", (100, 50)).save(data, "PNG")
    made = api.post("/api/v2/files/uploads", json={"name": "photo.png", "content_base64": base64.b64encode(data.getvalue()).decode()},
                    headers=headers(attempt["token"]))
    assert made.status_code == 200, made.text
    fid = made.json()["file"]["id"]
    with api.app.state.store.read() as c:
        blob = dict(c.execute("SELECT b.* FROM blobs b JOIN bot_file_versions v ON v.blob_id=b.id WHERE v.file_id=?", (fid,)).fetchone())
    worker.process(blob)
    with api.app.state.store.read() as c:
        version = c.execute("SELECT * FROM bot_file_versions WHERE file_id=?", (fid,)).fetchone()
        assert (version["width"], version["height"], version["media_state"]) == (100, 50, "ready")
        assert version["thumb_blob_id"]


@pytest.mark.slow
@pytest.mark.skipif(not shutil.which("pdftoppm"), reason="optional PDF tool")
def test_pdf_page_one_poster(api):
    from pypdf import PdfWriter
    worker = api.app.state.file_metadata
    worker.stop.set()
    worker.wake.set()
    data = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=200)
    writer.write(data)
    bid = attach(api, task(api), "page.pdf", data.getvalue())
    with api.app.state.store.read() as c:
        blob = dict(c.execute("SELECT * FROM blobs WHERE id=?", (bid,)).fetchone())
    worker.process(blob)
    meta = api.get("/api/v2/files/" + bid + "/meta", headers=headers("ana-test")).json()
    assert meta["media_state"] == "ready" and meta["poster_blob_id"]
    assert max(meta["width"], meta["height"]) <= 640


def test_storage_migration_preserves_old_version_and_reapplies():
    from backend.files import SCHEMA
    from backend.storage_schema import SCHEMA as STORAGE_SCHEMA
    from backend.store import H
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    c.execute('CREATE TABLE blobs(id TEXT PRIMARY KEY)')
    c.execute("INSERT INTO blobs VALUES('blob')")
    c.execute("INSERT INTO bot_files(id,bot,scope,identity,title,kind,locator,first_activity_at,last_activity_at) "
              "VALUES('file','bot','task:task','old','Old report','document','tico_blob','then','then')")
    old = ('file', 1, 'blob', 'digest', None, 10, 'report.txt', 'text/plain', None, None, None, 'bot:bot', 'then')
    c.execute('INSERT INTO bot_file_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', old)
    H._apply(c, STORAGE_SCHEMA)
    H._apply(c, STORAGE_SCHEMA)  # hub 20 and cloud 53 both use the idempotent apply helper
    version = c.execute('SELECT * FROM bot_file_versions').fetchone()
    assert tuple(version)[:13] == old and version['media_state'] == 'none'
    c.execute('UPDATE bot_file_versions SET width=10,height=5')
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE bot_file_versions SET blob_id='different'")
    c.close()


def test_upload_uses_parser_spool_and_caps_part_headers(api, monkeypatch):
    from backend import file_upload
    blobs = api.app.state.blobs
    monkeypatch.setattr(blobs, 'put_stream', lambda *a, **kw: pytest.fail('second staging pass'))
    tid = task(api)
    bid = attach(api, tid, 'direct.txt', b'direct spool')
    assert api.get('/api/v2/files/' + bid, headers=headers('ana-test')).content == b'direct spool'
    url = f'/api/v2/tasks/{tid}/files'
    spools, temporary_file = [], file_upload.tempfile.TemporaryFile
    def spool(*args, **kwargs):
        stream = temporary_file(*args, **kwargs)
        spools.append(stream)
        return stream
    monkeypatch.setattr(file_upload.tempfile, 'TemporaryFile', spool)
    for extra in (b'X-Header: a\r\n' * 16, b'X-A: ' + b'a' * 4000 + b'\r\nX-B: ' + b'b' * 4000 + b'\r\nX-C: ' + b'c' * 200 + b'\r\n'):
        body = (b'--boundary\r\nContent-Disposition: form-data; name="poster"; filename="poster.txt"\r\n\r\nposter\r\n'
                b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="safe.txt"\r\n' + extra + b'\r\nbody\r\n--boundary--\r\n')
        response = api.post(url, content=body, headers={**headers('ana-test'),
            'Content-Type': 'multipart/form-data; boundary=boundary'})
        assert response.status_code == 422
        assert 'headers' in response.json()['error']['detail']
        assert spools and all(stream.closed for stream in spools), 'header rejection closes earlier part spools'
    assert blobs.directory.stat().st_mode & 0o777 == 0o700
    malformed = api.post(url, content=b'--boundary\r\nprivate-invalid-header\r\n\r\nbody\r\n--boundary--\r\n',
                        headers={**headers('ana-test'), 'Content-Type': 'multipart/form-data; boundary=boundary'})
    assert malformed.status_code == 422
    assert malformed.json()['error']['detail'] == 'Malformed multipart upload'


def test_s3_head_skips_upload_without_request_verification(tmp_path):
    s3 = S3()
    settings = Settings(db_path=tmp_path / 'hub.db', blob_bucket='s3://private/team/files',
                        blob_region='us-east-1', blob_endpoint='https://s3.example.com')
    storage = Blobs(settings, s3)
    digest = storage.put(b'content')
    assert 'team/files/' + storage.key(digest) in s3.objects
    s3.get_object = lambda **kw: pytest.fail('verification read in request')
    assert storage.put(b'content') == digest
    assert len(s3.calls) == 1 and len(s3.head_calls) == 2
    assert storage.location == 'private/team/files'


def test_image_decoder_refuses_other_formats_and_pixel_bombs(tmp_path):
    from backend.file_metadata import DecodeError, image
    Image = pytest.importorskip('PIL.Image')
    source, target = tmp_path / 'source', tmp_path / 'preview.jpg'
    Image.new('RGB', (10, 10)).save(source, 'BMP')
    with pytest.raises(DecodeError):
        image(source, target)
    # Valid CRC on an oversized PNG header, without allocating the pixels.
    import zlib
    header = b'IHDR' + struct.pack('>IIBBBBB', 10000, 6000, 8, 2, 0, 0, 0)
    source.write_bytes(b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + header +
                       struct.pack('>I', zlib.crc32(header)))
    with pytest.raises(DecodeError):
        image(source, target)
    assert not target.exists()


def test_json_receipt_replays_across_upgrade(api):
    from backend.store import encode, digest
    tid = task(api)
    url = f'/api/v2/tasks/{tid}/files'
    body = {'name': 'legacy.txt', 'text': 'old upload'}
    h = headers('ana-test')
    first = api.post(url, json=body, headers=h)
    assert first.status_code == 200, first.text
    with api.app.state.store.transaction() as c:
        row = c.execute('SELECT request_hash FROM idempotency WHERE operation=? AND key=?',
                        (url, h['Idempotency-Key'])).fetchone()
        old_payload = {**body, 'content_base64': None}
        assert row[0] == digest(encode(old_payload))
        c.execute('UPDATE idempotency SET request_hash=? WHERE operation=? AND key=?',
                  (digest(encode(old_payload)), url, h['Idempotency-Key']))
    again = api.post(url, json=body, headers=h)
    assert again.status_code == 200 and again.json() == first.json()


@pytest.mark.parametrize('blocked', ['create'])
def test_shutdown_does_not_wait_for_in_flight_write_probe(tmp_path, blocked):
    from fastapi.testclient import TestClient
    from backend.app import create_app

    app = create_app(Settings(db_path=tmp_path / 'probe.db', scheduler_enabled=False, blob_bucket='acme-files'))
    blobs = app.state.blobs
    blobs._probe_s3 = s3 = S3()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = getattr(s3, blocked + '_multipart_upload')
    def blocked_call(**kw):
        entered.set()
        assert release.wait(5), 'test did not release the probe'
        return original(**kw)
    setattr(s3, blocked + '_multipart_upload', blocked_call)
    check_write = blobs._check_write
    def checked(*args):
        try:
            return check_write(*args)
        finally:
            finished.set()
    blobs._check_write = checked
    try:
        with TestClient(app) as client:
            assert entered.wait(2)
            assert client.get('/healthz').status_code == 200
        # Lifespan completed while the network call is still blocked.
        assert not release.is_set() and not finished.is_set()
    finally:
        release.set()
        assert finished.wait(2)
    with app.state.store.read() as c:
        detail = json.loads(c.execute("SELECT detail_json FROM service_health WHERE service='blob-s3'").fetchone()[0])
    if blocked == 'create':
        assert detail['upload_id'] == 'upload'  # retained for cleanup on next startup
        assert not hasattr(s3, 'aborted')
    else:
        assert 'upload_id' not in detail and s3.aborted
