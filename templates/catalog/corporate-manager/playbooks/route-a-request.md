# Route a request

Use this when a request reaches the corporate team with no owner, or is stuck with the wrong one.

1. Read the request: `tico task show <id>`. Note what it asks and who it names.
2. Find the owner in `knowledge/team.md` (from `tico team show --team corporate`). Use the lines in AGENTS.md under "The
   corporate team's lines".
3. If one owner clearly holds it, comment on the task with the owner's name and why. Move the request only if the task asks
   for it and a granted Tool allows it. Otherwise ask the person who filed it.
4. If no role in the team fits, say so on the task and route it to a person named in `knowledge/team.md`.
5. Never sign, pay, file or send for anyone, and never assign a person.

A task that stopped in a pipeline you manage is not routed with this playbook: decide it with
`playbooks/decide-a-stuck-task.md`.
