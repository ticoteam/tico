"""The bot files and settings under their new names (bot.yaml, routines:, tools:, groups.yaml, group:, needs_setup,
bot-<slug>), and the old names still read for one release."""

import yaml

from clients import manifest as M


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data) if not isinstance(data, str) else data)
    return path


def test_the_readers_take_either_manifest(tmp_path):
    from clients import registry
    from clients.remotecli import seed_automations

    routine = {"id": "daily", "title": "Daily", "cron": "0 9 * * 1-5", "enabled": False, "instructions": "Do it"}
    for folder, manifest in (
        (
            "old",
            {
                "name": "old",
                "schedules": [routine],
                "access": [{"service": "gmail", "identity": "a@x.example", "can": ["read"], "env": "OLD_KEY"}],
            },
        ),
        (
            "new",
            {
                "name": "new",
                "routines": [routine],
                "tools": [{"service": "gmail", "identity": "a@x.example", "can": ["read"], "env": "NEW_KEY"}],
            },
        ),
    ):
        write(
            tmp_path
            / (("emp-" if folder == "old" else "bot-") + folder)
            / ("employee.yaml" if folder == "old" else "bot.yaml"),
            manifest,
        )

    # A new bot's routines are owned by its id, which the client looks up by slug (as the real client's get does).
    bot_ids = {"old": "0190f3a2-7c1e-7d4a-9b8e-2f6c1a5d9e10", "new": "0190f3a2-7c1e-7d4a-9b8e-2f6c1a5d9e11"}

    class Hub:
        def __init__(self):
            self.gets, self.posts = [], []

        def get(self, path):
            self.gets.append(path)
            return {"id": bot_ids[path.removeprefix("bots/")]}

        def post(self, path, body):
            self.posts.append((path, body))
            return {"id": "auto_%d" % len(self.posts)}

    for folder in ("old", "new"):
        hub = Hub()
        assert seed_automations(hub, folder, M.repo_dir(tmp_path, folder)) == ["auto_1"]
        assert hub.gets == ["bots/" + folder]
        path, body = hub.posts[0]
        actor = "bot:" + bot_ids[folder]
        assert path == "automations" and body["name"] == "Daily" and body["enabled"] is False
        assert body["trigger"]["type"] == "schedule" and body["trigger"]["cron"] == "0 9 * * 1-5"
        assert body["owner"] == actor and body["steps"][0]["bot"] == actor and body["steps"][0]["prompt"] == "Do it"
    merged = registry.merge_employee({"name": "old"}, {}, root=tmp_path)
    assert registry.declared_env_keys(merged) == ["OLD_KEY"]
    assert registry.declared_env_keys(registry.merge_employee({"name": "new"}, {}, root=tmp_path)) == ["NEW_KEY"]


def test_a_repository_is_found_under_bot_or_emp_and_a_new_one_is_bot(tmp_path):
    assert M.repo_dir(tmp_path, "seo") == tmp_path / "bot-seo"  # neither exists: where a new one goes
    (tmp_path / "emp-seo").mkdir()
    assert M.repo_dir(tmp_path, "seo") == tmp_path / "emp-seo"  # an existing emp-* repository keeps working
    (tmp_path / "bot-seo").mkdir()
    assert M.repo_dir(tmp_path, "seo") == tmp_path / "bot-seo"

    from clients import catalog

    template = tmp_path / "catalog" / "helper"
    write(template / "bot.yaml", "name: CHANGE-ME\ndisplay_name: Helper\nroutines: []\n")
    write(template / "card.yaml", {"template": "helper", "name": "Helper"})
    write(template / "AGENTS.md", "# {{bot_name}}\n")
    workspace = tmp_path / "ws"
    (workspace / "emp-old").mkdir(parents=True)
    made = catalog.materialize("helper", "fresh", workspace, {}, {}, directory=tmp_path / "catalog")
    assert made.name == "bot-fresh" and yaml.safe_load((made / "bot.yaml").read_text())["name"] == "fresh"
    try:
        catalog.materialize("helper", "old", workspace, {}, {}, directory=tmp_path / "catalog")
    except ValueError as exc:
        assert "emp-old" in str(exc)  # never a second repository beside an existing one
    else:
        raise AssertionError("an existing emp-* repository was not respected")
