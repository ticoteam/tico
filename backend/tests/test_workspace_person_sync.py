"""Whole Workspace sync is independent of bot access and of having a message bot."""

import pytest

from backend import google_workspace as workspace, mailbox_access, routines
from backend.store import H, encode, readiness_document
from backend.tests.test_api import api, as_member, get, post, ready, runner, setup_attempt  # noqa: F401
from backend.tests.test_assistant import assistant_turn
from backend.tests.test_connectors import mail_message
from backend.tests.test_gmail_live import gmail_http  # noqa: F401
from backend.tests.test_google_workspace import personal
from backend.tests.test_google_workspace_authz import (  # noqa: F401
    BOX, PERSONAL, health, place, publish, revoke, token, workspace_key,
)
from backend.tests.test_mail_api import ORG, seed_org
from backend.tests.test_mail_targets import declare


@pytest.fixture
def known_person(api, workspace_key):
    seed_org(api)
    with api.app.state.store.transaction() as c:
        return mailbox_access.ensure_mailbox(c, PERSONAL)


def contact(api, machine, *, isolated=True):
    with api.app.state.store.read() as c:
        report = c.execute("SELECT readiness FROM runner_states WHERE runner_id=?", (machine["runner_id"],)).fetchone()
        readiness = readiness_document(report[0])
    readiness.update(schema_version=1, isolated=isolated)
    return post(api, "runners/heartbeat", {"version": "test", "platform": "test",
                "readiness": readiness}, machine["token"])


def computers(api, count=2, *, operator="ana", isolated=True, shared=False):
    made = [runner(api, operator=operator, label=f"Computer {n}") for n in range(count)]
    for machine in made:
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE runners SET accepts_member_bots=? WHERE id=?", (int(shared), machine["runner_id"]))
        contact(api, machine, isolated=isolated)
    return sorted(made, key=lambda m: m["runner_id"])


def owners(api, machines, address=PERSONAL):
    with api.app.state.store.read() as c:
        return [m["runner_id"] for m in machines if address in workspace.owned_mailboxes(c, m["runner_id"])]


def offline(api, machine):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runner_states SET last_seen_at=? WHERE runner_id=?",
                  (H.shift(H.now(), seconds=-120), machine["runner_id"]))


def addresses(api, machine):
    return {b["address"] for b in get(api, "connectors/mail/targets", machine["token"])["mailboxes"]}


def test_botless_person_gets_one_stable_online_server_and_fails_over(api, known_person):
    first, second, third = computers(api, 3)
    machines = [first, second, third]
    assert owners(api, machines) == [first["runner_id"]]
    contact(api, third)                                        # contact order does not change the owner
    assert owners(api, machines) == [first["runner_id"]]
    offline(api, first)
    assert owners(api, machines) == [second["runner_id"]]
    revoke(api, second)
    assert owners(api, machines) == [third["runner_id"]]
    offline(api, third)
    assert owners(api, machines) == []


def test_other_peoples_macs_never_take_a_persons_mailbox(api, known_person):
    machines = [computers(api, 1, operator=person, isolated=False, shared=True)[0]
                for person in ("ana", "ben", "lena")]
    ready(api, machines[0], [])                               # unknown isolation is not isolated either
    # Even an owner's, an admin's or an open-to-members Mac is not a company server.
    assert owners(api, machines) == []
    assert addresses(api, machines[0]) == set()
    for machine in machines[1:]:
        get(api, "connectors/mail/targets", machine["token"], expected=403)
    for machine in machines:
        assert get(api, "runners/connectors", machine["token"])["connectors"] == []
        token(api, machine["token"], PERSONAL, expected=403)
        publish(api, machine["token"], PERSONAL, expected=403)


def test_isolated_admin_server_precedes_own_mac_and_falls_back_when_offline(api, known_person):
    own, server = computers(api, operator="ben")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET owner_id='cara' WHERE id=?", (own["runner_id"],))
    contact(api, own, isolated=False)
    # The person's Mac has the lower id; server priority still wins.
    assert owners(api, [own, server]) == [server["runner_id"]]
    offline(api, server)
    assert owners(api, [own, server]) == [own["runner_id"]]
    contact(api, server)
    assert owners(api, [own, server]) == [server["runner_id"]]
    as_member(api, "ben@acme.example")
    assert owners(api, [own, server]) == [own["runner_id"]]


def test_own_lowest_online_computer_is_used_without_a_server(api, known_person):
    first, second = computers(api, operator="cara", isolated=False)
    assert owners(api, [first, second]) == [first["runner_id"]]
    offline(api, first)
    assert owners(api, [first, second]) == [second["runner_id"]]
    revoke(api, second)
    assert owners(api, [first, second]) == []


def test_a_members_isolated_computer_never_syncs_others_mail_even_when_shared(api, known_person):
    [machine] = computers(api, 1, operator="lena")
    assert owners(api, [machine]) == []
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET accepts_member_bots=1 WHERE id=?", (machine["runner_id"],))
    assert owners(api, [machine]) == []


def test_an_assistant_on_a_members_computer_does_not_take_others_mail(api, known_person):
    [member] = computers(api, 1, operator="lena")
    [server] = computers(api, 1)
    api.app.state.store.settings.assistant_bot = "ops"
    place(api, member, "ops")
    assert owners(api, [member, server]) == [server["runner_id"]]


def test_no_eligible_computer_means_no_sync_and_health_explains_it(api, known_person):
    assert owners(api, []) == []
    issues = get(api, "operations")["issues"]
    assert any(i["kind"] == "google_workspace" and PERSONAL in i["title"] for i in issues)
    [machine] = computers(api, 1, operator="cara", isolated=False)
    assert addresses(api, machine) == {PERSONAL}
    assert not any(i["kind"] == "google_workspace" for i in get(api, "operations")["issues"])


def test_the_configured_assistants_host_precedes_the_lowest_server(api, known_person):
    first, assistant = computers(api)
    api.app.state.store.settings.assistant_bot = "ops"
    place(api, assistant, "ops")
    assert owners(api, [first, assistant]) == [assistant["runner_id"]]
    offline(api, assistant)
    assert owners(api, [first, assistant]) == [first["runner_id"]]


def test_message_bot_placement_still_precedes_the_assistant_and_fallback(api, known_person):
    first, host = computers(api)
    declare(api, BOX)
    with api.app.state.store.transaction() as c:
        mailbox_access.ensure_mailbox(c, BOX)
    place(api, first, "coo")
    place(api, host, "inbox")
    assert owners(api, [first, host], BOX) == [host["runner_id"]]
    # Existing message-bot placement remains authoritative between heartbeats.
    offline(api, host)
    assert owners(api, [first, host], BOX) == [host["runner_id"]]
    revoke(api, host)
    assert owners(api, [first, host], BOX) == [first["runner_id"]]


def test_stopped_awaiting_foreign_and_other_provider_mailboxes_are_not_fallback_targets(api, known_person):
    [machine] = computers(api, 1)
    with api.app.state.store.transaction() as c:
        mailbox_access.ensure_mailbox(c, "waiting@acme.example")
        mailbox_access.ensure_mailbox(c, "shared@elsewhere.example", kind="shared")
        mailbox_access.ensure_mailbox(c, "other@acme.example", kind="shared", provider="other")
        c.execute("UPDATE mailboxes SET status='stopped' WHERE id=?", (known_person["id"],))
    assert owners(api, [machine]) == []
    assert addresses(api, machine) == set()
    token(api, machine["token"], PERSONAL, expected=403)


def test_a_personal_google_grant_keeps_its_holder_even_when_offline(api, known_person):
    first, holder = computers(api)
    personal(api, "cara", PERSONAL, holder=holder["runner_id"])
    place(api, first, "coo")
    offline(api, holder)
    assert owners(api, [first, holder]) == [holder["runner_id"]]
    assert token(api, first["token"], PERSONAL, expected=403)["error"]["kind"] == "personal_grant"


def test_targets_connector_principal_and_token_belong_to_the_chosen_computer_only(api, known_person):
    first, other = computers(api)
    assert addresses(api, first) == {PERSONAL}
    assert addresses(api, other) == set()
    with api.app.state.store.read() as c:
        mine = mailbox_access.connector_for(c, first["runner_id"], True)
        theirs = mailbox_access.connector_for(c, other["runner_id"], True)
        assert mailbox_access.level(c, mine, PERSONAL) == "read"
        assert mailbox_access.level(c, theirs, PERSONAL) == "none"
    assert get(api, "runners/connectors", first["token"])["connectors"] == ["mail", "calendar"]
    for service in ("gmail", "calendar"):
        assert token(api, first["token"], PERSONAL, service)["access_token"] == "ya29.workspace"
        token(api, other["token"], PERSONAL, service, expected=403)
    offline(api, first)
    assert addresses(api, other) == {PERSONAL}
    token(api, first["token"], PERSONAL, expected=403)
    assert token(api, other["token"], PERSONAL)["access_token"] == "ya29.workspace"


def test_a_non_publisher_computer_can_sync_a_botless_workspace_mailbox(api, known_person):
    [machine] = computers(api, 1, operator="cara", isolated=False)
    assert addresses(api, machine) == {PERSONAL}
    assert token(api, machine["token"], PERSONAL)["access_token"] == "ya29.workspace"
    publish(api, machine["token"], PERSONAL)


def test_publish_claim_report_and_calendar_follow_the_personal_mailbox_owner(api, known_person):
    chosen, other = computers(api)
    publish(api, chosen["token"], PERSONAL)
    publish(api, other["token"], PERSONAL, expected=403)
    assert post(api, "automations/mail-actions/claim", {"mailbox": PERSONAL}, chosen["token"])["actions"] == []
    post(api, "automations/mail-actions/claim", {"mailbox": PERSONAL}, other["token"], expected=403)
    health(api, chosen["token"], ok=[PERSONAL])
    health(api, other["token"], ok=[PERSONAL], expected=403)
    targets = get(api, "connectors/calendar/targets", chosen["token"])["people"]
    assert targets == [{"id": "cara", "email": PERSONAL}]
    snapshot = {"snapshots": [{"email": PERSONAL, "events": []}]}
    post(api, "connectors/calendar/snapshots", snapshot, chosen["token"])
    post(api, "connectors/calendar/snapshots", snapshot, other["token"], expected=403)
    assert get(api, "calendar/appointments?calendar=" + PERSONAL, "cara-test")["updated_at"]
    offline(api, chosen)
    publish(api, other["token"], PERSONAL)
    assert post(api, "automations/mail-actions/claim", {"mailbox": PERSONAL}, other["token"])["actions"] == []


def test_a_shared_mailbox_without_a_bot_only_syncs_on_a_server(api, known_person):
    machines = computers(api, operator="cara", isolated=False)
    place(api, machines[0], "coo")
    with api.app.state.store.transaction() as c:
        mailbox_access.ensure_mailbox(c, "support@acme.example", kind="shared")
    assert owners(api, machines, "support@acme.example") == []
    assert "support@acme.example" not in addresses(api, machines[0])
    [server] = computers(api, 1)
    machines.append(server)
    assert owners(api, machines, "support@acme.example") == [server["runner_id"]]
    assert "support@acme.example" in addresses(api, server)
    publish(api, server["token"], "support@acme.example")
    assert post(api, "automations/mail-actions/claim", {"mailbox": "support@acme.example"},
                server["token"])["actions"] == []
    offline(api, server)
    assert owners(api, machines, "support@acme.example") == []


def test_sync_does_not_give_a_colocated_bot_mail_api_cli_or_token_access(api, known_person, monkeypatch, capsys):
    from connectors.mail import Refused, access
    from connectors.mail import __main__ as cli
    machine, _, attempt = setup_attempt(api, "ops")
    contact(api, machine)
    assert attempt["mailbox_levels"] == {} and attempt["mailboxes"] == []
    assert owners(api, [machine]) == [machine["runner_id"]]
    post(api, "connectors/mail/messages", {"mailbox": PERSONAL, "messages": [mail_message()],
                                          "synced_at": H.now()}, machine["token"])
    turn = attempt["token"]
    answer = get(api, "mail/access?mailbox=" + PERSONAL, turn)
    assert answer["level"] == "none" and answer["mailboxes"] == []
    get(api, "mail/messages?mailbox=" + PERSONAL, turn, expected=403)
    get(api, "mail/messages/m-plain?mailbox=" + PERSONAL, turn, expected=403)
    monkeypatch.setattr(access, "SERVER_ACCESS", lambda slug: answer)
    monkeypatch.setattr(access, "RUNNER_ACCESS", lambda: get(api, "connectors/mail/targets", machine["token"]))
    manifest = {"tools": [{"service": "gmail", "identity": PERSONAL, "can": ["read"]}]}
    assert access.readable_mailboxes("ops", manifest=manifest) == []
    with pytest.raises(Refused):
        access.resolve("ops", PERSONAL, manifest=manifest)
    monkeypatch.setattr(cli, "open_gmail", lambda box: pytest.fail("An ungranted bot reached Gmail"))
    assert cli.main(["inbox", "--as", "ops", "--mailbox", PERSONAL]) == 2
    assert "may not use" in capsys.readouterr().err
    assert access.resolve(access.CONNECTOR, PERSONAL, manifest={})[0] == PERSONAL
    with api.app.state.store.read() as c:
        assert PERSONAL not in routines.token_mailboxes(c, "ops")
    # The sync computer can mint for its connector, but the bot's own credential cannot.
    token(api, turn, PERSONAL, expected=403)


def test_the_owners_assistant_and_mail_page_read_live_mail_without_a_message_bot(
        api, workspace_key, gmail_http, monkeypatch):
    from connectors.mail import access
    seed_org(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'",
                  (encode({"people": [{k: v for k, v in p.items() if k != "inbox_bot"} for p in ORG]}),))
        mailbox_access.ensure_mailbox(c, BOX)
    machine, attempt = assistant_turn(api, "ana-test", "Read my inbox")
    assert attempt["mailbox_levels"] == {BOX: "read_write"} and attempt["mailboxes"] == [BOX]
    with api.app.state.store.read() as c:
        assert routines.message_bot_mailboxes(c) == []
    assert addresses(api, machine) == {BOX}
    post(api, "connectors/mail/messages", {"mailbox": BOX, "messages": [mail_message()],
                                          "synced_at": H.now()}, machine["token"])
    with api.app.state.store.read() as c:
        synced = c.execute("SELECT msg_id,body FROM mail_messages WHERE mailbox=?", (BOX,)).fetchone()
        assert synced["msg_id"] == "m-plain" and synced["body"] == "How does pricing work?"
    for credential in ("ana-test", attempt["token"]):
        boxes = get(api, "mail/mailboxes", credential)["mailboxes"]
        box = next(b for b in boxes if b["address"] == BOX)
        assert box["message_count"] == 1 and box["agent_bot"] is None
        assert get(api, "mail/messages?mailbox=" + BOX, credential)["messages"][0]["msg_id"] == "m2"
        assert get(api, "mail/messages/m1?mailbox=" + BOX, credential)["body"] == "Live contract body."
    answer = get(api, "mail/access?mailbox=" + BOX, attempt["token"])
    assert answer["principal"] == "person:ana" and answer["level"] == "read_write"
    monkeypatch.setattr(access, "SERVER_ACCESS", lambda slug: answer)
    assert access.resolve("coo", BOX, manifest={})[0] == BOX
