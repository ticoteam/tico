//! Base clones for this computer: a supervisor-owned bare mirror of each repository
//! the server lists (`<state>/mirrors/<owner>__<repo>.git`), a base clone made from it in `<projects>/repos/`
//! marked `.git/tico-managed`, their state in `<state>/repositories.json` (the file the Python runner kept,
//! read and written the same way), and the heartbeat's `repositories` rows. Task worktrees are made from the base clones.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use regex::Regex;
use serde_json::{json, Map, Value};

use crate::client::Client;
use crate::env::Env;
use crate::gitrun::{self, Fault, Out};
use crate::redact;
use crate::regular_file::{self, CONTENT, SMALL};
use crate::util::{epoch_s, iso, text_of};

pub const FETCH_INTERVAL: f64 = 15.0 * 60.0;
pub const RETRY_FIRST: f64 = 5.0 * 60.0;
pub const RETRY_MOST: f64 = 6.0 * 3600.0;
pub const CLONE_TIMEOUT: u64 = 120;
pub const REMOVE_AFTER: f64 = 30.0 * 86400.0;
pub const GB: f64 = 1024.0 * 1024.0 * 1024.0;
const ELSEWHERE: &str = "already exists and is not an empty directory.";
const SYNC_LOCK_WAIT: std::time::Duration = std::time::Duration::from_secs(1);

pub fn valid_name(name: &str) -> bool {
    static NAME: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = NAME.get_or_init(|| Regex::new(r"^[A-Za-z0-9-]+/[A-Za-z0-9_.-]+$").unwrap());
    re.is_match(name) && !matches!(name.split('/').nth(1), Some(".") | Some(".."))
}

/// A repository's git address (clients/git_repository.py address): `owner/repo` on GitHub, or an HTTPS, SSH or
/// local file URL, or an scp-style `host:path`.
pub fn address(value: &str) -> Result<String, String> {
    const USE: &str = "Use owner/repo or a Git HTTPS or SSH URL";
    if value.is_empty()
        || value.len() > 200
        || value.chars().any(|c| c.is_whitespace() || (c as u32) < 32)
    {
        return Err(USE.into());
    }
    if valid_name(value) {
        return Ok(format!("https://github.com/{value}.git"));
    }
    if let Some((scheme, rest)) = value.split_once("://") {
        if ["https", "ssh", "file"].contains(&scheme) && !rest.contains('?') && !rest.contains('#')
        {
            let (netloc, path) = rest
                .split_once('/')
                .map(|(n, p)| (n, format!("/{p}")))
                .unwrap_or((rest, String::new()));
            let (userinfo, host) = netloc
                .rsplit_once('@')
                .map(|(u, h)| (Some(u), h))
                .unwrap_or((None, netloc));
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
    let scp = SCP.get_or_init(|| {
        Regex::new(r"^(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+$").unwrap()
    });
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
        Some((_, rest)) => rest
            .split_once('/')
            .map(|(_, p)| p.to_string())
            .unwrap_or_default(),
        None => url
            .split_once(':')
            .map(|(_, p)| p.to_string())
            .unwrap_or_default(),
    };
    let trimmed = path.trim_matches('/');
    let trimmed = trimmed.strip_suffix(".git").unwrap_or(trimmed);
    let parts: Vec<&str> = trimmed.split('/').collect();
    let tail = &parts[parts.len().saturating_sub(2)..];
    static BAD: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let bad = BAD.get_or_init(|| Regex::new(r"[^A-Za-z0-9_.-]").unwrap());
    let name: String = tail
        .iter()
        .map(|p| bad.replace_all(p, "-").into_owned())
        .collect::<Vec<_>>()
        .join("__");
    let name: String = name.chars().take(100).collect();
    let name = if name.is_empty() {
        "repo".to_string()
    } else {
        name
    };
    Ok(format!(
        "{name}-{}",
        &gitrun::sha256_hex(url.as_bytes())[..12]
    ))
}

/// Whether a remote URL is this repository's address (git_repository.matches).
pub fn matches(url: &str, value: &str) -> bool {
    address(value).is_ok_and(|a| url.trim_end_matches('/') == a.trim_end_matches('/'))
}

pub fn base_folder(name: &str) -> String {
    let key = if valid_name(name) {
        name.to_lowercase()
    } else {
        name.to_string()
    };
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
    let pattern = format!(
        r"(?i)^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/){}(?:\.git)?/?$",
        regex::escape(full_name)
    );
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
        (
            "GIT_CONFIG_KEY_2",
            "credential.https://github.com.useHttpPath",
        ),
        ("GIT_CONFIG_VALUE_2", "true"),
    ] {
        env.insert(k.into(), v.into());
    }
    env
}

pub fn machine_error(name: &str, detail: &str, env: &Env) -> String {
    let reason = redact::git_failure(detail, &redact::env_secrets(env), 240);
    let reason = if reason.is_empty() {
        "Git command failed".to_string()
    } else {
        reason
    };
    format!("git could not reach {name} with this computer's git login: {reason}")
}

fn git_error(what: &str, name: &str, done: &Out, env: &Env) -> String {
    if done.stderr.starts_with("repository copy needs attention:") {
        return redact::scrub_credentials(&done.stderr, &redact::env_secrets(env));
    }
    let reason = redact::git_failure(&done.stderr, &redact::env_secrets(env), 240);
    let reason = if reason.is_empty() {
        format!("exit {}", done.code)
    } else {
        reason
    };
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
    if minutes < 120 {
        format!("{minutes} minutes")
    } else {
        format!("{} hours", minutes / 60)
    }
}

pub fn mirror_warning(mirror: &Path) -> String {
    let age = regular_file::text(mirror.join("tico-fetched-at"), SMALL)
        .ok()
        .and_then(|t| t.trim().parse::<f64>().ok())
        .map(|at| format!("{} minutes old", ((epoch_s() - at) / 60.0).max(0.0) as i64))
        .unwrap_or_else(|| "age unknown".into());
    format!("Could not refresh origin; using the local mirror ({age})")
}

fn file_uri(path: &Path) -> String {
    url::Url::from_file_path(path)
        .map(|u| u.to_string())
        .unwrap_or_else(|_| format!("file://{}", path.display()))
}

fn uri_path(uri: &str) -> PathBuf {
    url::Url::parse(uri)
        .ok()
        .and_then(|u| u.to_file_path().ok())
        .unwrap_or_else(|| PathBuf::from(uri.trim_start_matches("file://")))
}

/// `env` with a global git config that trusts only `dirs` (safe_git.trusting): a local repository another user
/// owns, which `-c safe.directory` does not reach in the upload-pack git starts.
pub fn trusting(env: &Env, dirs: &[&Path]) -> Env {
    let mut seen = HashSet::new();
    let mut lines = String::new();
    for d in dirs {
        let d = d.display().to_string();
        if !d.is_empty() && !d.contains('\n') && seen.insert(d.clone()) {
            lines.push_str(&format!(
                "\tdirectory = \"{}\"\n",
                d.replace('\\', "\\\\").replace('"', "\\\"")
            ));
        }
    }
    let folder = std::env::temp_dir().join(format!("tico-trust-{}", std::process::id()));
    let _ = std::fs::create_dir_all(&folder);
    let path = folder.join(format!(
        "{}.gitconfig",
        &gitrun::sha256_hex(lines.as_bytes())[..20]
    ));
    if !path.exists() {
        let temp = folder.join(format!("{}.tmp", uuid::Uuid::new_v4().simple()));
        if regular_file::write(&temp, format!("[safe]\n{lines}").as_bytes(), CONTENT, true).is_ok()
        {
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
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let Ok(meta) = entry.path().symlink_metadata() else {
                continue;
            };
            if meta.file_type().is_symlink() {
                continue;
            }
            if meta.is_dir() {
                stack.push(entry.path());
            } else if meta.is_file() {
                size += meta.len();
                if let Ok(m) = meta.modified() {
                    let at = m
                        .duration_since(std::time::UNIX_EPOCH)
                        .map(|d| d.as_secs_f64())
                        .unwrap_or(0.0);
                    newest = newest.max(at);
                }
            }
        }
    }
    (size, newest)
}

fn is_symlink(p: &Path) -> bool {
    p.symlink_metadata()
        .is_ok_and(|m| m.file_type().is_symlink())
}

fn real_dir(p: &Path) -> bool {
    p.symlink_metadata().is_ok_and(|m| m.is_dir())
}

fn round1(v: f64) -> f64 {
    (v * 10.0).round() / 10.0
}

/// Why a step failed: a message for the row, a timeout, a full disk, or something else.
#[derive(Debug)]
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
        if e.raw_os_error() == Some(libc::ENOSPC) {
            Fail::Disk
        } else {
            Fail::Other
        }
    }
}

fn msg(text: impl Into<String>) -> Fail {
    Fail::Msg(text.into())
}

fn hooks_args() -> Vec<String> {
    vec![
        "git".into(),
        "-c".into(),
        format!("core.hooksPath={}", crate::safegit::hooks_dir().display()),
    ]
}

async fn run(prefix: &[String], args: &[&str], env: &Env, timeout: u64) -> Result<Out, Fail> {
    let mut argv = hooks_args();
    argv.extend(prefix.iter().cloned());
    argv.extend(args.iter().map(|a| a.to_string()));
    Ok(gitrun::run(&argv, env, None, timeout).await?)
}

async fn run_as_bot(
    prefix: &[String],
    args: &[&str],
    env: &Env,
    timeout: u64,
) -> Result<Out, Fail> {
    let mut argv = hooks_args();
    argv.extend(prefix.iter().cloned());
    argv.extend(args.iter().map(|a| a.to_string()));
    Ok(gitrun::run_as_bot(&argv, env, None, timeout).await?)
}

/// The runner's repositories, for the credential socket's mirror refresh.
fn attention(reason: impl AsRef<str>) -> Fail {
    msg(format!(
        "repository copy needs attention: {}",
        reason.as_ref()
    ))
}

/// Read refs without loading a possibly malformed config. Unknown ref backends/encodings stay intact.
fn mirror_refs(mirror: &Path) -> Result<std::collections::BTreeMap<String, String>, Fail> {
    let mut refs = std::collections::BTreeMap::new();
    let oid = |s: &str| [40, 64].contains(&s.len()) && s.bytes().all(|b| b.is_ascii_hexdigit());
    if mirror.join("reftable").exists() {
        return Err(attention("cannot inspect the mirror's reftable refs"));
    }
    match regular_file::text(mirror.join("packed-refs"), 8 * CONTENT) {
        Ok(text) => {
            for line in text
                .lines()
                .filter(|s| !s.starts_with(['#', '^']) && !s.is_empty())
            {
                let Some((sha, name)) = line.split_once(' ') else {
                    return Err(attention("cannot parse packed refs"));
                };
                if !oid(sha) || !name.starts_with("refs/") {
                    return Err(attention("cannot parse packed refs"));
                }
                refs.insert(name.into(), sha.into());
            }
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => return Err(attention("cannot read packed refs")),
    }
    let mut pending = vec![mirror.join("refs")];
    while let Some(dir) = pending.pop() {
        let entries = match std::fs::read_dir(&dir) {
            Ok(e) => e,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound && dir == mirror.join("refs") => {
                continue
            }
            Err(_) => return Err(attention("cannot inspect local refs")),
        };
        for entry in entries {
            let entry = entry.map_err(|_| attention("cannot inspect local refs"))?;
            let path = entry.path();
            let m = path
                .symlink_metadata()
                .map_err(|_| attention("cannot inspect local refs"))?;
            if m.is_dir() {
                pending.push(path);
                continue;
            }
            let name = path
                .strip_prefix(mirror)
                .unwrap()
                .to_str()
                .ok_or_else(|| attention("cannot decode local ref"))?
                .to_string();
            if name.ends_with(".lock") || name.contains(".lock.stale-") {
                continue;
            }
            if !m.is_file() {
                return Err(attention("local ref is linked or unreadable"));
            }
            let sha =
                regular_file::text(&path, SMALL).map_err(|_| attention("cannot read local ref"))?;
            if !oid(sha.trim()) {
                return Err(attention(format!("cannot resolve local ref {name}")));
            }
            refs.insert(name, sha.trim().into());
        }
    }
    let head = regular_file::text(mirror.join("HEAD"), SMALL)
        .map_err(|_| attention("cannot inspect mirror HEAD"))?;
    if !head.starts_with("ref: refs/") {
        if !oid(head.trim()) {
            return Err(attention("cannot resolve mirror HEAD"));
        }
        refs.insert("HEAD".into(), head.trim().into());
    }
    Ok(refs)
}

/// Alternates are relative to their objects directory, not to objects/info or the worktree.
fn no_alternate_users(roots: &[PathBuf], mirror: &Path) -> Result<(), Fail> {
    let objects = mirror
        .join("objects")
        .canonicalize()
        .map_err(|_| attention("cannot inspect mirror objects"))?;
    let mut pending = roots.to_vec();
    let mut seen = HashSet::new();
    while let Some(dir) = pending.pop() {
        if !dir
            .try_exists()
            .map_err(|_| attention("cannot inspect runner repositories for alternates"))?
        {
            continue;
        }
        let dir = dir
            .canonicalize()
            .map_err(|_| attention("cannot inspect runner repositories for alternates"))?;
        if !seen.insert(dir.clone()) {
            continue;
        }
        let alternate = dir.join("objects/info/alternates");
        let has_alternate = match alternate.symlink_metadata() {
            Ok(_) => true,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => false,
            Err(_) => return Err(attention("cannot inspect repository alternates")),
        };
        if dir != mirror && has_alternate {
            if is_symlink(&alternate) {
                return Err(attention(format!(
                    "cannot inspect alternates {}",
                    alternate.display()
                )));
            }
            let text = regular_file::text(&alternate, CONTENT)
                .map_err(|_| attention("cannot read repository alternates"))?;
            for line in text.lines().filter(|s| !s.is_empty()) {
                if line.contains(['"', '\\']) {
                    return Err(attention("cannot decode repository alternates"));
                }
                let target = dir
                    .join("objects")
                    .join(line)
                    .canonicalize()
                    .map_err(|_| attention("cannot resolve repository alternates"))?;
                if target == objects {
                    return Err(attention(format!(
                        "{} uses this mirror's objects",
                        alternate.display()
                    )));
                }
            }
        }
        let entries = std::fs::read_dir(&dir)
            .map_err(|_| attention("cannot scan runner repositories for alternates"))?;
        let git_metadata = dir.join("HEAD").is_file() && dir.join("objects").is_dir();
        for entry in entries {
            let entry =
                entry.map_err(|_| attention("cannot scan runner repositories for alternates"))?;
            let m = entry
                .file_type()
                .map_err(|_| attention("cannot scan runner repositories for alternates"))?;
            // Object databases hold no checkouts. Skipping them also avoids walking every loose object.
            if m.is_dir() && !(git_metadata && entry.file_name() == "objects") {
                pending.push(entry.path());
            }
        }
    }
    Ok(())
}

pub static MIRRORS: std::sync::OnceLock<Arc<Repositories>> = std::sync::OnceLock::new();

pub struct Repositories {
    pub root: PathBuf,
    state_file: PathBuf,
    pub mirrors: PathBuf,
    client: Client,
    rows: Mutex<Map<String, Value>>,
    visible: AtomicBool,
    pending: Mutex<Option<tokio::task::JoinHandle<()>>>,
    sync_locks: Mutex<HashMap<String, Arc<tokio::sync::Mutex<()>>>>,
}

impl Repositories {
    pub fn new(workspace: &Path, state_dir: &Path, client: Client) -> Repositories {
        let state_file = state_dir.join("repositories.json");
        let mut rows = Map::new();
        if let Some(Value::Object(saved)) = std::fs::read_to_string(&state_file)
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
        {
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
            sync_locks: Mutex::new(HashMap::new()),
        }
    }

    async fn sync_guard(&self, name: &str) -> Result<tokio::sync::OwnedMutexGuard<()>, String> {
        let lock = self
            .sync_locks
            .lock()
            .unwrap()
            .entry(base_folder(name))
            .or_default()
            .clone();
        tokio::time::timeout(SYNC_LOCK_WAIT, lock.lock_owned())
            .await
            .map_err(|_| "Repository sync is busy; retry shortly or use the cached copy".into())
    }

    /// The sync lock of the base clone in `repos/<folder>` if no fetch or sync holds it now (disk_clean.rs).
    pub fn try_sync_guard(&self, folder: &str) -> Option<tokio::sync::OwnedMutexGuard<()>> {
        self.sync_locks
            .lock()
            .unwrap()
            .entry(folder.to_string())
            .or_default()
            .clone()
            .try_lock_owned()
            .ok()
    }

    fn path(&self, name: &str) -> Result<PathBuf, Fail> {
        let full = self
            .rows
            .lock()
            .unwrap()
            .get(name)
            .map(|r| text_of(&r["full_name"]))
            .filter(|n| !n.is_empty())
            .unwrap_or_else(|| name.into());
        let path = self.root.join(base_folder(&full));
        crate::repo_safety::outside_installation(&path).map_err(msg)?;
        let parent_ok =
            path.parent().and_then(|p| p.canonicalize().ok()) == self.root.canonicalize().ok();
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
        // Different repositories can finish together; serialize snapshots and the shared temporary file.
        let rows = self.rows.lock().unwrap();
        let text = crate::util::py_dumps(&Value::Object(rows.clone()));
        std::fs::write(&temp, text)?;
        let result = std::fs::rename(&temp, &self.state_file);
        drop(rows);
        result
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
        self.rows
            .lock()
            .unwrap()
            .values()
            .map(Self::report_row)
            .collect()
    }

    /// What `doctor` prints: the server's current list with each one's saved status,
    /// without cloning or cleanup; empty when the server cannot say.
    pub async fn inspect(&self) -> Vec<Value> {
        let Ok(reply) = self.client.get("runners/me/repositories").await else {
            return vec![];
        };
        let Some(repos) = reply["repositories"].as_array() else {
            return vec![];
        };
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
        self.rows
            .lock()
            .unwrap()
            .get(name)
            .cloned()
            .unwrap_or(Value::Null)
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
        if repos
            .iter()
            .any(|r| !r.is_object() || !valid_name(&text_of(&r["full_name"])))
        {
            return None;
        }
        self.visible.store(true, Ordering::SeqCst);
        if self
            .pending
            .lock()
            .unwrap()
            .as_ref()
            .is_some_and(|p| !p.is_finished())
        {
            return Some(self.report());
        }
        let now = epoch_s();
        let wanted: Map<String, Value> = repos
            .iter()
            .map(|r| (text_of(&r["full_name"]).to_lowercase(), r.clone()))
            .collect();
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
                    now - r["attempted_at"].as_f64().unwrap_or(0.0)
                        >= r["retry_delay"].as_f64().unwrap_or(FETCH_INTERVAL)
                        && now - r["fetched_at"].as_f64().unwrap_or(0.0) >= FETCH_INTERVAL
                })
                .cloned()
                .collect();
            due.sort_by(|a, b| {
                rows[a]["attempted_at"]
                    .as_f64()
                    .unwrap_or(0.0)
                    .total_cmp(&rows[b]["attempted_at"].as_f64().unwrap_or(0.0))
            });
            let selected = due.into_iter().next();
            if let Some(s) = &selected {
                rows[s]["attempted_at"] = json!(now);
            }
            selected
        };
        if self.save().is_err() {
            for row in self.rows.lock().unwrap().values_mut() {
                row["state"] = json!("failed");
                row["error"] =
                    json!("Could not save repository state; free disk space and check permissions");
            }
            return Some(self.report());
        }
        let me = self.clone();
        *self.pending.lock().unwrap() =
            Some(tokio::spawn(
                async move { me.sync(wanted, selected, now).await },
            ));
        Some(self.report())
    }

    /// `repos/*/.git/tico-managed` markers: (lowercased name, adopted) of each base clone Tico made or took over.
    fn markers(&self) -> Vec<(String, bool)> {
        let mut out = vec![];
        let Ok(entries) = std::fs::read_dir(&self.root) else {
            return out;
        };
        for entry in entries.flatten() {
            let folder = entry.file_name().to_string_lossy().to_string();
            let marker = entry.path().join(".git").join("tico-managed");
            let Ok(meta) = marker.symlink_metadata() else {
                continue;
            };
            if !meta.is_file() || meta.len() > 1024 {
                continue;
            }
            let Ok(data) = regular_file::read(&marker, 1024) else {
                continue;
            };
            let stored: Value = if data.is_empty() {
                json!({})
            } else {
                serde_json::from_slice(&data).unwrap_or(Value::Null)
            };
            if !stored.is_object() {
                continue;
            }
            let fallback = folder.replacen("__", "/", 1);
            let name = Some(text_of(&stored["full_name"]))
                .filter(|n| !n.is_empty())
                .unwrap_or(fallback);
            let name = if valid_name(&name) {
                name.to_lowercase()
            } else {
                name
            };
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
            .filter(|(n, r)| {
                !wanted.contains_key(*n)
                    && now - r["left_at"].as_f64().unwrap_or(now) >= REMOVE_AFTER
            })
            .map(|(n, _)| n.clone())
            .collect();
        for name in stale {
            let Ok(_sync) = self.sync_guard(&name).await else {
                continue;
            };
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
                // Mirrors may hold unpublished refs or objects used elsewhere; retirement keeps them.
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
                    r.insert(
                        "error".into(),
                        json!("Could not remove base clone safely; check repos folder permissions"),
                    );
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
        let path = self
            .mirrors
            .join(format!("{}.git", name.to_lowercase().replace('/', "__")));
        if is_symlink(&self.mirrors) || is_symlink(&path) {
            return Err(msg(
                "Mirror contains a symlink; repair the computer mirror folder",
            ));
        }
        if path.exists() {
            let mut stack = vec![path.clone()];
            while let Some(dir) = stack.pop() {
                for entry in std::fs::read_dir(&dir).into_iter().flatten().flatten() {
                    if is_symlink(&entry.path()) {
                        return Err(msg(
                            "Mirror contains a symlink; repair the computer mirror folder",
                        ));
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
        if [path, git_dir.as_path()]
            .iter()
            .any(|p| p.symlink_metadata().map(|m| m.uid()).unwrap_or(u32::MAX) != me)
        {
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
                gitrun::git(&path, &env, &args, 10)
                    .await
                    .unwrap_or_default()
            }
        };
        if get("core.worktree", false).await.ok() {
            return "its work tree is somewhere else".into();
        }
        if get("core.bare", true).await.stdout.trim() == "true" {
            return "it is a bare repository".into();
        }
        let origin = get("remote.origin.url", false)
            .await
            .stdout
            .trim()
            .to_string();
        if !github_origin(&origin, full_name) {
            let shown: String = redact::scrub_credentials(&origin, &[])
                .chars()
                .take(120)
                .collect();
            return format!(
                "its origin is {}, not {full_name}",
                if shown.is_empty() {
                    "not set".into()
                } else {
                    shown
                }
            );
        }
        String::new()
    }

    /// A broken mirror remains a complete repository until a person reviews it.
    async fn preserve_mirror(
        &self,
        name: &str,
        url: &str,
        prefix: &[String],
        env: &Env,
        timeout: u64,
    ) -> Result<(), Fail> {
        let mirror = self.mirror_path(name)?;
        if !mirror.exists() {
            return Ok(());
        }
        let _guard = crate::gitlocks::recovery_lock(&mirror).ok_or_else(|| {
            attention("mirror recovery is busy or outside the runner's private roots")
        })?;
        match std::fs::read_dir(mirror.join("worktrees")) {
            Ok(mut entries) => {
                if entries.next().is_some() {
                    return Err(attention("mirror has registered worktrees"));
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(_) => return Err(attention("cannot inspect registered worktrees")),
        }
        crate::gitlocks::check_locked(&mirror).map_err(msg)?;
        no_alternate_users(
            &[
                self.root.parent().unwrap().to_path_buf(),
                self.mirrors.parent().unwrap().to_path_buf(),
            ],
            &mirror,
        )?;
        let before = mirror_refs(&mirror)?;
        // Query outside the mirror: neither its malformed config nor its HTTP settings receive a token.
        let remote = run(prefix, &["ls-remote", "--refs", "--", url], env, timeout)
            .await
            .map_err(|_| {
                attention("could not read remote refs to prove the mirror is published")
            })?;
        if !remote.ok() {
            return Err(attention(
                "could not read remote refs to prove the mirror is published",
            ));
        }
        let mut published = std::collections::BTreeMap::new();
        for line in remote.stdout.lines() {
            let Some((sha, name)) = line.split_once('\t') else {
                return Err(attention("cannot parse remote refs"));
            };
            published.insert(name.to_string(), sha.to_string());
        }
        for (name, sha) in &before {
            if (name == "HEAD" && !published.values().any(|v| v == sha))
                || (name != "HEAD" && published.get(name) != Some(sha))
            {
                return Err(attention(format!(
                    "local ref {name} is unpublished or differs from the remote"
                )));
            }
        }
        let broken_prefix = format!("{}.broken-", mirror.file_name().unwrap().to_string_lossy());
        for entry in std::fs::read_dir(&self.mirrors)? {
            let entry = entry?;
            if entry
                .file_name()
                .to_string_lossy()
                .starts_with(&broken_prefix)
            {
                return Err(attention(format!(
                    "previous broken mirror {} still needs review",
                    entry.path().display()
                )));
            }
        }
        // A rebuild uses the clone's format. Keep any non-default format for manual repair.
        let config = mirror.join("config");
        let original = regular_file::read(&config, CONTENT)
            .map_err(|_| attention("mirror config cannot be read; repair it manually"))?;
        let format = repository_format(&config, env).await?;
        if format.len() != 1
            || format
                .get("core.repositoryformatversion")
                .map(Vec::as_slice)
                != Some(&["0".to_string()][..])
        {
            return Err(attention(format!(
                "{}: mirror format cannot be kept during rebuild; repair its config manually",
                mirror.display()
            )));
        }
        // Ordinary Git writers bypass our gate. A readable mirror can gain users during any await,
        // without changing its refs or config or leaving a lock, so it must stay at this path.
        let git_dir = format!("--git-dir={}", mirror.display());
        let mut probe_env = env.clone();
        probe_env.insert("LC_ALL".into(), "C".into());
        let probe = run(
            prefix,
            &[&git_dir, "rev-parse", "--is-bare-repository"],
            &probe_env,
            10,
        )
        .await;
        if !matches!(probe, Ok(ref out) if out.code == 128 && out.stderr.contains("not a git repository"))
        {
            return Err(attention(format!(
                "{}: mirror may still be in use; left in place. Check its users and repair it by hand. Inspect with: git {} fsck --full",
                mirror.display(), crate::gitcred::shell_quote(&git_dir)
            )));
        }
        if mirror_refs(&mirror)? != before || regular_file::read(&config, CONTENT)? != original {
            return Err(attention("mirror changed during inspection"));
        }
        crate::gitlocks::check_locked(&mirror).map_err(msg)?;
        let backup = mirror.with_file_name(format!(
            "{broken_prefix}{}-{}",
            epoch_s() as u64,
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::rename(&mirror, &backup)?;
        crate::util::log(&format!(
            "Preserved broken repository copy at {}",
            backup.display()
        ));
        Ok(())
    }

    async fn update(&self, name: &str, repo: &Value, now: f64) {
        let Ok(_sync) = self.sync_guard(name).await else {
            return;
        };
        let mut token = String::new();
        let mut created = false;
        let result = self
            .update_inner(name, repo, now, &mut token, &mut created)
            .await;
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
                json!(format!(
                    "{}. Retrying in {}, or restart the computer's runner to retry now",
                    error.trim_end_matches('.'),
                    wait_words(delay)
                )),
            );
        });
    }

    async fn update_inner(
        &self,
        name: &str,
        repo: &Value,
        now: f64,
        token_out: &mut String,
        created: &mut bool,
    ) -> Result<(), Fail> {
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
        let managed = self.row(name)["managed"] == true
            || marker.symlink_metadata().is_ok_and(|m| m.is_file());
        if exists && (!real_dir(&path.join(".git"))) {
            return Err(msg(
                "Repository folder already exists and is not a managed base clone; left as it is",
            ));
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
            if self.root.exists() {
                self.root.clone()
            } else {
                self.root.parent().unwrap_or(&self.root).to_path_buf()
            },
            if self.mirrors.exists() {
                self.mirrors.clone()
            } else {
                self.state_file
                    .parent()
                    .unwrap_or(&self.mirrors)
                    .to_path_buf()
            },
        ];
        let (volume, free) = volumes
            .iter()
            .map(|p| {
                (
                    p.clone(),
                    crate::readiness::disk_usage(p)
                        .map(|d| d.1)
                        .unwrap_or(u64::MAX),
                )
            })
            .min_by_key(|(_, f)| *f)
            .unwrap();
        let size_kb = repo["size_kb"]
            .as_f64()
            .unwrap_or_else(|| self.row(name)["size_mb"].as_f64().unwrap_or(0.0) * 1024.0)
            as u64;
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
        let covered = granted["repositories"]
            .as_array()
            .is_some_and(|a| a.iter().any(|n| text_of(n).to_lowercase() == name));
        if !covered {
            return Err(msg(format!(
                "Tico could not get a GitHub read token for {full}: the GitHub App is not installed on it or cannot reach it; add it to the App's repository access on GitHub"
            )));
        }
        let mut env = clean.clone();
        env.extend(token_env(&token));
        let url = format!("https://github.com/{full}.git");
        let prefix: Vec<String> = [
            "-c",
            "core.fsmonitor=false",
            "-c",
            "gc.auto=0",
            "-c",
            "maintenance.auto=false",
            "-c",
            "http.followRedirects=false",
            "-c",
            "http.sslVerify=true",
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.https.allow=always",
            "-c",
            "protocol.file.allow=never",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        let timeout = (size_kb / 1024).clamp(900, 7200);
        let mut branch = text_of(&repo["default_branch"]);
        if branch.is_empty() {
            let result = run(
                &prefix,
                &["ls-remote", "--symref", &url, "HEAD"],
                &env,
                timeout,
            )
            .await?;
            let found = Regex::new(r"(?m)^ref: refs/heads/(.+)\tHEAD$")
                .unwrap()
                .captures(&result.stdout)
                .map(|c| c[1].to_string());
            match found {
                Some(b) if result.ok() => branch = b,
                _ if !result.ok() => {
                    return Err(msg(git_error(
                        "Finding the default branch",
                        &full,
                        &result,
                        &env,
                    )))
                }
                _ => {
                    return Err(msg(
                        "Could not find the default branch; check GitHub access",
                    ))
                }
            }
        }
        self.fill_mirror(
            name, &mirror, &prefix, &branch, &url, &full, &clean, &env, timeout, now, true,
        )
        .await?;
        let trusted = trusting(
            &clean,
            &[&mirror.canonicalize().unwrap_or(mirror.clone()), &mirror],
        );
        let mut local: Vec<String> = crate::safegit::prefix();
        for d in [
            mirror.canonicalize().unwrap_or(mirror.clone()),
            mirror.clone(),
            path.canonicalize().unwrap_or(path.clone()),
        ] {
            local.extend(["-c".into(), format!("safe.directory={}", d.display())]);
        }
        local.extend(
            [
                "-c",
                "core.fsmonitor=false",
                "-c",
                "gc.auto=0",
                "-c",
                "maintenance.auto=false",
                "-c",
                "protocol.allow=never",
                "-c",
                "protocol.file.allow=always",
            ]
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
            let clone_args = [
                "clone",
                "--quiet",
                "--single-branch",
                "--no-hardlinks",
                "--origin",
                "tico-mirror",
                "--branch",
                &branch,
                "--",
                &mirror_uri,
                &path_s,
            ];
            let mut done = run_as_bot(&local, &clone_args, &trusted, timeout).await?;
            if !done.ok() && !done.stderr.contains("already exists") {
                // The mirror itself can be what is broken while GitHub is fine: rebuild it once and clone again.
                let first = git_error("Cloning the local mirror", &full, &done, &clean);
                discard_failed_clone(&path, true).await;
                self.preserve_mirror(name, &url, &prefix, &env, timeout)
                    .await?;
                self.fill_mirror(
                    name, &mirror, &prefix, &branch, &url, &full, &clean, &env, timeout, now, false,
                )
                .await?;
                done = run_as_bot(&local, &clone_args, &trusted, timeout).await?;
                if !done.ok() {
                    let again =
                        redact::git_failure(&done.stderr, &redact::env_secrets(&clean), 200);
                    let again = if again.is_empty() {
                        format!("exit {}", done.code)
                    } else {
                        again
                    };
                    *created = !done.stderr.contains("already exists");
                    return Err(msg(format!("{first}; rebuilt the mirror from GitHub and cloning it failed again: {again}")));
                }
            } else if !done.ok() {
                // A turn's own clone made the folder meanwhile: that one is not this clone's to remove.
                *created = false;
                return Err(msg(git_error(
                    "Cloning the local mirror",
                    &full,
                    &done,
                    &clean,
                )));
            }
        }
        let mut settings: Vec<(String, String)> = vec![
            ("remote.tico-mirror.url".into(), mirror_uri.clone()),
            (
                "remote.tico-mirror.fetch".into(),
                format!("+refs/heads/{branch}:refs/remotes/tico-mirror/{branch}"),
            ),
        ];
        let adopted = self.row(name)["adopted"] == true;
        if !adopted {
            // An adopted clone keeps the origin and branch settings its owner chose (an SSH remote, say).
            settings.extend([
                ("remote.origin.url".into(), url.clone()),
                ("remote.origin.pushurl".into(), url.clone()),
                (
                    "remote.origin.fetch".into(),
                    "+refs/heads/*:refs/remotes/origin/*".into(),
                ),
                (format!("branch.{branch}.remote"), "origin".into()),
                (
                    format!("branch.{branch}.merge"),
                    format!("refs/heads/{branch}"),
                ),
            ]);
        }
        configure_base(&path, &settings, &local, &trusted, &full).await?;
        let fetch_args_owned = [
            format!("+refs/heads/{branch}:refs/remotes/origin/{branch}"),
            format!("+refs/heads/{branch}:refs/remotes/tico-mirror/{branch}"),
        ];
        let fetch_args = [
            "-C",
            &path_s,
            "fetch",
            "--quiet",
            "--no-tags",
            "--no-write-fetch-head",
            "--",
            &mirror_uri,
            &fetch_args_owned[0],
            &fetch_args_owned[1],
        ];
        let mut done = run_as_bot(&local, &fetch_args, &trusted, timeout).await?;
        if !done.ok() {
            let first = git_error(
                &format!(
                    "Git {} from the local mirror",
                    if exists { "fetch" } else { "clone" }
                ),
                &full,
                &done,
                &clean,
            );
            self.preserve_mirror(name, &url, &prefix, &env, timeout)
                .await?;
            self.fill_mirror(
                name, &mirror, &prefix, &branch, &url, &full, &clean, &env, timeout, now, false,
            )
            .await?;
            done = run_as_bot(&local, &fetch_args, &trusted, timeout).await?;
            if !done.ok() {
                let again = redact::git_failure(&done.stderr, &redact::env_secrets(&clean), 200);
                let again = if again.is_empty() {
                    format!("exit {}", done.code)
                } else {
                    again
                };
                return Err(msg(format!(
                    "{first}; rebuilt the mirror from GitHub and fetching it failed again: {again}"
                )));
            }
        }
        let head = format!("refs/remotes/origin/{branch}");
        if !run_as_bot(
            &local,
            &[
                "-C",
                &path_s,
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
                &head,
            ],
            &clean,
            10,
        )
        .await?
        .ok()
        {
            return Err(msg(
                "Could not record base default branch; check permissions",
            ));
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
        let result = self
            .fill_mirror_once(
                mirror,
                base_prefix,
                branch,
                url,
                full,
                clean,
                env,
                timeout,
                now,
            )
            .await;
        let repair = matches!(&result, Err(Fail::Msg(m)) if m.contains("Git config") || m.contains("mirror default branch") || mirror_broken(m, mirror));
        if repair && rebuild {
            self.preserve_mirror(name, url, base_prefix, env, timeout)
                .await?;
            return self
                .fill_mirror_once(
                    mirror,
                    base_prefix,
                    branch,
                    url,
                    full,
                    clean,
                    env,
                    timeout,
                    now,
                )
                .await;
        }
        result
    }

    #[allow(clippy::too_many_arguments)]
    async fn fill_mirror_once(
        &self,
        mirror: &Path,
        base_prefix: &[String],
        branch: &str,
        url: &str,
        full: &str,
        clean: &Env,
        env: &Env,
        timeout: u64,
        now: f64,
    ) -> Result<(), Fail> {
        let mut prefix = base_prefix.to_vec();
        let mirror_s = mirror.display().to_string();
        if !mirror.exists()
            && !run(
                &prefix,
                &[
                    "init",
                    "--bare",
                    "--quiet",
                    &format!("--initial-branch={branch}"),
                    &mirror_s,
                ],
                clean,
                10,
            )
            .await?
            .ok()
        {
            return Err(msg(
                "Could not create mirror; check disk space and permissions",
            ));
        }
        prefix.extend(mirror_config_overrides(&prefix, mirror, clean).await?);
        let refspec = format!("+refs/heads/{branch}:refs/heads/{branch}");
        let done = run(
            &prefix,
            &[
                "-C",
                &mirror_s,
                "fetch",
                "--quiet",
                "--prune",
                "--no-tags",
                "--no-write-fetch-head",
                "--",
                url,
                &refspec,
            ],
            env,
            timeout,
        )
        .await?;
        if !done.ok() {
            return Err(msg(git_error("Git mirror fetch", full, &done, env)));
        }
        if !run(
            &prefix,
            &[
                "-C",
                &mirror_s,
                "symbolic-ref",
                "HEAD",
                &format!("refs/heads/{branch}"),
            ],
            clean,
            10,
        )
        .await?
        .ok()
        {
            return Err(msg("Could not set mirror default branch"));
        }
        mirror_permissions(mirror);
        regular_file::write(
            mirror.join("tico-fetched-at"),
            py_float(now).as_bytes(),
            SMALL,
            false,
        )?;
        Ok(())
    }

    /// A bounded refresh a turn asked for through the credential socket; the token never enters the base.
    pub async fn refresh_mirror(&self, name: &str, token: &str) -> Result<Value, String> {
        if !valid_name(name) {
            return Err("Invalid mirror repository".into());
        }
        let _sync = self.sync_guard(name).await?;
        let fail = |f: Fail| match f {
            Fail::Msg(m) => m,
            Fail::Timeout(_) => "Mirror fetch timed out".into(),
            _ => "Mirror refresh failed".into(),
        };
        let mirror = self.mirror_path(name).map_err(fail)?;
        std::fs::create_dir_all(&self.mirrors).map_err(|e| e.to_string())?;
        let size = self
            .rows
            .lock()
            .unwrap()
            .get(&name.to_lowercase())
            .and_then(|r| r["size_mb"].as_f64())
            .unwrap_or(0.0)
            * 1024.0
            * 1024.0;
        if crate::readiness::disk_usage(&self.mirrors)
            .is_some_and(|(_, free)| (free as f64) < 5.0 * GB + 2.0 * size)
        {
            return Err(
                "Not enough disk to refresh the mirror; free space on the computer state volume"
                    .into(),
            );
        }
        let clean = crate::safegit::clean_environment(&crate::env::process_environment());
        let mut env = clean.clone();
        env.extend(token_env(token));
        let mut prefix: Vec<String> = crate::safegit::args(&mirror)[..5].to_vec();
        prefix.extend(
            [
                "-c",
                "safe.directory",
                "-c",
                "gc.auto=0",
                "-c",
                "maintenance.auto=false",
                "-c",
                "http.followRedirects=false",
                "-c",
                "http.sslVerify=true",
                "-c",
                "protocol.allow=never",
                "-c",
                "protocol.https.allow=always",
                "-c",
                "protocol.file.allow=never",
            ]
            .iter()
            .map(|s| s.to_string()),
        );
        prefix[6] = format!("safe.directory={}", mirror.display());
        let address = address(name)?;
        let r = |f: Fail| fail(f);
        let mirror_s = mirror.display().to_string();
        let head = run(
            &prefix,
            &["-C", &mirror_s, "symbolic-ref", "HEAD"],
            &clean,
            10,
        )
        .await
        .map_err(r)?;
        let mut branch = head
            .stdout
            .trim()
            .strip_prefix("refs/heads/")
            .unwrap_or_default()
            .to_string();
        if !head.ok() || branch.is_empty() {
            let result = run(
                &prefix,
                &["ls-remote", "--symref", "--", &address, "HEAD"],
                &env,
                15,
            )
            .await
            .map_err(r)?;
            let found = Regex::new(r"(?m)^ref: refs/heads/(.+)\tHEAD$")
                .unwrap()
                .captures(&result.stdout)
                .map(|c| c[1].to_string());
            branch = found
                .filter(|_| result.ok())
                .ok_or("Could not find the default branch; check GitHub access")?;
        }
        if let Err(problem) = self
            .fill_mirror(
                name,
                &mirror,
                &prefix,
                &branch,
                &address,
                name,
                &clean,
                &env,
                15,
                epoch_s(),
                true,
            )
            .await
        {
            if let Fail::Msg(note) = &problem {
                if note.starts_with("repository copy needs attention:") {
                    self.update_row(name, |row| {
                        row.insert("state".into(), json!("failed"));
                        row.insert("error".into(), json!(note));
                    });
                    let _ = self.save();
                    return Err(note.clone());
                }
            }
            // A network failure may use the last good mirror; malformed config must never be trusted with a token.
            if mirror_config_overrides(&prefix, &mirror, &clean)
                .await
                .is_ok()
                && run(
                    &prefix,
                    &[
                        "-C",
                        &mirror_s,
                        "show-ref",
                        "--verify",
                        &format!("refs/heads/{branch}"),
                    ],
                    &clean,
                    10,
                )
                .await
                .is_ok_and(|o| o.ok())
            {
                return Ok(
                    json!({"cached": true, "mirror": file_uri(&mirror), "default_branch": branch, "warning": mirror_warning(&mirror)}),
                );
            }
            return Err(fail(problem));
        }
        Ok(json!({"refreshed": true, "mirror": file_uri(&mirror), "default_branch": branch}))
    }
}

/// Parse outside the repository so a broken format cannot silently select Git's defaults.
/// Includes are refused because another file could change the effective format during repair.
async fn repository_format(
    config: &Path,
    env: &Env,
) -> Result<std::collections::BTreeMap<String, Vec<String>>, Fail> {
    let refuse = || {
        attention(format!(
            "{}: repository format cannot be read safely; config left intact; repair it manually",
            config.display()
        ))
    };
    let bytes = regular_file::read(config, CONTENT).map_err(|_| refuse())?;
    struct Snapshot(PathBuf);
    impl Drop for Snapshot {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }
    let snapshot = Snapshot(std::env::temp_dir().join(format!(
        "tico-config-read-{}",
        uuid::Uuid::new_v4().simple()
    )));
    {
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        let mut file = regular_file::open(
            &snapshot.0,
            std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600),
            CONTENT,
        )?;
        file.write_all(&bytes)?;
    }
    let parsed = run(
        &[],
        &[
            "config",
            "--file",
            &snapshot.0.display().to_string(),
            "--no-includes",
            "--null",
            "--list",
        ],
        env,
        10,
    )
    .await?;
    if !parsed.ok() {
        return Err(refuse());
    }
    let text = String::from_utf8(parsed.raw).map_err(|_| refuse())?;
    let mut format = std::collections::BTreeMap::<String, Vec<String>>::new();
    for entry in text.split('\0').filter(|s| !s.is_empty()) {
        let (key, value) = entry.split_once('\n').unwrap_or((entry, ""));
        let key = key.to_ascii_lowercase();
        if key.starts_with("include.") || key.starts_with("includeif.") {
            return Err(refuse());
        }
        if key == "core.repositoryformatversion" || key.starts_with("extensions.") {
            format.entry(key).or_default().push(value.to_string());
        }
    }
    if !format
        .get("core.repositoryformatversion")
        .and_then(|v| v.last())
        .is_some_and(|v| v == "0" || v == "1")
    {
        return Err(refuse());
    }
    Ok(format)
}

/// Rebuild only a broken base's config on a same-user computer. Refs, index, stash, files and worktree
/// registrations stay in place; an adopted base also needs a verifiable origin in its preserved config.
async fn configure_base(
    path: &Path,
    settings: &[(String, String)],
    local: &[String],
    env: &Env,
    full: &str,
) -> Result<(), Fail> {
    async fn configure(
        path: &Path,
        settings: &[(String, String)],
        local: &[String],
        env: &Env,
    ) -> Result<Out, Fail> {
        for (key, value) in settings {
            let done = run_as_bot(
                local,
                &[
                    "-C",
                    &path.display().to_string(),
                    "config",
                    "--local",
                    "--replace-all",
                    key,
                    value,
                ],
                env,
                10,
            )
            .await?;
            if !done.ok() {
                return Ok(done);
            }
        }
        Ok(Out::default())
    }
    if path.join(".git/config").symlink_metadata().is_err() {
        return Err(attention(format!("{}: base config is missing or unreadable; repository format is unknown; repair it manually", path.display())));
    }
    let first = configure(path, settings, local, env).await?;
    if first.ok() {
        return Ok(());
    }
    let reason = git_error("Configuring base clone mirror", full, &first, env);
    // The scratch checkout and its parents are bot-controlled; supervisor filesystem operations cannot trust them.
    if crate::isolation::enabled() {
        return Err(attention(format!("{reason}; automatic base config replacement is disabled when bots run as a separate user; repair the base clone's config and permissions manually")));
    }
    let git = path.join(".git");
    let config = git.join("config");
    if !real_dir(&git) || is_symlink(&config) {
        return Err(msg(format!(
            "{reason}; config left intact because its Git metadata is linked"
        )));
    }
    crate::gitlocks::check(&git).map_err(msg)?;
    let old = match regular_file::read(&config, CONTENT) {
        Ok(bytes) => Some(bytes),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
        Err(_) => {
            return Err(attention(format!(
                "{reason}; config is not a readable bounded regular file; left intact"
            )))
        }
    };
    let readable = run_as_bot(
        local,
        &[
            "-C",
            &path.display().to_string(),
            "config",
            "--local",
            "--no-includes",
            "--list",
        ],
        env,
        10,
    )
    .await?;
    // Only runner-created bases receive an origin in settings. An adopted base must retain its owner's verified origin.
    if !settings.iter().any(|(key, _)| key == "remote.origin.url") {
        let origin = run_as_bot(
            local,
            &[
                "-C",
                &path.display().to_string(),
                "config",
                "--local",
                "--no-includes",
                "--get-all",
                "remote.origin.url",
            ],
            env,
            10,
        )
        .await?;
        if old.is_none()
            || !readable.ok()
            || !origin.ok()
            || !github_origin(origin.stdout.trim(), full)
        {
            return Err(attention(format!("{reason}; adopted base config left intact because its original GitHub origin cannot be verified and preserved; repair the config manually")));
        }
    }
    // An unparseable config cannot prove that extensions or format settings are absent.
    let format = repository_format(&config, env).await?;
    struct Scratch(PathBuf);
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    let scratch = Scratch(path.with_file_name(format!(
        ".tico-base-repair-{}",
        uuid::Uuid::new_v4().simple()
    )));
    crate::isolation::mkdir(&scratch.0, 0o700)?;
    let rebuilt = scratch.0.join("config");
    let bytes = old.as_deref().ok_or_else(|| {
        attention("base config is missing; repository format is unknown; repair it manually")
    })?;
    stage_base_config(&rebuilt, bytes, settings, local, env).await?;
    if repository_format(&rebuilt, env).await? != format {
        return Err(attention(format!(
            "{}: repository format changed during config repair; left intact",
            path.display()
        )));
    }
    crate::gitlocks::check(&git).map_err(msg)?;
    // The metadata check is only a snapshot: Git writers must share this lock through the atomic install.
    let lock = BaseConfigLock::acquire(&config).map_err(|e| {
        msg(format!(
            "{reason}; could not lock base config for repair: {e}"
        ))
    })?;
    lock.install(old.as_deref(), &rebuilt)
}

/// Stage the complete config without discovering a scratch repository with a different format.
async fn stage_base_config(
    rebuilt: &Path,
    bytes: &[u8],
    settings: &[(String, String)],
    local: &[String],
    env: &Env,
) -> Result<(), Fail> {
    regular_file::write(rebuilt, bytes, CONTENT, true)?;
    let before = repository_format(rebuilt, env).await?;
    for (key, value) in settings {
        let done = run(
            local,
            &[
                "config",
                "--file",
                &rebuilt.display().to_string(),
                "--replace-all",
                key,
                value,
            ],
            env,
            10,
        )
        .await?;
        if !done.ok() {
            return Err(msg(format!(
                "Could not stage base config: {}",
                done.stderr.trim()
            )));
        }
    }
    if repository_format(rebuilt, env).await? != before {
        return Err(attention(
            "repository format changed while staging config; original left intact",
        ));
    }
    Ok(())
}

struct BaseConfigLock {
    path: PathBuf,
    file: std::fs::File,
    committed: bool,
}

impl BaseConfigLock {
    fn acquire(config: &Path) -> std::io::Result<Self> {
        use std::os::unix::fs::OpenOptionsExt;
        let path = config.with_file_name("config.lock");
        let file = regular_file::open(
            &path,
            std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600),
            CONTENT,
        )?;
        Ok(Self {
            path,
            file,
            committed: false,
        })
    }

    fn install(mut self, old: Option<&[u8]>, rebuilt: &Path) -> Result<(), Fail> {
        use std::io::{Read, Write};
        use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
        let config = self.path.with_file_name("config");
        let mut current = Vec::new();
        let permissions =
            match regular_file::open(&config, std::fs::OpenOptions::new().read(true), CONTENT) {
                Ok(mut file) => {
                    let permissions = file.metadata()?.permissions();
                    (&mut file).take(CONTENT + 1).read_to_end(&mut current)?;
                    if old != Some(current.as_slice()) {
                        return Err(msg(
                            "Git config changed during repair; left intact for the next retry",
                        ));
                    }
                    permissions
                }
                Err(e) if e.kind() == std::io::ErrorKind::NotFound && old.is_none() => {
                    std::fs::Permissions::from_mode(0o644)
                }
                _ => {
                    return Err(msg(
                        "Git config changed during repair; left intact for the next retry",
                    ))
                }
            };
        let replacement = regular_file::read(rebuilt, CONTENT)?;
        self.file.set_permissions(permissions.clone())?;
        self.file.write_all(&replacement)?;
        self.file.sync_all()?;
        if old.is_some() {
            let backup = config.with_file_name(format!(
                "tico-config-backup-{}",
                uuid::Uuid::new_v4().simple()
            ));
            let mut saved = regular_file::open(
                &backup,
                std::fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .mode(0o600),
                CONTENT,
            )?;
            saved.set_permissions(permissions)?;
            saved.write_all(&current)?;
            saved.sync_all()?;
        }
        // The original remains readable until Git's lock file takes its place in one rename.
        std::fs::rename(&self.path, &config)?;
        self.committed = true;
        Ok(())
    }
}

impl Drop for BaseConfigLock {
    fn drop(&mut self) {
        use std::os::unix::fs::MetadataExt;
        if !self.committed
            && self
                .file
                .metadata()
                .ok()
                .zip(self.path.symlink_metadata().ok())
                .is_some_and(|(held, current)| {
                    held.dev() == current.dev() && held.ino() == current.ino()
                })
        {
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

/// `-c key=value` overrides that neutralize any http.* setting in the mirror's own config; include files refused.
async fn mirror_config_overrides(
    prefix: &[String],
    mirror: &Path,
    clean: &Env,
) -> Result<Vec<String>, Fail> {
    let mirror_s = mirror.display().to_string();
    let config = run(
        prefix,
        &[
            "-C",
            &mirror_s,
            "config",
            "--no-includes",
            "--name-only",
            "--get-regexp",
            r"^(http|include|includeif)\.",
        ],
        clean,
        10,
    )
    .await?;
    if config.code != 0 && config.code != 1 {
        return Err(msg("Could not inspect mirror Git config"));
    }
    let mut out = vec![];
    for key in config.stdout.lines() {
        let lower = key.to_lowercase();
        if lower.starts_with("include.") || lower.starts_with("includeif.") {
            return Err(msg(
                "Mirror Git config includes another file; remove the include before fetching",
            ));
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
    if s.contains('.') || s.contains('e') {
        s
    } else {
        format!("{s}.0")
    }
}

/// Python's `{:g}` for the small GB figures a disk message names.
fn py_g(v: f64) -> String {
    let s = format!("{:.6}", v);
    let digits = format!("{}", v.round() as i64).len();
    let precise = format!("{:.*}", 6usize.saturating_sub(digits), v);
    let out = if precise.contains('.') {
        precise
            .trim_end_matches('0')
            .trim_end_matches('.')
            .to_string()
    } else {
        precise
    };
    if out.is_empty() {
        s
    } else {
        out
    }
}

/// Fetch (or make) the managed base clone a task worktree comes from, with this bot's own credentials
/// (repositories.worktree_base). `repo` carries `full_name`, `default_branch`, `machine_git` and gets
/// `fetch_warning`; `_mirror_refresh` is a refresh the caller already asked for.
pub async fn worktree_base(
    workspace: &Path,
    repo: &mut Map<String, Value>,
    env: &Env,
) -> Result<(PathBuf, String), String> {
    let machine_git = crate::util::truthy(repo.get("machine_git"));
    let source_env = env.clone();
    let name = repo.get("full_name").map(text_of).unwrap_or_default();
    if !valid_repository(&name) || (!machine_git && !valid_name(&name)) {
        return Err("Invalid repository name".into());
    }
    let root = workspace.join("repos");
    let path = root.join(base_folder(&name));
    crate::repo_safety::outside_installation(&path)?;
    let env = if machine_git {
        crate::safegit::machine_environment(env)
    } else {
        crate::safegit::environment(env)
    };
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
        let current = g(
            s(&["config", "--get", "remote.origin.url"]),
            env.clone(),
            15,
        )
        .await
        .map_err(|e| e.to_string())?;
        let origin = current.stdout.trim();
        let ok = if machine_git {
            matches(origin, &name)
        } else {
            same_repository(origin, &name)
        };
        if !ok {
            return Err("Base clone remote does not match this repository".into());
        }
        crate::isolation::adopt(&[path.clone()]);
    } else {
        let mut direct: Vec<String> = crate::safegit::prefix();
        direct.extend(
            [
                "-c",
                "protocol.allow=never",
                "-c",
                "protocol.https.allow=always",
                "-c",
                "protocol.ssh.allow=always",
                "-c",
                "protocol.file.allow=always",
                "-c",
                "http.followRedirects=false",
                "-c",
                "http.sslVerify=true",
                "clone",
                "--quiet",
                "--single-branch",
            ]
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
            command.splice(
                at..at,
                [
                    "-c".to_string(),
                    format!("safe.directory={}", mirror_path.display()),
                ],
            );
            command.extend(["--origin".into(), "tico-mirror".into()]);
            clone_env = trusting(&crate::safegit::clean_environment(&env), &[&mirror_path]);
            repo.insert("default_branch".into(), r["default_branch"].clone());
            if !text_of(&r["warning"]).is_empty() {
                repo.insert("fetch_warning".into(), r["warning"].clone());
            }
        }
        let branch_args: Vec<String> =
            match repo.get("default_branch").map(text_of).unwrap_or_default() {
                b if !b.is_empty() => vec!["--branch".into(), b],
                _ => vec![],
            };
        let problem = clone_into(
            &name,
            &path,
            &[command, branch_args.clone()].concat(),
            &url,
            &clone_env,
            machine_git,
        )
        .await;
        let mut problem = problem;
        if let (Some(p), true) = (&problem, refreshed.is_some()) {
            if !p.ends_with(ELSEWHERE) {
                // The computer's mirror could not be copied while GitHub may well be fine: clone from GitHub instead.
                if let Some(fallback) = clone_into(
                    &name,
                    &path,
                    &[direct.clone(), branch_args].concat(),
                    &address(&name)?,
                    &env,
                    false,
                )
                .await
                {
                    let prefix = format!("Git clone of {name} failed: ");
                    return Err(format!(
                        "{p}; cloning from GitHub instead also failed: {}",
                        fallback.strip_prefix(&prefix).unwrap_or(&fallback)
                    ));
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
            for (key, value) in [
                ("remote.origin.url", address(&name)?),
                (
                    "remote.origin.fetch",
                    "+refs/heads/*:refs/remotes/origin/*".into(),
                ),
            ] {
                if !g(s(&["config", key, &value]), clone_env.clone(), 15)
                    .await
                    .is_ok_and(|o| o.ok())
                {
                    return Err("Could not configure base clone origin".into());
                }
            }
        }
        mark_managed(&path, Some(&name), false)?;
    }
    let mut branch = repo.get("default_branch").map(text_of).unwrap_or_default();
    if branch.is_empty() {
        let out = g(
            s(&["symbolic-ref", "--short", "refs/remotes/origin/HEAD"]),
            env.clone(),
            15,
        )
        .await
        .unwrap_or_default();
        branch = out.stdout.trim().trim_start_matches("origin/").to_string();
    }
    if branch.is_empty()
        || branch.starts_with('-')
        || !gitrun::bare(&env, &["check-ref-format", "--branch", &branch], 15)
            .await
            .is_ok_and(|o| o.ok())
    {
        return Err("Repository default branch is missing; refresh Repositories".into());
    }
    let mut mirror_url = g(
        s(&["config", "--get", "remote.tico-mirror.url"]),
        env.clone(),
        15,
    )
    .await
    .unwrap_or_default()
    .stdout
    .trim()
    .to_string();
    if machine_git {
        mirror_url.clear();
    }
    if !mirror_url.is_empty() {
        let mirror = uri_path(&mirror_url);
        if refreshed.is_none() {
            repo.remove("fetch_warning");
        }
        // The runner itself has no turn socket to ask for a refresh: the mirror is used as the last fetch left it.
        if source_env
            .get(crate::gitcred::SOCKET_ENV)
            .is_none_or(|s| s.is_empty())
        {
            repo.insert("fetch_warning".into(), json!(mirror_warning(&mirror)));
        }
    }
    let source = if mirror_url.is_empty() {
        "origin".to_string()
    } else {
        mirror_url.clone()
    };
    let mut local_env = if mirror_url.is_empty() {
        env.clone()
    } else {
        crate::safegit::clean_environment(&env)
    };
    let mut extra: Vec<String> = vec![];
    if !mirror_url.is_empty() {
        let mirror = uri_path(&mirror_url);
        local_env = trusting(&local_env, &[&mirror]);
        extra = s(&[
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.file.allow=always",
            "-c",
        ]);
        extra.push(format!("safe.directory={}", mirror.display()));
    } else {
        extra = s(&["-c", "http.followRedirects=false"]);
    }
    let spec = format!("+refs/heads/{branch}:refs/remotes/origin/{branch}");
    let mut args = extra.clone();
    args.extend(s(&[
        "fetch",
        "--quiet",
        "--no-tags",
        "--no-write-fetch-head",
        "--",
        &source,
        &spec,
    ]));
    let fetched = g(
        args,
        local_env.clone(),
        if mirror_url.is_empty() { 15 } else { 30 },
    )
    .await;
    let (ok, fetch_error) = match fetched {
        Ok(o) if o.ok() => (true, String::new()),
        Ok(o) => (
            false,
            if o.stderr.is_empty() {
                format!("exit {}", o.code)
            } else {
                o.stderr
            },
        ),
        Err(Fault::Timeout(_)) => (false, "timed out".into()),
        Err(e) => (false, e.to_string()),
    };
    if !ok {
        let available = g(
            s(&[
                "show-ref",
                "--verify",
                &format!("refs/remotes/origin/{branch}"),
            ]),
            local_env.clone(),
            15,
        )
        .await;
        if !available.is_ok_and(|o| o.ok()) {
            if machine_git {
                return Err(machine_error(&name, &fetch_error, &local_env));
            }
            let reason = redact::git_failure(&fetch_error, &redact::env_secrets(&local_env), 240);
            return Err(format!(
                "Git fetch of {name} failed: {}",
                if reason.is_empty() {
                    "no reason given".into()
                } else {
                    reason
                }
            ));
        }
        repo.entry("fetch_warning").or_insert(json!(
            "Could not refresh origin; using the base clone (age unknown)"
        ));
    }
    mark_managed(&path, Some(&name), false)?;
    // A normal git push then updates the task branch's tracking ref, rather than comparing it to main.
    if !g(
        s(&[
            "config",
            "remote.origin.fetch",
            "+refs/heads/*:refs/remotes/origin/*",
        ]),
        env.clone(),
        15,
    )
    .await
    .is_ok_and(|o| o.ok())
    {
        return Err("Could not configure task branch tracking".into());
    }
    let head = format!("refs/remotes/origin/{branch}");
    if !g(
        s(&["symbolic-ref", "refs/remotes/origin/HEAD", &head]),
        local_env,
        15,
    )
    .await
    .is_ok_and(|o| o.ok())
    {
        return Err("Could not record base default branch".into());
    }
    Ok((path, branch))
}

/// Clone into a new `path`: None, or why not (git's own reason). What a failed clone left is removed.
async fn clone_into(
    name: &str,
    path: &Path,
    command: &[String],
    url: &str,
    env: &Env,
    machine_git: bool,
) -> Option<String> {
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
            let leaf = path
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default();
            Some(format!("Git clone of {name} failed: {leaf} {ELSEWHERE}"))
        }
        Ok(done) => {
            discard_failed_clone(path, true).await;
            Some(if machine_git {
                machine_error(
                    name,
                    if done.stderr.is_empty() {
                        "exit"
                    } else {
                        &done.stderr
                    },
                    env,
                )
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
    let Ok(meta) = path.symlink_metadata() else {
        return false;
    };
    if meta.file_type().is_symlink() || !owners.contains(&meta.uid()) {
        return false;
    }
    if std::fs::read_dir(path)
        .map(|mut d| d.next().is_none())
        .unwrap_or(false)
    {
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
    match gitrun::bare(
        &env,
        &[
            "config",
            "--file",
            &config_s,
            "--name-only",
            "--get-regexp",
            r"^remote\.",
        ],
        15,
    )
    .await
    {
        Ok(done) if done.code == 0 || done.code == 1 => done
            .stdout
            .split_whitespace()
            .all(|k| k.starts_with("remote.tico-mirror.")),
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
        let has = real_dir(&registered)
            && std::fs::read_dir(&registered)
                .map(|mut d| d.next().is_some())
                .unwrap_or(false);
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
    regular_file::text(&marker, 1024)
        .ok()
        .map(|t| {
            if t.is_empty() {
                json!({})
            } else {
                serde_json::from_str::<Value>(&t).unwrap_or(Value::Null)
            }
        })
        .is_some_and(|v| v["adopted"] == true)
}

/// A flattened instruction symlink contains no new work only if its index is unchanged and its bytes equal
/// the committed link or its unchanged target. A different edit (including a staged one) is never restored.
pub async fn repair_instruction_links(root: &Path, env: &Env) -> Option<String> {
    let Ok(root) = root.canonicalize() else {
        return None;
    };
    let Ok(git_dir) =
        gitrun::git_as_bot(&root, env, &["rev-parse", "--absolute-git-dir"], 15).await
    else {
        return None;
    };
    let git_dir = PathBuf::from(git_dir.stdout.trim_end_matches('\n'));
    if !git_dir.is_absolute() || !real_dir(&git_dir) {
        return None;
    }
    let Ok(list) = gitrun::git_as_bot(
        &root,
        env,
        &[
            "ls-files",
            "--stage",
            "-z",
            "--",
            "AGENTS.md",
            "CLAUDE.md",
            ":(glob)**/AGENTS.md",
            ":(glob)**/CLAUDE.md",
        ],
        15,
    )
    .await
    else {
        return None;
    };
    if !list.ok() {
        return None;
    }
    for entry in list.raw.split(|b| *b == 0) {
        let Ok(entry) = std::str::from_utf8(entry) else {
            continue;
        };
        let Some((info, name)) = entry.split_once('\t') else {
            continue;
        };
        if !info.starts_with("120000 ") || !info.ends_with(" 0") {
            continue;
        }
        let path = root.join(name);
        let Ok(before) = path.symlink_metadata() else {
            continue;
        };
        use std::os::unix::fs::MetadataExt;
        if !before.is_file()
            || before.nlink() != 1
            || path.parent().and_then(|p| p.canonicalize().ok()).as_deref() != path.parent()
        {
            continue;
        }
        if !gitrun::git_as_bot(
            &root,
            env,
            &["diff", "--cached", "--quiet", "HEAD", "--", name],
            15,
        )
        .await
        .is_ok_and(|o| o.ok())
        {
            continue;
        }
        let oid = info.split_whitespace().nth(1).unwrap_or_default();
        let Ok(link) = gitrun::git_as_bot(&root, env, &["cat-file", "blob", oid], 15).await else {
            continue;
        };
        if !link.ok() || link.stdout.is_empty() || link.stdout.contains('\n') {
            continue;
        }
        let Ok(target) = path.parent().unwrap().join(&link.stdout).canonicalize() else {
            continue;
        };
        let Ok(relative) = target.strip_prefix(&root) else {
            continue;
        };
        let Ok(content) = regular_file::read(&path, CONTENT) else {
            continue;
        };
        let Ok(accepted) = regular_file::read(&target, CONTENT) else {
            continue;
        };
        if content != link.raw {
            let spec = format!("HEAD:{}", relative.display());
            let original = gitrun::git_as_bot(&root, env, &["cat-file", "blob", &spec], 15).await;
            if !original.is_ok_and(|o| o.ok() && o.raw == content)
                || accepted != content
                || !gitrun::git_as_bot(
                    &root,
                    env,
                    &["diff", "--quiet", "HEAD", "--", &relative.to_string_lossy()],
                    15,
                )
                .await
                .is_ok_and(|o| o.ok())
            {
                continue;
            }
        }
        // A writer that already opened the regular file keeps writing to this named backup.
        let backup = git_dir
            .join("tico-instruction-backups")
            .join(Path::new(name).parent().unwrap())
            .join(format!(
                "{}.tico-backup-{}-{}-sha256-{}",
                path.file_name().unwrap().to_string_lossy(),
                epoch_s() as u64,
                uuid::Uuid::new_v4().simple(),
                gitrun::sha256_hex(&accepted)
            ));
        if std::fs::create_dir_all(backup.parent().unwrap()).is_err()
            || backup
                .parent()
                .and_then(|p| p.canonicalize().ok())
                .as_deref()
                != backup.parent()
        {
            continue;
        }
        if !path.symlink_metadata().is_ok_and(|m| {
            m.dev() == before.dev()
                && m.ino() == before.ino()
                && m.mtime() == before.mtime()
                && m.mtime_nsec() == before.mtime_nsec()
        }) || regular_file::read(&path, CONTENT).ok().as_ref() != Some(&content)
        {
            continue;
        }
        if std::fs::rename(&path, &backup).is_err() {
            continue;
        }
        if !backup
            .symlink_metadata()
            .is_ok_and(|m| m.dev() == before.dev() && m.ino() == before.ino())
            || std::os::unix::fs::symlink(&link.stdout, &path).is_err()
        {
            let _ = std::fs::hard_link(&backup, &path);
            continue;
        }
        crate::isolation::chown(&path);
    }
    instruction_backup_note(&root, &git_dir).await
}

async fn instruction_backup_note(root: &Path, git_dir: &Path) -> Option<String> {
    use std::os::unix::fs::MetadataExt;
    let saved = git_dir.join("tico-instruction-backups");
    let mut pending = vec![saved.clone()];
    let mut names = vec![];
    while let Some(dir) = pending.pop() {
        if dir.symlink_metadata().is_ok()
            && (!real_dir(&dir) || dir.canonicalize().ok().as_ref() != Some(&dir))
        {
            names.push(dir.strip_prefix(root).unwrap_or(&dir).display().to_string());
            continue;
        }
        let entries = match std::fs::read_dir(&dir) {
            Ok(e) => e,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound && dir == saved => continue,
            Err(_) => {
                names.push(dir.display().to_string());
                continue;
            }
        };
        for entry in entries {
            let Ok(entry) = entry else {
                names.push(dir.display().to_string());
                continue;
            };
            let path = entry.path();
            if real_dir(&path) {
                pending.push(path);
                continue;
            }
            let Some(filename) = path.file_name().and_then(|n| n.to_str()) else {
                continue;
            };
            let Some((name, _)) = filename.rsplit_once(".tico-backup-") else {
                continue;
            };
            let relative = path.strip_prefix(&saved).unwrap().with_file_name(name);
            let digest = filename
                .rsplit_once("-sha256-")
                .map(|(_, hash)| hash.to_string());
            let marked = digest.is_some();
            let digest = if let Some(hash) = digest {
                (hash.len() == 64
                    && hash
                        .bytes()
                        .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)))
                .then_some(hash)
            } else {
                root.join(&relative)
                    .canonicalize()
                    .ok()
                    .filter(|p| p.starts_with(root))
                    .and_then(|p| regular_file::read(p, CONTENT).ok())
                    .map(|b| gitrun::sha256_hex(&b))
            };
            let metadata = path
                .symlink_metadata()
                .ok()
                .filter(|m| m.is_file() && m.nlink() == 1);
            let Some((m, digest)) = metadata.zip(digest) else {
                names.push(
                    path.strip_prefix(root)
                        .unwrap_or(&path)
                        .display()
                        .to_string(),
                );
                continue;
            };
            let unchanged = || {
                regular_file::read(&path, CONTENT).is_ok_and(|b| gitrun::sha256_hex(&b) == digest)
            };
            if !unchanged() {
                names.push(
                    path.strip_prefix(root)
                        .unwrap_or(&path)
                        .display()
                        .to_string(),
                );
                continue;
            }
            // Keep every accepted backup: an open writer may still hold its inode.
            if !path.symlink_metadata().is_ok_and(|after| {
                after.dev() == m.dev()
                    && after.ino() == m.ino()
                    && after.mtime() == m.mtime()
                    && after.mtime_nsec() == m.mtime_nsec()
            }) || !unchanged()
            {
                names.push(
                    path.strip_prefix(root)
                        .unwrap_or(&path)
                        .display()
                        .to_string(),
                );
            } else if !marked {
                // Give a retained legacy backup a baseline only while it still equals the checkout.
                let baseline = path.with_file_name(format!("{filename}-sha256-{digest}"));
                if baseline.symlink_metadata().is_ok() || std::fs::rename(&path, &baseline).is_err()
                {
                    names.push(
                        path.strip_prefix(root)
                            .unwrap_or(&path)
                            .display()
                            .to_string(),
                    );
                }
            }
        }
    }
    if names.is_empty() {
        return None;
    }
    let note = format!(
        "repository copy needs attention: bot instruction edit to review: {}",
        names.iter().take(3).cloned().collect::<Vec<_>>().join(", ")
    );
    crate::util::log(&note);
    Some(note)
}

pub fn changed_file_names(status: &str) -> String {
    let names: Vec<_> = status.lines().filter_map(|s| s.get(3..)).collect();
    let mut text = names.iter().take(3).copied().collect::<Vec<_>>().join(", ");
    if names.len() > 3 {
        text.push_str(&format!(", and {} more", names.len() - 3));
    }
    redact::scrub_credentials(&text, &[])
        .chars()
        .take(240)
        .collect()
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
    regular_file::write(&marker, marker_text.as_bytes(), 1024, false)
        .map_err(|_| "Could not mark managed base clone; check permissions".to_string())?;
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
        async move {
            gitrun::git(path, &env, args, t)
                .await
                .ok()
                .filter(|o| o.ok())
        }
    };
    g(&["worktree", "prune", "--expire", "now"], 120)
        .await
        .ok_or("Could not prune base clone; repair without deleting local work")?;
    let listing = g(&["worktree", "list", "--porcelain"], 15)
        .await
        .ok_or("Could not inspect base clone worktrees")?;
    let count = listing
        .stdout
        .lines()
        .filter(|l| l.starts_with("worktree "))
        .count()
        .saturating_sub(1);
    if count > 0 {
        return Ok(Some(format!("kept: {count} task worktrees")));
    }
    let status = g(&["status", "--porcelain", "--untracked-files=all"], 30)
        .await
        .ok_or("Could not inspect base clone changes; repair without deleting local work")?;
    if !status.stdout.trim().is_empty() {
        return Ok(Some("kept: uncommitted or untracked files".into()));
    }
    let branches = g(
        &[
            "rev-list",
            "--count",
            "--ignore-missing",
            "--branches",
            "HEAD",
            "refs/stash",
            "--not",
            "--remotes",
        ],
        30,
    )
    .await
    .ok_or("Could not inspect local branches; repair without deleting local work")?;
    if branches.stdout.trim().parse::<i64>().unwrap_or(0) > 0 {
        return Ok(Some(
            "kept: local branches with unpublished commits (including HEAD or stash)".into(),
        ));
    }
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::gitlocks::TEST_FIXTURES as FIXTURES;

    struct RepoFixture {
        root: PathBuf,
        remote: PathBuf,
        seed: PathBuf,
        base: PathBuf,
        env: Env,
        _guard: tokio::sync::MutexGuard<'static, ()>,
    }

    fn sh(path: &Path, args: &[&str]) -> String {
        let out = std::process::Command::new("git")
            .args([
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@acme.example",
                "-c",
                "commit.gpgsign=false",
            ])
            .args(args)
            .current_dir(path)
            .env("GIT_CONFIG_GLOBAL", "/dev/null")
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "{args:?}: {}",
            String::from_utf8_lossy(&out.stderr)
        );
        String::from_utf8(out.stdout).unwrap().trim().to_string()
    }

    impl RepoFixture {
        async fn new() -> Self {
            Self::with_base_ref_format(None).await.unwrap()
        }
        async fn with_base_ref_format(format: Option<&str>) -> Option<Self> {
            let guard = FIXTURES.lock().await;
            let root =
                std::env::temp_dir().join(format!("tico-repair-{}", uuid::Uuid::new_v4().simple()));
            std::fs::create_dir(&root).unwrap();
            let (remote, seed, base) = (
                root.join("remote.git"),
                root.join("seed"),
                root.join("base"),
            );
            if let Some(format) = format {
                let out = std::process::Command::new("git")
                    .args([
                        "init",
                        "-q",
                        "-b",
                        "main",
                        &format!("--ref-format={format}"),
                        base.to_str().unwrap(),
                    ])
                    .current_dir(&root)
                    .env("GIT_CONFIG_GLOBAL", "/dev/null")
                    .env("GIT_CONFIG_NOSYSTEM", "1")
                    .output()
                    .unwrap();
                if !out.status.success() {
                    let error = String::from_utf8_lossy(&out.stderr);
                    assert!(
                        error.contains("unknown option")
                            || error.contains("unknown ref storage format")
                            || error.contains("invalid value for --ref-format"),
                        "git init failed: {error}"
                    );
                    eprintln!("SKIP reftable fixture: this Git lacks git init --ref-format={format}: {error}");
                    std::fs::remove_dir_all(&root).unwrap();
                    return None;
                }
            }
            sh(
                &root,
                &[
                    "init",
                    "--bare",
                    "-q",
                    "-b",
                    "main",
                    remote.to_str().unwrap(),
                ],
            );
            sh(
                &root,
                &[
                    "clone",
                    "-q",
                    remote.to_str().unwrap(),
                    seed.to_str().unwrap(),
                ],
            );
            std::fs::create_dir_all(seed.join("templates/employee-repo")).unwrap();
            std::fs::write(seed.join("CLAUDE.md"), "Repository rules\n").unwrap();
            std::fs::write(seed.join("templates/employee-repo/AGENT.md"), "Bot rules\n").unwrap();
            for (name, target) in [
                ("AGENTS.md", "CLAUDE.md"),
                ("templates/employee-repo/AGENTS.md", "AGENT.md"),
                ("templates/employee-repo/CLAUDE.md", "AGENT.md"),
            ] {
                std::os::unix::fs::symlink(target, seed.join(name)).unwrap();
            }
            sh(&seed, &["add", "."]);
            sh(&seed, &["commit", "-qm", "initial"]);
            sh(&seed, &["push", "-q", "origin", "main"]);
            if format.is_some() {
                sh(
                    &base,
                    &["remote", "add", "origin", remote.to_str().unwrap()],
                );
                sh(&base, &["fetch", "-q", "origin"]);
                sh(&base, &["reset", "-q", "--hard", "origin/main"]);
            } else {
                sh(
                    &root,
                    &[
                        "clone",
                        "-q",
                        remote.to_str().unwrap(),
                        base.to_str().unwrap(),
                    ],
                );
            }
            let mut env = crate::safegit::clean_environment(&crate::env::process_environment());
            env.extend([
                ("GIT_CONFIG_COUNT".into(), "1".into()),
                ("GIT_CONFIG_KEY_0".into(), "protocol.file.allow".into()),
                ("GIT_CONFIG_VALUE_0".into(), "always".into()),
            ]);
            crate::gitlocks::own(std::slice::from_ref(&root));
            Some(Self {
                root,
                remote,
                seed,
                base,
                env,
                _guard: guard,
            })
        }
        fn manager(&self) -> Repositories {
            Repositories::new(
                &self.root.join("workspace"),
                &self.root.join("state"),
                Client::new("http://localhost", "unused", 1, 0).unwrap(),
            )
        }
    }
    impl Drop for RepoFixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    #[tokio::test]
    async fn repository_syncs_do_not_block_other_refreshes_and_same_repository_wait_is_bounded() {
        let f = RepoFixture::new().await;
        let manager = f.manager();
        manager.rows.lock().unwrap().insert(
            "acme/web".into(),
            json!({"full_name": "Acme/Web", "state": "cloned"}),
        );
        let guard = manager.sync_guard("acme/web").await.unwrap();
        std::fs::create_dir_all(&manager.mirrors).unwrap();
        std::os::unix::fs::symlink(&f.remote, manager.mirrors.join("acme__other.git")).unwrap();
        // Reach a deterministic mirror validation error without any network access while A is syncing.
        let other = tokio::time::timeout(
            SYNC_LOCK_WAIT * 3,
            manager.refresh_mirror("acme/other", "unused"),
        )
        .await
        .unwrap()
        .unwrap_err();
        assert!(other.contains("symlink"), "{other}");
        let busy = tokio::time::timeout(
            SYNC_LOCK_WAIT * 3,
            manager.refresh_mirror("Acme/Web", "unused"),
        )
        .await
        .unwrap()
        .unwrap_err();
        assert!(busy.contains("sync is busy"), "{busy}");
        tokio::time::timeout(
            SYNC_LOCK_WAIT * 3,
            manager.update("acme/web", &json!({"full_name": "Acme/Web"}), epoch_s()),
        )
        .await
        .unwrap();
        assert_eq!(manager.row("acme/web")["state"], "cloned");
        drop(guard);
        assert!(manager.sync_guard("ACME/WEB").await.is_ok());
    }

    #[tokio::test]
    async fn malformed_mirrors_and_old_ref_locks_are_reported_without_replacement() {
        let f = RepoFixture::new().await;
        let manager = f.manager();
        let mirror = manager.mirror_path("acme/web").ok().unwrap();
        std::fs::create_dir_all(mirror.parent().unwrap()).unwrap();
        sh(&f.root, &["init", "--bare", "-q", mirror.to_str().unwrap()]);
        let original = std::fs::read(mirror.join("config")).unwrap();
        std::fs::write(mirror.join("config"), "[broken\n").unwrap();
        let prefix = vec!["-c".into(), "protocol.file.allow=always".into()];
        let lock = mirror.join("config.lock");
        std::fs::write(&lock, "still owned").unwrap();
        std::fs::File::open(&lock)
            .unwrap()
            .set_times(
                std::fs::FileTimes::new().set_modified(
                    std::time::SystemTime::now() - std::time::Duration::from_secs(3600),
                ),
            )
            .unwrap();
        let filled = manager
            .fill_mirror(
                "acme/web",
                &mirror,
                &prefix,
                "main",
                f.remote.to_str().unwrap(),
                "acme/web",
                &f.env,
                &f.env,
                10,
                epoch_s(),
                true,
            )
            .await;
        assert!(
            matches!(filled, Err(Fail::Msg(m)) if m.contains("minutes old") && m.contains("run: rm --"))
        );
        assert_eq!(std::fs::read_to_string(&lock).unwrap(), "still owned");
        std::fs::remove_file(&lock).unwrap();
        let filled = manager
            .fill_mirror(
                "acme/web",
                &mirror,
                &prefix,
                "main",
                f.remote.to_str().unwrap(),
                "acme/web",
                &f.env,
                &f.env,
                10,
                epoch_s(),
                true,
            )
            .await;
        assert!(
            matches!(filled, Err(Fail::Msg(m)) if m.contains("repository format cannot be read safely"))
        );
        assert_eq!(
            std::fs::read_to_string(mirror.join("config")).unwrap(),
            "[broken\n"
        );
        assert_eq!(std::fs::read_dir(&manager.mirrors).unwrap().count(), 1);
        // Once a person repairs the config, normal fetching can proceed.
        std::fs::write(mirror.join("config"), original).unwrap();
        assert!(manager
            .fill_mirror(
                "acme/web",
                &mirror,
                &prefix,
                "main",
                f.remote.to_str().unwrap(),
                "acme/web",
                &f.env,
                &f.env,
                10,
                epoch_s(),
                true
            )
            .await
            .is_ok());
        let ref_lock = mirror.join("refs/heads/main.lock");
        std::fs::write(&ref_lock, "still owned").unwrap();
        std::fs::File::open(&ref_lock)
            .unwrap()
            .set_times(
                std::fs::FileTimes::new().set_modified(
                    std::time::SystemTime::now() - std::time::Duration::from_secs(3600),
                ),
            )
            .unwrap();
        let inode = std::os::unix::fs::MetadataExt::ino(&ref_lock.metadata().unwrap());
        sh(&f.seed, &["commit", "-q", "--allow-empty", "-m", "later"]);
        sh(&f.seed, &["push", "-q"]);
        let failed = manager
            .fill_mirror(
                "acme/web",
                &mirror,
                &prefix,
                "main",
                f.remote.to_str().unwrap(),
                "acme/web",
                &f.env,
                &f.env,
                10,
                epoch_s(),
                true,
            )
            .await;
        assert!(
            matches!(failed, Err(Fail::Msg(m)) if m.contains("minutes old") && m.contains(&ref_lock.display().to_string()))
        );
        assert_eq!(
            std::os::unix::fs::MetadataExt::ino(&ref_lock.metadata().unwrap()),
            inode
        );
        assert_eq!(std::fs::read_to_string(&ref_lock).unwrap(), "still owned");
        assert!(!mirror.join("tico-lock-backups").exists());
    }

    #[tokio::test]
    async fn only_a_git_unreadable_default_format_mirror_is_preserved_before_rebuild() {
        let f = RepoFixture::new().await;
        let manager = f.manager();
        let mirror = manager.mirror_path("acme/web").ok().unwrap();
        std::fs::create_dir_all(&manager.mirrors).unwrap();
        sh(
            &f.root,
            &[
                "clone",
                "--bare",
                "-q",
                f.remote.to_str().unwrap(),
                mirror.to_str().unwrap(),
            ],
        );
        let readable = manager
            .preserve_mirror("acme/web", f.remote.to_str().unwrap(), &[], &f.env, 10)
            .await;
        assert!(
            matches!(readable, Err(Fail::Msg(m)) if m.contains("mirror may still be in use") && m.contains(&mirror.display().to_string()) && m.contains("fsck --full"))
        );
        assert!(mirror.exists());
        // Bare clone refs are packed. Removing its empty refs tree makes Git reject the repository
        // while the published refs and default-format config remain verifiable without Git setup.
        std::fs::remove_dir_all(mirror.join("refs")).unwrap();
        assert!(manager
            .preserve_mirror("acme/web", f.remote.to_str().unwrap(), &[], &f.env, 10)
            .await
            .is_ok());
        assert!(!mirror.exists());
        let prefix = vec!["-c".into(), "protocol.file.allow=always".into()];
        assert!(manager
            .fill_mirror(
                "acme/web",
                &mirror,
                &prefix,
                "main",
                f.remote.to_str().unwrap(),
                "acme/web",
                &f.env,
                &f.env,
                10,
                epoch_s(),
                true
            )
            .await
            .is_ok());
        assert_eq!(
            sh(&mirror, &["rev-parse", "main"]),
            sh(&f.seed, &["rev-parse", "HEAD"])
        );
        let again = manager
            .preserve_mirror("acme/web", f.remote.to_str().unwrap(), &prefix, &f.env, 10)
            .await;
        assert!(matches!(again, Err(Fail::Msg(m)) if m.contains("previous broken mirror")));
    }

    #[tokio::test]
    async fn unreadable_base_format_preserves_unpushed_branches_stash_index_files_and_worktrees() {
        let f = RepoFixture::new().await;
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
        sh(
            &f.base,
            &[
                "worktree",
                "add",
                "-q",
                "-b",
                "task-work",
                tree.to_str().unwrap(),
                "origin/main",
            ],
        );
        std::fs::write(tree.join("draft"), "task work\n").unwrap();
        let refs = sh(&f.base, &["show-ref"]);
        let index = std::fs::read(f.base.join(".git/index")).unwrap();
        let settings = vec![
            ("remote.origin.url".into(), f.remote.display().to_string()),
            ("remote.tico-mirror.url".into(), file_uri(&f.remote)),
        ];
        let prefix = vec!["-c".into(), "protocol.file.allow=always".into()];
        std::fs::write(f.base.join(".git/config.lock"), "recent").unwrap();
        let original = std::fs::read(f.base.join(".git/config")).unwrap();
        let blocked = configure_base(&f.base, &settings, &prefix, &f.env, "acme/web").await;
        assert!(
            matches!(blocked, Err(Fail::Msg(m)) if m.contains("config.lock") && m.contains("Git lock"))
        );
        assert_eq!(std::fs::read(f.base.join(".git/config")).unwrap(), original);
        std::fs::remove_file(f.base.join(".git/config.lock")).unwrap();
        // Another runner thread can fork while a Git command holds this gate, then wait before exec.
        let gate = crate::gitlocks::recovery_lock(&f.base.join(".git")).unwrap();
        let child = crate::lock::inherited_files();
        drop(gate);
        std::fs::write(f.base.join(".git/config"), "[broken\n").unwrap();
        let refused = configure_base(&f.base, &settings, &prefix, &f.env, "acme/web").await;
        assert!(
            matches!(&refused, Err(Fail::Msg(m)) if m.contains("repository format cannot be read safely")),
            "{refused:?}"
        );
        assert_eq!(
            std::fs::read_to_string(f.base.join(".git/config")).unwrap(),
            "[broken\n"
        );
        drop(child);
        std::fs::write(f.base.join(".git/config"), &original).unwrap();
        assert!(
            configure_base(&f.base, &settings, &prefix, &f.env, "acme/web")
                .await
                .is_ok()
        );
        assert_eq!(sh(&f.base, &["show-ref"]), refs);
        assert_eq!(std::fs::read(f.base.join(".git/index")).unwrap(), index);
        assert_eq!(
            std::fs::read_to_string(f.base.join("feature")).unwrap(),
            "unstaged\n"
        );
        assert_eq!(
            std::fs::read_to_string(f.base.join("untracked")).unwrap(),
            "not committed\n"
        );
        assert_eq!(
            std::fs::read_to_string(tree.join("draft")).unwrap(),
            "task work\n"
        );
        assert_eq!(sh(&tree, &["branch", "--show-current"]), "task-work");
        let backups: Vec<_> = std::fs::read_dir(f.base.join(".git"))
            .unwrap()
            .flatten()
            .filter(|e| {
                e.file_name()
                    .to_string_lossy()
                    .starts_with("tico-config-backup-")
            })
            .collect();
        assert!(
            backups.is_empty(),
            "an unreadable format must never be replaced"
        );
    }

    #[tokio::test]
    async fn reftable_base_format_is_preserved_or_repair_is_refused() {
        let Some(f) = RepoFixture::with_base_ref_format(Some("reftable")).await else {
            return;
        };
        sh(&f.base, &["branch", "bot-work"]);
        std::fs::write(f.base.join("CLAUDE.md"), "stashed work\n").unwrap();
        sh(&f.base, &["stash", "-q"]);
        sh(&f.base, &["config", "extensions.worktreeConfig", "true"]);
        let task = f.root.join("task");
        sh(
            &f.base,
            &[
                "worktree",
                "add",
                "-q",
                "-b",
                "task-work",
                task.to_str().unwrap(),
            ],
        );
        std::fs::write(task.join("draft"), "task work\n").unwrap();
        let refs = sh(&f.base, &["show-ref"]);
        let config = f.base.join(".git/config");
        let original = std::fs::read(&config).unwrap();
        let index = std::fs::read(f.base.join(".git/index")).unwrap();
        let settings = vec![
            ("remote.origin.url".into(), f.remote.display().to_string()),
            ("remote.tico-mirror.url".into(), file_uri(&f.remote)),
        ];
        let mut malformed = original.clone();
        malformed.extend_from_slice(b"\n[broken\n");
        std::fs::write(&config, &malformed).unwrap();
        let refused = configure_base(&f.base, &settings, &[], &f.env, "acme/web").await;
        assert!(
            matches!(refused, Err(Fail::Msg(m)) if m.contains("repository format cannot be read safely"))
        );
        assert_eq!(std::fs::read(&config).unwrap(), malformed);
        assert!(f.base.join(".git/reftable").is_dir());
        assert_eq!(std::fs::read(f.base.join(".git/index")).unwrap(), index);
        assert!(!f.base.join(".git/config.lock").exists());
        // With a verified config, stage and install through the same path used by config repair.
        std::fs::write(&config, &original).unwrap();
        let before = repository_format(&config, &f.env).await.ok().unwrap();
        let staged = f.root.join("staged-config");
        assert!(
            stage_base_config(&staged, &original, &settings, &[], &f.env)
                .await
                .is_ok()
        );
        assert!(BaseConfigLock::acquire(&config)
            .unwrap()
            .install(Some(&original), &staged)
            .is_ok());
        assert_eq!(
            repository_format(&config, &f.env).await.ok().unwrap(),
            before
        );
        assert_eq!(
            sh(&f.base, &["config", "core.repositoryformatversion"]),
            "1"
        );
        assert_eq!(
            sh(&f.base, &["config", "extensions.refStorage"]),
            "reftable"
        );
        assert_eq!(
            sh(&f.base, &["config", "extensions.worktreeConfig"]),
            "true"
        );
        assert_eq!(sh(&f.base, &["show-ref"]), refs);
        assert_eq!(sh(&task, &["branch", "--show-current"]), "task-work");
        assert_eq!(
            std::fs::read_to_string(task.join("draft")).unwrap(),
            "task work\n"
        );
    }

    #[tokio::test]
    async fn config_staging_keeps_all_extensions_and_refuses_unknown_formats() {
        let f = RepoFixture::new().await;
        let config = f.root.join("format-config");
        let original = b"[core]\nrepositoryFormatVersion = 1\n[extensions]\nobjectFormat = sha256\nrefStorage = reftable\nworktreeConfig = true\npreciousObjects = true\nfutureFormat = retained\n";
        let settings = vec![("remote.tico-mirror.url".into(), file_uri(&f.remote))];
        assert!(stage_base_config(&config, original, &settings, &[], &f.env)
            .await
            .is_ok());
        let format = repository_format(&config, &f.env).await.ok().unwrap();
        for (key, value) in [
            ("core.repositoryformatversion", "1"),
            ("extensions.objectformat", "sha256"),
            ("extensions.refstorage", "reftable"),
            ("extensions.worktreeconfig", "true"),
            ("extensions.preciousobjects", "true"),
            ("extensions.futureformat", "retained"),
        ] {
            assert_eq!(format.get(key).unwrap(), &[value.to_string()]);
        }
        std::fs::remove_file(&config).unwrap();
        let changes_format = vec![("extensions.objectFormat".into(), "sha1".into())];
        assert!(
            stage_base_config(&config, original, &changes_format, &[], &f.env)
                .await
                .is_err()
        );
        for bytes in [
            b"[broken\n".as_slice(),
            b"[core]\nrepositoryFormatVersion = 2\n",
            b"[core]\nrepositoryFormatVersion = 1\n[include]\npath = extra-config\n",
        ] {
            std::fs::write(&config, bytes).unwrap();
            assert!(repository_format(&config, &f.env).await.is_err());
        }
    }

    #[tokio::test]
    async fn base_config_repair_holds_gits_lock_until_atomic_install() {
        use std::os::unix::fs::PermissionsExt;
        let f = RepoFixture::new().await;
        let config = f.base.join(".git/config");
        set_mode(&config, 0o600);
        let original = std::fs::read(&config).unwrap();
        let rebuilt = f.root.join("rebuilt-config");
        let mut replacement = original.clone();
        replacement.extend_from_slice(b"\n[repair]\n\tinstalled = true\n");
        std::fs::write(&rebuilt, &replacement).unwrap();

        // A writer starting after the metadata check must fail while repair holds config.lock.
        let lock = BaseConfigLock::acquire(&config).unwrap();
        let writer = run_as_bot(
            &[],
            &[
                "-C",
                f.base.to_str().unwrap(),
                "config",
                "--local",
                "owner.edit",
                "concurrent",
            ],
            &f.env,
            10,
        )
        .await
        .ok()
        .unwrap();
        assert!(
            !writer.ok(),
            "a concurrent Git writer acquired repair's lock"
        );
        assert!(config.with_file_name("config.lock").exists());
        assert_eq!(std::fs::read(&config).unwrap(), original);
        assert!(lock.install(Some(&original), &rebuilt).is_ok());
        assert_eq!(std::fs::read(&config).unwrap(), replacement);
        assert!(!config.with_file_name("config.lock").exists());
        assert_eq!(
            config.metadata().unwrap().permissions().mode() & 0o777,
            0o600
        );
        let backups: Vec<_> = std::fs::read_dir(config.parent().unwrap())
            .unwrap()
            .flatten()
            .filter(|e| {
                e.file_name()
                    .to_string_lossy()
                    .starts_with("tico-config-backup-")
            })
            .collect();
        assert_eq!(backups.len(), 1);
        assert_eq!(std::fs::read(backups[0].path()).unwrap(), original);
        assert_eq!(
            backups[0].metadata().unwrap().permissions().mode() & 0o777,
            0o600
        );
        sh(
            &f.base,
            &["config", "--local", "owner.edit", "after repair"],
        );
        assert_eq!(
            sh(&f.base, &["config", "--get", "repair.installed"]),
            "true"
        );
        assert_eq!(
            sh(&f.base, &["config", "--get", "owner.edit"]),
            "after repair"
        );
    }

    #[tokio::test]
    async fn base_config_repair_preserves_late_git_locks_and_completed_edits() {
        let f = RepoFixture::new().await;
        let config = f.base.join(".git/config");
        let original = std::fs::read(&config).unwrap();
        let rebuilt = f.root.join("rebuilt-config");
        std::fs::write(&rebuilt, &original).unwrap();
        let pending = config.with_file_name("config.lock");
        std::fs::write(&pending, "writer's pending config").unwrap();
        assert!(BaseConfigLock::acquire(&config).is_err());
        assert_eq!(
            std::fs::read_to_string(&pending).unwrap(),
            "writer's pending config"
        );
        assert_eq!(std::fs::read(&config).unwrap(), original);
        std::fs::remove_file(&pending).unwrap();

        // A writer that commits before repair acquires the lock invalidates its earlier snapshot.
        sh(
            &f.base,
            &["config", "--local", "owner.edit", "saved during repair"],
        );
        let edited = std::fs::read(&config).unwrap();
        let result = BaseConfigLock::acquire(&config)
            .unwrap()
            .install(Some(&original), &rebuilt);
        assert!(matches!(result, Err(Fail::Msg(m)) if m.contains("changed during repair")));
        assert_eq!(std::fs::read(&config).unwrap(), edited);
        assert!(!pending.exists());
        assert!(!std::fs::read_dir(config.parent().unwrap())
            .unwrap()
            .flatten()
            .any(|e| e
                .file_name()
                .to_string_lossy()
                .starts_with("tico-config-backup-")));

        // A failed staging read releases only repair's own lock and keeps the current config.
        std::fs::remove_file(&rebuilt).unwrap();
        assert!(BaseConfigLock::acquire(&config)
            .unwrap()
            .install(Some(&edited), &rebuilt)
            .is_err());
        assert!(!pending.exists());
        assert_eq!(std::fs::read(&config).unwrap(), edited);
        sh(
            &f.base,
            &["config", "--local", "owner.edit", "writer can continue"],
        );
    }

    #[cfg(target_os = "linux")]
    #[tokio::test]
    #[ignore = "requires a disposable Linux two-user environment"]
    async fn isolated_base_config_failure_refuses_before_creating_bot_controlled_scratch() {
        assert_eq!(unsafe { libc::geteuid() }, 0);
        assert_eq!(crate::isolation::identity(), Some((10003, 10002)));
        let f = RepoFixture::new().await;
        crate::isolation::adopt(std::slice::from_ref(&f.base));
        let git = f.base.join(".git");
        let original = std::fs::read(git.join("config")).unwrap();
        // The bot owns the metadata and can make config updates fail while leaving the config readable.
        set_mode(&git, 0o555);
        let settings = vec![(
            "remote.origin.url".into(),
            "https://github.com/acme/web.git".into(),
        )];
        let prefix = vec!["-c".into(), "protocol.file.allow=always".into()];
        let failed = run_as_bot(
            &prefix,
            &[
                "-C",
                f.base.to_str().unwrap(),
                "config",
                "--local",
                "remote.origin.url",
                "https://github.com/acme/web.git",
            ],
            &f.env,
            10,
        )
        .await
        .ok()
        .unwrap();
        assert!(
            !failed.ok() && failed.stderr.contains("Permission denied"),
            "{}",
            failed.stderr
        );
        let result = configure_base(&f.base, &settings, &prefix, &f.env, "acme/web").await;
        assert!(
            matches!(result, Err(Fail::Msg(m)) if m.contains("repository copy needs attention") && m.contains("separate user"))
        );
        assert_eq!(std::fs::read(git.join("config")).unwrap(), original);
        assert!(
            !std::fs::read_dir(&f.root).unwrap().flatten().any(|e| e
                .file_name()
                .to_string_lossy()
                .starts_with(".tico-base-repair-")),
            "no scratch checkout can expose a bot-controlled config symlink to the supervisor"
        );
        assert!(!std::fs::read_dir(&git).unwrap().flatten().any(|e| e
            .file_name()
            .to_string_lossy()
            .starts_with("tico-config-backup-")));
    }

    #[tokio::test]
    async fn malformed_adopted_base_config_keeps_the_original_instead_of_a_local_mirror_origin() {
        let f = RepoFixture::new().await;
        sh(
            &f.base,
            &["remote", "set-url", "origin", "git@github.com:acme/web.git"],
        );
        mark_managed(&f.base, Some("acme/web"), true).unwrap();
        let config = f.base.join(".git/config");
        let mut original = std::fs::read(&config).unwrap();
        original.extend_from_slice(b"\n[broken\n");
        std::fs::write(&config, &original).unwrap();
        let index = std::fs::read(f.base.join(".git/index")).unwrap();
        let settings = vec![
            ("remote.tico-mirror.url".into(), file_uri(&f.remote)),
            (
                "remote.tico-mirror.fetch".into(),
                "+refs/heads/main:refs/remotes/tico-mirror/main".into(),
            ),
        ];
        let prefix = vec!["-c".into(), "protocol.file.allow=always".into()];
        let result = configure_base(&f.base, &settings, &prefix, &f.env, "acme/web").await;
        assert!(
            matches!(result, Err(Fail::Msg(m)) if m.contains("original GitHub origin cannot be verified and preserved"))
        );
        assert_eq!(std::fs::read(config).unwrap(), original);
        assert_eq!(std::fs::read(f.base.join(".git/index")).unwrap(), index);
        assert!(!std::fs::read_dir(f.base.join(".git"))
            .unwrap()
            .flatten()
            .any(|e| e
                .file_name()
                .to_string_lossy()
                .starts_with("tico-config-backup-")));
        assert!(!std::fs::read_dir(&f.root).unwrap().flatten().any(|e| e
            .file_name()
            .to_string_lossy()
            .starts_with(".tico-base-repair-")));
    }

    #[tokio::test]
    async fn metadata_inspection_and_managed_marker_writes_refuse_special_files() {
        if !regular_file::in_child("repositories::tests::metadata_inspection_and_managed_marker_writes_refuse_special_files").await { return; }
        let f = RepoFixture::new().await;
        let marker = f.base.join(".git/tico-managed");
        if marker.exists() {
            std::fs::remove_file(&marker).unwrap();
        }
        regular_file::fifo(&marker);
        assert!(!was_adopted(&f.base));
        assert!(mark_managed(&f.base, Some("acme/repo"), false).is_err());
        let repos = f.manager();
        std::fs::create_dir_all(&repos.root).unwrap();
        std::os::unix::fs::symlink(&f.base, repos.root.join("acme__repo")).unwrap();
        assert!(repos.markers().is_empty());
        let alternate = f.base.join(".git/objects/info/alternates");
        regular_file::fifo(&alternate);
        assert!(no_alternate_users(&[f.base.clone()], &f.remote).is_err());
        for name in ["HEAD", "packed-refs"] {
            let path = f.remote.join(name);
            let old = std::fs::read(&path).ok();
            if path.exists() {
                std::fs::remove_file(&path).unwrap();
            }
            regular_file::fifo(&path);
            assert!(mirror_refs(&f.remote).is_err());
            std::fs::remove_file(&path).unwrap();
            if let Some(old) = old {
                std::fs::write(&path, old).unwrap();
            }
        }
    }

    #[tokio::test]
    async fn flattened_instruction_links_are_repaired_but_bot_edits_stay_and_are_named() {
        let f = RepoFixture::new().await;
        let interrupted = f.base.join(".git").join(format!(
            "tico-instruction-link-{}",
            uuid::Uuid::new_v4().simple()
        ));
        std::os::unix::fs::symlink("CLAUDE.md", &interrupted).unwrap();
        for (name, text) in [
            ("AGENTS.md", "Repository rules\n"),
            ("templates/employee-repo/AGENTS.md", "Bot rules\n"),
            ("templates/employee-repo/CLAUDE.md", "Bot rules\n"),
        ] {
            let path = f.base.join(name);
            std::fs::remove_file(&path).unwrap();
            std::fs::write(path, text).unwrap();
        }
        assert!(repair_instruction_links(&f.base, &f.env).await.is_none());
        assert_eq!(sh(&f.base, &["status", "--porcelain"]), "");
        assert!(
            crate::gitlocks::check(&f.base.join(".git")).is_ok(),
            "an interrupted instruction repair cannot block config recovery"
        );
        std::fs::remove_file(f.base.join("AGENTS.md")).unwrap();
        std::fs::write(f.base.join("AGENTS.md"), "A bot's real instruction edit\n").unwrap();
        // Even an exact copy is work when staged deliberately as a new regular file.
        std::fs::remove_file(f.base.join("templates/employee-repo/CLAUDE.md")).unwrap();
        std::fs::write(
            f.base.join("templates/employee-repo/CLAUDE.md"),
            "Bot rules\n",
        )
        .unwrap();
        sh(&f.base, &["add", "templates/employee-repo/CLAUDE.md"]);
        sh(
            &f.seed,
            &["commit", "-q", "--allow-empty", "-m", "upstream"],
        );
        sh(&f.seed, &["push", "-q"]);
        let before = sh(&f.base, &["rev-parse", "HEAD"]);
        let (state, why) = crate::placement::fast_forward(&f.base, &f.env).await;
        assert_eq!(state, "kept", "{why}");
        assert!(
            why.contains("AGENTS.md") && why.contains("templates/employee-repo/CLAUDE.md"),
            "{why}"
        );
        assert_eq!(sh(&f.base, &["rev-parse", "HEAD"]), before);
        assert_eq!(
            std::fs::read_to_string(f.base.join("AGENTS.md")).unwrap(),
            "A bot's real instruction edit\n"
        );
        assert!(!f
            .base
            .join("templates/employee-repo/CLAUDE.md")
            .is_symlink());
    }

    #[test]
    fn folders_and_addresses_follow_the_shared_naming() {
        assert_eq!(base_folder("Acme/Product"), "acme__product");
        assert_eq!(
            address("Acme/product").unwrap(),
            "https://github.com/Acme/product.git"
        );
        assert!(address("https://user@github.com/a/b.git").is_err());
        assert!(
            address("ssh://git@github.com/a/b.git").is_ok()
                && address("git@host.example:a/b.git").is_ok()
        );
        let f = folder("https://gitlab.example/team/app.git").unwrap();
        assert!(f.starts_with("team__app-") && f.len() == "team__app-".len() + 12);
        assert!(
            github_origin("git@github.com:Acme/product.git", "acme/product")
                && !github_origin("https://x.example/acme/product", "acme/product")
        );
        assert_eq!(wait_words(300.0), "5 minutes");
        assert_eq!(wait_words(6.0 * 3600.0), "6 hours");
        assert_eq!(py_g(5.0), "5");
        assert_eq!(py_g(5.5), "5.5");
    }

    #[tokio::test]
    async fn a_half_clone_the_bot_user_made_is_still_tico_s_and_one_by_anyone_else_is_not() {
        use std::os::unix::fs::MetadataExt;
        let _guard = FIXTURES.lock().await;
        let tmp = std::env::temp_dir().join(format!("tico-half-clone-{}", std::process::id()));
        let path = tmp.join("half");
        std::fs::create_dir_all(&path).unwrap();
        let git = |args: &[&str]| {
            assert!(std::process::Command::new("git")
                .args(args)
                .status()
                .unwrap()
                .success())
        };
        let p = path.to_str().unwrap();
        git(&["init", "-q", p]);
        git(&["-C", p, "remote", "add", "tico-mirror", "file:///mirror"]);
        let bot = path.metadata().unwrap().uid();
        // The runner is another user; the folder was made by the bot user the clone ran as.
        assert!(half_clone_of(&path, &[bot + 1, bot]).await);
        // Made by neither the runner nor its bot user: someone else's, never Tico's.
        assert!(!half_clone_of(&path, &[bot + 1, bot + 2]).await);
        // A person's clone with its own origin is never a half clone, whoever made it.
        git(&[
            "-C",
            p,
            "remote",
            "add",
            "origin",
            "https://github.com/acme/web.git",
        ]);
        assert!(!half_clone_of(&path, &[bot]).await);
        std::fs::remove_dir_all(&tmp).unwrap();
    }
    #[tokio::test]
    async fn mirror_relocation_refuses_a_git_worktree_added_during_the_remote_query() {
        use std::os::unix::fs::PermissionsExt;
        let f = RepoFixture::new().await;
        let manager = f.manager();
        let mirror = manager.mirror_path("acme/web").ok().unwrap();
        std::fs::create_dir_all(&manager.mirrors).unwrap();
        sh(
            &f.root,
            &[
                "clone",
                "--bare",
                "-q",
                f.remote.to_str().unwrap(),
                mirror.to_str().unwrap(),
            ],
        );
        let refs = mirror_refs(&mirror).ok().unwrap();
        let config = std::fs::read(mirror.join("config")).unwrap();
        let task = f.root.join("task");
        let ssh = f.root.join("fixture-ssh");
        let quote = |p: &Path| crate::gitcred::shell_quote(p.to_str().unwrap());
        std::fs::write(&ssh, format!(
            "#!/bin/sh\nset -e\ngit --git-dir={} worktree add -q --detach {} HEAD\nexec git-upload-pack {}\n",
            quote(&mirror), quote(&task), quote(&f.remote)
        )).unwrap();
        std::fs::set_permissions(&ssh, std::fs::Permissions::from_mode(0o700)).unwrap();
        let prefix = vec![
            "-c".into(),
            format!("core.sshCommand={}", quote(&ssh)),
            "-c".into(),
            "protocol.ssh.allow=always".into(),
        ];
        let mut env = f.env.clone();
        env.insert("GIT_SSH_VARIANT".into(), "ssh".into());
        let result = manager
            .preserve_mirror("acme/web", "ssh://fixture/acme/web.git", &prefix, &env, 10)
            .await;
        let reason = match &result {
            Err(Fail::Msg(m)) => m.as_str(),
            _ => "unexpected mirror preservation result",
        };
        assert!(
            matches!(&result, Err(Fail::Msg(m)) if m.contains("mirror may still be in use") && m.contains("fsck --full")),
            "{reason}"
        );
        assert_eq!(std::fs::read(mirror.join("config")).unwrap(), config);
        assert_eq!(mirror_refs(&mirror).ok().unwrap(), refs);
        assert_eq!(
            sh(&task, &["rev-parse", "HEAD"]),
            sh(&f.seed, &["rev-parse", "HEAD"])
        );
        assert_eq!(std::fs::read_dir(&manager.mirrors).unwrap().count(), 1);
    }

    #[tokio::test]
    async fn mirror_rebuild_refuses_registered_worktrees() {
        let f = RepoFixture::new().await;
        let manager = f.manager();
        let mirror = manager.mirror_path("acme/web").ok().unwrap();
        std::fs::create_dir_all(&manager.mirrors).unwrap();
        sh(
            &f.root,
            &[
                "clone",
                "--bare",
                "-q",
                f.remote.to_str().unwrap(),
                mirror.to_str().unwrap(),
            ],
        );
        let task = f.root.join("task");
        sh(
            &mirror,
            &["worktree", "add", "--detach", task.to_str().unwrap()],
        );
        std::fs::write(task.join("draft"), "unpublished worktree work").unwrap();
        std::fs::write(mirror.join("config"), "[broken\n").unwrap();
        let result = manager
            .preserve_mirror("acme/web", f.remote.to_str().unwrap(), &[], &f.env, 10)
            .await;
        assert!(
            matches!(result, Err(Fail::Msg(m)) if m.contains("repository copy needs attention: mirror has registered worktrees"))
        );
        assert_eq!(
            std::fs::read_to_string(task.join("draft")).unwrap(),
            "unpublished worktree work"
        );
        assert_eq!(
            std::fs::read_to_string(mirror.join("config")).unwrap(),
            "[broken\n"
        );
    }

    #[tokio::test]
    async fn mirror_rebuild_refuses_a_repository_using_its_objects() {
        let f = RepoFixture::new().await;
        let manager = f.manager();
        let mirror = manager.mirror_path("acme/web").ok().unwrap();
        std::fs::create_dir_all(&manager.mirrors).unwrap();
        sh(
            &f.root,
            &[
                "clone",
                "--bare",
                "-q",
                f.remote.to_str().unwrap(),
                mirror.to_str().unwrap(),
            ],
        );
        let dependent = manager.root.join("dependent");
        std::fs::create_dir_all(&manager.root).unwrap();
        sh(
            &f.root,
            &[
                "clone",
                "--shared",
                "-q",
                mirror.to_str().unwrap(),
                dependent.to_str().unwrap(),
            ],
        );
        // Exercise Git's relative alternates resolution as well as its clone-written absolute form.
        for alternate in [
            mirror.join("objects").display().to_string(),
            "../../../../../state/mirrors/acme__web.git/objects".into(),
        ] {
            std::fs::write(dependent.join(".git/objects/info/alternates"), alternate).unwrap();
            let result = manager
                .preserve_mirror("acme/web", f.remote.to_str().unwrap(), &[], &f.env, 10)
                .await;
            assert!(
                matches!(result, Err(Fail::Msg(m)) if m.contains("uses this mirror's objects"))
            );
            assert!(mirror.exists());
        }
    }

    #[tokio::test]
    async fn mirror_rebuild_refuses_unpublished_refs_and_different_remote_shas() {
        let f = RepoFixture::new().await;
        let manager = f.manager();
        let mirror = manager.mirror_path("acme/web").ok().unwrap();
        std::fs::create_dir_all(&manager.mirrors).unwrap();
        sh(
            &f.root,
            &[
                "clone",
                "--bare",
                "-q",
                f.remote.to_str().unwrap(),
                mirror.to_str().unwrap(),
            ],
        );
        let original = sh(&mirror, &["rev-parse", "main"]);
        sh(&mirror, &["update-ref", "refs/heads/bot-work", &original]);
        std::fs::write(mirror.join("config"), "[broken\n").unwrap();
        let result = manager
            .preserve_mirror("acme/web", f.remote.to_str().unwrap(), &[], &f.env, 10)
            .await;
        let reason = match &result {
            Err(Fail::Msg(m)) => m.as_str(),
            _ => "unexpected mirror preservation result",
        };
        assert!(
            matches!(&result, Err(Fail::Msg(m)) if m.contains("local ref refs/heads/bot-work is unpublished")),
            "{reason}"
        );
        std::fs::remove_file(mirror.join("refs/heads/bot-work")).unwrap();
        sh(
            &f.seed,
            &["commit", "--allow-empty", "-qm", "new remote head"],
        );
        sh(&f.seed, &["push", "-q"]);
        let result = manager
            .preserve_mirror("acme/web", f.remote.to_str().unwrap(), &[], &f.env, 10)
            .await;
        let reason = match &result {
            Err(Fail::Msg(m)) => m.as_str(),
            _ => "unexpected mirror preservation result",
        };
        assert!(
            matches!(&result, Err(Fail::Msg(m)) if m.contains("local ref refs/heads/main is unpublished or differs")),
            "{reason}"
        );
        assert_eq!(
            mirror_refs(&mirror).ok().unwrap()["refs/heads/main"],
            original
        );
        assert_eq!(
            std::fs::read_to_string(mirror.join("config")).unwrap(),
            "[broken\n"
        );
    }

    #[tokio::test]
    async fn legacy_instruction_backups_gain_a_baseline_without_losing_open_writers() {
        use std::io::Write;
        let f = RepoFixture::new().await;
        let saved = f.base.join(".git/tico-instruction-backups");
        std::fs::create_dir_all(&saved).unwrap();
        let legacy = saved.join("AGENTS.md.tico-backup-1");
        std::fs::write(&legacy, "Repository rules\n").unwrap();
        let mut writer = std::fs::OpenOptions::new()
            .write(true)
            .open(&legacy)
            .unwrap();
        assert!(repair_instruction_links(&f.base, &f.env).await.is_none());
        let baseline = std::fs::read_dir(&saved)
            .unwrap()
            .next()
            .unwrap()
            .unwrap()
            .path();
        assert!(
            !legacy.exists()
                && baseline
                    .file_name()
                    .unwrap()
                    .to_str()
                    .unwrap()
                    .contains("-sha256-")
        );
        std::fs::remove_file(f.base.join("CLAUDE.md")).unwrap();
        assert!(repair_instruction_links(&f.base, &f.env).await.is_none());
        writer.write_all(b"late edit").unwrap();
        drop(writer);
        assert!(repair_instruction_links(&f.base, &f.env)
            .await
            .unwrap()
            .contains(baseline.file_name().unwrap().to_str().unwrap()));
    }

    #[tokio::test]
    async fn accepted_instruction_backups_survive_changed_or_removed_targets_but_late_edits_are_reported(
    ) {
        use std::io::{Seek, SeekFrom, Write};
        let f = RepoFixture::new().await;
        let path = f.base.join("AGENTS.md");
        std::fs::remove_file(&path).unwrap();
        std::fs::write(&path, "Repository rules\n").unwrap();
        // An open descriptor retains the inode on Linux as macOS always does.
        let mut writer = std::fs::OpenOptions::new().write(true).open(&path).unwrap();
        assert!(repair_instruction_links(&f.base, &f.env).await.is_none());
        let saved = f.base.join(".git/tico-instruction-backups");
        let backup = std::fs::read_dir(&saved)
            .unwrap()
            .next()
            .unwrap()
            .unwrap()
            .path();
        assert!(backup
            .file_name()
            .unwrap()
            .to_str()
            .unwrap()
            .ends_with(&format!(
                "-sha256-{}",
                gitrun::sha256_hex(b"Repository rules\n")
            )));
        std::fs::write(f.base.join("CLAUDE.md"), "Updated release rules\n").unwrap();
        sh(&f.base, &["add", "CLAUDE.md"]);
        sh(&f.base, &["commit", "-qm", "updated release"]);
        assert!(repair_instruction_links(&f.base, &f.env).await.is_none());
        sh(&f.base, &["rm", "-q", "AGENTS.md", "CLAUDE.md"]);
        sh(&f.base, &["commit", "-qm", "removed instructions"]);
        assert!(repair_instruction_links(&f.base, &f.env).await.is_none());
        assert!(backup.exists());
        writer.seek(SeekFrom::Start(0)).unwrap();
        writer.write_all(b"Late bot edit\n").unwrap();
        writer.set_len(b"Late bot edit\n".len() as u64).unwrap();
        drop(writer);
        let note = repair_instruction_links(&f.base, &f.env).await.unwrap();
        assert!(note.contains(backup.file_name().unwrap().to_str().unwrap()));
        assert_eq!(std::fs::read_to_string(&backup).unwrap(), "Late bot edit\n");
    }

    #[tokio::test]
    async fn differing_instruction_backups_are_kept_and_late_open_writers_are_reported() {
        use std::io::{Seek, SeekFrom, Write};
        let f = RepoFixture::new().await;
        let path = f.base.join("AGENTS.md");
        std::fs::remove_file(&path).unwrap();
        std::fs::write(&path, "CLAUDE.md").unwrap();
        let note = repair_instruction_links(&f.base, &f.env).await.unwrap();
        assert!(
            note.contains("bot instruction edit to review")
                && note.contains("AGENTS.md.tico-backup-")
        );
        let saved = f.base.join(".git/tico-instruction-backups");
        let different = std::fs::read_dir(&saved)
            .unwrap()
            .flatten()
            .next()
            .unwrap()
            .path();
        assert_eq!(std::fs::read_to_string(&different).unwrap(), "CLAUDE.md");
        std::fs::remove_file(&different).unwrap();
        std::fs::remove_file(&path).unwrap();
        std::fs::write(&path, "Repository rules\n").unwrap();
        let mut writer = std::fs::OpenOptions::new().write(true).open(&path).unwrap();
        assert!(repair_instruction_links(&f.base, &f.env).await.is_none());
        let backup = std::fs::read_dir(&saved)
            .unwrap()
            .flatten()
            .next()
            .unwrap()
            .path();
        writer.seek(SeekFrom::Start(0)).unwrap();
        writer.write_all(b"late bot edit\n").unwrap();
        writer.set_len(b"late bot edit\n".len() as u64).unwrap();
        drop(writer);
        assert_eq!(std::fs::read_to_string(&backup).unwrap(), "late bot edit\n");
        assert_eq!(
            std::fs::read_to_string(&path).unwrap(),
            "Repository rules\n"
        );
        let note = repair_instruction_links(&f.base, &f.env).await.unwrap();
        assert!(note.contains(backup.file_name().unwrap().to_str().unwrap()));
        assert!(backup.exists());
    }
}
