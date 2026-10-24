# Manage a company's billing (staff)

**Unreleased Hosting update:** billing remains off. The Hosting rules below do not confirm deployment or activation.

Only Tico staff see this page. Open **Customers** on the admin page, pick the company, then open **Billing**.
The page sits behind Cloudflare Access, so customers never reach it, and HQ only takes these changes with
the staff billing key. Customers cannot change their own minimum balance, grace period, or enforcement.

The **Account** panel shows the balance, the status, enforcement, the minimum balance and the Hosting grace
period. The company is **Active** only while its balance is above its minimum. At or below the minimum it is
**Paused**: a balance of $0.00 with a $0 minimum is paused. Below the panel are the settings history, credits,
the month's debits, usage by day and the month's statement, the same figures the company sees.

The balance here is the billed balance. Usage from the current hour is not billed yet, so right after a
company is paused the Gateway can keep refusing calls until a credit also covers that unbilled usage.

## Add credit

In the **Add credit** form:

1. Enter the **Amount (dollars)**, like 10, 25.50 or 0.01. It must be more than $0.
2. Pick the **Type**: **Grant**, or **Credit by arrangement** for credit the company pays for
   by arrangement (no card payment runs).
3. Optionally set **Ends**. The credit counts to the end of that day (UTC).
4. Enter the **Reason**. It is required.
5. Select **Add credit**.

The balance goes up by the amount. Sending the same form twice by accident still makes one credit.

## Reverse a debit

Each debit in **Debits in** the month that isn't reversed yet has its own Reverse form:

1. Enter the **Reason** next to the debit. It is required.
2. Select **Reverse**.

The original stays listed, marked **Reversed**, and the balance goes back up by that amount.
Nothing on this page is ever edited or deleted: every fix is a new row.

## Change settings

The **Change settings** form holds all three settings, filled in with the current values. Change the
ones you need, enter the **Reason** (required), and select **Change settings**. Each change is saved as a
new settings row.

### Minimum balance

Enter dollars. It may be negative: a −$50 minimum lets the company keep working until the balance
reaches −$50.

Raising the minimum to the balance or above pauses the company. In **Enforce** mode its Tico models
stop within 5 minutes. Lowering the minimum below the balance, or adding credit so the balance ends
above the minimum, lets them start again within 5 minutes.

For Hosting in **Enforce** mode, raising the minimum to the balance or above starts grace when you save the change.
Lowering it below the balance ends grace. Credit ends grace only when the balance is above the minimum.

### Hosting grace period (hours)

A whole number of hours. The default is 72. Zero means no grace; waitlist servers stop within five minutes of reaching the minimum.

The stop time counts from when the balance first reached the minimum. Changing the grace period
recomputes that stop time from the same starting point.
If the new deadline has already passed, servers stop at the next daily run at 00:05 UTC, rather than when you save.
For example, shortening a 72-hour grace to 48 hours after 50 hours schedules the stop for that next daily run.

Stops retain the servers' data. Grace notices are still pending: the design includes the stop time at grace start,
a reminder 24 hours before stop, and a stop email. Delivered notices and **Needs you** items are not yet confirmed.

For the daily charge and restart rules, see [Hosting grace and retained data](account.md#hosting-grace-and-retained-data)
and [More credit and restarting](account.md#more-credit-and-restarting).
Positive-grace accounts require manual owner restart; the guarded owner-start path is unfinished.
Zero-grace waitlist recovery instead checks both servers together before automatically restarting either.

### Enforcement

Pick **Record only** or **Enforce**.

**Record only** is the default for every company: usage and balance are recorded and shown, and
nothing stops. Tico Team's own install always stays **Record only**. With **Enforce**, the company's
Tico models stop at the minimum balance and its Hosting servers stop once the grace period ends.

## Credit notices

Open **Credit notices** in the staff admin navigation, then select the company.
The **Credit notices** panel shows **Live balance**, **Low-balance threshold**, and the **Low** flag strictly below that threshold.
The default is **$20 USD**: the first spend from a $20 grant triggers the low warning.
The owner receives one low email per below-threshold episode.
Repeated checks and partial top-ups that stay below the threshold send no additional warning.
Recovery to the threshold or above clears **Low** and the account-page warning banner, and re-arms the email.
A later drop strictly below the threshold sends one new warning.
For example, after a warning at $19, $19.50 remains low; exactly $20 clears it and re-arms the next warning.

To change the warning threshold:

1. Enter **Low-balance threshold (dollars)** under **Change low-balance threshold**, for example `10`.
2. Enter the required **Reason**.
3. Select **Change threshold**.

The new threshold applies to that company. **Low-balance threshold history** keeps the amount, who changed it, when, and the reason.
Changing this warning threshold does not change the minimum balance that pauses services.

**Paused: out of credit** shows separate reported stop times for the Gateway key, Tico server, and bot computer.
**Not reported yet** means a service time is unavailable.
For a zero-grace waitlist pause, the owner receives one run-out email saying the data is safe, with an **Ask for more credit** link.
These credit notices create no card inside the customer’s Tico. Their account page stays available while the servers are stopped.

Both pages show the same rounded-up-cent **Add at least $X to restart your servers** quote while credit is short.
It covers today’s uncharged server costs and anything owed, leaving at least one cent above the minimum.
See [More credit and restarting](account.md#more-credit-and-restarting) for an example and the billing-loop restart rules.
Gateway recovery alone leaves the server pause visible, with the Gateway start time alongside the servers’ stop times.
After funding covers the quote, the amount disappears; this does not confirm an actual restart.
Check service states and reported start times.
After recovery, **Restarted at** and each service’s reported start time show the recorded recovery.

**Record only** can show **Low**, but sends no credit-notice emails and shows no restart-credit quote.
Positive-grace companies keep their separate Hosting grace notices and manual owner-start policy.
The **Credit-notice emails** section records each credit-notice email’s result; **Email not sent** does not confirm delivery.

## Settings history

Every change needs a reason, and every change is listed under **Settings history** with the old
value, the new value, who made it, and when.

## If HQ does not answer

If HQ does not answer, the page shows **Send again** at the top with the same change. It is safe to
select it: HQ makes the credit or change at most once.

A company with no billing account yet shows only the Add credit and Change settings forms. Adding a
credit or changing a setting opens the account with HQ's defaults: minimum $0.00, Hosting grace
period 72 hours, Record only.

*Hosting changes checked 2026-10-23: ticoteam/tico-services#138; Billing system spec v1.1, boundary rule and tests 7–8 and 19–23; waitlist spec v3.4, Answers 15–16. Owner-start safeguard (Dev 430) and grace notices (Dev 347) remain unfinished. Billing remains off.*

*Credit notices checked 2026-10-23: waitlist spec v3.4 sections 2–3, Answer 15 and test 12g–i; services#130/#142, billing/credit_notices.py, billing/hosting.py (to_restart, _short), portal/views.py (credit_notice); admin#18/#21, admin/tico_admin/credit_notices.py (notices_section, account_page, threshold_history, email_history). Both restart-quote follow-ups are merged. Deployment, admin installation, email delivery and live stop/restart acceptance remain unverified.*

*Warning boundaries clarified 2026-10-24: Product waitlist spec v3.5, section 2 step 9a, tests 14–15, and Answer 18. One warning per strictly-below-threshold episode; recovery at or above the threshold re-arms it. Live email delivery is not verified.*
