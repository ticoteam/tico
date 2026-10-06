"""Attachment/download clients share credential precedence without changing the environment."""

import os
from pathlib import Path

import boto3
import pytest

from backend.blobs import Blobs
from backend.config import Settings
from backend.downloads import Downloads


@pytest.fixture
def client_options(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("AWS_", "LITESTREAM_", "TICO_BLOB_")) or key.startswith("TICO_BACKUP_"):
            monkeypatch.delenv(key)
    calls = []
    result = object()
    monkeypatch.setattr(boto3, "client", lambda service, **options: calls.append((service, options)) or result)
    return calls, result


@pytest.mark.parametrize("storage,aws", [
    (Blobs, {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_SESSION_TOKEN": "testing-token"}),
    (Downloads, {"AWS_PROFILE": "example"})])
def test_auto_tries_backup_first_even_with_aws_credential_source(storage, aws, client_options, monkeypatch):
    calls, result = client_options
    monkeypatch.setenv("LITESTREAM_ACCESS_KEY_ID", "testing-backup")
    monkeypatch.setenv("LITESTREAM_SECRET_ACCESS_KEY", "testing-backup-secret")
    for key, value in aws.items():
        monkeypatch.setenv(key, value)
    before = dict(os.environ)
    client = storage(Settings(db_path=Path("/example/hub.db"), blob_bucket="acme-files", blob_region="us-east-1"))
    assert client.s3 is result and client.s3 is result
    expected = {"region_name": "us-east-1", "endpoint_url": None}
    expected.update(aws_access_key_id="testing-backup", aws_secret_access_key="testing-backup-secret")
    assert calls == [("s3", expected)]
    assert dict(os.environ) == before


@pytest.mark.parametrize("backup", [{}, {"LITESTREAM_ACCESS_KEY_ID": "testing", "LITESTREAM_SECRET_ACCESS_KEY": ""}])
def test_absent_or_incomplete_backup_pair_keeps_default_chain(backup, client_options, monkeypatch):
    calls, _ = client_options
    for key, value in backup.items():
        monkeypatch.setenv(key, value)
    Blobs(Settings(db_path=Path("/example/hub.db"), blob_bucket="acme-files")).s3
    assert calls == [("s3", {"region_name": None, "endpoint_url": None})]


def test_no_file_bucket_does_not_use_backup_keys(client_options, monkeypatch):
    calls, _ = client_options
    monkeypatch.setenv("LITESTREAM_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("LITESTREAM_SECRET_ACCESS_KEY", "testing")
    Downloads(Settings(db_path=Path("/example/hub.db"))).s3
    assert calls == [("s3", {"region_name": None, "endpoint_url": None})]


@pytest.mark.parametrize("storage", [Blobs])
@pytest.mark.parametrize("env,expected", [
    ({"TICO_BLOB_REGION": "us-west-2", "TICO_BACKUP_REGION": "us-east-1", "AWS_REGION": "eu-west-1"}, "us-west-2"),
    ({"TICO_BLOB_ENDPOINT": "https://s3.example.com", "TICO_BACKUP_REGION": "auto", "AWS_DEFAULT_REGION": "eu-west-1"}, "eu-west-1"),
    ({"TICO_BACKUP_ENDPOINT": "https://s3.example.com", "TICO_BACKUP_REGION": "auto"}, None),
])
def test_region_resolution(storage, env, expected, client_options, monkeypatch):
    calls, _ = client_options
    monkeypatch.setenv("TICO_DB", "/example/hub.db")
    monkeypatch.setenv("TICO_BLOB_BUCKET", "acme-files")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    storage(Settings.from_env()).s3
    assert calls == [("s3", {"region_name": expected, "endpoint_url": env.get("TICO_BLOB_ENDPOINT") or None})]


@pytest.mark.parametrize("storage", [Blobs])
def test_dedicated_file_keys_override_aws_and_backup_without_export(storage, client_options, monkeypatch):
    calls, result = client_options
    for key, value in {"TICO_BLOB_ACCESS_KEY_ID": "testing-files", "TICO_BLOB_SECRET_ACCESS_KEY": "testing-files-secret",
                       "LITESTREAM_ACCESS_KEY_ID": "testing-backup", "LITESTREAM_SECRET_ACCESS_KEY": "testing-backup-secret",
                       "AWS_ACCESS_KEY_ID": "testing-aws", "AWS_SECRET_ACCESS_KEY": "testing-aws-secret",
                       "AWS_SESSION_TOKEN": "testing-token"}.items():
        monkeypatch.setenv(key, value)
    before = dict(os.environ)
    assert storage(Settings(db_path=Path("/example/hub.db"), blob_bucket="acme-files")).s3 is result
    assert calls == [("s3", {"region_name": None, "endpoint_url": None,
                             "aws_access_key_id": "testing-files", "aws_secret_access_key": "testing-files-secret"})]
    assert dict(os.environ) == before


@pytest.mark.parametrize("storage", [Downloads])
@pytest.mark.parametrize("key,secret", [("testing", "")])
def test_empty_or_incomplete_file_keys_use_backup_pair(storage, key, secret, client_options, monkeypatch):
    calls, _ = client_options
    for name, value in {"TICO_BLOB_ACCESS_KEY_ID": key, "TICO_BLOB_SECRET_ACCESS_KEY": secret,
                        "LITESTREAM_ACCESS_KEY_ID": "testing-backup", "LITESTREAM_SECRET_ACCESS_KEY": "testing-secret",
                        "AWS_ACCESS_KEY_ID": "", "AWS_SECRET_ACCESS_KEY": "", "AWS_PROFILE": ""}.items():
        monkeypatch.setenv(name, value)
    storage(Settings(db_path=Path("/example/hub.db"), blob_bucket="acme-files")).s3
    assert calls[0][1]["aws_access_key_id"] == "testing-backup"
    assert calls[0][1]["aws_secret_access_key"] == "testing-secret"


def test_empty_storage_settings_are_unset(client_options, monkeypatch):
    calls, _ = client_options
    monkeypatch.setenv("TICO_DB", "/example/hub.db")
    for key in ("TICO_BLOB_BUCKET", "TICO_BLOB_ENDPOINT", "TICO_BLOB_REGION", "TICO_UPLOAD_MAX_BYTES",
                "TICO_BLOB_ACCESS_KEY_ID", "TICO_BLOB_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(key, "")
    settings = Settings.from_env()
    assert not settings.blob_bucket and settings.upload_max_bytes == 2 * 1024 ** 3
    Downloads(settings).s3
    assert calls == [("s3", {"region_name": None, "endpoint_url": None})]


class CredentialS3:
    def __init__(self, kind):
        self.kind = kind
        self.writable = False
        self.readable = True
        self.cleanup = True
        self.objects = {}
        self.calls = []

    def denied(self, operation):
        from botocore.exceptions import ClientError
        raise ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'private credential material'}}, operation)

    def create_multipart_upload(self, **options):
        self.calls.append(('create', options))
        if not self.writable:
            self.denied('CreateMultipartUpload')
        return {'UploadId': 'probe'}

    def abort_multipart_upload(self, **options):
        self.calls.append(('abort', options))
        if not self.cleanup:
            self.denied('AbortMultipartUpload')

    def get_object(self, **options):
        import io
        self.calls.append(('get', options))
        if not self.readable:
            self.denied('GetObject')
        return {'Body': io.BytesIO(self.objects[options['Key']])}

    def head_object(self, **options):
        import hashlib
        from botocore.exceptions import ClientError
        self.calls.append(('head', options))
        if not self.readable:
            self.denied('HeadObject')
        if options['Key'] not in self.objects:
            raise ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        data = self.objects[options['Key']]
        return {'ContentLength': len(data), 'Metadata': {'sha256': hashlib.sha256(data).hexdigest()}}

    def put_object(self, **options):
        self.calls.append(('put', options))
        if not self.writable:
            self.denied('PutObject')
        body = options['Body']
        if hasattr(body, 'read'):
            chunks = []
            while chunk := body.read(1024 * 1024):
                chunks.append(chunk)
            body = b''.join(chunks)
        self.objects[options['Key']] = body

    def generate_presigned_url(self, operation, **options):
        return 'https://s3.example.com/' + self.kind

    def close(self):
        pass


@pytest.fixture
def credential_clients(client_options, monkeypatch):
    clients = {kind: CredentialS3(kind) for kind in ('keys', 'backup', 'role')}
    calls = []
    def make(service, **options):
        key = options.get('aws_access_key_id')
        kind = 'keys' if key == 'testing-files' else 'backup' if key == 'testing-backup' else 'role'
        calls.append((kind, options))
        return clients[kind]
    monkeypatch.setattr(boto3, 'client', make)
    return clients, calls


def enable_pairs(monkeypatch, pairs):
    for kind in pairs:
        prefix = 'TICO_BLOB' if kind == 'keys' else 'LITESTREAM'
        monkeypatch.setenv(prefix + '_ACCESS_KEY_ID', 'testing-files' if kind == 'keys' else 'testing-backup')
        monkeypatch.setenv(prefix + '_SECRET_ACCESS_KEY', 'testing-secret')


def storage_store(tmp_path, mode='auto'):
    from backend.store import Store
    settings = Settings(db_path=tmp_path / 'hub.db', blob_bucket='s3://acme-files/team', blob_credentials=mode)
    store = Store(settings)
    store.initialize()
    return Blobs(settings), store


def write_health(store):
    import json
    with store.read() as c:
        return json.loads(c.execute("SELECT detail_json FROM service_health WHERE service='blob-s3'").fetchone()[0])


@pytest.mark.parametrize('pairs,allowed,expected,tried', [
    (['backup'], ['role'], 'role', ['backup', 'role']),
    (['keys', 'backup'], ['backup', 'role'], 'backup', ['keys', 'backup']),
])
def test_write_probe_selects_first_writable_source(tmp_path, monkeypatch, credential_clients, pairs, allowed, expected, tried):
    from backend.health import storage_view
    clients, calls = credential_clients
    enable_pairs(monkeypatch, pairs)
    for kind in allowed:
        clients[kind].writable = True
    storage, store = storage_store(tmp_path)
    downloads = Downloads(store.settings, s3_source=storage)
    before = dict(os.environ)
    storage.check_write(store)
    assert [kind for kind, _ in calls] == tried
    for _, options in calls:
        config = options['config']
        assert (config.connect_timeout, config.read_timeout, config.retries) == (5, 10, {'max_attempts': 2})
    assert storage.s3 is downloads.s3 is clients[expected]
    assert write_health(store)['credentials'] == expected
    with store.read() as c:
        assert storage_view(c, store.settings)['credentials'] == expected
    assert not write_health(store)['error']
    storage.put(b'new attachment')
    assert any(operation == 'put' for operation, _ in clients[expected].calls)
    assert dict(os.environ) == before


@pytest.mark.parametrize('mode', ['backup'])
def test_explicit_source_does_not_fall_back_for_writes(tmp_path, monkeypatch, credential_clients, mode):
    clients, calls = credential_clients
    enable_pairs(monkeypatch, ['keys', 'backup'])
    for source in clients.values():
        source.writable = True
    storage, store = storage_store(tmp_path, mode)
    storage.check_write(store)
    assert [kind for kind, _ in calls] == [mode]
    assert storage.s3 is clients[mode]
    clients[mode].writable = False
    storage.check_write(store)
    assert write_health(store)['error']
    assert [kind for kind, options in calls if 'config' in options] == [mode, mode]


def test_none_writable_warns_and_retains_first_source_and_local_reads(tmp_path, monkeypatch, credential_clients, caplog):
    import hashlib
    import io
    import json
    clients, _ = credential_clients
    enable_pairs(monkeypatch, ['backup'])
    storage, store = storage_store(tmp_path)
    data = b'retained attachment'
    digest = hashlib.sha256(data).hexdigest()
    storage._local(io.BytesIO(data), digest)
    for source in clients.values():
        source.readable = False
    storage.check_write(store)
    detail = write_health(store)
    assert detail['credentials'] == 'backup' and storage.s3 is clients['backup']
    assert 'acme-files' in detail['error'] and 's3:PutObject' in detail['error']
    assert storage.get(digest) == data
    assert 'private credential material' not in json.dumps(detail) + caplog.text


@pytest.mark.parametrize('operation', ['get_object', 'head_object'])
def test_denied_read_tries_other_source_once_without_switching_writes(tmp_path, monkeypatch, credential_clients, operation):
    import hashlib
    clients, _ = credential_clients
    enable_pairs(monkeypatch, ['keys', 'backup'])
    clients['keys'].writable = True
    storage, store = storage_store(tmp_path)
    storage.check_write(store)
    clients['keys'].readable = clients['backup'].readable = False
    data = b'earlier attachment'
    digest = hashlib.sha256(data).hexdigest()
    clients['role'].objects[storage.s3_key(digest)] = data
    response, source = storage.read_s3(operation, Bucket=storage.bucket, Key=storage.s3_key(digest))
    assert source is clients['role']
    if operation == 'get_object':
        assert response['Body'].read() == data
        response['Body'].close()
    for client in clients.values():
        assert sum(name == ('get' if operation == 'get_object' else 'head') for name, _ in client.calls) == 1
    assert storage.get(digest) == data
    assert storage.s3 is clients['keys']


def test_download_reads_and_signing_use_the_source_that_can_read(tmp_path, monkeypatch, credential_clients):
    import json
    clients, _ = credential_clients
    enable_pairs(monkeypatch, ['backup'])
    clients['role'].writable = True
    storage, store = storage_store(tmp_path)
    downloads = Downloads(store.settings, s3_source=storage)
    storage.check_write(store)
    clients['role'].readable = False
    clients['backup'].objects[downloads.prefix + 'latest.json'] = json.dumps({
        'version': '0.3.11', 'app_kind': 'company',
        'platforms': {'darwin-aarch64': {'signature': 'sig',
            'url': 'https://acme.example/download/file/0.3.11/Tico.app.tar.gz'}},
        'installers': {'mac': {'file': 'Tico.dmg'}}}).encode()
    clients['backup'].objects[downloads.prefix + '0.3.11/Tico.dmg'] = b'installer'
    assert downloads.bucket_manifest()['version'] == '0.3.11'
    assert downloads.file_url('0.3.11', 'Tico.dmg') == 'https://s3.example.com/backup'
    assert downloads.s3 is storage.s3 is clients['role']


def test_slow_reprobe_heals_without_restart_and_downloads_follow(tmp_path, monkeypatch, credential_clients):
    import threading
    clients, calls = credential_clients
    enable_pairs(monkeypatch, ['backup'])
    storage, store = storage_store(tmp_path)
    downloads = Downloads(store.settings, s3_source=storage)
    assert downloads.s3 is clients['backup']
    stop = threading.Event()
    waits = []
    def wait(interval):
        waits.append(interval)
        assert storage._write_failed and write_health(store)['error']
        clients['role'].writable = True
        return False
    monkeypatch.setattr(stop, 'wait', wait)
    storage.storage_loop(store, stop)
    assert waits == [1800]
    assert [kind for kind, options in calls if 'config' in options] == ['backup', 'role', 'backup', 'role']
    assert not storage._write_failed and not write_health(store)['error']
    assert downloads.s3 is storage.s3 is clients['role']


def test_pending_cleanup_can_use_another_source_without_accumulating_probes(tmp_path, monkeypatch, credential_clients):
    clients, _ = credential_clients
    enable_pairs(monkeypatch, ['backup'])
    clients['backup'].writable = clients['role'].writable = True
    clients['backup'].cleanup = False
    storage, store = storage_store(tmp_path)
    storage.check_write(store)
    assert storage.s3 is clients['role']
    assert [operation for operation, _ in clients['role'].calls] == ['abort', 'create', 'abort']
    assert 'upload_id' not in write_health(store)


def test_invalid_credentials_setting_never_echoes_value():
    with pytest.raises(ValueError, match='must be auto, role, backup or keys') as error:
        Settings(db_path=Path('/example/hub.db'), blob_credentials='private credential material')
    assert 'private credential material' not in str(error.value)


@pytest.mark.parametrize('mode', ['keys'])
def test_explicit_missing_pair_does_not_use_role(tmp_path, credential_clients, mode):
    _, calls = credential_clients
    storage, store = storage_store(tmp_path, mode)
    storage.check_write(store)
    assert not calls and storage._write_failed
    assert write_health(store)['credentials'] == mode


@pytest.mark.parametrize('mode,method', [('auto', 'put'), ('keys', 'put_staged')])
def test_uploads_reject_unselected_and_denied_sources_then_heal(tmp_path, monkeypatch, credential_clients, mode, method):
    import hashlib
    import io
    from backend.store import Problem

    clients, _ = credential_clients
    enable_pairs(monkeypatch, ['keys', 'backup'])
    storage, store = storage_store(tmp_path, mode)
    data = b'  byte-exact\x00\xff\n'
    digest = hashlib.sha256(data).hexdigest()
    def upload():
        if method == 'put':
            return storage.put(data)
        if method == 'put_stream':
            return storage.put_stream(io.BytesIO(data))
        return storage.put_staged(io.BytesIO(data), digest, len(data))
    # Available clients alone are never evidence of write permission.
    storage.s3
    with pytest.raises(Problem, match='write check is still pending') as error:
        upload()
    assert error.value.status == 503 and error.value.retryable
    assert all(not source.calls for source in clients.values())
    assert storage.check_write(store) is False
    before = {kind: list(source.calls) for kind, source in clients.items()}
    with pytest.raises(Problem, match='s3:PutObject') as error:
        upload()
    assert 'private credential material' not in error.value.detail
    assert {kind: source.calls for kind, source in clients.items()} == before
    chosen = 'role' if mode == 'auto' else mode
    clients[chosen].writable = True
    assert storage.check_write(store) is True
    assert upload() == digest and storage.get(digest) == data
    assert not list(storage.directory.rglob('.upload-*'))
    assert all(not any(op == 'put' for op, _ in source.calls)
               for kind, source in clients.items() if kind != chosen)


def test_blocked_probe_gates_uploads_and_reuses_one_in_flight_check(tmp_path, monkeypatch, credential_clients):
    import threading
    from backend.store import Problem

    clients, calls = credential_clients
    enable_pairs(monkeypatch, ['backup'])
    clients['backup'].writable = True
    clients['backup'].cleanup = False
    clients['role'].writable = True
    storage, store = storage_store(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = clients['backup'].create_multipart_upload
    def blocked(**options):
        entered.set()
        assert release.wait(3)
        return original(**options)
    monkeypatch.setattr(clients['backup'], 'create_multipart_upload', blocked)
    first_stop = threading.Event()
    worker = threading.Thread(target=storage.check_write, args=(store, first_stop))
    worker.start()
    try:
        assert entered.wait(2)
        for size in (1, 9 * 1024 ** 2):
            import io
            import hashlib
            with pytest.raises(Problem, match='pending'):
                storage._upload(io.BytesIO(b'x'), hashlib.sha256(b'x').hexdigest(), size, 'application/octet-stream')
        first_stop.set()
        worker.join(2)
        assert not worker.is_alive()
        # A shutdown return leaves the original daemon alive, so another caller must join it.
        second_stop = threading.Event()
        timer = threading.Timer(0.1, second_stop.set)
        timer.start()
        try:
            assert storage.check_write(store, second_stop) is False
        finally:
            timer.cancel()
        assert [kind for kind, _ in calls] == ['backup']
        assert all(not any(op == 'put' for op, _ in source.calls) for source in clients.values())
    finally:
        first_stop.set()
        release.set()
        worker.join(2)
        assert storage._probe_task[0].wait(2)
    assert storage.check_write(store) is True
    assert storage._sources.selected == 'role'


def test_upload_client_construction_errors_never_expose_credentials(tmp_path, monkeypatch, credential_clients, caplog):
    from backend.store import Problem
    clients, _ = credential_clients
    clients['role'].writable = True
    storage, store = storage_store(tmp_path)
    assert storage.check_write(store)
    def fail(kind):
        raise RuntimeError('private credential material')
    monkeypatch.setattr(storage._sources, 'client_for', fail)
    with pytest.raises(Problem) as error:
        storage.put(b'attachment')
    assert error.value.status == 503 and error.value.retryable
    assert 'private credential material' not in error.value.detail + caplog.text


@pytest.mark.parametrize('fail', [False, True])
def test_multipart_keeps_its_selected_identity_through_completion_or_abort(tmp_path, monkeypatch, credential_clients, fail):
    import hashlib
    import io
    from backend.store import Problem

    clients, _ = credential_clients
    enable_pairs(monkeypatch, ['keys', 'backup'])
    clients['keys'].writable = True
    storage, store = storage_store(tmp_path)
    assert storage.check_write(store)
    source = clients['keys']
    def create(**options):
        source.calls.append(('upload-create', options))
        with storage._write_lock:
            storage._sources.selected = 'backup'
        return {'UploadId': 'attachment'}
    def part(**options):
        source.calls.append(('part', options))
        if fail:
            raise RuntimeError('private credential material')
        return {'ETag': 'part'}
    monkeypatch.setattr(source, 'create_multipart_upload', create)
    monkeypatch.setattr(source, 'upload_part', part, raising=False)
    monkeypatch.setattr(source, 'complete_multipart_upload', lambda **options: source.calls.append(('complete', options)), raising=False)
    data = b'x' * (8 * 1024 ** 2 + 1)
    if fail:
        with pytest.raises(Problem) as error:
            storage.put_stream(io.BytesIO(data))
        assert 'private credential material' not in error.value.detail
    else:
        assert storage.put_stream(io.BytesIO(data)) == hashlib.sha256(data).hexdigest()
    assert not clients['backup'].calls
    assert source.calls[-1][0] == ('abort' if fail else 'complete')
    assert source.calls[-1][1]['UploadId'] == 'attachment'


def test_pending_read_client_construction_is_bounded_shared_and_keeps_local_fallback(tmp_path, monkeypatch, credential_clients):
    import hashlib
    import io
    import threading
    from backend import blob_s3

    clients, calls = credential_clients
    storage, store = storage_store(tmp_path)
    downloads = Downloads(store.settings, s3_source=storage)
    data = b'  retained\nbytes\x00'
    digest = hashlib.sha256(data).hexdigest()
    storage._local(io.BytesIO(data), digest)
    entered, release = threading.Event(), threading.Event()
    original = blob_s3.client
    def blocked(*args, **options):
        entered.set()
        assert release.wait(2)
        return original(*args, **options)
    monkeypatch.setattr(blob_s3, 'CLIENT_BUILD_TIMEOUT', 0.05)
    monkeypatch.setattr(blob_s3, 'client', blocked)
    try:
        assert storage.get(digest) == data
        assert entered.is_set() and not calls
        assert downloads.bucket_manifest() is None
        assert downloads.file_url('0.3.11', 'Tico.dmg') is None
        assert len(storage._sources._building) == 1
        assert not storage._sources._building[('role', True)][0].is_set()
    finally:
        release.set()
        assert storage._sources._building[('role', True)][0].wait(1)
    assert len(calls) == 1
    assert storage.s3 is downloads.s3 is clients['role']


@pytest.mark.parametrize('stage', ['create'])
def test_probe_deadline_bounds_provider_and_s3_latency_without_reselecting_late(tmp_path, monkeypatch, credential_clients, stage):
    import threading
    from backend import blob_s3
    from backend.store import Problem

    clients, calls = credential_clients
    clients['role'].writable = True
    storage, store = storage_store(tmp_path)
    entered, release = threading.Event(), threading.Event()
    target = blob_s3 if stage == 'client' else clients['role']
    name = 'client' if stage == 'client' else stage + '_multipart_upload'
    original = getattr(target, name)
    def blocked(*args, **options):
        entered.set()
        assert release.wait(2)
        return original(*args, **options)
    monkeypatch.setattr(target, name, blocked)
    try:
        assert storage.check_write(store, timeout=0.05) is False
        assert entered.is_set() and 'timed out' in write_health(store)['error']
        with pytest.raises(Problem, match='timed out'):
            storage.put(b'new attachment')
        task = storage._probe_task
        assert storage.check_write(store, timeout=0.05) is False
        assert storage._probe_task is task
        assert len(calls) == (0 if stage == 'client' else 1)
    finally:
        release.set()
        assert storage._probe_task[0].wait(1)
    assert storage._sources.selected is None
    assert 'timed out' in write_health(store)['error']
    assert storage.check_write(store) is True
    assert storage._sources.selected == 'role'
    assert 'upload_id' not in write_health(store) and not write_health(store)['error']
