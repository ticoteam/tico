---
name: write-a-delivery-plan
description: Turn a requested outcome that spans repositories into a delivery plan with checkable acceptance checks, one child task per repository or area with an engineer who has access, ordered dependencies, and any release step named on the parent. An area with no owner becomes a question, never a guessed owner.
---
# Write a delivery plan

When to use: when a request asks for a change across one or more repositories and needs a parent task and child tasks.

Steps:
1. Restate the outcome in one sentence: what is wanted, who asked, and the date it matters if one was given.
2. Write each acceptance check so someone can run or observe it: "the account page shows the renewal date for a test
   account", not "the account page works well". A first plan with more than six checks is probably two outcomes.
3. List each repository or area the change touches. Take each owner from `knowledge/delivery.md`. An area with no owner
   is a question for the requester.
4. Check access: read each candidate engineer bot's repository grants with `hub bot repos <slug>`. `hub team show` does not
   list them; its `access` field is only your own access to each bot. Find an engineer bot with write access to each
   repository. If none has it, or you cannot read a bot's grants, still write the child, and say on the parent that
   access is needed.
5. Create the parent task with the outcome and its acceptance checks. It stays open until every required child is done.
6. Create one child per repository or area with `hub task child`. Each child carries the repository, the expected
   behaviour, its dependencies and its checks.
7. Order the dependencies: which child must merge before which. Name any release step as its own item on the parent.
8. Assign each child to its engineer bot, and write the plan on the parent with `hub task comment`.

Checks before you finish:
- Every acceptance check can be run or observed.
- Every child names a repository, an expected behaviour and at least one check.
- Every dependency is ordered, and no child depends on work that is not in the plan.
- Every area without an owner is a named question, not a guessed owner.

Example (invented, for illustration): "Show the renewal date on the account page." Checks: the account page shows the
renewal date for a test account, and the API returns the same field. Children: the web repository, owned by the web
engineer bot, and the API repository, owned by the API engineer bot. Dependency: the API child merges first. Open
question: the mobile repository has no owner, so it is not in the plan.
