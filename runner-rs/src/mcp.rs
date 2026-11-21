//! A bot's remote MCP servers: the `mcp:` blocks of its `tools:` entries (clients/mcp_servers.py and
//! `clean_mcp`/`secret_in` in clients/access_entry.py), for the hosts, readiness and the reachability check.
//!
//! A header may use only its entry's own `env` variable as a `${VAR}` placeholder, filled only from what this
//! bot was granted for the run; a server whose variable is not granted is left out and named, never sent half
//! configured. Where the harness reads `${VAR}` itself (Claude Code, Gemini CLI, Codex's env headers) the
//! placeholder goes through untouched, so no value lands on a command line or in a file.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use regex::Regex;
use serde_json::{json, Map, Value};

use crate::env::{Env, ProxiedTool};
use crate::util::{text_of, truthy};

pub const REACH_TIMEOUT_S: u64 = 4;
pub const REACH_TTL_S: u64 = 300;
const MAX_HEADERS: usize = 10;
const NOT_SETTABLE: &[&str] = &["host", "content-length", "transfer-encoding", "connection"];

fn re(cell: &'static OnceLock<Regex>, pattern: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(pattern).unwrap())
}

fn placeholder_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    re(&R, r"\$\{([A-Z_][A-Z0-9_]*)\}")
}

/// Key shapes seen in the wild, and a URL with a password in it (access_entry.SECRET_SHAPES).
fn secret_shapes() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    re(
        &R,
        r"sk-[A-Za-z0-9_-]{8,}|[sr]k_(?:live|test)_\w+|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_\w+|glpat-[\w-]{10,}|xox[abposr]-[\w-]+|xapp-[\w-]+|ph[xcs]_[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{12,}|ASIA[0-9A-Z]{12,}|AIza[\w-]{20,}|ya29\.[\w.-]+|eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]*|-----BEGIN [A-Z ]*KEY|npm_[A-Za-z0-9]{20,}|SG\.[\w-]{16,}|\bBearer\s+\S{12,}|\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@|\bop:/{2}\S+",
    )
}

fn entropy(token: &str) -> f64 {
    let chars: Vec<char> = token.chars().collect();
    let mut counts: HashMap<char, usize> = HashMap::new();
    for c in &chars {
        *counts.entry(*c).or_default() += 1;
    }
    let n = chars.len() as f64;
    -counts
        .values()
        .map(|&c| c as f64 / n * (c as f64 / n).log2())
        .sum::<f64>()
}

/// A public identifier with a random-looking body, never a secret (access_entry.ID_SHAPES): a Twilio SID, two
/// capitals and 32 lowercase hex digits.
fn id_shape(token: &str) -> bool {
    let b = token.as_bytes();
    b.len() == 34
        && b[..2].iter().all(|c| c.is_ascii_uppercase())
        && b[2..]
            .iter()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(c))
}

/// A reason when `text` looks like a credential value, else "" (access_entry.secret_in).
pub fn secret_in(text: &str) -> String {
    if secret_shapes().is_match(text) {
        return "it contains what looks like a key, token or password".into();
    }
    for token in text.split(|c: char| c.is_whitespace() || ",;()\"'<>".contains(c)) {
        let long = token.chars().count() >= 24;
        if long
            && !id_shape(token)
            && token
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || "_+/=-".contains(c))
            && token.chars().any(|c| c.is_ascii_lowercase())
            && token.chars().any(|c| c.is_ascii_uppercase())
            && token.chars().any(|c| c.is_ascii_digit())
            && entropy(token) >= 3.9
        {
            return "it contains a long random-looking string".into();
        }
    }
    String::new()
}

/// The variable names `${NAME}` placeholders in `text` refer to, in order.
pub fn placeholders(text: &str) -> Vec<String> {
    placeholder_re()
        .captures_iter(text)
        .map(|c| c[1].to_string())
        .collect()
}

/// `text` with each `${VAR}` replaced by env[VAR], for a harness that cannot read the placeholder itself.
pub fn expand(text: &str, env: &Env) -> String {
    placeholder_re()
        .replace_all(text, |c: &regex::Captures| {
            env.get(&c[1]).cloned().unwrap_or_default()
        })
        .into_owned()
}

fn loopback_host(url: &url::Url) -> bool {
    match url.host() {
        Some(url::Host::Domain(d)) => {
            let d = d.to_lowercase();
            let d = d.trim_end_matches('.');
            d == "localhost" || d.ends_with(".localhost")
        }
        Some(url::Host::Ipv4(ip)) => ip.is_loopback(),
        Some(url::Host::Ipv6(ip)) => ip.is_loopback(),
        None => false,
    }
}

/// A `mcp:` block, validated and normalised: {url, transport, headers?} (access_entry.clean_mcp).
pub fn clean_mcp(mcp: &Value, env: &str) -> Result<Value, String> {
    let Some(m) = mcp.as_object() else {
        return Err(
            "mcp is an object such as {url: \"https://mcp.example.com/mcp\", transport: http}"
                .into(),
        );
    };
    let mut unknown: Vec<&String> = m
        .keys()
        .filter(|k| !["url", "transport", "headers"].contains(&k.as_str()))
        .collect();
    unknown.sort();
    if !unknown.is_empty() {
        return Err(format!(
            "mcp does not take {}; it takes url, transport, headers",
            unknown
                .iter()
                .map(|s| s.as_str())
                .collect::<Vec<_>>()
                .join(", ")
        ));
    }
    let url = crate::manifest::text_or_empty(m.get("url"))
        .trim()
        .to_string();
    if url.is_empty() || url.chars().count() > 500 || url.chars().any(char::is_whitespace) {
        return Err("mcp.url is the server's address, such as https://mcp.example.com/mcp".into());
    }
    let parsed = url::Url::parse(&url).map_err(|_| "mcp.url is not a valid address".to_string())?;
    match parsed.scheme() {
        "http" if !loopback_host(&parsed) => return Err(
            "mcp.url must be https; plain http is only for a server on this computer (localhost)"
                .into(),
        ),
        "http" | "https" => {}
        _ => return Err("mcp.url must be https".into()),
    }
    if parsed.host().is_none()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.fragment().is_some_and(|f| !f.is_empty())
    {
        return Err("mcp.url is a plain address: a host, no password and no #fragment".into());
    }
    if url.contains("${") || !secret_in(&url).is_empty() {
        return Err(
            "mcp.url holds no credential (put it in a header as ${VAR}, never in the address)"
                .into(),
        );
    }
    let transport = {
        let t = crate::manifest::text_or_empty(m.get("transport"));
        let t = if t.is_empty() { "http".to_string() } else { t };
        t.trim().to_lowercase()
    };
    if !["http", "sse"].contains(&transport.as_str()) {
        return Err("mcp.transport is one of http, sse".into());
    }
    let empty = Map::new();
    let headers = match m.get("headers") {
        None | Some(Value::Null) => &empty,
        Some(v) if !truthy(Some(v)) && !v.is_object() => &empty,
        Some(Value::Object(h)) if h.len() <= MAX_HEADERS => h,
        Some(_) => {
            return Err(format!(
                "mcp.headers is an object of at most {MAX_HEADERS} headers"
            ))
        }
    };
    static NAME: OnceLock<Regex> = OnceLock::new();
    let name_re = re(&NAME, r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$");
    let mut kept = Map::new();
    for (name, value) in headers {
        let name = name.trim().to_string();
        if !name_re.is_match(&name) || NOT_SETTABLE.contains(&name.to_lowercase().as_str()) {
            return Err(format!("'{name}' is not a header name Tico will send"));
        }
        let Some(value) = value.as_str().filter(|v| {
            !v.trim().is_empty()
                && v.chars().count() <= 500
                && !v.contains('\n')
                && !v.contains('\r')
        }) else {
            return Err(format!(
                "mcp.headers.{name} is one line of text, such as \"Bearer ${{JIRA_API_TOKEN}}\""
            ));
        };
        let bare = placeholder_re().replace_all(value, "");
        if bare.contains('$') {
            return Err(format!("mcp.headers.{name} may only use ${{VARIABLE}} placeholders (capitals, digits, underscores)"));
        }
        if !secret_in(&bare).is_empty() {
            return Err(format!(
                "mcp.headers.{name} holds what looks like a credential. Keep the value out of the entry: store it as the env variable and write ${{VARIABLE}} here"
            ));
        }
        for used in placeholders(value) {
            if used != env {
                let own = if env.is_empty() {
                    "(set `env`) ".to_string()
                } else {
                    format!("(${{{env}}}) ")
                };
                return Err(format!("mcp.headers.{name} uses ${{{used}}}, but only the entry's own env variable {own}may be a placeholder"));
            }
        }
        kept.insert(name, json!(value.trim()));
    }
    let mut out = json!({"url": url, "transport": transport});
    if !kept.is_empty() {
        out["headers"] = Value::Object(kept);
    }
    Ok(out)
}

fn has_mcp(entry: &Value) -> bool {
    match entry.get("mcp") {
        None | Some(Value::Null) => false,
        Some(Value::Object(o)) => !o.is_empty(),
        Some(Value::String(s)) => !s.is_empty(),
        Some(_) => true,
    }
}

fn entry_env(entry: &Value) -> String {
    crate::manifest::text_or_empty(entry.get("env"))
        .trim()
        .to_string()
}

fn service_of(entry: &Value) -> String {
    crate::manifest::text_or_empty(entry.get("service"))
        .trim()
        .to_lowercase()
}

/// [(entry, cleaned mcp)] for each `tools:` entry with a valid `mcp:` block.
pub fn declared(tools: &Value) -> Vec<(Value, Value)> {
    let mut found = vec![];
    for entry in tools.as_array().into_iter().flatten() {
        if entry.is_object() && has_mcp(entry) {
            if let Ok(m) = clean_mcp(&entry["mcp"], &entry_env(entry)) {
                found.push((entry.clone(), m));
            }
        }
    }
    found
}

/// Tico's own MCP server's name on every harness (hosts insert it after the bot's servers): no bot.yaml or granted
/// server takes it, so none is silently replaced by it.
pub const TICO_SERVER: &str = "tico";

/// The names a run's servers may not take: Tico's own.
fn reserved() -> HashSet<String> {
    HashSet::from([TICO_SERVER.to_string()])
}

/// `server_name(service)`, else it with `_2`, `_3`... the first `used` does not hold, which then holds it: two servers
/// never share a name, the same input always gives the same names.
fn unique_name(service: &str, used: &mut HashSet<String>) -> String {
    let base = server_name(service);
    let (mut name, mut n) = (base.clone(), 1);
    while used.contains(&name) {
        n += 1;
        name = format!("{base}_{n}");
    }
    used.insert(name.clone());
    name
}

/// The name a harness shows the model: the service, safe for a tool name (`google-drive` -> `google_drive`).
pub fn server_name(service: &str) -> String {
    let name: String = service
        .to_lowercase()
        .chars()
        .map(|c| {
            if c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect();
    if name.is_empty() {
        "mcp".into()
    } else {
        name
    }
}

/// The address a declared MCP server is reached by when Tico holds its key: the run's variable holds a proxy token,
/// which the vendor would refuse, so the call goes to the tool's proxy address with the rest of the server's path
/// under the tool's API address. None when the server is not under that address.
pub fn through_proxy(url: &str, tool: &ProxiedTool) -> Option<String> {
    let base = tool.base.trim_end_matches('/');
    if base.is_empty() {
        return None;
    }
    let rest = url.strip_prefix(base)?;
    if !(rest.is_empty() || rest.starts_with('/') || rest.starts_with('?')) {
        return None;
    }
    Some(format!(
        "{}/{}",
        tool.url.trim_end_matches('/'),
        rest.trim_start_matches('/')
    ))
}

/// (servers, problems) for one run. A server is {name, service, url, transport, headers, env}; one whose
/// variable is not in `env` is left out and named in `problems`. A server whose key Tico holds (`proxied`) is
/// reached through the proxy (`through_proxy`), or left out and named when it cannot be.
pub fn servers_for_run(
    tools: &Value,
    env: &Env,
    proxied: &[ProxiedTool],
) -> (Vec<Value>, Vec<String>) {
    let (mut servers, mut problems, mut used) = (vec![], vec![], reserved());
    for (entry, mcp) in declared(tools) {
        let service = service_of(&entry);
        let mut needs: Vec<String> = mcp["headers"]
            .as_object()
            .into_iter()
            .flatten()
            .flat_map(|(_, v)| placeholders(&text_of(v)))
            .collect();
        needs.sort();
        needs.dedup();
        if needs
            .iter()
            .any(|v| env.get(v).is_none_or(|x| x.trim().is_empty()))
        {
            problems.push(format!(
                "{service}: MCP server not passed, {} is not granted to this bot",
                needs.join(", ")
            ));
            continue;
        }
        let upstream = mcp["url"].clone();
        let mut url = upstream.clone();
        if let Some(tool) = proxied.iter().find(|t| needs.contains(&t.env)) {
            match through_proxy(&text_of(&url), tool) {
                Some(via) => url = json!(via),
                None => {
                    problems.push(format!(
                        "{service}: MCP server not passed. Tico holds its key, so it is reached through Tico, and its \
                         address {} is not under {}'s API address{}. Set that address to the MCP server's in Tools > {}",
                        text_of(&url),
                        tool.name,
                        if tool.base.is_empty() { String::new() } else { format!(" {}", tool.base) },
                        tool.name
                    ));
                    continue;
                }
            }
        }
        let name = unique_name(&service, &mut used);
        servers.push(json!({"name": name, "service": service, "url": url, "upstream": upstream, "transport": mcp["transport"],
                            "headers": mcp.get("headers").cloned().unwrap_or(json!({})), "env": entry_env(&entry)}));
    }
    (servers, problems)
}

/// The MCP servers a run's grants bring (credential-runtime's `mcp_servers`, backend/mcp_connect.py): one per granted
/// MCP tool, with no bot.yaml entry. Each is held to bot.yaml's rules (clean_mcp: its header uses only its own `env`
/// as a placeholder), and passed only when that variable reached this turn (`held`). A proxied one's address is the
/// tool proxy's path on the address this runner reaches the server by, as a proxied key's (env.rs `proxied_tool`).
/// (servers, problems), servers shaped as `servers_for_run`'s.
pub fn granted(items: &Value, server: &str, held: &BTreeSet<String>) -> (Vec<Value>, Vec<String>) {
    let (mut servers, mut problems) = (vec![], vec![]);
    // One server per connection (its address and variable): a tool's two keys in two variables are two servers. Two
    // tools whose ids make one name, or one whose id is Tico's own server's, each get their own name.
    let (mut used, mut seen) = (reserved(), HashSet::new());
    for item in items.as_array().into_iter().flatten() {
        let label = text_of(&item["tool"]);
        let service = text_of(&item["name"]).trim().to_lowercase();
        let env = text_of(&item["env"]);
        if service.is_empty() {
            continue;
        }
        let label = if label.is_empty() {
            service.clone()
        } else {
            label
        };
        if env.is_empty() || !held.contains(&env) {
            problems.push(format!(
                "{label}: MCP server not passed, its key did not reach this turn"
            ));
            continue;
        }
        let url = text_of(&item["url"]);
        let proxied = item["proxied"] == true;
        // The server's own address: a proxied one's `url` is Tico's proxy (an older server sends no `upstream`).
        let upstream = match text_of(&item["upstream"]) {
            u if !u.is_empty() => u,
            _ if !proxied => url.clone(),
            _ => String::new(),
        };
        let (checked, url) = if proxied {
            let Some(at) = url.find("/api/v2/tool-proxy/") else {
                problems.push(format!(
                    "{label}: MCP server not passed, its address is not Tico's tool proxy"
                ));
                continue;
            };
            let path = &url[at..];
            let base = if server.is_empty() {
                &url[..at]
            } else {
                server.trim_end_matches('/')
            };
            // The proxy is Tico's own address (plain http inside a Docker network, say): its path is what is checked.
            (
                format!("https://tico.invalid{path}"),
                format!("{base}{path}"),
            )
        } else {
            (url.clone(), url)
        };
        let block =
            json!({"url": checked, "transport": item["transport"], "headers": item["headers"]});
        match clean_mcp(&block, &env) {
            Ok(m) => {
                let server = json!({"service": service, "url": url, "upstream": upstream, "env": env,
                                    "transport": m["transport"], "headers": m.get("headers").cloned().unwrap_or(json!({}))});
                if seen.insert(connection(&server)) {
                    let mut server = server;
                    server["name"] = json!(unique_name(&service, &mut used));
                    servers.push(server);
                }
            }
            Err(why) => problems.push(format!("{label}: MCP server not passed. {why}")),
        }
    }
    (servers, problems)
}

/// A server's connection: the server's own address (`upstream`, whatever proxy address the run reaches it by) and the
/// variable its credential comes in. Two servers are one connection only when both match, so two accounts on one
/// endpoint stay two, and a proxied grant of bot.yaml's server is that server.
fn connection(server: &Value) -> (String, String) {
    let upstream = text_of(&server["upstream"]);
    let url = if upstream.is_empty() {
        text_of(&server["url"])
    } else {
        upstream
    };
    (url, text_of(&server["env"]))
}

/// `problems` without the bot.yaml servers left out for a variable the bot does not hold whose very connection (its
/// address and variable) a granted MCP tool brings: `merge` passes the granted one, so nothing is missing.
pub fn drop_covered(problems: Vec<String>, tools: &Value, granted: &[Value]) -> Vec<String> {
    let brought: HashSet<(String, String)> = granted.iter().map(connection).collect();
    let covered: Vec<String> = declared(tools)
        .iter()
        .filter(|(entry, mcp)| brought.contains(&(text_of(&mcp["url"]), entry_env(entry))))
        .map(|(entry, _)| format!("{}: MCP server not passed, ", service_of(entry)))
        .collect();
    problems
        .into_iter()
        .filter(|p| !covered.iter().any(|c| p.starts_with(c.as_str())))
        .collect()
}

/// bot.yaml's servers, then the granted ones, every name from one set (Tico's own `tico` held back). A granted server
/// that is one of bot.yaml's connections (`connection`: address and variable) is passed once, as bot.yaml's; any other
/// takes the next free name (`linear_2`), so none is dropped.
pub fn merge(mut servers: Vec<Value>, granted: &[Value]) -> Vec<Value> {
    let mut used = reserved();
    used.extend(servers.iter().map(|s| text_of(&s["name"])));
    for s in granted {
        let own = connection(s);
        if servers.iter().any(|d| connection(d) == own) {
            continue;
        }
        let mut s = s.clone();
        let base = match text_of(&s["service"]) {
            service if !service.is_empty() => service,
            _ => text_of(&s["name"]),
        };
        s["name"] = json!(unique_name(&base, &mut used));
        servers.push(s);
    }
    servers
}

/// (name, transports, why) for a harness (mcp_servers.SUPPORT): its manifest's `mcp_transports` and
/// `mcp_unsupported`, named as its variant or manifest names it.
fn support(runtime: &str, harness: &str) -> (String, Vec<String>, String) {
    let caps = crate::harness_tools::caps(runtime, harness);
    let name = match crate::harness_tools::variant(runtime, harness) {
        Some(v) => v.name,
        None if runtime.is_empty() => "This harness".into(),
        None => crate::harness_tools::name_for(runtime),
    };
    let why = if !caps.mcp_unsupported.is_empty() {
        caps.mcp_unsupported
    } else if caps.mcp_transports.is_empty() {
        "it has no MCP support in Tico".into()
    } else {
        String::new()
    };
    (name, caps.mcp_transports, why)
}

/// The servers this harness cannot take, as [(server, why)].
pub fn unsupported(servers: &[Value], runtime: &str, harness: &str) -> Vec<(Value, String)> {
    let (name, allowed, why) = support(runtime, harness);
    servers
        .iter()
        .filter(|s| !allowed.contains(&text_of(&s["transport"])))
        .map(|s| {
            let reason = if why.is_empty() {
                format!(
                    "it takes {} servers, not {}",
                    allowed.join(" and "),
                    text_of(&s["transport"])
                )
            } else {
                why.clone()
            };
            (s.clone(), format!("{name} cannot use it: {reason}"))
        })
        .collect()
}

pub fn supported(servers: &[Value], runtime: &str, harness: &str) -> Vec<Value> {
    let (_, allowed, _) = support(runtime, harness);
    servers
        .iter()
        .filter(|s| allowed.contains(&text_of(&s["transport"])))
        .cloned()
        .collect()
}

/// Readiness warnings for one bot: each MCP server its harness cannot pass. Needs no credential.
pub fn warnings_for(tools: &Value, runtime: &str, harness: &str) -> Vec<String> {
    let servers: Vec<Value> = declared(tools).iter().map(|(e, m)| json!({"service": service_of(e), "transport": m["transport"], "url": m["url"]})).collect();
    unsupported(&servers, runtime, harness)
        .into_iter()
        .map(|(s, why)| {
            format!(
                "{}: its MCP server is not passed to the bot's runs. {why}",
                text_of(&s["service"])
            )
        })
        .collect()
}

fn headers_of(s: &Value) -> Map<String, Value> {
    s["headers"].as_object().cloned().unwrap_or_default()
}

/// `--mcp-config` entries for Claude Code, which expands `${VAR}` from the turn's own environment.
pub fn claude_config(servers: &[Value]) -> Map<String, Value> {
    let mut out = Map::new();
    for s in servers {
        let mut row = json!({"type": s["transport"], "url": s["url"]});
        let headers = headers_of(s);
        if !headers.is_empty() {
            row["headers"] = Value::Object(headers);
        }
        out.insert(text_of(&s["name"]), row);
    }
    out
}

/// `mcpServers` for pi's `mcp.json` (streamable HTTP only). `deferred` exposure keeps each tool out of the request
/// until `tool_search` loads it, as Claude Code and Codex find theirs by search, so a large server costs no prompt
/// tokens before it is used. Headers keep their `${VAR}` placeholders, which pi reads from the turn's environment.
pub fn pi_config(servers: &[Value]) -> Map<String, Value> {
    let mut out = Map::new();
    for s in servers {
        let mut row = json!({"url": s["url"], "exposure": "deferred"});
        let headers = headers_of(s);
        if !headers.is_empty() {
            row["headers"] = Value::Object(headers);
        }
        out.insert(text_of(&s["name"]), row);
    }
    out
}

/// `mcpServers` for Gemini CLI's settings file: `httpUrl` is streamable HTTP, `url` is SSE.
#[allow(dead_code)] // the Gemini host reads it
pub fn gemini_config(servers: &[Value]) -> Map<String, Value> {
    let mut out = Map::new();
    for s in servers {
        let key = if s["transport"] == "http" {
            "httpUrl"
        } else {
            "url"
        };
        let mut row = Map::new();
        row.insert(key.into(), s["url"].clone());
        let headers = headers_of(s);
        if !headers.is_empty() {
            row.insert("headers".into(), Value::Object(headers));
        }
        out.insert(text_of(&s["name"]), Value::Object(row));
    }
    out
}

/// ACP `mcpServers` for Grok's `session/new`: headers resolved, since ACP has no placeholders.
pub fn grok_config(servers: &[Value], env: &Env) -> Vec<Value> {
    servers
        .iter()
        .map(|s| {
            let headers: Vec<Value> = headers_of(s)
                .iter()
                .map(|(k, v)| json!({"name": k, "value": expand(&text_of(v), env)}))
                .collect();
            json!({"type": s["transport"], "name": s["name"], "url": s["url"], "headers": headers})
        })
        .collect()
}

/// `mcp_servers` tables for Codex. `Bearer ${VAR}` on Authorization is `bearer_token_env_var` and `${VAR}`
/// alone is `env_http_headers`, read by Codex from its own process environment (`process_env`) when it holds
/// the same value; any other shape is resolved into `http_headers`.
pub fn codex_config(servers: &[Value], env: &Env, process_env: &Env) -> Map<String, Value> {
    let mut out = Map::new();
    for s in servers {
        let mut table = Map::new();
        table.insert("url".into(), s["url"].clone());
        let (mut statics, mut by_env) = (Map::new(), Map::new());
        for (name, value) in headers_of(s) {
            let value = text_of(&value);
            let found = placeholders(&value);
            let var = if found.len() == 1 {
                found[0].clone()
            } else {
                String::new()
            };
            let held = process_env.get(&var).filter(|v| !v.is_empty());
            if !var.is_empty() && held.is_some() && held == env.get(&var) {
                if name.to_lowercase() == "authorization"
                    && value.trim() == format!("Bearer ${{{var}}}")
                {
                    table.insert("bearer_token_env_var".into(), json!(var));
                    continue;
                }
                if value.trim() == format!("${{{var}}}") {
                    by_env.insert(name, json!(var));
                    continue;
                }
            }
            statics.insert(name, json!(expand(&value, env)));
        }
        if !by_env.is_empty() {
            table.insert("env_http_headers".into(), Value::Object(by_env));
        }
        if !statics.is_empty() {
            table.insert("http_headers".into(), Value::Object(statics));
        }
        out.insert(text_of(&s["name"]), Value::Object(table));
    }
    out
}

/// The bot's servers merged beside Codex's stubbed operator ones: a name the stubs (or Tico's own server, `tico`)
/// already use takes a `bot_` prefix, so a bot's table never merges into a disabled stub or replaces Tico's.
pub fn codex_merge(servers: &mut Map<String, Value>, remote: Map<String, Value>) {
    for (name, table) in remote {
        let (mut key, mut n) = (name.clone(), 1);
        if servers.contains_key(&key) || key == TICO_SERVER {
            key = format!("bot_{name}");
        }
        // Never over another table, a `bot_` one included: the next free `bot_<name>_<n>`.
        while servers.contains_key(&key) {
            n += 1;
            key = format!("bot_{name}_{n}");
        }
        servers.insert(key, table);
    }
}

/// reachable | auth_failed | unreachable, from one cheap request: an MCP `initialize` POST, or the GET that
/// opens an SSE stream. Redirects are not followed, so a credential never leaves for another host.
pub async fn reachability(
    url: &str,
    transport: &str,
    headers: &[(String, String)],
) -> &'static str {
    let Ok(http) = crate::tls::http_builder()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(REACH_TIMEOUT_S))
        .user_agent("tico-runner")
        .build()
    else {
        return "unreachable";
    };
    let mut req = if transport == "sse" {
        http.get(url).header("Accept", "text/event-stream")
    } else {
        let body = r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"tico-runner","version":"1"}}}"#;
        http.post(url)
            .header("Content-Type", "application/json")
            .header("Accept", "application/json, text/event-stream")
            .body(body)
    };
    for (k, v) in headers {
        if !v.contains('\n') && !v.contains('\r') {
            req = req.header(k.as_str(), v.as_str());
        }
    }
    let status = match req.send().await {
        Ok(r) => r.status().as_u16(),
        Err(_) => return "unreachable",
    };
    match status {
        401 | 403 => "auth_failed",
        s if s >= 500 || s == 404 || s == 410 => "unreachable",
        _ => "reachable",
    }
}

/// Probe answers, kept REACH_TTL_S and asked for off the heartbeat's path (service.py mcp_reach).
#[derive(Default)]
pub struct Reach {
    seen: Arc<Mutex<HashMap<String, (Instant, String)>>>,
    probing: Arc<Mutex<HashSet<String>>>,
}

impl Reach {
    /// reachable | auth_failed | unreachable | unchecked for one declared server. Before a run a vault grant has
    /// not arrived, so the header's variable is never in hand here: a server that needs one is unchecked
    /// (the missing credential is already the tool's problem), except a hub-vault one, asked without it and
    /// counted reachable when it refuses.
    pub fn status(&self, bot: &str, row: &Value, entry: &Value) -> String {
        let mcp = &row["mcp"];
        let needs: Vec<String> = mcp["headers"]
            .as_object()
            .into_iter()
            .flatten()
            .flat_map(|(_, v)| placeholders(&text_of(v)))
            .collect();
        if !needs.is_empty() && entry.get("vault").and_then(|v| v.as_str()) != Some("hub") {
            return "unchecked".into();
        }
        let (url, transport) = (text_of(&mcp["url"]), text_of(&mcp["transport"]));
        let key = format!(
            "{bot}\u{1f}{}\u{1f}{url}\u{1f}{transport}",
            text_of(&row["service"])
        );
        let seen = self.seen.lock().unwrap().get(&key).cloned();
        let stale = seen
            .as_ref()
            .is_none_or(|(at, _)| at.elapsed() >= Duration::from_secs(REACH_TTL_S));
        if stale && self.probing.lock().unwrap().insert(key.clone()) {
            let (cache, probing, needed) =
                (self.seen.clone(), self.probing.clone(), !needs.is_empty());
            tokio::spawn(async move {
                let status = reachability(&url, &transport, &[]).await;
                let status = if status == "auth_failed" && needed {
                    "reachable"
                } else {
                    status
                };
                cache
                    .lock()
                    .unwrap()
                    .insert(key.clone(), (Instant::now(), status.to_string()));
                probing.lock().unwrap().remove(&key);
            });
        }
        seen.map(|(_, s)| s).unwrap_or_else(|| "unchecked".into())
    }
}

/// Why an entry's `mcp:` block cannot be used, or "".
#[allow(dead_code)]
pub fn problem_of(entry: &Value) -> String {
    if !entry.is_object() || !has_mcp(entry) {
        return String::new();
    }
    match clean_mcp(&entry["mcp"], &entry_env(entry)) {
        Ok(_) => String::new(),
        Err(e) => format!("The MCP block is not valid: {e}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env(pairs: &[(&str, &str)]) -> Env {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    fn tools() -> Value {
        json!([
            {"service": "linear", "env": "LINEAR_API_KEY", "can": ["read"],
             "mcp": {"url": "https://mcp.linear.app/mcp", "headers": {"Authorization": "Bearer ${LINEAR_API_KEY}"}}},
            {"service": "docs", "mcp": {"url": "https://docs.example.com/sse", "transport": "sse"}},
            {"service": "broken", "mcp": {"url": "http://example.com/mcp"}},
            {"service": "plain", "can": ["read"]}
        ])
    }

    #[test]
    fn a_block_is_validated_like_access_entry_clean_mcp() {
        let ok = clean_mcp(
            &json!({"url": " https://mcp.example.com/mcp ", "headers": {"X-Key": "${K}"}}),
            "K",
        )
        .unwrap();
        assert_eq!(
            ok,
            json!({"url": "https://mcp.example.com/mcp", "transport": "http", "headers": {"X-Key": "${K}"}})
        );
        assert!(clean_mcp(&json!({"url": "http://localhost:8080/mcp"}), "").is_ok());
        assert!(clean_mcp(&json!({"url": "http://127.0.0.1/mcp"}), "").is_ok());
        let err = |m: Value, e: &str| clean_mcp(&m, e).unwrap_err();
        assert!(err(json!({"url": "http://example.com"}), "").contains("must be https"));
        assert!(err(json!({"url": "https://u:p@example.com"}), "").contains("plain address"));
        assert!(err(
            json!({"url": "https://x.com", "headers": {"A": "${OTHER}"}}),
            "K"
        )
        .contains("only the entry's own env variable (${K})"));
        assert!(err(
            json!({"url": "https://x.com", "headers": {"A": "Bearer sk-abcdefghijklmnop"}}),
            ""
        )
        .contains("looks like a credential"));
        assert!(err(
            json!({"url": "https://x.com", "headers": {"Host": "x"}}),
            ""
        )
        .contains("'Host' is not a header name"));
        assert!(err(
            json!({"url": "https://x.com", "headers": {"A": "$HOME"}}),
            ""
        )
        .contains("may only use"));
        assert!(
            err(json!({"url": "https://x.com", "transport": "stdio"}), "")
                .contains("one of http, sse")
        );
        assert!(err(json!({"url": "https://x.com", "command": "x"}), "")
            .contains("does not take command"));
        assert!(err(json!({"url": "https://x.com/?k=${K}"}), "K").contains("holds no credential"));
        assert_eq!(secret_in("a normal sentence"), "");
        assert!(!secret_in("token Zq8vR2mXk4Lp9Wn3Ts7Yb1Cd6Fg").is_empty());
        // A Twilio SID is an id, however random its hex looks.
        assert_eq!(
            secret_in(&format!("AC{}", "0123456789abcdef".repeat(2))),
            ""
        );
    }

    #[test]
    fn a_server_whose_key_tico_holds_goes_through_the_proxy_or_is_named() {
        let tool = |base: &str| ProxiedTool {
            name: "Linear".into(),
            env: "LINEAR_API_KEY".into(),
            url_env: "TICO_TOOL_LINEAR_URL".into(),
            url: "http://tico.internal/api/v2/tool-proxy/linear".into(),
            base: base.into(),
        };
        let held = env(&[("LINEAR_API_KEY", "tpx1.a.b")]);
        let (servers, problems) =
            servers_for_run(&tools(), &held, &[tool("https://mcp.linear.app")]);
        assert!(problems.is_empty(), "{problems:?}");
        assert_eq!(
            text_of(&servers[0]["url"]),
            "http://tico.internal/api/v2/tool-proxy/linear/mcp"
        );
        assert_eq!(
            through_proxy("https://mcp.linear.app/", &tool("https://mcp.linear.app/")).as_deref(),
            Some("http://tico.internal/api/v2/tool-proxy/linear/")
        );
        // Another host, a look-alike prefix, or no address: not passed, and the line says what to set.
        assert_eq!(
            through_proxy(
                "https://mcp.linear.app.evil/mcp",
                &tool("https://mcp.linear.app")
            ),
            None
        );
        assert_eq!(through_proxy("https://mcp.linear.app/mcp", &tool("")), None);
        let (servers, problems) =
            servers_for_run(&tools(), &held, &[tool("https://api.linear.app")]);
        assert_eq!(servers.len(), 1, "only docs");
        assert!(
            problems[0].starts_with("linear: MCP server not passed. Tico holds its key"),
            "{problems:?}"
        );
        assert!(problems[0].ends_with("in Tools > Linear"));
    }

    #[test]
    fn a_granted_server_reaches_the_run_on_this_runners_address_and_bot_yaml_wins_a_name() {
        let items = json!([
            {"name": "acme-errors", "tool": "Acme Errors", "transport": "http", "env": "ACME_ERRORS_MCP_TOKEN",
             "url": "https://tico.acme.example/api/v2/tool-proxy/acme-errors/mcp", "proxied": true,
             "headers": {"Authorization": "Bearer ${ACME_ERRORS_MCP_TOKEN}"}},
            {"name": "acme-docs", "tool": "Acme Docs", "transport": "http", "env": "ACME_DOCS_MCP_TOKEN",
             "url": "https://docs.acme.example/mcp", "proxied": false, "headers": {"X-Key": "${ACME_DOCS_MCP_TOKEN}"}},
            {"name": "acme-sneaky", "tool": "Acme Sneaky", "transport": "http", "env": "ACME_SNEAKY_MCP_TOKEN",
             "url": "https://sneaky.acme.example/mcp", "headers": {"X-Key": "${HOME}"}},
            {"name": "acme-missing", "tool": "Acme Missing", "transport": "http", "env": "ACME_MISSING_MCP_TOKEN",
             "url": "https://missing.acme.example/mcp", "headers": {}}
        ]);
        let held: BTreeSet<String> = [
            "ACME_ERRORS_MCP_TOKEN",
            "ACME_DOCS_MCP_TOKEN",
            "ACME_SNEAKY_MCP_TOKEN",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        let (servers, problems) = granted(&items, "http://server:8080/", &held);
        assert_eq!(
            servers
                .iter()
                .map(|s| text_of(&s["url"]))
                .collect::<Vec<_>>(),
            [
                "http://server:8080/api/v2/tool-proxy/acme-errors/mcp",
                "https://docs.acme.example/mcp"
            ]
        );
        assert_eq!(text_of(&servers[0]["name"]), "acme_errors");
        assert_eq!(problems.len(), 2, "{problems:?}");
        assert!(problems[0].starts_with("Acme Sneaky: MCP server not passed."));
        assert_eq!(
            problems[1],
            "Acme Missing: MCP server not passed, its key did not reach this turn"
        );
        // bot.yaml's entry for the same connection (its address and variable): bot.yaml's is passed
        let declared = vec![
            json!({"name": "acme_errors", "env": "ACME_ERRORS_MCP_TOKEN",
                                   "url": "http://server:8080/api/v2/tool-proxy/acme-errors/mcp"}),
        ];
        let merged = merge(declared, &servers);
        assert_eq!(merged.len(), 2);
        assert_eq!(text_of(&merged[1]["name"]), "acme_docs");
    }

    #[test]
    fn merged_servers_share_one_set_of_names_and_codex_never_writes_over_a_table() {
        let yaml = |env: &str, url: &str| json!({"name": "linear", "service": "linear", "url": url, "env": env});
        let declared = vec![yaml("LINEAR_API_KEY", "https://mcp.linear.example/mcp")];
        let granted = [
            // bot.yaml's own connection (its address and variable), granted too: passed once, as bot.yaml's
            json!({"name": "linear", "service": "linear", "url": "https://mcp.linear.example/mcp", "env": "LINEAR_API_KEY"}),
            // another account on the same endpoint: its own name, never dropped
            json!({"name": "linear", "service": "linear", "url": "https://mcp.linear.example/mcp", "env": "LINEAR_OPS"}),
        ];
        let names: Vec<String> = merge(declared, &granted)
            .iter()
            .map(|s| text_of(&s["name"]))
            .collect();
        assert_eq!(names, ["linear", "linear_2"]);
        let mut tables = Map::new();
        for name in ["tico", "bot_linear", "linear"] {
            tables.insert(name.to_string(), json!({"stub": name}));
        }
        let mut remote = Map::new();
        remote.insert(
            "linear".to_string(),
            json!({"url": "https://mcp.linear.example/mcp"}),
        );
        remote.insert(
            "tico".to_string(),
            json!({"url": "https://tico-tool.example/mcp"}),
        );
        codex_merge(&mut tables, remote);
        assert_eq!(tables["bot_linear"], json!({"stub": "bot_linear"}));
        assert_eq!(
            tables["bot_linear_2"],
            json!({"url": "https://mcp.linear.example/mcp"})
        );
        assert_eq!(tables["tico"], json!({"stub": "tico"}));
        assert_eq!(
            tables["bot_tico"],
            json!({"url": "https://tico-tool.example/mcp"})
        );
    }

    #[test]
    fn a_proxied_grant_of_bot_yamls_own_server_is_that_server() {
        let declared = json!([{"service": "linear", "env": "LINEAR_API_KEY",
                               "mcp": {"url": "https://mcp.linear.example/mcp", "headers": {"Authorization": "Bearer ${LINEAR_API_KEY}"}}}]);
        let grant = json!([{"name": "linear", "tool": "Linear", "transport": "http", "env": "LINEAR_API_KEY", "proxied": true,
                            "url": "https://tico.acme.example/api/v2/tool-proxy/linear/mcp", "upstream": "https://mcp.linear.example/mcp",
                            "headers": {"Authorization": "Bearer ${LINEAR_API_KEY}"}}]);
        let held: BTreeSet<String> = ["LINEAR_API_KEY".to_string()].into_iter().collect();
        let (granted, _) = granted(&grant, "", &held);
        // bot.yaml's key did not reach the run: no warning, as the grant brings that very server
        let (_, problems) = servers_for_run(&declared, &env(&[]), &[]);
        assert!(drop_covered(problems, &declared, &granted).is_empty());
        // both there: passed once
        let (servers, _) =
            servers_for_run(&declared, &env(&[("LINEAR_API_KEY", "lin_made_up")]), &[]);
        assert_eq!(merge(servers, &granted).len(), 1);
    }

    #[test]
    fn granted_servers_never_share_a_name_nor_take_ticos_own() {
        let item = |name: &str, env: &str| {
            json!({"name": name, "tool": name, "transport": "http", "env": env,
                   "url": format!("https://{}.acme.example/mcp", name.replace(['_', '.'], "-")),
                   "headers": {"Authorization": format!("Bearer ${{{env}}}")}})
        };
        let items = json!([
            item("tico", "TICO_MCP_TOKEN"),
            item("acme-docs", "ACME_DOCS_TOKEN"),
            item("acme.docs", "ACME_DOCS_TWO_TOKEN"),
            // the same key again: one connection, passed once
            item("acme-docs", "ACME_DOCS_TOKEN"),
            // the same tool's second key, in its own variable: a second connection, never dropped
            item("acme-docs", "ACME_DOCS_OPS_TOKEN")
        ]);
        let held: BTreeSet<String> = [
            "TICO_MCP_TOKEN",
            "ACME_DOCS_TOKEN",
            "ACME_DOCS_TWO_TOKEN",
            "ACME_DOCS_OPS_TOKEN",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        let (servers, problems) = granted(&items, "", &held);
        assert!(problems.is_empty(), "{problems:?}");
        assert_eq!(
            servers
                .iter()
                .map(|s| text_of(&s["name"]))
                .collect::<Vec<_>>(),
            ["tico_2", "acme_docs", "acme_docs_2", "acme_docs_3"]
        );
        let declared = json!([{"service": "tico", "mcp": {"url": "https://tico.example/mcp"}}]);
        let (servers, _) = servers_for_run(&declared, &env(&[]), &[]);
        assert_eq!(text_of(&servers[0]["name"]), "tico_2");
    }

    #[test]
    fn servers_for_a_run_need_their_granted_variable_and_each_harness_takes_its_transports() {
        let (servers, problems) = servers_for_run(&tools(), &env(&[]), &[]);
        assert_eq!(servers.len(), 1, "linear needs LINEAR_API_KEY");
        assert_eq!(
            problems,
            ["linear: MCP server not passed, LINEAR_API_KEY is not granted to this bot"]
        );
        let (servers, problems) = servers_for_run(&tools(), &env(&[("LINEAR_API_KEY", "v")]), &[]);
        assert!(problems.is_empty());
        assert_eq!(
            servers
                .iter()
                .map(|s| text_of(&s["name"]))
                .collect::<Vec<_>>(),
            ["linear", "docs"]
        );
        assert_eq!(supported(&servers, "claude", "").len(), 2);
        let codex = unsupported(&servers, "codex", "");
        assert_eq!(codex.len(), 1);
        assert_eq!(
            codex[0].1,
            "Codex cannot use it: it takes http servers, not sse"
        );
        assert_eq!(supported(&servers, "gemini", "antigravity").len(), 0);
        // Pi takes streamable HTTP only: the SSE server is named, the HTTP one passes.
        assert_eq!(supported(&servers, "pi", "").len(), 1);
        assert!(unsupported(&servers, "pi", "")[0]
            .1
            .ends_with("cannot use it: it takes http servers, not sse"));
        let warnings = warnings_for(&tools(), "codex", "");
        assert_eq!(warnings, ["docs: its MCP server is not passed to the bot's runs. Codex cannot use it: it takes http servers, not sse"]);
        let claude = claude_config(&servers);
        assert_eq!(
            claude["linear"],
            json!({"type": "http", "url": "https://mcp.linear.app/mcp", "headers": {"Authorization": "Bearer ${LINEAR_API_KEY}"}})
        );
        assert_eq!(
            claude["docs"],
            json!({"type": "sse", "url": "https://docs.example.com/sse"})
        );
        let gemini = gemini_config(&servers);
        assert_eq!(
            (
                gemini["linear"]["httpUrl"].as_str(),
                gemini["docs"]["url"].as_str()
            ),
            (
                Some("https://mcp.linear.app/mcp"),
                Some("https://docs.example.com/sse")
            )
        );
    }

    #[test]
    fn codex_reads_env_headers_itself_and_resolves_the_rest() {
        let server = |headers: Value| json!({"name": "svc", "url": "https://x.com/mcp", "transport": "http", "headers": headers});
        let granted = env(&[("K", "secret-value")]);
        let held = codex_config(
            &[server(
                json!({"Authorization": "Bearer ${K}", "X-Key": "${K}", "X-Basic": "Basic ${K}"}),
            )],
            &granted,
            &granted,
        );
        assert_eq!(
            held["svc"],
            json!({"url": "https://x.com/mcp", "bearer_token_env_var": "K", "env_http_headers": {"X-Key": "K"}, "http_headers": {"X-Basic": "Basic secret-value"}})
        );
        let absent = codex_config(
            &[server(json!({"Authorization": "Bearer ${K}"}))],
            &granted,
            &env(&[]),
        );
        assert_eq!(
            absent["svc"]["http_headers"],
            json!({"Authorization": "Bearer secret-value"})
        );
        let mut stubs = Map::new();
        stubs.insert("svc".into(), json!({"enabled": false}));
        let mut remote = Map::new();
        remote.insert("svc".into(), json!({"url": "a"}));
        remote.insert("tico".into(), json!({"url": "b"}));
        remote.insert("new".into(), json!({"url": "c"}));
        codex_merge(&mut stubs, remote);
        assert_eq!(
            stubs.keys().cloned().collect::<Vec<_>>(),
            ["svc", "bot_svc", "bot_tico", "new"]
        );
    }

    #[tokio::test]
    async fn reachability_reads_the_status_and_follows_no_redirect() {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        tokio::spawn(async move {
            for status in [401u16, 302, 503, 200] {
                let (mut sock, _) = listener.accept().await.unwrap();
                let mut buf = vec![0u8; 4096];
                let _ = sock.read(&mut buf).await;
                let reply = format!("HTTP/1.1 {status} X\r\nLocation: http://127.0.0.1:9/\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
                let _ = sock.write_all(reply.as_bytes()).await;
            }
        });
        let url = format!("http://127.0.0.1:{port}/mcp");
        assert_eq!(reachability(&url, "http", &[]).await, "auth_failed");
        assert_eq!(reachability(&url, "http", &[]).await, "reachable");
        assert_eq!(reachability(&url, "sse", &[]).await, "unreachable");
        assert_eq!(reachability(&url, "http", &[]).await, "reachable");
        assert_eq!(
            reachability("http://127.0.0.1:9/mcp", "http", &[]).await,
            "unreachable"
        );
    }
}
