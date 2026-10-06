from clients import hubtools


class _Api:
    """One route answers `hub_health_check` for everyone: the server picks what the caller gets."""
    def __init__(self):
        self.calls = []

    def get(self, path, **query):
        self.calls.append(path)
        if path == "me":
            return {"actor": "human:ana"}
        if path == "health/issues":
            return {"issues": []}
        raise AssertionError(path)


class _Groups:
    """The group tools: a person's own token writes to the routes itself; a bot (BotOps) is the requester."""
    def __init__(self, actor="human:ana"):
        self.actor, self.calls = actor, []

    def get(self, path, **query):
        self.calls.append(("GET", path, None))
        return {"actor": self.actor, "role": "bot" if self.actor.startswith("bot:") else "human"} if path == "me" else [{"id": "seo"}]

    def post(self, path, body=None, key=None):
        self.calls.append(("POST", path, body))
        return {"id": "seo"}

    def patch(self, path, body=None, key=None):
        self.calls.append(("PATCH", path, body))
        return {"id": "seo"}

    def call(self, method, path, body=None, key=None, query=None, delegate=False):
        self.calls.append((method, path, body, delegate))
        return {"id": "seo"}


def test_group_tools_list_create_and_update_and_botops_acts_as_the_requester():
    fn = hubtools.BY_NAME
    api = _Groups()
    assert fn["hub_group_list"]["fn"](api, {}) == [{"id": "seo"}]
    fn["hub_group_update"]["fn"](api, {"name": "SEO", "parent": "marketing", "add_bots": ["seo"]})
    fn["hub_group_update"]["fn"](api, {"group": "seo", "parent": "", "remove_humans": ["cara"]})
    assert [call for call in api.calls if call[1] != "me"][1:] == [
        ("POST", "groups", {"name": "SEO", "add": {"people": [], "bots": ["seo"]}, "parent": "marketing"}),
        ("PATCH", "groups/seo", {"add": {"people": [], "bots": []}, "remove": {"people": ["cara"], "bots": []}, "parent": ""})]
    botops = _Groups("bot:botops")
    fn["hub_group_update"]["fn"](botops, {"group": "seo", "name": "Search"})
    assert botops.calls[-1][:2] == ("PATCH", "groups/seo") and botops.calls[-1][3] is True
    try:
        fn["hub_group_update"]["fn"](api, {})
    except ValueError as exc:
        assert "Name the new group" in str(exc)
    assert "hub_group_update" in hubtools.AUDIENCE and "assistant" not in hubtools.offered_to(fn["hub_group_update"])


def test_tool_add_and_remove_are_the_requesters_when_botops_calls_them():
    fn = hubtools.BY_NAME
    entry = {"bot": "inbox-manager", "service": "gmail", "can": "read,draft,send", "identity": "a@acme.example"}
    person, botops = _Groups(), _Groups("bot:botops")
    fn["hub_tool_add"]["fn"](person, entry)
    fn["hub_tool_remove"]["fn"](person, {"bot": "inbox-manager", "id": "gmail"})
    assert [c[:2] for c in person.calls if c[1] != "me"] == [("POST", "bots/inbox-manager/tools"),
                                                            ("POST", "bots/inbox-manager/tools/gmail/delete")]
    fn["hub_tool_add"]["fn"](botops, entry)
    fn["hub_tool_remove"]["fn"](botops, {"bot": "inbox-manager", "id": "gmail"})
    assert [c[:2] + (c[3],) for c in botops.calls if c[1] != "me"] == [
        ("POST", "bots/inbox-manager/tools", True), ("POST", "bots/inbox-manager/tools/gmail/delete", True)]
    assert "botops" in hubtools.offered_to(fn["hub_tool_add"]) and "bot" not in hubtools.offered_to(fn["hub_tool_add"])


def test_tool_update_changes_only_what_is_sent_and_is_the_requesters_when_botops_calls_it():
    fn = hubtools.BY_NAME
    person, botops = _Groups(), _Groups("bot:botops")
    args = {"bot": "inbox-manager", "id": "gmail", "can": "read, draft,send", "scope": ["mailbox=a@acme.example", "sites="],
            "note": ""}
    fn["hub_tool_update"]["fn"](person, args)
    assert [c for c in person.calls if c[1] != "me"] == [("POST", "bots/inbox-manager/tools/gmail/update", {
        "can": ["read", "draft", "send"], "scope": {"mailbox": "a@acme.example", "sites": ""}, "note": ""})]
    fn["hub_tool_update"]["fn"](botops, {"bot": "inbox-manager", "id": "gmail", "note": "sends on Fridays"})
    assert [c for c in botops.calls if c[1] != "me"] == [
        ("POST", "bots/inbox-manager/tools/gmail/update", {"note": "sends on Fridays"}, True)]
    try:
        fn["hub_tool_update"]["fn"](person, {"bot": "inbox-manager", "id": "gmail"})
        raise AssertionError("an update that changes nothing is refused")
    except ValueError as exc:
        assert "Say what changes" in str(exc)
    assert "botops" in hubtools.offered_to(fn["hub_tool_update"]) and "bot" not in hubtools.offered_to(fn["hub_tool_update"])


def test_task_list_asks_for_the_callers_own_requests_with_requester_me():
    asked = []

    class Api:
        def get(self, path, **query):
            asked.append((path, query))
            return {"actor": "bot:botops"} if path == "me" else {"tasks": [{"id": "t1"}]}

    assert hubtools.BY_NAME["hub_task_list"]["fn"](Api(), {"requester": "me", "status": ["open", "waiting"]}) == [{"id": "t1"}]
    assert asked[-1] == ("tasks", {"owner": None, "requester": "bot:botops", "status": "open,waiting", "lane": None, "label": None})

