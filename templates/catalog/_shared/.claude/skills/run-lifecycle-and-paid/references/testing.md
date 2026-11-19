# Tests: when to run one, how to size it, how to read it

A test is the most expensive way to make a decision. It costs traffic and weeks. Run one when the answer is worth
that and cannot be had more cheaply.

## 1. Fix, ship or test?

| Situation | Do |
|---|---|
| Something is broken (dead button, form fails on phones, price disagrees with the ad) | **Fix** now. Log it. |
| The change is clearly better and cheap to undo (clearer wording, a missing fact added) | **Ship** it. Watch the number before and after, and say that is what you did. |
| Every possible result would lead to the same decision | **Do not test.** Make the decision. |
| The traffic cannot reach the sample in eight weeks | **Do not test** this change. Test a bolder one, or ship and watch. |
| A real choice, a real cost of being wrong, and enough traffic | **Test.** |

A before-and-after read is weaker than a test: seasons, campaigns and news move the number too. Report it as "after
the change the rate was X, against Y before, over the same number of days", never as "the change caused".

## 2. The design, written before the start

| Field | Rule |
|---|---|
| Prediction | "Changing A to B will move [metric] from X to Y, because [reason from evidence]." A number, not "improve". |
| One change | One variable differs between the versions. A whole-page redesign can be tested, but then the lesson is only "this page beat that page". |
| Main metric | One, as close to money as the traffic allows: purchases over sign-ups, sign-ups over clicks. For email: clicks or replies or the goal event, never opens. |
| Counter-metric | The thing that must not get worse: refunds, unsubscribes, complaints, average order value, support contacts. |
| Who is in it | The audience, the split (default half and half, assigned at random per person, not per visit), and who is excluded |
| Sample size | Computed below. Per version. |
| End date | The date the sample will be reached, rounded up to whole weeks, at least two full weeks so each weekday appears twice |
| The decision | Written now: what you will do if it wins, loses, or lands in between |

File it in Growth Experiments with a Go-live page. The prediction and the end date are on that page.

## 3. Sample size

For a rate (a share of people who do something), with the usual settings of a 5% false-alarm rate (two-sided) and an
80% chance of catching a real effect:

**n per version = 7.849 x [ p1 x (1 - p1) + p2 x (1 - p2) ] / (p2 - p1)^2**

where p1 is today's rate and p2 is the smallest improved rate worth acting on, both as decimals. Run the arithmetic
with a tool and paste it. A quick sanity check: n is about 16 x p x (1 - p) / (p2 - p1)^2.

Visitors needed per version, by today's rate and the relative lift you want to detect:

| Today's rate | +10% | +20% | +50% |
|---|---|---|---|
| 2% | 80,680 | 21,106 | 3,823 |
| 5% | 31,232 | 8,156 | 1,468 |
| 10% | 14,749 | 3,839 | 683 |
| 20% | 6,507 | 1,680 | 291 |

What the table teaches: small lifts on low rates need enormous traffic. A page with 3,000 visits a month cannot
detect a 10% lift on a 2% rate in any useful time. Such a page should test big changes (a different offer, a
different promise), or not test.

Choose "the smallest lift worth acting on" from the money: the lift at which the change pays for the work. Do not
choose it to make the sample fit the traffic.

For a metric that is an amount (revenue per visitor), use `size-a-test-before-it-starts` or the test tool's own
calculator, since the spread of the amounts matters.

## 4. Starting it

1. Launch to a small share first (default 10% of traffic for two days). Check that both versions load, record the
   main metric and the counter-metric, and that the split is close to what you set.
2. Then open it to the full planned share. Do not change the versions, the split or the audience after this. A
   change restarts the test.
3. Note anything unusual during the run (a sale, an outage, a press mention) in the log.

## 5. While it runs

- **Do not read the main metric for a decision.** Each early look at a normal test is another chance for luck to
  cross the line. In Evan Miller's worked example, checking after every batch and stopping at the first "significant"
  result gave a 26.1% false-alarm rate where 5% was intended.
- **Do watch for harm.** A broken version, a jump in complaints or refunds, or a counter-metric falling fast is a
  reason to pause. Pausing for harm is always allowed.
- **If the test tool uses a sequential or Bayesian method built for continuous monitoring**, follow that tool's own
  stopping rule, write down which method it is, and still fix a maximum length in advance.
- **A test past 30 days with no end date is retired.** Record it as inconclusive.

## 6. Reading it, once, at the end

1. **Is the split right?** With a 50/50 split, compute (a - e)^2 / e + (b - e)^2 / e, where a and b are the people in
   each version and e is their average. Above 10.83 (a chance under 1 in 1,000), the assignment is broken. Do not
   read the result. Find the cause.
2. **The difference and its range.** With rates pa and pb from na and nb people:
   difference = pb - pa, and the 95% range is difference plus or minus
   1.96 x square root of [ pa(1 - pa)/na + pb(1 - pb)/nb ].
3. **The verdict.**
   - The whole range is above zero and the counter-metric held: **ship**.
   - The whole range is below zero: **drop**.
   - The range includes zero: **inconclusive**. Say so. Keep the simpler or cheaper version. Do not run it "a bit
     longer" to get a winner.
4. **Report the range, not just the winner.** "B converted 0.69 points higher (0.20 to 1.18)" tells the reader how
   sure to be. "B won by 33%" does not.
5. **Do not slice afterwards for a winner.** A segment result found after the fact ("it won on mobile") is an idea
   for the next test, not a finding.
6. **Record it** in `knowledge/tests.md`: prediction, design, dates, sample, result with range, counter-metric,
   verdict, and what was learned about the customer. Set the task's label: `result:worked-once` for a first win
   (it becomes a practice only when it holds again), `result:failed`, or `result:watching`.

Worked reading (invented numbers): A 161 of 7,660 (2.10%), B 214 of 7,655 (2.80%). Split check: 0.002, fine.
Difference 0.69 points, range 0.20 to 1.18 points. The range is above zero: ship, if refunds held.

## 7. Email tests

- One variable: subject, sender name, send time, offer or body. Not two.
- Judge by clicks, replies or the goal event. A subject-line test read on opens is read on noise.
- Size it with the same formula. Click rates are low, so most lists cannot detect a small difference: say when the
  list is too small, and do not send two versions for the look of it.
- Split at random before the send. Do not compare this week's send with last week's.
- A holdout (people who get no email at all) answers the bigger question: does the programme do anything?

## 8. Ad tests

- Use the platform's own experiment tool, so the split is random per person. Comparing two campaigns side by side is
  not a test: the platform sends each to different people.
- One variable: audience, creative, page or bid strategy.
- Judge on customers from the records where the counts allow, and say when the verdict rests on platform
  conversions.
- Give each arm enough budget to leave the platform's learning phase, or the test measures the learning, not the ad.
- Use `test-ad-copy-and-landing-page-fit` and `read-an-audience-or-creative-test` for the detail.

## 9. When traffic is too low to test at all

Most small companies are here. Do the cheaper things, and say that is what they are.

- Fix everything section 1 calls a fix.
- Make bold changes, one at a time, and watch before and after across equal periods.
- Watch five people use the page, or read session recordings, and list where they stop.
- Read the replies, the support threads and the lost-deal notes for the objection the page does not answer.
- Ask new customers one question: "What nearly stopped you?"
