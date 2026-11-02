"""A new bot's routines are owned by the bot's id, not its slug: a bot's id is its primary key and a rename changes its
slug, so an automation owned by `bot:<slug>` would name a bot that no longer answers to that string."""
from clients import manifest, remotecli, routines


class _Manifest:
    def read_text(self):
        return ""


class _Client:
    def __init__(self, bot_id="0190f3a2-7c1e-7d4a-9b8e-2f6c1a5d9e10"):
        self.bot_id, self.gets, self.posts = bot_id, [], []

    def get(self, path):
        self.gets.append(path)
        return {"id": self.bot_id, "slug": "acme-bot"}

    def post(self, path, body):
        self.posts.append((path, body))
        return {"id": "auto-1"}


def _one_routine(monkeypatch):
    monkeypatch.setattr(manifest, "manifest_path", lambda path: _Manifest())
    monkeypatch.setattr(manifest, "routines_of", lambda data: [{"title": "Daily check"}])
    monkeypatch.setattr(routines, "validate_schedules", lambda declared, read: [{
        "title": "Daily check", "on": None, "cron": "0 9 * * *", "timezone": "UTC", "enabled": True,
        "instructions": "Check the inbox."}])


def test_routine_actor_is_the_bot_id_when_registration_returned_it(monkeypatch, tmp_path):
    _one_routine(monkeypatch)
    client = _Client()
    seeded = remotecli.seed_automations(client, "acme-bot", tmp_path, "0190f3a2-7c1e-7d4a-9b8e-2f6c1a5d9e10")
    assert seeded == ["auto-1"] and not client.gets
    (path, body), = client.posts
    actor = "bot:0190f3a2-7c1e-7d4a-9b8e-2f6c1a5d9e10"
    assert path == "automations" and body["owner"] == actor and body["steps"][0]["bot"] == actor


def test_routine_actor_is_looked_up_by_slug_when_registration_did_not_return_the_id(monkeypatch, tmp_path):
    _one_routine(monkeypatch)
    client = _Client()
    remotecli.seed_automations(client, "acme-bot", tmp_path)
    assert client.gets == ["bots/acme-bot"]
    assert client.posts[0][1]["owner"] == "bot:0190f3a2-7c1e-7d4a-9b8e-2f6c1a5d9e10"
