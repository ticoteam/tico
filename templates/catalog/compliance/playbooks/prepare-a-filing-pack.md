# Prepare a filing pack

Triggered by the weekly calendar when an obligation enters its lead time, or by a task naming one. Budget 20
minutes. The outcome is a pack the owner can file from in one sitting. You never file, pay or sign.

---

## 1. Find the rule and last year

    hub task show <id>
    hub doc search "<authority> <obligation>"

Read the register row, last year's filing or confirmation (`knowledge/proof.md` says where), and the authority's
current public page for the form, the fee and the due date (`hub doc fetch <url>`). Cite each with its date.

## 2. List what the filing asks for

Usually: legal name and entity number, principal and registered office address, registered agent, officers or
directors, members or shareholders where asked, business activity, and the fee. For a licence or permit: the
premises, the licensed activity, certificates and inspections. For an insurance renewal: the broker's
questionnaire items (revenue, headcount, locations, claims).

## 3. Fill from the record and mark changes

For each item, last year's answer and its source, then whether anything changed since (a new office, a new
officer, a moved registered agent, a headcount jump). `hub doc ask "<the fact>"` when the docs should
know. A value you cannot source is `[NEEDS:...]` with who would know. Never guess an officer, an address or a
number.

## 4. Hand over

Save `reports/packs/<obligation>-<year>.md`: due date and rule, the owner, the fee, where to file (the official
page), the filled items, changes, and gaps. `hub file publish` it and put it on the task: "Pack ready for
<owner>. Summary for a human, not legal advice; a human files." When the owner files, ask them to attach the
confirmation, then record it in `knowledge/proof.md`.

## Done when
- `reports/packs/<obligation>-<year>.md` names the due date with its source and the date read, the fee, the official page, and the owner.
- Every item is filled from a sourced value, or marked `[NEEDS:...]` with who would know.
- The pack is published and on the task, and the owner has been told a human files it.
