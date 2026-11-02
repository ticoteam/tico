"""The integration pages (`integrations/*.md`), their query catalogs and the shared learnings.

A page is curated markdown with YAML frontmatter, shipped in the release and read once per
process; the query catalog next to it (`integrations/queries/<service>.yaml`) the same. A company
layers its own pages and catalogs over the release from a directory of its own
(`<registry>/integrations`, or `TICO_INTEGRATIONS_DIR`); see docs/databases.md. What
changes at run time is the learnings: one row per note a bot or a person adds under a page,
kept in `learnings` in the hub database. Any signed-in person or bot may read everything here
and add a learning; only the owner deletes one. Nothing secret is in these files.
"""

import logging
import re
from pathlib import Path

import yaml
from fastapi import Request
from pydantic import Field

from .models import Contract
from .store import H, Problem
from . import task_privacy as privacy

KINDS = ("api", "sql", "browser", "mail", "cli")
WRITES = ("never", "approval", "allowed")
REQUIRED = ("service", "title", "kind", "summary", "access", "credentials", "declared_as", "writes", "owner")
OPTIONAL = ("aliases",)
SECTIONS = ("What it is", "What data it has", "How a bot uses it", "Rules", "Recipes", "Gotchas", "Learnings")
SERVICE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)
QUERY_KEYS = ("id", "title", "description", "category", "tags", "database", "sql", "params")
# A MongoDB entry carries `mongo:` (op, collection, filter or pipeline) where a SQL one carries `sql:`.
MONGO_OPS = ("find", "aggregate", "count", "distinct")
MONGO_KEYS = ("op", "collection", "filter", "projection", "sort", "limit", "pipeline", "field")
# One actor adds at most this many learnings a day; the page is the rule, not a chat log.
LEARNINGS_PER_DAY = 20
log = logging.getLogger("tico.integrations")


class Learning(Contract):
    text: str = Field(min_length=1, max_length=2000)


def parse_page(text, name):
    """Frontmatter and body of one page, validated; raises ValueError with the page name."""
    match = FRONTMATTER.match(text)
    if not match:
        raise ValueError(f"{name}: no YAML frontmatter")
    meta = yaml.safe_load(match.group(1)) or {}
    body = match.group(2).strip() + "\n"
    if not isinstance(meta, dict):
        raise ValueError(f"{name}: frontmatter is not a mapping")
    missing = [key for key in REQUIRED if key not in meta]
    if missing:
        raise ValueError(f"{name}: frontmatter is missing {', '.join(missing)}")
    extra = [key for key in meta if key not in REQUIRED + OPTIONAL]
    if extra:
        raise ValueError(f"{name}: frontmatter has unknown keys {', '.join(extra)}")
    if meta["service"] != name or not SERVICE.match(name):
        raise ValueError(f"{name}: `service` must equal the file name and be lower-case")
    if meta["kind"] not in KINDS:
        raise ValueError(f"{name}: kind must be one of {', '.join(KINDS)}")
    if meta["writes"] not in WRITES:
        raise ValueError(f"{name}: writes must be one of {', '.join(WRITES)}")
    for key in ("title", "summary", "access", "declared_as", "owner"):
        if not isinstance(meta[key], str) or not meta[key].strip():
            raise ValueError(f"{name}: {key} must be a non-empty string")
    if not isinstance(meta["credentials"], list) or not all(isinstance(c, str) and c.strip() for c in meta["credentials"]):
        raise ValueError(f"{name}: credentials must be a list of strings")
    aliases = meta.get("aliases") or []
    if not isinstance(aliases, list) or not all(isinstance(a, str) and SERVICE.match(a) for a in aliases):
        raise ValueError(f"{name}: aliases must be a list of lower-case names")
    headings = [line[3:].strip() for line in body.splitlines() if line.startswith("## ")]
    if headings != list(SECTIONS):
        raise ValueError(f"{name}: sections must be {' · '.join(SECTIONS)} in that order (found {' · '.join(headings)})")
    meta["aliases"] = aliases
    meta["declared_as"] = meta["declared_as"].rstrip() + "\n"
    return meta, body


def placeholders(node):
    """The names used by `{"$param": name}` nodes anywhere in a MongoDB entry."""
    if isinstance(node, dict):
        if "$param" in node:
            return {node["$param"]} if len(node) == 1 and isinstance(node["$param"], str) else {None}
        return set().union(*(placeholders(v) for v in node.values())) if node else set()
    if isinstance(node, list):
        return set().union(*(placeholders(v) for v in node)) if node else set()
    return set()


def check_mongo_entry(name, query):
    spec, where = query["mongo"], f"{name}: query {query['id']}"
    if not isinstance(spec, dict) or spec.get("op") not in MONGO_OPS or not isinstance(spec.get("collection"), str):
        raise ValueError(f"{where}: `mongo` needs op ({', '.join(MONGO_OPS)}) and a collection")
    extra = [k for k in spec if k not in MONGO_KEYS]
    if extra:
        raise ValueError(f"{where}: `mongo` has unknown keys {', '.join(extra)}")
    if (spec["op"] == "aggregate") != ("pipeline" in spec) or (spec["op"] == "distinct") != ("field" in spec):
        raise ValueError(f"{where}: aggregate takes `pipeline`, distinct takes `field`, and no other op takes either")
    declared = {p["name"] for p in query["params"]}
    used = placeholders(spec)
    if None in used or used - declared:
        raise ValueError(f"{where}: `$param` must be {{\"$param\": name}} for a name listed under params "
                         f"(unknown: {', '.join(sorted(str(u) for u in used - declared))})")


def parse_queries(text, name):
    data = yaml.safe_load(text) or {}
    queries = data.get("queries") if isinstance(data, dict) else None
    if not isinstance(queries, list):
        raise ValueError(f"{name}: expected a `queries:` list")
    seen = set()
    out = []
    for i, query in enumerate(queries):
        mongo = isinstance(query, dict) and "mongo" in query
        keys = tuple("mongo" if k == "sql" else k for k in QUERY_KEYS) if mongo else QUERY_KEYS
        if not isinstance(query, dict) or set(query) != set(keys):
            raise ValueError(f"{name}: query {i} must have exactly {', '.join(keys)}")
        if not isinstance(query["id"], str) or not SERVICE.match(query["id"]):
            raise ValueError(f"{name}: query {i} has a bad id")
        if query["id"] in seen:
            raise ValueError(f"{name}: duplicate query id {query['id']}")
        seen.add(query["id"])
        if not mongo and (not isinstance(query["sql"], str) or not query["sql"].strip()):
            raise ValueError(f"{name}: query {query['id']} has no sql")
        if not isinstance(query["tags"], list) or not isinstance(query["params"], list):
            raise ValueError(f"{name}: query {query['id']} tags and params must be lists")
        for param in query["params"]:
            if not isinstance(param, dict) or "name" not in param:
                raise ValueError(f"{name}: query {query['id']} has a param without a name")
        if mongo:
            check_mongo_entry(name, query)
            out.append(query)
        else:
            out.append({**query, "sql": query["sql"].strip() + "\n"})
    return out


def read_dir(directory):
    """(pages, catalogs) found in one directory; a catalog is not yet tied to a page."""
    directory = Path(directory)
    pages, catalogs = {}, {}
    for path in sorted(directory.glob("*.md")):
        if path.name == "README.md":
            continue
        meta, body = parse_page(path.read_text(), path.stem)
        pages[meta["service"]] = {**meta, "body": body, "queries": []}
    for path in sorted((directory / "queries").glob("*.yaml")):
        catalogs[path.stem] = parse_queries(path.read_text(), "queries/" + path.name)
    return pages, catalogs


def load(directory, *company):
    """Every page and catalog under `directory`, keyed by service; raises on the first bad file.

    Each `company` directory is layered over the one before it: a page with the same name
    replaces the release's, and a catalog replaces that service's queries. A page that arrives
    without a catalog keeps the queries it had. That is how a company's private config adds its
    databases and their named queries without touching the release."""
    pages, catalogs = read_dir(directory)
    for extra in company:
        if not Path(extra).is_dir():
            continue
        more_pages, more_catalogs = read_dir(extra)
        pages.update({name: {**page, "queries": pages.get(name, page)["queries"]} for name, page in more_pages.items()})
        catalogs.update(more_catalogs)
    for name, queries in catalogs.items():
        if name not in pages:
            raise ValueError(f"queries/{name}.yaml: no page named {name}")
        pages[name]["queries"] = queries
    aliases = {}
    for service, page in pages.items():
        for alias in page["aliases"]:
            if alias in pages or alias in aliases:
                raise ValueError(f"{service}: alias {alias} names another integration")
            aliases[alias] = service
    return pages, aliases


class Catalog:
    def __init__(self, directory, *company):
        self.directory = Path(directory)
        self.company = tuple(Path(d) for d in company)
        self._cache = None

    @property
    def loaded(self):
        # The release tree never changes while the process runs, so one parse per process.
        if self._cache is None:
            if not self.directory.is_dir():
                log.warning("No integrations directory at %s", self.directory)
                self._cache = ({}, {})
            else:
                self._cache = load(self.directory, *self.company)
        return self._cache

    def resolve(self, name):
        pages, aliases = self.loaded
        name = str(name or "").strip().lower()
        service = aliases.get(name, name)
        if service not in pages:
            raise Problem("not_found", f"No tool named {name}", 404)
        return pages[service]

    def listing(self):
        pages, _ = self.loaded
        # credentials and access belong on the index: MCP `hub_tool_list` is what a bot
        # reads to learn every outside system and what it needs before opening a page.
        keys = ("service", "title", "kind", "summary", "access", "credentials", "declared_as",
                "writes", "owner", "aliases")
        return [{key: page[key] for key in keys} | {"query_count": len(page["queries"])}
                for page in pages.values()]


# Verbs that change nothing outside: a bot whose entry may do only these is read only, whatever the page allows.
# `use` is the model a bot runs on.
READ_VERBS = ("read", "list", "search", "query", "use")


def write_level(can, writes):
    """What one bot may change through a tool: `never` (read only), `approval` or `allowed`, the page's `writes`
    narrowed by the verbs the bot's own entry declares."""
    return "never" if writes == "never" or all(verb in READ_VERBS for verb in can or ()) else writes


def inventory(c, settings, auth, who, aliases=None):
    """{service: {service, title, bots, connections}} for the tools the bots `who` may read declare. A bot may name a
    page by one of its `aliases` (`close` for close-crm), so each is filed under the page's own name."""
    from . import bot_tools
    found = {}
    for bot in H.bots(c):
        slug = bot["slug"]
        if bot["state"] == "archived" or not auth.bot_access(c, who, bot["id"])["read"]:
            continue
        for tool in bot_tools.listing(c, settings, bot["id"])["tools"]:
            service = tool["service"]
            service = (aliases or {}).get(str(service).lower(), service)
            item = found.setdefault(service, {"service": service, "title": tool["name"], "bots": [], "connections": []})
            if bot["id"] not in item["bots"]:
                item["bots"].append(bot["id"])
            # The problem a computer or the hub found (a missing credential, an MCP server that refused it) and the
            # variable it is read from: Library › Tools lists them as the tool's issues. Names only, never a value.
            item["connections"].append({"bot": bot["id"], "slug": slug, "status": tool["status"], "identity": tool["identity"], "can": tool["can"],
                                        **({"problem": tool["problem"]} if tool.get("problem") else {}),
                                        **({"env": tool["env"]} if tool.get("env") else {}),
                                        **({"mcp": tool["mcp"]} if tool.get("mcp") else {})})
    for item in found.values():
        statuses = {connection["status"] for connection in item["connections"]}
        item["status"] = "problem" if "problem" in statuses else "ready" if "ready" in statuses else "pending" if "pending" in statuses else "unknown"
    return found


def custom_page(item):
    return {"service": item["service"], "title": item["title"], "kind": "mcp" if any(connection.get("mcp") for connection in item["connections"]) else "api",
            "summary": "Configured by a bot",
            "access": "", "credentials": [], "declared_as": "",
            "writes": "allowed" if any(verb not in READ_VERBS
                                       for connection in item["connections"] for verb in connection["can"]) else "never", "owner_id": "owner", "aliases": [], "queries": [], "query_count": 0,
            "body": "", **item}   # who has it is Library › Tools' Access card


def learnings(c, service):
    return [dict(row) for row in c.execute(
        "SELECT id, integration, actor, text, created_at FROM learnings "
        "WHERE integration=? AND deleted_at IS NULL ORDER BY created_at DESC, id", (service,))]


def learning_counts(c):
    return {row[0]: row[1] for row in c.execute(
        "SELECT integration, count(*) FROM learnings WHERE deleted_at IS NULL GROUP BY integration")}


def add_learning(c, who, service, text):
    if privacy.private_execution(c, who):
        raise Problem("privacy", "Private task work cannot be published as a company learning", 403)
    day = H.now()[:10]
    n = c.execute("SELECT count(*) FROM learnings WHERE actor=? AND substr(created_at,1,10)=?",
                  (who.actor, day)).fetchone()[0]
    if n >= LEARNINGS_PER_DAY:
        raise Problem("cap", f"{n} learnings today; the cap is {LEARNINGS_PER_DAY} a day. "
                             "Fold what you know into the page instead.", 429)
    row = {"id": H.new_id(), "integration": service, "actor": who.actor, "text": text.strip(), "created_at": H.now()}
    c.execute("INSERT INTO learnings(id, integration, actor, text, created_at) VALUES(:id, :integration, :actor, :text, :created_at)", row)
    H.event(c, who.actor, "learning.add", service, {"id": row["id"], "chars": len(row["text"])})
    return row


class DatabaseQuery(Contract):
    """One `hub db` query, as its runner reports it: the statement and its size, never a row."""
    database: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,40}$")
    kind: str = Field(pattern=r"^(postgres|mysql|mongodb|sqlite)$")
    statement: str = Field(min_length=1, max_length=4000)
    rows: int = Field(ge=0, le=1_000_000)
    truncated: bool = False
    ms: int = Field(ge=0, le=3_600_000)
    query: str | None = Field(default=None, max_length=100)
    params: list[str] = Field(default_factory=list, max_length=20)
    error: str | None = Field(default=None, max_length=40)
    # MongoDB: the statement is the call's shape, values replaced by types (clients/dbmongo.py)
    operation: str | None = Field(default=None, pattern=r"^[a-z]{1,20}$")
    collection: str | None = Field(default=None, max_length=120)


def install_integrations(app, store, auth, mutate):
    company = store.settings.company_integrations_dir or store.settings.registry_dir / "integrations"
    catalog = app.state.integrations = Catalog(store.settings.integrations_dir, company)

    def named(c, page):
        """`owner: owner` is a role, so a shipped page never names anyone; serve the company owner's name."""
        if str(page.get("owner", "")).strip().lower() != "owner":
            return page
        row = c.execute("SELECT name FROM humans WHERE id=?", (auth.owner_id(c),)).fetchone() if auth.owner_id(c) else None
        return page | {"owner_id": (row and row["name"]) or store.settings.owner_email or "owner"}

    def retained(service):
        return {**custom_page({"service": service, "title": service, "connections": [], "bots": []}),
                "summary": "Tool removed", "status": "removed", "read_only": True,
                "body": "This tool is no longer declared. Its learnings are kept here."}

    def resolve(service, who):
        try:
            return catalog.resolve(service)
        except Problem as exc:
            if exc.code != "not_found":
                raise
            with store.read() as c:
                item = inventory(c, store.settings, auth, who, catalog.loaded[1]).get(service)
                if not item and c.execute("SELECT 1 FROM learnings WHERE integration=? AND deleted_at IS NULL LIMIT 1",
                                          (service,)).fetchone():
                    return retained(service)
            if item:
                return custom_page(item)
            raise

    @app.post("/api/v2/databases/audit")
    def database_audit(request: Request, body: DatabaseQuery):
        """The runner records each company-database query here before it shows the rows."""
        who = request.state.identity
        auth.domain(who)

        def work(c):
            # Param names, not values: a value may be the very thing the query looks up.
            H.event(c, who.actor, "db.query", body.database, body.model_dump(exclude={"database"}))
            return {"ok": True}
        return mutate(request, body, work)

    @app.get("/api/v2/integrations")
    def integrations(request: Request):
        with store.read() as c:
            counts = learning_counts(c)
            connected = inventory(c, store.settings, auth, request.state.identity, catalog.loaded[1])
            # A page keeps its own title; the bots' entries add who uses it and how that is going.
            items = [named(c, item) | {key: value for key, value in connected.get(
                         item["service"], {"status": "not_configured", "bots": [], "connections": []}).items() if key != "title"}
                     for item in catalog.listing()]
            known = {item["service"] for item in items}
            items += [named(c, custom_page(item)) for service, item in connected.items() if service not in known]
            present = {item["service"] for item in items}
            items += [named(c, retained(service)) for service in counts if service not in present]
        for item in items:
            for connection in item["connections"]:
                connection["write"] = write_level(connection["can"], item["writes"])
        return {"label": "Available tools", "integrations": [item | {"learning_count": counts.get(item["service"], 0)} for item in items]}

    @app.get("/api/v2/integrations/{service}")
    def integration(request: Request, service: str):
        page = resolve(service, request.state.identity)
        with store.read() as c:
            notes = learnings(c, page["service"])
            page = named(c, page)
        return {**page, "learnings": notes}

    @app.get("/api/v2/integrations/{service}/queries")
    def queries(request: Request, service: str, term: str = "", id: str | None = None):
        """The catalog search `hub tool query-search` does, served so every client reads alike."""
        from clients.hubtools import query_search
        page = resolve(service, request.state.identity)
        if id:
            for query in page["queries"]:
                if query["id"] == id:
                    return query
            raise Problem("not_found", f"No query {id} under {page['service']}", 404)
        return {"service": page["service"], "queries": query_search(page["queries"], term)}

    @app.post("/api/v2/integrations/{service}/learnings")
    def learn(request: Request, service: str, body: Learning):
        who = request.state.identity
        auth.domain(who)
        page = resolve(service, request.state.identity)

        def work(c):
            if page.get("read_only"):
                raise Problem("removed", "This tool was removed; its existing learnings are still available", 409)
            return add_learning(c, who, page["service"], body.text)
        return mutate(request, body, work)

    @app.post("/api/v2/integrations/{service}/learnings/{lid}/delete")
    def forget(request: Request, service: str, lid: str):
        who = request.state.identity
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner deletes a learning", 403)
        try:
            service = catalog.resolve(service)["service"]
        except Problem as exc:
            if exc.code != "not_found":
                raise
        with store.transaction() as c:
            row = c.execute("SELECT id FROM learnings WHERE id=? AND integration=? AND deleted_at IS NULL",
                            (lid, service)).fetchone()
            if not row:
                raise Problem("not_found", "Learning not found", 404)
            c.execute("UPDATE learnings SET deleted_at=? WHERE id=?", (H.now(), lid))
            H.event(c, who.actor, "learning.delete", service, {"id": lid})
        return {"ok": True}
