# New org readiness

What must be true in a brand-new organization for it to work from its first minute. Each item says where the default
is set, how a fresh org fails without it, and how it is checked.

Status: partial. Items marked "check: none yet" have no test that runs them on a fresh org. The first-day e2e test
(a fresh org from the default templates, Heartbeat and Maintenance on a fake clock, one Product to Engineering card)
is not written yet.

## How a fresh org is made

1. `clients/environments.py` `seed_registry` copies `templates/environment-registry/*.yaml` with the owner and company
   names filled in. Only `coo` (the Assistant) and `botops` are seeded, both `planned`.
2. `backend/manage.py` `initialize`: `Store.initialize()`, then `Store.seed(entries=resolved_registry(...))` and
   `enqueue_existing()`.
3. `backend/app.py` `create_app(settings)` boots the server on that database.
4. The owner saves setup picks (`PUT /api/v2/onboarding`, `backend/onboarding.py` `Onboarding.save`) and finishes
   (`POST /api/v2/onboarding/complete`, `Onboarding.complete`). Built-ins are created as Active bots; every other pick
   becomes a Recruiting seat (`Onboarding._lay_out`, `backend/lifecycle.py` `lay_out`).
5. A computer enrolls (`POST /api/v2/enrollments`, `POST /api/v2/runners/enroll`); `Onboarding.on_runner_enrolled`
   places the bots.
6. Pipelines: `backend/pipeline_setup.py` `apply_factory_setup` with preset `tico_default` (Product, Engineering,
   Marketing, Sales, Success, Legal; link Product Review spawns into Engineering). Route `POST /api/v2/pipelines/setup`.

## Checklist

### Bots and computers

- [ ] **The built-ins exist and are Active once a computer hosts them.**
  Set: `backend/onboarding.py` `Onboarding.complete` (cards with `required`/`bootstrap` in `templates/catalog/*/card.yaml`).
  Fails: with no computer the built-ins stay `planned` and nothing claims work.
  Check: `backend/tests/test_onboarding.py` `test_direct_signup_with_no_chosen_chart_is_the_built_ins_and_an_empty_chart`,
  `test_completing_onboarding_activates_the_bootstrap_bots_once_a_machine_hosts_them` (written catalog, not the shipped one).
- [ ] **A model provider is on and every bot resolves to it.**
  Set: `backend/providers.py` `bot_choice`; seeded from `TICO_ENABLED_PROVIDERS` / `TICO_DEFAULT_*`
  (`clients/environments.py` `server_values`). Bots carry no runtime or model of their own.
  Fails: bots are created but no turn can start.
  Check: `test_onboarding.py` `test_the_team_is_created_with_no_provider_and_no_computer_and_starts_once_they_exist`.
- [ ] **Gateway key present and not capped below a day's use.** Fails: model calls refused; Decide calls fall back to
  unchecked. Check: none yet.
- [ ] **Builder computers have the repository cloned before a build card reaches them.** Runner readiness reports
  `repository_present` per bot (`evals/factory/world.py` `World.readiness` mirrors it). Check: none yet on a fresh org.

### Pipelines and cards

- [ ] **Product and Engineering pipelines exist with the preset stages and the Review to Engineering link.**
  Set: `backend/pipeline_setup.py` `PRESETS["tico_default"]`, `apply_factory_setup`. Check: `backend/tests/test_pipeline_setup.py`.
- [ ] **A card waiting on CI is woken when its checks finish.** Fails: cards sit in waiting after green CI.
  Fix: PR #3070 (queued). Check: its test in `backend/tests/test_pr_checks_flow.py`.
- [ ] **The stage judge sees the work it judges.** Fails: the judge answers unsure or not met on work it cannot read.
  Fix: branch `chris/judge-sees-work`. Check: `backend/tests/test_pipeline_judge_eval.py` (judge_once with a fake engine).
- [ ] **Stage caps and On ice hold work without stranding it.** In progress elsewhere. Check: none yet.
- [ ] **No card waits with nothing that will wake it.** Check: none yet on a fresh org (the factory sim checks this on a
  hand-built Dev pipeline, `evals/factory/scenarios.py`, not the default templates).

### Rules and spend

- [ ] **Rule checks ask Jev (Decide Low) only.**
  Set on main: `backend/config.py` `rule_check_level = "medium"` (line 359, env `TICO_RULE_CHECK_LEVEL`), read at
  `backend/org_rule_check.py:502` and `:957`; `clients/judge.py` `decide_model` maps medium to `tico-decide-medium-1.0`.
  Fails: a fresh org spends its $50/month rules cap on the Medium model, then outputs go out unchecked.
  Fix: PR #3068 (queued) replaces the setting with a fixed `RULE_LEVEL` of low. Check: its tests in
  `backend/tests/test_org_rule_check.py`.
- [ ] **The monthly judge cap is saved and enforced.** Set: Maintenance `monthly_cap_usd`, else
  `TICO_DECISIONS_MONTHLY_USD` (default 50) (docs/maintenance.md). Check: `backend/tests/test_judge_spend.py`.

### System jobs

- [ ] **Heartbeat runs every 10 seconds.** Set: `backend/heartbeat.py` `beat`, called from `backend/scheduler.py`
  `Scheduler.tick`. Check: none yet on a fresh org.
- [ ] **Maintenance runs hourly and finishes.** Set: `backend/maintenance.py` `tick` (from `Scheduler.tick`), `_start`
  runs steps on a thread. Check: `backend/tests/test_maintenance.py`; none yet on a fresh org.
- [ ] **System jobs file no tasks on a person.** Health findings go to BotOps (docs/maintenance.md, Check health).
  Fails: the owner's Needs you fills with upkeep. Check: none yet on a fresh org.
- [ ] **Follow-up timers do not wake bots on waiting cards.** Fails: repeated wakes with nothing to do. Check: none yet.
- [ ] **Bots do not file many tasks on the person.** Check: none yet.
- [ ] **Bot memory files are capped.** Fails: state.md and learnings.md grow without bound. Check: none yet.

### Demo company

- [ ] **The demo seed makes no network call.** Fix: PR #3060 (queued). Check: `backend/tests/test_demo_company.py`.

## Left to do

- Fill in the "none yet" items' file and function references (Health Monitor task owner, follow-up timers, memory caps,
  gateway key caps, wake presets, budgets).
- The first-day e2e test: a fresh org built through the steps above with the shipped catalog, Heartbeat and
  Maintenance ticks on a fake clock (`time_machine`, Maintenance inlined as `evals/factory/world.py` does), one
  Product to Engineering card with fake model answers (`app.state.judge` replaced as in
  `backend/tests/test_org_rule_check.py` `Judge`), asserting no stuck cards, no person tasks from system jobs, no
  budget exhaustion, rule checks at level low only, bounded wakes and no server errors.
