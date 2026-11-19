//! A turn's start brings each checkout up to its upstream (`execute::pull_repo`, `sessions::sync_shared`), and a task
//! turn does it twice for one repository: the bot's checkout, then the task's worktree of it, a second or so apart.
//! Inside a turn (`in_turn`), a fetch of a repository and upstream the same turn already fetched, or pushed to, within
//! FRESH_FOR is skipped; the merge or rebase onto it still runs.
//!
//! A chat turn (a person waits for it in the bot's own checkout, `in_turn` with `chat`) also starts on this runner's
//! last fetch or push of the repository from any turn, when it began less than CHAT_FRESH_FOR ago. The turn moves the
//! remote-tracking ref to what the last background fetch brought (`Upstream::take_behind`), fast-forwards onto it, and
//! then fetches again in the background beside the model's start (`Upstream::refresh_behind`), so the next chat turn
//! starts on what the remote holds now. That fetch writes only its own ref, refs/tico/fetched/<remote>/<branch>, never
//! FETCH_HEAD or the remote-tracking ref the running turn and its git see. Every other turn, and a chat turn with no
//! recent fetch (the first after a pause, a restart or a placement, after a background fetch failed, or when the
//! remote's branch was rewritten), fetches before it starts, as before.
//!
//! A hub that knows of pushes (its GitHub App's webhook) tells this runner of a push to a bot's repository at once
//! (`pushed` on the event stream, to a runner that advertises PUSH_HINTS: `run_loop`). The runner forgets its fetch of
//! that checkout (`Upstream::stale`), so a chat turn that starts before the next fetch lands fetches first, and fetches
//! it in the background at once. Without the webhook, CHAT_FRESH_FOR is the bound on how old a chat turn's start is.

use std::collections::{HashMap, HashSet};
use std::future::Future;
use std::path::Path;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use crate::env::Env;
use crate::util::text_of;

/// The heartbeat capability that asks the hub for `pushed` events (backend/github.py tell_pushed).
pub const PUSH_HINTS: &str = "push_hints_v1";

/// How long a turn's fetch of a repository stands for a later one in the same turn.
const FRESH_FOR: Duration = Duration::from_secs(15);
/// How long this runner's last fetch or push of a repository stands for a chat turn's start.
const CHAT_FRESH_FOR: Duration = Duration::from_secs(300);
/// The background fetch's limit, the same as a turn's own fetch.
const BEHIND_TIMEOUT_S: u64 = 45;
/// Where a background fetch leaves what it brought, beside the remote-tracking ref it stands for.
const BEHIND: &str = "refs/tico/fetched/";

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

/// Repository and upstream -> when this runner's last successful fetch or push of it began, from any turn: the
/// remote-tracking ref, or the background fetch's ref beside it, is at least as new as the remote was then.
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

/// Repository and upstream -> a background fetch of it is running (one at a time), and whether another must follow it
/// (a push was heard while it ran, so it may have missed it).
fn running() -> &'static Mutex<HashMap<String, bool>> {
    static RUNNING: OnceLock<Mutex<HashMap<String, bool>>> = OnceLock::new();
    RUNNING.get_or_init(Default::default)
}

/// Repository and upstream -> when the hub last said it was pushed to: only a fetch that began after it counts.
fn pushed() -> &'static Mutex<HashMap<String, Instant>> {
    static PUSHED: OnceLock<Mutex<HashMap<String, Instant>>> = OnceLock::new();
    PUSHED.get_or_init(Default::default)
}

/// Pushes the hub told of, not yet handled by `run_loop`.
struct Heard {
    pending: Mutex<Pending>,
    /// The last `Pending::told` that `run_loop` has handled: each checkout told of until then is marked stale.
    handled: Mutex<u64>,
    wake: tokio::sync::Notify,
}

#[derive(Default)]
struct Pending {
    bots: HashSet<String>,
    /// How many times the hub has told of a push.
    told: u64,
}

fn new_heard() -> Heard {
    Heard {
        pending: Mutex::new(Pending::default()),
        handled: Mutex::new(0),
        wake: tokio::sync::Notify::new(),
    }
}

#[cfg(not(test))]
fn heard_pushes() -> &'static Heard {
    static HEARD: OnceLock<Heard> = OnceLock::new();
    HEARD.get_or_init(new_heard)
}

/// In tests, each test thread hears only its own pushes: one test's push does not make another's chat turn fetch first.
#[cfg(test)]
fn heard_pushes() -> &'static Heard {
    thread_local! {
        static HEARD: &'static Heard = Box::leak(Box::new(new_heard()));
    }
    HEARD.with(|h| *h)
}

/// The event stream said these bots' repositories were pushed to (crate::events). Until `run_loop` has marked their
/// checkouts stale (it looks up the bot's GitHub access first), no chat turn starts on a recent fetch (`unheard`).
pub fn heard(bots: &[String]) {
    let heard = heard_pushes();
    {
        let mut pending = heard.pending.lock().unwrap();
        pending.bots.extend(bots.iter().cloned());
        pending.told += 1;
    }
    heard.wake.notify_one();
}

/// Whether a push the hub told of is not yet handled: which checkout it concerns is not known until it is.
fn unheard() -> bool {
    let heard = heard_pushes();
    let told = heard.pending.lock().unwrap().told;
    *heard.handled.lock().unwrap() != told
}

#[cfg(test)]
pub(crate) fn heard_of(bot: &str) -> bool {
    heard_pushes().pending.lock().unwrap().bots.contains(bot)
}

/// What `run_loop` takes from the pushes told of: the bots, and the count to mark handled once they are.
fn take_heard() -> (Vec<String>, u64) {
    let mut pending = heard_pushes().pending.lock().unwrap();
    (pending.bots.drain().collect(), pending.told)
}

fn handled(told: u64) {
    let mut handled = heard_pushes().handled.lock().unwrap();
    if *handled < told {
        *handled = told;
    }
}

/// Each bot of this runner the hub said was pushed to: its checkout's fetch is forgotten and fetched again at once in
/// the background. A shared checkout, which no chat turn starts on a recent fetch of, is left alone.
pub async fn run_loop(runner: Arc<crate::runner::Runner>) {
    let heard = heard_pushes();
    while !runner.stopping() {
        heard.wake.notified().await;
        let (bots, told) = take_heard();
        handle_pushes(&runner, bots).await;
        handled(told);
    }
}

async fn handle_pushes(runner: &crate::runner::Runner, bots: Vec<String>) {
    let mine = runner.config.runner_id();
    let assignments = runner.assignments_seen();
    for bot in bots {
        let Some(entry) = assignments
            .iter()
            .find(|e| text_of(&e["bot"]) == bot && text_of(&e["runner_id"]) == mine)
        else {
            continue;
        };
        if crate::sessions::is_shared(&entry["config"]) {
            continue;
        }
        let path = crate::sessions::local_path(&runner.config, &bot, &entry["config"]);
        if crate::repo_safety::outside_installation(&path).is_err() || !path.join(".git").exists() {
            continue;
        }
        match runner.github_access(&bot, entry).await {
            Ok(env) => after_push(&path, &crate::safegit::environment(&env)).await,
            Err(why) => {
                // Without the bot's GitHub access the fetch cannot run: its next chat turn fetches first. Marked
                // stale rather than forgotten, so a fetch that began before the push cannot stand for it.
                let env = crate::safegit::environment(&crate::env::process_environment());
                if let Some(up) = upstream(&path, &env).await {
                    up.stale();
                }
                crate::util::diagnostic(&format!(
                        "Tico runner: {bot}: heard of a push but cannot fetch it now ({why}); its next chat turn fetches first"
                    ));
            }
        }
    }
}

/// The hub said the checkout's repository was pushed to: no chat turn starts on a fetch from before, and one runs now.
pub async fn after_push(path: &Path, env: &Env) {
    if let Some(up) = upstream(path, env).await {
        up.stale();
        up.refresh_behind(path, env);
    }
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

/// Runs `work` in the turn, but fetching first as any other turn does: for the repositories a chat turn refreshes
/// beside the bot's own checkout (its `reads:` siblings), which nothing fetches in the background.
pub async fn fetching_first<F: Future>(work: F) -> F::Output {
    match TURN.try_with(|t| t.aid.clone()) {
        Ok(aid) => in_turn(&aid, false, work).await,
        Err(_) => work.await,
    }
}

/// A checkout's repository (its git common directory) and upstream ref, inside a turn.
pub struct Upstream {
    turn: String,
    chat: bool,
    /// The remote-tracking ref, refs/remotes/<remote>/<branch>.
    tracked: String,
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
        tracked: tracked.to_string(),
    })
}

/// A new placement of the bot: its checkout is caught up from GitHub, and no chat turn starts on a fetch from before.
pub async fn forget(path: &Path, env: &Env) {
    if let Some(up) = upstream(path, env).await {
        synced().lock().unwrap().remove(&up.key);
    }
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
    /// CHAT_FRESH_FOR: the turn may start on that (`take_behind`), and fetches behind it (`refresh_behind`).
    pub fn recent(&self) -> bool {
        let span = chat_fresh_for();
        let since_push = pushed().lock().unwrap().get(&self.key).copied();
        self.chat
            && !unheard()
            && synced()
                .lock()
                .unwrap()
                .get(&self.key)
                .is_some_and(|at| at.elapsed() < span && since_push.is_none_or(|p| *at > p))
    }

    /// The hub said the remote was pushed to: a fetch or push that began before now no longer stands for a chat
    /// turn's start, and a background fetch running now is followed by another.
    pub fn stale(&self) {
        let mut pushed = pushed().lock().unwrap();
        pushed.retain(|_, at| at.elapsed() < CHAT_FRESH_FOR);
        pushed.insert(self.key.clone(), Instant::now());
        if let Some(again) = running().lock().unwrap().get_mut(&self.key) {
            *again = true;
        }
    }

    /// Whether a person waits for this turn in the bot's own checkout (`in_turn`).
    pub fn chat(&self) -> bool {
        self.chat
    }

    /// The turn's attempt id, for its flight marks; empty outside a turn.
    pub fn turn(&self) -> &str {
        &self.turn
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

    fn behind_ref(&self) -> String {
        format!(
            "{BEHIND}{}",
            self.tracked.trim_start_matches("refs/remotes/")
        )
    }

    /// A chat turn starting on the runner's recent fetch: move the remote-tracking ref up to what the last background
    /// fetch brought. True when the turn may start on it; false when it must fetch first (the remote's branch was
    /// rewritten since, or the ref moved under this turn): then the record and the background fetch's ref are dropped.
    pub async fn take_behind(&self, path: &Path, env: &Env) -> bool {
        let behind = self.behind_ref();
        let taken = move_up(path, env, &self.tracked, &behind).await;
        if !taken {
            synced().lock().unwrap().remove(&self.key);
            let _ = run(path, env, &["update-ref", "-d", &behind]).await;
        }
        taken
    }

    /// A chat turn that started on the runner's recent fetch: fetch in the background, beside the model's start, so the
    /// next chat turn starts on what the remote holds now. The fetch writes only its own ref (module comment), so this
    /// turn's tree, its remote-tracking ref and FETCH_HEAD stay as they are. One at a time per repository and upstream.
    /// A failed fetch forgets the last one, so the next chat turn fetches before it starts and says why, as before.
    pub fn refresh_behind(&self, path: &Path, env: &Env) {
        {
            let mut running = running().lock().unwrap();
            if running.contains_key(&self.key) {
                return;
            }
            running.insert(self.key.clone(), false);
        }
        let (path, env, key) = (path.to_path_buf(), env.clone(), self.key.clone());
        let (behind, tracked) = (self.behind_ref(), self.tracked.clone());
        tokio::spawn(async move {
            // A push heard while it ran: fetch once more, so the push is not missed.
            loop {
                if !fetch_once(&path, &env, &key, &tracked, &behind).await {
                    running().lock().unwrap().remove(&key);
                    break;
                }
                if !again(&key) {
                    break;
                }
            }
        });
    }
}

/// One background fetch into `behind`: true when it succeeded.
async fn fetch_once(path: &Path, env: &Env, key: &str, tracked: &str, behind: &str) -> bool {
    let began = Instant::now();
    let fetched = fetch_behind(path, env, tracked, behind).await;
    if fetched {
        note_synced(key, began);
    } else {
        synced().lock().unwrap().remove(key);
        let name = path
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        crate::util::diagnostic(&format!(
            "Tico runner: {name}: a background fetch failed; the next chat turn fetches before it starts"
        ));
    }
    fetched
}

/// After a background fetch of `key` that succeeded: whether a push was heard while it ran (`Upstream::stale`), so
/// another must follow; if not, the fetch is no longer running. One lock, so no push is heard in between.
fn again(key: &str) -> bool {
    let mut running = running().lock().unwrap();
    if running.get(key) == Some(&true) {
        running.insert(key.to_string(), false);
        true
    } else {
        running.remove(key);
        false
    }
}

async fn run(path: &Path, env: &Env, args: &[&str]) -> Option<crate::gitrun::Out> {
    crate::gitrun::git_as_bot(path, env, args, 15)
        .await
        .ok()
        .filter(|o| o.ok())
}

/// Moves `tracked` up to `behind` when that is a fast-forward (`Upstream::take_behind`).
async fn move_up(path: &Path, env: &Env, tracked: &str, behind: &str) -> bool {
    let Some(out) = run(
        path,
        env,
        &[
            "for-each-ref",
            "--format=%(refname)%09%(objectname)",
            behind,
            tracked,
        ],
    )
    .await
    else {
        return false;
    };
    let mut at = HashMap::new();
    for line in out.stdout.lines() {
        if let Some((name, oid)) = line.trim().split_once('\t') {
            at.insert(name.to_string(), oid.to_string());
        }
    }
    // No background fetch has finished yet: the last fetch or push moved the remote-tracking ref itself.
    let Some(fetched) = at.get(behind) else {
        return true;
    };
    let Some(old) = at.get(tracked) else {
        return false;
    };
    if fetched == old {
        return true;
    }
    let ancestor = |a: &str, b: &str| {
        let (a, b) = (a.to_string(), b.to_string());
        async move {
            run(
                path,
                env,
                &["merge-base", "--is-ancestor", a.as_str(), b.as_str()],
            )
            .await
            .is_some()
        }
    };
    if ancestor(fetched, old).await {
        return true; // a later fetch or push moved the remote-tracking ref past it
    }
    if !ancestor(old, fetched).await {
        return false; // the remote's branch was rewritten: fetch first, as any turn would see it
    }
    // Only from the value read above: a fetch that moved it meanwhile wins, and this turn fetches first.
    run(
        path,
        env,
        &[
            "update-ref",
            "-m",
            "fetch: tico background fetch",
            tracked,
            fetched.as_str(),
            old.as_str(),
        ],
    )
    .await
    .is_some()
}

/// Fetch the checkout's upstream branch into `behind` alone.
async fn fetch_behind(path: &Path, env: &Env, tracked: &str, behind: &str) -> bool {
    let Some(branch) = run(path, env, &["symbolic-ref", "-q", "HEAD"])
        .await
        .map(|o| o.stdout.trim().to_string())
    else {
        return false;
    };
    let Some(target) = run(
        path,
        env,
        &[
            "for-each-ref",
            "--format=%(upstream)%09%(upstream:remotename)%09%(upstream:remoteref)",
            &branch,
        ],
    )
    .await
    .map(|o| o.stdout.trim().to_string()) else {
        return false;
    };
    let mut parts = target.split('\t');
    let (Some(upstream), Some(remote), Some(remoteref)) =
        (parts.next(), parts.next(), parts.next())
    else {
        return false;
    };
    // The branch moved to another upstream since the turn started: nothing to fetch for this one.
    if upstream != tracked || remote.is_empty() || remote == "." || !remoteref.starts_with("refs/")
    {
        return false;
    }
    let refspec = format!("+{remoteref}:{behind}");
    // Outside the repository's command gate (gitrun::fetch_ungated_as_bot): the turn's own git must not queue behind it.
    // `--refmap=` keeps git from moving the remote-tracking ref along with it, and no gc or maintenance runs beside the
    // turn.
    crate::gitrun::fetch_ungated_as_bot(
        path,
        env,
        &[
            "-c",
            "gc.auto=0",
            "-c",
            "maintenance.auto=false",
            "fetch",
            "--quiet",
            "--no-tags",
            "--no-write-fetch-head",
            "--refmap=",
            remote,
            &refspec,
        ],
        BEHIND_TIMEOUT_S,
    )
    .await
    .is_ok_and(|out| out.ok())
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
        let key = upstream(path, env).await.unwrap().key;
        while running().lock().unwrap().contains_key(&key) {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }

    fn head(dir: &Path, rev: &str) -> String {
        sh(dir, &["rev-parse", rev])
    }

    fn fetch_head(dir: &Path) -> String {
        std::fs::read_to_string(dir.join(".git/FETCH_HEAD")).unwrap_or_default()
    }

    const BEHIND_MAIN: &str = "refs/tico/fetched/origin/main";

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
        let (tracked, fetch_head_was) = (head(&f.mine, "origin/main"), fetch_head(&f.mine));
        // The next chat turn starts on that fetch, without fetching first.
        push_elsewhere(&f.seed, "late.md");
        assert_eq!(
            in_turn("c2", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            !f.mine.join("late.md").exists(),
            "the chat turn fetched before it started"
        );
        // Its fetch behind it brings the push into its own ref alone: the turn's remote-tracking ref and FETCH_HEAD
        // stay as they were.
        behind_done(&f.mine, &env).await;
        assert_eq!(head(&f.mine, BEHIND_MAIN), head(&f.seed, "HEAD"));
        assert_eq!(head(&f.mine, "origin/main"), tracked);
        assert_eq!(fetch_head(&f.mine), fetch_head_was);
        // The next chat turn starts on what that fetch brought.
        assert_eq!(
            in_turn("c3", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(f.mine.join("late.md").is_file());
        assert_eq!(head(&f.mine, "origin/main"), head(&f.seed, "HEAD"));
        behind_done(&f.mine, &env).await;
        // Any other turn fetches before it starts.
        push_elsewhere(&f.seed, "task.md");
        assert_eq!(
            in_turn("t1", false, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            f.mine.join("task.md").is_file(),
            "a task turn started on an earlier turn's fetch"
        );
        // A new placement forgets the runner's fetch: the chat turn after it fetches first.
        push_elsewhere(&f.seed, "placed.md");
        forget(&f.mine, &env).await;
        assert_eq!(
            in_turn("c4", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            f.mine.join("placed.md").is_file(),
            "a placement kept the record"
        );
        // Past CHAT_FRESH_FOR a chat turn fetches before it starts again.
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
    async fn a_rewritten_upstream_or_a_failed_background_fetch_makes_the_next_chat_turn_fetch_first(
    ) {
        let f = fixture();
        let env = turn_env(&f.root);
        // A push outside any turn's refresh (the end of a turn) counts for the chat turns after it.
        std::fs::write(f.mine.join("mine.md"), "mine\n").unwrap();
        sh(&f.mine, &["add", "-A"]);
        sh(&f.mine, &["commit", "-qm", "mine"]);
        assert_eq!(crate::execute::push_repo(&f.mine, &env).await.1, "");
        push_elsewhere(&f.seed, "late.md");
        assert_eq!(
            in_turn("c1", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            !f.mine.join("late.md").exists(),
            "the push did not count for the chat turn"
        );
        behind_done(&f.mine, &env).await;

        // The remote's branch is rewritten: the background fetch brings that, and the next chat turn fetches first, as
        // any turn would, and drops the background fetch's ref.
        sh(&f.seed, &["pull", "-q", "--rebase"]);
        sh(&f.seed, &["reset", "-q", "--hard", "HEAD~1"]);
        std::fs::write(f.seed.join("rewritten.md"), "x\n").unwrap();
        sh(&f.seed, &["add", "-A"]);
        sh(&f.seed, &["commit", "-qm", "rewritten"]);
        sh(&f.seed, &["push", "-q", "--force", "origin", "HEAD:main"]);
        assert_eq!(
            in_turn("c2", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        behind_done(&f.mine, &env).await;
        assert_eq!(head(&f.mine, BEHIND_MAIN), head(&f.seed, "HEAD"));
        assert!(
            !in_turn("c3", true, crate::execute::pull_repo(&f.mine, &env))
                .await
                .is_empty(),
            "a rewritten upstream fast-forwarded"
        );
        assert_eq!(head(&f.mine, "origin/main"), head(&f.seed, "HEAD"));
        assert!(sh(&f.mine, &["for-each-ref", BEHIND_MAIN]).is_empty());
        sh(&f.mine, &["reset", "-q", "--hard", "origin/main"]);

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
            in_turn("c4", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        behind_done(&f.mine, &env).await;
        sh(&f.mine, &["config", "remote.origin.url", &url]);
        push_elsewhere(&f.seed, "later.md");
        assert_eq!(
            in_turn("c5", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            f.mine.join("later.md").is_file(),
            "a chat turn trusted a failed background fetch"
        );
    }

    /// A chat turn's `reads:` siblings fetch first: nothing fetches them in the background.
    #[tokio::test]
    async fn fetching_first_keeps_the_turn_but_not_its_chat() {
        let f = fixture();
        let env = turn_env(&f.root);
        assert_eq!(crate::execute::pull_repo(&f.mine, &env).await, "");
        push_elsewhere(&f.seed, "late.md");
        let pulled = in_turn("r1", true, async {
            let chat = upstream(&f.mine, &env).await.unwrap();
            let read = fetching_first(async {
                let up = upstream(&f.mine, &env).await.unwrap();
                (up.turn().to_string(), up.chat())
            })
            .await;
            (chat.chat(), read)
        })
        .await;
        assert_eq!(pulled, (true, ("r1".to_string(), false)));
    }

    #[tokio::test]
    async fn a_push_the_hub_tells_of_makes_the_next_chat_turn_fetch_first_and_is_fetched_at_once() {
        let f = fixture();
        let env = turn_env(&f.root);
        assert_eq!(
            in_turn("h1", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        // The hub says the repository was pushed to; a chat turn that starts before the fetch it asks for lands
        // fetches first.
        push_elsewhere(&f.seed, "late.md");
        upstream(&f.mine, &env).await.unwrap().stale();
        assert_eq!(
            in_turn("h2", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            f.mine.join("late.md").is_file(),
            "a chat turn started on a fetch from before a push the hub told of"
        );
        // The hint's own fetch runs at once, beside nothing: the next chat turn starts on it, push and all.
        push_elsewhere(&f.seed, "hinted.md");
        let fetch_head_was = fetch_head(&f.mine);
        after_push(&f.mine, &env).await;
        behind_done(&f.mine, &env).await;
        assert_eq!(head(&f.mine, BEHIND_MAIN), head(&f.seed, "HEAD"));
        assert_eq!(fetch_head(&f.mine), fetch_head_was);
        assert_eq!(
            in_turn("h3", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(f.mine.join("hinted.md").is_file());
        assert_eq!(
            fetch_head(&f.mine),
            fetch_head_was,
            "the chat turn fetched first"
        );
        // Heard on the event stream but not yet handled (the bot's GitHub access is still being looked up): which
        // checkout it concerns is not known yet, so a chat turn starting now fetches first.
        behind_done(&f.mine, &env).await;
        push_elsewhere(&f.seed, "told.md");
        heard(&["ops".to_string()]);
        assert!(heard_of("ops") && unheard());
        assert_eq!(
            in_turn("h4", true, crate::execute::pull_repo(&f.mine, &env)).await,
            ""
        );
        assert!(
            f.mine.join("told.md").is_file(),
            "a chat turn started on a recent fetch while a push it was told of was not handled"
        );
        let (bots, told) = take_heard();
        assert_eq!(bots, vec!["ops".to_string()]);
        handled(told);
        assert!(!unheard());
    }

    #[test]
    fn a_push_heard_while_a_background_fetch_runs_gets_one_more() {
        let key = format!("again-{}", uuid::Uuid::new_v4().simple());
        running().lock().unwrap().insert(key.clone(), true);
        assert!(again(&key), "the push heard while it ran was dropped");
        assert!(!again(&key));
        assert!(
            !running().lock().unwrap().contains_key(&key),
            "still running"
        );
    }
}
