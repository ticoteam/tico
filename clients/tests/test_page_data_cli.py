"""`tico page data` and `--app`: the CLI and its MCP twins send a page app's data rows and declaration as the API takes
them (docs/pages.md#apps)."""

from clients import hubcli, hubtools
from clients.cli_spec import NULL
from clients.commands import _access
from clients.commands.content import _page_fields, pages_data_get, pages_data_list, pages_data_rm, pages_data_set


class Api:
    _tico_is_agent_ops = False

    def __init__(self):
        self.sent = []

    def get(self, path, **query):
        self.sent.append(("GET", path, {k: v for k, v in query.items() if v is not None}))
        return {"rows": [], "next": None}

    def call(self, method, path, body, key=None):
        self.sent.append((method, path, body))
        return {}


def run(api, words, tool):
    return tool(api, vars(hubcli.parser().parse_args(["page", "data", *words])))


def test_page_data_commands_send_each_row_by_collection_and_id():
    api = Api()
    run(api, ["list", "p1", "spend", "--limit", "5"], pages_data_list)
    run(api, ["get", "p1", "spend", "2026-11-24"], pages_data_get)
    run(api, ["set", "p1", "spend", "2026-11-24", '{"meta": 120}'], pages_data_set)
    run(api, ["rm", "p1", "spend", "a b"], pages_data_rm)
    assert api.sent == [
        ("GET", "pages/p1/data", {"collection": "spend", "limit": 5}),
        ("GET", "pages/p1/data/spend/2026-11-24", {}),
        ("PUT", "pages/p1/data/spend/2026-11-24", {"data": {"meta": 120}}),
        ("DELETE", "pages/p1/data/spend/a%20b", None),
    ]
    assert _page_fields({"app": "reads=tasks,store", "title": "Spend"}) == {
        "title": "Spend",
        "app": "reads=tasks,store",
    }
    # a JSON null is a value to send, not a flag left out
    assert hubcli.parser().parse_args(["page", "data", "set", "p1", "spend", "x", "null"]).data is NULL
    tools = (pages_data_list, pages_data_get, pages_data_set, pages_data_rm)
    for name, fn in zip(
        ("tico_page_data_list", "tico_page_data_get", "tico_page_data_set", "tico_page_data_rm"), tools
    ):
        assert name in _access.AUDIENCE
        assert hubtools.BY_NAME[name]["fn"] is fn  # the tool runs its own handler, never a helper beside it
