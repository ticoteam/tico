# Records

How the company's operating records work in Tico, and how you read and change them. The company's own names, numbers and
people are in `knowledge/company.md`; this file is about the records themselves.

You reach them through the Finance tools: `hub finance search`, `hub finance act` and `hub finance action` (MCP
`hub_finance_search`, `hub_finance_act`, `hub_finance_action`). Every call runs as you, with your own rights and view.

## What the records are

- **Locations**: a store, office, warehouse, plant, customer site or other place. A location can sit under a parent.
- **Products and services** (`item` records): name, SKU, kind, sale price, description, taxable. On-hand stock comes from the
  owning system (QuickBooks), is read only, and carries the time it was synced.
- **Projects**: a piece of work for a customer, open or closed. Income and costs are tagged to it in Finance.
- **Equipment**: owned (a machine, a van or a truck) or a customer's (the furnace in a customer's building). Make, model, serial,
  install date, warranty end, location, customer, photo. A van or truck is owned equipment with its VIN as the serial, never a location.
- **Due dates**: a date that opens a task, on a location, a piece of equipment or a vendor. Each occurrence completes on
  its own and keeps its history.

## Find a record

- `hub finance search --kind <kind> --q <words>`. Operations kinds: `location`, `item`, `project`, `equipment`, `due_date`.
  Finance kinds: `contact`, `bill`, `invoice`, `po`, `estimate`, `entry`, `feed`, `rule`, `card`. Name a kind to page with
  `--cursor`. `--active true` leaves archived records out.
- An answer shows only what you may see. A missing record is not proof it does not exist: say you could not see it.
- Cite each record by its kind, id, label and the time you read it, plus the `source` and `as_of` the answer gives. Do not
  name a source the answer does not give.

## Change a record

- Every change is one action: `hub finance act <kind> --payload '<json>' --operation-id <key>`. `hub finance act help` lists
  the kinds this server has, and `hub finance act help --payload '{"kind":"<kind>"}'` gives one kind's fields. That list
  counts. A kind named here that it does not list is not available: say so on the task.
- Make the key from what you act on, such as `po:<vendor>:<number>`. A retry with the same key never acts twice. A `denied`
  or `failed` key stays that way; a new attempt needs a new key, after you have checked the record did not change.
  `--dry-run` says what would happen and changes nothing.
- Results are `committed`, `needs_approval`, `denied` or `failed`, with a reason. `hub finance action <id>` shows one later.
  `needs_approval` is a request, not a success: say what you asked for on the task and move on. `denied` is final for the
  run; do not retry in another shape.
- Changing a location, product or service, project, equipment or due date needs the records right (`finance.records`), which
  the owner and admins hold and can give. Without it your change waits as a request for someone who has it. An item's price
  also needs a finance admin.
- An archived record cannot be edited. Ask for it to be unarchived; do not work round the refusal.

## Nothing lapses

A date that must not be missed is a due date on the record it belongs to, with a lead time: a warranty end, a permit or
license on a location, a van's registration or insurance, a maintenance agreement, a vendor's notice deadline. A vendor's
notice is its renewal date less its notice period, so the task lands before the cancel-by date, not the renewal date.

- A due date opens a task before it is due, for its assignee. Name the assignee when you create one.
- The task asks for the work: a draft offer, a call list or a vendor booking. Drafts wait for a person.
- Complete an occurrence with `due_date_occurrence_complete` only with its evidence (a visit note, a renewed document, a
  certificate), and say what it was. Nothing is marked done that was not done. Reopen an error with
  `due_date_occurrence_reopen`.
- A task listing several due dates does not complete them. Each completes on its own; closing the task with some open
  leaves those due.
- A schedule that counts every N days moves from the day it was actually completed. Monthly, quarterly and yearly schedules
  keep their calendar dates. A due date on archived equipment or a location opens no task.
- A register or checklist file that holds the same date points to the record rather than copying the date.

## Buy with control

A quote, a need or a low-stock note becomes a draft purchase order, and a person approves it.

1. `po_create` with the vendor, the PO date and the lines, each naming its item, location and project where you know them.
   A draft holds no budget.
2. Approval is a person's call. Leave the draft and say on the task what needs approving and why. Call `po_approve` only
   when the task asks for it and a granted tool allows it. Approval holds each line's unbilled amount in its budget; the
   company's rule decides who approves (the default asks the owner for an order over $1,000).
3. `po_send` only marks the order sent; nothing is emailed. Sending to a supplier follows the Sending rules in AGENT.md.
4. Bills match the order's lines in parts; it shows partial until closed. `po_close` releases what was not billed.
   `po_void` is for a draft, or an order nothing was billed against.
5. Never split one need into several orders to stay under an approval limit.

## From a photo to equipment

A technician's photo of a data plate (a furnace, a cooler, a machine) becomes a drafted equipment record. A person confirms
it before it is saved.

1. Read the plate: make, model, serial, and the install date if shown. For a blurred or cut-off plate, ask once for another
   photo on the task, and create nothing.
2. For a unit at a customer's site (kind `customer`), find the customer and site with `hub finance search --kind contact` and
   `--kind location`. If the site is not on record, ask on the task which one. Do not create a customer or site from the plate
   alone. A unit the company owns (kind `owned`) skips the customer lookup; find its location with `--kind location`.
3. `equipment_create` with kind `customer` when the unit is at a customer's site, or kind `owned` when the company owns it (a
   machine, a van or a truck). Give its location, make, model, serial and the photo; for `customer`, also the customer. Use a key
   such as `plate:<serial>`. It waits as `needs_approval` until a person confirms it.
4. Name the draft on the task with its serial and location, so the person can check it in a minute.

## Limits

- Costs, accounts, P&L, bills, purchase orders and estimates are Finance's. Read them through the Finance tools, cut to your
  view. If your view hides a figure, say so; do not estimate it.
- Share a customer's or vendor's details only where the task needs them.
- Change a record, approve, send or pay only when the task asks for it and a granted tool allows it, as AGENT.md says.
- Never give a customer a date that is not on the record.

## For this bot

- Vehicles are owned equipment. A van's registration and insurance are due dates on the van, with a lead time, so the task lands before the deadline.
- A carrier is a vendor. Its contract's renewal date and notice period are on its contact record.
- Shipments, exceptions and claims are not records. They stay in `knowledge/exceptions.md` and `knowledge/claims.md`.

## Sources

- 2026-11-01: Tico Operations build plan v1.0 (records, access, due dates, buy with control, photo to equipment).
- 2026-11-01: Tico Finance tools (hub finance search, act and action; action results; operation keys).
