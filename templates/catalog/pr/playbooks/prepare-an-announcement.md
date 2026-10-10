# Prepare an announcement

Triggered by a task like "we launch X on the 14th, can we get press?" or "we closed our funding
round". Budget 60 minutes. The outcome is a press pack on the task: release, targeted pitches,
spokesperson briefing and a plan, every outbound item ready to act on.

---

## 1. Find the story

    hub task show <id>

Ask what is actually new and who it matters to. A feature is rarely news; a number, a trend the
team can show with its own data, a customer outcome or a first often is. Write the angle in one
sentence. If there is no story for press, say so and suggest the team's own channels instead.

## 2. Check the rules

Read `knowledge/rules.md`: what may be named, who may speak, what must wait. Anything not cleared is
marked "needs clearance" and a question goes to the owner with `hub task ask <id>`.

## 3. Write the release

`reports/<announcement>/release.md`: headline with the news, a first sentence that could stand alone,
the facts in order of importance, one quote from the spokesperson and one from a cleared customer
(each marked "facts supplied by <owner>"), the boilerplate from `knowledge/company.md`, a contact line.
Under 500 words.

## 4. Choose the reporters and write the pitches

Five to ten from `knowledge/media-list.md` whose recent work fits the angle. One pitch each in
`reports/<announcement>/pitches.md`: why them (their article, dated), the news in one line, what you
can offer, the embargo if the owner agreed one. Under 150 words each.

## 5. Brief the spokesperson

`reports/<announcement>/briefing.md`: the three messages, likely questions with suggested answers,
what not to discuss, numbers they may use.

## 6. Put it up for review

On the task: the pack, the send plan (who, when, one follow-up after two business days). Send requested pitches with their recipients and text, and publish requested releases, with your Tools when a person has turned mail sending on in Tico; otherwise keep drafts. `hub task update <id> --status done --note`.
