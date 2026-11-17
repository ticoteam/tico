# Auth types

Every way a service asks to be authenticated, how a person hands it to Tico today, and what the bot then does with
it. The Tools Owner reads this to pick the right setup for a service; the test named under each type proves it works.

Two ways a value reaches a bot ([credential-vault.md](credential-vault.md#proxied-tools)):

- **Proxy** (Tico holds the key): an API tool with an address. The run gets a `tpx1.` token in the key's variable and
  the proxy's address in `TICO_TOOL_<ID>_URL`; it calls `$TICO_TOOL_<ID>_URL/<path>` with the token where the key goes,
  and Tico puts the real key on the call. The proxy can place a key in exactly five ways, the tool's **Auth**: Bearer
  token, Basic with the key as user, Basic with the key as password (the key's **Username** as user), a header, a query
  parameter. Anything that must be computed from the key (a signature, a digest, a TLS handshake) cannot go through
  the proxy.
- **On computers** (the bot gets the value itself): an API tool with no address, or a key stored with **Tools > Add >
  Key** (types API key, password, token, file). The value is in the run's environment under its variable (a file
  credential is an owner-only file whose path is in the variable), for that run only, and masked in everything the run
  posts. Only the value travels: a key's **Username** field does not reach a bot on computers, so a second field goes
  in as its own key with its own variable. A bot at **Read** on a computer tool that has an address and only a Read and
  write key gets a proxy token instead, so it is held to reads.

A key stored with **Tools > Add > Key** whose name or variable names a known service (`STRIPE_API_KEY`, "Close API
key") joins that service's tool, on the proxy when Tico knows its address (backend/tool_services.py). A webhook or
signing secret never does: it stays a plain key a bot gets as itself.

Tests: `runner_support/tests/test_credential_live.py` runs each scenario named below against the public service
(opt-in, `TICO_LIVE_AUTH=1`); `runner_support/tests/test_credential_delivery.py` runs the delivery paths against a stub
vendor. The candidates are published test credentials needing no account or spend.

## Keys and passwords

**API key in a header** (`api_key_header`). A static key in a named header (`x-api-key`, `xi-api-key`). Hand it over:
Tools > Add > API, Auth **Header** with the header's name, the address, a check path, then Add key; give bots a level.
Proxy. The bot calls `$TICO_TOOL_<ID>_URL/...` with the token in that header. Candidate: reqres.in, `x-api-key:
reqres-free-v1` (a wrong key is 403; no key at all is accepted, so only a wrong key proves it). Test:
`test_api_key_header_through_the_proxy_reqres` (the wrong key's 403 shows on Check now as Works (limited): only a
401 reads Key rejected, so a vendor that answers a bad key with 403 is judged by its real calls).

**API key in the query** (`api_key_query`). Hand it over: Auth **Query parameter** with the parameter's name. Proxy;
Activity keeps the parameter's name, never its value. Candidate: NASA `api_key=DEMO_KEY` (a few calls an hour per
address). Test: `test_api_key_query_through_the_proxy_nasa`.

**Static bearer token** (`bearer_static`) and **personal access token** (`pat_bearer`). Hand it over: Auth **Bearer
token**. Proxy (or on computers with no address). Candidate: httpbin.org `/bearer` (any token; no token is 401).
Tests: `test_bearer_through_the_proxy_httpbin` (httpbin's echo of the real key is scrubbed before the bot reads it),
`test_bearer_on_computers_httpbin`. A GitHub, Linear or Raygun PAT is the same shape (tier B, needs an account).

**Basic, user and password** (`basic_user_pass`). Hand it over: Auth **Basic, key as password**, the key's Username set
to the user. Proxy. On computers the bot gets only the password, so the task (or a second key) says the user. Candidates:
postman-echo.com `/basic-auth` (postman / password), httpbin.org `/basic-auth/<user>/<password>`. Tests:
`test_basic_password_through_the_proxy_postman_echo`, `test_basic_on_computers_httpbin`.

**Basic, key as user** (`basic_key_as_user`). Stripe, Close: the key is the user, the password empty. Hand it over:
Auth **Basic, key as user**. Proxy. No account-free candidate; the stub suite covers the proxy's Basic placement.

**Digest** (`digest_auth`). Challenge and response computed from the password. Hand it over: an API tool with no
address and its key (the password), on computers. The bot answers the challenge itself (Python's
`HTTPDigestAuthHandler`, `curl --digest`). Not through the proxy. Candidate: httpbin.org `/digest-auth/auth/<user>/<password>`.
Test: `test_digest_on_computers_httpbin`.

**Several fields** (`multi_field`). Twilio's SID and secret, an access key pair, Plaid's client id and secret. Hand it
over: one key per field, each with its own variable (the secret as the tool's key, the others with Tools > Add > Key),
all granted to the bot. On computers. Non-secret fields (a host, a region, a project id) become tool Settings later;
today they are keys too, or written in the task. Candidate: play.min.io's published key pair. Test:
`test_aws_sigv4_key_pair_on_computers_minio_play`.

**Secret in the URL** (`secret_in_url`). Telegram's `/bot<token>/`, a Discord webhook URL, an MCP URL with `?token=`.
Hand it over: a key on computers; the bot builds the URL from the variable. The proxy places keys only in a header or
the query, and a bot.yaml MCP `url` takes no `${VAR}`, so an MCP server keyed in its URL is not supported yet. No
account-free candidate.

**Database URL** (`db_url`). `postgres://user:password@host/db`. Hand it over: Tools > Add > Key with variable
`DATABASE_URL`, on computers; the bot's client connects with it. Not tested live (needs a local container).

**Mail login** (`smtp_imap_login`). An IMAP or SMTP username and password (or app password). Hand it over: Tools > Add >
Key, type password, granted to the bot; the user goes in the task or a second key. The bot logs in itself. Tico's own
mailboxes are a separate feature ([mail.md](mail.md)). Candidate: test.rebex.net IMAP (demo / password). Test:
`test_imap_login_on_computers_rebex`.

**CLI login** (`cli_login`). `GH_TOKEN`, `CLOUDFLARE_API_TOKEN`, `VERCEL_TOKEN` read by a vendor's CLI. Hand it over:
Tools > Add > Key with the variable the CLI reads, on computers (a proxy token would not work: the CLI talks to the
vendor itself). Tier B (needs an account).

**Session cookie** (`session_cookie`). A site's login that sets a cookie the next calls carry. Hand it over: the
password as Tools > Add > Key (type password), on computers; the bot logs in and keeps the cookie for the run. For a
site used in a browser, a website tool instead (below). Candidate: DummyJSON (emilys / emilyspass). Test:
`test_session_cookie_login_on_computers_dummyjson`.

**Website login only** (`website_login_only`). A site with no API: username, password, maybe a 2FA key. Hand it over:
Tools > Add > Website, its sites and an account. The bot never sees either value: `tico login fill` types them in
the browser ([credential-vault.md](credential-vault.md#website-logins)). Candidates: the-internet.herokuapp.com,
authenticationtest.com (published 2FA key). Covered by the stub suite's browser-host scenario; a live run needs Chrome.

## Files and certificates

**Client certificate, mTLS** (`mtls_cert`). A certificate and key the TLS handshake presents. Hand it over: Tools >
Add > Key, type file, with the PEM (certificate and key) and a variable, plus the key's passphrase as a second key
(type password). On computers: the bot loads the file (`curl --cert`, `ssl.load_cert_chain`). The proxy cannot present
a client certificate. Candidate: client.badssl.com with badssl.com's published client certificate (no certificate is
400). Test: `test_mtls_client_certificate_file_badssl`.

**SSH key** (`ssh_key`). Hand it over: a file key (the private key) with a variable; the file is mode 0600, as `ssh -i`
requires. On computers. Not tested live (needs a local container).

**Service account JSON** (`service_account_json`). Google's key file. Hand it over: a file key with variable
`GOOGLE_APPLICATION_CREDENTIALS`; the bot's Google library signs its own token request. On computers. Tico signing and
holding the token (so the key stays on the server) is not supported yet. Tier B.

## Signed requests

The bot holds the secret on computers and signs each request itself; the proxy cannot sign.

**AWS Signature Version 4** (`aws_sigv4`). Hand it over: `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` as two keys
(Several fields, above). The bot's SDK or `curl --aws-sigv4` signs. Short-lived STS credentials and assumed roles are
not supported yet. Candidate: play.min.io. Test: `test_aws_sigv4_key_pair_on_computers_minio_play`.

**OAuth 1.0a** (`oauth1_signed`). Consumer key and secret, optionally a token and token secret; HMAC-SHA1 per request.
Hand it over: each as a key with its own variable. The bot signs. Candidate: postman-echo.com `/oauth1` (published
consumer key and secret; it checks no token secret). Test: `test_oauth1_consumer_key_and_secret_on_computers_postman_echo`.

**Hawk** (`hawk`). An id and a key; a MAC per request. Hand it over: two keys. The bot signs. Candidate:
postman-echo.com `/auth/hawk`. Test: `test_hawk_on_computers_postman_echo`.

**HMAC request signing** (`hmac_request_signing`). Akamai EdgeGrid, Binance. Hand it over: the secret and client
tokens as keys; the bot signs. Only official test vectors exist account-free; not tested live.

**Webhook secret** (`webhook_secret`). A secret the vendor signs its deliveries with; the receiver checks the
signature. Hand it over: Tools > Add > Key with a variable such as `GITHUB_WEBHOOK_SECRET`: never a tool's key, so the
bot gets the secret itself. Candidate: GitHub's documented example (secret "It's a Secret to Everybody", payload
"Hello, World!"). Test: `test_webhook_secret_checks_githubs_documented_signature`.

## Where the service is

**Tenant subdomain** (`per_tenant_subdomain`). `acme.atlassian.net`, an Auth0 tenant, a PostHog region. Hand it over:
the tool's address is the tenant's (`https://acme.atlassian.net`). Proxy. Tier B.

**Sandbox and production** (`sandbox_prod_split`). Stripe test and live keys, PayPal sandbox and live hosts. Hand it
over today: two API tools (or a Read key and a Read and write key on one). Grouping a vendor's connections under one
vendor comes with the credential model. Tier B.

**IP allowlist** (`ip_allowlist`). The service accepts calls only from listed addresses. A proxied call leaves from the
Tico server's address; a call on computers from that computer's. Allowlist the one the tool uses. Not tested.

## MCP servers

**No auth** (`no_auth`). Hand it over: a `tools:` entry in the bot's `bot.yaml` with `mcp: {url, transport: http}` and
no `env` ([connect-tools.md](connect-tools.md#how-a-tool-is-connected)). Candidate: DeepWiki. Test:
`test_mcp_server_with_no_auth_deepwiki`.

**Pasted token** (an MCP server taking a PAT or API key in a header). Hand it over: an API tool whose address the
server's URL sits under, its key, and the `bot.yaml` entry's header as `Authorization: "Bearer ${VAR}"`. With the key
on the proxy the runner sends the server's calls through the proxy with the run's token in that header and Tico puts the
key in its place; a bot at Read gets only the server's reads ([credential-vault.md](credential-vault.md#proxied-tools)).
A key on computers goes in the header as itself. Covered by the stub suite; no account-free server takes a token.

**MCP OAuth** (`oauth_mcp`, `oauth_dcr`, `oauth_client_allowlist`). Sign-in with dynamic client registration and
PKCE. Not supported yet for bots' MCP servers. Candidates for when it is: the MCP conformance suite's auth scenarios,
example-server.modelcontextprotocol.io.

## Not supported yet

Tico does not run these flows or hold their tokens yet; they come with the credential model's ways to connect. Until
then a bot can only do what its own code does with secrets it gets on computers, and a person renews anything that
expires.

- **OAuth authorization code** (`oauth_authcode`), **PKCE public client** (`pkce_public_client`), **static client id
  and secret** (`oauth_static_client`), **refresh token rotation** (`refresh_token_rotation`): a person signing in to
  an API for the bots. Candidate: Duende's demo IdentityServer (bob / bob).
- **Client credentials** (`oauth_client_credentials`): Duende demo `m2m` / `secret`.
- **Device code** (`oauth_device_code`): Duende demo client `device`.
- **JWT client assertion** (`signed_jwt_key`, private_key_jwt) and **JWT bearer grant** (`jwt_bearer_grant`): Duende
  demo `m2m.jwt`.
- **DPoP** (`dpop`): Duende demo `m2m.dpop`.
- **Token exchange** (`token_exchange`, RFC 8693) and **CIBA** (`ciba_backchannel`).
