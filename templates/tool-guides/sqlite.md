---
name: SQLite
category: data
summary: "A file database for local data, scratch tables and reports"
commands: [sqlite3]
---
## What it is for
Loading a CSV or JSON export into tables you can query exactly, on your own computer.

## How to use it

    sqlite3 work.db ".import --csv orders.csv orders"
    sqlite3 -header -csv work.db "select status, count(*) from orders group by status"

## Good to know
- Never open Tico's own database; use `tico` for Tico's data.
