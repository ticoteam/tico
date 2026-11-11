"""Who asks: the kinds of caller, the Requester a tool calls the API through, and acting for a person.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

from clients.commands._schemas import _s


# ----------------------------------------------------------------------------- bots and people, for BotOps
# The server applies the requester's rights to all BotOps calls (docs/permissions.md).
def _for_person(api):
    """`{"on_behalf_of": "turn"}` when a bot (BotOps) is calling: the requester's rights, not the bot's."""
    return {"on_behalf_of": "turn"} if kind_of(api.get("me")) == "botops" else {}


# ----------------------------------------------------------------------------- BotOps: what the app can do, as the requester
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


def _as_person(api, ref=None):
    """BotOps uses the requester; other callers keep their own rights. `ref` names the request BotOps follows
    through on when something else started this run: the open task the person asked for, or their message id."""
    if isinstance(api, _Requester):
        return _Requester(api.api, ref) if ref else api
    if "_tico_is_botops" not in api.__dict__:
        api.__dict__["_tico_is_botops"] = kind_of(api.get("me")) == "botops"
    return _Requester(api, ref) if api.__dict__["_tico_is_botops"] else api


def _asked(api, args):
    """The Assistant, doing a task action the person asked for in their own Assistant chat: the write cites their
    message (`on_behalf_of`), and the server checks it (backend/assistant.py `instruction`)."""
    if not args.get("on_behalf_of"):
        return api
    return _Requester(api.api if isinstance(api, _Requester) else api, args["on_behalf_of"])


ON_BEHALF_OF = _s(
    "BotOps, following a person's request in a run something else started (a daily update, "
    "a notice): the id of the open task they asked you for. Default: the request that started this run"
)


# ----------------------------------------------------------------------------- who sees which tool
# `tools/list` offers a caller the tools it can use, so a bot does not carry BotOps's or the Assistant's tools in its
# context. Every line below repeats a check the server already makes; hiding a tool is only for reading, and
# `tools/call` refuses a tool that is not offered to the caller. Rules the server enforces on the routes stay where they are.
PEOPLE = ("owner", "admin", "member")  # a human, however it connects (a personal token is that human)


BOTS = ("bot", "botops", "agent")  # a bot's own runs; `agent` is an external agent run by a bot (a Hermes profile)


KINDS = PEOPLE + BOTS + ("assistant",)  # the Assistant: a human's private room, which may only propose what matters


NOT_ASSISTANT = PEOPLE + BOTS


BOTOPS = ("botops",)


# `_as_person` tools: a human's own rights, or BotOps's as the human who asked it (the server refuses any other bot).
REQUESTER = PEOPLE + BOTOPS


REQUESTER_READ = REQUESTER + ("assistant",)


HUMANS_AND_ASSISTANT = PEOPLE + ("assistant",)  # views.human_only: bots are refused


def kind_of(me):
    """The caller's kind from what the server says about it (`GET /me`: `kind`); for a server that does not say yet, from
    its role and actor."""
    if me.get("kind") in KINDS:
        return me["kind"]
    role, actor = me.get("role"), str(me.get("actor") or "")
    if role == "owner":
        return "owner"
    if role == "human":
        return "member"
    if role == "bot":
        return (
            "agent"
            if me.get("agent")
            else "botops"
            if actor == "bot:botops"
            else "counsel"
            if actor == "bot:" + COUNSEL_BOT
            else "bot"
        )
    return None


# Counsel (backend/counsel.py) has no credentials and sends nothing, so it is offered only the tools it needs: its rules,
# the document it reads, authorized physical mail, and its own task. A web search or fetch, a message to anyone, and every other tool are not its
# to call. The server still decides every call; this keeps those tools out of its list and refuses them if it asks.
COUNSEL = "counsel"  # its kind of caller, the role offered_to names


COUNSEL_BOT = "tico-counsel"  # its slug (backend/hubdb.py COUNSEL)
