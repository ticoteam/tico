//! A turn's start brings each checkout up to its upstream (`execute::pull_repo`, `sessions::sync_shared`), and a task
//! turn does it twice for one repository: the bot's checkout, then the task's worktree of it, a second or so apart.
//! Inside a turn (`in_turn`), a fetch of a repository and upstream the same turn already fetched, or pushed to, within
//! FRESH_FOR is skipped; the merge or rebase onto it still runs. Only the turn's own fetches count, so every turn
//! still fetches each repository once and sees what the remote held when it started, as before.

use std::collections::HashMap;
use std::future::Future;
use std::path::Path;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use crate::env::Env;

/// How long a turn's fetch of a repository stands for a later one in the same turn.
const FRESH_FOR: Duration = Duration::from_secs(15);

tokio::task_local! {
    static TURN: String;
}

#[cfg(test)]
thread_local! {
    pub(crate) static FRESH_OVERRIDE: std::cell::Cell<Option<Duration>> = const { std::cell::Cell::new(None) };
}

fn fresh_for() -> Duration {
    #[cfg(test)]
    if let Some(span) = FRESH_OVERRIDE.with(|v| v.get()) {
        return span;
    }
    FRESH_FOR
}

/// (turn, repository and upstream) -> when its last successful fetch or push began.
fn seen() -> &'static Mutex<HashMap<(String, String), Instant>> {
    static SEEN: OnceLock<Mutex<HashMap<(String, String), Instant>>> = OnceLock::new();
    SEEN.get_or_init(Default::default)
}

/// Runs `work` as turn `aid`'s: its fetches and pushes count for each other.
pub async fn in_turn<F: Future>(aid: &str, work: F) -> F::Output {
    TURN.scope(aid.to_string(), work).await
}

/// A checkout's repository (its git common directory) and upstream ref, inside a turn.
pub struct Upstream {
    turn: String,
    key: String,
}

/// The checkout's Upstream, or None outside a turn or when it tracks no remote branch: then nothing is skipped or kept.
pub async fn upstream(path: &Path, env: &Env) -> Option<Upstream> {
    let turn = TURN.try_with(|t| t.clone()).ok()?;
    let out = crate::gitrun::git_as_bot(
        path,
        env,
        &[
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
            "--symbolic-full-name",
            "@{u}",
        ],
        15,
    )
    .await
    .ok()?;
    if !out.ok() {
        return None;
    }
    let mut lines = out.stdout.lines().map(str::trim);
    let common = lines.next().filter(|c| !c.is_empty())?;
    // A branch that tracks a local branch fetches nothing that matters to it.
    let tracked = lines.next().filter(|u| u.starts_with("refs/remotes/"))?;
    let common = Path::new(common)
        .canonicalize()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|_| common.to_string());
    Some(Upstream {
        turn,
        key: format!("{common}\n{tracked}"),
    })
}

impl Upstream {
    /// Whether this turn fetched or pushed this repository's upstream within FRESH_FOR.
    pub fn fresh(&self) -> bool {
        let span = fresh_for();
        seen()
            .lock()
            .unwrap()
            .get(&(self.turn.clone(), self.key.clone()))
            .is_some_and(|at| at.elapsed() < span)
    }

    /// A fetch or push that began at `began` succeeded: the upstream ref is at least as new as the remote was then.
    pub fn fetched(&self, began: Instant) {
        let span = fresh_for();
        let mut seen = seen().lock().unwrap();
        seen.retain(|_, at| at.elapsed() < span.max(FRESH_FOR));
        seen.insert((self.turn.clone(), self.key.clone()), began);
    }
}

/// After a successful fetch or push of the checkout that began at `began`, inside a turn.
pub async fn note(path: &Path, env: &Env, began: Instant) {
    if TURN.try_with(|_| ()).is_err() {
        return;
    }
    if let Some(up) = upstream(path, env).await {
        up.fetched(began);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn sh(dir: &Path, args: &[&str]) -> String {
        let out = std::process::Command::new("git")
            .args([
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "init.defaultBranch=main",
                "-c",
                "commit.gpgsign=false",
            ])
            .args(args)
            .current_dir(dir)
            .env_clear()
            .env("PATH", "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin")
            .env("HOME", dir)
            .env("GIT_CONFIG_GLOBAL", "/dev/null")
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "git {args:?}: {}",
            String::from_utf8_lossy(&out.stderr)
        );
        String::from_utf8_lossy(&out.stdout).trim().to_string()
    }

    fn turn_env(home: &Path) -> Env {
        let mut env = Env::new();
        env.insert(
            "PATH".into(),
            "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin".into(),
        );
        env.insert("HOME".into(), home.display().to_string());
        let pairs = [
            ("user.name", "Ops Bot"),
            ("user.email", "ops@bots.invalid"),
            ("commit.gpgsign", "false"),
        ];
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

    struct Fixture {
        root: PathBuf,
        seed: PathBuf,
        mine: PathBuf,
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            FRESH_OVERRIDE.with(|v| v.set(None));
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    fn fixture() -> Fixture {
        let root =
            std::env::temp_dir().join(format!("tico-fresh-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&root).unwrap();
        let root = root.canonicalize().unwrap();
        let bare = root.join("bot.git");
        sh(
            &root,
            &["init", "--bare", "-q", "-b", "main", bare.to_str().unwrap()],
        );
        let seed = root.join("seed");
        sh(
            &root,
            &[
                "clone",
                "-q",
                bare.to_str().unwrap(),
                seed.to_str().unwrap(),
            ],
        );
        std::fs::write(seed.join("AGENT.md"), "# Ops\n").unwrap();
        sh(&seed, &["add", "-A"]);
        sh(&seed, &["commit", "-qm", "start"]);
        sh(&seed, &["push", "-q", "origin", "HEAD:main"]);
        let mine = root.join("mine");
        sh(
            &root,
            &[
                "clone",
                "-q",
                bare.to_str().unwrap(),
                mine.to_str().unwrap(),
            ],
        );
        Fixture { root, seed, mine }
    }

    fn push_elsewhere(seed: &Path, file: &str) {
        sh(seed, &["pull", "-q", "--rebase"]);
        std::fs::write(seed.join(file), "elsewhere\n").unwrap();
        sh(seed, &["add", "-A"]);
        sh(seed, &["commit", "-qm", file]);
        sh(seed, &["push", "-q", "origin", "HEAD:main"]);
    }

    #[tokio::test]
    async fn a_turn_fetches_each_repository_once_and_every_turn_fetches_its_own() {
        let f = fixture();
        let env = turn_env(&f.root);
        assert_eq!(
            in_turn("a1", crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        push_elsewhere(&f.seed, "late.md");
        // The same turn's second refresh of this repository and upstream merges what its first fetch brought.
        assert_eq!(
            in_turn("a1", crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            !f.mine.join("late.md").exists(),
            "the turn's second fetch ran"
        );
        // Past FRESH_FOR the turn fetches again.
        FRESH_OVERRIDE.with(|v| v.set(Some(Duration::ZERO)));
        assert_eq!(
            in_turn("a1", crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            f.mine.join("late.md").is_file(),
            "a stale fetch was trusted"
        );
        FRESH_OVERRIDE.with(|v| v.set(None));
        // Another turn never relies on this one's fetch: its first refresh always fetches.
        push_elsewhere(&f.seed, "later.md");
        assert_eq!(
            in_turn("a2", crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            f.mine.join("later.md").is_file(),
            "a new turn skipped its first fetch"
        );
        // Outside a turn nothing is skipped.
        push_elsewhere(&f.seed, "outside.md");
        assert_eq!(crate::execute::pull_repo(&f.mine, &env).await, "");
        assert!(f.mine.join("outside.md").is_file());
    }

    #[tokio::test]
    async fn a_push_the_turn_made_counts_as_its_fetch_and_a_failed_fetch_does_not() {
        let f = fixture();
        let env = turn_env(&f.root);
        std::fs::write(f.mine.join("mine.md"), "mine\n").unwrap();
        sh(&f.mine, &["add", "-A"]);
        sh(&f.mine, &["commit", "-qm", "mine"]);
        assert_eq!(
            in_turn("p1", crate::execute::push_repo(&f.mine, &env))
                .await
                .1,
            ""
        );
        push_elsewhere(&f.seed, "late.md");
        assert_eq!(
            in_turn("p1", crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            !f.mine.join("late.md").exists(),
            "the push did not count as fresh"
        );

        // A fetch that fails keeps nothing: the next refresh in the turn fetches again.
        let url = sh(&f.mine, &["config", "--get", "remote.origin.url"]);
        sh(
            &f.mine,
            &[
                "config",
                "remote.origin.url",
                &f.root.join("gone.git").display().to_string(),
            ],
        );
        assert!(!in_turn("p2", crate::execute::pull_repo(&f.mine, &env))
            .await
            .is_empty());
        sh(&f.mine, &["config", "remote.origin.url", &url]);
        assert_eq!(
            in_turn("p2", crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            f.mine.join("late.md").is_file(),
            "a failed fetch counted as fresh"
        );
    }
}
