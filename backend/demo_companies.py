"""Demo companies for Operations: Acme Heating & Air, Acme Kitchen and Acme Works. Each is fictional (acme.example),
and its records come in the way a customer's own files would: through `POST /api/v2/imports/commit`, one CSV file per
record type, in one format. Its contacts, projects, purchase orders and estimates (documents.json) are made by the
Finance and shared-record actions a person would use, and its business type is set last.

`python -m backend.demo --company heating-air` (or `kitchen`, `works`). The files are in
backend/demo_data/<company>/."""
import dataclasses
import json
from pathlib import Path

COMPANIES = {"heating-air": "Acme Heating & Air", "kitchen": "Acme Kitchen", "works": "Acme Works"}
# The business type each company is set up as (backend/sidebar.py): which Operations tabs it shows.
BUSINESS = {"heating-air": "field_service", "kitchen": "restaurant", "works": "manufacturer"}
DATA = Path(__file__).parent / "demo_data"
# A location before the equipment in it, and a customer (a contact) before the equipment that names it.
FILES = ("locations", "items", "equipment", "due_dates")
OWNER_ACTOR = "human:ana"
# The default chart (backend/finance/setup.py): what a purchase line posts to, and what an estimate line earns.
COST, REVENUE = "5000", "4000"


def populate(settings, company):
    """Write the company's database under `settings`: its people, Finance set up, its contacts, then its records from
    the CSV files, then its documents and business type. A file whose record type this build does not have yet is named
    on the way out, not loaded."""
    from fastapi.testclient import TestClient
    from . import demo_content, demo_seed, hubdb, sidebar
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
    skipped, contacts = [], {}
    with TestClient(app) as api:     # starting the app creates its tables; Finance is set up on them
        with store.transaction() as c:
            # The roster a first boot would read from registry/people.yaml: Finance needs the owner among its humans.
            hubdb.sync_registry(c, {}, {"people": demo_content.PEOPLE})
            assert finance.ensure_setup(c, settings.owner_email)
            text = (DATA / company / "contacts.csv").read_text(encoding="utf-8")
            for row in csvimport.read(text, ("name", "kind", "email"), ("name", "kind"))[0]:
                payload = {"name": row.cells["name"], "kind": row.cells["kind"]}
                if row.cells["email"]:
                    payload["emails"] = [row.cells["email"]]
                done = finance.act(c, OWNER_ACTOR, "contact_create", payload, key("contact", str(row.line)))
                assert done.status == "committed", done.reason
                contacts[row.cells["name"]] = str(done.result["contact_id"])
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
        with store.transaction() as c:
            _documents(c, company, contacts, key)
            sidebar.set_business_type(c, OWNER_ACTOR, BUSINESS[company])
    for name in skipped:
        print(f"  {name}.csv not loaded: this build does not have that record type yet", flush=True)


def _committed(done, what):
    """The result of an action the demo expects to commit: a demo that does not load says why, rather than going on."""
    assert done.status == "committed", f"{what}: {done.reason}"
    return done.result


def _documents(c, company, contacts, key):
    """The projects, purchase orders and estimates in documents.json, each one a Finance or shared-record action. Lines
    name their item by name and post to the default chart's accounts (COST for purchases, REVENUE for estimates)."""
    from .finance import actions as finance
    from .finance.banking import by_code
    from .finance.context import load
    from .records import csvimport
    from .records import reads
    doc = json.loads((DATA / company / "documents.json").read_text(encoding="utf-8"))
    ctx = load(c)
    accounts = {COST: by_code(ctx, COST).id, REVENUE: by_code(ctx, REVENUE).id}
    items = {csvimport.match_key(str(name)): str(record_id)
             for record_id, name in reads.active_rows(c, "items", ("name",))}

    def lines(rows, account):
        return [{"item_id": items[csvimport.match_key(row["item"])], "account_id": accounts[account],
                 "amount": row["amount"]} for row in rows]
    projects, orders, estimates = doc.get("projects", []), doc.get("purchase_orders", []), doc.get("estimates", [])
    for n, project in enumerate(projects, 1):
        body = {"name": project["name"]}
        if "customer" in project:
            body["customer_id"] = contacts[project["customer"]]
        _committed(finance.act(c, OWNER_ACTOR, "project_create", body, key("project", str(n))), project["name"])
    for n, po in enumerate(orders, 1):
        body = {"vendor_id": contacts[po["vendor"]], "po_date": po["po_date"], "memo": po["memo"],
                "lines": lines(po["lines"], COST)}
        made = _committed(finance.act(c, OWNER_ACTOR, "po_create", body, key("po", str(n))), po["memo"])
        if po.get("send"):
            _committed(finance.act(c, OWNER_ACTOR, "po_send", {"po_id": made["po_id"]}, key("po", str(n), "send")),
                       po["memo"])
    for n, estimate in enumerate(estimates, 1):
        body = {"customer_id": contacts[estimate["customer"]], "estimate_date": estimate["estimate_date"],
                "memo": estimate["memo"], "lines": lines(estimate["lines"], REVENUE)}
        _committed(finance.act(c, OWNER_ACTOR, "estimate_create", body, key("estimate", str(n))), estimate["memo"])
    print(f"  documents: {len(projects)} projects, {len(orders)} purchase orders, {len(estimates)} estimates",
          flush=True)
