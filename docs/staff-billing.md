# Manage a company's billing (staff)

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

### Hosting grace period (hours)

A whole number of hours. The default is 72. Enter 0 and the company's servers stop at once when
the balance reaches the minimum.

The stop time counts from when the balance first reached the minimum. Changing the grace period
recomputes that stop time from the same starting point.

### Enforcement

Pick **Record only** or **Enforce**.

**Record only** is the default for every company: usage and balance are recorded and shown, and
nothing stops. Tico Team's own install always stays **Record only**. With **Enforce**, the company's
Tico models stop at the minimum balance and its Hosting servers stop once the grace period ends.

## Settings history

Every change needs a reason, and every change is listed under **Settings history** with the old
value, the new value, who made it, and when.

## If HQ does not answer

If HQ does not answer, the page shows **Send again** at the top with the same change. It is safe to
select it: HQ makes the credit or change at most once.

A company with no billing account yet shows only the Add credit and Change settings forms. Adding a
credit or changing a setting opens the account with HQ's defaults: minimum $0.00, Hosting grace
period 72 hours, Record only.
