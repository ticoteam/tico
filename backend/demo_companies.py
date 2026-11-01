"""Demo companies for Operations: Acme Heating & Air, Acme Kitchen and Acme Works. Each is fictional (acme.example),
and its records come in the way a customer's own files would: through `POST /api/v2/imports/commit`, one CSV file per
record type, in one format. Its contacts are made by the Finance action that makes them.

`python -m backend.demo --company heating-air` (or `kitchen`, `works`). The files are in backend/demo_data/<company>/."""
import dataclasses
from pathlib import Path

COMPANIES = {"heating-air": "Acme Heating & Air", "kitchen": "Acme Kitchen", "works": "Acme Works"}
DATA = Path(__file__).parent / "demo_data"
# A location before the equipment in it, and a customer (a contact) before the equipment that names it.
FILES = ("locations", "items", "equipment", "due_dates")
OWNER_ACTOR = "human:ana"


def populate(settings, company):
    """Write the company's database under `settings`: its people, Finance set up, its contacts, then its records from
    the CSV files. A file whose record type this build does not have yet is named on the way out, not loaded."""
    from fastapi.testclient import TestClient
    from . import demo_content, demo_seed, hubdb
    from .app import create_app
    from .auth import Identity
    from .finance import actions as finance
    from .records import csvimport
    if company not in COMPANIES:
        raise ValueError("no demo company " + company)
    demo_seed.write_registry(settings)
    seeding = dataclasses.replace(
        settings, demo=False, demo_public=False, local_owner_token_file=None,
        test_identities={demo_seed.OWNER: Identity(OWNER_ACTOR, "owner", settings.owner_email)})
    app = create_app(seeding)
    store = app.state.store
    headers = {"Authorization": "Bearer " + demo_seed.OWNER}

    def key(*parts):
        return "-".join(("demo", company) + parts)
    skipped = []
    with TestClient(app) as api:     # starting the app creates its tables; Finance is set up on them
        with store.transaction() as c:
            # The roster a first boot would read from registry/people.yaml: Finance needs the owner among its humans.
            hubdb.sync_registry(c, {}, {"people": demo_content.PEOPLE})
            assert finance.ensure_setup(c, settings.owner_email)
            contacts = (DATA / company / "contacts.csv").read_text(encoding="utf-8")
            for row in csvimport.read(contacts, ("name", "kind", "email"), ("name", "kind"))[0]:
                payload = {"name": row.cells["name"], "kind": row.cells["kind"]}
                if row.cells["email"]:
                    payload["emails"] = [row.cells["email"]]
                done = finance.act(c, OWNER_ACTOR, "contact_create", payload, key("contact", str(row.line)))
                assert done.status == "committed", done.reason
        for name in FILES:
            path = DATA / company / (name + ".csv")
            if not path.exists():
                continue
            body = {"record_type": name, "csv": path.read_text(encoding="utf-8")}
            reply = api.post("/api/v2/imports/commit", json=body,
                             headers={**headers, "Idempotency-Key": key("import", name)})
            if reply.status_code == 409 and reply.json()["error"]["code"] == "not_available":
                skipped.append(name)
                continue
            assert reply.status_code == 200, reply.text[:300]
            print(f"  {name}: {reply.json()['summary']}", flush=True)
    for name in skipped:
        print(f"  {name}.csv not loaded: this build does not have that record type yet", flush=True)
