# The lease abstract and the register

One abstract per site in `knowledge/leases/<site>.md`. One register row per site in `knowledge/leases.md`. Fill the
abstract from the signed documents, read in date order, with the clause number beside every term. Where an
amendment changes a term, show the current term and name the amendment. A term that is not there is "not found in
the documents read".

## The abstract

```
Site:                 (name used inside the company, and the address)
Kind:                 direct lease / sublease (in or out) / licence or membership / 3PL contract / address service
Documents read:       (each with its date: lease, amendments 1..n, commencement letter, guarantee, consents)
Landlord:             (legal name; notice address and method, clause no.)
Tenant:               (legal entity; notice address the landlord holds)
Guarantor:            (who, the cap, when it ends; or none)
Premises and area:    (rentable, usable, standard named, clause no.)
Permitted use:        (quoted)

Dates
  Commencement:       (date, and the document that fixed it)
  Rent commencement:
  Expiry:
  Renewal option:     (number and length; notice window "not less than X nor more than Y"; method; conditions)
  Termination right:  (date, notice, fee)
  Other options:      (expansion, first offer)

Money
  Base rent:          (schedule by period)
  Escalations:        (date and amount or formula of each)
  Operating expenses: (lease type; tenant's share; base year; cap; exclusions; gross-up; audit right and window)
  Deposit:            (amount, form, return terms)
  Allowances:         (improvement allowance, free rent, clawback)
  Late charge:        (amount, grace period)
  Holdover rent:      (rate, clause no.)

Obligations
  Insurance:          (coverages, limits, additional insureds, certificate timing; sent to manage-insurance on <date>)
  Repairs:            (tenant's / landlord's)
  Alterations:        (consent needed; removal at end)
  Surrender:          (condition required, quoted)
  Estoppel:           (deadline to return)
  Reporting:          (financial statements or other deliveries the landlord may ask for)

Flexibility
  Assignment and sublease: (consent standard; recapture; profit share; change of control)
  Relocation:
  Subordination:      (non-disturbance agreement in place? yes / no)

Default:              (notice and cure periods for money and other defaults)
Unusual terms:
Open questions:       (each with who would know)
Abstracted on:        (date; by whom; "not legal advice; counsel to confirm before acting on a date")
```

## Critical dates

For each date write: what it is, the clause, the date, the arithmetic, the lead date, the owner, the automation.

| Date | Default lead time (when the task opens) |
| --- | --- |
| Last day to give a renewal or termination notice | 12 months before |
| First day a renewal notice may be given | On the day |
| Expiry | 12 months before, and again at 90 days for move-out |
| Each rent escalation | 30 days before, so the forecast and the bill check are ready |
| Operating expense statement expected | On the usual month; then the last day to object, the day it arrives |
| Insurance certificate due to the landlord | With each policy renewal |
| Deposit reduction or return | 30 days before |
| Guarantee falls away | 30 days before; write to the landlord to confirm |
| Sublease expiry (always before the lease's own) | 6 months before |
| Membership or licence renewal and price change | The notice period plus 30 days |

Lead times are this skill's working defaults. Create each date as an automation with a due trigger and a turn for
this bot. `tico automation guide` prints the full format. Save it with `tico automation create --file -`, sending the JSON on stdin. An example:

```json
{"name": "Elm Street: last day for renewal notice", "owner": "bot:<this bot's slug>", "enabled": true,
 "trigger": {"type": "due", "first_due": "2027-04-02", "repeats": "once", "open_days": 365,
             "tz": "America/New_York"},
 "steps": [{"id": "decide", "type": "bot_turn", "enabled": true, "bot": "<this bot's slug>",
            "prompt": "Renewal decision for Elm Street. Follow manage-leases-and-real-estate, steps 15 and 16. Re-count the notice date from the lease first."}]}
```

`open_days` is how many days before the date the task opens.

## The register row

```
| Site | Kind | Landlord | Expiry | Last notice date | Monthly cost now | Users | Next critical date | Abstract |
```

## Rules

- Count a "not less than X days before" deadline backwards from the anchor date, write each step, and check whether
  the notice must be received or only sent by then. If the lease is silent on weekends and holidays, work to the
  last business day before.
- A date defined by an event ("the Commencement Date is the date Landlord delivers the Premises") needs the letter
  or email that fixed it. Without one, write "date not confirmed" and ask the landlord to sign a commencement
  letter.
- Re-abstract after every amendment. The old abstract is kept, marked superseded.
- Each quarter, check the register against the book: every site with rent in the book has an abstract, and every
  abstract's monthly cost matches what was billed.
