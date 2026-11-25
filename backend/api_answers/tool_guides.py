# pyright: strict
"""The answers of the Tools registry routes (backend/tool_guides.py). Never a credential value: access is a level, a
way in and variable names."""

from typing import Literal, TypedDict

from ._base import DateTime, answer

Origin = Literal["vendor", "built"]


@answer
class ToolGuideRow(TypedDict):
    slug: str
    """The vendor's id (tool_services.vendor_of) for a vendor's tool, the company's own name for a built one"""
    name: str
    origin: Origin
    category: str
    summary: str
    connections: int
    """The Tools rows of this vendor you may see (API, MCP and website tools, connections)"""
    used_by: int
    """Agents that list it or used it in the last 30 days"""
    last_used_at: DateTime | None
    has_guide: bool
    """False: "No guide yet", a vendor in Tools nobody has written words on"""


@answer
class ToolGuideList(TypedDict):
    tool_guides: list[ToolGuideRow]
    next: str | None
    """The cursor of the next page, null on the last"""


@answer
class ToolGuideNeed(TypedDict):
    tool: str
    level: Literal["read", "write"]


@answer
class ToolGuide(TypedDict):
    slug: str
    name: str
    origin: Origin
    category: str
    summary: str
    instructions: str
    """How to use it: the company's words over the shipped guide's"""
    guidance: str
    """The company's own notes: when to use it, its accounts, what to ask first"""
    commands: list[str]
    """The programs or MCP servers that are this tool in a run (gh, psql)"""
    source: str
    """A built tool's repository and path"""
    needs: list[ToolGuideNeed]
    """A built tool's own tools, each at the level it needs"""
    owner: str | None
    has_guide: bool
    shipped: bool
    """Tico ships a guide for it (templates/tool-guides/)"""
    created_by: str | None
    updated_by: str | None
    updated_at: DateTime | None


@answer
class ToolGuideAccess(TypedDict):
    id: str
    """The Tools row (api:<id>, website:<id>, github, ...)"""
    name: str
    kind: str
    level: Literal["none", "read", "write"] | None
    """Yours on it; null where Tools keeps no level (a connection)"""
    arrives: Literal["proxy", "env", "login"] | None
    """How a run reaches it: through Tico's proxy, a variable on the computer, or a website sign-in"""
    env: list[str]
    """Its keys' variable names, never a value"""
    url_env: str | None
    """A proxied tool's address variable (TICO_TOOL_<ID>_URL)"""
    request: str | None
    """The command that asks for Read when an agent has none"""


@answer
class ToolGuideUser(TypedDict):
    id: str
    name: str
    listed: bool
    note: str
    uses: int
    last_used_at: DateTime | None


@answer
class ToolGuideLearning(TypedDict):
    id: str
    actor: str
    text: str
    created_at: DateTime


@answer
class ToolGuideDetail(ToolGuide):
    access: list[ToolGuideAccess]
    used_by: list[ToolGuideUser]
    """Agents that list it, and agents seen using it in the last 30 days"""
    learnings: list[ToolGuideLearning]
    """What agents learned using this vendor's tools (the integration page's learnings)"""


@answer
class ToolGuideArchived(TypedDict):
    slug: str
    archived: bool


@answer
class BotToolGuideRow(TypedDict):
    slug: str
    name: str
    origin: Origin
    category: str
    note: str
    """What the agent uses it for, in its own words"""
    access: Literal["proxy", "key", "none"]
    uses: int
    """Calls through Tico's proxy and keys read, last 30 days"""
    last_used_at: DateTime | None
    built: bool
    """The agent built it"""
    listed: bool
    """On its list, not only granted or used"""


@answer
class BotToolGuideList(TypedDict):
    bot: str
    tool_guides: list[BotToolGuideRow]
    next: str | None


@answer
class BotToolGuideListed(TypedDict):
    bot: str
    slug: str
    note: str
    listed: bool
