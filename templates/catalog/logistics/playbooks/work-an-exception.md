# Work an exception

Triggered by a shipment entering the exceptions queue, or by a task from `support` or a human:
"Order 4471 hasn't arrived." Budget 10 minutes. The outcome is the facts, the carrier case, and the
customer update ready to use.

---

## 1. Get the facts

    hub task show <id>

Order, carrier, tracking number, service, ship date, promised date, and the last scan with its time
(export first, then the public tracking page). Check `knowledge/exceptions.md` for an open case.

## 2. Classify

**Late** (moving, past promise), **stuck** (no scan for 3 working days), **returned**, **damaged**
(customer report with photos), **lost** (the carrier says so, or stuck past its investigation period).

## 3. Next step with the carrier

Late: note and check again tomorrow. Stuck: open a trace with the carrier. Damaged or lost: prepare a
claim with the value (invoice), the proof of shipment and the customer's photos, before the deadline.
Attach carrier messages to the task and send within the requested work when a person has turned mail sending on in Tico; otherwise keep drafts.

## 4. The customer update

Short and factual: what the tracking shows, what the team is doing, when they will hear next. No
new date the carrier has not given, and no reship or refund unless the named human has decided one.
Put it on the task for review; if the request came from `support`, give it to them to send.

## 5. Record

Update `knowledge/exceptions.md` (case number, customer told when, next check date). Close the row when
delivered, reshipped or refunded, with the date.
