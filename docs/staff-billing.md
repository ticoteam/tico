# Manage a company's billing (staff)

Only Tico staff see this page. Open **Admin → Customers**, pick the company, then open **Billing**.
The page sits behind Cloudflare Access, so customers never reach it. Customers cannot change
their own minimum balance, grace period, or enforcement.

The company is **Active** only while its balance sits strictly above its minimum. At or below
the minimum, the company is paused: a balance of $0.00 with a $0 minimum is paused.

## Add credit

1. Select **Add credit**.
2. Enter the **Amount** in dollars.
3. Pick the **Type**: **Grant**, or **Credit by arrangement** for credit a company pays
   by arrangement (no card payment runs).
4. Optionally set an **End date**. The credit counts through the end of that UTC day.
5. Enter the **Reason**. It is required.
6. Select **Send**.

The balance goes up by the amount. Selecting **Send** twice by accident still makes one credit.

## Reverse a debit

1. Find the debit in the list and select **Reverse**.
2. Enter the **Reason**. It is required.
3. Select **Send**.

The original stays listed, marked **Reversed**, and the balance goes back up by that amount.
Nothing on this page is ever edited or deleted: every fix is a new row.

## Change the minimum balance

1. Select **Edit** next to **Minimum balance**.
2. Enter the new amount in dollars. It may be negative: a −$50 minimum lets the company's
   Tico models keep working until the balance reaches −$50.
3. Enter the **Reason**. It is required.
4. Select **Send**.

Raising the minimum above the balance pauses the company. In **Enforce** mode the company's
Tico models stop within about a minute. Lowering the minimum back below the balance, or adding
credit so the balance ends above the minimum, resumes them within about a minute.

## Change the Hosting grace period

1. Select **Edit** next to **Hosting grace period**.
2. Enter the new length in hours. The default is 72. Enter 0 and the company's servers
   stop at once when the balance reaches the minimum.
3. Enter the **Reason**. It is required.
4. Select **Send**.

The stop time counts from when the balance first reached the minimum. Changing the grace
period recomputes that stop time from the same starting point.

## Switch enforcement

1. Select **Edit** next to **Enforcement**.
2. Pick **Record only** or **Enforce**.
3. Enter the **Reason**. It is required.
4. Select **Send**.

**Record only** is the default for every company: usage and balance are recorded and shown,
and nothing stops. Tico Team's own install always stays **Record only**. With **Enforce**,
the company's Tico models stop at the minimum balance and its Hosting servers stop once the
grace period ends.

## Settings history

Every change above needs a reason, and every change is listed under **Settings history**
with the old value, the new value, who made it, and when.

## If HQ does not answer

If HQ does not answer, the page offers **Send again** with the same values filled in.
It is safe to send it again: the credit or change is written once.
