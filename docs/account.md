# Your Tico account page

**Unreleased:** this help covers the account page, waitlist approval, credit notices, and Hosting grace notices.
HQ deployment, the separate staff admin installation, and live acceptance are not confirmed.
Hosting billing activation remains unverified.

[app.tico.dev](https://app.tico.dev/) is your company’s account page, hosted by Tico on HQ.
It stays available when your Tico server or bot computer stops.
Sign in with your Tico account and select your company from the **Company** menu.
The page shows only companies you belong to.

## Before approval

While you wait, the page shows **You’re on the waitlist**.
If staff decline your sign-up, it shows **We can’t offer a spot right now**.
See [Joining the Tico waitlist](waitlist.md).

## Your starting credit and what happens at $0

**Intended policy — unreleased:** waitlist approval commits one starting-credit grant before setup starts.
It is normally **$20.00**. Staff can approve a different amount. It pays for Hosting and Tico models.
After the approval and grant commit succeeds, setup provisions two **Starter** servers: your Tico server and your bot computer.
It also issues your Tico models key. A failed approval or grant commit must not start setup.

Failed setup keeps the original grant. Repeated approval and interrupted or concurrent setup retries preserve its amount,
approver, original approval time, and expiry. They do not grant again or reset the 30 days.

**Known runtime gap — unimplemented:** the current runtime grants credit after the Tico server installs, rather than on approval.
It can still show no credit before installation. The intended timing is not implemented or confirmed released by this help update.

Your starting grant expires **30 days after approval**, at the UTC expiry shown on your account page.
Setup retries keep that original expiry. They do not start another 30 days.
For example, approval at 14:30 UTC on October 23 gives an expiry of 14:30 UTC on November 22.
Unused credit expires by its remaining amount: if $6 is left, expiry lowers the balance by $6.
An expiry that takes the balance to $0 triggers the normal pause below.

Waitlist approval sets billing to **Enforce**, with a **$0 minimum balance** and **0-hour Hosting grace**.
At exactly $0, the company is paused. A balance below $0 is paused too.

| Service | What happens at $0 under these rules |
|---|---|
| Tico models | The Gateway refuses calls within five minutes, normally on the next call. |
| Tico server and bot computer | Both stop within five minutes, with no Hosting grace. Your Tico goes dark. |
| Your account page | [app.tico.dev](https://app.tico.dev/) stays available to check credit and ask for more. |
| Your data | Kept for at least 30 days. Running out of credit does not delete it. |

The Gateway uses a balance cached for at most five minutes and subtracts each process’s own call costs between refreshes.
It refuses the next call when that cached balance is at or below the minimum.
Other spending can take up to five minutes to reach that cache, allowing up to five minutes of extra usage.
Hourly model debits record usage; they are not the pause-check interval.
See [Gateway credit rules](ai-gateway.md#credit-and-the-minimum-balance).

Adding credit must cover anything owed and leave the balance above $0.
Models resume within five minutes. Both servers must also pass the separate [restart check](#more-credit-and-restarting).
A positive balance can restore model access while both servers remain stopped.

## Balance and credit

After approval, **Account** shows your balance, remaining credit, and when the credit expires.
The current page may show **No credit yet** before installation because of the grant-timing gap above.
That is an implementation limitation; the intended policy grants credit on approval, before setup.

- **Balance** is the live balance, including Tico models usage not yet recorded in an hourly debit.
- **Credit** shows the unused amount out of the original grant. The page shows up to three current credits.
- A credit labelled **ends end of YYYY-MM-DD (UTC)** includes that whole UTC day.
  For example, credit ending on October 31 expires at 00:00 UTC on November 1.
  If an expiry includes a time, that UTC time is the expiry.

The live balance can fall before the usage tables update. Credit remaining and balance can therefore differ.
Expired unused credit no longer counts toward the balance.

## Credit notices

Strictly below the warning threshold, the page shows **$X of credit left; things stop at $0**.
The threshold defaults to **$20 USD**, so the warning appears immediately after the first spend from a $20 grant.
Staff can change the threshold with a required reason; each change is kept in its history.
The warning email sends once per below-threshold episode.
Repeated checks and partial top-ups that leave the balance below the threshold send no additional warning.
Recovery to the threshold or above clears the warning banner and staff **Low** flag, and re-arms the email.
A later drop strictly below the threshold sends one new warning.
For example, after a warning at $19, a top-up to $19.50 does not re-arm it. Recovery to exactly $20 does.
If spending takes you straight to paused before a warning is sent, you get only the run-out email.

At the minimum, **Paused: out of credit** shows the reported stop times for the Gateway key and both servers.
**Not reported yet** means that service’s stop time is not available yet.
For zero-grace companies, the owner gets one run-out email for that pause, saying the bots and server are paused and the data is safe.
It includes an **Ask for more credit** link.
Companies with positive Hosting grace use the [grace emails below](#hosting-grace-and-retained-data).
Waitlist credit notices do not create a card inside your Tico.
A Gateway recovery alone does not clear **Paused: out of credit** while billing-stopped servers remain stopped.
Both pages show the Gateway’s reported start time alongside the servers’ stop times during that recovery.
After recovery, **Restarted** shows the restart time and the individual service start times reported so far.

The staff admin page shows the same pause and recovery information, plus a **Low** flag below the threshold.
**Record only** companies can show low-balance information, but receive no credit-notice emails or restart-credit quote.

These notices describe the merged update. Delivered emails and live status changes remain unverified.

## Usage

The account page and staff admin page use the same company billing records.

| Section | Shows |
|---|---|
| **Tico models today, by hour (UTC)** | Today’s recorded model charges, grouped by UTC hour. |
| **Servers this month, by day (UTC)** | This month’s recorded server charges, grouped by UTC day. |

An empty section says **No usage yet**.
The server table’s daily plan rate is separate from these recorded charges.

Hosting records one charge per running server per UTC day, at the daily run at 00:05 UTC.
Each server has its own charge. Restarting on a day already charged does not add a second charge for that day.

## Servers

**Servers** lists your **Tico server**, which runs your Tico, and your **Bot computer**, which runs your bots.
Each row shows its address or label, **State**, and **Price**.

| State | Price shown | Open your Tico |
|---|---|---|
| **Waiting**, **Creating**, or **Installing** | Advertised daily plan rate. This does not confirm a running server or a debit. | Unavailable. |
| **Ready** and running | Daily plan rate. | Available on the Tico server row only. |
| **Stopped**, **Failed**, or **Ended** | A dash, with no running price. Earlier charges remain in usage. | Unavailable. |

## Sleep when idle

On a server Tico hosts, an owner or an admin can have Tico stop the server after 1 to 24 quiet hours (2 is a good start)
and stop the bot computers with it (**Stop bot computers too**, on by default). Set it in Settings > Computers, or with
`hub sleep set --hours 2` and `hub sleep status`; `hours` 0 turns it off. A self-run server never shows the setting.

The server is quiet when, for all of those hours, nobody used the app signed in and no run started or ended, and right
now nothing is queued or running, no computer is working on a job, nothing is being sent or due to be retried within
15 minutes (Slack posts, outgoing webhooks) and no routine, daily system job or release hour is due in the next 15 minutes.
Jobs that repeat every few hours or less are housekeeping that catches up after a wake and never keep it up.
Tico then records "server.sleep" in History and asks to stop the
server, passing the time of the next routine, daily job, release hour or send retry as its wake-up time.

A sleeping server starts again when a webhook arrives at an address through Tico Gateway (Settings > Webhooks >
Through Tico Gateway), when that routine time comes, or when someone signs in at your account page. A server Tico stopped
because the balance ran out never starts on its own, and webhooks sent to it wait at Tico Gateway until it runs again.
If the stop request fails, Health says so and the server keeps running.

## Hosting grace and retained data

**Unreleased:** Hosting billing remains off. Stop-time change emails and renewed reminders have passed source review and synthetic QA.
The original notices and stop-time changes are merged and automatically deployed to HQ. Billing activation and live acceptance remain unverified.

The default Hosting grace period is 72 hours. Tico staff can set a different period for your company.
In **Enforce** mode with a positive grace period, grace starts when your balance reaches or falls below the staff-set minimum.
Exactly the minimum still counts as paused.

Adding credit ends grace only when the balance rises above the minimum.
A partial credit that leaves the balance at or below the minimum keeps the original stop time.
For example, with a $0 minimum, raising a balance from −$5 to $0 leaves grace running.
Raising it to $0.01 ends grace.

If staff raise the minimum to your balance or above it, grace starts when they save the change.
Lowering the minimum below your balance ends grace, as enough credit does.

Changing the grace period keeps its original starting point.
If staff shorten it past the time already elapsed, servers stop at the next daily run at 00:05 UTC.
For example, changing a 72-hour grace to 48 hours after 50 hours schedules that stop for the next daily run.
The change email names that next 00:05 UTC run, not the already-passed deadline.

During grace, the company owner sees one **Needs you: add credit** card on this account page.
It shows the stop time in UTC, says **Your data is kept**, and directs you to **Ask for more credit** below.
The card appears only for the owner, not other company members, and stays on the account page outside your Tico.
Refreshing the page does not create another card.

A real stop-time change updates this same card and sends one **Stop time changed** email to the current owner.
This applies to earlier and later moves, before or after a reminder has been sent.
An edit that leaves the effective stop time unchanged sends nothing.
The card disappears when your balance rises above the minimum or the stop completes.
Credit or a lower minimum that ends grace sends no stop-time change email.
Use [Ask for more credit](ask-for-credit.md) to request credit from staff. The card does not take a card payment.

The grace emails go to the current company owner, using the account page’s company membership and profile email.
They do not rely on the original sign-up email.

| Email | When it is sent | What it tells you |
|---|---|---|
| Grace started | When grace starts. | Your servers keep running until the UTC stop time unless credit is added. Your data is kept. |
| Stop time changed | After a real stop-time move during grace. | The new UTC stop time and how to sign in to request credit. |
| Reminder | 24 hours before the current stop, if it was more than 24 hours away when scheduled. | The stop time and how to sign in to request credit. |
| Servers stopped | After the stop completes. | The actual successful stop time and confirmation that your data is kept. |

After a change, a stop more than 24 hours away gets one reminder at the new stop time minus 24 hours.
If the new stop is 24 hours away or less, the change email replaces the reminder.
A reminder is sent at most once per stop time within the same grace period.
Returning to an already-reminded stop time does not send another reminder for it.
An initial grace of 24 hours or less has no separate reminder.

The card and the start or change email must show the same current stop time.
A complete start or change notice requires both the matching owner-only card and an accepted email.
A failed or partial delivery is recorded as unsent and is not automatically retried.
Check this account page for the current deadline.

Zero-hour companies, including waitlist companies, receive no grace card or grace emails.
Their separate [credit notices](#credit-notices) provide the one run-out email.
**Record only** companies receive no grace notices.
Delivered emails and live account-page behaviour remain unverified.

When grace expires, Tico stops the servers and keeps their data. It does not delete them for running out of credit.
Waitlist accounts have zero grace: both servers stop within five minutes of reaching the minimum.
Your account page stays available while your Tico server and bot computer are stopped.

## More credit and restarting

[Ask for more credit](ask-for-credit.md) sends a request to Tico staff from this account page.
You can use it while your servers are stopped.

After staff add enough credit to cover model usage and bring the balance above the minimum, Tico models resume within five minutes.
Server restart follows a separate check.

Server restart requires enough credit to cover today’s uncharged server costs and keep the live balance above the minimum.
Amounts already owed must also be covered. A positive balance alone may not cover a new UTC day’s server costs.

While you are short, the account page and staff admin page show the same **Add at least $X to restart your servers** amount.
It covers today’s uncharged server costs and anything owed, leaving at least one cent above the minimum.
The amount to add rounds up to a whole cent.
For example, with a $0 minimum, $4 of uncharged server costs and a −$0.005 live balance, the quote is **$4.02**.
When enough credit is available, the quote disappears; that alone does not confirm a server restart.
Check the servers’ reported states and start times.

| Account | Restart rule for the billing update |
|---|---|
| Waitlist account with zero grace | Both servers restart automatically at the next billing run, normally within a minute, once the balance covers today’s uncharged costs for both. It must stay above the minimum. If either fails this check, neither restarts. |
| Account with a positive grace period | Stopped servers need a manual owner restart. Each server must pass the same balance and daily-cost check. This owner-start path is still being completed. |

For example, credit added on the same UTC day can reuse server charges already paid for that day.
After a new UTC day starts, the restart check includes that day’s uncharged server costs.
This check does not change the separate model-recovery rule above.

The intended manual-start safeguard refuses starts while paused or short of today’s server costs.
Its owner control is unfinished, so this help does not provide a start procedure.

**Unreleased dependency:** the manual owner-start safeguard remains separate work.
The billing and account-page merges do not confirm their availability or enable billing.

*Sources checked 2026-10-23: ticoteam/tico-services#127, portal/views.py (account, _ends, _usage), portal/account.py (summary), and hosting/preview.py (company_servers); spec v3.4 sections 2–3 and Answers 14–15. Credit notices are covered below from services#130. Deployment and live sign-in not verified.*

*Hosting rules checked 2026-10-23: ticoteam/tico-services#138; Billing system spec v1.1, boundary rule, section 2, and tests 7–8 and 19–23; billing database design, Option A and Settled; waitlist spec v3.4, section 2 and Answers 15–16. Manual owner-start dependency: Dev 430. Grace notices are covered below from services#147. Billing remains off; deployment and live power actions not verified.*

*Starting credit and notices checked 2026-10-23: waitlist spec v3.4 §§2–3 and Answers 14–16; services#132, hosting/approve.py (CREDIT_DAYS, approve, on_installed, _grant, SignupSetup, _send); Engineering’s accepted receipt ordering on Dev 344; services#130, portal/views.py (credit_notice). Restart quote and partial-recovery status: services#142, billing/hosting.py (to_restart, _short), billing/credit_notices.py (status, _to_restart), portal/views.py (credit_notice); admin#18/#21, admin/tico_admin/credit_notices.py. Both follow-ups merged; installation and production acceptance remain unverified. No live grant, email, server power action, or deployment verified.*

*Grace notices checked 2026-10-23: [ticoteam/tico-services#147](https://github.com/ticoteam/tico-services/pull/147), billing/grace_notices.py (status, __call__), billing/hosting.py (grace_text, _grace_period, _grace_row), billing/credit_notices.py (OWNER_EMAIL, status), portal/account.py (summary), and portal/views.py (account); Billing system spec v1.1, tests 7 and 19. Synthetic receipts only; HQ deployment, daily billing activation, live card rendering, and email delivery not verified.*

*Stop-time changes checked 2026-10-23: [ticoteam/tico-services#154](https://github.com/ticoteam/tico-services/pull/154), source a4a34d49, billing/hosting.py (_grace_period, _scheduled_at, _has, grace_text) and billing/grace_notices.py (__call__); Billing system spec v1.2, tests 19, 21, and 24. Both serial reviews and independent QA passed, including tests 7, 19, 21, and 24. Synthetic evidence only; merge, deployment, activation, live email, and power actions not confirmed.*

*Policy correction, 2026-10-24: Product waitlist spec v3.5, section 2, Permissions and errors, tests 4 and 7, and Answer 17. This supersedes the earlier install-time grant ordering. Current hosting/approve.py (approve, on_installed, _grant) still grants after installation; the runtime correction remains unimplemented. No live recovery or backfill is authorized.*

*Warning boundaries clarified 2026-10-24: Product waitlist spec v3.5, section 2 step 9a, tests 14–15, and Answer 18. One warning per strictly-below-threshold episode; recovery at or above the threshold re-arms it. Live email delivery is not verified.*

*Delivery update, 2026-10-24: services#147 and services#154 are merged and their automatic HQ deployments are verified. Test 24 and funded-grace source QA prerequisites have passed. Separate staff installation, live card/email/power acceptance, and explicit activation authorization remain; Hosting billing stays off.*
