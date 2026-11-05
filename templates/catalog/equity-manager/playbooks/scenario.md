# Round scenario

Triggered by a task asking what a raise would do to ownership. Budget 15 minutes. The outcome is a scenario table the human can discuss. It is never a recommendation on terms.

**Inputs:** the raise, the pre-money value or range, the pool top-up and whether it sits pre- or post-money, and `knowledge/cap-table.md`. If the placement is not given, show both placements for the first variant.

**Output:** the assumptions, one table per variant (three at most), and the checks from the skill.

**Done when:** every table sums to 100.0%, each is labelled a draft, and the task is marked done with the tables attached.

---

1. Read the inputs and write down anything missing.
2. Build each variant with `skills/build-a-round-scenario/SKILL.md`.
3. Hand over. Terms and decisions belong to the human and their lawyer.
4. Publish the report with `hub file publish`, then `hub task update <id> --status done --note`.
