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
    assert "hub_group_update" in hubtools.AUDIENCE


def test_the_strategy_bot_makes_an_asked_for_org_change_citing_the_persons_words():
    fn = hubtools.BY_NAME
    for name in ("hub_group_update", "hub_group_delete", "hub_org_reports_to"):
        assert "assistant" in hubtools.offered_to(fn[name]) and "bot" not in hubtools.offered_to(fn[name])
    api = _Groups("human:ana")
    fn["hub_group_update"]["fn"](api, {"group": "seo", "lead": "bot:writer", "on_behalf_of": "turn"})
    fn["hub_group_delete"]["fn"](api, {"group": "seo", "on_behalf_of": "turn"})
    fn["hub_org_reports_to"]["fn"](api, {"teammate": "human:cara", "reports_to": "human:ana", "on_behalf_of": "turn"})
    assert api.calls[-3:] == [
        ("PATCH", "groups/seo", {"add": {"people": [], "bots": []}, "remove": {"people": [], "bots": []}, "lead": "bot:writer"}, "turn"),
        ("DELETE", "groups/seo", None, "turn"), ("POST", "humans/cara", {"reports_to": "ana"}, "turn")]


def test_reports_to_for_a_bot_sends_its_revision():
    class _Bots(_Groups):
        def get(self, path, **query):
            return [{"slug": "writer", "revision": 7}] if path == "bots" else super().get(path, **query)
    api = _Bots()
    hubtools.BY_NAME["hub_org_reports_to"]["fn"](api, {"teammate": "bot:writer", "reports_to": "cmo"})
    assert api.calls[-1] == ("POST", "bots/writer/definition", {"reports_to": "cmo", "expected_revision": 7})
    try:
        hubtools.BY_NAME["hub_org_reports_to"]["fn"](api, {"teammate": "writer", "reports_to": "cmo"})
    except ValueError as exc:
        assert "bot:<slug> or human:<id>" in str(exc)
    else:
        raise AssertionError("an unprefixed teammate is refused")


def test_org_order_reads_and_sets_and_botops_sets_as_the_requester():
    fn = hubtools.BY_NAME["hub_org_order"]["fn"]
    api = _Groups()
    fn(api, {"manager": "engineering-lead"})
    fn(api, {"manager": "human:ana", "set": ["bot:cmo", "ben"]})
    assert [call for call in api.calls if call[1] != "me"] == [
        ("GET", "org/order/engineering-lead", None), ("PUT", "org/order/human:ana", {"reports": ["bot:cmo", "ben"]}, False)]
    botops = _Groups("bot:botops")
    fn(botops, {"manager": "cmo", "set": ["writer"]})
    assert botops.calls[-1][:2] == ("PUT", "org/order/cmo") and botops.calls[-1][3] is True


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



class _Sync:
    """external/sync on a server with or without `inbox`: one from before it refuses the field."""
    def __init__(self, knows_inbox):
        self.knows_inbox, self.bodies = knows_inbox, []

    def post(self, path, body=None, key=None):
        assert path == "external/sync"
        self.bodies.append(body)
        if "inbox" in body and not self.knows_inbox:
            raise hubtools.APIError("validation", "body.inbox: Extra inputs are not permitted", 422)
        return {"bots": []}


def test_only_the_new_sync_tool_takes_the_inbox_and_an_older_server_still_syncs():
    bots = [{"id": "g1", "name": "Designer"}]
    for knows in (True, False):
        api = _Sync(knows)
        assert hubtools.BY_NAME["hub_external_sync"]["fn"](api, {"provider": "grokbot", "bots": bots}) == {"bots": []}
        assert [b.get("inbox") for b in api.bodies] == ([True] if knows else [True, None])
    api = _Sync(True)
    hubtools.BY_NAME["hub_grokbot_sync"]["fn"](api, {"bots": bots})     # routines from before the inbox ignore it
    assert api.bodies == [{"provider": "grokbot", "bots": bots}]
