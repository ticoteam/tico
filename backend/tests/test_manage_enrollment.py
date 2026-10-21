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
