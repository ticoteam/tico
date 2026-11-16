"""Flow areas with no setup questions yet (docs/flow-scenarios.md): what each area's owner expects Flow to ask, state by
state, written down before the build so it starts with its expectations (owners' round 2 replies). Data only, no
backend: who is asked, the expected question, where its suggested answer comes from, where the answer saves, and the
gap when Flow cannot ask it yet. backend/tests/test_flow_pending_areas.py checks the shape and the questions' properties
(the card's rules and the rubric's mechanical checks); the words are a guide, not a spec. When an area's backend lands
it becomes an `Area` in flow_scenarios.py and these states become its scenarios. Invented names only: Acme, Globex,
Pixelmill, Clipcast, Dana Lee, Ana, Ben."""

from dataclasses import dataclass


@dataclass(frozen=True)
class State:
    """One company state and the question Flow should ask in it. `breaks`: why the expected words fail a rule today
    (a strict xfail each, so a rewrite that fixes them takes the entry out); `gap`: what Flow lacks to ask it."""

    name: str
    who: str
    question: str
    suggest_from: str
    saves_to: str
    choices: tuple[str, ...] = ()
    gap: str = ""
    breaks: str = ""


@dataclass(frozen=True)
class PendingArea:
    """An area Flow will ask about: how its answers save ("draft", published later, or "at once", taken back from the
    end card) and its states."""

    name: str
    saves: str
    states: tuple[State, ...]


GROWTH = PendingArea(
    "growth",
    "at once",
    (
        State(
            "nothing set",
            "the Growth owner or an admin",
            "What does one customer pay you a month, on average?",
            "none",
            "revenue per customer",
        ),
        State(
            "channel but no source",
            "the Growth owner or an admin",
            "Where will Search ads' monthly numbers come from?",
            "Finance's import when it already has that platform",
            "the channel's source",
            choices=("I'll type them", "Finance's import"),
            gap="whether typed numbers count as a Growth source is still the product owner's open question",
        ),
        State(
            "source connected, no month",
            "the Growth owner or an admin",
            "How many new customers did Search ads bring in October?",
            "none: spend arrived, new customers did not",
            "that month",
        ),
        State(
            "spend but no revenue",
            "the Growth owner or an admin",
            "What does one customer pay you a month, on average?",
            "none",
            "revenue per customer",
        ),
        State(
            "channel collapsing",
            "the Growth owner or an admin",
            "Are Search ads' 4 new customers in October right?",
            "the month as imported (22 in September, 4 in October)",
            "the month, marked actual or estimate",
            choices=("Yes", "Not all counted yet", "Type the right number"),
        ),
        State(
            "cost per customer doubled",
            "the Growth owner or an admin",
            "What's the most you'll pay for one new customer on Search ads?",
            "the channel's cost per customer ($210, now $420)",
            "the channel's spending limit",
            gap="needs a cost-per-customer KPI set as the channel's limit",
        ),
    ),
)

GOALS = PendingArea(
    "goals",
    "at once",
    (
        State(
            "no goals",
            "the company owner",
            "What must be true by the end of this quarter?",
            "the published Strategy's active bet; none without one",
            "a new company goal, tied to that bet",
        ),
        State(
            "goals, none measured",
            "the goal's owner or someone above them, top company goal first",
            "How will you know Raise activation is working?",
            "a measure the team has but has not linked, or one the Strategy bot suggested",
            "that goal's measure",
        ),
        State(
            "a measure with no number",
            "the measure's owner or someone above them",
            "What is Weekly sign-ups today?",
            "none: no real number to offer",
            "the measure's first reading",
        ),
        State(
            "number 30 days stale",
            "the measure's owner or someone above them",
            "What is Weekly sign-ups now? Last time it was 42 on Oct 12.",
            "Still 42",
            "a new reading for today",
            choices=("Still 42",),
            breaks="two sentences: the last reading comes after the question",
        ),
        State(
            "a goal with no bet",
            "the goal's owner or someone above them, once Strategy is published",
            "Which bet does Raise activation serve?",
            "the active bets",
            "the goal's bet",
            choices=("Small landlords will trust a bot with tenant messages", "None, it's day-to-day work"),
        ),
        State(
            "a goal hit early",
            "the goal's owner or someone above them",
            "Activation reached 70% before Dec 31. What now?",
            "Mark it done",
            "the goal's status, or the measure's target",
            choices=("Mark it done", "Raise the target", "Keep it as it is"),
            breaks="two sentences: it opens with the news, then asks",
        ),
    ),
)

COMPANY = PendingArea(
    "company",
    "at once",
    (
        State(
            "new company, owner alone",
            "the owner",
            "What does Acme do, in two sentences?",
            "the owner's onboarding 'what we do' answer",
            "company description",
        ),
        State(
            "teams with no lead",
            "the owner or an admin",
            "Who leads Product?",
            "the member the rest of the team reports to",
            "team lead",
        ),
        State(
            "a person with no manager",
            "the owner or an admin",
            "Who does Dana Lee report to?",
            "her team's lead, else the owner",
            "Dana Lee's manager",
        ),
        State(
            "a person with no team",
            "the owner or an admin",
            "Which team is Dana Lee on?",
            "the team of the person she reports to",
            "Dana Lee's team",
        ),
        State(
            "a team lead left",
            "the owner or an admin",
            "Who leads Growth now that Ben has left?",
            "the member the rest of Growth reports to",
            "team lead",
        ),
        State(
            "a teammate who cannot edit the org chart",
            "nobody: they cannot save the answer",
            "",
            "",
            "",
        ),
        State(
            "everything set, nothing changed",
            "nobody",
            "",
            "",
            "",
        ),
    ),
)

RULES = PendingArea(
    "rules",
    "at once",
    (
        State(
            "emails sent back for the same reason",
            "the person who sent them back",
            "Should bots ask you before sending a customer an email that names a price?",
            "three emails sent back with 'don't quote prices'",
            "a rule on that stage",
        ),
        State(
            "a rule held what you sent anyway",
            "the rule's owner",
            "The rule 'Ask before emailing a customer' held 5 emails and you sent all 5. Should it only warn from now on?",
            "five held, five sent",
            "the rule's action",
            breaks="two sentences, over the card's length: the count comes before the question",
        ),
        State(
            "appeals approved against one rule",
            "the rule's owner",
            "Should the support bot stop following 'No refunds over $100 without asking'?",
            "three appeals approved",
            "the rule, for that bot",
        ),
        State(
            "the same yes three times",
            "the person who said yes",
            "Bots asked 4 times if they may send a customer a copy of their own invoice, and you said yes each time. "
            "Should that be a rule?",
            "four yeses, grouped by one model call",
            "a new rule",
            gap="needs one model call to group answers that mean the same thing",
            breaks="two sentences, over the card's length",
        ),
        State(
            "the same pick three times",
            "the person who picked it",
            "You picked Free for a new signup's plan 3 times. Should bots pick Free without asking?",
            "three picks, grouped by one model call",
            "a new rule",
            gap="needs one model call to group answers that mean the same thing",
            breaks="two sentences: the count comes before the question",
        ),
        State(
            "a yearly due date marked done twice",
            "the person who marked it",
            "Should you own the 1099-NEC due date every January, with a reminder 30 days before?",
            "two years marked done",
            "a yearly obligation with its owner",
        ),
    ),
)

FINANCE = PendingArea(
    "finance",
    "at once",
    (
        State(
            "no approver named",
            "the owner",
            "Who should approve money your bots ask to spend?",
            "the owner, today's default",
            "the spend approver",
            choices=("You", "Another Finance admin"),
        ),
        State(
            "AI budget has no cap",
            "the budget owner",
            "What should AI services be capped at each month?",
            "last 30 days' spend",
            "the budget's monthly cap",
            choices=("Last 30 days' spend plus 20%", "Same as last month", "No cap yet"),
        ),
        State(
            "budget 90% used",
            "the budget owner",
            "AI services has used $900 of $1,000 with 9 days left. Raise the cap for this month?",
            "where spending is heading",
            "this month's cap",
            choices=("Raise to $1,150", "Keep it and refuse past $1,000"),
            breaks="two sentences: the numbers come before the question",
        ),
        State(
            "bill past due",
            "the bill's approver",
            "Pixelmill's $540 bill was due Nov 10. Pay it today?",
            "the bill",
            "the bill's payment",
            choices=("Pay today", "Pick a date", "Already paid", "Hold it"),
            gap="paying needs a connected bank",
            breaks="two sentences: the bill comes before the question",
        ),
        State(
            "subscription unused 30 days",
            "the budget owner",
            "Clipcast charged $15 on Nov 3 and nobody has used it in 30 days. Stop paying for it?",
            "the charge and the tool's use",
            "the card's freeze",
            choices=("Freeze its card", "Keep it", "Ask again in a month"),
            gap="Finance sees charges, not use, except for AI spend and Tico's own tools; it can only freeze the card",
            breaks="two sentences: the charge comes before the question",
        ),
        State(
            "month ready to close",
            "a Finance admin",
            "October is reconciled and nothing is uncategorized. Close it so nothing more posts into it?",
            "the month's reconciliation",
            "the month's close",
            choices=("Close it", "Not yet"),
            breaks="two sentences: the month's state comes before the question",
        ),
    ),
)

CONTACTS = PendingArea(
    "contacts",
    "at once",
    (
        State(
            "contacts almost empty",
            "the inbox owner, else an admin (product owner's recommendation, not decided)",
            "Who are your most important customers?",
            "the companies Finance invoices most; none without Finance data",
            "contacts, as customers",
        ),
        State(
            "a company often met, no relationship",
            "the inbox owner, else an admin",
            "What is Globex to us?",
            "Customer if you invoice them, Vendor if they bill you",
            "the company's relationship",
            choices=("Customer", "Vendor", "Partner", "Prospect"),
        ),
        State(
            "two records that look like one",
            "the inbox owner, else an admin",
            "Are 'Acme' and 'Acme Inc.' the same company?",
            "Same, keeping 'Acme Inc.', which has more details",
            "the merge",
            choices=("Same, keep 'Acme Inc.'", "Different companies"),
        ),
        State(
            "a person with no company",
            "the inbox owner, else an admin",
            "Where does Dana Lee work?",
            "the company of the others in her meetings",
            "the person's company",
        ),
        State(
            "two sources disagree",
            "the inbox owner, else an admin",
            "Which is Acme's billing address now?",
            "the newer one, with its source and date",
            "the company's billing address",
        ),
        State(
            "where we operate is empty",
            "the owner or an admin",
            "Which countries do you operate in?",
            "the country on your company page",
            "where we operate",
        ),
    ),
)

ONBOARDING = PendingArea(
    "onboarding",
    "draft",
    (
        State(
            "signed up from a website link",
            "the owner",
            "Who do you serve?",
            "the draft's unconfirmed who we serve, from the website research",
            "Strategy draft: who we serve",
        ),
        State(
            "no website, only the one line",
            "the owner",
            "Who do you serve?",
            "none: the research left it empty, failed or is still running",
            "Strategy draft: who we serve",
        ),
        State(
            "imported from another tool, all basics confirmed and a bet active",
            "the owner",
            "What should your team do first?",
            "the active bet in its own words, and the live bot that owns that kind of work",
            "a task for that bot, the owner as requester",
            gap="where it saves, and whether it may go to a bot still in setup, are the product owner's open questions; "
            "whether an imported company file marks its strategy confirmed is unchecked",
        ),
        State(
            "an invited teammate, not the owner",
            "nobody: they cannot save Strategy or setup",
            "",
            "",
            "",
        ),
    ),
)

BOT_CONVERSATIONS = PendingArea(
    "bot-conversations",
    "at once",
    (
        State(
            "sorting expenses",
            "the person who asked the finance bot for the work",
            "Which category is the $1,240 Pixelmill charge on Nov 3?",
            "Software, from past Pixelmill charges",
            "the expense's category",
        ),
        State(
            "a refund reply",
            "the person who asked the support bot for the work",
            "How many days after purchase do you refund?",
            "30 days, from the help page",
            "the refund policy",
        ),
        State(
            "booking a customer call",
            "the person who asked their Assistant",
            "How long should customer calls be?",
            "30 minutes, from recent customer meetings",
            "the call length",
        ),
        State(
            "release day",
            "the person who asked the release bot",
            "Which day should releases go out?",
            "Tuesday, from the last four releases",
            "the release day",
        ),
        State(
            "outbound sales",
            "the person who asked the sales bot",
            "Whose name should sales emails be sent under?",
            "whoever sent most past sales emails",
            "the sales sender",
        ),
        State(
            "a contact's kind",
            "the person who asked the contacts bot",
            "Is Globex a customer or a prospect?",
            "Customer, from its paid invoice",
            "the company's relationship",
            choices=("Customer", "Prospect"),
        ),
    ),
)

PENDING = (GROWTH, GOALS, COMPANY, RULES, FINANCE, CONTACTS, ONBOARDING, BOT_CONVERSATIONS)
# The Strategy owner's per-part wording (round 2), for the builder replacing the guide's questions: checked for the
# same properties, so a proposal that breaks them is known before it ships.
STRATEGY_WORDING = {
    "serve": ("Which customers do you serve first, the ones who need this most?", ""),
    "sell": ("What do you sell, and what does a customer pay for it?", ""),
    "bet": ("What is the one bet you're making now that could turn out wrong?", ""),
    "not": ("What won't you do, even when a good customer asks for it?", ""),
    "proof": (
        "How do you know customers want it? Name who uses it and how often.",
        "two sentences, the second not a question",
    ),
    "now": ("What changed recently that makes this possible, or needed, now?", ""),
    "know": ("What do you know about this market that your competitors don't?", ""),
    "how": (
        "Where do your next 100 customers come from? Name the channel, or the people.",
        "kept as is by the owner: two sentences, ending in a period",
    ),
}
