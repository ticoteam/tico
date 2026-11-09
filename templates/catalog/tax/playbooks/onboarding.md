# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 30 minutes. The outcome is five recorded answers, the next 90 days of the tax
calendar, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>

Check the attachments for last year's returns, a list of registrations, sales by state or country, a
contractor payment list. Do not ask for what these already show.

## 2. Introduce yourself in three lines

What you do (the tax calendar with its inputs, a registration threshold watch, the accountant's
document lists), that you never file, pay or register, and that nothing you write is tax advice.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Who prepares and files your taxes, and which taxes does each one file?
2. Where are you registered for income, payroll and sales tax or VAT? Attach last year's returns list.
3. Where do you sell and ship to? Attach the last twelve months of sales by state or country.
4. Do you pay contractors, and where are their payments and tax forms kept?
5. How many days before a deadline does your accountant need the inputs? (Default: 15.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Build `knowledge/tax-calendar.md` from the
registrations and the official calendars (for US federal dates, the IRS tax calendar; for each state or
country, its revenue department's page), each date with its source and the date you checked. Build
`knowledge/thresholds.md` for each place the team sells into.

## 5. Produce the first result now

Follow `playbooks/monthly-tax-calendar.md`. Write `reports/YYYY-MM-tax-calendar.md`, attach it to the
task and label it "First draft, not yet reviewed". Contact nobody.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will refresh the calendar on the 1st of each month." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
