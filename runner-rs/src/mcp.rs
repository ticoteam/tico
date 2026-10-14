//! A bot's remote MCP servers: the `mcp:` blocks of its `tools:` entries (clients/mcp_servers.py and
//! `clean_mcp`/`secret_in` in clients/access_entry.py), for the hosts, readiness and the reachability check.
//!
//! A header may use only its entry's own `env` variable as a `${VAR}` placeholder, filled only from what this
//! bot was granted for the run; a server whose variable is not granted is left out and named, never sent half
//! configured. Where the harness reads `${VAR}` itself (Claude Code, Gemini CLI, Codex's env headers) the
//! placeholder goes through untouched, so no value lands on a command line or in a file.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use regex::Regex;
use serde_json::{json, Map, Value};

use crate::env::Env;
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
    -counts.values().map(|&c| c as f64 / n * (c as f64 / n).log2()).sum::<f64>()
}

/// A reason when `text` looks like a credential value, else "" (access_entry.secret_in).
pub fn secret_in(text: &str) -> String {
    if secret_shapes().is_match(text) {
        return "it contains what looks like a key, token or password".into();
    }
    for token in text.split(|c: char| c.is_whitespace() || ",;()\"'<>".contains(c)) {
        let long = token.chars().count() >= 24;
        if long
            && token.chars().all(|c| c.is_ascii_alphanumeric() || "_+/=-".contains(c))
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
    placeholder_re().captures_iter(text).map(|c| c[1].to_string()).collect()
}

/// `text` with each `${VAR}` replaced by env[VAR], for a harness that cannot read the placeholder itself.
pub fn expand(text: &str, env: &Env) -> String {
    placeholder_re().replace_all(text, |c: &regex::Captures| env.get(&c[1]).cloned().unwrap_or_default()).into_owned()
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
        return Err("mcp is an object such as {url: \"https://mcp.example.com/mcp\", transport: http}".into());
    };
    let mut unknown: Vec<&String> = m.keys().filter(|k| !["url", "transport", "headers"].contains(&k.as_str())).collect();
    unknown.sort();
    if !unknown.is_empty() {
        return Err(format!("mcp does not take {}; it takes url, transport, headers", unknown.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ")));
    }
    let url = crate::manifest::text_or_empty(m.get("url")).trim().to_string();
    if url.is_empty() || url.chars().count() > 500 || url.chars().any(char::is_whitespace) {
        return Err("mcp.url is the server's address, such as https://mcp.example.com/mcp".into());
    }
    let parsed = url::Url::parse(&url).map_err(|_| "mcp.url is not a valid address".to_string())?;
    match parsed.scheme() {
        "http" if !loopback_host(&parsed) => return Err("mcp.url must be https; plain http is only for a server on this computer (localhost)".into()),
        "http" | "https" => {}
        _ => return Err("mcp.url must be https".into()),
    }
    if parsed.host().is_none() || !parsed.username().is_empty() || parsed.password().is_some() || parsed.fragment().is_some_and(|f| !f.is_empty()) {
        return Err("mcp.url is a plain address: a host, no password and no #fragment".into());
    }
    if url.contains("${") || !secret_in(&url).is_empty() {
        return Err("mcp.url holds no credential (put it in a header as ${VAR}, never in the address)".into());
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
        Some(_) => return Err(format!("mcp.headers is an object of at most {MAX_HEADERS} headers")),
    };
    static NAME: OnceLock<Regex> = OnceLock::new();
    let name_re = re(&NAME, r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$");
    let mut kept = Map::new();
    for (name, value) in headers {
        let name = name.trim().to_string();
        if !name_re.is_match(&name) || NOT_SETTABLE.contains(&name.to_lowercase().as_str()) {
            return Err(format!("'{name}' is not a header name Tico will send"));
        }
        let Some(value) = value.as_str().filter(|v| !v.trim().is_empty() && v.chars().count() <= 500 && !v.contains('\n') && !v.contains('\r')) else {
            return Err(format!("mcp.headers.{name} is one line of text, such as \"Bearer ${{JIRA_API_TOKEN}}\""));
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
                let own = if env.is_empty() { "(set `env`) ".to_string() } else { format!("(${{{env}}}) ") };
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
    crate::manifest::text_or_empty(entry.get("env")).trim().to_string()
}

fn service_of(entry: &Value) -> String {
    crate::manifest::text_or_empty(entry.get("service")).trim().to_lowercase()
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

/// The name a harness shows the model: the service, safe for a tool name (`google-drive` -> `google_drive`).
pub fn server_name(service: &str) -> String {
    let name: String = service.to_lowercase().chars().map(|c| if c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' { c } else { '_' }).collect();
    if name.is_empty() { "mcp".into() } else { name }
}

/// (servers, problems) for one run. A server is {name, service, url, transport, headers, env}; one whose
/// variable is not in `env` is left out and named in `problems`.
pub fn servers_for_run(tools: &Value, env: &Env) -> (Vec<Value>, Vec<String>) {
    let (mut servers, mut problems, mut used) = (vec![], vec![], HashSet::new());
    for (entry, mcp) in declared(tools) {
        let service = service_of(&entry);
        let mut needs: Vec<String> = mcp["headers"].as_object().into_iter().flatten().flat_map(|(_, v)| placeholders(&text_of(v))).collect();
        needs.sort();
        needs.dedup();
        if needs.iter().any(|v| env.get(v).is_none_or(|x| x.trim().is_empty())) {
            problems.push(format!("{service}: MCP server not passed, {} is not granted to this bot", needs.join(", ")));
            continue;
        }
        let (mut name, mut n) = (server_name(&service), 1);
        while used.contains(&name) {
            n += 1;
            name = format!("{}_{n}", server_name(&service));
        }
        used.insert(name.clone());
        servers.push(json!({"name": name, "service": service, "url": mcp["url"], "transport": mcp["transport"],
                            "headers": mcp.get("headers").cloned().unwrap_or(json!({})), "env": entry_env(&entry)}));
    }
    (servers, problems)
}

/// The SUPPORT key for a bot's runtime (and, for Gemini's second harness, its harness).
pub fn harness_key(runtime: &str, harness: &str) -> String {
    if harness == "antigravity" { "antigravity".into() } else { runtime.into() }
}

/// (name, transports, why) for a harness (mcp_servers.SUPPORT).
fn support(key: &str, runtime: &str) -> (String, &'static [&'static str], String) {
    match key {
        "claude" => ("Claude Code".into(), &["http", "sse"], String::new()),
        "codex" => ("Codex".into(), &["http"], String::new()),
        "gemini" => ("Gemini CLI".into(), &["http", "sse"], String::new()),
        "grok" => ("Grok Build".into(), &["http", "sse"], String::new()),
        "antigravity" => ("Antigravity".into(), &[], "it reads MCP servers only from a config file in the operator's home, which Tico does not touch (the operator's own servers would reach every bot)".into()),
        "cursor" => ("Cursor".into(), &[], "cursor-agent reads MCP servers only from ~/.cursor/mcp.json or the bot's .cursor/mcp.json, with no per-run setting, so a bot would also get the operator's own servers".into()),
        "pi" => ("pi".into(), &[], "pi has no MCP support".into()),
        _ => (if runtime.is_empty() { "This harness".into() } else { runtime.into() }, &[], "it has no MCP support in Tico".into()),
    }
}

/// The servers this harness cannot take, as [(server, why)].
pub fn unsupported(servers: &[Value], runtime: &str, harness: &str) -> Vec<(Value, String)> {
    let (name, allowed, why) = support(&harness_key(runtime, harness), runtime);
    servers
        .iter()
        .filter(|s| !allowed.contains(&text_of(&s["transport"]).as_str()))
        .map(|s| {
            let reason = if why.is_empty() { format!("it takes {} servers, not {}", allowed.join(" and "), text_of(&s["transport"])) } else { why.clone() };
            (s.clone(), format!("{name} cannot use it: {reason}"))
        })
        .collect()
}

pub fn supported(servers: &[Value], runtime: &str, harness: &str) -> Vec<Value> {
    let (_, allowed, _) = support(&harness_key(runtime, harness), runtime);
    servers.iter().filter(|s| allowed.contains(&text_of(&s["transport"]).as_str())).cloned().collect()
}

/// Readiness warnings for one bot: each MCP server its harness cannot pass. Needs no credential.
pub fn warnings_for(tools: &Value, runtime: &str, harness: &str) -> Vec<String> {
    let servers: Vec<Value> = declared(tools).iter().map(|(e, m)| json!({"service": service_of(e), "transport": m["transport"], "url": m["url"]})).collect();
    unsupported(&servers, runtime, harness)
        .into_iter()
        .map(|(s, why)| format!("{}: its MCP server is not passed to the bot's runs. {why}", text_of(&s["service"])))
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

/// `mcpServers` for Gemini CLI's settings file: `httpUrl` is streamable HTTP, `url` is SSE.
#[allow(dead_code)] // the Gemini host reads it
pub fn gemini_config(servers: &[Value]) -> Map<String, Value> {
    let mut out = Map::new();
    for s in servers {
        let key = if s["transport"] == "http" { "httpUrl" } else { "url" };
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
#[allow(dead_code)] // the Grok host reads it
pub fn grok_config(servers: &[Value], env: &Env) -> Vec<Value> {
    servers
        .iter()
        .map(|s| {
            let headers: Vec<Value> = headers_of(s).iter().map(|(k, v)| json!({"name": k, "value": expand(&text_of(v), env)})).collect();
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
            let var = if found.len() == 1 { found[0].clone() } else { String::new() };
            let held = process_env.get(&var).filter(|v| !v.is_empty());
            if !var.is_empty() && held.is_some() && held == env.get(&var) {
                if name.to_lowercase() == "authorization" && value.trim() == format!("Bearer ${{{var}}}") {
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

/// The bot's servers merged beside Codex's stubbed operator ones: a name the stubs (or the hub) already use
/// takes a `tico_` prefix, so a bot's table never merges into a disabled stub.
pub fn codex_merge(servers: &mut Map<String, Value>, remote: Map<String, Value>) {
    for (name, table) in remote {
        let key = if servers.contains_key(&name) || name == "hub" { format!("tico_{name}") } else { name };
        servers.insert(key, table);
    }
}

/// reachable | auth_failed | unreachable, from one cheap request: an MCP `initialize` POST, or the GET that
/// opens an SSE stream. Redirects are not followed, so a credential never leaves for another host.
pub async fn reachability(url: &str, transport: &str, headers: &[(String, String)]) -> &'static str {
    let Ok(http) = reqwest::Client::builder()
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
        http.post(url).header("Content-Type", "application/json").header("Accept", "application/json, text/event-stream").body(body)
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
        let needs: Vec<String> = mcp["headers"].as_object().into_iter().flatten().flat_map(|(_, v)| placeholders(&text_of(v))).collect();
        if !needs.is_empty() && entry.get("vault").and_then(|v| v.as_str()) != Some("hub") {
            return "unchecked".into();
        }
        let (url, transport) = (text_of(&mcp["url"]), text_of(&mcp["transport"]));
        let key = format!("{bot}\u{1f}{}\u{1f}{url}\u{1f}{transport}", text_of(&row["service"]));
        let seen = self.seen.lock().unwrap().get(&key).cloned();
        let stale = seen.as_ref().is_none_or(|(at, _)| at.elapsed() >= Duration::from_secs(REACH_TTL_S));
        if stale && self.probing.lock().unwrap().insert(key.clone()) {
            let (cache, probing, needed) = (self.seen.clone(), self.probing.clone(), !needs.is_empty());
            tokio::spawn(async move {
                let status = reachability(&url, &transport, &[]).await;
                let status = if status == "auth_failed" && needed { "reachable" } else { status };
                cache.lock().unwrap().insert(key.clone(), (Instant::now(), status.to_string()));
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
        pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect()
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
        let ok = clean_mcp(&json!({"url": " https://mcp.example.com/mcp ", "headers": {"X-Key": "${K}"}}), "K").unwrap();
        assert_eq!(ok, json!({"url": "https://mcp.example.com/mcp", "transport": "http", "headers": {"X-Key": "${K}"}}));
        assert!(clean_mcp(&json!({"url": "http://localhost:8080/mcp"}), "").is_ok());
        assert!(clean_mcp(&json!({"url": "http://127.0.0.1/mcp"}), "").is_ok());
        let err = |m: Value, e: &str| clean_mcp(&m, e).unwrap_err();
        assert!(err(json!({"url": "http://example.com"}), "").contains("must be https"));
        assert!(err(json!({"url": "https://u:p@example.com"}), "").contains("plain address"));
        assert!(err(json!({"url": "https://x.com", "headers": {"A": "${OTHER}"}}), "K").contains("only the entry's own env variable (${K})"));
        assert!(err(json!({"url": "https://x.com", "headers": {"A": "Bearer sk-abcdefghijklmnop"}}), "").contains("looks like a credential"));
        assert!(err(json!({"url": "https://x.com", "headers": {"Host": "x"}}), "").contains("'Host' is not a header name"));
        assert!(err(json!({"url": "https://x.com", "headers": {"A": "$HOME"}}), "").contains("may only use"));
        assert!(err(json!({"url": "https://x.com", "transport": "stdio"}), "").contains("one of http, sse"));
        assert!(err(json!({"url": "https://x.com", "command": "x"}), "").contains("does not take command"));
        assert!(err(json!({"url": "https://x.com/?k=${K}"}), "K").contains("holds no credential"));
        assert_eq!(secret_in("a normal sentence"), "");
        assert!(!secret_in("token Zq8vR2mXk4Lp9Wn3Ts7Yb1Cd6Fg").is_empty());
    }

    #[test]
    fn servers_for_a_run_need_their_granted_variable_and_each_harness_takes_its_transports() {
        let (servers, problems) = servers_for_run(&tools(), &env(&[]));
        assert_eq!(servers.len(), 1, "linear needs LINEAR_API_KEY");
        assert_eq!(problems, ["linear: MCP server not passed, LINEAR_API_KEY is not granted to this bot"]);
        let (servers, problems) = servers_for_run(&tools(), &env(&[("LINEAR_API_KEY", "v")]));
        assert!(problems.is_empty());
        assert_eq!(servers.iter().map(|s| text_of(&s["name"])).collect::<Vec<_>>(), ["linear", "docs"]);
        assert_eq!(supported(&servers, "claude", "").len(), 2);
        let codex = unsupported(&servers, "codex", "");
        assert_eq!(codex.len(), 1);
        assert_eq!(codex[0].1, "Codex cannot use it: it takes http servers, not sse");
        assert_eq!(supported(&servers, "gemini", "antigravity").len(), 0);
        assert!(unsupported(&servers, "pi", "")[0].1.contains("pi has no MCP support"));
        let warnings = warnings_for(&tools(), "codex", "");
        assert_eq!(warnings, ["docs: its MCP server is not passed to the bot's runs. Codex cannot use it: it takes http servers, not sse"]);
        let claude = claude_config(&servers);
        assert_eq!(claude["linear"], json!({"type": "http", "url": "https://mcp.linear.app/mcp", "headers": {"Authorization": "Bearer ${LINEAR_API_KEY}"}}));
        assert_eq!(claude["docs"], json!({"type": "sse", "url": "https://docs.example.com/sse"}));
        let gemini = gemini_config(&servers);
        assert_eq!((gemini["linear"]["httpUrl"].as_str(), gemini["docs"]["url"].as_str()), (Some("https://mcp.linear.app/mcp"), Some("https://docs.example.com/sse")));
    }

    #[test]
    fn codex_reads_env_headers_itself_and_resolves_the_rest() {
        let server = |headers: Value| json!({"name": "svc", "url": "https://x.com/mcp", "transport": "http", "headers": headers});
        let granted = env(&[("K", "secret-value")]);
        let held = codex_config(&[server(json!({"Authorization": "Bearer ${K}", "X-Key": "${K}", "X-Basic": "Basic ${K}"}))], &granted, &granted);
        assert_eq!(held["svc"], json!({"url": "https://x.com/mcp", "bearer_token_env_var": "K", "env_http_headers": {"X-Key": "K"}, "http_headers": {"X-Basic": "Basic secret-value"}}));
        let absent = codex_config(&[server(json!({"Authorization": "Bearer ${K}"}))], &granted, &env(&[]));
        assert_eq!(absent["svc"]["http_headers"], json!({"Authorization": "Bearer secret-value"}));
        let mut stubs = Map::new();
        stubs.insert("svc".into(), json!({"enabled": false}));
        let mut remote = Map::new();
        remote.insert("svc".into(), json!({"url": "a"}));
        remote.insert("hub".into(), json!({"url": "b"}));
        remote.insert("new".into(), json!({"url": "c"}));
        codex_merge(&mut stubs, remote);
        assert_eq!(stubs.keys().cloned().collect::<Vec<_>>(), ["svc", "tico_svc", "tico_hub", "new"]);
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
        assert_eq!(reachability("http://127.0.0.1:9/mcp", "http", &[]).await, "unreachable");
    }
}
