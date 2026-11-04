---
name: check-a-demo-script-before-it-runs
description: Test a demo script step by step against what has shipped, the buyer's own workflows and the never-show list, the day before a demo and again whenever the script is reused for a new buyer.
---
# Check a demo script before it runs

When to use: the day before a demo, when a script is reused for a new buyer, and after a release that changes a screen the script shows.

Steps:
1. Read the script in `knowledge/demos/<deal>.md` and list each step as the screen it shows.
2. Check each step against `knowledge/never-show.md`. A step that shows a never-show item is removed or replaced before the demo, not shown with a caveat.
3. Check each step's feature against the public changelog and the docs. A step showing something unreleased is removed.
4. Check each step traces to one of the three to five workflows from discovery, in the buyer's words. A feature with no buyer workflow behind it is cut.
5. Check the data on screen: clearly invented data only. Never a customer's data, the buyer's own included, and never a redacted copy of one.
6. Run the script once in the sandbox or a test account the day before. Note every step that breaks.
7. Write down what was cut and why in the deal's technical note, so the next demo reuses the reasoning.

Checks before you finish:
- Every step has a workflow, a shipped feature and invented data behind it.
- No step depends on a feature flag the buyer will not have.
- The script still fits the time booked for the demo.
- The cut steps are written down with their reasons.

Example (invented, for illustration): "Step 6 shows a reporting screen added after the last changelog check and not in the docs. Cut: unshipped. Step 3 shows the buyer's scheduling workflow from discovery: kept. Data: the test account's invented classes. Result: four steps in twenty minutes, inside the thirty booked."
