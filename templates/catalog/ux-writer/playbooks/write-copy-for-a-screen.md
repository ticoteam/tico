# Write copy for a screen

Triggered by a task that links a spec or a design and asks for the words. Budget 30 minutes per screen.
The outcome is a copy table engineers can paste from, with every state covered.

---

## 1. Read the spec

    hub task show <id>

Read the spec's problem, acceptance criteria and edge cases. List every state the screen can be in:
empty, loading, filled, error (each kind), success, no permission, offline. A state the spec does not
describe is a question for the Product Manager, not a guess. Follow `skills/cover-every-state-of-a-screen/SKILL.md` for the list.

## 2. Check the words

Use the glossary's names. If the feature needs a new noun, propose one with the reason and two
alternatives, and check `hub doc ask "what do we call <thing>?"` so help articles and product agree.

## 3. Write the table

`copy/<spec-slug>.md`: one row per element and state: screen, element (title, label, helper, button,
error, empty state, toast, notification), the string, character limit if any, and a note for engineers
(variables, plural forms). Errors follow `knowledge/error-rules.md` and `skills/rewrite-an-error-message/SKILL.md`. Offer one alternative only where a
choice is real.

## 4. Read it as the user

Read the flow top to bottom as someone new: does each step say what happens next? Is any word used two
ways? Cut every word that does not help.

## 5. Hand over

`hub file publish copy/<spec-slug>.md --task <id>`, commit, and `hub task update <id> --status done
--note`: the path, new terms proposed, and questions for the Product Manager.
