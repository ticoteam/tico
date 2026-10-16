//! GitHub credentials for a turn (`runner/git_credentials.py`, `runner/credential_socket.py`,
//! `runner/credential_bin/gh`).
//!
//! The hub mints a repository-scoped GitHub App token that lasts an hour; a turn can run longer, so git
//! does not keep one. Its credential helper is `tico-runner git-credential`, which asks this runner's
//! credential socket (one JSON line each way, the attempt token in, a GitHub token out) on every `get`.
//! `gh` reaches GitHub through a shim, the same binary called as `gh`, which picks the token for the
//! repository the command names and then runs the real `gh`.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt};

use crate::client::{ApiError, Client};
use crate::env::Env;
use crate::redact::Redactor;
use crate::util::{epoch_s, log, parse_iso};

pub const SOCKET_ENV: &str = "TICO_CRED_SOCKET";
pub const MAX_LINE: usize = 4096;
pub const KEY: &str = "credential.https://github.com.helper";
pub const TOKENS_KEY: &str = "TICO_GITHUB_TOKENS";
pub const OTHER_TOKEN_KEY: &str = "TICO_GITHUB_OTHER_TOKEN";
pub const TURN_PATH_KEY: &str = "TICO_TURN_PATH";
pub const REPOSITORY_KEY: &str = "TICO_GITHUB_REPOSITORY";
pub const FAILED_HELPER: &str = "!f() { echo \"Tico: GitHub App token unavailable; check Settings > Tools > GitHub\" >&2; echo quit=true; }; f";
pub const STATIC_HELPER: &str = "!f() { echo username=x-access-token; echo \"password=$GH_TOKEN\"; }; f";
const GIT_MARGIN: f64 = 600.0;
const TURN_MARGIN: f64 = 2700.0;
const HOLD_S: f64 = 300.0;

pub fn shell_quote(s: &str) -> String {
    if !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric() || "@%+=:,./-_".contains(c)) {
        return s.to_string();
    }
    format!("'{}'", s.replace('\'', "'\"'\"'"))
}

fn expiry(values: &[&Value]) -> f64 {
    let mut min = f64::INFINITY;
    for v in values {
        match v.as_str().map(parse_iso) {
            Some(t) if t > 0.0 => min = min.min(t),
            _ => return 0.0,
        }
    }
    if min.is_finite() { min } else { 0.0 }
}

/// POST github/token, reusing an answer held while every token in it has the purpose's margin left.
pub struct TokenCache {
    held: Mutex<HashMap<String, (f64, Value)>>,
    legacy_purpose: Mutex<bool>,
}

impl TokenCache {
    pub fn new() -> TokenCache {
        TokenCache { held: Mutex::new(HashMap::new()), legacy_purpose: Mutex::new(false) }
    }

    pub async fn get(&self, client: &Client, body: Value) -> Result<Value, ApiError> {
        let key = body.to_string();
        let margin = if body["purpose"].as_str() == Some("git") { GIT_MARGIN } else { TURN_MARGIN };
        if let Some((until, granted)) = self.held.lock().unwrap().get(&key) {
            if *until > epoch_s() {
                return Ok(granted.clone());
            }
        }
        let granted = client.post("github/token", &body, None).await?;
        let mut stamps = vec![&granted["expires_at"]];
        if let Some(tokens) = granted["tokens"].as_array() {
            stamps.extend(tokens.iter().map(|g| &g["expires_at"]));
        }
        let until = if granted["token"].as_str().is_some_and(|t| !t.is_empty()) { expiry(&stamps) } else { 0.0 };
        // BotOps' answer depends on the runs live when it was asked; the hub says so with hold=false.
        let holdable = granted.get("hold").map(|h| h != &Value::Bool(false)).unwrap_or(body["bot"].as_str() != Some("botops"));
        if holdable && until - margin > epoch_s() {
            self.held.lock().unwrap().insert(key, ((until - margin).min(epoch_s() + HOLD_S), granted.clone()));
        }
        Ok(granted)
    }

    /// The git-purpose token, asking an older server without `purpose` once it refused the field.
    pub async fn git(&self, client: &Client, bot: &str) -> Result<Value, ApiError> {
        if *self.legacy_purpose.lock().unwrap() {
            return self.get(client, json!({"bot": bot})).await;
        }
        match self.get(client, json!({"bot": bot, "purpose": "git"})).await {
            Err(e) if rejects_purpose(&e) => {
                *self.legacy_purpose.lock().unwrap() = true;
                self.get(client, json!({"bot": bot})).await
            }
            other => other,
        }
    }
}

fn rejects_purpose(e: &ApiError) -> bool {
    if e.status != 422 || !["validation", "validation_error", "unknown_field", "extra_forbidden"].contains(&e.code.to_lowercase().as_str()) {
        return false;
    }
    let d = e.detail.to_lowercase();
    d.contains("purpose") && ["extra input", "extra field", "unknown field", "unexpected field", "extra_forbidden"].iter().any(|m| d.contains(m))
}

/// Old servers have one token; new ones have authoritative repository groups.
pub fn select_token(granted: &Value, repository: Option<&str>) -> String {
    if !crate::util::truthy(granted.get("configured")) {
        return String::new();
    }
    if let (Some(repo), Some(groups)) = (repository, granted.get("tokens").and_then(|t| t.as_array())) {
        for g in groups {
            if g["repositories"].as_array().is_some_and(|names| names.iter().any(|n| n.as_str().is_some_and(|n| n.eq_ignore_ascii_case(repo)))) {
                return g["token"].as_str().unwrap_or_default().to_string();
            }
        }
        return String::new();
    }
    granted["token"].as_str().unwrap_or_default().to_string()
}

// ----------------------------------------------------------------------------- the socket

struct Registered {
    bot: String,
    /// The mailboxes the hub named for this attempt (crate::mail_key::answer).
    mailboxes: std::collections::BTreeSet<String>,
    issued: Vec<String>,
    redactor: Option<Arc<Redactor>>,
}

pub struct CredentialSocket {
    pub path: PathBuf,
    dir: Option<PathBuf>,
    attempts: Arc<Mutex<HashMap<String, Registered>>>,
}

impl CredentialSocket {
    /// Listen in a private temporary directory, as `credential_socket.serve` does without isolation.
    /// Under isolation (or with TICO_RUNNER_CRED_SOCKET) it is a fixed path the bot user owns.
    pub fn serve(client: Client, cache: Arc<TokenCache>) -> std::io::Result<CredentialSocket> {
        let named = std::env::var("TICO_RUNNER_CRED_SOCKET").ok().filter(|p| !p.is_empty());
        let (dir, path) = match named {
            None if !crate::isolation::enabled() => {
                let dir = PathBuf::from("/tmp").join(format!("tico-credentials-{}", uuid::Uuid::new_v4().simple()));
                std::fs::create_dir(&dir)?;
                std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700))?;
                let path = dir.join("credential.sock");
                (Some(dir), path)
            }
            named => {
                let path = PathBuf::from(named.unwrap_or_else(|| "/run/tico-runner/git-credential.sock".into()));
                if let Some(parent) = path.parent() {
                    use std::os::unix::fs::DirBuilderExt;
                    std::fs::DirBuilder::new().recursive(true).mode(0o755).create(parent)?;
                }
                let _ = std::fs::remove_file(&path);
                (None, path)
            }
        };
        let listener = tokio::net::UnixListener::bind(&path)?;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))?;
        crate::isolation::chown(&path);
        let attempts: Arc<Mutex<HashMap<String, Registered>>> = Arc::new(Mutex::new(HashMap::new()));
        let shared = attempts.clone();
        tokio::spawn(async move {
            loop {
                let Ok((stream, _)) = listener.accept().await else { continue };
                let (attempts, client, cache) = (shared.clone(), client.clone(), cache.clone());
                tokio::spawn(async move {
                    let (read, mut write) = stream.into_split();
                    let mut reader = tokio::io::BufReader::new(read).take(MAX_LINE as u64);
                    let mut line = String::new();
                    let answered = tokio::time::timeout(Duration::from_secs(20), async {
                        let _ = reader.read_line(&mut line).await;
                        let reply = answer(&attempts, &client, &cache, &line).await;
                        let _ = write.write_all(format!("{reply}\n").as_bytes()).await;
                    });
                    let _ = answered.await;
                });
            }
        });
        Ok(CredentialSocket { path, dir, attempts })
    }

    pub fn register(&self, attempt_token: &str, bot: &str) {
        self.attempts.lock().unwrap().insert(attempt_token.into(), Registered { bot: bot.into(), mailboxes: Default::default(), issued: vec![], redactor: None });
    }

    /// The mailboxes this attempt may ask a mail token for.
    pub fn set_mailboxes(&self, attempt_token: &str, mailboxes: std::collections::BTreeSet<String>) {
        if let Some(r) = self.attempts.lock().unwrap().get_mut(attempt_token) {
            r.mailboxes = mailboxes;
        }
    }

    pub fn set_redactor(&self, attempt_token: &str, redactor: Arc<Redactor>) {
        if let Some(r) = self.attempts.lock().unwrap().get_mut(attempt_token) {
            redactor.add(&std::mem::take(&mut r.issued));
            r.redactor = Some(redactor);
        }
    }

    pub fn unregister(&self, attempt_token: &str) {
        self.attempts.lock().unwrap().remove(attempt_token);
    }
}

impl Drop for CredentialSocket {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
        if let Some(dir) = &self.dir {
            let _ = std::fs::remove_dir(dir);
        }
    }
}

async fn answer(attempts: &Mutex<HashMap<String, Registered>>, client: &Client, cache: &TokenCache, line: &str) -> Value {
    let Ok(asked) = serde_json::from_str::<Value>(line) else { return json!({"error": "bad request"}) };
    let Some(token) = asked.get("token").and_then(|t| t.as_str()).filter(|t| !t.is_empty()) else {
        return json!({"error": if asked.is_object() { "unknown attempt" } else { "bad request" }});
    };
    let known = attempts.lock().unwrap().get(token).map(|r| (r.bot.clone(), r.mailboxes.clone()));
    let Some((bot, mailboxes)) = known else { return json!({"error": "unknown attempt"}) };
    if let Some(mail) = asked.get("mail") {
        return crate::mail_key::answer(&mailboxes, mail).await;
    }
    let repository = match asked.get("repository") {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) => Some(s.clone()),
        Some(_) => return json!({"error": "bad repository"}),
    };
    let purpose = asked.get("purpose").and_then(|p| p.as_str()).unwrap_or("turn");
    if !(asked.get("purpose").is_none() || ["turn", "git"].contains(&purpose)) || (asked.get("purpose").is_some() && asked["purpose"].as_str().is_none()) {
        return json!({"error": "bad purpose"});
    }
    let granted = if purpose == "git" { cache.git(client, &bot).await } else { cache.get(client, json!({"bot": bot})).await };
    let granted = match granted {
        Ok(g) => select_token(&g, repository.as_deref().filter(|r| !r.is_empty())),
        Err(e) if e.status != 0 => return json!({"error": format!("APIError: {}", token_problem(&e, repository.as_deref().unwrap_or("")))}),
        Err(_) => return json!({"error": "APIError"}),
    };
    if asked.get("refresh").is_some_and(|r| crate::util::truthy(Some(r))) {
        // A bounded mirror refresh for a turn that may read the repository; the token never leaves this process.
        let (Some(repository), Some(mirrors)) = (repository.filter(|r| !r.is_empty()), crate::repositories::MIRRORS.get()) else {
            return json!({"error": "mirror refresh unavailable"});
        };
        if granted.is_empty() {
            return json!({"error": "mirror refresh unavailable"});
        }
        return mirrors.refresh_mirror(&repository, &granted).await.unwrap_or_else(|_| json!({"error": "ValueError"}));
    }
    if granted.is_empty() {
        return json!({"error": "no token"});
    }
    let mut map = attempts.lock().unwrap();
    let Some(entry) = map.get_mut(token) else { return json!({"error": "unknown attempt"}) };
    match &entry.redactor {
        Some(r) => r.add(std::slice::from_ref(&granted)),
        None => entry.issued.push(granted.clone()),
    }
    json!({"token": granted})
}

/// Why Tico could not get a GitHub token, in plain words.
pub fn token_problem(e: &ApiError, repository: &str) -> String {
    let what = if repository.is_empty() { "for this bot's repositories".to_string() } else { format!("for {repository}") };
    // Scrubbed whole before it is cut: a token cut in half no longer looks like one.
    let detail = crate::redact::scrub_credentials(&e.detail.split_whitespace().collect::<Vec<_>>().join(" "), &[]);
    let detail: String = detail.chars().take(200).collect();
    let why = if e.code == "github_repo_not_accessible" || (e.status == 409 && detail.contains("not reachable")) {
        "the GitHub App is not installed on it or cannot reach it; add it to the App's repository access on GitHub, then check Settings > Tools > GitHub".to_string()
    } else if e.status == 403 && detail.contains("organization") {
        format!("it is outside the connected GitHub organization ({detail})")
    } else if e.status != 0 {
        if detail.is_empty() { format!("the server answered {} ({})", e.status, if e.code.is_empty() { "no detail" } else { &e.code }) } else { detail }
    } else {
        "the server did not answer (APIError)".to_string()
    };
    crate::redact::scrub_credentials(&format!("Tico could not get a GitHub token {what}: {why}"), &[])
}

// ----------------------------------------------------------------------------- the turn's environment

fn git_environment(env: &mut Env, token: &str, helper: &str) {
    for (k, v) in [
        ("GH_TOKEN", token),
        ("GITHUB_TOKEN", token),
        ("GIT_TERMINAL_PROMPT", "0"),
        ("GIT_CONFIG_COUNT", "3"),
        ("GIT_CONFIG_KEY_0", KEY),
        ("GIT_CONFIG_VALUE_0", ""),
        ("GIT_CONFIG_KEY_1", KEY),
        ("GIT_CONFIG_VALUE_1", helper),
        ("GIT_CONFIG_KEY_2", "credential.https://github.com.useHttpPath"),
        ("GIT_CONFIG_VALUE_2", "true"),
    ] {
        env.insert(k.into(), v.into());
    }
}

/// The helper git runs on every `get`: this binary asking the socket.
pub fn fresh_helper(socket: &Path, bot: &str) -> String {
    let exe = std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_else(|_| "tico-runner".into());
    let run = [exe.as_str(), "git-credential", "--socket", &socket.display().to_string(), "--bot", bot]
        .iter()
        .map(|p| shell_quote(p))
        .collect::<Vec<_>>()
        .join(" ");
    format!("!f() {{ [ \"$1\" = get ] || exit 0; exec {run}; }}; f")
}

fn failed_helper(reason: &str) -> String {
    let reason: String = reason.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(300).collect();
    if reason.is_empty() {
        return FAILED_HELPER.into();
    }
    format!("!f() {{ echo {} >&2; echo quit=true; }}; f", shell_quote(&reason))
}

/// The `gh` shim: a directory holding `gh`, a link to this binary.
pub fn shim_dir(state_dir: &Path) -> Option<PathBuf> {
    let dir = state_dir.join("bin");
    let gh = dir.join("gh");
    let exe = std::env::current_exe().ok()?;
    std::fs::create_dir_all(&dir).ok()?;
    // Under isolation the bot user runs it, through a state directory it may only traverse.
    if crate::isolation::enabled() {
        let _ = std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o755));
    }
    if std::fs::read_link(&gh).ok().as_deref() != Some(exe.as_path()) {
        let _ = std::fs::remove_file(&gh);
        std::os::unix::fs::symlink(&exe, &gh).ok()?;
    }
    Some(dir)
}

/// Add the bot's repository-scoped GitHub credentials to `env`. Never fails the turn.
pub async fn apply(env: &mut Env, client: &Client, cache: &TokenCache, bot: &str, socket: Option<&Path>, state_dir: &Path) -> bool {
    let mut reason = String::new();
    let granted = match cache.get(client, json!({"bot": bot})).await {
        Ok(g) => g,
        Err(e) => {
            reason = token_problem(&e, "");
            log(&format!("Tico runner: {bot}: GitHub App token unavailable (APIError): {reason}"));
            json!({"configured": true})
        }
    };
    if !crate::util::truthy(granted.get("configured")) {
        return false;
    }
    let token = granted["token"].as_str().unwrap_or_default().to_string();
    let applied = !token.is_empty();
    let stored = env.get("GH_TOKEN").or_else(|| env.get("GITHUB_TOKEN")).cloned().unwrap_or_default();
    if applied && !stored.is_empty() && stored != token && crate::util::truthy(granted.get("repository")) {
        env.insert(OTHER_TOKEN_KEY.into(), stored);
    }
    let has_tokens = granted.get("tokens").is_some();
    let helper = match socket {
        Some(s) => fresh_helper(s, bot),
        None if has_tokens => fresh_helper(Path::new("/nonexistent"), bot),
        None => STATIC_HELPER.into(),
    };
    git_environment(env, if applied { &token } else { "" }, &if applied { helper } else { failed_helper(&reason) });
    if has_tokens {
        let tokens = if applied { granted["tokens"].clone() } else { json!([]) };
        env.insert(TOKENS_KEY.into(), tokens.to_string());
        let path = env.get("PATH").cloned().unwrap_or_default();
        if let (Some(gh), Some(dir)) = (crate::env::which("gh", &path), shim_dir(state_dir)) {
            env.insert("TICO_GITHUB_GH".into(), gh.display().to_string());
            env.insert("TICO_GITHUB_BOT".into(), bot.into());
            let turn_path = format!("{}:{path}", dir.display());
            env.insert("PATH".into(), turn_path.clone());
            env.insert(TURN_PATH_KEY.into(), turn_path);
            if cfg!(target_os = "macos") {
                let startup = crate::env::root().join("runner").join("shell_init");
                env.insert("TICO_TURN_BASH_ENV".into(), env.get("BASH_ENV").cloned().unwrap_or_default());
                let zdot = env.get("ZDOTDIR").or_else(|| env.get("HOME")).cloned().unwrap_or_default();
                env.insert("TICO_TURN_ZDOTDIR".into(), zdot);
                env.insert("BASH_ENV".into(), startup.join("bash-env").display().to_string());
                env.insert("ZDOTDIR".into(), startup.display().to_string());
            }
        }
    }
    if let Some(s) = socket {
        env.insert(SOCKET_ENV.into(), s.display().to_string());
    }
    if let Some(repo) = granted["repository"].as_str().filter(|r| !r.is_empty()) {
        env.insert(REPOSITORY_KEY.into(), repo.into());
    }
    applied
}

// ----------------------------------------------------------------------------- inside a turn

/// Normalize a git credential path, GitHub URL or owner/repo argument.
pub fn repository_name(value: &str) -> Option<String> {
    let mut v = value.trim().to_string();
    for prefix in ["https://github.com/", "http://github.com/", "git@github.com:"] {
        if v.len() >= prefix.len() && v[..prefix.len()].eq_ignore_ascii_case(prefix) {
            v = v[prefix.len()..].to_string();
            break;
        }
    }
    let parts: Vec<&str> = v.trim_matches('/').split('/').collect();
    if parts.len() < 2 {
        return None;
    }
    let name = parts[1].strip_suffix(".git").unwrap_or(parts[1]);
    let ok = |s: &str| !s.is_empty() && s.chars().all(|c| c.is_alphanumeric() || c == '_' || c == '.' || c == '-');
    (ok(parts[0]) && ok(name)).then(|| format!("{}/{}", parts[0], name))
}

fn held_token(repository: Option<&str>) -> String {
    let groups: Value = std::env::var(TOKENS_KEY).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or(json!([]));
    for g in groups.as_array().into_iter().flatten() {
        let matches = match repository {
            None => true,
            Some(r) => g["repositories"].as_array().is_some_and(|n| n.iter().any(|x| x.as_str().is_some_and(|x| x.eq_ignore_ascii_case(r)))),
        };
        if matches {
            let fresh = expiry(&[&g["expires_at"]]) - GIT_MARGIN > epoch_s();
            return if fresh { g["token"].as_str().unwrap_or_default().to_string() } else { String::new() };
        }
    }
    String::new()
}

fn outside_token(repository: Option<&str>) -> String {
    let org = std::env::var(REPOSITORY_KEY).unwrap_or_default().split('/').next().unwrap_or_default().to_lowercase();
    let owner = repository.unwrap_or_default().split('/').next().unwrap_or_default().to_lowercase();
    if !org.is_empty() && !owner.is_empty() && owner != org {
        std::env::var(OTHER_TOKEN_KEY).unwrap_or_default()
    } else {
        String::new()
    }
}

/// One request to the credential socket; Err(reason) on any refusal.
pub fn socket_request(path: &Path, attempt_token: &str, repository: Option<&str>, purpose: &str) -> Result<String, String> {
    let mut stream = std::os::unix::net::UnixStream::connect(path).map_err(|e| e.to_string())?;
    stream.set_read_timeout(Some(Duration::from_secs(15))).ok();
    let mut asked = json!({"token": attempt_token});
    if let Some(r) = repository {
        asked["repository"] = json!(r);
    }
    if purpose != "turn" {
        asked["purpose"] = json!(purpose);
    }
    stream.write_all(format!("{asked}\n").as_bytes()).map_err(|e| e.to_string())?;
    let mut line = String::new();
    BufReader::new(stream.take(MAX_LINE as u64)).read_line(&mut line).map_err(|e| e.to_string())?;
    let reply: Value = serde_json::from_str(&line).map_err(|e| e.to_string())?;
    match reply["token"].as_str().filter(|t| !t.is_empty()) {
        Some(t) => Ok(t.to_string()),
        None => Err(reply["error"].as_str().unwrap_or("refused").to_string()),
    }
}

/// The token for `repository`: the bot's own for outside the App's organization, the turn's held one while
/// it has ten minutes left, else a fresh one from the socket, else the turn's start-of-turn token.
pub fn credential(socket: Option<&str>, repository: Option<&str>) -> String {
    let other = outside_token(repository);
    if !other.is_empty() {
        return other;
    }
    let held = held_token(repository);
    if !held.is_empty() {
        return held;
    }
    if let Some(s) = socket.filter(|s| !s.is_empty()) {
        match socket_request(Path::new(s), &std::env::var("HUB_TOKEN").unwrap_or_default(), repository, "git") {
            Ok(t) => return t,
            Err(e) => eprintln!("Tico runner: no fresh GitHub App token (socket): {}", e.chars().take(300).collect::<String>()),
        }
    }
    let mut granted = json!({"configured": true, "token": std::env::var("GH_TOKEN").unwrap_or_default()});
    if let Ok(t) = std::env::var(TOKENS_KEY) {
        granted["tokens"] = serde_json::from_str(&t).unwrap_or(json!([]));
    }
    select_token(&granted, repository)
}

/// `tico-runner git-credential --socket S --bot B`: git's credential helper protocol on stdin/stdout.
pub fn git_credential_main(socket: Option<String>) -> i32 {
    let mut input = String::new();
    let _ = std::io::stdin().read_to_string(&mut input);
    let wanted: HashMap<String, String> = input.lines().filter_map(|l| l.split_once('=')).map(|(k, v)| (k.into(), v.into())).collect();
    let host = std::env::var("TICO_GITHUB_HOST").ok().filter(|h| !h.is_empty()).unwrap_or_else(|| "github.com".into());
    if wanted.get("host") != Some(&host) {
        return 0;
    }
    let repo = wanted.get("path").and_then(|p| repository_name(p));
    let token = credential(socket.as_deref(), repo.as_deref());
    if token.is_empty() {
        eprintln!("Tico runner: no GitHub App token for this repository; check Settings > Tools > GitHub");
        println!("quit=true");
    } else {
        print!("username=x-access-token\npassword={token}\n");
    }
    0
}

/// Which repository a `gh` command is about (`git_credentials.gh_repository`).
pub fn gh_repository(argv: &[String], gh_repo: Option<&str>) -> Option<String> {
    const VALUED: &[&str] = &[
        "--body", "-b", "--title", "-t", "--body-file", "-F", "--field", "-f", "--raw-field", "--header", "-H", "--comment", "--notes", "-m",
        "--message", "--jq", "-q", "--search", "--template",
    ];
    let mut skip = false;
    let mut positional = vec![];
    for (i, arg) in argv.iter().enumerate() {
        if skip {
            skip = false;
            continue;
        }
        if VALUED.contains(&arg.as_str()) {
            skip = true;
            continue;
        }
        if (arg == "-R" || arg == "--repo") && i + 1 < argv.len() {
            return repository_name(&argv[i + 1]);
        }
        if let Some(v) = arg.strip_prefix("--repo=") {
            return repository_name(v);
        }
        if arg.starts_with("-R") && arg.len() > 2 {
            return repository_name(&arg[2..]);
        }
        if !arg.starts_with('-') {
            positional.push(arg.clone());
        }
    }
    for arg in &positional {
        if arg.starts_with("https://github.com/") {
            return repository_name(arg);
        }
        let trimmed = arg.trim_start_matches('/');
        if let Some(rest) = trimmed.strip_prefix("repos/") {
            if let Some(n) = repository_name(rest) {
                return Some(n);
            }
        }
    }
    if argv.len() > 2 && argv[0] == "repo" && ["view", "clone", "fork"].contains(&argv[1].as_str()) && !argv[2].starts_with('-') {
        return repository_name(&argv[2]);
    }
    if let Some(r) = gh_repo.filter(|r| !r.is_empty()) {
        return repository_name(r);
    }
    let out = std::process::Command::new("git").args(["config", "--get", "remote.origin.url"]).output().ok()?;
    out.status.success().then(|| repository_name(&String::from_utf8_lossy(&out.stdout))).flatten()
}

/// The `gh` shim: pick the token for the command's repository, then become the real `gh`.
pub fn gh_main(args: Vec<String>) -> i32 {
    use std::os::unix::process::CommandExt;
    let repo = gh_repository(&args, std::env::var("GH_REPO").ok().as_deref());
    let token = credential(std::env::var(SOCKET_ENV).ok().as_deref(), repo.as_deref());
    if token.is_empty() {
        eprintln!("Tico runner: no GitHub App token for this repository; check Settings > Tools > GitHub");
        return 1;
    }
    let Ok(gh) = std::env::var("TICO_GITHUB_GH") else {
        eprintln!("Tico runner: gh is not installed on this computer");
        return 1;
    };
    let err = std::process::Command::new(&gh).args(&args).env("GH_TOKEN", &token).env("GITHUB_TOKEN", &token).exec();
    eprintln!("Tico runner: could not run {gh}: {err}");
    127
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gh_finds_the_repository_the_way_the_python_shim_does() {
        let a = |s: &str| s.split(' ').map(String::from).collect::<Vec<_>>();
        assert_eq!(gh_repository(&a("pr list -R acme/web"), None).as_deref(), Some("acme/web"));
        assert_eq!(gh_repository(&a("pr create --title acme/x --repo=acme/api"), None).as_deref(), Some("acme/api"));
        assert_eq!(gh_repository(&a("api repos/acme/site/pulls"), None).as_deref(), Some("acme/site"));
        assert_eq!(gh_repository(&a("pr view https://github.com/acme/app/pull/3"), None).as_deref(), Some("acme/app"));
        assert_eq!(gh_repository(&a("repo clone acme/tools"), None).as_deref(), Some("acme/tools"));
        assert_eq!(gh_repository(&a("pr list"), Some("acme/fromenv")).as_deref(), Some("acme/fromenv"));
        assert_eq!(repository_name("git@github.com:acme/bot-ops.git").as_deref(), Some("acme/bot-ops"));
    }

    #[test]
    fn a_token_is_selected_by_its_repository_group() {
        let granted = json!({"configured": true, "token": "t0", "tokens": [{"token": "t1", "repositories": ["Acme/One"]}]});
        assert_eq!(select_token(&granted, Some("acme/one")), "t1");
        assert_eq!(select_token(&granted, Some("acme/two")), "");
        assert_eq!(select_token(&granted, None), "t0");
        assert_eq!(select_token(&json!({"configured": false, "token": "x"}), None), "");
    }

    #[test]
    fn workflow_commands_pick_the_repository_s_actions_token() {
        let a = |s: &str| s.split(' ').map(String::from).collect::<Vec<_>>();
        let granted = json!({"configured": true, "token": "plain", "tokens": [
            {"token": "plain", "repositories": ["Acme/bot-rm"], "access": "write"},
            {"token": "actions", "repositories": ["Acme/product"], "access": "write", "actions": "write"}]});
        for command in ["workflow run release.yml -R acme/product -f sha=abc", "api -X POST repos/acme/product/actions/workflows/1/dispatches",
                        "run rerun 42 --repo acme/product", "run cancel 42 -R acme/product"] {
            let repo = gh_repository(&a(command), None);
            assert_eq!(select_token(&granted, repo.as_deref()), "actions", "{command}");
        }
        assert_eq!(select_token(&granted, gh_repository(&a("pr list -R acme/bot-rm"), None).as_deref()), "plain");
    }

    #[test]
    fn the_helper_is_a_shell_function_that_only_answers_get() {
        let h = fresh_helper(Path::new("/tmp/s dir/credential.sock"), "ops");
        assert!(h.starts_with("!f() { [ \"$1\" = get ] || exit 0; exec "));
        assert!(h.contains("git-credential --socket '/tmp/s dir/credential.sock' --bot ops; }; f"));
    }
}
