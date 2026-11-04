"""Demo sections that sit on top of the core seed in backend/demo_seed.py.

Each module owns one area of the app: its words and its rows. `seed(builder)` gets the demo_seed Builder
(`builder.call` for the API as Ana, `builder.write` for a transaction, `builder.at(days=, hours=)` to set the clock,
`builder.ago`, `builder.tasks`). Rows go through the same API and domain code a real install runs, and every name and
address stays fictional (acme.example).
"""

from . import automations_learnings, budgets, finance, growth, meeting_links, recruit, reach, schedules, services, strategy, threads_rules, work_hours

# In this order: a later section builds on rows an earlier one made. Plain imports, so the PR check's import map sees
# what the demo loads (a computed import would make it run every test on any change).
SECTIONS = (strategy, threads_rules, recruit, automations_learnings, finance, growth, services, schedules, work_hours,
            budgets, reach, meeting_links)


def seed_all(builder):
    for section in SECTIONS:
        section.seed(builder)
