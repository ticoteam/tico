# The vision question

The server asks the owner "What is this team for?" for you while the team has no vision: a task you hold, titled
"What is this team for?", with the question on it (a typed answer, no choices). Their reply to it wakes you. Never ask it
again yourself, and never write a vision of your own.

1. `tico task show <id>` and read the newest thing the owner wrote on it: a comment, or their answer to the question.
2. **A vision** (one or two sentences on where the team is going, or what it sells to whom): save it in their words,
   `tico strategy vision --task <id> "<the vision, as they wrote it>"`. Send their sentences as written; you may leave out
   words around them ("Our vision is"), never change a word inside. The server checks the text is theirs, saves it,
   says so on the task and closes the question. Nothing more to do.
3. **A question** (what a vision is, an example, why you need it): answer it on the task in two or three plain sentences,
   from the published strategy and the market pages, never with a vision written for them. The question stays open
   for their next reply. If they answered the question itself with a question, ask it again on the task:
   `tico task ask <id> --to <owner> "What is this team for? ..." --why "Every goal ties to the vision ..." --version N`
   (N is the version `tico task show <id>` prints).
4. **Anything else** (they want it changed later, they are not sure): say in one line that it is saved only when they
   send it, and leave the question open.

The server refuses a vision once the team has one; changing it is the owner's, on the Strategy page.
