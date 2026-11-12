A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme release notes draft, week of Mon 2026-09-21

Sample output for Acme, a fictional studio-software team. Every pull request is invented.
Nothing has been published, tagged or committed.

**Suggested version: 2.14.0 (a new feature, no breaking change). Range: 2026-09-14 to 2026-09-24, 14 changes. What users will notice most: waitlist reminders by text.**

## Changelog entry
### [2.14.0] - 2026-09-24
#### Added
- Clients on a waitlist can now be reminded by text message ([#398](https://github.example/acme/web/pull/398)).
- A studio can reset its booking link from Settings ([#405](https://github.example/acme/web/pull/405)).
#### Changed
- Invoices round each line item to the cent, then total ([#412](https://github.example/acme/web/pull/412)).
#### Fixed
- Class times no longer shift by an hour after a daylight saving change ([#401](https://github.example/acme/web/pull/401)).

## Customer-facing notes
**Reminders by text.** If a class is full, clients on the waitlist can now get a text when a spot opens.
Turn it on under Settings, then Reminders.
**Reset your booking link.** If a link was shared by mistake, reset it in Settings. The old link stops working straight away.
**Times stay right after the clocks change.** We fixed a bug that showed some classes an hour early.

## Could not classify (a human decides)
- [#417](https://github.example/acme/web/pull/417) "Update pricing constants": no description. Is this visible to customers, and is it a breaking change?

## Left out
- 6 changes: 3 dependency updates, 2 refactors, 1 test-only.

## Sources
- gh pr list (merged since 2026-09-14), gh release list, 2026-09-28; knowledge/versioning.md, knowledge/voice.md
```
