# Attribution and holdouts

Attribution is a counting rule. It says which channel each customer is counted under. It does not prove the channel
caused the customer. A consistent rule kept for years is worth more than a cleverer rule that changes, because only a
fixed rule lets this month be compared with last year. Cause is settled a different way: by a holdout.

## The rule

One rule for every customer and every month. The default, used until the owner chooses another:

1. **Tagged touch.** The last tagged, non-direct visit in the 30 days before first payment names the channel. "Direct"
   (typed address, bookmark, app open) never takes credit from an earlier tagged touch inside the window.
2. **Code.** No tagged touch: a referral, partner or promo code on the first order names it.
3. **Asked.** No code: the customer's answer to "how did you hear about us" names it
   (`skills/ask-how-they-heard` where your repository has it).
4. **Unknown.** None of these. Unknown is a real row. It has customers and no CAC.

Tico Growth records which of these named each customer (`utm`, `code`, `asked`, `unknown`). Keep that column on every
channel table: a channel whose customers are mostly "asked" is less certain than one whose customers are mostly tagged.

For sales cycles over 30 days, use a 90-day window and keep the first touch in a second column. First touch says what
opened the door; last touch says what closed it. Report both; compute CAC on the rule in the settings.

Write the rule, the window and the date it took effect in `knowledge/unit-economics.md`.

## Rules for the count

- **The customer count comes from the system of record** (billing or the product database). An ad platform's
  "conversions" are that platform's claim, not customers.
- **Never add platform claims together.** Each platform credits itself for any customer it touched, so the sum is
  larger than the real total.
- **Three columns, never merged**: customers by the rule; what each platform claims; what customers said. Agreement
  between them raises confidence. A gap is a finding to write down, not an error to average away.
- **Unknown's share is on the page every month.** Over 25%, paid CAC and every channel figure carry "attribution
  weak". The fix is tagging and asking, not a model.
- **Changing the rule** is a proposal the owner confirms. Restate the last twelve months under the old and new rule
  side by side, once, and name the date of the change on every later chart.

## What each source tends to get wrong

| Source | Leans toward | Why |
| --- | --- | --- |
| Ad platform reports | Over-crediting itself | Counts views and clicks on its own ads, including people who would have bought anyway |
| Last tagged touch | Channels near the purchase (search on the company's name, retargeting, coupons) | They catch demand other channels made |
| First touch | Channels that start the journey | Ignores what closed it |
| "How did you hear" answers | Memorable channels (word of mouth, podcasts, video) | People recall the story, not the last click; many do not answer |
| Web analytics | Under-counting | Blocked cookies, app-to-web hand-offs, more than one device |

No source is right. The rule is chosen for being stable; the others are kept beside it to show where it may be wrong.

## When a holdout is needed

Check these three each month. They are this skill's defaults, not an industry standard.

1. **A channel takes more than a fifth of paid spend** and has had no holdout in the last twelve months.
2. **The rule and another column disagree by more than two to one** on a channel, and a budget decision depends on it.
3. **A demand-catching channel is about to get more money**: retargeting, affiliate and coupon sites. These are the
   channels a last-touch rule flatters most. Search ads on the company's own name are the exception: they run as a
   standing line by default and are not held out, but they are always reported on their own line, apart from
   non-brand, so they never flatter a channel's numbers.

## Designs, strongest first

A holdout withholds the channel from a comparable group and counts the difference in customers in the system of
record. Record the prediction first, as a number, on the test's Go-live page.

1. **Random audience holdout.** A random share of the eligible audience (10% to 20% is usual) does not get the
   channel. Works for email, push, direct mail, and inside ad platforms that offer a lift test. A platform's lift test
   only measures that platform; read its result against the system of record where the platform lets audiences be
   matched.
2. **Geographic holdout.** Switch the channel off in some regions and keep it on in matched ones. Match regions on the
   last 8 to 12 weeks of new customers, not on size alone. Use for channels that cannot be split by person: TV, radio,
   outdoor, local search, podcasts.
3. **Switch-off test.** Turn the channel off everywhere for a set period and compare with the forecast from before.
   The weakest design, because the season and other channels move too. Use only when the first two are impossible, and
   say so.

Sizing and reading:

- Size it before it starts (`skills/size-a-test-before-it-starts` where your repository has it): the expected new customers in each group and
  the smallest difference worth acting on decide the length. Public guides commonly give two to four weeks for lift
  and regional tests (read November 2026); the company's own buying cycle wins over that.
- Run whole weeks. Stop on the date written at the start, not when the number looks good
  (`skills/read-a-test-without-peeking` where your repository has it).
- Result: incremental customers = customers in the exposed group minus customers the holdout group implies for the
  same size. Incremental CAC = the channel's spend on the exposed group divided by incremental customers. Give the
  range, not one number.
- If the test was too small to tell, say "not settled" and what size would settle it. Do not read a direction into it.

## Using a result

- Put it on the channel table beside the rule's figure: "CAC by rule $40; incremental CAC from holdout $95 (range $70
  to $150), October 2026".
- The rule does not change because of one test. The budget decision uses the holdout; the monthly series keeps the
  rule.
- A holdout result is good for about twelve months, or until the channel's spend, creative or audience changes a lot.
- Log every holdout in `knowledge/unit-economics.md`: channel, design, dates, sizes, prediction, result with range,
  decision.
- File the test as a task in the Growth Experiments pipeline, with a Go-live page and a Results page, as any other
  growth experiment.

## Platform facts that change

Check these in the account before planning; they move. As read in November 2026, from trade reports and not
confirmed in the platforms' own documents:

- Google Ads conversion lift was reported in late 2025 to need a far smaller minimum spend than before (about $5,000
  in place of about $100,000).
- Meta does not publish a fixed minimum for its lift tests; eligibility shows in the ad account.
- Google's data-driven attribution wants enough recent volume to work (commonly cited: about 200 conversions and
  2,000 ad interactions in 30 days). Below that, a platform model is guessing; another reason to keep the simple rule.
