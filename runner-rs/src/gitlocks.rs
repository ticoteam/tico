//! Recover only in runner-owned data/work roots. These copies must be private to one runner's PID
//! namespace, never bind-mounted into a sibling container or shared with a host. Filesystem type
//! cannot cheaply prove this deployment assumption. Uncertainty keeps the lock for Health to name.

use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::os::unix::io::AsRawFd;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Duration;

const OLD: Duration = Duration::from_secs(30 * 60);
static OWNED: Mutex<Vec<PathBuf>> = Mutex::new(Vec::new());

// Exiting Git from another fixture can be temporarily unreadable to the live-owner safety check.
#[cfg(test)]
pub static TEST_FIXTURES: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

/// Only registration paths, never an arbitrary Git command's cwd, authorize recovery.
pub fn own(roots: &[PathBuf]) {
    OWNED.lock().unwrap().extend(roots.iter().filter(|r| !r.as_os_str().is_empty()).cloned());
}

pub fn owned(path: &Path) -> bool {
    let Ok(path) = path.canonicalize() else { return false };
    OWNED.lock().unwrap().iter().filter_map(|r| r.canonicalize().ok()).any(|r| path.starts_with(r))
}

/// The file is never unlinked: every engine must flock the same inode.
pub fn recovery_lock(common: &Path) -> Option<std::fs::File> {
    if !owned(common) { return None; }
    let file = std::fs::OpenOptions::new().write(true).create(true).truncate(false)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK).mode(0o600).open(common.join("tico-lock-recovery")).ok()?;
    let m = file.metadata().ok()?;
    if !m.is_file() || m.nlink() != 1 { return None; }
    (unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0).then_some(file)
}

pub fn owned_roots() -> Vec<PathBuf> { OWNED.lock().unwrap().clone() }

fn old_file(path: &Path) -> Option<std::fs::Metadata> {
    let m = path.symlink_metadata().ok()?;
    (m.is_file() && m.nlink() == 1 && m.modified().ok()?.elapsed().ok()? >= OLD).then_some(m)
}

#[cfg(target_os = "linux")]
fn inaccessible_git(process: &Path) -> bool {
    match std::fs::read_to_string(process.join("comm")) {
        Ok(name) => name.trim().starts_with("git"),
        Err(e) => e.kind() != std::io::ErrorKind::NotFound,
    }
}

fn related(cwd: &Path, args: &[String], environment: &[String], roots: &[PathBuf]) -> Option<bool> {
    let inside = |p: &Path| -> Option<bool> {
        let p = p.canonicalize().ok()?;
        Some(roots.iter().any(|r| p.starts_with(r)))
    };
    if inside(cwd)? { return Some(true); }
    let mut at = cwd.to_path_buf();
    let mut args = args.iter().skip(1);
    while let Some(arg) = args.next() {
        if arg == "-C" {
            at = at.join(args.next()?).canonicalize().ok()?;
            if inside(&at)? { return Some(true); }
        } else if arg == "--git-dir" {
            if inside(&at.join(args.next()?))? { return Some(true); }
        } else if let Some(path) = arg.strip_prefix("--git-dir=") {
            if inside(&at.join(path))? { return Some(true); }
        }
    }
    for entry in environment {
        if let Some(path) = entry.strip_prefix("GIT_DIR=").or_else(|| entry.strip_prefix("GIT_COMMON_DIR=")) {
            if inside(&at.join(path))? { return Some(true); }
        }
    }
    Some(false)
}

#[cfg(target_os = "linux")]
fn exited(process: &Path) -> bool {
    if !process.exists() { return true; }
    // A zombie has released its descriptors and cwd; an unreadable live process still blocks recovery.
    std::fs::read_to_string(process.join("stat")).ok()
        .and_then(|s| s.rsplit_once(") ").map(|(_, rest)| matches!(rest.chars().next(), Some('Z' | 'X'))))
        .unwrap_or(false)
}

#[cfg(target_os = "linux")]
fn git_related(process: &Path, roots: &[PathBuf]) -> Option<bool> {
    let cwd = std::fs::read_link(process.join("cwd")).ok()?;
    let args = std::fs::read_to_string(process.join("cmdline")).ok()?;
    let environment = std::fs::read_to_string(process.join("environ")).ok()?;
    if args.is_empty() { return None; }
    related(&cwd, &args.trim_end_matches('\0').split('\0').map(String::from).collect::<Vec<_>>(),
        &environment.split('\0').map(String::from).collect::<Vec<_>>(), roots)
}

#[cfg(target_os = "linux")]
async fn unused(_path: &Path, lock: &std::fs::Metadata, roots: &[PathBuf], strict: bool) -> bool {
    let Ok(processes) = std::fs::read_dir("/proc") else { return false };
    for process in processes {
        let Ok(process) = process else { return false };
        if !process.file_name().to_string_lossy().bytes().all(|c| c.is_ascii_digit()) { continue; }
        if inaccessible_git(&process.path()) && git_related(&process.path(), roots) != Some(false) {
            if !exited(&process.path()) { return false; }
            continue;
        }
        let files = match std::fs::read_dir(process.path().join("fd")) {
            Ok(files) => files,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
            Err(_) if !strict && !inaccessible_git(&process.path()) => continue,
            Err(_) => return false,
        };
        for file in files {
            let Ok(file) = file else { return false };
            match std::fs::metadata(file.path()) {
                Ok(m) if m.dev() == lock.dev() && m.ino() == lock.ino() => return false,
                Err(e) if e.kind() != std::io::ErrorKind::NotFound && (strict || inaccessible_git(&process.path())) => return false,
                _ => {}
            }
        }
    }
    true
}

#[cfg(not(target_os = "linux"))]
async fn output(program: &str, args: &[&str]) -> Option<std::process::Output> {
    let mut cmd = tokio::process::Command::new(program);
    cmd.args(args).kill_on_drop(true);
    tokio::time::timeout(Duration::from_secs(5), cmd.output()).await.ok()?.ok()
}

#[cfg(not(target_os = "linux"))]
async fn unused(path: &Path, _lock: &std::fs::Metadata, roots: &[PathBuf], strict: bool) -> bool {
    // lsof may silently omit another user's descriptors. Instruction backups keep their inode on macOS.
    if strict { return false; }
    let Some(out) = output("/usr/sbin/lsof", &["-F", "p", "--", &path.to_string_lossy()]).await else { return false };
    if out.status.code() != Some(1) || !out.stdout.is_empty() || !out.stderr.is_empty() { return false; }
    let Some(out) = output("ps", &["-axo", "pid=,comm="]).await else { return false };
    if !out.status.success() || !out.stderr.is_empty() { return false; }
    let Ok(processes) = String::from_utf8(out.stdout) else { return false };
    for line in processes.lines() {
        let Some((pid, name)) = line.trim().split_once(char::is_whitespace) else { return false };
        if !Path::new(name.trim()).file_name().is_some_and(|n| n.to_string_lossy().starts_with("git")) { continue; }
        let Some(cwd) = output("/usr/sbin/lsof", &["-a", "-p", pid, "-d", "cwd", "-Fn"]).await else { return false };
        let Some(args) = output("ps", &["eww", "-p", pid, "-o", "command="]).await else { return false };
        if !cwd.status.success() || !args.status.success() || !cwd.stderr.is_empty() || !args.stderr.is_empty() { return false; }
        let (Ok(cwd), Ok(args)) = (String::from_utf8(cwd.stdout), String::from_utf8(args.stdout)) else { return false };
        let names: Vec<_> = cwd.lines().filter_map(|s| s.strip_prefix('n')).collect();
        if names.len() != 1 || args.trim().is_empty() || args.contains(['\'', '"', '\\']) { return false; }
        let words: Vec<String> = args.split_whitespace().map(String::from).collect();
        if related(Path::new(names[0]), &words, &words, roots) != Some(false) { return false; }
        // ps does not preserve argv/environment boundaries; path arguments may contain spaces.
        if words.iter().any(|w| w.contains("GIT_DIR=") || w.contains("GIT_COMMON_DIR=") || w == "-C" || w.starts_with("--git-dir")) { return false; }
    }
    true
}

pub async fn no_open_file(path: &Path, metadata: &std::fs::Metadata) -> bool { unused(path, metadata, &[], true).await }

fn quarantine(path: &Path, before: &std::fs::Metadata, archive: Option<&Path>) -> Result<bool, String> {
    let directory = archive.unwrap_or_else(|| path.parent().unwrap());
    if std::fs::create_dir_all(directory).is_err() || directory.canonicalize().ok().as_deref() != Some(directory) { return Ok(false); }
    let backup = directory.join(format!("{}.stale-{}-{}", path.file_name().unwrap().to_string_lossy(),
        crate::util::epoch_s() as u64, uuid::Uuid::new_v4().simple()));
    if std::fs::rename(path, &backup).is_err() { return Ok(false); }
    if !backup.symlink_metadata().is_ok_and(|m| m.dev() == before.dev() && m.ino() == before.ino()) {
        // hard_link is the portable no-replace restore: a new Git lock always wins its own pathname.
        if std::fs::hard_link(&backup, path).is_ok() { let _ = std::fs::remove_file(&backup); }
        return Err(format!("repository copy needs attention: Git lock changed during recovery; inspect {} and {}", path.display(), backup.display()));
    }
    Ok(true)
}

/// Called under the common directory's sidecar, including the first stat and all process inspection.
async fn move_abandoned(path: &Path, roots: &[PathBuf], common: &Path) -> Result<bool, String> {
    let Some(before) = old_file(path) else { return Ok(false) };
    if !path.parent().and_then(|p| p.canonicalize().ok()).is_some_and(|p| roots.iter().any(|r| p.starts_with(r)))
        || !unused(path, &before, roots, false).await { return Ok(false); }
    let Some(after) = old_file(path) else { return Ok(false) };
    if (before.dev(), before.ino(), before.mtime(), before.mtime_nsec()) != (after.dev(), after.ino(), after.mtime(), after.mtime_nsec()) { return Ok(false); }
    quarantine(path, &before, Some(&common.join("tico-lock-backups")))
}

fn metadata_roots(argv: &[String], cwd: Option<&Path>) -> Option<(Vec<PathBuf>, PathBuf)> {
    let at = argv.windows(2).rfind(|p| p[0] == "-C").map(|p| Path::new(&p[1])).or(cwd)?.canonicalize().ok()?;
    if !owned(&at) { return None; }
    let git = at.join(".git");
    let roots = match git.symlink_metadata() {
        Ok(m) if m.is_dir() => vec![git.canonicalize().ok()?],
        Ok(m) if m.is_file() && m.len() < 4096 => {
            let text = std::fs::read_to_string(&git).ok()?;
            let admin = at.join(text.trim().strip_prefix("gitdir: ")?).canonicalize().ok()?;
            let back = std::fs::read_to_string(admin.join("gitdir")).ok()?;
            if Path::new(back.trim()).canonicalize().ok()? != git.canonicalize().ok()? { return None; }
            let common = std::fs::read_to_string(admin.join("commondir")).ok()?;
            let common = admin.join(common.trim()).canonicalize().ok()?;
            if admin.parent()? != common.join("worktrees").as_path() { return None; }
            vec![admin, common]
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound && at.join("HEAD").is_file() && at.join("objects").is_dir() => vec![at.clone()],
        _ => return None,
    };
    if !roots.iter().all(|r| owned(r)) { return None; }
    Some((roots, at))
}

fn process_roots(metadata: &[PathBuf], at: &Path) -> Option<Vec<PathBuf>> {
    let mut roots = metadata.to_vec();
    roots.push(at.to_path_buf());
    let common = metadata.last()?;
    if common.file_name().is_some_and(|n| n == ".git") {
        let main = common.parent()?;
        let (verified, _) = metadata_roots(&[], Some(main))?;
        if verified.as_slice() != std::slice::from_ref(common) { return None; }
        // Prepared ref transactions close their lock descriptors but still belong to this checkout.
        roots.push(main.to_path_buf());
    } else if at != common {
        // A nonstandard common directory does not establish where its main checkout lives.
        return None;
    }
    let worktrees = common.join("worktrees");
    match std::fs::read_dir(worktrees) {
        Ok(entries) => for entry in entries {
            let entry = entry.ok()?;
            let pointer = std::fs::read_to_string(entry.path().join("gitdir")).ok()?;
            roots.push(Path::new(pointer.trim()).parent()?.canonicalize().ok()?);
        },
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {},
        Err(_) => return None,
    }
    Some(roots)
}

/// Only the lock Git actually reports is eligible, never a sweep of a bot's checkout.
pub async fn recover(argv: &[String], cwd: Option<&Path>, stderr: &str) -> Result<bool, String> {
    if argv.first().is_none_or(|s| Path::new(s).file_name().is_none_or(|n| n != "git")) || !stderr.contains("File exists") { return Ok(false); }
    let Some((roots, at)) = metadata_roots(argv, cwd) else { return Ok(false) };
    let Some(_guard) = recovery_lock(roots.last().unwrap()) else { return Ok(false) };
    let Some(processes) = process_roots(&roots, &at) else { return Ok(false) };
    let re = regex::Regex::new(r"'([^'\n]+\.lock)'").unwrap();
    let mut paths: Vec<_> = re.captures_iter(stderr).map(|c| PathBuf::from(&c[1])).collect();
    let config = regex::Regex::new(r"could not lock config file ([^\n]+): File exists").unwrap();
    for cap in config.captures_iter(stderr) { paths.push(PathBuf::from(format!("{}.lock", cap[1].trim_matches('\'')))); }
    for path in paths {
        let path = if path.is_absolute() { path } else { at.join(path) };
        if !path.parent().and_then(|p| p.canonicalize().ok()).is_some_and(|p| roots.iter().any(|r| p.starts_with(r))) { continue; }
        if move_abandoned(&path, &processes, roots.last().unwrap()).await? { return Ok(true); }
    }
    Ok(false)
}

fn instruction_temporary(root: &Path, path: &Path) -> bool {
    let Some(parent) = path.parent() else { return false };
    let in_metadata = parent == root || parent.parent().is_some_and(|p| p.file_name().is_some_and(|n| n == "worktrees") && p.parent() == Some(root));
    let Some(name) = path.file_name().and_then(|n| n.to_str()).and_then(|n| n.strip_prefix("tico-instruction-link-")) else { return false };
    in_metadata && name.len() == 32 && name.bytes().all(|c| c.is_ascii_hexdigit())
}

/// A private config may be repaired only while no Git lock is owned or recent.
pub async fn idle(root: &Path) -> bool {
    let Some(_guard) = recovery_lock(root) else { return false };
    idle_locked(root).await
}

pub async fn idle_locked(root: &Path) -> bool {
    let Ok(root) = root.canonicalize() else { return false };
    if !owned(&root) { return false; }
    let at = if root.file_name().is_some_and(|s| s == ".git") { root.parent().unwrap() } else { &root };
    let Some(roots) = process_roots(std::slice::from_ref(&root), at) else { return false };
    let Ok(metadata) = root.metadata() else { return false };
    if !unused(&root, &metadata, &roots, false).await { return false; }
    let mut pending = vec![root.clone()];
    while let Some(dir) = pending.pop() {
        let Ok(entries) = std::fs::read_dir(dir) else { return false };
        for entry in entries {
            let Ok(entry) = entry else { return false };
            let path = entry.path();
            let Ok(m) = path.symlink_metadata() else { return false };
            if m.file_type().is_symlink() {
                if instruction_temporary(&root, &path) { continue; }
                return false;
            }
            if m.is_dir() { pending.push(path); }
            else if path.extension().is_some_and(|s| s == "lock") && !move_abandoned(&path, &roots, &root).await.unwrap_or(false) { return false; }
        }
    }
    true
}
#[cfg(test)]
mod tests {
    use super::*;

    fn age(path: &Path) {
        std::fs::File::open(path).unwrap().set_times(std::fs::FileTimes::new().set_modified(std::time::SystemTime::now() - OLD * 2)).unwrap();
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn inaccessible_bot_git_writers_block_recovery_but_model_processes_do_not() {
        let root = std::env::temp_dir().join(format!("tico-git-owner-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("comm"), "model-host\n").unwrap();
        assert!(!inaccessible_git(&root));
        std::fs::write(root.join("comm"), "git\n").unwrap();
        assert!(inaccessible_git(&root));
        std::fs::remove_file(root.join("comm")).unwrap();
        std::fs::create_dir(root.join("comm")).unwrap();
        assert!(inaccessible_git(&root), "unknown process names cannot authorize deleting a Git lock");
        std::fs::remove_dir_all(root).unwrap();
    }

    #[tokio::test]
    async fn an_abandoned_config_or_index_lock_is_preserved_and_git_retried() {
        let _guard = TEST_FIXTURES.lock().await;
        let root = std::env::temp_dir().join(format!("tico-gitlocks-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir(&root).unwrap();
        own(std::slice::from_ref(&root));
        let env = crate::safegit::clean_environment(&crate::env::process_environment());
        assert!(crate::gitrun::git(&root, &env, &["init", "-q"], 10).await.unwrap().ok());
        for (name, args) in [("config.lock", vec!["config", "test.recovered", "yes"]), ("index.lock", vec!["add", "draft"])] {
            std::fs::write(root.join("draft"), "work").unwrap();
            let lock = root.join(".git").join(name);
            std::fs::write(&lock, "abandoned").unwrap();
            age(&lock);
            let out = crate::gitrun::git(&root, &env, &args, 10).await.unwrap();
            assert!(out.ok(), "{}", out.stderr);
            assert!(!lock.exists());
        }
        std::fs::remove_dir_all(root).unwrap();
    }

    #[tokio::test]
    async fn a_task_worktrees_abandoned_index_lock_recovers_without_touching_the_base_index() {
        let _guard = TEST_FIXTURES.lock().await;
        let root = std::env::temp_dir().join(format!("tico-gitlocks-{}", uuid::Uuid::new_v4().simple()));
        let base = root.join("base");
        let task = root.join("task");
        std::fs::create_dir_all(&base).unwrap();
        own(std::slice::from_ref(&root));
        let env = crate::safegit::clean_environment(&crate::env::process_environment());
        assert!(crate::gitrun::git(&base, &env, &["init", "-q"], 10).await.unwrap().ok());
        std::fs::write(base.join("saved"), "base work\n").unwrap();
        assert!(crate::gitrun::git(&base, &env, &["add", "saved"], 10).await.unwrap().ok());
        assert!(crate::gitrun::git(&base, &env, &["-c", "user.name=Fixture", "-c", "user.email=fixture@acme.example", "commit", "-qm", "initial"], 10).await.unwrap().ok());
        assert!(crate::gitrun::git(&base, &env, &["worktree", "add", "--detach", task.to_str().unwrap()], 10).await.unwrap().ok());
        let index = std::fs::read(base.join(".git/index")).unwrap();
        let found = crate::gitrun::git(&task, &env, &["rev-parse", "--git-path", "index.lock"], 10).await.unwrap();
        let lock = PathBuf::from(found.stdout.trim());
        std::fs::write(&lock, "abandoned").unwrap();
        age(&lock);
        std::fs::write(task.join("draft"), "task work\n").unwrap();
        let out = crate::gitrun::git(&task, &env, &["add", "draft"], 10).await.unwrap();
        assert!(out.ok(), "{}", out.stderr);
        assert!(!lock.exists());
        assert_eq!(std::fs::read(base.join(".git/index")).unwrap(), index);
        assert_eq!(std::fs::read_to_string(task.join("draft")).unwrap(), "task work\n");
        std::fs::remove_dir_all(root).unwrap();
    }

    #[tokio::test]
    async fn linked_recovery_keeps_a_prepared_main_checkout_transaction() {
        let _guard = TEST_FIXTURES.lock().await;
        use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
        use std::process::Stdio;
        let root = std::env::temp_dir().join(format!("tico-prepared-ref-{}", uuid::Uuid::new_v4().simple()));
        let base = root.join("base");
        let task = root.join("task");
        std::fs::create_dir_all(&base).unwrap();
        own(std::slice::from_ref(&root));
        let env = crate::safegit::clean_environment(&crate::env::process_environment());
        assert!(crate::gitrun::git(&base, &env, &["init", "-q"], 10).await.unwrap().ok());
        assert!(crate::gitrun::git(&base, &env, &["-c", "user.name=Fixture", "-c", "user.email=fixture@acme.example", "commit", "--allow-empty", "-qm", "initial"], 10).await.unwrap().ok());
        assert!(crate::gitrun::git(&base, &env, &["worktree", "add", "--detach", task.to_str().unwrap()], 10).await.unwrap().ok());
        let head = crate::gitrun::git(&base, &env, &["rev-parse", "HEAD"], 10).await.unwrap().stdout;
        let mut owner = tokio::process::Command::new("git").args(["update-ref", "--stdin"]).current_dir(&base)
            .env_clear().envs(&env).stdin(Stdio::piped()).stdout(Stdio::piped()).kill_on_drop(true).spawn().unwrap();
        let mut input = owner.stdin.take().unwrap();
        let mut lines = BufReader::new(owner.stdout.take().unwrap()).lines();
        input.write_all(format!("start\nupdate refs/heads/prepared {}\nprepare\n", head.trim()).as_bytes()).await.unwrap();
        for expected in ["start: ok", "prepare: ok"] {
            assert_eq!(tokio::time::timeout(Duration::from_secs(5), lines.next_line()).await.unwrap().unwrap().as_deref(), Some(expected));
        }
        let lock = base.join(".git/refs/heads/prepared.lock");
        age(&lock);
        let before = lock.metadata().unwrap();
        #[cfg(target_os = "linux")]
        for fd in std::fs::read_dir(format!("/proc/{}/fd", owner.id().unwrap())).unwrap() {
            let metadata = std::fs::metadata(fd.unwrap().path());
            assert!(!metadata.is_ok_and(|m| m.dev() == before.dev() && m.ino() == before.ino()), "prepare must close the ref lock descriptor");
        }
        let argv = vec!["git".into(), "-C".into(), task.display().to_string(), "update-ref".into()];
        assert!(!recover(&argv, None, &format!("Unable to create '{}': File exists", lock.display())).await.unwrap());
        assert_eq!(lock.metadata().unwrap().ino(), before.ino());
        assert!(!base.join(".git/tico-lock-backups").exists());
        input.write_all(b"abort\n").await.unwrap();
        drop(input);
        assert!(tokio::time::timeout(Duration::from_secs(5), owner.wait()).await.unwrap().unwrap().success());
        assert!(!lock.exists());
        // Unverified common directories cannot rule out a main-checkout owner.
        assert!(process_roots(&[root.join("nonstandard")], &task).is_none());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[tokio::test]
    async fn a_fresh_or_open_lock_and_a_lock_outside_git_metadata_stay() {
        let _guard = TEST_FIXTURES.lock().await;
        let root = std::env::temp_dir().join(format!("tico-gitlocks-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(root.join(".git")).unwrap();
        own(std::slice::from_ref(&root));
        let lock = root.join(".git/config.lock");
        let argv = vec!["git".into(), "-C".into(), root.display().to_string(), "config".into()];
        let error = format!("Unable to create '{}': File exists", lock.display());
        std::fs::write(&lock, "recent").unwrap();
        assert!(!recover(&argv, None, &error).await.unwrap());
        age(&lock);
        let writer = std::fs::File::open(&lock).unwrap();
        assert!(!recover(&argv, None, &error).await.unwrap());
        assert!(!idle(&root.join(".git")).await);
        drop(writer);
        assert!(recover(&argv, None, &error).await.unwrap());
        let outside = root.join("draft.lock");
        std::fs::write(&outside, "bot work").unwrap();
        age(&outside);
        assert!(!recover(&argv, None, &format!("Unable to create '{}': File exists", outside.display())).await.unwrap());
        std::os::unix::fs::symlink(&outside, &lock).unwrap();
        assert!(!recover(&argv, None, &error).await.unwrap());
        assert_eq!(std::fs::read_to_string(outside).unwrap(), "bot work");
        std::fs::remove_dir_all(root).unwrap();
    }
    #[tokio::test]
    async fn an_old_lock_outside_the_runners_roots_is_never_touched() {
        let _guard = TEST_FIXTURES.lock().await;
        let root = std::env::temp_dir().join(format!("tico-outside-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(root.join(".git")).unwrap();
        let lock = root.join(".git/index.lock");
        std::fs::write(&lock, "outside work").unwrap();
        age(&lock);
        let argv = vec!["git".into(), "-C".into(), root.display().to_string(), "add".into()];
        assert!(!recover(&argv, None, &format!("Unable to create '{}': File exists", lock.display())).await.unwrap());
        assert_eq!(std::fs::read_to_string(&lock).unwrap(), "outside work");
        assert!(!root.join(".git/tico-lock-recovery").exists());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn a_git_process_owns_closed_locks_by_cwd_arguments_and_environment() {
        let root = std::env::temp_dir().join(format!("tico-process-{}", uuid::Uuid::new_v4().simple()));
        let repo = root.join("repo");
        let admin = repo.join(".git");
        let process = root.join("123");
        std::fs::create_dir_all(&admin).unwrap();
        std::fs::create_dir(&process).unwrap();
        std::fs::write(process.join("environ"), "").unwrap();
        let roots = vec![repo.clone(), admin.clone()];
        std::os::unix::fs::symlink(&repo, process.join("cwd")).unwrap();
        std::fs::write(process.join("cmdline"), "git\0commit\0").unwrap();
        assert_eq!(git_related(&process, &roots), Some(true));
        std::fs::remove_file(process.join("cwd")).unwrap();
        std::os::unix::fs::symlink(&root, process.join("cwd")).unwrap();
        for args in [format!("git\0-C\0{}\0commit\0", repo.display()), format!("git\0--git-dir\0{}\0commit\0", admin.display()), format!("git\0--git-dir={}\0commit\0", admin.display())] {
            std::fs::write(process.join("cmdline"), args).unwrap();
            assert_eq!(git_related(&process, &roots), Some(true));
        }
        std::fs::write(process.join("cmdline"), "git\0commit\0").unwrap();
        std::fs::write(process.join("environ"), format!("GIT_DIR={}\0", admin.display())).unwrap();
        assert_eq!(git_related(&process, &roots), Some(true));
        std::fs::write(process.join("environ"), "").unwrap();
        assert_eq!(git_related(&process, &roots), Some(false));
        std::fs::remove_file(process.join("environ")).unwrap();
        assert_eq!(git_related(&process, &roots), None);
        assert!(!exited(&process), "unreadable live Git remains a possible owner");
        std::fs::write(process.join("stat"), "123 (git) Z 1 0 0\n").unwrap();
        assert!(exited(&process), "a zombie cannot own a closed lock");
        std::fs::write(process.join("stat"), "123 (git) S 1 0 0\n").unwrap();
        assert!(!exited(&process));
        std::fs::remove_dir_all(root).unwrap();
    }

    #[tokio::test]
    async fn pythons_sidecar_lock_blocks_rust_recovery_and_the_stale_inode_is_kept() {
        let _guard = TEST_FIXTURES.lock().await;
        use std::io::{BufRead, BufReader};
        use std::process::{Command, Stdio};
        let root = std::env::temp_dir().join(format!("tico-sidecar-{}", uuid::Uuid::new_v4().simple()));
        let metadata = root.join(".git");
        std::fs::create_dir_all(&metadata).unwrap();
        own(std::slice::from_ref(&root));
        let lock = metadata.join("index.lock");
        std::fs::write(&lock, "preserved").unwrap();
        let argv = vec!["git".into(), "-C".into(), root.display().to_string(), "add".into()];
        let error = format!("Unable to create '{}': File exists", lock.display());
        std::fs::File::open(&lock).unwrap().set_times(std::fs::FileTimes::new().set_modified(std::time::SystemTime::now() - Duration::from_secs(600))).unwrap();
        assert!(!recover(&argv, None, &error).await.unwrap(), "ten minutes is not abandoned");
        age(&lock);
        let inode = lock.metadata().unwrap().ino();
        let mut python = Command::new("python3").args(["-c", "import fcntl,sys; f=open(sys.argv[1],'w'); fcntl.flock(f,fcntl.LOCK_EX); print('held',flush=True); sys.stdin.read()"])
            .arg(metadata.join("tico-lock-recovery")).stdin(Stdio::piped()).stdout(Stdio::piped()).spawn().unwrap();
        let mut line = String::new();
        BufReader::new(python.stdout.take().unwrap()).read_line(&mut line).unwrap();
        assert_eq!(line.trim(), "held");
        assert!(!recover(&argv, None, &error).await.unwrap());
        drop(python.stdin.take());
        python.wait().unwrap();
        assert!(recover(&argv, None, &error).await.unwrap());
        let stale: Vec<_> = std::fs::read_dir(metadata.join("tico-lock-backups")).unwrap().flatten().filter(|e| e.file_name().to_string_lossy().starts_with("index.lock.stale-")).collect();
        assert_eq!(stale.len(), 1);
        assert_eq!(stale[0].metadata().unwrap().ino(), inode);
        assert_eq!(std::fs::read_to_string(stale[0].path()).unwrap(), "preserved");
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn a_replaced_lock_is_restored_instead_of_unlinked() {
        let root = std::env::temp_dir().join(format!("tico-race-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir(&root).unwrap();
        let root = root.canonicalize().unwrap();
        let lock = root.join("index.lock");
        std::fs::write(&lock, "old lock").unwrap();
        let before = lock.metadata().unwrap();
        std::fs::rename(&lock, root.join("old")).unwrap();
        std::fs::write(&lock, "new writer").unwrap();
        assert!(quarantine(&lock, &before, None).unwrap_err().contains("Git lock changed during recovery"));
        assert_eq!(std::fs::read_to_string(&lock).unwrap(), "new writer");
        assert_eq!(std::fs::read_to_string(root.join("old")).unwrap(), "old lock");
        std::fs::remove_dir_all(root).unwrap();
    }

}
