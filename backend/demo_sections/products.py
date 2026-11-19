# pyright: strict
"""Company › Products: what Acme Lights sells, through the Products API as Ana, the way a bot's `tico products` calls
would. The kit depends on the vendors the Directory section made (directory.py runs first), so the product joins them by
name; the agreements are new Directory documents. Nothing names a real company."""

from typing import Any

PRODUCTS = (
    {
        "name": "Lightshow Kit",
        "kind": "goods",
        "status": "live",
        "tax_kind": "goods",
        "owner_teammate": "human:ana",
        "agreements": ["Retail Supply Agreement"],
        "depends_on": ["Lumen Parts", "Northline Freight"],
    },
    {
        "name": "Lightshow App",
        "kind": "software",
        "status": "beta",
        "tax_kind": "software_service",
        "agreements": ["App Terms of Use"],
    },
)


def seed(b: Any) -> None:  # backend.demo_seed.Builder
    for product in PRODUCTS:
        b.call("POST", "products", product)
