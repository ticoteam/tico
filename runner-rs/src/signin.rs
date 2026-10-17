//! Headless model sign-in (the old Python runner's `headless_secret`, `team_model_keys`, `team_key_only`,
//! `codex_key_login`, here `key_login`): the API key or long-lived token that signs a runtime in without a browser, from this
//! process's environment, `secrets/_shared.env`, or the key the company gave every computer, which the runner
//! fetches into `secrets/_team_model.env` when this computer has none of its own.

use std::collections::HashMap;
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};
use std::sync::{Condvar, Mutex};
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::client::Client;
use crate::env::Env;
use crate::util::{log, text_of};

pub const TEAM_KEYS_FILE: &str = "_team_model.env";
/// The runtimes whose computer login a person signed out from the app (backend/model_login.py): no key on this
/// computer signs them in again, not the company's and not the 10-minute key login, until a person signs in or
/// chooses the shared key. A JSON list of runtimes, kept across restarts.
pub const SIGNED_OUT_FILE: &str = "_signed_out.json";
/// The runtimes whose computer login a person switched from its API key to a subscription (backend/model_login.py
/// `to_subscription`): no key on this computer reaches that login, its turns or the 10-minute key login, even while
/// it is signed in, until a person chooses the shared key. Kept across restarts like SIGNED_OUT_FILE; unlike it, a
/// sign-in does not lift it.
pub const KEY_OFF_FILE: &str = "_key_off.json";
const TEAM_KEY_RETRY: Duration = Duration::from_secs(300);
const KEY_LOGIN_RETRY: Duration = Duration::from_secs(600);
/// How long a sign-out waits for a key login already running (its own limit is 30 seconds) to end.
pub const KEY_LOGIN_DRAIN: Duration = Duration::from_secs(40);
/// How long a refused key or sign-in is trusted before one turn tries again (service.py REJECT_RECHECK_S).
pub const REJECT_RECHECK: Duration = Duration::from_secs(300);

/// A provider's refusal of this computer's key or sign-in on a real turn.
#[derive(Clone, Debug)]
pub struct Rejection {
    pub at: String,
    pub mono: Instant,
    pub reason: String,
}

/// The keys that sign `runtime` in on this computer for every bot: its manifest's `auth.api_key_env` when the
/// computer holds them (`api_keys = "computer"`). Keys each bot holds itself are never a computer's sign-in.
pub fn headless_names(runtime: &str) -> Vec<String> {
    if crate::harness_tools::caps(runtime, "").api_keys == "computer" {
        crate::harness_tools::api_key_env(runtime)
    } else {
        vec![]
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
    key_tried: Mutex<HashMap<(String, u64), Instant>>,
    rejected: Mutex<HashMap<(String, String, String), Rejection>>,
    /// Set when a rejection is recorded or cleared: the loop sends a heartbeat at once.
    dirty: std::sync::atomic::AtomicBool,
    /// Held for every read-modify-write of SIGNED_OUT_FILE and TEAM_KEYS_FILE, so two runtimes signed out (or in)
    /// at once never write back each other's stale list or key. It also counts the key logins running per runtime:
    /// one starts only while the runtime is not signed out, and a sign-out waits for those already running.
    keys: Mutex<HashMap<String, usize>>,
    key_login_ended: Condvar,
    /// Runtimes whose key logins wait while a switch to a subscription puts its login in place and checks it. The key
    /// itself stays: in turns and on disk, until the subscription works.
    held: Mutex<std::collections::HashSet<String>>,
}

/// No key login starts for `runtime` while this lives; the key and its company copy are untouched.
pub struct KeyLoginsHeld<'a> {
    sign: &'a SignIn,
    runtime: String,
}

impl Drop for KeyLoginsHeld<'_> {
    fn drop(&mut self) {
        let _keys = self.sign.keys.lock().unwrap_or_else(|e| e.into_inner());
        self.sign.held.lock().unwrap_or_else(|e| e.into_inner()).remove(&self.runtime);
    }
}

/// A key login running for `runtime`: counted until it ends, however it ends.
struct KeyLoginRunning<'a> {
    sign: &'a SignIn,
    runtime: String,
}

impl Drop for KeyLoginRunning<'_> {
    fn drop(&mut self) {
        let mut running = self.sign.keys.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(n) = running.get_mut(&self.runtime) {
            *n = n.saturating_sub(1);
        }
        self.sign.key_login_ended.notify_all();
    }
}

impl SignIn {
    pub fn new(projects_dir: &Path) -> SignIn {
        SignIn {
            secrets: projects_dir.join("secrets"),
            asked: Mutex::new(HashMap::new()),
            key_tried: Mutex::new(HashMap::new()),
            rejected: Mutex::new(HashMap::new()),
            dirty: Default::default(),
            keys: Mutex::new(HashMap::new()),
            key_login_ended: Condvar::new(),
            held: Mutex::new(std::collections::HashSet::new()),
        }
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

    /// Whether a person signed `runtime`'s computer login out from the app (SIGNED_OUT_FILE).
    pub fn signed_out(&self, runtime: &str) -> bool {
        self.list(SIGNED_OUT_FILE).iter().any(|r| r == runtime)
    }

    /// Whether a person switched `runtime`'s computer login to a subscription (KEY_OFF_FILE).
    pub fn key_off(&self, runtime: &str) -> bool {
        self.list(KEY_OFF_FILE).iter().any(|r| r == runtime)
    }

    /// No key on this computer may sign `runtime` in or reach its turns: signed out, or switched to a subscription.
    fn no_key(&self, runtime: &str) -> bool {
        self.signed_out(runtime) || self.key_off(runtime)
    }

    fn list(&self, file: &str) -> Vec<String> {
        std::fs::read_to_string(self.secrets.join(file))
            .ok()
            .and_then(|t| serde_json::from_str::<Vec<String>>(&t).ok())
            .unwrap_or_default()
    }

    fn write_list(&self, file: &str, list: &[String]) -> std::io::Result<()> {
        use std::io::Write;
        use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
        std::fs::DirBuilder::new().recursive(true).mode(0o770).create(&self.secrets)?;
        let temp = self.secrets.join(format!("{file}.tmp"));
        let mut out = std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&temp)?;
        out.write_all(serde_json::to_string(list).unwrap_or_default().as_bytes())?;
        out.sync_all()?;
        std::fs::rename(&temp, self.secrets.join(file))
    }

    /// A person signs `runtime`'s computer login out: from now no key on this computer signs it in, and the
    /// company's key this computer holds for it is deleted. Written before the CLI logs out, so no readiness pass
    /// in between signs it straight back in.
    pub fn mark_signed_out(&self, runtime: &str) -> std::io::Result<()> {
        self.mark(SIGNED_OUT_FILE, runtime)
    }

    /// A person switched `runtime`'s computer login to a subscription, which is now in place: from now no key on this
    /// computer reaches it, and the company's key this computer holds for it is deleted.
    pub fn mark_key_off(&self, runtime: &str) -> std::io::Result<()> {
        self.mark(KEY_OFF_FILE, runtime)
    }

    fn mark(&self, file: &str, runtime: &str) -> std::io::Result<()> {
        let _keys = self.keys.lock().unwrap_or_else(|e| e.into_inner());
        let mut list = self.list(file);
        if !list.iter().any(|r| r == runtime) {
            list.push(runtime.into());
            list.sort();
            self.write_list(file, &list)?;
        }
        let names = headless_names(runtime);
        let mut team = self.team();
        if names.iter().any(|n| team.contains_key(n)) {
            team.retain(|k, _| !names.contains(k));
            self.write_team(&team)?;
        }
        self.dirty.store(true, std::sync::atomic::Ordering::SeqCst);
        Ok(())
    }

    /// A person signed `runtime` in again, or chose the shared key: keys sign it in as before, at once.
    pub fn unmark_signed_out(&self, runtime: &str) -> std::io::Result<()> {
        self.unmark(SIGNED_OUT_FILE, runtime)
    }

    /// A person chose the shared key for a login switched to a subscription, or the switch did not go in after all.
    pub fn unmark_key_off(&self, runtime: &str) -> std::io::Result<()> {
        self.unmark(KEY_OFF_FILE, runtime)
    }

    fn unmark(&self, file: &str, runtime: &str) -> std::io::Result<()> {
        {
            let _keys = self.keys.lock().unwrap_or_else(|e| e.into_inner());
            let mut list = self.list(file);
            if list.iter().any(|r| r == runtime) {
                list.retain(|r| r != runtime);
                self.write_list(file, &list)?;
            }
        }
        self.asked.lock().unwrap().remove(runtime);
        self.key_tried.lock().unwrap().retain(|(home, _), _| !home.starts_with(&format!("{runtime}:")));
        self.dirty.store(true, std::sync::atomic::Ordering::SeqCst);
        Ok(())
    }

    /// A switch to a subscription is about to put its login in place: no key login starts for `runtime` until the
    /// returned guard drops, so none writes the key's login over the new one. Unlike `mark_key_off` the key stays in
    /// use and the company's copy stays on disk, so a switch that fails or is cancelled leaves everything as it was.
    pub fn hold_key_logins(&self, runtime: &str) -> KeyLoginsHeld<'_> {
        let _keys = self.keys.lock().unwrap_or_else(|e| e.into_inner());
        self.held.lock().unwrap_or_else(|e| e.into_inner()).insert(runtime.into());
        KeyLoginsHeld { sign: self, runtime: runtime.into() }
    }

    /// Wait until no key login for `runtime` is running, at most `limit`. After `mark_signed_out` none starts, so
    /// once this is true no key login can write a login back after the CLI's logout. False when one is still running.
    pub fn wait_for_key_logins(&self, runtime: &str, limit: Duration) -> bool {
        let running = self.keys.lock().unwrap_or_else(|e| e.into_inner());
        let (running, _) = self
            .key_login_ended
            .wait_timeout_while(running, limit, |r| r.get(runtime).is_some_and(|n| *n > 0))
            .unwrap_or_else(|e| e.into_inner());
        !running.get(runtime).is_some_and(|n| *n > 0)
    }

    fn hand(&self) -> Env {
        let mut env: Env = std::env::vars().collect();
        env.extend(read_env(&self.secrets.join("_shared.env")));
        env
    }

    fn team(&self) -> Env {
        read_env(&self.secrets.join(TEAM_KEYS_FILE))
    }

    /// (name, value) of the key that signs `runtime` in headlessly, or None. None while a person has it signed
    /// out: that is what keeps it out of readiness, the key login, turns and the fallback.
    pub fn headless_secret(&self, runtime: &str) -> Option<(String, String)> {
        if self.no_key(runtime) {
            return None;
        }
        let mut env = self.team();
        env.extend(self.hand());
        headless_names(runtime).iter().find_map(|n| {
            env.get(n).filter(|v| !v.is_empty()).map(|v| (n.to_string(), v.trim_matches(|c| c == '"' || c == '\'').to_string()))
        })
    }

    /// Whether the only key that signs `runtime` in is the company's.
    pub fn team_key_only(&self, runtime: &str) -> bool {
        if self.no_key(runtime) {
            return false;
        }
        let (hand, team) = (self.hand(), self.team());
        let names = headless_names(runtime);
        !names.iter().any(|n| hand.get(n).is_some_and(|v| !v.is_empty())) && names.iter().any(|n| team.get(n).is_some_and(|v| !v.is_empty()))
    }

    /// Take the company's model key when this computer has none of its own for `runtime`, at most every
    /// TEAM_KEY_RETRY. Written owner-only, never logged. A team key the provider refused is asked for again, in
    /// case the company rotated it.
    pub async fn team_model_keys(&self, client: &Client, runtime: &str) -> bool {
        let names = headless_names(runtime);
        if names.is_empty() || self.no_key(runtime) {
            return false;
        }
        let (hand, mut team) = (self.hand(), self.team());
        let refused = self.rejection(runtime, None).is_some();
        if names.iter().any(|n| hand.get(n).is_some_and(|v| !v.is_empty())) || (names.iter().any(|n| team.get(n).is_some_and(|v| !v.is_empty())) && !refused) {
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
            if names.contains(&k) && !v.is_empty() && !v.contains(['\r', '\n', '\0']) {
                given.insert(k, v);
            }
        }
        // The fetch took a while: decide on the files as they are now, under the lock. A person may have signed this
        // runtime out meanwhile, or another runtime's key may have changed.
        let _keys = self.keys.lock().unwrap_or_else(|e| e.into_inner());
        if self.no_key(runtime) {
            return false;
        }
        team = self.team();
        if given.is_empty() || given.iter().all(|(k, v)| team.get(k) == Some(v)) {
            return false;
        }
        for n in &names {
            team.remove(n);
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

    /// Turn the computer's key for `runtime` into a stored login with the manifest's `api_key_login` command (Codex:
    /// `codex login --with-api-key`), the key on stdin, never in arguments or logs. A key that failed is not tried
    /// again for KEY_LOGIN_RETRY unless the key or the CLI's home changes.
    pub async fn key_login(&self, runtime: &str, executable: &Path) -> bool {
        let args = crate::harness_tools::caps(runtime, "").api_key_login;
        if args.is_empty() {
            return false;
        }
        let Some((name, key)) = self.headless_secret(runtime) else { return false };
        let label = crate::harness_tools::name_for(runtime);
        let var = crate::harness_tools::caps(runtime, "").profile_home;
        let home = format!("{runtime}:{}", if var.is_empty() { String::new() } else { std::env::var(&var).unwrap_or_default() });
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        key.hash(&mut hasher);
        let attempt = (home, hasher.finish());
        {
            let mut tried = self.key_tried.lock().unwrap();
            if tried.get(&attempt).is_some_and(|at| at.elapsed() < KEY_LOGIN_RETRY) {
                return false;
            }
            tried.insert(attempt.clone(), Instant::now());
        }
        // Counted from here, and only while not signed out: a sign-out marked before this never sees it start, and
        // one marked after it waits for it to end before logging out.
        let _running = {
            let mut running = self.keys.lock().unwrap_or_else(|e| e.into_inner());
            if self.held.lock().unwrap_or_else(|e| e.into_inner()).contains(runtime) {
                // Tried again once the switch is over, not KEY_LOGIN_RETRY later.
                self.key_tried.lock().unwrap().remove(&attempt);
                return false;
            }
            if self.no_key(runtime) {
                return false;
            }
            *running.entry(runtime.to_string()).or_default() += 1;
            KeyLoginRunning { sign: self, runtime: runtime.to_string() }
        };
        use tokio::io::AsyncWriteExt;
        let child = crate::isolation::command(executable)
            .args(&args)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true)
            .spawn();
        let Ok(mut child) = child else {
            log(&format!("{label} sign-in with {name} did not run: OSError"));
            return false;
        };
        if let Some(mut stdin) = child.stdin.take() {
            let _ = stdin.write_all(format!("{key}\n").as_bytes()).await;
        }
        let done = match tokio::time::timeout(Duration::from_secs(30), child.wait_with_output()).await {
            Ok(Ok(out)) => out,
            _ => {
                log(&format!("{label} sign-in with {name} did not run: TimeoutExpired"));
                return false;
            }
        };
        if !done.status.success() {
            let text = if done.stderr.is_empty() { done.stdout } else { done.stderr };
            let first = String::from_utf8_lossy(&text).trim().lines().next().unwrap_or_default().replace(&key, "***");
            let reason = crate::redact::scrub_credentials(&first, std::slice::from_ref(&key));
            log(&format!("{label} sign-in with {name} failed (exit {}): {}", done.status.code().unwrap_or(-1), reason.chars().take(200).collect::<String>()));
            return false;
        }
        log(&format!("{label} signed in with {name}"));
        true
    }
}

/// Whether any assigned bot runs `runtime`.
pub fn assigned(assignments: &[Value], runtime: &str) -> bool {
    assignments.iter().any(|row| row["config"]["runtime"] == runtime)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    fn projects() -> PathBuf {
        let dir = std::env::temp_dir().join(format!("tico-signed-out-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("secrets")).unwrap();
        dir
    }

    /// A stand-in `codex` that records each run, so a key login that happens is seen.
    fn fake_codex(dir: &Path) -> (PathBuf, PathBuf) {
        let (exe, runs) = (dir.join("codex"), dir.join("runs"));
        std::fs::write(&exe, format!("#!/bin/sh\necho \"$@\" >> '{}'\ncat > /dev/null\n", runs.display())).unwrap();
        std::fs::set_permissions(&exe, std::fs::Permissions::from_mode(0o755)).unwrap();
        (exe, runs)
    }

    /// A server that hands out the company's model key and counts how often it was asked.
    async fn team_server() -> (Client, Arc<AtomicUsize>) {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let asked = Arc::new(AtomicUsize::new(0));
        let count = asked.clone();
        tokio::spawn(async move {
            while let Ok((mut sock, _)) = listener.accept().await {
                count.fetch_add(1, Ordering::SeqCst);
                let mut buf = vec![0u8; 8192];
                let _ = sock.read(&mut buf).await;
                let body = r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-team-0123456789"}]}"#;
                let reply = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
                let _ = sock.write_all(reply.as_bytes()).await;
                let _ = sock.shutdown().await;
            }
        });
        (Client::new(&format!("http://127.0.0.1:{port}"), "runner-token", 5, 0).unwrap(), asked)
    }

    #[tokio::test]
    async fn a_signed_out_login_is_not_signed_back_in_by_the_shared_key() {
        let dir = projects();
        let secrets = dir.join("secrets");
        std::fs::write(secrets.join(TEAM_KEYS_FILE), "OPENAI_API_KEY=sk-team-0123456789\nOTHER=kept\n").unwrap();
        let (exe, runs) = fake_codex(&dir);
        let sign = SignIn::new(&dir);
        sign.mark_signed_out("codex").unwrap();
        assert!(sign.signed_out("codex") && !sign.signed_out("claude"));
        // The company's key this computer held for it is gone, and nothing else in that file.
        let team = read_env(&secrets.join(TEAM_KEYS_FILE));
        assert!(!team.contains_key("OPENAI_API_KEY") && team.get("OTHER").map(String::as_str) == Some("kept"));
        // No key signs it in: not readiness's "ready with a key", not the 10-minute key login, not the next fetch.
        assert_eq!(sign.headless_secret("codex"), None);
        assert!(!sign.team_key_only("codex"));
        std::fs::write(secrets.join(TEAM_KEYS_FILE), "OPENAI_API_KEY=sk-team-0123456789\n").unwrap();
        assert_eq!(sign.headless_secret("codex"), None, "a key that came back still does not sign it in");
        assert!(!sign.team_key_only("codex"), "nor counts as the company's key signing it in");
        assert!(!sign.key_login("codex", &exe).await);
        assert!(!runs.exists(), "the key login ran while signed out");
        let (client, asked) = team_server().await;
        std::fs::remove_file(secrets.join(TEAM_KEYS_FILE)).unwrap();
        assert!(!sign.team_model_keys(&client, "codex").await);
        assert_eq!(asked.load(Ordering::SeqCst), 0, "the shared key was fetched while signed out");
        // It stays so across a restart: the mark is on disk.
        let restarted = SignIn::new(&dir);
        assert!(restarted.signed_out("codex") && restarted.headless_secret("codex").is_none());
        // A person chose the shared key (or signed in): it is fetched and logged in with again, at once.
        restarted.unmark_signed_out("codex").unwrap();
        assert!(restarted.team_model_keys(&client, "codex").await);
        assert_eq!(asked.load(Ordering::SeqCst), 1);
        assert!(restarted.key_login("codex", &exe).await);
        assert_eq!(std::fs::read_to_string(&runs).unwrap().trim(), "login --with-api-key");
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[tokio::test]
    async fn a_login_switched_to_a_subscription_takes_no_key_even_signed_in_until_the_shared_key_is_chosen() {
        // Switch to subscription (backend/model_login.py to_subscription): once the subscription is in place no key on
        // this computer reaches the login. The turn's key comes only from headless_secret (execute.rs
        // `api_key_in_turn`), so the next turn runs on the subscription with no API key in its environment.
        let dir = projects();
        let secrets = dir.join("secrets");
        std::fs::write(secrets.join(TEAM_KEYS_FILE), "ANTHROPIC_API_KEY=sk-ant-team-0123456789\nOPENAI_API_KEY=sk-team-0123456789\n").unwrap();
        std::fs::write(secrets.join("_shared.env"), "ANTHROPIC_API_KEY=sk-ant-hand-0123456789\n").unwrap();
        let (exe, runs) = fake_codex(&dir);
        let sign = SignIn::new(&dir);
        assert!(sign.headless_secret("claude").is_some(), "before the switch the key is what it runs on");
        sign.mark_key_off("claude").unwrap();
        sign.mark_key_off("codex").unwrap();
        assert!(sign.key_off("claude") && !sign.signed_out("claude"), "switched, not signed out");
        // The company's copy is deleted; the key placed by hand stays on disk but reaches nothing.
        assert!(!read_env(&secrets.join(TEAM_KEYS_FILE)).contains_key("ANTHROPIC_API_KEY"));
        assert_eq!(sign.headless_secret("claude"), None, "no API key for the turn's environment");
        assert!(!sign.team_key_only("claude"));
        assert!(!sign.key_login("codex", &exe).await);
        assert!(!runs.exists(), "the 10-minute key login ran over the subscription");
        let (client, asked) = team_server().await;
        assert!(!sign.team_model_keys(&client, "codex").await);
        assert_eq!(asked.load(Ordering::SeqCst), 0, "the shared key was fetched for a login switched to a subscription");
        // A later sign-in (Switch account) lifts a sign-out, not this; and it holds across a restart.
        sign.unmark_signed_out("claude").unwrap();
        let restarted = SignIn::new(&dir);
        assert!(restarted.key_off("claude") && restarted.headless_secret("claude").is_none());
        // Use the shared key is the person saying so.
        restarted.unmark_key_off("codex").unwrap();
        assert!(restarted.team_model_keys(&client, "codex").await);
        assert!(restarted.headless_secret("codex").is_some() && restarted.headless_secret("claude").is_none());
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[tokio::test]
    async fn a_switch_to_a_subscription_that_fails_leaves_the_key_in_use_with_nothing_to_fetch_again() {
        // While the subscription is put in place and checked only key logins wait; the key still reaches turns and
        // the company's copy stays, so when the switch fails the next turn has it at once.
        let dir = projects();
        let secrets = dir.join("secrets");
        std::fs::write(secrets.join(TEAM_KEYS_FILE), "OPENAI_API_KEY=sk-team-0123456789\n").unwrap();
        let (exe, runs) = fake_codex(&dir);
        let (client, asked) = team_server().await;
        let sign = SignIn::new(&dir);
        {
            let _held = sign.hold_key_logins("codex");
            assert!(sign.wait_for_key_logins("codex", Duration::from_millis(10)));
            assert!(!sign.key_login("codex", &exe).await);
            assert!(!runs.exists(), "a key login ran over the subscription being checked");
            assert!(sign.headless_secret("codex").is_some(), "the key left the turns before the subscription worked");
        }
        assert!(!sign.key_off("codex"));
        assert!(read_env(&secrets.join(TEAM_KEYS_FILE)).contains_key("OPENAI_API_KEY"));
        assert!(!sign.team_model_keys(&client, "codex").await);
        assert_eq!(asked.load(Ordering::SeqCst), 0, "the company key was fetched again after a failed switch");
        assert_eq!(sign.headless_secret("codex").map(|(_, v)| v), Some("sk-team-0123456789".into()));
        assert!(sign.key_login("codex", &exe).await, "the key login waits KEY_LOGIN_RETRY after the switch");
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn two_logins_signed_out_or_in_at_once_each_keep_their_own_change() {
        // The runner signs out Codex and Claude Code on threads of their own; neither may write back the other's
        // stale list or company key.
        for _ in 0..100 {
            let dir = projects();
            let secrets = dir.join("secrets");
            let both = "OPENAI_API_KEY=sk-team-a\nCLAUDE_CODE_OAUTH_TOKEN=team-b\nOTHER=kept\n";
            std::fs::write(secrets.join(TEAM_KEYS_FILE), both).unwrap();
            let sign = Arc::new(SignIn::new(&dir));
            let race = |a: &'static str, b: &'static str, unmark_a: bool| {
                let barrier = Arc::new(std::sync::Barrier::new(2));
                let threads: Vec<_> = [(a, unmark_a), (b, false)].into_iter().map(|(runtime, unmark)| {
                    let (sign, barrier) = (sign.clone(), barrier.clone());
                    std::thread::spawn(move || {
                        barrier.wait();
                        if unmark { sign.unmark_signed_out(runtime) } else { sign.mark_signed_out(runtime) }.unwrap();
                    })
                }).collect();
                threads.into_iter().for_each(|t| t.join().unwrap());
            };
            race("codex", "claude", false);
            assert!(sign.signed_out("codex") && sign.signed_out("claude"));
            let again = SignIn::new(&dir); // as the runner reads them after a restart
            assert!(again.signed_out("codex") && again.signed_out("claude"));
            let team = read_env(&secrets.join(TEAM_KEYS_FILE));
            assert!(!team.contains_key("OPENAI_API_KEY") && !team.contains_key("CLAUDE_CODE_OAUTH_TOKEN"), "{team:?}");
            assert_eq!(team.get("OTHER").map(String::as_str), Some("kept"));
            // One signed in again while the other is signed out: each keeps its own.
            sign.unmark_signed_out("claude").unwrap();
            race("codex", "claude", true);
            assert!(!sign.signed_out("codex") && sign.signed_out("claude"));
            let again = SignIn::new(&dir);
            assert!(!again.signed_out("codex") && again.signed_out("claude"));
            std::fs::remove_dir_all(&dir).unwrap();
        }
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_key_login_already_running_cannot_sign_a_signed_out_login_back_in() {
        let dir = projects();
        std::fs::write(dir.join("secrets").join(TEAM_KEYS_FILE), "OPENAI_API_KEY=sk-team-0123456789\n").unwrap();
        // A key login that has started and writes its login only once it is let go.
        let (exe, auth) = (dir.join("codex"), dir.join("auth.json"));
        let script = format!("#!/bin/sh\ncat > /dev/null\n: > '{d}/started'\nwhile [ ! -f '{d}/release' ]; do sleep 0.05; done\necho key > '{a}'\n",
            d = dir.display(), a = auth.display());
        std::fs::write(&exe, script).unwrap();
        std::fs::set_permissions(&exe, std::fs::Permissions::from_mode(0o755)).unwrap();
        let sign = Arc::new(SignIn::new(&dir));
        let (k, exe2) = (sign.clone(), exe.clone());
        let login = tokio::spawn(async move { k.key_login("codex", &exe2).await });
        let until = Instant::now() + Duration::from_secs(10);
        while !dir.join("started").exists() && Instant::now() < until {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        // The sign-out, as run_sign_out does it: mark, wait for key logins already running, then the CLI's logout.
        let (s2, auth2) = (sign.clone(), auth.clone());
        let out = std::thread::spawn(move || {
            s2.mark_signed_out("codex").unwrap();
            let drained = s2.wait_for_key_logins("codex", Duration::from_secs(10));
            let _ = std::fs::remove_file(&auth2);
            drained
        });
        tokio::time::sleep(Duration::from_millis(300)).await;
        std::fs::write(dir.join("release"), "").unwrap();
        assert!(login.await.unwrap(), "the key login finished");
        assert!(tokio::task::spawn_blocking(move || out.join().unwrap()).await.unwrap(), "the sign-out waited for it");
        assert!(!auth.exists(), "the key login wrote the login back after the logout");
        assert!(sign.headless_secret("codex").is_none());
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[tokio::test]
    async fn a_company_key_fetched_while_the_login_was_signed_out_is_not_saved() {
        let dir = projects();
        let team = dir.join("secrets").join(TEAM_KEYS_FILE);
        // A server that answers only once it is let go, so the sign-out lands while the key is on its way.
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let (asked_tx, asked_rx) = tokio::sync::oneshot::channel::<()>();
        let (go_tx, go_rx) = tokio::sync::oneshot::channel::<()>();
        tokio::spawn(async move {
            let (mut sock, _) = listener.accept().await.unwrap();
            let mut buf = vec![0u8; 8192];
            let _ = sock.read(&mut buf).await;
            let _ = asked_tx.send(());
            let _ = go_rx.await;
            let body = r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-team-0123456789"}]}"#;
            let reply = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
            let _ = sock.write_all(reply.as_bytes()).await;
            let _ = sock.shutdown().await;
        });
        let client = Client::new(&format!("http://127.0.0.1:{port}"), "runner-token", 5, 0).unwrap();
        let sign = Arc::new(SignIn::new(&dir));
        let k = sign.clone();
        let fetch = tokio::spawn(async move { k.team_model_keys(&client, "codex").await });
        asked_rx.await.unwrap();
        sign.mark_signed_out("codex").unwrap();
        go_tx.send(()).unwrap();
        assert!(!fetch.await.unwrap());
        assert!(!read_env(&team).contains_key("OPENAI_API_KEY"), "the key was saved after the sign-out");
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn the_signed_out_mark_is_owner_only_and_a_bad_file_marks_nothing() {
        let dir = projects();
        let sign = SignIn::new(&dir);
        sign.mark_signed_out("claude").unwrap();
        sign.mark_signed_out("claude").unwrap();
        let path = dir.join("secrets").join(SIGNED_OUT_FILE);
        assert_eq!(std::fs::read_to_string(&path).unwrap(), r#"["claude"]"#);
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        std::fs::write(&path, "not json").unwrap();
        assert!(!sign.signed_out("claude"));
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
