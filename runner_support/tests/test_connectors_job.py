"""The connectors job on a computer with no Google key file of its own (runner_support/connectors.py).

It runs with only the mail script, and every calendar snapshot and health report names the accounts it read this
time (ok_accounts), so the server clears their failures and no others. A hub from before that field gets the same body
without it.
"""
from clients.tico import APIError
from runner_support.connectors import ConnectorPublisher

ANA, BEN = "ana@acme.example", "ben@acme.example"


class Hub:
    """The Tico server as the job reaches it. `old` is a hub from before ok_accounts: its models forbid the field and
    refuse it with a 422, and `posts` only keeps what it accepted."""

    def __init__(self, mailboxes=(), old=False):
        self.mailboxes, self.old = list(mailboxes), old
        self.posts, self.refused = [], []

    def get(self, path):
        if path == "connectors/calendar/targets":
            return {"hours": 24, "people": [{"id": "ana", "email": ANA}, {"id": "ben", "email": BEN}]}
        assert path == "connectors/mail/targets", path
        return {"mailboxes": [{"address": a, "message_count": 1} for a in self.mailboxes], "batch": 100}

    def post(self, path, body):
        if path == "connectors/calendar/actions/claim":
            return {"action": None}
        if self.old and "ok_accounts" in body:
            self.refused.append((path, body))
            raise APIError("invalid_request", "unknown field ok_accounts", status=422)
        self.posts.append((path, body))
        return {"ok": True}

    def health(self, service):
        return [body for path, body in self.posts if path == "connectors/health" and body["service"] == service]


def fake_mail(failing=()):
    """The local mail CLI. A backfill answers with a history id and an export has no rows, so a sync posts no messages.
    A mailbox in `failing` answers its sync with a Google refusal."""
    def run(args):
        address = args[args.index("--mailbox") + 1] if "--mailbox" in args else ""
        if address in failing:
            raise RuntimeError("Google said invalid_grant for " + address)
        if args[1:2] == ["export"]:
            return {"messages": []}
        return {"history_id": "1"}
    return run


def no_key(monkeypatch, tmp_path):
    """A computer with no key file and no connected account, and the runner's way to the server."""
    for name in ("GOOGLE_OAUTH_GRANTS", "HUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOOGLE_SA_KEY", str(tmp_path / "no-key" / "google-sa.json"))


def publisher(tmp_path, hub, fetch, mail):
    script = tmp_path / "hub/scripts/mail.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\nexit 1\n")
    script.chmod(0o755)
    made = ConnectorPublisher({"url": "https://tico.example", "token": "runner-token",
                               "projects_dir": str(tmp_path), "mail_sync_seconds": 0, "owner_handle": "ana"},
                              client=hub, fetch=fetch, mail=mail)
    made.script = script
    made.mail_interval = 0
    return made


def test_the_job_runs_on_a_computer_with_no_key_file(tmp_path, monkeypatch):
    no_key(monkeypatch, tmp_path)
    monkeypatch.setenv("HUB_TOKEN", "runner-hub-token")
    hub = Hub(mailboxes=[ANA])
    made = publisher(tmp_path, hub, fetch=lambda email, hours: [], mail=fake_mail())
    made.ready()                                    # only the mail script is needed
    assert made.mail_secret() is None               # nothing to hand the mail CLI as a key file
    env = made.mail_env()
    assert env["TICO_GOOGLE_BROKER_URL"] == "https://tico.example"
    assert env["TICO_GOOGLE_BROKER_AUTH"] == "runner-token"
    assert "HUB_TOKEN" not in env
    assert made.tick() == 2
    assert made.mail_tick() == 0
    assert [path for path, _ in hub.posts] == ["connectors/calendar/snapshots", "connectors/health",
                                               "connectors/health"]


def test_the_calendar_cli_reports_conference_preparation_as_a_definite_retryable_non_send(tmp_path, monkeypatch):
    import json
    import subprocess
    from types import SimpleNamespace
    import pytest
    from runner_support.connectors import CalendarInvitationPending
    no_key(monkeypatch, tmp_path)
    made = publisher(tmp_path, Hub(), fetch=lambda *args: [], mail=fake_mail())
    result = SimpleNamespace(returncode=75, stdout=json.dumps({"kind": "conference_pending", "retryable": True, "event_id": "silent-hold"}))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: result)
    with pytest.raises(CalendarInvitationPending) as pending:
        made.calendar_event({"id": "action"})
    assert pending.value.event_id == "silent-hold" and "Retry scheduling" in str(pending.value)
    result.stdout = "{}"
    with pytest.raises(RuntimeError, match="creation failed"):
        made.calendar_event({"id": "action"})


def test_calendar_snapshots_and_health_name_the_accounts_read(tmp_path, monkeypatch):
    no_key(monkeypatch, tmp_path)
    hub = Hub()

    def fetch(email, hours):
        if email == BEN:
            raise RuntimeError("provider detail must stay on this computer")
        return []
    made = publisher(tmp_path, hub, fetch=fetch, mail=fake_mail())
    assert made.tick() == 1
    (snapshot_path, snapshots), (health_path, health) = hub.posts
    assert snapshot_path == "connectors/calendar/snapshots"
    assert snapshots["ok_accounts"] == [ANA]
    assert [snapshot["email"] for snapshot in snapshots["snapshots"]] == [ANA]
    assert health_path == "connectors/health" and health["service"] == "calendar"
    assert health["ok_accounts"] == [ANA]
    assert [entry["account"] for entry in health["failing"]] == [BEN]
    assert "provider detail" not in str(hub.posts)


def test_a_mailbox_that_fails_is_left_out_of_ok_accounts(tmp_path, monkeypatch):
    no_key(monkeypatch, tmp_path)
    hub = Hub(mailboxes=[ANA, BEN])
    made = publisher(tmp_path, hub, fetch=lambda email, hours: [], mail=fake_mail(failing={BEN}))
    made.mail_tick()                                # ana: read
    made.mail_tick()                                # ben: Google refuses
    first, second = hub.health("mail")
    assert first["ok_accounts"] == [ANA] and first["failing"] == []
    assert second["ok_accounts"] == [] and [entry["account"] for entry in second["failing"]] == [BEN]


def test_a_hub_from_before_ok_accounts_gets_the_same_bodies_without_it(tmp_path, monkeypatch):
    no_key(monkeypatch, tmp_path)
    hub = Hub(mailboxes=[ANA], old=True)
    made = publisher(tmp_path, hub, fetch=lambda email, hours: [], mail=fake_mail())
    assert made.tick() == 2
    made.mail_tick()
    paths = ["connectors/calendar/snapshots", "connectors/health", "connectors/health"]
    assert [path for path, _ in hub.refused] == paths           # each was first sent with the field
    assert [path for path, _ in hub.posts] == paths              # and accepted once it was sent without it
    assert all("ok_accounts" not in body for _, body in hub.posts)
    assert [body["service"] for _, body in hub.posts[1:]] == ["calendar", "mail"]
