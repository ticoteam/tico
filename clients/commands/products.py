"""Products (backend/products.py): `tico products`. clients/commands/__init__.py says how the table is put together.

A product is a Contacts Product record our company owns; these tools read and write its eight fields. Reads are offered
to every kind of caller; writes to people and bots, not the Assistant (the default for a writing tool)."""

from clients.cli_spec import Arg, Command, Group
from clients.commands._registry import tool
from clients.commands._schemas import _key, _s

KINDS = ("software", "service", "goods")
STATUSES = ("planned", "beta", "live", "retired")
TAX_KINDS = ("software_service", "service", "goods", "digital_goods", "not_taxed", "unknown")
KEY = _s("The product's key: its id after product/")
FIELDS = {
    "kind": _s("What it is; '' clears", enum=["", *KINDS]),
    "status": _s("Where it is in its life; '' clears", enum=["", *STATUSES]),
    "tax_kind": _s("How sales tax treats it; '' clears", enum=["", *TAX_KINDS]),
    "owner_teammate": _s("The person or bot who owns it; '' clears"),
    "repositories": {"type": "array", "items": {"type": "string"}, "description": "Repos in Tico (owner/name)"},
    "agreements": {"type": "array", "items": {"type": "string"}, "description": "Agreements it is sold under"},
    "depends_on": {"type": "array", "items": {"type": "string"}, "description": "Vendors it depends on"},
}
LISTS = ("repositories", "agreements", "depends_on")


def _body(args):
    """The fields the caller gave. A list replaces the set; a list of only '' (the CLI's `--repo ''`) clears it."""
    body = {k: args[k] for k in FIELDS if k not in LISTS and args.get(k) is not None}
    for k in LISTS:
        if args.get(k) is not None:
            body[k] = [v for v in args[k] if v]
    return body


@tool("tico_products_list", "Our products, each with its eight fields.", {})
def products_list(api, args):
    return api.get("products")


@tool("tico_products_show", "One product.", {"key": KEY}, required=("key",))
def products_show(api, args):
    return api.get("products/" + args["key"])


@tool(
    "tico_products_add",
    "Add a product. Agreements and vendors are ids or names; a new name makes the record.",
    {"name": _s("The product's name"), **FIELDS},
    required=("name",),
    writes=True,
)
def products_add(api, args):
    return api.post("products", {"name": args["name"], **_body(args)}, key=_key(args))


@tool(
    "tico_products_update",
    "Edit a product. A field left out is unchanged; a list replaces the whole set.",
    {"key": KEY, "name": _s("A new name"), **FIELDS},
    required=("key",),
    writes=True,
)
def products_update(api, args):
    body = _body(args)
    if args.get("name"):
        body["name"] = args["name"]
    return api.post("products/" + args["key"], body, key=_key(args))


_FLAGS = (
    Arg("owner_teammate", flag="--owner"),
    Arg("tax_kind", flag="--tax-kind"),
    Arg("repositories", flag="--repo"),
    Arg("agreements", flag="--agreement"),
    Arg("depends_on", flag="--depends-on"),
)
PRODUCTS = (
    Group("products", "products: our products, their agreements, code and vendors"),
    Command("products list", "our products", example="tico products list"),
    Command("products show", "one product", example="tico products show tidy-web"),
    Command(
        "products add",
        "add a product",
        args=_FLAGS,
        example="tico products add 'TIDY Web' --kind software --status live --repo tidyapp/web --depends-on Stripe",
    ),
    Command(
        "products update",
        "edit a product; --repo, --agreement and --depends-on replace the set ('' clears)",
        args=(*_FLAGS, Arg("name", flag="--name")),
        example="tico products update tidy-web --agreement 'Customer Agreement'",
    ),
)
