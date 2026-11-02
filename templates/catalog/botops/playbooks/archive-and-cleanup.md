# Archive, restore, delete and clean up

Inputs: the requester's chat message or task asking to archive, restore, delete or clean up a bot, a repository or a credential, and what it names.

Done when: each named item is archived, restored or deleted as asked and checked against the server's answer; anything blocked is listed on the task with the one step that remains.

## Archive is the removal
"Delete" or "remove" a bot means `hub bot archive <slug>`, the app's Remove action. Say that its history stays. Archiving
removes its Routines and placement and may revoke its External agent Credential; restore does not recover those. Verify the bot
is archived, and the Credential is deleted, when asked.

## Restore
Restore only on an explicit restore request. A new-bot request never restores an archived namesake: check
`hub api GET bots?include_archived=true` before choosing the slug, pick a fresh one if it exists, and name it in the report.

## Deleting a repository
Only when the Team Owner asked. Use `hub api DELETE github/repos/<owner>/<repo>` as the requester. It uses the GitHub App's
Administration permission without a Confirm step. Only `deleted: true` proves it was deleted; a limited identity's 404 does not.

## Git
Never force a push. Delete a branch only once it is merged.

## When cleanup is blocked
If a click or an unsupported operation blocks cleanup, finish what is allowed, pause bots, disable unused Routines, revoke
grants, and list exactly what remains. Revocation is not deletion; a pending card is not done.
