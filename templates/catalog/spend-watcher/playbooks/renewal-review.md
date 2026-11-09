# Renewal review

Triggered by a task asking "should we renew X?" or by a renewal inside 60 days in
`knowledge/renewals.md`. Budget 25 minutes. The outcome is a one-page brief a human decides from.
You recommend nothing you cannot source, and you never act on the renewal.

Inputs: the vendor line in `knowledge/vendors.md`, the contract or invoice if attached, the last two reports' mentions of the vendor, and the exports for the last 12 months.

Done when: the brief is attached with the decide-by date first, every fact has its source or is marked not measured, and the task is closed with the suggestion.

---

## 1. Read what you have

    hub task show <id>

Read the vendor's line in `knowledge/vendors.md`, the contract or invoice if attached (price, term,
seats, notice period, auto-renewal clause) and the last two reports' mentions of it.

## 2. Gather the facts

- **Cost**: what was paid for the last 12 months (export rows), and the renewal price if stated,
  with the date and source. A price rise is the percent and the source line.
- **Usage**: seats billed against seats active, with dated usage lines; features in use if a human
  gave them. No usage data means "not measured", with what would measure it.
- **Owner and need**: who uses it and what breaks if it is gone; ask the owner through the task,
  never by messaging them.
- **Alternatives**: overlaps in `knowledge/vendors.md`. A real comparison is the Procurement Manager's:
  `hub task create --owner procurement`.
- **The clock**: renewal date, notice period and the decide-by date, in the first line.

## 3. Write the brief

Decide-by date and cost first. Then keep, reduce or drop as a suggestion, labelled "suggestion, not a
decision", with the two facts that support it. Then the questions only the owner can answer. Give
negotiation angles only from sourced facts: vendor fiscal year end if the human told you, seats you can
show are idle, a multi-year offer already in the paper.

## 4. Finish

Attach the brief, then `hub task update <id> --status done --note`: the decide-by date, the suggestion, and
what you could not read. The human cancels, renegotiates or renews; you never contact the vendor.
When the notice date has passed, say so in the first line.
