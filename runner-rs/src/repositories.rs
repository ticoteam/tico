//! Base clones for this computer: a supervisor-owned bare mirror of each repository
//! the server lists (`<state>/mirrors/<owner>__<repo>.git`), a base clone made from it in `<projects>/repos/`
//! marked `.git/tico-managed`, their state in `<state>/repositories.json` (the file the Python runner kept,
//! read and written the same way), and the heartbeat's `repositories` rows. Task worktrees are made from the base clones.

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use regex::Regex;
use serde_json::{json, Map, Value};

use crate::client::Client;
use crate::env::Env;
use crate::gitrun::{self, Fault, Out};
use crate::redact;
use crate::util::{epoch_s, iso, text_of};

pub const FETCH_INTERVAL: f64 = 15.0 * 60.0;
pub const RETRY_FIRST: f64 = 5.0 * 60.0;
pub const RETRY_MOST: f64 = 6.0 * 3600.0;
pub const CLONE_TIMEOUT: u64 = 120;
pub const REMOVE_AFTER: f64 = 30.0 * 86400.0;
pub const GB: f64 = 1024.0 * 1024.0 * 1024.0;
const ELSEWHERE: &str = "already exists and is not an empty directory.";

pub fn valid_name(name: &str) -> bool {
    static NAME: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = NAME.get_or_init(|| Regex::new(r"^[A-Za-z0-9-]+/[A-Za-z0-9_.-]+$").unwrap());
    re.is_match(name) && !matches!(name.split('/').nth(1), Some(".") | Some(".."))
}

/// A repository's git address (clients/git_repository.py address): `owner/repo` on GitHub, or an HTTPS, SSH or
/// local file URL, or an scp-style `host:path`.
pub fn address(value: &str) -> Result<String, String> {
    const USE: &str = "Use owner/repo or a Git HTTPS or SSH URL";
    if value.is_empty() || value.len() > 200 || value.chars().any(|c| c.is_whitespace() || (c as u32) < 32) {
        return Err(USE.into());
    }
    if valid_name(value) {
        return Ok(format!("https://github.com/{value}.git"));
    }
    if let Some((scheme, rest)) = value.split_once("://") {
        if ["https", "ssh", "file"].contains(&scheme) && !rest.contains('?') && !rest.contains('#') {
            let (netloc, path) = rest.split_once('/').map(|(n, p)| (n, format!("/{p}"))).unwrap_or((rest, String::new()));
            let (userinfo, host) = netloc.rsplit_once('@').map(|(u, h)| (Some(u), h)).unwrap_or((None, netloc));
            let password = userinfo.is_some_and(|u| u.contains(':'));
            let hostname = host.split(':').next().unwrap_or_default();
            if !(path.is_empty() || password || (scheme == "https" && userinfo.is_some())) {
                if scheme == "file" {
                    if !(netloc.is_empty() || netloc == "localhost") {
                        return Err("Use a local file remote".into());
                    }
                    return Ok(value.into());
                }
                if !hostname.is_empty() {
                    return Ok(value.into());
                }
            }
        }
    }
    static SCP: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let scp = SCP.get_or_init(|| Regex::new(r"^(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+$").unwrap());
    if scp.is_match(value) {
        return Ok(value.into());
    }
    Err(USE.into())
}

pub fn valid_repository(name: &str) -> bool {
    address(name).is_ok()
}

/// The stable folder name of a repository (git_repository.folder).
pub fn folder(value: &str) -> Result<String, String> {
    let url = address(value)?;
    if url == format!("https://github.com/{value}.git") {
        return Ok(value.replace('/', "__"));
    }
    let path = match url.split_once("://") {
        Some((_, rest)) => rest.split_once('/').map(|(_, p)| p.to_string()).unwrap_or_default(),
        None => url.split_once(':').map(|(_, p)| p.to_string()).unwrap_or_default(),
    };
    let trimmed = path.trim_matches('/');
    let trimmed = trimmed.strip_suffix(".git").unwrap_or(trimmed);
    let parts: Vec<&str> = trimmed.split('/').collect();
    let tail = &parts[parts.len().saturating_sub(2)..];
    static BAD: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let bad = BAD.get_or_init(|| Regex::new(r"[^A-Za-z0-9_.-]").unwrap());
    let name: String = tail.iter().map(|p| bad.replace_all(p, "-").into_owned()).collect::<Vec<_>>().join("__");
    let name: String = name.chars().take(100).collect();
    let name = if name.is_empty() { "repo".to_string() } else { name };
    Ok(format!("{name}-{}", &gitrun::sha256_hex(url.as_bytes())[..12]))
}

/// Whether a remote URL is this repository's address (git_repository.matches).
pub fn matches(url: &str, value: &str) -> bool {
    address(value).is_ok_and(|a| url.trim_end_matches('/') == a.trim_end_matches('/'))
}

pub fn base_folder(name: &str) -> String {
    let key = if valid_name(name) { name.to_lowercase() } else { name.to_string() };
    folder(&key).unwrap_or(key)
}

/// Whether a remote URL names `owner/name` on github.com, however it is spelled (git_credentials._same_repository).
pub fn same_repository(url: &str, repository: &str) -> bool {
    let text = url.trim().to_lowercase();
    let text = text.strip_suffix('/').unwrap_or(&text);
    let text = text.strip_suffix(".git").unwrap_or(text);
    let repo = repository.to_lowercase();
    text.ends_with(&format!("github.com/{repo}")) || text.ends_with(&format!("github.com:{repo}"))
}

/// Whether `url` is GitHub's address for `full_name`, HTTPS or SSH, no user, no other host.
pub fn github_origin(url: &str, full_name: &str) -> bool {
    let pattern = format!(r"(?i)^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/){}(?:\.git)?/?$", regex::escape(full_name));
    Regex::new(&pattern).is_ok_and(|re| re.is_match(url.trim()))
}

/// The variables that hand git a GitHub token (git_credentials.environment).
pub fn token_env(token: &str) -> Env {
    let mut env = Env::new();
    for (k, v) in [
        ("GH_TOKEN", token),
        ("GITHUB_TOKEN", token),
        ("GIT_TERMINAL_PROMPT", "0"),
        ("GIT_CONFIG_COUNT", "3"),
        ("GIT_CONFIG_KEY_0", crate::gitcred::KEY),
        ("GIT_CONFIG_VALUE_0", ""),
        ("GIT_CONFIG_KEY_1", crate::gitcred::KEY),
        ("GIT_CONFIG_VALUE_1", crate::gitcred::STATIC_HELPER),
        ("GIT_CONFIG_KEY_2", "credential.https://github.com.useHttpPath"),
        ("GIT_CONFIG_VALUE_2", "true"),
    ] {
        env.insert(k.into(), v.into());
    }
    env
}

pub fn machine_error(name: &str, detail: &str, env: &Env) -> String {
    let reason = redact::git_failure(detail, &redact::env_secrets(env), 240);
    let reason = if reason.is_empty() { "Git command failed".to_string() } else { reason };
    format!("git could not reach {name} with this computer's git login: {reason}")
}

fn git_error(what: &str, name: &str, done: &Out, env: &Env) -> String {
    let reason = redact::git_failure(&done.stderr, &redact::env_secrets(env), 240);
    let reason = if reason.is_empty() { format!("exit {}", done.code) } else { reason };
    format!("{what} of {name} failed: {reason}")
}

fn mirror_broken(stderr: &str, mirror: &Path) -> bool {
    static BROKEN: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = BROKEN.get_or_init(|| {
        Regex::new(
            r"(?i)corrupt|bad object|missing (?:blob|tree|commit|object)|unable to read|did not send all|index-pack|unpack-objects|is not a valid|not a git repository|could not (?:open|read)|permission denied|unable to create temporary",
        )
        .unwrap()
    });
    re.is_match(stderr) || stderr.contains(&mirror.display().to_string())
}

pub fn wait_words(seconds: f64) -> String {
    let minutes = (seconds / 60.0).round() as i64;
    if minutes < 120 { format!("{minutes} minutes") } else { format!("{} hours", minutes / 60) }
}

pub fn mirror_warning(mirror: &Path) -> String {
    let age = std::fs::read_to_string(mirror.join("tico-fetched-at"))
        .ok()
        .and_then(|t| t.trim().parse::<f64>().ok())
        .map(|at| format!("{} minutes old", ((epoch_s() - at) / 60.0).max(0.0) as i64))
        .unwrap_or_else(|| "age unknown".into());
    format!("Could not refresh origin; using the local mirror ({age})")
}

fn file_uri(path: &Path) -> String {
    url::Url::from_file_path(path).map(|u| u.to_string()).unwrap_or_else(|_| format!("file://{}", path.display()))
}

fn uri_path(uri: &str) -> PathBuf {
    url::Url::parse(uri).ok().and_then(|u| u.to_file_path().ok()).unwrap_or_else(|| PathBuf::from(uri.trim_start_matches("file://")))
}

/// `env` with a global git config that trusts only `dirs` (safe_git.trusting): a local repository another user
/// owns, which `-c safe.directory` does not reach in the upload-pack git starts.
pub fn trusting(env: &Env, dirs: &[&Path]) -> Env {
    let mut seen = HashSet::new();
    let mut lines = String::new();
    for d in dirs {
        let d = d.display().to_string();
        if !d.is_empty() && !d.contains('\n') && seen.insert(d.clone()) {
            lines.push_str(&format!("\tdirectory = \"{}\"\n", d.replace('\\', "\\\\").replace('"', "\\\"")));
        }
    }
    let folder = std::env::temp_dir().join(format!("tico-trust-{}", std::process::id()));
    let _ = std::fs::create_dir_all(&folder);
    let path = folder.join(format!("{}.gitconfig", &gitrun::sha256_hex(lines.as_bytes())[..20]));
    if !path.exists() {
        let temp = folder.join(format!("{}.tmp", uuid::Uuid::new_v4().simple()));
        if std::fs::write(&temp, format!("[safe]\n{lines}")).is_ok() {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(&temp, std::fs::Permissions::from_mode(0o644));
            let _ = std::fs::rename(&temp, &path);
        }
    }
    let mut out = env.clone();
    out.insert("GIT_CONFIG_GLOBAL".into(), path.display().to_string());
    out
}

/// Bytes of the files under `root` (symlinks not followed) and the newest modification time.
pub fn tree_size(root: &Path) -> (u64, f64) {
    let (mut size, mut newest) = (0u64, 0f64);
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else { continue };
        for entry in entries.flatten() {
            let Ok(meta) = entry.path().symlink_metadata() else { continue };
            if meta.file_type().is_symlink() {
                continue;
            }
            if meta.is_dir() {
                stack.push(entry.path());
            } else if meta.is_file() {
                size += meta.len();
                if let Ok(m) = meta.modified() {
                    let at = m.duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
                    newest = newest.max(at);
                }
            }
        }
    }
    (size, newest)
}

fn is_symlink(p: &Path) -> bool {
    p.symlink_metadata().is_ok_and(|m| m.file_type().is_symlink())
}

fn real_dir(p: &Path) -> bool {
    p.symlink_metadata().is_ok_and(|m| m.is_dir())
}

fn round1(v: f64) -> f64 {
    (v * 10.0).round() / 10.0
}

/// Why a step failed: a message for the row, a timeout, a full disk, or something else.
enum Fail {
    Msg(String),
    Timeout(u64),
    Disk,
    Other,
}

impl From<Fault> for Fail {
    fn from(f: Fault) -> Fail {
        match f {
            Fault::Timeout(s) => Fail::Timeout(s),
            Fault::Stopped => Fail::Msg(gitrun::STOPPED.into()),
            Fault::Io(_) => Fail::Other,
        }
    }
}

impl From<String> for Fail {
    fn from(m: String) -> Fail {
        Fail::Msg(m)
    }
}

impl From<&str> for Fail {
    fn from(m: &str) -> Fail {
        Fail::Msg(m.into())
    }
}

impl From<std::io::Error> for Fail {
    fn from(e: std::io::Error) -> Fail {
        if e.raw_os_error() == Some(libc::ENOSPC) { Fail::Disk } else { Fail::Other }
    }
}

fn msg(text: impl Into<String>) -> Fail {
    Fail::Msg(text.into())
}

fn hooks_args() -> Vec<String> {
    vec!["git".into(), "-c".into(), format!("core.hooksPath={}", crate::safegit::hooks_dir().display())]
}

async fn run(prefix: &[String], args: &[&str], env: &Env, timeout: u64) -> Result<Out, Fail> {
    let mut argv = hooks_args();
    argv.extend(prefix.iter().cloned());
    argv.extend(args.iter().map(|a| a.to_string()));
    Ok(gitrun::run(&argv, env, None, timeout).await?)
}

async fn run_as_bot(prefix: &[String], args: &[&str], env: &Env, timeout: u64) -> Result<Out, Fail> {
    let mut argv = hooks_args();
    argv.extend(prefix.iter().cloned());
    argv.extend(args.iter().map(|a| a.to_string()));
    Ok(gitrun::run_as_bot(&argv, env, None, timeout).await?)
}

/// The runner's repositories, for the credential socket's mirror refresh.
pub static MIRRORS: std::sync::OnceLock<Arc<Repositories>> = std::sync::OnceLock::new();

pub struct Repositories {
    pub root: PathBuf,
    state_file: PathBuf,
    pub mirrors: PathBuf,
    client: Client,
    rows: Mutex<Map<String, Value>>,
    visible: AtomicBool,
    pending: Mutex<Option<tokio::task::JoinHandle<()>>>,
    sync_lock: tokio::sync::Mutex<()>,
}

impl Repositories {
    pub fn new(workspace: &Path, state_dir: &Path, client: Client) -> Repositories {
        let state_file = state_dir.join("repositories.json");
        let mut rows = Map::new();
        if let Some(Value::Object(saved)) = std::fs::read_to_string(&state_file).ok().and_then(|t| serde_json::from_str(&t).ok()) {
            for (name, row) in saved {
                if valid_repository(&name) && row.is_object() {
                    rows.insert(name, row);
                }
            }
        }
        // Restarting the runner is how a person says "try again now": a failure's wait does not outlive it.
        for row in rows.values_mut() {
            if row["state"] == "failed" || crate::util::truthy(row.get("failures")) {
                let r = row.as_object_mut().unwrap();
                r.remove("attempted_at");
                r.insert("failures".into(), json!(0));
                r.insert("retry_delay".into(), json!(FETCH_INTERVAL as i64));
            }
        }
        Repositories {
            root: workspace.join("repos"),
            state_file,
            mirrors: state_dir.join("mirrors"),
            client,
            rows: Mutex::new(rows),
            visible: AtomicBool::new(false),
            pending: Mutex::new(None),
            sync_lock: tokio::sync::Mutex::new(()),
        }
    }

    fn path(&self, name: &str) -> Result<PathBuf, Fail> {
        let full = self.rows.lock().unwrap().get(name).map(|r| text_of(&r["full_name"])).filter(|n| !n.is_empty()).unwrap_or_else(|| name.into());
        let path = self.root.join(base_folder(&full));
        let parent_ok = path.parent().and_then(|p| p.canonicalize().ok()) == self.root.canonicalize().ok();
        if is_symlink(&self.root) || is_symlink(&path) || (self.root.exists() && !parent_ok) {
            return Err(msg("Repository folder points outside repos; left as it is"));
        }
        Ok(path)
    }

    fn save(&self) -> std::io::Result<()> {
        if let Some(parent) = self.state_file.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let temp = self.state_file.with_extension("tmp");
        let text = crate::util::py_dumps(&Value::Object(self.rows.lock().unwrap().clone()));
        std::fs::write(&temp, text)?;
        std::fs::rename(&temp, &self.state_file)
    }

    fn report_row(row: &Value) -> Value {
        let mut out = Map::new();
        for k in ["full_name", "state", "last_fetch", "size_mb", "error"] {
            if let Some(v) = row.get(k) {
                out.insert(k.into(), v.clone());
            }
        }
        Value::Object(out)
    }

    pub fn report(&self) -> Vec<Value> {
        if !self.visible.load(Ordering::SeqCst) {
            return vec![];
        }
        self.rows.lock().unwrap().values().map(Self::report_row).collect()
    }

    /// What `doctor` prints: the server's current list with each one's saved status,
    /// without cloning or cleanup; empty when the server cannot say.
    pub async fn inspect(&self) -> Vec<Value> {
        let Ok(reply) = self.client.get("runners/me/repositories").await else { return vec![] };
        let Some(repos) = reply["repositories"].as_array() else { return vec![] };
        let rows = self.rows.lock().unwrap();
        repos
            .iter()
            .filter_map(|r| r["full_name"].as_str().filter(|n| valid_name(n)))
            .map(|name| {
                let row = rows.get(&name.to_lowercase()).cloned();
                Self::report_row(&row.unwrap_or_else(|| json!({"full_name": name, "state": "cloning", "last_fetch": null, "size_mb": 0})))
            })
            .collect()
    }

    fn update_row(&self, name: &str, f: impl FnOnce(&mut Map<String, Value>)) {
        if let Some(Value::Object(r)) = self.rows.lock().unwrap().get_mut(name) {
            f(r);
        }
    }

    fn row(&self, name: &str) -> Value {
        self.rows.lock().unwrap().get(name).cloned().unwrap_or(Value::Null)
    }

    /// GET the repositories this computer should hold, adopt marked clones, start the removal clock for ones it
    /// no longer should, and start at most one sync in the background. None when the server could not say.
    pub async fn poll(self: &Arc<Self>) -> Option<Vec<Value>> {
        if gitrun::stopping() {
            return Some(self.report());
        }
        let reply = match self.client.get("runners/me/repositories").await {
            Ok(r) => r,
            Err(_) => {
                self.visible.store(false, Ordering::SeqCst);
                return None;
            }
        };
        let repos = reply["repositories"].as_array()?.clone();
        if repos.iter().any(|r| !r.is_object() || !valid_name(&text_of(&r["full_name"]))) {
            return None;
        }
        self.visible.store(true, Ordering::SeqCst);
        if self.pending.lock().unwrap().as_ref().is_some_and(|p| !p.is_finished()) {
            return Some(self.report());
        }
        let now = epoch_s();
        let wanted: Map<String, Value> = repos.iter().map(|r| (text_of(&r["full_name"]).to_lowercase(), r.clone())).collect();
        let adopted = self.markers();
        let selected = {
            let mut rows = self.rows.lock().unwrap();
            for (name, is_adopted) in adopted {
                rows.entry(name.clone()).or_insert_with(|| {
                    let mut row = json!({"full_name": name, "state": "cloned", "managed": true, "left_at": now, "size_mb": 0});
                    if is_adopted {
                        row["adopted"] = json!(true);
                    }
                    row
                });
            }
            let names: Vec<String> = rows.keys().cloned().collect();
            for name in names {
                if wanted.contains_key(&name) {
                    rows[&name].as_object_mut().unwrap().remove("left_at");
                } else if rows[&name]["state"] == "removed" {
                    rows.remove(&name);
                } else if rows[&name].get("left_at").is_none() {
                    rows[&name]["left_at"] = json!(now);
                }
            }
            for (name, repo) in &wanted {
                rows.entry(name.clone()).or_insert_with(|| json!({"full_name": repo["full_name"], "state": "cloning", "last_fetch": null, "size_mb": 0}));
            }
            let mut due: Vec<String> = wanted
                .keys()
                .filter(|n| {
                    let r = &rows[*n];
                    now - r["attempted_at"].as_f64().unwrap_or(0.0) >= r["retry_delay"].as_f64().unwrap_or(FETCH_INTERVAL)
                        && now - r["fetched_at"].as_f64().unwrap_or(0.0) >= FETCH_INTERVAL
                })
                .cloned()
                .collect();
            due.sort_by(|a, b| rows[a]["attempted_at"].as_f64().unwrap_or(0.0).total_cmp(&rows[b]["attempted_at"].as_f64().unwrap_or(0.0)));
            let selected = due.into_iter().next();
            if let Some(s) = &selected {
                rows[s]["attempted_at"] = json!(now);
            }
            selected
        };
        if self.save().is_err() {
            for row in self.rows.lock().unwrap().values_mut() {
                row["state"] = json!("failed");
                row["error"] = json!("Could not save repository state; free disk space and check permissions");
            }
            return Some(self.report());
        }
        let me = self.clone();
        *self.pending.lock().unwrap() = Some(tokio::spawn(async move { me.sync(wanted, selected, now).await }));
        Some(self.report())
    }

    /// `repos/*/.git/tico-managed` markers: (lowercased name, adopted) of each base clone Tico made or took over.
    fn markers(&self) -> Vec<(String, bool)> {
        let mut out = vec![];
        let Ok(entries) = std::fs::read_dir(&self.root) else { return out };
        for entry in entries.flatten() {
            let folder = entry.file_name().to_string_lossy().to_string();
            let marker = entry.path().join(".git").join("tico-managed");
            let Ok(meta) = marker.symlink_metadata() else { continue };
            if !meta.is_file() || meta.len() > 1024 {
                continue;
            }
            let Ok(data) = std::fs::read(&marker) else { continue };
            let stored: Value = if data.is_empty() { json!({}) } else { serde_json::from_slice(&data).unwrap_or(Value::Null) };
            if !stored.is_object() {
                continue;
            }
            let fallback = folder.replacen("__", "/", 1);
            let name = Some(text_of(&stored["full_name"])).filter(|n| !n.is_empty()).unwrap_or(fallback);
            let name = if valid_name(&name) { name.to_lowercase() } else { name };
            if valid_repository(&name) && base_folder(&name) == folder {
                out.push((name, stored["adopted"] == true));
            }
        }
        out
    }

    async fn sync(self: Arc<Self>, wanted: Map<String, Value>, selected: Option<String>, now: f64) {
        let stale: Vec<String> = self
            .rows
            .lock()
            .unwrap()
            .iter()
            .filter(|(n, r)| !wanted.contains_key(*n) && now - r["left_at"].as_f64().unwrap_or(now) >= REMOVE_AFTER)
            .map(|(n, _)| n.clone())
            .collect();
        for name in stale {
            let _sync = self.sync_lock.lock().await;
            let result: Result<(), Fail> = async {
                let path = self.path(&name)?;
                let row = self.row(&name);
                if row["adopted"] == true && path.exists() {
                    // A clone a person made is theirs: Tico stops looking after it and leaves it where it is.
                    let marker = path.join(".git").join("tico-managed");
                    if marker.symlink_metadata().is_ok_and(|m| m.is_file()) {
                        std::fs::remove_file(marker)?;
                    }
                } else if row["managed"] == true && path.exists() {
                    if let Some(kept) = base_kept(&path).await? {
                        self.update_row(&name, |r| {
                            r.insert("error".into(), json!(kept));
                        });
                        return Ok(());
                    }
                    std::fs::remove_dir_all(&path)?;
                }
                if valid_name(&name) {
                    self.discard_mirror(&name).await?;
                }
                self.update_row(&name, |r| {
                    r.insert("state".into(), json!("removed"));
                    r.insert("size_mb".into(), json!(0));
                    r.remove("error");
                });
                Ok(())
            }
            .await;
            if result.is_err() {
                self.update_row(&name, |r| {
                    r.insert("state".into(), json!("failed"));
                    r.insert("error".into(), json!("Could not remove base clone safely; check repos folder permissions"));
                });
            }
        }
        if let Some(s) = &selected {
            self.update(s, &wanted[s], now).await;
        }
        if self.save().is_err() {
            if let Some(s) = &selected {
                self.update_row(s, |r| {
                    r.insert("state".into(), json!("failed"));
                    r.insert("error".into(), json!("Could not save repository state; free disk space and check permissions"));
                });
            }
        }
    }

    fn mirror_path(&self, name: &str) -> Result<PathBuf, Fail> {
        let path = self.mirrors.join(format!("{}.git", name.to_lowercase().replace('/', "__")));
        if is_symlink(&self.mirrors) || is_symlink(&path) {
            return Err(msg("Mirror contains a symlink; repair the computer mirror folder"));
        }
        if path.exists() {
            let mut stack = vec![path.clone()];
            while let Some(dir) = stack.pop() {
                for entry in std::fs::read_dir(&dir).into_iter().flatten().flatten() {
                    if is_symlink(&entry.path()) {
                        return Err(msg("Mirror contains a symlink; repair the computer mirror folder"));
                    }
                    if real_dir(&entry.path()) {
                        stack.push(entry.path());
                    }
                }
            }
        }
        Ok(path)
    }

    async fn adoption_problem(&self, path: &Path, full_name: &str) -> String {
        use std::os::unix::fs::MetadataExt;
        let git_dir = path.join(".git");
        if !real_dir(&git_dir) {
            return "it is not a Git checkout".into();
        }
        let me = unsafe { libc::geteuid() };
        if [path, git_dir.as_path()].iter().any(|p| p.symlink_metadata().map(|m| m.uid()).unwrap_or(u32::MAX) != me) {
            return "it belongs to another user".into();
        }
        if git_dir.join("objects/info/alternates").exists() || git_dir.join("commondir").exists() {
            return "it shares its objects with another repository".into();
        }
        let env = crate::safegit::clean_environment(&crate::env::process_environment());
        let get = |key: &'static str, boolean: bool| {
            let path = path.to_path_buf();
            let env = env.clone();
            async move {
                let mut args = vec!["config", "--local", "--get"];
                if boolean {
                    args.push("--bool");
                }
                args.push(key);
                gitrun::git(&path, &env, &args, 10).await.unwrap_or_default()
            }
        };
        if get("core.worktree", false).await.ok() {
            return "its work tree is somewhere else".into();
        }
        if get("core.bare", true).await.stdout.trim() == "true" {
            return "it is a bare repository".into();
        }
        let origin = get("remote.origin.url", false).await.stdout.trim().to_string();
        if !github_origin(&origin, full_name) {
            let shown: String = redact::scrub_credentials(&origin, &[]).chars().take(120).collect();
            return format!("its origin is {}, not {full_name}", if shown.is_empty() { "not set".into() } else { shown });
        }
        String::new()
    }

    async fn discard_mirror(&self, name: &str) -> Result<(), Fail> {
        let mirror = self.mirror_path(name)?;
        if mirror.exists() {
            if !crate::gitlocks::idle(&mirror).await {
                return Err(msg("Mirror has an active or recent Git lock; left intact for the next retry"));
            }
            std::fs::remove_dir_all(mirror)?;
        }
        Ok(())
    }

    async fn update(&self, name: &str, repo: &Value, now: f64) {
        let _sync = self.sync_lock.lock().await;
        let mut token = String::new();
        let mut created = false;
        let result = self.update_inner(name, repo, now, &mut token, &mut created).await;
        let Err(fail) = result else { return };
        if created {
            if let Ok(path) = self.path(name) {
                discard_failed_clone(&path, true).await;
                self.update_row(name, |r| {
                    r.remove("partial");
                });
            }
        }
        let full = text_of(&repo["full_name"]);
        let error = match fail {
            Fail::Timeout(s) => format!("Git timed out after {s} seconds syncing {full}; a large repository or a slow network needs longer"),
            Fail::Msg(m) => redact::scrub_credentials(&m, &if token.is_empty() { vec![] } else { vec![token.clone()] }),
            Fail::Disk => "Not enough disk space; free space on the workspace volume".into(),
            Fail::Other => "Could not sync base clone; check GitHub connection, network, disk space and folder permissions".into(),
        };
        self.update_row(name, |r| {
            let failures = r.get("failures").and_then(|f| f.as_i64()).unwrap_or(0) + 1;
            let delay = (RETRY_FIRST * 2f64.powi((failures - 1).min(10) as i32)).min(RETRY_MOST);
            r.insert("state".into(), json!("failed"));
            r.insert("failures".into(), json!(failures));
            r.insert("retry_delay".into(), json!(delay as i64));
            r.insert("attempted_at".into(), json!(epoch_s()));
            r.insert(
                "error".into(),
                json!(format!("{}. Retrying in {}, or restart the computer's runner to retry now", error.trim_end_matches('.'), wait_words(delay))),
            );
        });
    }

    async fn update_inner(&self, name: &str, repo: &Value, now: f64, token_out: &mut String, created: &mut bool) -> Result<(), Fail> {
        let full = text_of(&repo["full_name"]);
        let path = self.path(name)?;
        let mut exists = path.exists();
        let marker = path.join(".git").join("tico-managed");
        if exists && self.row(name)["partial"] == true && !marker.exists() {
            if tico_half_clone(&path).await {
                // A clone this runner started and never finished: nothing in it is anyone's work.
                discard_failed_clone(&path, true).await;
                exists = path.exists();
            } else {
                self.update_row(name, |r| {
                    r.remove("partial");
                    r.remove("managed");
                });
            }
        }
        let managed = self.row(name)["managed"] == true || marker.symlink_metadata().is_ok_and(|m| m.is_file());
        if exists && (!real_dir(&path.join(".git"))) {
            return Err(msg("Repository folder already exists and is not a managed base clone; left as it is"));
        }
        if exists && !managed {
            let problem = self.adoption_problem(&path, &full).await;
            if !problem.is_empty() {
                return Err(msg(format!("Repository folder already exists and is not a managed base clone ({problem}); left as it is")));
            }
            self.update_row(name, |r| {
                r.insert("managed".into(), json!(true));
                r.insert("adopted".into(), json!(true));
            });
        }
        if exists {
            // This validated base clone is shared with bot worktrees; authenticated mirrors stay private.
            crate::isolation::adopt(&[path.clone()]);
        }
        let volumes = [
            if self.root.exists() { self.root.clone() } else { self.root.parent().unwrap_or(&self.root).to_path_buf() },
            if self.mirrors.exists() { self.mirrors.clone() } else { self.state_file.parent().unwrap_or(&self.mirrors).to_path_buf() },
        ];
        let (volume, free) = volumes
            .iter()
            .map(|p| (p.clone(), crate::readiness::disk_usage(p).map(|d| d.1).unwrap_or(u64::MAX)))
            .min_by_key(|(_, f)| *f)
            .unwrap();
        let size_kb = repo["size_kb"].as_f64().unwrap_or_else(|| self.row(name)["size_mb"].as_f64().unwrap_or(0.0) * 1024.0) as u64;
        let floor = 5.0 * GB + 2.0 * size_kb as f64 * 1024.0;
        if (free as f64) < floor {
            let verb = if exists { "fetch" } else { "clone" };
            let message = format!(
                "Not enough disk to {verb} {full}: {:.1} GB free, needs {} GB. Free space on {}",
                free as f64 / GB,
                py_g(floor / GB),
                volume.display()
            );
            self.update_row(name, |r| {
                r.insert("state".into(), json!("disk_low"));
                r.insert("error".into(), json!(message));
            });
            return Ok(());
        }
        let clean = crate::safegit::clean_environment(&crate::env::process_environment());
        let mirror = self.mirror_path(name)?;
        std::fs::create_dir_all(&self.mirrors)?;
        set_mode(&self.mirrors, 0o755);
        let granted = self
            .client
            .post("runners/me/repositories/token", &json!({}), None)
            .await
            .map_err(|e| msg(crate::gitcred::token_problem(&e, &full)))?;
        let token = text_of(&granted["token"]);
        if token.is_empty() {
            return Err(msg(format!("Tico could not get a GitHub read token for {full}: the GitHub App gave none; check Settings > Tools > GitHub")));
        }
        *token_out = token.clone();
        let covered = granted["repositories"].as_array().is_some_and(|a| a.iter().any(|n| text_of(n).to_lowercase() == name));
        if !covered {
            return Err(msg(format!(
                "Tico could not get a GitHub read token for {full}: the GitHub App is not installed on it or cannot reach it; add it to the App's repository access on GitHub"
            )));
        }
        let mut env = clean.clone();
        env.extend(token_env(&token));
        let url = format!("https://github.com/{full}.git");
        let prefix: Vec<String> = [
            "-c", "core.fsmonitor=false", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "-c", "http.followRedirects=false", "-c",
            "http.sslVerify=true", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "protocol.file.allow=never",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        let timeout = (size_kb / 1024).clamp(900, 7200);
        let mut branch = text_of(&repo["default_branch"]);
        if branch.is_empty() {
            let result = run(&prefix, &["ls-remote", "--symref", &url, "HEAD"], &env, timeout).await?;
            let found = Regex::new(r"(?m)^ref: refs/heads/(.+)\tHEAD$").unwrap().captures(&result.stdout).map(|c| c[1].to_string());
            match found {
                Some(b) if result.ok() => branch = b,
                _ if !result.ok() => return Err(msg(git_error("Finding the default branch", &full, &result, &env))),
                _ => return Err(msg("Could not find the default branch; check GitHub access")),
            }
        }
        self.fill_mirror(name, &mirror, &prefix, &branch, &url, &full, &clean, &env, timeout, now, true).await?;
        let trusted = trusting(&clean, &[&mirror.canonicalize().unwrap_or(mirror.clone()), &mirror]);
        let mut local: Vec<String> = crate::safegit::prefix();
        for d in [mirror.canonicalize().unwrap_or(mirror.clone()), mirror.clone(), path.canonicalize().unwrap_or(path.clone())] {
            local.extend(["-c".into(), format!("safe.directory={}", d.display())]);
        }
        local.extend(
            ["-c", "core.fsmonitor=false", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "-c", "protocol.allow=never", "-c", "protocol.file.allow=always"]
                .iter()
                .map(|s| s.to_string()),
        );
        let mirror_uri = file_uri(&mirror);
        let path_s = path.display().to_string();
        if !exists {
            crate::isolation::mkdir(&self.root, 0o755)?;
            crate::isolation::chown(&self.root);
            *created = true;
            self.update_row(name, |r| {
                r.insert("state".into(), json!("cloning"));
                r.insert("managed".into(), json!(true));
                r.insert("partial".into(), json!(true));
            });
            let _ = self.save();
            let clone_args = ["clone", "--quiet", "--single-branch", "--no-hardlinks", "--origin", "tico-mirror", "--branch", &branch, "--", &mirror_uri, &path_s];
            let mut done = run_as_bot(&local, &clone_args, &trusted, timeout).await?;
            if !done.ok() && !done.stderr.contains("already exists") {
                // The mirror itself can be what is broken while GitHub is fine: rebuild it once and clone again.
                let first = git_error("Cloning the local mirror", &full, &done, &clean);
                discard_failed_clone(&path, true).await;
                self.discard_mirror(name).await?;
                self.fill_mirror(name, &mirror, &prefix, &branch, &url, &full, &clean, &env, timeout, now, false).await?;
                done = run_as_bot(&local, &clone_args, &trusted, timeout).await?;
                if !done.ok() {
                    let again = redact::git_failure(&done.stderr, &redact::env_secrets(&clean), 200);
                    let again = if again.is_empty() { format!("exit {}", done.code) } else { again };
                    *created = !done.stderr.contains("already exists");
                    return Err(msg(format!("{first}; rebuilt the mirror from GitHub and cloning it failed again: {again}")));
                }
            } else if !done.ok() {
                // A turn's own clone made the folder meanwhile: that one is not this clone's to remove.
                *created = false;
                return Err(msg(git_error("Cloning the local mirror", &full, &done, &clean)));
            }
        }
        let mut settings: Vec<(String, String)> =
            vec![("remote.tico-mirror.url".into(), mirror_uri.clone()), ("remote.tico-mirror.fetch".into(), format!("+refs/heads/{branch}:refs/remotes/tico-mirror/{branch}"))];
        let adopted = self.row(name)["adopted"] == true;
        if !adopted {
            // An adopted clone keeps the origin and branch settings its owner chose (an SSH remote, say).
            settings.extend([
                ("remote.origin.url".into(), url.clone()),
                ("remote.origin.pushurl".into(), url.clone()),
                ("remote.origin.fetch".into(), "+refs/heads/*:refs/remotes/origin/*".into()),
                (format!("branch.{branch}.remote"), "origin".into()),
                (format!("branch.{branch}.merge"), format!("refs/heads/{branch}")),
            ]);
        }
        configure_base(&path, &mirror_uri, &branch, &settings, &local, &trusted, &full).await?;
        let fetch_args_owned = [format!("+refs/heads/{branch}:refs/remotes/origin/{branch}"), format!("+refs/heads/{branch}:refs/remotes/tico-mirror/{branch}")];
        let fetch_args = ["-C", &path_s, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--", &mirror_uri, &fetch_args_owned[0], &fetch_args_owned[1]];
        let mut done = run_as_bot(&local, &fetch_args, &trusted, timeout).await?;
        if !done.ok() {
            let first = git_error(&format!("Git {} from the local mirror", if exists { "fetch" } else { "clone" }), &full, &done, &clean);
            self.discard_mirror(name).await?;
            self.fill_mirror(name, &mirror, &prefix, &branch, &url, &full, &clean, &env, timeout, now, false).await?;
            done = run_as_bot(&local, &fetch_args, &trusted, timeout).await?;
            if !done.ok() {
                let again = redact::git_failure(&done.stderr, &redact::env_secrets(&clean), 200);
                let again = if again.is_empty() { format!("exit {}", done.code) } else { again };
                return Err(msg(format!("{first}; rebuilt the mirror from GitHub and fetching it failed again: {again}")));
            }
        }
        let head = format!("refs/remotes/origin/{branch}");
        if !run_as_bot(&local, &["-C", &path_s, "symbolic-ref", "refs/remotes/origin/HEAD", &head], &clean, 10).await?.ok() {
            return Err(msg("Could not record base default branch; check permissions"));
        }
        mark_managed(&path, if adopted { Some(&full) } else { None }, adopted).map_err(msg)?;
        let (bytes, _) = tree_size(&path);
        let size = round1(bytes as f64 / (1024.0 * 1024.0));
        self.update_row(name, |r| {
            r.insert("state".into(), json!("cloned"));
            r.insert("managed".into(), json!(true));
            r.insert("fetched_at".into(), json!(now));
            r.insert("last_fetch".into(), iso(&json!(now)));
            r.insert("size_mb".into(), json!(size));
            r.remove("error");
            r.remove("partial");
            r.insert("failures".into(), json!(0));
            r.insert("retry_delay".into(), json!(FETCH_INTERVAL as i64));
        });
        Ok(())
    }

    /// Create the mirror if it is missing and fetch the default branch into it from GitHub.
    #[allow(clippy::too_many_arguments)]
    async fn fill_mirror(
        &self,
        name: &str,
        mirror: &Path,
        base_prefix: &[String],
        branch: &str,
        url: &str,
        full: &str,
        clean: &Env,
        env: &Env,
        timeout: u64,
        now: f64,
        rebuild: bool,
    ) -> Result<(), Fail> {
        let result = self.fill_mirror_once(mirror, base_prefix, branch, url, full, clean, env, timeout, now).await;
        let repair = matches!(&result, Err(Fail::Msg(m)) if m.contains("Git config") || m.contains("mirror default branch") || mirror_broken(m, mirror));
        if repair && rebuild {
            self.discard_mirror(name).await?;
            return self.fill_mirror_once(mirror, base_prefix, branch, url, full, clean, env, timeout, now).await;
        }
        result
    }

    #[allow(clippy::too_many_arguments)]
    async fn fill_mirror_once(
        &self, mirror: &Path, base_prefix: &[String], branch: &str, url: &str, full: &str,
        clean: &Env, env: &Env, timeout: u64, now: f64,
    ) -> Result<(), Fail> {
        let mut prefix = base_prefix.to_vec();
        let mirror_s = mirror.display().to_string();
        if !mirror.exists() && !run(&prefix, &["init", "--bare", "--quiet", &format!("--initial-branch={branch}"), &mirror_s], clean, 10).await?.ok() {
            return Err(msg("Could not create mirror; check disk space and permissions"));
        }
        prefix.extend(mirror_config_overrides(&prefix, mirror, clean).await?);
        let refspec = format!("+refs/heads/{branch}:refs/heads/{branch}");
        let done = run(&prefix, &["-C", &mirror_s, "fetch", "--quiet", "--prune", "--no-tags", "--no-write-fetch-head", "--", url, &refspec], env, timeout).await?;
        if !done.ok() {
            return Err(msg(git_error("Git mirror fetch", full, &done, env)));
        }
        if !run(&prefix, &["-C", &mirror_s, "symbolic-ref", "HEAD", &format!("refs/heads/{branch}")], clean, 10).await?.ok() {
            return Err(msg("Could not set mirror default branch"));
        }
        mirror_permissions(mirror);
        std::fs::write(mirror.join("tico-fetched-at"), py_float(now))?;
        Ok(())
    }

    /// A bounded refresh a turn asked for through the credential socket; the token never enters the base.
    pub async fn refresh_mirror(&self, name: &str, token: &str) -> Result<Value, String> {
        let _sync = self.sync_lock.lock().await;
        if !valid_name(name) {
            return Err("Invalid mirror repository".into());
        }
        let fail = |f: Fail| match f {
            Fail::Msg(m) => m,
            Fail::Timeout(_) => "Mirror fetch timed out".into(),
            _ => "Mirror refresh failed".into(),
        };
        let mirror = self.mirror_path(name).map_err(fail)?;
        std::fs::create_dir_all(&self.mirrors).map_err(|e| e.to_string())?;
        let size = self.rows.lock().unwrap().get(&name.to_lowercase()).and_then(|r| r["size_mb"].as_f64()).unwrap_or(0.0) * 1024.0 * 1024.0;
        if crate::readiness::disk_usage(&self.mirrors).is_some_and(|(_, free)| (free as f64) < 5.0 * GB + 2.0 * size) {
            return Err("Not enough disk to refresh the mirror; free space on the computer state volume".into());
        }
        let clean = crate::safegit::clean_environment(&crate::env::process_environment());
        let mut env = clean.clone();
        env.extend(token_env(token));
        let mut prefix: Vec<String> = crate::safegit::args(&mirror)[..5].to_vec();
        prefix.extend(["-c", "safe.directory", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "-c", "http.followRedirects=false", "-c", "http.sslVerify=true", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "protocol.file.allow=never"].iter().map(|s| s.to_string()));
        prefix[6] = format!("safe.directory={}", mirror.display());
        let address = address(name)?;
        let r = |f: Fail| fail(f);
        let mirror_s = mirror.display().to_string();
        let head = run(&prefix, &["-C", &mirror_s, "symbolic-ref", "HEAD"], &clean, 10).await.map_err(r)?;
        let mut branch = head.stdout.trim().strip_prefix("refs/heads/").unwrap_or_default().to_string();
        if !head.ok() || branch.is_empty() {
            let result = run(&prefix, &["ls-remote", "--symref", "--", &address, "HEAD"], &env, 15).await.map_err(r)?;
            let found = Regex::new(r"(?m)^ref: refs/heads/(.+)\tHEAD$").unwrap().captures(&result.stdout).map(|c| c[1].to_string());
            branch = found.filter(|_| result.ok()).ok_or("Could not find the default branch; check GitHub access")?;
        }
        if let Err(problem) = self.fill_mirror(name, &mirror, &prefix, &branch, &address, name, &clean, &env, 15, epoch_s(), true).await {
            // A network failure may use the last good mirror; malformed config must never be trusted with a token.
            if mirror_config_overrides(&prefix, &mirror, &clean).await.is_ok()
                && run(&prefix, &["-C", &mirror_s, "show-ref", "--verify", &format!("refs/heads/{branch}")], &clean, 10).await.is_ok_and(|o| o.ok()) {
                return Ok(json!({"cached": true, "mirror": file_uri(&mirror), "default_branch": branch, "warning": mirror_warning(&mirror)}));
            }
            return Err(fail(problem));
        }
        Ok(json!({"refreshed": true, "mirror": file_uri(&mirror), "default_branch": branch}))
    }
}

/// Rebuild only a broken base's config from a fresh clone. Refs, index, stash, files and worktree registrations
/// stay in place, even when corrupt config prevents proving whether the base has unpublished work.
async fn configure_base(
    path: &Path, source: &str, branch: &str, settings: &[(String, String)], local: &[String], env: &Env, full: &str,
) -> Result<(), Fail> {
    async fn configure(path: &Path, settings: &[(String, String)], local: &[String], env: &Env) -> Result<Out, Fail> {
        for (key, value) in settings {
            let done = run_as_bot(local, &["-C", &path.display().to_string(), "config", "--local", "--replace-all", key, value], env, 10).await?;
            if !done.ok() { return Ok(done); }
        }
        Ok(Out::default())
    }
    let first = configure(path, settings, local, env).await?;
    if first.ok() { return Ok(()); }
    let reason = git_error("Configuring base clone mirror", full, &first, env);
    let git = path.join(".git");
    let config = git.join("config");
    if !real_dir(&git) || is_symlink(&config) || !crate::gitlocks::idle(&git).await {
        return Err(msg(format!("{reason}; config left intact because its Git metadata is linked or locked")));
    }
    let old = std::fs::read(&config).ok();
    struct Scratch(PathBuf);
    impl Drop for Scratch {
        fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); }
    }
    let scratch = Scratch(path.with_file_name(format!(".tico-base-repair-{}", uuid::Uuid::new_v4().simple())));
    crate::isolation::mkdir(&scratch.0, 0o755)?;
    let fresh = run_as_bot(local, &["clone", "--quiet", "--no-checkout", "--no-hardlinks", "--single-branch", "--branch", branch, "--", source, &scratch.0.display().to_string()], env, CLONE_TIMEOUT).await?;
    if !fresh.ok() {
        return Err(msg(format!("{reason}; {}", git_error("Rebuilding base config", full, &fresh, env))));
    }
    // Valid custom branch/upstream settings survive a permissions repair. Invalid config is retained as a backup.
    let readable = run_as_bot(local, &["-C", &path.display().to_string(), "config", "--local", "--no-includes", "--list"], env, 10).await?;
    if readable.ok() {
        if let Some(bytes) = &old { std::fs::write(scratch.0.join(".git/config"), bytes)?; }
    }
    let done = configure(&scratch.0, settings, local, env).await?;
    if !done.ok() { return Err(msg(git_error("Configuring rebuilt base", full, &done, env))); }
    if std::fs::read(&config).ok() != old || !crate::gitlocks::idle(&git).await {
        return Err(msg(format!("{reason}; Git metadata changed during repair; left intact for the next retry")));
    }
    let backup = git.join(format!("tico-config-backup-{}", uuid::Uuid::new_v4().simple()));
    if config.exists() { std::fs::rename(&config, &backup)?; }
    if let Err(e) = std::fs::rename(scratch.0.join(".git/config"), &config) {
        if backup.exists() { let _ = std::fs::rename(&backup, &config); }
        return Err(e.into());
    }
    crate::isolation::chown(&config);
    Ok(())
}

/// `-c key=value` overrides that neutralize any http.* setting in the mirror's own config; include files refused.
async fn mirror_config_overrides(prefix: &[String], mirror: &Path, clean: &Env) -> Result<Vec<String>, Fail> {
    let mirror_s = mirror.display().to_string();
    let config = run(prefix, &["-C", &mirror_s, "config", "--no-includes", "--name-only", "--get-regexp", r"^(http|include|includeif)\."], clean, 10).await?;
    if config.code != 0 && config.code != 1 {
        return Err(msg("Could not inspect mirror Git config"));
    }
    let mut out = vec![];
    for key in config.stdout.lines() {
        let lower = key.to_lowercase();
        if lower.starts_with("include.") || lower.starts_with("includeif.") {
            return Err(msg("Mirror Git config includes another file; remove the include before fetching"));
        }
        let setting = lower.rsplit('.').next().unwrap_or_default().to_string();
        let value = if setting == "sslverify" {
            "true".to_string()
        } else if setting == "sslcainfo" {
            clean.get("GIT_SSL_CAINFO").cloned().unwrap_or_default()
        } else {
            String::new()
        };
        out.extend(["-c".to_string(), format!("{key}={value}")]);
    }
    Ok(out)
}

fn set_mode(path: &Path, mode: u32) {
    use std::os::unix::fs::PermissionsExt;
    let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode));
}

/// Folders 0755 and files 0644: the bot user reads the supervisor's mirror (Repositories.mirror_permissions).
fn mirror_permissions(path: &Path) {
    let mut stack = vec![path.to_path_buf()];
    while let Some(dir) = stack.pop() {
        set_mode(&dir, 0o755);
        for entry in std::fs::read_dir(&dir).into_iter().flatten().flatten() {
            let p = entry.path();
            if is_symlink(&p) {
                continue;
            }
            if real_dir(&p) {
                stack.push(p);
            } else {
                set_mode(&p, 0o644);
            }
        }
    }
}

/// Python's `str(float)` for a positive epoch time (`1760000000.123456`).
fn py_float(v: f64) -> String {
    let s = format!("{v}");
    if s.contains('.') || s.contains('e') { s } else { format!("{s}.0") }
}

/// Python's `{:g}` for the small GB figures a disk message names.
fn py_g(v: f64) -> String {
    let s = format!("{:.6}", v);
    let digits = format!("{}", v.round() as i64).len();
    let precise = format!("{:.*}", 6usize.saturating_sub(digits), v);
    let out = if precise.contains('.') { precise.trim_end_matches('0').trim_end_matches('.').to_string() } else { precise };
    if out.is_empty() { s } else { out }
}

/// Fetch (or make) the managed base clone a task worktree comes from, with this bot's own credentials
/// (repositories.worktree_base). `repo` carries `full_name`, `default_branch`, `machine_git` and gets
/// `fetch_warning`; `_mirror_refresh` is a refresh the caller already asked for.
pub async fn worktree_base(workspace: &Path, repo: &mut Map<String, Value>, env: &Env) -> Result<(PathBuf, String), String> {
    let machine_git = crate::util::truthy(repo.get("machine_git"));
    let source_env = env.clone();
    let env = if machine_git { crate::safegit::machine_environment(env) } else { crate::safegit::environment(env) };
    let name = repo.get("full_name").map(text_of).unwrap_or_default();
    if !valid_repository(&name) || (!machine_git && !valid_name(&name)) {
        return Err("Invalid repository name".into());
    }
    let root = workspace.join("repos");
    let path = root.join(base_folder(&name));
    if is_symlink(&root) {
        return Err("Base clone points outside repos".into());
    }
    crate::isolation::mkdir(&root, 0o755).map_err(|e| e.to_string())?;
    crate::isolation::chown(&root);
    let parent_ok = path.parent().and_then(|p| p.canonicalize().ok()) == root.canonicalize().ok();
    if is_symlink(&root) || is_symlink(&path) || !parent_ok {
        return Err("Base clone points outside repos".into());
    }
    let g = |args: Vec<String>, env: Env, timeout: u64| {
        let path = path.clone();
        async move {
            let refs: Vec<&str> = args.iter().map(String::as_str).collect();
            gitrun::git_as_bot(&path, &env, &refs, timeout).await
        }
    };
    let s = |v: &[&str]| v.iter().map(|a| a.to_string()).collect::<Vec<String>>();
    let mut refreshed = repo.remove("_mirror_refresh").filter(|v| v.is_object());
    if path.exists() {
        if !real_dir(&path.join(".git")) {
            return Err("Base clone folder is not a Git repository".into());
        }
        let current = g(s(&["config", "--get", "remote.origin.url"]), env.clone(), 15).await.map_err(|e| e.to_string())?;
        let origin = current.stdout.trim();
        let ok = if machine_git { matches(origin, &name) } else { same_repository(origin, &name) };
        if !ok {
            return Err("Base clone remote does not match this repository".into());
        }
        crate::isolation::adopt(&[path.clone()]);
    } else {
        let mut direct: Vec<String> = crate::safegit::prefix();
        direct.extend(
            ["-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "protocol.ssh.allow=always", "-c", "protocol.file.allow=always", "-c", "http.followRedirects=false", "-c", "http.sslVerify=true", "clone", "--quiet", "--single-branch"]
                .iter()
                .map(|a| a.to_string()),
        );
        let mut command = direct.clone();
        let mut clone_env = env.clone();
        let mut url = address(&name)?;
        if let Some(r) = &refreshed {
            url = text_of(&r["mirror"]);
            let mirror_path = uri_path(&url);
            let at = command.iter().position(|a| a == "clone").unwrap();
            command.splice(at..at, ["-c".to_string(), format!("safe.directory={}", mirror_path.display())]);
            command.extend(["--origin".into(), "tico-mirror".into()]);
            clone_env = trusting(&crate::safegit::clean_environment(&env), &[&mirror_path]);
            repo.insert("default_branch".into(), r["default_branch"].clone());
            if !text_of(&r["warning"]).is_empty() {
                repo.insert("fetch_warning".into(), r["warning"].clone());
            }
        }
        let branch_args: Vec<String> = match repo.get("default_branch").map(text_of).unwrap_or_default() {
            b if !b.is_empty() => vec!["--branch".into(), b],
            _ => vec![],
        };
        let problem = clone_into(&name, &path, &[command, branch_args.clone()].concat(), &url, &clone_env, machine_git).await;
        let mut problem = problem;
        if let (Some(p), true) = (&problem, refreshed.is_some()) {
            if !p.ends_with(ELSEWHERE) {
                // The computer's mirror could not be copied while GitHub may well be fine: clone from GitHub instead.
                if let Some(fallback) = clone_into(&name, &path, &[direct.clone(), branch_args].concat(), &address(&name)?, &env, false).await {
                    let prefix = format!("Git clone of {name} failed: ");
                    return Err(format!("{p}; cloning from GitHub instead also failed: {}", fallback.strip_prefix(&prefix).unwrap_or(&fallback)));
                }
                problem = None;
                refreshed = None;
                clone_env = env.clone();
                repo.insert("fetch_warning".into(), json!(format!("Could not copy this computer's mirror of {name}; cloned from GitHub instead")));
            }
        }
        if let Some(p) = problem {
            return Err(p);
        }
        if refreshed.is_some() {
            for (key, value) in [("remote.origin.url", address(&name)?), ("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*".into())] {
                if !g(s(&["config", key, &value]), clone_env.clone(), 15).await.is_ok_and(|o| o.ok()) {
                    return Err("Could not configure base clone origin".into());
                }
            }
        }
        mark_managed(&path, Some(&name), false)?;
    }
    let mut branch = repo.get("default_branch").map(text_of).unwrap_or_default();
    if branch.is_empty() {
        let out = g(s(&["symbolic-ref", "--short", "refs/remotes/origin/HEAD"]), env.clone(), 15).await.unwrap_or_default();
        branch = out.stdout.trim().trim_start_matches("origin/").to_string();
    }
    if branch.is_empty() || branch.starts_with('-') || !gitrun::bare(&env, &["check-ref-format", "--branch", &branch], 15).await.is_ok_and(|o| o.ok()) {
        return Err("Repository default branch is missing; refresh Repositories".into());
    }
    let mut mirror_url = g(s(&["config", "--get", "remote.tico-mirror.url"]), env.clone(), 15).await.unwrap_or_default().stdout.trim().to_string();
    if machine_git {
        mirror_url.clear();
    }
    if !mirror_url.is_empty() {
        let mirror = uri_path(&mirror_url);
        if refreshed.is_none() {
            repo.remove("fetch_warning");
        }
        // The runner itself has no turn socket to ask for a refresh: the mirror is used as the last fetch left it.
        if source_env.get(crate::gitcred::SOCKET_ENV).is_none_or(|s| s.is_empty()) {
            repo.insert("fetch_warning".into(), json!(mirror_warning(&mirror)));
        }
    }
    let source = if mirror_url.is_empty() { "origin".to_string() } else { mirror_url.clone() };
    let mut local_env = if mirror_url.is_empty() { env.clone() } else { crate::safegit::clean_environment(&env) };
    let mut extra: Vec<String> = vec![];
    if !mirror_url.is_empty() {
        let mirror = uri_path(&mirror_url);
        local_env = trusting(&local_env, &[&mirror]);
        extra = s(&["-c", "protocol.allow=never", "-c", "protocol.file.allow=always", "-c"]);
        extra.push(format!("safe.directory={}", mirror.display()));
    } else {
        extra = s(&["-c", "http.followRedirects=false"]);
    }
    let spec = format!("+refs/heads/{branch}:refs/remotes/origin/{branch}");
    let mut args = extra.clone();
    args.extend(s(&["fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--", &source, &spec]));
    let fetched = g(args, local_env.clone(), if mirror_url.is_empty() { 15 } else { 30 }).await;
    let (ok, fetch_error) = match fetched {
        Ok(o) if o.ok() => (true, String::new()),
        Ok(o) => (false, if o.stderr.is_empty() { format!("exit {}", o.code) } else { o.stderr }),
        Err(Fault::Timeout(_)) => (false, "timed out".into()),
        Err(e) => (false, e.to_string()),
    };
    if !ok {
        let available = g(s(&["show-ref", "--verify", &format!("refs/remotes/origin/{branch}")]), local_env.clone(), 15).await;
        if !available.is_ok_and(|o| o.ok()) {
            if machine_git {
                return Err(machine_error(&name, &fetch_error, &local_env));
            }
            let reason = redact::git_failure(&fetch_error, &redact::env_secrets(&local_env), 240);
            return Err(format!("Git fetch of {name} failed: {}", if reason.is_empty() { "no reason given".into() } else { reason }));
        }
        repo.entry("fetch_warning").or_insert(json!("Could not refresh origin; using the base clone (age unknown)"));
    }
    mark_managed(&path, Some(&name), false)?;
    // A normal git push then updates the task branch's tracking ref, rather than comparing it to main.
    if !g(s(&["config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"]), env.clone(), 15).await.is_ok_and(|o| o.ok()) {
        return Err("Could not configure task branch tracking".into());
    }
    let head = format!("refs/remotes/origin/{branch}");
    if !g(s(&["symbolic-ref", "refs/remotes/origin/HEAD", &head]), local_env, 15).await.is_ok_and(|o| o.ok()) {
        return Err("Could not record base default branch".into());
    }
    Ok((path, branch))
}

/// Clone into a new `path`: None, or why not (git's own reason). What a failed clone left is removed.
async fn clone_into(name: &str, path: &Path, command: &[String], url: &str, env: &Env, machine_git: bool) -> Option<String> {
    let mut argv = vec!["git".to_string()];
    argv.extend(command.iter().cloned());
    argv.extend(["--".into(), url.into(), path.display().to_string()]);
    match gitrun::run_as_bot(&argv, env, None, CLONE_TIMEOUT).await {
        Err(f) => {
            discard_failed_clone(path, true).await;
            Some(match f {
                Fault::Timeout(_) => {
                    format!("Git clone of {name} timed out after {CLONE_TIMEOUT} seconds; a large repository or a slow network needs longer")
                }
                other if machine_git => machine_error(name, &other.to_string(), &Env::new()),
                other => format!("Git clone of {name} failed: {other}"),
            })
        }
        Ok(done) if done.ok() => None,
        Ok(done) if done.stderr.contains("already exists") => {
            let leaf = path.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
            Some(format!("Git clone of {name} failed: {leaf} {ELSEWHERE}"))
        }
        Ok(done) => {
            discard_failed_clone(path, true).await;
            Some(if machine_git {
                machine_error(name, if done.stderr.is_empty() { "exit" } else { &done.stderr }, env)
            } else {
                git_error("Git clone", name, &done, env)
            })
        }
    }
}

/// Whether the folder is plainly what Tico's own clone left when stopped halfway: empty, or a Git folder
/// this runner or its bot user made whose only remote is `tico-mirror` (or none yet). Anything else is never deleted.
/// Base clones run as the bot user (`run_as_bot`), so where bots are another user the half clone is theirs.
pub async fn tico_half_clone(path: &Path) -> bool {
    // SAFETY: geteuid has no preconditions.
    let mut owners = vec![unsafe { libc::geteuid() }];
    owners.extend(crate::isolation::identity().map(|(uid, _)| uid));
    half_clone_of(path, &owners).await
}

async fn half_clone_of(path: &Path, owners: &[u32]) -> bool {
    use std::os::unix::fs::MetadataExt;
    let Ok(meta) = path.symlink_metadata() else { return false };
    if meta.file_type().is_symlink() || !owners.contains(&meta.uid()) {
        return false;
    }
    if std::fs::read_dir(path).map(|mut d| d.next().is_none()).unwrap_or(false) {
        return true;
    }
    let git_dir = path.join(".git");
    match git_dir.symlink_metadata() {
        Ok(m) if m.is_dir() && owners.contains(&m.uid()) => {}
        _ => return false,
    }
    let config = git_dir.join("config");
    if is_symlink(&config) {
        return false;
    }
    if !config.exists() {
        return true;
    }
    let env = crate::safegit::clean_environment(&crate::env::process_environment());
    let config_s = config.display().to_string();
    match gitrun::bare(&env, &["config", "--file", &config_s, "--name-only", "--get-regexp", r"^remote\."], 15).await {
        Ok(done) if done.code == 0 || done.code == 1 => done.stdout.split_whitespace().all(|k| k.starts_with("remote.tico-mirror.")),
        _ => false,
    }
}

/// Remove what a failed clone left behind (repositories.discard_failed_clone). `fresh`: the folder did not exist
/// before this clone, so nothing in it is anyone's work unless a task worktree was already registered on it.
pub async fn discard_failed_clone(path: &Path, fresh: bool) {
    if !path.exists() || is_symlink(path) {
        return;
    }
    if fresh && !is_symlink(&path.join(".git")) {
        let registered = path.join(".git").join("worktrees");
        let has = real_dir(&registered) && std::fs::read_dir(&registered).map(|mut d| d.next().is_some()).unwrap_or(false);
        if !is_symlink(&registered) && !has {
            let _ = std::fs::remove_dir_all(path);
            return;
        }
    }
    if path.join(".git").exists() {
        match base_kept(path).await {
            Ok(None) => {}
            _ => return,
        }
    }
    let _ = std::fs::remove_dir_all(path);
}

pub fn was_adopted(path: &Path) -> bool {
    let marker = path.join(".git").join("tico-managed");
    match marker.symlink_metadata() {
        Ok(m) if m.is_file() && m.len() <= 1024 => {}
        _ => return false,
    }
    std::fs::read_to_string(&marker)
        .ok()
        .map(|t| if t.is_empty() { json!({}) } else { serde_json::from_str::<Value>(&t).unwrap_or(Value::Null) })
        .is_some_and(|v| v["adopted"] == true)
}

/// A flattened instruction symlink contains no new work only if its index is unchanged and its bytes equal
/// the committed link or its unchanged target. A different edit (including a staged one) is never restored.
pub async fn repair_instruction_links(root: &Path, env: &Env) {
    let Ok(root) = root.canonicalize() else { return };
    let Ok(list) = gitrun::git_as_bot(&root, env, &["ls-files", "--stage", "-z", "--", "AGENTS.md", "CLAUDE.md", ":(glob)**/AGENTS.md", ":(glob)**/CLAUDE.md"], 15).await else { return };
    if !list.ok() { return; }
    for entry in list.raw.split(|b| *b == 0) {
        let Ok(entry) = std::str::from_utf8(entry) else { continue };
        let Some((info, name)) = entry.split_once('\t') else { continue };
        if !info.starts_with("120000 ") || !info.ends_with(" 0") { continue; }
        let path = root.join(name);
        let Ok(before) = path.symlink_metadata() else { continue };
        use std::os::unix::fs::MetadataExt;
        if !before.is_file() || before.nlink() != 1 || path.parent().and_then(|p| p.canonicalize().ok()).as_deref() != path.parent() { continue; }
        if !gitrun::git_as_bot(&root, env, &["diff", "--cached", "--quiet", "HEAD", "--", name], 15).await.is_ok_and(|o| o.ok()) { continue; }
        let oid = info.split_whitespace().nth(1).unwrap_or_default();
        let Ok(link) = gitrun::git_as_bot(&root, env, &["cat-file", "blob", oid], 15).await else { continue };
        if !link.ok() || link.stdout.is_empty() || link.stdout.contains('\n') { continue; }
        let Ok(target) = path.parent().unwrap().join(&link.stdout).canonicalize() else { continue };
        let Ok(relative) = target.strip_prefix(&root) else { continue };
        let Ok(content) = std::fs::read(&path) else { continue };
        if content != link.raw {
            let spec = format!("HEAD:{}", relative.display());
            let original = gitrun::git_as_bot(&root, env, &["cat-file", "blob", &spec], 15).await;
            if !original.is_ok_and(|o| o.ok() && o.raw == content)
                || std::fs::read(&target).ok().as_ref() != Some(&content)
                || !gitrun::git_as_bot(&root, env, &["diff", "--quiet", "HEAD", "--", &relative.to_string_lossy()], 15).await.is_ok_and(|o| o.ok()) { continue; }
        }
        let temp = path.with_file_name(format!(".tico-link-{}", uuid::Uuid::new_v4().simple()));
        if std::os::unix::fs::symlink(&link.stdout, &temp).is_err() { continue; }
        if path.symlink_metadata().is_ok_and(|m| m.ino() == before.ino() && m.mtime() == before.mtime() && m.mtime_nsec() == before.mtime_nsec())
            && std::fs::read(&path).ok().as_ref() == Some(&content) {
            let _ = std::fs::rename(&temp, &path);
            crate::isolation::chown(&path);
        }
        let _ = std::fs::remove_file(temp);
    }
}

pub fn changed_file_names(status: &str) -> String {
    let names: Vec<_> = status.lines().filter_map(|s| s.get(3..)).collect();
    let mut text = names.iter().take(3).copied().collect::<Vec<_>>().join(", ");
    if names.len() > 3 { text.push_str(&format!(", and {} more", names.len() - 3)); }
    redact::scrub_credentials(&text, &[]).chars().take(240).collect()
}

/// Write `.git/tico-managed` (`{"full_name": ...}` as Python writes it), never through a symlink.
pub fn mark_managed(path: &Path, name: Option<&str>, adopted: bool) -> Result<(), String> {
    let adopted = adopted || was_adopted(path);
    let marker_text = match name {
        Some(n) => {
            let mut m = Map::new();
            m.insert("full_name".into(), json!(n));
            if adopted {
                m.insert("adopted".into(), json!(true));
            }
            crate::util::py_dumps(&Value::Object(m))
        }
        None => String::new(),
    };
    let marker = path.join(".git").join("tico-managed");
    if is_symlink(&marker) {
        return Err("Could not mark managed base clone; check permissions".into());
    }
    std::fs::write(&marker, marker_text).map_err(|_| "Could not mark managed base clone; check permissions".to_string())?;
    crate::isolation::chown(&marker);
    Ok(())
}

/// Why a base clone must stay (`kept: ...`), or None when nothing in it would be lost (repositories.base_kept).
pub async fn base_kept(path: &Path) -> Result<Option<String>, String> {
    if is_symlink(&path.join(".git")) || is_symlink(&path.join(".git").join("worktrees")) {
        return Err("Base clone registrations point outside repos".into());
    }
    let env = crate::safegit::clean_environment(&crate::env::process_environment());
    let g = |args: &'static [&'static str], t: u64| {
        let env = env.clone();
        async move { gitrun::git(path, &env, args, t).await.ok().filter(|o| o.ok()) }
    };
    g(&["worktree", "prune", "--expire", "now"], 120).await.ok_or("Could not prune base clone; repair without deleting local work")?;
    let listing = g(&["worktree", "list", "--porcelain"], 15).await.ok_or("Could not inspect base clone worktrees")?;
    let count = listing.stdout.lines().filter(|l| l.starts_with("worktree ")).count().saturating_sub(1);
    if count > 0 {
        return Ok(Some(format!("kept: {count} task worktrees")));
    }
    let status = g(&["status", "--porcelain", "--untracked-files=all"], 30).await.ok_or("Could not inspect base clone changes; repair without deleting local work")?;
    if !status.stdout.trim().is_empty() {
        return Ok(Some("kept: uncommitted or untracked files".into()));
    }
    let branches = g(&["rev-list", "--count", "--ignore-missing", "--branches", "HEAD", "refs/stash", "--not", "--remotes"], 30)
        .await
        .ok_or("Could not inspect local branches; repair without deleting local work")?;
    if branches.stdout.trim().parse::<i64>().unwrap_or(0) > 0 {
        return Ok(Some("kept: local branches with unpublished commits (including HEAD or stash)".into()));
    }
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;

    struct RepoFixture { root: PathBuf, remote: PathBuf, seed: PathBuf, base: PathBuf, env: Env }

    fn sh(path: &Path, args: &[&str]) -> String {
        let out = std::process::Command::new("git").args(["-c", "user.name=Fixture", "-c", "user.email=fixture@acme.example", "-c", "commit.gpgsign=false"])
            .args(args).current_dir(path).env("GIT_CONFIG_GLOBAL", "/dev/null").env("GIT_CONFIG_NOSYSTEM", "1").output().unwrap();
        assert!(out.status.success(), "{args:?}: {}", String::from_utf8_lossy(&out.stderr));
        String::from_utf8(out.stdout).unwrap().trim().to_string()
    }

    impl RepoFixture {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!("tico-repair-{}", uuid::Uuid::new_v4().simple()));
            std::fs::create_dir(&root).unwrap();
            let (remote, seed, base) = (root.join("remote.git"), root.join("seed"), root.join("base"));
            sh(&root, &["init", "--bare", "-q", "-b", "main", remote.to_str().unwrap()]);
            sh(&root, &["clone", "-q", remote.to_str().unwrap(), seed.to_str().unwrap()]);
            std::fs::create_dir_all(seed.join("templates/employee-repo")).unwrap();
            std::fs::write(seed.join("CLAUDE.md"), "Repository rules\n").unwrap();
            std::fs::write(seed.join("templates/employee-repo/AGENT.md"), "Bot rules\n").unwrap();
            for (name, target) in [("AGENTS.md", "CLAUDE.md"), ("templates/employee-repo/AGENTS.md", "AGENT.md"), ("templates/employee-repo/CLAUDE.md", "AGENT.md")] {
                std::os::unix::fs::symlink(target, seed.join(name)).unwrap();
            }
            sh(&seed, &["add", "."]);
            sh(&seed, &["commit", "-qm", "initial"]);
            sh(&seed, &["push", "-q", "origin", "main"]);
            sh(&root, &["clone", "-q", remote.to_str().unwrap(), base.to_str().unwrap()]);
            let mut env = crate::safegit::clean_environment(&crate::env::process_environment());
            env.extend([("GIT_CONFIG_COUNT".into(), "1".into()), ("GIT_CONFIG_KEY_0".into(), "protocol.file.allow".into()), ("GIT_CONFIG_VALUE_0".into(), "always".into())]);
            Self { root, remote, seed, base, env }
        }
        fn manager(&self) -> Repositories {
            Repositories::new(&self.root.join("workspace"), &self.root.join("state"), Client::new("http://localhost", "unused", 1, 0).unwrap())
        }
    }
    impl Drop for RepoFixture {
        fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.root); }
    }

    #[tokio::test]
    async fn malformed_mirror_config_is_rebuilt_from_the_remote_but_locked_mirrors_stay() {
        let f = RepoFixture::new();
        let manager = f.manager();
        let mirror = manager.mirror_path("acme/web").ok().unwrap();
        std::fs::create_dir_all(mirror.parent().unwrap()).unwrap();
        sh(&f.root, &["init", "--bare", "-q", mirror.to_str().unwrap()]);
        std::fs::write(mirror.join("config"), "[broken\n").unwrap();
        let prefix = vec!["-c".into(), "protocol.file.allow=always".into()];
        let lock = mirror.join("config.lock");
        std::fs::write(&lock, "active").unwrap();
        let filled = manager.fill_mirror("acme/web", &mirror, &prefix, "main", f.remote.to_str().unwrap(), "acme/web", &f.env, &f.env, 10, epoch_s(), true).await;
        assert!(filled.is_err());
        assert_eq!(std::fs::read_to_string(mirror.join("config")).unwrap(), "[broken\n");
        assert!(lock.exists());
        std::fs::remove_file(lock).unwrap();
        assert!(manager.fill_mirror("acme/web", &mirror, &prefix, "main", f.remote.to_str().unwrap(), "acme/web", &f.env, &f.env, 10, epoch_s(), true).await.is_ok());
        assert_eq!(sh(&mirror, &["rev-parse", "main"]), sh(&f.seed, &["rev-parse", "HEAD"]));
        assert!(mirror.join("tico-fetched-at").exists());
        let ref_lock = mirror.join("refs/heads/main.lock");
        std::fs::write(&ref_lock, "abandoned").unwrap();
        std::fs::File::open(&ref_lock).unwrap().set_times(std::fs::FileTimes::new().set_modified(std::time::SystemTime::now() - std::time::Duration::from_secs(600))).unwrap();
        std::fs::write(mirror.join("not-rebuilt"), "keep").unwrap();
        sh(&f.seed, &["commit", "-q", "--allow-empty", "-m", "later"]);
        sh(&f.seed, &["push", "-q"]);
        assert!(manager.fill_mirror("acme/web", &mirror, &prefix, "main", f.remote.to_str().unwrap(), "acme/web", &f.env, &f.env, 10, epoch_s(), true).await.is_ok());
        assert!(!ref_lock.exists() && mirror.join("not-rebuilt").exists());
        assert_eq!(sh(&mirror, &["rev-parse", "main"]), sh(&f.seed, &["rev-parse", "HEAD"]));
    }

    #[tokio::test]
    async fn rebuilding_base_config_preserves_unpushed_branches_stash_index_files_and_worktrees() {
        let f = RepoFixture::new();
        sh(&f.base, &["checkout", "-qb", "bot-work"]);
        std::fs::write(f.base.join("feature"), "unpublished\n").unwrap();
        sh(&f.base, &["add", "feature"]);
        sh(&f.base, &["commit", "-qm", "bot work"]);
        std::fs::write(f.base.join("feature"), "stash\n").unwrap();
        sh(&f.base, &["stash", "-q"]);
        std::fs::write(f.base.join("feature"), "staged\n").unwrap();
        sh(&f.base, &["add", "feature"]);
        std::fs::write(f.base.join("feature"), "unstaged\n").unwrap();
        std::fs::write(f.base.join("untracked"), "not committed\n").unwrap();
        let tree = f.root.join("task");
        sh(&f.base, &["worktree", "add", "-q", "-b", "task-work", tree.to_str().unwrap(), "origin/main"]);
        std::fs::write(tree.join("draft"), "task work\n").unwrap();
        let refs = sh(&f.base, &["show-ref"]);
        let index = std::fs::read(f.base.join(".git/index")).unwrap();
        let settings = vec![("remote.origin.url".into(), f.remote.display().to_string()), ("remote.tico-mirror.url".into(), file_uri(&f.remote))];
        let prefix = vec!["-c".into(), "protocol.file.allow=always".into()];
        std::fs::write(f.base.join(".git/config.lock"), "recent").unwrap();
        let original = std::fs::read(f.base.join(".git/config")).unwrap();
        let blocked = configure_base(&f.base, &file_uri(&f.remote), "main", &settings, &prefix, &f.env, "acme/web").await;
        assert!(matches!(blocked, Err(Fail::Msg(m)) if m.contains("config.lock") && m.contains("File exists")));
        assert_eq!(std::fs::read(f.base.join(".git/config")).unwrap(), original);
        std::fs::remove_file(f.base.join(".git/config.lock")).unwrap();
        std::fs::write(f.base.join(".git/config"), "[broken\n").unwrap();
        assert!(configure_base(&f.base, &file_uri(&f.remote), "main", &settings, &prefix, &f.env, "acme/web").await.is_ok());
        assert_eq!(sh(&f.base, &["show-ref"]), refs);
        assert_eq!(std::fs::read(f.base.join(".git/index")).unwrap(), index);
        assert_eq!(std::fs::read_to_string(f.base.join("feature")).unwrap(), "unstaged\n");
        assert_eq!(std::fs::read_to_string(f.base.join("untracked")).unwrap(), "not committed\n");
        assert_eq!(std::fs::read_to_string(tree.join("draft")).unwrap(), "task work\n");
        assert_eq!(sh(&tree, &["branch", "--show-current"]), "task-work");
        let backups: Vec<_> = std::fs::read_dir(f.base.join(".git")).unwrap().flatten().filter(|e| e.file_name().to_string_lossy().starts_with("tico-config-backup-")).collect();
        assert_eq!(backups.len(), 1);
        assert_eq!(std::fs::read_to_string(backups[0].path()).unwrap(), "[broken\n");
    }

    #[tokio::test]
    async fn flattened_instruction_links_are_repaired_but_bot_edits_stay_and_are_named() {
        let f = RepoFixture::new();
        for (name, text) in [("AGENTS.md", "Repository rules\n"), ("templates/employee-repo/AGENTS.md", "AGENT.md"), ("templates/employee-repo/CLAUDE.md", "Bot rules\n")] {
            let path = f.base.join(name);
            std::fs::remove_file(&path).unwrap();
            std::fs::write(path, text).unwrap();
        }
        repair_instruction_links(&f.base, &f.env).await;
        assert_eq!(sh(&f.base, &["status", "--porcelain"]), "");
        std::fs::remove_file(f.base.join("AGENTS.md")).unwrap();
        std::fs::write(f.base.join("AGENTS.md"), "A bot's real instruction edit\n").unwrap();
        // Even an exact copy is work when staged deliberately as a new regular file.
        std::fs::remove_file(f.base.join("templates/employee-repo/CLAUDE.md")).unwrap();
        std::fs::write(f.base.join("templates/employee-repo/CLAUDE.md"), "Bot rules\n").unwrap();
        sh(&f.base, &["add", "templates/employee-repo/CLAUDE.md"]);
        sh(&f.seed, &["commit", "-q", "--allow-empty", "-m", "upstream"]);
        sh(&f.seed, &["push", "-q"]);
        let before = sh(&f.base, &["rev-parse", "HEAD"]);
        let (state, why) = crate::placement::fast_forward(&f.base, &f.env).await;
        assert_eq!(state, "kept", "{why}");
        assert!(why.contains("AGENTS.md") && why.contains("templates/employee-repo/CLAUDE.md"), "{why}");
        assert_eq!(sh(&f.base, &["rev-parse", "HEAD"]), before);
        assert_eq!(std::fs::read_to_string(f.base.join("AGENTS.md")).unwrap(), "A bot's real instruction edit\n");
        assert!(!f.base.join("templates/employee-repo/CLAUDE.md").is_symlink());
    }

    #[test]
    fn folders_and_addresses_follow_the_shared_naming() {
        assert_eq!(base_folder("Acme/Product"), "acme__product");
        assert_eq!(address("Acme/product").unwrap(), "https://github.com/Acme/product.git");
        assert!(address("https://user@github.com/a/b.git").is_err());
        assert!(address("ssh://git@github.com/a/b.git").is_ok() && address("git@host.example:a/b.git").is_ok());
        let f = folder("https://gitlab.example/team/app.git").unwrap();
        assert!(f.starts_with("team__app-") && f.len() == "team__app-".len() + 12);
        assert!(github_origin("git@github.com:Acme/product.git", "acme/product") && !github_origin("https://x.example/acme/product", "acme/product"));
        assert_eq!(wait_words(300.0), "5 minutes");
        assert_eq!(wait_words(6.0 * 3600.0), "6 hours");
        assert_eq!(py_g(5.0), "5");
        assert_eq!(py_g(5.5), "5.5");
    }

    #[tokio::test]
    async fn a_half_clone_the_bot_user_made_is_still_tico_s_and_one_by_anyone_else_is_not() {
        use std::os::unix::fs::MetadataExt;
        let tmp = std::env::temp_dir().join(format!("tico-half-clone-{}", std::process::id()));
        let path = tmp.join("half");
        std::fs::create_dir_all(&path).unwrap();
        let git = |args: &[&str]| assert!(std::process::Command::new("git").args(args).status().unwrap().success());
        let p = path.to_str().unwrap();
        git(&["init", "-q", p]);
        git(&["-C", p, "remote", "add", "tico-mirror", "file:///mirror"]);
        let bot = path.metadata().unwrap().uid();
        // The runner is another user; the folder was made by the bot user the clone ran as.
        assert!(half_clone_of(&path, &[bot + 1, bot]).await);
        // Made by neither the runner nor its bot user: someone else's, never Tico's.
        assert!(!half_clone_of(&path, &[bot + 1, bot + 2]).await);
        // A person's clone with its own origin is never a half clone, whoever made it.
        git(&["-C", p, "remote", "add", "origin", "https://github.com/acme/web.git"]);
        assert!(!half_clone_of(&path, &[bot]).await);
        std::fs::remove_dir_all(&tmp).unwrap();
    }
}
