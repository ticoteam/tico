//! The clean-up a computer runs on its own when its workspace volume runs low (docs/worktrees.md#low-disk). Only
//! what a tool rebuilds or a finished task left behind goes, least useful first, and it stops once the volume is
//! back above the mark:
//!
//! 1. finished tasks' worktrees, through the ordinary cleanup the server asked for (worktrees.rs `act`, which saves
//!    unpushed or uncommitted work first and keeps a tree it cannot save), without the computer's own retry wait;
//! 2. on Linux, top-level entries of the temp folder untouched for a day that no process holds open, and files
//!    untouched for a day in the runner user's package caches (pip, uv, npm, yarn, Go, Cargo downloads; never
//!    browsers);
//! 3. rebuildable folders (worktrees.rs `rebuildable`) in finished tasks' folders;
//! 4. `git worktree prune` and `git gc --auto` in the base clones;
//! 5. build caches (Cargo `target`, `node_modules`, pytest caches; never a virtual environment) in idle open tasks'
//!    worktrees, the longest untouched first.
//!
//! Never touched: a task folder while any turn could be in it, a git checkout itself, bot repositories, the
//! runner's state and configuration, credentials, Docker. Nothing is followed through a symlink, and every path is
//! checked again just before it goes. Each removal is logged with its class and size, and the last run rides on
//! the readiness report (`disk_cleanup`).

use std::collections::{BTreeMap, HashMap, HashSet};
use std::os::unix::fs::{FileTypeExt, MetadataExt};
use std::path::{Component, Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::client::Client;
use crate::repositories::GB;
use crate::util::{log, text_of};
use crate::worktrees::{self, Host, Worktrees};

/// At most one clean-up this often: a volume it could not free is not walked again every heartbeat.
pub const CLEAN_EVERY: Duration = Duration::from_secs(30 * 60);
/// A temp entry or cache file this long untouched is no longer any running tool's.
const OLD_S: f64 = 24.0 * 3600.0;
/// Free space the clean-up aims for above the low mark, so it does not run again an hour later.
const HEADROOM: u64 = 5 * (GB as u64);
/// `git gc` writes new packs before it drops old ones.
const GC_FLOOR: u64 = 2 * (GB as u64);

/// Package caches under the runner user's home whose files are only downloads a tool fetches again. Browser
/// downloads (Playwright, Puppeteer, Cypress) are left: bots need them and they are slow to fetch.
const HOME_CACHES: &[&str] = &[".cache/pip", ".cache/uv", ".cache/go-build", ".cache/yarn", ".cache/node-gyp", ".npm/_cacache", ".cargo/registry/cache"];

/// Free bytes under which the clean-up runs: 15% of the volume or 10 GB, whichever is larger, but never above a
/// quarter of a small volume or 40 GB of a large one.
pub fn low_mark(total: u64) -> u64 {
    let gb = GB as u64;
    (total / 100 * 15).max(10 * gb).min(total / 4).min(40 * gb)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Class {
    Tmp,
    HomeCache,
    FinishedTaskCache,
    Git,
    IdleTaskCache,
}

impl Class {
    pub fn name(self) -> &'static str {
        match self {
            Class::Tmp => "tmp",
            Class::HomeCache => "home_cache",
            Class::FinishedTaskCache => "finished_task_cache",
            Class::Git => "git",
            Class::IdleTaskCache => "idle_task_cache",
        }
    }
}

/// What a task folder (`tasks/<task-short>`) is to the clean-up, from the worktree links the server tracks there.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FolderState {
    /// A turn of its bot (or any turn on its task) is running: nothing in it is touched.
    Running,
    /// Its task is open and no turn is running: only build caches go, and only after everything else.
    Open,
    /// Every link in it is a finished task's: its rebuildable folders go.
    Closed,
    /// No link names it: rebuildable folders untouched for a day go.
    Unknown,
}

#[derive(Debug, Clone)]
pub struct Folder {
    pub state: FolderState,
    /// (bot, task) of each link in the folder, whose locks are held while anything in it goes.
    pub owners: Vec<(String, String)>,
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
        let bot = text_of(&row["owner"]).trim_start_matches("bot:").to_string();
        let task = text_of(&row["task_id"]);
        let state = if busy(&bot, &task) {
            FolderState::Running
        } else if !worktrees::closed(row) && row["state"] != "removed" {
            FolderState::Open
        } else {
            FolderState::Closed
        };
        let folder = out.entry(name.to_string_lossy().into_owned()).or_insert(Folder { state, owners: vec![] });
        // Running outranks open, which outranks closed.
        let rank = |s: FolderState| match s { FolderState::Running => 3, FolderState::Open => 2, FolderState::Closed => 1, FolderState::Unknown => 0 };
        if rank(state) > rank(folder.state) {
            folder.state = state;
        }
        if !folder.owners.contains(&(bot.clone(), task.clone())) {
            folder.owners.push((bot, task));
        }
    }
    out
}

/// Where the clean-up may look, and what it must leave.
#[derive(Debug, Clone, Default)]
pub struct Scope {
    pub workspace: PathBuf,
    /// None when the server's worktree rows could not be read: then no task folder is touched.
    pub folders: Option<HashMap<String, Folder>>,
    pub homes: Vec<PathBuf>,
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
    /// Remove only the files in it untouched for a day, not the folder.
    pub sweep: bool,
    /// The task folder it is in, whose owners' locks are held while it goes.
    pub folder: Option<String>,
}

#[derive(Default)]
struct Tree {
    bytes: u64,
    old_bytes: u64,
    newest: f64,
    /// A socket, pipe or device: something live, never a leftover.
    special: bool,
    /// Something another user owns, which this user cannot remove anyway.
    foreign: bool,
}

fn mtime(meta: &std::fs::Metadata) -> f64 {
    meta.mtime() as f64 + meta.mtime_nsec() as f64 / 1e9
}

/// Size and age of everything under `root`, symlinks never followed (a symlink counts as itself only).
fn walk(root: &Path, cutoff: f64) -> Tree {
    let mut tree = Tree::default();
    let uid = unsafe { libc::geteuid() };
    let Ok(meta) = root.symlink_metadata() else { return tree };
    let mut stack = vec![(root.to_path_buf(), meta)];
    while let Some((path, meta)) = stack.pop() {
        let kind = meta.file_type();
        tree.newest = tree.newest.max(mtime(&meta));
        tree.foreign |= meta.uid() != uid;
        if kind.is_socket() || kind.is_fifo() || kind.is_block_device() || kind.is_char_device() {
            tree.special = true;
        } else if kind.is_file() {
            tree.bytes += meta.len();
            if mtime(&meta) < cutoff {
                tree.old_bytes += meta.len();
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

/// What the clean-up would remove, in the order it goes. Read-only.
pub fn plan(scope: &Scope) -> Vec<Candidate> {
    let cutoff = scope.now - OLD_S;
    let mut out = vec![];
    if let Some(held) = &scope.held {
        for root in scope.temps.iter().filter_map(|t| real_root(t)) {
            let Ok(entries) = std::fs::read_dir(&root) else { continue };
            let mut found = vec![];
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().into_owned();
                let path = entry.path();
                // The runner's own temp files (`tico-*`) and the system's dot folders are not leftovers.
                if name.starts_with("tico") || name.starts_with('.') || is_symlink(&path) || kept(&path, &scope.keep) || held_under(&path, held) {
                    continue;
                }
                let tree = walk(&path, cutoff);
                if tree.special || tree.foreign || tree.newest >= cutoff {
                    continue;
                }
                found.push(Candidate { class: Class::Tmp, path, bytes: tree.bytes, newest: tree.newest, sweep: false, folder: None });
            }
            found.sort_by(|a, b| a.newest.total_cmp(&b.newest));
            out.extend(found);
        }
        for home in scope.homes.iter().filter_map(|h| real_root(h)) {
            for cache in HOME_CACHES {
                let Some(path) = under(&home, cache) else { continue };
                if kept(&path, &scope.keep) {
                    continue;
                }
                let tree = walk(&path, cutoff);
                if tree.old_bytes > 0 {
                    out.push(Candidate { class: Class::HomeCache, path, bytes: tree.old_bytes, newest: tree.newest, sweep: true, folder: None });
                }
            }
        }
    }
    if let (Some(folders), Some(tasks)) = (&scope.folders, real_root(&scope.workspace).and_then(|w| under(&w, "tasks"))) {
        let mut finished = vec![];
        let mut idle = vec![];
        let Ok(entries) = std::fs::read_dir(&tasks) else { return out };
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            let folder = entry.path();
            if name.starts_with('.') || is_symlink(&folder) || !folder.is_dir() || kept(&folder, &scope.keep) {
                continue;
            }
            let state = folders.get(&name).map(|f| f.state).unwrap_or(FolderState::Unknown);
            if state == FolderState::Running {
                continue;
            }
            let (mut caches, mut checkouts) = (vec![], vec![]);
            worktrees::scratch(&folder, 1, &mut caches, &mut checkouts);
            for dir in caches {
                // A virtual environment holds what a bot installed by hand; an open task keeps it.
                if state == FolderState::Open && dir.join("pyvenv.cfg").is_file() {
                    continue;
                }
                let tree = walk(&dir, cutoff);
                if state == FolderState::Unknown && tree.newest >= cutoff {
                    continue;
                }
                let c = Candidate {
                    class: if state == FolderState::Open { Class::IdleTaskCache } else { Class::FinishedTaskCache },
                    path: dir,
                    bytes: tree.bytes,
                    newest: tree.newest,
                    sweep: false,
                    folder: Some(name.clone()),
                };
                if state == FolderState::Open { idle.push(c) } else { finished.push(c) }
            }
        }
        finished.sort_by(|a, b| b.bytes.cmp(&a.bytes));
        idle.sort_by(|a, b| a.newest.total_cmp(&b.newest));
        out.extend(finished);
        out.extend(idle);
    }
    out
}

/// The roots a candidate may be in.
fn roots(scope: &Scope) -> Vec<PathBuf> {
    let mut roots: Vec<PathBuf> = scope.temps.iter().chain(scope.homes.iter()).filter_map(|r| real_root(r)).collect();
    if let Some(tasks) = real_root(&scope.workspace).and_then(|w| under(&w, "tasks")) {
        roots.push(tasks);
    }
    roots
}

/// Remove one planned candidate after checking it again; the bytes freed.
pub fn remove(c: &Candidate, scope: &Scope) -> u64 {
    let roots = roots(scope);
    // Still where it was planned: not a symlink, nothing on the way resolved elsewhere, inside a root.
    if is_symlink(&c.path) || c.path.canonicalize().ok().as_deref() != Some(c.path.as_path()) || !roots.iter().any(|r| c.path.starts_with(r) && c.path != *r) {
        return 0;
    }
    if kept(&c.path, &scope.keep) || scope.held.as_ref().is_some_and(|h| held_under(&c.path, h)) {
        return 0;
    }
    if c.sweep {
        return sweep(&c.path, scope.now - OLD_S, scope.held.as_ref());
    }
    let meta = match c.path.symlink_metadata() { Ok(m) => m, Err(_) => return 0 };
    let gone = if meta.is_dir() { std::fs::remove_dir_all(&c.path) } else { std::fs::remove_file(&c.path) };
    match gone {
        Ok(()) => c.bytes,
        // Partly removed: what is left is measured.
        Err(_) => c.bytes.saturating_sub(walk(&c.path, 0.0).bytes),
    }
}

/// Remove the files under `root` older than `cutoff` that no process holds, then the folders left empty (never
/// `root` itself); the bytes freed. Symlinks are never followed.
fn sweep(root: &Path, cutoff: f64, held: Option<&HashSet<PathBuf>>) -> u64 {
    let mut freed = 0;
    let mut dirs = vec![];
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else { continue };
        for entry in entries.flatten() {
            let path = entry.path();
            let Ok(meta) = path.symlink_metadata() else { continue };
            if meta.is_dir() {
                dirs.push(path.clone());
                stack.push(path);
            } else if meta.is_file() && mtime(&meta) < cutoff && !held.is_some_and(|h| h.contains(&path)) && std::fs::remove_file(&path).is_ok() {
                freed += meta.len();
            }
        }
    }
    for dir in dirs.iter().rev() {
        let _ = std::fs::remove_dir(dir); // only when empty
    }
    freed
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
    let named = [("workspace", &scope.workspace)].into_iter().chain(scope.homes.iter().map(|h| ("home", h))).chain(scope.temps.iter().map(|t| ("tmp", t)));
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

/// One clean-up; its report.
async fn run(workspace: &Path, keep: Vec<PathBuf>, client: &Client, host: Arc<dyn Host>, trees: Arc<Worktrees>, low: u64, before: u64) -> Value {
    log(&format!("Tico runner: disk low ({:.1} GB free, mark {:.1} GB); cleaning up what is safe to remove", before as f64 / GB, low as f64 / GB));
    let target = low.saturating_add(HEADROOM);
    // Finished tasks' worktrees first, through their usual cleanup (it saves unpushed work or keeps the tree).
    trees.expedite(host.clone());
    let rows = client.get("runners/me/worktrees").await.ok().and_then(|r| r["worktrees"].as_array().cloned());
    let busy_host = host.clone();
    let folders = rows.map(|rows| folder_states(&rows, |bot, task| !busy_host.idle(bot, task) || busy_host.task_busy(task)));
    let linux = cfg!(target_os = "linux");
    let mut temps = vec![];
    let mut homes = vec![];
    if linux {
        temps.push(std::env::temp_dir());
        if std::env::temp_dir().canonicalize().ok() != Path::new("/tmp").canonicalize().ok() {
            temps.push(PathBuf::from("/tmp"));
        }
        if let Ok(home) = std::env::var("HOME") {
            if !home.is_empty() {
                homes.push(PathBuf::from(home));
            }
        }
    }
    // A root inside the Tico install is never cleaned.
    temps.retain(|r| crate::repo_safety::outside_installation(r).is_ok());
    homes.retain(|r| crate::repo_safety::outside_installation(r).is_ok());
    let workspace_ok = crate::repo_safety::outside_installation(workspace).is_ok();
    let keep: Vec<PathBuf> = keep.into_iter().filter(|k| k.is_absolute()).map(|k| k.canonicalize().unwrap_or(k)).collect();
    let scope = Scope {
        workspace: workspace.to_path_buf(),
        folders: folders.filter(|_| workspace_ok),
        homes,
        temps,
        held: tokio::task::spawn_blocking(held_paths).await.ok().flatten(),
        keep,
        now: crate::util::epoch_s(),
    };
    let planned = {
        let s = scope.clone();
        tokio::task::spawn_blocking(move || plan(&s)).await.unwrap_or_default()
    };
    let mut tally = Tally::default();
    let mut git_done = false;
    for c in planned {
        if free(workspace) >= target {
            break;
        }
        if c.class == Class::IdleTaskCache && !git_done {
            git_done = true;
            git_maintenance(workspace, &mut tally).await;
            if free(workspace) >= target {
                break;
            }
        }
        let owners = c.folder.as_ref().and_then(|f| scope.folders.as_ref().and_then(|m| m.get(f))).map(|f| f.owners.clone()).unwrap_or_default();
        // The owners' checkout and lane locks keep a turn from starting in the folder while it is cleaned.
        let mut guards = vec![];
        let mut free_to_go = true;
        for (bot, task) in &owners {
            match host.try_locks(bot, &format!("task:{task}")) {
                Some(g) if host.idle(bot, task) && !host.task_busy(task) => guards.push(g),
                _ => {
                    free_to_go = false;
                    break;
                }
            }
        }
        if !free_to_go {
            continue;
        }
        let bots: Vec<String> = owners.iter().map(|o| o.0.clone()).collect();
        trees.maintaining.lock().unwrap().extend(bots.iter().cloned());
        let (s, cand) = (scope.clone(), c.clone());
        let freed = tokio::task::spawn_blocking(move || remove(&cand, &s)).await.unwrap_or(0);
        for b in &bots {
            trees.maintaining.lock().unwrap().remove(b);
        }
        drop(guards);
        if freed > 0 {
            log(&format!("Tico runner: disk clean-up removed {} {} ({:.1} MB)", c.class.name(), label(&c.path, &scope), freed as f64 / 1e6));
            tally.add(c.class, freed);
        }
    }
    if !git_done && free(workspace) < target {
        git_maintenance(workspace, &mut tally).await;
    }
    let after = free(workspace);
    let freed: u64 = tally.classes.values().map(|v| v.1).sum();
    let still_low = after < low;
    crate::util::diagnostic(&format!(
        "Tico runner: disk clean-up freed {:.1} GB ({:.1} GB free now){}",
        freed as f64 / GB,
        after as f64 / GB,
        if still_low { "; still low: what is left needs a person (docs/worktrees.md#low-disk)" } else { "" }
    ));
    report(&tally, before, after, low, crate::util::epoch_s())
}

/// `git worktree prune` and `git gc --auto` in each base clone under `repos/` (gc only with room to repack).
async fn git_maintenance(workspace: &Path, tally: &mut Tally) {
    let Some(repos) = real_root(workspace).and_then(|w| under(&w, "repos")) else { return };
    let mut bases = vec![];
    let mut stack = vec![(repos, 0)];
    while let Some((dir, depth)) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else { continue };
        for entry in entries.flatten() {
            let path = entry.path();
            if !entry.file_type().is_ok_and(|t| t.is_dir()) || entry.file_name().to_string_lossy().starts_with('.') {
                continue;
            }
            let git = path.join(".git");
            if git.symlink_metadata().is_ok_and(|m| m.is_dir()) {
                bases.push(path);
            } else if depth < 2 {
                stack.push((path, depth + 1));
            }
        }
    }
    let env = crate::safegit::environment(&crate::env::process_environment());
    for base in bases {
        let before = free(workspace);
        let _ = worktrees::git(&base, &env, &["worktree", "prune"], false).await;
        if free(workspace) > GC_FLOOR {
            let _ = worktrees::git(&base, &env, &["gc", "--auto", "--quiet"], false).await;
        }
        let gained = free(workspace).saturating_sub(before);
        tally.add(Class::Git, gained);
        if gained > 0 {
            log(&format!("Tico runner: disk clean-up ran git maintenance in workspace/{} ({:.1} MB)", base.strip_prefix(workspace).unwrap_or(&base).display(), gained as f64 / 1e6));
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
        Scope {
            workspace: root.join("ws"),
            folders: Some(folder_states(&rows(), busy)),
            homes: vec![root.join("home")],
            temps: vec![root.join("tmp")],
            held: Some(HashSet::new()),
            keep: vec![root.join("home/state-r1")],
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

    #[test]
    fn low_mark_is_fifteen_percent_or_ten_gb_capped() {
        let gb = GB as u64;
        assert_eq!(low_mark(250 * gb), 250 * gb / 100 * 15); // 15% of a 250 GB volume
        assert_eq!(low_mark(50 * gb), 10 * gb); // 10 GB beats 7.5 GB
        assert_eq!(low_mark(20 * gb), 5 * gb); // never above a quarter
        assert_eq!(low_mark(2000 * gb), 40 * gb); // nor above 40 GB
    }

    #[test]
    fn folder_states_rank_running_over_open_over_closed() {
        let mut more = rows();
        more.push(json!({"path": "tasks/done1/acme__lib", "owner": "bot:dev", "task_id": "done1-id", "task_status": "done", "state": "removed"}));
        more.push(json!({"path": "tasks/open1/acme__lib", "owner": "bot:ops", "task_id": "open1-id", "task_status": "done", "state": "present"}));
        let f = folder_states(&more, |_, task| task == "live1-id");
        assert_eq!(f["done1"].state, FolderState::Closed);
        assert_eq!(f["done1"].owners, vec![("dev".to_string(), "done1-id".to_string())]);
        assert_eq!(f["open1"].state, FolderState::Open);
        assert_eq!(f["open1"].owners.len(), 2);
        assert_eq!(f["live1"].state, FolderState::Running);
        assert!(!f.contains_key("acme__app"));
    }

    #[test]
    fn finished_task_caches_go_and_running_turns_checkouts_and_venvs_of_open_tasks_stay() {
        let (_g, root) = temp("tasks");
        let s = scope(&root);
        let tasks = root.join("ws/tasks");
        // A done task's clean worktree: its build output and node_modules go.
        cargo_target(&tasks.join("done1/acme__app/target"));
        file(&tasks.join("done1/acme__app/node_modules/x/index.js"), 100);
        file(&tasks.join("done1/acme__app/src/main.rs"), 10);
        file(&tasks.join("done1/venv/pyvenv.cfg"), 10);
        // An open idle task: build caches go last; its virtual environment stays.
        cargo_target(&tasks.join("open1/acme__app/target"));
        file(&tasks.join("open1/acme__app/.venv/pyvenv.cfg"), 10);
        // A running turn's folder, even of a done task, is never touched.
        cargo_target(&tasks.join("live1/acme__app/target"));
        cargo_target(&tasks.join("livedone/acme__app/target"));
        // A folder no link names: only caches untouched for a day.
        file(&tasks.join("orphan-new/node_modules/a.js"), 10);
        file(&tasks.join("orphan-old/node_modules/a.js"), 10);
        age_tree(&tasks.join("orphan-old"), 3.0 * 86400.0);
        let got = planned(&s);
        let finished: Vec<&PathBuf> = got.iter().filter(|(c, _)| *c == Class::FinishedTaskCache).map(|(_, p)| p).collect();
        let idle: Vec<&PathBuf> = got.iter().filter(|(c, _)| *c == Class::IdleTaskCache).map(|(_, p)| p).collect();
        assert!(finished.contains(&&tasks.join("done1/acme__app/target")));
        assert!(finished.contains(&&tasks.join("done1/acme__app/node_modules")));
        assert!(finished.contains(&&tasks.join("done1/venv")));
        assert!(finished.contains(&&tasks.join("orphan-old/node_modules")));
        assert!(!finished.contains(&&tasks.join("orphan-new/node_modules")));
        assert_eq!(idle, vec![&tasks.join("open1/acme__app/target")]);
        assert!(got.iter().all(|(_, p)| !p.starts_with(tasks.join("live1")) && !p.starts_with(tasks.join("livedone"))));
        // Idle open tasks' caches come after every finished one.
        let last_finished = got.iter().rposition(|(c, _)| *c == Class::FinishedTaskCache).unwrap();
        let first_idle = got.iter().position(|(c, _)| *c == Class::IdleTaskCache).unwrap();
        assert!(last_finished < first_idle);
        for c in plan(&s) {
            remove(&c, &s);
        }
        assert!(!tasks.join("done1/acme__app/target").exists() && !tasks.join("done1/acme__app/node_modules").exists());
        assert!(tasks.join("done1/acme__app/src/main.rs").exists());
        assert!(tasks.join("open1/acme__app/.venv/pyvenv.cfg").exists() && !tasks.join("open1/acme__app/target").exists());
        assert!(tasks.join("live1/acme__app/target/debug/big").exists());
        // Without the server's rows no task folder is touched.
        cargo_target(&tasks.join("done1/acme__app/target"));
        let blind = Scope { folders: None, ..s.clone() };
        assert!(plan(&blind).iter().all(|c| !c.path.starts_with(&tasks)));
    }

    #[tokio::test]
    async fn an_unpushed_or_dirty_checkout_in_a_finished_folder_is_kept_with_its_work() {
        let (_g, root) = temp("unpushed");
        let s = scope(&root);
        let tree = root.join("ws/tasks/done1/acme__app");
        std::fs::create_dir_all(&tree).unwrap();
        git(&tree, &["init", "-q", "-b", "main"]);
        file(&tree.join("a.txt"), 5);
        git(&tree, &["add", "a.txt"]);
        git(&tree, &["commit", "-q", "-m", "unpushed work"]);
        file(&tree.join("b.txt"), 5); // uncommitted
        cargo_target(&tree.join("target"));
        let got = plan(&s);
        assert_eq!(got.iter().map(|c| c.path.clone()).collect::<Vec<_>>(), vec![tree.join("target")]);
        for c in got {
            remove(&c, &s);
        }
        assert!(tree.join(".git").is_dir() && tree.join("a.txt").exists() && tree.join("b.txt").exists());
        let log = std::process::Command::new("git").arg("-C").arg(&tree).args(["log", "--format=%s"]).output().unwrap();
        assert_eq!(String::from_utf8_lossy(&log.stdout).trim(), "unpushed work");
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
        std::os::unix::fs::symlink(&outside, tasks.join("done2")).unwrap();
        file(&tasks.join("done1/acme__app/target/CACHEDIR.TAG"), 0);
        std::fs::write(tasks.join("done1/acme__app/target/CACHEDIR.TAG"), "Signature: 8a477f597d28d172789f06886806bc55\n").unwrap();
        std::os::unix::fs::symlink(outside.join("target"), tasks.join("done1/acme__app/target/out")).unwrap();
        // A tmp entry and a home cache reached through symlinks.
        std::fs::create_dir_all(root.join("tmp")).unwrap();
        std::os::unix::fs::symlink(&outside, root.join("tmp/old-link")).unwrap();
        std::fs::create_dir_all(root.join("home/.cache")).unwrap();
        std::os::unix::fs::symlink(&outside, root.join("home/.cache/pip")).unwrap();
        let got = plan(&s);
        assert_eq!(got.iter().map(|c| c.path.clone()).collect::<Vec<_>>(), vec![tasks.join("done1/acme__app/target")]);
        for c in &got {
            remove(c, &s);
        }
        assert!(outside.join("target/debug/big").exists() && outside.join("node_modules/keep.js").exists());
        // A planned folder swapped for a symlink before removal is refused.
        let (_w, ws2) = temp("swap");
        let s2 = Scope { workspace: ws2.clone(), ..s.clone() };
        cargo_target(&ws2.join("tasks/done1/acme__app/target"));
        let c = plan(&s2).into_iter().find(|c| c.class == Class::FinishedTaskCache).unwrap();
        std::fs::remove_dir_all(&c.path).unwrap();
        std::os::unix::fs::symlink(outside.join("target"), &c.path).unwrap();
        assert_eq!(remove(&c, &s2), 0);
        assert!(outside.join("target/debug/big").exists());
    }

    #[test]
    fn old_unheld_temp_entries_and_old_cache_files_go_and_the_rest_stays() {
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
        s.held = Some(HashSet::from([tmp.join("held-old/log.txt")]));
        let pip = root.join("home/.cache/pip");
        file(&pip.join("http/old"), 500);
        age(&pip.join("http/old"), 3.0 * 86400.0);
        file(&pip.join("http/new"), 500);
        file(&root.join("home/.cache/ms-playwright/chromium/chrome"), 500);
        age_tree(&root.join("home/.cache/ms-playwright"), 3.0 * 86400.0);
        let got = planned(&s);
        let tmp_paths: Vec<&PathBuf> = got.iter().filter(|(c, _)| *c == Class::Tmp).map(|(_, p)| p).collect();
        assert_eq!(tmp_paths.len(), 2);
        assert!(tmp_paths.contains(&&tmp.join("old-build")) && tmp_paths.contains(&&tmp.join("old-file.log")));
        assert_eq!(got.iter().filter(|(c, _)| *c == Class::HomeCache).map(|(_, p)| p.clone()).collect::<Vec<_>>(), vec![pip.clone()]);
        let freed: u64 = plan(&s).iter().map(|c| remove(c, &s)).sum();
        assert_eq!(freed, 1000 + 10 + 500);
        assert!(!tmp.join("old-build").exists() && tmp.join("fresh/out.bin").exists() && tmp.join("held-old/log.txt").exists());
        assert!(tmp.join("tico-trust-1/x.gitconfig").exists());
        assert!(!pip.join("http/old").exists() && pip.join("http/new").exists());
        assert!(root.join("home/.cache/ms-playwright/chromium/chrome").exists());
        // Where open files cannot be read, the temp folder and home caches are left.
        file(&tmp.join("old2/x"), 10);
        age_tree(&tmp.join("old2"), 2.0 * 86400.0);
        let blind = Scope { held: None, ..s.clone() };
        assert!(plan(&blind).iter().all(|c| c.class != Class::Tmp && c.class != Class::HomeCache));
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
