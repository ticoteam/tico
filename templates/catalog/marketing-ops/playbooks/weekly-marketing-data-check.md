# Weekly marketing data check

Schedule: Mondays at 10:00 team time (routine `weekly-marketing-data-check`), after setup. Also run by hand. Budget 35 minutes. The outcome is one page: tagging,
sources and handoff, each with the fixes and their owners. Nothing in any system is changed.

---

## 1. Read where things stand

    hub task show <id>
    hub goal list --all

Then `knowledge/tracking.md`, `knowledge/handoff.md`, `knowledge/campaigns.md` and last week's check.
Check whether last week's fixes were made.

## 2. Tagging

Read `skills/check-campaign-tagging/SKILL.md` first. From last week's traffic-by-source export: values that break the convention (capitals, spaces, a
medium the analytics tool does not recognise, the same source spelled two ways), campaign traffic
landing as "direct" or "unassigned" where a live campaign should have tagged it, and any parameter
holding a name or an email address (a privacy problem: first line of the report). Map each error to
the campaign and owner in `knowledge/campaigns.md`.

## 3. Sources

From the CRM (read only) or the lead export: new leads last week, the share with an original source,
and the forms or imports that produced the ones without. A form with no hidden source field is a fix.

## 4. Handoff

Read `skills/measure-lead-handoff-delay/SKILL.md` first. For leads that met the ready-for-sales rule last week: time from ready to first sales touch, median
and slowest, against the target in `knowledge/handoff.md`; leads still untouched; leads handed to
nobody. Report the queue, never a seller's name.

## 5. Write and hand over

Write `reports/YYYY-MM-DD-marketing-data.md` in the shape of `knowledge/examples/marketing-data-check.md`:
the three shares first, then fixes (what, where, should be, owner), then the four-week trend. Then
`hub file publish reports/YYYY-MM-DD-marketing-data.md`. Ask once with `hub task ask <id>` whether to
create the fix tasks. Commit, and `hub task update <id> --status done --note`.

Done when: the report is published with the three shares, every error has an owner, the four-week trend is in,
and each source you could not read is named as unknown.
