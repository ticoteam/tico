### Added
- **Five company models for the setup org chart** replace the one 22-bot starter pack: Everything (73 seats), Software
  company (51), Services firm (38), Local service business (41) and Online store (39), with Legal, People, Operations
  and Leadership and bigger Finance, Sales, Marketing and Support teams. The first chart starts from the model the
  company's signals point to (new signals: sells services, on-site jobs, sells products; none of them is Everything),
  adds every department head it lacks and the roles that fit what the visitor said, and drops nothing. New setup
  actions switch the chart to a model (`model_use`; "show me everything" is Everything) and put a person in a seat
  (`seat_fill`, "Ana runs our cap table"). Controller, Equity Manager, Fundraising Manager, Scheduling & Bookings and
  Quality & Inspections are seats with no bot yet: a person can hold one, and importing the company file makes no bot
  for it or for any seat a person holds (the coworkers who hold seats stay in the saved plan; they join in Tico). The
  cap table moves from the Corporate Secretary's description to the Equity Manager's. A whole model chart is over the
  8 KB the installer can carry today, so `setup` asks for it to be imported in the app until the plan-bucket
  transport lands.
