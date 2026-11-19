---
name: recover-a-bad-experience
description: Runs service recovery when the team let a customer down. Confirms the failure, apologises at once and specifically when the team really failed (never by reflex), fixes it completely, adds one gesture, follows up, and files the cause. The bot may say yes on its own up to a set limit. Also picks the moments that matter most, measures each, and runs a satisfaction survey after each conversation (NPS at most twice a year) so every low score gets a personal reply within 24 hours. Use it when a task, ticket, review or survey contains a complaint, an angry or upset customer, "this is unacceptable", a late, lost, wrong or damaged order, a wrong charge, an outage or downtime, a missed appointment, a broken promise, a second contact about the same problem, a low survey score, a detractor, a bad public review, a cancellation that blames a failure, or "make this right", "service recovery", "set up NPS" or "set up CSAT". Not for routine questions (answer-a-customer) or legal demands (answer-a-demand-or-complaint).
metadata:
  function: customers
  roles: [support-owner, escalations-manager, reputation-manager, success-owner, returns-and-refunds-specialist, retention-specialist]
  actions: [read, draft, send, spend, change-records]
  review: none
---

# Recover a bad experience

When to use: the team failed a customer, or a customer believes it did. One customer and one failure at a time, or
one incident that hit many customers. Also the setup and weekly running of moments that matter and of surveys.

Words used here: the **team** is the business you work for. The **owner** is the team's owner, or whoever the setup
answers name as the person who decides for this area. A **task** is a unit of work in Tico. An **OK** is a person's
recorded approval of exactly what they were shown. **Tools** are the connected systems you may use. A **page** is an
HTML report you publish with `tico page publish`. The **yes limit** is the most you may give a customer on your own to
put something right. The **fix** restores what the customer should have had. The **gesture** is something extra, given
because they were put out. The **customer record** is the customer's row in the CRM or help desk Tool, or
`knowledge/customers/<customer-key>.md` when no such Tool is connected.

## What great looks like

A customer who was let down ends up saying "they handled that well". Five things get them there, in this order:

1. **A real apology, at once, when the team really failed.** Within the hour of confirming it, naming what went
   wrong in their terms and what it cost them. Apologies are kept for real failures and never used by reflex, so
   that one means something. Before the failure is confirmed, say what you are checking and when you will be back;
   that is respect, not an apology.
2. **A complete fix,** done for them, confirmed as done. A partial fix with a nice note is still a failure.
3. **One gesture beyond the fix,** matched to what they lost. Small and apt beats large and generic.
4. **A follow-up** a few days later to check the fix held.
5. **The cause removed,** so the next customer never meets it.

The person at the front can say yes without asking. A bot that must ask permission to put things right is slow by
design, and slow recovery is failed recovery. Inside the yes limit you act; above it you still do not refuse, you
ask with a recommendation. Only a no needs a person.

Two cautions from the research (see `references/sources.md`). An apology does more than money in the one field
experiment that compared them, so when the team failed, never send compensation in place of an apology. And a good recovery lifts
satisfaction but has not been shown to lift repeat purchase above never having failed, so recovery is a repair, not
a growth tactic. The real win is step 5.

## First time at a company

Read `knowledge/recovery.md`. If it has a "Setup answers" section, skip this part.

If not, send the owner these questions once, in one numbered message (`tico chat ask --to <owner> "..."`), and start
work on the defaults at once. Save the answers, dated, under "Setup answers" in `knowledge/recovery.md`.

1. **What is my yes limit?** Default, in three parts:
   - whatever the written policy or the law owes the customer: no limit, I just do it;
   - beyond that, up to $100 (or the same in the team's currency) per customer per incident, and never more than
     that customer paid in the last 12 months;
   - across all customers, a monthly total up to the recovery budget line in Finance if there is one, else 1% of last
     month's revenue (`tico finance metric revenue`), else $500.
   These are starting numbers for this library, not benchmarks. For comparison only: Ritz-Carlton is widely reported
   to let any employee spend up to $2,000 per guest to solve a problem without asking (secondary sources; not
   verified against a company document).
2. **Which gestures may I use?** Default: a credit on the next bill or order, a free upgrade or extra month, free
   expedited shipping, a replacement plus keep-the-original, a short personal note. Never a discount that only works
   if they spend more.
3. **Who decides above the limit, and who handles the public side?** Default: the owner for both.
4. **Which survey do we run, and when?** Default: a one-question satisfaction survey after each support
   conversation closes. This is the lead customer score. NPS is added only once there are enough customers for 20
   responses a round, and then no more than twice a year per customer. See `references/surveys.md`.
5. **What are the five to ten moments that matter most to our customers?** Default: I propose them from the data
   using `references/moments-that-matter.md` and you correct the list.

## Steps

### A. One customer, one failure

1. **Spot it, including when nobody complained.** Recovery starts from any of these:
   - a complaint, in any channel;
   - a failure you can see in a Tool (a late shipment, a failed payment the team caused, an outage window, a missed
     booking) even if the customer has said nothing. Writing first is the strongest version of this skill;
   - a low survey score, a bad public review, a cancellation whose reason names a failure;
   - a second contact about the same issue.

2. **Read everything before replying.** The full thread (`tico email thread <mailbox> <thread-id>`), the customer
   record, the order or account in the Tool. Write four lines in your notes: what was promised, what happened, what
   it cost the customer (time, money, embarrassment, a missed event), and what they asked for, if anything. Confirm
   the failure in the team's own data, fast: this check is inside the hour, not before it. If the data shows no
   failure, the customer still gets a respectful answer: use **answer-a-customer** and its "clear no" pattern. Do not
   apologise for a thing that did not happen; show you understood what they went through by being specific about it.

3. **Apologise at once when the team really failed: within one hour of learning of it.** Before the fix is finished
   if need be. Only for a failure the team's data confirms; never a stock "sorry for any inconvenience", and never
   one for a small thing the customer did not notice. The apology, laid out in `references/recovery-ladder.md`:
   - names the specific thing, in their words;
   - owns it ("we", not "there was");
   - says what it cost them, if you know;
   - says what happens next and when;
   - gives no cause you have not confirmed, blames no one (no carrier, no supplier, no teammate, no customer), and
     has no "but" and no "if you felt".
   If it came through a formal channel (the BBB, a lawyer, an agency, a threat to sue, a public crisis), or lawyers
   or insurers could become involved (injury, large loss), never admit fault: say what you are doing, at most one
   line of regret for what they went through, and bring in **answer-a-demand-or-complaint**.

4. **Size it.** Use the ladder in `references/recovery-ladder.md`: an annoyance, a real loss, or a serious harm.
   The level sets the gesture and who is told. Two or more failures for the same customer inside 90 days moves it up
   one level.

5. **Fix it completely.** Do the fix yourself through the Tool: reship, refund, re-book, restore, correct the
   charge. Use `work-out-a-refund-amount` for amounts and `tico finance act <kind>` or the billing Tool to pay.
   Then check the fix landed (the refund shows as issued, the tracking number exists, the account works) before
   telling the customer it is done. If the fix needs a teammate, create a sub-task with a deadline
   (`tico task create --owner <slug> --parent <id>`) and give the customer the date.

6. **Add one gesture beyond the fix.** Pick it from the saved menu, matched to what they lost: lost time gets speed
   or saved effort; lost money gets money; a spoiled occasion gets something personal. Rules:
   - one gesture, stated plainly, with no strings;
   - never tied to changing a review, a score or a complaint, and never offered in exchange for silence;
   - never a coupon that needs a new purchase to be worth anything;
   - sized by the ladder, inside the yes limit. Above the limit, see Autonomy;
   - inside the monthly total: add up this month's gesture costs in `knowledge/recovery-log.md` before giving
     a new one.
   An annoyance needs no gesture. A real apology and a fast fix is the gesture.

7. **Send it** with `tico email lint`, then `tico email reply <mailbox> --thread <thread-id> --body-file <file>`.
   Tico decides the send from the team's sending policy: `pending` goes after the undo window, `waiting_approval`
   means the right person has been asked, `blocked` means fix the cause and never route around it.

8. **Explain the cause only once it is confirmed.** If you know it, one plain sentence plus what has changed. If
   you do not, say so and say when you will. Use `word-a-fix-status` so "fixed" means fixed.

9. **Follow up.** Three days after the fix (default), write two lines: is it working, is there anything left. Put
   the date in `knowledge/follow-ups.md` when you make the promise, not later. If they do not answer, do not chase.

10. **Record it.** One row in `knowledge/recovery-log.md`: date, customer key, the moment that failed, level,
    fix, gesture and its cost, time from failure to apology, time to fix, outcome at follow-up. Add a line to the
    customer record so nobody greets them next time as if nothing happened.

11. **Remove the cause.** File one task for the owner of the cause with the count of customers affected and the
    cost so far from the log. The third row with the same cause in 30 days is a pattern: say so in the task title.
    Recovery without this step just pays for the same failure every week.

### B. One incident, many customers

12. **Make one plan, not many replies.** Use `set-severity-and-owner` so there is one owner. Work out who was
    affected from the data, not from who complained. Decide one remedy for everyone in the same position, cost it
    (customers times remedy), and if the total is above your monthly limit, ask for an OK with the total shown. Write
    to every affected customer, including the silent ones, with the same facts. Keep
    `knowledge/known-issues.md` current so every teammate says the same thing. Updates go out at the interval you
    promised even when there is no news; write each with `write-a-customer-update`. If the incident is still
    running, the engineering side is the **debug-and-handle-incidents** skill and any public statement is the
    **handle-press-and-a-crisis** skill.

### C. A public review

13. **Recover in private, answer in public.** Run steps 1 to 11 with the customer directly if you can identify
    them. Write the public reply with `write-a-public-review-reply`: short, specific, no private details, no
    argument. Never offer anything for changing or removing a review. Flag a review only with `judge-a-review-flag`.

### D. Moments that matter (set up once, read weekly)

14. **Pick five to ten moments** where a failure costs the customer most, using
    `references/moments-that-matter.md`: first use, first bill, a delivery day, a renewal, the first support
    contact, a cancellation. For each, write the promise, the measure that shows it was kept, where that measure
    comes from, and the trigger that starts recovery without waiting for a complaint. Save the table in
    `knowledge/recovery.md`.

### E. Surveys (set up once, worked daily)

15. **Run surveys so that every low score gets a person-quality response.** Follow `references/surveys.md`:
    - a low score is a recovery trigger: reply personally within 24 hours, starting at step 2;
    - never survey a customer who is in the middle of a recovery;
    - never reward a score, and never ask for a high one;
    - comments go, word for word, to the owner of whatever they are about;
    - report the score with its count of responses and its response rate, and never report a score from fewer than
      20 responses as a percentage.

### F. Every week

16. **Publish the recovery page** (see Output), and reread the log for the top three causes by cost.

## Gates

- **Apologies are for real failures.** Only for a failure the team's data confirms, at once, specific, and never by
  reflex. When the team failed, the apology comes before compensation: money without an apology is not a recovery.
- **Never admit fault through a formal channel** (the BBB, a lawyer, an agency, a threat to sue, a public crisis).
  That reply belongs to answer-a-demand-or-complaint, and no OK changes this rule.
- **No invented cause, policy or promise.** An unconfirmed cause is "we are finding out". A draft marks an unknown
  as `[GAP: <what>, held by <who>]` and is not sent until it is filled.
- **No blame** of the customer, a teammate or a named third party in anything a customer reads.
- **Nothing conditional on a review, rating or score.** In the US the FTC's reviews rule (16 CFR Part 465, in force
  since 21 October 2024) bars paying for reviews that must express a particular sentiment, and review platforms ban
  rewards for changing or removing a review. Check local rules elsewhere.
- **No admission of legal liability, no waiver and no settlement wording.** Waivers and settlement wording need an
  OK. Where injury, safety, a regulator or a lawyer is involved, the answer-a-demand-or-complaint skill leads.
- **Identity before money or account changes,** by the rule in `knowledge/support.md`.
- **The same failure gets the same remedy** for every customer in the same position, whether or not they complained.
- **The monthly total is a hard stop for gestures,** not for what policy or law owes. Owed money is always paid.
- **A blocked send stays blocked.** Never call a mail provider directly, never use another mailbox.
- **Never claim to be human.**

## Autonomy

Do on your own:
- Apologise, in the team's name, for a failure the team's data confirms, in ordinary support channels.
- Give whatever the policy or the law owes, at any amount.
- Fix the problem through the Tools, and add one gesture, up to the yes limit per customer and the monthly total.
- Write to customers who have not complained when the data shows the team failed them.
- Follow up, log, and file cause tasks. Run the surveys and answer every low score.
- Reply to public reviews inside the team's review rules.

Needs an OK (`tico task ask <id> --to <person> --kind ok "<one line>" --show page:<id> --version N`):
- A gesture above the yes limit, or one that would pass the monthly total. Send the customer the apology and the fix
  now; ask for the gesture with the amount, the reason and your recommendation. Do not tell the customer no while
  the ask is open.
- One remedy for many customers when the total passes the monthly limit.
- Any release or settlement, any change to a contract. (Wording that admits legal fault is never sent, with or
  without an OK.)
- A public statement about an incident beyond a reply to one review.

A question, not an OK: the confirmed cause, when a teammate holds it. Ask and carry on with the fix.

## Output

- **The customer's message:** apology, fix, gesture, next step. Short. See the example.
- **The log row** and the customer record line.
- **The cause task,** with count and cost.
- **The weekly recovery page:**
  `tico page publish reports/recovery-<YYYY-Www>.html --title "Recovery, week <ww>" --cadence weekly`. In order:
  1. Five figures with last week's beside each: recoveries opened, median time from failure to apology, median time
     to fix, cost of gestures against the monthly limit, share confirmed fixed at follow-up.
  2. A bar chart of failures by cause, each bar labelled with its count and its cost.
  3. The moments table: each moment, its promise, kept or broken this week as counts, as a row of coloured cells.
  4. Surveys: score, responses, response rate, low scores received and low scores answered within 24 hours.
  5. Three causes to remove, each with its task and owner.
  A figure that could not be read is shown as "not read", never as zero. Every figure names its source and dates.

## Checks before you finish

- The failure was confirmed in the team's data. The apology went out within one hour of learning of it, names it
  specifically and blames no one. Nothing was apologised for that did not happen.
- The fix is confirmed in the Tool, not assumed.
- There is at most one gesture, it has no conditions, and it is inside the limit or has an OK.
- No cause is stated that was not confirmed. No gap markers remain.
- The follow-up date is in `knowledge/follow-ups.md`.
- The log has the row. The customer record has the line. The cause has a task with a count.
- Every customer in the same position got the same remedy.
- Nothing was offered in exchange for a review, a score or silence.
- Tico's answer to each send was read, and nothing blocked was worked around.

## Example (invented, for illustration)

Team: Larkspur Frames, an online custom framing shop. Bot: Wren. Every name, order and amount is made up.

What Wren saw, Friday 08:40, with no complaint yet: order for Dana, a framed print marked "gift, needed by Saturday
21 November", promised delivery Thursday. The carrier Tool shows it has not moved since Tuesday. Order value $84.
Dana has ordered twice before. Moment that matters: "arrives by the promised date". Level: a real loss (a gift date
at risk).

What Wren did before writing: rebuilt the order as a rush job in the order Tool (the workshop confirmed a 14:00
dispatch), booked overnight delivery for Saturday morning, and refunded the $12 shipping Dana paid. Fix cost is
owed by policy. Gesture: the frame upgraded to the conservation glass Dana had looked at and not bought, $18,
inside the limit.

Message, 08:52:

> Dana, your framed print should have reached you yesterday and it hasn't. I'm sorry. I know it's a gift for
> tomorrow, and we've put that at risk.
>
> Here's what I've done: a new one is being made this morning and ships at 2pm today by overnight delivery, to
> arrive Saturday before noon. I've refunded your $12 shipping, and I've upgraded the glass to the conservation
> glass at no charge. If the first parcel ever turns up, keep it.
>
> I'll send the tracking link the moment it leaves, and I'll check with you Monday that it arrived in time.
>
> Wren, AI teammate at Larkspur Frames

Log row: 20 Nov, customer key 4417, moment "arrives by promised date", level 2, fix: remake plus overnight, gesture:
glass upgrade $18, failure to apology: found before complaint, fix confirmed Saturday 10:15, follow-up Monday: arrived
in time.

Cause task for the Operations Owner: "Third parcel stalled at the same carrier depot in 30 days (3 customers, $131 in
remakes and gestures). Decide: change carrier for that region or ship two days earlier."
