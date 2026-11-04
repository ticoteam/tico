//! Recover locks left by interrupted Git commands, only after their writers are gone.

use std::path::{Path, PathBuf};
use std::os::unix::fs::MetadataExt;
use std::time::Duration;

const OLD: Duration = Duration::from_secs(5 * 60);

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

#[cfg(target_os = "linux")]
async fn unused(path: &Path, lock: &std::fs::Metadata) -> bool {
    let Ok(processes) = std::fs::read_dir("/proc") else { return false };
    for process in processes {
        let Ok(process) = process else { return false };
        if !process.file_name().to_string_lossy().bytes().all(|c| c.is_ascii_digit()) { continue; }
        let files = match std::fs::read_dir(process.path().join("fd")) {
            Ok(files) => files,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
            // Docker's supervisor cannot inspect the bot user's descriptors. Unrelated model processes must
            // not hold recovery back forever; an inaccessible Git process (or unknown name) does.
            Err(_) if !inaccessible_git(&process.path()) => continue,
            Err(_) => return false,
        };
        for file in files {
            let Ok(file) = file else { return false };
            match std::fs::metadata(file.path()) {
                Ok(m) if m.dev() == lock.dev() && m.ino() == lock.ino() => return false,
                Err(e) if e.kind() != std::io::ErrorKind::NotFound && inaccessible_git(&process.path()) => return false,
                _ => {}
            }
        }
    }
    let _ = path;
    true
}

#[cfg(not(target_os = "linux"))]
async fn unused(path: &Path, _lock: &std::fs::Metadata) -> bool {
    let mut cmd = tokio::process::Command::new("/usr/sbin/lsof");
    cmd.args(["-F", "p", "--"]).arg(path).kill_on_drop(true);
    match tokio::time::timeout(Duration::from_secs(5), cmd.output()).await {
        Ok(Ok(out)) => out.status.code() == Some(1) && out.stdout.is_empty() && out.stderr.is_empty(),
        _ => false,
    }
}

/// Refuse fresh, open, replaced or linked locks. The canonical parent must stay inside Git metadata.
async fn remove_abandoned(path: &Path, root: &Path) -> bool {
    let Some(before) = old_file(path) else { return false };
    if !path.parent().and_then(|p| p.canonicalize().ok()).is_some_and(|p| p.starts_with(root)) || !unused(path, &before).await {
        return false;
    }
    let Some(after) = old_file(path) else { return false };
    if (before.dev(), before.ino(), before.mtime(), before.mtime_nsec()) != (after.dev(), after.ino(), after.mtime(), after.mtime_nsec()) {
        return false;
    }
    std::fs::remove_file(path).is_ok()
}

fn metadata_roots(argv: &[String], cwd: Option<&Path>) -> Option<(Vec<PathBuf>, PathBuf)> {
    let at = argv.windows(2).rfind(|p| p[0] == "-C").map(|p| Path::new(&p[1])).or(cwd)?;
    let git = at.join(".git");
    let roots = match git.symlink_metadata() {
        Ok(m) if m.is_dir() => vec![git.canonicalize().ok()?],
        Ok(m) if m.is_file() && m.len() < 4096 => {
            let text = std::fs::read_to_string(&git).ok()?;
            let admin = at.join(text.trim().strip_prefix("gitdir: ")?).canonicalize().ok()?;
            // Accept only a registered worktree's Git directory, never an arbitrary gitdir pointer.
            let back = std::fs::read_to_string(admin.join("gitdir")).ok()?;
            if Path::new(back.trim()).canonicalize().ok()? != git.canonicalize().ok()? { return None; }
            let common = std::fs::read_to_string(admin.join("commondir")).ok()?;
            let common = admin.join(common.trim()).canonicalize().ok()?;
            if admin.parent()? != common.join("worktrees").as_path() { return None; }
            vec![admin, common]
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound && at.join("HEAD").is_file() && at.join("objects").is_dir() => vec![at.canonicalize().ok()?],
        _ => return None,
    };
    Some((roots, at.canonicalize().ok()?))
}

/// Only the lock Git actually reports is eligible, never a sweep of a bot's checkout.
pub async fn recover(argv: &[String], cwd: Option<&Path>, stderr: &str) -> bool {
    if Path::new(&argv[0]).file_name().is_none_or(|n| n != "git") || !stderr.contains("File exists") {
        return false;
    }
    let Some((roots, at)) = metadata_roots(argv, cwd) else { return false };
    let re = regex::Regex::new(r"'([^'\n]+\.lock)'").unwrap();
    let mut paths = vec![];
    for cap in re.captures_iter(stderr) {
        paths.push(PathBuf::from(&cap[1]));
    }
    // `git config` names its config file instead of the lock it tried to create.
    let config = regex::Regex::new(r"could not lock config file ([^\n]+): File exists").unwrap();
    for cap in config.captures_iter(stderr) {
        let path = PathBuf::from(cap[1].trim_matches('\''));
        paths.push(PathBuf::from(format!("{}.lock", path.display())));
    }
    for path in paths {
        let path = if path.is_absolute() { path } else { at.join(path) };
        for root in &roots {
            if remove_abandoned(&path, root).await { return true; }
        }
    }
    false
}

fn instruction_temporary(root: &Path, path: &Path) -> bool {
    let Some(parent) = path.parent() else { return false };
    let in_metadata = parent == root || parent.parent().is_some_and(|p| p.file_name().is_some_and(|n| n == "worktrees") && p.parent() == Some(root));
    let Some(name) = path.file_name().and_then(|n| n.to_str()).and_then(|n| n.strip_prefix("tico-instruction-link-")) else { return false };
    in_metadata && name.len() == 32 && name.bytes().all(|c| c.is_ascii_hexdigit())
}

/// A private mirror/config may be rebuilt only while no Git lock still has a writer or is recent.
pub async fn idle(root: &Path) -> bool {
    let Ok(root) = root.canonicalize() else { return false };
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
            else if path.extension().is_some_and(|s| s == "lock") && !remove_abandoned(&path, &root).await { return false; }
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
    async fn an_abandoned_config_or_index_lock_is_removed_and_git_retried() {
        let root = std::env::temp_dir().join(format!("tico-gitlocks-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir(&root).unwrap();
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
        let root = std::env::temp_dir().join(format!("tico-gitlocks-{}", uuid::Uuid::new_v4().simple()));
        let base = root.join("base");
        let task = root.join("task");
        std::fs::create_dir_all(&base).unwrap();
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
    async fn a_fresh_or_open_lock_and_a_lock_outside_git_metadata_stay() {
        let root = std::env::temp_dir().join(format!("tico-gitlocks-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(root.join(".git")).unwrap();
        let lock = root.join(".git/config.lock");
        let argv = vec!["git".into(), "-C".into(), root.display().to_string(), "config".into()];
        let error = format!("Unable to create '{}': File exists", lock.display());
        std::fs::write(&lock, "recent").unwrap();
        assert!(!recover(&argv, None, &error).await);
        age(&lock);
        let writer = std::fs::File::open(&lock).unwrap();
        assert!(!recover(&argv, None, &error).await);
        assert!(!idle(&root.join(".git")).await);
        drop(writer);
        assert!(recover(&argv, None, &error).await);
        let outside = root.join("draft.lock");
        std::fs::write(&outside, "bot work").unwrap();
        age(&outside);
        assert!(!recover(&argv, None, &format!("Unable to create '{}': File exists", outside.display())).await);
        std::os::unix::fs::symlink(&outside, &lock).unwrap();
        assert!(!recover(&argv, None, &error).await);
        assert_eq!(std::fs::read_to_string(outside).unwrap(), "bot work");
        std::fs::remove_dir_all(root).unwrap();
    }
}
