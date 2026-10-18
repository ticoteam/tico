//! One turn at a time on a login whose provider allows it only one job stream (`one_turn_per_login`, Codex signed in
//! with ChatGPT: OpenAI's Codex docs allow one auth.json per serialized job stream, never shared by concurrent jobs).
//! A turn takes its login before its host starts and keeps it until the host stops; another bot's turn on the same
//! login waits for it in order, or runs on its own fallback (crate::execute). A login signed in with a key (an OpenAI
//! API key) is never held, and neither is any other CLI's.
//!
//! In memory: this runner starts every turn on this computer, and a turn that ends, fails, runs out of time or
//! panics drops what it holds, which frees the login at once. A restarted runner holds nothing.

use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock};

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

/// A login held by one turn; dropping it (the turn's host stopped, or the turn ended any other way) frees it.
pub struct Held {
    key: String,
    _guard: OwnedMutexGuard<()>,
}

impl Drop for Held {
    fn drop(&mut self) {
        // Cleared before the guard goes, so the next turn's own name is the one left.
        set_holder(&self.key, None);
    }
}

/// The login if it is free now, else Err with the bot whose turn holds it.
pub fn try_take(key: &str, bot: &str) -> Result<Held, String> {
    match lock_for(key).try_lock_owned() {
        Ok(guard) => {
            set_holder(key, Some(bot));
            Ok(Held { key: key.into(), _guard: guard })
        }
        Err(_) => Err(holder(key).unwrap_or_default()),
    }
}

/// The login once every turn that asked for it earlier has had it (first come, first served).
pub async fn take(key: &str, bot: &str) -> Held {
    let guard = lock_for(key).lock_owned().await;
    set_holder(key, Some(bot));
    Held { key: key.into(), _guard: guard }
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
