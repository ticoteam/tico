"""The hosted install's offline enrollment uses the existing owner's roster identity."""

import json
import stat

from clients.environments import seed_registry

from backend import manage
from backend.auth import Auth
from backend.config import Settings
from backend.execution import Execution
from backend.models import Enrollment
from backend.store import Store


def test_enrollment_resolves_the_owner_email_to_the_existing_roster_id(tmp_path, monkeypatch, capsys):
    registry = seed_registry(tmp_path, "Ana", "ana@acme.example",
                             {"company_name": "Acme", "app_name": "Acme", "assistant_name": "Assistant"})
    monkeypatch.setenv("TICO_REGISTRY_DIR", str(registry))
    store = Store(Settings(db_path=tmp_path / "hub.sqlite", registry_dir=registry))
    store.initialize()
    store.seed()
    out = tmp_path / "enrollment.json"
    manage.main(["enrollment", str(store.settings.db_path), "--owner", "Ana@Acme.Example", "--out", str(out)])
    code = json.loads(out.read_text())
    assert code["operator"] == "ana"
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert code["code"] not in capsys.readouterr().out
    computer = Execution(store, Auth(store)).enroll(Enrollment(code=code["code"], label="Server computer", platform="linux"))
    with store.read() as c:
        assert c.execute("SELECT operator FROM runners WHERE id=?", (computer["runner_id"],)).fetchone()[0] == "ana"


def test_hosting_digest_is_bound_to_owner_machine_and_one_runner(tmp_path, monkeypatch):
    import hashlib
    import pytest
    from backend.hosting_enrollment import enrollment
    from backend.store import H, Problem
    registry = seed_registry(tmp_path, 'Ana', 'ana@acme.example',
                             {'company_name':'Acme','app_name':'Acme','assistant_name':'Assistant'})
    monkeypatch.setenv('TICO_REGISTRY_DIR', str(registry))
    store = Store(Settings(db_path=tmp_path/'hub.sqlite', registry_dir=registry))
    store.initialize()
    store.seed()
    auth = Auth(store)
    code, ident, label = 'private-one-time-join-'+'x'*32, 'pv-'+'a'*16, 'pv-machine'
    hashed = hashlib.sha256(code.encode()).hexdigest()
    assert enrollment(store, auth, ident, hashed, label, 'ana@acme.example') == {'online':False}
    with pytest.raises(Problem):
        enrollment(store, auth, ident, hashed, label, 'other@acme.example')
    with pytest.raises(ValueError):
        enrollment(store, auth, ident, 'b'*64, label, 'ana@acme.example')
    execution = Execution(store, auth)
    with pytest.raises(Problem):
        execution.enroll(Enrollment(code=code, label='other', platform='linux'))
    first = execution.enroll(Enrollment(code=code, label=label, platform='linux'))
    again = execution.enroll(Enrollment(code=code, label=label, platform='linux'))
    assert first == again
    enrollment(store, auth, ident, hashed, label, 'ana@acme.example')
    with store.transaction() as c:
        assert c.execute('SELECT count(*) FROM enrollments').fetchone()[0] == 1
        c.execute('UPDATE runners SET last_seen=? WHERE id=?', (H.now(), first['runner_id']))
    assert enrollment(store, auth, ident, hashed, label, 'ana@acme.example', status=True) == {'online':True}
    assert code.encode() not in (tmp_path/'hub.sqlite').read_bytes()
