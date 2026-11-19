# Pick the method

Use it in step 3. Start from what you need to know, not from the method you like. Take the cheapest method that can answer the question; "cheapest" counts the customer's time first.

| You need to know | Method | Good for | Bad for | Size to start |
|---|---|---|---|---|
| What customers have already told us | **Synthesis** of tickets, calls, reviews, requests | What hurts, in their words; how many accounts; which segments | Why; anything nobody thought to write in about | All of the last 90 days, or a stated sample |
| Why they do something, what happened around it | **Interviews** | Motives, workarounds, the order of events, surprises | How many; what they will do in future | 5 per segment |
| Why they bought, or left | **Switch interviews** (the story from first thought to decision) | What triggered the change, what they compared, what nearly stopped them | Feature wishes | 5 recent buyers or leavers |
| How many, how often, in what order | **A usage query** | Size, frequency, drop-off points | Why | The whole population, with the date range |
| Whether people can use a design | **A usability round** (`skills/plan-a-usability-round/SKILL.md`) | Where they get stuck on a specific screen | Whether they want it at all | 5 per round |
| How widespread a known thing is | **A short survey** | Putting a number on something interviews already found | Discovering anything new; "would you" questions | Enough answers per segment to read (see below) |
| Whether a change works | **A test in the product** (`skills/choose-the-next-test/SKILL.md`) | Behaviour under real conditions | Explaining the result | Set before starting |

## Order

1. A usage query and a synthesis, together. They cost customers nothing and tell you whether the question is already answered.
2. Interviews, to explain what the first two show and to hear what nobody wrote in about.
3. A survey or a test, only to size or confirm what interviews found.

Skipping to step 3 produces confident numbers about the wrong question.

## How many interviews

There is no agreed number, and sources differ:

- The often-quoted "five users" (Nielsen, 2000) is about usability tests of one design, not about interviews.
- For interviews, a 2006 study by Guest, Bunce and Johnson is the usual citation for "most themes appear within the first six to twelve interviews of one similar group". That is a secondary reading; open the paper before quoting it.
- Practitioner guides suggest ranges of about seven to fourteen.

This skill's rule, which does not depend on any of them: start with five per segment, stop when two in a row add no new evidence line toward the questions, and treat twelve in one segment without an answer as a sign the questions are wrong. One segment's interviews say nothing about another segment.

## Surveys, briefly

- Use one only after interviews, with answer options taken from customers' own words.
- Ask about what happened ("In the last month, how many invoices did you send more than two days after the job?"), not about the future.
- State the response rate and who did not answer. People who answer surveys differ from people who do not.
- Read results by segment. An average over unlike segments describes nobody.
- With fewer than about 30 answers in a segment, report counts, not percentages.
- The "how would you feel if you could no longer use this?" question (the Sean Ellis test, with its rule of thumb that 40% answering "very disappointed" marks a product people need) is a rough signal for a whole product, not a way to judge a feature.

## Which customers, by question

| Question | Draw from |
|---|---|
| Why is this step hard? | Accounts that did the step in the last 30 days; accounts that started it and stopped |
| Why do people leave? | Accounts that cancelled or went quiet in the last 90 days, as soon after leaving as possible |
| Why did they pick us? | Accounts that bought in the last 30 days (memory fades quickly) |
| Is this request a real need? | The requesters, plus the same number of similar accounts that did not ask |
| What do our best customers do differently? | The top tenth by the measure that matters, beside a matched group from the middle |

Always include some who are not fans: the quiet, the lapsed, the ones who chose something else. Record how the list was drawn.

## When not to research

- The decision is cheap to reverse and a test in the product would answer it faster: run the test.
- A finding at pattern confidence already answers it.
- No answer could change the decision.
- The only customers available are the three who always reply. Say so, and find others first.
