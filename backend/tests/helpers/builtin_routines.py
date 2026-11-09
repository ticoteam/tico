"""Helpers that other tests share, moved out of test_builtin_routines.py so they import no test file."""

from pathlib import Path
import yaml
from backend import routines


COO = {"coo": {"name": "Coo", "status": "active", "template": "assistant"}}


SWEEP = "coo:daily-sweep"


def _write_assistant_routines(api):
    folder = Path(api.app.state.store.settings.catalog_dir) / "assistant"
    (folder / "playbooks").mkdir(exist_ok=True)
    (folder / "bot.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "assistant",
                "routines": [
                    {
                        "id": "daily-sweep",
                        "title": "Daily sweep of each person's queue",
                        "cron": "0 8 * * *",
                        "template": "playbooks/daily-sweep.md",
                    }
                ],
            }
        )
    )
    (folder / "playbooks" / "daily-sweep.md").write_text("Sweep {{company_name}}'s queue.\n")


def _row(api):
    """The routine as the person sees it: a routine written the new way moves to an automation at once
    (redesign_migration.adopt), and its schedule row is then soft-deleted; `routines.row` reads the automation."""
    with api.app.state.store.read() as c:
        return routines.row(c, SWEEP)


def _ensure(api):
    with api.app.state.store.transaction() as c:
        return api.app.state.onboarding.ensure_builtin_routines(c)
