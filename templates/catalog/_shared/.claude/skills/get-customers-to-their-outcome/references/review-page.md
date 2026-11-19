# The business review page

A review is for the customer. It shows them what they got against what they wanted and asks for one decision. It is
not a product tour, a usage dump or a renewal pitch.

## When to hold one

Hold a review when at least one is true:

- there is a result to show against the goal (reached, or clear progress with a number);
- there is a decision only the customer can make (a changed goal, a blocked milestone, a renewal, a next phase);
- the people on their side have changed and the goal needs confirming with the new ones;
- the customer asked for it.

The calendar alone is not a reason. If a quarterly slot comes round with none of the above, cancel it with a
two-line update: where things stand against the goal, and when you will next have something to show. Customers
notice and value the returned hour.

Default rhythm for accounts worked one by one: a review when first value is reached, then roughly every quarter if
one of the conditions holds, and always one before renewal work starts (about 120 days before the notice date).

## Before you build it

1. Reread the outcome plan. If the goal has not been confirmed in the last six months, the first item of the review
   is confirming it.
2. Pull the measure from its source, with dates. Use their number where it is theirs. If you cannot get it, ask the
   champion for it a week ahead; do not substitute a usage figure and call it the result.
3. Read the last quarter of tickets, the recovery log and the mailbox, so nothing they raised is news to you.
4. Check health. If the account is red, the review is about the problem, and says so at the top.
5. Decide the one decision you need from them. If you cannot name it, you may not need a meeting.

## The page

One screen, built as HTML and published with
`tico page publish reviews/<customer-key>-<YYYY-MM>.html --title "<Customer> review, <Month YYYY>"`.
A Tico page is for teammates; a customer cannot open it. Send the customer the same content as an attachment
(`tico email draft <mailbox> --to <address> --subject "<subject>" --body-file <file> --attach <review file>`, then
`tico email send <mailbox> --draft <draft-id> --task <id>`), with the goal, the headline figure and the decision
repeated in the email body so it can be read without opening anything. Before it leaves, read it once for anything
internal: health bands, scores, internal notes and other customers' data never appear.

Sections, in this order:

1. **Your goal.** Their words, quoted exactly, with the measure, target and date. One line.
2. **Where you are.** One chart: the measure over time, with the starting value and the target as lines. A single
   large figure beside it: now against start. If the measure is theirs, say "your figures". If it is not visible,
   say so here and make getting it the decision.
3. **What changed since last time.** Three to five lines: milestones reached, problems fixed (including ours, named
   plainly), things they asked for and what happened to each.
4. **What is in the way.** At most three items, each with who owns it and a date. Include anything waiting on the
   team.
5. **What is next.** The next result they said they care about, and the two or three steps toward it.
6. **One decision.** The question you need answered, with your recommendation.

What stays off the page: feature lists, a roadmap unless they asked and the owner has OK'd what is shown, charts of
logins for their own sake, anything about price unless the decision is a renewal or they raised it.

## Rules for the numbers

- Every figure has its source and date range in small print under it.
- Their numbers are theirs; quote them as given and say when they gave them.
- A before-and-after claim needs both numbers measured the same way. If the method changed, say so.
- Do not claim the product caused a result you cannot separate from other things that changed. Say "since you
  started" rather than "because of".
- No number is invented, rounded up, or borrowed from another customer.
- A money value is stated only if the customer supplied the unit cost or agreed it; otherwise show the count.

## Running it

- Send the page two working days ahead with one line: the decision you would like to reach.
- If it is a call, it follows the page top to bottom and takes half the booked time at most; the rest is theirs.
- Ask the goal question every time: "Is this still the right goal?" Goals change when people or markets change,
  and a team working toward last year's goal loses the account while hitting its plan.
- Ask one forward question: "What would make next quarter a clear success for you?" Write the answer into the plan
  in their words.
- If it is done by email, the page plus three sentences is the review. Many customers prefer that.

## Afterwards, the same day

- Send a recap under 100 words: what was decided, who does what by when.
- Update the outcome plan: goal confirmed or changed, new milestones, the log line, the version.
- Create tasks for anything the team owes (`tico task create --owner <slug> --parent <id>`).
- If they stated a new goal the current plan cannot reach, that is an expansion signal; rate it as in
  `saves-and-expansion.md`.
- If they raised a problem the team caused, start recover-a-bad-experience.
