# Change a listing

Used by the weekly review (step 5) and by any task that asks for a listing change or a store test. Budget 25 minutes.
Nothing here publishes to a store: a person does, with the text this playbook produces.

Outcome: one dated draft in `knowledge/drafts/`, one entry in `knowledge/changes.md` with status "proposed", and a task for
the person who publishes it.

---

## 1. Name the element and the reason

Name the store, the element (title, subtitle, short description, keyword field, description or one screenshot caption), and
the reason in one sentence with the number that prompted it, from the export.

## 2. Check the keyword, if one is involved

Follow `skills/check-a-keyword-before-using-it/SKILL.md`. A keyword that the store's results do not show for the listing is not
added. Record the check's date.

## 3. Draft the exact text

Write the new text in the team's voice from `knowledge/company.md`, within the store's current limit for that element. Read
the limit from the store's own page and record it with its date in `knowledge/store-rules.md`. A text over the limit is not
drafted; a shorter one is.

## 4. Check the claims

Run the text through `hub brand check -` and fix every hit. Then check each claim against `hub brand show --section claims`
by hand. Every number, award, ranking, "free", "fastest" or "number one" needs a dated source the team owns. Without one it is
removed, or it is marked for the owner. Do not fill the gap.

## 5. Decide the route

- A change a person publishes directly: save the draft, and create a task for that person with the exact text, the store and
  the element. Record the publish date when they confirm it.
- A store test: name the store's own experiment tool, the variant text, the split, the primary metric (impression to install),
  the sample and the stop rule. Write the stop rule before the test starts, and log it as "proposed" until a person starts it
  in the console.

## 6. Set the check

Write the date the change will be read: 28 days after a person publishes it, or the end of a store test. The read uses the same
store's export and the same window length as the change's "before" number. Record it in `knowledge/changes.md`.

## 7. Log it

Add the change to `knowledge/changes.md`: store, element, status, the text's draft path, the reason, the number before, the
check date. Never record a change as published until a person has said so.
