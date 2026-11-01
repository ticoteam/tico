"""A bot made after bots got stable ids (a UUIDv7 id) runs on the real Rust runner in the folder named from the slug it
was made with, answers by slug or id, and keeps its folder, its work and its history when its slug is renamed
(backend/settings_admin.py rename, runner-rs/src/sessions.rs named_folder, docs/how-it-works.md).

Slow: the runner binary is built once per run (cargo). Run it on RunsOn:
`python3 scripts/remote.py --setup rust -- python3 -m pytest -q -o addopts='' -m slow backend/tests/test_e2e_bot_rename.py`.
"""
import json
import uuid
from pathlib import Path

import pytest

from backend.store import encode
from backend.tests.test_api import api, assign, get, headers, post  # noqa: F401
from backend.tests.live import live  # noqa: F401
from runner.tests.test_conformance import MODELS, Computer, reply_to, rust_binary, until  # noqa: F401

pytestmark = pytest.mark.slow


@pytest.fixture
def machine(api, live, tmp_path, rust_binary):
    computer = Computer(api, live, tmp_path, "claude", rust_binary)
    yield computer
    computer.stop()


def test_a_new_bot_runs_in_its_named_folder_and_keeps_it_after_a_rename(api, machine, tmp_path):
    made = api.post("/api/v2/bots", json={"slug": "sales-helper", "display_name": "Sales Helper", "status": "active",
                                          "description": "Answers sales questions"}, headers=headers())
    assert made.status_code == 200, made.text
    bot = get(api, "bots/sales-helper")["id"]
    assert uuid.UUID(bot).version == 7
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()[0])
        assert config["folder"] == "bot-sales-helper"           # named once, from the slug it was made with
        config.update({"runtime": machine.runtime, "harness": "claude", "model": MODELS["claude"],
                       "model_managed_by": None, "status": "active"})
        c.execute("UPDATE bot_config SET config=? WHERE bot=?", (encode(config), bot))
        placed = c.execute("SELECT generation FROM assignments WHERE bot=?", (bot,)).fetchone()
    # placed on this computer by its slug (it may already be placed by its creation); stored by its id
    assign(api, machine.registration, "sales-helper", generation=placed[0] if placed else 0)
    projects = tmp_path / "projects"
    folder = projects / "bot-sales-helper"
    folder.mkdir()
    (folder / "AGENT.md").write_text("# Sales Helper\n\nAnswer the Acme team.\n")
    (folder / "bot.yaml").write_text(f"name: sales-helper\nruntime: {machine.runtime}\nmodel: {MODELS['claude']}\n")

    machine.start()
    try:
        until(lambda: machine.readiness().get(bot, {}).get("ready"), "the new bot ready on the computer")
    except AssertionError:
        raise AssertionError(json.dumps(machine.readiness().get(bot)) + machine.output.read_text()[-2000:])

    first = post(api, "chat/sales-helper", {"text": "What did we sell?"})
    answer = until(lambda: reply_to(api, first["conversation_id"], bot=bot), "the new bot's reply", timeout=60)
    assert answer["sender_id"] == "bot:" + bot
    turn = next(call for call in machine.prompts() if "What did we sell?" in call["prompt"])
    assert Path(turn["cwd"]).resolve() == folder.resolve()

    revision = get(api, "bots/" + bot)["revision"]
    renamed = post(api, f"bots/{bot}/definition", {"slug": "revenue-helper", "expected_revision": revision})
    assert (renamed["id"], renamed["slug"]) == (bot, "revenue-helper")

    second = post(api, "chat/revenue-helper", {"text": "And this week?"})
    assert second["conversation_id"] == first["conversation_id"]   # the same chat: it is the same bot
    until(lambda: any("And this week?" in call["prompt"] for call in machine.prompts()), "the renamed bot's turn",
          timeout=60)
    turn = next(call for call in machine.prompts() if "And this week?" in call["prompt"])
    assert Path(turn["cwd"]).resolve() == folder.resolve()          # the folder stays
    assert not (projects / f"bot-{bot}").exists() and not (projects / "bot-revenue-helper").exists()
    messages = get(api, f"conversations/{first['conversation_id']}/messages")
    assert {m["sender_id"] for m in messages if m["sender_id"].startswith("bot:")} == {"bot:" + bot}
