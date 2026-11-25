---
name: DuckDB
category: data
summary: "Fast SQL over CSV, JSON and Parquet files without loading them first"
commands: [duckdb]
---
## What it is for
Analysing exports and logs: joins, group-bys and window functions straight over files.

## How to use it

    duckdb -c "select date_trunc('week', created_at) w, sum(amount) from 'payments.csv' group by 1 order by 1"
    duckdb -csv -c "select * from read_json_auto('events.json') limit 20"

## Good to know
- Prefer it to reading a large file into your context: the answer is exact and the file stays out of the chat.
