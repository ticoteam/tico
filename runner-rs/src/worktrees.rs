//! Computer-owned task worktrees (runner/worktrees.py): the heartbeat's report of each worktree the server
//! tracks here, an attached checkout's local proof (`tasks/.checkout-state/<sha256(link)>.json`, the Python
//! marker format), and the cleanup and restore actions a heartbeat reply asks for. Never removes a tree before
//! preserving its history. Adding and setting up a worktree inside a turn stay in the hub CLI (`hub worktree
//! add`), which takes the same task lock (`tasks/.locks/<sha[:2]>`) and writes the same markers; a worktree
//! the server reserved for a waiting task (backend/worktree_waits.py) is checked out and set up here, while
//! its bot is idle (`prepare`).

use std::collections::{BTreeSet, HashMap, HashSet};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use regex::Regex;
use serde_json::{json, Map, Value};

use crate::client::{ApiError, Client};
use crate::env::Env;
use crate::gitrun::{self, Fault, Out};
use crate::repositories::{self, GB};
use crate::util::text_of;

/// A failure: a message for a person (with git's own words for network commands), a cleanup to retry
/// later, or an operating-system error named by kind.
#[derive(Debug, Clone)]
pub enum WtErr {
    Msg(String, Option<String>),
    Deferred(String),
    Os(String),
}

impl WtErr {
    fn msg(text: impl Into<String>) -> WtErr {
        WtErr::Msg(text.into(), None)
    }

    pub fn text(&self) -> String {
        match self {
            WtErr::Msg(m, _) | WtErr::Deferred(m) | WtErr::Os(m) => m.clone(),
        }
    }
}

impl From<String> for WtErr {
    fn from(m: String) -> WtErr {
        WtErr::Msg(m, None)
    }
}

impl From<std::io::Error> for WtErr {
    fn from(e: std::io::Error) -> WtErr {
        WtErr::Os(format!("OSError ({e})"))
    }
}

type R<T> = Result<T, WtErr>;

const SHA: &str = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$";

fn is_sha(text: &str) -> bool {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(SHA).unwrap()).is_match(text)
}

fn is_symlink(p: &Path) -> bool {
    p.symlink_metadata().is_ok_and(|m| m.file_type().is_symlink())
}

/// Lexically normal absolute path with symlinks resolved for the part that exists (Python `Path.resolve()`).
fn resolve(path: &Path) -> PathBuf {
    if let Ok(p) = path.canonicalize() {
        return p;
    }
    let mut tail = vec![];
    let mut head = path.to_path_buf();
    while !head.exists() {
        match (head.file_name().map(|n| n.to_os_string()), head.parent()) {
            (Some(name), Some(parent)) => {
                tail.push(name);
                head = parent.to_path_buf();
            }
            _ => return path.to_path_buf(),
        }
    }
    let mut out = head.canonicalize().unwrap_or(head);
    for name in tail.into_iter().rev() {
        out.push(name);
    }
    out
}

/// A worktree's place in the team workspace: relative, no `..`, no symlinks, never in or under `repos/`
/// (compared case-insensitively on a case-insensitive volume).
pub fn safe_path(workspace: &Path, relative: &str) -> Result<PathBuf, String> {
    let root = resolve(workspace);
    if relative.len() > 1000 || relative.chars().any(|c| (c as u32) < 32) {
        return Err("Invalid worktree path".into());
    }
    let raw = Path::new(relative);
    let path = root.join(raw);
    let parts: Vec<_> = raw.components().collect();
    let dotdot = parts.iter().any(|c| matches!(c, std::path::Component::ParentDir));
    if raw.is_absolute() || dotdot || relative.is_empty() || parts.is_empty() || is_symlink(&path) {
        return Err("Use a worktree inside the team workspace".into());
    }
    let resolved = resolve(&path);
    let normal: PathBuf = path.components().filter(|c| !matches!(c, std::path::Component::CurDir)).collect();
    if resolved != normal {
        return Err("Worktree path must not contain symlinks".into());
    }
    let swapped: String = root.display().to_string().chars().map(|c| if c.is_lowercase() { c.to_ascii_uppercase() } else { c.to_ascii_lowercase() }).collect();
    let insensitive = same_file(&root, Path::new(&swapped));
    let mut repo_path = root.join("repos").display().to_string();
    let mut candidate = resolved.display().to_string();
    if insensitive {
        repo_path = repo_path.to_lowercase();
        candidate = candidate.to_lowercase();
    }
    if candidate == repo_path || candidate.starts_with(&format!("{repo_path}/")) {
        return Err("Worktree must stay outside repos".into());
    }
    if resolved == root || !resolved.starts_with(&root) || resolved.starts_with(root.join("repos")) {
        return Err("Worktree must stay inside the team workspace, outside repos".into());
    }
    Ok(resolved)
}

fn same_file(a: &Path, b: &Path) -> bool {
    use std::os::unix::fs::MetadataExt;
    match (std::fs::metadata(a), std::fs::metadata(b)) {
        (Ok(x), Ok(y)) => x.dev() == y.dev() && x.ino() == y.ino(),
        _ => false,
    }
}

pub fn disk_floor(workspace: &Path) -> R<()> {
    let free = crate::readiness::disk_usage(workspace).map(|d| d.1).unwrap_or(u64::MAX);
    let floor = 5.0 * GB;
    if (free as f64) < floor {
        return Err(WtErr::msg(format!("Not enough disk to create worktree: {:.1} GB free, needs 5 GB. Free space on this volume", free as f64 / GB)));
    }
    Ok(())
}

/// Runner git in a worktree or base clone (worktrees.git): the restricted environment, at most 120 s.
pub async fn git(path: &Path, env: &Env, args: &[&str], check: bool) -> R<Out> {
    if args.len() >= 2 && args[0] == "worktree" && args[1] == "prune" && is_symlink(&path.join(".git").join("worktrees")) {
        return Err(WtErr::msg("Worktree registrations point outside the base clone; left as they are"));
    }
    let done = gitrun::git_as_bot(path, &crate::safegit::environment(env), args, 120).await.map_err(|f| match f {
        Fault::Timeout(_) => WtErr::Os("TimeoutExpired".into()),
        Fault::Stopped => WtErr::msg(gitrun::STOPPED),
        Fault::Io(e) => WtErr::Os(format!("OSError ({e})")),
    })?;
    if check && !done.ok() {
        let detail = ["push", "fetch", "ls-remote"].contains(&args[0]).then(|| if done.stderr.is_empty() { format!("exit {}", done.code) } else { done.stderr.clone() });
        return Err(WtErr::Msg(format!("Git {} failed (exit {}); check access, network, disk space and repository state", args[0], done.code), detail));
    }
    Ok(done)
}

/// The task lock the hub CLI takes too: one worktree step at a time per task path on this computer.
pub struct TaskLock(#[allow(dead_code)] std::fs::File);

pub async fn locked(workspace: &Path, task: &str) -> R<TaskLock> {
    let tasks = workspace.join("tasks");
    let folder = tasks.join(".locks");
    if is_symlink(&tasks) || is_symlink(&folder) {
        return Err(WtErr::msg("Worktree locks point through a symlink; left as they are"));
    }
    crate::isolation::mkdir(&folder, 0o755)?;
    crate::isolation::chown(&tasks);
    // Locks an older runner made as the supervisor (0.3.46) stay unopenable to the bot's `hub worktree` otherwise.
    adopt_task_state(workspace, ".locks");
    let name = &gitrun::sha256_hex(task.as_bytes())[..2];
    let file = std::fs::OpenOptions::new().create(true).append(true).custom_flags(libc::O_NOFOLLOW).open(folder.join(name))?;
    crate::isolation::chown_file(&file)?;
    let file = tokio::task::spawn_blocking(move || {
        use std::os::fd::AsRawFd;
        // SAFETY: flock on a file this process holds open.
        let rc = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX) };
        if rc == 0 { Ok(file) } else { Err(std::io::Error::last_os_error()) }
    })
    .await
    .map_err(|e| WtErr::Os(e.to_string()))??;
    Ok(TaskLock(file))
}

fn detail(row: &Value) -> Map<String, Value> {
    match &row["detail_json"] {
        Value::String(s) if !s.is_empty() => serde_json::from_str::<Value>(s).ok().and_then(|v| v.as_object().cloned()).unwrap_or_default(),
        Value::Object(m) => m.clone(),
        _ => Map::new(),
    }
}

/// A formerly-present link an old heartbeat demoted, recognized for verification (_demoted_legacy_history).
fn legacy_demoted(row: &Value, d: &Map<String, Value>) -> bool {
    let last = d.get("last_commit").and_then(|v| v.as_str()).unwrap_or("");
    row["state"] == "pending"
        && d.get("checkout_state").is_none_or(|v| v.is_null() || v == "unverified")
        && !crate::util::truthy(d.get("setup_pending"))
        && ["expected_head", "checkout_target", "expected_base"].iter().all(|k| !crate::util::truthy(d.get(*k)))
        && !text_of(&row["branch"]).is_empty()
        && d.get("current_branch") == Some(&row["branch"])
        && is_sha(last)
        && d.get("dirty_files").and_then(|v| v.as_i64()).is_some_and(|n| n >= 0)
}

async fn common_dir(path: &Path, env: &Env) -> R<PathBuf> {
    let out = git(path, env, &["rev-parse", "--path-format=absolute", "--git-common-dir"], true).await?;
    Ok(resolve(Path::new(out.stdout.trim())))
}

/// The service needs only a stable comparison token for the base clone, never the runner's path.
fn base_identity(common: &Path) -> String {
    gitrun::sha256_hex(resolve(common).display().to_string().as_bytes())
}

fn marker_path(workspace: &Path, link_id: &str, create: bool) -> R<Option<PathBuf>> {
    if link_id.is_empty() || link_id.len() > 200 {
        return Err(WtErr::msg("Worktree initialization has an invalid link identity; left as it is"));
    }
    let tasks = resolve(workspace).join("tasks");
    let folder = tasks.join(".checkout-state");
    if is_symlink(&tasks) || is_symlink(&folder) {
        return Err(WtErr::msg("Worktree initialization state points through a symlink; left as it is"));
    }
    if create {
        crate::isolation::mkdir(&folder, 0o700)?;
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&folder, std::fs::Permissions::from_mode(0o700));
        // The bot's `hub worktree` replaces proofs here too, including ones an older runner left as the supervisor.
        adopt_task_state(workspace, ".checkout-state");
    }
    if !folder.exists() {
        return Ok(None);
    }
    let marker = folder.join(format!("{}.json", gitrun::sha256_hex(link_id.as_bytes())));
    if is_symlink(&marker) {
        return Err(WtErr::msg("Worktree initialization marker is a symlink; left as it is"));
    }
    Ok(Some(marker))
}

/// Hand the bot user what the supervisor left in `tasks/<name>` (`.checkout-state` proofs, `.locks`): the folder is
/// adopted by descriptor, so inodes, modes and bytes stay (a held lock stays held), and a symlink, special file or
/// foreign hard link in it changes nothing.
fn adopt_task_state(workspace: &Path, name: &str) {
    let folder = resolve(workspace).join("tasks").join(name);
    if folder.symlink_metadata().is_ok_and(|m| m.is_dir()) {
        crate::isolation::adopt(&[folder]);
    }
}

/// Every heartbeat: the bot's own `hub worktree add/setup` opens these locks and reads these proofs between passes,
/// whatever the server lists.
pub fn adopt_task_states(workspace: &Path) {
    adopt_task_state(workspace, ".locks");
    adopt_task_state(workspace, ".checkout-state");
}

pub fn read_marker(workspace: &Path, link_id: &str) -> R<Option<Map<String, Value>>> {
    let Some(marker) = marker_path(workspace, link_id, false)? else { return Ok(None) };
    if !marker.exists() {
        return Ok(None);
    }
    if !marker.is_file() {
        return Err(WtErr::msg("Worktree initialization marker is not a regular file; left as it is"));
    }
    let value: Value = std::fs::read_to_string(&marker)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .ok_or_else(|| WtErr::msg("Worktree initialization marker cannot be read; left as it is"))?;
    match value {
        Value::Object(m) if m.get("schema") == Some(&json!(1)) => Ok(Some(m)),
        _ => Err(WtErr::msg("Worktree initialization marker is invalid; left as it is")),
    }
}

/// Temp file (O_EXCL, O_NOFOLLOW, 0600), fsync, then replace: a crash leaves the old marker or the new one.
pub fn write_marker(workspace: &Path, value: &Map<String, Value>) -> R<()> {
    let link = text_of(&value["link_id"]);
    let marker = marker_path(workspace, &link, true)?.ok_or_else(|| WtErr::msg("Worktree initialization marker directory is unavailable"))?;
    let name = marker.file_name().unwrap().to_string_lossy().to_string();
    let temp = marker.with_file_name(format!("{name}.{}.tmp", uuid::Uuid::new_v4().simple()));
    let written = (|| -> std::io::Result<()> {
        use std::io::Write;
        let mut f = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).custom_flags(libc::O_NOFOLLOW).open(&temp)?;
        crate::isolation::chown_file(&f)?;
        f.write_all(gitrun::compact_sorted(&Value::Object(value.clone())).as_bytes())?;
        f.flush()?;
        f.sync_all()?;
        std::fs::rename(&temp, &marker)
    })();
    if written.is_err() {
        let _ = std::fs::remove_file(&temp);
        return Err(WtErr::msg("Worktree initialization marker could not be committed atomically"));
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn marker_identity(link_id: &str, task: &str, path: &str, repo: &str, branch: &str, head: &str, target: &str, base: &str) -> Map<String, Value> {
    let mut m = Map::new();
    for (k, v) in [
        ("schema", json!(1)),
        ("link_id", json!(link_id)),
        ("task_id", json!(task)),
        ("path", json!(path)),
        ("repo", json!(repo)),
        ("branch", json!(branch)),
        ("expected_head", json!(head)),
        ("checkout_target", json!(target)),
        ("expected_base", json!(base)),
    ] {
        m.insert(k.into(), v);
    }
    m
}

fn check_marker(marker: Option<&Map<String, Value>>, expected: &Map<String, Value>) -> R<()> {
    let Some(marker) = marker else { return Err(WtErr::msg("Worktree has no local initialization proof; kept unchanged for inspection")) };
    if expected.iter().any(|(k, v)| marker.get(k).unwrap_or(&Value::Null) != v) {
        return Err(WtErr::msg("Worktree initialization marker does not match this task registration; kept unchanged"));
    }
    Ok(())
}

pub async fn checked_branch(branch: &str) -> R<String> {
    if branch.is_empty() || branch.len() > 200 || branch.starts_with('-') {
        return Err(WtErr::msg("Invalid or oversized worktree branch"));
    }
    let env = crate::safegit::clean_environment(&crate::env::process_environment());
    match gitrun::bare(&env, &["check-ref-format", "--branch", branch], 15).await {
        Ok(o) if o.ok() => Ok(branch.into()),
        _ => Err(WtErr::msg("Invalid worktree branch")),
    }
}

fn repo_matches(origin: &str, repo: &str, machine_git: bool) -> bool {
    if machine_git { repositories::matches(origin, repo) } else { repositories::same_repository(origin, repo) }
}

#[allow(clippy::too_many_arguments)]
async fn verify_worktree(path: &Path, workspace: &Path, repo: &str, machine_git: bool, branch: &str, env: &Env, expected_base: Option<&str>, detached_ok: bool) -> R<PathBuf> {
    if !path.join(".git").is_file() {
        return Err(WtErr::msg("Tracked path is not a Git worktree; left as it is"));
    }
    let origin = git(path, env, &["config", "--get", "remote.origin.url"], true).await?.stdout.trim().to_string();
    if !repo_matches(&origin, repo, machine_git) {
        return Err(WtErr::msg("Worktree repository does not match its task link; left as it is"));
    }
    if detached_ok {
        let head = git(path, env, &["symbolic-ref", "--short", "-q", "HEAD"], false).await?.stdout.trim().to_string();
        if !head.is_empty() && head != branch {
            return Err(WtErr::msg("Worktree branch does not match its task link; left as it is"));
        }
    } else if git(path, env, &["symbolic-ref", "--short", "HEAD"], true).await?.stdout.trim() != branch {
        return Err(WtErr::msg("Worktree branch does not match its task link; left as it is"));
    }
    let common = common_dir(path, env).await?;
    if !common.starts_with(resolve(workspace)) || common == resolve(workspace) {
        return Err(WtErr::msg("Worktree base must be inside the team workspace"));
    }
    if expected_base.is_some_and(|b| !b.is_empty() && base_identity(&common) != b) {
        return Err(WtErr::msg("Worktree is attached to a different managed base clone; left as it is"));
    }
    Ok(common.parent().map(Path::to_path_buf).unwrap_or(common))
}

async fn worktree_registered(base: &Path, path: &Path, env: &Env) -> R<bool> {
    let expected = resolve(path);
    let listed = git(base, env, &["worktree", "list", "--porcelain"], true).await?;
    Ok(listed.stdout.lines().any(|l| l.strip_prefix("worktree ").is_some_and(|p| resolve(Path::new(p)) == expected)))
}

fn field(row: &Value, d: &Map<String, Value>, key: &str) -> String {
    let own = text_of(&row[key]);
    if own.is_empty() { d.get(key).map(text_of).unwrap_or_default() } else { own }
}

async fn verify_ready_registration(workspace: &Path, row: &Value, d: &Map<String, Value>, repo: &str) -> R<()> {
    let link_id = Some(text_of(&row["id"])).filter(|s| !s.is_empty()).unwrap_or_else(|| text_of(&row["link_id"]));
    let head = field(row, d, "expected_head");
    let target = field(row, d, "checkout_target");
    let base = field(row, d, "expected_base");
    let marker = read_marker(workspace, &link_id)?;
    let task = Some(text_of(&row["task_id"])).filter(|t| !t.is_empty()).or_else(|| marker.as_ref().map(|m| own(m, "task_id"))).unwrap_or_default();
    let (path, branch) = (text_of(&row["path"]), text_of(&row["branch"]));
    if [&link_id, &task, &path, &branch, &head, &target, &base].iter().any(|s| s.is_empty()) {
        return Err(WtErr::msg("Ready worktree has no complete registration proof; kept unverified"));
    }
    let identity = marker_identity(&link_id, &task, &path, repo, &branch, &head, &target, &base);
    check_marker(marker.as_ref(), &identity)?;
    if marker.unwrap().get("phase") != Some(&json!("ready")) {
        return Err(WtErr::msg("Ready worktree has no completed local setup record; kept unverified"));
    }
    Ok(())
}

/// Certify a checkout a person or the CLI attached (worktrees._record_attached_checkout): its branch at its
/// current commit, registered in its base, nothing deleted. Returns (expected_head, checkout_target, base).
async fn record_attached(workspace: &Path, row: &Value, branch: &str, repo: &str, machine_git: bool, env: &Env) -> R<(String, String, String)> {
    let rel = text_of(&row["path"]);
    let path = safe_path(workspace, &rel)?;
    let branch = checked_branch(branch).await?;
    let common = common_dir(&path, env).await?;
    let expected_base = base_identity(&common);
    verify_worktree(&path, workspace, repo, machine_git, &branch, env, Some(&expected_base), false).await?;
    let target = format!("refs/heads/{branch}");
    let link_id = text_of(&row["id"]);
    let task_id = text_of(&row["task_id"]);
    if link_id.is_empty() || task_id.is_empty() {
        return Err(WtErr::msg("Attached worktree has no task registration identity; kept unchanged"));
    }
    if crate::util::truthy(row.get("setup_pending")) || crate::util::truthy(detail(row).get("setup_pending")) {
        return Err(WtErr::msg("Attached worktree setup is still pending; kept unchanged"));
    }
    if repo.is_empty() {
        return Err(WtErr::msg("Attached worktree repository is not identified; kept unchanged"));
    }
    if !worktree_registered(common.parent().unwrap_or(&common), &path, env).await? {
        return Err(WtErr::msg("Attached path is not registered in its managed base; kept unchanged"));
    }
    if let Some(marker) = read_marker(workspace, &link_id)? {
        // A prior local proof may outlive a lost heartbeat: replay it; a later commit must not rewrite it.
        let mut stable = Map::new();
        for (k, v) in [("schema", json!(1)), ("link_id", json!(link_id)), ("task_id", json!(task_id)), ("path", json!(rel)), ("repo", json!(repo)), ("branch", json!(branch)), ("expected_base", json!(expected_base))] {
            stable.insert(k.into(), v);
        }
        check_marker(Some(&marker), &stable)?;
        let expected = own(&marker, "expected_head");
        let recorded = own(&marker, "checkout_target");
        let mut valid = recorded == target;
        if !valid {
            if let Some(remote) = recorded.strip_prefix("refs/remotes/origin/") {
                valid = checked_branch(remote).await.is_ok_and(|b| b == remote);
            }
        }
        if marker.get("phase") != Some(&json!("ready")) || !is_sha(&expected) || !valid {
            return Err(WtErr::msg("Attached worktree has no matching completed local proof; kept unchanged"));
        }
        return Ok((expected, recorded, expected_base));
    }
    // An attachment cannot tell an unfinished checkout from a deletion: refuse to certify, change nothing.
    if !git(&path, env, &["ls-files", "--deleted", "-z"], true).await?.stdout.is_empty() {
        return Err(WtErr::msg("Attached worktree has missing tracked files; kept unchanged and unverified"));
    }
    let expected = git(&path, env, &["rev-parse", "HEAD"], true).await?.stdout.trim().to_string();
    if git(&path, env, &["rev-parse", "--verify", &target], true).await?.stdout.trim() != expected {
        return Err(WtErr::msg("Attached worktree branch does not point at its current commit; kept unchanged"));
    }
    let mut identity = marker_identity(&link_id, &task_id, &rel, repo, &branch, &expected, &target, &expected_base);
    identity.insert("phase".into(), json!("ready"));
    write_marker(workspace, &identity)?;
    Ok((expected, target, expected_base))
}

async fn record_restored(workspace: &Path, row: &Value, repo: &mut Map<String, Value>, env: &Env) -> R<(String, String, String)> {
    let rel = text_of(&row["path"]);
    let path = safe_path(workspace, &rel)?;
    let (base, _) = repositories::worktree_base(workspace, repo, env).await?;
    let expected_base = base_identity(&common_dir(&base, env).await?);
    let branch = checked_branch(&text_of(&row["branch"])).await?;
    let name = text_of(&row["repo"]);
    verify_worktree(&path, workspace, &name, crate::util::truthy(repo.get("machine_git")), &branch, env, Some(&expected_base), false).await?;
    let expected = git(&path, env, &["rev-parse", "HEAD"], true).await?.stdout.trim().to_string();
    let target = format!("refs/heads/{branch}");
    if !git(&base, env, &["rev-parse", "--verify", &target], false).await?.ok() {
        return Err(WtErr::msg("Restored task branch is missing from its managed base; kept worktree"));
    }
    let mut identity = marker_identity(&text_of(&row["id"]), &text_of(&row["task_id"]), &rel, &name, &branch, &expected, &target, &expected_base);
    identity.insert("phase".into(), json!("restored"));
    write_marker(workspace, &identity)?;
    Ok((expected, target, expected_base))
}

/// What a reserved worktree is checked out and set up for (worktrees.prepare): the repository, whether it goes
/// through the computer's own git, and the setup command the repository names.
struct Checkout<'a> {
    workspace: &'a Path,
    repo: &'a str,
    machine_git: bool,
    branch: &'a str,
    env: &'a Env,
}

/// `git status` records of a checkout, untracked files included (worktrees._porcelain_records).
async fn porcelain_records(path: &Path, env: &Env) -> R<Vec<String>> {
    let done = git(path, env, &["status", "--porcelain=v1", "-z", "--untracked-files=all"], false).await?;
    if !done.ok() {
        return Err(WtErr::msg("Could not verify checkout contents; left as they are"));
    }
    Ok(done.stdout.split('\0').filter(|r| !r.is_empty()).map(String::from).collect())
}

/// The task id a marker already records for this task, so a short id never rewrites it (_canonical_task_id).
fn canonical_task(task: &str, marker: Option<&Map<String, Value>>) -> String {
    match marker.map(|m| own(m, "task_id")) {
        Some(recorded) if recorded == task || (task.len() >= 8 && recorded.starts_with(task)) => recorded,
        _ => task.to_string(),
    }
}

/// Accept only a complete clean materialization; never restore or overwrite its contents
/// (_resume_interrupted_checkout).
async fn resume_interrupted(path: &Path, c: &Checkout<'_>, expected_head: &str, expected_base: &str, identity: &Map<String, Value>) -> R<()> {
    if !is_sha(expected_head) {
        return Err(WtErr::msg("Interrupted checkout has no valid expected commit; left as it is"));
    }
    verify_worktree(path, c.workspace, c.repo, c.machine_git, c.branch, c.env, Some(expected_base), false).await?;
    if git(path, c.env, &["rev-parse", "HEAD"], true).await?.stdout.trim() != expected_head {
        return Err(WtErr::msg("Interrupted checkout moved from its recorded commit; left as it is"));
    }
    let marker = read_marker(c.workspace, &own(identity, "link_id"))?;
    check_marker(marker.as_ref(), identity)?;
    let mut marker = marker.unwrap();
    let phase = own(&marker, "phase");
    if phase != "materializing" && phase != "checkout_complete" {
        return Err(WtErr::msg("Worktree is not in a resumable checkout phase; kept unchanged"));
    }
    if !porcelain_records(path, c.env).await?.is_empty() {
        return Err(WtErr::msg("Checkout contents changed or are incomplete; kept worktree without restoring files"));
    }
    if phase == "materializing" {
        marker.insert("phase".into(), json!("checkout_complete"));
        write_marker(c.workspace, &marker)?;
    }
    Ok(())
}

/// The task branch when the base has it, else the default branch's remote head, and its commit.
async fn checkout_target(base: &Path, branch: &str, default: &str, env: &Env) -> R<(String, String)> {
    let local = format!("refs/heads/{branch}");
    let target = if git(base, env, &["show-ref", "--verify", &local], false).await?.ok() { local } else { format!("refs/remotes/origin/{default}") };
    let head = git(base, env, &["rev-parse", "--verify", &target], true).await?.stdout.trim().to_string();
    Ok((target, head))
}

/// The link's checkout record on the server: pending with setup pending until it is present and ready.
async fn patch_checkout(client: &Client, task: &str, link: &str, checkout_state: &str, proof: (&str, &str, &str)) -> R<()> {
    let present = checkout_state == "ready";
    let body = json!({"state": if present { "present" } else { "pending" }, "setup_pending": !present, "checkout_state": checkout_state,
                      "expected_head": proof.0, "checkout_target": proof.1, "expected_base": proof.2});
    client.patch(&format!("tasks/{task}/links/{link}"), &body).await.map_err(|e| WtErr::msg(e.detail))?;
    Ok(())
}

/// Create a queued checkout, or resume an interrupted one, with its local proof written before and after
/// `git worktree add` (_create_or_resume_checkout). Returns (expected_head, checkout_target, expected_base).
async fn create_or_resume(path: &Path, c: &Checkout<'_>, base: &Path, default: &str, link: &Map<String, Value>, task: &str, client: &Client) -> R<(String, String, String)> {
    let mut state = Some(own(link, "checkout_state")).filter(|s| !s.is_empty()).unwrap_or_else(|| "queued".into());
    let fresh_queued = state == "queued";
    let (mut expected, mut target, mut expected_base) = (own(link, "expected_head"), own(link, "checkout_target"), own(link, "expected_base"));
    let branch = c.branch;
    let actual_base = base_identity(&common_dir(base, c.env).await?);
    let link_id = own(link, "link_id");
    if state == "initializing" {
        if expected.is_empty() || target.is_empty() || expected_base.is_empty() {
            return Err(WtErr::msg("Initialization is missing its registered commit or base; kept worktree unchanged"));
        }
        if expected_base != actual_base {
            return Err(WtErr::msg("Managed base clone changed since initialization; kept worktree unchanged"));
        }
        if target != format!("refs/heads/{branch}") && target != format!("refs/remotes/origin/{default}") {
            return Err(WtErr::msg("Interrupted checkout target does not match its task; left as it is"));
        }
        let head = git(base, c.env, &["rev-parse", "--verify", &target], false).await?;
        if !head.ok() || head.stdout.trim() != expected {
            return Err(WtErr::msg("Task branch changed since checkout initialization; left as it is"));
        }
    } else if state == "queued" {
        (target, expected) = checkout_target(base, branch, default, c.env).await?;
        expected_base = actual_base;
        patch_checkout(client, task, &link_id, "initializing", (&expected, &target, &expected_base)).await?;
        state = "initializing".into();
    } else {
        return Err(WtErr::msg("Checkout is not in a resumable initialization state; leave it unchanged"));
    }
    let marker = read_marker(c.workspace, &link_id)?;
    let identity = marker_identity(&link_id, &canonical_task(task, marker.as_ref()), &own(link, "path"), c.repo, branch, &expected, &target, &expected_base);
    let with_phase = |m: &Map<String, Value>, phase: &str| {
        let mut m = m.clone();
        m.insert("phase".into(), json!(phase));
        m
    };
    if path.exists() {
        resume_interrupted(path, c, &expected, &expected_base, &identity).await?;
    } else {
        match marker {
            Some(marker) => {
                let registered = worktree_registered(base, path, c.env).await?;
                if fresh_queued && !registered {
                    // A new add can reuse a removed link only when its path is absent and Git no longer
                    // registers it, with the same task, repository, branch and base.
                    let stable: Map<String, Value> = identity.iter().filter(|(k, _)| ["link_id", "task_id", "path", "repo", "branch", "expected_base"].contains(&k.as_str())).map(|(k, v)| (k.clone(), v.clone())).collect();
                    check_marker(Some(&marker), &stable)?;
                    write_marker(c.workspace, &with_phase(&identity, "planned"))?;
                } else {
                    check_marker(Some(&marker), &identity)?;
                    match own(&marker, "phase").as_str() {
                        "materializing" if registered => return Err(WtErr::msg("Git still registers the missing interrupted worktree; preserved for bounded recovery")),
                        "materializing" => write_marker(c.workspace, &with_phase(&marker, "planned"))?,
                        "planned" if registered => return Err(WtErr::msg("Git registers a worktree without a materialized path; preserved for bounded recovery")),
                        "planned" => {}
                        _ => return Err(WtErr::msg("A previously materialized worktree is missing; preserved for bounded recovery")),
                    }
                }
            }
            None => {
                if state != "initializing" || worktree_registered(base, path, c.env).await? {
                    return Err(WtErr::msg("Worktree is missing without safe initialization proof; preserved for recovery"));
                }
                write_marker(c.workspace, &with_phase(&identity, "planned"))?;
            }
        }
        if let Some(parent) = path.parent() {
            // The bot user works in the worktree, so the folder it lands in is the bot's (sessions.rs does the same).
            crate::isolation::mkdir(parent, 0o755)?;
        }
        write_marker(c.workspace, &with_phase(&identity, "materializing"))?;
        // Given its own group before git writes the bot's files into it (sessions.rs does the same).
        crate::checkout_groups::prepare(path.to_path_buf()).await;
        let p = path.display().to_string();
        let added = if target == format!("refs/heads/{branch}") {
            git(base, c.env, &["worktree", "add", &p, branch], true).await
        } else {
            git(base, c.env, &["worktree", "add", "--no-track", "-b", branch, &p, &target], true).await
        };
        if let Err(e) = added {
            // The prepared folder goes if git left it empty, so the next attempt finds the path absent as before.
            let _ = std::fs::remove_dir(path);
            return Err(e);
        }
        resume_interrupted(path, c, &expected, &expected_base, &identity).await?;
    }
    patch_checkout(client, task, &link_id, "checkout_ready", (&expected, &target, &expected_base)).await?;
    Ok((expected, target, expected_base))
}

/// What a setup command sees (worktrees.setup_environment): no credentials, only the toolchain basics.
fn setup_environment(env: &Env) -> Env {
    const KEYS: &[&str] = &["PATH", "HOME", "SHELL", "LANG", "TZ", "TMPDIR", "TMP", "TEMP", "VIRTUAL_ENV", "CONDA_PREFIX", "PYENV_ROOT", "CARGO_HOME", "RUSTUP_HOME", "GOPATH", "JAVA_HOME"];
    env.iter().filter(|(k, _)| KEYS.contains(&k.as_str()) || k.starts_with("LC_")).map(|(k, v)| (k.clone(), v.clone())).collect()
}

/// Run the repository's setup command in a new worktree (worktrees.setup): as the bot user, its own process
/// group, output discarded, at most ten minutes. It gets no granted credentials, so it takes no leased turn user.
async fn setup(path: &Path, command: &str, env: &Env) -> R<()> {
    if command.is_empty() {
        return Ok(());
    }
    let mut cmd = crate::isolation::command("/bin/sh");
    cmd.arg("-c")
        .arg(command)
        .current_dir(path)
        .env_clear()
        .envs(setup_environment(env))
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .process_group(0)
        .kill_on_drop(true);
    let mut child = cmd.spawn()?;
    let pid = child.id().map(|p| p as i32).unwrap_or(0);
    match tokio::time::timeout(SETUP_TIMEOUT, child.wait()).await {
        Ok(status) => {
            let status = status?;
            if !status.success() {
                use std::os::unix::process::ExitStatusExt;
                let code = status.code().unwrap_or_else(|| -status.signal().unwrap_or(1));
                return Err(WtErr::msg(format!("Worktree created but setup failed (exit {code}); run setup again; setup has no credentials, so network authentication must be configured outside setup")));
            }
            Ok(())
        }
        Err(_) => {
            if pid > 0 {
                // SAFETY: a plain signal to the process group this runner started for setup.
                unsafe { libc::killpg(pid, libc::SIGKILL) };
            }
            let _ = child.wait().await;
            Err(WtErr::msg("Worktree setup timed out; run setup again"))
        }
    }
}

const SETUP_TIMEOUT: Duration = Duration::from_secs(600);

/// Run a checked-out worktree's setup once its checkout proof holds (_run_registered_setup), recording
/// setup_running, then ready or setup_failed, locally and on the server.
async fn run_registered_setup(path: &Path, c: &Checkout<'_>, setup_command: &str, link: &Map<String, Value>, task: &str, client: &Client) -> R<()> {
    let link_id = Some(own(link, "link_id")).filter(|s| !s.is_empty()).unwrap_or_else(|| own(link, "id"));
    let (state, expected, target, base) = (own(link, "checkout_state"), own(link, "expected_head"), own(link, "checkout_target"), own(link, "expected_base"));
    if !["checkout_ready", "setup_failed", "setup_running"].contains(&state.as_str()) {
        return Err(WtErr::msg("Legacy or unknown worktree initialization is unverified; setup was not run"));
    }
    if expected.is_empty() || target.is_empty() || base.is_empty() {
        return Err(WtErr::msg("Worktree setup lacks recorded commit and base identity; kept unchanged"));
    }
    let marker = read_marker(c.workspace, &link_id)?;
    let identity = marker_identity(&link_id, &canonical_task(task, marker.as_ref()), &own(link, "path"), c.repo, c.branch, &expected, &target, &base);
    check_marker(marker.as_ref(), &identity)?;
    let mut marker = marker.unwrap();
    let phase = own(&marker, "phase");
    if !["checkout_complete", "restored", "setup_failed", "setup_running", "ready"].contains(&phase.as_str()) {
        return Err(WtErr::msg("Worktree setup has no completed checkout proof; kept unchanged"));
    }
    let base_clone = verify_worktree(path, c.workspace, c.repo, c.machine_git, c.branch, c.env, Some(&base), false).await?;
    if git(path, c.env, &["rev-parse", "HEAD"], true).await?.stdout.trim() != expected {
        return Err(WtErr::msg("Worktree moved from its recorded setup commit; kept unchanged"));
    }
    if state == "checkout_ready" && phase == "checkout_complete" && !porcelain_records(path, c.env).await?.is_empty() {
        return Err(WtErr::msg("Checkout changed before setup; kept worktree without running setup"));
    }
    let proof = (expected.as_str(), target.as_str(), base.as_str());
    if phase == "ready" && state == "setup_running" {
        return patch_checkout(client, task, &link_id, "ready", proof).await;
    }
    // The supervisor checked it out; setup and the bot's turns run as the bot user, who must be able to write the
    // worktree and the base clone its index, refs and objects live in (placement.rs adopts a checkout the same way).
    crate::isolation::adopt(&[path.to_path_buf(), base_clone]);
    marker.insert("phase".into(), json!("setup_running"));
    write_marker(c.workspace, &marker)?;
    patch_checkout(client, task, &link_id, "setup_running", proof).await?;
    if let Err(e) = setup(path, setup_command, c.env).await {
        marker.insert("phase".into(), json!("setup_failed"));
        write_marker(c.workspace, &marker)?;
        patch_checkout(client, task, &link_id, "setup_failed", proof).await?;
        return Err(e);
    }
    marker.insert("phase".into(), json!("ready"));
    write_marker(c.workspace, &marker)?;
    patch_checkout(client, task, &link_id, "ready", proof).await
}

/// Check out and set up a worktree the server reserved for a waiting task (backend/worktree_waits.py,
/// worktrees.prepare), as `hub task worktree add` does inside a turn, with the same records and proof: the bot
/// is idle, and the server wakes its task once this reports the worktree present. `repo` is the repository
/// row (`full_name`, `machine_git`, `setup_command`, `default_branch`). Returns the checkout proof.
pub async fn prepare(workspace: &Path, row: &Value, repo: &mut Map<String, Value>, env: &Env, client: &Client) -> R<(String, String, String)> {
    let task = text_of(&row["task_id"]);
    let mut link = row.as_object().cloned().unwrap_or_default();
    link.extend(detail(row));
    link.insert("link_id".into(), row["id"].clone());
    let _lock = locked(workspace, &text_of(&row["path"])).await?;
    let branch = checked_branch(&text_of(&row["branch"])).await?;
    let path = safe_path(workspace, &text_of(&row["path"]))?;
    let name = own(repo, "full_name");
    let c = Checkout { workspace, repo: &name, machine_git: crate::util::truthy(repo.get("machine_git")), branch: &branch, env };
    let state = Some(own(&link, "checkout_state")).filter(|s| !s.is_empty()).unwrap_or_else(|| "queued".into());
    if state == "queued" || state == "initializing" {
        if path.exists() && state == "queued" {
            return Err(WtErr::msg("Worktree path already exists without an initialization record; kept unchanged"));
        }
        disk_floor(workspace)?;
        let (base, default) = repositories::worktree_base(workspace, repo, env).await?;
        link.insert("checkout_state".into(), json!(state));
        let (head, target, base_id) = create_or_resume(&path, &c, &base, &default, &link, &task, client).await?;
        for (k, v) in [("checkout_state", "checkout_ready"), ("expected_head", head.as_str()), ("checkout_target", target.as_str()), ("expected_base", base_id.as_str())] {
            link.insert(k.into(), json!(v));
        }
    } else if state != "checkout_ready" && state != "setup_running" {
        return Err(WtErr::msg("Worktree is not waiting to be prepared; kept unchanged"));
    }
    run_registered_setup(&path, &c, &own(repo, "setup_command"), &link, &task, client).await?;
    Ok((own(&link, "expected_head"), own(&link, "checkout_target"), own(&link, "expected_base")))
}

pub type SizeCache = HashMap<String, (Instant, f64, f64)>;

/// The heartbeat's row for one tracked worktree (worktrees.inspect). Never changes the tree, except that an
/// attached checkout is certified here (its marker written) the first time it is seen whole.
pub async fn inspect(workspace: &Path, row: &Value, env: &Env, cache: Option<&mut SizeCache>) -> Value {
    let mut result = json!({"link_id": text_of(&row["id"]).chars().take(100).collect::<String>(), "state": "unknown", "branch": null});
    if let Some(updated) = row["updated"].as_str().filter(|u| u.len() <= 40) {
        result["record_updated"] = json!(updated);
    }
    let env = crate::safegit::environment(env);
    let mut step = "find it";
    let outcome = inspect_inner(workspace, row, &env, cache, &mut result, &mut step).await;
    if let Err(e) = outcome {
        let reason = e.text();
        result["error"] = json!(format!("Could not {step}: {reason}").chars().take(300).collect::<String>());
    }
    result
}

async fn inspect_inner(workspace: &Path, row: &Value, env: &Env, cache: Option<&mut SizeCache>, result: &mut Value, step: &mut &'static str) -> R<()> {
    let path = safe_path(workspace, &text_of(&row["path"]))?;
    let d = detail(row);
    let checkout_state = d.get("checkout_state").map(text_of).unwrap_or_default();
    let legacy_recorded = checkout_state == "legacy_present" && (row["state"] == "present" || row["state"] == "unknown");
    let legacy_candidate = ((checkout_state.is_empty() && row["state"] == "present") || legacy_demoted(row, &d)) && !crate::util::truthy(d.get("setup_pending"));
    let legacy = legacy_recorded || legacy_candidate;
    let mut not_ready = (checkout_state != "ready" && !legacy_recorded) || crate::util::truthy(d.get("setup_pending"));
    result["checkout_state"] = json!(if checkout_state.is_empty() { "unverified" } else { &checkout_state });
    let expected_base = d.get("expected_base").map(text_of).unwrap_or_default();
    let expected_head = d.get("expected_head").map(text_of).unwrap_or_default();
    let checkout_target = d.get("checkout_target").map(text_of).unwrap_or_default();
    for key in ["expected_head", "checkout_target", "expected_base"] {
        if let Some(v) = d.get(key).filter(|v| !v.is_null()) {
            result[key] = v.clone();
        }
    }
    if !path.exists() {
        if legacy_recorded {
            result["checkout_state"] = json!("legacy_present");
        }
        result["state"] = json!(if row["state"] == "removed" { "removed" } else if not_ready { "pending" } else { "missing" });
        return Ok(());
    }
    if !path.join(".git").is_file() {
        return Err(WtErr::msg("Tracked path is not a Git worktree; left as it is"));
    }
    *step = "read its remote";
    let origin = git(&path, env, &["config", "--get", "remote.origin.url"], true).await?.stdout.trim().to_string();
    let actual = crate::gitcred::repository_name(&origin).unwrap_or_else(|| origin.clone());
    let machine_git = crate::util::truthy(row.get("machine_git"));
    let mut repo = Some(text_of(&row["repo"])).filter(|r| !r.is_empty()).unwrap_or(actual);
    if checkout_state == "attached_pending" {
        let expected = text_of(&row["repo"]);
        if !expected.is_empty() && !repo_matches(&origin, &expected, machine_git) {
            return Err(WtErr::msg("Attached worktree repository does not match its task link"));
        }
    }
    result["repo"] = json!(repo);
    if !repo.is_empty() && (repo.len() > 200 || !repositories::valid_repository(&repo)) {
        result["repo"] = Value::Null;
        result["error"] = json!("Invalid or oversized repository name");
        repo.clear();
    }
    *step = "read its branch";
    // A detached HEAD (a review checkout of a commit) is a normal state: no branch to report.
    let branch = git(&path, env, &["symbolic-ref", "--short", "-q", "HEAD"], false).await?.stdout.trim().to_string();
    *step = "check its base clone";
    let common = common_dir(&path, env).await?;
    if !expected_base.is_empty() && base_identity(&common) != expected_base {
        return Err(WtErr::msg("Worktree is attached to a different managed base clone"));
    }
    if legacy {
        *step = "verify the legacy worktree";
        let (rrepo, rbranch) = (text_of(&row["repo"]), text_of(&row["branch"]));
        if rrepo.is_empty() || rbranch.is_empty() {
            return Err(WtErr::msg("Legacy worktree has no registered repository or branch; kept unverified"));
        }
        verify_worktree(&path, workspace, &rrepo, !repositories::same_repository(&origin, &rrepo), &rbranch, env, None, true).await?;
        if !git(&path, env, &["ls-files", "--deleted", "-z"], true).await?.stdout.is_empty() {
            result["state"] = json!("pending");
            result["checkout_state"] = json!("unverified");
            result["error"] = json!("Legacy worktree has missing tracked files; kept unchanged and unverified");
            return Ok(());
        }
        result["checkout_state"] = json!("legacy_present");
        not_ready = crate::util::truthy(d.get("setup_pending"));
    }
    if checkout_state == "ready" && (expected_base.is_empty() || expected_head.is_empty() || checkout_target.is_empty()) {
        not_ready = true;
        result["checkout_state"] = json!("unverified");
    } else if checkout_state == "ready" {
        *step = "verify its checkout";
        let mut merged = row.clone();
        for (k, v) in &d {
            merged[k] = v.clone();
        }
        verify_ready_registration(workspace, &merged, &d, &repo).await?;
    }
    *step = "read its changes";
    result["state"] = json!(if not_ready { "pending" } else { "present" });
    result["dirty_files"] = json!(git(&path, env, &["status", "--porcelain"], true).await?.stdout.lines().count());
    result["last_commit"] = json!(git(&path, env, &["rev-parse", "HEAD"], true).await?.stdout.trim().chars().take(100).collect::<String>());
    if !branch.is_empty() {
        match checked_branch(&branch).await {
            Ok(b) => result["branch"] = json!(b),
            Err(_) => result["error"] = json!("Invalid or oversized worktree branch"),
        }
    }
    if checkout_state == "attached_pending" {
        *step = "record the attached checkout";
        if result.get("error").is_some() {
            return Err(WtErr::msg("Attached worktree metadata is invalid; left as it is"));
        }
        let reported = text_of(&result["branch"]);
        if !text_of(&row["branch"]).is_empty() && text_of(&row["branch"]) != reported {
            return Err(WtErr::msg("Attached worktree branch does not match its task link"));
        }
        let (head, target, base) = record_attached(workspace, row, &reported, &repo, machine_git, env).await?;
        result["checkout_state"] = json!("ready");
        result["expected_head"] = json!(head);
        result["checkout_target"] = json!(target);
        result["expected_base"] = json!(base);
        result["state"] = json!("present");
    }
    *step = "compare it with the remote";
    let task_branch = text_of(&row["branch"]);
    let remote = if task_branch.is_empty() { String::new() } else { format!("refs/remotes/origin/{task_branch}") };
    if !remote.is_empty() && git(&path, env, &["show-ref", "--verify", &remote], false).await?.ok() {
        let counts = git(&path, env, &["rev-list", "--left-right", "--count", &format!("HEAD...{remote}")], true).await?.stdout;
        let mut it = counts.split_whitespace().map(|n| n.parse::<i64>().unwrap_or(0));
        result["ahead"] = json!(it.next().unwrap_or(0));
        result["behind"] = json!(it.next().unwrap_or(0));
    } else {
        let out = git(&path, env, &["rev-list", "--count", "HEAD", "--not", "--remotes=origin"], true).await?;
        result["ahead"] = json!(out.stdout.trim().parse::<i64>().map_err(|_| WtErr::msg("invalid literal for int()"))?);
        result["behind"] = json!(0);
    }
    // File statistics are expensive on dependency folders; refresh at most every ten minutes.
    *step = "measure its size";
    let id = text_of(&row["id"]);
    let fresh = |c: &(Instant, f64, f64)| c.0.elapsed() < Duration::from_secs(600);
    let cached = cache.as_ref().and_then(|c| c.get(&id).cloned()).filter(fresh);
    let entry = match cached {
        Some(c) => c,
        None => {
            let (bytes, newest) = repositories::tree_size(&path);
            let made = (Instant::now(), (bytes as f64 / (1024.0 * 1024.0) * 10.0).round() / 10.0, newest);
            if let Some(c) = cache {
                c.insert(id, made);
            }
            made
        }
    };
    result["size_mb"] = json!(entry.1);
    result["last_activity"] = json!(entry.2);
    Ok(())
}

const BUILD: &[&str] = &[
    "node_modules", ".venv", "venv", "dist", "build", "target", ".next", "__pycache__", ".cache", "coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".gradle",
    ".turbo", ".parcel-cache", ".DS_Store", ".idea", ".vscode", "out", ".build", ".swiftpm", "DerivedData",
];

pub fn build_file(name: &str) -> bool {
    let parts: Vec<&str> = name.split('/').filter(|p| !p.is_empty()).collect();
    parts.iter().any(|p| BUILD.contains(p) || p.ends_with(".egg-info") || p.ends_with(".pyc"))
        || parts.windows(2).any(|w| w[0] == ".terraform" && (w[1] == "providers" || w[1] == "plugin-cache"))
        // Tauri regenerates `gen/schemas`; `gen/apple` and `gen/android` hold mobile projects and signing files, and a bare `gen` may be source.
        || parts.windows(2).any(|w| w[0] == "gen" && w[1] == "schemas")
}

/// fnmatch of a lowercased file name against `.env*`, `*.pem`, `*.key`, `id_rsa*`, `credentials*`, `*.p12`.
fn secret_name(name: &str) -> bool {
    [".env", "id_rsa", "credentials"].iter().any(|p| name.starts_with(p)) || [".pem", ".key", ".p12"].iter().any(|s| name.ends_with(s))
}

pub fn wip_branch(row: &Value) -> String {
    let link = Some(text_of(&row["id"])).filter(|s| !s.is_empty()).unwrap_or_else(|| text_of(&row["link_id"]));
    format!("wip/{}-{}", text_of(&row["task_id"]).chars().take(8).collect::<String>(), link.chars().take(6).collect::<String>())
}

async fn remote_branch(base: &Path, branch: &str, env: &Env) -> R<Option<String>> {
    checked_branch(branch).await?;
    let listed = git(base, env, &["ls-remote", "--exit-code", "origin", &format!("refs/heads/{branch}")], false).await?;
    if listed.code == 2 {
        return Ok(None);
    }
    if !listed.ok() {
        let detail = if listed.stderr.is_empty() { format!("exit {}", listed.code) } else { listed.stderr };
        return Err(WtErr::Msg("Git fetch failed; cannot determine remote branch, retry later".into(), Some(detail)));
    }
    git(base, env, &["fetch", "--no-tags", "origin", &format!("+refs/heads/{branch}:refs/remotes/origin/{branch}")], true).await?;
    Ok(Some(format!("refs/remotes/origin/{branch}")))
}

async fn fast_forward(path: &Path, branch: &str, target: &str, env: &Env) -> R<()> {
    let r#ref = format!("refs/heads/{}", checked_branch(branch).await?);
    let old = git(path, env, &["rev-parse", "--verify", &r#ref], false).await?;
    let head = git(path, env, &["rev-parse", target], true).await?.stdout.trim().to_string();
    let previous = if old.ok() {
        let previous = old.stdout.trim().to_string();
        if !git(path, env, &["merge-base", "--is-ancestor", &previous, &head], false).await?.ok() {
            return Err(WtErr::msg("Task branch has separate history; kept worktree"));
        }
        if git(path, env, &["worktree", "list", "--porcelain"], true).await?.stdout.lines().any(|l| l == format!("branch {}", r#ref)) {
            return Err(WtErr::msg("Task branch is checked out elsewhere; kept worktree"));
        }
        previous
    } else {
        "0".repeat(head.len())
    };
    // A concurrent branch update must fail rather than discard its new commits.
    git(path, env, &["update-ref", &r#ref, &head, &previous], true).await?;
    Ok(())
}

async fn count(path: &Path, env: &Env, args: &[&str]) -> R<i64> {
    Ok(git(path, env, args, true).await?.stdout.trim().parse::<i64>().unwrap_or(0))
}

/// A folder a tool can rebuild on its own: a Python virtual environment (`pyvenv.cfg`), a cache that declares
/// itself (`CACHEDIR.TAG`, which Cargo's `target` and `.pytest_cache` write), `node_modules`, or a pytest base
/// temp folder.
fn rebuildable(dir: &Path) -> bool {
    let name = dir.file_name().map(|n| n.to_string_lossy().to_lowercase()).unwrap_or_default();
    let tagged = std::fs::read(dir.join("CACHEDIR.TAG")).is_ok_and(|b| b.starts_with(b"Signature: 8a477f597d28d172789f06886806bc55"));
    dir.join("pyvenv.cfg").is_file()
        || tagged
        || name == "node_modules"
        || name.starts_with("pytest-of-")
        || (name.starts_with("tmp") && name.contains("pytest"))
}

/// Folders under `dir` to delete and git checkouts to consider, never through a symlink and at most six levels down.
fn scratch(dir: &Path, depth: u32, caches: &mut Vec<PathBuf>, checkouts: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    for entry in entries.flatten() {
        let path = entry.path();
        if !entry.file_type().is_ok_and(|t| t.is_dir()) || entry.file_name() == ".git" {
            continue;
        }
        if rebuildable(&path) {
            caches.push(path);
            continue;
        }
        if path.join(".git").is_file() {
            checkouts.push(path.clone());
        }
        if depth < 6 {
            scratch(&path, depth + 1, caches, checkouts);
        }
    }
}

/// After a finished task's worktree is removed, what its bot built beside it in `tasks/<task-short>/` goes too:
/// rebuildable caches anywhere in the folder, and extra git worktrees with nothing unsaved or unpushed. Logs,
/// notes and anything else stay, and a checkout that is dirty, ahead of origin or holds ignored files outside
/// build folders is kept as it is.
pub async fn clear_task_scratch(workspace: &Path, removed: &str, env: &Env) {
    let root = resolve(workspace);
    let tasks = root.join("tasks");
    let mut parts = Path::new(removed).components();
    let (Some(std::path::Component::Normal(top)), Some(std::path::Component::Normal(task))) = (parts.next(), parts.next()) else { return };
    let folder = tasks.join(task);
    if top != "tasks" || is_symlink(&tasks) || is_symlink(&folder) || resolve(&folder).parent() != Some(tasks.as_path()) || !folder.is_dir() {
        return;
    }
    let walk = folder.clone();
    let Ok((caches, checkouts)) = tokio::task::spawn_blocking(move || {
        let (mut caches, mut checkouts) = (vec![], vec![]);
        scratch(&walk, 1, &mut caches, &mut checkouts);
        (caches, checkouts)
    })
    .await
    else {
        return;
    };
    for tree in checkouts {
        let Ok(common) = common_dir(&tree, env).await else { continue };
        if !common.starts_with(&root) || common.starts_with(&folder) || !tree.exists() {
            continue;
        }
        let clean = git(&tree, env, &["status", "--porcelain"], true).await.is_ok_and(|o| o.stdout.trim().is_empty());
        let ignored = git(&tree, env, &["ls-files", "--others", "--ignored", "--exclude-standard", "-z"], true).await;
        let only_build = ignored.is_ok_and(|o| o.stdout.split('\0').all(|n| n.is_empty() || build_file(n)));
        if clean && only_build && count(&tree, env, &["rev-list", "--count", "HEAD", "--not", "--remotes=origin"]).await.is_ok_and(|n| n == 0) {
            let _ = git(common.parent().unwrap(), env, &["worktree", "remove", &tree.display().to_string()], true).await;
        }
    }
    let gone = tokio::task::spawn_blocking(move || {
        let mut freed = 0;
        for dir in caches.iter().filter(|d| !is_symlink(d) && d.starts_with(&folder)) {
            if std::fs::remove_dir_all(dir).is_ok() {
                freed += 1;
            }
        }
        let _ = std::fs::remove_dir(&folder); // only when nothing else is left
        freed
    })
    .await
    .unwrap_or(0);
    if gone > 0 {
        crate::util::diagnostic(&format!("Tico runner: removed {gone} rebuildable folders from finished task folder {}", task.to_string_lossy()));
    }
}

/// The pushes a turn made from its task's worktrees since `since` (unix seconds), as `{"path", "count"}` per
/// worktree that pushed, `path` as its link records it: Tico never sees a push, so the run's result reports them
/// (backend/run_triggers.py). A push leaves `update by push` in the reflog of the remote-tracking ref of the branch
/// it sent, and a fetch or pull leaves something else. Only each worktree's own branch is read: worktrees of one
/// base clone share remote-tracking refs, and git checks a branch out in one worktree at a time, so another task's
/// push is never counted here.
pub async fn pushes(workspace: &Path, links: &[Value], since: i64, env: &Env) -> Vec<Value> {
    let mut out = vec![];
    let mut seen: HashSet<PathBuf> = HashSet::new();
    for link in links {
        let recorded = text_of(&link["path"]);
        let Ok(path) = safe_path(workspace, &recorded) else { continue };
        if !path.join(".git").is_file() || !seen.insert(path.clone()) {
            continue;
        }
        let Ok(branch) = git(&path, env, &["symbolic-ref", "--short", "-q", "HEAD"], false).await else { continue };
        let branch = branch.stdout.trim().to_string();
        if branch.is_empty() {
            continue; // detached: no branch of its own to have pushed
        }
        let mut refs = vec![format!("refs/remotes/origin/{branch}")];
        if let Ok(up) = git(&path, env, &["rev-parse", "--symbolic-full-name", "@{upstream}"], false).await {
            let up = up.stdout.trim().to_string();
            // An upstream of another name (origin/main) is a ref sibling worktrees push to as well.
            if up.starts_with("refs/remotes/") && up.ends_with(&format!("/{branch}")) && !refs.contains(&up) {
                refs.push(up);
            }
        }
        let mut n = 0;
        for r in &refs {
            let Ok(log) = git(&path, env, &["log", "-g", "--date=unix", "--format=%gd%x09%gs", r, "--"], false).await else { continue };
            n += log.stdout.lines().filter(|line| {
                let (at, subject) = line.split_once('\t').unwrap_or((line, ""));
                let ts = at.rsplit_once("@{").and_then(|(_, t)| t.trim_end_matches('}').parse::<i64>().ok());
                subject == "update by push" && ts.is_some_and(|t| t >= since)
            }).count();
        }
        if n > 0 {
            out.push(json!({"path": recorded, "count": n.min(1000)}));
        }
    }
    out
}

/// A remove or restore (worktrees._act). `still_closed` is asked again just before the tree goes.
/// A field of an action's row, "" when it has none (a Map index would panic).
fn own(row: &Map<String, Value>, key: &str) -> String {
    row.get(key).map(text_of).unwrap_or_default()
}

/// Git's conflict-marker lines with their labels dropped, so a file and git's own re-merge of its stages compare equal.
fn marker_shape(text: &str) -> String {
    static MARKER: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    MARKER.get_or_init(|| Regex::new(r"(?m)^(<{7}|\|{7}|>{7})(?: .*)?$").unwrap()).replace_all(text, "$1").into_owned()
}

/// A scratch folder inside the worktree's git folder, removed when it goes out of scope.
struct Scratch(PathBuf);
impl Drop for Scratch {
    fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); }
}

/// True when a conflicted file is still exactly git's own conflicted text for its stages: nothing resolved or edited.
async fn untouched_conflict(path: &Path, gitdir: &Path, name: &str, env: &Env) -> R<bool> {
    let Ok(text) = std::fs::read_to_string(path.join(name)) else { return Ok(false) };
    let mut stages: HashMap<&str, String> = HashMap::new();
    for stage in ["1", "2", "3"] {
        let shown = git(path, env, &["show", &format!(":{stage}:{name}")], false).await?;
        if shown.ok() {
            stages.insert(stage, shown.stdout);
        }
    }
    let (Some(ours), Some(theirs)) = (stages.get("2"), stages.get("3")) else { return Ok(false) };
    let base = stages.get("1").cloned().unwrap_or_default();
    let scratch = Scratch(gitdir.join(format!("tico-merge-check-{}", uuid::Uuid::new_v4().simple())));
    std::fs::create_dir_all(&scratch.0)?;
    let (o, b, t) = (scratch.0.join("ours"), scratch.0.join("base"), scratch.0.join("theirs"));
    std::fs::write(&o, ours)?;
    std::fs::write(&b, base)?;
    std::fs::write(&t, theirs)?;
    let (o, b, t) = (o.display().to_string(), b.display().to_string(), t.display().to_string());
    let shaped = marker_shape(&text);
    for flags in [vec![], vec!["--diff3"]] {
        let mut args = vec!["merge-file", "-p"];
        args.extend(flags);
        args.extend([o.as_str(), b.as_str(), t.as_str()]);
        let merged = git(path, env, &args, false).await?;
        if (1..=127).contains(&merged.code) && marker_shape(&merged.stdout) == shaped {
            return Ok(true);
        }
    }
    Ok(false)
}

/// The tree git's own merge of HEAD and MERGE_HEAD writes, from `git merge-tree --write-tree` output: None when the
/// output is not one (an older git refuses the option, which keeps the worktree).
fn merge_tree_oid(out: &Out) -> Option<String> {
    let tree = out.stdout.lines().next().unwrap_or("").trim();
    let shaped = (out.code == 0 || out.code == 1) && (tree.len() == 40 || tree.len() == 64) && tree.bytes().all(|b| b.is_ascii_hexdigit());
    shaped.then(|| tree.to_string())
}

/// Aborts a merge this removal finds abandoned in its own tree, before any switch, add or commit: the rules of
/// runner/worktrees.py `_clear_merge`. Anything not provably abandoned is kept, untouched.
async fn clear_merge(path: &Path, release: bool, env: &Env) -> R<()> {
    let gitdir = PathBuf::from(git(path, env, &["rev-parse", "--path-format=absolute", "--git-dir"], true).await?.stdout.trim());
    if !gitdir.join("MERGE_HEAD").exists() {
        return Ok(());
    }
    if release {
        return Err(WtErr::msg("kept: merge in progress; finish or abort it to free this worktree"));
    }
    const UNRESOLVED: &str = "kept: merge in progress with resolved or unrecognised conflicts; commit or abort it to free this worktree";
    if ["CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"].iter().any(|n| gitdir.join(n).exists()) {
        return Err(WtErr::msg(UNRESOLVED));
    }
    let listed = git(path, env, &["ls-files", "-u", "-z"], true).await?.stdout;
    let unmerged: BTreeSet<String> = listed.split('\0').filter_map(|entry| entry.split_once('\t').map(|(_, name)| name.to_string())).collect();
    if unmerged.is_empty() {
        return Err(WtErr::msg(UNRESOLVED));
    }
    for name in &unmerged {
        if !untouched_conflict(path, &gitdir, name, env).await? {
            return Err(WtErr::msg(UNRESOLVED));
        }
    }
    let tree = merge_tree_oid(&git(path, env, &["merge-tree", "--write-tree", "HEAD", "MERGE_HEAD"], false).await?);
    let Some(tree) = tree else {
        return Err(WtErr::msg(UNRESOLVED));
    };
    // Renames off: a staged deletion paired with an identical addition must still be listed as a deletion.
    let mut touched: BTreeSet<String> = BTreeSet::new();
    for listing in [
        git(path, env, &["diff", "--no-renames", "--name-only", "-z", "HEAD", "MERGE_HEAD"], true).await?.stdout,
        git(path, env, &["diff", "--no-renames", "--cached", "--name-only", "-z"], true).await?.stdout,
    ] {
        touched.extend(listing.split('\0').filter(|name| !name.is_empty()).map(str::to_string));
    }
    // The abort refuses an unstaged edit to a path the merge itself changed, so keep that case with the same error.
    let changed: BTreeSet<String> = git(path, env, &["diff", "--no-renames", "--name-only", "-z", "HEAD", &tree], true).await?.stdout
        .split('\0').filter(|name| !name.is_empty()).map(str::to_string).collect();
    let edited: BTreeSet<String> = git(path, env, &["diff", "--no-renames", "--name-only", "-z"], true).await?.stdout
        .split('\0').filter(|name| !name.is_empty()).map(str::to_string).collect();
    if edited.iter().any(|name| changed.contains(name) && !unmerged.contains(name)) {
        return Err(WtErr::msg(UNRESOLVED));
    }
    // The index must hold git's own merge result for every path the merge touched, conflicts aside. Any other entry is
    // a resolution, which the abort would discard. Equal blobs prove nothing: a resolution can select either side.
    for name in touched.iter().filter(|name| !unmerged.contains(*name)) {
        // Mode and blob both: a staged mode change on a path the merge touched would otherwise be reset by the abort.
        let ours: Vec<String> = git(path, env, &["ls-files", "-s", "--", name], false).await?.stdout.split_whitespace().take(2).map(str::to_string).collect();
        let expected: Vec<String> = git(path, env, &["ls-tree", &tree, "--", name], false).await?.stdout.split_whitespace().enumerate().filter(|(i, _)| *i != 1).take(2).map(|(_, t)| t.to_string()).collect();
        if ours != expected {
            return Err(WtErr::msg(UNRESOLVED));
        }
    }
    git(path, env, &["merge", "--abort"], true).await?;
    Ok(())
}

pub async fn act<F, Fut>(workspace: &Path, row: &mut Map<String, Value>, action: &str, env: &Env, vault: &[String], still_closed: F) -> R<&'static str>
where
    F: Fn() -> Fut,
    Fut: std::future::Future<Output = bool>,
{
    let _lock = locked(workspace, &own(row, "path")).await?;
    let done = act_inner(workspace, row, action, env, vault, &still_closed).await;
    match done {
        Err(WtErr::Msg(_, Some(detail))) if crate::util::truthy(row.get("machine_git")) => Err(WtErr::msg(repositories::machine_error(&own(row, "repo"), &detail, &Env::new()))),
        other => other,
    }
}

async fn act_inner<F, Fut>(workspace: &Path, row: &mut Map<String, Value>, action: &str, env: &Env, vault: &[String], still_closed: &F) -> R<&'static str>
where
    F: Fn() -> Fut,
    Fut: std::future::Future<Output = bool>,
{
    let env = &crate::safegit::environment(env);
    let path = safe_path(workspace, &own(row, "path"))?;
    let value = Value::Object(row.clone());
    let repo = own(row, "repo");
    if action == "remove" {
        if !still_closed().await {
            return Err(WtErr::Deferred("Task reopened or bot is running; retry cleanup later".into()));
        }
        if !path.exists() {
            // Prune even when the folder was removed outside Tico.
            if repo.is_empty() {
                return Ok("removed");
            }
            let base = workspace.join("repos").join(repositories::base_folder(&repo));
            let parent = base.parent().unwrap();
            if is_symlink(parent) || is_symlink(&base) || is_symlink(&base.join(".git")) || resolve(&base).parent() != Some(resolve(parent).as_path()) {
                return Err(WtErr::msg("Worktree base points outside repos"));
            }
            if base.exists() {
                git(&base, env, &["worktree", "prune", "--expire", "now"], true).await?;
            }
            return Ok("removed");
        }
        if repo.is_empty() {
            return Err(WtErr::msg("Repository not identified; kept worktree"));
        }
        let branch = checked_branch(&own(row, "branch")).await?;
        if !path.join(".git").is_file() {
            return Err(WtErr::msg("Tracked path is not a worktree; left as it is"));
        }
        let common = common_dir(&path, env).await?;
        if !common.starts_with(resolve(workspace)) {
            return Err(WtErr::msg("Worktree base is outside the team workspace"));
        }
        let base = common.parent().unwrap().to_path_buf();
        let origin_head = git(&base, env, &["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], false).await?.stdout.trim().trim_start_matches("origin/").to_string();
        let defaults: HashSet<String> = [own(row, "default_branch"), origin_head].into_iter().filter(|n| !n.is_empty()).collect();
        if defaults.is_empty() {
            return Err(WtErr::msg("Repository default branch is unknown; refresh Repositories before cleanup"));
        }
        git(&base, env, &["worktree", "prune", "--expire", "now"], true).await?;
        clear_merge(&path, crate::util::truthy(row.get("release")), env).await?;
        let ignored = git(&path, env, &["ls-files", "--others", "--ignored", "--exclude-standard", "-z"], true).await?.stdout;
        if ignored.split('\0').any(|n| !n.is_empty() && !build_file(n)) {
            return Err(WtErr::msg("kept: ignored files"));
        }
        let dirty = !git(&path, env, &["status", "--porcelain"], true).await?.stdout.trim().is_empty();
        let wip = wip_branch(&value);
        if defaults.contains(&wip) {
            return Err(WtErr::msg("Snapshot branch matches the default branch; kept worktree"));
        }
        let current = git(&path, env, &["symbolic-ref", "--short", "HEAD"], false).await?.stdout.trim().to_string();
        let unpushed = ["rev-list", "--count", "HEAD", "--not", "--remotes=origin"];
        if crate::util::truthy(row.get("release")) {
            // Released while its task is open: the bot may still use it, so nothing is saved on its behalf.
            if dirty {
                return Err(WtErr::msg("kept: unsaved changes; commit and push them, or discard them, to free this worktree"));
            }
            if count(&path, env, &unpushed).await? > 0 {
                return Err(WtErr::msg("kept: unpushed commits; push them to free this worktree"));
            }
        } else if dirty {
            let mut names_text = git(&path, env, &["diff", "HEAD", "--name-only", "-z"], true).await?.stdout;
            names_text += &git(&path, env, &["ls-files", "--others", "--exclude-standard", "-z"], true).await?.stdout;
            let names: Vec<&str> = names_text.split('\0').filter(|n| !n.is_empty()).collect();
            let skipped: Vec<String> = names
                .iter()
                .filter(|n| {
                    let file = path.join(n);
                    let leaf = Path::new(n).file_name().map(|f| f.to_string_lossy().to_lowercase()).unwrap_or_default();
                    secret_name(&leaf) || is_symlink(&file) || std::fs::metadata(&file).is_ok_and(|m| m.len() > 20 * 1024 * 1024)
                })
                .map(|n| n.to_string())
                .collect();
            if !skipped.is_empty() {
                row.insert("skipped_files".into(), json!(skipped.iter().take(100).map(|n| n.chars().take(1000).collect::<String>()).collect::<Vec<_>>()));
                return Err(WtErr::msg(format!("kept: skipped unsafe or oversized files: {}", skipped.join(", ").chars().take(150).collect::<String>())));
            }
            // Divergent HEAD must never replace the task branch, even after a successful snapshot push.
            let exists = git(&path, env, &["show-ref", "--verify", &format!("refs/heads/{branch}")], false).await?.ok();
            if exists && !git(&path, env, &["merge-base", "--is-ancestor", &branch, "HEAD"], false).await?.ok() {
                return Err(WtErr::msg("Task branch has separate history; kept worktree to preserve unpushed commits"));
            }
            let mut patch = git(&path, env, &["diff", "--no-ext-diff", "--no-textconv", "HEAD"], true).await?.stdout;
            for n in &names {
                if path.join(n).is_file() {
                    patch += &String::from_utf8_lossy(&std::fs::read(path.join(n)).unwrap_or_default());
                }
            }
            let pairs: Vec<(String, String)> = env.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
            let known = crate::redact::for_turn(&pairs, vault);
            static SECRET: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
            let shaped = SECRET.get_or_init(|| {
                Regex::new(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|sk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}").unwrap()
            });
            if crate::redact::scrub_log(&patch) != patch || known.is_some_and(|k| k.holds(patch.as_bytes())) || shaped.is_match(&patch) {
                return Err(WtErr::msg("Possible secret in unsaved changes; kept worktree"));
            }
            if current != wip {
                if git(&path, env, &["show-ref", "--verify", &format!("refs/heads/{wip}")], false).await?.ok()
                    && !git(&path, env, &["merge-base", "--is-ancestor", &wip, "HEAD"], false).await?.ok()
                {
                    return Err(WtErr::msg("Saved work has separate history; kept worktree"));
                }
                git(&path, env, &["switch", "-C", &wip], true).await?;
            }
            git(&path, env, &["add", "--all"], true).await?;
            git(&path, env, &["-c", "user.name=Tico", "-c", "user.email=bot@example.com", "commit", "-m", "Save task work before cleanup"], true).await?;
            git(&path, env, &["push", "origin", &format!("HEAD:refs/heads/{wip}")], true).await?;
            if !defaults.contains(&branch) {
                let moved = fast_forward(&path, &branch, "HEAD", env).await;
                git(&path, env, &["switch", &branch], true).await?;
                moved?;
            }
        } else if current == wip {
            // Retrying a failed snapshot push must still save its history.
            git(&path, env, &["push", "origin", &format!("HEAD:refs/heads/{wip}")], true).await?;
            if !defaults.contains(&branch) {
                if !git(&path, env, &["merge-base", "--is-ancestor", &branch, "HEAD"], false).await?.ok() {
                    return Err(WtErr::msg("Task branch has separate history; kept worktree"));
                }
                let moved = fast_forward(&path, &branch, "HEAD", env).await;
                git(&path, env, &["switch", &branch], true).await?;
                moved?;
            }
        } else if count(&path, env, &unpushed).await? > 0 {
            if crate::util::truthy(row.get("prs_finished")) || current.is_empty() {
                git(&path, env, &["push", "origin", &format!("HEAD:refs/heads/{wip}")], true).await?;
            } else if current != branch || defaults.contains(&branch) {
                return Err(WtErr::msg("Unpushed history on another or default branch; kept worktree"));
            } else {
                git(&path, env, &["push", "origin", &format!("{branch}:refs/heads/{branch}")], true).await?;
            }
        }
        if !still_closed().await {
            if !defaults.contains(&branch) && git(&path, env, &["symbolic-ref", "--short", "HEAD"], false).await?.stdout.trim() == wip {
                git(&path, env, &["switch", &branch], true).await?;
            }
            return Err(WtErr::Deferred("Task reopened or bot is running; retry cleanup later".into()));
        }
        git(&base, env, &["worktree", "remove", &path.display().to_string()], true).await?;
        return Ok("removed");
    }
    let branch = checked_branch(&own(row, "branch")).await?;
    disk_floor(workspace)?;
    if path.exists() {
        let mut probe = value.clone();
        if text_of(&probe["id"]).is_empty() {
            probe["id"] = row.get("link_id").cloned().unwrap_or(Value::Null);
        }
        if inspect(workspace, &probe, env, None).await["state"] != "present" {
            return Err(WtErr::msg("Restore path already exists and is not a worktree"));
        }
        let base = verify_worktree(&path, workspace, &repo, crate::util::truthy(row.get("machine_git")), &branch, env, None, false).await?;
        crate::isolation::adopt(&[path.clone(), base]);
        return Ok("present");
    }
    let (base, default) = repositories::worktree_base(workspace, row, env).await?;
    if let Some(w) = row.get("fetch_warning").cloned() {
        row.insert("restore_source".into(), w);
    }
    git(&base, env, &["worktree", "prune", "--expire", "now"], true).await?;
    crate::isolation::mkdir(path.parent().unwrap(), 0o755)?;
    let local = git(&base, env, &["show-ref", "--verify", &format!("refs/heads/{branch}")], false).await?.ok();
    let task = own(row, "task_id");
    let found: R<(Option<String>, Option<String>)> = async {
        let task_remote = remote_branch(&base, &branch, env).await?;
        let mut saved = remote_branch(&base, &wip_branch(&value), env).await?;
        if saved.is_none() {
            saved = remote_branch(&base, &format!("wip/{}", task.chars().take(8).collect::<String>()), env).await?;
        }
        Ok((task_remote, saved))
    }
    .await;
    let (task_remote, saved) = match found {
        Ok(f) => f,
        Err(e) if !local => return Err(e),
        Err(_) => {
            row.insert("restore_source".into(), json!("local (remote unavailable)"));
            (None, None)
        }
    };
    let mut start = if local { branch.clone() } else { task_remote.clone().unwrap_or_else(|| format!("origin/{default}")) };
    if let Some(saved) = saved {
        if !(local || task_remote.is_some()) || git(&base, env, &["merge-base", "--is-ancestor", &start, &saved], false).await?.ok() {
            start = saved;
        } else {
            row.insert("snapshot_skipped".into(), json!(saved));
        }
    }
    let p = path.display().to_string();
    if local {
        if start != branch {
            fast_forward(&base, &branch, &start, env).await?;
        }
        git(&base, env, &["worktree", "add", &p, &branch], true).await?;
    } else {
        git(&base, env, &["worktree", "add", "--no-track", "-b", &branch, &p, &start], true).await?;
    }
    // Restored by the supervisor for the bot user to work in, as a prepared worktree is.
    crate::isolation::adopt(&[path.clone(), base.clone()]);
    Ok("present")
}

fn server_lacks_checkout_fields(e: &ApiError) -> bool {
    let d = e.detail.to_lowercase();
    e.status == 422
        && ["checkout_state", "expected_head", "checkout_target", "expected_base"].iter().any(|n| d.contains(n))
        && ["extra", "unknown field", "unexpected field", "not permitted"].iter().any(|m| d.contains(m))
}

fn closed(row: &Value) -> bool {
    let d = detail(row);
    ["done", "closed", "declined"].contains(&text_of(&row["task_status"]).as_str()) || row["bot_state"] == "archived" || crate::util::truthy(d.get("delete_requested"))
}

/// What the manager needs from the runner: whether a bot's turn could be in a task's worktree, the bot's
/// checkout and lane locks (held without waiting), and the vault values last granted to the bot.
pub trait Host: Send + Sync + 'static {
    fn idle(&self, bot: &str, task: &str) -> bool;
    fn try_locks(&self, bot: &str, lane: &str) -> Option<Box<dyn Send>>;
    fn vault(&self, bot: &str) -> Vec<String>;
    fn retain_vault(&self, owners: &HashSet<String>);
    fn repositories(&self) -> Option<Arc<repositories::Repositories>>;
}

#[derive(Default)]
struct Inner {
    reports: Vec<Value>,
    actions: Map<String, Value>,
    errors: HashMap<String, String>,
    retry: HashMap<String, (Instant, u32)>,
    /// Explicit requests are consumed once and remain consumed across ordinary cleanup failures.
    cleanup_retry_generations: HashMap<String, String>,
    stats: SizeCache,
}

/// The computer's worktree reports and the one background pass that refreshes them (worktrees.Worktrees).
pub struct Worktrees {
    workspace: PathBuf,
    client: Client,
    inner: Mutex<Inner>,
    pending: Mutex<Option<tokio::task::JoinHandle<()>>>,
    /// Bots whose worktree maintenance is running: no claim names them meanwhile.
    pub maintaining: Mutex<HashSet<String>>,
}

impl Worktrees {
    pub fn new(workspace: &Path, client: Client) -> Worktrees {
        Worktrees { workspace: workspace.to_path_buf(), client, inner: Mutex::new(Inner::default()), pending: Mutex::new(None), maintaining: Mutex::new(HashSet::new()) }
    }

    /// Queue the heartbeat reply's actions and start a pass if none is running; the last reports meanwhile.
    pub fn poll(self: &Arc<Self>, host: Arc<dyn Host>, actions: &[Value]) -> Vec<Value> {
        let mut inner = self.inner.lock().unwrap();
        for a in actions {
            inner.actions.insert(text_of(&a["link_id"]), a.clone());
        }
        let mut pending = self.pending.lock().unwrap();
        if pending.as_ref().is_none_or(|p| p.is_finished()) {
            let queued: Vec<Value> = std::mem::take(&mut inner.actions).into_values().collect();
            let me = self.clone();
            *pending = Some(tokio::spawn(async move { me.sync(host, queued).await }));
        }
        inner.reports.clone()
    }

    pub fn latest(&self) -> Vec<Value> {
        self.inner.lock().unwrap().reports.clone()
    }

    /// Wait for the running pass (tests and shutdown).
    pub async fn settle(&self) {
        let handle = self.pending.lock().unwrap().take();
        if let Some(h) = handle {
            let _ = h.await;
        }
    }

    pub async fn sync(self: Arc<Self>, host: Arc<dyn Host>, actions: Vec<Value>) {
        adopt_task_states(&self.workspace);
        let Ok(reply) = self.client.get("runners/me/worktrees").await else { return };
        let mut rows: Vec<Value> = reply["worktrees"].as_array().cloned().unwrap_or_default();
        let owners: HashSet<String> = rows.iter().map(|r| text_of(&r["owner"]).trim_start_matches("bot:").to_string()).collect();
        host.retain_vault(&owners);
        let ids: HashSet<String> = rows.iter().map(|r| text_of(&r["id"])).collect();
        let mut errors: HashMap<String, String> = self.inner.lock().unwrap().errors.iter().filter(|(k, _)| ids.contains(*k)).map(|(k, v)| (k.clone(), v.clone())).collect();
        let mut failed: HashSet<String> = HashSet::new();
        for action in &actions {
            let link = text_of(&action["link_id"]);
            let Some(index) = rows.iter().position(|r| text_of(&r["id"]) == link) else { continue };
            let row = rows[index].clone();
            let d = detail(&row);
            let is_closed = closed(&row);
            let released = crate::util::truthy(action.get("release")) && crate::util::truthy(d.get("release_requested"));
            let kind = text_of(&action["action"]);
            if (kind == "remove" && !(is_closed || released)) || ((kind == "restore" || kind == "prepare") && is_closed) {
                continue;
            }
            let generation = text_of(&action["cleanup_retry_generation"]);
            if !generation.is_empty() && (kind != "remove" || !crate::util::truthy(d.get("delete_requested"))
                    || d.get("cleanup_retry_generation").map(text_of).unwrap_or_default() != generation) {
                continue; // A queued action cannot consume a different, newer explicit request.
            }
            let explicit_retry = !generation.is_empty()
                && self.inner.lock().unwrap().cleanup_retry_generations.get(&link) != Some(&generation);
            if !explicit_retry && self.inner.lock().unwrap().retry.get(&link).is_some_and(|(at, _)| Instant::now() < *at) {
                continue;
            }
            let owner = text_of(&row["owner"]);
            let bot = owner.trim_start_matches("bot:").to_string();
            let task = text_of(&row["task_id"]);
            // A turn in the bot's main checkout or this task's lane may be using the worktree.
            let Some(guards) = host.try_locks(&bot, &format!("task:{task}")) else { continue };
            if !host.idle(&bot, &task) {
                continue;
            }
            if explicit_retry {
                let mut inner = self.inner.lock().unwrap();
                inner.retry.remove(&link);
                inner.cleanup_retry_generations.insert(link.clone(), generation);
            }
            self.maintaining.lock().unwrap().insert(bot.clone());
            let mut action_row = Map::new();
            let outcome = self.act_on(&host, &row, action, &kind, released, &mut action_row).await;
            if kind == "remove" && is_closed && !released && outcome.as_ref().is_ok_and(|s| s.0 == "removed") {
                let path = text_of(&row["path"]);
                let folder: Vec<_> = Path::new(&path).components().take(2).collect();
                let shared = rows.iter().enumerate().any(|(i, r)| {
                    i != index && r["state"] != "removed" && Path::new(&text_of(&r["path"])).components().take(2).eq(folder.iter().copied())
                });
                if !shared {
                    clear_task_scratch(&self.workspace, &path, &crate::env::process_environment()).await;
                }
            }
            self.maintaining.lock().unwrap().remove(&bot);
            drop(guards);
            match outcome {
                Ok(state) => {
                    rows[index]["state"] = json!(state.0);
                    if let Some(d) = state.1 {
                        rows[index]["detail_json"] = json!(d);
                    }
                    errors.remove(&link);
                    self.inner.lock().unwrap().retry.remove(&link);
                }
                Err(WtErr::Deferred(_)) => continue,
                Err(e) => {
                    failed.insert(link.clone());
                    if let Some(files) = action_row.get("skipped_files") {
                        let _ = self.client.patch(&format!("tasks/{task}/links/{link}"), &json!({"skipped_files": files})).await;
                    }
                    let text = match e {
                        WtErr::Os(_) => "Worktree action failed; history kept. Check repository access, network, disk space and permissions".to_string(),
                        other => format!("Worktree action failed; history kept. {}", other.text()),
                    };
                    errors.insert(link, text.chars().take(300).collect());
                }
            }
        }
        {
            let mut inner = self.inner.lock().unwrap();
            for key in failed {
                let failures = inner.retry.get(&key).map(|r| r.1).unwrap_or(0) + 1;
                let wait = (300.0 * 2f64.powi((failures - 1).min(7) as i32)).min(21600.0);
                inner.retry.insert(key, (Instant::now() + Duration::from_secs_f64(wait), failures));
            }
            inner.stats.retain(|k, _| ids.contains(k));
            inner.retry.retain(|k, _| ids.contains(k));
            inner.cleanup_retry_generations.retain(|k, _| ids.contains(k));
        }
        let mut stats = std::mem::take(&mut self.inner.lock().unwrap().stats);
        let env = crate::env::process_environment();
        let mut reports = vec![];
        for r in &rows {
            let mut report = inspect(&self.workspace, r, &env, Some(&mut stats)).await;
            // What this pass found in the tree is newer than the last failed action, which comes back once it clears.
            if let Some(e) = errors.get(&text_of(&report["link_id"])).filter(|_| report["error"].is_null()) {
                report["error"] = json!(e);
            }
            reports.push(report);
        }
        let mut inner = self.inner.lock().unwrap();
        inner.stats = stats;
        inner.errors = errors;
        inner.reports = reports;
    }

    /// One action on one row: (the link's new state, its new detail_json when a restore changed it).
    async fn act_on(&self, host: &Arc<dyn Host>, row: &Value, action: &Value, kind: &str, released: bool, action_row: &mut Map<String, Value>) -> R<(String, Option<String>)> {
        let owner = text_of(&row["owner"]);
        let bot = owner.trim_start_matches("bot:").to_string();
        let (link, task, name) = (text_of(&row["id"]), text_of(&row["task_id"]), text_of(&row["repo"]));
        let process = crate::env::process_environment();
        let mut env = crate::safegit::environment(&process);
        let mut repo = Map::new();
        let mut token = String::new();
        if !name.is_empty() {
            let granted = self.client.post(&format!("runners/me/worktrees/{link}/token"), &json!({}), None).await.map_err(|e| WtErr::msg(e.detail))?;
            if granted["configured"] == false {
                env = crate::safegit::machine_environment(&process);
                repo.insert("machine_git".into(), json!(true));
            } else if text_of(&granted["token"]).is_empty() {
                return Err(WtErr::msg("No repository credential for this worktree"));
            } else {
                token = text_of(&granted["token"]);
                env.extend(repositories::token_env(&token));
                let listed = self.client.get("runners/me/repositories").await.map_err(|e| WtErr::msg(e.detail))?;
                if let Some(found) = listed["repositories"].as_array().and_then(|a| a.iter().find(|r| text_of(&r["full_name"]).to_lowercase() == name.to_lowercase())) {
                    repo = found.as_object().cloned().unwrap_or_default();
                }
            }
        }
        for source in [row.as_object(), Some(&repo), action.as_object()].into_iter().flatten() {
            for (k, v) in source {
                action_row.insert(k.clone(), v.clone());
            }
        }
        action_row.insert("full_name".into(), json!(name));
        if (kind == "restore" || kind == "prepare") && !name.is_empty() && !crate::util::truthy(repo.get("machine_git")) {
            if let Some(repos) = host.repositories() {
                // Unavailable: worktree_base reports the cached mirror's age instead.
                if let Ok(refreshed) = repos.refresh_mirror(&name, &token).await {
                    action_row.insert("_mirror_refresh".into(), refreshed);
                }
            }
        }
        if kind == "prepare" {
            // A worktree reserved for a waiting task: checked out and set up here, which reports each step itself.
            let mut base_repo = repo.clone();
            base_repo.insert("full_name".into(), json!(name));
            if let Some(refreshed) = action_row.get("_mirror_refresh") {
                base_repo.insert("_mirror_refresh".into(), refreshed.clone());
            }
            let (head, target, base) = match prepare(&self.workspace, row, &mut base_repo, &env, &self.client).await {
                Err(WtErr::Msg(_, Some(detail))) if crate::util::truthy(repo.get("machine_git")) => return Err(WtErr::msg(repositories::machine_error(&name, &detail, &Env::new()))),
                other => other?,
            };
            // The report that follows is of the prepared worktree, not of the row read before it.
            let mut d = detail(row);
            for (k, v) in [("checkout_state", json!("ready")), ("setup_pending", json!(false)), ("expected_head", json!(head)), ("checkout_target", json!(target)), ("expected_base", json!(base))] {
                d.insert(k.into(), v);
            }
            return Ok(("present".into(), Some(Value::Object(d).to_string())));
        }
        let vault = host.vault(&bot);
        let client = self.client.clone();
        let (h, l, b, t) = (host.clone(), link.clone(), bot.clone(), task.clone());
        let still_closed = move || {
            let (client, h, l, b, t) = (client.clone(), h.clone(), l.clone(), b.clone(), t.clone());
            async move {
                let Ok(reply) = client.get("runners/me/worktrees").await else { return false };
                let Some(fresh) = reply["worktrees"].as_array().and_then(|a| a.iter().find(|r| text_of(&r["id"]) == l).cloned()) else { return false };
                let fd = detail(&fresh);
                h.idle(&b, &t) && (closed(&fresh) || (released && crate::util::truthy(fd.get("release_requested"))))
            }
        };
        let state = act(&self.workspace, action_row, kind, &env, &vault, still_closed).await?;
        let mut restore_pending = kind == "restore";
        let mut recorded = None;
        if restore_pending {
            let mut base_repo = repo.clone();
            base_repo.insert("full_name".into(), json!(name));
            recorded = Some(record_restored(&self.workspace, row, &mut base_repo, &env).await?);
        }
        let mut body = Map::new();
        body.insert("state".into(), json!(if restore_pending { "pending" } else { state }));
        body.insert("cleanup".into(), json!(kind == "remove"));
        body.insert("setup_pending".into(), json!(restore_pending));
        if let Some((head, target, base)) = &recorded {
            body.insert("checkout_state".into(), json!("checkout_ready"));
            body.insert("expected_head".into(), json!(head));
            body.insert("checkout_target".into(), json!(target));
            body.insert("expected_base".into(), json!(base));
        }
        let mut kept = Map::new();
        for k in ["snapshot_skipped", "restore_source"] {
            if let Some(v) = action_row.get(k) {
                kept.insert(k.into(), v.clone());
            }
        }
        body.extend(kept.clone());
        let path = format!("tasks/{task}/links/{link}");
        if let Err(e) = self.client.patch(&path, &Value::Object(body)).await {
            if !(restore_pending && server_lacks_checkout_fields(&e)) {
                return Err(WtErr::msg(e.detail));
            }
            // A server from before checkout records: report the restore as it knew it, setup pending.
            let mut old = json!({"state": state, "cleanup": false, "setup_pending": true});
            old.as_object_mut().unwrap().extend(kept);
            self.client.patch(&path, &old).await.map_err(|e| WtErr::msg(e.detail))?;
            restore_pending = false;
        }
        if restore_pending {
            let (head, target, base) = recorded.unwrap();
            let mut d = detail(row);
            d.insert("checkout_state".into(), json!("checkout_ready"));
            d.insert("setup_pending".into(), json!(true));
            d.insert("expected_head".into(), json!(head));
            d.insert("checkout_target".into(), json!(target));
            d.insert("expected_base".into(), json!(base));
            return Ok(("pending".into(), Some(Value::Object(d).to_string())));
        }
        Ok((state.into(), None))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Folder(PathBuf);
    impl Drop for Folder {
        fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); }
    }

    /// A server whose `runners/me/worktrees` answer is the one row given, whatever the request.
    async fn serve_rows(row: Arc<Mutex<Value>>) -> (String, tokio::task::JoinHandle<()>) {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let server = tokio::spawn(async move {
            loop {
                let Ok((mut socket, _)) = listener.accept().await else { return };
                let mut request = Vec::new();
                let mut buffer = [0u8; 8192];
                while !request.windows(4).any(|part| part == b"\r\n\r\n") {
                    let n = socket.read(&mut buffer).await.unwrap_or(0);
                    if n == 0 { break; }
                    request.extend_from_slice(&buffer[..n]);
                    assert!(request.len() <= 65536);
                }
                let body = json!({"worktrees": [row.lock().unwrap().clone()]}).to_string();
                let response = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
                let _ = socket.write_all(response.as_bytes()).await;
                let _ = socket.shutdown().await;
            }
        });
        (url, server)
    }

    #[tokio::test]
    async fn a_fresh_inspection_error_wins_over_the_remembered_action_error() {
        struct IdleHost;
        impl Host for IdleHost {
            fn idle(&self, _: &str, _: &str) -> bool { true }
            fn try_locks(&self, _: &str, _: &str) -> Option<Box<dyn Send>> { Some(Box::new(())) }
            fn vault(&self, _: &str) -> Vec<String> { vec![] }
            fn retain_vault(&self, _: &HashSet<String>) {}
            fn repositories(&self) -> Option<Arc<repositories::Repositories>> { None }
        }
        let root = std::env::temp_dir().join(format!("tico-inspect-error-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&root).unwrap();
        let folder = Folder(root.canonicalize().unwrap());
        let row = Arc::new(Mutex::new(json!({
            "id": "link", "task_id": "task", "path": "tasks/task/product", "repo": "", "branch": "task",
            "owner": "bot:ops", "task_status": "open", "bot_state": "active", "state": "present", "detail_json": "{}"
        })));
        let (url, server) = serve_rows(row).await;
        let manager = Arc::new(Worktrees::new(&folder.0, Client::new(&url, "fixture", 5, 0).unwrap()));
        let failed = "Worktree action failed; history kept. Restore path already exists and is not a worktree";
        manager.inner.lock().unwrap().errors.insert("link".into(), failed.into());
        manager.clone().sync(Arc::new(IdleHost), vec![]).await;
        assert_eq!(manager.latest()[0]["error"], json!(failed), "a tree that inspects cleanly still reports the failed action");

        std::fs::create_dir_all(folder.0.join("tasks/task/product")).unwrap();
        manager.clone().sync(Arc::new(IdleHost), vec![]).await;
        assert_eq!(manager.latest()[0]["error"], json!("Could not find it: Tracked path is not a Git worktree; left as it is"));
        server.abort();
    }

    #[tokio::test]
    async fn explicit_cleanup_retry_reaches_preservation_once_despite_local_backoff() {
        use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

        struct RetryHost { idle: AtomicBool, locks_available: AtomicBool, attempts: AtomicUsize }
        impl Host for RetryHost {
            fn idle(&self, _: &str, _: &str) -> bool { self.idle.load(Ordering::SeqCst) }
            fn try_locks(&self, _: &str, _: &str) -> Option<Box<dyn Send>> {
                self.locks_available.load(Ordering::SeqCst).then(|| Box::new(()) as Box<dyn Send>)
            }
            fn vault(&self, _: &str) -> Vec<String> {
                self.attempts.fetch_add(1, Ordering::SeqCst);
                vec![]
            }
            fn retain_vault(&self, _: &HashSet<String>) {}
            fn repositories(&self) -> Option<Arc<repositories::Repositories>> { None }
        }
        let root = std::env::temp_dir().join(format!("tico-cleanup-retry-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(root.join("tasks/task/product")).unwrap();
        let folder = Folder(root.canonicalize().unwrap());
        let wanted = folder.0.join("tasks/task/product/wanted.txt");
        std::fs::write(&wanted, "unfinished work").unwrap();
        let generation = uuid::Uuid::new_v4().to_string();
        let row = Arc::new(Mutex::new(json!({
            "id": "link", "task_id": "task", "path": "tasks/task/product", "repo": "", "branch": "task",
            "owner": "bot:ops", "task_status": "closed", "bot_state": "active", "state": "present",
            "detail_json": json!({"delete_requested": true, "cleanup_retry_generation": generation}).to_string()
        })));
        let (url, server) = serve_rows(row.clone()).await;
        let manager = Arc::new(Worktrees::new(&folder.0, Client::new(&url, "fixture", 5, 0).unwrap()));
        let host = Arc::new(RetryHost { idle: AtomicBool::new(true), locks_available: AtomicBool::new(true), attempts: AtomicUsize::new(0) });
        manager.inner.lock().unwrap().retry.insert("link".into(), (Instant::now() + Duration::from_secs(21600), 8));
        let legacy = json!({"link_id": "link", "action": "remove"});
        manager.clone().sync(host.clone(), vec![legacy]).await;
        assert_eq!(host.attempts.load(Ordering::SeqCst), 0, "ordinary heartbeats keep local backoff");
        let action = json!({"link_id": "link", "action": "remove", "cleanup_retry_generation": generation});
        manager.clone().sync(host.clone(), vec![action.clone()]).await;
        assert_eq!(host.attempts.load(Ordering::SeqCst), 1, "a fresh explicit retry reaches the real preservation checks");
        assert!(manager.inner.lock().unwrap().errors["link"].contains("Repository not identified; kept worktree"));
        assert_eq!(std::fs::read_to_string(&wanted).unwrap(), "unfinished work");
        manager.clone().sync(host.clone(), vec![action.clone()]).await;
        assert_eq!(host.attempts.load(Ordering::SeqCst), 1, "the same generation cannot bypass new failure backoff");

        let next = uuid::Uuid::new_v4().to_string();
        row.lock().unwrap()["detail_json"] = json!(json!({"delete_requested": true, "cleanup_retry_generation": next}).to_string());
        manager.clone().sync(host.clone(), vec![action]).await;
        assert_eq!(host.attempts.load(Ordering::SeqCst), 1, "a stale queued generation cannot reset a newer request");
        let next_action = json!({"link_id": "link", "action": "remove", "cleanup_retry_generation": next});
        host.locks_available.store(false, Ordering::SeqCst);
        manager.clone().sync(host.clone(), vec![next_action.clone()]).await;
        assert_eq!(host.attempts.load(Ordering::SeqCst), 1, "a retry still waits for the checkout locks");
        host.locks_available.store(true, Ordering::SeqCst);
        host.idle.store(false, Ordering::SeqCst);
        manager.clone().sync(host.clone(), vec![next_action.clone()]).await;
        assert_eq!(host.attempts.load(Ordering::SeqCst), 1, "a retry still waits for the bot to be idle");
        host.idle.store(true, Ordering::SeqCst);
        manager.clone().sync(host.clone(), vec![next_action.clone()]).await;
        assert_eq!(host.attempts.load(Ordering::SeqCst), 2, "the idle deferral did not consume the request");
        manager.clone().sync(host.clone(), vec![next_action]).await;
        assert_eq!(host.attempts.load(Ordering::SeqCst), 2);
        assert_eq!(std::fs::read_to_string(&wanted).unwrap(), "unfinished work");
        server.abort();
    }

    #[test]
    fn worktree_paths_stay_inside_the_workspace_and_outside_repos() {
        let root = std::env::temp_dir().join(format!("tico-wt-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(root.join("repos/x")).unwrap();
        std::fs::create_dir_all(root.join("real")).unwrap();
        std::os::unix::fs::symlink(root.join("real"), root.join("link")).unwrap();
        assert!(safe_path(&root, "work/product").is_ok());
        assert_eq!(safe_path(&root, "../out").unwrap_err(), "Use a worktree inside the team workspace");
        assert_eq!(safe_path(&root, "/abs").unwrap_err(), "Use a worktree inside the team workspace");
        assert_eq!(safe_path(&root, "repos/x/y").unwrap_err(), "Worktree must stay outside repos");
        assert_eq!(safe_path(&root, "link").unwrap_err(), "Use a worktree inside the team workspace");
        assert_eq!(safe_path(&root, "link/inner").unwrap_err(), "Worktree path must not contain symlinks");
        assert!(build_file("web/node_modules/x.js") && build_file("a/.terraform/providers/p") && !build_file("src/main.rs"));
        assert!(build_file("app/.build/arm64-apple-macosx/debug/App.o") && build_file("app/.swiftpm/xcode/package.xcworkspace/x"));
        assert!(build_file("ios/DerivedData/Build/Products/x") && build_file("app/gen/schemas/desktop-schema.json"));
        assert!(!build_file("app/gen/handwritten.rs") && !build_file("gen/main.go") && !build_file(".env.local"));
        assert!(!build_file("app/gen/android/keystore.properties") && !build_file("app/gen/apple/ExportOptions.plist"));
        assert!(secret_name(".env.local") && secret_name("server.pem") && !secret_name("main.rs"));
        assert_eq!(wip_branch(&json!({"task_id": "0123456789", "id": "abcdefgh"})), "wip/01234567-abcdef");
        let _ = std::fs::remove_dir_all(&root);
    }

    const UNRESOLVED: &str = "kept: merge in progress with resolved or unrecognised conflicts; commit or abort it to free this worktree";

    fn test_env() -> Env {
        std::env::vars().collect()
    }

    /// A git command in `dir` with a fixed identity; panics with git's stderr when it fails.
    fn sh(dir: &Path, args: &[&str]) -> String {
        let out = std::process::Command::new("git")
            .arg("-C").arg(dir)
            .args(["-c", "user.name=Sam", "-c", "user.email=sam@example.com"])
            .args(args)
            .output()
            .unwrap();
        assert!(out.status.success(), "git {args:?}: {}", String::from_utf8_lossy(&out.stderr));
        String::from_utf8_lossy(&out.stdout).trim().to_string()
    }

    /// Whether a git command in `dir` succeeds (a merge that conflicts exits 1).
    fn sh_ok(dir: &Path, args: &[&str]) -> bool {
        std::process::Command::new("git")
            .arg("-C").arg(dir)
            .args(["-c", "user.name=Sam", "-c", "user.email=sam@example.com"])
            .args(args)
            .output()
            .unwrap()
            .status
            .success()
    }

    struct Merge {
        workspace: PathBuf,
        remote: PathBuf,
        path: PathBuf,
        row: Map<String, Value>,
    }

    /// A task worktree whose merge of main is left in progress: main changes each `conflicts` name, deletes each
    /// `deleted` name and adds a clean file, `mainonly`. The task's own commit is pushed, so nothing is unpushed.
    fn merge_in_progress(folder: &Path, conflicts: &[&str], deleted: &[&str]) -> Merge {
        merge_in_progress_with(folder, conflicts, deleted, &[])
    }

    /// As `merge_in_progress`, and main also adds each `copies` name with the content of the task's `other` file.
    fn merge_in_progress_with(folder: &Path, conflicts: &[&str], deleted: &[&str], copies: &[&str]) -> Merge {
        let (workspace, remote, clone) = (folder.join("workspace"), folder.join("remote.git"), folder.join("clone"));
        let base = workspace.join("repos/org__product");
        let path = workspace.join("tasks/12345678/org__product");
        std::fs::create_dir_all(workspace.join("repos")).unwrap();
        std::fs::create_dir_all(workspace.join("tasks/12345678")).unwrap();
        let names: Vec<&str> = conflicts.iter().chain(deleted).copied().collect();
        sh(folder, &["init", "-q", "--bare", "--initial-branch=main", remote.to_str().unwrap()]);
        sh(folder, &["clone", "-q", remote.to_str().unwrap(), clone.to_str().unwrap()]);
        for name in &names {
            std::fs::write(clone.join(name), format!("base {name}\n")).unwrap();
        }
        sh(&clone, &["add", "-A"]);
        sh(&clone, &["commit", "-qm", "Base"]);
        sh(&clone, &["push", "-q", "origin", "main"]);
        sh(folder, &["clone", "-q", remote.to_str().unwrap(), base.to_str().unwrap()]);
        sh(&base, &["worktree", "add", "-q", "-b", "tico/12345678-build", path.to_str().unwrap(), "origin/main"]);
        for name in &names {
            std::fs::write(path.join(name), format!("task {name}\n")).unwrap();
        }
        std::fs::write(path.join("other"), "other\n").unwrap();
        sh(&path, &["add", "-A"]);
        sh(&path, &["commit", "-qm", "Task"]);
        sh(&path, &["push", "-q", "origin", "tico/12345678-build"]);
        for name in conflicts {
            std::fs::write(clone.join(name), format!("main {name}\n")).unwrap();
        }
        for name in deleted {
            std::fs::remove_file(clone.join(name)).unwrap();
        }
        std::fs::write(clone.join("mainonly"), "main only\n").unwrap();
        if !copies.is_empty() {
            std::fs::write(clone.join("other"), "other\n").unwrap();
        }
        for name in copies {
            std::fs::write(clone.join(name), "other\n").unwrap();
        }
        sh(&clone, &["add", "-A"]);
        sh(&clone, &["commit", "-qm", "Main"]);
        sh(&clone, &["push", "-q", "origin", "main"]);
        sh(&base, &["fetch", "-q", "--no-tags", "origin"]);
        assert!(!sh_ok(&path, &["merge", "-q", "--no-edit", "origin/main"]), "the merge must conflict");
        sh(&path, &["rev-parse", "--verify", "MERGE_HEAD"]);
        let row = json!({
            "id": "link1", "link_id": "link1", "task_id": "12345678abcdef", "path": "tasks/12345678/org__product",
            "repo": "org/product", "branch": "tico/12345678-build", "default_branch": "main", "state": "present"
        });
        Merge { workspace, remote, path, row: row.as_object().unwrap().clone() }
    }

    fn temp_folder(name: &str) -> Folder {
        let root = std::env::temp_dir().join(format!("tico-merge-{name}-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&root).unwrap();
        Folder(root.canonicalize().unwrap())
    }

    #[tokio::test]
    async fn a_mid_merge_tree_with_untouched_conflicts_is_saved_and_removed() {
        let folder = temp_folder("saved");
        let mut m = merge_in_progress(&folder.0, &["file", "second"], &[]);
        std::fs::write(m.path.join("other"), "edited\n").unwrap();
        let state = act(&m.workspace, &mut m.row, "remove", &test_env(), &[], || async { true }).await.unwrap();
        assert_eq!(state, "removed");
        assert!(!m.path.exists());
        let wip = wip_branch(&Value::Object(m.row.clone()));
        assert_eq!(sh(&m.remote, &["show", &format!("{wip}:other")]), "edited");
        assert_eq!(sh(&m.remote, &["show", &format!("{wip}:file")]), "task file");
        assert_eq!(sh(&m.remote, &["show", &format!("{wip}:second")]), "task second");
        assert!(!sh(&m.remote, &["ls-tree", "--name-only", &wip]).lines().any(|name| name == "mainonly"));
    }

    #[tokio::test]
    async fn a_conflict_resolved_by_hand_keeps_the_merge_and_the_worktree() {
        let folder = temp_folder("resolved");
        let mut m = merge_in_progress(&folder.0, &["file"], &[]);
        std::fs::write(m.path.join("file"), "resolved by hand\n").unwrap();
        let err = act(&m.workspace, &mut m.row, "remove", &test_env(), &[], || async { true }).await.unwrap_err();
        assert_eq!(err.text(), UNRESOLVED);
        assert_eq!(std::fs::read_to_string(m.path.join("file")).unwrap(), "resolved by hand\n");
        sh(&m.path, &["rev-parse", "--verify", "MERGE_HEAD"]);
        assert_eq!(sh(&m.path, &["symbolic-ref", "--short", "HEAD"]), "tico/12345678-build");
        assert!(!sh(&m.remote, &["show-ref", "--heads"]).contains("wip/"));
    }

    #[tokio::test]
    async fn a_resolution_staged_beside_a_conflict_is_kept() {
        let folder = temp_folder("staged");
        let mut m = merge_in_progress(&folder.0, &["file", "second"], &[]);
        std::fs::write(m.path.join("file"), "resolved and staged\n").unwrap();
        sh(&m.path, &["add", "file"]);
        let err = act(&m.workspace, &mut m.row, "remove", &test_env(), &[], || async { true }).await.unwrap_err();
        assert_eq!(err.text(), UNRESOLVED);
        sh(&m.path, &["rev-parse", "--verify", "MERGE_HEAD"]);
        assert_eq!(std::fs::read_to_string(m.path.join("file")).unwrap(), "resolved and staged\n");
        assert!(sh(&m.path, &["diff", "--cached", "--name-only"]).lines().any(|name| name == "file"));
    }

    #[tokio::test]
    async fn a_modify_delete_conflict_without_markers_is_kept() {
        let folder = temp_folder("modify-delete");
        let mut m = merge_in_progress(&folder.0, &["second"], &["file"]);
        let err = act(&m.workspace, &mut m.row, "remove", &test_env(), &[], || async { true }).await.unwrap_err();
        assert_eq!(err.text(), UNRESOLVED);
        sh(&m.path, &["rev-parse", "--verify", "MERGE_HEAD"]);
    }

    #[tokio::test]
    async fn a_release_tree_mid_merge_is_kept_and_never_aborted() {
        let folder = temp_folder("release");
        let mut m = merge_in_progress(&folder.0, &["file"], &[]);
        m.row.insert("release".into(), json!(true));
        m.row.insert("prs_finished".into(), json!(true));
        let err = act(&m.workspace, &mut m.row, "remove", &test_env(), &[], || async { true }).await.unwrap_err();
        assert_eq!(err.text(), "kept: merge in progress; finish or abort it to free this worktree");
        sh(&m.path, &["rev-parse", "--verify", "MERGE_HEAD"]);
        assert!(std::fs::read_to_string(m.path.join("file")).unwrap().contains("<<<<<<< "));
    }

    #[tokio::test]
    async fn another_operation_beside_a_merge_is_kept() {
        for marker in ["CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"] {
            let folder = temp_folder(&format!("op-{marker}"));
            let mut m = merge_in_progress(&folder.0, &["file"], &[]);
            let gitdir = PathBuf::from(sh(&m.path, &["rev-parse", "--path-format=absolute", "--git-dir"]));
            if marker.starts_with("rebase") {
                std::fs::create_dir_all(gitdir.join(marker)).unwrap();
            } else {
                std::fs::write(gitdir.join(marker), format!("{}\n", sh(&m.path, &["rev-parse", "HEAD"]))).unwrap();
            }
            let err = act(&m.workspace, &mut m.row, "remove", &test_env(), &[], || async { true }).await.unwrap_err();
            assert_eq!(err.text(), UNRESOLVED, "{marker}");
            sh(&m.path, &["rev-parse", "--verify", "MERGE_HEAD"]);
        }
    }

    #[tokio::test]
    async fn a_staged_deletion_paired_with_an_identical_addition_is_kept() {
        let folder = temp_folder("deletion-copy");
        let mut m = merge_in_progress_with(&folder.0, &["file"], &[], &["copy"]);
        sh(&m.path, &["rm", "-q", "other"]);
        let err = act(&m.workspace, &mut m.row, "remove", &test_env(), &[], || async { true }).await.unwrap_err();
        assert_eq!(err.text(), UNRESOLVED);
        sh(&m.path, &["rev-parse", "--verify", "MERGE_HEAD"]);
        assert!(!sh(&m.path, &["ls-files"]).lines().any(|name| name == "other"));
    }

    #[tokio::test]
    async fn a_staged_addition_the_merge_did_not_make_is_kept() {
        let folder = temp_folder("addition");
        let mut m = merge_in_progress(&folder.0, &["file"], &[]);
        std::fs::write(m.path.join("extra"), "bot file\n").unwrap();
        sh(&m.path, &["add", "extra"]);
        let err = act(&m.workspace, &mut m.row, "remove", &test_env(), &[], || async { true }).await.unwrap_err();
        assert_eq!(err.text(), UNRESOLVED);
        sh(&m.path, &["rev-parse", "--verify", "MERGE_HEAD"]);
        assert_eq!(sh(&m.path, &["show", ":extra"]), "bot file");
    }

    #[tokio::test]
    async fn an_unstaged_edit_to_a_path_the_merge_changed_is_kept() {
        let folder = temp_folder("unstaged");
        let mut m = merge_in_progress(&folder.0, &["file"], &[]);
        std::fs::write(m.path.join("mainonly"), "edited after the merge\n").unwrap();
        let err = act(&m.workspace, &mut m.row, "remove", &test_env(), &[], || async { true }).await.unwrap_err();
        assert_eq!(err.text(), UNRESOLVED);
        sh(&m.path, &["rev-parse", "--verify", "MERGE_HEAD"]);
        assert_eq!(std::fs::read_to_string(m.path.join("mainonly")).unwrap(), "edited after the merge\n");
    }

    #[tokio::test]
    async fn every_conflict_resolved_and_staged_with_the_merge_open_is_kept() {
        let folder = temp_folder("all-resolved");
        let mut m = merge_in_progress(&folder.0, &["file", "second"], &[]);
        std::fs::write(m.path.join("file"), "resolved\n").unwrap();
        std::fs::write(m.path.join("second"), "resolved\n").unwrap();
        sh(&m.path, &["add", "-A"]);
        let err = act(&m.workspace, &mut m.row, "remove", &test_env(), &[], || async { true }).await.unwrap_err();
        assert_eq!(err.text(), UNRESOLVED);
        sh(&m.path, &["rev-parse", "--verify", "MERGE_HEAD"]);
    }

    #[test]
    fn the_merge_result_tree_is_read_only_when_git_wrote_one() {
        let oid = "0123456789abcdef0123456789abcdef01234567";
        let out = |code: i32, stdout: &str| Out { code, stdout: stdout.into(), stderr: String::new() };
        assert_eq!(merge_tree_oid(&out(129, "usage: git merge-tree")), None);
        assert_eq!(merge_tree_oid(&out(0, "not a tree\n")), None);
        assert_eq!(merge_tree_oid(&out(0, &format!("{oid}\n"))).as_deref(), Some(oid));
        assert_eq!(merge_tree_oid(&out(1, &format!("{oid}\nCONFLICT (content): file\n"))).as_deref(), Some(oid));
    }

    async fn resolution_beside_a_conflict_is_kept(side: &str, kept: &str) {
        let folder = temp_folder(&format!("side-{}", side.trim_start_matches("--")));
        let mut m = merge_in_progress(&folder.0, &["file", "second"], &[]);
        sh(&m.path, &["checkout", side, "--", "file"]);
        sh(&m.path, &["add", "file"]);
        let err = act(&m.workspace, &mut m.row, "remove", &test_env(), &[], || async { true }).await.unwrap_err();
        assert_eq!(err.text(), UNRESOLVED);
        sh(&m.path, &["rev-parse", "--verify", "MERGE_HEAD"]);
        assert_eq!(sh(&m.path, &["symbolic-ref", "--short", "HEAD"]), "tico/12345678-build");
        assert_eq!(std::fs::read_to_string(m.path.join("file")).unwrap(), format!("{kept}\n"));
        assert_eq!(sh(&m.path, &["show", ":file"]), kept);
        assert!(!sh(&m.remote, &["show-ref", "--heads"]).contains("wip/"));
    }

    #[tokio::test]
    async fn a_resolution_taking_our_side_beside_a_conflict_is_kept() {
        resolution_beside_a_conflict_is_kept("--ours", "task file").await;
    }

    #[tokio::test]
    async fn a_resolution_taking_main_beside_a_conflict_is_kept() {
        resolution_beside_a_conflict_is_kept("--theirs", "main file").await;
    }

    #[tokio::test]
    async fn a_line_ending_only_edit_to_a_conflicted_file_is_kept() {
        let folder = temp_folder("crlf");
        let mut m = merge_in_progress(&folder.0, &["file"], &[]);
        let bytes = std::fs::read(m.path.join("file")).unwrap();
        let crlf: Vec<u8> = bytes.iter().flat_map(|&b| if b == b'\n' { vec![b'\r', b'\n'] } else { vec![b] }).collect();
        std::fs::write(m.path.join("file"), crlf).unwrap();
        let err = act(&m.workspace, &mut m.row, "remove", &test_env(), &[], || async { true }).await.unwrap_err();
        assert_eq!(err.text(), UNRESOLVED);
        sh(&m.path, &["rev-parse", "--verify", "MERGE_HEAD"]);
        assert!(std::fs::read(m.path.join("file")).unwrap().windows(2).any(|w| w == b"\r\n"));
    }

    #[tokio::test]
    async fn a_staged_mode_change_on_a_path_the_merge_touched_is_kept() {
        let folder = temp_folder("mode");
        let mut m = merge_in_progress(&folder.0, &["file"], &[]);
        sh(&m.path, &["update-index", "--chmod=+x", "mainonly"]);
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(m.path.join("mainonly"), std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        let err = act(&m.workspace, &mut m.row, "remove", &test_env(), &[], || async { true }).await.unwrap_err();
        assert_eq!(err.text(), UNRESOLVED);
        sh(&m.path, &["rev-parse", "--verify", "MERGE_HEAD"]);
        assert!(sh(&m.path, &["diff", "--cached", "--summary"]).contains("mode 100755"));
    }
}
