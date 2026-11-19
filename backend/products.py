# pyright: strict
"""Company › Products (`tico products`): our products, the record that joins Finance, Legal, Growth and Customers.

A product is a Contacts Product record that our company owns (company/self `owns` it). This module is a thin layer over
backend/entities/store.py: the record, its values (kind, status, tax kind, owner, repositories) and its links (`sold_under`
an agreement, `depends_on` a vendor) all live in the Directory's tables, under its read rule and writer gate, so a product
also reads on its Contacts page and in `tico contacts get`.

A product's path names it by its key: the part of its id after `product/` (a path parameter cannot hold the slash).
"""

import re
import sqlite3
from typing import TYPE_CHECKING, Any, cast

from fastapi import Request
from pydantic import Field

from backend.api_contract import api_v2
from backend.entities import store as ES
from backend.models import Contract
from backend.store import Problem

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from fastapi import FastAPI

    from backend.auth import Auth
    from backend.store import Store

SOURCE = (
    "hub"  # a value's source when a person or a bot wrote it here, as Contacts writes it; the source_id is the actor
)
TYPE = "product"
CHOICES = ("kind", "status", "tax_kind")  # the select fields, by attribute key
LINKS = {"agreements": ("sold_under", "document"), "depends_on": ("depends_on", "organization")}
KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,299}$")  # the slug after product/: a 200-character name plus a -2 still fits
MOST = 50  # the most repositories, agreements or vendors one write names


class ProductFields(Contract):
    kind: str | None = None  # software | service | goods; "" clears
    status: str | None = None  # planned | beta | live | retired; "" clears
    tax_kind: str | None = None  # software_service | service | goods | digital_goods | not_taxed | unknown; "" clears
    owner_teammate: str | None = None  # a person or a bot; "" clears
    repositories: list[str] | None = Field(default=None, max_length=MOST)  # owner/name, each a repository in Tico
    agreements: list[str] | None = Field(default=None, max_length=MOST)  # documents: an id, or a name (made if new)
    depends_on: list[str] | None = Field(default=None, max_length=MOST)  # organizations: an id, or a name (made if new)


class ProductCreate(ProductFields):
    name: str = Field(min_length=1, max_length=200)


class ProductUpdate(ProductFields):
    name: str | None = Field(default=None, min_length=1, max_length=200)


def text(value: object) -> str | None:
    """A single field's value as the answer carries it. A hub that defined the attribute itself before this migration may
    hold another shape; that reads as unset rather than breaking the list."""
    return value if isinstance(value, str) and value else None


def texts(value: object) -> list[str]:
    """A multiple field's values: a list of text, or one text value when the attribute was defined single."""
    items = cast("list[object]", value) if isinstance(value, list) else [value]
    return [item for item in items if isinstance(item, str) and item]


def install(
    app: "FastAPI",
    store: "Store",
    auth: "Auth",
    mutate: "Callable[[Request, Contract, Callable[[sqlite3.Connection], Any]], Any]",
) -> None:
    def read() -> "AbstractContextManager[sqlite3.Connection]":
        return cast("AbstractContextManager[sqlite3.Connection]", store.read())

    def caller(request: Request) -> Any:
        who = request.state.identity
        auth.domain(who)  # pyright: ignore[reportUnknownMemberType]
        return who

    def ours(c: sqlite3.Connection, actor: str) -> list[str]:
        """The ids of our products the actor sees: the product records company/self owns."""
        me = ES.self_id(c)
        if me is None:
            return []
        ids = [str(row["to_entity_id"]) for row in ES.links_from(c, [me], "owns")]
        facts = ES.entity_facts(c, ids)
        return sorted(ES.visible_ids(c, actor, [i for i in ids if facts.get(i, {}).get("type") == TYPE]))

    def files(c: sqlite3.Connection, who: Any, document_ids: list[str]) -> dict[str, dict[str, Any] | None]:
        """The file each agreement is, when the caller may open it ({id, title, page}), as a Contacts page shows it."""
        from backend import files as F

        shown: dict[str, dict[str, Any] | None] = {}
        cache: dict[Any, Any] = {}
        for entity_id, file_id in ES.document_files(c, document_ids).items():
            row = c.execute(
                "SELECT f.*, p.file_id AS page_id FROM bot_files f LEFT JOIN pages p ON p.file_id = f.id "
                "WHERE f.id = ? AND f.state <> 'deleted'",
                (file_id,),
            ).fetchone()
            visible = bool(row) and F.file_visible(c, auth, who, row, cache)  # pyright: ignore[reportUnknownMemberType]
            shown[entity_id] = (
                {"id": row["id"], "title": row["title"], "page": row["page_id"] is not None}
                if row and visible
                else None
            )
        return shown

    def views(c: sqlite3.Connection, who: Any, ids: list[str]) -> list[dict[str, Any]]:
        """Each product as the answers carry it, read in a few queries for the whole list."""
        actor = str(who.actor)
        if not ids:
            return []
        names = ES.entity_names(c, ids)
        values = ES.values_for(c, actor, ids)
        linked: dict[str, dict[str, list[dict[str, Any]]]] = {i: {field: [] for field in LINKS} for i in ids}
        ends: dict[str, list[dict[str, Any]]] = {}
        for field, (link_key, _) in LINKS.items():
            for row in ES.links_from(c, ids, link_key):
                item = {"id": str(row["to_entity_id"]), "link_id": str(row["id"])}
                linked[str(row["from_entity_id"])][field].append(item)
                ends.setdefault(item["id"], []).append(item)
        seen = ES.visible_ids(c, actor, list(ends))
        far = ES.entity_names(c, list(seen))
        agreement_ids = {item["id"] for links in linked.values() for item in links["agreements"]}
        agreement_files = files(c, who, sorted(agreement_ids & seen))
        bots: dict[str, bool] = {}

        def owner(actor: object) -> str | None:
            """The owner as this reader may know it: a bot they cannot see reads as no owner."""
            if not isinstance(actor, str) or not actor:
                return None
            if actor.startswith("bot:"):
                slug = actor.removeprefix("bot:")
                if slug not in bots:
                    bots[slug] = bool(auth.visible_bot(c, who, slug))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                if not bots[slug]:
                    return None
            return actor

        out: list[dict[str, Any]] = []
        for entity_id in ids:
            own = values.get(entity_id, {})
            product: dict[str, Any] = {
                "id": entity_id,
                "key": entity_id.removeprefix(TYPE + "/"),
                "name": names.get(entity_id, ""),
                **{field: text(own.get(field)) for field in CHOICES},
                "owner_teammate": owner(own.get("owner_teammate")),
                "repositories": sorted(texts(own.get("repositories")), key=str.lower),
            }
            for field in LINKS:
                items = [{**item, "name": far[item["id"]]} for item in linked[entity_id][field] if item["id"] in seen]
                if field == "agreements":
                    for item in items:
                        item["file"] = agreement_files.get(item["id"])
                product[field] = sorted(items, key=lambda item: str(item["name"]).lower())
            out.append(product)
        return sorted(out, key=lambda product: (str(product["name"]).lower(), product["id"]))

    def product_id(c: sqlite3.Connection, actor: str, key: str) -> str:
        """The id of the product a path names, when it is ours and the caller sees it; otherwise not found."""
        entity_id = TYPE + "/" + key
        if not KEY.match(key) or entity_id not in ours(c, actor):
            raise Problem("not_found", f"No product {key}", 404)
        return entity_id

    def repository(c: sqlite3.Connection, name: str) -> str:
        """A repository's full name as Tico has it. One Tico does not have is refused: Code links only to real repos."""
        row = c.execute(
            "SELECT full_name FROM repositories WHERE full_name = ? COLLATE NOCASE", (name.strip(),)
        ).fetchone()
        if row is None:
            raise Problem("kind", f"No repository {name.strip()} in Tico; add it under Repositories first", 422)
        return str(row["full_name"])

    def record(c: sqlite3.Connection, actor: str, type_key: str, ref: str) -> str:
        """The document or organization an item names: an id or a name you can see, or a new record of that name."""
        text = ref.strip()
        if not text:
            raise Problem("kind", f"Name the {type_key}", 422)
        found = [row for row in ES.get(c, actor, text) if row["type"] == type_key]
        if len(found) > 1:
            raise Problem(
                "ambiguous", f"{', '.join(str(r['id']) for r in found)} all match {text}; name one by id", 422
            )
        if found:
            return str(found[0]["id"])
        if text.startswith(type_key + "/"):
            raise Problem("not_found", f"No {type_key} {text}", 404)
        made = ES.upsert(c, actor, type_key, match={"name": text}, name=text, source_type=SOURCE, source_id=actor)
        return str(made["id"])

    def apply(c: sqlite3.Connection, who: Any, entity_id: str, body: ProductFields) -> None:
        """Write the fields a body names. A field left out is unchanged; "" clears a single field; a list replaces the set."""
        actor = str(who.actor)
        fields = body.model_fields_set
        values: dict[str, object] = {}
        for field in CHOICES:
            if field in fields and getattr(body, field) is not None:
                values[field] = getattr(body, field)
        if "owner_teammate" in fields and body.owner_teammate is not None:
            values["owner_teammate"] = (
                auth.target(c, who, body.owner_teammate) if body.owner_teammate else ""  # pyright: ignore[reportUnknownMemberType]
            )
        cleared = [field for field, value in values.items() if value == ""]
        for field in cleared:
            ES.clear_value(c, actor, entity_id, field)
            values.pop(field)
        if values:
            ES.upsert(c, actor, TYPE, match={"id": entity_id}, values=values, source_type=SOURCE, source_id=actor)
        if body.repositories is not None:
            wanted = list(dict.fromkeys(repository(c, name) for name in body.repositories))
            have = texts(ES.values_for(c, actor, [entity_id]).get(entity_id, {}).get("repositories"))
            for name in have:
                if name not in wanted:
                    ES.clear_value(c, actor, entity_id, "repositories", name)
            for name in wanted:
                if name not in have:
                    ES.upsert(
                        c,
                        actor,
                        TYPE,
                        match={"id": entity_id},
                        values={"repositories": name},
                        source_type=SOURCE,
                        source_id=actor,
                    )
        for field, (link_key, type_key) in LINKS.items():
            refs = cast("list[str] | None", getattr(body, field))
            if refs is None:
                continue
            wanted_ids = list(dict.fromkeys(record(c, actor, type_key, ref) for ref in refs))
            current = ES.links_from(c, [entity_id], link_key)
            visible = ES.visible_ids(c, actor, [str(row["to_entity_id"]) for row in current])
            for row in current:
                # A link to a record the caller cannot see stays: a write never touches what its writer cannot read.
                if str(row["to_entity_id"]) in visible and str(row["to_entity_id"]) not in wanted_ids:
                    ES.unlink(c, actor, str(row["id"]))
            linked_now = {str(row["to_entity_id"]) for row in current}
            for target in wanted_ids:
                if target not in linked_now:
                    ES.link(c, actor, entity_id, link_key, target, source_type=SOURCE, source_id=actor)

    def one(c: sqlite3.Connection, who: Any, entity_id: str) -> dict[str, Any]:
        return {"product": views(c, who, [entity_id])[0]}

    @app.get("/api/v2/products", **api_v2("Products", "listProducts", "Our products", "ProductList"))
    def list_products(request: Request):
        who = caller(request)
        with read() as c:
            return {"products": views(c, who, ours(c, str(who.actor)))}

    @app.get(
        "/api/v2/products/{key}",
        **api_v2(
            "Products",
            "getProduct",
            "One product",
            "ProductResult",
            parameters={"key": "The product's key: its id after product/"},
        ),
    )
    def get_product(request: Request, key: str):
        who = caller(request)
        with read() as c:
            return one(c, who, product_id(c, str(who.actor), key))

    @app.post("/api/v2/products", **api_v2("Products", "addProduct", "Add a product", "ProductResult"))
    def add_product(request: Request, body: ProductCreate):
        who = caller(request)

        def work(c: sqlite3.Connection):
            actor = str(who.actor)
            me = ES.self_id(c) or ES.ensure_self(c, company_name=None)
            name = body.name.strip()
            existing = [row for row in ES.get(c, actor, name) if row["type"] == TYPE]
            if any(str(row["id"]) in ours(c, actor) for row in existing):
                raise Problem("duplicate", f"There is already a product named {name}", 409)
            made = ES.upsert(c, actor, TYPE, match={"name": name}, name=name, source_type=SOURCE, source_id=actor)
            entity_id = str(made["id"])
            ES.link(c, actor, me, "owns", entity_id, source_type=SOURCE, source_id=actor)
            apply(c, who, entity_id, body)
            return one(c, who, entity_id)

        return mutate(request, body, work)

    @app.post(
        "/api/v2/products/{key}",
        **api_v2(
            "Products",
            "updateProduct",
            "Edit a product",
            "ProductResult",
            description="A field left out is unchanged. An empty string clears kind, status, tax_kind or owner_teammate. "
            "repositories, agreements and depends_on replace the whole set: [] removes every one.",
            parameters={"key": "The product's key: its id after product/"},
        ),
    )
    def update_product(request: Request, key: str, body: ProductUpdate):
        who = caller(request)

        def work(c: sqlite3.Connection):
            actor = str(who.actor)
            entity_id = product_id(c, actor, key)
            if body.name is not None and body.name.strip():
                name = body.name.strip()
                clash = [r for r in ES.get(c, actor, name) if r["type"] == TYPE and str(r["id"]) != entity_id]
                if any(str(row["id"]) in ours(c, actor) for row in clash):
                    raise Problem("duplicate", f"There is already a product named {name}", 409)
                ES.upsert(c, actor, TYPE, match={"id": entity_id}, name=name, source_type=SOURCE, source_id=actor)
            apply(c, who, entity_id, body)
            return one(c, who, entity_id)

        return mutate(request, body, work)
