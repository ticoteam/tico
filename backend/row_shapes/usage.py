"""Usage, budgets and meter reads.

Rows come from `sqlite3.Row` (SELECT *); see `backend/rows.py` for the rules."""

from typing import TypedDict


class UsageFactRow(TypedDict):
    """One `usage_facts` row (SELECT *): a metered segment, append-only (backend/usage_facts.py)."""

    id: int
    source: str
    run_id: str | None
    position: int
    bot_id: str | None
    task_id: str | None
    provider: str | None
    model: str | None
    family: str | None
    runtime: str | None
    harness: str | None
    effort: str | None
    profile: str | None
    billing: str | None
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    cost_nano: int | None
    low_estimate: int | None
    meter_key: str | None
    gateway_key_id: str | None
    install_id: str | None
    started_at: str
    metered_at: str
    created_at: str


class UsageMeterReadRow(TypedDict):
    """One `usage_meter_reads` row (SELECT *): the newest read of the Gateway's meter taken for a month
    (backend/usage_facts.py `record_meter`)."""

    id: int
    month: str
    as_of: str
    total_nano: int
    read_at: str


class BudgetNodeRow(TypedDict):
    """One `budget_nodes` row (SELECT *): a money limit, its amount, period and scope (backend/usage_limits.py)."""

    id: str
    scope: str
    team_id: str | None
    bot_id: str | None
    per_bot: int
    period: str
    amount_nano: int
    created_at: str
    updated_at: str
    updated_by_id: str | None


TABLES = {
    UsageFactRow: "usage_facts",
    UsageMeterReadRow: "usage_meter_reads",
    BudgetNodeRow: "budget_nodes",
}
