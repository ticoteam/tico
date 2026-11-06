"""The tool registry and the argument helpers every hub tool module shares (clients/hubtools_*.py).

`tool()` appends each tool to TOOLS when its module is imported, so the facade imports the tool modules in
their file order. Nothing here calls a tool."""

GOAL_STATUSES = ("red", "yellow", "green", "done", "dropped")  # what a person or bot sets by hand
PROPOSAL_KINDS = (
    "goal_wording",
    "goal_kpi",
    "kpi_definition",
    "kpi_target",
    "flag",
    "goal_why",
    "recovery",
    "strategy_draft",
    "brand_draft",
)
TOOLS = []


def _s(description, **extra):
    return {"type": "string", "description": description, **extra}


def tool(name, description, properties, required=(), *, writes=False, local=False):
    """Register one tool; the decorated function is `fn(api, args) -> result`. A `local` tool runs on the
    computer that runs the bot and is served only by the local MCP server (`clients/hubmcp.py`), never by
    the Tico server's own endpoint (backend/mcp.py): it reaches out to the internet, and the server must not."""
    schema = {"type": "object", "properties": dict(properties), "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    if writes:
        schema["properties"]["operation_id"] = _s(
            "Stable id so a retry lands once. "
            + ("This tool requires the id." if "operation_id" in schema.get("required", []) else "Omit for a fresh id.")
        )

    def register(fn):
        TOOLS.append(
            {
                "name": name,
                "description": description,
                "inputSchema": schema,
                "writes": writes,
                "local": local,
                "fn": fn,
            }
        )
        return fn

    return register


def _target(api, value):
    if value in ("me", "self"):
        return api.get("me")["actor"]
    return value


def _key(args, suffix=""):
    op = args.get("operation_id")
    return (op + suffix) if op else None


class _Requester:
    """The same `api`, using the requester's rights, recorded via BotOps."""

    def __init__(self, api, ref=None):
        self.api, self.ref = api, ref

    def call(self, method, path, body=None, key=None, query=None):
        return self.api.call(method, path, body, key, query, delegate=self.ref or True)

    def get(self, path, /, **query):  # positional, as Client.get
        return self.call("GET", path, query=query)

    def post(self, path, body=None, key=None):
        return self.call("POST", path, body if body is not None else {}, key)

    def patch(self, path, body=None, key=None):
        return self.call("PATCH", path, body if body is not None else {}, key)


def _asked(api, args):
    """The Assistant, doing a task action the person asked for in their own Assistant chat: the write cites their
    message (`on_behalf_of`), and the server checks it (backend/assistant.py `instruction`)."""
    if not args.get("on_behalf_of"):
        return api
    return _Requester(api.api if isinstance(api, _Requester) else api, args["on_behalf_of"])
