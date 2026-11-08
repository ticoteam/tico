//! Contract scenarios from runner/tests/test_worktrees.py, test_task_sessions.py, test_shared_bots.py and
//! test_fetch_repository.py, with real git against local bare repositories (no GitHub, no network).

use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use crate::env::Env;
use crate::{execute, fetchrepo, sessions, worktrees};

struct Tmp(PathBuf);

impl Tmp {
    fn new() -> Tmp {
        let dir = std::env::temp_dir().join(format!("tico-git-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        Tmp(dir.canonicalize().unwrap())
    }
}

impl Drop for Tmp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// Git for setting fixtures up, with no user or system config.
fn sh(dir: &Path, args: &[&str]) -> String {
    let out = std::process::Command::new("git")
        .args(["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false"])
        .args(args)
        .current_dir(dir)
        .env_clear()
        .env("PATH", "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin")
        .env("HOME", dir)
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .output()
        .unwrap();
    assert!(out.status.success(), "git {args:?}: {}", String::from_utf8_lossy(&out.stderr));
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

/// A turn's environment as the runner gives git: helper-style config (so no machine config is read) that
/// carries the bot's identity, plus the identity variables a turn sets.
fn turn_env(home: &Path, extra: &[(&str, &str)]) -> Env {
    let mut env = Env::new();
    env.insert("PATH".into(), "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin".into());
    env.insert("HOME".into(), home.display().to_string());
    let mut pairs = vec![("user.name", "Ops Bot"), ("user.email", "ops@bots.invalid"), ("commit.gpgsign", "false")];
    pairs.extend_from_slice(extra);
    env.insert("GIT_CONFIG_COUNT".into(), pairs.len().to_string());
    for (i, (k, v)) in pairs.iter().enumerate() {
        env.insert(format!("GIT_CONFIG_KEY_{i}"), k.to_string());
        env.insert(format!("GIT_CONFIG_VALUE_{i}"), v.to_string());
    }
    for role in ["AUTHOR", "COMMITTER"] {
        env.insert(format!("GIT_{role}_NAME"), "Ops Bot".into());
        env.insert(format!("GIT_{role}_EMAIL"), "ops@bots.invalid".into());
    }
    env
}

/// A bare remote with one commit on main, and a working clone of it at `seed`.
fn remote(root: &Path, name: &str) -> (PathBuf, PathBuf) {
    let bare = root.join(format!("{name}.git"));
    sh(root, &["init", "--bare", "-q", "-b", "main", bare.to_str().unwrap()]);
    let seed = root.join(format!("{name}-seed"));
    sh(root, &["clone", "-q", bare.to_str().unwrap(), seed.to_str().unwrap()]);
    std::fs::write(seed.join("AGENT.md"), "# Ops\n").unwrap();
    sh(&seed, &["add", "-A"]);
    sh(&seed, &["commit", "-qm", "start"]);
    sh(&seed, &["push", "-q", "origin", "HEAD:main"]);
    (bare, seed)
}

fn commit_and_push(seed: &Path, file: &str, text: &str) {
    sh(seed, &["pull", "-q", "--rebase"]);
    std::fs::write(seed.join(file), text).unwrap();
    sh(seed, &["add", "-A"]);
    sh(seed, &["commit", "-qm", &format!("elsewhere: {file}")]);
    sh(seed, &["push", "-q", "origin", "HEAD:main"]);
}

// Run these permission contracts only in a disposable Linux container, as root with
// TICO_RUNNER_BOT_UID=10003 TICO_RUNNER_BOT_GID=10002. They never touch an installed runner.
#[cfg(target_os = "linux")]
#[tokio::test]
#[ignore = "requires a disposable Linux two-user environment"]
async fn isolated_workspace_git_reuses_restored_metadata_and_creates_new_metadata_as_the_bot() {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    assert_eq!(unsafe { libc::geteuid() }, 0);
    assert_eq!(crate::isolation::identity(), Some((10003, 10002)));
    let tmp = Tmp::new();
    let (bare, _) = remote(&tmp.0, "bot-ops");
    let projects = tmp.0.join("projects");
    std::fs::create_dir_all(&projects).unwrap();
    let main = projects.join("bot-ops");
    sh(&projects, &["clone", "--no-hardlinks", "-q", bare.to_str().unwrap(), main.to_str().unwrap()]);
    let old = sessions::path_for(&projects, "ops", "old");
    std::fs::create_dir_all(old.parent().unwrap()).unwrap();
    sh(&main, &["worktree", "add", "-q", "-b", "tico-session/old", old.to_str().unwrap(), "origin/main"]);
    sh(&old, &["branch", "--set-upstream-to=origin/main"]);
    sh(&old, &["fetch", "-q"]);
    let old_fetch = main.join(".git/worktrees/old/FETCH_HEAD");
    assert_eq!(old_fetch.metadata().unwrap().uid(), 0, "restored supervisor-owned metadata");

    let private = tmp.0.join("runner.json");
    std::fs::write(&private, "private fixture").unwrap();
    std::fs::set_permissions(&private, std::fs::Permissions::from_mode(0o600)).unwrap();
    std::os::unix::fs::symlink(&private, main.join(".git/private-link")).unwrap();
    // This local remote stands in for GitHub; local upload-pack must run as its owner.
    crate::isolation::adopt(&[bare.clone()]);
    let mirror = tmp.0.join("mirror.git");
    std::fs::create_dir(&mirror).unwrap();
    let env = turn_env(&tmp.0, &[]);
    assert_eq!(sessions::ensure(&main, &old, "old", &env).await, ("origin/main".into(), "".into()));
    assert_eq!(old_fetch.metadata().unwrap().uid(), 10003);
    assert_eq!(old.metadata().unwrap().uid(), 10003, "the existing-return path adopts the checkout too");
    assert_eq!(sessions::sync_shared(&old, &env).await, "");
    assert_eq!(old_fetch.metadata().unwrap().uid(), 10003, "fetch does not recreate supervisor metadata");

    let new = sessions::path_for(&projects, "ops", "new");
    assert_eq!(sessions::ensure(&main, &new, "new", &env).await.1, "");
    let write = crate::gitrun::run_as_bot(&["/bin/sh".into(), "-c".into(), "mkdir memory && printf lesson > memory/lesson.md".into()], &env, Some(&new), 10).await.unwrap();
    assert!(write.ok(), "{}", write.stderr);
    assert_eq!(sessions::finish(&new, "run", &env, &[], true).await, "");
    for file in ["worktrees/new/index", "worktrees/new/FETCH_HEAD", "logs/refs/heads/tico-session/new"] {
        assert_eq!(main.join(".git").join(file).metadata().unwrap().uid(), 10003, "{file}");
    }
    assert_eq!(private.metadata().unwrap().uid(), 0, "adoption never follows a symlink into private state");
    let denied = crate::gitrun::run_as_bot(&["cat".into(), private.display().to_string()], &env, None, 10).await.unwrap();
    assert!(!denied.ok(), "the supervisor's registration remains private");
    let mirror_stamp = mirror.join("supervisor-stamp");
    let command = vec!["touch".into(), mirror_stamp.display().to_string()];
    assert!(crate::gitrun::run(&command, &env, None, 10).await.unwrap().ok());
    assert_eq!(mirror_stamp.metadata().unwrap().uid(), 0, "mirror commands keep the supervisor identity");
    let foreign = projects.join("foreign");
    std::fs::create_dir(&foreign).unwrap();
    std::os::unix::fs::symlink(&mirror, foreign.join(".git")).unwrap();
    assert!(!sessions::ensure(&foreign, &tmp.0.join("outside"), "foreign", &env).await.1.is_empty());
    assert_eq!(mirror.metadata().unwrap().uid(), 0, "an external gitdir is never adopted");
}

#[cfg(target_os = "linux")]
#[tokio::test]
#[ignore = "requires a disposable Linux two-user environment"]
async fn isolated_workspace_locks_and_checkout_proofs_are_bot_owned_without_replacing_lock_inodes() {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    assert_eq!(unsafe { libc::geteuid() }, 0);
    assert_eq!(crate::isolation::identity(), Some((10003, 10002)));
    let tmp = Tmp::new();
    let folder = tmp.0.join("tasks/.locks");
    std::fs::create_dir_all(&folder).unwrap();
    let key = &crate::gitrun::sha256_hex(b"task")[..2];
    let lock = folder.join(key);
    std::fs::write(&lock, "").unwrap();
    std::fs::set_permissions(&lock, std::fs::Permissions::from_mode(0o644)).unwrap();
    let inode = lock.metadata().unwrap().ino();
    let held = worktrees::locked(&tmp.0, "task").await.unwrap();
    assert_eq!(lock.metadata().unwrap().uid(), 10003);
    assert_eq!(lock.metadata().unwrap().ino(), inode);
    assert_eq!(lock.metadata().unwrap().mode() & 0o777, 0o644, "the inode changes owner, not mode");
    let env = turn_env(&tmp.0, &[]);
    let argv = vec!["flock".into(), "-n".into(), lock.display().to_string(), "true".into()];
    assert!(!crate::gitrun::run_as_bot(&argv, &env, None, 10).await.unwrap().ok(), "the bot opens the same locked inode");
    drop(held);
    assert!(crate::gitrun::run_as_bot(&argv, &env, None, 10).await.unwrap().ok());
    drop(worktrees::locked(&tmp.0, "fresh-task").await.unwrap());
    assert_eq!(folder.join(&crate::gitrun::sha256_hex(b"fresh-task")[..2]).metadata().unwrap().uid(), 10003);

    let proof = json!({"schema":1,"link_id":"link","phase":"ready"}).as_object().unwrap().clone();
    worktrees::write_marker(&tmp.0, &proof).unwrap();
    let marker = tmp.0.join("tasks/.checkout-state").join(format!("{}.json", crate::gitrun::sha256_hex(b"link")));
    assert_eq!(marker.metadata().unwrap().uid(), 10003);
    assert_eq!(marker.metadata().unwrap().mode() & 0o777, 0o600);
    assert!(crate::gitrun::run_as_bot(&["cat".into(), marker.display().to_string()], &env, None, 10).await.unwrap().ok());
    let private = tmp.0.join("private");
    std::fs::write(&private, "private").unwrap();
    std::os::unix::fs::symlink(&private, folder.join(&crate::gitrun::sha256_hex(b"malicious")[..2])).unwrap();
    assert!(worktrees::locked(&tmp.0, "malicious").await.is_err());
    assert_eq!(private.metadata().unwrap().uid(), 0, "a lock symlink never changes private ownership");
}

/// Leave `path` as an older runner did (owner and mode), and return what it holds for the unchanged-bytes check.
#[cfg(target_os = "linux")]
fn seed_owner(path: &Path, uid: u32, mode: u32) -> Vec<u8> {
    use std::os::unix::fs::PermissionsExt;
    std::os::unix::fs::lchown(path, Some(uid), Some(uid)).unwrap();
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode)).unwrap();
    if path.is_file() { std::fs::read(path).unwrap() } else { vec![] }
}

#[cfg(target_os = "linux")]
fn owner_mode(path: &Path) -> (u32, u32) {
    use std::os::unix::fs::MetadataExt;
    let m = path.symlink_metadata().unwrap();
    (m.uid(), m.mode() & 0o7777)
}

// HotMic 0.3.46 leftovers: checkout proofs the supervisor wrote as ticorun (10002), 0600 in a 0700 folder.
#[cfg(target_os = "linux")]
#[tokio::test]
#[ignore = "requires a disposable Linux two-user environment"]
async fn isolated_existing_checkout_proofs_go_to_the_bot_and_links_in_their_folder_change_nothing_outside() {
    assert_eq!(unsafe { libc::geteuid() }, 0);
    assert_eq!(crate::isolation::identity(), Some((10003, 10002)));
    let tmp = Tmp::new();
    let env = turn_env(&tmp.0, &[]);
    let folder = tmp.0.join("tasks/.checkout-state");
    std::fs::create_dir_all(&folder).unwrap();
    seed_owner(&tmp.0.join("tasks"), 10002, 0o755);
    let marker = folder.join(format!("{}.json", crate::gitrun::sha256_hex(b"old-link")));
    std::fs::write(&marker, r#"{"link_id":"old-link","phase":"checkout_complete","schema":1}"#).unwrap();
    let bytes = seed_owner(&marker, 10002, 0o600);
    seed_owner(&folder, 10002, 0o700);
    let private = tmp.0.join("runner.json");
    std::fs::write(&private, "private fixture").unwrap();
    seed_owner(&private, 0, 0o600);
    std::fs::hard_link(&private, folder.join("alias.json")).unwrap();
    let linked = tmp.0.join("supervisor-dir");
    std::fs::create_dir(&linked).unwrap();
    seed_owner(&linked, 0, 0o700);
    std::os::unix::fs::symlink(&linked, folder.join("escape.json")).unwrap();
    let cat = |p: &Path| vec!["cat".to_string(), p.display().to_string()];
    assert!(!crate::gitrun::run_as_bot(&cat(&marker), &env, None, 10).await.unwrap().ok(), "the 0.3.46 layout the bot could not read");

    // A heartbeat's worktree pass, before any link is read.
    worktrees::adopt_task_states(&tmp.0);
    assert_eq!(owner_mode(&folder), (10003, 0o700));
    assert_eq!(owner_mode(&marker), (10003, 0o600));
    assert_eq!(std::fs::read(&marker).unwrap(), bytes, "ownership changes, the proof does not");
    let read = crate::gitrun::run_as_bot(&cat(&marker), &env, None, 10).await.unwrap();
    assert!(read.ok() && read.stdout.contains("checkout_complete"), "{}", read.stderr);
    assert_eq!(worktrees::read_marker(&tmp.0, "old-link").unwrap().unwrap()["phase"], "checkout_complete");
    assert_eq!(owner_mode(&private), (0, 0o600), "a foreign hard link in the folder grants nothing");
    assert!(!crate::gitrun::run_as_bot(&cat(&private), &env, None, 10).await.unwrap().ok());
    assert_eq!(owner_mode(&linked), (0, 0o700), "a symlink in the folder is never followed");
    assert_eq!(owner_mode(&folder.join("escape.json")).0, 0, "nor handed over itself");

    // The bot's `hub worktree setup` replaces a proof the way runner/worktrees.py does: temp file, then rename.
    let replace = format!("printf %s '{{\"schema\":1}}' > {0}.tmp && mv {0}.tmp {0}", marker.display());
    let replaced = crate::gitrun::run_as_bot(&["/bin/sh".into(), "-c".into(), replace], &env, None, 10).await.unwrap();
    assert!(replaced.ok(), "{}", replaced.stderr);

    // A proof the supervisor writes later, beside one still left as 10002, hands both over.
    let other = folder.join(format!("{}.json", crate::gitrun::sha256_hex(b"other-link")));
    std::fs::write(&other, r#"{"schema":1}"#).unwrap();
    seed_owner(&other, 10002, 0o600);
    seed_owner(&folder, 10002, 0o700);
    let proof = json!({"schema":1,"link_id":"new-link","phase":"ready"}).as_object().unwrap().clone();
    worktrees::write_marker(&tmp.0, &proof).unwrap();
    assert_eq!(owner_mode(&folder), (10003, 0o700));
    assert_eq!(owner_mode(&other), (10003, 0o600));
    assert_eq!(owner_mode(&private), (0, 0o600));
}

// Tico Team 0.3.46 leftovers: per-task locks (tasks/.locks/<sha[:2]>) made as ticorun, 644 in a 755 folder,
// which the bot's `hub worktree` could not open. The same inodes go to the bot, a held lock stays held.
#[cfg(target_os = "linux")]
#[tokio::test]
#[ignore = "requires a disposable Linux two-user environment"]
async fn isolated_existing_task_locks_go_to_the_bot_without_replacing_inodes_or_following_links() {
    use std::os::unix::fs::MetadataExt;
    assert_eq!(unsafe { libc::geteuid() }, 0);
    assert_eq!(crate::isolation::identity(), Some((10003, 10002)));
    let tmp = Tmp::new();
    let env = turn_env(&tmp.0, &[]);
    let folder = tmp.0.join("tasks/.locks");
    std::fs::create_dir_all(&folder).unwrap();
    seed_owner(&tmp.0.join("tasks"), 10002, 0o755);
    let lock_for = |task: &str| folder.join(&crate::gitrun::sha256_hex(task.as_bytes())[..2]);
    let (held, idle) = (lock_for("held-task"), lock_for("idle-task"));
    assert_ne!(held, idle);
    for lock in [&held, &idle] {
        std::fs::write(lock, "").unwrap();
        seed_owner(lock, 10002, 0o644);
    }
    seed_owner(&folder, 10002, 0o755);
    let private = tmp.0.join("runner.json");
    std::fs::write(&private, "private fixture").unwrap();
    seed_owner(&private, 0, 0o600);
    std::fs::hard_link(&private, folder.join("zz-alias")).unwrap();
    let linked = tmp.0.join("supervisor-dir");
    std::fs::create_dir(&linked).unwrap();
    seed_owner(&linked, 0, 0o700);
    std::os::unix::fs::symlink(&linked, folder.join("zz-escape")).unwrap();
    let open = |lock: &Path| vec!["/bin/sh".to_string(), "-c".into(), format!("exec 9>>{} && flock -n 9", lock.display())];
    assert!(!crate::gitrun::run_as_bot(&open(&idle), &env, None, 10).await.unwrap().ok(), "the 0.3.46 layout the bot could not open");
    let inodes = (held.metadata().unwrap().ino(), idle.metadata().unwrap().ino());

    // The supervisor holds one task's lock while a heartbeat adopts the folder.
    let guard = worktrees::locked(&tmp.0, "held-task").await.unwrap();
    worktrees::adopt_task_states(&tmp.0);
    assert_eq!((held.metadata().unwrap().ino(), idle.metadata().unwrap().ino()), inodes, "no lock inode is replaced");
    for lock in [&held, &idle] {
        assert_eq!(owner_mode(lock), (10003, 0o644), "{lock:?}");
    }
    assert_eq!(owner_mode(&folder), (10003, 0o755));
    assert!(!crate::gitrun::run_as_bot(&open(&held), &env, None, 10).await.unwrap().ok(), "a held lock stays held for the bot");
    assert!(crate::gitrun::run_as_bot(&open(&idle), &env, None, 10).await.unwrap().ok(), "the bot opens and takes an idle lock");
    drop(guard);
    assert!(crate::gitrun::run_as_bot(&open(&held), &env, None, 10).await.unwrap().ok());
    assert_eq!(owner_mode(&private), (0, 0o600), "a foreign hard link in the folder grants nothing");
    assert_eq!(owner_mode(&linked), (0, 0o700), "a symlink in the folder is never followed");

    // Taking a lock adopts the folder too, not only the lock it opens.
    let other = lock_for("other-task");
    std::fs::write(&other, "").unwrap();
    seed_owner(&other, 10002, 0o644);
    seed_owner(&folder, 10002, 0o755);
    drop(worktrees::locked(&tmp.0, "held-task").await.unwrap());
    assert_eq!(owner_mode(&folder), (10003, 0o755));
    assert_eq!(owner_mode(&other), (10003, 0o644));
    assert_eq!(owner_mode(&private), (0, 0o600));
}

// HotMic 0.3.46 leftovers: a managed base clone (repos/<name>/.git, its config and tico-managed) owned by
// ticorun, directories 755, files 644. Refreshing it hands it to the bot, whose worktree add and
// mark_managed (runner/repositories.py) then work.
#[cfg(target_os = "linux")]
#[tokio::test]
#[ignore = "requires a disposable Linux two-user environment"]
async fn isolated_existing_base_clone_left_by_the_supervisor_group_is_refreshed_and_used_by_the_bot() {
    assert_eq!(unsafe { libc::geteuid() }, 0);
    assert_eq!(crate::isolation::identity(), Some((10003, 10002)));
    let tmp = Tmp::new();
    let env = turn_env(&tmp.0, &[]);
    let (mirror, seed) = remote(&tmp.0, "app");
    let base = tmp.0.join("repos").join(crate::repositories::base_folder("acme/app"));
    std::fs::create_dir_all(base.parent().unwrap()).unwrap();
    sh(&tmp.0, &["clone", "-q", "--no-hardlinks", mirror.to_str().unwrap(), base.to_str().unwrap()]);
    sh(&base, &["remote", "rename", "origin", "tico-mirror"]);
    sh(&base, &["remote", "add", "origin", "https://github.com/acme/app.git"]);
    sh(&base, &["fetch", "-q", "tico-mirror", "+refs/heads/main:refs/remotes/origin/main"]);
    std::fs::write(base.join(".git/tico-managed"), r#"{"full_name": "acme/app"}"#).unwrap();
    commit_and_push(&seed, "later.md", "newer than the base clone\n");
    let entries = |root: &Path| -> Vec<(PathBuf, bool)> {
        let output = std::process::Command::new("find").arg(root).args(["-type", "d", "-printf", "d %p\n", "-o", "-type", "f", "-printf", "f %p\n"]).output().unwrap();
        String::from_utf8(output.stdout).unwrap().lines().map(|l| (PathBuf::from(&l[2..]), l.starts_with('d'))).collect()
    };
    let mut before = vec![];
    for (path, dir) in entries(&base) {
        let mode = if dir { 0o755 } else { 0o644 };
        let bytes = seed_owner(&path, 10002, mode);
        before.push((path.clone(), path.metadata().map(|m| std::os::unix::fs::MetadataExt::ino(&m)).unwrap(), mode, bytes));
    }
    let mark = format!("test ! -L .git/tico-managed && printf %s '{{\"full_name\": \"acme/app\"}}' > .git/tico-managed");
    let mark = vec!["/bin/sh".to_string(), "-c".into(), mark];
    assert!(!crate::gitrun::run_as_bot(&mark, &env, Some(&base), 10).await.unwrap().ok(), "the 0.3.46 layout mark_managed failed on");

    let mut repo = json!({"full_name": "acme/app", "default_branch": "main"}).as_object().unwrap().clone();
    let (path, branch) = crate::repositories::worktree_base(&tmp.0, &mut repo, &env).await.unwrap();
    assert_eq!((path.as_path(), branch.as_str()), (base.as_path(), "main"));
    for (path, _) in entries(&base) {
        assert_eq!(owner_mode(&path).0, 10003, "{path:?}");
    }
    for (path, inode, mode, bytes) in &before {
        // Git replaces the refs and config it updates; every inode the refresh only adopted keeps its mode.
        if path.metadata().is_ok_and(|m| std::os::unix::fs::MetadataExt::ino(&m) == *inode) {
            assert_eq!(owner_mode(path).1, *mode, "{path:?}");
            if path.symlink_metadata().is_ok_and(|m| m.is_file()) && path.components().any(|c| c.as_os_str() == "objects") {
                assert_eq!(&std::fs::read(path).unwrap(), bytes, "{path:?}");
            }
        }
    }
    let (mirror_owner, _) = owner_mode(&mirror);
    assert_eq!(mirror_owner, 0, "the computer's mirror stays the supervisor's");

    assert!(crate::gitrun::run_as_bot(&mark, &env, Some(&base), 10).await.unwrap().ok());
    let task = tmp.0.join("tasks/t1/acme__app");
    std::fs::create_dir_all(task.parent().unwrap()).unwrap();
    crate::isolation::chown(&tmp.0.join("tasks"));
    crate::isolation::chown(task.parent().unwrap());
    let add = crate::gitrun::git_as_bot(&base, &env, &["worktree", "add", "--quiet", "-b", "tico/t1", &task.display().to_string(), "origin/main"], 30).await.unwrap();
    assert!(add.ok(), "{}", add.stderr);
    assert!(task.join("later.md").is_file(), "the refresh fetched the newer commit");
    std::fs::write(task.join("work.txt"), "bot work\n").unwrap();
    crate::isolation::chown(&task.join("work.txt"));
    for args in [&["add", "work.txt"][..], &["commit", "-qm", "bot work"][..]] {
        let done = crate::gitrun::git_as_bot(&task, &env, args, 30).await.unwrap();
        assert!(done.ok(), "{args:?}: {}", done.stderr);
    }
}

// A session left with mixed owners: the base index root's (644), a worktree's FETCH_HEAD and admin folder
// ticorun's (644, 755). The next turn's ensure and finish work as the bot and keep every mode.
#[cfg(target_os = "linux")]
#[tokio::test]
#[ignore = "requires a disposable Linux two-user environment"]
async fn isolated_mixed_owner_session_metadata_is_ensured_and_finished_as_the_bot() {
    assert_eq!(unsafe { libc::geteuid() }, 0);
    assert_eq!(crate::isolation::identity(), Some((10003, 10002)));
    let tmp = Tmp::new();
    let (bare, _) = remote(&tmp.0, "bot-ops");
    let projects = tmp.0.join("projects");
    std::fs::create_dir_all(&projects).unwrap();
    let main = projects.join("bot-ops");
    sh(&projects, &["clone", "--no-hardlinks", "-q", bare.to_str().unwrap(), main.to_str().unwrap()]);
    let session = sessions::path_for(&projects, "ops", "mixed");
    std::fs::create_dir_all(session.parent().unwrap()).unwrap();
    sh(&main, &["worktree", "add", "-q", "-b", "tico-session/mixed", session.to_str().unwrap(), "origin/main"]);
    sh(&session, &["branch", "--set-upstream-to=origin/main"]);
    sh(&session, &["fetch", "-q"]);
    crate::isolation::adopt(&[bare.clone()]); // stands in for GitHub; local upload-pack runs as its owner
    let index = main.join(".git/index");
    let index_bytes = seed_owner(&index, 0, 0o644);
    let admin = main.join(".git/worktrees/mixed");
    let fetch_head = admin.join("FETCH_HEAD");
    seed_owner(&fetch_head, 10002, 0o644);
    seed_owner(&admin, 10002, 0o755);
    let env = turn_env(&tmp.0, &[]);

    assert_eq!(sessions::ensure(&main, &session, "mixed", &env).await, ("origin/main".into(), "".into()));
    assert_eq!(owner_mode(&index), (10003, 0o644));
    assert_eq!(std::fs::read(&index).unwrap(), index_bytes);
    assert_eq!(owner_mode(&fetch_head), (10003, 0o644));
    assert_eq!(owner_mode(&admin), (10003, 0o755));
    let write = crate::gitrun::run_as_bot(&["/bin/sh".into(), "-c".into(), "mkdir memory && printf lesson > memory/lesson.md".into()], &env, Some(&session), 10).await.unwrap();
    assert!(write.ok(), "{}", write.stderr);
    assert_eq!(sessions::finish(&session, "run", &env, &[], true).await, "");
    assert_eq!(owner_mode(&admin), (10003, 0o755));
    assert_eq!(owner_mode(&fetch_head).0, 10003);
    assert_eq!(owner_mode(&index), (10003, 0o644));
}

#[tokio::test]
async fn a_task_lane_commits_its_memory_stamps_its_run_and_lands_beside_the_other_lanes() {
    let tmp = Tmp::new();
    let (bare, seed) = remote(&tmp.0, "bot-ops");
    let projects = tmp.0.join("projects");
    std::fs::create_dir_all(&projects).unwrap();
    let main = projects.join("bot-ops");
    sh(&projects, &["clone", "-q", bare.to_str().unwrap(), main.to_str().unwrap()]);
    let env = turn_env(&tmp.0, &[]);
    let session = sessions::path_for(&projects, "ops", "t1");

    let (tracked, problem) = sessions::ensure(&main, &session, "t1", &env).await;
    assert_eq!((tracked.as_str(), problem.as_str()), ("origin/main", ""));
    assert_eq!(sh(&session, &["symbolic-ref", "--short", "HEAD"]), "tico-session/t1");
    // A second turn on the task reuses the worktree.
    assert_eq!(sessions::ensure(&main, &session, "t1", &env).await.1, "");

    // The bot commits code and leaves a lesson uncommitted; another lane pushes meanwhile.
    std::fs::write(session.join("code.txt"), "work\n").unwrap();
    sh(&session, &["add", "code.txt"]);
    sh(&session, &["commit", "-qm", "Task work"]);
    std::fs::create_dir_all(session.join("memory")).unwrap();
    std::fs::write(session.join("memory/lesson.md"), "keep tests fast\n").unwrap();
    commit_and_push(&seed, "other.md", "another lane\n");

    assert_eq!(sessions::finish(&session, "run-1", &env, &[], true).await, "");
    let log = sh(&session, &["log", "--format=%B%x00", "origin/main..HEAD"]);
    let bodies: Vec<&str> = log.split('\0').map(str::trim).filter(|b| !b.is_empty()).collect();
    assert_eq!(bodies.len(), 2, "{log}");
    assert!(bodies.iter().all(|b| b.ends_with("Tico-Run: run-1")), "{log}");
    assert!(bodies.iter().any(|b| b.starts_with("Memory from run run-1")));
    assert!(session.join("other.md").is_file(), "rebased onto what the other lane pushed");

    // The push races one more lane: refused as behind, caught up, pushed.
    commit_and_push(&seed, "third.md", "a third lane\n");
    let (ahead, error) = execute::push_repo(&session, &env).await;
    assert_eq!((ahead, error.as_str()), (2, "non-fast-forward"));
    assert_eq!(sessions::catch_up(&session, &env).await, "");
    assert_eq!(execute::push_repo(&session, &env).await.1, "");
    let pushed = sh(&tmp.0, &["--git-dir", bare.to_str().unwrap(), "log", "--format=%B", "main"]);
    assert_eq!(pushed.matches("Tico-Run: run-1").count(), 2, "{pushed}");

    // Finished and fully pushed: the worktree and its branch go.
    assert!(sessions::remove(&main, &session, "t1", &env).await);
    assert!(!session.exists());
    assert_eq!(sh(&main, &["branch", "--list", "tico-session/t1"]), "");
}

#[tokio::test]
async fn a_stateless_task_lane_commits_no_memory_and_leaves_what_it_wrote_in_memory_as_it_is() {
    let tmp = Tmp::new();
    let (bare, _seed) = remote(&tmp.0, "bot-counsel");
    let projects = tmp.0.join("projects");
    std::fs::create_dir_all(&projects).unwrap();
    let main = projects.join("bot-counsel");
    sh(&projects, &["clone", "-q", bare.to_str().unwrap(), main.to_str().unwrap()]);
    let env = turn_env(&tmp.0, &[]);
    let session = sessions::path_for(&projects, "tico-counsel", "t2");
    assert_eq!(sessions::ensure(&main, &session, "t2", &env).await, ("origin/main".into(), "".into()));

    std::fs::write(session.join("code.txt"), "work\n").unwrap();
    sh(&session, &["add", "code.txt"]);
    sh(&session, &["commit", "-qm", "Task work"]);
    std::fs::create_dir_all(session.join("memory")).unwrap();
    std::fs::write(session.join("memory/lesson.md"), "an earlier document's terms\n").unwrap();

    assert_eq!(sessions::finish(&session, "run-2", &env, &[], false).await, "");
    let log = sh(&session, &["log", "--format=%B", "origin/main..HEAD"]);
    assert!(!log.contains("Memory from run"), "{log}");
    assert!(sh(&session, &["status", "--porcelain"]).contains("memory/"), "what the bot wrote there stays uncommitted");
}

#[tokio::test]
async fn a_memory_pass_checkout_drops_its_edits_and_commits_and_leaves_main_alone() {
    let tmp = Tmp::new();
    let (bare, _seed) = remote(&tmp.0, "bot-pass");
    let projects = tmp.0.join("projects");
    std::fs::create_dir_all(&projects).unwrap();
    let main = projects.join("bot-pass");
    sh(&projects, &["clone", "-q", bare.to_str().unwrap(), main.to_str().unwrap()]);
    let env = turn_env(&tmp.0, &[]);
    let session = sessions::path_for(&projects, "tico-pass", "t4");
    assert_eq!(sessions::ensure(&main, &session, "t4", &env).await, ("origin/main".into(), "".into()));
    let base = sh(&session, &["rev-parse", "HEAD"]);

    // A pass commits outside memory/ and edits memory/ without committing it; both must be gone after the turn.
    std::fs::write(session.join("stray.txt"), "outside memory\n").unwrap();
    sh(&session, &["add", "stray.txt"]);
    sh(&session, &["commit", "-qm", "Pass commit"]);
    std::fs::create_dir_all(session.join("memory")).unwrap();
    std::fs::write(session.join("memory/learnings.md"), "a rule the pass rewrote\n").unwrap();

    let changed = sessions::discard_pass(&session, &env).await.unwrap();
    assert!(changed.iter().any(|c| c.starts_with("commit ")), "{changed:?}");
    assert!(changed.iter().any(|c| c.contains("memory/learnings.md")), "{changed:?}");
    assert_eq!(sh(&session, &["rev-parse", "HEAD"]), base, "the pass's commit is dropped");
    assert_eq!(sh(&session, &["status", "--porcelain"]), "", "nothing is left uncommitted");
    assert!(!session.join("stray.txt").exists() && !session.join("memory").exists());
    assert_eq!(sh(&session, &["rev-parse", "origin/main"]), base, "nothing reached main");
    assert_eq!(sh(&session, &["log", "--format=%H", "origin/main..HEAD"]), "");
}

#[tokio::test]
async fn a_memory_pass_with_nothing_changed_discards_nothing_and_says_so() {
    let tmp = Tmp::new();
    let (bare, _seed) = remote(&tmp.0, "bot-quiet");
    let projects = tmp.0.join("projects");
    std::fs::create_dir_all(&projects).unwrap();
    let main = projects.join("bot-quiet");
    sh(&projects, &["clone", "-q", bare.to_str().unwrap(), main.to_str().unwrap()]);
    let env = turn_env(&tmp.0, &[]);
    let session = sessions::path_for(&projects, "tico-quiet", "t5");
    assert_eq!(sessions::ensure(&main, &session, "t5", &env).await, ("origin/main".into(), "".into()));
    assert_eq!(sessions::discard_pass(&session, &env).await.unwrap(), Vec::<String>::new());
}

#[tokio::test]
async fn a_private_meetings_checkout_needs_no_upstream_but_a_tasks_does() {
    // A repository made on this computer with no remote: a task has no upstream to share, a private meeting's copy,
    // never pushed, starts from the checkout's own HEAD.
    let tmp = Tmp::new();
    let projects = tmp.0.join("projects");
    let main = projects.join("bot-ops");
    std::fs::create_dir_all(&main).unwrap();
    sh(&main, &["init", "-q", "-b", "main"]);
    std::fs::write(main.join("AGENT.md"), "# Ops\n").unwrap();
    sh(&main, &["add", "-A"]);
    sh(&main, &["commit", "-qm", "start"]);
    let env = turn_env(&tmp.0, &[]);
    let task = sessions::path_for(&projects, "ops", "t1");
    assert_eq!(sessions::ensure(&main, &task, "t1", &env).await, (String::new(), sessions::NO_UPSTREAM.into()));
    let name = sessions::private_of("private:c-1");
    let session = sessions::path_for(&projects, "ops", &name);
    assert_eq!(sessions::ensure(&main, &session, &name, &env).await, (String::new(), String::new()));
    assert_eq!(sh(&session, &["rev-parse", "HEAD"]), sh(&main, &["rev-parse", "HEAD"]));
    assert!(session.join("AGENT.md").is_file());
    assert_eq!(sessions::ensure(&main, &session, &name, &env).await, (String::new(), String::new()), "reused");
    assert!(sessions::remove_private(&main, &session, &name, &env).await);
}

#[tokio::test]
async fn a_private_meetings_checkout_tracks_nothing_keeps_its_changes_and_goes_whole_when_the_meeting_ends() {
    let tmp = Tmp::new();
    let (bare, _seed) = remote(&tmp.0, "bot-ops");
    let projects = tmp.0.join("projects");
    std::fs::create_dir_all(&projects).unwrap();
    let main = projects.join("bot-ops");
    sh(&projects, &["clone", "-q", bare.to_str().unwrap(), main.to_str().unwrap()]);
    let env = turn_env(&tmp.0, &[]);
    let name = sessions::private_of("private:c-1");
    let session = sessions::path_for(&projects, "ops", &name);
    let before = sh(&tmp.0, &["--git-dir", bare.to_str().unwrap(), "rev-parse", "main"]);

    assert_eq!(sessions::ensure(&main, &session, &name, &env).await, ("origin/main".into(), "".into()));
    assert_eq!(sh(&session, &["symbolic-ref", "--short", "HEAD"]), "tico-session/private-c-1");
    // No upstream, and the bot's checkout keeps its push setting: a bare `git push` from the meeting goes nowhere.
    assert_eq!(sh(&session, &["for-each-ref", "--format=%(upstream)", "refs/heads/tico-session/private-c-1"]), "");
    assert!(!std::fs::read_to_string(main.join(".git/config")).unwrap().contains("[push]"));
    // What the meeting wrote stays in its copy, for its later turns.
    std::fs::create_dir_all(session.join("memory")).unwrap();
    std::fs::write(session.join("memory/learnings.md"), "the press embargo lifts Nov 17\n").unwrap();
    sh(&session, &["add", "-A"]);
    sh(&session, &["commit", "-qm", "Embargo"]);
    std::fs::write(session.join("notes.md"), "unsaved\n").unwrap();
    assert_eq!(sessions::ensure(&main, &session, &name, &env).await.1, "");
    assert!(session.join("notes.md").is_file() && !main.join("memory/learnings.md").exists());
    // A task's careful remove keeps it (unpushed, dirty); the meeting's end deletes it all.
    assert!(!sessions::remove(&main, &session, &name, &env).await);
    assert!(!sessions::remove_private(&main, &session, "t1", &env).await, "only a private meeting's folder");
    assert!(sessions::remove_private(&main, &session, &name, &env).await);
    assert!(!session.exists());
    assert_eq!(sh(&main, &["branch", "--list", "tico-session/private-c-1"]), "");
    assert_eq!(sh(&tmp.0, &["--git-dir", bare.to_str().unwrap(), "rev-parse", "main"]), before, "nothing reached the remote");
}

#[tokio::test]
async fn a_task_turn_moves_the_bots_claude_memory_into_its_worktree_and_it_is_done_once_pushed() {
    use crate::harness_memory::{claude_dir, move_for_turn, Moved};
    let tmp = Tmp::new();
    let (bare, _seed) = remote(&tmp.0, "bot-ops");
    let projects = tmp.0.join("projects");
    std::fs::create_dir_all(&projects).unwrap();
    let main = projects.join("bot-ops");
    sh(&projects, &["clone", "-q", bare.to_str().unwrap(), main.to_str().unwrap()]);
    let env = turn_env(&tmp.0, &[]);
    let session = sessions::path_for(&projects, "ops", "t1");
    assert_eq!(sessions::ensure(&main, &session, "t1", &env).await.1, "");
    // Claude named the bot's memory by its own checkout, not the task's worktree.
    let home = tmp.0.join("home");
    let dir = claude_dir(&home, &main);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("MEMORY.md"), "- [Ask first](ask-first.md) — hook\n").unwrap();
    std::fs::write(dir.join("ask-first.md"), "---\nname: ask-first\ndescription: Ask before a spec\n---\nAsk first.\n").unwrap();
    let (gemini, state) = (tmp.0.join("gemini-home"), tmp.0.join("state"));

    let turn = |checkout: &std::path::Path| move_for_turn(&home, &main, checkout, &gemini, &state, "2026-10-21").unwrap();
    assert_eq!(turn(&session), Moved::Copied(1), "a task turn copies into its worktree before the harness starts");
    assert!(std::fs::read_to_string(session.join("memory/learnings.md")).unwrap().contains("[source: claude-memory/ask-first.md;"));
    // A main-lane turn meanwhile leaves it to the task's checkout: no second copy to collide on rebase.
    assert_eq!(turn(&main), Moved::Elsewhere);
    assert!(!main.join("memory/learnings.md").exists());
    // Not done until the repository has it: the turn's memory commit, then the push.
    assert_eq!(turn(&session), Moved::Copied(0));
    assert_eq!(sessions::finish(&session, "run-1", &env, &[], true).await, "");
    assert_eq!(execute::push_repo(&session, &env).await.1, "");
    assert_eq!(turn(&session), Moved::Done);
    assert!(state.join("done").exists());
    // Done means done: an owner's edit that removes a moved rule stays removed.
    sh(&main, &["pull", "-q"]);
    std::fs::write(main.join("memory/learnings.md"), "# Learnings\n").unwrap();
    assert_eq!(turn(&main), Moved::Done);
    assert_eq!(std::fs::read_to_string(main.join("memory/learnings.md")).unwrap(), "# Learnings\n");
}

#[tokio::test]
async fn a_shared_checkout_never_forces_and_says_which_files_conflict() {
    let tmp = Tmp::new();
    let (bare, seed) = remote(&tmp.0, "bot-eng");
    let mine = tmp.0.join("mine");
    sh(&tmp.0, &["clone", "-q", bare.to_str().unwrap(), mine.to_str().unwrap()]);
    let env = turn_env(&tmp.0, &[]);
    std::fs::write(mine.join("AGENT.md"), "# Mine\n").unwrap();
    sh(&mine, &["commit", "-qam", "my lesson"]);
    commit_and_push(&seed, "AGENT.md", "# Theirs\n");

    assert_eq!(execute::push_repo(&mine, &env).await.1, "non-fast-forward");
    let problem = sessions::catch_up(&mine, &env).await;
    assert!(problem.contains("they conflict with its unpushed commits in AGENT.md"), "{problem}");
    assert!(!mine.join(".git/rebase-merge").exists() && !mine.join(".git/rebase-apply").exists(), "the rebase was undone");
    assert_eq!(std::fs::read_to_string(mine.join("AGENT.md")).unwrap(), "# Mine\n");

    // Before a turn a clean shared checkout rebases; one with leftovers says so and is left alone.
    sh(&mine, &["reset", "-q", "--hard", "origin/main"]);
    commit_and_push(&seed, "notes.md", "from another branch\n");
    assert_eq!(sessions::sync_shared(&mine, &env).await, "");
    assert!(mine.join("notes.md").is_file());
    std::fs::write(mine.join("notes.md"), "edited\n").unwrap();
    assert_eq!(sessions::sync_shared(&mine, &env).await, "it has uncommitted changes from an earlier turn");
}

/// BotOps, COO, Support and PR Reviewer on the Tico Team box: "Not possible to fast-forward" before each turn and a
/// refused push after it, only logged, for hours. The turn's prompt now tells the bot to fix its own checkout.
#[tokio::test]
async fn a_bots_own_diverged_checkout_is_named_in_its_prompt() {
    let tmp = Tmp::new();
    let (bare, seed) = remote(&tmp.0, "emp-coo");
    let mine = tmp.0.join("mine");
    sh(&tmp.0, &["clone", "-q", bare.to_str().unwrap(), mine.to_str().unwrap()]);
    let env = turn_env(&tmp.0, &[]);
    std::fs::write(mine.join("AGENT.md"), "# Mine\n").unwrap();
    sh(&mine, &["commit", "-qam", "my lesson"]);
    commit_and_push(&seed, "AGENT.md", "# Theirs\n");
    commit_and_push(&seed, "notes.md", "more\n");
    let before = sh(&mine, &["rev-parse", "HEAD"]);

    assert!(!execute::pull_repo(&mine, &env).await.is_empty());
    let problem = sessions::divergence(&mine, &env).await;
    assert_eq!(problem, "it has 1 commits `origin/main` doesn't, and `origin/main` has 2 it doesn't; conflicting files: AGENT.md");
    assert_eq!(sh(&mine, &["rev-parse", "HEAD"]), before, "nothing moved");
    let mut attempt = json!({"bot": "coo", "config": {}, "repo_sync_problem": sessions::own_sync_line(&mine, &problem)});
    let mut lines = vec![(crate::prompt::Part::Standing, "You are coo.".to_string())];
    sessions::prompt_head(&attempt, &mut lines);
    assert!(lines[0].1.starts_with(&format!("Before anything else: your repository `{}` has diverged", mine.display())), "{lines:?}");
    assert!(lines[0].1.ends_with("keeping both sides' facts, and push."));
    assert_eq!(lines[0].0, crate::prompt::Part::Turn, "a sync problem is the turn's own, never left out as standing");
    // A shared checkout says it its own way; an up-to-date one says nothing.
    attempt["config"] = json!({"shared": true});
    let mut lines = vec![];
    sessions::prompt_head(&attempt, &mut lines);
    assert!(!lines.iter().any(|(_, l)| l.contains("has diverged")));
    sh(&mine, &["reset", "-q", "--hard", "origin/main"]);
    assert_eq!(sessions::divergence(&mine, &env).await, "");
}

#[tokio::test]
async fn fetching_a_bots_repository_clones_it_and_leaves_no_half_clone() {
    let tmp = Tmp::new();
    let (bare, _seed) = remote(&tmp.0, "bot-ops");
    let env = turn_env(&tmp.0, &[]);
    let url = bare.display().to_string();
    let path = tmp.0.join("projects/bot-ops");
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    assert_eq!(fetchrepo::clone_repository(&path, "org/bot-ops", &env, Some(&url)).await, ("cloned", String::new()));
    assert!(path.join("AGENT.md").is_file());
    let (state, detail) = fetchrepo::clone_repository(&path, "org/bot-ops", &env, Some(&url)).await;
    assert_eq!((state, detail.as_str()), ("failed", "bot-ops already exists here and is not an empty folder; left as it is"));

    // GitHub's repository is still empty: nothing is left behind to block the next try.
    let empty = tmp.0.join("empty.git");
    sh(&tmp.0, &["init", "--bare", "-q", empty.to_str().unwrap()]);
    let target = tmp.0.join("projects/bot-new");
    let (state, detail) = fetchrepo::clone_repository(&target, "org/bot-new", &env, Some(&empty.display().to_string())).await;
    assert_eq!(state, "failed");
    assert!(detail.starts_with("GitHub's org/bot-new is empty"), "{detail}");
    assert!(!target.exists());
    let (state, _) = fetchrepo::clone_repository(&target, "org/bot-new", &env, Some(&tmp.0.join("missing.git").display().to_string())).await;
    assert_eq!(state, "failed");
    assert!(!target.exists());
}

#[tokio::test]
async fn an_attached_worktree_is_certified_then_saved_to_a_wip_branch_when_its_task_closes() {
    let tmp = Tmp::new();
    let (bare, _seed) = remote(&tmp.0, "product");
    let workspace = tmp.0.join("ws");
    let base = workspace.join("repos").join(crate::repositories::base_folder("org/product"));
    std::fs::create_dir_all(base.parent().unwrap()).unwrap();
    sh(&tmp.0, &["clone", "-q", bare.to_str().unwrap(), base.to_str().unwrap()]);
    sh(&base, &["remote", "set-url", "origin", "https://github.com/org/product.git"]);
    sh(&base, &["config", &format!("url.{}.insteadOf", bare.display()), "https://github.com/org/product.git"]);
    let tree = workspace.join("work/product");
    sh(&base, &["worktree", "add", "-q", "-b", "tico/feature", tree.to_str().unwrap()]);
    let env = turn_env(&tmp.0, &[]);
    let mut row = json!({
        "id": "link-0001", "task_id": "task-0001", "path": "work/product", "repo": "org/product",
        "branch": "tico/feature", "owner": "bot:ops", "state": "pending",
        "detail_json": json!({"checkout_state": "attached_pending"}).to_string(),
    });

    let first = worktrees::inspect(&workspace, &row, &env, None).await;
    assert_eq!(first["state"], "present", "{first}");
    assert_eq!(first["checkout_state"], "ready");
    assert_eq!(first["branch"], "tico/feature");
    assert_eq!(first["expected_head"].as_str().unwrap(), sh(&tree, &["rev-parse", "HEAD"]));
    // The server records the proof; the next report verifies the marker instead of certifying again.
    let mut detail = serde_json::Map::new();
    for k in ["expected_head", "checkout_target", "expected_base"] {
        detail.insert(k.into(), first[k].clone());
    }
    detail.insert("checkout_state".into(), json!("ready"));
    row["detail_json"] = json!(Value::Object(detail).to_string());
    row["state"] = json!("present");
    let again = worktrees::inspect(&workspace, &row, &env, None).await;
    assert_eq!((again["state"].as_str(), again.get("error")), (Some("present"), None), "{again}");

    // The task closes with unsaved work: it is pushed to a wip branch, then the worktree goes.
    std::fs::write(tree.join("draft.txt"), "half done\n").unwrap();
    row["state"] = json!("present");
    let mut action_row = row.as_object().unwrap().clone();
    action_row.insert("full_name".into(), json!("org/product"));
    let removed = worktrees::act(&workspace, &mut action_row, "remove", &env, &[], || async { true }).await;
    assert!(matches!(removed, Ok("removed")), "{:?}", removed.err().map(|e| e.text()));
    assert!(!tree.exists());
    let wip = worktrees::wip_branch(&row);
    let saved = sh(&tmp.0, &["--git-dir", bare.to_str().unwrap(), "show", &format!("{wip}:draft.txt")]);
    assert_eq!(saved, "half done");
}

#[tokio::test]
async fn a_removal_with_a_changed_name_that_is_not_utf8_keeps_the_worktree_and_saves_nothing() {
    use std::os::unix::ffi::OsStrExt;
    let tmp = Tmp::new();
    let (bare, _seed) = remote(&tmp.0, "product");
    let workspace = tmp.0.join("ws");
    let base = workspace.join("repos").join(crate::repositories::base_folder("org/product"));
    std::fs::create_dir_all(base.parent().unwrap()).unwrap();
    sh(&tmp.0, &["clone", "-q", bare.to_str().unwrap(), base.to_str().unwrap()]);
    sh(&base, &["remote", "set-url", "origin", "https://github.com/org/product.git"]);
    sh(&base, &["config", &format!("url.{}.insteadOf", bare.display()), "https://github.com/org/product.git"]);
    let tree = workspace.join("work/product");
    sh(&base, &["worktree", "add", "-q", "-b", "tico/feature", tree.to_str().unwrap()]);
    let env = turn_env(&tmp.0, &[]);
    let row = json!({
        "id": "link-0002", "task_id": "task-0002", "path": "work/product", "repo": "org/product",
        "branch": "tico/feature", "owner": "bot:ops", "state": "present",
        "detail_json": json!({"checkout_state": "attached_pending"}).to_string(),
    });
    let first = worktrees::inspect(&workspace, &row, &env, None).await;
    assert_eq!(first["state"], "present", "{first}");
    let head_before = sh(&tree, &["rev-parse", "HEAD"]);

    // An untracked file whose name is not UTF-8 holds a token-shaped string; a second dirty file sits beside it.
    let odd = tree.join(std::ffi::OsStr::from_bytes(b"notes-\xff.txt"));
    std::fs::write(&odd, "ghp_abcdefghijklmnopqrstuvwxyz0123\n").unwrap();
    std::fs::write(tree.join("draft.txt"), "half done\n").unwrap();
    let mut action_row = row.as_object().unwrap().clone();
    action_row.insert("full_name".into(), json!("org/product"));
    let kept = worktrees::act(&workspace, &mut action_row, "remove", &env, &[], || async { true }).await;
    let why = kept.err().map(|e| e.text()).unwrap_or_default();
    assert!(why.contains("not valid UTF-8"), "{why}");

    // Nothing is added, committed or pushed: the worktree, its files and its HEAD stay as they were.
    assert!(tree.exists() && odd.exists() && tree.join("draft.txt").exists());
    assert_eq!(sh(&tree, &["rev-parse", "HEAD"]), head_before);
    let wip = worktrees::wip_branch(&row);
    let heads = sh(&tmp.0, &["--git-dir", bare.to_str().unwrap(), "for-each-ref", "--format=%(refname)", "refs/heads"]);
    assert!(!heads.lines().any(|line| line == format!("refs/heads/{wip}")), "{heads}");
}

#[tokio::test]
async fn a_finished_tasks_rebuildable_scratch_goes_and_its_notes_and_unsaved_checkouts_stay() {
    let tmp = Tmp::new();
    let (bare, _seed) = remote(&tmp.0, "product");
    let workspace = tmp.0.join("ws");
    let base = workspace.join("repos").join(crate::repositories::base_folder("org/product"));
    std::fs::create_dir_all(base.parent().unwrap()).unwrap();
    sh(&tmp.0, &["clone", "-q", bare.to_str().unwrap(), base.to_str().unwrap()]);
    let folder = workspace.join("tasks/abcd1234");
    let suite = folder.join("suite-1");
    let dirty = folder.join("suite-2");
    for tree in [&suite, &dirty] {
        sh(&base, &["worktree", "add", "-q", "--detach", tree.to_str().unwrap(), "origin/HEAD"]);
    }
    std::fs::write(dirty.join("draft.txt"), "not saved\n").unwrap();
    let venv = folder.join("venv");
    let target = folder.join("validation/target");
    let pytest = folder.join("tmp-pytest");
    let inner = dirty.join("node_modules");
    for dir in [&venv, &target, &pytest.join("test_a0"), &inner, &folder.join("logs")] {
        std::fs::create_dir_all(dir).unwrap();
    }
    std::fs::write(venv.join("pyvenv.cfg"), "home = /usr/bin\n").unwrap();
    std::fs::write(target.join("CACHEDIR.TAG"), "Signature: 8a477f597d28d172789f06886806bc55\n").unwrap();
    std::fs::write(folder.join("logs/run.log"), "ok\n").unwrap();
    std::os::unix::fs::symlink(&venv, folder.join("linked-venv")).unwrap();

    worktrees::clear_task_scratch(&workspace, "tasks/abcd1234/product", &turn_env(&tmp.0, &[])).await;

    for gone in [&venv, &target, &pytest, &inner, &suite] {
        assert!(!gone.exists(), "{} should be removed", gone.display());
    }
    assert_eq!(std::fs::read_to_string(folder.join("logs/run.log")).unwrap(), "ok\n");
    assert_eq!(std::fs::read_to_string(dirty.join("draft.txt")).unwrap(), "not saved\n");
    assert!(folder.join("validation").is_dir() && folder.join("linked-venv").symlink_metadata().is_ok());
    // A path outside `tasks/` is never swept.
    let outside = workspace.join("work/venv");
    std::fs::create_dir_all(&outside).unwrap();
    std::fs::write(outside.join("pyvenv.cfg"), "").unwrap();
    worktrees::clear_task_scratch(&workspace, "work/product", &turn_env(&tmp.0, &[])).await;
    assert!(outside.exists());
}

#[tokio::test]
async fn a_turn_counts_only_its_own_tasks_pushes() {
    let tmp = Tmp::new();
    let (bare, seed) = remote(&tmp.0, "product");
    let workspace = tmp.0.join("ws");
    let base = workspace.join("repos/product");
    std::fs::create_dir_all(base.parent().unwrap()).unwrap();
    sh(&tmp.0, &["clone", "-q", bare.to_str().unwrap(), base.to_str().unwrap()]);
    let (mine, theirs) = (workspace.join("tasks/mine/product"), workspace.join("tasks/theirs/product"));
    sh(&base, &["worktree", "add", "-q", "-b", "tico/mine", mine.to_str().unwrap()]);
    sh(&base, &["worktree", "add", "-q", "-b", "tico/theirs", theirs.to_str().unwrap()]);
    let env = turn_env(&tmp.0, &[]);
    let links = [json!({"path": "tasks/mine/product"})];
    let since = crate::util::epoch_s() as i64;
    assert_eq!(worktrees::pushes(&workspace, &links, since, &env).await, Vec::<Value>::new(), "nothing pushed yet");
    // Another task's worktree of the same clone pushes, and this one only fetches its own branch, which someone else
    // pushed: neither is this turn's push.
    sh(&theirs, &["commit", "-q", "--allow-empty", "-m", "theirs"]);
    sh(&theirs, &["push", "-q", "-u", "origin", "HEAD"]);
    // ... and pushes to main, which this worktree tracks: a shared ref of another name is not read.
    sh(&theirs, &["push", "-q", "origin", "HEAD:main"]);
    sh(&mine, &["branch", "-q", "--set-upstream-to=origin/main"]);
    sh(&seed, &["push", "-q", "origin", "HEAD:tico/mine"]);
    sh(&mine, &["fetch", "-q", "origin"]);
    assert_eq!(worktrees::pushes(&workspace, &links, since, &env).await, Vec::<Value>::new());
    for n in ["one", "two"] {
        sh(&mine, &["commit", "-q", "--allow-empty", "-m", n]);
        sh(&mine, &["push", "-q", "-u", "origin", "HEAD"]);
    }
    let both = [links[0].clone(), json!({"path": "tasks/mine/product"})]; // named twice: counted once
    assert_eq!(worktrees::pushes(&workspace, &both, since, &env).await, vec![json!({"path": "tasks/mine/product", "count": 2})]);
    // A push before the turn began is not its own.
    assert_eq!(worktrees::pushes(&workspace, &links, since + 3600, &env).await, Vec::<Value>::new());
}

/// A server that answers every request `{}` and records each request's method, path and JSON body.
async fn recording_server() -> (crate::client::Client, std::sync::Arc<std::sync::Mutex<Vec<(String, Value)>>>) {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let seen = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let log = seen.clone();
    tokio::spawn(async move {
        loop {
            let Ok((mut sock, _)) = listener.accept().await else { return };
            let mut got = Vec::new();
            let mut buf = vec![0u8; 65536];
            let (head, body) = loop {
                let n = sock.read(&mut buf).await.unwrap_or(0);
                got.extend_from_slice(&buf[..n]);
                let text = String::from_utf8_lossy(&got).to_string();
                if let Some(end) = text.find("\r\n\r\n") {
                    let length = text[..end].lines().find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse::<usize>().unwrap())).unwrap_or(0);
                    if got.len() >= end + 4 + length || n == 0 {
                        break (text[..end].to_string(), text[end + 4..].to_string());
                    }
                } else if n == 0 {
                    break (text, String::new());
                }
            };
            let line = head.lines().next().unwrap_or("").split(' ').take(2).collect::<Vec<_>>().join(" ");
            log.lock().unwrap().push((line, serde_json::from_str(&body).unwrap_or(Value::Null)));
            let reply = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}";
            let _ = sock.write_all(reply.as_bytes()).await;
            let _ = sock.shutdown().await;
        }
    });
    (crate::client::Client::new(&format!("http://127.0.0.1:{port}"), "t", 5, 0).unwrap(), seen)
}

#[tokio::test]
async fn a_worktree_reserved_for_a_waiting_task_is_checked_out_set_up_and_reported_ready() {
    let tmp = Tmp::new();
    let (bare, _seed) = remote(&tmp.0, "product");
    let workspace = tmp.0.join("ws");
    let base = workspace.join("repos").join(crate::repositories::base_folder("org/product"));
    std::fs::create_dir_all(base.parent().unwrap()).unwrap();
    sh(&tmp.0, &["clone", "-q", bare.to_str().unwrap(), base.to_str().unwrap()]);
    sh(&base, &["remote", "set-url", "origin", "https://github.com/org/product.git"]);
    sh(&base, &["config", &format!("url.{}.insteadOf", bare.display()), "https://github.com/org/product.git"]);
    let env = turn_env(&tmp.0, &[]);
    let (client, seen) = recording_server().await;
    let row = json!({
        "id": "link-0002", "task_id": "task-0002", "path": "tasks/t2/product", "repo": "org/product",
        "branch": "tico/waited", "owner": "bot:ops", "state": "pending",
        "detail_json": json!({"checkout_state": "queued", "wait_id": "w1", "setup_pending": true}).to_string(),
    });
    // Setup runs in the new worktree without the turn's git identity or credentials.
    let mut repo = serde_json::Map::new();
    repo.insert("full_name".into(), json!("org/product"));
    repo.insert("default_branch".into(), json!("main"));
    repo.insert("setup_command".into(), json!("test -z \"$GIT_AUTHOR_NAME\" && echo ok > ../setup-ran"));

    let proof = worktrees::prepare(&workspace, &row, &mut repo, &env, &client).await;
    let (head, target, _) = proof.unwrap_or_else(|e| panic!("{}", e.text()));
    let tree = workspace.join("tasks/t2/product");
    assert_eq!(head, sh(&tree, &["rev-parse", "HEAD"]));
    assert_eq!(target, "refs/remotes/origin/main");
    assert_eq!(sh(&tree, &["symbolic-ref", "--short", "HEAD"]), "tico/waited");
    assert!(workspace.join("tasks/t2/setup-ran").exists());
    let states: Vec<String> = seen.lock().unwrap().iter().filter(|(l, _)| l == "PATCH /api/v2/tasks/task-0002/links/link-0002").map(|(_, b)| format!("{}/{}", b["state"].as_str().unwrap(), b["checkout_state"].as_str().unwrap())).collect();
    assert_eq!(states, ["pending/initializing", "pending/checkout_ready", "pending/setup_running", "present/ready"]);

    // The heartbeat's next report verifies the local proof and says present and ready.
    let mut ready = row.clone();
    ready["state"] = json!("present");
    ready["detail_json"] = json!(json!({"checkout_state": "ready", "expected_head": head, "checkout_target": target,
                                        "expected_base": seen.lock().unwrap().last().unwrap().1["expected_base"]}).to_string());
    let report = worktrees::inspect(&workspace, &ready, &env, None).await;
    assert_eq!((report["state"].as_str(), report.get("error")), (Some("present"), None), "{report}");

    // A failed setup is recorded once and not run again: the server tells the task instead.
    let failing = json!({
        "id": "link-0003", "task_id": "task-0003", "path": "tasks/t3/product", "repo": "org/product",
        "branch": "tico/failing", "owner": "bot:ops", "state": "pending",
        "detail_json": json!({"checkout_state": "queued", "wait_id": "w2"}).to_string(),
    });
    repo.insert("setup_command".into(), json!("exit 3"));
    let failed = worktrees::prepare(&workspace, &failing, &mut repo, &env, &client).await.unwrap_err().text();
    assert!(failed.contains("setup failed (exit 3)"), "{failed}");
    let last = seen.lock().unwrap().last().unwrap().1.clone();
    assert_eq!((last["state"].as_str(), last["checkout_state"].as_str()), (Some("pending"), Some("setup_failed")));
}
