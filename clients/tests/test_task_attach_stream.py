"""CLI feature negotiation and bounded multipart uploads."""
import io
from pathlib import Path
import urllib.error
from unittest.mock import Mock

import pytest

from clients import hubcli, hubtools, remotecli
from clients.tico import APIError, Client


@pytest.mark.parametrize('multipart', [False, True])
def test_task_attach_negotiates_without_read_bytes(monkeypatch, tmp_path, multipart):
    path = tmp_path / 'draft.txt'
    path.write_bytes(b'  hello\n')
    monkeypatch.setenv('HUB_API_URL', 'https://api.example.com')
    client = Mock()
    client.get.return_value = {'actor': 'human:ana'}
    client.features.return_value = {'task_files_multipart': multipart}
    monkeypatch.setattr(remotecli, 'Client', lambda *args, **kw: client)
    monkeypatch.setattr(Path, 'read_bytes', lambda _: pytest.fail('whole file read'))
    args = hubcli.parser().parse_args(['task', 'attach', 'task', str(path)])
    remotecli.run(args)
    if multipart:
        client.post_multipart.assert_called_once_with('tasks/task/files', {'file': path}, {'name': path.name}, key=None)
        client.post.assert_not_called()
    else:
        client.post.assert_called_once_with('tasks/task/files', {'name': path.name, 'text': '  hello\n'}, key=None)
        client.post_multipart.assert_not_called()


def test_old_server_refuses_large_before_open(monkeypatch, tmp_path):
    path = tmp_path / 'movie.mp4'
    with path.open('wb') as file:
        file.truncate(10_000_001)
    monkeypatch.setenv('HUB_API_URL', 'https://api.example.com')
    client = Mock()
    client.get.return_value = {'actor': 'human:ana'}
    client.features.return_value = {}
    monkeypatch.setattr(remotecli, 'Client', lambda *args, **kw: client)
    monkeypatch.setattr(Path, 'open', lambda *args: pytest.fail('large file opened'))
    args = hubcli.parser().parse_args(['task', 'attach', 'task', str(path)])
    with pytest.raises(APIError) as exc:
        remotecli.run(args)
    assert exc.value.status == 413


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
    monkeypatch.setattr('clients.tico.time.sleep', lambda _: None)
    client = Client('https://api.example.com', 'private-token', retries=1)
    client.opener = Opener()
    assert client.post_multipart('tasks/task/files', {'file': path}, {'name': 'draft.txt'}, key='same') == {'file': 'done'}
    first, second = client.opener.calls
    assert first[1] == second[1]
    assert first[0].get_header('Idempotency-key') == second[0].get_header('Idempotency-key') == 'same'
    assert int(first[0].get_header('Content-length')) == len(first[1])


@pytest.mark.parametrize('bad', ['symlink', 'credential'])
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
    monkeypatch.setattr(BF, 'checkout_root', lambda: root)
    client = Mock()
    client.features.return_value = {'task_files_multipart': True}
    args = {'id': 'task', 'name': 'safe.txt', 'path': str(safe)}
    if bad == 'poster':
        args['poster'] = str(outside)
    else:
        args['path'] = str({'outside': outside, 'symlink': link, 'credential': secret}[bad])
    with pytest.raises(BF.Refused):
        hubtools.task_attach(client, args)
    client.post_multipart.assert_not_called()


def test_cli_attach_combines_streaming_poster_note_and_choices(monkeypatch, tmp_path):
    path, poster = tmp_path / 'draft.md', tmp_path / 'poster.png'
    path.write_text('# Draft')
    poster.write_bytes(b'poster')
    monkeypatch.setenv('HUB_API_URL', 'https://api.example.com')
    client = Mock()
    client.get.return_value = {'actor': 'human:ana'}
    client.features.return_value = {'task_files_multipart': True}
    monkeypatch.setattr(remotecli, 'Client', lambda *args, **kw: client)
    args = hubcli.parser().parse_args(['task', 'attach', 'task', str(path), '--poster', str(poster),
                                      '--note', 'Revised', '--choices', 'Approve,Request changes'])
    remotecli.run(args)
    call = client.post_multipart.call_args
    assert call.args[:2] == ('tasks/task/files', {'file': path, 'poster': poster})
    body = call.args[-1]
    assert body['name'] == 'draft.md' and body['note'] == 'Revised'
    assert body['ask']['questions'][0]['options'] == [{'label': 'Approve'}, {'label': 'Request changes'}]
