"""CLI feature negotiation and bounded multipart uploads."""

import io
import urllib.error

import pytest

from clients import hubcli, hubtools, remotecli
from clients.tico import APIError, Client
from clients.tests.helpers.wire import Wire, cli_wire


@pytest.mark.parametrize('multipart', [False])
def test_task_attach_negotiates_the_upload_form(monkeypatch, tmp_path, multipart):
    path = tmp_path / 'draft.txt'
    path.write_bytes(b'  hello\n')
    wire = cli_wire(
        monkeypatch,
        {'GET config': {'features': {'task_files_multipart': multipart}}, 'POST tasks/task/files': {'file': 'f1'}},
    )
    args = hubcli.parser().parse_args(['task', 'attach', 'task', str(path)])
    remotecli.run(args)
    (sent,) = wire.writes()
    assert sent.path == '/api/v2/tasks/task/files'
    if multipart:
        assert sent.body == {'file': {'filename': 'draft.txt', 'content': b'  hello\n'}, 'name': 'draft.txt'}
    else:
        assert sent.body == {'name': 'draft.txt', 'text': '  hello\n'}


def test_old_server_refuses_large_before_open(monkeypatch, tmp_path):
    path = tmp_path / 'movie.mp4'
    with path.open('wb') as file:
        file.truncate(10_000_001)
    path.chmod(0)  # unreadable: the refusal must come from its size, before any open
    wire = cli_wire(monkeypatch, {'GET config': {'features': {}}})
    args = hubcli.parser().parse_args(['task', 'attach', 'task', str(path)])
    with pytest.raises(APIError) as exc:
        remotecli.run(args)
    assert exc.value.status == 413
    assert wire.writes() == []


def test_mcp_server_does_not_read_local_paths():
    class Server:
        def post(self, *args, **kw):
            pytest.fail('server path must be refused first')

    with pytest.raises(ValueError, match='Computer'):
        hubtools.task_attach(Server(), {'id': 'task', 'name': 'draft.txt', 'path': '/private/server-file'})


def test_multipart_retry_replays_same_parts_and_key(tmp_path, monkeypatch):
    path = tmp_path / 'draft.txt'
    path.write_bytes(b'draft')

    class Response(io.BytesIO):
        headers = {}

    class Opener:
        calls = []

        def open(self, request, timeout=None):
            self.calls.append((request, b''.join(request.data)))
            if len(self.calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 503, 'Down', {}, io.BytesIO(b'{}'))
            return Response(b'{"file": "done"}')

    monkeypatch.setattr('clients.tico._sleep', lambda _: None)
    client = Client('https://api.example.com', 'private-token', retries=1)
    client.opener = Opener()  # double: the HTTP transport; it fails once with a 503 and records each request
    assert client.post_multipart('tasks/task/files', {'file': path}, {'name': 'draft.txt'}, key='same') == {
        'file': 'done'
    }
    first, second = client.opener.calls
    assert first[1] == second[1]
    assert first[0].get_header('Idempotency-key') == second[0].get_header('Idempotency-key') == 'same'
    assert int(first[0].get_header('Content-length')) == len(first[1])


@pytest.mark.parametrize('bad', ['symlink', 'credential', 'program'])
def test_local_mcp_attach_refuses_unsafe_paths(monkeypatch, tmp_path, bad):
    from clients import bot_files as BF

    root = tmp_path / 'checkout'
    root.mkdir()
    safe = root / 'safe.txt'
    safe.write_text('safe')
    outside = tmp_path / 'outside.txt'
    outside.write_text('outside')
    secret = root / 'credentials.txt'
    secret.write_text('example only')
    link = root / 'link.txt'
    link.symlink_to(outside)
    program = root / 'tool'  # no extension: known by its first bytes
    program.write_bytes(b'\x7fELF\x02\x01\x01\x00' + bytes(8))  # a 64-bit ELF's first 16 bytes
    (root / 'Makefile').write_text('all:\n')
    assert BF.local_file(root, 'Makefile')[1] == 'Makefile'
    assert BF.local_file(root, 'tool')[1] == 'tool'  # a website upload (tico browser upload) is not inspected
    with pytest.raises(BF.Refused, match='is a program'):
        BF.read_local(root, 'tool')  # publishing a file reads its first bytes
    with pytest.raises(BF.Refused):
        BF.check_program('run.', b'#!/bin/sh')  # a trailing dot is no extension, as in the runner
    for fat in (b'\xca\xfe\xba\xbf\x00\x00\x00\x02', b'\xbf\xba\xfe\xca\x02\x00\x00\x00'):  # universal 64-bit Mach-O
        with pytest.raises(BF.Refused):
            BF.check_program('universal', fat)
    assert BF.check_name('.gitignore') == ('.gitignore', 'application/octet-stream')
    with pytest.raises(BF.Refused):
        BF.check_name('..')
    monkeypatch.chdir(root)  # not a git checkout, so the command's checkout is this folder
    wire = Wire()
    args = {'id': 'task', 'name': 'safe.txt', 'path': str(safe)}
    if bad == 'poster':
        args['poster'] = str(outside)
    else:
        args['path'] = str({'outside': outside, 'symlink': link, 'credential': secret, 'program': program}[bad])
    with pytest.raises(BF.Refused):
        hubtools.task_attach(wire.client(), args)
    assert wire.writes() == []


def test_cli_attach_combines_streaming_poster_and_note(monkeypatch, tmp_path):
    path, poster = tmp_path / 'draft.md', tmp_path / 'poster.png'
    path.write_text('# Draft')
    poster.write_bytes(b'poster')
    wire = cli_wire(
        monkeypatch,
        {'GET config': {'features': {'task_files_multipart': True}}, 'POST tasks/task/files': {'file': 'f1'}},
    )
    args = hubcli.parser().parse_args(
        ['task', 'attach', 'task', str(path), '--poster', str(poster), '--note', 'Revised']
    )
    remotecli.run(args)
    (sent,) = wire.writes()
    assert sent.path == '/api/v2/tasks/task/files'
    # asks are gone: an attachment carries no question, so there is no `ask` field
    assert sent.body == {
        'file': {'filename': 'draft.md', 'content': b'# Draft'},
        'poster': {'filename': 'poster.png', 'content': b'poster'},
        'name': 'draft.md',
        'note': 'Revised',
    }
