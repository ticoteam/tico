# pyright: strict
"""The answers of the Products routes (backend/products.py; backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

SCHEMAS.update(
    {
        "ProductLink": obj(
            {
                "id": "s",
                "name": "s",
                "link_id": "s",
                "file": {
                    "anyOf": [obj({"id": "s", "title": "s", "page": "b"}), {"type": "null"}],
                    "description": "An agreement's file, when the caller may open it (agreements only)",
                },
            },
            required=("id", "name", "link_id"),
        ),
        "ProductRisk": obj(
            {
                "id": "s",
                "key": "s",
                "label": "s",
                "via": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["matter", "agreement"]},
                    "description": "How the risk touches the product: a matter about it, or an agreement it is sold under",
                },
            }
        ),
        "Product": obj(
            {
                "id": "s",
                "key": "s",
                "name": "s",
                "kind": "n",
                "status": "n",
                "tax_kind": "n",
                "owner_teammate": "n",
                "repositories": items("s"),
                "agreements": items(ref("ProductLink")),
                "depends_on": items(ref("ProductLink")),
                "open_matters": {"type": "integer", "description": "Open matters about it, among the tasks you read"},
                "risks": items(ref("ProductRisk")),
            }
        ),
        "ProductList": obj({"products": items(ref("Product"))}),
        "ProductResult": obj({"product": ref("Product")}),
    }
)
