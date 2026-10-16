"""Run the collector: `python -m hq`. It will not start unless TICO_HQ_KEY is set, so it cannot be turned on by
accident (in particular, not inside a customer's Tico). The key is a deliberate-enable switch: clients are not
authenticated, the public endpoints are open by design."""
import os
import sys

import uvicorn

from . import tunnel
from .app import create_app
from .db import Database
from .notices import COMPARE_URL
from .releases import RELEASES_URL, Latest


def main():
    key = os.environ.get("TICO_HQ_KEY", "").strip()
    if len(key) < 16:
        sys.exit("tico-hq: set TICO_HQ_KEY to a string of at least 16 characters to run the collector. It is not part "
                 "of a Tico server and does nothing until you turn it on on purpose (docs/telemetry.md).")
    tunnel.prepare()                    # the cloudflared profile's route, when its volume is mounted
    db = Database(os.environ.get("HQ_DB", "/data/hq.db"))
    latest = Latest(os.environ.get("HQ_RELEASES_URL", "") or RELEASES_URL, os.environ.get("GITHUB_TOKEN", ""))
    staff_key = os.environ.get("HQ_STAFF_KEY", "").strip()
    if staff_key and len(staff_key) < 24:
        sys.exit("tico-hq: HQ_STAFF_KEY must be at least 24 characters (or unset, which turns the staff routes off).")
    watch_args = {"compare_url": os.environ.get("HQ_COMPARE_URL", "") or COMPARE_URL, "token": os.environ.get("GITHUB_TOKEN", "")}
    app = create_app(db, latest, client_ip_header=os.environ.get("HQ_CLIENT_IP_HEADER", ""), staff_key=staff_key,
                     watch_args=watch_args)
    # access_log=False: nothing logs a request, so no address or query string is ever written.
    uvicorn.run(app, host=os.environ.get("HQ_HOST", "0.0.0.0"), port=int(os.environ.get("HQ_PORT", "8770")),
                access_log=False, log_level="warning", server_header=False, proxy_headers=False)


if __name__ == "__main__":
    main()
