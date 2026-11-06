//! The clean-up a computer runs on its own when its workspace volume runs low (docs/worktrees.md#low-disk). It
//! removes only what nothing can still need, on the workspace's own volume, and stops once the volume is back above
//! the mark:
//!
//! 1. finished tasks' worktrees, through the ordinary cleanup the server asked for (worktrees.rs `act`, which saves
//!    unpushed or uncommitted work first and keeps a tree it cannot save), without the computer's own retry wait;
//! 2. rebuildable folders (worktrees.rs `rebuildable`: Cargo `target`, `node_modules`, pytest caches, virtual
//!    environments) in task folders whose every worktree is a finished task's, owned by a bot, not kept for a
//!    person and not in use, and never one Git tracks or a checkout holding anything unsaved;
//! 3. on Linux, top-level entries of the temp folder untouched for a day that no process holds open and that hold
//!    no Git checkout;
//! 4. `git worktree prune` and `git gc --auto` in Tico's base clones, one at a time under the lock fetches take.
//!
//! Never touched: an open task's folder, a folder the server lists nothing for, package caches in the home folder,
//! a git checkout itself, bot repositories, the runner's state and configuration, credentials, Docker, and
//! anything on another volume or under a mount point. Nothing is followed through a symlink, and every path is
//! checked again just before it goes. Each removal is logged with its class and size, and the last run rides on
//! the readiness report (`disk_cleanup`). What is left is for the machine's disk to grow (cloud machines do it on
//! their own) or for a person.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::os::unix::fs::{FileTypeExt, MetadataExt};
use std::path::{Component, Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::client::Client;
use crate::env::Env;
use crate::repositories::GB;
use crate::util::{log, text_of, truthy};
use crate::worktrees::{self, Host, Worktrees};

/// At most one clean-up this often: a volume it could not free is not walked again every heartbeat.
pub const CLEAN_EVERY: Duration = Duration::from_secs(30 * 60);
/// A temp entry this long untouched is no longer any running tool's.
const OLD_S: f64 = 24.0 * 3600.0;
/// Free space the clean-up aims for above the low mark, so it does not run again an hour later.
const HEADROOM: u64 = 5 * (GB as u64);
/// `git gc` writes new packs before it drops old ones.
const GC_FLOOR: u64 = 2 * (GB as u64);
/// How long the clean-up waits for a worktree pass, before and after asking for finished trees to go.
const PASS_WAIT: Duration = Duration::from_secs(10 * 60);
/// `gc` stays in the foreground, so the base clone's lock covers all of it and a detached repack does not keep
/// writing after the clean-up measured what it freed.
pub const GC_ARGS: &[&str] = &["-c", "gc.autoDetach=false", "gc", "--auto", "--quiet"];

/// Free bytes under which the clean-up runs: 15% of the volume or 10 GB, whichever is larger, but never above a
/// quarter of a small volume or 40 GB of a large one.
pub fn low_mark(total: u64) -> u64 {
    let gb = GB as u64;
    (total / 100 * 15).max(10 * gb).min(total / 4).min(40 * gb)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Class {
    Tmp,
    FinishedTaskCache,
    Git,
}

impl Class {
    pub fn name(self) -> &'static str {
        match self {
            Class::Tmp => "tmp",
            Class::FinishedTaskCache => "finished_task_cache",
            Class::Git => "git",
        }
    }
}

/// What a task folder (`tasks/<task-short>`) is to the clean-up, from the worktree links the server lists there.
/// Only `Closed` is cleaned; a folder the server lists nothing for is not in the map and is not cleaned either.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FolderState {
    /// A turn could be in it: one of its bots in its main lane or on its task, or any turn on its task.
    Running,
    /// A person's checkout, or a tree the cleanup kept for a person (a refused or given-up cleanup).
    Kept,
    /// Its task is open (a removed link on an open task included).
    Open,
    /// Every link in it is a finished task's, owned by a bot that is not running there.
    Closed,
}

impl FolderState {
    fn rank(self) -> u8 {
        match self {
            FolderState::Running => 4,
            FolderState::Kept => 3,
            FolderState::Open => 2,
            FolderState::Closed => 1,
        }
    }
}

#[derive(Debug, Clone)]
pub struct Folder {
    pub state: FolderState,
    /// (bot, task) of each link in the folder, whose locks are held while anything in it goes.
    pub owners: Vec<(String, String)>,
    /// Each link's path, whose hub CLI task lock (worktrees.rs `locked`) is held too.
    pub paths: Vec<String>,
}

/// Each task folder the server's worktree rows name, and whether a turn could be in it (`busy(bot, task)`).
pub fn folder_states(rows: &[Value], busy: impl Fn(&str, &str) -> bool) -> HashMap<String, Folder> {
    let mut out: HashMap<String, Folder> = HashMap::new();
    for row in rows {
        let path = text_of(&row["path"]);
        let mut parts = Path::new(&path).components();
        let (Some(Component::Normal(top)), Some(Component::Normal(name))) = (parts.next(), parts.next()) else { continue };
        if top != "tasks" {
            continue;
        }
        let owner = text_of(&row["owner"]);
        let bot = owner.strip_prefix("bot:").unwrap_or("").to_string();
        let task = text_of(&row["task_id"]);
        let d = worktrees::detail(row);
        let state = if bot.is_empty() {
            FolderState::Kept
        } else if busy(&bot, &task) {
            FolderState::Running
        } else if !worktrees::closed(row) {
            FolderState::Open
        } else if truthy(d.get("cleanup_gave_up")) || truthy(d.get("error")) {
            FolderState::Kept
        } else {
            FolderState::Closed
        };
        let folder = out.entry(name.to_string_lossy().into_owned()).or_insert(Folder { state, owners: vec![], paths: vec![] });
        if state.rank() > folder.state.rank() {
            folder.state = state;
        }
        if !bot.is_empty() && !folder.owners.contains(&(bot.clone(), task.clone())) {
            folder.owners.push((bot, task));
        }
        folder.paths.push(path);
    }
    out
}

/// Where the clean-up may look, and what it must leave.
#[derive(Debug, Clone, Default)]
pub struct Scope {
    pub workspace: PathBuf,
    /// None when the server's worktree rows could not be read: then no task folder is touched.
    pub folders: Option<HashMap<String, Folder>>,
    pub temps: Vec<PathBuf>,
    /// Paths a process holds open (files, working folders, mapped files); None where they cannot be read, and then
    /// the temp folder is left alone.
    pub held: Option<HashSet<PathBuf>>,
    /// Folders never removed nor removed from: the runner's state and configuration.
    pub keep: Vec<PathBuf>,
    pub now: f64,
}

#[derive(Debug, Clone)]
pub struct Candidate {
    pub class: Class,
    pub path: PathBuf,
    /// Bytes the removal frees, as planned.
    pub bytes: u64,
    /// Newest modification time in it (epoch seconds).
    pub newest: f64,
    /// The task folder it is in, whose owners' locks are held while it goes.
    pub folder: Option<String>,
    /// The git checkout it is inside, which Git must show holds nothing of it and nothing unsaved.
    pub checkout: Option<PathBuf>,
}

#[derive(Default)]
struct Tree {
    /// What removing it frees: blocks of files no other hard link keeps.
    bytes: u64,
    newest: f64,
    /// A socket, pipe or device: something live, never a leftover.
    special: bool,
    /// Something another user owns, which this user cannot remove anyway.
    foreign: bool,
    /// It is on another volume, or a folder in it is (a mount point): not descended into, never removed.
    mount: bool,
    /// A `.git` in it: a checkout, never a leftover.
    git: bool,
}

impl Tree {
    /// Nothing in it rules the removal out (`foreign` only matters for the temp folder).
    fn plain(&self) -> bool {
        !self.special && !self.mount && !self.git
    }
}

fn mtime(meta: &std::fs::Metadata) -> f64 {
    meta.mtime() as f64 + meta.mtime_nsec() as f64 / 1e9
}

/// The volume `path` itself is on, the path not followed if a symlink.
fn device(path: &Path) -> Option<u64> {
    path.symlink_metadata().ok().map(|m| m.dev())
}

/// Size and age of everything under `root` on volume `dev`, symlinks never followed (a symlink counts as itself
/// only) and folders on another volume never entered.
fn walk(root: &Path, dev: u64) -> Tree {
    let mut tree = Tree::default();
    let uid = unsafe { libc::geteuid() };
    let Ok(meta) = root.symlink_metadata() else { return tree };
    let mut stack = vec![(root.to_path_buf(), meta)];
    while let Some((path, meta)) = stack.pop() {
        let kind = meta.file_type();
        tree.newest = tree.newest.max(mtime(&meta));
        tree.foreign |= meta.uid() != uid;
        tree.git |= path != root && path.file_name().is_some_and(|n| n == ".git");
        if meta.dev() != dev {
            tree.mount = true;
            continue;
        }
        if kind.is_socket() || kind.is_fifo() || kind.is_block_device() || kind.is_char_device() {
            tree.special = true;
        } else if kind.is_file() {
            if meta.nlink() <= 1 {
                tree.bytes += meta.blocks() * 512;
            }
        } else if kind.is_dir() {
            let Ok(entries) = std::fs::read_dir(&path) else { continue };
            for entry in entries.flatten() {
                if let Ok(m) = entry.path().symlink_metadata() {
                    stack.push((entry.path(), m));
                }
            }
        }
    }
    tree
}

fn is_symlink(p: &Path) -> bool {
    p.symlink_metadata().is_ok_and(|m| m.file_type().is_symlink())
}

/// `root` itself, resolved, when it is a real folder that is not a symlink.
fn real_root(root: &Path) -> Option<PathBuf> {
    if is_symlink(root) || !root.is_dir() {
        return None;
    }
    root.canonicalize().ok()
}

/// `relative` under `root` with no component a symlink, resolved; None otherwise.
fn under(root: &Path, relative: &str) -> Option<PathBuf> {
    let mut path = root.to_path_buf();
    for part in Path::new(relative).components() {
        let Component::Normal(name) = part else { return None };
        path.push(name);
        if is_symlink(&path) {
            return None;
        }
    }
    path.is_dir().then_some(path)
}

fn kept(path: &Path, keep: &[PathBuf]) -> bool {
    keep.iter().any(|k| k.starts_with(path) || path.starts_with(k))
}

fn held_under(path: &Path, held: &HashSet<PathBuf>) -> bool {
    held.iter().any(|h| h.starts_with(path))
}

/// The workspace folder and its volume.
fn workspace_device(scope: &Scope) -> Option<(PathBuf, u64)> {
    let ws = real_root(&scope.workspace)?;
    let dev = device(&ws)?;
    Some((ws, dev))
}

/// The nearest git checkout holding `dir`, looked for up to and including its task folder.
fn checkout_of(dir: &Path, folder: &Path) -> Option<PathBuf> {
    let mut at = dir.parent()?;
    loop {
        if at.join(".git").symlink_metadata().is_ok() {
            return Some(at.to_path_buf());
        }
        if at == folder {
            return None;
        }
        at = at.parent().filter(|p| p.starts_with(folder))?;
    }
}

/// What the clean-up would remove, in the order it goes. Read-only.
pub fn plan(scope: &Scope) -> Vec<Candidate> {
    let mut out = vec![];
    let Some((ws, dev)) = workspace_device(scope) else { return out };
    if let (Some(folders), Some(tasks)) = (&scope.folders, under(&ws, "tasks")) {
        let mut finished = vec![];
        if let Ok(entries) = std::fs::read_dir(&tasks) {
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().into_owned();
                let folder = entry.path();
                if name.starts_with('.') || is_symlink(&folder) || !folder.is_dir() || kept(&folder, &scope.keep) || device(&folder) != Some(dev) {
                    continue;
                }
                // A folder the server lists nothing for may be anything (a retired computer lists nothing at all).
                if folders.get(&name).map(|f| f.state) != Some(FolderState::Closed) {
                    continue;
                }
                let (mut caches, mut checkouts) = (vec![], vec![]);
                worktrees::scratch(&folder, 1, &mut caches, &mut checkouts);
                for dir in caches {
                    let tree = walk(&dir, dev);
                    if !tree.plain() || scope.held.as_ref().is_some_and(|h| held_under(&dir, h)) {
                        continue;
                    }
                    let checkout = checkout_of(&dir, &folder);
                    finished.push(Candidate { class: Class::FinishedTaskCache, path: dir, bytes: tree.bytes, newest: tree.newest, folder: Some(name.clone()), checkout });
                }
            }
        }
        finished.sort_by(|a, b| b.bytes.cmp(&a.bytes));
        out.extend(finished);
    }
    if let Some(held) = &scope.held {
        let cutoff = scope.now - OLD_S;
        for root in scope.temps.iter().filter_map(|t| real_root(t)).filter(|r| device(r) == Some(dev)) {
            let Ok(entries) = std::fs::read_dir(&root) else { continue };
            let mut found = vec![];
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().into_owned();
                let path = entry.path();
                // The runner's own temp files (`tico-*`) and the system's dot folders are not leftovers.
                if name.starts_with("tico") || name.starts_with('.') || is_symlink(&path) || kept(&path, &scope.keep) || held_under(&path, held) {
                    continue;
                }
                let tree = walk(&path, dev);
                if !tree.plain() || tree.foreign || tree.newest >= cutoff {
                    continue;
                }
                found.push(Candidate { class: Class::Tmp, path, bytes: tree.bytes, newest: tree.newest, folder: None, checkout: None });
            }
            found.sort_by(|a, b| a.newest.total_cmp(&b.newest));
            out.extend(found);
        }
    }
    out
}

/// Whether Git shows a planned candidate as safe to go: nothing under it tracked or shown as changed, and its
/// checkout clean, holding no ignored files outside build folders and no commits origin lacks (the rule worktrees.rs
/// `clear_task_scratch` keeps a checkout by). Any Git failure keeps it.
pub async fn git_clear(c: &Candidate, env: &Env) -> bool {
    let Some(checkout) = &c.checkout else { return true };
    let Ok(rel) = c.path.strip_prefix(checkout) else { return false };
    let rel = rel.to_string_lossy().into_owned();
    for args in [&["ls-files", "-z", "--", &rel][..], &["status", "--porcelain", "-z", "--untracked-files=all", "--", &rel], &["status", "--porcelain"]] {
        if !worktrees::git(checkout, env, args, true).await.is_ok_and(|o| o.stdout.trim_matches('\0').trim().is_empty()) {
            return false;
        }
    }
    let ignored = worktrees::git(checkout, env, &["ls-files", "--others", "--ignored", "--exclude-standard", "-z"], true).await;
    if !ignored.is_ok_and(|o| o.stdout.split('\0').all(|n| n.is_empty() || worktrees::build_file(n))) {
        return false;
    }
    worktrees::git(checkout, env, &["rev-list", "--count", "HEAD", "--not", "--remotes=origin"], true).await.is_ok_and(|o| o.stdout.trim() == "0")
}

/// The roots a candidate may be in.
fn roots(scope: &Scope, dev: u64) -> Vec<PathBuf> {
    let mut roots: Vec<PathBuf> = scope.temps.iter().filter_map(|r| real_root(r)).filter(|r| device(r) == Some(dev)).collect();
    if let Some(tasks) = real_root(&scope.workspace).and_then(|w| under(&w, "tasks")) {
        roots.push(tasks);
    }
    roots
}

/// Remove one planned candidate after checking it again; the bytes freed on the workspace volume.
pub fn remove(c: &Candidate, scope: &Scope) -> u64 {
    let Some((_, dev)) = workspace_device(scope) else { return 0 };
    let roots = roots(scope, dev);
    // Still where it was planned: not a symlink, nothing on the way resolved elsewhere, inside a root.
    if is_symlink(&c.path) || c.path.canonicalize().ok().as_deref() != Some(c.path.as_path()) || !roots.iter().any(|r| c.path.starts_with(r) && c.path != *r) {
        return 0;
    }
    if kept(&c.path, &scope.keep) || scope.held.as_ref().is_some_and(|h| held_under(&c.path, h)) {
        return 0;
    }
    // Nothing in it became a checkout, a live file or a mount point since it was planned.
    let tree = walk(&c.path, dev);
    if !tree.plain() || (c.class == Class::Tmp && tree.foreign) {
        return 0;
    }
    let meta = match c.path.symlink_metadata() { Ok(m) => m, Err(_) => return 0 };
    // remove_dir_all never follows a symlink, even one swapped in meanwhile.
    let gone = if meta.is_dir() { std::fs::remove_dir_all(&c.path) } else { std::fs::remove_file(&c.path) };
    match gone {
        Ok(()) => tree.bytes,
        // Partly removed: what is left is measured.
        Err(_) => tree.bytes.saturating_sub(walk(&c.path, dev).bytes),
    }
}

/// Every path a process this user can see holds: open files, working folders, programs and mapped files (Linux
/// `/proc`). None elsewhere.
pub fn held_paths() -> Option<HashSet<PathBuf>> {
    let proc = Path::new("/proc");
    if !cfg!(target_os = "linux") || !proc.join("self").exists() {
        return None;
    }
    let mut held = HashSet::new();
    let Ok(entries) = std::fs::read_dir(proc) else { return None };
    for entry in entries.flatten() {
        if !entry.file_name().to_string_lossy().bytes().all(|b| b.is_ascii_digit()) {
            continue;
        }
        let pid = entry.path();
        let mut add = |link: PathBuf| {
            if let Ok(target) = std::fs::read_link(link) {
                if target.is_absolute() {
                    held.insert(target);
                }
            }
        };
        add(pid.join("cwd"));
        add(pid.join("exe"));
        if let Ok(fds) = std::fs::read_dir(pid.join("fd")) {
            for fd in fds.flatten() {
                add(fd.path());
            }
        }
        if let Ok(maps) = std::fs::read_to_string(pid.join("maps")) {
            for line in maps.lines() {
                if let Some(at) = line.find(" /") {
                    held.insert(PathBuf::from(line[at + 1..].trim_end_matches(" (deleted)")));
                }
            }
        }
    }
    Some(held)
}

/// Bytes, per class, that one clean-up freed.
#[derive(Debug, Default, Clone)]
pub struct Tally {
    pub classes: BTreeMap<Class, (u64, u64)>,
}

impl Tally {
    fn add(&mut self, class: Class, bytes: u64) {
        let e = self.classes.entry(class).or_default();
        e.0 += 1;
        e.1 += bytes;
    }
}

fn label(path: &Path, scope: &Scope) -> String {
    let named = [("workspace", &scope.workspace)].into_iter().chain(scope.temps.iter().map(|t| ("tmp", t)));
    for (name, root) in named {
        if let Some(rest) = root.canonicalize().ok().and_then(|r| path.strip_prefix(&r).ok().map(Path::to_path_buf)) {
            return format!("{name}/{}", rest.display());
        }
    }
    path.display().to_string()
}

fn free(workspace: &Path) -> u64 {
    crate::readiness::disk_usage(workspace).map(|d| d.1).unwrap_or(u64::MAX)
}

/// Runs the clean-up when the workspace volume is low, at most once per CLEAN_EVERY, one at a time, and keeps the
/// last run's report for readiness.
#[derive(Default)]
pub struct DiskClean {
    last: Mutex<Option<Instant>>,
    task: Mutex<Option<tokio::task::JoinHandle<()>>>,
    report: Mutex<Option<Value>>,
}

impl DiskClean {
    pub fn new() -> DiskClean {
        DiskClean::default()
    }

    /// The last clean-up, for readiness `disk_cleanup`.
    pub fn report(&self) -> Option<Value> {
        self.report.lock().unwrap().clone()
    }

    /// Start a clean-up when the workspace volume is below its low mark and none ran within CLEAN_EVERY.
    pub fn check(self: &Arc<Self>, workspace: &Path, keep: Vec<PathBuf>, client: &Client, host: Arc<dyn Host>, trees: Arc<Worktrees>) {
        let Some((total, free_now)) = crate::readiness::disk_usage(workspace) else { return };
        let low = low_mark(total);
        if free_now >= low {
            return;
        }
        let mut task = self.task.lock().unwrap();
        if task.as_ref().is_some_and(|t| !t.is_finished()) || self.last.lock().unwrap().is_some_and(|at| at.elapsed() < CLEAN_EVERY) {
            return;
        }
        *self.last.lock().unwrap() = Some(Instant::now());
        let (me, workspace, client) = (self.clone(), workspace.to_path_buf(), client.clone());
        *task = Some(tokio::spawn(async move {
            let report = run(&workspace, keep, &client, host, trees, low, free_now).await;
            *me.report.lock().unwrap() = Some(report);
        }));
    }
}

/// Every lock that keeps a turn or worktree step out of a task folder while something in it goes: each owner's
/// checkout and task-lane locks (with the owner then shown busy to claims, and checked idle with no turn of any bot
/// on its task), then the hub CLI task lock of every link path. None when any of them is taken.
fn hold_folder(folder: &Folder, workspace: &Path, host: &Arc<dyn Host>, trees: &Worktrees) -> Option<(Vec<Box<dyn Send>>, Vec<worktrees::TaskLock>, Vec<String>)> {
    let mut guards = vec![];
    let mut marked = vec![];
    let release = |marked: &[String]| {
        for b in marked {
            trees.maintaining.lock().unwrap().remove(b);
        }
    };
    for (bot, task) in &folder.owners {
        let Some(g) = host.try_locks(bot, &format!("task:{task}")) else {
            release(&marked);
            return None;
        };
        guards.push(g);
        // The checkout lock is held, so no worktree pass has this bot marked: the mark is this clean-up's own.
        if trees.maintaining.lock().unwrap().insert(bot.clone()) {
            marked.push(bot.clone());
        }
        if !host.idle(bot, task) || host.task_busy(task) {
            release(&marked);
            return None;
        }
    }
    let Some(task_locks) = worktrees::try_locked(workspace, &folder.paths) else {
        release(&marked);
        return None;
    };
    Some((guards, task_locks, marked))
}

/// One clean-up; its report.
async fn run(workspace: &Path, keep: Vec<PathBuf>, client: &Client, host: Arc<dyn Host>, trees: Arc<Worktrees>, low: u64, before: u64) -> Value {
    log(&format!("Tico runner: disk low ({:.1} GB free, mark {:.1} GB); cleaning up what is safe to remove", before as f64 / GB, low as f64 / GB));
    let target = low.saturating_add(HEADROOM);
    // Finished tasks' worktrees first, through their usual cleanup (it saves unpushed work or keeps the tree).
    trees.expedite(host.clone(), PASS_WAIT).await;
    let rows = client.get("runners/me/worktrees").await.ok().and_then(|r| r["worktrees"].as_array().cloned());
    let busy_host = host.clone();
    let folders = rows.map(|rows| folder_states(&rows, |bot, task| !busy_host.idle(bot, task) || busy_host.task_busy(task)));
    let mut temps = vec![];
    if cfg!(target_os = "linux") {
        temps.push(std::env::temp_dir());
        if std::env::temp_dir().canonicalize().ok() != Path::new("/tmp").canonicalize().ok() {
            temps.push(PathBuf::from("/tmp"));
        }
    }
    // A root inside the Tico install is never cleaned.
    temps.retain(|r| crate::repo_safety::outside_installation(r).is_ok());
    let workspace_ok = crate::repo_safety::outside_installation(workspace).is_ok();
    let keep: Vec<PathBuf> = keep.into_iter().filter(|k| k.is_absolute()).map(|k| k.canonicalize().unwrap_or(k)).collect();
    let scope = Scope {
        workspace: workspace.to_path_buf(),
        folders: folders.filter(|_| workspace_ok),
        temps,
        held: tokio::task::spawn_blocking(held_paths).await.ok().flatten(),
        keep,
        now: crate::util::epoch_s(),
    };
    let planned = {
        let s = scope.clone();
        tokio::task::spawn_blocking(move || plan(&s)).await.unwrap_or_default()
    };
    let env = crate::env::process_environment();
    let mut tally = Tally::default();
    for c in planned {
        if free(workspace) >= target {
            break;
        }
        let folder = c.folder.as_ref().and_then(|f| scope.folders.as_ref().and_then(|m| m.get(f)));
        let held = match folder {
            Some(f) => match hold_folder(f, workspace, &host, &trees) {
                Some(h) => Some(h),
                None => continue,
            },
            None if c.folder.is_some() => continue,
            None => None,
        };
        let freed = if git_clear(&c, &env).await {
            let (s, cand) = (scope.clone(), c.clone());
            tokio::task::spawn_blocking(move || remove(&cand, &s)).await.unwrap_or(0)
        } else {
            0
        };
        if let Some((guards, task_locks, marked)) = held {
            for b in &marked {
                trees.maintaining.lock().unwrap().remove(b);
            }
            drop(task_locks);
            drop(guards);
        }
        if freed > 0 {
            log(&format!("Tico runner: disk clean-up removed {} {} ({:.1} MB)", c.class.name(), label(&c.path, &scope), freed as f64 / 1e6));
            tally.add(c.class, freed);
        }
    }
    if free(workspace) < target {
        git_maintenance(workspace, host.repositories(), &env, &mut tally).await;
    }
    let after = free(workspace);
    let freed: u64 = tally.classes.values().map(|v| v.1).sum();
    let still_low = after < low;
    crate::util::diagnostic(&format!(
        "Tico runner: disk clean-up freed {:.1} GB ({:.1} GB free now){}",
        freed as f64 / GB,
        after as f64 / GB,
        if still_low { "; still low: what is left is not safe for the computer to remove (docs/worktrees.md#low-disk)" } else { "" }
    ));
    report(&tally, before, after, low, crate::util::epoch_s())
}

/// Tico's own base clones: `repos/<folder>` with a `.git` folder holding Tico's `tico-managed` marker.
pub fn base_clones(workspace: &Path) -> Vec<(String, PathBuf)> {
    let Some(repos) = real_root(workspace).and_then(|w| under(&w, "repos")) else { return vec![] };
    let Ok(entries) = std::fs::read_dir(&repos) else { return vec![] };
    let mut bases: Vec<(String, PathBuf)> = entries
        .flatten()
        .filter(|e| e.file_type().is_ok_and(|t| t.is_dir()) && !e.file_name().to_string_lossy().starts_with('.'))
        .filter(|e| {
            let git = e.path().join(".git");
            git.symlink_metadata().is_ok_and(|m| m.is_dir()) && git.join("tico-managed").symlink_metadata().is_ok_and(|m| m.is_file())
        })
        .map(|e| (e.file_name().to_string_lossy().into_owned(), e.path()))
        .collect();
    bases.sort();
    bases
}

/// `git worktree prune` and `git gc --auto` (gc only with room to repack) in each base clone, one at a time, each
/// under the lock its fetches take; a clone whose fetch is running is left for the next clean-up.
async fn git_maintenance(workspace: &Path, repositories: Option<Arc<crate::repositories::Repositories>>, env: &Env, tally: &mut Tally) {
    let Some(repositories) = repositories else { return };
    for (folder, base) in base_clones(workspace) {
        let Some(_sync) = repositories.try_sync_guard(&folder) else { continue };
        let before = free(workspace);
        let _ = worktrees::git(&base, env, &["worktree", "prune"], false).await;
        if free(workspace) > GC_FLOOR {
            let _ = worktrees::git(&base, env, GC_ARGS, false).await;
        }
        let gained = free(workspace).saturating_sub(before);
        if gained > 0 {
            tally.add(Class::Git, gained);
            log(&format!("Tico runner: disk clean-up ran git maintenance in workspace/repos/{folder} ({:.1} MB)", gained as f64 / 1e6));
        }
    }
}

/// The readiness `disk_cleanup` document (backend/models.py DiskCleanupReadiness).
pub fn report(tally: &Tally, before: u64, after: u64, low: u64, at: f64) -> Value {
    let classes: Vec<Value> = tally.classes.iter().map(|(c, (n, b))| json!({"kind": c.name(), "count": n, "bytes": b})).collect();
    json!({
        "at": crate::util::iso(&json!(at.floor())),
        "free_before_bytes": before,
        "free_after_bytes": after,
        "low_bytes": low,
        "freed_bytes": tally.classes.values().map(|v| v.1).sum::<u64>(),
        "classes": classes,
        "still_low": after < low,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Gone(PathBuf);
    impl Drop for Gone {
        fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); }
    }

    fn temp(name: &str) -> (Gone, PathBuf) {
        let root = std::env::temp_dir().join(format!("disk-clean-{name}-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&root).unwrap();
        let root = root.canonicalize().unwrap();
        (Gone(root.clone()), root)
    }

    fn file(path: &Path, bytes: usize) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, vec![b'x'; bytes]).unwrap();
    }

    fn age(path: &Path, seconds_ago: f64) {
        let at = std::time::SystemTime::now() - Duration::from_secs_f64(seconds_ago);
        let f = std::fs::File::options().write(true).open(path).or_else(|_| std::fs::File::open(path)).unwrap();
        f.set_modified(at).unwrap();
    }

    /// Age a whole tree, files and folders.
    fn age_tree(path: &Path, seconds_ago: f64) {
        for entry in std::fs::read_dir(path).unwrap().flatten() {
            if entry.file_type().unwrap().is_dir() {
                age_tree(&entry.path(), seconds_ago);
            } else if entry.file_type().unwrap().is_file() {
                age(&entry.path(), seconds_ago);
            }
        }
        age(path, seconds_ago);
    }

    fn git(dir: &Path, args: &[&str]) {
        let out = std::process::Command::new("git").arg("-C").arg(dir).args(args)
            .env_clear().envs(crate::safegit::clean_environment(&crate::env::process_environment()))
            .env("GIT_AUTHOR_NAME", "t").env("GIT_AUTHOR_EMAIL", "t@acme.example").env("GIT_COMMITTER_NAME", "t").env("GIT_COMMITTER_EMAIL", "t@acme.example")
            .output().unwrap();
        assert!(out.status.success(), "git {args:?}: {}", String::from_utf8_lossy(&out.stderr));
    }

    fn rows() -> Vec<Value> {
        vec![
            json!({"path": "tasks/done1/acme__app", "owner": "bot:dev", "task_id": "done1-id", "task_status": "done", "state": "present"}),
            json!({"path": "tasks/open1/acme__app", "owner": "bot:dev", "task_id": "open1-id", "task_status": "doing", "state": "present"}),
            json!({"path": "tasks/live1/acme__app", "owner": "bot:qa", "task_id": "live1-id", "task_status": "doing", "state": "present"}),
            json!({"path": "tasks/livedone/acme__app", "owner": "bot:qa", "task_id": "livedone-id", "task_status": "done", "state": "present"}),
        ]
    }

    fn scope(root: &Path) -> Scope {
        let busy = |_: &str, task: &str| task == "live1-id" || task == "livedone-id";
        std::fs::create_dir_all(root.join("ws/tasks")).unwrap();
        std::fs::create_dir_all(root.join("tmp")).unwrap();
        Scope {
            workspace: root.join("ws"),
            folders: Some(folder_states(&rows(), busy)),
            temps: vec![root.join("tmp")],
            held: Some(HashSet::new()),
            keep: vec![root.join("state-r1")],
            now: crate::util::epoch_s(),
        }
    }

    fn cargo_target(dir: &Path) {
        file(&dir.join("CACHEDIR.TAG"), 0);
        std::fs::write(dir.join("CACHEDIR.TAG"), "Signature: 8a477f597d28d172789f06886806bc55\n").unwrap();
        file(&dir.join("debug/big"), 4096);
    }

    fn planned(scope: &Scope) -> Vec<(Class, PathBuf)> {
        plan(scope).into_iter().map(|c| (c.class, c.path)).collect()
    }

    fn env() -> Env {
        crate::env::process_environment()
    }

    #[test]
    fn low_mark_is_fifteen_percent_or_ten_gb_capped() {
        let gb = GB as u64;
        assert_eq!(low_mark(250 * gb), 250 * gb / 100 * 15); // 15% of a 250 GB volume
        assert_eq!(low_mark(50 * gb), 10 * gb); // 10 GB beats 7.5 GB
        assert_eq!(low_mark(20 * gb), 5 * gb); // never above a quarter
        assert_eq!(low_mark(2000 * gb), 40 * gb); // nor above 40 GB
    }

    #[test]
    fn folder_states_rank_running_over_kept_over_open_over_closed() {
        let mut more = rows();
        more.push(json!({"path": "tasks/done1/acme__lib", "owner": "bot:ops", "task_id": "done1-id", "task_status": "done", "state": "present"}));
        more.push(json!({"path": "tasks/open1/acme__lib", "owner": "bot:ops", "task_id": "open1-id", "task_status": "done", "state": "present"}));
        let f = folder_states(&more, |_, task| task == "live1-id");
        assert_eq!(f["done1"].state, FolderState::Closed);
        assert_eq!(f["done1"].owners, vec![("dev".to_string(), "done1-id".to_string()), ("ops".to_string(), "done1-id".to_string())]);
        assert_eq!(f["done1"].paths, vec!["tasks/done1/acme__app".to_string(), "tasks/done1/acme__lib".to_string()]);
        assert_eq!(f["open1"].state, FolderState::Open);
        assert_eq!(f["live1"].state, FolderState::Running);
        assert!(!f.contains_key("acme__app"));
    }

    #[test]
    fn a_person_owned_or_kept_tree_keeps_its_whole_folder() {
        let rows = vec![
            json!({"path": "tasks/mine/acme__app", "owner": "bot:dev", "task_id": "t", "task_status": "done", "state": "present"}),
            json!({"path": "tasks/mine/acme__web", "owner": "u_acme", "task_id": "t", "task_status": "done", "state": "present"}),
            json!({"path": "tasks/refused/acme__app", "owner": "bot:dev", "task_id": "r", "task_status": "done", "state": "present",
                   "detail_json": "{\"cleanup_requested\": true, \"error\": \"kept: ignored files\"}"}),
            json!({"path": "tasks/gaveup/acme__app", "owner": "bot:dev", "task_id": "g", "task_status": "done", "state": "present",
                   "detail_json": "{\"cleanup_gave_up\": true}"}),
        ];
        let f = folder_states(&rows, |_, _| false);
        assert_eq!(f["mine"].state, FolderState::Kept);
        assert_eq!(f["mine"].owners, vec![("dev".to_string(), "t".to_string())]);
        assert_eq!(f["refused"].state, FolderState::Kept);
        assert_eq!(f["gaveup"].state, FolderState::Kept);
        let (_g, root) = temp("kept");
        let s = Scope { folders: Some(f), ..scope(&root) };
        for name in ["mine", "refused", "gaveup"] {
            cargo_target(&root.join(format!("ws/tasks/{name}/acme__app/target")));
        }
        assert!(plan(&s).is_empty());
    }

    #[test]
    fn a_folder_the_server_lists_nothing_for_or_only_a_removed_link_on_an_open_task_is_left() {
        let (_g, root) = temp("unlisted");
        let tasks = root.join("ws/tasks");
        let rows = vec![json!({"path": "tasks/reopened/acme__app", "owner": "bot:dev", "task_id": "x", "task_status": "doing", "state": "removed"})];
        let s = Scope { folders: Some(folder_states(&rows, |_, _| false)), ..scope(&root) };
        cargo_target(&tasks.join("reopened/acme__app/target"));
        file(&tasks.join("orphan/node_modules/a.js"), 10);
        age_tree(&tasks.join("orphan"), 30.0 * 86400.0);
        assert!(plan(&s).is_empty());
        // A retired computer's empty inventory deletes nothing.
        let empty = Scope { folders: Some(folder_states(&[], |_, _| false)), ..s.clone() };
        cargo_target(&tasks.join("done1/acme__app/target"));
        assert!(plan(&empty).is_empty());
    }

    #[test]
    fn finished_task_caches_go_and_open_tasks_and_running_turns_folders_stay() {
        let (_g, root) = temp("tasks");
        let s = scope(&root);
        let tasks = root.join("ws/tasks");
        // A done task's folder: its build output, node_modules and a venv beside the tree go.
        cargo_target(&tasks.join("done1/acme__app/target"));
        file(&tasks.join("done1/acme__app/node_modules/x/index.js"), 100);
        file(&tasks.join("done1/acme__app/src/main.rs"), 10);
        file(&tasks.join("done1/venv/pyvenv.cfg"), 10);
        // An open idle task's caches stay: its next turn needs them.
        cargo_target(&tasks.join("open1/acme__app/target"));
        // A running turn's folder, even of a done task, is never touched.
        cargo_target(&tasks.join("live1/acme__app/target"));
        cargo_target(&tasks.join("livedone/acme__app/target"));
        let got = planned(&s);
        let paths: Vec<&PathBuf> = got.iter().map(|(_, p)| p).collect();
        assert!(got.iter().all(|(c, _)| *c == Class::FinishedTaskCache));
        assert_eq!(paths.len(), 3);
        for p in ["done1/acme__app/target", "done1/acme__app/node_modules", "done1/venv"] {
            assert!(paths.contains(&&tasks.join(p)), "{p}");
        }
        for c in plan(&s) {
            remove(&c, &s);
        }
        assert!(!tasks.join("done1/acme__app/target").exists() && !tasks.join("done1/acme__app/node_modules").exists());
        assert!(tasks.join("done1/acme__app/src/main.rs").exists());
        assert!(tasks.join("open1/acme__app/target/debug/big").exists());
        assert!(tasks.join("live1/acme__app/target/debug/big").exists());
        // Without the server's rows no task folder is touched.
        cargo_target(&tasks.join("done1/acme__app/target"));
        let blind = Scope { folders: None, ..s.clone() };
        assert!(plan(&blind).iter().all(|c| !c.path.starts_with(&tasks)));
    }

    #[test]
    fn a_subtask_turn_running_in_its_parents_lane_keeps_the_subtasks_folder() {
        let (_g, root) = temp("subtask");
        let rows = vec![json!({"path": "tasks/t2short/acme__app", "owner": "bot:dev", "task_id": "t2", "task_status": "done", "state": "present"})];
        // The turn is recorded under its lane's task t1 but claims t2 (sessions.rs LiveTurn).
        let turn = crate::sessions::LiveTurn::of(&json!({"bot": "bot:dev", "lane": "task:t1", "task": {"id": "t2"}}));
        let folders = folder_states(&rows, |bot, task| turn.bot == bot && turn.on(task));
        assert_eq!(folders["t2short"].state, FolderState::Running);
        let s = Scope { folders: Some(folders), ..scope(&root) };
        cargo_target(&root.join("ws/tasks/t2short/acme__app/target"));
        assert!(plan(&s).is_empty());
    }

    /// A clean checkout of `origin` at `tree` with everything pushed and `node_modules/` and `target/` ignored.
    fn pushed_checkout(root: &Path, tree: &Path) {
        let origin = root.join("origin.git");
        std::fs::create_dir_all(&origin).unwrap();
        git(&origin, &["init", "-q", "--bare", "-b", "main"]);
        std::fs::create_dir_all(tree).unwrap();
        git(tree, &["init", "-q", "-b", "main"]);
        git(tree, &["remote", "add", "origin", &origin.display().to_string()]);
        std::fs::write(tree.join(".gitignore"), "node_modules/\ntarget/\n").unwrap();
        git(tree, &["add", ".gitignore"]);
        git(tree, &["commit", "-q", "-m", "first"]);
        git(tree, &["push", "-q", "origin", "main"]);
    }

    #[tokio::test]
    async fn an_ignored_cache_in_a_clean_pushed_checkout_goes_and_one_git_tracks_stays() {
        let (_g, root) = temp("tracked");
        let s = scope(&root);
        let tree = root.join("ws/tasks/done1/acme__app");
        pushed_checkout(&root, &tree);
        cargo_target(&tree.join("target"));
        // A committed node_modules (vendored dependencies) is source, not a cache.
        file(&tree.join("node_modules/vendored/index.js"), 50);
        git(&tree, &["add", "-f", "node_modules/vendored/index.js"]);
        git(&tree, &["commit", "-q", "-m", "vendor"]);
        git(&tree, &["push", "-q", "origin", "main"]);
        let got = plan(&s);
        let by = |p: &str| got.iter().find(|c| c.path == tree.join(p)).cloned().unwrap();
        assert_eq!(by("target").checkout.as_deref(), Some(tree.as_path()));
        assert!(git_clear(&by("target"), &env()).await);
        assert!(!git_clear(&by("node_modules"), &env()).await);
    }

    #[tokio::test]
    async fn a_checkout_holding_unsaved_work_keeps_even_its_caches() {
        let (_g, root) = temp("unsaved");
        let s = scope(&root);
        let tree = root.join("ws/tasks/done1/acme__app");
        pushed_checkout(&root, &tree);
        cargo_target(&tree.join("target"));
        let target = || plan(&s).into_iter().find(|c| c.path == tree.join("target")).unwrap();
        assert!(git_clear(&target(), &env()).await);
        // Uncommitted work.
        file(&tree.join("notes.txt"), 5);
        assert!(!git_clear(&target(), &env()).await);
        // Committed but not pushed.
        git(&tree, &["add", "notes.txt"]);
        git(&tree, &["commit", "-q", "-m", "unpushed work"]);
        assert!(!git_clear(&target(), &env()).await);
        git(&tree, &["push", "-q", "origin", "main"]);
        assert!(git_clear(&target(), &env()).await);
        // An ignored file outside build folders (a local .env).
        std::fs::write(tree.join(".gitignore"), "node_modules/\ntarget/\n.env\n").unwrap();
        git(&tree, &["commit", "-q", "-am", "ignore env"]);
        git(&tree, &["push", "-q", "origin", "main"]);
        file(&tree.join(".env"), 5);
        assert!(!git_clear(&target(), &env()).await);
        // A file under the cache git would show (not ignored there).
        std::fs::remove_file(tree.join(".env")).unwrap();
        std::fs::write(tree.join(".gitignore"), "node_modules/\ntarget/debug/\n").unwrap();
        git(&tree, &["commit", "-q", "-am", "narrow"]);
        git(&tree, &["push", "-q", "origin", "main"]);
        assert!(!git_clear(&target(), &env()).await);
        assert!(tree.join("target/debug/big").exists() && tree.join(".git").is_dir());
    }

    #[test]
    fn symlinks_out_of_the_workspace_are_never_followed_or_removed() {
        let (_g, root) = temp("links");
        let (_o, outside) = temp("outside");
        let s = scope(&root);
        let tasks = root.join("ws/tasks");
        cargo_target(&outside.join("target"));
        file(&outside.join("node_modules/keep.js"), 10);
        std::fs::create_dir_all(tasks.join("done1/acme__app")).unwrap();
        // A cache folder that is a symlink, a task folder that is a symlink, and a link inside a cache.
        std::os::unix::fs::symlink(outside.join("node_modules"), tasks.join("done1/acme__app/node_modules")).unwrap();
        std::os::unix::fs::symlink(&outside, tasks.join("open1")).unwrap();
        file(&tasks.join("done1/acme__app/target/CACHEDIR.TAG"), 0);
        std::fs::write(tasks.join("done1/acme__app/target/CACHEDIR.TAG"), "Signature: 8a477f597d28d172789f06886806bc55\n").unwrap();
        std::os::unix::fs::symlink(outside.join("target"), tasks.join("done1/acme__app/target/out")).unwrap();
        // A tmp entry reached through a symlink.
        std::os::unix::fs::symlink(&outside, root.join("tmp/old-link")).unwrap();
        let got = plan(&s);
        assert_eq!(got.iter().map(|c| c.path.clone()).collect::<Vec<_>>(), vec![tasks.join("done1/acme__app/target")]);
        for c in &got {
            remove(c, &s);
        }
        assert!(outside.join("target/debug/big").exists() && outside.join("node_modules/keep.js").exists());
        // A planned folder swapped for a symlink before removal is refused.
        cargo_target(&tasks.join("done1/acme__app/target"));
        let c = plan(&s).into_iter().find(|c| c.class == Class::FinishedTaskCache).unwrap();
        std::fs::remove_dir_all(&c.path).unwrap();
        std::os::unix::fs::symlink(outside.join("target"), &c.path).unwrap();
        assert_eq!(remove(&c, &s), 0);
        assert!(outside.join("target/debug/big").exists());
    }

    #[test]
    fn nothing_on_another_volume_or_under_a_mount_point_is_planned_or_removed() {
        let (_g, root) = temp("volumes");
        let s = scope(&root);
        let dev = device(&root.join("ws")).unwrap();
        // A walk marks anything on another volume and does not enter it.
        let other = walk(&root.join("tmp"), dev + 1);
        assert!(other.mount && other.bytes == 0);
        // A temp root on another volume (tmpfs /dev/shm, when the test folder is elsewhere) is never planned.
        let shm = Path::new("/dev/shm");
        if device(shm).is_some_and(|d| d != dev) && shm.is_dir() {
            let away = shm.join(format!("disk-clean-{}", uuid::Uuid::new_v4().simple()));
            let _a = Gone(away.clone());
            file(&away.join("old/x.bin"), 100);
            age_tree(&away, 3.0 * 86400.0);
            let s2 = Scope { temps: vec![away.clone()], ..s.clone() };
            assert!(plan(&s2).is_empty());
            let c = Candidate { class: Class::Tmp, path: away.join("old"), bytes: 100, newest: 0.0, folder: None, checkout: None };
            assert_eq!(remove(&c, &s2), 0);
            assert!(away.join("old/x.bin").exists());
        }
        // The workspace's own entries count only blocks no other hard link keeps.
        file(&root.join("tmp/linked/a.bin"), 8192);
        std::fs::hard_link(root.join("tmp/linked/a.bin"), root.join("ws/a-link.bin")).unwrap();
        assert_eq!(walk(&root.join("tmp/linked"), dev).bytes, 0);
    }

    #[test]
    fn old_unheld_temp_entries_go_and_checkouts_live_and_fresh_ones_stay() {
        let (_g, root) = temp("tmp");
        let mut s = scope(&root);
        let tmp = root.join("tmp");
        file(&tmp.join("old-build/out.bin"), 1000);
        age_tree(&tmp.join("old-build"), 2.0 * 86400.0);
        file(&tmp.join("fresh/out.bin"), 1000);
        file(&tmp.join("held-old/log.txt"), 1000);
        age_tree(&tmp.join("held-old"), 2.0 * 86400.0);
        file(&tmp.join("tico-trust-1/x.gitconfig"), 10);
        age_tree(&tmp.join("tico-trust-1"), 2.0 * 86400.0);
        file(&tmp.join("old-file.log"), 10);
        age(&tmp.join("old-file.log"), 2.0 * 86400.0);
        // A checkout someone made in the temp folder, at the top or one level down.
        file(&tmp.join("clone/.git/HEAD"), 10);
        age_tree(&tmp.join("clone"), 2.0 * 86400.0);
        file(&tmp.join("work/repo/.git"), 10);
        age_tree(&tmp.join("work"), 2.0 * 86400.0);
        s.held = Some(HashSet::from([tmp.join("held-old/log.txt")]));
        let got = planned(&s);
        assert!(got.iter().all(|(c, _)| *c == Class::Tmp));
        let paths: Vec<&PathBuf> = got.iter().map(|(_, p)| p).collect();
        assert_eq!(paths.len(), 2, "{paths:?}");
        assert!(paths.contains(&&tmp.join("old-build")) && paths.contains(&&tmp.join("old-file.log")));
        let freed: u64 = plan(&s).iter().map(|c| remove(c, &s)).sum();
        assert!(freed >= 1000);
        assert!(!tmp.join("old-build").exists() && tmp.join("fresh/out.bin").exists() && tmp.join("held-old/log.txt").exists());
        assert!(tmp.join("tico-trust-1/x.gitconfig").exists() && tmp.join("clone/.git/HEAD").exists() && tmp.join("work/repo/.git").exists());
        // Where open files cannot be read, the temp folder is left.
        file(&tmp.join("old2/x"), 10);
        age_tree(&tmp.join("old2"), 2.0 * 86400.0);
        let blind = Scope { held: None, ..s.clone() };
        assert!(plan(&blind).is_empty());
    }

    #[test]
    fn the_runners_state_is_never_a_candidate() {
        let (_g, root) = temp("keep");
        let mut s = scope(&root);
        let state = root.join("tmp/runner-state");
        file(&state.join("heartbeat"), 10);
        age_tree(&state, 2.0 * 86400.0);
        s.keep = vec![state.clone()];
        assert!(plan(&s).iter().all(|c| !c.path.starts_with(&state) && !state.starts_with(&c.path)));
    }

    #[tokio::test]
    async fn a_hub_cli_step_on_any_link_in_the_folder_keeps_it() {
        let (_g, root) = temp("tasklock");
        let ws = root.join("ws");
        std::fs::create_dir_all(ws.join("tasks")).unwrap();
        let paths = vec!["tasks/done1/acme__app".to_string(), "tasks/done1/acme__lib".to_string()];
        let step = worktrees::locked(&ws, &paths[1]).await.unwrap();
        assert!(worktrees::try_locked(&ws, &paths).is_none());
        drop(step);
        let held = worktrees::try_locked(&ws, &paths).unwrap();
        assert!(!held.is_empty());
        // Two paths sharing a lock file take it once, so the clean-up never waits on itself.
        let same: Vec<String> = vec![paths[0].clone(), paths[0].clone()];
        drop(held);
        assert_eq!(worktrees::try_locked(&ws, &same).unwrap().len(), 1);
    }

    #[test]
    fn git_maintenance_runs_gc_in_the_foreground_and_only_in_tico_base_clones() {
        assert_eq!(&GC_ARGS[..2], &["-c", "gc.autoDetach=false"]);
        let (_g, root) = temp("bases");
        let repos = root.join("ws/repos");
        file(&repos.join("acme__app/.git/tico-managed"), 0);
        file(&repos.join("acme__bot/.git/HEAD"), 0); // a bot's own repository: no marker
        file(&repos.join("nested/acme__x/.git/tico-managed"), 0);
        std::os::unix::fs::symlink(repos.join("acme__app"), repos.join("acme__link")).unwrap();
        let got: Vec<String> = base_clones(&root.join("ws")).into_iter().map(|b| b.0).collect();
        assert_eq!(got, vec!["acme__app".to_string()]);
    }

    #[tokio::test]
    async fn a_base_clone_whose_fetch_holds_its_lock_is_left() {
        let (_g, root) = temp("synclock");
        let client = Client::new("http://127.0.0.1:9", "t", 1, 0).unwrap();
        let repos = Arc::new(crate::repositories::Repositories::new(&root.join("ws"), &root.join("state"), client));
        let fetch = repos.try_sync_guard("acme__app").unwrap();
        assert!(repos.try_sync_guard("acme__app").is_none());
        drop(fetch);
        assert!(repos.try_sync_guard("acme__app").is_some());
    }

    #[test]
    fn a_low_disk_retries_only_finished_worktrees_the_server_asked_to_clean() {
        let asked = "{\"cleanup_requested\": true}";
        let row = |id: &str, status: &str, state: &str, detail: &str| {
            json!({"id": id, "path": format!("tasks/{id}/acme__app"), "task_id": id, "owner": "bot:dev", "repo": "acme/app",
                   "branch": "tico/x", "task_status": status, "state": state, "detail_json": detail})
        };
        let rows = vec![
            row("done", "done", "present", asked),
            row("declined", "declined", "present", asked),
            row("open", "doing", "present", asked),
            row("gave-up", "done", "present", "{\"cleanup_requested\": true, \"cleanup_gave_up\": true}"),
            row("gone", "done", "removed", asked),
            row("not-asked", "done", "present", "{}"),
            row("queued", "done", "present", asked),
        ];
        let got = worktrees::urgent_removals(&rows, &[json!({"link_id": "queued", "action": "remove"})]);
        let ids: Vec<String> = got.iter().map(|a| text_of(&a["link_id"])).collect();
        assert_eq!(ids, vec!["done", "declined"]);
        assert_eq!(got[0]["action"], "remove");
        assert_eq!(got[0]["path"], "tasks/done/acme__app");
        assert_eq!(got[0]["owner"], "bot:dev");
    }

    #[test]
    fn the_report_names_each_class_and_whether_the_disk_is_still_low() {
        let mut t = Tally::default();
        t.add(Class::Tmp, 100);
        t.add(Class::Tmp, 50);
        t.add(Class::FinishedTaskCache, 1000);
        let r = report(&t, 10, 20, 30, 0.0);
        assert_eq!(r["freed_bytes"], 1150);
        assert_eq!(r["still_low"], true);
        assert_eq!(r["classes"][0], json!({"kind": "tmp", "count": 2, "bytes": 150}));
        assert_eq!(r["at"], "1970-01-01T00:00:00+00:00");
    }
}
