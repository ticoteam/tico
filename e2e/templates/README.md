# Templates end-to-end

`backend/tests/test_e2e_templates.py` takes each core template and makes a company from it on a temporary database:

1. `clients/catalog.materialize` writes `<workspace>/bot-<slug>` and commits it; `catalog.check` must return no problems.
2. The bot is created through `POST /api/v2/bots` with that template, marked onboarded by its owner (a new bot is still
   onboarding and takes no task until a person above it on the org chart says so), and read back.
3. The first task given to the bot queues a job for it.

The core set is the templates whose card has `suggest: default` in `templates/catalog/*/card.yaml`, plus `botops`, which
every company gets. The slug is the template's name. `test_the_core_set_is_the_default_suggestions_and_botops` fails if
the set shrinks below 19 or loses BotOps, so a card renamed in a cleanup shows up here.

## Run

    python3 -m pytest -q -o addopts='' -m slow backend/tests/test_e2e_templates.py

About 27 seconds for the 21 core templates.
