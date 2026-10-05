# Temporary task assignments

A persistent role can allocate one existing delivery task to a temporary actor with its own Tico session and a separate local Git clone/branch. The assignment uses the source role's model and runtime, but its display name is only a label: identity is the source role, stable assignment key, and generation. Retired keys stay bound to their original task.

## Authority and setup

The source bot must have **Allow branches** enabled and already be assigned to an online computer whose heartbeat advertises `assignment_instances_v1`. The actor uses that same computer; it cannot be transferred through the legacy bot assignment route. Only a human manager of the source role can enable its direct-parent allocator policy. The allowed bot allocator is exactly the source role's current `reports_to` bot. A human manager may allocate directly where the task is one they requested and is already owned by the source role. Allocation uses Tico's existing task permission checks and changes only that task's owner.

The runner keeps task work at `projects/assignments/<actor>` with branch `assignment/<actor>`. It is a separate clone with a Git common directory distinct from the source role. The reusable-learning worktree is `projects/assignment-learning/<actor>` with branch `assignment-learning/<actor>`; it shares the source role's Git common directory and starts at the explicit `origin/HEAD` remote-tracking ref. A local registration marker binds both paths, both branches, the assignment id, task, source, generation, repository, common directories and learning trunk. The runner reports the actor ready only after both trees pass registration, `AGENT.md`, `HEAD`, exact branch, common-directory and tracked-file checks. Dirty and untracked work stays in place. An ambiguous or incomplete tree is left untouched and reported not ready.

At a safe assignment checkpoint, the runner fetches the registered trunk ref and fast-forwards a clean learning worktree only. Dirty drafts or a diverged branch are retained and shown as a readiness warning; no reset, rebase or force-push runs. The temporary actor sees this worktree for generalized lesson drafts, but cannot publish it. A human manager uses **Review reusable lesson** (or `--reviewed-learning-file` plus `--confirm-learning-review`) to send only explicitly reviewed text to the persistent role in a non-task notice. That notice has no assignment task transcript, code, attachments or customer context. The persistent role publishes the reviewed lesson through its existing shared-checkout and explicit upstream flow. This separates task-code commits from lesson publication. The assignment actor never receives the source role's GitHub token. Archive and cancellation preserve local trees, registration and Hub history. A source-role manager can separately request cleanup after the terminal lifecycle step. The original runner rechecks for active local execution, exact registration and paths, changed/untracked/ignored files, and commits not already reachable on the corresponding origin/source trunk. Any uncertainty blocks cleanup and retains the remaining paths. The runner uses the source role's scoped read credential only to verify remote preservation; it never publishes, resets or force-pushes assignment work. Cleanup never removes the source role's checkout, trunk, actor record, task, receipts or lifecycle history.

## CLI

```sh
hub bot assignment list software-engineer
hub bot assignment policy software-engineer --enable --revision 4
hub bot assignment create software-engineer --task TASK_ID --name "Pro Workflow Engineer" \
  --key feature-123 --generation 1
hub bot assignment update ACTOR --revision 1 --note "Checkpoint: API implemented; tests next" \
  --checkpoint-json '{"next":"run focused tests"}'
hub bot assignment update ACTOR --revision 2 --reviewed-learning-file ./reviewed-lesson.md \
  --confirm-learning-review --note "Reviewed general lesson at checkpoint"
hub bot assignment update ACTOR --revision 3 --phase cancelled --note "Scope handed back to the source role" \
  --handoff-task FOLLOW_UP_TASK_ID
hub bot assignment cleanup ASSIGNMENT_ID --revision 5
```

The reviewer must confirm that the submitted lesson is generalized and excludes task/customer details, private conversation content and credentials. Repeated identical lesson text for one assignment is sent only once; the review message id is recorded in assignment history for the later learning receipt.

The key and generation are stable identifiers; do not reuse them for another task. A retry with the same identity and task returns the same actor. `waiting_review` and `waiting_release` free one of the three execution slots while preserving the actor, checkpoint, task, runner assignment and local tree. Paused, interrupted and offline work continues to count against capacity. Resuming from interrupted work requires a checkpoint. Archive requires a completed task plus deployed version, acceptance, reviewed-learning and evidence receipt references. The API also requires a human-reviewed reusable-lesson notice already recorded in assignment history; `learning_receipt` should cite the source role's resulting reviewed commit or handoff evidence. The API preserves that reference but does not observe a remote push. Cancellation requires a reason and returns the linked task to the source role. An optional handoff reference must name a separate open task already owned by the source role and requested by the caller; it is recorded in history without changing either task. Archive or cancellation only retires the actor. Guarded cleanup is a separate, explicit manager request sent to the original runner; busy, dirty, untracked, ignored, mismatched, incomplete or unpreserved work stays in place.

The REST API is `GET/POST /api/v2/bots/{source}/assignment-branches`, `PUT /api/v2/bots/{source}/assignment-branches/policy`, `PATCH /api/v2/assignment-branches/{id}`, `POST /api/v2/assignment-branches/{id}/cleanup`, and `GET /api/v2/assignment-branches/{id}/events`.
