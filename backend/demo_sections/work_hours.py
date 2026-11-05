"""The bots' long working runs: each bot's daily stretch on its queue, so Overview's Bot hours reads a team at work.

Acme's 55 bots work about eight hours a day each, the most on support, operations and marketing queues. Against
4 people at 40 hours a week that is about 20 bot hours for each human hour in the last 7 days, and less in the weeks
before, as the team grew. Each run is one turn its bot finished on its subscription, priced at list price like any
subscription run; it comes before Budgets, which sizes the limits from this spend.
"""

from datetime import timedelta

from .. import demo_content as D, hubdb, providers
from ..store import P

TARGET = 20.0                 # bot hours per human hour over the last 7 days
GROWTH = 1.1                  # the ratio a week lower for each week before
FLOOR = 9.0                   # the lowest a past week falls to
WEEKS = 12                    # matches the Overview card's weeks
SUMMARY = "Worked through the queue."


def seed(b):
    human = len(P.visible_people({"people": D.PEOPLE})) * 40
    bots = [slug for slug, _, _ in D.BOTS]
    # Busier queues get longer days: 0.6 to 1.4 of the average, the same for a bot every day.
    weight = {slug: 0.6 + (index * 37 % 9) / 10 for index, slug in enumerate(bots)}
    total = sum(weight.values())
    runs = []                                       # (start, hours, bot)
    for day in range(WEEKS * 7):
        ratio = TARGET if day < 7 else max(FLOOR, TARGET - GROWTH * (day // 7))
        hours = ratio * human / 7                   # bot hours this day
        for index, slug in enumerate(bots):
            length = hours * weight[slug] / total
            # Each run ends at least half an hour before now, staggered by bot, so none is still going.
            end = b.ago(days=day, hours=0.5 + (index % 7) * 0.4)
            runs.append((end - timedelta(hours=length), length, slug))

    def work(c):
        kept = {row["id"]: row["last_turn_at"] for row in c.execute("SELECT id, last_turn_at FROM bots")}
        for index, (start, length, slug) in enumerate(sorted(runs)):
            b.clock.at = start
            turn = hubdb.turn_start(c, hubdb.KEEPER, slug, trigger="task")
            b.clock.at = start + timedelta(hours=length)
            model = D.USAGE.get(slug, ("claude-sonnet-5-5", "anthropic", "api"))[0]
            provider = "openai" if model.startswith("gpt") else "anthropic"
            tokens_in = int(length * 60 * 1500) + index % 7 * 4000      # about 1.5k a minute, mostly from cache
            cached, tokens_out = tokens_in * 4 // 5, int(length * 60 * 80)
            hubdb.turn_finish(c, hubdb.KEEPER, turn["id"], "completed", SUMMARY,
                              usage={"input_tokens": tokens_in - cached, "cached_tokens": cached,
                                     "output_tokens": tokens_out, "model": model, "provider": provider,
                                     "est_cost_usd": providers.estimate_cost(model, tokens_in - cached, cached, tokens_out),
                                     "billing": "subscription"})
        # A finish stamps the bot's last turn; the newer runs seeded before these keep theirs.
        for slug, at in kept.items():
            if at:
                c.execute("UPDATE bots SET last_turn_at=max(coalesce(last_turn_at,''),?) WHERE id=?", (at, slug))
                c.execute("UPDATE bot_status SET last_turn_at=max(coalesce(last_turn_at,''),?) WHERE bot=?", (at, slug))
    b.write(work)


    # Follow-up appointments use the same central defaults as real task creation. Historical demo tasks retain a
    # useful future date even when their creation clock was replayed weeks earlier.
    def followups(c):
        from backend import hubdb, task_check_backs
        for task in c.execute(f"SELECT * FROM {hubdb.TASKS} tasks WHERE status IN ('open','doing','waiting','review','ready') "
                              "AND (owner_id LIKE 'bot:%' OR requester_id LIKE 'bot:%') ORDER BY id LIMIT 50").fetchall():
            task_check_backs.set_time(c, dict(task), hubdb.shift(hubdb.now(), hours=2), "Check the delegated result and continue.", system=True)
    b.write(followups)
