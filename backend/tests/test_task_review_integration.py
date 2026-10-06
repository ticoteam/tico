"""In-process release contract: streamed versions, previews, reviews and the next turn."""
import hashlib
import json
from pathlib import Path
import struct
import zlib


from backend.tests.test_api import api, assign, claim, headers, post, ready, runner  # noqa: F401
from backend.tests.test_task_review import ask, get, task
from runner.service import Runner


def png(color):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 16, 8, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\x00' + bytes(color) * 16) * 8)) + chunk(b'IEND', b''))


def task_row(api, tid):
    return next(row for row in get(api, 'tasks')['tasks'] if row['id'] == tid)


def test_streamed_task_versions_review_and_next_wake(api, monkeypatch):
    worker = api.app.state.file_metadata
    worker.stop.set()
    worker.wake.set()
    # Supplied posters must work on the standard server without optional video tools.
    monkeypatch.setattr('backend.file_metadata.shutil.which', lambda _: None)
    tid = task(api)
    assert task_row(api, tid)['cover'] is None
    assert task_row(api, tid)['open_asks'] == 0
    machine = runner(api)
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    attempt = claim(api, machine, 'ops')
    token = attempt['token']
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'task-review'}, machine['token'])
    first_image, second_image = png((0, 0, 255)), png((255, 0, 0))
    video = Path(__file__).with_name('fixtures').joinpath('task-review.mp4').read_bytes()
    contents = {
        'image.png': (first_image, second_image),
        'video.mp4': (video, video + struct.pack('>I', 10) + b'freev2'),
        'script.md': (b'# Script v1\nFirst draft.', b'# Script v2\nRevised opening.'),
    }
    ids = {}
    for name, versions in contents.items():
        for number, data in enumerate(versions, 1):
            files = {'file': (name, data)}
            fields = {'note': f'Version {number}'}
            if name == 'video.mp4':
                files['poster'] = ('poster.png', second_image, 'image/png')
            if name == 'script.md' and number == 2:
                fields['ask'] = json.dumps(ask())
            h = headers(token)
            made = api.post(f'/api/v2/tasks/{tid}/files', data=fields, files=files, headers=h)
            assert made.status_code == 200, made.text
            result = made.json()
            assert result['version'] == number
            assert result['file']['file_id'] == result['file_id']
            assert result['file']['version'] == number
            if number == 1:
                ids[name] = result['file_id']
            assert result['file_id'] == ids[name]
            replay = api.post(f'/api/v2/tasks/{tid}/files', data=fields, files=files, headers=h)
            assert replay.json() == result
            # Exercise the real derivation code, deterministically outside the background loop.
            with api.app.state.store.read() as c:
                row = dict(c.execute('SELECT b.*,m.poster_blob_id FROM blobs b JOIN blob_media m '
                                     'ON m.blob_id=b.id WHERE b.id=?', (result['file']['id'],)).fetchone())
            worker.process(row)
    before = task_row(api, tid)
    assert before['open_asks'] == 1
    assert before['cover']['url'] == f"/api/v2/files/{ids['video.mp4']}/poster?v=2"
    assert api.get(before['cover']['url'], headers=headers()).content == second_image
    listed = {f['name']: f for f in get(api, f'tasks/{tid}/files')['files']}
    assert len(listed) == 3
    for name, versions in contents.items():
        file = listed[name]
        assert file['id'] == ids[name] and file['current_version'] == 2
        assert [v['n'] for v in file['versions']] == [2, 1]
        for v in file['versions']:
            expected = versions[v['n'] - 1]
            assert v['by'] == 'bot:ops' and v['note'] == f"Version {v['n']}"
            assert v['mime'] == file['mime'] and v['size'] == len(expected)
            assert v['sha256'] == hashlib.sha256(expected).hexdigest()
            response = api.get(v['url'], headers=headers())
            assert response.status_code == 200 and response.content == expected
            assert response.headers['cache-control'] == 'private, no-store'
            assert response.headers['etag'] == '"' + v['sha256'] + '"'
            head = api.head(v['url'], headers=headers())
            assert head.status_code == 200 and head.content == b''
            assert head.headers['content-length'] == str(len(expected))
            assert head.headers['cache-control'] == response.headers['cache-control']
            assert api.get(v['url']).status_code == 401
            if name == 'image.png':
                assert (v['width'], v['height'], v['media_state']) == (16, 8, 'ready')
                if v['thumb_url']:
                    assert api.get(v['thumb_url'], headers=headers()).status_code == 200
            elif name == 'video.mp4':
                assert v['poster_url'] and v['media_state'] == 'ready'
                poster = api.head(v['poster_url'], headers=headers())
                assert poster.status_code == 200 and poster.headers['content-length'] == str(len(second_image))
    script = listed['script.md']['versions'][0]
    assert script['ask']['by'] == 'bot:ops' and script['answers'] == []
    assert script['ask']['questions'][0]['id'] == 'verdict'
    assert script['ask']['who'] is None
    for number, data in enumerate(contents['video.mp4'], 1):
        partial = api.get(f"/api/v2/files/{ids['video.mp4']}?v={number}",
                          headers={**headers(), 'Range': 'bytes=2-12'})
        assert partial.status_code == 206 and partial.content == data[2:13]
        assert partial.headers['content-range'] == f'bytes 2-12/{len(data)}'
        assert partial.headers['cache-control'] == 'private, no-store'
    comment = post(api, f'tasks/{tid}/comments', {'text': 'Review the second script.',
                   'attachments': [ids['script.md'] + '@2']}, token)['comment']
    attached = comment['refs']['attachments'][0]
    assert (attached['id'], attached['name'], attached['version']) == (ids['script.md'], 'script.md', 2)
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'completed', 'text': 'Ready for review.', 'last_seq': 0}, machine['token'])
    answer = post(api, f'tasks/{tid}/answers', {
        'target': {'file': ids['script.md'], 'version': 2}, 'answers': {'verdict': ['Approve']}})
    assert answer['answer']['by'] == 'human:ana'
    assert task_row(api, tid)['open_asks'] == 0
    assert task_row(api, tid)['cover'] == before['cover']
    versions = get(api, f'tasks/{tid}/files')['files']
    script = next(f for f in versions if f['id'] == ids['script.md'])['versions'][0]
    assert script['answers'] == [answer['answer']]
    comments = get(api, f'tasks/{tid}/comments')['comments']
    question = next(m for m in comments if m['kind'] == 'ask')
    assert question['ask'] == script['ask'] and question['answers'] == script['answers']
    next_turn = claim(api, machine, 'ops')
    assert next_turn['message']['refs']['answer'] == answer['answer']
    prompt = Runner.__new__(Runner).prompt(next_turn)
    assert json.loads(next(line[8:] for line in prompt.splitlines() if line.startswith('answer: '))) == answer['answer']
    assert answer['comment']['body'] in prompt
