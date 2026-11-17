"""The `tico update`, `changelog`, `group`, `team`, `slack`, `credential` and `general-manager` commands built from
specs (clients/commands/assistant.py, admin.py, tools.py, task_work.py): each sends what the hand parser it replaced
sent (clients/hubcli.py and clients/remotecli.py before these specs)."""

from clients.tests.helpers.cli_fixtures import run_line


def test_update_mark_read_sends_ids_all_and_read_as_before(monkeypatch):
    result = run_line(["update", "mark-read"], monkeypatch)
    assert result["tools"] == [["tico_update_mark_read", {"all": False, "ids": [], "operation_id": None, "read": True}]]
    unread = run_line(["update", "mark-read", "u1", "--unread"], monkeypatch)
    assert unread["http"][-1] == ["POST", "updates/read", {"ids": ["u1"], "all": False, "read": False}]


def test_update_settings_sends_on_and_off_as_typed(monkeypatch):
    result = run_line(["update", "settings", "builder", "--weekly", "off"], monkeypatch)
    assert result["tools"] == [["tico_update_settings", {"bot": "builder", "operation_id": None, "weekly": "off"}]]
    assert result["http"][-1] == ["POST", "bots/builder/updates", {"weekly": False}]


def test_update_redo_takes_day_and_bots_as_flags(monkeypatch):
    result = run_line(["update", "redo", "--day", "2026-11-14", "--bot", "a", "--with-managers"], monkeypatch)
    name, sent = result["tools"][0]
    assert name == "tico_update_redo"
    assert sent == {"bots": ["a"], "day": "2026-11-14", "managers": True, "operation_id": None}
    assert run_line(["update", "redo"], monkeypatch)["exit"] != 0


def test_changelog_list_defaults_to_product_changes(monkeypatch):
    result = run_line(["changelog", "list"], monkeypatch)
    assert result["tools"] == [["tico_changelog_list", {"kind": "product", "operation_id": None, "unread": False}]]


def test_group_update_creates_without_a_group(monkeypatch):
    result = run_line(["group", "update", "--name", "Ops", "--add-bot", "b1", "--add-bot", "b2"], monkeypatch)
    assert result["tools"][0] == [
        "tico_group_update",
        {"add_bots": ["b1", "b2"], "name": "Ops", "operation_id": None},
    ]


def test_slack_channel_add_post_flags_set_one_property(monkeypatch):
    result = run_line(["slack", "channel", "add", "#launches", "--no-post", "--post"], monkeypatch)
    assert result["tools"][0] == [
        "tico_slack_channel_add",
        {"channel": "#launches", "operation_id": None, "post": True},
    ]
    left_out = run_line(["slack", "channel", "add", "#launches"], monkeypatch)["tools"][0][1]
    assert "post" not in left_out


def test_credential_grant_and_revoke_take_their_flags(monkeypatch):
    grant = run_line(["credential", "grant", "JIRA", "--to", "support"], monkeypatch)["tools"][0]
    assert grant == ["tico_credential_grant", {"credential": "JIRA", "operation_id": None, "to_bot": "support"}]
    assert run_line(["credential", "revoke", "JIRA"], monkeypatch)["exit"] != 0  # --from is required


def test_credential_set_stays_hand_built_and_reads_standard_input(monkeypatch):
    result = run_line(["credential", "set", "JIRA", "--for-bot", "support"], monkeypatch)
    name, sent = result["tools"][0]
    assert name == "tico_credential_set" and sent["value"] == "Plain words, no hype."


def test_general_manager_paths_send_what_the_hand_branch_sent(monkeypatch):
    from clients.commands import task_work

    assert task_work._no_key({"operation_id": None}) == {}
    status = run_line(["general-manager", "status"], monkeypatch)
    assert status["tools"] == [["tico_general_manager", {}]]
    on = run_line(["general-manager", "on"], monkeypatch)
    assert on["tools"] == [["tico_general_manager_set", {"on": True, "operation_id": None}]]
    schedule = run_line(["general-manager", "schedule", "daily", "--at", "08:00"], monkeypatch)
    assert schedule["tools"][0][1] == {"at": "08:00", "operation_id": None, "preset": "daily"}
    bot = run_line(["general-manager", "bot", "off"], monkeypatch)
    assert bot["tools"] == [["tico_general_manager_set", {"bot": "off", "operation_id": None}]]
