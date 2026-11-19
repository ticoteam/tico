---
name: quick-perf-check
description: "For a change that adds or changes SQL (a query, a schema, a migration or an index): get each query's plan, time it before and after on a realistic copy of the data, scan for N+1 queries, and post those results on the task before the change merges. Skip any change that touches no SQL."
metadata:
  function: engineering
---
# Quick perf check

When to use: a change that adds or changes SQL. For any other change, skip this skill.

Steps:
1. List each new or changed query, with the file and function that runs it.
2. Get its plan: `EXPLAIN QUERY PLAN <query>` on SQLite, `EXPLAIN <query>` on Postgres or MySQL. Note every full table scan.
3. Run the query on a copy of the data as close to real as you can get. Say which copy, and how many rows it has.
4. Time the old query and the new one on that copy, the same way: three runs each, and write down both times.
5. Scan for N+1 patterns: a query run inside a loop over rows, or one lookup per item in a list. Fold it into one query or a join, then time that too.
6. Before the change merges, post on the task: each query, its plan, its old and new time, and any N+1 found and fixed. With no real copy of the data, post that instead of a timing.

Checks before you finish:
- Every number is from the head commit you post on the task.
- A full scan of a large table is named, with the reason it stays or what replaces it.
