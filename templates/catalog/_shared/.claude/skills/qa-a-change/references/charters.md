# Exploratory charters

Scripted checks confirm what you already expected. A charter sends you looking for what nobody expected. It is one
line that names where to look, under what odd condition, and what kind of trouble you are hunting, with a time limit
so the session ends.

## The charter line

```
Explore <area or flow>
with <an odd condition, role, device or kind of data>
to find <the risk>.
Limit: <minutes> or <probes>, whichever comes first.
```

Good: "Explore checkout with a discount code and a slow network, to find double charges. Limit: 15 minutes or 12
probes."

Too wide: "Test checkout." It names no condition and no risk, so it never ends and proves nothing.

## Picking charters for a change

Write two to five. Choose from the diff and the spec, not from habit:

1. One for each risky surface the change touches (money, sign-in and permissions, stored data, messages sent to
   customers, anything shared between customers).
2. One for the newest or least tested part of the change.
3. One from the list below that this change has not obviously handled.

## Odd conditions worth trying

| Condition | What to do | What tends to break |
| --- | --- | --- |
| Empty state | A brand-new account with no data | Blank screens, errors on empty lists, missing first step |
| First time and hundredth time | No history, then a very long history | Onboarding gaps; slow or cut-off lists |
| Slow or dropped network | Throttle the connection; cut it mid-request | Spinners that never end, double submits, lost input |
| Phone size | 390 px wide, touch only | Buttons off screen, text cut, menus that cannot be closed |
| Second submit | Double click; press Enter and click; submit again after an error | Duplicate records, double charges |
| Back, refresh, new tab | Go back mid-flow; refresh on each step; open the same flow in two tabs | Lost state, stale screens, steps skipped |
| Two people at once | Two accounts change the same thing | Last write silently wins, wrong totals |
| Wrong permission | A role that should not see or do this; signed out; another team's id in the address | Data shown or changed without the right |
| Expired session | Leave the screen open past sign-in expiry, then act | Silent failure, lost work |
| Odd input | Very long text, empty, spaces only, emoji, right-to-left text, pasted formatting, zero, negative, a huge number | Layout breaks, bad stored data, crashes |
| Dates and time | Month end, leap day, a time zone far from the server's, clock change day | Off-by-one days, wrong renewals |
| Interrupted work | Close the tab mid-save; the server restarts mid-job | Half-written records, jobs that run twice |
| Old client | An older app version or cached page against the new server | Fields the old client does not know |

## Running a session

1. **Show it working first.** Do the plain version of the flow and capture the result, including what was stored. A
   failure only means something next to a known success.
2. **Write the guess and the next action before each probe.** "Guess: a second press creates a second record. Next:
   throttle to slow, press Pause twice."
3. **One probe at a time.** Change one thing, look, write down what happened.
4. **When something fails, replay it** from the same starting state before you call it a bug. Note how many tries out
   of how many failed.
5. **Strip it down.** Remove steps until every remaining one is needed for the failure.
6. **Stop at the limit.** If the session is still finding things, write a second charter rather than running over.

A tool that refused, a page that timed out, or a prerequisite you did not have is not a pass. Write it under "Not
checked".

## Session notes

Keep these for each charter and put them in the QA report:

```
Charter:
Build (SHA) and where it ran:
Start state (account, data):
Time and probes used:
What worked (with evidence):
What failed (steps, expected, actual, evidence, tries failed out of tries made):
Questions raised:
Not checked, and why:
New charter worth running:
```

## Limits by kind of change

Starting values. A team sets its own in `knowledge/engineering.md`.

| Kind of change | Charters | Limit each |
| --- | --- | --- |
| Small fix, one flow | 2 | 5 minutes or 12 probes |
| Ordinary feature | 3 | 15 minutes or 12 probes |
| Touches money, sign-in or stored data | 5 | 15 minutes or 12 probes |
