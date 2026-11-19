# Writing the no

A person who asked for something is owed an answer they can use. The worst reply is not "no". It is silence, or a reply that shows nobody read what they wrote. A good no is fast, shows the person was understood, gives the real reason, and leaves them knowing what to do next.

## The five parts, in order

1. **Their problem, in their words.** One sentence about what they are trying to get done. Not "thanks for the suggestion", and not the feature's name repeated back.
2. **The answer.** Inside the first two sentences. "We're not going to build this." "Not now." Plain words.
3. **The real reason.** One sentence that is true for this request and would not fit another.
4. **What they can do today.** A workaround or an existing feature, tested by you on the day. If there is nothing, say "there isn't a good way to do this today".
5. **What would change it.** For "later": the trigger, in words they can understand. For "don't build": only if there is an honest one. Never a date.

Under 100 words. No greeting paragraph, no closing paragraph.

## Never write

- "Great idea!" or "We love this" before a no. It reads as a yes until the third line.
- "It's on our roadmap", "we're considering it", "we'll pass it to the team", unless that exact thing is true and recorded.
- A date, a quarter, "soon", "in a future release".
- "Unfortunately, due to limited resources". Capacity is never the real reason; the real reason is that something else matters more, or that this adds more than it is worth.
- A workaround you have not tried.
- Another customer's name, or how many others asked by name.
- An apology for the decision. Saying no is not a failure by the company, so there is nothing to apologise for. Show instead that you understood what they need, in their words.
- A question that reopens it ("would it help if...?") unless you mean to do that thing.

## To a customer

Don't build:

    You want <their goal, in their words>. We're not going to build <the feature>: <the real reason, one sentence>.
    Today you can <what works, where to find it>; I checked it on <date>.
    <One honest line on what would change this, or nothing.>

Build later:

    You want <their goal>. We're not building <the feature> now: <the real reason>.
    Until then, <what works today>.
    We'll look again when <the trigger, in plain words>. You're on the request, so you'll hear from us if that changes.

Not a product problem (it already works, or the fix is elsewhere):

    You want <their goal>. You can do that today: <the steps, in order, tested on <date>>.
    <If it was hard to find:> That should have been easier to find, and I've passed that on.

## To a teammate, on the task

Bots and people on the team get the same five parts, plus the ledger row, and less cushioning:

    Not building: <item>.
    Problem as I read it: <one sentence>.
    Reason: <one sentence>. Evidence: <thin or pattern>, <n> accounts, <sources>, <date>.
    Today: <what works>.
    Back if: <trigger>. Ledger: knowledge/not-building.md, "<row title>".

Then close the task, or snooze it to the look-again date. If a person asked for the task, ask them to close it.

## To the owner, about the owner's own idea

Say it once, with a way forward, worded so "yes" takes your recommendation:

    Before I spec <idea>: the evidence is <thin: n accounts / none yet>, and it would add <weight in counts>.
    The same problem looks solvable by <smaller thing>. Recommended: I do <smaller thing> now and park <idea> with the trigger "<trigger>".
    Yes to go that way, or tell me to build <idea> as you described and I will.

Whatever the answer, record it with the date as the owner's decision. Do not raise it again.

## When they push back

- **New information** (another account, a cost you did not know, a broken workaround): thank them for the specific thing, add it to the ledger row, and re-run the verdict once. Tell them the result either way.
- **The same request again, louder**: one reply. Repeat the answer and the reason in fresh words, and name what would change it. Do not add reasons; a second reason sounds like the first was not real.
- **A threat to leave**: this is now a retention matter as well as a product one. Keep the product answer as it is and hand the account to whoever owns customers (the Success or Support Owner) on a task, with the ledger row.

## Check before sending

- Would the person recognise their own problem in sentence one?
- Is the answer in the first two sentences?
- Could the reason be pasted into another reply? If yes, rewrite it.
- Did you test what you told them to do today?
- Is there any promise, date or "roadmap" in it? Remove it.
- Is the ledger row written?
