"""The bot files and settings under their new names (bot.yaml, routines:, tools:, groups.yaml, group:, needs_setup,
bot-<slug>), and the old names still read for one release."""

import yaml

from backend import statuses
from backend.config import Settings
from backend.store import Store
from clients import manifest as M


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data) if not isinstance(data, str) else data)
    return path


def test_the_readers_take_either_manifest(tmp_path):
    from clients import registry
    from clients.remotecli import seed_automations
    from connectors.mail import access as mail

    routine = {"id": "daily", "title": "Daily", "cron": "0 9 * * 1-5", "enabled": False, "instructions": "Do it"}
    for folder, manifest in (("old", {"name": "old", "schedules": [routine], "access": [{"service": "gmail", "identity": "a@x.example", "can": ["read"], "env": "OLD_KEY"}]}),
                             ("new", {"name": "new", "routines": [routine], "tools": [{"service": "gmail", "identity": "a@x.example", "can": ["read"], "env": "NEW_KEY"}]})):
        write(tmp_path / (("emp-" if folder == "old" else "bot-") + folder) / ("employee.yaml" if folder == "old" else "bot.yaml"), manifest)

    class Hub:
        def __init__(self):
            self.posts = []

        def post(self, path, body):
            self.posts.append((path, body))
            return {"id": "auto_%d" % len(self.posts)}

    for folder in ("old", "new"):
        hub = Hub()
        assert seed_automations(hub, folder, M.repo_dir(tmp_path, folder)) == ["auto_1"]
        path, body = hub.posts[0]
        assert path == "automations" and body["name"] == "Daily" and body["enabled"] is False
        assert body["trigger"]["type"] == "schedule" and body["trigger"]["cron"] == "0 9 * * 1-5"
        assert body["steps"][0]["bot"] == "bot:" + folder and body["steps"][0]["prompt"] == "Do it"
    merged = registry.merge_employee({"name": "old"}, {}, root=tmp_path)
    assert registry.declared_env_keys(merged) == ["OLD_KEY"]
    assert registry.declared_env_keys(registry.merge_employee({"name": "new"}, {}, root=tmp_path)) == ["NEW_KEY"]
    # The mail connector (its own package) reads the same two names.
    assert [e["identity"] for e in mail.entries({"access": [{"service": "gmail", "identity": "old@x.example"}]})] == ["old@x.example"]
    assert [e["identity"] for e in mail.entries({"tools": [{"service": "gmail", "identity": "new@x.example"}],
                                                 "access": [{"service": "gmail", "identity": "old@x.example"}]})] == ["new@x.example"]


def test_a_stored_needs_onboarding_row_becomes_needs_setup_and_readers_take_both(tmp_path):
    settings = Settings(db_path=tmp_path / "hub.db", registry_dir=tmp_path)
    store = Store(settings)
    store.initialize()
    with store.transaction() as c:
        assert c.execute("SELECT 1 FROM cloud_migrations WHERE version=45").fetchone()
        for slug, state in (("old", "needs_onboarding"), ("new", "needs_setup"), ("done", "onboarded")):
            c.execute("INSERT INTO bots(slug,display_name) VALUES(?,?)", (slug, slug))
            c.execute("INSERT INTO bot_config(bot,config,operator,onboarding_state) VALUES(?,?,?,?)", (slug, "{}", "morgan", state))
        c.execute("DELETE FROM cloud_migrations WHERE version=45")             # the state a database is in before this release
    store.initialize()
    with store.read() as c:
        states = dict(c.execute("SELECT bot,onboarding_state FROM bot_config").fetchall())
    assert states == {"old": "needs_setup", "new": "needs_setup", "done": "onboarded"}
    assert statuses.is_parked("needs_onboarding") and statuses.is_parked("needs_setup") and not statuses.is_parked("onboarded")
    # A reader on a row a not-yet-migrated peer wrote: the SQL helper matches both.
    with store.read() as c:
        assert c.execute("SELECT 'needs_onboarding' IN " + statuses.PARKED_SQL).fetchone()[0] == 1


def test_a_repository_is_found_under_bot_or_emp_and_a_new_one_is_bot(tmp_path):
    assert M.repo_dir(tmp_path, "seo") == tmp_path / "bot-seo"                  # neither exists: where a new one goes
    (tmp_path / "emp-seo").mkdir()
    assert M.repo_dir(tmp_path, "seo") == tmp_path / "emp-seo"                   # an existing emp-* repository keeps working
    (tmp_path / "bot-seo").mkdir()
    assert M.repo_dir(tmp_path, "seo") == tmp_path / "bot-seo"

    from clients import catalog
    template = tmp_path / "catalog" / "helper"
    write(template / "bot.yaml", "name: CHANGE-ME\ndisplay_name: Helper\nroutines: []\n")
    write(template / "card.yaml", {"template": "helper", "name": "Helper"})
    write(template / "AGENT.md", "# {{bot_name}}\n")
    workspace = tmp_path / "ws"
    (workspace / "emp-old").mkdir(parents=True)
    made = catalog.materialize("helper", "fresh", workspace, {}, {}, directory=tmp_path / "catalog")
    assert made.name == "bot-fresh" and yaml.safe_load((made / "bot.yaml").read_text())["name"] == "fresh"
    try:
        catalog.materialize("helper", "old", workspace, {}, {}, directory=tmp_path / "catalog")
    except ValueError as exc:
        assert "emp-old" in str(exc)                                             # never a second repository beside an existing one
    else:
        raise AssertionError("an existing emp-* repository was not respected")
