//! What a bot's `tools:` block (older: `access:`) says, cut to what its page may show
//! (runner/declared_access.py): the service, identity, verbs, a few scope fields, the note and the *name* of
//! the credential's variable, never a value, plus whether that credential is on this computer.

use std::sync::OnceLock;

use regex::Regex;
use serde_json::{json, Map, Value};

use crate::env::Env;
use crate::manifest::py_str;

pub const MAX_ENTRIES: usize = 30;
pub const MAX_CAN: usize = 20;
pub const MAX_SCOPE: usize = 20;
pub const MAX_FORWARD: usize = 20;
const DATABASE_SERVICES: &[&str] = &["postgres", "postgresql", "mysql", "mariadb", "sqlite", "mongodb"];
/// The fields, beyond service, identity, can, env and note, that say what a tool is scoped to.
pub const SCOPE_KEYS: &[&str] = &[
    "database", "channels", "channel", "project", "projects", "mailbox", "mailboxes", "sites", "site", "repo", "repos",
    "repositories", "org", "organization", "workspace", "account", "region", "domain", "domains", "calendars", "folders",
    "drive", "drives", "bucket", "buckets", "table", "tables", "dataset", "datasets", "collections", "read_only", "org_read",
    "max_rows", "timeout_seconds", "read_preference",
];

/// `" ".join(str(value).split())[:limit]`.
pub fn one_line(value: &str, limit: usize) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(limit).collect()
}

/// A field as one line of text with any URL password taken out; "" for a missing one.
pub fn text(value: Option<&Value>, limit: usize) -> String {
    static URL_PASSWORD: OnceLock<Regex> = OnceLock::new();
    let re = URL_PASSWORD.get_or_init(|| Regex::new(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@").unwrap());
    match value {
        None | Some(Value::Null) => String::new(),
        Some(v) => one_line(&re.replace_all(&py_str(v), "$1"), limit),
    }
}

fn scalar(value: &Value, limit: usize) -> String {
    match value {
        Value::Bool(b) => if *b { "yes" } else { "no" }.into(),
        Value::String(_) | Value::Number(_) => one_line(&py_str(value), limit),
        _ => String::new(),
    }
}

/// The scope fields of an entry as {key: text | [text]}, capped.
pub fn scope_of(entry: &Value) -> Map<String, Value> {
    let mut result = Map::new();
    for key in SCOPE_KEYS {
        match entry.get(*key) {
            Some(Value::Array(items)) => {
                let kept: Vec<Value> = items.iter().take(20).map(|i| scalar(i, 100)).filter(|s| !s.is_empty()).map(Value::String).collect();
                if !kept.is_empty() {
                    result.insert(key.to_string(), Value::Array(kept));
                }
            }
            None | Some(Value::Null) => {}
            Some(Value::String(s)) if s.is_empty() => {}
            Some(v) => {
                let s = scalar(v, 200);
                if !s.is_empty() {
                    result.insert(key.to_string(), json!(s));
                }
            }
        }
        if result.len() >= MAX_SCOPE {
            break;
        }
    }
    result
}

/// The variable the credential arrives in: the declared one, else the database default.
pub fn env_name(entry: &Value) -> String {
    let name = crate::manifest::text_or_empty(entry.get("env")).trim().to_string();
    if !name.is_empty() {
        return name;
    }
    let database = crate::manifest::text_or_empty(entry.get("database")).trim().to_string();
    let service = crate::manifest::text_or_empty(entry.get("service")).to_lowercase();
    if !database.is_empty() && DATABASE_SERVICES.contains(&service.as_str()) {
        return format!("DB_{}_URL", database.to_uppercase().replace('-', "_"));
    }
    String::new()
}

/// present | missing | hub-vault | not-declared. Presence only; the value is never read into the report.
pub fn credential_state(entry: &Value, name: &str, environment: &Env) -> &'static str {
    if entry.get("vault").and_then(|v| v.as_str()) == Some("hub") {
        return "hub-vault";
    }
    if name.is_empty() {
        return "not-declared";
    }
    if environment.get(name).is_some_and(|v| !v.trim().is_empty()) { "present" } else { "missing" }
}

fn has_mcp(entry: &Value) -> bool {
    !matches!(entry.get("mcp"), None | Some(Value::Null)) && entry["mcp"] != json!({}) && entry["mcp"] != json!("")
}

/// The entries a report row is made for: objects with a service, in order.
pub fn reported_entries(access: &Value) -> Vec<&Value> {
    access.as_array().into_iter().flatten().filter(|e| e.is_object() && !crate::manifest::text_or_empty(e.get("service")).trim().is_empty()).collect()
}

/// The report rows for one bot's tools; [] when absent or malformed. `held` names variables this computer keeps
/// for its bots instead of putting them in a run (the Google key on a Docker runner): present, marked `held`.
pub fn declared_tools(access: &Value, environment: &Env, held: &[String]) -> Vec<Value> {
    let mut rows = vec![];
    for entry in reported_entries(access) {
        let name = env_name(entry);
        let can: Vec<Value> = match entry.get("can") {
            Some(Value::String(s)) => vec![json!(s)],
            Some(Value::Array(a)) => a.clone(),
            _ => vec![],
        };
        let verbs: Vec<Value> = can.iter().take(MAX_CAN).map(|v| text(Some(v), 40)).filter(|v| !v.is_empty()).map(Value::String).collect();
        let scope: Map<String, Value> = scope_of(entry)
            .into_iter()
            .map(|(k, v)| {
                let v = match v {
                    Value::Array(items) => Value::Array(items.iter().map(|i| json!(text(Some(i), 100))).collect()),
                    other => json!(text(Some(&other), 200)),
                };
                (k, v)
            })
            .collect();
        let mut row = json!({
            "service": text(entry.get("service"), 100), "identity": text(entry.get("identity"), 300), "can": verbs,
            "scope": scope, "env": one_line(&name, 100), "note": text(entry.get("note"), 500),
            "credential": credential_state(entry, &name, environment),
        });
        if row["credential"] == "missing" && held.contains(&name) {
            row["credential"] = json!("present");
            row["held"] = json!(true);
        }
        if has_mcp(entry) {
            match crate::mcp::clean_mcp(&entry["mcp"], &name) {
                Ok(m) => row["mcp"] = m,
                Err(e) => row["problem"] = json!(text(Some(&json!(format!("The MCP block is not valid: {e}"))), 300)),
            }
        }
        if crate::manifest::text_or_empty(entry.get("identity")).contains("{{") {
            row["problem"] = json!("The identity is still the template placeholder");
        }
        rows.push(row);
        if rows.len() >= MAX_ENTRIES {
            break;
        }
    }
    rows
}

/// What bot.yaml asks for its mail: `outbound_send` and the `forward_to` addresses, or None for neither.
pub fn mail_request(declared: &Value) -> Option<Value> {
    let d = declared.as_object()?;
    let raw: Vec<Value> = match d.get("forward_to") {
        Some(Value::String(s)) => s.replace(';', ",").split(',').map(|p| json!(p)).collect(),
        Some(Value::Array(a)) => a.clone(),
        _ => vec![],
    };
    let mut targets: Vec<String> = vec![];
    for item in &raw {
        let addr = text(Some(item), 320).trim().to_lowercase();
        if addr.contains('@') && !addr.contains(' ') && !targets.contains(&addr) {
            targets.push(addr);
        }
    }
    let on = d.get("outbound_send") == Some(&Value::Bool(true));
    if !on && targets.is_empty() {
        return None;
    }
    targets.truncate(MAX_FORWARD);
    Some(json!({"outbound_send": on, "forward_to": targets}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rows_name_variables_never_values_and_say_where_the_credential_is() {
        let access = json!([
            {"service": "PostHog", "identity": "analytics@acme.example", "can": "read", "env": "POSTHOG_KEY", "note": "  dashboards\n only "},
            {"service": "postgres", "database": "ware-house", "can": ["read", ""], "read_only": true, "channels": ["#a", {"x": 1}, 7]},
            {"service": "gmail", "env": "GOOGLE_SA_KEY", "can": ["read"], "identity": "{{bot_email}}"},
            {"service": "board", "vault": "hub", "can": ["act"]},
            {"service": "linear", "env": "K", "mcp": {"url": "http://example.com"}},
            {"service": "docs", "env": "K", "mcp": {"url": "https://u:pw@x.example/mcp"}, "identity": "see https://bob:hunter2@x.example/"},
            {"no": "service"}, "text"
        ]);
        let env: Env = [("POSTHOG_KEY".to_string(), "phc_value".to_string())].into_iter().collect();
        let rows = declared_tools(&access, &env, &["GOOGLE_SA_KEY".to_string()]);
        assert_eq!(rows.len(), 6);
        assert_eq!(rows[0], json!({"service": "PostHog", "identity": "analytics@acme.example", "can": ["read"], "scope": {},
                                   "env": "POSTHOG_KEY", "note": "dashboards only", "credential": "present"}));
        assert_eq!(rows[1]["env"], "DB_WARE_HOUSE_URL");
        assert_eq!(rows[1]["credential"], "missing");
        assert_eq!(rows[1]["scope"], json!({"database": "ware-house", "channels": ["#a", "7"], "read_only": "yes"}));
        assert_eq!((rows[2]["credential"].as_str(), rows[2]["held"].as_bool()), (Some("present"), Some(true)));
        assert_eq!(rows[2]["problem"], "The identity is still the template placeholder");
        assert_eq!(rows[3]["credential"], "hub-vault");
        assert!(rows[4]["problem"].as_str().unwrap().starts_with("The MCP block is not valid: mcp.url must be https"));
        assert!(rows[5]["problem"].as_str().unwrap().contains("plain address"));
        assert_eq!(rows[5]["identity"], "see https://x.example/");
        assert!(!serde_json::to_string(&rows).unwrap().contains("phc_value"));
        assert!(declared_tools(&json!("x"), &env, &[]).is_empty());
    }

    #[test]
    fn a_mail_request_is_only_what_bot_yaml_asks() {
        assert_eq!(mail_request(&json!({"name": "ops"})), None);
        assert_eq!(mail_request(&json!({"outbound_send": "yes"})), None);
        assert_eq!(
            mail_request(&json!({"forward_to": "A@x.example; b@x.example,not-an-address, a@x.example"})),
            Some(json!({"outbound_send": false, "forward_to": ["a@x.example", "b@x.example"]}))
        );
        assert_eq!(mail_request(&json!({"outbound_send": true})), Some(json!({"outbound_send": true, "forward_to": []})));
    }
}
