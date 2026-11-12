"""Real network server over the e2e suite's disposable settings and fictional company."""

import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import uvicorn

from backend.app import create_app
from backend.store import Store
from backend.tests.helpers.company import seed
from e2e.server import settings_for


def main():
    directory, port = Path(sys.argv[1]), int(sys.argv[2])
    first = not (directory / "hub.db").exists()
    registry = directory / "hub-registry"
    if registry.exists():
        # api_settings creates this directory; it contains only this run's generated access file.
        (registry / "hub-access.yaml").unlink()
        registry.rmdir()
    settings = dataclasses.replace(
        settings_for(directory, directory / "hub.db"),
        lease_seconds=3600,
        public_url=f"http://127.0.0.1:{port}",
    )
    (settings.registry_dir / "hub-access.yaml").write_text("owner: ana@acme.example\nbot_admins: []\n")
    if first:
        store = Store(settings)
        store.initialize()
        with store.transaction() as c:
            seed(c)
            c.execute("UPDATE bot_config SET owner_id='ben',reports_to='human:ben' WHERE bot='ops'")
        store.close()
    app = create_app(settings)

    @app.get("/api/v2/live-browser-queued")
    def queued(account: str):
        from backend.browser_commands import MAILBOX

        # A count makes the live control race deterministic without revealing or changing any command payload.
        with MAILBOX.lock:
            return {
                "click": sum(
                    cmd.verb == "click" and cmd.account_id == account
                    for queue in MAILBOX.queues.values()
                    for cmd in queue
                )
            }

    app.router.routes.insert(0, app.router.routes.pop())

    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
