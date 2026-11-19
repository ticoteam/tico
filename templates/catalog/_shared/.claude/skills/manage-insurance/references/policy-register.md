# The policy register

The register lives in `knowledge/insurance.md` under "Register". One block per policy. Fill it from the policy
document itself. A field not in the document read is "not found in the document read"; ask the broker and note the
date asked.

## One policy

```
Policy:              (kind, e.g. general liability)
Carrier:             (the insurance company's legal name, from the declarations page)
Policy number:
Broker and contact:
Named insureds:      (every entity, exactly as written)
Period:              (from, to, and the time and place expiry is measured)
Form:                occurrence / claims-made
Retroactive date:    (claims-made only; "none shown" is a finding, not a blank)
Limits:              (each claim or occurrence / aggregate / sub-limits that matter)
Retention:           (deductible or self-insured retention, per claim)
Defence costs:       inside the limit / in addition to it
Premium:             (amount, and what it is based on: revenue, payroll, units, flat)
Audited:             yes / no  (is the premium adjusted after the term)
Where it applies:    (states, sites, territory; for workers' compensation, the states on the declarations page)
Endorsements:        (number and one line each: additional insureds, waivers, exclusions added)
Main exclusions:     (the three or four that touch this company)
Notice of claim:     (to whom, how, by when, quoted with its section number)
Notice of change:    (what the company must tell the carrier mid-term)
Cancellation:        (notice the carrier must give; notice the company must give)
Claims contact:      (phone or portal; breach hotline for cyber)
Required by:         (which contracts, lease, loan or law need this policy)
Expiry / start renewal on:
File:                (where the full policy is stored)
Read on:             (date, and by whom)
```

## Putting the dates on the calendar

For each policy create an automation that opens the renewal task 120 days before expiry. `tico automation guide`
prints the full format. Save it with `tico automation create --file -`, sending the JSON on stdin. An example:

```json
{"name": "Renewal: cyber and E&O policy", "owner": "bot:<this bot's slug>", "enabled": true,
 "trigger": {"type": "due", "first_due": "2027-02-14", "repeats": "yearly", "open_days": 120,
             "tz": "America/New_York"},
 "steps": [{"id": "renew", "type": "bot_turn", "enabled": true, "bot": "<this bot's slug>",
            "prompt": "Renew the cyber and E&O policy. Follow manage-insurance, steps 9 to 12. Check the expiry date against the policy first."}]}
```

## Reading a policy in order

1. **Declarations.** Insureds, period, limits, premium, the list of forms and endorsements that make up the policy.
   Check the list against the pages you actually hold; a missing endorsement is a question for the broker.
2. **Insuring agreement.** What triggers cover, in the policy's own words.
3. **Definitions.** "Claim", "insured", "professional services", "computer system", "employee" each decide real
   outcomes. Note any that leave out something the company does.
4. **Exclusions.** List the ones that touch the company's actual work.
5. **Conditions.** Notice, cooperation, consent to settle, other insurance, cancellation.
6. **Endorsements.** They override everything before them. Read every one.

## Rules

- Quote clause text exactly with its section number; do not paraphrase a notice clause.
- Entities matter. A subsidiary, a new entity or a changed company name that is not a named insured is a gap.
- Keep expired policies. Occurrence policies can respond years later; store them and list them under "Expired, kept".
- Record what the company gets through others (a payroll provider's workers' compensation programme, a
  marketplace's seller cover) as its own block, with who controls it.
- Once a year ask the broker for loss runs (the carrier's record of claims) for each policy and store them.

## Quarterly gap check, in five questions

1. Has anything on the trigger list happened: first employee, new state, new site, new product, new entity, revenue
   or payroll far from the estimate, new data types, a contract with new insurance terms?
2. Does every contract requirement still have a policy and endorsement that meets it?
3. Is any certificate, in or out, expiring within 60 days?
4. Is any policy inside its renewal window without a renewal task?
5. Is any claim or incident open without a next date?
