//! GitHub credentials for a turn. This module owns them: the Python turn helper and gh wrapper it replaced are gone.
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
pub const STATIC_HELPER: &str =
    "!f() { echo username=x-access-token; echo \"password=$GH_TOKEN\"; }; f";
const GIT_MARGIN: f64 = 600.0;
const TURN_MARGIN: f64 = 2700.0;
const HOLD_S: f64 = 300.0;

pub fn shell_quote(s: &str) -> String {
    if !s.is_empty()
        && s.chars()
            .all(|c| c.is_ascii_alphanumeric() || "@%+=:,./-_".contains(c))
    {
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
    if min.is_finite() {
        min
    } else {
        0.0
    }
}

/// POST github/token, reusing an answer held while every token in it has the purpose's margin left.
pub struct TokenCache {
    held: Mutex<HashMap<String, (f64, Value)>>,
    legacy_purpose: Mutex<bool>,
}

impl TokenCache {
    pub fn new() -> TokenCache {
        TokenCache {
            held: Mutex::new(HashMap::new()),
            legacy_purpose: Mutex::new(false),
        }
    }

    pub async fn get(&self, client: &Client, body: Value) -> Result<Value, ApiError> {
        let key = body.to_string();
        let margin = if body["purpose"].as_str() == Some("git") {
            GIT_MARGIN
        } else {
            TURN_MARGIN
        };
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
        let until = if granted["token"].as_str().is_some_and(|t| !t.is_empty()) {
            expiry(&stamps)
        } else {
            0.0
        };
        // BotOps' answer depends on the runs live when it was asked; the hub says so with hold=false.
        let holdable = granted
            .get("hold")
            .map(|h| h != &Value::Bool(false))
            .unwrap_or(body["bot"].as_str() != Some("botops"));
        if holdable && until - margin > epoch_s() {
            self.held.lock().unwrap().insert(
                key,
                ((until - margin).min(epoch_s() + HOLD_S), granted.clone()),
            );
        }
        Ok(granted)
    }

    /// The git-purpose token, asking an older server without `purpose` once it refused the field.
    pub async fn git(&self, client: &Client, bot: &str) -> Result<Value, ApiError> {
        if *self.legacy_purpose.lock().unwrap() {
            return self.get(client, json!({"bot": bot})).await;
        }
        match self
            .get(client, json!({"bot": bot, "purpose": "git"}))
            .await
        {
            Err(e) if rejects_purpose(&e) => {
                *self.legacy_purpose.lock().unwrap() = true;
                self.get(client, json!({"bot": bot})).await
            }
            other => other,
        }
    }
}

fn rejects_purpose(e: &ApiError) -> bool {
    if e.status != 422
        || ![
            "validation",
            "validation_error",
            "unknown_field",
            "extra_forbidden",
        ]
        .contains(&e.code.to_lowercase().as_str())
    {
        return false;
    }
    let d = e.detail.to_lowercase();
    d.contains("purpose")
        && [
            "extra input",
            "extra field",
            "unknown field",
            "unexpected field",
            "extra_forbidden",
        ]
        .iter()
        .any(|m| d.contains(m))
}

/// Old servers have one token; new ones have authoritative repository groups.
pub fn select_token(granted: &Value, repository: Option<&str>) -> String {
    if !crate::util::truthy(granted.get("configured")) {
        return String::new();
    }
    if let (Some(repo), Some(groups)) =
        (repository, granted.get("tokens").and_then(|t| t.as_array()))
    {
        for g in groups {
            if g["repositories"].as_array().is_some_and(|names| {
                names
                    .iter()
                    .any(|n| n.as_str().is_some_and(|n| n.eq_ignore_ascii_case(repo)))
            }) {
                return g["token"].as_str().unwrap_or_default().to_string();
            }
        }
        return String::new();
    }
    granted["token"].as_str().unwrap_or_default().to_string()
}

/// Why a configured answer holds no token for `repository` ("" for the bot's own), in plain words, or "" when the
/// answer does not say. The hub withholds a bot's own repository when the App is installed on chosen repositories and
/// not it, or another company made it (backend/repositories.py Homes), and says which in `history`.
pub fn no_token_reason(granted: &Value, repository: Option<&str>) -> String {
    if !crate::util::truthy(granted.get("configured")) {
        return String::new();
    }
    let what = match repository.filter(|r| !r.is_empty()) {
        Some(r) => format!("for {r}"),
        None => "for this bot's repository".to_string(),
    };
    let history = granted["history"].as_str().unwrap_or_default().trim();
    let why = if !history.is_empty() {
        history.to_string()
    } else if let (Some(repo), Some(groups)) = (
        repository.filter(|r| !r.is_empty()),
        granted.get("tokens").and_then(|t| t.as_array()),
    ) {
        let held = groups.iter().any(|g| {
            g["repositories"].as_array().is_some_and(|n| {
                n.iter()
                    .any(|n| n.as_str().is_some_and(|n| n.eq_ignore_ascii_case(repo)))
            })
        });
        if held {
            return String::new();
        }
        "it is outside this bot's repository access".to_string()
    } else {
        return String::new();
    };
    let said: String = why
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(240)
        .collect();
    crate::redact::scrub_credentials(&format!("Tico gives no GitHub token {what}: {said}"), &[])
}

// ----------------------------------------------------------------------------- the socket

struct Registered {
    bot: String,
    issued: Vec<String>,
    redactor: Option<Arc<Redactor>>,
    /// A turn that asks for its GitHub tokens with its own token, and its own cache (register_as).
    own: Option<(Client, Arc<TokenCache>)>,
}

pub struct CredentialSocket {
    pub path: PathBuf,
    dir: Option<PathBuf>,
    attempts: Arc<Mutex<HashMap<String, Registered>>>,
    client: Client,
    cache: Arc<TokenCache>,
}

/// A Gateway turn's own door to the credential socket: in its private directory, owned by its leased user and
/// owner-only, so neither the bot user nor a sibling turn can connect through it. Answers like the shared one.
/// The turn's lease owns it (crate::turn_users::Lease::hold_socket), so it closes before the UID can be lent again.
pub struct TurnSocket {
    pub path: PathBuf,
    task: tokio::task::JoinHandle<()>,
    /// The (dev, ino) bound at `path`: the path is fixed per UID, and a later turn's socket there is not this one's.
    bound: (u64, u64),
}

impl Drop for TurnSocket {
    fn drop(&mut self) {
        use std::os::unix::fs::MetadataExt;
        self.task.abort();
        if std::fs::symlink_metadata(&self.path).is_ok_and(|m| (m.dev(), m.ino()) == self.bound) {
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

fn accept(
    listener: tokio::net::UnixListener,
    shared: Arc<Mutex<HashMap<String, Registered>>>,
    client: Client,
    cache: Arc<TokenCache>,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        loop {
            let Ok((stream, _)) = listener.accept().await else {
                continue;
            };
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
    })
}

impl CredentialSocket {
    /// Without isolation, listen in a private temporary directory.
    /// Under isolation (or with TICO_RUNNER_CRED_SOCKET) it is a fixed path the bot user owns.
    pub fn serve(client: Client, cache: Arc<TokenCache>) -> std::io::Result<CredentialSocket> {
        let named = std::env::var("TICO_RUNNER_CRED_SOCKET")
            .ok()
            .filter(|p| !p.is_empty());
        let (dir, path) = match named {
            None if !crate::isolation::enabled() => {
                let dir = PathBuf::from("/tmp").join(format!(
                    "tico-credentials-{}",
                    uuid::Uuid::new_v4().simple()
                ));
                std::fs::create_dir(&dir)?;
                std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700))?;
                let path = dir.join("credential.sock");
                (Some(dir), path)
            }
            named => {
                let path = PathBuf::from(
                    named.unwrap_or_else(|| "/run/tico-runner/git-credential.sock".into()),
                );
                if let Some(parent) = path.parent() {
                    use std::os::unix::fs::DirBuilderExt;
                    std::fs::DirBuilder::new()
                        .recursive(true)
                        .mode(0o755)
                        .create(parent)?;
                }
                let _ = std::fs::remove_file(&path);
                (None, path)
            }
        };
        let listener = tokio::net::UnixListener::bind(&path)?;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))?;
        crate::isolation::chown(&path);
        let attempts: Arc<Mutex<HashMap<String, Registered>>> =
            Arc::new(Mutex::new(HashMap::new()));
        // The listener task lives as long as the runner.
        drop(accept(
            listener,
            attempts.clone(),
            client.clone(),
            cache.clone(),
        ));
        Ok(CredentialSocket {
            path,
            dir,
            attempts,
            client,
            cache,
        })
    }

    /// `serve` for one leased turn user at `path` (crate::turn_users), and `env` pointed at it instead.
    pub fn serve_for(
        &self,
        env: &mut Env,
        path: &Path,
        owner: (u32, u32),
    ) -> std::io::Result<TurnSocket> {
        let _ = std::fs::remove_file(path);
        let listener = tokio::net::UnixListener::bind(path)?;
        use std::os::unix::fs::MetadataExt;
        let info = std::fs::symlink_metadata(path)?;
        let task = accept(
            listener,
            self.attempts.clone(),
            self.client.clone(),
            self.cache.clone(),
        );
        let turn = TurnSocket {
            path: path.to_path_buf(),
            task,
            bound: (info.dev(), info.ino()),
        };
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
        std::os::unix::fs::lchown(path, Some(owner.0), Some(owner.1))?;
        self.repoint(env, path);
        Ok(turn)
    }

    /// Point a turn's helper and socket variables from this socket to its own `path`.
    pub fn repoint(&self, env: &mut Env, path: &Path) {
        let (shared, own) = (self.path.display().to_string(), path.display().to_string());
        for value in env.values_mut() {
            if value.contains(&shared) {
                *value = value.replace(&shared, &own);
            }
        }
    }

    pub fn register(&self, attempt_token: &str, bot: &str) {
        self.attempts.lock().unwrap().insert(
            attempt_token.into(),
            Registered {
                bot: bot.into(),
                issued: vec![],
                redactor: None,
                own: None,
            },
        );
    }

    /// `register`, for a turn whose GitHub tokens the hub answers by the turn itself, not by this computer: a private
    /// meeting's, which gets read-only ones (backend/github_app.py token). Never a token this computer's cache holds.
    pub fn register_as(&self, attempt_token: &str, bot: &str, own: Client) {
        let entry = Registered {
            bot: bot.into(),
            issued: vec![],
            redactor: None,
            own: Some((own, Arc::new(TokenCache::new()))),
        };
        self.attempts
            .lock()
            .unwrap()
            .insert(attempt_token.into(), entry);
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

async fn answer(
    attempts: &Mutex<HashMap<String, Registered>>,
    client: &Client,
    cache: &TokenCache,
    line: &str,
) -> Value {
    let Ok(asked) = serde_json::from_str::<Value>(line) else {
        return json!({"error": "bad request"});
    };
    let Some(token) = asked
        .get("token")
        .and_then(|t| t.as_str())
        .filter(|t| !t.is_empty())
    else {
        return json!({"error": if asked.is_object() { "unknown attempt" } else { "bad request" }});
    };
    let known = attempts
        .lock()
        .unwrap()
        .get(token)
        .map(|r| (r.bot.clone(), r.own.clone()));
    let Some((bot, own)) = known else {
        return json!({"error": "unknown attempt"});
    };
    let (client, cache) = own
        .as_ref()
        .map_or((client, cache), |(c, k)| (c, k.as_ref()));
    // Mail and calendar run on the server (docs/mail.md): a mail request never falls through to a GitHub token.
    if asked.get("mail").is_some() {
        return json!({"error": "mail access is on the server: use tico email"});
    }
    let repository = match asked.get("repository") {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) => Some(s.clone()),
        Some(_) => return json!({"error": "bad repository"}),
    };
    let purpose = asked
        .get("purpose")
        .and_then(|p| p.as_str())
        .unwrap_or("turn");
    if !(asked.get("purpose").is_none() || ["turn", "git"].contains(&purpose))
        || (asked.get("purpose").is_some() && asked["purpose"].as_str().is_none())
    {
        return json!({"error": "bad purpose"});
    }
    let granted = if purpose == "git" {
        cache.git(client, &bot).await
    } else {
        cache.get(client, json!({"bot": bot})).await
    };
    let mut why = String::new();
    let granted = match granted {
        Ok(g) => {
            let wanted = repository.as_deref().filter(|r| !r.is_empty());
            let token = select_token(&g, wanted);
            if token.is_empty() {
                why = no_token_reason(&g, wanted);
            }
            token
        }
        Err(e) if e.status != 0 => {
            return json!({"error": format!("APIError: {}", token_problem(&e, repository.as_deref().unwrap_or("")))})
        }
        Err(_) => return json!({"error": "APIError"}),
    };
    if asked
        .get("refresh")
        .is_some_and(|r| crate::util::truthy(Some(r)))
    {
        // A bounded mirror refresh for a turn that may read the repository; the token never leaves this process.
        let (Some(repository), Some(mirrors)) = (
            repository.filter(|r| !r.is_empty()),
            crate::repositories::MIRRORS.get(),
        ) else {
            return json!({"error": "mirror refresh unavailable"});
        };
        if granted.is_empty() {
            return json!({"error": "mirror refresh unavailable"});
        }
        return mirrors
            .refresh_mirror(&repository, &granted)
            .await
            .unwrap_or_else(|_| json!({"error": "ValueError"}));
    }
    if granted.is_empty() {
        return json!({"error": if why.is_empty() { "no token".to_string() } else { why }});
    }
    let mut map = attempts.lock().unwrap();
    let Some(entry) = map.get_mut(token) else {
        return json!({"error": "unknown attempt"});
    };
    match &entry.redactor {
        Some(r) => r.add(std::slice::from_ref(&granted)),
        None => entry.issued.push(granted.clone()),
    }
    json!({"token": granted})
}

/// Why Tico could not get a GitHub token, in plain words.
pub fn token_problem(e: &ApiError, repository: &str) -> String {
    let what = if repository.is_empty() {
        "for this bot's repositories".to_string()
    } else {
        format!("for {repository}")
    };
    // Scrubbed whole before it is cut: a token cut in half no longer looks like one.
    let detail = crate::redact::scrub_credentials(
        &e.detail.split_whitespace().collect::<Vec<_>>().join(" "),
        &[],
    );
    let detail: String = detail.chars().take(200).collect();
    let why = if e.code == "github_repo_not_accessible"
        || (e.status == 409 && detail.contains("not reachable"))
    {
        "the GitHub App is not installed on it or cannot reach it; add it to the App's repository access on GitHub, then check Settings > Tools > GitHub".to_string()
    } else if e.status == 403 && detail.contains("organization") {
        format!("it is outside the connected GitHub organization ({detail})")
    } else if e.status != 0 {
        if detail.is_empty() {
            format!(
                "the server answered {} ({})",
                e.status,
                if e.code.is_empty() {
                    "no detail"
                } else {
                    &e.code
                }
            )
        } else {
            detail
        }
    } else {
        "the server did not answer (APIError)".to_string()
    };
    crate::redact::scrub_credentials(
        &format!("Tico could not get a GitHub token {what}: {why}"),
        &[],
    )
}

// ----------------------------------------------------------------------------- the turn's environment

pub(crate) fn git_environment(env: &mut Env, token: &str, helper: &str) {
    for (k, v) in [
        ("GH_TOKEN", token),
        ("GITHUB_TOKEN", token),
        ("GIT_TERMINAL_PROMPT", "0"),
        ("GIT_CONFIG_COUNT", "3"),
        ("GIT_CONFIG_KEY_0", KEY),
        ("GIT_CONFIG_VALUE_0", ""),
        ("GIT_CONFIG_KEY_1", KEY),
        ("GIT_CONFIG_VALUE_1", helper),
        (
            "GIT_CONFIG_KEY_2",
            "credential.https://github.com.useHttpPath",
        ),
        ("GIT_CONFIG_VALUE_2", "true"),
    ] {
        env.insert(k.into(), v.into());
    }
}

/// A turn's git credentials from `granted` (`apply_granted`), with the credential socket when it has one. A personal
/// claim (crate::sessions::PersonalCopy) gets none: `env` is left with nothing to sign in with, and the runner's grant
/// goes only into the environment returned, which refreshes the shared checkout before the turn's copy is made.
pub fn for_turn(
    env: &mut Env,
    granted: Result<Value, ApiError>,
    bot: &str,
    socket: Option<&Path>,
    state_dir: &Path,
    personal: bool,
) -> Option<Env> {
    if !personal {
        apply_granted(env, granted, bot, socket, state_dir);
        return None;
    }
    let mut refresh = env.clone();
    apply_granted(&mut refresh, granted, bot, None, state_dir);
    without_credentials(env);
    Some(refresh)
}

/// A turn that must push nothing (a person's own Assistant chat, crate::sessions::PersonalCopy): no GitHub token, no
/// credential socket, no SSH agent or askpass program, no system git config and no credential helper (an empty
/// `credential.helper` read last clears every helper the user's own config names), so git and gh have nothing to sign in
/// with. Keys left on disk under the user's HOME are beyond what an environment can take away.
pub fn without_credentials(env: &mut Env) {
    let dropped: Vec<String> = env
        .keys()
        .filter(|k| {
            k.starts_with("GH_")
                || k.starts_with("GITHUB_")
                || k.starts_with("TICO_GITHUB_")
                || [
                    SOCKET_ENV,
                    "SSH_AUTH_SOCK",
                    "GIT_ASKPASS",
                    "SSH_ASKPASS",
                    "GIT_SSH",
                    "GIT_SSH_COMMAND",
                ]
                .contains(&k.as_str())
        })
        .cloned()
        .collect();
    for key in dropped {
        env.remove(&key);
    }
    let count: usize = env
        .get("GIT_CONFIG_COUNT")
        .and_then(|c| c.parse().ok())
        .unwrap_or(0);
    for i in 0..count {
        let helper = env
            .get(&format!("GIT_CONFIG_KEY_{i}"))
            .is_some_and(|k| k.starts_with("credential.") && k.ends_with(".helper"));
        if helper {
            env.insert(format!("GIT_CONFIG_VALUE_{i}"), String::new());
        }
    }
    push_config(env, "credential.helper", "");
    env.insert("GIT_CONFIG_NOSYSTEM".into(), "1".into());
    env.insert("GIT_TERMINAL_PROMPT".into(), "0".into());
}

/// Git refuses a work tree another user owns. A Gateway turn's leased user works in the bot's checkout, so the
/// turn trusts exactly these paths (never `*`), after the entries `git_environment` set.
pub fn trust_directories(env: &mut Env, paths: &[PathBuf]) {
    for path in paths {
        add_config(env, "safe.directory", &path.display().to_string());
    }
}

/// One more command-line config entry (GIT_CONFIG_KEY_n/VALUE_n), unless it is already there.
pub fn add_config(env: &mut Env, key: &str, value: &str) {
    let count: usize = env
        .get("GIT_CONFIG_COUNT")
        .and_then(|c| c.parse().ok())
        .unwrap_or(0);
    let present = (0..count).any(|i| {
        env.get(&format!("GIT_CONFIG_KEY_{i}")).map(String::as_str) == Some(key)
            && env
                .get(&format!("GIT_CONFIG_VALUE_{i}"))
                .map(String::as_str)
                == Some(value)
    });
    if value.is_empty() || present {
        return;
    }
    env.insert(format!("GIT_CONFIG_KEY_{count}"), key.into());
    env.insert(format!("GIT_CONFIG_VALUE_{count}"), value.into());
    env.insert("GIT_CONFIG_COUNT".into(), (count + 1).to_string());
}

/// One more command-line config entry, even an empty one: for a key git reads as a list (`credential.helper`,
/// `http.extraHeader`) an empty value clears what came before it, and for most others it means "none".
pub fn push_config(env: &mut Env, key: &str, value: &str) {
    let count: usize = env
        .get("GIT_CONFIG_COUNT")
        .and_then(|c| c.parse().ok())
        .unwrap_or(0);
    env.insert(format!("GIT_CONFIG_KEY_{count}"), key.into());
    env.insert(format!("GIT_CONFIG_VALUE_{count}"), value.into());
    env.insert("GIT_CONFIG_COUNT".into(), (count + 1).to_string());
}

/// The helper git runs on every `get`: this binary asking the socket.
pub fn fresh_helper(socket: &Path, bot: &str) -> String {
    let exe = std::env::current_exe()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|_| "tico-runner".into());
    let run = [
        exe.as_str(),
        "git-credential",
        "--socket",
        &socket.display().to_string(),
        "--bot",
        bot,
    ]
    .iter()
    .map(|p| shell_quote(p))
    .collect::<Vec<_>>()
    .join(" ");
    format!("!f() {{ [ \"$1\" = get ] || exit 0; exec {run}; }}; f")
}

fn failed_helper(reason: &str) -> String {
    let reason: String = reason
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(300)
        .collect();
    if reason.is_empty() {
        return FAILED_HELPER.into();
    }
    format!(
        "!f() {{ echo {} >&2; echo quit=true; }}; f",
        shell_quote(&reason)
    )
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
pub async fn apply(
    env: &mut Env,
    client: &Client,
    cache: &TokenCache,
    bot: &str,
    socket: Option<&Path>,
    state_dir: &Path,
) -> bool {
    let granted = cache.get(client, json!({"bot": bot})).await;
    apply_granted(env, granted, bot, socket, state_dir)
}

/// `apply` with the token already asked for (`TokenCache::get` with `{"bot": bot}`), so the ask can run beside a
/// turn's other preparation.
pub fn apply_granted(
    env: &mut Env,
    granted: Result<Value, ApiError>,
    bot: &str,
    socket: Option<&Path>,
    state_dir: &Path,
) -> bool {
    let mut reason = String::new();
    let granted = match granted {
        Ok(g) => {
            if g["token"].as_str().unwrap_or_default().is_empty() {
                reason = no_token_reason(&g, None);
            }
            g
        }
        Err(e) => {
            reason = token_problem(&e, "");
            log(&format!(
                "Tico runner: {bot}: GitHub App token unavailable (APIError): {reason}"
            ));
            json!({"configured": true})
        }
    };
    if !crate::util::truthy(granted.get("configured")) {
        return false;
    }
    let token = granted["token"].as_str().unwrap_or_default().to_string();
    let applied = !token.is_empty();
    let stored = env
        .get("GH_TOKEN")
        .or_else(|| env.get("GITHUB_TOKEN"))
        .cloned()
        .unwrap_or_default();
    if applied
        && !stored.is_empty()
        && stored != token
        && crate::util::truthy(granted.get("repository"))
    {
        env.insert(OTHER_TOKEN_KEY.into(), stored);
    }
    let has_tokens = granted.get("tokens").is_some();
    let helper = match socket {
        Some(s) => fresh_helper(s, bot),
        None if has_tokens => fresh_helper(Path::new("/nonexistent"), bot),
        None => STATIC_HELPER.into(),
    };
    git_environment(
        env,
        if applied { &token } else { "" },
        &if applied {
            helper
        } else {
            failed_helper(&reason)
        },
    );
    if has_tokens {
        let tokens = if applied {
            granted["tokens"].clone()
        } else {
            json!([])
        };
        env.insert(TOKENS_KEY.into(), tokens.to_string());
        let path = env.get("PATH").cloned().unwrap_or_default();
        if let (Some(gh), Some(dir)) = (crate::env::which("gh", &path), shim_dir(state_dir)) {
            env.insert("TICO_GITHUB_GH".into(), gh.display().to_string());
            env.insert("TICO_GITHUB_BOT".into(), bot.into());
            let turn_path = format!("{}:{path}", dir.display());
            env.insert("PATH".into(), turn_path.clone());
            env.insert(TURN_PATH_KEY.into(), turn_path);
            if cfg!(target_os = "macos") {
                let startup = crate::env::root().join("runner_support").join("shell_init");
                env.insert(
                    "TICO_TURN_BASH_ENV".into(),
                    env.get("BASH_ENV").cloned().unwrap_or_default(),
                );
                let zdot = env
                    .get("ZDOTDIR")
                    .or_else(|| env.get("HOME"))
                    .cloned()
                    .unwrap_or_default();
                env.insert("TICO_TURN_ZDOTDIR".into(), zdot);
                env.insert(
                    "BASH_ENV".into(),
                    startup.join("bash-env").display().to_string(),
                );
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
    for prefix in [
        "https://github.com/",
        "http://github.com/",
        "git@github.com:",
    ] {
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
    let ok = |s: &str| {
        !s.is_empty()
            && s.chars()
                .all(|c| c.is_alphanumeric() || c == '_' || c == '.' || c == '-')
    };
    (ok(parts[0]) && ok(name)).then(|| format!("{}/{}", parts[0], name))
}

fn held_token(repository: Option<&str>) -> String {
    let groups: Value = std::env::var(TOKENS_KEY)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or(json!([]));
    for g in groups.as_array().into_iter().flatten() {
        let matches = match repository {
            None => true,
            Some(r) => g["repositories"].as_array().is_some_and(|n| {
                n.iter()
                    .any(|x| x.as_str().is_some_and(|x| x.eq_ignore_ascii_case(r)))
            }),
        };
        if matches {
            let fresh = expiry(&[&g["expires_at"]]) - GIT_MARGIN > epoch_s();
            return if fresh {
                g["token"].as_str().unwrap_or_default().to_string()
            } else {
                String::new()
            };
        }
    }
    String::new()
}

fn outside_token(repository: Option<&str>) -> String {
    let org = std::env::var(REPOSITORY_KEY)
        .unwrap_or_default()
        .split('/')
        .next()
        .unwrap_or_default()
        .to_lowercase();
    let owner = repository
        .unwrap_or_default()
        .split('/')
        .next()
        .unwrap_or_default()
        .to_lowercase();
    if !org.is_empty() && !owner.is_empty() && owner != org {
        std::env::var(OTHER_TOKEN_KEY).unwrap_or_default()
    } else {
        String::new()
    }
}

/// One request to the credential socket; Err(reason) on any refusal.
pub fn socket_request(
    path: &Path,
    attempt_token: &str,
    repository: Option<&str>,
    purpose: &str,
) -> Result<String, String> {
    let mut stream = std::os::unix::net::UnixStream::connect(path).map_err(|e| e.to_string())?;
    stream.set_read_timeout(Some(Duration::from_secs(15))).ok();
    let mut asked = json!({"token": attempt_token});
    if let Some(r) = repository {
        asked["repository"] = json!(r);
    }
    if purpose != "turn" {
        asked["purpose"] = json!(purpose);
    }
    stream
        .write_all(format!("{asked}\n").as_bytes())
        .map_err(|e| e.to_string())?;
    let mut line = String::new();
    BufReader::new(stream.take(MAX_LINE as u64))
        .read_line(&mut line)
        .map_err(|e| e.to_string())?;
    let reply: Value = serde_json::from_str(&line).map_err(|e| e.to_string())?;
    match reply["token"].as_str().filter(|t| !t.is_empty()) {
        Some(t) => Ok(t.to_string()),
        None => Err(reply["error"].as_str().unwrap_or("refused").to_string()),
    }
}

/// The token for `repository`: the bot's own for outside the App's organization, the turn's held one while
/// it has ten minutes left, else a fresh one from the socket, else the turn's start-of-turn token. Err holds the
/// socket's refusal ("" when there is none to tell), for the line git or gh shows the bot.
pub fn credential(socket: Option<&str>, repository: Option<&str>) -> Result<String, String> {
    let other = outside_token(repository);
    if !other.is_empty() {
        return Ok(other);
    }
    let held = held_token(repository);
    if !held.is_empty() {
        return Ok(held);
    }
    let mut refused = String::new();
    if let Some(s) = socket.filter(|s| !s.is_empty()) {
        match socket_request(
            Path::new(s),
            &crate::client::bearer(&std::env::var("HUB_TOKEN").unwrap_or_default())
                .unwrap_or_default(),
            repository,
            "git",
        ) {
            Ok(t) => return Ok(t),
            Err(e) => refused = e,
        }
    }
    let mut granted =
        json!({"configured": true, "token": std::env::var("GH_TOKEN").unwrap_or_default()});
    if let Ok(t) = std::env::var(TOKENS_KEY) {
        granted["tokens"] = serde_json::from_str(&t).unwrap_or(json!([]));
    }
    let token = select_token(&granted, repository);
    if token.is_empty() {
        Err(refused)
    } else {
        Ok(token)
    }
}

/// The line git or gh shows when there is no token: the hub's reason when it gave one, else where to look.
pub fn missing_token_line(refused: &str) -> String {
    let refused: String = refused
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(300)
        .collect();
    if refused.is_empty() || refused == "no token" {
        "Tico runner: no GitHub App token for this repository; check Settings > Tools > GitHub"
            .to_string()
    } else {
        format!("Tico runner: no GitHub App token for this repository: {refused}")
    }
}

/// `tico-runner git-credential --socket S --bot B`: git's credential helper protocol on stdin/stdout.
pub fn git_credential_main(socket: Option<String>) -> i32 {
    let mut input = String::new();
    let _ = std::io::stdin().read_to_string(&mut input);
    let wanted: HashMap<String, String> = input
        .lines()
        .filter_map(|l| l.split_once('='))
        .map(|(k, v)| (k.into(), v.into()))
        .collect();
    let host = std::env::var("TICO_GITHUB_HOST")
        .ok()
        .filter(|h| !h.is_empty())
        .unwrap_or_else(|| "github.com".into());
    if wanted.get("host") != Some(&host) || wanted.get("protocol").is_some_and(|p| p != "https") {
        return 0;
    }
    let repo = wanted.get("path").and_then(|p| repository_name(p));
    match credential(socket.as_deref(), repo.as_deref()) {
        Ok(token) => print!("username=x-access-token\npassword={token}\n"),
        Err(refused) => {
            eprintln!("{}", missing_token_line(&refused));
            println!("quit=true");
        }
    }
    0
}

/// Which repository a `gh` command is about (`git_credentials.gh_repository`).
pub fn gh_repository(argv: &[String], gh_repo: Option<&str>) -> Option<String> {
    const VALUED: &[&str] = &[
        "--body",
        "-b",
        "--title",
        "-t",
        "--body-file",
        "-F",
        "--field",
        "-f",
        "--raw-field",
        "--header",
        "-H",
        "--comment",
        "--notes",
        "-m",
        "--message",
        "--jq",
        "-q",
        "--search",
        "--template",
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
    if argv.len() > 2
        && argv[0] == "repo"
        && ["view", "clone", "fork"].contains(&argv[1].as_str())
        && !argv[2].starts_with('-')
    {
        return repository_name(&argv[2]);
    }
    if let Some(r) = gh_repo.filter(|r| !r.is_empty()) {
        return repository_name(r);
    }
    let out = std::process::Command::new("git")
        .args(["config", "--get", "remote.origin.url"])
        .output()
        .ok()?;
    out.status
        .success()
        .then(|| repository_name(&String::from_utf8_lossy(&out.stdout)))
        .flatten()
}

/// The `gh` shim: pick the token for the command's repository, then become the real `gh`.
pub fn gh_main(args: Vec<String>) -> i32 {
    use std::os::unix::process::CommandExt;
    let repo = gh_repository(&args, std::env::var("GH_REPO").ok().as_deref());
    let token = match credential(std::env::var(SOCKET_ENV).ok().as_deref(), repo.as_deref()) {
        Ok(token) => token,
        Err(refused) => {
            eprintln!("{}", missing_token_line(&refused));
            return 1;
        }
    };
    let Ok(gh) = std::env::var("TICO_GITHUB_GH") else {
        eprintln!("Tico runner: gh is not installed on this computer");
        return 1;
    };
    let err = std::process::Command::new(&gh)
        .args(&args)
        .env("GH_TOKEN", &token)
        .env("GITHUB_TOKEN", &token)
        .exec();
    eprintln!("Tico runner: could not run {gh}: {err}");
    127
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gh_finds_the_repository_the_way_the_python_shim_does() {
        let a = |s: &str| s.split(' ').map(String::from).collect::<Vec<_>>();
        assert_eq!(
            gh_repository(&a("pr list -R acme/web"), None).as_deref(),
            Some("acme/web")
        );
        assert_eq!(
            gh_repository(&a("pr create --title acme/x --repo=acme/api"), None).as_deref(),
            Some("acme/api")
        );
        assert_eq!(
            gh_repository(&a("api repos/acme/site/pulls"), None).as_deref(),
            Some("acme/site")
        );
        assert_eq!(
            gh_repository(&a("pr view https://github.com/acme/app/pull/3"), None).as_deref(),
            Some("acme/app")
        );
        assert_eq!(
            gh_repository(&a("repo clone acme/tools"), None).as_deref(),
            Some("acme/tools")
        );
        assert_eq!(
            gh_repository(&a("pr list"), Some("acme/fromenv")).as_deref(),
            Some("acme/fromenv")
        );
        assert_eq!(
            repository_name("git@github.com:acme/bot-ops.git").as_deref(),
            Some("acme/bot-ops")
        );
    }

    #[test]
    fn a_token_is_selected_by_its_repository_group() {
        let granted = json!({"configured": true, "token": "t0", "tokens": [{"token": "t1", "repositories": ["Acme/One"]}]});
        assert_eq!(select_token(&granted, Some("acme/one")), "t1");
        assert_eq!(select_token(&granted, Some("acme/two")), "");
        assert_eq!(select_token(&granted, None), "t0");
        assert_eq!(
            select_token(&json!({"configured": false, "token": "x"}), None),
            ""
        );
    }

    /// GitHub's stateless installation token (`ghs_<app id>_<JWT>`, ~520 characters with dots and dashes; the
    /// default since Oct 2026), synthetic. backend/tests/helpers/github_app.py stateless_token makes the same shape.
    fn stateless_token() -> String {
        format!(
            "ghs_4242_eyJhbGciOiJSUzI1NiJ9.eyJpaWQiOjc3LCJub25jZSI6InN5bnRoZXRpYyJ9.{}",
            "S1g-n_aTuRe".repeat(40)
        )
    }

    #[test]
    fn a_stateless_token_reaches_git_exactly_as_the_socket_answers_it() {
        let token = stateless_token();
        let dir =
            PathBuf::from("/tmp").join(format!("tico-cred-fmt-{}", uuid::Uuid::new_v4().simple())); // short
        std::fs::create_dir(&dir).unwrap();
        let path = dir.join("git-credential.sock");
        let listener = std::os::unix::net::UnixListener::bind(&path).unwrap();
        let answer = token.clone();
        let served = std::thread::spawn(move || {
            let (stream, _) = listener.accept().unwrap();
            let mut asked = String::new();
            BufReader::new(&stream).read_line(&mut asked).unwrap();
            (&stream)
                .write_all(format!("{}\n", json!({ "token": answer })).as_bytes())
                .unwrap();
            asked
        });
        let got = socket_request(&path, "attempt-1", Some("Acme/emp-ops"), "git").unwrap();
        let asked: Value = serde_json::from_str(&served.join().unwrap()).unwrap();
        assert_eq!(
            asked,
            json!({"token": "attempt-1", "repository": "Acme/emp-ops", "purpose": "git"})
        );
        assert_eq!(
            got, token,
            "the token git is given is the one the socket answered"
        );
        let granted = json!({"configured": true, "token": token, "tokens": [{"token": token, "repositories": ["Acme/emp-ops"]}]});
        assert_eq!(select_token(&granted, Some("acme/emp-ops")), token);
        assert_eq!(select_token(&granted, None), token);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn a_turn_user_trusts_only_its_checkout_and_owns_its_own_credential_socket() {
        let dir =
            PathBuf::from("/tmp").join(format!("tico-turn-cred-{}", uuid::Uuid::new_v4().simple())); // socket paths are short
        std::fs::create_dir(&dir).unwrap();
        let client = Client::new("http://127.0.0.1:9", "synthetic", 5, 0).unwrap();
        let shared = CredentialSocket {
            path: dir.join("shared.sock"),
            dir: None,
            attempts: Default::default(),
            client,
            cache: Arc::new(TokenCache::new()),
        };
        let mut env = Env::new();
        git_environment(&mut env, "t", &fresh_helper(&shared.path, "ops"));
        env.insert(SOCKET_ENV.into(), shared.path.display().to_string());
        let (main, worktree) = (
            PathBuf::from("/home/runner/workspace/bot-ops"),
            PathBuf::from("/home/runner/worktrees/t1"),
        );
        trust_directories(&mut env, &[main.clone(), worktree.clone(), main.clone()]);
        assert_eq!(env["GIT_CONFIG_COUNT"], "5");
        assert_eq!(
            (
                env["GIT_CONFIG_KEY_3"].as_str(),
                env["GIT_CONFIG_VALUE_3"].as_str()
            ),
            ("safe.directory", "/home/runner/workspace/bot-ops")
        );
        assert_eq!(env["GIT_CONFIG_VALUE_4"], "/home/runner/worktrees/t1");
        assert!(!env.values().any(|v| v == "*"));
        // This test's own IDs stand in for the leased user: the runner chowns the socket to it, owner-only.
        let owner = unsafe { (libc::geteuid(), libc::getegid()) };
        let own = dir.join("turn.sock");
        let turn = shared.serve_for(&mut env, &own, owner).unwrap();
        let info = std::fs::symlink_metadata(&own).unwrap();
        use std::os::unix::fs::MetadataExt;
        assert_eq!(
            (info.uid(), info.gid(), info.mode() & 0o777),
            (owner.0, owner.1, 0o600)
        );
        assert_eq!(env[SOCKET_ENV], own.display().to_string());
        assert!(
            env["GIT_CONFIG_VALUE_1"].contains(&own.display().to_string())
                && !env.values().any(|v| v.contains("shared.sock"))
        );
        let reply = tokio::task::spawn_blocking(move || {
            let mut stream = std::os::unix::net::UnixStream::connect(&own).unwrap();
            stream.write_all(b"not json\n").unwrap();
            let mut line = String::new();
            BufReader::new(stream).read_line(&mut line).unwrap();
            line
        })
        .await
        .unwrap();
        assert!(reply.contains("bad request"), "{reply}");
        drop(turn);
        assert!(!dir.join("turn.sock").exists());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[tokio::test]
    async fn a_finished_turn_leaves_the_next_borrowers_socket_at_the_same_path() {
        let dir = PathBuf::from("/tmp")
            .join(format!("tico-turn-reuse-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir(&dir).unwrap();
        let client = Client::new("http://127.0.0.1:9", "synthetic", 5, 0).unwrap();
        let shared = CredentialSocket {
            path: dir.join("shared.sock"),
            dir: None,
            attempts: Default::default(),
            client,
            cache: Arc::new(TokenCache::new()),
        };
        let owner = unsafe { (libc::geteuid(), libc::getegid()) };
        // The path is fixed per UID: the next turn on the same UID binds it while the old one is still being dropped.
        let own = dir.join("git-credential.sock");
        let old = shared.serve_for(&mut Env::new(), &own, owner).unwrap();
        let new = shared.serve_for(&mut Env::new(), &own, owner).unwrap();
        drop(old);
        let path = own.clone();
        let reply = tokio::task::spawn_blocking(move || {
            let mut stream = std::os::unix::net::UnixStream::connect(&path).unwrap();
            stream.write_all(b"not json\n").unwrap();
            let mut line = String::new();
            BufReader::new(stream).read_line(&mut line).unwrap();
            line
        })
        .await
        .unwrap();
        assert!(reply.contains("bad request"), "{reply}");
        drop(new);
        assert!(!own.exists());
        std::fs::remove_dir_all(dir).unwrap();
    }

    /// A hub that answers each POST github/token with `answer` (HTTP 200), however often it is asked.
    async fn token_hub(answer: Value) -> Client {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        tokio::spawn(async move {
            while let Ok((mut sock, _)) = listener.accept().await {
                let mut got = Vec::new();
                let mut buf = vec![0u8; 8192];
                loop {
                    let n = sock.read(&mut buf).await.unwrap_or(0);
                    got.extend_from_slice(&buf[..n]);
                    let text = String::from_utf8_lossy(&got).to_string();
                    let done = text.find("\r\n\r\n").is_some_and(|end| {
                        let length = text[..end]
                            .lines()
                            .find_map(|l| {
                                l.to_ascii_lowercase()
                                    .strip_prefix("content-length:")
                                    .map(|v| v.trim().parse::<usize>().unwrap_or(0))
                            })
                            .unwrap_or(0);
                        got.len() >= end + 4 + length
                    });
                    if done || n == 0 {
                        break;
                    }
                }
                let body = answer.to_string();
                let reply = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = sock.write_all(reply.as_bytes()).await;
                let _ = sock.shutdown().await;
            }
        });
        Client::new(&format!("http://127.0.0.1:{port}"), "synthetic", 5, 0).unwrap()
    }

    const TAKEN: &str = "Bot history is not backed up to GitHub: a repository with the bot's name was already there, \
                         made by someone else, so Tico leaves it alone";

    fn withheld() -> Value {
        // github_app.py token: the bot's own repository withheld (repositories.Homes) and nothing else granted.
        json!({"configured": true, "token": "", "expires_at": null, "repository": "", "repositories": [],
               "tokens": [], "hold": true, "history": TAKEN})
    }

    #[test]
    fn an_empty_answer_says_why_when_the_hub_does() {
        let said = no_token_reason(&withheld(), None);
        assert!(
            said.starts_with("Tico gives no GitHub token for this bot's repository: Bot history is not backed up")
                && said.contains("made by someone else"),
            "{said}"
        );
        assert!(no_token_reason(&withheld(), Some("Acme/emp-legal"))
            .contains("for Acme/emp-legal: Bot history"));
        // A repository the answer has no token group for, with no history to tell.
        let granted = json!({"configured": true, "token": "t", "tokens": [{"token": "t", "repositories": ["Acme/emp-ops"]}]});
        assert_eq!(
            no_token_reason(&granted, Some("Acme/other")),
            "Tico gives no GitHub token for Acme/other: it is outside this bot's repository access"
        );
        assert_eq!(no_token_reason(&granted, Some("acme/emp-ops")), "");
        assert_eq!(no_token_reason(&json!({"configured": false}), None), "");
        assert_eq!(
            no_token_reason(&json!({"configured": true, "token": ""}), None),
            ""
        );
    }

    #[test]
    fn git_and_gh_show_the_hubs_reason_or_where_to_look() {
        assert_eq!(
            missing_token_line(""),
            "Tico runner: no GitHub App token for this repository; check Settings > Tools > GitHub"
        );
        assert_eq!(missing_token_line("no token"), missing_token_line(""));
        let said = missing_token_line(&no_token_reason(&withheld(), Some("Acme/emp-legal")));
        assert!(
            said.starts_with("Tico runner: no GitHub App token for this repository: Tico gives no GitHub token for Acme/emp-legal: Bot history")
                && !said.contains("Settings > Tools"),
            "{said}"
        );
    }

    #[tokio::test]
    async fn a_withheld_repository_gives_git_the_hubs_reason_not_token_unavailable() {
        let client = token_hub(withheld()).await;
        let dir =
            PathBuf::from("/tmp").join(format!("tico-withheld-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir(&dir).unwrap();
        // The start-of-turn helper: what `git push` runs when the turn has no token.
        let mut env = Env::new();
        let cache = TokenCache::new();
        assert!(!apply(&mut env, &client, &cache, "legal-owner", None, &dir).await);
        let helper = env
            .iter()
            .find(|(k, v)| k.starts_with("GIT_CONFIG_VALUE_") && v.contains("quit=true"))
            .map(|(_, v)| v.clone())
            .expect("a failed helper");
        assert_ne!(
            helper, FAILED_HELPER,
            "the generic 'token unavailable' hides the hub's reason"
        );
        assert!(
            helper.contains("made by someone else, so Tico leaves it alone"),
            "{helper}"
        );
        // The credential socket's answer for a git call later in the turn says the same.
        let attempts = Mutex::new(HashMap::from([(
            "attempt-1".to_string(),
            Registered {
                bot: "legal-owner".into(),
                issued: vec![],
                redactor: None,
                own: None,
            },
        )]));
        let reply = answer(
            &attempts,
            &client,
            &TokenCache::new(),
            &json!({"token": "attempt-1", "repository": "Acme/emp-legal", "purpose": "git"})
                .to_string(),
        )
        .await;
        let error = reply["error"].as_str().unwrap_or_default();
        assert!(
            error.starts_with(
                "Tico gives no GitHub token for Acme/emp-legal: Bot history is not backed up"
            ),
            "{reply}"
        );
        std::fs::remove_dir_all(dir).unwrap();
    }
}
