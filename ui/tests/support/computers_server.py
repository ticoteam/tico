"""A real Tico server for the Computers page browser test (ui/tests/computers-shared.cjs).

    python3 ui/tests/support/computers_server.py

A loopback install (local owner bearer) with the credential vault on, and three fake computers, "Team box", "Lab box"
and "Spare box", with Claude Code installed and not signed in. Spare box only sends heartbeats: it never takes a job or
fetches a key, so it stays a computer a key serves that has not used it yet. Each fake computer does what runner-rs/src/signin.rs does with a key:
it takes a `use_key` job, fetches the staged key from `runner-model-credentials`, reports it working and from then on
says Claude Code signs in with that key; it also fetches its keys every second, as a credentials change tells it to,
and signs in with a shared key it is handed. Every key value a computer received is appended to the file named
`received`, so the test can show the key reached the computer while the page never showed it again.
Prints one JSON line when ready: {"url", "token", "received", "computers": {label: id}}. Stops on SIGTERM.
"""

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
import yaml  # noqa: E402

from backend.app import create_app  # noqa: E402
from backend.auth import Identity  # noqa: E402
from backend.config import Settings  # noqa: E402
from backend.credentials import CredentialCipher  # noqa: E402
from backend.store import H, encode  # noqa: E402
from backend.tests.test_credentials import FakeKMS  # noqa: E402

LOCAL_TOKEN = "local-owner-token-for-the-browser-test-0123456789"
OWNER = "ana@acme.example"
CAPABILITIES = ["model_sign_out_v1", "model_keys_v1"]


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fake_computer(base, admin, label, received, ready, works=True):
    def call(method, path, body=None, token=admin, **params):
        r = httpx.request(method, base + "/api/v2/" + path, json=body, params=params or None, timeout=20,
                          headers={"Authorization": "Bearer " + token, "Idempotency-Key": H.new_id()})
        r.raise_for_status()
        return r.json()
    code = call("POST", "enrollments", {"operator": "ana"})["code"]
    made = call("POST", "runners/enroll", {"code": code, "label": label, "platform": "linux"}, token="none")
    token = made["token"]
    claude = {"installed": True, "authenticated": "missing"}

    def beat():
        call("POST", "runners/heartbeat", {"version": "test", "platform": "linux", "capabilities": CAPABILITIES,
                                           "readiness": {"schema_version": 1, "runtimes": {"claude": claude}, "bots": {}}},
             token)

    def use(credential, source):
        with open(received, "a") as f:
            f.write(credential["value"] + "\n")
        claude.clear()
        claude.update({"installed": True, "authenticated": "ready", "key": {"name": credential["env"], "source": source},
                       "account": {"kind": "api_key", "checked_at": H.now()}})
    beat()
    ready[label] = made["runner_id"]
    while not works:
        beat()
        time.sleep(1)
    while True:
        for job in call("GET", "runner-logins", token=token)["logins"]:
            state = "failed"
            if job["kind"] == "use_key":
                found = call("GET", "runner-model-credentials", token=token, runtime="claude", login=job["id"])
                use(found["credentials"][0], "computer")
                state = "done"
            elif job["kind"] == "clear_key":
                claude.clear()
                claude.update({"installed": True, "authenticated": "missing"})
                state = "signed_out"
            elif job["kind"] == "use_shared_key":
                state = "done"    # the key fetch below signs it in
            call("POST", f"runner-logins/{job['id']}/report", {"state": state, "message": ""}, token=token)
        found = call("GET", "runner-model-credentials", token=token, runtime="claude")
        keys = found["credentials"]
        if found.get("signed_out") or found.get("key_off"):
            claude.clear()
            claude.update({"installed": True, "authenticated": "missing"})
        elif keys and keys[0]["source"] == "shared" and claude.get("key", {}).get("source") != "shared":
            use(keys[0], "shared")
        elif keys and keys[0]["source"] == "shared" and keys[0]["value"] not in open(received).read().split("\n"):
            use(keys[0], "shared")    # the shared key was replaced
        beat()
        time.sleep(0.5)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="tico-computers-"))
    registry = tmp / "registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text(yaml.safe_dump({"owner": OWNER}))
    port = free_port()
    url = "http://127.0.0.1:%d" % port
    settings = Settings(db_path=tmp / "hub.db", registry_dir=registry, public_url=url, company_name="Acme",
                        app_name="Acme HQ", local_owner_email=OWNER, local_owner_token=LOCAL_TOKEN,
                        credential_kms_key="test-key", credential_admins=(OWNER,),
                        test_identities={"admin": Identity("human:ana", "owner", OWNER)})
    app = create_app(settings)
    app.state.vault.cipher = CredentialCipher("test-key", FakeKMS())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    people = [{"id": "ana", "name": "Ana Rivera", "email": OWNER, "title": "CEO", "primary_for": ["*"]}]
    with app.state.store.transaction() as c:
        H.sync_registry(c, {}, {"people": people})
        c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode({"people": people}),))
        c.execute("INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({"completed": "2026-01-01T00:00:00Z"}),))
    received, ready = tmp / "received.txt", {}
    received.write_text("")
    for label in ("Team box", "Lab box", "Spare box"):
        threading.Thread(target=fake_computer, args=(url, "admin", label, received, ready, label != "Spare box"),
                         daemon=True).start()
    while len(ready) < 3:
        time.sleep(0.05)
    print(json.dumps({"url": url, "token": LOCAL_TOKEN, "received": str(received), "computers": ready}), flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
