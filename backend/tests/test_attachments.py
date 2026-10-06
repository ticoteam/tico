"""Real attachment submissions, authorization boundaries, retries, and local downloads."""

import pytest

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
    task = upload(api, 'tasks', {'title': 'Recover task file', 'body': 'Review', 'owner': 'cpo'}, 'ben-test', data=b'Task bytes')
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
