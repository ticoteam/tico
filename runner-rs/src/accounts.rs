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
    /// When the account was last read; None when only a turn's plan is known, so a read is still due.
    at: Option<Instant>,
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

/// When an `account/read` last started per (profile, runtime), answered or not, so one that keeps failing is retried
/// every READ_EVERY instead of on every report.
fn tried() -> &'static Mutex<HashMap<(String, String), Instant>> {
    static T: OnceLock<Mutex<HashMap<(String, String), Instant>>> = OnceLock::new();
    T.get_or_init(Default::default)
}

/// Keep `account` as what `profile` is signed in to for `runtime`, stamped now.
pub fn remember(profile: &str, runtime: &str, account: Value) {
    let mut account = account;
    if let Some(m) = account.as_object_mut() {
        m.insert("checked_at".into(), crate::util::iso(&json!(crate::util::epoch_s())));
    }
    known().lock().unwrap().insert((profile.into(), runtime.into()), Known { at: Some(Instant::now()), account });
}

/// A plan a turn reported (Codex `planType` on its rate limits): kept on the known account, or as the account. It
/// leaves the account's read time alone, so turns never make an email look freshly checked or put off its next read.
pub fn remember_plan(profile: &str, runtime: &str, value: &Value) {
    let Some(p) = plan(value) else { return };
    let mut known = known().lock().unwrap();
    let entry = known.entry((profile.into(), runtime.into())).or_insert_with(|| Known { at: None, account: json!({}) });
    entry.account["plan"] = json!(p);
}

/// A signed-out login: its account and the time of its last read go, so the next sign-in is read at once.
pub fn forget(profile: &str, runtime: &str) {
    let key = (profile.to_string(), runtime.to_string());
    known().lock().unwrap().remove(&key);
    tried().lock().unwrap().remove(&key);
}

pub fn account(profile: &str, runtime: &str) -> Option<Value> {
    known().lock().unwrap().get(&(profile.to_string(), runtime.to_string())).map(|k| k.account.clone())
}

/// Whether a read of `key` may start now: none running, and none read or tried within READ_EVERY. A yes marks it
/// running and tried.
fn claim_read(key: &(String, String)) -> bool {
    let fresh = |at: &Instant| at.elapsed() < READ_EVERY;
    if known().lock().unwrap().get(key).is_some_and(|k| k.at.as_ref().is_some_and(fresh)) || tried().lock().unwrap().get(key).is_some_and(fresh) {
        return false;
    }
    if !reading().lock().unwrap().insert(key.clone()) {
        return false;
    }
    tried().lock().unwrap().insert(key.clone(), Instant::now());
    true
}

/// For an `app-server` runtime: start a background `account/read` in `env` when the last one, answered or not, is
/// older than READ_EVERY, so a heartbeat never waits on it. The answer is picked up by the next report.
pub fn refresh_in_background(profile: &str, runtime: &str, env: Env) {
    if crate::harness_tools::caps(runtime, "").account != "app-server" {
        return;
    }
    let Ok(handle) = tokio::runtime::Handle::try_current() else { return };
    let key = (profile.to_string(), runtime.to_string());
    if !claim_read(&key) {
        return;
    }
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

/// `<exe> app-server` in `env`, as the bot user like sign-in and turns: it writes in the profile's CODEX_HOME,
/// which is the bot user's under isolation.
fn app_server(exe: &std::path::Path, env: &Env) -> tokio::process::Command {
    let mut cmd = crate::isolation::command(exe);
    cmd.arg("app-server")
        .env_clear()
        .envs(env)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true);
    cmd
}

/// One `account/read` on a fresh `<executable> app-server` in `env`.
pub async fn read_app_server(runtime: &str, env: &Env) -> Option<Value> {
    let exe = crate::env::which(&crate::harness_tools::executable_for(runtime), &std::env::var("PATH").unwrap_or_default())?;
    let mut child = app_server(&exe, env).spawn().ok()?;
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
    fn the_account_reader_runs_as_the_bot_user_in_the_profile_home() {
        const CHILD: &str = "TICO_TEST_ACCOUNT_IDENTITY";
        if std::env::var_os(CHILD).is_none() {
            // A separate process keeps the configured isolation identity out of other parallel tests.
            let uid = if unsafe { libc::geteuid() } == 10003 { 10004 } else { 10003 };
            let out = std::process::Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "accounts::tests::the_account_reader_runs_as_the_bot_user_in_the_profile_home", "--nocapture"])
                .env(CHILD, "1").env(crate::isolation::UID_ENV, uid.to_string()).env(crate::isolation::GID_ENV, "10002")
                .output().unwrap();
            assert!(out.status.success(), "{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr));
            return;
        }
        let uid = std::env::var(crate::isolation::UID_ENV).unwrap();
        let env: Env = [("CODEX_HOME".into(), "/subscriptions/acme/codex".into())].into_iter().collect();
        let command = app_server(std::path::Path::new("/tools/codex"), &env);
        let command = command.as_std();
        assert_eq!(command.get_program(), crate::isolation::SETPRIV);
        let args: Vec<String> = command.get_args().map(|a| a.to_string_lossy().into_owned()).collect();
        assert_eq!(args, [format!("--reuid={uid}"), "--regid=10002".into(), "--clear-groups".into(), "--inh-caps=-all".into(),
            "--ambient-caps=-all".into(), "/tools/codex".into(), "app-server".into()]);
        assert!(command.get_envs().any(|(k, v)| k == "CODEX_HOME" && v == Some(std::ffi::OsStr::new("/subscriptions/acme/codex"))));
    }

    /// Root plays the supervisor, `TICO_RUNNER_BOT_UID=10003 TICO_RUNNER_BOT_GID=10002` the bot user. The real
    /// `read_app_server` finds a provider-free stand-in for codex first on PATH, which answers initialize and
    /// account/read and records which user it ran as in the profile's bot-owned CODEX_HOME.
    #[cfg(target_os = "linux")]
    #[test]
    #[ignore = "requires a disposable Linux two-user environment"]
    fn isolated_the_account_reader_writes_in_a_bot_owned_codex_home() {
        use std::os::unix::fs::{chown, MetadataExt, PermissionsExt};
        const CHILD: &str = "TICO_TEST_ACCOUNT_READER_HOME";
        assert_eq!(unsafe { libc::geteuid() }, 0, "disposable supervisor fixture");
        assert!(std::env::var_os("TICO_TEST_DISPOSABLE").is_some());
        assert_eq!(crate::isolation::identity(), Some((10003, 10002)));
        let Some(dir) = std::env::var_os(CHILD).map(std::path::PathBuf::from) else {
            let dir = std::env::temp_dir().join(format!("tico-account-contract-{}", uuid::Uuid::new_v4().simple()));
            let (bin, home) = (dir.join("bin"), dir.join("profile/codex"));
            std::fs::create_dir_all(&bin).unwrap();
            std::fs::create_dir_all(&home).unwrap();
            for (path, mode) in [(&dir, 0o755), (&bin, 0o755), (&dir.join("profile"), 0o711), (&home, 0o700)] {
                std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode)).unwrap();
            }
            chown(&home, Some(10003), Some(10002)).unwrap(); // a profile home after profiles::hand_to_bot
            let exe = bin.join(crate::harness_tools::executable_for("codex"));
            std::fs::write(&exe, concat!(
                "#!/bin/sh\n",
                "[ \"$1\" = app-server ] || exit 2\n",
                "read -r init; case \"$init\" in *'\"initialize\"'*) ;; *) exit 3;; esac\n",
                "echo '{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}'\n",
                "read -r initialized\n",
                "read -r account; case \"$account\" in *'\"account/read\"'*) ;; *) exit 4;; esac\n",
                "id -u > \"$CODEX_HOME/app-server-state\" || exit 5\n",
                "echo '{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"account\":{\"type\":\"chatgpt\",\"email\":\"ops@acme.example\",\"planType\":\"plus\"}}}'\n",
            )).unwrap();
            std::fs::set_permissions(&exe, std::fs::Permissions::from_mode(0o755)).unwrap();
            // A separate process puts the stand-in first on PATH without changing it for other tests.
            let path = format!("{}:{}", bin.display(), std::env::var("PATH").unwrap_or_default());
            let out = std::process::Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "accounts::tests::isolated_the_account_reader_writes_in_a_bot_owned_codex_home", "--ignored", "--nocapture"])
                .env(CHILD, &dir).env("PATH", path)
                .output().unwrap();
            let _ = std::fs::remove_dir_all(&dir);
            assert!(out.status.success(), "{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr));
            return;
        };
        let home = dir.join("profile/codex");
        let env: Env = [("CODEX_HOME".to_string(), home.display().to_string()), ("PATH".to_string(), "/usr/bin:/bin".to_string())]
            .into_iter().collect();
        let account = tokio::runtime::Runtime::new().unwrap().block_on(read_app_server("codex", &env));
        assert_eq!(account, Some(json!({"email": "ops@acme.example", "plan": "plus", "kind": "subscription"})));
        let state = home.join("app-server-state");
        assert_eq!(std::fs::read_to_string(&state).unwrap().trim(), "10003", "the reader's child ran as the bot user");
        assert_eq!(std::fs::symlink_metadata(&state).unwrap().uid(), 10003, "the bot user can change it on its next turn");
    }

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

    #[test]
    fn a_turns_plan_never_counts_as_an_account_read() {
        let key = ("plan-test".to_string(), "codex".to_string());
        // Only a turn's plan known: a read is still due, and nothing says the account was checked.
        remember_plan("plan-test", "codex", &json!("plus"));
        assert!(known().lock().unwrap()[&key].at.is_none());
        assert!(account("plan-test", "codex").unwrap()["checked_at"].is_null());
        // A read, then a turn's plan: the read's time and stamp stay.
        remember("plan-test", "codex", json!({"email": "dev@acme.example"}));
        let (at, stamp) = { let k = known().lock().unwrap(); (k[&key].at, k[&key].account["checked_at"].clone()) };
        remember_plan("plan-test", "codex", &json!("pro"));
        let k = known().lock().unwrap();
        assert_eq!((k[&key].at, k[&key].account["checked_at"].clone(), k[&key].account["plan"].clone()), (at, stamp, json!("pro")));
        drop(k);
        forget("plan-test", "codex");
    }

    #[test]
    fn a_failed_read_waits_its_turn() {
        let key = ("retry-test".to_string(), "codex".to_string());
        // A turn's plan only (no account read yet), then a read that answers nothing: no second read within READ_EVERY.
        remember_plan("retry-test", "codex", &json!("plus"));
        assert!(claim_read(&key), "the first read starts");
        assert!(!claim_read(&key), "never two at once");
        reading().lock().unwrap().remove(&key);
        assert!(!claim_read(&key), "a failed read is not retried on the next report");
        assert!(known().lock().unwrap()[&key].at.is_none(), "still no account read");
        // Once READ_EVERY has passed it is tried again (where the clock reaches back that far).
        if let Some(earlier) = Instant::now().checked_sub(READ_EVERY) {
            tried().lock().unwrap().insert(key.clone(), earlier);
            assert!(claim_read(&key));
            reading().lock().unwrap().remove(&key);
        }
        // Signing out clears the wait, so the next sign-in is read at once.
        forget("retry-test", "codex");
        assert!(claim_read(&key));
        reading().lock().unwrap().remove(&key);
        forget("retry-test", "codex");
    }
}
