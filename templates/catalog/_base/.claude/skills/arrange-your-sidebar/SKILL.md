---
name: arrange-your-sidebar
description: Change the sidebar on your own page when someone asks - pin a page, add a card such as your tasks by stage, hide or move Updates, Tasks and Files. Everyone who opens your page sees the same sidebar.
metadata:
  function: tico
---
# Your sidebar

Your page shows your chat with the sidebar beside it. When someone asks ("show my videos by stage at the top", "hide
Files"), arrange it yourself. It is shared: everyone who opens your page sees the change, so change it only when asked
and say in one line what you changed.

It holds the built-ins `updates`, `tasks` and `files`, then any pages and cards you pin, in the order you keep. Its place
is `bot:<your slug>`. A position counts everything there, built-ins too: 0 is the top, as `tico_card_list` lists it.

- See it: `tico_card_list` with that place (`tico card list --place bot:<your slug>`), for pin ids.
- Pin one of your pages: `tico_pin_add` with item `page:<file id>` (and `position` for where).
- Add a card: `tico_card_add` with the place, and `position` for where. Tasks by stage is a pipeline's open, not
  snoozed tasks grouped by stage, each with its first titles. At the top:
  `tico card add tasks_by_stage --pipeline Videos --agent <your slug> --place bot:<your slug> --position 0 --title "Videos by stage"`.
  Leave out `agent` for everyone's tasks in that pipeline; `count` is titles per stage (3 unless set).
- Move a page or card: `tico_pin_update` with its pin id and `position`.
- Hide, show or move a built-in: `tico_pin_update` with pin `updates`, `tasks` or `files` and `hidden` or `position`
  (`tico pin update files --hide`, `tico pin update tasks --position 0`).
- Remove a page or card: `tico_pin_remove` with its pin id.

A card shows each person only the tasks they may read, so two people can see different counts; a card with nothing to
show someone stays out of their sight.
