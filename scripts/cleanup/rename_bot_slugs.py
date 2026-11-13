#!/usr/bin/env python3
"""Rename every bot template whose slug is not its name, so a template's slug, its folder under templates/catalog/ and
its repository (`bot-<slug>`) are its card name in kebab-case; and remove the templates a newer one replaced.

Rerunnable and idempotent: a second run finds nothing, and after a rebase brings new text a rerun changes only that
text. There is no migration: every company starts fresh, so an old slug simply stops existing.

Many old slugs are ordinary words (`brand`, `support`, `content`, `pipeline`), so a slug is never replaced as a word.
It is renamed only where it is used as a slug:
  - its template folder (git mv; a removed template's folder is deleted) and the card's `template`, `slug`, `name`,
    and `team_templates`, and the bot.yaml `name`, `display_name` and `owner:` label,
  - an actor or id that carries it: `bot:<slug>`, `b:<slug>` (a chart parent), `owner:<slug>` (a label),
    `bot-<slug>` (a repository), `catalog/<slug>`, `#/bot/<slug>`, `/bots/<slug>`, `bot=<slug>`,
  - a value under a key that holds a slug (`template`, `slug`, `bot`, `head`, `owner`, ... in YAML, JSON, Python and
    TypeScript), an item of a `team_templates:` list, and a CLI flag that takes one (`--owner <slug>`, `--bot <slug>`),
  - a hyphenated slug (and `coo`), which is no English word, anywhere in a string, comment or text file,
  - a quoted word slug only in the files in BOT_KEYED, where every quoted word is a bot's slug,
  - the Python and TypeScript constants that name a bot (IDENTIFIERS).
A removed template's slug becomes the slug of the template that replaced it.

Never touched: docs/history/, reports/, scripts/cleanup/report-*.md, applied migrations, and KEEP below.

    python3 scripts/cleanup/rename_bot_slugs.py            # rewrite, and print counts
    python3 scripts/cleanup/rename_bot_slugs.py --check    # change nothing; exit 1 if anything would change
    python3 scripts/cleanup/rename_bot_slugs.py PATH...    # only these files or folders
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOG = "templates/catalog"

# The built-ins, the seats of the growth plan and the catalog: old slug -> new slug.
RENAMED = {
    "coo": "assistant",
    "goal-manager": "strategy-owner",
    "bookkeeper": "finance-owner",
    "brand": "design-owner",
    "tico-counsel": "rules-owner",
    "video-producer": "video-owner",
    # Their replacements (content-owner, email-owner) are the growth packs; until they land these are renamed to them.
    "content": "content-owner",
    "email-marketing": "email-owner",
    "pr-reviewer": "reviewer",
    "spend-watcher": "fpa-analyst",
    "accounts-payable": "accounts-payable-specialist",
    "affiliates": "affiliates-owner",
    "app-store": "app-store-owner",
    "ar-followup": "accounts-receivable-specialist",
    "benefits": "benefits-administrator",
    "billing": "billing-specialist",
    "board-updates": "investor-relations-manager",
    "community": "community-manager",
    "compensation": "compensation-analyst",
    "compliance": "compliance-manager",
    "docs-writer": "technical-writer",
    "employee-experience": "employee-experience-manager",
    "escalations": "escalations-manager",
    "events": "event-marketing-manager",
    "feedback-analyst": "customer-insights-analyst",
    "inbox": "inbox-manager",
    "incident-scribe": "site-reliability-engineer",
    "inventory": "inventory-planner",
    "it-support": "it-support-specialist",
    "learning": "learning-and-development-specialist",
    "legal-ops": "legal-operations-manager",
    "legal-review": "contracts-manager",
    "listening": "social-media-manager",
    "logistics": "logistics-coordinator",
    "marketing-ops": "marketing-operations-manager",
    "meeting-notes": "project-coordinator",
    "onboarding-specialist": "customer-onboarding-specialist",
    "ops-manager": "operations-manager",
    "payroll": "payroll-specialist",
    "people-hr": "hr-generalist",
    "people-lead": "head-of-people",
    "people-ops": "people-operations-specialist",
    "pr": "pr-manager",
    "pricing": "pricing-analyst",
    "privacy": "privacy-manager",
    "procurement": "procurement-manager",
    "product-ops": "product-operations-manager",
    "product-researcher": "ux-researcher",
    "recruiting": "recruiter",
    "release-notes": "release-notes-writer",
    "reputation": "reputation-manager",
    "retention": "retention-specialist",
    "returns": "returns-and-refunds-specialist",
    "sales-enablement": "sales-enablement-manager",
    "sales-ops": "sales-operations-manager",
    "security-compliance": "security-and-compliance-analyst",
    "strategy-planning": "strategy-analyst",
    "support-ops": "support-operations-specialist",
    "support-qa": "support-quality-analyst",
    "tax": "tax-specialist",
    "technical-support": "technical-support-engineer",
    "travel": "travel-coordinator",
}

# Removed templates -> the template that replaced each one.
REMOVED = {
    "bookkeeping": "finance-owner",
    "cro": "cro-owner",
    "customer-success": "success-owner",
    "engineering-lead": "engineering-team-manager",
    "finance-lead": "finance-owner",
    "issue-triage": "qa-owner",
    "lead-generation": "lead-generation-owner",
    "marketing-lead": "growth-team-manager",
    "paid-media": "search-ads-owner",  # and social-ads-owner
    "partnerships": "partnerships-owner",
    "pipeline": "release-owner",
    "product-marketing": "segment-marketing-owner",
    "sales": "inbound-sales-owner",
    "sales-lead": "growth-team-manager",
    "sdr-research": "outbound-sales-owner",
    "seo-visibility": "seo-owner",
    "support": "support-owner",
    "support-lead": "success-team-manager",
}

SLUGS = {**RENAMED, **REMOVED}

# Card names that change with the slug (the rest keep their name, which the new slug now matches).
NAMES = {
    "design-owner": "Design Owner",
    "video-owner": "Video Owner",
    "content-owner": "Content Owner",
    "email-owner": "Email Owner",
    "reviewer": "Reviewer",
    "cro-owner": "CRO Owner",
    "search-ads-owner": "Search Ads Owner",
    "social-ads-owner": "Social Ads Owner",
}
OLD_NAMES = {
    "design-owner": "Brand Owner",
    "video-owner": "Video Producer",
    "content-owner": "Content Marketer",
    "email-owner": "Email Marketing Manager",
    "reviewer": "Senior Software Engineer",
    "cro-owner": "Conversion Rate Optimization Owner",
    "search-ads-owner": "Search Ad Owner",
    "social-ads-owner": "Social Ad Owner",
}

# Old card names a file still calls these bots by (the built-ins and the renamed cards); "Senior Software Engineer" stays,
# since it is a job title the recruiter matches, and the Reviewer's own AGENTS.md is written by hand.
PROSE_NAMES = {
    "Brand Owner": "Design Owner",
    "Goal Manager": "Strategy Owner",
    "Video Producer": "Video Owner",
    "Content Marketer": "Content Owner",
    "Email Marketing Manager": "Email Owner",
    "Conversion Rate Optimization Owner": "CRO Owner",
    "Search Ad Owner": "Search Ads Owner",
    "Social Ad Owner": "Social Ads Owner",
}
PROSE = re.compile(r"\b(?P<name>" + "|".join(map(re.escape, PROSE_NAMES)) + r")(?=s?\b)")
# The Finance Owner's old name, renamed in the product, its templates and docs only: a test's "Bookkeeper" is often a
# job title a person types for a seat, which the recruiter matches.
PRODUCT_PROSE = re.compile(r"\bBookkeeper(?=s?\b)")
# The Rules Owner's old name, renamed in the same places. "Counsel" was a bare name ("Counsel reads it"), so its new name
# takes "the" unless a word before it already does the job ("a Counsel review", "the built-in Counsel"), and "The" where
# it starts a sentence. A General Counsel is a role of its own.
COUNSEL = re.compile(r"(?<!General )(?<!Tico )\bCounsel\b")
DETERMINERS = {
    "a",
    "an",
    "the",
    "built-in",
    "private",
    "stateless",
    "its",
    "no",
    "one",
    "other",
    "any",
    "each",
    "every",
}
SENTENCE_START = re.compile(r"^[\s#/*|>\-\"'`(\d.]*$|[.!?:]\s+[\"'`(*]*$")


def counsel(line):
    def one(m):
        before, after = line[: m.start()], line[m.end() :]
        word = re.search(r"([A-Za-z-]+)\s+$", before)
        if word and word.group(1).lower() in DETERMINERS:
            return "Rules Owner"
        if before[-1:] in ("'", '"', "`") and after[:1] == before[-1]:
            return "Rules Owner"  # a name on its own: a label, a constant
        if before[-1:] in ("'", '"', "`") or SENTENCE_START.search(before):
            return "The Rules Owner"
        return "the Rules Owner"

    return COUNSEL.sub(one, line)


# Lines that name a bot's old names on purpose (strategy_bot.OLD_NAMES, the checks that an old name is never shown, the
# upgrade test that seeds one): the old name stays there.
PROSE_OLD = re.compile(
    r"CEO Bot|Strategy Bot|Goal Manager becomes|that was the Goal Manager|display_name='Goal Manager'|/Goal Manager\|"
    r"|Bookkeeper as Finance Owner"
)

# Slugs that are English words or name something else too (a group, a channel, a tool, a feature, a CSS class): these are
# renamed only in a slug context or a BOT_KEYED file, never as a bare or quoted word.
WORDS = {
    "affiliates", "app-store", "benefits", "billing", "bookkeeper", "bookkeeping", "brand", "community", "compensation",
    "compliance", "content", "cro", "escalations", "events", "inbox", "inventory", "learning", "lead-generation",
    "listening", "logistics", "meeting-notes", "partnerships", "payroll", "pipeline", "pr", "pricing", "privacy",
    "procurement", "recruiting", "release-notes", "reputation", "retention", "returns", "sales", "support", "tax",
    "travel",
}  # fmt: skip
# `coo` is no word, but it is short: renamed in strings, comments and text, never as a code identifier.
DISTINCT = {slug for slug in SLUGS if slug not in WORDS}

# Files where every quoted word that is an old slug is a bot's slug: the demo company's bots and their content, and the
# tables keyed by slug.
BOT_KEYED = (
    "backend/demo_content.py",
    "backend/demo_seed.py",
    "backend/demo_sections/",
    "backend/tests/test_demo_",
    "backend/tests/helpers/demo_",
    "ui/app/avatars.ts",
    "ui/app/sidebar.ts",
    "ui/app/state.ts",
)
# Lines in BOT_KEYED files where a quoted word is not a slug.
BOT_KEYED_EXCEPT = re.compile(
    r"\"track\": \"brand\"|CONTENT\[\"brand\"\]|\"brand\": \{|\"brand\": 4\.2|days\[\"brand\"\]|\"brand/(draft|publish)\""
    r"|PUT\", \"brand|\bkind\b|\"source\"|\"category\"|\"service\"|\"channel\""
    r"|\[[\"']events[\"']\]|\"inbox\": page\(|used_for|\"inbox\": \"[^\"]*@|\"id\": \"support\",|\"to\": \"support\",|\"orders\": \"Orders\""
    r"|\"sort\.to\"|\"near_group\"|\"key\": \"(pipeline|returns)\"|\"path\": \"SKILL\.md\"|icon: 'inbox'"
    r"|\"content\": \"#|\"returns\": (\(|cycle)|CONTENT\[\"returns\"\]|\"billing\": (billing|\"subscription\")|doc\[\"content\"\]"
)
# A rule or a message in a BOT_KEYED file names a bot as @slug, the way the Rules page shows a mention chip.
MENTION = re.compile(
    r"(?<![\w@])@(?P<slug>" + "|".join(re.escape(s) for s in sorted(WORDS, key=len, reverse=True)) + r")(?![\w-])"
)
# Words that are never a bot in a BOT_KEYED file (a KPI, a routine key, the seeded events).
BOT_KEYED_NEVER = {"events", "pipeline"}
# The demo's groups named like the old bots (`support`, `sales`): a group fix's first item and a rule's "groups" keep the
# group's id, which the quoted-word rename took for a bot.
GROUP_IDS = {"support-owner": "support", "inbound-sales-owner": "sales"}
GROUP_ID = re.compile(r'(?m)(^    \(|"groups": \[)"(?P<id>support-owner|inbound-sales-owner)"(?=, \(|\])')

# Keys whose value is a bot's or a template's slug.
KEYS = (
    "template|templates|slug|bot|bot_slug|bot_id|template_id|head|owner|owner_id|assignee|receiver|closer|root|"
    "reports_to|manager|manager_id|assistant_bot|drafted_by|teammate|from_template|replaced_by|seat_template"
)
# Flags of the tico CLI (and the browser connector) that take a slug.
FLAGS = "owner|bot|as|assignee|template|to-bot"

# An applied migration is never edited (backend/migrations checks each file's checksum), comments included.
SKIP_PREFIXES = ("docs/history/", "reports/", ".git/", "backend/migrations/", "node_modules/")
SKIP_GLOBS = ("scripts/cleanup/report-*.md",)
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".woff", ".woff2", ".zip", ".gz", ".sqlite", ".db", ".ico", ".icns"}
BINARY |= {".mp4", ".mp3", ".wav", ".webp", ".ttf", ".otf", ".lock", ".svg", ".car", ".a", ".so", ".dylib"}
CODE = {".py", ".ts", ".cts", ".mts", ".js", ".mjs", ".cjs", ".rs", ".sh"}

KEEP = {
    "scripts/cleanup/rename_bot_slugs.py",
    "scripts/cleanup/renames.yaml",  # the earlier renames' own record
    "scripts/cleanup/keep.yaml",
    "scripts/cleanup/rename.py",
    "scripts/cleanup/gen_map.py",
    "scripts/cleanup/rename_hub_to_tico.py",
    "scripts/cleanup/rename_hub_tools_to_tico.py",
    "backend/tests/test_slugs_match_names.py",  # names the exceptions to the rule
    # An old release's database, as that release made it: its bots and rows keep the slugs of their day.
    "scripts/make_upgrade_fixture.py",
    "scripts/journey-test.sh",
    "backend/tests/test_upgrade.py",
    "backend/tests/fixtures/schema_old_main.sql",
    "backend/tests/fixtures/schema_pre_cleanup.sql",
    "scripts/tests/stale_pr_keeper/pytest-failed.log",  # a CI log, as CI wrote it
    "ui/tests/onboarding.cts",  # a mocked catalog of its own; only its message bot's template follows the product
    "backend/tests/test_bot_team_default.py",  # tests an applied migration, which knows the bots by the slugs of its day
}
# (file, line pattern): a line where an old slug is not a slug.
KEEP_LINES = (
    # The Legal pipeline's Review stage is called legal-review, like the old template; a stage id is not a bot.
    ("backend/pipeline_presets_more.py", re.compile(r'^\s+"legal-review",$|"legal-intake", "legal-review"')),
    ("backend/tests/fixtures/shipped_preset_texts.json", re.compile(r'"legal-review/purpose"')),
    # The old slugs the software factory guide must not name any more.
    ("backend/tests/test_product_engineering_presets.py", re.compile(r"^\s+for old in \(\"`product-lead`\"")),
    ("backend/company_file/draft.py", re.compile(r"HEAD = re\.compile|\"operations\": \[")),
    ("backend/company_file/draft.py", re.compile(r"^RETIRED = ")),
    # The retired template the saved-draft test names, as draft.RETIRED does.
    ("backend/tests/test_company_file.py", re.compile(r"^        \"template\": \"bookkeeping\",$")),
)

# Constants named after a bot's old slug -> the new name, as (path prefixes, old, new).
IDENTIFIERS = (
    (("backend/", "clients/", "evals/", "scripts/"), "H.COUNSEL", "H.RULES_OWNER"),
    (("backend/hubdb.py",), "COUNSEL", "RULES_OWNER"),
    (("clients/", "ui/"), "COUNSEL_BOT", "RULES_OWNER_BOT"),
    (("backend/",), "BRAND_OWNER", "DESIGN_OWNER"),
    (("backend/", "clients/", "evals/"), "GOAL_MANAGER", "STRATEGY_OWNER"),
    (
        (
            "backend/finance/delivery.py",
            "scripts/finance_bookkeeper_rehearsal.py",
            "backend/demo_sections/finance.py",
            "backend/tests/helpers/demo_finance.py",
        ),
        "BOOKKEEPER",
        "FINANCE_OWNER",
    ),
    (("evals/redesign/",), "COO", "ASSISTANT"),
)


def tracked(only=()):
    names = subprocess.run(
        ["git", "ls-files", "-z", "--", *only], cwd=ROOT, capture_output=True, check=True
    ).stdout.split(b"\0")
    for raw in names:
        name = raw.decode()
        if not name or name.startswith(SKIP_PREFIXES) or name in KEEP:
            continue
        if any(Path(name).match(glob) for glob in SKIP_GLOBS) or Path(name).suffix.lower() in BINARY:
            continue
        if not (ROOT / name).is_file():
            continue
        yield name


def alternation(words):
    return "|".join(re.escape(w) for w in sorted(words, key=lambda w: (-len(w), w)))


# Not part of a longer slug or identifier on either side.
LEFT = r"(?<![A-Za-z0-9_-])"
RIGHT = r"(?![A-Za-z0-9_]|-[A-Za-z0-9])"


TEMPLATE_KEYS = "template|templates|template_id|from_template|seat_template"
Q = "[\"'`]"


def patterns(slugs):
    """The slug contexts for `slugs`: every old slug in product code, the hyphenated ones (and `coo`) in tests. A test's
    own bots are often named with a word (`inbox`, `sales`), so there a word slug is renamed only where it names a catalog
    template: `catalog/<slug>` and a quoted `template` key (TEMPLATE)."""
    every, words = alternation(slugs), alternation(set(slugs) & WORDS)
    distinct = alternation(set(slugs) & DISTINCT)
    return {
        # bot:<slug>, b:<slug>, owner:<slug>, bot-<slug>, #/bot/<slug>, /bots/<slug>, bot=<slug>
        "context": re.compile(
            rf"(?P<pre>(?<![A-Za-z0-9_-])(?:bot:|b:|owner:|bot-)|#/bot/|/bots/|[?&]bot=)(?P<slug>{every}){RIGHT}"
        ),
        # "template": "x", template="x", template: 'x' (quoted, so a code variable is never taken for a slug)
        "key": re.compile(
            rf"(?P<pre>(?<![A-Za-z0-9_])[\"']?(?:{KEYS})[\"']?\s*[:=]\s*{Q})(?P<slug>{every}){RIGHT}(?={Q})"
        ),
        # template: x in a YAML or Markdown file
        "key_text": re.compile(
            rf"(?P<pre>(?<![A-Za-z0-9_])(?:{KEYS}):[ \t]+)(?P<slug>{every})(?=[ \t]*(?:$|#|,|\]|\}}))", re.M
        ),
        "flag": re.compile(rf"(?P<pre>--(?:{FLAGS})[ =]{Q}?)(?P<slug>{every}){RIGHT}"),
        "distinct": re.compile(rf"{LEFT}(?P<slug>{distinct}){RIGHT}(?!\.[a-z]{{1,4}}\b)"),
        "quoted_word": re.compile(rf"(?P<q>{Q})(?P<slug>{words})(?P=q)"),
    }


EVERY = alternation(SLUGS)
CATALOG_PATH = re.compile(rf"(?P<pre>catalog/)(?P<slug>{EVERY}){RIGHT}")
TEMPLATE = re.compile(
    rf"(?P<pre>(?<![A-Za-z0-9_])[\"']?(?:{TEMPLATE_KEYS})[\"']?\s*[:=]\s*{Q})(?P<slug>{EVERY}){RIGHT}(?={Q})"
)
TEAM_LIST = re.compile(r"(?P<pre>\b(?:team_templates|primary_for)\s*:\s*\[)(?P<body>[^\]\n]*)\]")
TOKEN = re.compile(rf"{LEFT}(?P<slug>{EVERY}){RIGHT}")


def is_test(name):
    path = Path(name)
    return (
        "tests" in path.parts
        or "fixtures" in path.parts
        or path.name.startswith("test_")
        or name.startswith(("e2e/", "evals/", "docs-eval/", "bench/"))
    )


# The spans of a code file that are strings or comments, where `coo` is renamed.
STRINGS = {
    ".py": re.compile(r"(\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|#[^\n]*)"),
    ".rs": re.compile(r"(\"(?:\\.|[^\"\\])*\"|//[^\n]*)"),
    ".sh": re.compile(r"(\"(?:\\.|[^\"\\])*\"|'[^']*'|#[^\n]*)"),
}
JS = re.compile(r"(\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`|//[^\n]*|/\*[\s\S]*?\*/)")


def new_slug(match):
    return SLUGS[match.group("slug")]


def rename_in_span(text, pats, coo):
    """A string, comment or text file's hyphenated slugs (and `coo` when `coo`)."""

    def one(m):
        slug = m.group("slug")
        return SLUGS[slug] if (slug != "coo" or coo) else slug

    return pats["distinct"].sub(one, text)


PATTERNS = {"product": patterns(SLUGS), "test": patterns(DISTINCT)}
# In a test, a quoted `bookkeeper` (or bot:bookkeeper) is the built-in Finance Owner or a bot standing in for it; never
# an event or job key (`bookkeeper.view_asked`, `bookkeeper_check`), which keep their names.
TEST_BOOKKEEPER = re.compile(
    r"(?:(?P<q>[\"'`/])bookkeeper(?=[\"'`/])|(?P<pre>\bbot:|\bb:)bookkeeper(?![A-Za-z0-9_.-]))"
)
# Tests whose quoted `brand` is the built-in Design Owner (a slug list, a catalog folder), not the Brand page.
TEST_BRAND_FILES = (
    "backend/tests/test_brand_owner.py",
    "backend/tests/test_company_import.py",
    "ui/tests/sidebar-team-builtins.cts",
)
TEST_BRAND = re.compile(r"(?P<q>[\"'`/])brand(?=[\"'`/])")
# In a template's own Markdown, a word slug in backticks names a teammate ("goes to `support`"), except in these
# files, where it is a flow preset, an API field or a review category.
BACKTICK = re.compile(r"`(?P<slug>" + "|".join(re.escape(s) for s in sorted(WORDS, key=len, reverse=True)) + r")`")
BACKTICK_SKIP = (
    "templates/catalog/assistant/playbooks/acting-for-them.md",
    "templates/catalog/librarian/playbooks/answer-a-question.md",
    "templates/catalog/reputation-manager/knowledge/surfaces.md",
)


def rewrite_text(name, text):
    suffix = Path(name).suffix.lower()
    pats = PATTERNS["test" if is_test(name) and not name.startswith(BOT_KEYED) else "product"]
    keep = [pat for path, pat in KEEP_LINES if path == name]
    lines = text.split("\n")
    held = {i: line for i, line in enumerate(lines) if any(pat.search(line) for pat in keep)}
    if held:
        text = "\n".join("\0KEEP%d\0" % i if i in held else line for i, line in enumerate(lines))

    sub = lambda m: m.group("pre") + new_slug(m)
    if is_test(name):
        text = TEST_BOOKKEEPER.sub(lambda m: (m.group("q") or m.group("pre")) + "finance-owner", text)
        if name in TEST_BRAND_FILES:
            text = TEST_BRAND.sub(lambda m: m.group("q") + "design-owner", text)
    text = CATALOG_PATH.sub(sub, text)
    text = TEMPLATE.sub(sub, text)
    text = TEAM_LIST.sub(
        lambda m: m.group("pre") + TOKEN.sub(lambda t: SLUGS[t.group("slug")], m.group("body")) + "]", text
    )
    text = pats["context"].sub(sub, text)
    text = pats["key"].sub(sub, text)
    if suffix not in CODE:
        text = pats["key_text"].sub(sub, text)
    text = pats["flag"].sub(sub, text)
    # A hyphenated slug is never a code identifier; `coo` is renamed in a code file's strings and comments only.
    text = rename_in_span(text, pats, coo=suffix not in CODE)
    if suffix in CODE:
        spans = STRINGS.get(suffix, JS)
        text = spans.sub(lambda m: rename_in_span(m.group(0), pats, coo=True), text)
    if name.startswith(CATALOG + "/") and suffix == ".md" and name not in BACKTICK_SKIP:
        text = BACKTICK.sub(lambda m: "`" + SLUGS[m.group("slug")] + "`", text)
    if name.startswith(BOT_KEYED):
        out = []
        for line in text.split("\n"):
            if not BOT_KEYED_EXCEPT.search(line):
                line = pats["quoted_word"].sub(
                    lambda m: (
                        m.group(0) if m.group("slug") in BOT_KEYED_NEVER else m.group("q") + new_slug(m) + m.group("q")
                    ),
                    line,
                )
                line = MENTION.sub(lambda m: "@" + SLUGS[m.group("slug")], line)
            out.append(line)
        text = "\n".join(out)
        text = GROUP_ID.sub(lambda m: m.group(1) + '"' + GROUP_IDS[m.group("id")] + '"', text)
    product = not is_test(name)

    def prose(line):
        if PROSE_OLD.search(line):
            return line
        line = PROSE.sub(lambda m: PROSE_NAMES[m.group("name")], line)
        return counsel(PRODUCT_PROSE.sub("Finance Owner", line)) if product else line

    text = "\n".join(prose(line) for line in text.split("\n"))
    for prefixes, old, new in IDENTIFIERS:
        if name.startswith(prefixes):
            text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])", new, text)

    if held:
        text = re.sub(r"\0KEEP(\d+)\0", lambda m: held[int(m.group(1))], text)
    return text


def git(*argv):
    subprocess.run(["git", *argv], cwd=ROOT, check=True, capture_output=True)


def move_folders():
    """git mv each renamed template's folder (and its lint baselines'), and delete each removed one. A second run finds
    them done."""
    moved = removed = 0
    for catalog in [ROOT / CATALOG, *sorted((ROOT / "baselines").glob("*/" + CATALOG))]:
        m, r = move_in(catalog)
        moved, removed = moved + m, removed + r
    return moved, removed


def move_in(catalog):
    moved = removed = 0
    for old, new in RENAMED.items():
        source, target = catalog / old, catalog / new
        if source.is_dir() and old != new and not target.exists():
            git("mv", str(source.relative_to(ROOT)), str(target.relative_to(ROOT)))
            moved += 1
    for old in REMOVED:
        source = catalog / old
        if source.is_dir():
            git("rm", "-r", "-q", str(source.relative_to(ROOT)))
            if source.exists():
                shutil.rmtree(source)
            removed += 1
    return moved, removed


def team_list(values, own):
    """A card's team_templates with each slug renamed, each removed template's replacement listed once, and never the
    card itself."""
    out = []
    for value in values:
        slug = SLUGS.get(value, value)
        if slug != own and slug not in out:
            out.append(slug)
    return out


def fix_card(folder):
    """The card's template, slug and name, and its team_templates; the manifest's name, label and display name."""
    card = folder / "card.yaml"
    slug = folder.name
    text = card.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^template: .*$", "template: " + slug, text)
    text = re.sub(r"(?m)^slug: .*$", "slug: " + slug, text)
    if slug in NAMES:
        text = re.sub(r"(?m)^name: .*$", "name: " + NAMES[slug], text)

    def team(m):
        values = [v.strip() for v in m.group(2).split(",") if v.strip()]
        return m.group(1) + ", ".join(team_list(values, slug)) + "]"

    text = re.sub(r"(?m)^(team_templates:\s*\[)([^\]]*)\]", team, text)
    card.write_text(text, encoding="utf-8")
    manifest = folder / "bot.yaml"
    if manifest.is_file():
        text = manifest.read_text(encoding="utf-8")
        if slug in NAMES:
            text = re.sub(r'(?m)^display_name: .*$', 'display_name: "%s"' % NAMES[slug], text)
        text = re.sub(r"(?m)^(name: )(\S+)", lambda m: m.group(1) + slug, text)
        manifest.write_text(text, encoding="utf-8")


def run(write=True, only=()):
    if write and not only:
        moved, removed = move_folders()
        print(f"moved {moved} template folders, removed {removed}")
        for folder in sorted((ROOT / CATALOG).iterdir()):
            if (folder / "card.yaml").is_file():
                fix_card(folder)
    elif not only:
        pending = [old for old in SLUGS if (ROOT / CATALOG / old).is_dir()]
        if pending:
            print("would move or remove the template folders", ", ".join(pending))
            return len(pending)
    files = 0
    for name in tracked(only):
        path = ROOT / name
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new = rewrite_text(name, text)
        if new != text:
            files += 1
            if write:
                path.write_text(new, encoding="utf-8")
            else:
                print("would rewrite", name)
    verb = "rewrote" if write else "would rewrite"
    print(f"{verb} old bot slugs in {files} files")
    return files


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="change nothing; exit 1 when anything would change")
    ap.add_argument("paths", nargs="*", help="only these files or folders (default: the whole checkout)")
    args = ap.parse_args(argv)
    changes = run(write=not args.check, only=args.paths)
    return 1 if args.check and changes else 0


if __name__ == "__main__":
    sys.exit(main())
