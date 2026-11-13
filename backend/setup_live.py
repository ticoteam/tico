# pyright: strict
"""A seat bot's setup ends with the bot live (company_import._go_live): BotOps builds it on its "Set up" task and takes it
live, and the task is done only once the bot is active and takes work. Saying done earlier would leave the bot `planned`
with nobody left to finish it, while its pipeline's tasks wait on it.

`GOES_LIVE` is the declaration key that marks such a bot; its `setup_task_id` names the task. A go-live waiting for the
computer to get the repository (backend/go_live.py) finishes by itself, so BotOps waits for it; one that failed was
already said to the person who asked, once, so the task may close with BotOps' note.

Once live, the seat bot runs its template's own setup (`playbooks/onboarding.md`, the setup a person's first message
starts for a hired bot) with no person: `start_setup` gives it a task, first in its queue, that says to take each
question's default, write the knowledge files that setup writes, mark what nobody has told it yet, and ask nothing now.
"""

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, cast

from . import go_live as pending_go_lives
from .store import H, Problem

GOES_LIVE = "goes_live_on_setup"
# When the seat bot was given its own setup task, so it is given once. A time, not the task's id: the declaration goes
# to its computer, which may not read the task.
SELF_SETUP = "self_setup_given_at"


def _object(value: Any) -> dict[str, Any]:
    try:
        found = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return {}
    return cast("dict[str, Any]", found) if isinstance(found, dict) else {}


def bot_of(c: sqlite3.Connection, task_id: str) -> tuple[str | None, dict[str, Any]]:
    """The bot whose setup task this is, with its declaration, or (None, {})."""
    row = c.execute(
        "SELECT bot_id, extra FROM bot_settings WHERE json_extract(extra,'$.setup_task_id')=? LIMIT 1", (task_id,)
    ).fetchone()
    if not row:
        return None, {}
    return str(row["bot_id"]), _object(row["extra"])


def pending(c: sqlite3.Connection, bot: str) -> dict[str, Any] | None:
    """The go-live waiting for this bot's repository (backend/go_live.py), or None."""
    found = c.execute("SELECT value FROM registry_metadata WHERE key=?", (pending_go_lives.PREFIX + bot,)).fetchone()
    return _object(found["value"]) if found else None


def require_live(c: sqlite3.Connection, task_id: str) -> str | None:
    """Refuse marking a seat bot's setup task done while the bot cannot take work yet, saying what finishes it. Returns
    the seat bot when it is live, so its routines go on with it however it went live, else None."""
    bot, declared = bot_of(c, task_id)
    if not bot or not declared.get(GOES_LIVE):
        return None
    row = cast("dict[str, Any] | None", cast("Any", H).bot(c, bot))
    if not row or row["state"] == "archived":
        return None
    if row["state"] == "active":
        return bot  # live; an onboarding hold is a person's step, not one BotOps can finish
    name, slug = row.get("display_name") or row["slug"], row["slug"]
    waiting = pending(c, bot)
    if waiting and waiting.get("failed"):
        return None  # the person who asked was told why it could not go live; the note says the rest
    if waiting:
        where = waiting.get("computer") or "its computer"
        raise Problem(
            "bot_not_live",
            f"{name} is not live yet: Tico finishes its go-live by itself when {where} has its repository. Snooze "
            f"this task for a few minutes and mark it done once {name} is active (`tico bot show {slug}`)",
            409,
        )
    raise Problem(
        "bot_not_live",
        f"{name} is not live yet. Take it live with `tico bot go-live {slug}`, then mark this task done once it is "
        "active",
        409,
    )


def refuse_snooze_once_live(c: sqlite3.Connection, task_id: str) -> None:
    """Refuse snoozing a seat bot's setup task once the bot is live: a snooze there waits for a go-live that has already
    happened, and nothing would end it before its date. BotOps reads a bot before it goes live, so it can ask for this
    after the bot is already live; the task is then marked done instead."""
    from . import task_engine as E

    bot, declared = bot_of(c, task_id)
    if not bot or not declared.get(GOES_LIVE):
        return
    row = cast("dict[str, Any] | None", cast("Any", H).bot(c, bot))
    task = E.task(c, task_id)
    if not row or row["state"] != "active" or not task or task.get("status") != "open":
        return
    name = row.get("display_name") or row["slug"]
    raise Problem("bot_live", f"{name} is already live, so there is nothing to wait for. Mark this task done.", 409)


def go_live_ends_botops_wait(c: sqlite3.Connection, bot: str, by: str) -> None:
    """A go-live ends the wait BotOps set on the bot's setup task while the bot waited for its repository
    ("waiting to go live"): the snooze ends as the bot goes live, and BotOps is woken to say done, as a done ends the
    owner's own snooze (backend/answers.py). Only a snooze BotOps set: a person's snooze is theirs to end."""
    from . import task_engine as E

    found = c.execute(
        "SELECT json_extract(extra,'$.setup_task_id') FROM bot_settings WHERE bot_id=?", (bot,)
    ).fetchone()
    task_id = str(found[0]) if found and found[0] else ""
    task = E.task(c, task_id) if task_id else None
    if not task or task.get("status") != "open" or not task.get("snooze"):
        return
    # BotOps acts for the person it works for: the snooze row names that person, the history row names BotOps.
    last = c.execute(
        "SELECT via FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='snooze' "
        "ORDER BY created_at DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    if last and last["via"] == "botops":
        E.unsnooze(c, task_id, by, "Live")


def being_set_up(c: sqlite3.Connection, bot: str) -> bool:
    """Whether a planned bot is on its way live: BotOps has its setup task open, or its go-live waits for its
    repository. Such a bot's tasks wait on its setup, not on a person."""
    found = pending(c, bot)
    if found is not None and not found.get("failed"):
        return True
    task = c.execute("SELECT json_extract(extra,'$.setup_task_id') FROM bot_settings WHERE bot_id=?", (bot,)).fetchone()
    return bool(
        task and task[0] and c.execute("SELECT 1 FROM tasks WHERE id=? AND status='open'", (task[0],)).fetchone()
    )


def setup_files(catalog_dir: str, template: str) -> list[str]:
    """The knowledge files a template's setup writes: those its `playbooks/onboarding.md` names under `## 4. Record`,
    in order. Examples it points at are read, not written. [] for a template with no such setup."""
    page = Path(catalog_dir) / template / "playbooks" / "onboarding.md"
    try:
        text = page.read_text()
    except OSError:
        return []
    record = re.search(r"^## 4\. Record\b.*?(?=^## |\Z)", text, re.M | re.S)
    found = re.findall(r"knowledge/[\w./-]*\w\.md", record.group(0) if record else "")
    return list(dict.fromkeys(name for name in found if not name.startswith("knowledge/examples/")))


def _stages(c: sqlite3.Connection, bot: str) -> list[str]:
    """`<pipeline> (you manage it; you own <stage>, <stage>)` for each pipeline this bot manages or owns a stage in."""
    actor = "bot:" + bot
    owned: dict[str, list[str]] = {}
    managed: set[str] = set()
    for row in c.execute(
        "SELECT p.name, s.name, s.owner=?, p.manager_id=? FROM pipelines p JOIN stages s ON s.pipeline_id=p.id "
        "WHERE p.retired_at IS NULL AND (p.manager_id=? OR s.owner=?) ORDER BY p.name, s.position",
        (actor, actor, actor, actor),
    ):
        stages = owned.setdefault(str(row[0]), [])
        if row[2]:
            stages.append(str(row[1]))
        if row[3]:
            managed.add(str(row[0]))
    out: list[str] = []
    for name, stages in owned.items():
        parts = (["you manage it"] if name in managed else []) + (["you own " + ", ".join(stages)] if stages else [])
        out.append(f"{name} ({'; '.join(parts)})")
    return out


def setup_brief(company: str, files: list[str], pipelines: list[str]) -> str:
    """The seat bot's own setup task: its template's setup, run with no person."""
    lines = [
        "Run your setup now (`playbooks/onboarding.md`), with no person. You fill a seat on the company chart, and "
        "nobody is waiting to answer setup questions: a person signs off only on specs and on the pre-release page.",
        "",
        "- Read what Tico already knows first: section 1 of the playbook, then `tico strategy show` and "
        '`tico doc search "vision"` for the company\'s strategy or vision pages, if there are any.',
        "- Do not introduce yourself, ask the setup questions, message anyone or open a chat, and ask nothing on this "
        "task.",
        "- Answer each setup question from what Tico shows, else take its default. Record each, dated, in the "
        "`knowledge/` file it belongs to, with `(default)` after each answer nobody gave you.",
        "- Write the files your setup writes from those answers"
        + (": " + ", ".join(f"`{name}`" for name in files) if files else "")
        + ". Seed them from your template's practice files and what Tico knows. In each, end with a `Not yet told:` "
        "line naming the questions it rests on that nobody has answered, or `none`.",
        "- Produce the first report only from inputs that already exist; if they do not, say on this task which are "
        "missing and go on.",
        "- Record what you did (with defaults) in `memory/decisions.md`, commit, "
        "run `tico bot setup-done`, and mark this task done with a note listing the files you wrote.",
        "- Later, when a task really needs an answer you do not have, ask it then as a question on that task.",
        "",
        "What Tico knows:",
        f"- Company: {company or 'not named yet'}",
        "- Pipelines: " + ("; ".join(pipelines) if pipelines else "none yet"),
    ]
    return "\n".join(lines)


def start_setup(c: sqlite3.Connection, onboarding: Any, bot: str) -> str | None:
    """Give a seat bot that just went live its own setup task, once, first in its queue (see the module note). The
    requester is BotOps, which set it up, so no person is asked or told. Returns the task id, or None when there is
    nothing to give: not a seat bot, given before, or a template with no setup."""
    from . import onboarding as O

    declared: dict[str, Any] = onboarding._declared(c, bot)
    template = str(declared.get("template") or "")
    if not declared.get(GOES_LIVE) or declared.get(SELF_SETUP) or not template:
        return None
    catalog = str(onboarding.settings.catalog_dir)
    if not (Path(catalog) / template / "playbooks" / "onboarding.md").is_file():
        return None
    names = cast("dict[str, Any]", cast("Any", O).load(c).get("names") or {})
    company = str(names.get("company_name") or "").strip()
    body = setup_brief(company, setup_files(catalog, template), _stages(c, bot))
    by = "bot:botops" if cast("Any", H).bot(c, "botops") else "human:" + str(onboarding.auth.owner_id(c))
    task = cast("dict[str, Any]", cast("Any", H).task_create(c, by, "Set yourself up", body, "bot:" + bot, top=True))
    declared[SELF_SETUP] = H.now()
    onboarding._write_config(c, bot, declared)
    return str(task["id"])
