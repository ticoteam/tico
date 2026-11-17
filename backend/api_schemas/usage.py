# pyright: strict
"""The answers of the Usage routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import NUM_N, SCHEMAS, items, obj

PERIODS = (
    "per_30m",
    "hourly",
    "daily",
    "weekly",
    "monthly",
)  # the spend limit periods (backend/usage_limits.py); the 30-minute one


SCHEMAS.update(
    {
        "UsageFigures": obj(
            {
                "runs": "i",
                "input_tokens": {
                    "type": "integer",
                    "description": "Uncached input tokens, without cache writes (a run whose runner did not count cache "
                    "writes apart includes them)",
                },
                "cached_tokens": {"type": "integer", "description": "Input tokens read from the provider's cache"},
                "cache_write_tokens": {
                    "type": "integer",
                    "description": "Input tokens written to the provider's prompt cache (Claude, Cursor, pi); 0 for runtimes "
                    "that report none (Codex, Gemini, Antigravity, Muse Code) and for runs from before they were counted apart",
                },
                "output_tokens": "i",
                "est_cost_usd": {
                    "type": ["number", "null"],
                    "description": "List-price estimate for runs billed by API key; "
                    "null when every such run used a model with no list price",
                },
                "subscription_equiv_usd": {
                    "type": "number",
                    "description": "What runs on a ChatGPT or Claude sign-in would "
                    "cost through the API: not money spent, never added to est_cost_usd",
                },
                "unpriced_runs": {"type": "integer", "description": "Runs with tokens on a model with no list price"},
                "low_estimate_runs": {
                    "type": "integer",
                    "description": "Priced runs whose estimate may be low: the model "
                    "charges more for long prompts and the runner did not report every request's tokens",
                },
            }
        ),
        "Usage": obj(
            {
                "from": "s",
                "to": "s",
                "prices_as_of": {"type": "string", "description": "The date the price table was read"},
                "group": {"enum": ["bot", "day", "routine", "task", "harness", "model", "effort", "subscription"]},
                "department": "n",
                "totals": ref("UsageFigures"),
                "rows": items(
                    {
                        "type": "object",
                        "description": "By bot: bot, name, department; by day: day; by routine: "
                        "routine (null for runs that came from none), title, bot, name; by task: value and name (its title, "
                        "'A private task', or null and 'Chats and other runs' for runs with none). Every row has the "
                        "usage figures and `share`, its part of the total (estimate plus API-equivalent)",
                        "additionalProperties": True,
                    }
                ),
                "dimensions": {"type": "object", "description": "Available dimension filters, scoped to visible runs"},
                "dimension_labels": {
                    "type": "object",
                    "description": "Display labels keyed by immutable filter value; subscription names include their Computer",
                },
                "departments": items({"type": "string"}),
                "bot": "s",
                "name": "s",
                "daily": items({"type": "object", "additionalProperties": True}),
                "routines": items({"type": "object", "additionalProperties": True}),
                "tasks": items(
                    {
                        "type": "object",
                        "description": "?bot only: its top 10 tasks by spend, as group=task names them (value and name), plus runs with no task, "
                        "each with the usage figures and `share`, its part of the bot's spend",
                        "additionalProperties": True,
                    }
                ),
                "tasks_more": {
                    "type": "object",
                    "description": "?bot only: `count`, the tasks past the top 10, and their usage figures summed",
                    "additionalProperties": True,
                },
                "models": items(
                    {
                        "type": "object",
                        "description": "?bot only: every model it used, value (null when not recorded) and name, with "
                        "the usage figures and `share`",
                        "additionalProperties": True,
                    }
                ),
            },
            required=["from", "to", "prices_as_of", "totals"],
        ),
        "UsageLimitView": obj(
            {
                **{p + "_usd": NUM_N for p in PERIODS},
                "source": {"type": "object", "description": "Where each cap comes from: bot, company or null"},
                **{p + "_spent": NUM_N for p in ("hour", "day", "week", "month")},
                "percent": {
                    "type": "integer",
                    "description": "The highest share of one of the bot's own limits reached",
                },
                "blocked": {
                    "enum": [*PERIODS, "quarterly", "yearly", "budget", None],
                    "description": "Set while a "
                    "limit is met, the bot's own, a pool's or a Finance budget's (quarterly, yearly, or budget "
                    "for a one-time budget): the bot takes no new job",
                },
                "blocked_by": {
                    "type": ["object", "null"],
                    "description": "Whose limit holds the bot: scope (bot, company, "
                    "group:<id> or budget), name (a pool's or budget's), period, until for a budget (\"\" "
                    "when it never turns over), and spent and limit (a pool's to the owner and "
                    "administrators only)",
                    "additionalProperties": True,
                },
                **{"own_" + p + "_usd": NUM_N for p in PERIODS},
                "may_edit": "b",
            }
        ),
        "UsageDefaultView": obj({"default": obj({**{p + "_usd": NUM_N for p in PERIODS}, "count_subscription": "b"})}),
        "UsagePoolView": obj(
            {
                **{p + "_usd": NUM_N for p in PERIODS},
                **{p + "_spent": NUM_N for p in ("30m", "hour", "day", "week", "month")},
                "percent": "i",
                "blocked": {"enum": [*PERIODS, None]},
            }
        ),
        "UsagePoolLimit": obj(
            {"scope": {"type": "string", "description": "company or group:<id>"}, "limit": ref("UsagePoolView")}
        ),
        "UsageLimits": obj(
            {
                "default": obj({**{p + "_usd": NUM_N for p in PERIODS}, "count_subscription": "b"}),
                "may_edit_default": "b",
                "pools": {
                    "type": ["object", "null"],
                    "description": "Owner and administrators: company (UsagePoolView) and "
                    "groups, {id: UsagePoolView with name and parent}. A lead who is "
                    "not an administrator: company null and only the groups it leads and those below, each "
                    "with may_edit true",
                    "additionalProperties": True,
                },
                "bots": {"type": "object", "additionalProperties": ref("UsageLimitView")},
            }
        ),
        "BotUsageLimit": obj({"bot": "s", "limit": ref("UsageLimitView")}),
        "QueuePurgeResult": obj(
            {
                "scope": {"type": "string", "description": "company, or group:<id>"},
                "cancelled": "i",
                "kept_people": "i",
                "still_running": "i",
            }
        ),
    }
)
