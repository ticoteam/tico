# Your Tico account page

**Unreleased:** this help describes the merged account-page update. Deployment is still being verified.

[app.tico.dev](https://app.tico.dev/) is your company’s account page, hosted by Tico on HQ.
It stays available when your Tico server or bot computer stops.
Sign in with your Tico account and select your company from the **Company** menu.
The page shows only companies you belong to.

## Before approval

While you wait, the page shows **You’re on the waitlist**.
If staff decline your sign-up, it shows **We can’t offer a spot right now**.
See [Joining the Tico waitlist](waitlist.md).

## Balance and credit

After approval, **Account** shows your balance, remaining credit, and when the credit expires.
If billing is still being set up, it shows **No credit yet**.

- **Balance** is the live balance, including Tico models usage not yet recorded in an hourly debit.
- **Credit** shows the unused amount out of the original grant. The page shows up to three current credits.
- A credit labelled **ends end of YYYY-MM-DD (UTC)** includes that whole UTC day.
  For example, credit ending on October 31 expires at 00:00 UTC on November 1.
  If an expiry includes a time, that UTC time is the expiry.

The live balance can fall before the usage tables update. Credit remaining and balance can therefore differ.
Expired unused credit no longer counts toward the balance.

## Usage

The account page and staff admin page use the same company billing records.

| Section | Shows |
|---|---|
| **Tico models today, by hour (UTC)** | Today’s recorded model charges, grouped by UTC hour. |
| **Servers this month, by day (UTC)** | This month’s recorded server charges, grouped by UTC day. |

An empty section says **No usage yet**.
The server table’s daily plan rate is separate from these recorded charges.

## Servers

**Servers** lists your **Tico server**, which runs your Tico, and your **Bot computer**, which runs your bots.
Each row shows its address or label, **State**, and **Price**.

| State | Price shown | Open your Tico |
|---|---|---|
| **Waiting**, **Creating**, or **Installing** | Advertised daily plan rate. This does not confirm a running server or a debit. | Unavailable. |
| **Ready** and running | Daily plan rate. | Available on the Tico server row only. |
| **Stopped**, **Failed**, or **Ended** | A dash, with no running price. Earlier charges remain in usage. | Unavailable. |

## More credit and restarting

[Ask for more credit](ask-for-credit.md) sends a request to Tico staff from this account page.
You can use it while your servers are stopped.

After staff add enough credit to cover model usage and bring the balance above the minimum, Tico models resume within five minutes.
Server restart is separate: a positive balance alone does not guarantee that your servers can restart.

Server restart requires enough credit for today’s uncharged server costs while keeping the balance above the minimum.
For waitlist accounts, both servers must qualify before either restarts automatically.

**Unreleased dependencies:** server restart rules, low-balance and paused banners, stop times, and restart-shortfall amounts are separate updates still under review.
Their availability is not confirmed by the account-page merge.

*Sources checked 2026-10-23: ticoteam/tico-services#127, portal/views.py (account, _ends, _usage), portal/account.py (summary), and hosting/preview.py (company_servers); spec v3.4 sections 2–3 and Answers 14–15. Banner dependency: Credit notices (Dev 346). Restart dependency: Billing 4/5. Deployment and live sign-in not verified.*
