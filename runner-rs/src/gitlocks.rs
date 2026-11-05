//! Serialize the runner's Git commands and report old locks. Other Git writers do not use this gate,
//! so age never authorizes removing or moving their locks.

use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::os::unix::io::AsRawFd;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Duration;

const OLD: Duration = Duration::from_secs(30 * 60);
static OWNED: Mutex<Vec<PathBuf>> = Mutex::new(Vec::new());

#[cfg(test)]
pub static TEST_FIXTURES: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

/// Only registered runner paths receive a gate or lock report.
pub fn own(roots: &[PathBuf]) {
    OWNED.lock().unwrap().extend(roots.iter().filter(|r| !r.as_os_str().is_empty()).cloned());
}

pub fn owned(path: &Path) -> bool {
    let Ok(path) = path.canonicalize() else { return false };
    OWNED.lock().unwrap().iter().filter_map(|r| r.canonicalize().ok()).any(|r| path.starts_with(r))
}

/// The file is never unlinked: both engines must flock the same inode.
pub fn recovery_lock(common: &Path) -> Option<std::fs::File> {
    if !owned(common) { return None; }
    let file = crate::regular_file::open(&common.join("tico-lock-recovery"),
        std::fs::OpenOptions::new().write(true).create(true).truncate(false).mode(0o600), crate::regular_file::SMALL).ok()?;
    let m = file.metadata().ok()?;
    if !m.is_file() || m.nlink() != 1 { return None; }
    (unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0).then_some(file)
}

pub fn owned_roots() -> Vec<PathBuf> { OWNED.lock().unwrap().clone() }

/// The native install's linked checkout can store its metadata outside the runner folders.
pub fn own_checkout(root: &Path) {
    if !owned(root) { return; }
    if let Some(roots) = checkout_metadata(root) { own(&roots); }
}

fn stale_note(path: &Path) -> Option<String> {
    let m = path.symlink_metadata().ok()?;
    if !m.is_file() { return None; }
    let age = m.modified().ok()?.elapsed().ok()?;
    if age < OLD { return None; }
    let quoted = format!("'{}'", path.to_string_lossy().replace('\'', "'\"'\"'"));
    Some(format!("repository copy needs attention: Git lock is {} minutes old. Check no Git is running, then run: rm -- {quoted}", age.as_secs() / 60))
}

fn checkout_metadata(at: &Path) -> Option<Vec<PathBuf>> {
    let git = at.join(".git");
    Some(match git.symlink_metadata() {
        Ok(m) if m.is_dir() => vec![git.canonicalize().ok()?],
        Ok(m) if m.is_file() && m.len() < 4096 => {
            let text = crate::regular_file::text(&git, crate::regular_file::SMALL).ok()?;
            let admin = at.join(text.trim().strip_prefix("gitdir: ")?).canonicalize().ok()?;
            let back = crate::regular_file::text(admin.join("gitdir"), crate::regular_file::SMALL).ok()?;
            if Path::new(back.trim()).canonicalize().ok()? != git.canonicalize().ok()? { return None; }
            let common = crate::regular_file::text(admin.join("commondir"), crate::regular_file::SMALL).ok()?;
            let common = admin.join(common.trim()).canonicalize().ok()?;
            if admin.parent()? != common.join("worktrees").as_path() { return None; }
            vec![admin, common]
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound && at.join("HEAD").is_file() && at.join("objects").is_dir() => vec![at.to_path_buf()],
        _ => return None,
    })
}

fn metadata_roots(argv: &[String], cwd: Option<&Path>) -> Option<(Vec<PathBuf>, PathBuf)> {
    let at = argv.windows(2).rfind(|p| p[0] == "-C").map(|p| Path::new(&p[1])).or(cwd)?.canonicalize().ok()?;
    if !owned(&at) { return None; }
    let roots = checkout_metadata(&at)?;
    if !roots.iter().all(|r| owned(r)) { return None; }
    Some((roots, at))
}

/// The runner and updater share this gate; ordinary Git writers use Git's own locks.
pub struct CommandLock {
    _file: std::fs::File,
}

pub async fn command_lock(argv: &[String], cwd: Option<&Path>, timeout_s: u64) -> Result<Option<CommandLock>, String> {
    let Some((roots, _)) = metadata_roots(argv, cwd) else { return Ok(None) };
    let common = roots.last().unwrap();
    let until = std::time::Instant::now() + Duration::from_secs(timeout_s.min(10));
    loop {
        if let Some(file) = recovery_lock(common) {
            return Ok(Some(CommandLock { _file: file }));
        }
        if std::time::Instant::now() >= until {
            return Err("repository copy needs attention: Git work is busy or its lock cannot be checked".into());
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
}

/// Add a useful error without changing the lock or retrying the failed command.
pub fn report(argv: &[String], cwd: Option<&Path>, stderr: &str) -> Option<String> {
    if argv.first().is_none_or(|s| Path::new(s).file_name().is_none_or(|n| n != "git")) || !stderr.contains("File exists") { return None; }
    let (roots, at) = metadata_roots(argv, cwd)?;
    let re = regex::Regex::new(r"'([^'\n]+\.lock)'").unwrap();
    let mut paths: Vec<_> = re.captures_iter(stderr).map(|c| PathBuf::from(&c[1])).collect();
    let config = regex::Regex::new(r"could not lock config file ([^\n]+): File exists").unwrap();
    for cap in config.captures_iter(stderr) { paths.push(PathBuf::from(format!("{}.lock", cap[1].trim_matches('\'')))); }
    for path in paths {
        let path = if path.is_absolute() { path } else { at.join(path) };
        if !path.parent().and_then(|p| p.canonicalize().ok()).is_some_and(|p| roots.iter().any(|r| p.starts_with(r))) { continue; }
        if let Some(note) = stale_note(&path) { return Some(note); }
    }
    None
}

fn instruction_temporary(root: &Path, path: &Path) -> bool {
    let Some(parent) = path.parent() else { return false };
    let in_metadata = parent == root || parent.parent().is_some_and(|p| p.file_name().is_some_and(|n| n == "worktrees") && p.parent() == Some(root));
    let Some(name) = path.file_name().and_then(|n| n.to_str()).and_then(|n| n.strip_prefix("tico-instruction-link-")) else { return false };
    in_metadata && name.len() == 32 && name.bytes().all(|c| c.is_ascii_hexdigit())
}

pub fn check(root: &Path) -> Result<(), String> {
    let _guard = recovery_lock(root).ok_or_else(|| format!("repository copy needs attention: {}: Git work is busy or its lock cannot be checked", root.display()))?;
    check_locked(root)
}

/// Refuse repair at every Git lock, including old ones. This is a snapshot, not writer exclusion.
pub fn check_locked(root: &Path) -> Result<(), String> {
    let inspect = || format!("repository copy needs attention: {}: Git metadata cannot be checked", root.display());
    let root = root.canonicalize().map_err(|_| inspect())?;
    if !owned(&root) { return Err(inspect()); }
    let mut pending = vec![root.clone()];
    while let Some(dir) = pending.pop() {
        for entry in std::fs::read_dir(dir).map_err(|_| inspect())? {
            let path = entry.map_err(|_| inspect())?.path();
            let m = path.symlink_metadata().map_err(|_| inspect())?;
            if m.file_type().is_symlink() {
                if instruction_temporary(&root, &path) { continue; }
                return Err(inspect());
            }
            if m.is_dir() { pending.push(path); }
            else if path.extension().is_some_and(|s| s == "lock") {
                return Err(stale_note(&path).unwrap_or_else(|| format!("repository copy needs attention: {}: Git lock {} is present; retry when Git has finished", root.display(), path.display())));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn age(path: &Path) {
        std::fs::File::open(path).unwrap().set_times(std::fs::FileTimes::new().set_modified(std::time::SystemTime::now() - OLD * 2)).unwrap();
    }

    #[tokio::test]
    async fn registration_and_recovery_sidecar_access_refuse_special_files() {
        if !crate::regular_file::in_child("gitlocks::tests::registration_and_recovery_sidecar_access_refuse_special_files").await { return; }
        let root = std::env::temp_dir().join(format!("tico-git-registration-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir(&root).unwrap();
        let root = root.canonicalize().unwrap();
        let common = root.join("base/.git");
        let admin = common.join("worktrees/task");
        let task = root.join("task");
        std::fs::create_dir_all(&admin).unwrap();
        std::fs::create_dir(&task).unwrap();
        own(std::slice::from_ref(&root));
        let pointers = [(task.join(".git"), format!("gitdir: {}\n", admin.display())),
            (admin.join("gitdir"), format!("{}\n", task.join(".git").display())), (admin.join("commondir"), "../..\n".into())];
        for (path, content) in &pointers { std::fs::write(path, content).unwrap(); }
        assert!(metadata_roots(&[], Some(&task)).is_some());
        for (path, content) in &pointers {
            std::fs::remove_file(path).unwrap();
            crate::regular_file::fifo(path);
            assert!(metadata_roots(&[], Some(&task)).is_none());
            std::fs::remove_file(path).unwrap();
            std::fs::write(path, content).unwrap();
        }
        let sidecar = common.join("tico-lock-recovery");
        crate::regular_file::fifo(&sidecar);
        assert!(recovery_lock(&common).is_none());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[tokio::test]
    async fn old_config_index_and_ref_locks_stay_and_the_failed_git_command_reports_them() {
        let _guard = TEST_FIXTURES.lock().await;
        let root = std::env::temp_dir().join(format!("tico-gitlocks-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir(&root).unwrap();
        own(std::slice::from_ref(&root));
        let env = crate::safegit::clean_environment(&crate::env::process_environment());
        assert!(crate::gitrun::git(&root, &env, &["init", "-q"], 10).await.unwrap().ok());
        assert!(crate::gitrun::git(&root, &env, &["-c", "user.name=Fixture", "-c", "user.email=fixture@acme.example", "commit", "--allow-empty", "-qm", "initial"], 10).await.unwrap().ok());
        for (name, args) in [("config.lock", vec!["config", "test.recovered", "yes"]), ("index.lock", vec!["add", "draft"]),
            ("refs/heads/blocked.lock", vec!["update-ref", "refs/heads/blocked", "HEAD"])] {
            std::fs::write(root.join("draft"), "work").unwrap();
            let lock = root.join(".git").join(name);
            std::fs::write(&lock, "still owned").unwrap();
            age(&lock);
            let before = lock.metadata().unwrap();
            // A closed descriptor is no reason to take another writer's lock, either.
            for open in [true, false] {
                let writer = open.then(|| std::fs::File::open(&lock).unwrap());
                let out = crate::gitrun::git(&root, &env, &args, 10).await.unwrap();
                assert!(!out.ok(), "{}", out.stderr);
                assert!(out.stderr.contains("minutes old") && out.stderr.contains("Check no Git is running, then run: rm --"), "{}", out.stderr);
                assert!(out.stderr.contains(&root.display().to_string()) && out.stderr.contains(&lock.display().to_string()));
                assert_eq!(lock.metadata().unwrap().ino(), before.ino());
                assert_eq!(std::fs::read_to_string(&lock).unwrap(), "still owned");
                assert!(check(&root.join(".git")).unwrap_err().contains(&lock.display().to_string()));
                drop(writer);
            }
            std::fs::remove_file(lock).unwrap();
        }
        assert!(!root.join(".git/tico-lock-backups").exists());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[tokio::test]
    async fn linked_worktree_lock_stays_and_base_index_is_unchanged() {
        let _guard = TEST_FIXTURES.lock().await;
        let root = std::env::temp_dir().join(format!("tico-gitlocks-{}", uuid::Uuid::new_v4().simple()));
        let base = root.join("base");
        let task = root.join("task");
        std::fs::create_dir_all(&base).unwrap();
        own(std::slice::from_ref(&task));
        let env = crate::safegit::clean_environment(&crate::env::process_environment());
        assert!(crate::gitrun::git(&base, &env, &["init", "-q"], 10).await.unwrap().ok());
        std::fs::write(base.join("saved"), "base work\n").unwrap();
        assert!(crate::gitrun::git(&base, &env, &["add", "saved"], 10).await.unwrap().ok());
        assert!(crate::gitrun::git(&base, &env, &["-c", "user.name=Fixture", "-c", "user.email=fixture@acme.example", "commit", "-qm", "initial"], 10).await.unwrap().ok());
        assert!(crate::gitrun::git(&base, &env, &["worktree", "add", "--detach", task.to_str().unwrap()], 10).await.unwrap().ok());
        assert!(!owned(&base.join(".git")));
        assert!(metadata_roots(&[], Some(&task)).is_none());
        own_checkout(&task);
        let (roots, _) = metadata_roots(&[], Some(&task)).unwrap();
        assert!(roots.iter().all(|r| owned(r)));
        assert_eq!(roots.last().unwrap(), &base.join(".git").canonicalize().unwrap());
        let index = std::fs::read(base.join(".git/index")).unwrap();
        let found = crate::gitrun::git(&task, &env, &["rev-parse", "--git-path", "index.lock"], 10).await.unwrap();
        let lock = PathBuf::from(found.stdout.trim());
        std::fs::write(&lock, "worktree writer").unwrap();
        age(&lock);
        let before = lock.metadata().unwrap();
        std::fs::write(task.join("draft"), "task work\n").unwrap();
        let out = crate::gitrun::git(&task, &env, &["add", "draft"], 10).await.unwrap();
        assert!(!out.ok() && out.stderr.contains("minutes old"), "{}", out.stderr);
        assert!(out.stderr.contains(&lock.display().to_string()));
        let error = crate::worktrees::git(&task, &env, &["add", "draft"], true).await.unwrap_err().text();
        assert!(error.contains(&lock.display().to_string()) && error.contains("run: rm --"), "{error}");
        assert_eq!(lock.metadata().unwrap().ino(), before.ino());
        assert_eq!(std::fs::read(base.join(".git/index")).unwrap(), index);
        assert_eq!(std::fs::read_to_string(task.join("draft")).unwrap(), "task work\n");
        std::fs::remove_dir_all(root).unwrap();
    }

    #[tokio::test]
    async fn reports_are_read_only_and_recheck_a_replaced_lock() {
        let _guard = TEST_FIXTURES.lock().await;
        let root = std::env::temp_dir().join(format!("tico-git-report-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(root.join(".git")).unwrap();
        own(std::slice::from_ref(&root));
        let lock = root.join(".git/index.lock");
        std::fs::write(&lock, "recent").unwrap();
        let argv = vec!["git".into(), "-C".into(), root.display().to_string(), "add".into()];
        let error = format!("Unable to create '{}': File exists", lock.display());
        assert!(report(&argv, None, &error).is_none());
        age(&lock);
        assert!(report(&argv, None, &error).is_some());
        std::fs::rename(&lock, root.join("old")).unwrap();
        std::fs::write(&lock, "replacement writer").unwrap();
        assert!(report(&argv, None, &error).is_none());
        assert_eq!(std::fs::read_to_string(&lock).unwrap(), "replacement writer");
        assert!(!root.join(".git/tico-lock-recovery").exists());
        let outside = root.join("draft.lock");
        std::fs::write(&outside, "bot work").unwrap();
        age(&outside);
        assert!(report(&argv, None, &format!("Unable to create '{}': File exists", outside.display())).is_none());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[tokio::test]
    async fn runner_git_and_updater_share_the_common_repository_gate() {
        let _guard = TEST_FIXTURES.lock().await;
        let root = std::env::temp_dir().join(format!("tico-git-gate-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir(&root).unwrap();
        let root = root.canonicalize().unwrap();
        let base = root.join("base");
        let common = base.join(".git");
        let task = root.join("task");
        let admin = common.join("worktrees/task");
        std::fs::create_dir_all(&admin).unwrap();
        std::fs::create_dir(&task).unwrap();
        std::fs::write(task.join(".git"), format!("gitdir: {}\n", admin.display())).unwrap();
        std::fs::write(admin.join("gitdir"), format!("{}\n", task.join(".git").display())).unwrap();
        std::fs::write(admin.join("commondir"), "../..\n").unwrap();
        own(std::slice::from_ref(&root));
        let argv = vec!["git".into(), "-C".into(), task.display().to_string(), "config".into()];
        let gate = command_lock(&argv, None, 0).await.unwrap().unwrap();
        assert!(recovery_lock(&common).is_none(), "the base and its linked worktrees must use one gate");
        let env = crate::safegit::clean_environment(&crate::env::process_environment());
        assert!(matches!(crate::gitrun::git(&base, &env, &["config", "test.blocked", "yes"], 0).await, Err(crate::gitrun::Fault::Io(_))));
        drop(gate);
        let mut python = tokio::process::Command::new("python3").args(["-c", "import fcntl,sys; f=open(sys.argv[1],'w'); fcntl.flock(f,fcntl.LOCK_EX); print('held',flush=True); sys.stdin.read()"])
            .arg(common.join("tico-lock-recovery")).stdin(std::process::Stdio::piped()).stdout(std::process::Stdio::piped()).kill_on_drop(true).spawn().unwrap();
        use tokio::io::{AsyncBufReadExt, BufReader};
        assert_eq!(BufReader::new(python.stdout.take().unwrap()).lines().next_line().await.unwrap().as_deref(), Some("held"));
        assert!(command_lock(&argv, None, 0).await.is_err());
        drop(python.stdin.take());
        assert!(python.wait().await.unwrap().success());
        assert!(command_lock(&argv, None, 0).await.unwrap().is_some());
        std::fs::remove_dir_all(root).unwrap();
    }
}
