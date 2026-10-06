"""A real listening API, stdlib HTTP client, bot CLI subprocess, and local runtime adapter."""

import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from backend.tests.test_api import api, assign, claim, get, post, ready, runner  # noqa: F401
from clients.tico import Client
from runner.service import Runner
from runner.hosts.fake import FakeHost


@pytest.fixture
def live(api):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(api.app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
    sock.close()
    assert not thread.is_alive()


@pytest.mark.slow
def test_full_http_runner_roundtrip_and_one_provider_session_per_bot(api, live, tmp_path):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    hosts = []
    def factory(attempt, env):
        host = FakeHost(replies=["This answer ran on the local machine."])
        hosts.append(host)
        return host
    service = Runner({"url": live, "token": r["token"], "projects_dir": str(tmp_path)},
                     tmp_path / "runner", host_factory=factory)
    service.environment = lambda attempt: {"HUB_API_URL": live, "HUB_TOKEN": attempt["token"]}
    client = Client(live, "ana-test")
    msg = client.post("chat/ops", {"text": "Please answer locally"})["message"]
    service.execute(claim(api, r))
    messages = client.get(f"conversations/{msg['conversation_id']}/messages")["messages"]
    assert messages[-1]["body"] == "This answer ran on the local machine."
    assert service.state.unfinished() == []
    assert hosts[0].prompts
    ben = Client(live, "ben-test")
    other = ben.post("chat/ops", {"text": "A separate person's conversation"})["message"]
    service.execute(claim(api, r))
    assert other["conversation_id"] != msg["conversation_id"]
    assert hosts[1].resumes == [hosts[0].prompts[0][0]]
    assert "Please answer locally" not in hosts[1].prompts[0][1]
    service.pool.shutdown()


def test_vault_injects_only_granted_secrets_and_removes_temporary_files(api, live, tmp_path, monkeypatch):
    from backend.tests.test_credentials import setup, create
    setup(api)
    file_secret='synthetic-service-account-fixture'
    secret=create(api,name='Service account',kind='file',env='GOOGLE_SA_KEY',secret=file_secret)
    create(api,name='Unassigned',env='UNASSIGNED_API_KEY',secret='never-deliver-this-fixture')
    grant=post(api,f"credentials/{secret['id']}/grants",{'subject':'bot:ops'})
    machine=runner(api);assign(api,machine,'ops');ready(api,machine,['ops'])
    msg=post(api,'chat/ops',{'text':'Use a synthetic granted credential'})
    secrets=tmp_path/'secrets'
    secrets.mkdir()
    (secrets/'_shared.env').write_text('SHARED_API_KEY=legacy-fixture\nGOOGLE_SA_KEY=legacy-fallback\n')
    (secrets/'ops.env').write_text('OWN_API_KEY=own-fixture\n')
    monkeypatch.setenv('AMBIENT_API_KEY', 'ambient-fixture')
    files=[]
    def factory(attempt,env):
        p=Path(env['GOOGLE_SA_KEY']);files.append(p)
        assert p.read_text()==file_secret and p.stat().st_mode & 0o777 == 0o600
        assert not {'UNASSIGNED_API_KEY','SHARED_API_KEY','OWN_API_KEY','AMBIENT_API_KEY'} & env.keys()
        return FakeHost(replies=['A diagnostic with '+file_secret])
    service=Runner({'url':live,'token':machine['token'],'projects_dir':str(tmp_path)},tmp_path/'state',host_factory=factory)
    try:
        service.execute(claim(api,machine))
        assert files and not files[0].exists()
        assert not service.vault_values and not service.vault_files
        messages=get(api,f"conversations/{msg['conversation_id']}/messages")
        assert file_secret not in json.dumps(messages)
        assert '••••' in messages[-1]['body']
        post(api,f"credentials/{secret['id']}/grants/{grant['id']}/revoke",{})
        post(api,'chat/ops',{'text':'QA check after revocation'})
        after=claim(api,machine)
        env=service.environment(after)
        assert not {'GOOGLE_SA_KEY','SHARED_API_KEY','OWN_API_KEY','AMBIENT_API_KEY'} & env.keys()
    finally:
        service.pool.shutdown()
