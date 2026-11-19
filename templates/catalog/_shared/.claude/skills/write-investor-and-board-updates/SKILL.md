---
name: write-investor-and-board-updates
description: Write the update that goes to investors or the board of directors, and pick the right format for it, a short monthly investor email, a quarterly letter, a board pack sent ahead as a pre-read, or a same-day bad-news note. Numbers come first in a fixed table with the same definitions every period, every figure is read from the books and KPIs and tied to a second source, lowlights are stated plainly beside highlights, last period's promises are scored, and asks are specific and placed where they cannot be missed. The bot drafts the hard questions first, checks for things that must not be shared, publishes the draft as a page and sends after one OK. Use when a task says investor update, monthly update, shareholder letter, quarterly letter, board pack, board deck, board meeting, board pre-read, board materials, update the investors, report to the board, lender update, or when the month's books close and an update is due.
metadata:
  function: operations
  roles: [investor-relations-manager, fundraising-manager, chief-of-staff, finance-owner, fpa-analyst, corporate-secretary]
  actions: [read, draft, send]
  review: professional
---
# Write investor and board updates

When to use: when a month or quarter closes and an update is due; when a board meeting is on the calendar; when
something material happens that investors should hear from the company first; when a task asks for any of these.

Words used here: **board** in this skill always means the board of directors, the people with legal duty to oversee
the company. It never means a Tico pipeline. An **investor** is anyone who holds shares, notes or other securities in
the company. A **lender** gets the same care as an investor. The **books** are the company's ledger in Tico Finance. A
**figure sheet** is the working table behind an update that lists every number with its source and check. A **page** is
an HTML page a bot builds and publishes in Tico. An **OK** is a person's approval of one exact version.

On `review: professional`: a routine monthly or quarterly update needs no outside reviewer. A lawyer checks four
things: anything sent to people who are not yet investors while the company is raising money; a board pack that asks
for a vote; a note about a legal claim, a security incident or a regulator; and the first update, to confirm what the
investor agreements require. A CPA checks financial statements that are labelled as statements and go to a lender or
investor at year end.

## What great looks like

- **The same numbers, in the same order, every time.** A reader compares this update with the last in ten seconds.
  Definitions do not change quietly. When one must, the old and new values are both shown once.
- **Every figure is from the books or a KPI, and has been tied to a second source.** A figure that cannot be tied is
  written "not supplied". It is never estimated to fill the table.
- **Bad news is in the update, in plain words, early.** Investors forgive a miss. They do not forgive learning about it
  late or finding it softened. A lowlight says what happened, why, and what is being done.
- **Promises are scored.** "Last time we said" appears every time, with done or not done.
- **Asks are specific.** A named kind of person, a named company, a decision by a date. "Any help appreciated" is not
  an ask.
- **It is short.** A monthly update is read on a phone in two minutes. A board pack is read once, ahead, and the
  meeting is spent on decisions.
- **It is on time, every time.** A late or skipped update is itself bad news.

## First time at a company

Read `knowledge/investor-updates.md`. If it has an "Our settings" section, skip this. If not, ask the owner these in
one numbered message, save the answers there with the date, and work on the defaults until they reply.

1. **Who gets what, and how often?** Default: one monthly email to all investors, sent by the 10th business day after
   the month's books close. Quarterly instead once the company is profitable or has more than about 18 months of
   runway and is not raising. See `references/formats.md`.
2. **Which numbers are the fixed table?** Default: cash, net burn, runway in months, revenue (monthly recurring revenue
   for a subscription business), growth against last month, the North Star KPI, and number of customers. Six to eight
   rows.
3. **What must never go in?** Default: customer names without their permission, the cap table, the terms or valuation
   of a round, anyone's pay, anything under a confidentiality agreement, details of a legal dispute, unfixed security
   weaknesses.
4. **Who sends it, from which mailbox, and who OKs it?** Default: it goes from the owner's mailbox in the owner's
   name, and the owner OKs every send.
5. **What do the investor agreements require?** Default: unknown. Read the investor rights agreement, side letters and
   loan agreements in team docs for what must be delivered and when, list each duty with its clause, and mark the rest
   "not checked".
6. **Is there a board of directors? How often does it meet, and who chairs?** Default: none, so the board pack format
   is not used. If there is one: the pre-read goes out three working days before the meeting.

## Steps

1. **Read what came before.** The settings, the last update exactly as it was sent (`reports/investor-updates/`), the
   promises and asks in it, and replies from investors since (`tico email inbox`, `tico email thread`).
2. **Pick the format** with `references/formats.md`:
   - monthly investor email: the default for an early company;
   - quarterly letter: a steadier company, or what the agreements require;
   - board pack: when a board meeting is scheduled;
   - same-day note: when something material happens between updates. Material means a reasonable investor would want
     to know before the next update: cash under six months, loss of a customer above 10% of revenue, a founder or key
     person leaving, a legal claim, a security incident, a missed loan term. The settings may change these lines.
3. **Check the period is closed.** Find the close page for the month (`tico page list`; it is made by the skill
   `close-the-month`) or ask the Finance Owner on the task. If the books are not closed, either wait or label every
   money figure "preliminary, books not closed". Never mix closed and preliminary figures without labels.
4. **Pull every number and build the figure sheet.** Follow `references/figure-check.md`. Sources:
   - `tico finance metric cash`, `burn`, `runway_months`, `revenue`;
   - `tico finance report pnl`, `balance_sheet`, `cash_flow`, `budget_vs_actual` for the period;
   - `tico kpi show <kpi>` for the North Star and other KPIs, with the reading's period and quality;
   - `tico growth numbers` for customers, growth and retention.
   For each figure: read it from the source yourself; tie it to a second source; confirm the definition matches last
   period's; compute the change; and find the reason, in the records, for any move over 20%. A figure that fails any
   check is "not supplied", with the reason on the sheet.
5. **Write the hard questions first.** Before any prose, list the five questions a sharp investor will ask about these
   numbers (`references/hard-questions.md`). Answer each from the records. The answers shape the lowlights and the
   summary line. An honest "we do not know yet, and here is how we will find out" is an answer.
6. **Write the lowlights.** One to three. Each has three parts: what happened, with the number; why, as far as the
   records show; what is being done, by whom, by when. No softening words ("slightly", "some headwinds"). If a lowlight
   moves a number in the table, it also goes in the summary line.
7. **Write the highlights.** Three to five. Each is a fact with a number or a named event and a date. No adjectives
   doing the work of evidence.
8. **Score last time's promises.** Each thing the last update said would happen: done or not done, and a clause on
   why. Then two or three things that will happen by the next update. Promise only what is already a task with an
   owner.
9. **Write the asks.** One to three. Each names what is wanted, from what kind of person, and what a yes looks like:
   "An introduction to the head of operations at a property manager with over 500 units." Report what came of last
   time's asks, and thank by name.
10. **Assemble.** Use the template for the format. Monthly order: one summary line, the numbers table, asks,
    lowlights, highlights, last time and next. Under 350 words, not counting the table. Board pack order: decisions
    needed, summary, numbers against plan, lowlights and risks, one or two topics for discussion, asks, appendix.
11. **Run the exclusion scan.** Check every line against the "never goes in" list. Check forward-looking lines are
    labelled as plan or forecast with their main assumption. Check there are no terms, valuations or round details
    unless the owner wrote them. Check the recipient list against the settings: current investors only.
12. **Publish and ask.** Publish the draft with the figure sheet below it:
    `tico page publish reports/investor-update-<period>.html --title "Investor update <period>" --task <task id>`.
    Ask: `tico task ask <task id> --to <owner> --kind ok "Send the <period> investor update" --show page:<n> --version N`. The OK
    is for the version shown. Any change to a number or a lowlight after the OK needs a new one.
13. **Send.** Read the sending rule (`tico email policy --bot <your slug>`). Draft from the mailbox in the settings
    (`tico email draft <address> --to <recipient> --subject "<Company> investor update, <period>" --body-file <path>`)
    and send (`tico email send <address> --draft <id> --task <task id>`). Recipients must not see each other's
    addresses unless the settings say they share a list. Plain text or simple HTML; the numbers table must survive a
    phone screen. No attachments for a monthly update.
14. **File and follow up.** Save the exact sent text as `reports/investor-updates/<period>.md` with the figure sheet.
    Turn each ask into a task. Watch for replies for seven days: answer factual questions from the figure sheet, and
    route offers of help, anything about terms, and anything you are unsure of to the owner as one ask.

## Gates

- **No figure without a source, a period, a date read and a tie.** Otherwise the cell says "not supplied".
- **Same definition as last period.** A changed definition shows old and new side by side, once, with the reason.
- **A restated figure is called restated.** Never silently replace a number an investor has already seen.
- **Lowlights are not optional.** An update with none says "No lowlights this period" only if the hard-questions step
  found none, and that will be rare.
- **Nothing forward-looking without a label.** "Plan" or "forecast", with the assumption that matters most.
- **Committed money only when it is in writing.** A verbal yes is not in the cash or runway figures.
- **No terms.** No valuation, price, round size or investor names for a round in progress, unless the owner wrote
  that paragraph.
- **Current holders only.** An update that mentions a raise does not go to anyone who is not already an investor
  without a lawyer's check. US securities rules limit how some private raises may be promoted; the rule for the raise
  type must be checked.
- **Legal matters travel separately.** Advice from the company's lawyers and analysis of a dispute do not go in the
  update or the board pack. Counsel shares them, so the protection for legal advice is kept.
- **Statements to investors must be true and complete in what matters.** In the US, a misleading statement or
  omission to investors can be securities fraud whether or not the company is public. When unsure whether something
  must be said, ask the owner and counsel; do not decide to leave it out on your own.
- **Nothing is sent without the OK for that version.**

## Autonomy

Do on your own: start the update when the books close, pull and check every figure, write the whole draft, score the
promises, publish the page, file the sent copy, turn asks into tasks, answer investors' factual questions from the
figure sheet, and chase the Finance Owner for a figure that would not tie.

Ask for an OK, once, with the page attached:

- every send to investors, the board or a lender (it leaves the team and cannot be recalled);
- a same-day note about material bad news, with a lawyer's check when it concerns a claim, an incident or a regulator;
- any change to the fixed table or a definition;
- anything that would break the "never goes in" list.

The owner may later set routine monthly updates to send without an OK when every figure ties and no lowlight is new.
That is the owner's setting to make, in `knowledge/investor-updates.md`. Do not assume it.

## Output

- **The update**, in the chosen format, as a page for the OK and then as the sent email or pre-read.
- **The figure sheet**: every number with value, unit, period, source, date read, second source, tie result,
  definition, change, reason. It sits under the draft on the page and is filed with the sent copy.
- **Charts where they help**: for a quarterly letter or a board pack, small trend charts for cash, revenue and the
  North Star over the last 12 months, and a plan-against-actual bar for the quarter. A monthly email carries the table
  only.
- **Tasks** for each ask and each promise.
- **A list of duties** from the investor agreements, in the knowledge file, each with its clause and due date.

## Checks before you finish

- The table has the same rows, order and definitions as last period, or shows the change.
- Every figure is on the figure sheet with a tie, or reads "not supplied".
- Money figures come from a closed period, or carry the "preliminary" label.
- Each lowlight has what, why and what is being done; none uses softening words.
- "Last time we said" is there and scored.
- Each ask could be acted on by a reader today.
- Nothing from the "never goes in" list is present; no terms; no unlabelled forecast.
- The recipients are the current list from the settings and cannot see each other.
- The sent text is exactly the version that got the OK, and it is filed.

## Example

Invented for illustration. The company, people and numbers are made up.

**Subject: Fernhill investor update, February 2026**

February in one line: revenue grew 6% but we lost our second-largest customer, which takes 9% off March revenue.

| | Feb 2026 | Jan 2026 | Change |
|-|----------|----------|--------|
| Cash | $1,412,000 | $1,538,000 | −$126,000 |
| Net burn | $126,000 | $118,000 | +7% |
| Runway | 11.2 months | 13.0 months | −1.8 |
| Monthly recurring revenue | $84,300 | $79,500 | +6% |
| Paying customers | 212 | 201 | +11 |
| Weekly active teams | not supplied | 148 | |

Weekly active teams is not supplied: the product database and the KPI disagreed by 9 and we have not found why.

**Asks.** (1) An introduction to an operations lead at a regional property manager with 500 or more units. (2) A
finance contractor who has set up usage billing; ten hours.

**Lowlights.** Lakeshore Rentals ($7,600 a month) cancelled on 19 February, effective March. They moved to an in-house
tool; price was not the reason they gave. We are calling our next five largest customers this week (Dana, by 13
March). Runway fell under 12 months, and we will bring a plan to reach 15 to the April update.

**Highlights.** 14 new customers, 9 from referrals. Median time to first value fell from 6 days to 3 after the setup
change on 3 February. Signed a pilot with a 1,200-unit operator on 24 February.

**Last time we said.** Ship usage billing: not done, moved to March, waiting on the finance set-up above. Hire no one:
done. **By next update:** usage billing live; five customer calls done; runway plan drafted.
