//! Headless model sign-in (the old Python runner's `headless_secret`, `team_model_keys`, `team_key_only`,
//! `codex_key_login`, here `key_login`): the API key or long-lived token that signs a runtime in without a browser, from
//! the key the app keeps for this computer alone (`secrets/_computer_model.env`), this process's environment or
//! `secrets/_shared.env`, or the key the company gave every computer (`secrets/_team_model.env`). The runner fetches
//! the two from the app and asks again every KEY_REFRESH, so a key replaced or cleared elsewhere reaches it.

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
/// The key the app keeps in the vault for this computer alone: it signs the runtime in before any other.
pub const COMPUTER_KEYS_FILE: &str = "_computer_model.env";
/// The keys put on this computer from its terminal.
pub const HAND_KEYS_FILE: &str = "_shared.env";
/// The runtimes whose computer login a person signed out from the app (backend/model_login.py): no key on this
/// computer signs them in again, not the company's and not the 10-minute key login, until a person signs in or
/// chooses the shared key. A JSON list of runtimes, kept across restarts.
pub const SIGNED_OUT_FILE: &str = "_signed_out.json";
const TEAM_KEY_RETRY: Duration = Duration::from_secs(300);
/// How often a key that came from the app is asked for again, so one rotated, replaced or cleared while this computer
/// was away arrives.
const KEY_REFRESH: Duration = Duration::from_secs(600);
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
    /// Held for every read-modify-write of SIGNED_OUT_FILE and the key files, so two runtimes signed out (or in)
    /// at once never write back each other's stale list or key. It also counts the key logins running per runtime:
    /// one starts only while the runtime is not signed out, and a sign-out waits for those already running.
    keys: Mutex<HashMap<String, usize>>,
    key_login_ended: Condvar,
    /// Per runtime, how many key changes (a use_key job, a clear from the app) have begun, and whether one is running:
    /// while it runs the regular key fetch and key login leave the runtime's key alone, and a fetch that began
    /// before it writes nothing. Locked after `keys`, never before.
    changes: Mutex<HashMap<String, (u64, bool)>>,
}

/// A key change running for `runtime` (`SignIn::begin_key_change`): over however it ends.
pub struct KeyChange<'a> {
    sign: &'a SignIn,
    runtime: String,
}

impl Drop for KeyChange<'_> {
    fn drop(&mut self) {
        if let Some(c) = self.sign.changes.lock().unwrap_or_else(|e| e.into_inner()).get_mut(&self.runtime) {
            c.1 = false;
        }
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
            changes: Mutex::new(HashMap::new()),
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
        self.signed_out_list().iter().any(|r| r == runtime)
    }

    fn signed_out_list(&self) -> Vec<String> {
        std::fs::read_to_string(self.secrets.join(SIGNED_OUT_FILE))
            .ok()
            .and_then(|t| serde_json::from_str::<Vec<String>>(&t).ok())
            .unwrap_or_default()
    }

    fn write_signed_out(&self, list: &[String]) -> std::io::Result<()> {
        self.write_file(SIGNED_OUT_FILE, serde_json::to_string(list).unwrap_or_default().as_bytes())
    }

    /// Write `file` in the secrets folder owner-only, whole or not at all (a temporary file renamed over it).
    fn write_file(&self, file: &str, text: &[u8]) -> std::io::Result<()> {
        use std::io::Write;
        use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
        std::fs::DirBuilder::new().recursive(true).mode(0o770).create(&self.secrets)?;
        let temp = self.secrets.join(format!("{file}.tmp"));
        let mut out = std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&temp)?;
        out.write_all(text)?;
        out.sync_all()?;
        std::fs::rename(&temp, self.secrets.join(file))
    }

    /// A person signs `runtime`'s computer login out: from now no key on this computer signs it in, and the
    /// company's key this computer holds for it is deleted. Written before the CLI logs out, so no readiness pass
    /// in between signs it straight back in.
    pub fn mark_signed_out(&self, runtime: &str) -> std::io::Result<()> {
        let _keys = self.keys.lock().unwrap_or_else(|e| e.into_inner());
        let mut list = self.signed_out_list();
        if !list.iter().any(|r| r == runtime) {
            list.push(runtime.into());
            list.sort();
            self.write_signed_out(&list)?;
        }
        self.drop_names(TEAM_KEYS_FILE, &headless_names(runtime))?;
        self.dirty.store(true, std::sync::atomic::Ordering::SeqCst);
        Ok(())
    }

    /// A person signed `runtime` in again, or chose the shared key: keys sign it in as before, at once.
    pub fn unmark_signed_out(&self, runtime: &str) -> std::io::Result<()> {
        {
            let _keys = self.keys.lock().unwrap_or_else(|e| e.into_inner());
            let mut list = self.signed_out_list();
            if list.iter().any(|r| r == runtime) {
                list.retain(|r| r != runtime);
                self.write_signed_out(&list)?;
            }
        }
        self.refresh_now(runtime);
        self.retry_key_login(runtime);
        self.dirty.store(true, std::sync::atomic::Ordering::SeqCst);
        Ok(())
    }

    /// Begin changing `runtime`'s key, waiting at most `limit` for a change already running to end; None if it has not.
    pub fn begin_key_change(&self, runtime: &str, limit: Duration) -> Option<KeyChange<'_>> {
        let until = Instant::now() + limit;
        loop {
            if let Some(change) = self.key_change_after(runtime, None) {
                return Some(change);
            }
            if Instant::now() > until {
                return None;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
    }

    /// Begin changing `runtime`'s key if none is running and, given `seen`, none has begun since `seen` changes.
    fn key_change_after(&self, runtime: &str, seen: Option<u64>) -> Option<KeyChange<'_>> {
        let _keys = self.keys.lock().unwrap_or_else(|e| e.into_inner());
        let mut changes = self.changes.lock().unwrap_or_else(|e| e.into_inner());
        let c = changes.entry(runtime.to_string()).or_default();
        if c.1 || seen.is_some_and(|n| n != c.0) {
            return None;
        }
        *c = (c.0 + 1, true);
        Some(KeyChange { sign: self, runtime: runtime.to_string() })
    }

    /// (key changes begun for `runtime`, whether one is running).
    fn key_changes(&self, runtime: &str) -> (u64, bool) {
        self.changes.lock().unwrap_or_else(|e| e.into_inner()).get(runtime).copied().unwrap_or_default()
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
        env.extend(read_env(&self.secrets.join(HAND_KEYS_FILE)));
        env
    }

    fn team(&self) -> Env {
        read_env(&self.secrets.join(TEAM_KEYS_FILE))
    }

    fn computer(&self) -> Env {
        read_env(&self.secrets.join(COMPUTER_KEYS_FILE))
    }

    /// The one order keys sign `runtime` in: the app's key for this computer alone, then one put on it from the
    /// terminal (`_shared.env` or the environment), then the company's. (name, value, "computer" | "terminal" |
    /// "shared"), signed out or not.
    fn key_in_use(&self, runtime: &str) -> Option<(String, String, &'static str)> {
        let names = headless_names(runtime);
        [(self.computer(), "computer"), (self.hand(), "terminal"), (self.team(), "shared")].into_iter().find_map(|(env, source)| {
            names.iter().find_map(|n| {
                let value = env.get(n)?.trim_matches(|c| c == '"' || c == '\'');
                (!value.is_empty()).then(|| (n.to_string(), value.to_string(), source))
            })
        })
    }

    /// (name, value) of the key that signs `runtime` in headlessly, or None. None while a person has it signed
    /// out: that is what keeps it out of readiness, the key login, turns and the fallback.
    pub fn headless_secret(&self, runtime: &str) -> Option<(String, String)> {
        if self.signed_out(runtime) {
            return None;
        }
        self.key_in_use(runtime).map(|(name, value, _)| (name, value))
    }

    /// Which key signs `runtime` in: its name and where it is kept ("computer", "terminal" or "shared"), never its
    /// value. None while signed out or with no key.
    pub fn key_source(&self, runtime: &str) -> Option<(String, &'static str)> {
        if self.signed_out(runtime) {
            return None;
        }
        self.key_in_use(runtime).map(|(name, _, source)| (name, source))
    }

    /// Whether the only key that signs `runtime` in is the company's.
    pub fn team_key_only(&self, runtime: &str) -> bool {
        self.key_source(runtime).is_some_and(|(_, source)| source == "shared")
    }

    /// Ask the app for `runtime`'s key at the next call, not after the wait: it changed there.
    pub fn refresh_now(&self, runtime: &str) {
        self.asked.lock().unwrap().remove(runtime);
    }

    /// Let the next key login of `runtime` run even with a key tried a moment ago.
    pub fn retry_key_login(&self, runtime: &str) {
        self.key_tried.lock().unwrap().retain(|(home, _), _| !home.starts_with(&format!("{runtime}:")));
    }

    /// Fetch `runtime`'s key from the app: the key it keeps for this computer alone into COMPUTER_KEYS_FILE, the
    /// company's into TEAM_KEYS_FILE, and a key no longer given taken out of its file (other names in each file kept).
    /// Asked when there is no key, at most every TEAM_KEY_RETRY (and so again when the provider refused one from the
    /// app, in case it was rotated), and every KEY_REFRESH while a key from the app signs it in. Never while signed out,
    /// while a key from the terminal signs it in, or while a key change runs. A key cleared from the app while this
    /// computer was away is cleared here as a clear_key job would (login.rs clear_key). Written owner-only, never
    /// logged. True when a key for `runtime` changed.
    pub async fn team_model_keys(&self, client: &Client, runtime: &str) -> bool {
        let names = headless_names(runtime);
        let (changes, changing) = self.key_changes(runtime);
        if names.is_empty() || self.signed_out(runtime) || changing {
            return false;
        }
        let wait = match self.key_source(runtime) {
            Some((_, "terminal")) => return false,
            Some(_) if self.rejection(runtime, None).is_none() => KEY_REFRESH,
            _ => TEAM_KEY_RETRY,
        };
        {
            let mut asked = self.asked.lock().unwrap();
            if asked.get(runtime).is_some_and(|at| at.elapsed() < wait) {
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
        // A key change that began while this fetch was out decides the key; this reply may be older than it.
        let overtaken = || self.key_changes(runtime) != (changes, false);
        if reply["clear_key"] == true {
            if self.signed_out(runtime) {
                return false;
            }
            let Some(_change) = self.key_change_after(runtime, Some(changes)) else { return false };
            let cli = crate::login::key_cli(runtime);
            match blocking(|| crate::login::clear_key(self, runtime, cli.as_ref().map(|(exe, env)| (exe.as_str(), env)))) {
                Ok(()) => log(&format!("Key for {runtime} cleared from the app")),
                Err(e) => log(&format!("Key for {runtime} cleared from the app, but: {e}")),
            }
            return true;
        }
        // A login a person signed out from the app runs through its own job.
        if reply["signed_out"] == true {
            return false;
        }
        // Per file: the keys given, or None when one was unusable and the file is left as it is. An older server
        // names no source: its key is the company's.
        let mut given: HashMap<&str, Option<Env>> = [("computer", Some(Env::new())), ("shared", Some(Env::new()))].into();
        for row in reply["credentials"].as_array().cloned().unwrap_or_default() {
            let source = match text_of(&row["source"]).as_str() {
                "" | "shared" => "shared",
                "computer" => "computer",
                _ => continue,
            };
            let (k, v) = (text_of(&row["env"]), text_of(&row["value"]).trim().to_string());
            if !names.contains(&k) {
                continue;
            }
            let keys = given.get_mut(source).unwrap();
            if v.is_empty() || v.contains(['\r', '\n', '\0']) {
                *keys = None;
            } else if let Some(keys) = keys {
                keys.insert(k, v);
            }
        }
        // The fetch took a while: decide on the files as they are now, under the lock. A person may have signed this
        // runtime out meanwhile, or another runtime's key may have changed.
        let _keys = self.keys.lock().unwrap_or_else(|e| e.into_inner());
        if self.signed_out(runtime) || overtaken() {
            return false;
        }
        let mut changed = false;
        for (file, source, whose) in [(COMPUTER_KEYS_FILE, "computer", "this computer's"), (TEAM_KEYS_FILE, "shared", "the team's")] {
            let Some(keys) = given.remove(source).flatten() else { continue };
            match self.replace_keys(file, &names, &keys) {
                Ok(false) => {}
                Ok(true) if keys.is_empty() => {
                    log(&format!("Removed {whose} model key for {runtime}: the app no longer gives it"));
                    changed = true;
                }
                Ok(true) => {
                    log(&format!("Took {whose} model key for {}", keys.keys().cloned().collect::<Vec<_>>().join(", ")));
                    changed = true;
                }
                Err(_) => log(&format!("Model key for {runtime} could not be saved")),
            }
        }
        changed
    }

    /// Put `keys` in place of `names` in the key file `file`, its other names kept; whether it changed. Under the
    /// `keys` lock.
    fn replace_keys(&self, file: &str, names: &[String], keys: &Env) -> std::io::Result<bool> {
        let before = read_env(&self.secrets.join(file));
        let mut env = before.clone();
        env.retain(|k, _| !names.contains(k));
        env.extend(keys.clone());
        if env == before {
            return Ok(false);
        }
        self.write_file(file, env.iter().map(|(k, v)| format!("{k}={v}\n")).collect::<String>().as_bytes())?;
        Ok(true)
    }

    /// Take `names` out of the key file `file`, every other line kept as it was. Under the `keys` lock.
    fn drop_names(&self, file: &str, names: &[String]) -> std::io::Result<()> {
        let text = match std::fs::read_to_string(self.secrets.join(file)) {
            Ok(text) => text,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Err(e) => return Err(e),
        };
        let named = |line: &&str| !line.trim_start().starts_with('#') && line.split_once('=').is_some_and(|(k, _)| names.iter().any(|n| n == k.trim()));
        if !text.lines().any(|l| named(&l)) {
            return Ok(());
        }
        let kept: String = text.lines().filter(|l| !named(l)).map(|l| format!("{l}\n")).collect();
        self.write_file(file, kept.as_bytes())
    }

    /// Take `runtime`'s key off this computer: marked signed out (the company's copy goes with it), and its names
    /// taken out of the computer's own key file and `_shared.env`. A key in the process environment cannot be
    /// deleted; the mark keeps it out.
    pub fn forget_keys(&self, runtime: &str) -> std::io::Result<()> {
        self.mark_signed_out(runtime)?;
        let _keys = self.keys.lock().unwrap_or_else(|e| e.into_inner());
        let names = headless_names(runtime);
        self.drop_names(COMPUTER_KEYS_FILE, &names)?;
        self.drop_names(HAND_KEYS_FILE, &names)
    }

    /// The old key from the terminal goes once a new one from the app works: `runtime`'s names out of `_shared.env`.
    pub fn forget_hand_keys(&self, runtime: &str) -> std::io::Result<()> {
        let _keys = self.keys.lock().unwrap_or_else(|e| e.into_inner());
        self.drop_names(HAND_KEYS_FILE, &headless_names(runtime))
    }

    /// Put `keys` in place of `runtime`'s keys in the computer's own key file, other runtimes' kept.
    pub fn set_computer_keys(&self, runtime: &str, keys: &Env) -> std::io::Result<()> {
        let _keys = self.keys.lock().unwrap_or_else(|e| e.into_inner());
        self.replace_keys(COMPUTER_KEYS_FILE, &headless_names(runtime), keys).map(|_| ())
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
            tried.insert(attempt, Instant::now());
        }
        // Counted from here, and only while not signed out: a sign-out marked before this never sees it start, and
        // one marked after it waits for it to end before logging out.
        let _running = {
            let mut running = self.keys.lock().unwrap_or_else(|e| e.into_inner());
            // A key change running for this runtime checks its new key itself and puts it in place.
            if self.signed_out(runtime) || self.key_changes(runtime).1 {
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

/// Run blocking work from async code: off the async workers when the runtime has more than one.
fn blocking<T>(work: impl FnOnce() -> T) -> T {
    match tokio::runtime::Handle::try_current().map(|h| h.runtime_flavor()) {
        Ok(tokio::runtime::RuntimeFlavor::MultiThread) => tokio::task::block_in_place(work),
        _ => work(),
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

    /// A server that answers the model key fetch with whatever `reply` holds at the time, and counts how often it
    /// was asked.
    async fn key_server(reply: &str) -> (Client, Arc<AtomicUsize>, Arc<Mutex<String>>) {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let (asked, body) = (Arc::new(AtomicUsize::new(0)), Arc::new(Mutex::new(reply.to_string())));
        let (count, answer) = (asked.clone(), body.clone());
        tokio::spawn(async move {
            while let Ok((mut sock, _)) = listener.accept().await {
                count.fetch_add(1, Ordering::SeqCst);
                let mut buf = vec![0u8; 8192];
                let _ = sock.read(&mut buf).await;
                let body = answer.lock().unwrap().clone();
                let reply = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
                let _ = sock.write_all(reply.as_bytes()).await;
                let _ = sock.shutdown().await;
            }
        });
        (Client::new(&format!("http://127.0.0.1:{port}"), "runner-token", 5, 0).unwrap(), asked, body)
    }

    #[test]
    fn the_computers_own_key_signs_in_before_the_terminals_and_the_terminals_before_the_companys() {
        let dir = projects();
        let secrets = dir.join("secrets");
        let sign = SignIn::new(&dir);
        assert_eq!(sign.key_source("codex"), None);
        std::fs::write(secrets.join(TEAM_KEYS_FILE), "OPENAI_API_KEY=sk-team-0123456789\n").unwrap();
        assert_eq!(sign.headless_secret("codex"), Some(("OPENAI_API_KEY".into(), "sk-team-0123456789".into())));
        assert_eq!(sign.key_source("codex"), Some(("OPENAI_API_KEY".into(), "shared")));
        assert!(sign.team_key_only("codex"));
        std::fs::write(secrets.join(HAND_KEYS_FILE), "OPENAI_API_KEY='sk-hand-0123456789'\n").unwrap();
        assert_eq!(sign.headless_secret("codex"), Some(("OPENAI_API_KEY".into(), "sk-hand-0123456789".into())));
        assert_eq!(sign.key_source("codex"), Some(("OPENAI_API_KEY".into(), "terminal")));
        assert!(!sign.team_key_only("codex"));
        std::fs::write(secrets.join(COMPUTER_KEYS_FILE), "OPENAI_API_KEY=sk-mine-0123456789\n").unwrap();
        assert_eq!(sign.headless_secret("codex"), Some(("OPENAI_API_KEY".into(), "sk-mine-0123456789".into())));
        assert_eq!(sign.key_source("codex"), Some(("OPENAI_API_KEY".into(), "computer")));
        assert!(!sign.team_key_only("codex"));
        // The computer's own key alone, with the company's beside it: still the computer's.
        std::fs::remove_file(secrets.join(HAND_KEYS_FILE)).unwrap();
        assert_eq!(sign.key_source("codex"), Some(("OPENAI_API_KEY".into(), "computer")));
        assert!(!sign.team_key_only("codex"));
        sign.mark_signed_out("codex").unwrap();
        assert_eq!((sign.headless_secret("codex"), sign.key_source("codex")), (None, None));
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[tokio::test]
    async fn each_key_from_the_app_goes_to_its_own_file_and_one_no_longer_given_goes() {
        let dir = projects();
        let (computer, team) = (dir.join("secrets").join(COMPUTER_KEYS_FILE), dir.join("secrets").join(TEAM_KEYS_FILE));
        std::fs::write(&computer, "ANTHROPIC_API_KEY=kept-computer\n").unwrap();
        std::fs::write(&team, "OTHER=kept\n").unwrap();
        let both = r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-mine-0123456789","source":"computer"},
                                       {"env":"OPENAI_API_KEY","value":"sk-team-0123456789","source":"shared"}]}"#;
        let (client, asked, reply) = key_server(both).await;
        let sign = SignIn::new(&dir);
        assert!(sign.team_model_keys(&client, "codex").await);
        let (c, t) = (read_env(&computer), read_env(&team));
        assert_eq!((c["OPENAI_API_KEY"].as_str(), c["ANTHROPIC_API_KEY"].as_str()), ("sk-mine-0123456789", "kept-computer"));
        assert_eq!((t["OPENAI_API_KEY"].as_str(), t["OTHER"].as_str()), ("sk-team-0123456789", "kept"));
        assert_eq!(std::fs::metadata(&computer).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(sign.key_source("codex"), Some(("OPENAI_API_KEY".into(), "computer")));
        // The computer's own key was taken away in the app: gone from its file, the rest kept; the company's signs in.
        *reply.lock().unwrap() = r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-team-0123456789","source":"shared"}]}"#.into();
        sign.refresh_now("codex");
        assert!(sign.team_model_keys(&client, "codex").await);
        let c = read_env(&computer);
        assert!(!c.contains_key("OPENAI_API_KEY") && c["ANTHROPIC_API_KEY"] == "kept-computer");
        assert_eq!(sign.key_source("codex"), Some(("OPENAI_API_KEY".into(), "shared")));
        // A server that says the login was signed out changes nothing here; its own job does.
        *reply.lock().unwrap() = r#"{"credentials":[],"signed_out":true}"#.into();
        sign.refresh_now("codex");
        assert!(!sign.team_model_keys(&client, "codex").await);
        assert_eq!(read_env(&team)["OPENAI_API_KEY"], "sk-team-0123456789");
        // An older server names no source: its key is the company's.
        *reply.lock().unwrap() = r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-team-rotated-0123"}]}"#.into();
        sign.refresh_now("codex");
        assert!(sign.team_model_keys(&client, "codex").await);
        assert_eq!(read_env(&team)["OPENAI_API_KEY"], "sk-team-rotated-0123");
        assert!(!read_env(&computer).contains_key("OPENAI_API_KEY"));
        sign.refresh_now("codex");
        assert!(!sign.team_model_keys(&client, "codex").await, "the same key again is no change");
        // The company's key taken away too: none is left.
        *reply.lock().unwrap() = r#"{"credentials":[]}"#.into();
        sign.refresh_now("codex");
        assert!(sign.team_model_keys(&client, "codex").await);
        assert_eq!(read_env(&team).get("OTHER").map(String::as_str), Some("kept"));
        assert_eq!(sign.key_source("codex"), None);
        assert_eq!(asked.load(Ordering::SeqCst), 6);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[tokio::test]
    async fn a_key_from_the_app_is_asked_for_again_every_ten_minutes_or_at_once_when_it_changed() {
        let dir = projects();
        let secrets = dir.join("secrets");
        std::fs::write(secrets.join(TEAM_KEYS_FILE), "OPENAI_API_KEY=sk-team-0123456789\n").unwrap();
        let (client, asked, reply) = key_server(r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-team-0123456789","source":"shared"}]}"#).await;
        let sign = SignIn::new(&dir);
        // Held from before a restart: asked once, the same key, no change.
        assert!(!sign.team_model_keys(&client, "codex").await);
        assert_eq!(asked.load(Ordering::SeqCst), 1);
        // Within KEY_REFRESH: not asked again.
        assert!(!sign.team_model_keys(&client, "codex").await);
        assert_eq!(asked.load(Ordering::SeqCst), 1);
        // Rotated in the app: asked at once, and the new key is taken.
        *reply.lock().unwrap() = r#"{"credentials":[{"env":"OPENAI_API_KEY","value":"sk-team-rotated-0123","source":"shared"}]}"#.into();
        sign.refresh_now("codex");
        assert!(sign.team_model_keys(&client, "codex").await);
        assert_eq!(asked.load(Ordering::SeqCst), 2);
        assert_eq!(sign.headless_secret("codex").unwrap().1, "sk-team-rotated-0123");
        // A key from the terminal signs it in: the app is not asked.
        std::fs::write(secrets.join(HAND_KEYS_FILE), "OPENAI_API_KEY=sk-hand-0123456789\n").unwrap();
        sign.refresh_now("codex");
        assert!(!sign.team_model_keys(&client, "codex").await);
        assert_eq!(asked.load(Ordering::SeqCst), 2);
        std::fs::remove_dir_all(&dir).unwrap();
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
