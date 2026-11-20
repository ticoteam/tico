# pyright: strict
"""The answers of the Products routes (backend/products.py)."""

from typing import Literal, NotRequired, TypedDict

from ._base import answer


@answer
class ProductLinkFile(TypedDict):
    id: str
    title: str
    page: bool


@answer
class ProductLink(TypedDict):
    id: str
    name: str
    link_id: str
    file: NotRequired[ProductLinkFile | None]
    """An agreement's file, when the caller may open it (agreements only)"""


@answer
class ProductRisk(TypedDict):
    id: str
    key: str
    label: str
    via: list[Literal["matter", "agreement"]]
    """How the risk touches the product: a matter about it, or an agreement it is sold under"""


@answer
class Product(TypedDict):
    id: str
    key: str
    name: str
    kind: str | None
    status: str | None
    tax_kind: str | None
    owner_teammate: str | None
    repositories: list[str]
    agreements: list[ProductLink]
    depends_on: list[ProductLink]
    open_matters: int
    """Open matters about it, among the tasks you read"""
    risks: list[ProductRisk]


@answer
class ProductList(TypedDict):
    products: list[Product]


@answer
class ProductResult(TypedDict):
    product: Product
