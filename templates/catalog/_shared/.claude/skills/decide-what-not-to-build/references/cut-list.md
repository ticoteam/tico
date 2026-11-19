# The cut list

Three checklists for steps 4, 5 and 7: what an addition weighs, how to cut a sketch, and the bar for what stays.

## 1. What an addition weighs

Count each line. Write the counts in the verdict comment.

**For the customer**

- New screens or panels.
- New settings, toggles, options or modes.
- New words they must learn (a new noun in the product is a new concept).
- New decisions they must make before they get a result.
- More to read past for every customer who does not need it.

**For the product**

- New states to design: first time, empty, one, many, the limit, loading, error, no permission, offline, done twice, two people at once.
- Existing features it overlaps, and which of the two a customer should now use.
- Existing data that must be changed or carried over.
- What it slows down: a page, a query, the setup.

**For the team, for as long as it exists**

- Tests to keep green.
- Docs and help pages to keep right.
- Support questions it will cause.
- One more thing every future change has to stay compatible with.

Rule of thumb: a setting weighs double. It is two behaviours to build, test and explain, and it moves a decision from the team to every customer.

## 2. How to cut a sketch

Ask of each part, in this order. One "yes" is enough to cut it.

1. If this is gone, does the customer still get the result?
2. Is this here for a customer we have not met? ("Someone might want...")
3. Is this a second way to do something the sketch already does one way?
4. Is this an option because we did not decide?
5. Is this for an edge case that under about one in twenty of the evidence accounts has? Then handle it plainly (a clear message, the old path) instead of building for it.
6. Could this be added later without redoing what ships now? Then it is "later", with a trigger.
7. Is this admin, reporting or export around the feature, not the feature itself?
8. Did this arrive during design, with no evidence line of its own?

What must never be cut to make something fit:

- The path the evidence accounts actually take, from start to result.
- What happens when it fails (an error the customer can understand and recover from).
- The customer's existing data still working.
- Undo or a confirmation for anything that cannot be reversed.
- Privacy, permissions and security.

## 3. The quality bar for what stays

Whatever survives the cut ships finished. Before a part is called in scope, each line below is true or the part is cut smaller.

- **Every state it touches is designed**: empty, loading, error, full, no permission. None falls through to a blank screen or a raw error.
- **It works on a phone and on a desktop**, at the widths the team's pages are checked at.
- **The words are the customer's.** Labels use the terms customers used in the evidence, not internal names. No label needs a tooltip to be understood.
- **It looks like the rest of the product.** It reuses existing components and patterns. A new component is itself an addition and needs its own reason.
- **One obvious next step on every screen.** No dead ends, no "now go to Settings".
- **It is fast enough to feel instant** where the customer is waiting, or it says what it is doing.
- **Nothing is half there.** No "coming soon", no greyed-out control for a part that was cut. A cut part leaves no trace.

If the appetite cannot cover this bar for the current scope, the scope is still too big. Cut again. The finish is never what gets cut.

## 4. Smells that an addition should not pass

- The reason given is a competitor's feature list.
- The reason given is one large account, and nobody has checked whether others share the problem.
- The request is for a setting "so everyone is happy".
- The problem sentence cannot be written without the feature's name.
- Nobody can say which number would move.
- "While we're in there" or "it's only a small thing".
- It makes the first-run experience longer for every new customer.
