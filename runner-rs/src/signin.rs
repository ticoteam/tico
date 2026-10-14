//! Headless model sign-in (runner/service.py `headless_secret`, `team_model_keys`, `team_key_only`,
//! `codex_key_login`): the API key or long-lived token that signs a runtime in without a browser, from this
//! process's environment, `secrets/_shared.env`, or the key the company gave every computer, which the runner
//! fetches into `secrets/_team_model.env` when this computer has none of its own.

use std::collections::HashMap;
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::client::Client;
use crate::env::Env;
use crate::util::{log, text_of};

pub const TEAM_KEYS_FILE: &str = "_team_model.env";
const TEAM_KEY_RETRY: Duration = Duration::from_secs(300);
const CODEX_LOGIN_RETRY: Duration = Duration::from_secs(600);
/// How long a refused key or sign-in is trusted before one turn tries again (service.py REJECT_RECHECK_S).
pub const REJECT_RECHECK: Duration = Duration::from_secs(300);

/// A provider's refusal of this computer's key or sign-in on a real turn.
#[derive(Clone, Debug)]
pub struct Rejection {
    pub at: String,
    pub mono: Instant,
    pub reason: String,
}

pub fn headless_names(runtime: &str) -> &'static [&'static str] {
    match runtime {
        "claude" => &["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"],
        "cursor" => &["CURSOR_API_KEY"],
        "codex" => &["OPENAI_API_KEY"],
        "musecode" => &["META_API_KEY"],
        _ => &[],
    }
}

/// `KEY=value` lines (service.py `_read_env`); a missing file is empty.
pub fn read_env(path: &Path) -> Env {
    let mut values = Env::new();
    if let Ok(text) = std::fs::read_to_string(path) {
        for line in text.lines() {
            if line.contains('=') && !line.trim_start().starts_with('#') {
                let (k, v) = line.split_once('=').unwrap_or_default();
                values.insert(k.trim().into(), v.trim().into());
            }
        }
    }
    values
}

pub struct SignIn {
    secrets: PathBuf,
    asked: Mutex<HashMap<String, Instant>>,
    codex_tried: Mutex<HashMap<(String, u64), Instant>>,
    rejected: Mutex<HashMap<(String, String, String), Rejection>>,
    /// Set when a rejection is recorded or cleared: the loop sends a heartbeat at once.
    dirty: std::sync::atomic::AtomicBool,
}

impl SignIn {
    pub fn new(projects_dir: &Path) -> SignIn {
        SignIn { secrets: projects_dir.join("secrets"), asked: Mutex::new(HashMap::new()), codex_tried: Mutex::new(HashMap::new()), rejected: Mutex::new(HashMap::new()), dirty: Default::default() }
    }

    /// Changes when a key or sign-in might have (service.py credential_fingerprint); never holds a secret.
    pub fn credential_fingerprint(&self, profile: Option<&crate::profiles::Profile>) -> String {
        use std::os::unix::fs::MetadataExt;
        let mut env: Vec<(String, String)> = std::env::vars().filter(|(k, _)| k.contains("API_KEY") || k.contains("OAUTH_TOKEN")).collect();
        env.sort();
        let mut seen: Vec<String> = env.into_iter().map(|(k, v)| format!("{k}={v}")).collect();
        let home = PathBuf::from(std::env::var("HOME").unwrap_or_default());
        let codex = std::env::var("CODEX_HOME").map(PathBuf::from).unwrap_or_else(|_| home.join(".codex"));
        let mut files = vec![codex.join("auth.json"), home.join(".claude.json"), home.join(".claude/.credentials.json")];
        let mut secrets: Vec<PathBuf> = std::fs::read_dir(&self.secrets)
            .map(|d| d.flatten().map(|e| e.path()).filter(|p| p.extension().is_some_and(|x| x == "env")).collect())
            .unwrap_or_default();
        secrets.sort();
        files.extend(secrets);
        files.push(crate::hosts::musecode::config_home(None).join("muse").join("auth.json"));
        if let Some(p) = profile {
            seen.push(format!("{}:{}", p.directory.display(), p.share_operator));
            files = vec![
                p.directory.join("codex/auth.json"),
                p.directory.join("claude/.claude.json"),
                p.directory.join("claude/.claude/.credentials.json"),
                p.directory.join("musecode/muse/auth.json"),
            ];
        }
        for path in files {
            if let Ok(m) = std::fs::metadata(&path) {
                seen.push(format!("{}:{}:{}:{}", path.display(), m.mtime(), m.mtime_nsec(), m.size()));
            }
        }
        crate::profiles::sha256_hex(seen.join("\n").as_bytes())
    }

    /// The live refusal of `runtime` on `profile` while its credentials are unchanged, or None.
    pub fn rejection(&self, runtime: &str, profile: Option<&crate::profiles::Profile>) -> Option<Rejection> {
        let key = (runtime.to_string(), profile.map(|p| p.name.clone()).unwrap_or_default(), self.credential_fingerprint(profile));
        let mut rows = self.rejected.lock().unwrap();
        if rows.get(&key).is_some_and(|r| r.mono.elapsed() >= REJECT_RECHECK) {
            rows.remove(&key);
        }
        rows.get(&key).cloned()
    }

    /// Record that the provider refused `runtime`'s key or sign-in (service.py reject): the runtime stops taking
    /// work here until a credential changes, a sign-in succeeds, or REJECT_RECHECK passes.
    pub fn reject(&self, runtime: &str, text: &str, profile: Option<&crate::profiles::Profile>) {
        let key = (runtime.to_string(), profile.map(|p| p.name.clone()).unwrap_or_default(), self.credential_fingerprint(profile));
        let at = crate::util::iso(&serde_json::json!(crate::util::epoch_s().floor()));
        let at = text_of(&at).chars().take(19).collect::<String>() + "Z";
        let mut rows = self.rejected.lock().unwrap();
        rows.retain(|_, r| r.mono.elapsed() < REJECT_RECHECK);
        rows.insert(key, Rejection { at, mono: Instant::now(), reason: crate::hosts::rejection_reason(text) });
        drop(rows);
        crate::profiles::forget_report();
        self.dirty.store(true, std::sync::atomic::Ordering::SeqCst);
    }

    /// A sign-in succeeded: forget the refusals of `runtime` on `profile` ("" = the operator's own).
    pub fn clear_rejection(&self, runtime: &str, profile: &str) {
        self.rejected.lock().unwrap().retain(|k, _| !(k.0 == runtime && k.1 == profile));
        crate::profiles::forget_report();
        self.dirty.store(true, std::sync::atomic::Ordering::SeqCst);
    }

    /// Whether a rejection changed since the last call (the loop then heartbeats at once).
    pub fn take_dirty(&self) -> bool {
        self.dirty.swap(false, std::sync::atomic::Ordering::SeqCst)
    }

    fn hand(&self) -> Env {
        let mut env: Env = std::env::vars().collect();
        env.extend(read_env(&self.secrets.join("_shared.env")));
        env
    }

    fn team(&self) -> Env {
        read_env(&self.secrets.join(TEAM_KEYS_FILE))
    }

    /// (name, value) of the key that signs `runtime` in headlessly, or None.
    pub fn headless_secret(&self, runtime: &str) -> Option<(String, String)> {
        let mut env = self.team();
        env.extend(self.hand());
        headless_names(runtime).iter().find_map(|n| {
            env.get(*n).filter(|v| !v.is_empty()).map(|v| (n.to_string(), v.trim_matches(|c| c == '"' || c == '\'').to_string()))
        })
    }

    /// Whether the only key that signs `runtime` in is the company's.
    pub fn team_key_only(&self, runtime: &str) -> bool {
        let (hand, team) = (self.hand(), self.team());
        let names = headless_names(runtime);
        !names.iter().any(|n| hand.get(*n).is_some_and(|v| !v.is_empty())) && names.iter().any(|n| team.get(*n).is_some_and(|v| !v.is_empty()))
    }

    /// Take the company's model key when this computer has none of its own for `runtime`, at most every
    /// TEAM_KEY_RETRY. Written owner-only, never logged. A team key the provider refused is asked for again, in
    /// case the company rotated it.
    pub async fn team_model_keys(&self, client: &Client, runtime: &str) -> bool {
        let names = headless_names(runtime);
        if names.is_empty() {
            return false;
        }
        let (hand, mut team) = (self.hand(), self.team());
        let refused = self.rejection(runtime, None).is_some();
        if names.iter().any(|n| hand.get(*n).is_some_and(|v| !v.is_empty())) || (names.iter().any(|n| team.get(*n).is_some_and(|v| !v.is_empty())) && !refused) {
            return false;
        }
        {
            let mut asked = self.asked.lock().unwrap();
            if asked.get(runtime).is_some_and(|at| at.elapsed() < TEAM_KEY_RETRY) {
                return false;
            }
            asked.insert(runtime.into(), Instant::now());
        }
        let reply = match client.get_query("runner-model-credentials", &[("runtime", runtime.to_string())]).await {
            Ok(r) => r,
            Err(e) => {
                log(&format!("Team model key for {runtime} was not fetched: {}", if e.status != 0 { "APIError" } else { "unreachable" }));
                return false;
            }
        };
        let mut given = Env::new();
        for row in reply["credentials"].as_array().cloned().unwrap_or_default() {
            let (k, v) = (text_of(&row["env"]), text_of(&row["value"]).trim().to_string());
            if names.contains(&k.as_str()) && !v.is_empty() && !v.contains(['\r', '\n', '\0']) {
                given.insert(k, v);
            }
        }
        if given.is_empty() || given.iter().all(|(k, v)| team.get(k) == Some(v)) {
            return false;
        }
        for n in names {
            team.remove(*n);
        }
        team.extend(given.clone());
        if self.write_team(&team).is_err() {
            log(&format!("Team model key for {runtime} could not be saved"));
            return false;
        }
        log(&format!("Took the team's model key for {}", given.keys().cloned().collect::<Vec<_>>().join(", ")));
        true
    }

    fn write_team(&self, team: &Env) -> std::io::Result<()> {
        use std::io::Write;
        use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
        std::fs::DirBuilder::new().recursive(true).mode(0o770).create(&self.secrets)?;
        let path = self.secrets.join(TEAM_KEYS_FILE);
        let temp = self.secrets.join(format!("{TEAM_KEYS_FILE}.tmp"));
        let mut out = std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&temp)?;
        out.write_all(team.iter().map(|(k, v)| format!("{k}={v}\n")).collect::<String>().as_bytes())?;
        out.sync_all()?;
        std::fs::rename(&temp, &path)
    }

    /// Sign Codex in with OPENAI_API_KEY (`codex login --with-api-key`, the key on stdin, never in arguments or
    /// logs). A key that failed is not tried again for CODEX_LOGIN_RETRY unless the key or the home changes.
    pub async fn codex_key_login(&self, executable: &Path) -> bool {
        let Some((name, key)) = self.headless_secret("codex") else { return false };
        let home = std::env::var("CODEX_HOME").unwrap_or_else(|_| format!("{}/.codex", std::env::var("HOME").unwrap_or_default()));
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        key.hash(&mut hasher);
        let attempt = (home, hasher.finish());
        {
            let mut tried = self.codex_tried.lock().unwrap();
            if tried.get(&attempt).is_some_and(|at| at.elapsed() < CODEX_LOGIN_RETRY) {
                return false;
            }
            tried.insert(attempt, Instant::now());
        }
        use tokio::io::AsyncWriteExt;
        let child = crate::isolation::command(executable)
            .args(["login", "--with-api-key"])
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true)
            .spawn();
        let Ok(mut child) = child else {
            log(&format!("Codex sign-in with {name} did not run: OSError"));
            return false;
        };
        if let Some(mut stdin) = child.stdin.take() {
            let _ = stdin.write_all(format!("{key}\n").as_bytes()).await;
        }
        let done = match tokio::time::timeout(Duration::from_secs(30), child.wait_with_output()).await {
            Ok(Ok(out)) => out,
            _ => {
                log(&format!("Codex sign-in with {name} did not run: TimeoutExpired"));
                return false;
            }
        };
        if !done.status.success() {
            let text = if done.stderr.is_empty() { done.stdout } else { done.stderr };
            let first = String::from_utf8_lossy(&text).trim().lines().next().unwrap_or_default().replace(&key, "***");
            let reason = crate::redact::scrub_credentials(&first, std::slice::from_ref(&key));
            log(&format!("Codex sign-in with {name} failed (exit {}): {}", done.status.code().unwrap_or(-1), reason.chars().take(200).collect::<String>()));
            return false;
        }
        log(&format!("Codex signed in with {name}"));
        true
    }
}

/// Whether any assigned bot runs `runtime`.
pub fn assigned(assignments: &[Value], runtime: &str) -> bool {
    assignments.iter().any(|row| row["config"]["runtime"] == runtime)
}
