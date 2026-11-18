//! A turn's start brings each checkout up to its upstream (`execute::pull_repo`, `sessions::sync_shared`), and a task
//! turn does it twice for one repository: the bot's checkout, then the task's worktree of it, a second or so apart.
//! Inside a turn (`in_turn`), a fetch of a repository and upstream the same turn already fetched, or pushed to, within
//! FRESH_FOR is skipped; the merge or rebase onto it still runs.
//!
//! A chat turn (a person waiting in the bot's own checkout, `in_turn` with `chat`) also starts on this runner's last
//! fetch or push of the repository from any turn, when it began less than CHAT_FRESH_FOR ago: the fast-forward onto
//! what that fetch brought still runs, and the fetch itself runs in the background beside the model's start
//! (`Upstream::refresh_behind`), so the next turn starts on what the remote holds now. Every other turn, and a chat
//! turn with no recent fetch (the first after a pause, a restart or a placement, or after a background fetch failed),
//! fetches before it starts, as before.

use std::collections::HashMap;
use std::future::Future;
use std::path::Path;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use crate::env::Env;

/// How long a turn's fetch of a repository stands for a later one in the same turn.
const FRESH_FOR: Duration = Duration::from_secs(15);
/// How long this runner's last fetch or push of a repository stands for a chat turn's start.
const CHAT_FRESH_FOR: Duration = Duration::from_secs(300);
/// The background fetch's limit, the same as a turn's own fetch.
const BEHIND_TIMEOUT_S: u64 = 45;

#[derive(Clone)]
struct Scope {
    aid: String,
    chat: bool,
}

tokio::task_local! {
    static TURN: Scope;
}

#[cfg(test)]
thread_local! {
    pub(crate) static FRESH_OVERRIDE: std::cell::Cell<Option<Duration>> = const { std::cell::Cell::new(None) };
    pub(crate) static CHAT_OVERRIDE: std::cell::Cell<Option<Duration>> = const { std::cell::Cell::new(None) };
}

fn fresh_for() -> Duration {
    #[cfg(test)]
    if let Some(span) = FRESH_OVERRIDE.with(|v| v.get()) {
        return span;
    }
    FRESH_FOR
}

fn chat_fresh_for() -> Duration {
    #[cfg(test)]
    if let Some(span) = CHAT_OVERRIDE.with(|v| v.get()) {
        return span;
    }
    CHAT_FRESH_FOR
}

/// (turn, repository and upstream) -> when its last successful fetch or push began.
fn seen() -> &'static Mutex<HashMap<(String, String), Instant>> {
    static SEEN: OnceLock<Mutex<HashMap<(String, String), Instant>>> = OnceLock::new();
    SEEN.get_or_init(Default::default)
}

/// Repository and upstream -> when this runner's last successful fetch or push of it began, from any turn.
fn synced() -> &'static Mutex<HashMap<String, Instant>> {
    static SYNCED: OnceLock<Mutex<HashMap<String, Instant>>> = OnceLock::new();
    SYNCED.get_or_init(Default::default)
}

fn note_synced(key: &str, began: Instant) {
    let mut synced = synced().lock().unwrap();
    synced.retain(|_, at| at.elapsed() < CHAT_FRESH_FOR);
    let at = synced.entry(key.to_string()).or_insert(began);
    if *at < began {
        *at = began;
    }
}

/// Repository (its git common directory) -> the runner's fetches of it, one at a time: a fetch moves every
/// remote-tracking ref, so a turn's fetch waits for a background one of any upstream in the same repository.
fn gate(repo: &str) -> Arc<tokio::sync::Mutex<()>> {
    static GATES: OnceLock<Mutex<HashMap<String, Arc<tokio::sync::Mutex<()>>>>> = OnceLock::new();
    GATES
        .get_or_init(Default::default)
        .lock()
        .unwrap()
        .entry(repo.to_string())
        .or_default()
        .clone()
}

/// Runs `work` as turn `aid`'s: its fetches and pushes count for each other. `chat`: a person waits for this turn in
/// the bot's own checkout, and it may start on the runner's recent fetch (module comment).
pub async fn in_turn<F: Future>(aid: &str, chat: bool, work: F) -> F::Output {
    TURN.scope(
        Scope {
            aid: aid.to_string(),
            chat,
        },
        work,
    )
    .await
}

/// A checkout's repository (its git common directory) and upstream ref, inside a turn.
pub struct Upstream {
    turn: String,
    chat: bool,
    repo: String,
    key: String,
}

/// The checkout's Upstream, or None when it tracks no remote branch: then nothing is skipped or kept. Outside a turn
/// nothing is skipped, but a fetch or push still counts for the chat turns after it.
pub async fn upstream(path: &Path, env: &Env) -> Option<Upstream> {
    let scope = TURN.try_with(|t| t.clone()).unwrap_or(Scope {
        aid: String::new(),
        chat: false,
    });
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
        turn: scope.aid,
        chat: scope.chat,
        key: format!("{common}\n{tracked}"),
        repo: common,
    })
}

impl Upstream {
    /// Whether this turn fetched or pushed this repository's upstream within FRESH_FOR.
    pub fn fresh(&self) -> bool {
        let span = fresh_for();
        !self.turn.is_empty()
            && seen()
                .lock()
                .unwrap()
                .get(&(self.turn.clone(), self.key.clone()))
                .is_some_and(|at| at.elapsed() < span)
    }

    /// Whether this is a chat turn and this runner fetched or pushed this repository's upstream, in any turn, within
    /// CHAT_FRESH_FOR: the turn starts on that, and fetches behind it (`refresh_behind`).
    pub fn recent(&self) -> bool {
        let span = chat_fresh_for();
        self.chat
            && synced()
                .lock()
                .unwrap()
                .get(&self.key)
                .is_some_and(|at| at.elapsed() < span)
    }

    /// A fetch or push that began at `began` succeeded: the upstream ref is at least as new as the remote was then.
    pub fn fetched(&self, began: Instant) {
        let span = fresh_for();
        if !self.turn.is_empty() {
            let mut seen = seen().lock().unwrap();
            seen.retain(|_, at| at.elapsed() < span.max(FRESH_FOR));
            seen.insert((self.turn.clone(), self.key.clone()), began);
        }
        note_synced(&self.key, began);
    }

    /// Held while the runner fetches or pushes this repository: it waits for a background fetch of it to finish, so the
    /// two never race for the same remote-tracking ref.
    pub async fn one_at_a_time(&self) -> tokio::sync::OwnedMutexGuard<()> {
        gate(&self.repo).lock_owned().await
    }

    /// A chat turn that starts on the runner's recent fetch: fetch in the background, beside the model's start, so the
    /// next turn starts on what the remote holds now. Only the remote-tracking ref moves; this turn's tree does not.
    /// One at a time per repository: with one running already, nothing more starts. A failed fetch forgets the last
    /// one, so the next chat turn fetches before it starts and says why, as before.
    pub fn refresh_behind(&self, path: &Path, env: &Env) {
        crate::util::mark("fetch_skipped", &self.turn);
        let Ok(one) = gate(&self.repo).try_lock_owned() else {
            return;
        };
        let (path, env, key) = (path.to_path_buf(), env.clone(), self.key.clone());
        tokio::spawn(async move {
            let _one = one;
            let began = Instant::now();
            // Outside the repository's command gate (gitrun::fetch_ungated_as_bot): it writes only objects and
            // remote-tracking refs, and the turn's own git must not queue behind it.
            let fetched = crate::gitrun::fetch_ungated_as_bot(
                &path,
                &env,
                &["fetch", "--quiet", "--no-tags"],
                BEHIND_TIMEOUT_S,
            )
            .await;
            if fetched.as_ref().is_ok_and(|out| out.ok()) {
                note_synced(&key, began);
            } else {
                synced().lock().unwrap().remove(&key);
                let name = path
                    .file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .unwrap_or_default();
                crate::util::diagnostic(&format!(
                    "Tico runner: {name}: the background fetch after a chat turn's start failed; the next chat turn fetches before it starts"
                ));
            }
        });
    }
}

/// After a successful fetch or push of the checkout that began at `began`: for the rest of its turn, and for the chat
/// turns after it (a push at a turn's end, outside the turn's refresh, counts for them).
pub async fn note(path: &Path, env: &Env, began: Instant) {
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
            CHAT_OVERRIDE.with(|v| v.set(None));
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
            in_turn("a1", false, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        push_elsewhere(&f.seed, "late.md");
        // The same turn's second refresh of this repository and upstream merges what its first fetch brought.
        assert_eq!(
            in_turn("a1", false, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            !f.mine.join("late.md").exists(),
            "the turn's second fetch ran"
        );
        // Past FRESH_FOR the turn fetches again.
        FRESH_OVERRIDE.with(|v| v.set(Some(Duration::ZERO)));
        assert_eq!(
            in_turn("a1", false, crate::execute::pull_repo(&f.mine, &env)).await,
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
            in_turn("a2", false, crate::execute::pull_repo(&f.mine, &env)).await,
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
            in_turn("p1", false, crate::execute::push_repo(&f.mine, &env))
                .await
                .1,
            ""
        );
        push_elsewhere(&f.seed, "late.md");
        assert_eq!(
            in_turn("p1", false, crate::execute::pull_repo(&f.mine, &env)).await,
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
        assert!(
            !in_turn("p2", false, crate::execute::pull_repo(&f.mine, &env))
                .await
                .is_empty()
        );
        sh(&f.mine, &["config", "remote.origin.url", &url]);
        assert_eq!(
            in_turn("p2", false, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            f.mine.join("late.md").is_file(),
            "a failed fetch counted as fresh"
        );
    }

    /// Waits for a background fetch of the checkout's repository to finish.
    async fn behind_done(path: &Path, env: &Env) {
        drop(hold(path, env).await);
    }

    /// Holds the repository's fetches: no background fetch starts while it is held.
    async fn hold(path: &Path, env: &Env) -> tokio::sync::OwnedMutexGuard<()> {
        upstream(path, env).await.unwrap().one_at_a_time().await
    }

    fn head(dir: &Path, rev: &str) -> String {
        sh(dir, &["rev-parse", rev])
    }

    #[tokio::test]
    async fn a_chat_turn_starts_on_the_runners_recent_fetch_and_fetches_behind_it() {
        let f = fixture();
        let env = turn_env(&f.root);
        // No fetch on this runner yet: the first chat turn fetches before it starts.
        push_elsewhere(&f.seed, "first.md");
        assert_eq!(
            in_turn("c1", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            f.mine.join("first.md").is_file(),
            "a chat turn with no recent fetch did not fetch"
        );
        // The next chat turn starts on that fetch, without fetching first (its background fetch is held here, so
        // nothing can bring the push in while it starts).
        push_elsewhere(&f.seed, "late.md");
        let held = hold(&f.mine, &env).await;
        assert_eq!(
            in_turn("c2", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            !f.mine.join("late.md").exists(),
            "the chat turn fetched before it started"
        );
        drop(held);
        // A chat turn's background fetch moves the remote-tracking ref (its own fast-forward may or may not take it,
        // as the fetch races it), and the next chat turn's fast-forward takes it.
        assert_eq!(
            in_turn("c3", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        behind_done(&f.mine, &env).await;
        assert_eq!(head(&f.mine, "origin/main"), head(&f.seed, "HEAD"));
        assert_eq!(
            in_turn("c4", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(f.mine.join("late.md").is_file());
        // Any other turn fetches before it starts, after waiting for a background fetch.
        push_elsewhere(&f.seed, "task.md");
        assert_eq!(
            in_turn("t1", false, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            f.mine.join("task.md").is_file(),
            "a task turn started on an earlier turn's fetch"
        );
        // Past CHAT_FRESH_FOR a chat turn fetches before it starts again.
        behind_done(&f.mine, &env).await;
        push_elsewhere(&f.seed, "paused.md");
        CHAT_OVERRIDE.with(|v| v.set(Some(Duration::ZERO)));
        assert_eq!(
            in_turn("c5", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            f.mine.join("paused.md").is_file(),
            "a chat turn trusted a fetch older than CHAT_FRESH_FOR"
        );
    }

    #[tokio::test]
    async fn a_push_at_a_turns_end_counts_for_chat_turns_and_a_failed_background_fetch_does_not() {
        let f = fixture();
        let env = turn_env(&f.root);
        // A push outside any turn's refresh (the end of a turn) counts for the chat turns after it.
        std::fs::write(f.mine.join("mine.md"), "mine\n").unwrap();
        sh(&f.mine, &["add", "-A"]);
        sh(&f.mine, &["commit", "-qm", "mine"]);
        assert_eq!(crate::execute::push_repo(&f.mine, &env).await.1, "");
        push_elsewhere(&f.seed, "late.md");
        let held = hold(&f.mine, &env).await;
        assert_eq!(
            in_turn("c1", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            !f.mine.join("late.md").exists(),
            "the push did not count for the chat turn"
        );
        drop(held);

        // A background fetch that fails forgets the last fetch: the next chat turn fetches before it starts.
        let url = sh(&f.mine, &["config", "--get", "remote.origin.url"]);
        sh(
            &f.mine,
            &[
                "config",
                "remote.origin.url",
                &f.root.join("gone.git").display().to_string(),
            ],
        );
        assert_eq!(
            in_turn("c2", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        behind_done(&f.mine, &env).await;
        sh(&f.mine, &["config", "remote.origin.url", &url]);
        push_elsewhere(&f.seed, "later.md");
        assert_eq!(
            in_turn("c3", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            f.mine.join("later.md").is_file(),
            "a chat turn trusted a failed background fetch"
        );
    }
}
