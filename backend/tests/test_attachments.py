"""Real attachment submissions, authorization boundaries, retries, and local downloads."""

import hashlib
import io
import json

import pytest
import threading
from PIL import Image

from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner  # noqa: F401


def upload(api, path, fields, token="ana-test", key=None, data=b"Original material", expected=200):
    response = api.post("/api/v2/uploads/" + path, data=fields,
                        files={"files": ("../draft<one>.txt", data, "text/plain")}, headers=headers(token, key))
    assert response.status_code == expected, response.text
    return response.json()


@pytest.mark.parametrize("path,fields", [
    ("tasks", {"title": "Private task", "body": "Private", "owner": "inbox"}),
])
def test_forbidden_upload_never_publishes_file_or_work(api, path, fields):
    upload(api, path, fields, "cara-test", expected=404)      # a bot he cannot see
    with api.app.state.store.read() as c:
        for table in ("blobs", "messages", "tasks", "jobs", "message_assets", "task_assets"):
            assert c.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0


def test_same_bot_other_conversation_cannot_download_private_attachment(api):
    result = upload(api, 'chat/product-design', {'text': 'Private Ben conversation'}, 'ben-test')
    attachment = result['message']['refs']['attachments'][0]
    machine = runner(api, 'ben')
    assign(api, machine, 'product-design')
    ready(api, machine, ['product-design'])
    first = claim(api, machine)
    post(api, f"attempts/{first['id']}/started", {'thread_id': 'first'}, machine['token'])
    post(api, f"attempts/{first['id']}/complete", {'outcome': 'completed', 'last_seq': 0, 'text': 'Read'}, machine['token'])
    post(api, 'chat/product-design', {'text': 'Different Ana conversation'})
    other = claim(api, machine)
    assert other['conversation']['id'] != result['conversation']['id']
    assert api.get(attachment['url'], headers=headers(other['token'])).status_code == 403


def test_bot_downloads_a_chat_image_only_in_its_authorized_conversation(api):
    image = io.BytesIO()
    Image.new('RGB', (1, 1)).save(image, format='PNG')
    data = image.getvalue()
    response = api.post('/api/v2/uploads/chat/ops', data={'text': 'Review this image'},
                        files={'files': ('sample.png', data, 'image/png')}, headers=headers('ben-test'))
    assert response.status_code == 200, response.text
    chat = response.json()
    attachment = chat['message']['refs']['attachments'][0]
    machine = runner(api)
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    attempt = claim(api, machine, 'ops')
    assert attempt['conversation']['id'] == chat['conversation']['id']
    get(api, 'conversations/' + chat['conversation']['id'] + '/messages', attempt['token'])
    downloaded = api.get(attachment['url'], headers=headers(attempt['token']))
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == data
    assert downloaded.headers['content-type'] == 'image/png'
    assert downloaded.headers['x-content-sha256'] == hashlib.sha256(data).hexdigest()
    # A later turn of the same bot in somebody else's chat still cannot read this image.
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'image'}, machine['token'])
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'completed', 'last_seq': 0, 'text': 'Read'}, machine['token'])
    post(api, 'chat/ops', {'text': 'A separate conversation'})
    other = claim(api, machine, 'ops')
    assert other['conversation']['id'] != chat['conversation']['id']
    assert api.get(attachment['url'], headers=headers(other['token'])).status_code == 403


def test_company_owner_cannot_download_another_persons_private_chat_attachment(api):
    result = upload(api, 'chat/ops', {'text': 'Ben-only attachment'}, 'ben-test')
    attachment = result['message']['refs']['attachments'][0]
    assert api.get(attachment['url'], headers=headers('ana-test')).status_code == 403
    assert api.get(attachment['url'], headers=headers('ben-test')).status_code == 200


def test_chat_and_task_attachments_survive_full_backup_restore(api, tmp_path):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.backup import restore_bundle
    from backend.tests.test_backup_bundle import ObjectStore, bundle, target
    chat = upload(api, 'chat/cpo', {'text': 'Private attachment'}, 'ben-test')
    task = upload(api, 'tasks', {'title': 'Recover task file', 'body': 'Review', 'owner_id': 'cpo'}, 'ben-test', data=b'Task bytes')
    storage = ObjectStore()
    report = bundle(api, storage)
    assert report['objects'] == 2
    api.app.state.blobs.directory.rename(tmp_path / 'unavailable-live-files')
    settings = target(tmp_path)
    restore_bundle('backup-bucket', report['key'], settings, storage)
    settings.test_identities = api.app.state.store.settings.test_identities
    with TestClient(create_app(settings)) as restored:
        attachment = chat['message']['refs']['attachments'][0]
        history = get(restored, 'conversations/' + chat['conversation']['id'] + '/messages', 'ben-test')
        assert history[0]['refs']['attachments'] == [attachment]
        assert restored.get(attachment['url'], headers=headers('ben-test')).content == b'Original material'
        assert restored.get(attachment['url'], headers=headers('cara-test')).status_code == 403
        recovered = get(restored, 'tasks/' + task['task']['id'])['task']
        assert recovered['attachments'] == task['task']['attachments']
        assert restored.get(recovered['attachments'][0]['url'], headers=headers()).content == b'Task bytes'


def test_person_forwards_a_chat_file_to_a_bot_task_and_only_readers_gain_it(api):
    chat = upload(api, 'chat/product-design', {'text': 'Source export'}, 'ben-test', data=b'Chat bytes')
    attachment = chat['message']['refs']['attachments'][0]
    tid = post(api, 'tasks', {'owner_id': 'ops', 'title': 'Use the export', 'body': 'Read it.'}, 'ben-test')['id']
    machine = runner(api)
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    attempt = claim(api, machine, 'ops')
    assert api.get(attachment['url'], headers=headers(attempt['token'])).status_code == 403
    ref = attachment['id'] + '@1'
    # A person who cannot open the file, and a bot, cannot attach it by its id.
    assert api.get(attachment['url'], headers=headers('ana-test')).status_code == 403
    post(api, f'tasks/{tid}/comments', {'text': 'Here', 'attachments': [ref]}, 'ana-test', expected=404)
    post(api, f'tasks/{tid}/comments', {'text': 'Here', 'attachments': [ref]}, attempt['token'], expected=404)
    assert api.get(attachment['url'], headers=headers(attempt['token'])).status_code == 403
    comment = post(api, f'tasks/{tid}/comments', {'text': 'The export', 'attachments': [ref]}, 'ben-test')['comment']
    forwarded = comment['refs']['attachments'][0]
    assert forwarded['id'] != attachment['id'] and forwarded['name'] == attachment['name']
    downloaded = api.get(forwarded['url'], headers=headers(attempt['token']))
    assert downloaded.status_code == 200 and downloaded.content == b'Chat bytes'
    # The task got a copy: the chat file itself is still closed to the task's bot.
    assert api.get(attachment['url'], headers=headers(attempt['token'])).status_code == 403
    again = post(api, f'tasks/{tid}/comments', {'text': 'Again', 'attachments': [ref]}, 'ben-test')['comment']
    assert again['refs']['attachments'][0]['ref'] == forwarded['ref']
    # Sharing it is the point: whoever reads the task now opens it through the task.
    assert api.get(forwarded['url'], headers=headers('ana-test')).status_code == 200


def test_sharing_a_chat_file_into_a_private_task_leaves_the_chat_file_readable(api):
    chat = upload(api, 'chat/ops', {'text': 'Source export'}, 'ben-test', data=b'Chat bytes')
    attachment = chat['message']['refs']['attachments'][0]
    machine = runner(api)
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    attempt = claim(api, machine, 'ops')
    assert attempt['conversation']['id'] == chat['conversation']['id']
    tid = post(api, 'tasks', {'owner_id': 'cpo', 'title': 'Private', 'body': 'Read it.', 'private': True}, 'ben-test')['id']
    post(api, f'tasks/{tid}/comments', {'text': 'The export', 'attachments': [attachment['id'] + '@1']}, 'ben-test')
    assert api.get(attachment['url'], headers=headers(attempt['token'])).status_code == 200


class CountedWake(threading.Event):
    def __init__(self):
        super().__init__()
        self.sets = 0

    def set(self):
        self.sets += 1
        super().set()


def test_a_shared_chat_file_records_what_an_upload_does(api, monkeypatch):
    """Sharing a chat file to a task by comment adds the same task activity line, audit event and preview row as
    uploading it, and wakes the preview worker rather than leaving it to the next poll."""
    chat = upload(api, 'chat/product-design', {'text': 'Source export'}, 'ben-test', data=b'Chat bytes')
    ref = chat['message']['refs']['attachments'][0]['id'] + '@1'
    tid = post(api, 'tasks', {'owner_id': 'ops', 'title': 'Use the export', 'body': 'Read it.'}, 'ben-test')['id']
    wake = CountedWake()
    monkeypatch.setattr(api.app.state.file_metadata, 'wake', wake)
    forwarded = post(api, f'tasks/{tid}/comments', {'text': 'The export', 'attachments': [ref]},
                     'ben-test')['comment']['refs']['attachments'][0]
    assert wake.sets == 1
    with api.app.state.store.read() as c:
        blob = c.execute("SELECT blob_id FROM bot_file_versions WHERE file_id=? AND version=?",
                         (forwarded['id'], forwarded['version'])).fetchone()[0]
        lines = c.execute("SELECT actor,new FROM task_events WHERE task_id=? AND field='file'", (tid,)).fetchall()
        audit = [json.loads(r[0]) for r in c.execute("SELECT detail FROM events WHERE action='task.file' AND target=?",
                                                     (tid,))]
        preview = c.execute("SELECT 1 FROM blob_media WHERE blob_id=?", (blob,)).fetchone()
    assert [tuple(r) for r in lines] == [('human:ben', forwarded['name'])]
    assert [a['file'] for a in audit] == [blob]
    assert preview
    # Sharing the same file again points at the copy already there: nothing new to record.
    post(api, f'tasks/{tid}/comments', {'text': 'Again', 'attachments': [ref]}, 'ben-test')
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM task_events WHERE task_id=? AND field='file'", (tid,)).fetchone()[0] == 1
