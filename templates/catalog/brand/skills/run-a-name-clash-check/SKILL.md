---
name: run-a-name-clash-check
description: Run a first-pass clash search for a proposed product, feature, campaign or company name across the trademark registers, app stores, company registries and the web, and list what was found without calling the name clear.
---
# Run a name clash check

When to use: a task proposes a name and asks whether it can be used. The result is a list for the owner and counsel, never a clearance.

Steps:
1. Write down the name, what it will name (goods or services), the market, and the countries. If any of these is missing, ask once with `hub task ask <id>`.
2. Check the naming rules in the brand card (`hub brand show --section naming`): descriptive or coined, length, how it reads aloud, and how it sits next to existing product names.
3. Search the registers for each target market. For the US, use the USPTO trademark search: an exact-match, single-term wordmark search first, then close spellings and sounds. For the EU, use EUIPO eSearch plus; TMview covers other national offices. For each identical or close hit, note its number, status and classes.
4. Search the app stores and the marketplaces the team will sell on for the same or similar names.
5. Search company registries in the countries and the web for the same name in the same field. Note the domain and the main social handles as a signal of availability only.
6. Write the list: each hit with its link, its date and why it is close. Say what you did not search.
7. Close with: "This is a starting point, not clearance. Legal counsel decides."

Checks before you finish:
- "Clear" and "available" appear only inside quoted search terms, never as a verdict.
- Each hit has a link and a date.
- The goods, classes and countries searched are stated.
- The searches not done are listed.

Example (generic): "Name: 'Zeltra' for a scheduling feature, EU and UK. EUIPO: two identical wordmarks in unrelated classes, one in the software class, status pending; listed. App stores: one scheduling app with a close name; listed. Not searched: national registers outside the EU and the UK. This is a starting point, not clearance. Legal counsel decides."
