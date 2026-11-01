# UX writing standards

Outside rules this team builds on, with the source and the date each was read. Labels: **primary** (the page was read
on the date given), **team rule** (ours; not a published standard). Quote a rule only from its primary source, and
cite the source and year in any rewrite that relies on it.

## Errors

- **Primary**: GOV.UK Design System, "Error message" (read 2026-11-01). Describe what happened and tell the person how to
  fix it, in plain English, with positive language, getting to the point. Avoid generic messages such as "Select an
  option" or "This field is required". The page advises against "valid", "invalid" and "please": "valid" and "invalid"
  add nothing, and "please" implies a choice the person does not have. Do not repeat an example in the message if the
  screen already shows one. Read the message aloud; if a listener cannot tell what to do next, rewrite it.
- **Primary**: W3C, Understanding WCAG 2.2, Success Criterion 3.3.3 Error Suggestion, level AA (read 2026-11-01). If an
  input error is detected automatically and the correction is known, the suggestion is provided to the person, unless
  that would jeopardise the security or purpose of the content. The companion criterion, 3.3.1 Error Identification
  (level A), is not read here; read the W3C page before you quote it.
- **Team rule**: say the problem, then the fix or the next step, in two short sentences. No codes alone. No blame. No
  exclamation marks.

## Capitalisation

- **Primary**: Microsoft Style Guide, "Capitalization" (page updated 2026-10-15; read 2026-11-01). Use sentence-style
  capitalisation. Capitalise the first word of a UI label such as a button or checkbox name, and lowercase the rest.
  Use title case only for product or service names and specific title types.
- **Team rule**: the same applies to headings and empty-state titles. Product names keep the capitals they are
  written with.

## Message types

| Situation | What the text does | Shape |
|---|---|---|
| Error: an input or action failed | Names the problem and the fix | One problem, one fix, in two sentences |
| Warning: an action has a cost | States the cost before the action, and the way out | "This also cancels three bookings. Move them first, or cancel anyway." |
| Confirmation: an action is done | Names the object and the result | "Class saved. It shows on Tuesday's schedule." |
| Empty state: nothing yet | Says what goes here and the one action | "No classes this week. Add a class to fill the schedule." |
| Hint: help before the action | Only where a person would otherwise guess | "Use the format 10:30 or 10:30 pm." |

The examples are invented. They show the shape only.

## One name per object

- **Team rule**: one noun per object in the glossary, with the words it must not be called. A word that names two
  things (a place in a class and a seat count, for example) gets two glossary entries.
- A rename changes every state, every error and every notification that uses the name. List each place before the
  change is proposed.

## Checks for any rewrite

- The rule it follows is named.
- The message type matches the situation.
- No "invalid", "valid" or "please".
- The object has the glossary name in every string.
- Sentence case for labels, buttons and headings.
