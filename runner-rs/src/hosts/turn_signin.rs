//! A supervisor-owned refresher for each subscription login. Only access tokens leave it.
//! The lock covers refresh, never a turn; concurrent turns share neither UID nor refresh token.
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::AsyncWriteExt;

use crate::env::Env;

#[derive(Clone)]
pub struct Broker {
    kind: String,
    env: Env,
    lock: Arc<tokio::sync::Mutex<()>>,
}

impl Broker {
    pub fn new(kind: &str, env: &Env) -> Self {
        static LOCKS: OnceLock<Mutex<HashMap<String, Arc<tokio::sync::Mutex<()>>>>> = OnceLock::new();
        // No turn credentials enter the refresher's process; it only needs the original login home and process settings.
        let env: Env = env.iter().filter(|(k, _)| crate::env::SETTINGS.contains(&k.as_str()) || k.as_str() == "CLAUDE_CONFIG_DIR")
            .map(|(k, v)| (k.clone(), v.clone())).collect();
        let home = PathBuf::from(env.get("HOME").cloned().unwrap_or_default());
        let folder = match kind {
            "codex-external" => env.get("CODEX_HOME").map(PathBuf::from).unwrap_or_else(|| home.join(".codex")),
            _ => env.get("CLAUDE_CONFIG_DIR").map(PathBuf::from).unwrap_or_else(|| home.join(".claude")),
        };
        let key = format!("{kind}:{}", folder.components().collect::<PathBuf>().display());
        let lock = LOCKS.get_or_init(Default::default).lock().unwrap().entry(key).or_default().clone();
        Self { kind: kind.into(), env, lock }
    }

    #[cfg(test)]
    pub(crate) async fn hold_refresh(&self) -> tokio::sync::OwnedMutexGuard<()> {
        self.lock.clone().lock_owned().await
    }

    pub async fn access(&self, minimum: f64, previous: Option<&Value>) -> Result<Value, String> {
        let broker = self.clone();
        let request = json!({"kind": self.kind, "minimum": minimum, "parent": std::process::id(),
            "previous": previous.and_then(|p| p.get("chatgptAccountId")),
            "previousAccess": previous.and_then(|p| p.get("accessToken"))});
        // A stopped/cancelled turn cannot release the refresh lock while its helper still rotates the login.
        // Dropping the JoinHandle detaches this bounded worker; the worker retains its guard until exit.
        let run = tokio::spawn(async move {
            let _guard = broker.lock.lock().await;
            let mut cmd = crate::isolation::command(crate::env::python());
            cmd.args(["-I", "-c", include_str!("signin_broker.py")])
                .env_clear().envs(&broker.env).current_dir("/")
                .stdin(std::process::Stdio::piped()).stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::null()).kill_on_drop(true);
            let mut child = cmd.spawn().map_err(|_| ())?;
            child.stdin.take().ok_or(())?.write_all(request.to_string().as_bytes()).await.map_err(|_| ())?;
            // The helper bounds each vendor subprocess itself and reaps it before exiting. Keep the
            // guard until that cleanup completes, including when the requesting turn was cancelled.
            let out = child.wait_with_output().await.map_err(|_| ())?;
            if !out.status.success() || out.stdout.len() > 1024 * 1024 { return Err(()) }
            let value: Value = serde_json::from_slice(&out.stdout).map_err(|_| ())?;
            let secret = value["accessToken"].as_str().or_else(|| value["apiKey"].as_str()).filter(|s| !s.is_empty()).ok_or(())?;
            crate::redact::protect_access_token(secret);
            Ok(value)
        });
        run.await.ok().and_then(Result::ok)
            .ok_or_else(|| "Subscription access token unavailable; sign in again or retry this turn".into())
    }
}

/// Background account/usage reads must not start an independent managed-token refresh.
pub async fn guard(runtime: &str, env: &Env) -> Option<tokio::sync::OwnedMutexGuard<()>> {
    if !crate::isolation::enabled() || !crate::turn_users::available() { return None }
    let kind = crate::harness_tools::caps(runtime, "").turn_sign_in;
    if kind.is_empty() { return None }
    Some(Broker::new(&kind, env).lock.lock_owned().await)
}

/// Provider conversation files live in a supervisor-only store between turns. A turn restores only its
/// conversation's snapshot, into its own HOME; authentication/configuration are never copied or persisted here.
pub struct Store {
    private: PathBuf,
    root: PathBuf,
    identity: (u32, u32),
}

impl Store {
    pub async fn restore(kind: &str, env: &Env, state: &Path, conversation: &str, identity: (u32, u32)) -> Result<Option<Self>, String> {
        let variable = match kind { "claude-oauth" => "CLAUDE_CONFIG_DIR", "codex-external" => "CODEX_HOME", _ => return Ok(None) };
        let private = PathBuf::from(env.get(variable).ok_or("Private provider home missing")?);
        std::fs::create_dir(&private).map_err(|_| "Cannot create private provider home")?;
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&private, std::fs::Permissions::from_mode(0o700)).map_err(|_| "Cannot protect private provider home")?;
        std::os::unix::fs::lchown(&private, Some(identity.0), Some(identity.1)).map_err(|_| "Cannot own private provider home")?;
        let key = crate::gitrun::sha256_hex(format!("{kind}:{conversation}").as_bytes());
        let root = state.join("turn-state").join(key);
        std::fs::create_dir_all(&root).map_err(|_| "Cannot create private provider store")?;
        crate::isolation::keep_supervisor_directory(&root, 0o700).map_err(|_| "Cannot protect provider store")?;
        let own = Self { private, root, identity };
        if let Some(saved) = own.current() {
            own.copy(&saved, &own.private, unsafe { libc::geteuid() }, identity.0, identity.1).await?;
        }
        Ok(Some(own))
    }

    fn current(&self) -> Option<PathBuf> {
        let name = std::fs::read_to_string(self.root.join("current")).ok()?;
        // Names are supervisor-generated UUIDs; never interpret a path from a pointer file.
        (name.len() == 32 && name.bytes().all(|b| b.is_ascii_hexdigit())).then(|| self.root.join(name))
    }

    async fn copy(&self, source: &Path, destination: &Path, source_uid: u32, target_uid: u32, gid: u32) -> Result<(), String> {
        let mut cmd = tokio::process::Command::new(crate::env::python());
        cmd.args(["-I", "-c", include_str!("turn_state.py")]).env_clear().current_dir("/")
            .stdin(std::process::Stdio::piped()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).kill_on_drop(true);
        let mut child = cmd.spawn().map_err(|_| "Cannot start provider state handoff")?;
        let request = json!({"source": source, "destination": destination, "source_uid": source_uid, "target_uid": target_uid, "gid": gid});
        child.stdin.take().ok_or("Provider state handoff missing stdin")?.write_all(request.to_string().as_bytes()).await.map_err(|_| "Cannot send provider state handoff")?;
        let status = tokio::time::timeout(Duration::from_secs(30), child.wait()).await.map_err(|_| "Provider state handoff timed out")?.map_err(|_| "Provider state handoff failed")?;
        if status.success() { Ok(()) } else { Err("Provider state handoff refused unsafe or oversized state".into()) }
    }

    pub async fn save(self) -> Result<(), String> {
        use std::os::unix::fs::OpenOptionsExt;
        use std::io::Write;
        let old = self.current();
        let name = uuid::Uuid::new_v4().simple().to_string();
        let destination = self.root.join(&name);
        std::fs::create_dir(&destination).map_err(|_| "Cannot create provider snapshot")?;
        crate::isolation::keep_supervisor_directory(&destination, 0o700).map_err(|_| "Cannot protect provider snapshot")?;
        if let Err(e) = self.copy(&self.private, &destination, self.identity.0, unsafe { libc::geteuid() }, unsafe { libc::getegid() }).await {
            let _ = std::fs::remove_dir_all(destination);
            return Err(e);
        }
        let pointer = self.root.join(format!("current-{name}"));
        let mut file = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&pointer).map_err(|_| "Cannot create provider snapshot pointer")?;
        file.write_all(name.as_bytes()).and_then(|_| file.sync_all()).map_err(|_| "Cannot save provider snapshot pointer")?;
        std::fs::rename(pointer, self.root.join("current")).map_err(|_| "Cannot publish provider snapshot")?;
        if let Some(old) = old { let _ = std::fs::remove_dir_all(old); }
        Ok(())
    }
}

/// Return a broker only for a leased subscription turn. API keys need no refresh or shared home.
pub async fn prepare(kind: &str, source: &Env, env: &mut Env, private_home: &Path, minimum: f64) -> Result<(Option<Broker>, Option<std::time::Instant>), String> {
    if kind.is_empty() { return Ok((None, None)) }
    match kind {
        "claude-oauth" => {
            env.insert("CLAUDE_CONFIG_DIR".into(), private_home.join(".claude").display().to_string());
            if !env.get("CLAUDE_CODE_OAUTH_TOKEN").is_some_and(|s| !s.is_empty()) && !env.get("ANTHROPIC_API_KEY").is_some_and(|s| !s.is_empty()) {
                let broker = Broker::new(kind, source);
                let access = broker.access(minimum + 60.0, None).await?;
                env.insert("CLAUDE_CODE_OAUTH_TOKEN".into(), access["accessToken"].as_str().unwrap().into());
                let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_err(|_| "Invalid clock")?.as_secs_f64();
                let remaining = access["expiresAt"].as_f64().filter(|v| v.is_finite() && *v > now + 30.0).ok_or("Subscription access token expired; retry this turn")? - now - 30.0;
                return Ok((None, Some(std::time::Instant::now() + Duration::from_secs_f64(remaining))));
            }
            Ok((None, None))
        }
        "codex-external" => {
            env.insert("CODEX_HOME".into(), private_home.join(".codex").display().to_string());
            Ok(((!env.get("OPENAI_API_KEY").is_some_and(|s| !s.is_empty())).then(|| Broker::new(kind, source)), None))
        }
        _ => Err("Unsupported subscription broker".into()),
    }
}


#[cfg(test)]
mod tests {
    use super::*;

    fn source(home: &str) -> Env {
        [("HOME", home), ("PATH", "/usr/bin:/bin"), ("HUB_TOKEN", "synthetic-turn-token"),
         ("GH_TOKEN", "synthetic-github-token"), ("GRANTED_SECRET", "synthetic-grant")]
            .into_iter().map(|(k, v)| (k.into(), v.into())).collect()
    }

    #[tokio::test]
    async fn one_login_has_one_refresh_lock_and_unrelated_logins_remain_parallel() {
        let a = Broker::new("codex-external", &source("/synthetic/login-one"));
        let b = Broker::new("codex-external", &source("/synthetic/login-one"));
        let c = Broker::new("codex-external", &source("/synthetic/login-two"));
        assert!(Arc::ptr_eq(&a.lock, &b.lock));
        assert!(!Arc::ptr_eq(&a.lock, &c.lock));
        let held = a.lock.lock().await;
        assert!(b.lock.try_lock().is_err());
        assert!(c.lock.try_lock().is_ok());
        assert!(!a.env.contains_key("HUB_TOKEN") && !a.env.contains_key("GH_TOKEN") && !a.env.contains_key("GRANTED_SECRET"));
        drop(held);
        assert!(b.lock.try_lock().is_ok());
    }

    #[tokio::test]
    async fn api_keys_prepare_in_parallel_without_touching_a_managed_login() {
        let source = source("/synthetic/no-login");
        let mut claude: Env = [("ANTHROPIC_API_KEY".into(), "synthetic-api-key".into())].into();
        let mut codex: Env = [("OPENAI_API_KEY".into(), "synthetic-api-key".into())].into();
        let (a, b) = tokio::join!(prepare("claude-oauth", &source, &mut claude, Path::new("/private/a"), 3600.0),
                                 prepare("codex-external", &source, &mut codex, Path::new("/private/b"), 3600.0));
        assert!(matches!(a, Ok((None, None))) && matches!(b, Ok((None, None))));
        assert_eq!(claude["CLAUDE_CONFIG_DIR"], "/private/a/.claude");
        assert_eq!(codex["CODEX_HOME"], "/private/b/.codex");
    }
}
