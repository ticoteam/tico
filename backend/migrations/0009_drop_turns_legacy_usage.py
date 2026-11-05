"""A run's one-number tokens_in and tokens_out and its cost, kept before measured usage, move into input_tokens,
output_tokens and est_cost_usd where those are empty (the readers already fell back to them); then the three columns
go. A run with measured usage keeps its own numbers. A new file's turns table never had them."""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(turns)")}
    if "tokens_in" in columns:
        conn.execute("UPDATE turns SET input_tokens=tokens_in WHERE input_tokens IS NULL AND cached_tokens IS NULL "
                     "AND tokens_in IS NOT NULL")
    if "tokens_out" in columns:
        conn.execute("UPDATE turns SET output_tokens=tokens_out WHERE output_tokens IS NULL AND tokens_out IS NOT NULL")
    if "cost" in columns:
        conn.execute("UPDATE turns SET est_cost_usd=cost WHERE est_cost_usd IS NULL AND cost IS NOT NULL")
    for column in ("tokens_in", "tokens_out", "cost"):
        if column in columns:
            conn.execute(f"ALTER TABLE turns DROP COLUMN {column}")
