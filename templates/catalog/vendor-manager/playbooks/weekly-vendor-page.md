# Weekly vendor and renewals page

Schedule: Tuesdays at 09:00 team time (routine `weekly-vendor-page`), after setup. Budget 30 minutes. Nothing is sent,
renewed or cancelled by this page.

Inputs: `knowledge/vendors.md`, `knowledge/renewal-practice.md`, open and waiting tasks that name a vendor, new contracts
in `hub doc search "contract"`, and, where a mailbox is connected, renewal notices and first invoices from unknown
senders.

Outcome: `reports/YYYY-MM-DD-vendors.md` in the shape of `knowledge/examples/vendor-page.md`: headline, urgent renewals,
open renewals with a one-line call each, reviews due, gaps, and register changes.

Done when: every contract in the register has a notice deadline with its clause, or "not on file"; every renewal inside
90 days has a call; every gap has a line; the page is published with `hub file publish`; and the task note carries the
headline and the counts.

---

## 1. Refresh the register

    hub task show <id>
    hub task list --status open --status waiting

Read new tasks tagged with a vendor name, new contracts since last week, and, where a mailbox is connected, renewal
notices and first invoices from unknown senders. A vendor that is paid but not in the register is added with "found via
invoice <date>" and no owner.

## 2. Compute notice dates

Use `skills/calculate-a-notice-deadline/SKILL.md` for each contract. Sort by notice deadline, not end date. Mark each:
**open now** (inside the window, default 90 days), **urgent** (inside 30 days), or **passed** (the contract has renewed;
say so, with the new end date if the contract gives it).

## 3. Reviews due

Tier 1 vendors with no review in 90 days, and tier 2 vendors with none in a year. List each with its owner and the last
issue on file: missed deliveries, outages, support tickets, invoice disputes.

## 4. Gaps

Vendors with no owner, no contract on file, no tier, or holding team data with no security review on file. One line
each. Where `security-compliance` keeps a review list, check it there.

## 5. Write the page

Write `reports/YYYY-MM-DD-vendors.md` in the shape of `knowledge/examples/vendor-page.md`. Each renewal over the
always-show amount in `knowledge/vendors.md` (default 5,000 a year) gets its full brief through
`playbooks/renewal-brief.md`. Then publish it:

    hub file publish reports/YYYY-MM-DD-vendors.md

## 6. Finish

Commit, then `hub task update <id> --status done --note` with the headline and the counts. A quiet week is one line, and
the task is still finished.
