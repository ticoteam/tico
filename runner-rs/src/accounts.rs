//! The account a model CLI is signed in to (#610): its email, plan and organization name, read only from the
//! manifest's documented status source (`account`): `status-json` is the sign-in check's own JSON
//! (`claude auth status --json`), `app-server` is Codex app-server's `account/read`, always without a token refresh.
//! Credential files are not read for this. Each field is allow-listed and checked for its own shape, so a value that
//! looks like a key or token is left out instead of reported.

use std::collections::{HashMap, HashSet};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde_json::{json, Map, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

use crate::env::Env;
use crate::util::text_of;

/// How long an app-server account read stands before it is read again in the background.
const READ_EVERY: Duration = Duration::from_secs(600);

/// Text that reads like a credential: a known key prefix, a JWT, or one unbroken run of 32 or more key characters.
pub fn token_like(text: &str) -> bool {
    static R: OnceLock<regex::Regex> = OnceLock::new();
    let r = R.get_or_init(|| {
        regex::Regex::new(r"(?i)(\b(?:sk-|sk_|ghp_|gho_|ghs_|ghu_|github_pat_|xox[abp]-|bearer\s)|eyJ[A-Za-z0-9_-]{8,}|[A-Za-z0-9_\-+/=]{32,})").unwrap()
    });
    r.is_match(text)
}

fn email(v: &Value) -> Option<String> {
    static R: OnceLock<regex::Regex> = OnceLock::new();
    let r = R.get_or_init(|| regex::Regex::new(r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}$").unwrap());
    let s = v.as_str()?.trim();
    (s.len() <= 254 && r.is_match(s) && !token_like(s)).then(|| s.to_string())
}

fn plan(v: &Value) -> Option<String> {
    static R: OnceLock<regex::Regex> = OnceLock::new();
    let r = R.get_or_init(|| regex::Regex::new(r"^[A-Za-z0-9][A-Za-z0-9 _.+-]{0,39}$").unwrap());
    let s = v.as_str()?.trim();
    (r.is_match(s) && !token_like(s)).then(|| s.to_string())
}

fn org(v: &Value) -> Option<String> {
    let s = v.as_str()?.trim();
    (!s.is_empty() && s.chars().count() <= 100 && !s.chars().any(char::is_control) && !token_like(s)).then(|| s.to_string())
}

fn row(email_v: Option<String>, plan_v: Option<String>, org_v: Option<String>, kind: &str) -> Option<Value> {
    let mut out = Map::new();
    for (key, value) in [("email", email_v), ("plan", plan_v), ("org", org_v)] {
        if let Some(v) = value {
            out.insert(key.into(), json!(v));
        }
    }
    if !kind.is_empty() {
        out.insert("kind".into(), json!(kind));
    }
    (!out.is_empty()).then_some(Value::Object(out))
}

/// The account in a signed-in `claude auth status --json` answer: `email`, `subscriptionType` and `orgName` only.
pub fn from_status_json(status: &Value) -> Option<Value> {
    if status["loggedIn"] != true {
        return None;
    }
    let method = text_of(&status["authMethod"]).to_lowercase().replace(['_', '-', ' '], "");
    let kind = if method.contains("apikey") { "api_key" } else { "" };
    row(email(&status["email"]), plan(&status["subscriptionType"]), org(&status["orgName"]), kind)
}

/// The account in a Codex app-server `account/read` reply: a ChatGPT login's `email` and `planType`, or that an
/// API key signs it in (no plan usage).
pub fn from_app_server(reply: &Value) -> Option<Value> {
    let account = &reply["account"];
    match account["type"].as_str() {
        Some("chatgpt") => row(email(&account["email"]), plan(&account["planType"]), None, "subscription"),
        Some("apiKey") => row(None, None, None, "api_key"),
        _ => None,
    }
}

struct Known {
    at: Instant,
    account: Value,
}

/// Accounts per (profile, runtime); "" is the computer's own login.
fn known() -> &'static Mutex<HashMap<(String, String), Known>> {
    static K: OnceLock<Mutex<HashMap<(String, String), Known>>> = OnceLock::new();
    K.get_or_init(Default::default)
}

fn reading() -> &'static Mutex<HashSet<(String, String)>> {
    static R: OnceLock<Mutex<HashSet<(String, String)>>> = OnceLock::new();
    R.get_or_init(Default::default)
}

/// Keep `account` as what `profile` is signed in to for `runtime`, stamped now.
pub fn remember(profile: &str, runtime: &str, account: Value) {
    let mut account = account;
    if let Some(m) = account.as_object_mut() {
        m.insert("checked_at".into(), crate::util::iso(&json!(crate::util::epoch_s())));
    }
    known().lock().unwrap().insert((profile.into(), runtime.into()), Known { at: Instant::now(), account });
}

/// A plan a turn reported (Codex `planType` on its rate limits): kept on the known account, or as the account.
pub fn remember_plan(profile: &str, runtime: &str, value: &Value) {
    let Some(p) = plan(value) else { return };
    let mut account = account(profile, runtime).unwrap_or_else(|| json!({}));
    account["plan"] = json!(p);
    remember(profile, runtime, account);
}

pub fn forget(profile: &str, runtime: &str) {
    known().lock().unwrap().remove(&(profile.to_string(), runtime.to_string()));
}

pub fn account(profile: &str, runtime: &str) -> Option<Value> {
    known().lock().unwrap().get(&(profile.to_string(), runtime.to_string())).map(|k| k.account.clone())
}

/// For an `app-server` runtime: start a background `account/read` in `env` when the last one is older than
/// READ_EVERY, so a heartbeat never waits on it. The answer is picked up by the next report.
pub fn refresh_in_background(profile: &str, runtime: &str, env: Env) {
    if crate::harness_tools::caps(runtime, "").account != "app-server" {
        return;
    }
    let key = (profile.to_string(), runtime.to_string());
    if known().lock().unwrap().get(&key).is_some_and(|k| k.at.elapsed() < READ_EVERY) || !reading().lock().unwrap().insert(key.clone()) {
        return;
    }
    let Ok(handle) = tokio::runtime::Handle::try_current() else {
        reading().lock().unwrap().remove(&key);
        return;
    };
    let runtime = runtime.to_string();
    handle.spawn(async move {
        if let Some(account) = read_app_server(&runtime, &env).await {
            remember(&key.0, &key.1, account);
        }
        reading().lock().unwrap().remove(&key);
    });
}

/// Every key a runtime signs in with leaves the environment, so only the stored login answers.
pub fn without_keys(mut env: Env) -> Env {
    for k in crate::harness_tools::runtimes().iter().flat_map(|r| crate::harness_tools::api_key_env(r)).chain(["ANTHROPIC_AUTH_TOKEN".to_string()]) {
        env.remove(&k);
    }
    env
}

/// One `account/read` on a fresh `<executable> app-server` in `env`.
pub async fn read_app_server(runtime: &str, env: &Env) -> Option<Value> {
    let exe = crate::env::which(&crate::harness_tools::executable_for(runtime), &std::env::var("PATH").unwrap_or_default())?;
    let mut cmd = tokio::process::Command::new(exe);
    cmd.arg("app-server")
        .env_clear()
        .envs(env)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true);
    let mut child = cmd.spawn().ok()?;
    let (mut stdin, stdout) = (child.stdin.take()?, child.stdout.take()?);
    let mut lines = BufReader::new(stdout).lines();
    let reply = async {
        crate::subscription_refresh::rpc(&mut stdin, &mut lines, 1, "initialize", json!({"clientInfo": {"name": "tico-keeper", "title": "Tico keeper", "version": "0.1"}})).await?;
        let note = json!({"jsonrpc": "2.0", "method": "initialized", "params": {}}).to_string() + "\n";
        stdin.write_all(note.as_bytes()).await.map_err(|_| ())?;
        crate::subscription_refresh::rpc(&mut stdin, &mut lines, 2, "account/read", json!({"refreshToken": false})).await
    }
    .await;
    drop(stdin);
    if tokio::time::timeout(Duration::from_secs(5), child.wait()).await.is_err() {
        let _ = child.kill().await;
    }
    from_app_server(&reply.ok()?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_allowed_fields_travel() {
        let status = json!({"loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty", "email": "ops@acme.example",
                            "orgId": "0c1d", "orgName": "Acme Ltd", "subscriptionType": "max", "oauthToken": "sk-ant-oat01-abcdef"});
        assert_eq!(from_status_json(&status), Some(json!({"email": "ops@acme.example", "plan": "max", "org": "Acme Ltd"})));
        assert_eq!(from_status_json(&json!({"loggedIn": false, "email": "ops@acme.example"})), None);
        // A value in the wrong shape, or one that reads like a key, is left out.
        let odd = json!({"loggedIn": true, "email": "sk-ant-api03-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa@x.io", "subscriptionType": "sk-proj-abc",
                         "orgName": "eyJhbGciOiJIUzI1NiJ9.payload"});
        assert_eq!(from_status_json(&odd), None);
        assert_eq!(from_status_json(&json!({"loggedIn": true, "authMethod": "api_key"})), Some(json!({"kind": "api_key"})));
        let chatgpt = json!({"account": {"type": "chatgpt", "email": "dev@acme.example", "planType": "pro"}, "requiresOpenaiAuth": true});
        assert_eq!(from_app_server(&chatgpt), Some(json!({"email": "dev@acme.example", "plan": "pro", "kind": "subscription"})));
        assert_eq!(from_app_server(&json!({"account": {"type": "apiKey"}})), Some(json!({"kind": "api_key"})));
        assert_eq!(from_app_server(&json!({"account": null})), None);
        assert!(token_like("ghp_0123456789abcdefghij") && token_like("x".repeat(40).as_str()) && !token_like("Acme Ltd") && !token_like("Task-force Inc"));
    }

    #[test]
    fn a_turns_plan_joins_the_known_account() {
        remember("acct-test", "codex", json!({"email": "dev@acme.example"}));
        remember_plan("acct-test", "codex", &json!("plus"));
        remember_plan("acct-test", "codex", &json!("sk-proj-0123456789"));
        let got = account("acct-test", "codex").unwrap();
        assert_eq!((got["email"].clone(), got["plan"].clone()), (json!("dev@acme.example"), json!("plus")));
        assert!(got["checked_at"].is_string());
        forget("acct-test", "codex");
        assert!(account("acct-test", "codex").is_none());
    }
}
