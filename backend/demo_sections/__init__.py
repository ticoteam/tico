"""Demo sections that sit on top of the core seed in backend/demo_seed.py.

Each module owns one area of the app: its words and its rows. `seed(builder)` gets the demo_seed Builder
(`builder.call` for the API as Ana, `builder.write` for a transaction, `builder.at(days=, hours=)` to set the clock,
`builder.ago`, `builder.tasks`). Rows go through the same API and domain code a real install runs, and every name and
address stays fictional (acme.example).
"""

import importlib

SECTIONS = ("strategy", "threads_rules", "recruit", "automations_learnings", "finance", "growth", "services",
            "schedules")


def seed_all(builder):
    for name in SECTIONS:
        importlib.import_module(f"{__name__}.{name}").seed(builder)
