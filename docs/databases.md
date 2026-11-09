# Databases

Bots often need a number that lives in your own database: orders this week, signups by
region, tickets waiting. `hub db` lets a bot ask for it with one read-only statement, without the
server or the bot ever holding a writable password. PostgreSQL, MySQL/MariaDB, MongoDB (Atlas) and
SQLite files work out of the box; BigQuery and Snowflake are covered at the end.

```bash
hub db list                                             # what this bot may use
hub db warehouse "SELECT status, count(*) FROM orders GROUP BY 1"
hub db warehouse --query revenue-by-month --param start=2026-01-01 --param end=2026-07-01
hub db atlas find orders '{"status": "paid"}' --limit 20        # MongoDB: see "MongoDB Atlas" below
```

(`hub sql` is a different thing: read-only SQL over Tico's own database, [hub-sql.md](hub-sql.md).)

## Where it runs, and why

`hub db` runs on the computer that runs the bot, inside the run, not on the Tico server. The
connection string is stored in Tico's Credentials and delivered only to a bot granted it, for one run.
The server stores, decrypts and delivers it, so a compromised server could expose it; protect the
[Credential key and backups](credential-vault.md#the-local-credential-key-and-backups). Actual database access depends on network reachability and the database role.
The server authenticates the caller, keeps the audit trail and serves named queries; `hub db` executes on the computer, and
there is no `hub_db` tool on the server's MCP endpoint for that reason.

Layers, in order of strength (MongoDB has its own list under [MongoDB Atlas](#mongodb-atlas)):

1. **The database role**: a user that can only `SELECT`. This is the real boundary.
2. **The session**: opened read-only (`BEGIN READ ONLY` on PostgreSQL, `SET SESSION TRANSACTION
   READ ONLY` on MySQL, `mode=ro` plus an authorizer on SQLite), so a role that was granted too
   much still cannot write through `hub db`.
3. **The statement**: one `SELECT`, `WITH`, `VALUES`, `SHOW` or `EXPLAIN`; no `INTO`, no second
   statement. This turns mistakes into clear messages; do not rely on it alone.
4. **Limits**: a row cap (default 500, ceiling 10,000) and a statement timeout (default 20 s,
   ceiling 120 s). A result that hit the cap says `truncated`.
5. **The grant**: the bot must declare the database under `tools:` in its `bot.yaml`.
6. **The audit**: each query is recorded in Tico as a `db.query` event (statement, row count,
   time, parameter names; never a value or a row) before the rows are shown. If Tico cannot
   record it the result is withheld.
7. **Redaction**: the connection string and its password are scrubbed from every error.

## Set up in five steps

### 1. Create a read-only user

Do this on a replica if you have one (see Security). Use a long random password.

PostgreSQL:

```sql
CREATE ROLE tico_readonly LOGIN PASSWORD 'generate-a-long-random-one';
GRANT CONNECT ON DATABASE app TO tico_readonly;
GRANT USAGE ON SCHEMA public TO tico_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO tico_readonly;
-- Tables created later, by the role that creates them (repeat per owner):
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public GRANT SELECT ON TABLES TO tico_readonly;
ALTER ROLE tico_readonly SET default_transaction_read_only = on;
ALTER ROLE tico_readonly SET statement_timeout = '30s';
ALTER ROLE tico_readonly CONNECTION LIMIT 5;
-- Keep personal columns out of reach by granting columns instead of the table:
--   REVOKE SELECT ON customers FROM tico_readonly;
--   GRANT SELECT (id, plan, region, created_at) ON customers TO tico_readonly;
```

MySQL and MariaDB:

```sql
CREATE USER 'tico_readonly'@'10.%' IDENTIFIED BY 'generate-a-long-random-one' WITH MAX_USER_CONNECTIONS 5;
GRANT SELECT ON app.* TO 'tico_readonly'@'10.%';
-- Or column by column: GRANT SELECT (id, plan, region, created_at) ON app.customers TO 'tico_readonly'@'10.%';
```

(MariaDB: `CREATE USER ... IDENTIFIED BY '...'` then `GRANT ...`, and set `max_user_connections`
with `GRANT USAGE ON *.* TO ... WITH MAX_USER_CONNECTIONS 5`.) Restrict the host to the network
your runner computers are on.

SQLite: nothing to create; give the owner's user read access to the file. `hub db` opens it
`mode=ro`.

### 2. Store a Credential and grant it

The variable name is `DB_<NAME>_URL` for a database you will call `<name>` (`warehouse` gives
`DB_WAREHOUSE_URL`; a `-` becomes `_`). In **Tools → Credentials → Add credential**, choose
**connection**, set **Bot variable name** to `DB_WAREHOUSE_URL`, and paste the read-only connection
string into **Value**. Save, then use **Access → Grant access to** to grant it to each bot that needs it.
BotOps can open the Credential card and grant it for you. Never paste the value into ordinary chat,
a task or a repository.

Formats: `postgresql://user:pass@host:5432/db?sslmode=require`, `mysql://user:pass@host:3306/db`
(`?ssl=true` or `?ssl_ca=/path/ca.pem` for TLS), `sqlite:////absolute/path/data.db`. Percent-encode
special characters in a password (`@` is `%40`). Tico delivers the granted variable only during the
bot's run. A stored `op://Team Bots/Warehouse read only/url` reference is resolved on its computer
([Credentials](credential-vault.md)).

Existing installs migrate their old credential files into grants on upgrade. File import is a
migration path, not setup for a new bot: `hub credential import DB_WAREHOUSE_URL --from-bot <bot>`
imports an old own-file value, then `hub credential grant DB_WAREHOUSE_URL --to <bot>` grants it.
New runs do not load `secrets/<bot>.env` or `_shared.env` as a fallback.

### 3. Grant bots

Add a `tools:` entry to each bot's `bot.yaml` ([creating-bots.md](creating-bots.md),
"Access and credentials"). Not listed means not allowed, and the owner decides:

```yaml
tools:
  - service: postgres            # postgres | mysql | sqlite
    identity: read-only role on the reporting replica
    database: warehouse          # the name after `hub db`
    can: [read]
    env: DB_WAREHOUSE_URL        # optional; this is the default for `warehouse`
    max_rows: 200                # optional: lower this bot's row cap
    timeout_seconds: 10          # optional: lower this bot's timeout
    note: "revenue reporting only; counts, no customer rows"
```

A bot without the entry is refused even when it has the Credential grant. Both are needed. A human who
runs `hub db` from their own shell (with their personal API token, see
[how-it-works.md](how-it-works.md)) is granted by having the connection string in their own
environment.

### 4. Add the database's page and its named queries

Named queries are statements humans already trust, so a bot picks `revenue-by-month`
instead of inventing SQL. They belong to your team, not to Tico: put them in your
[private team config](#a-private-team-config), not in this repository.

`integrations/warehouse.md` describes the database for bots (what it holds, which columns are
personal data, gotchas) in the format every tool page has ([integrations/README.md](../integrations/README.md));
`integrations/queries/warehouse.yaml` lists queries as `{id, title, description, category, tags,
database, sql, params}`. Bind parameters as `:name`; `$1`, `$2` also work and mean the params in the
order listed. A full example for a fictional team is in
[templates/company-config/](../templates/company-config/). The page and query file are named after the
database (`warehouse`), so `hub tool query-search warehouse revenue` searches and `hub db warehouse --query
<id>` runs.

### 5. Test with the doctor

```bash
hub db doctor warehouse
```

It checks the grant, that the credential is set, that it connects, that the session is read-only,
and whether the role can write to any table (a warning names the fix). Run it as a bot by asking
the bot to run it in a run, or as yourself:

```bash
export HUB_API_URL=https://tico.acme.example HUB_TOKEN=tico_pt_...      # a personal API token
export DB_WAREHOUSE_URL='postgresql://tico_readonly:...@replica.internal:5432/app'
hub db doctor warehouse
```

## MongoDB Atlas

`hub db` also reads MongoDB, and MongoDB Atlas is the case it is built for. A bot cannot send SQL or
a command: it picks one of five operations and passes Extended JSON.

```bash
hub db atlas collections                                         # collections, with field names and types from a sample
hub db atlas find orders '{"status": "paid"}' --projection '{"total": 1}' --sort '{"placed_at": -1}' --limit 20
hub db atlas aggregate orders '[{"$match": {"status": "paid"}}, {"$group": {"_id": "$region", "n": {"$sum": 1}}}]'
hub db atlas count orders '{"status": "paid"}'
hub db atlas distinct orders status ['{"region": "eu"}']
hub db atlas --query signups-since --param since=2026-09-01       # a named query (step 4 below)
```

Input is Extended JSON in its relaxed form: plain JSON plus `{"$oid": "..."}` and
`{"$date": "2026-09-01T00:00:00Z"}`. Output is the same, one document per line, then `N documents (M ms)`;
`--json` prints the full result. Dates are read as UTC.

### Layers for MongoDB

1. **The Atlas user**: the built-in `read` role on one database. This is the real boundary.
2. **The tool**: five operations only; `$out`, `$merge` and the JavaScript operators `$where`,
   `$function` and `$accumulator` are refused at any depth of a filter or pipeline (a `$unionWith` or
   `$lookup` sub-pipeline too), as are `system.*` collections and `$currentOp`-style stages. This holds
   even if the user was given a write role by mistake, and turns a mistake into a clear message.
3. **Read preference**: `secondaryPreferred` by default, so reads go to a secondary when there is one.
   Change it per bot with `read_preference:` in the `tools:` entry, or `?readPreference=` in the URL.
4. **Limits**: `maxTimeMS` (default 20 s, ceiling 120 s) on every operation and a cap on documents
   (default 500, ceiling 10,000; an aggregation gets a `$limit` appended on the server). A result that
   hit the cap says `truncated`.
5. **The doctor**: asks Atlas which privileges the user holds (`connectionStatus` with
   `showPrivileges`) and warns when any is a write action, or when the role reaches other databases.
6. **The audit and the grant**, as for the other databases.

### 1. Create the Atlas user and the network path

In the Atlas UI, in the project that holds the cluster:

1. **Security, Database & Network Access, Database Access, Add New Database User.**
2. Authentication method **Password**; set a username (`tico_readonly`) and a long random password
   (Autogenerate Secure Password, copy it now).
3. **Database User Privileges**: do not pick a built-in role such as **Only read any database**
   (it reaches every database) and clear any role Atlas preselects. Expand **Specific Privileges**,
   click **Add Specific Privilege**, choose the role **read** and enter your database (for example
   `app`), collection empty. This is the built-in `read` role on that one database.
4. **Restrict Access to Specific Clusters/Data Lakes**: turn it on and select only this cluster.
5. Leave **Temporary User** off (a temporary user stops working when it expires), then **Add User**.

The same with the [Atlas CLI](https://www.mongodb.com/docs/atlas/cli/current/) (`--role` is
`roleName@database`; `--scope` limits the user to the named cluster):

```bash
atlas dbusers create --username tico_readonly --password "$TICO_RO_PASSWORD" --role read@app --scope Cluster0 --projectId <project-id>
```

Network access, in one of two ways:

- **IP access list** (the simple way): **Security, Database & Network Access, Network Access, Add IP
  Address** and enter the runner computer's public egress IP as a single address (`203.0.113.7/32`),
  with a comment saying which runner it is. CLI: `atlas accessLists create 203.0.113.7/32 --type cidrBlock --comment "tico runner" --projectId <project-id>`.
  Give the runner a stable egress IP (an Elastic IP or a NAT gateway) or the entry goes stale.
  **Do not use `0.0.0.0/0` ("Allow access from anywhere")**: it puts the cluster's login on the whole
  internet and leaves a password as the only barrier. Use a temporary entry for a first test only and
  delete it.
- **Private endpoint** (better, for runners in AWS, Azure or Google Cloud): **Network Access, Private
  Endpoint**, create the endpoint for the runner's VPC, and use the *private-endpoint* connection
  string Atlas shows for it (`mongodb+srv://...-pri.xxxxx.mongodb.net`). Nothing is opened to the internet.

### 2. Store and grant the SRV connection string

In **Atlas, Database, Connect, Drivers**, copy the `mongodb+srv://` string and add the password, the
database name in the path and `authSource=admin`:

Store that string as a **connection** Credential in **Tools → Credentials**, set **Bot variable name**
to `DB_ATLAS_URL`, and grant it to each bot that needs Atlas (the same steps as SQL step 2).
Its shape is `mongodb+srv://tico_readonly:<password>@<cluster-host>/app?authSource=admin&retryWrites=false`;
put the actual value only in the Credential card.

The name follows the same rule as for the SQL databases (`atlas` gives `DB_ATLAS_URL`). The path
database (`/app`) is required: it is the database the operations run on. For a host under
`mongodb.net` the tool sets `authSource=admin` if the URL does not say (Atlas users live in `admin`).
`mongodb+srv://` turns TLS on and reads the cluster's hosts from DNS, so the runner needs working DNS
for SRV and TXT records. Percent-encode special characters in the password (`@` is `%40`). A
self-managed replica set works too: `mongodb://user:pass@host1:27017,host2:27017/app?replicaSet=rs0&tls=true`.

### 3. Grant bots

```yaml
tools:
  - service: mongodb
    identity: Atlas user with the read role on app
    database: atlas              # the name after `hub db`
    can: [read]
    env: DB_ATLAS_URL            # optional; this is the default for `atlas`
    max_rows: 200                # optional: lower this bot's document cap
    timeout_seconds: 10          # optional: lower this bot's maxTimeMS
    read_preference: secondaryPreferred   # optional; the default
    note: "product analytics: counts and groups, no email addresses"
```

### 4. Named queries for MongoDB

A query entry has `mongo:` where a SQL entry has `sql:`: an `op` (`find`, `aggregate`, `count`,
`distinct`), the `collection` and a `filter`, `pipeline` (or `field`, for `distinct`), plus the
usual `params`. A value comes from a parameter as `{"$param": "name"}`:

```yaml
- id: signups-since
  title: Signups since a date
  category: growth
  tags: [signups]
  database: atlas
  mongo:
    op: count
    collection: accounts
    filter:
      created_at: {$gte: {$param: since}}
      plan: {$param: plan}
  params:
  - {name: since, type: date, label: Since, required: true}
  - {name: plan, type: text, label: Plan, required: false, default: team}
```

The placeholder replaces a whole value in the parsed document with a typed value (`text`, `int`,
`number`, `bool`, `date`, `objectid`, `list`); it is never pasted into JSON text, so a value such as
`{"$ne": null}` stays one string and cannot become an operator. A text value that starts with `$` is
refused, since inside a pipeline it would be read as a field path. The server checks the entry when
the query file loads (op, collection, every `$param` declared). The full example is
[templates/company-config/integrations/queries/atlas.yaml](../templates/company-config/integrations/queries/atlas.yaml).

### 5. Test with the doctor

```bash
hub db doctor atlas
```

It checks the grant, the credential, that it connects (and the server version), the read preference,
and the user's privileges. `WARN role privileges: roles readWrite@app allow writes (insert, update, ...)`
means the Atlas user is not the read-only one you meant to create; fix it in Atlas, not here.

### What the audit records for MongoDB

Tico gets the operation, the collection, the shape of the call and the number of documents:
`find accounts {"filter":{"email":"<string>","age":{"$gt":"<number>"}}}`. Every value in a call the
bot wrote is replaced by its type, so an email address, a name or an id someone looked up never lands
in the audit, which anyone who can read events sees. Field names and operators are kept, since they
are what a review needs ("who looked at `email`"), so do not put personal data in a field name. A
named query is the exception on purpose: its entry is reviewed text in your team's queries, so the
audit records the entry with `{"$param": ...}` left in and the parameter names, never their values.

### MongoDB troubleshooting

| You see | Usually |
|---|---|
| `srv_dns` | the host is misspelled, the cluster was deleted, or the runner's DNS resolver refuses SRV/TXT queries (some corporate resolvers do). Try `dig SRV _mongodb._tcp.cluster0.ab1cd.mongodb.net` on the runner; use another resolver, or Atlas's standard (non-SRV) string from the Connect dialog |
| `unreachable` (no server answered in 10 s) | the runner's public IP is not on the IP access list (check the address the runner really egresses from: `curl https://checkip.amazonaws.com`), the cluster is paused, or the private endpoint is not reachable from this computer |
| `tls` | the system clock is off, the CA bundle is missing or old, or a proxy re-signs TLS. Atlas needs TLS 1.2 or newer |
| `auth` | wrong user or password, the password is not percent-encoded, or `authSource` is not `admin` for an Atlas user |
| `read_only ... not authorized` | the collection is outside the user's role (the `read` role is per database) |
| `timeout` | add a `$match` on an indexed field or a date window, or raise `timeout_seconds` (ceiling 120 s) |
| `url: ... database in its path` | put the database in the string: `.../app?authSource=admin` |
| `driver: the mongodb driver is not installed` | `pip install 'pymongo>=4.10' dnspython` in the runner's environment; both are in `backend/requirements.txt` |

## Security guidance

- **Use a replica** when you can: a mistake in a query then costs the replica, not production. A
  bot's `max_rows` and `timeout_seconds` protect the server, but a replica is the real bulkhead.
- **Least privilege**: `SELECT` on the tables the bots need and nothing else, no superuser, no
  `CREATE`, a connection limit. Doctor warns when the role can write.
- **Personal data**: bots read what the role can read, and what a bot reads can end up in a
  task, a message or a memory file. Grant columns instead of tables, or expose a view that hides
  emails, phones and addresses, and say in the database's page which columns are off limits. The
  audit records the statement, so a review can see who asked for what.
- **Prompt injection**: text in the database (a customer note, a ticket body) is data, not
  instructions, and a bot that reads it can be told to do things. Keep the credential read-only
  so the worst outcome is a read the role allowed anyway, keep personal columns out of the role,
  and keep the rule "counts and ids, not rows of humans" on the database's page.
- **The statement checks are not a sandbox.** They catch the common mistake; a database
  function with side effects is only stopped by the role and the read-only session.
- **Never share a writable URL** with a bot, and do not put the connection string in a repository,
  a task or a chat. Rotate the password in the database and update the stored Credential's value in **Tools → Credentials**
  (step 2). Keep its name and grants; the next run receives the new value.

## Troubleshooting

| You see | Usually |
|---|---|
| `grant: ... does not declare database` | the bot's `bot.yaml` has no `tools:` entry with `database: <name>` (step 3) |
| `credential: DB_X_URL is not set` | step 2: store the Credential with the exact Bot variable name and grant it to this bot; files and shell exports do not grant access |
| `driver: the postgres driver is not installed` | `pip install 'psycopg[binary]'` (MySQL: `PyMySQL`) in the runner's environment; both are in `backend/requirements.txt` |
| `timeout` | add a date window or an index, or lower the work; the limit is 20 s unless the entry raises `timeout_seconds` (ceiling 120 s) |
| `read_only` | the statement wrote, or used `INTO`. `hub db` never writes |
| `refused: one statement per call` | remove the second statement; a trailing `;` is fine |
| `audit: ... result is withheld` | Tico could not record the query; check the runner can reach Tico and retry |
| connection refused / timed out | the database's firewall or security group does not allow the runner computer, or a VPN is down |
| `password authentication failed` | wrong password, or special characters not percent-encoded |
| `SSL` errors | add `?sslmode=require` (PostgreSQL) or `?ssl=true` (MySQL); a private CA needs `ssl_ca` |
| `prepared statement ... does not exist` behind a pooler | use the pooler's session mode, or the database's direct endpoint |
| `srv_dns: the SRV record ... could not be resolved` | MongoDB: a typo in the cluster host, the runner's DNS blocking SRV/TXT lookups, or a paused/deleted cluster; see [MongoDB Atlas](#mongodb-atlas) |
| `unreachable: no server answered within 10 s` | MongoDB: the runner's public IP is not on the Atlas IP access list, or the cluster is paused |
| `auth: authentication failed` | MongoDB: wrong Atlas user or password (percent-encode it), or `authSource` is not `admin` |
| `tls: the TLS handshake failed` | MongoDB: a wrong system clock, an old or missing CA bundle, or a proxy that intercepts TLS |
| result says `truncated` | more rows exist than the cap; add a `WHERE` or `LIMIT`, or aggregate |

## A private team config

Tico's repository is the product. Your team's own material (roster, skills, tool pages,
query files, playbooks) stays in a repository of your own, layered over an upstream release:

```
company-config/                    # a private git repository
  registry/                        # becomes the server's TICO_REGISTRY_DIR
    employees.yaml  people.yaml  hub-access.yaml  ...
    integrations/                  # layered over the release's integrations/
      warehouse.md
      queries/warehouse.yaml
  bot-<slug>/                      # one repository per bot (bot.yaml `tools:`, instructions)
  skills/                          # team skills your bots read
```

**How layering works.** The server reads the release's `integrations/` first, then your team's
directory: `<TICO_REGISTRY_DIR>/integrations` by default, or the directory named by
`TICO_INTEGRATIONS_DIR`. A team page replaces a release page of the same name (your `postgres.md`
would replace the generic one); a team `queries/<service>.yaml` replaces that service's
queries; a page with no queries keeps the one it had. Queries with no page, or a malformed file, are an error that
surfaces on the Tools page rather than being skipped silently. The files are read once per
process, so restart the server after a change.

**Deploying to the server.**

- Docker: the registry is `/data/registry` on the `tico-data` volume. Copy the files in and restart:
  ```bash
  docker compose cp company-config/registry/integrations/. server:/data/registry/integrations/
  docker compose restart server
  ```
  A bind mount (`./company-registry:/data/registry`) in a `compose.override.yaml` also works and
  lets `git pull` be the deploy.
- Native Linux checkout: copy the files into the environment's `registry/` directory (`TICO_REGISTRY_DIR`), then run
  `scripts/tico-computer -e <slug> server restart` as the service user. The systemd user unit is `tico-<slug>-api.service`
  (`tico-api.service` without a named environment). A cloud Docker installation uses the Docker steps above.
- Local environment on a Mac: the environment's `registry/` folder
  ([environments.md](environments.md)), then `scripts/tico-computer -e <slug> server restart`.

**Deploying to the runners.** A runner needs three things and no query files:

1. The Tico release (`hub`, `clients/dbquery.py`), which is what the runner already runs.
2. Each bot's repository with its `bot.yaml` `tools:` entries declaring the databases.
3. A stored Credential for each `DB_<NAME>_URL`, granted to that bot and delivered for its run.
   Keep values out of git and out of the config repository.

Named queries reach the runner over Tico's API at query time (`hub db <name> --query <id>` asks
the server for the statement), so a query update is a server deploy only.

**What stays upstream.** Tool pages for the services Tico has built-in support for, `hub db`, the driver setup and this
guide. What belongs in your config is anything with a team name, host, table or human in it.
Do not send upstream pull requests that contain them.

## BigQuery, Snowflake and other warehouses

`hub db` speaks PostgreSQL, MySQL/MariaDB, MongoDB and SQLite. For a warehouse with its own CLI, keep the
same shape by hand: stored Credentials granted to the bot, a read-only role or service account,
a bot `tools:` entry, and a tool page in your private config saying how to call the CLI.
Examples: `bq query --use_legacy_sql=false --maximum_bytes_billed=1000000000 --format=csv 'SELECT ...'`
(a service account with only `roles/bigquery.dataViewer` and `roles/bigquery.jobUser`, and a
billing cap) or `snowsql -o friendly=false -o output_format=csv -q 'SELECT ...'` (a role with only
`SELECT` on a warehouse with a resource monitor). Snowflake and Redshift accept a PostgreSQL-like
URL only in some setups; if yours does, `postgresql://` may work through `hub db` directly, but test
it with `hub db doctor` first. A dedicated driver adapter for either is a good upstream contribution:
add a `run_<kind>` next to the others in `clients/dbquery.py` with its own read-only session.
