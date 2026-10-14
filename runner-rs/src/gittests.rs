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

    assert_eq!(sessions::finish(&session, "run-1", &env, &[]).await, "");
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
