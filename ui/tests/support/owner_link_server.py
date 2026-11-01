"""A real Tico server on the owner sign-in link, for ui/tests/owner-link.cjs.

    python3 ui/tests/support/owner_link_server.py

TICO_AUTH_PROXY=owner-link on a hosted install whose public address is https://acme.hosted.test; the browser test
routes that address here. The link's secret is made here, the server gets only its hash, as the installer does.
Prints one JSON line when ready: {"url", "public", "token", "db", "registry"}. Stops on SIGTERM.
"""

import json
import secrets
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import uvicorn  # noqa: E402
import yaml  # noqa: E402

from backend.app import create_app  # noqa: E402
from backend.config import Settings  # noqa: E402
from backend.store import H, digest, encode  # noqa: E402

PUBLIC = "https://acme.hosted.test"
OWNER = "ana@acme.example"


def main():
    tmp = Path(tempfile.mkdtemp(prefix="tico-owner-link-"))
    registry = tmp / "registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text(yaml.safe_dump({"owner_id": OWNER}))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    token = secrets.token_urlsafe(32)
    settings = Settings(db_path=tmp / "hub.db", registry_dir=registry, public_url=PUBLIC, company_name="Acme",
                        app_name="Acme HQ", auth_proxy="owner-link", hosted="own", owner_email=OWNER,
                        owner_link_hash=digest(token))
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    people = [{"id": "ana", "name": "Ana Rivera", "email": OWNER, "title": "CEO", "primary_for": ["*"]}]
    with app.state.store.transaction() as c:
        H.sync_registry(c, {}, {"people": people})
        c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode({"people": people}),))
        c.execute("INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({"completed": "2026-01-01T00:00:00Z"}),))
    print(json.dumps({"url": "http://127.0.0.1:%d" % port, "public": PUBLIC, "token": token,
                      "db": str(settings.db_path), "registry": str(registry)}), flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
