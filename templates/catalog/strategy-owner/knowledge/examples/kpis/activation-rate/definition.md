# Activation rate

Definition version: 1 (Tico's `definition_version`; update this file when a confirmed proposal changes it)
Unit: %, direction: up, cadence: weekly, owner: the Product lead

Of the accounts created in the period, the share that finished setup within 7 days of signing up.
- Numerator: accounts created in the period with `setup_finished_at` within 7 days of `created_at`.
- Denominator: accounts created in the period, test accounts excluded (`is_test = 0`).
- The period is Monday 00:00 to Sunday 23:59 UTC. An account created in the last 7 days is not yet countable:
  the week is `partial` until 7 days after its end and the reading says so.
