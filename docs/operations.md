# Operations

**Unreleased:** this page describes Operations as it ships after the cleanup releases.

Operations keeps the records a business runs on: **locations**, **products and services**, **projects** and **equipment**,
plus **due dates** that open a task before they lapse. A record is kept only when something uses it: a P&L sums it, a due
date opens a task, a tool syncs it, or access is limited to it. Procedures, manuals and contacts stay in the Library and
link from the record.

## Tabs

| Tab | Holds |
|---|---|
| Locations | Stores, offices, warehouses, plants and customer sites: address, parent, customer, open purchase orders, equipment there, and this month's P&L |
| Products & services | Items bought or sold: name, SKU, kind, price, on hand, description |
| Projects | Jobs and builds: status, customer, and the money on them |
| Equipment | Things that need service: owned machines, vans and trucks (the VIN is the serial), and customers' equipment; kind, location, customer, make, model, serial, next due date |

The tabs shown depend on the business type set in Settings → General:

- Field service, manufacturer, construction or contractor: all four tabs.
- Restaurant or food, multi-location services: Locations, Products & services and Equipment.
- Software or professional services: Products & services and Projects.
- Retail or online store, other, or skipped: Operations is hidden.

A company that already has location, item, project or equipment records, a pending import or a connected Operations tool
keeps Operations shown when it upgrades. Nothing else changes.

## Who sees and changes records

- Teammates see location, project and equipment names, addresses, kinds and due dates. Guests see Operations records only
  when a task or doc shares one.
- The **records right** lets a person add and change records. The owner and Admins have it by default and grant it to
  others the way they grant Finance rights. It is separate from Finance's book rights.
- Changing a price also needs a Finance admin.
- Money on a panel follows Finance view access. A person who sees part of a location's lines sees that part, labeled
  **Your view**, never a full total.
- Attachments follow the record they are attached to.

## Hiding sections

Settings → General → Sidebar. The owner and Admins choose which optional sections show for the whole company: Strategy,
Growth, Finance, Operations, Meetings, and each Operations tab.

Hiding is display only. Data, the API, the CLI, bots and due dates keep working. Sidebar links, ⌘K entries and shortcuts
go away; links from elsewhere still open the page. Admins see a muted **Hidden** row at the bottom of the sidebar that
opens the Sidebar settings. Others do not see it. Permissions do not change.

## In books

A location marked **In books** maps to a Department or tracking option in the connected books, so P&L lines tagged with it
roll up there. Stores, offices, warehouses, plants and other default to on. Customer sites default to off and stay local. Where the books own a name, renaming it in the books renames the record.

## CSV import

Locations, products and services, equipment and due dates can be imported from a CSV file. The import shows a preview
first: errors per row, and likely duplicates (same address, serial or SKU, or a customer name close to one already here)
for a person to decide. An import adds rows. It does not overwrite fields on records already in Tico. The first line
names the columns; a column not listed here is ignored.

- **Locations:** `name` (required), `kind`, `street`, `city`, `state`, `postal_code`, `in_books`, `customer`.
- **Products and services:** `name` and `kind` (required), `sku`, `unit`, `price`, `description`, `taxable` (yes or no).
- **Equipment:** `name` and `kind` (required), `location`, `customer`, `make`, `model`, `serial`, `installed_on`,
  `warranty_ends_on`.
- **Due dates:** `name`, `subject_type` (location, equipment or contact), `subject`, `repeats` (once, weekly, monthly,
  quarterly, yearly, every_days or every_months), `every_count`, `starts_on`, `lead_days` and `assignee` (a teammate's or
  a bot's name), all required except `every_count` and `lead_days`.

A `customer` names a customer on the books. A name the books lack is added as a customer by the import, once per file; the
preview counts those new customers. A name close to a customer or contact already here is a likely duplicate: it is not
linked to that one, and its line imports only when a person names it. In a book that QuickBooks or Xero mirrors, add the
customer there first.

## Due dates

A due date sits on a location, a piece of equipment or a vendor. It opens a task before the date, so warranties, permits,
licenses, vehicle registrations, insurance, service agreements and contract notice deadlines do not pass unnoticed.

Repeats (weekly, monthly, quarterly, yearly, or every N days from the last completed visit) are one due date. Each
occurrence completes and reopens on its own. A due date on archived equipment or a location opens no tasks.

## Bots

The Operations bots read records through the [Finance tools](finance-tools.md). Each reads its `knowledge/records.md`
first and names the record an answer came from. Two workflows run through them:

- **Nothing lapses.** A due date opens its task ahead of the deadline, and the bot drafts the offer, call list or vendor
  booking. A visit or renewal completes when it is done. Nothing is marked done that was not done.
- **Buy with control.** A quote, a need or a low-stock note becomes a draft purchase order. Approval follows the company's
  rules. A bot does not split an order to stay under a limit.

A photo of an equipment data plate becomes a drafted equipment record. A person confirms it before it is saved.

The Inbox bot can read a Square or Toast daily summary email, or a sales export attached to a task, and list each store's
sales and orders each morning. A store with no summary is reported as missing, not filled from an earlier day.

Bots draft messages to people outside the team until mail sending is turned on for that bot.

## Related

- [Finance tools](finance-tools.md): the `hub finance` commands the bots use.
- [Bookkeeper](bookkeeper.md): keeps the books that In books maps to.
- [Navigation](navigation.md): where each tab and setting lives.
