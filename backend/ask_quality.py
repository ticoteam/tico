# pyright: strict
"""How well each bot asks people (Tasks spec "Bots ask people a question with choices", AC-12), for the Decide readout
(backend/decide_readout.py): per bot, its asks to people in the window, how many a person sent back as "Badly written"
(the decision view's chip, a 👎 on the ask), and the share (the target is under 1 in 20, read three weeks after the
release: `tico decision readout --days 21`).

The guard: refusing badly written asks must not stop a bot asking at all. Each refusal of a bot's ask to a person (its
shape, `task_ask_shape`; a rule that covers it, `task_rule`; a decision filed as a to-do, ask ingest's `decision`) is
followed within a day by an ask of that bot's that got through. A refusal with none is `stuck`; one too recent to tell
(less than a day before the window's end) is not counted either way.

Plain code over what is stored, no model; counts and bot names only, never an ask's words.
"""

from datetime import datetime, timedelta
from typing import Any

from . import hubdb as _hubdb

H: Any = _hubdb
BADLY_WRITTEN = "Badly written"  # the chip's words, which the 👎 reason starts with (ui/app/decide.ts DEC_CHIPS)
TARGET = 1 / 20
REFUSED_RULES = ("task_ask_shape", "task_rule")
DAY = timedelta(days=1)


def _at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def per_bot(c: Any, start: str, end: str) -> list[dict[str, Any]]:
    """[{bot, name, asks, badly_written, share, refused, stuck}] for every bot that asked a person or was refused in
    [start, end), most asks first. `share` is None with no asks."""
    asks: dict[str, list[str]] = {}
    for bot, created in c.execute(
        "SELECT added_by_id, created_at FROM requirements WHERE by_bot=1 AND visit_id IS NOT NULL "
        "AND person_id LIKE 'human:%' AND kind IN ('answer','ok') AND created_at>=? AND created_at<?",
        (start, end),
    ):
        asks.setdefault(str(bot), []).append(str(created))
    badly: dict[str, int] = {}
    for bot, n in c.execute(
        "SELECT bot_id, COUNT(*) FROM feedbacks WHERE subject_type='ask' AND rating='down' AND reason LIKE ? "
        "AND created_at>=? AND created_at<? GROUP BY bot_id",
        (BADLY_WRITTEN + "%", start, end),
    ):
        badly[str(bot)] = int(n)
    refused: dict[str, list[str]] = {}
    marks = ",".join("?" * len(REFUSED_RULES))
    for bot, created in c.execute(
        f"SELECT actor, created_at FROM refusals WHERE rule IN ({marks}) AND actor LIKE 'bot:%' "
        "AND created_at>=? AND created_at<?",
        (*REFUSED_RULES, start, end),
    ):
        refused.setdefault(str(bot), []).append(str(created))
    for bot, created in c.execute(
        "SELECT asker, created_at FROM ask_ingest_items WHERE outcome='sent_back' AND reason='decision' "
        "AND created_at>=? AND created_at<?",
        (start, end),
    ):
        refused.setdefault(str(bot), []).append(str(created))
    # An ask that got through within a day after a refusal may fall after the window's end: read up to a day past it.
    later: dict[str, list[datetime]] = {}
    for bot, created in c.execute(
        "SELECT added_by_id, created_at FROM requirements WHERE by_bot=1 AND visit_id IS NOT NULL "
        "AND person_id LIKE 'human:%' AND kind IN ('answer','ok') AND created_at>=? AND created_at<?",
        (start, (_at(end) + DAY).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"),
    ):
        later.setdefault(str(bot), []).append(_at(str(created)))
    out: list[dict[str, Any]] = []
    for bot in sorted(set(asks) | set(refused) | set(badly)):
        made = len(asks.get(bot, []))
        stuck = 0
        for when in refused.get(bot, []):
            at = _at(when)
            if at + DAY > _at(end):
                continue  # too recent to tell
            if not any(at < got <= at + DAY for got in later.get(bot, [])):
                stuck += 1
        sent_back = badly.get(bot, 0)
        out.append(
            {
                "bot": bot,
                "name": H.actor_name(c, bot) or H.actor_id(bot),
                "asks": made,
                "badly_written": sent_back,
                "share": round(sent_back / made, 4) if made else None,
                "refused": len(refused.get(bot, [])),
                "stuck": stuck,
            }
        )
    out.sort(key=lambda row: (-row["asks"], row["name"]))
    return out


def lines(rows: list[dict[str, Any]]) -> list[str]:
    """The readout's lines for these rows: one per bot, then whether the target and the guard hold."""
    if not rows:
        return []
    out = ["Asks to people, per bot (“Badly written” send-backs; target under 1 in 20):"]
    for row in rows:
        share = "n/a" if row["share"] is None else f"{row['share'] * 100:.0f}%"
        line = f"  {row['name']}: {row['asks']} asks, {row['badly_written']} badly written ({share})"
        if row["refused"]:
            line += f"; refused {row['refused']}, " + (
                f"{row['stuck']} with no ask through within a day" if row["stuck"] else "each followed by an ask"
            )
        out.append(line)
    over = [r["name"] for r in rows if r["share"] is not None and r["share"] >= TARGET]
    stuck = [r["name"] for r in rows if r["stuck"]]
    out.append("  Target: " + ("met by every bot" if not over else "missed by " + ", ".join(over)))
    out.append(
        "  Guard: " + ("every refused bot asked again within a day" if not stuck else "not met by " + ", ".join(stuck))
    )
    return out
