//! One turn at a time on a login whose provider allows it only one job stream (`one_turn_per_login`, Codex signed in
//! with ChatGPT: OpenAI's Codex docs allow one auth.json per serialized job stream, never shared by concurrent jobs).
//! A turn takes its login before its host starts and keeps it until the host stops; another bot's turn on the same
//! login waits for it in order, or runs on its own fallback (crate::execute). A login signed in with a key (an OpenAI
//! API key) is never held, and neither is any other CLI's.
//!
//! In memory: this runner starts every turn on this computer, and a turn that ends, fails, runs out of time or
//! panics drops what it holds. That frees the login at once, unless the process of the host the turn started is still
//! running (a panic or a cancelled turn skips or cuts off its stop, or the host stopped answering but lives on): then
//! the login is kept until that process has been stopped and reaped. A restarted runner holds nothing.
//!
//! A login is never copied into another bot's home to get round this: a copy of a ChatGPT auth.json rotates apart
//! from the original, and two bots on two copies would slip past the lock (the guard test below).

use std::collections::HashMap;
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use tokio::sync::OwnedMutexGuard;

/// A login that serves one turn at a time: `key` is the runtime and the login (crate::fallback::login: the
/// profile's own, or "" for the computer's), `label` how a person reads it.
#[derive(Clone, Debug, PartialEq)]
pub struct Login {
    pub key: String,
    pub label: String,
}

impl Login {
    pub fn new(runtime: &str, login: &str) -> Login {
        let label = if login.is_empty() { "this computer's ChatGPT login".to_string() } else { format!("ChatGPT login \"{login}\"") };
        Login { key: format!("{runtime}:{login}"), label }
    }

    /// The chat's and the run's line while a turn waits: "Waiting for <login> to be free (busy with <bot>)".
    pub fn waiting(&self, holder: &str, bot: &str) -> String {
        format!("Waiting for {} to be free (busy with {})", self.label, who(holder, bot))
    }

    /// The run's note when the turn goes to its fallback instead: "ChatGPT login busy with <bot>; ...".
    pub fn busy(&self, holder: &str, bot: &str) -> String {
        let label = self.label.strip_prefix("this computer's ").map_or_else(|| self.label.clone(), String::from);
        format!("{label} busy with {}", who(holder, bot))
    }
}

fn who(holder: &str, bot: &str) -> String {
    if holder == bot { "another turn of this bot".into() } else { holder.into() }
}

struct Slot {
    lock: Arc<tokio::sync::Mutex<()>>,
    holder: Option<String>,
}

fn slots() -> &'static Mutex<HashMap<String, Slot>> {
    static S: OnceLock<Mutex<HashMap<String, Slot>>> = OnceLock::new();
    S.get_or_init(Default::default)
}

fn lock_for(key: &str) -> Arc<tokio::sync::Mutex<()>> {
    let mut slots = slots().lock().unwrap();
    slots.entry(key.into()).or_insert_with(|| Slot { lock: Default::default(), holder: None }).lock.clone()
}

fn set_holder(key: &str, bot: Option<&str>) {
    if let Some(slot) = slots().lock().unwrap().get_mut(key) {
        slot.holder = bot.map(String::from);
    }
}

/// What a held login waits for before it is free again: the host its turn started on the login. `running` is about
/// the process, not the protocol: a host that stopped answering, or whose stop was begun and cut off, may still run.
pub trait Lingering: Send + 'static {
    fn running(&self) -> bool;
    /// Stop the host if it still runs, and return once its process is gone.
    fn stop(self: Box<Self>) -> Pin<Box<dyn Future<Output = ()> + Send>>;
}

impl Lingering for crate::hosts::Host {
    fn running(&self) -> bool {
        crate::hosts::Host::running(self)
    }

    fn stop(self: Box<Self>) -> Pin<Box<dyn Future<Output = ()> + Send>> {
        Box::pin(async move { crate::hosts::Host::stop_and_reap(&self).await })
    }
}

/// A login held by one turn; dropping it (the turn's host stopped, or the turn ended any other way) frees it.
pub struct Held {
    key: String,
    guard: Option<OwnedMutexGuard<()>>,
    host: Option<Box<dyn Lingering>>,
}

impl Held {
    fn new(key: &str, guard: OwnedMutexGuard<()>) -> Held {
        Held { key: key.into(), guard: Some(guard), host: None }
    }

    /// The host this turn runs on the login: if it is still running when the turn lets go, the login stays held
    /// until it has stopped, so no other turn's host ever runs beside it.
    pub fn bind(&mut self, host: impl Lingering) {
        self.host = Some(Box::new(host));
    }
}

impl Drop for Held {
    fn drop(&mut self) {
        let (key, guard) = (std::mem::take(&mut self.key), self.guard.take());
        match (self.host.take().filter(|h| h.running()), tokio::runtime::Handle::try_current()) {
            (Some(host), Ok(runtime)) => {
                runtime.spawn(async move {
                    host.stop().await;
                    set_holder(&key, None);
                    drop(guard);
                });
            }
            _ => {
                // Cleared before the guard goes, so the next turn's own name is the one left.
                set_holder(&key, None);
                drop(guard);
            }
        }
    }
}

/// The login if it is free now, else Err with the bot whose turn holds it.
pub fn try_take(key: &str, bot: &str) -> Result<Held, String> {
    match lock_for(key).try_lock_owned() {
        Ok(guard) => {
            set_holder(key, Some(bot));
            Ok(Held::new(key, guard))
        }
        Err(_) => Err(holder(key).unwrap_or_default()),
    }
}

/// The login once every turn that asked for it earlier has had it (first come, first served).
pub async fn take(key: &str, bot: &str) -> Held {
    let guard = lock_for(key).lock_owned().await;
    set_holder(key, Some(bot));
    Held::new(key, guard)
}

/// The login for a turn that may wait for it: at once when it is free, else in its place in the queue. `over` says
/// why the turn should not run any more (its lease, a Stop, the computer stopping); it is asked before taking the
/// login, every `tick` while waiting and once more when the login is granted, so a cancelled turn never starts on it
/// (dropping it passes the login to the next turn in the queue). `waiting` hears the holder when the turn starts
/// to wait and whenever the holder changes.
pub async fn wait_for(key: &str, bot: &str, over: impl Fn() -> Option<String>, mut waiting: impl FnMut(&str), tick: Duration) -> Result<Held, String> {
    if let Some(why) = over() {
        return Err(why);
    }
    let mut by = match try_take(key, bot) {
        Ok(held) => return Ok(held),
        Err(by) => by,
    };
    waiting(&by);
    let queued = take(key, bot);
    tokio::pin!(queued);
    let held = loop {
        tokio::select! {
            held = &mut queued => break held,
            _ = tokio::time::sleep(tick) => {
                if let Some(why) = over() {
                    return Err(why);
                }
                if let Some(now) = holder(key).filter(|h| *h != by) {
                    by = now;
                    waiting(&by);
                }
            }
        }
    };
    if let Some(why) = over() {
        return Err(why);
    }
    Ok(held)
}

/// The bot whose turn holds the login, if one does.
pub fn holder(key: &str) -> Option<String> {
    slots().lock().unwrap().get(key).and_then(|s| s.holder.clone())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::time::Duration;

    /// Two turns on `keys` at once, each holding its login for a moment: the most that ran together.
    async fn overlap(keys: [&str; 2]) -> usize {
        let (now, most) = (Arc::new(AtomicUsize::new(0)), Arc::new(AtomicUsize::new(0)));
        let turns = keys.map(|key| {
            let (now, most, key) = (now.clone(), most.clone(), key.to_string());
            tokio::spawn(async move {
                let _held = take(&key, "ops").await;
                let running = now.fetch_add(1, Ordering::SeqCst) + 1;
                most.fetch_max(running, Ordering::SeqCst);
                tokio::time::sleep(Duration::from_millis(50)).await;
                now.fetch_sub(1, Ordering::SeqCst);
            })
        });
        for t in turns {
            t.await.unwrap();
        }
        most.load(Ordering::SeqCst)
    }

    #[tokio::test]
    async fn two_turns_on_one_login_run_one_after_the_other_and_other_logins_run_beside_them() {
        assert_eq!(overlap(["codex:test-same", "codex:test-same"]).await, 1);
        assert_eq!(overlap(["codex:test-a", "codex:test-b"]).await, 2);
        assert_eq!(overlap(["codex:", "codex:test-own"]).await, 2);
    }

    #[tokio::test]
    async fn a_busy_login_names_its_holder_and_is_free_again_once_the_turn_lets_go() {
        let key = "codex:test-busy";
        let held = try_take(key, "builder").unwrap();
        assert_eq!(holder(key).as_deref(), Some("builder"));
        assert_eq!(try_take(key, "support").err().as_deref(), Some("builder"));
        drop(held);
        assert_eq!(holder(key), None);
        let again = try_take(key, "support").unwrap();
        assert_eq!(holder(key).as_deref(), Some("support"));
        drop(again);
    }

    #[tokio::test]
    async fn waiting_turns_take_the_login_in_the_order_they_asked() {
        let key = "codex:test-order";
        let first = try_take(key, "a").unwrap();
        let order = Arc::new(Mutex::new(vec![]));
        let mut waiters = vec![];
        for bot in ["b", "c", "d"] {
            let order = order.clone();
            waiters.push(tokio::spawn(async move {
                let _held = take(key, bot).await;
                order.lock().unwrap().push(bot);
                tokio::time::sleep(Duration::from_millis(5)).await;
            }));
            tokio::time::sleep(Duration::from_millis(10)).await; // each one asks after the last
        }
        drop(first);
        for w in waiters {
            w.await.unwrap();
        }
        assert_eq!(*order.lock().unwrap(), ["b", "c", "d"]);
    }

    #[tokio::test]
    async fn a_turn_that_panics_or_runs_out_of_time_frees_the_login() {
        let key = "codex:test-crash";
        let crashed = tokio::spawn(async move {
            let _held = take(key, "ops").await;
            panic!("the turn crashed");
        });
        assert!(crashed.await.unwrap_err().is_panic());
        assert_eq!(holder(key), None);
        drop(try_take(key, "next").expect("free after a panic"));
        // A turn cut off at its time limit (the future dropped mid-turn) lets go too.
        let timed_out = tokio::time::timeout(Duration::from_millis(20), async move {
            let _held = take(key, "ops").await;
            tokio::time::sleep(Duration::from_secs(60)).await;
        })
        .await;
        assert!(timed_out.is_err());
        drop(try_take(key, "next").expect("free after a timeout"));
        // A turn that gave up waiting leaves the queue: the login is not kept for it.
        let held = try_take(key, "a").unwrap();
        assert!(tokio::time::timeout(Duration::from_millis(20), take(key, "gave-up")).await.is_err());
        drop(held);
        assert_eq!(try_take(key, "b").map(|_| ()).err(), None);
    }

    /// A real Codex host on a stand-in app-server: it answers `initialize`, writes its pid to $FAKE_PID, and then
    /// runs until it is stopped. With FAKE_MODE=deaf it ignores TERM (only the KILL five seconds later ends it); with
    /// FAKE_MODE=silent it closes its output once started and runs on; otherwise a TERM ends it half a second later.
    async fn codex_host(mode: &str) -> (crate::hosts::Host, i32, std::path::PathBuf) {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!("tico-login-host-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let program = dir.join("codex");
        std::fs::write(&program, r#"#!/usr/bin/env python3
import json, os, signal, sys, time
mode = os.environ.get("FAKE_MODE", "")
with open(os.environ["FAKE_PID"], "w") as f:
    f.write(str(os.getpid()))
if mode == "deaf":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
else:
    signal.signal(signal.SIGTERM, lambda *_: (time.sleep(0.5), os._exit(0)))
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "initialize":
        print(json.dumps({"id": msg["id"], "result": {}}), flush=True)
    if msg.get("method") == "initialized" and mode == "silent":
        sys.stdout = open(os.devnull, "w")
        os.close(1)
time.sleep(60)
"#).unwrap();
        std::fs::set_permissions(&program, std::fs::Permissions::from_mode(0o755)).unwrap();
        let pid_file = dir.join("pid");
        let path = format!("{}:{}", dir.display(), std::env::var("PATH").unwrap_or_default());
        let env: crate::env::Env = [("PATH".to_string(), path),
                                    ("FAKE_MODE".to_string(), mode.to_string()),
                                    ("FAKE_PID".to_string(), pid_file.display().to_string())].into_iter().collect();
        let (tx, _rx) = tokio::sync::mpsc::unbounded_channel();
        let host = crate::hosts::Host::Codex(crate::hosts::codex::CodexHost::on_home(env, tx, None));
        host.start().await.expect("the stand-in app-server starts");
        let pid = std::fs::read_to_string(&pid_file).unwrap().trim().parse().unwrap();
        (host, pid, dir)
    }

    /// Whether `pid` is still a process (a zombie not yet reaped counts).
    fn exists(pid: i32) -> bool {
        unsafe { libc::kill(pid, 0) == 0 }
    }

    #[tokio::test]
    async fn a_codex_turn_cut_off_while_its_host_is_stopping_keeps_the_login_until_the_app_server_is_reaped() {
        let key = "codex:test-cut-stop";
        let (host, pid, _dir) = codex_host("deaf").await;
        let mut held = take(key, "ops").await;
        held.bind(host.clone());
        // The turn's own cleanup begins the stop and is cut off while the app-server, deaf to TERM, still runs: the
        // protocol says not alive already, the process lives on until the KILL five seconds later.
        let started = std::time::Instant::now();
        assert!(tokio::time::timeout(Duration::from_millis(300), host.stop()).await.is_err());
        assert!(!host.alive() && host.running() && exists(pid));
        drop(held);
        tokio::time::sleep(Duration::from_secs(1)).await;
        assert_eq!(try_take(key, "next").map(|_| ()).err().as_deref(), Some("ops"), "free while the app-server still ran");
        let next = tokio::time::timeout(Duration::from_secs(15), take(key, "next")).await.expect("free once reaped");
        assert!(!exists(pid), "the next turn got the login before the app-server was reaped");
        assert!(started.elapsed() >= Duration::from_secs(4), "{:?}", started.elapsed());
        drop(next);
    }

    #[tokio::test]
    async fn a_turn_that_panics_with_its_codex_host_running_keeps_the_login_until_the_app_server_is_reaped() {
        let key = "codex:test-panic";
        // An app-server that closed its output but still runs: the protocol says not alive, the process is there.
        let (host, pid, _dir) = codex_host("silent").await;
        tokio::time::timeout(Duration::from_secs(5), async {
            while host.alive() {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("the closed output is seen");
        assert!(host.running() && exists(pid));
        let crashed = tokio::spawn(async move {
            let mut held = take(key, "ops").await;
            held.bind(host);
            panic!("the worker crashed mid-turn");
        });
        assert!(crashed.await.unwrap_err().is_panic());
        assert_eq!(try_take(key, "next").map(|_| ()).err().as_deref(), Some("ops"));
        let next = tokio::time::timeout(Duration::from_secs(15), take(key, "next")).await.expect("free once reaped");
        assert!(!exists(pid), "the next turn got the login while the crashed turn's app-server still ran");
        drop(next);

        // A turn cut off mid-run (its future dropped) is the same.
        let (host, pid, _dir) = codex_host("").await;
        let h = host.clone();
        let cut = tokio::time::timeout(Duration::from_millis(50), async move {
            let mut held = take(key, "ops").await;
            held.bind(h);
            std::future::pending::<()>().await;
        });
        assert!(cut.await.is_err());
        let next = tokio::time::timeout(Duration::from_secs(15), take(key, "next")).await.expect("free once reaped");
        assert!(!exists(pid) && !host.running());
        drop(next);

        // A host the turn already stopped frees the login at once.
        let (host, _pid, _dir) = codex_host("").await;
        host.stop_and_reap().await;
        let mut held = try_take(key, "ops").unwrap();
        held.bind(host);
        drop(held);
        drop(try_take(key, "next").expect("free at once"));
    }

    #[tokio::test]
    async fn a_waiting_turn_that_is_over_never_takes_the_login_and_the_next_one_does() {
        let over = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let why = |over: &Arc<std::sync::atomic::AtomicBool>| {
            let over = over.clone();
            move || over.load(Ordering::SeqCst).then(|| "Execution interrupted after stop".to_string())
        };
        // Already stopped, login free: refused, and the login stays free.
        let key = "codex:test-over-free";
        over.store(true, Ordering::SeqCst);
        assert!(wait_for(key, "ops", why(&over), |_| {}, Duration::from_secs(60)).await.is_err());
        assert!(try_take(key, "next").is_ok());

        // Stopped just before the holder lets go, with no tick in between: the grant is checked too, and the turn
        // queued behind it gets the login.
        let key = "codex:test-over-grant";
        over.store(false, Ordering::SeqCst);
        let first = try_take(key, "builder").unwrap();
        let said = Arc::new(Mutex::new(vec![]));
        let heard = said.clone();
        let stopped = tokio::spawn(wait_for(key, "ops", why(&over), move |h| heard.lock().unwrap().push(h.to_string()), Duration::from_secs(60)));
        tokio::time::sleep(Duration::from_millis(20)).await;
        let behind = tokio::spawn(wait_for(key, "support", || None, |_| {}, Duration::from_secs(60)));
        tokio::time::sleep(Duration::from_millis(20)).await;
        over.store(true, Ordering::SeqCst);
        drop(first);
        assert!(stopped.await.unwrap().is_err(), "a stopped turn started on the login");
        let next = tokio::time::timeout(Duration::from_secs(5), behind).await.expect("the next waiter proceeds").unwrap().unwrap();
        assert_eq!(holder(key).as_deref(), Some("support"));
        assert_eq!(*said.lock().unwrap(), ["builder"]);
        drop(next);

        // A lease lost while the holder still runs ends the wait at the next tick.
        let key = "codex:test-over-lost";
        over.store(false, Ordering::SeqCst);
        let first = try_take(key, "builder").unwrap();
        let lost = tokio::spawn(wait_for(key, "ops", why(&over), |_| {}, Duration::from_millis(10)));
        tokio::time::sleep(Duration::from_millis(30)).await;
        over.store(true, Ordering::SeqCst);
        assert!(tokio::time::timeout(Duration::from_secs(5), lost).await.expect("the wait ends").unwrap().is_err());
        assert_eq!(holder(key).as_deref(), Some("builder"));
        drop(first);
        assert!(try_take(key, "next").is_ok(), "the given-up waiter left the queue");
    }

    /// No code path copies a login file into another home. Switch account signs in inside the login's own home
    /// (login.rs begin_switch) and seeds nothing for Codex; the only other copy is the replay recorder's, test-only.
    #[test]
    fn no_code_path_copies_a_codex_login_between_homes() {
        let login = crate::harness_tools::login_command("codex").expect("Codex has a login section");
        assert!(login.seed.is_empty(), "Switch account copies {:?} into the side home", login.seed);
        let src = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
        let (mut files, mut dirs) = (vec![], vec![src.clone()]);
        while let Some(d) = dirs.pop() {
            for e in std::fs::read_dir(&d).unwrap().flatten() {
                let p = e.path();
                if p.is_dir() {
                    dirs.push(p);
                } else if p.extension().is_some_and(|x| x == "rs") {
                    files.push(p);
                }
            }
        }
        let copying = regex::Regex::new(r#"fs::copy|fs::write|fs::rename|write_private|"cp"|\bcopy\b"#).unwrap();
        let mut found = vec![];
        for path in files {
            let rel = path.strip_prefix(&src).unwrap().display().to_string();
            if rel == "hosts/replay.rs" || rel == "login_turns.rs" {
                continue; // test-only (hosts/mod.rs: #[cfg(test)] mod replay), and this guard itself
            }
            let text = std::fs::read_to_string(&path).unwrap();
            // Production code only: everything before the file's own test module.
            let code = text.split("#[cfg(test)]\nmod ").next().unwrap_or_default();
            for (n, line) in code.lines().enumerate() {
                if line.contains("auth.json") && copying.is_match(line) {
                    found.push(format!("{rel}:{}: {}", n + 1, line.trim()));
                }
            }
        }
        assert!(found.is_empty(), "a login file is copied: {found:#?}");
    }

    #[test]
    fn the_lines_name_the_login_and_who_holds_it() {
        let computer = Login::new("codex", "");
        assert_eq!(computer.key, "codex:");
        assert_eq!(computer.waiting("builder", "support"), "Waiting for this computer's ChatGPT login to be free (busy with builder)");
        assert_eq!(computer.busy("builder", "support"), "ChatGPT login busy with builder");
        let work = Login::new("codex", "work");
        assert_eq!(work.waiting("support", "support"), "Waiting for ChatGPT login \"work\" to be free (busy with another turn of this bot)");
        assert_eq!(work.busy("builder", "support"), "ChatGPT login \"work\" busy with builder");
    }
}
