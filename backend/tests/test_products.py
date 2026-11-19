"""Company › Products (backend/products.py): our products over the Directory's Product records, end to end through the
API, `tico products` and the published contract."""

import json

import pytest

from backend import hubdb as H
from backend.tests.helpers.api import headers
from backend.tests.helpers.openapi_v2 import conforms
from clients import hubcli, remotecli
from clients.tico import APIError


def answer(api, method, path, token="ana-test", **kwargs):
    return getattr(api, method)("/api/v2/" + path, headers=headers(token), **kwargs)


def ok(api, method, path, token="ana-test", **kwargs):
    r = answer(api, method, path, token, **kwargs)
    assert r.status_code == 200, r.text
    return r.json()


def refused(api, method, path, status, code, **kwargs):
    r = answer(api, method, path, **kwargs)
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code, r.text
    return r.json()["error"]["detail"]


def names(items):
    return [item["name"] for item in items]


@pytest.fixture
def repos(api):
    with api.app.state.store.transaction() as c:
        for rid, name in (("r-web", "Acme/web"), ("r-app", "Acme/app")):
            c.execute("INSERT INTO repositories(id,full_name,enabled,updated_at) VALUES(?,?,1,?)", (rid, name, H.now()))


def test_a_product_holds_its_eight_fields_and_reads_back_as_the_contract_says(api, repos):
    made = ok(
        api,
        "post",
        "products",
        json={
            "name": "Lightshow App",
            "kind": "software",
            "status": "live",
            "tax_kind": "software_service",
            "owner_teammate": "human:ben",
            "repositories": ["acme/web"],  # matched as Tico spells it
            "agreements": ["App Terms"],
            "depends_on": ["Lumen Parts", "Northline Freight"],
        },
    )["product"]
    assert made["id"] == "product/" + made["key"]
    assert {k: made[k] for k in ("name", "kind", "status", "tax_kind", "owner_teammate", "repositories")} == {
        "name": "Lightshow App",
        "kind": "software",
        "status": "live",
        "tax_kind": "software_service",
        "owner_teammate": "human:ben",
        "repositories": ["Acme/web"],
    }
    [terms] = made["agreements"]
    assert terms["name"] == "App Terms" and terms["id"].startswith("document/") and terms["file"] is None
    assert names(made["depends_on"]) == ["Lumen Parts", "Northline Freight"]
    assert all(item["id"].startswith("organization/") and item["link_id"] for item in made["depends_on"])

    listed = ok(api, "get", "products", "cara-test")
    assert listed["products"] == [made]
    shown = ok(api, "get", "products/" + made["key"])

    document = ok(api, "get", "openapi.json")
    for path, method, result in (
        ("/api/v2/products", "get", listed),
        ("/api/v2/products", "post", {"product": made}),
        ("/api/v2/products/{key}", "get", shown),
        ("/api/v2/products/{key}", "post", shown),
    ):
        schema = document["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert conforms(result, schema, document) is None, (path, method)

    # A product is a Contacts record: its card shows the links the product page wrote.
    card = ok(api, "get", "entities/card", params={"ref": made["id"]})
    assert {link["other_name"] for link in card["links"]} >= {"App Terms", "Lumen Parts", "Northline Freight"}


def test_an_edit_changes_only_what_it_names_clears_with_empty_and_replaces_whole_sets(api, repos):
    key = ok(
        api,
        "post",
        "products",
        json={
            "name": "Lightshow Kit",
            "kind": "goods",
            "status": "beta",
            "owner_teammate": "human:ben",
            "repositories": ["Acme/web", "Acme/app"],
            "depends_on": ["Lumen Parts", "Northline Freight"],
        },
    )["product"]["key"]

    edited = ok(
        api, "post", "products/" + key, "cara-test", json={"status": "live", "depends_on": ["Northline Freight"]}
    )
    product = edited["product"]
    assert (product["kind"], product["status"], product["owner_teammate"]) == ("goods", "live", "human:ben")
    assert product["repositories"] == ["Acme/app", "Acme/web"]
    assert names(product["depends_on"]) == ["Northline Freight"]

    cleared = ok(
        api, "post", "products/" + key, json={"kind": "", "owner_teammate": "", "repositories": [], "depends_on": []}
    )["product"]
    assert (cleared["kind"], cleared["owner_teammate"], cleared["status"]) == (None, None, "live")
    assert cleared["repositories"] == [] and cleared["depends_on"] == []

    renamed = ok(api, "post", "products/" + key, json={"name": "Lightshow Kit 2", "repositories": ["Acme/app"]})
    assert renamed["product"]["name"] == "Lightshow Kit 2" and renamed["product"]["repositories"] == ["Acme/app"]
    assert renamed["product"]["key"] == key  # the key stays when the name changes

    # The values a clear removed are kept in the record's history, as any Directory edit is.
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT value FROM entity_value_history WHERE entity_id=?", ("product/" + key,))
        kept = {json.loads(row[0]) for row in rows}
    assert {"goods", "human:ben", "Acme/web"} <= kept


def test_refusals_name_what_is_wrong(api, repos):
    ok(api, "post", "products", json={"name": "Lightshow App"})
    key = ok(api, "get", "products")["products"][0]["key"]

    assert "Acme/missing" in refused(
        api, "post", "products/" + key, 422, "kind", json={"repositories": ["Acme/missing"]}
    )
    refused(api, "post", "products", 409, "duplicate", json={"name": "lightshow app"})
    refused(api, "get", "products/no-such-product", 404, "not_found")
    refused(api, "post", "products/no-such-product", 404, "not_found", json={"status": "live"})
    refused(api, "get", "products/Not%20A%20Key", 404, "not_found")
    assert answer(api, "post", "products/" + key, json={"kind": "spaceship"}).status_code == 422
    assert ok(api, "get", "products/" + key)["product"]["kind"] is None  # nothing changed


def test_a_directory_product_becomes_ours_when_added_and_others_stay_out_of_the_list(api):
    existing = ok(
        api, "post", "entities", json={"type": "product", "match": {"name": "Show Cloud"}, "name": "Show Cloud"}
    )
    ok(api, "post", "entities", json={"type": "product", "match": {"name": "Rival Lamp"}, "name": "Rival Lamp"})
    assert ok(api, "get", "products")["products"] == []

    added = ok(api, "post", "products", json={"name": "Show Cloud", "status": "planned"})["product"]
    assert added["id"] == existing["id"]
    assert names(ok(api, "get", "products")["products"]) == ["Show Cloud"]
    refused(api, "get", "products/" + "rival-lamp", 404, "not_found")


class Forward:
    """The client `tico products` calls, answered by the app's own test client as ana."""

    def __init__(self, api):
        self.api = api

    def _answer(self, r):
        if r.status_code >= 400:
            error = r.json()["error"]
            raise APIError(error["code"], error["detail"], r.status_code)
        return r.json()

    def get(self, path, /, **query):
        return self._answer(self.api.get("/api/v2/" + path, params=query, headers=headers("ana-test")))

    def post(self, path, body=None, key=None):
        return self._answer(self.api.post("/api/v2/" + path, json=body or {}, headers=headers("ana-test", key)))


def test_tico_products_adds_edits_and_clears_a_set(api, repos, monkeypatch):
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: Forward(api))

    def tico(*argv):
        return remotecli.run(hubcli.parser().parse_args(["products", *argv]))

    tico("add", "Lightshow App", "--kind", "software", "--repo", "Acme/web", "--depends-on", "Lumen Parts")
    [product] = ok(api, "get", "products")["products"]
    assert (product["kind"], product["repositories"], names(product["depends_on"])) == (
        "software",
        ["Acme/web"],
        ["Lumen Parts"],
    )

    tico("update", product["key"], "--repo", "", "--tax-kind", "software_service", "--agreement", "App Terms")
    product = ok(api, "get", "products/" + product["key"])["product"]
    assert product["repositories"] == [] and product["tax_kind"] == "software_service"
    assert names(product["agreements"]) == ["App Terms"] and names(product["depends_on"]) == ["Lumen Parts"]
